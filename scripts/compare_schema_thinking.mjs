#!/usr/bin/env node
// Capture real fixture UI requests in RAM, then replay identical requests without
// executing proposed tools. Never persist prompts, private tool results or reasoning.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn, execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {expectedNoteTitles} from './note_test_oracle.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const upstream = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const userText = body => body.messages.findLast(m=>m.role==='user')?.content;
const titleNorm = s => String(s || '').trim().toLowerCase().replace(/^reminder\s*:\s*/, '').replace(/\s+/g,' ');

export function recordsIn(messages) {
  return messages.filter(m=>m.role==='tool').flatMap(m=>{
    let content=String(m.content || '');
    try { const obj=JSON.parse(content); content=obj.results || obj.stdout || obj.output || content; } catch {}
    return [...String(content).matchAll(/- \[([a-f0-9-]{36})\] \*\*([^\n]+?)\*\*/g)]
      .map(match=>({id:match[1],title:match[2]}));
  });
}

export function reformatNoteResult(content, format) {
  if(!['quoted','jsonl'].includes(format)) throw Error('Unknown note result format');
  let wrapper, key, text=content;
  try {
    wrapper=JSON.parse(content);
    key=['results','stdout','output'].find(k=>typeof wrapper?.[k]==='string');
    if(!key) throw Error('Unsupported result wrapper');
    text=wrapper[key];
  } catch(error) {
    if(wrapper!==undefined) throw error;
  }
  const lines=String(text).split('\n');
  const rows=lines.map(line=>{
    const m=line.match(/^- \[([a-f0-9-]{36})\] \*\*(.+?)\*\*(.*)$/);
    if(!m) throw Error('Refuse to drop unrecognized result data');
    return {id:m[1],title:m[2],suffix:m[3]};
  });
  const formatted=rows.map(r=>format==='quoted'
    ? `- [${r.id}] ${JSON.stringify(r.title)}${r.suffix}` : JSON.stringify(r)).join('\n');
  // Round-trip the presentation before using it; preserve record order and all
  // original fields, including tags/type/pinning suffixes and wrapper metadata.
  const decoded=formatted.split('\n').map(line=>{
    if(format==='jsonl') return JSON.parse(line);
    const m=line.match(/^- \[([a-f0-9-]{36})\] ("(?:[^"\\]|\\.)*")(.*)$/);
    if(!m) throw Error('Quoted format failed round trip');
    return {id:m[1],title:JSON.parse(m[2]),suffix:m[3]};
  });
  if(JSON.stringify(decoded)!==JSON.stringify(rows)) throw Error('Result data changed');
  if(key) {wrapper[key]=formatted;return JSON.stringify(wrapper);}
  return formatted;
}

export function scoreCalls(calls, records, expected) {
  const selected=[], invalid=[];
  let readCalls=0;
  for(const call of calls) {
    let args;
    try { args=JSON.parse(call.function.arguments); } catch {invalid.push('invalid_json');continue;}
    if(!args || typeof args!=='object' || Array.isArray(args)) {invalid.push('invalid_arguments');continue;}
    if(call.function.name!=='manage_notes') {invalid.push('other_tool');continue;}
    if(['list','search','find','view'].includes(args.action)) {readCalls++;continue;}
    if(!['delete','remove'].includes(args.action)) {invalid.push('other_action');continue;}
    const id=String(args.id || args.note_id || args.noteId || '').trim();
    let matches=id ? records.filter(r=>r.id.startsWith(id)) : [];
    if(!matches.length) matches=records.filter(r=>titleNorm(r.title)===titleNorm(args.title || args.query || args.text));
    if(matches.length!==1) {invalid.push(matches.length?'ambiguous_target':'unknown_target');continue;}
    selected.push(matches[0].title);
  }
  const unique=[...new Set(selected)].sort();
  return {exact_target_proposal:invalid.length===0 && selected.length===unique.length && JSON.stringify(unique)===JSON.stringify([...expected].sort()),
    proposal_stage_only:true,
    selected_titles:unique,invalid,read_calls:readCalls,duplicate_targets:selected.length-unique.length,
    wrong_targets:unique.filter(t=>!expected.includes(t)),missing_targets:expected.filter(t=>!unique.includes(t))};
}

export function auditHistory(request, priorRequests, ids, savedEvidence=null) {
  const toolResults=request.messages.filter(m=>m.role==='tool');
  const priorResults=priorRequests.flatMap(r=>r.messages.filter(m=>m.role==='tool'));
  const noteResult=priorResults.find(m=>ids.every(id=>String(m.content).includes(id)));
  const records=recordsIn(request.messages).filter(r=>ids.includes(r.id));
  const callIds=new Set(request.messages.flatMap(m=>(m.tool_calls || []).map(c=>c.id)));
  return {message_roles:request.messages.map(m=>m.role),
    user_turns:request.messages.filter(m=>m.role==='user').length,
    tool_result_count:toolResults.length,
    fixture_ids_present:ids.filter(id=>records.some(r=>r.id===id)).length,
    exact_prior_note_result_preserved:savedEvidence ? toolResults.some(m=>
      m.tool_call_id===savedEvidence.call_id && hash(m.content)===savedEvidence.content_sha256)
      : Boolean(noteResult && toolResults.some(m=>
        m.tool_call_id===noteResult.tool_call_id && m.content===noteResult.content)),
    comparison_source:savedEvidence?'prior_turn_saved_tool_result':'prior_outbound_request',
    orphan_tool_results:toolResults.filter(m=>!callIds.has(m.tool_call_id)).length,
    messages_sha256:hash(request.messages),
    compact_schemas_sha256:hash(request.tools),
    offered_tools:(request.tools || []).map(s=>s.function.name),
    thinking:request.chat_template_kwargs?.enable_thinking,
    forced_tool_choice:request.tool_choice || null};
}

async function completion(body) {
  const started=performance.now();
  let buffer='',firstDelta=null,firstTool=null,usage={},finish=null,content='',reasoningChars=0;
  const calls=new Map();
  const response=await fetch(upstream,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body),signal:AbortSignal.timeout(90000)});
  if(!response.ok) throw Error(`Inference HTTP ${response.status}`);
  const consume = frame => {
    const raw=frame.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');
    if(!raw || raw==='[DONE]') return;
    const p=JSON.parse(raw);
    if(p.usage) usage=p.usage;
    for(const c of p.choices || []) {
      if(c.finish_reason) finish=c.finish_reason;
      const d=c.delta || {};
      if(d.content || d.reasoning_content || d.reasoning || d.tool_calls?.length)
        firstDelta ??= (performance.now()-started)/1000;
      reasoningChars+=String(d.reasoning_content || d.reasoning || '').length;
      content+=d.content || '';
      for(const part of d.tool_calls || []) {
        firstTool ??= (performance.now()-started)/1000;
        const v=calls.get(part.index) || {function:{name:'',arguments:''}};
        v.function.name+=part.function?.name || '';
        v.function.arguments+=part.function?.arguments || '';
        calls.set(part.index,v);
      }
    }
  };
  for await(const chunk of response.body) {
    buffer+=Buffer.from(chunk).toString('utf8');
    let end;
    while((end=buffer.indexOf('\n\n'))>=0) {consume(buffer.slice(0,end));buffer=buffer.slice(end+2);}
  }
  if(buffer.trim()) consume(buffer);
  const endThink=content.indexOf('</think>');
  const unparsedThinking=endThink>=0 || content.includes('<think>');
  const seconds=(performance.now()-started)/1000;
  return {calls:[...calls.values()],metrics:{seconds,first_delta_s:firstDelta,first_tool_delta_s:firstTool,
    input_tokens:usage.prompt_tokens ?? null,output_tokens:usage.completion_tokens ?? null,
    generation_tok_s:usage.completion_tokens && firstDelta!==null && seconds>firstDelta
      ? usage.completion_tokens/(seconds-firstDelta) : null,
    finish_reason:finish,reasoning_chars:reasoningChars,thinking_in_content:unparsedThinking,
    content_chars:content.length}};
}

async function main() {
  const stamp=new Date().toISOString().replace(/[:.]/g,'-');
  const formatting=process.env.EXPERIMENT==='result_format';
  const prefix=formatting?'result-format':'schema-thinking';
  const file=path.join(root,'reports',`${prefix}-${stamp}.json`);
  const cases=(process.env.PROBE_CASES || 'original,typo,drinks,schedule_words,quoted,negative,subset,single').split(',');
  const fullSchemas=formatting?[]:JSON.parse(execFileSync((process.env.PYTHON || "python3"),[
    '-c','import json; from src.tool_schemas import FUNCTION_TOOL_SCHEMAS; print(json.dumps(FUNCTION_TOOL_SCHEMAS))'
  ],{cwd:root,maxBuffer:4*1024*1024,encoding:'utf8'}));
  const report={status:'running',scope:'Actual 7011 fixture history audit; direct proposal replay does not execute tools.',
    experiment:prefix,model:'odysseus-qwen3.5-tools-pre-heretic',temperature:0,max_tokens:2048,cases,runs:[]};
  const save=()=>fs.writeFileSync(file,JSON.stringify(report,null,2)+'\n');
  let captured=[];
  const server=http.createServer(async(req,res)=>{
    if(req.method!=='POST' || req.url!=='/v1/chat/completions') {res.writeHead(404).end();return;}
    try {
      let raw=''; for await(const c of req) {raw+=c;if(raw.length>2*1024*1024)throw Error('Request too large');}
      const body=JSON.parse(raw);
      if(body.model!==report.model) {res.writeHead(400).end();return;}
      captured.push(structuredClone(body));
      const result=await fetch(upstream,{method:'POST',headers:{'Content-Type':'application/json'},
        body:raw,signal:AbortSignal.timeout(90000)});
      res.writeHead(result.status,{'Content-Type':result.headers.get('content-type') || 'text/event-stream'});
      for await(const c of result.body) res.write(c);
      res.end();
    } catch {if(!res.headersSent)res.writeHead(502);res.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const local=`http://127.0.0.1:${server.address().port}/v1/chat/completions`;
  const endpointId=crypto.randomUUID();
  const endpointName=`[schema-thinking-fixture] ${endpointId}`;
  const endpointDB=(operation)=>execFileSync((process.env.PYTHON || "python3"),[
    '-c', `import sqlite3,sys,json
c=sqlite3.connect('${process.env.ODYSSEUS_DB_PATH || path.join(root, "data", "app.db")}')
op,ident,name,url,model=sys.argv[1:]
if op=='add':
 c.execute('INSERT INTO model_endpoints (id,name,base_url,owner,is_enabled,cached_models,pinned_models,model_type,endpoint_kind,model_refresh_mode,supports_tools,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',(ident,name,url,'sft_alex_creator',1,json.dumps([model]),json.dumps([model]),'llm','local','manual',1))
else:
 c.execute('DELETE FROM model_endpoints WHERE id=? AND name=? AND owner=? AND base_url=?',(ident,name,'sft_alex_creator',url))
c.commit()
print(c.execute('SELECT count(*) FROM model_endpoints WHERE id=?',(ident,)).fetchone()[0])`,
    operation,endpointId,endpointName,local.replace('/chat/completions',''),report.model,
  ],{encoding:'utf8'}).trim();
  save();
  try {
    if(endpointDB('add')!=='1') throw Error('Fixture proxy registration failed');
    for(const [index,name] of cases.entries()) {
      captured=[];
      const uiFile=path.join(root,'reports',`${formatting?'format-ui':'schema-ui'}-${stamp}-${name}.json`);
      await new Promise((resolve,reject)=>{
        const p=spawn(process.execPath,['scripts/verify_multi_note_delete_followup.mjs'],{cwd:root,
          env:{...process.env,ENDPOINT_URL:local,ENDPOINT_ID:endpointId,ROUTING_MODE:'recent_fixture_only',
            FOLLOWUP_CASE:name,TITLE_STYLE:'plain',REPORT_PATH:uiFile,AUDIT_FINAL:'true'},
          stdio:['ignore','pipe','pipe']});
        p.stdout.resume();p.stderr.resume();p.on('error',reject);p.on('exit',resolve);
      });
      const ui=JSON.parse(fs.readFileSync(uiFile,'utf8'));
      if(ui.error || !ui.outcome || !ui.outcome.unrelated_preserved ||
          Object.keys(ui.cleanup).length!==4 || !Object.values(ui.cleanup).every(Boolean))
        throw Error(`Invalid UI fixture capture: ${name}; see child report`);
      const ids=Object.keys(ui.cleanup).filter(k=>k!=='session');
      // Third user turn is the real follow-up; later rounds retain that request.
      const firstIndex=captured.findIndex(r=>r.messages.filter(m=>m.role==='user').length===3);
      if(firstIndex<0) throw Error(`No real outbound follow-up captured: ${name}`);
      const request=captured[firstIndex];
      const audit=auditHistory(request,captured.slice(0,firstIndex),ids,ui.prior_note_evidence);
      const records=recordsIn(request.messages).filter(r=>ids.includes(r.id));
      const expected=expectedNoteTitles(name,records.map(r=>r.title));
      const run={case:name,ui_report:path.relative(root,uiFile),ui_outcome:ui.outcome,history:audit,variants:[]};
      report.runs.push(run);save();
      if(audit.fixture_ids_present!==3 || !audit.exact_prior_note_result_preserved || audit.orphan_tool_results)
        throw Error(`History audit failed: ${name}`);
      if(formatting) {
        const noteIndex=request.messages.findIndex(m=>m.role==='tool' &&
          m.tool_call_id===ui.prior_note_evidence.call_id);
        const variants=['original','quoted','jsonl'];
        const order=variants.slice(index%3).concat(variants.slice(0,index%3));
        for(const variant of order) {
          const body={...structuredClone(request),max_tokens:2048};
          if(variant!=='original') body.messages[noteIndex].content=
            reformatNoteResult(body.messages[noteIndex].content,variant);
          const otherMessagesUnchanged=request.messages.every((m,i)=>i===noteIndex || hash(m)===hash(body.messages[i]));
          const sameSchemas=hash(body.tools)===hash(request.tools);
          if(!otherMessagesUnchanged || !sameSchemas || body.chat_template_kwargs.enable_thinking!==false)
            throw Error('Non-format change in formatting comparison');
          const result=await completion(body);
          run.variants.push({variant,other_messages_unchanged:otherMessagesUnchanged,
            schemas_unchanged:sameSchemas,lossless_result:true,
            result_chars:body.messages[noteIndex].content.length,
            ...scoreCalls(result.calls,records,expected),metrics:result.metrics});
          save();
        }
        console.log(JSON.stringify({case:name,variants:run.variants.map(v=>({mode:v.variant,
          exact:v.exact_target_proposal,seconds:v.metrics.seconds}))}));
        continue;
      }
      const full=request.tools.map(s=>fullSchemas.find(f=>f.function.name===s.function.name));
      if(full.some(s=>!s)) throw Error('Missing canonical full schema');
      run.schema_comparison={compact_bytes:JSON.stringify(request.tools).length,full_bytes:JSON.stringify(full).length,
        same_tool_names:JSON.stringify(full.map(s=>s.function.name))===JSON.stringify(request.tools.map(s=>s.function.name)),
        full_schemas_sha256:hash(full)};
      const variants=['compact_off','full_off','compact_on'];
      const order=variants.slice(index%3).concat(variants.slice(0,index%3));
      for(const variant of order) {
        const body={...structuredClone(request),max_tokens:2048,
          tools:variant==='full_off'?full:request.tools,
          chat_template_kwargs:{...request.chat_template_kwargs,enable_thinking:variant==='compact_on'}};
        const result=await completion(body);
        run.variants.push({variant,messages_sha256:hash(body.messages),
          ...scoreCalls(result.calls,records,expected),metrics:result.metrics});
        save();
      }
      // Error-only progressive thinking replays the actual next model request,
      // after successful partial effects and tool errors; it never re-executes them.
      const second=captured.slice(firstIndex+1).find(r=>userText(r)===userText(request));
      const failed=ui.turns.at(-1).errors.length>0;
      run.progressive={triggered:failed};
      if(failed && second) {
        const remaining=ui.turns.at(-1).remaining_fixture_titles;
        const result=await completion({...structuredClone(second),max_tokens:2048,
          chat_template_kwargs:{...second.chat_template_kwargs,enable_thinking:true}});
        run.progressive={triggered:true,...scoreCalls(result.calls,records,remaining.filter(t=>expected.includes(t))),
          metrics:result.metrics,scope:'Error-round recovery proposal only; not executed or timed end-to-end.'};
      }
      save();
      console.log(JSON.stringify({case:name,history_ok:true,variants:run.variants.map(v=>({mode:v.variant,
        exact:v.exact_target_proposal,seconds:v.metrics.seconds})),progressive:run.progressive.triggered}));
    }
    report.status='measured';
  } catch(e) {report.status='blocked';report.error=String(e.message).slice(0,300);
    report.capture_diagnostic={requests:captured.length,user_turn_counts:captured.map(r=>r.messages.filter(m=>m.role==='user').length)};}
  finally {captured=[];server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
    report.fixture_endpoint_removed=endpointDB('remove')==='0';save();}
  console.log(JSON.stringify({report:file,status:report.status,completed:report.runs.length,error:report.error}));
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) await main();

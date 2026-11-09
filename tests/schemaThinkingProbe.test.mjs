import test from 'node:test';
import assert from 'node:assert/strict';
import {recordsIn,scoreCalls,auditHistory,reformatNoteResult} from '../scripts/compare_schema_thinking.mjs';
const records=[{id:'aaaaaaaa-0000-0000-0000-000000000000',title:'Japan'},
  {id:'bbbbbbbb-0000-0000-0000-000000000000',title:'Today'}];
const call=(args)=>({function:{name:'manage_notes',arguments:JSON.stringify(args)}});

test('proposal grading resolves supported title and ID references, not concatenations',()=>{
  assert.equal(scoreCalls([call({action:'delete',title:'Japan Today'})],records,['Japan','Today']).exact_target_proposal,false);
  assert.equal(scoreCalls([call({action:'delete',id:'aaaaaaaa'}),call({action:'delete',title:'Today'})],records,['Japan','Today']).exact_target_proposal,true);
});
test('negative controls fail if any record is deleted',()=>{
  assert.equal(scoreCalls([],records,[]).exact_target_proposal,true);
  assert.equal(scoreCalls([call({action:'delete',title:'Japan'})],records,[]).exact_target_proposal,false);
});
test('missing targets are not hidden by repeated calls or exploratory reads',()=>{
  const result=scoreCalls([call({action:'delete',title:'Japan'}),call({action:'delete',title:'Japan'}),call({action:'list'})],records,['Japan','Today']);
  assert.equal(result.exact_target_proposal,false);
  assert.equal(result.duplicate_targets,1);
  assert.deepEqual(result.missing_targets,['Today']);
});
test('audit checks actual tool result bytes and native call pairing',()=>{
  const result={role:'tool',tool_call_id:'c1',content:records.map(r=>`- [${r.id}] **${r.title}**`).join('\n')};
  const messages=[{role:'user',content:'notes'},
    {role:'assistant',tool_calls:[{id:'c1'}]},result,{role:'user',content:'delete both'}];
  const request={messages,tools:[],chat_template_kwargs:{enable_thinking:false}};
  assert.deepEqual(recordsIn(messages),records);
  assert.equal(auditHistory(request,[{messages}],records.map(r=>r.id)).exact_prior_note_result_preserved,true);
  const broken={...request,messages:[{...result,content:result.content+' changed'}]};
  const audit=auditHistory(broken,[{messages}],records.map(r=>r.id));
  assert.equal(audit.exact_prior_note_result_preserved,false);
  assert.equal(audit.orphan_tool_results,1);
});

test('format-only variants retain IDs, titles, ordering, suffixes and wrapper fields',()=>{
  const rows=[{...records[0],suffix:' [PINNED] #travel'},
    {...records[1],title:'Today "special"',suffix:' [checklist] #todo'}];
  const text=rows.map(r=>`- [${r.id}] **${r.title}**${r.suffix}`).join('\n');
  const wrapped=JSON.stringify({results:text,exit_code:0,extra:'unchanged'});
  const quoted=JSON.parse(reformatNoteResult(wrapped,'quoted'));
  assert.equal(quoted.extra,'unchanged');
  assert.equal(quoted.exit_code,0);
  assert.equal(quoted.results,rows.map(r=>`- [${r.id}] ${JSON.stringify(r.title)}${r.suffix}`).join('\n'));
  const jsonl=JSON.parse(reformatNoteResult(wrapped,'jsonl'));
  assert.deepEqual(jsonl.results.split('\n').map(l=>JSON.parse(l)),rows);
  assert.throws(()=>reformatNoteResult(text+'\nAdditional body text','jsonl'));
});

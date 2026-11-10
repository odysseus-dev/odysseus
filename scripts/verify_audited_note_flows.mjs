#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
const root=path.resolve(new URL('..',import.meta.url).pathname);
const stamp=new Date().toISOString().replace(/[:.]/g,'-');
const allCases=['quoted','quoted_typo','single','subset','except_one','contrast','negative','all_three',
  'neutral','neutral_typo','user_punctuation','original','typo','drinks','schedule_words'];
const cases=process.env.FLOW_CASES?process.env.FLOW_CASES.split(','):allCases;
if(!cases.length || new Set(cases).size!==cases.length || cases.some(c=>!allCases.includes(c)))
  throw Error('Unregistered audit cases');
const file=path.join(root,'reports',`audited-note-flows-${stamp}.json`);
const report={status:'running',rubric:'NOTE_FLOW_V2_RUBRIC.md',cases,runs:[],semantic_review:'pending'};
const save=()=>fs.writeFileSync(file,JSON.stringify(report,null,2)+'\n');
save();
try {
  for(const name of cases) {
    const childFile=path.join(root,'reports',`audited-note-${stamp}-${name}.json`);
    await new Promise((resolve,reject)=>{
      const p=spawn(process.execPath,['scripts/verify_multi_note_delete_followup.mjs'],{cwd:root,
        env:{...process.env,AUDITED_FLOW:'true',TITLE_STYLE:'plain',AUDIT_FINAL:'true',
          ROUTING_MODE:'recent_fixture_only',FOLLOWUP_CASE:name,REPORT_PATH:childFile},
        stdio:['ignore','pipe','pipe']});
      p.stdout.resume();p.stderr.resume();p.on('error',reject);p.on('exit',resolve);
    });
    const r=JSON.parse(fs.readFileSync(childFile,'utf8'));
    const cleanup=Object.keys(r.cleanup || {}).length===4 && Object.values(r.cleanup).every(Boolean);
    const setup=r.turns.slice(0,2).length===2 && r.turns.slice(0,2).every(t=>Object.values(t.checks).every(Boolean));
    if(r.error || !r.audited || !cleanup || !setup || !r.outcome.unrelated_preserved)
      throw Error(`Invalid/unsafe test ${name}: ${r.error || 'setup/cleanup/state verification failed'}`);
    report.runs.push({case:name,report:path.relative(root,childFile),cleanup,...r.audited});save();
    console.log(JSON.stringify({case:name,kind:r.audited.kind,initial_exact:r.audited.initial_state.exact,
      clarified:r.audited.clarification_sent,final_exact:r.audited.final_state.exact}));
  }
  report.status='measured_pending_semantic_review';
} catch(e) {report.status='blocked';report.error=String(e.message).slice(0,300);}
save();console.log(JSON.stringify({report:file,status:report.status,completed:report.runs.length,error:report.error}));

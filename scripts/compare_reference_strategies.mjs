#!/usr/bin/env node
// Serial fixture-only experiment; mode order rotates per case.
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
const root = path.resolve(new URL('..', import.meta.url).pathname);
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const manifest = path.join(root,'reports',`reference-strategies-${stamp}.json`);
const availableCases = ['original','reversed','quoted','all_three','negative','typo',
  'subset','keep_all','contrast','drinks','schedule_words','explicit_ids',
  'quoted_typo','single','except_one','punctuated'];
const cases = process.env.REFERENCE_CASES ? process.env.REFERENCE_CASES.split(',') : availableCases;
if (!cases.length || new Set(cases).size !== cases.length || cases.some(c=>!availableCases.includes(c)))
  throw Error('Invalid reference cases');
const modes = (process.env.REFERENCE_MODES || 'recent_fixture_only').split(',');
if (!modes.length || new Set(modes).size !== modes.length || modes.some(m =>
    !['recent','recent_no_family_gate','recent_fixture_only'].includes(m)))
  throw Error('Invalid reference experiment modes');
const report = {status:'running',model:'odysseus-qwen3.5-tools-pre-heretic',
  thinking:false,cases,modes,runs:[],scope:'plain-title synthetic notes in 7011 Agent UI; no production default change'};
const save = () => fs.writeFileSync(manifest,JSON.stringify(report,null,2)+'\n');
save();
try {
  for (let index=0; index<cases.length; index++) {
    const order = modes.slice(index%modes.length).concat(modes.slice(0,index%modes.length));
    for (const mode of order) {
      const file = path.join(root,'reports',`reference-${stamp}-${cases[index]}-${mode}.json`);
      await new Promise((resolve,reject) => {
        const p = spawn(process.execPath,[path.join(root,'scripts/verify_multi_note_delete_followup.mjs')],{
          cwd:root,env:{...process.env,TITLE_STYLE:'plain',AUDIT_FINAL:'true',
            ROUTING_MODE:mode,FOLLOWUP_CASE:cases[index],REPORT_PATH:file},
          stdio:['ignore','pipe','pipe'],
        });
        p.stdout.resume(); p.stderr.resume();
        p.on('error',reject); p.on('exit',resolve);
      });
      const result = JSON.parse(fs.readFileSync(file,'utf8'));
      const cleaned = Object.keys(result.cleanup || {}).length === 4 && Object.values(result.cleanup).every(Boolean);
      const setupOK = result.turns?.slice(0,2).length === 2 && result.turns.slice(0,2).every(t=>Object.values(t.checks).every(Boolean));
      report.runs.push({case:cases[index],mode,outcome:result.outcome || null,setup_ok:setupOK,
        cleanup:cleaned,report:path.relative(root,file),diagnostics:result.diagnostics || null});
      save();
      if (result.error || !result.outcome || !cleaned || !setupOK)
        throw Error(`Invalid experiment/precondition in ${path.basename(file)}: ${result.error || 'setup/outcome/cleanup missing'}`);
      if (!result.outcome.unrelated_preserved) throw Error('Unrelated data changed; stop testing.');
    }
  }
  report.status='measured';
} catch(error) {
  report.status='blocked';report.error=String(error.message).slice(0,500);
}
save();
console.log(JSON.stringify({manifest,status:report.status,completed:report.runs.length}));

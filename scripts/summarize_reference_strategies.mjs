#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
const root = path.resolve(new URL('..', import.meta.url).pathname);
const manifests = process.argv.slice(2).map(p=>JSON.parse(fs.readFileSync(p,'utf8')));
const groups = {};
const median = xs => {
  const a=xs.filter(Number.isFinite).sort((a,b)=>a-b), n=a.length;
  return !n ? null : n%2 ? a[(n-1)/2] : (a[n/2-1]+a[n/2])/2;
};
for (const manifest of manifests) for (const run of manifest.runs) {
  const r=JSON.parse(fs.readFileSync(path.join(root,run.report),'utf8'));
  const labels=run.case==='drinks'?['Milk','Tea','Coffee']
    :run.case==='schedule_words'?['Tomorrow','Work','Weekend']:['Groceries','Japan','Today'];
  const expected=['negative','keep_all'].includes(run.case)?[]
    :run.case==='subset'?['Japan','Groceries']:run.case==='contrast'?['Groceries']
    :run.case==='single'?['Today']:run.case==='except_one'?['Groceries','Today']:labels;
  const remaining=r.turns.at(-1)?.remaining_fixture_titles;
  const wrong=Array.isArray(remaining)?labels.filter(label=>!expected.includes(label)&&!remaining.includes(label)).length:null;
  (groups[run.mode] ||= []).push({...run,wrong_targets:wrong});
}
console.log(JSON.stringify({
  measured:manifests.every(m=>m.status==='measured'),
  modes:Object.fromEntries(Object.entries(groups).map(([mode,runs])=>[mode,{
    total:runs.length,cases:new Set(runs.map(r=>r.case)).size,
    exact_pass:runs.filter(r=>r.outcome?.passed).length,
    all_setup_cleanup_ok:runs.every(r=>r.setup_ok&&r.cleanup),
    all_unrelated_preserved:runs.every(r=>r.outcome?.unrelated_preserved),
    wrong_target_deletions:runs.every(r=>r.wrong_targets!==null)?runs.reduce((n,r)=>n+r.wrong_targets,0):null,
    negative_controls:runs.filter(r=>['negative','keep_all'].includes(r.case)).map(r=>({case:r.case,pass:r.outcome.passed})),
    failed_cases:runs.filter(r=>!r.outcome?.passed).map(r=>({case:r.case,deleted:r.outcome?.deleted_fixtures,expected:r.outcome?.expected_deleted})),
    median_response_s:median(runs.map(r=>r.diagnostics?.response_time)),
    median_injected_tokens:median(runs.map(r=>r.diagnostics?.injected_tokens)),
  }]))
},null,2));

#!/usr/bin/env node
import fs from 'node:fs';
const reports=process.argv.slice(2).map(p=>JSON.parse(fs.readFileSync(p,'utf8')));
if(!reports.length || reports.some(r=>r.status!=='measured' ||
    r.experiment!=='result-format' || !r.fixture_endpoint_removed))
  throw Error('Need completed, cleaned result-format reports');
const runs=reports.flatMap(r=>r.runs);
const median=xs=>{const a=xs.filter(Number.isFinite).sort((a,b)=>a-b),n=a.length;
  return n ? (n%2?a[(n-1)/2]:(a[n/2-1]+a[n/2])/2) : null;};
const modes=Object.groupBy(runs.flatMap(r=>r.variants.map(v=>({...v,case:r.case}))),v=>v.variant);
console.log(JSON.stringify({cases:runs.length,distinct_cases:new Set(runs.map(r=>r.case)).size,
  format_only_verified:runs.every(r=>r.variants.every(v=>v.other_messages_unchanged &&
    v.schemas_unchanged && v.lossless_result)),
  modes:Object.fromEntries(Object.entries(modes).map(([mode,vs])=>[mode,{
    exact_proposals:vs.filter(v=>v.exact_target_proposal).length,total:vs.length,
    median_call_s:median(vs.map(v=>v.metrics.seconds)),
    median_first_tool_delta_s:median(vs.map(v=>v.metrics.first_tool_delta_s)),
    median_input_tokens:median(vs.map(v=>v.metrics.input_tokens)),
    median_output_tokens:median(vs.map(v=>v.metrics.output_tokens)),
    wrong_targets:vs.reduce((n,v)=>n+v.wrong_targets.length,0),
    length_limited:vs.filter(v=>v.metrics.finish_reason==='length').length,
    failed:vs.filter(v=>!v.exact_target_proposal).map(v=>({case:v.case,
      missing:v.missing_targets,invalid:v.invalid})),
  }])),
},null,2));

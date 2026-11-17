#!/usr/bin/env node
import fs from 'node:fs';
const reports=process.argv.slice(2).map(p=>JSON.parse(fs.readFileSync(p,'utf8')));
if(!reports.length || reports.some(r=>r.status!=='measured' || !r.fixture_endpoint_removed))
  throw Error('Only completed, cleaned capture reports may be summarized');
const runs=reports.flatMap(r=>r.runs);
const median=xs=>{const a=xs.filter(Number.isFinite).sort((a,b)=>a-b),n=a.length;
  return n ? (n%2?a[(n-1)/2]:(a[n/2-1]+a[n/2])/2) : null;};
const byMode=Object.groupBy(runs.flatMap(r=>r.variants.map(v=>({...v,case:r.case}))),v=>v.variant);
console.log(JSON.stringify({cases:runs.length,
  distinct_cases:new Set(runs.map(r=>r.case)).size,
  all_history_intact:runs.every(r=>r.history.exact_prior_note_result_preserved &&
    r.history.fixture_ids_present===3 && r.history.orphan_tool_results===0),
  identical_messages_across_variants:runs.every(r=>r.variants.every(v=>v.messages_sha256===r.history.messages_sha256)),
  same_tool_names:runs.every(r=>r.schema_comparison.same_tool_names),
  modes:Object.fromEntries(Object.entries(byMode).map(([mode,vs])=>[mode,{
    exact_proposals:vs.filter(v=>v.exact_target_proposal).length,total:vs.length,
    median_call_s:median(vs.map(v=>v.metrics.seconds)),
    median_first_tool_delta_s:median(vs.map(v=>v.metrics.first_tool_delta_s)),
    median_input_tokens:median(vs.map(v=>v.metrics.input_tokens)),
    median_output_tokens:median(vs.map(v=>v.metrics.output_tokens)),
    thinking_in_content:vs.filter(v=>v.metrics.thinking_in_content).length,
    length_limited:vs.filter(v=>v.metrics.finish_reason==='length').length,
    failed:vs.filter(v=>!v.exact_target_proposal).map(v=>({case:v.case,
      missing:v.missing_targets,wrong:v.wrong_targets,invalid:v.invalid})),
  }])),
  progressive_error_retry:{triggered:runs.filter(r=>r.progressive.triggered).length,
    exact_remaining_target_proposals:runs.filter(r=>r.progressive.triggered && r.progressive.exact_target_proposal).length,
    median_retry_call_s:median(runs.filter(r=>r.progressive.triggered).map(r=>r.progressive.metrics?.seconds)),
    note:'One error-round retry, not a full execution benchmark. Silent omissions do not trigger it.'},
},null,2));

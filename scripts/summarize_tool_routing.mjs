#!/usr/bin/env node
// Read-only aggregation. Routing diagnostics are not semantic/blind accuracy.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export function summarize(manifest, readReport) {
  const modes = {};
  const mean = xs => xs.length ? xs.reduce((a,b) => a+b, 0) / xs.length : null;
  const median = xs => {
    if (!xs.length) return null;
    const sorted = [...xs].sort((a,b) => a-b), n = sorted.length;
    return n % 2 ? sorted[(n-1)/2] : (sorted[n/2-1]+sorted[n/2])/2;
  };
  for (const mode of ['baseline', 'recent', 'all']) {
    const runs = manifest.runs.filter(r => r.mode === mode);
    const reads = runs.filter(r => r.suite === 'read').map(r => readReport(r.report));
    const notes = runs.filter(r => r.suite === 'notes').map(r => readReport(r.report));
    const chains = reads.flatMap(r => r.chains || []);
    const turns = chains.flatMap(c => c.turns);
    const valid = t => t.checks.http_ok && t.checks.experiment_selected && t.checks.clean_route;
    const executed = t => valid(t) && t.checks.expected_offered && t.checks.expected_succeeded;
    const families = {};
    for (const t of turns) {
      const f = families[t.capability] ||= {turns:0, expected_tool_succeeded:0, strict_diagnostic_pass:0};
      f.turns++; f.expected_tool_succeeded += Number(executed(t));
      f.strict_diagnostic_pass += Number(t.status === 'passed');
    }
    const metrics = {};
    for (const key of ['input_tokens', 'injected_tokens', 'output_tokens', 'time_to_first_token', 'response_time']) {
      const xs = turns.map(t => t.metrics[key]).filter(x => typeof x === 'number' && Number.isFinite(x));
      metrics[key] = {samples:xs.length, missing:turns.length-xs.length, mean:mean(xs), median:median(xs)};
    }
    modes[mode] = {
      read_runs:reads.length, note_runs:notes.length, turns:turns.length,
      strict_diagnostic_pass:turns.filter(t => t.status === 'passed').length,
      expected_tool_succeeded:turns.filter(executed).length,
      expected_tool_succeeded_without_reported_recovery:turns.filter(t => executed(t) && !t.recovered).length,
      strict_conversations:chains.filter(c => c.status === 'passed').length,
      conversations:chains.length,
      valid_contract_turns:turns.filter(valid).length,
      reasoning_leak_turns:turns.filter(t => !t.checks.no_reasoning_leak).length,
      offered_tools: {mean:mean(turns.map(t => t.offered.length)), median:median(turns.map(t => t.offered.length))},
      infrastructure_errors:chains.filter(c => c.infrastructure_failure).map(c => ({chain:c.name,error:c.error})),
      cleanup_confirmed:chains.every(c => c.cleanup) && notes.every(r => Object.keys(r.cleanup || {}).length === 4 && Object.values(r.cleanup).every(Boolean)),
      email_ordinal_checks:turns.flatMap(t => t.calls.filter(c => c.tool === 'read_email').map(c => ({second_email:c.email_uid_ordinal === 2, account_present:c.email_account_present}))),
      notes:notes.map(r => {
        const t = r.turns.find(t => t.name === 'delete-followup');
        return {status:r.status, error:r.error || null, deletion_verified:!!t?.checks.all_targets_gone,
          unrelated_notes_preserved:t?.checks.unrelated_notes_preserved ?? null,
          successful_delete_calls:t?.delete_calls ?? null, offered:t?.offered || [], errors:t?.errors || [],
          policy_decisions:t?.policy_decisions ?? null};
      }),
      failures:chains.flatMap(c => c.turns.filter(t => t.status !== 'passed').map(t => ({chain:c.name,index:t.index,
        failed_checks:Object.keys(t.checks).filter(k => !t.checks[k]), tools:t.tools, outputs:t.outputs}))),
      families, metrics,
    };
  }
  const expectedBatches = new Set(['baseline','recent','all'].flatMap(mode =>
    [1,2,3].flatMap(repeat => ['read','notes'].map(suite => `${mode}:${repeat}:${suite}`))));
  const actualBatches = manifest.runs.map(r => `${r.mode}:${r.repeat}:${r.suite}`);
  const exactBatches = actualBatches.length === 18 && new Set(actualBatches).size === 18
    && actualBatches.every(key => expectedBatches.has(key));
  return {status:manifest.status, complete_design:manifest.status === 'measured' && exactBatches && Object.values(modes).every(m => m.read_runs === 3 && m.note_runs === 3 && m.turns === 99 && m.valid_contract_turns === 99 && m.conversations === 33 && !m.infrastructure_errors.length && m.cleanup_confirmed),
    caveats:['Expected tool success is NOT full functional accuracy.',
      'Only synthetic note deletion has a datastore outcome oracle; email ordinal checks validate identifiers.',
      'Raw/recovered flags are limited to events recorded by the runner; baseline model proposals were not recorded.',
      'Baseline versus experimental modes bundles inventory and forced-call/argument-normalization changes.',
      'Null TTFT is missing data, not zero latency. No automatic promotion.'],modes};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const manifest = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  console.log(JSON.stringify(summarize(manifest, p => JSON.parse(fs.readFileSync(path.resolve(root,p), 'utf8'))), null, 2));
}

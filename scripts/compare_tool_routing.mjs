#!/usr/bin/env node
/** Sequential, reproducible UI comparisons. Never changes the live default. */
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.join(root, 'reports', `routing-comparison-${stamp}.json`);
const endpoint = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const report = {status: 'running', model, thinking: false, repetitions: 3, runs: [],
  coverage: '11 read conversation chains plus calendar/notes multi-delete; broader CRUD/mobile gate remains required',
  promotion_eligible: false};
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
fs.mkdirSync(path.dirname(reportPath), {recursive: true});
save();
async function preflight() {
  const response = await fetch(endpoint, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({model, messages: [{role: 'user', content: 'Reply OK.'}],
      temperature: 0, max_tokens: 8, stream: false,
      chat_template_kwargs: {enable_thinking: false}}),
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw Error(`Inference preflight HTTP ${response.status}`);
  const body = await response.json();
  if (!body.choices?.length) throw Error('Inference preflight returned no choices');
}
async function execute(script, env) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(root, 'scripts', script)], {
      cwd: root, env: {...process.env, ...env}, stdio: ['ignore', 'pipe', 'pipe'],
    });
    // Child artifacts are authoritative; do not copy private console output.
    child.stdout.resume(); child.stderr.resume();
    child.on('error', reject); child.on('exit', resolve);
  });
}
try {
  for (let repeat = 1; repeat <= 3; repeat++) {
    // Rotate ordering to reduce warm-cache/order bias. Run serially: mutation
    // snapshots must never race another test's fixture creation or cleanup.
    const modes = ['baseline', 'recent', 'all'];
    const order = modes.slice(repeat - 1).concat(modes.slice(0, repeat - 1));
    for (const mode of order) {
      await preflight();
      for (const suite of ['read', 'notes']) {
        const childPath = path.join(root, 'reports', `routing-${stamp}-${mode}-${repeat}-${suite}.json`);
        const code = await execute(suite === 'read'
          ? 'verify_interleaved_tool_followups.mjs' : 'verify_multi_note_delete_followup.mjs', {
          ROUTING_MODE: mode, REPORT_PATH: childPath,
          OWNER: suite === 'read' ? 'pewds' : 'sft_alex_creator', KEEP_SESSION: 'false',
        });
        const result = JSON.parse(fs.readFileSync(childPath, 'utf8'));
        report.runs.push({mode, repeat, suite, exit_code: code, status: result.status,
          summary: result.summary || null, report: path.relative(root, childPath)});
        save();
        if (result.chains?.some(c => c.infrastructure_failure) || /PRECONDITION|Timeout|ECONN/.test(result.error || '')) {
          throw Error(`Infrastructure failure in ${suite}; inspect ${childPath}`);
        }
      }
    }
  }
  report.status = 'measured';
} catch (error) {
  report.status = 'blocked'; report.blocker = String(error.message).slice(0, 500);
}
save();
console.log(JSON.stringify({report: reportPath, status: report.status, runs: report.runs.length}));
if (report.status !== 'measured') process.exitCode = 1;

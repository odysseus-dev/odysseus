#!/usr/bin/env node
/** Real 7011 native workspace read/write/execute follow-ups in isolated temp roots. */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/native-workspace-followups-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const selected = new Set((process.env.CASES || '').split(',').map(value => value.trim()).filter(Boolean));
let cases = [
  {
    name: 'grep-missing-recover', tools: ['grep', 'grep', 'grep'],
    fixture: ['search fixture.txt', 'Alpha\nalpha\nviolet-72\n'],
    prompts: [
      'Use grep to search /workspace/missing.txt for alpha. Report whether the search succeeded. Read only.',
      'Sorry, I meant /workspace/search fixture.txt. Search for the same pattern, case-sensitive. Read only.',
      'Now search that same file for the same pattern, but ignore case. Show both matching lines. Read only.',
    ],
    expectedFailures: [true, false, false],
    outputEvidence: [[], ['search fixture.txt:2:alpha'], ['search fixture.txt:1:Alpha', 'search fixture.txt:2:alpha']],
    answerCheck: (index, text) => index !== 0 || (/not found|does not exist|doesn't exist|missing|failed/i.test(text) && !/no matches/i.test(text)),
    validate: (index, args, workspace) => args.pattern === 'alpha'
      && (index === 0 ? ['/workspace/missing.txt', path.join(workspace, 'missing.txt')].includes(args.path)
        : ['/workspace/search fixture.txt', path.join(workspace, 'search fixture.txt')].includes(args.path))
      && (index === 2 ? args.ignore_case === true : !args.ignore_case),
    verifyTurn: workspace => fs.readFileSync(path.join(workspace, 'search fixture.txt'), 'utf8') === 'Alpha\nalpha\nviolet-72\n',
  },
  {
    name: 'file-edit-undo', tools: ['edit_file', 'edit_file', 'read_file'],
    fixture: ['edit fixture.txt', 'First: alpha\r\nSecond: alpha\r\nKeep: violet-72\r\n'],
    prompts: [
      'Use edit_file on /workspace/edit fixture.txt to replace only Second: alpha with Second: beta. Preserve everything else, including line endings.',
      'Undo only that last change using edit_file on the same file. Preserve everything else.',
      'Read that same file using read_file and report its contents. Read only.',
    ],
    validate: (index, args, workspace) => ['/workspace/edit fixture.txt', path.join(workspace, 'edit fixture.txt')].includes(args.path) && (index === 2
      || (args.old_string === (index === 0 ? 'Second: alpha' : 'Second: beta')
        && args.new_string === (index === 0 ? 'Second: beta' : 'Second: alpha') && args.replace_all !== true)),
    verifyTurn: (workspace, index) => fs.readFileSync(path.join(workspace, 'edit fixture.txt'), 'utf8')
      === `First: alpha\r\nSecond: ${index === 0 ? 'beta' : 'alpha'}\r\nKeep: violet-72\r\n`,
    verify: workspace => fs.readFileSync(path.join(workspace, 'edit fixture.txt'), 'utf8')
      === 'First: alpha\r\nSecond: alpha\r\nKeep: violet-72\r\n',
  },
  {
    name: 'glob-grep-read', tools: ['glob', 'grep', 'read_file'],
    fixture: ['search fixture.txt', 'alpha\nbeta\nviolet-72\n'],
    prompts: [
      'Use glob to find the *.txt files in /workspace. Read only.',
      'Use grep to find violet-72 in that file. Show the matching line. Read only.',
      'Use read_file on that same file to show only its second line. Read only.',
    ],
    expectedAnswers: ['search fixture.txt', 'violet-72', 'beta'],
    validate: (index, args, workspace) => index === 0
      ? typeof args.pattern === 'string' && args.pattern.includes('*.txt')
      : index === 1 ? args.pattern === 'violet-72'
      : ['/workspace/search fixture.txt', path.join(workspace, 'search fixture.txt')].includes(args.path)
        && args.offset === 2 && args.limit === 1,
    verifyTurn: workspace => fs.readFileSync(path.join(workspace, 'search fixture.txt'), 'utf8') === 'alpha\nbeta\nviolet-72\n',
  },
  {
    name: 'two-output-followup', tools: ['python', 'read_file'],
    expectedAnswers: [null, 'SECOND_OK'],
    prompts: [
      'Use python to create /workspace/first.v2.txt containing exactly FIRST_OK and /workspace/second.v2.txt containing exactly SECOND_OK. Do not add newlines.',
      'Use read_file to read the second file you created and report its exact contents. Read only.',
    ],
    validate: (index, args) => index === 0
      ? typeof args.code === 'string' && args.code.includes('first.v2.txt') && args.code.includes('second.v2.txt')
      : args.path === '/workspace/second.v2.txt',
    verify: workspace => fs.readFileSync(path.join(workspace, 'first.v2.txt'), 'utf8') === 'FIRST_OK'
      && fs.readFileSync(path.join(workspace, 'second.v2.txt'), 'utf8') === 'SECOND_OK',
  },
  {
    name: 'workspace-to-list', tools: ['get_workspace', 'ls'],
    prompts: [
      'Use get_workspace to inspect the current confined workspace. Read only.',
      'Now use ls to list that same workspace directory. Read only.',
    ],
    validate: (index, args) => index === 0
      ? Object.keys(args).length === 0
      : !args.path || ['/workspace', '.'].includes(args.path),
  },
  {
    name: 'file-read-refine', tools: ['read_file', 'read_file'],
    fixture: ['sample.txt', 'alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\n'],
    prompts: [
      'Use read_file to read the first three lines of /workspace/sample.txt. Read only.',
      'Read that same file again starting at line 4, returning at most two lines. Read only.',
    ],
    validate: (index, args) => args.path === '/workspace/sample.txt'
      && (index === 0 ? (args.offset == null || args.offset === 1) && args.limit === 3 : args.offset === 4 && args.limit === 2),
  },
  {
    name: 'write-then-read', tools: ['write_file', 'read_file'],
    prompts: [
      'Use write_file to create /workspace/followup.txt containing exactly NATIVE_WRITE_OK followed by a newline.',
      'Use read_file to read that same file and report its exact contents. Read only.',
    ],
    validate: (index, args) => index === 0
      ? args.path === '/workspace/followup.txt' && args.content === 'NATIVE_WRITE_OK\n'
      : args.path === '/workspace/followup.txt',
    verify: workspace => fs.readFileSync(path.join(workspace, 'followup.txt'), 'utf8') === 'NATIVE_WRITE_OK\n',
  },
  {
    name: 'python-then-read', tools: ['python', 'read_file'],
    prompts: [
      "Use python to write the exact text NATIVE_PYTHON_OK followed by a newline to /workspace/python-result.txt.",
      'Use read_file to read that generated file and report its exact contents. Read only.',
    ],
    validate: (index, args) => index === 0
      ? typeof args.code === 'string' && args.code.includes('python-result.txt') && args.code.includes('NATIVE_PYTHON_OK')
      : args.path === '/workspace/python-result.txt',
    verify: workspace => fs.readFileSync(path.join(workspace, 'python-result.txt'), 'utf8') === 'NATIVE_PYTHON_OK\n',
  },
].filter(spec => !selected.size || selected.has(spec.name));
if (!cases.length) throw Error('No matching cases selected');

const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = {
  model, status: 'running', cases: [],
  privacy: 'Only synthetic fixture names, tool names, argument keys, effect booleans, and contract checks retained; no model answers or file contents.',
};
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };
const safeArgs = args => ({
  ...(typeof args.path === 'string' ? { path: args.path } : {}),
  ...(Number.isInteger(args.offset) ? { offset: args.offset } : {}),
  ...(Number.isInteger(args.limit) ? { limit: args.limit } : {}),
  ...(typeof args.content === 'string' ? {
    content_length: args.content.length,
    content_has_final_newline: args.content.endsWith('\n'),
  } : {}),
  ...(typeof args.code === 'string' ? {
    code_length: args.code.length,
    code_mentions_target: args.code.includes('python-result.txt'),
    code_mentions_marker: args.code.includes('NATIVE_PYTHON_OK'),
    code_uses_chr_10: /chr\s*\(\s*10\s*\)/.test(args.code),
  } : {}),
});

let browser, context;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), `odysseus-${spec.name}-`));
    if (spec.fixture) fs.writeFileSync(path.join(workspace, spec.fixture[0]), spec.fixture[1]);
    const result = { name: spec.name, expected_tools: spec.tools, turns: [], cleanup: { session: false, workspace: false }, status: 'running' };
    report.cases.push(result); save();
    let session = '';
    try {
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[native-workspace-followup] ${spec.name}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false', cwd: workspace,
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      const runtime = JSON.stringify({
        surface: 'odysseus-native', terminal_agent: true, unattended_mode: true,
        input_files: spec.fixture ? [`/workspace/${spec.fixture[0]}`] : [],
      });
      for (let index = 0; index < spec.prompts.length; index++) {
        const response = await context.request.post(`${base}/api/chat_stream`, { multipart: {
          message: spec.prompts[index], session, mode: 'agent', agent_prompt_mode: 'auto',
          selected_endpoint_id: endpointId, selected_endpoint_url: endpointUrl,
          selected_model: model, cwd: workspace, workspace,
          client_runtime_context: runtime,
        }, timeout: 180000 });
        const events = parseSSE(await response.text());
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const starts = events.filter(event => event.type === 'tool_start');
        const expected = spec.tools[index];
        const matchingStarts = starts.filter(event => event.tool === expected);
        const outputs = events.filter(event => event.type === 'tool_output' && event.tool === expected);
        const successfulOutputs = outputs.filter(event => !event.error && (event.exit_code == null || event.exit_code === 0));
        const args = parseArgs(matchingStarts[0]);
        const final = events.filter(event => event.type === 'final_response')
          .map(event => event.content || '').join('')
          || events.filter(event => typeof event.delta === 'string').map(event => event.delta).join('');
        const checks = {
          http_ok: response.ok(), clean_route: contract.selection_mode === 'clean_compact_v3_preview',
          native_workspace: contract.native_workspace === true,
          shell_family: (contract.active_capabilities || []).includes('shell_files')
            || (contract.native_workspace === true && (contract.offered || []).includes(expected)),
          expected_offered: (contract.offered || []).includes(expected),
          exactly_one_execution: starts.length === 1 && matchingStarts.length === 1,
          argument_contract: matchingStarts.length === 1 && spec.validate(index, args, workspace),
          expected_execution_outcome: spec.expectedFailures?.[index]
            ? outputs.length === 1 && (outputs[0].error || outputs[0].exit_code === 1)
            : successfulOutputs.length === 1,
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
          expected_answer: !spec.expectedAnswers?.[index] || final.includes(spec.expectedAnswers[index]),
          state_after_turn: !spec.verifyTurn || spec.verifyTurn(workspace, index),
          output_evidence: !spec.outputEvidence || spec.outputEvidence[index].every(value => outputs.some(event => String(event.output || '').includes(value))),
          answer_semantics: !spec.answerCheck || spec.answerCheck(index, final),
        };
        result.turns.push({
          index, expected_tool: expected, offered: (contract.offered || []).slice().sort(),
          tools: starts.map(event => event.tool), argument_keys: Object.keys(args).sort(),
          calls: starts.map(event => ({ tool: event.tool, round: event.round, args: safeArgs(parseArgs(event)) })),
          outputs: outputs.map(event => ({ round: event.round, error: event.error === true, exit_code: event.exit_code ?? null })),
          checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed',
        });
        save();
      }
      result.effect_verified = spec.verify ? spec.verify(workspace) : true;
      result.status = result.effect_verified && result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.error = String(error).split('\n')[0].slice(0, 400); result.status = 'failed';
    } finally {
      if (session) result.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      fs.rmSync(workspace, { recursive: true, force: true });
      result.cleanup.workspace = !fs.existsSync(workspace);
      if (!result.cleanup.session || !result.cleanup.workspace) result.status = 'failed';
      save();
    }
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (browser) await browser.close();
}
report.status = report.cases.length === cases.length && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: cases.length, turns: report.cases.reduce((sum, item) => sum + item.turns.length, 0) };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, failures: report.cases.filter(item => item.status !== 'passed') }));
if (report.status !== 'passed') process.exitCode = 1;

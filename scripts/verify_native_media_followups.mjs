#!/usr/bin/env node
/** Real 7011 native-workspace media tool follow-ups with synthetic/local fixtures. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const expectCleanRoute = process.env.EXPECT_CLEAN_ROUTE !== 'false';
const expectNativeContractMetadata = process.env.EXPECT_NATIVE_CONTRACT_METADATA !== 'false';
const seconds = value => {
  if (typeof value === 'number') return value;
  const text = String(value ?? '').trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(':').map(Number);
  if (parts.length === 3 && parts.every(Number.isFinite)) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return Number.NaN;
};
const workspacePath = value => path.posix.normalize(
  `/workspace/${String(value ?? '').replace(/^\/workspace\/?/, '').replace(/^\/+/, '')}`,
);
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/native-media-followups-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
let cases = [
  {
    name: 'inspect-video-refine', tool: 'inspect_media',
    workspace: (process.env.ODYSSEUS_WORKSPACE || '/workspace'),
    input: '/workspace/fixtures/commuter_drive.mp4',
    prompts: [
      'Inspect /workspace/fixtures/commuter_drive.mp4 with overview sampling and report the visible road scene. Read only.',
      'Inspect that same video again, focusing only on its first two seconds. Read only.',
    ],
    validate: (index, args) => workspacePath(args.path) === '/workspace/fixtures/commuter_drive.mp4'
      && (index === 0 ? args.sampling === 'overview' : seconds(args.start) <= 0.1 && seconds(args.end) >= 1.9 && seconds(args.end) <= 2.1),
  },
  {
    name: 'transcribe-audio-repeat', tool: 'transcribe_media',
    workspace: (process.env.ODYSSEUS_WORKSPACE || '/workspace'),
    input: '/workspace/jo.wav',
    prompts: [
      'Transcribe the speech in /workspace/jo.wav. Read only and do not create an output file.',
      'Transcribe that same audio again, this time requesting timestamped segments. Read only and do not create an output file.',
    ],
    validate: (_index, args) => workspacePath(args.path) === '/workspace/jo.wav' && !args.output_path,
  },
  {
    name: 'ocr-image-refine', tool: 'extract_text',
    workspace: (process.env.ODYSSEUS_WORKSPACE || '/workspace'),
    input: '/workspace/tests/fixtures/vl/quarterly-dashboard.png',
    prompts: [
      'Use local OCR to extract the exact visible text from /workspace/tests/fixtures/vl/quarterly-dashboard.png. Include text positions. Read only.',
      'Run OCR on that same image again, returning only numbers. Read only.',
    ],
    validate: (index, args) => workspacePath(args.path) === '/workspace/tests/fixtures/vl/quarterly-dashboard.png'
      && (index === 0 ? (args.mode || 'all') === 'all' : args.mode === 'numbers'),
  },
];
if (process.env.CASE) cases = cases.filter(spec => spec.name === process.env.CASE);
if (!cases.length) throw Error(`Unknown CASE ${process.env.CASE}`);
for (const spec of cases) {
  const hostInput = path.join(spec.workspace, spec.input.replace(/^\/workspace\//, ''));
  if (!fs.existsSync(hostInput)) throw Error(`Missing fixture for ${spec.name}`);
}
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { model, status: 'running', cases: [], privacy: 'Only local fixture basenames, tool names, argument keys, and boolean checks retained; no media, OCR text, transcripts, model answer, or tool output.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };

let browser, context;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const result = { name: spec.name, expected_tool: spec.tool, fixture: path.basename(spec.input), turns: [], cleanup: false, status: 'running' };
    report.cases.push(result); save();
    let session = '';
    try {
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[native-media-followup] ${spec.name}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false', cwd: spec.workspace,
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      const runtime = JSON.stringify({
        surface: 'odysseus-native', terminal_agent: true, unattended_mode: true,
        input_files: [spec.input],
      });
      for (let index = 0; index < spec.prompts.length; index++) {
        const response = await context.request.post(`${base}/api/chat_stream`, { multipart: {
          message: spec.prompts[index], session, mode: 'agent', agent_prompt_mode: 'auto',
          selected_endpoint_id: endpointId, selected_endpoint_url: endpointUrl,
          selected_model: model, cwd: spec.workspace, workspace: spec.workspace,
          client_runtime_context: runtime,
        }, timeout: 180000 });
        const events = parseSSE(await response.text());
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const starts = events.filter(event => event.type === 'tool_start');
        const outputs = events.filter(event => event.type === 'tool_output' && event.tool === spec.tool);
        const successfulOutputs = outputs.filter(
          event => event.error !== true && (event.exit_code == null || event.exit_code === 0),
        );
        const expected = starts.filter(event => event.tool === spec.tool);
        const args = parseArgs(expected[0]);
        const checks = {
          http_ok: response.ok(),
          clean_route: !expectCleanRoute || contract.selection_mode === 'clean_compact_v3_preview',
          native_workspace: !expectNativeContractMetadata || contract.native_workspace === true,
          expected_offered: !expectNativeContractMetadata || (contract.offered || []).includes(spec.tool),
          exactly_one_expected_call: starts.length === 1 && expected.length === 1,
          argument_contract: expected.length === 1 && spec.validate(index, args),
          exactly_one_successful_output: successfulOutputs.length === 1,
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        };
        const safe_arguments = Object.fromEntries(
          Object.entries(args).filter(([key]) => ['path', 'mode', 'include_layout', 'sampling', 'start', 'end'].includes(key)),
        );
        result.turns.push({
          index,
          offered: (contract.offered || []).slice().sort(),
          tools: starts.map(event => event.tool),
          output_events: events.filter(event => event.type === 'tool_output').map(event => ({
            tool: event.tool,
            error: event.error === true,
            exit_code: event.exit_code ?? null,
          })),
          argument_keys: Object.keys(args).sort(),
          safe_arguments,
          checks,
          status: Object.values(checks).every(Boolean) ? 'passed' : 'failed',
        });
        save();
      }
      result.status = result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.error = String(error).split('\n')[0].slice(0, 400); result.status = 'failed';
    } finally {
      if (session) result.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      if (!result.cleanup) result.status = 'failed';
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

#!/usr/bin/env node
/** Real 7011 web subtool follow-ups with public fixtures and sanitized reports. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const routingMode = 'recent_model_choice';
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/web-subtool-followups-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const selected = new Set((process.env.CASES || '').split(',').map(value => value.trim()).filter(Boolean));
const youtubeUrl = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
const pdfUrl = 'https://arxiv.org/pdf/1706.03762';
const cases = [
  { name: 'hf-search-refine', tool: 'search_hf_models', prompts: [
    'Search Hugging Face for official Qwen 3.5 models. Return at most three repo IDs. Read only.',
    'Search those again, but narrow it to 9B models. Read only.',
  ], evidence: (index, output) => /Qwen\/[^\s"\\]*Qwen/i.test(output) && (index === 0 || /9B/i.test(output)),
  validate: (index, args) => index === 0
    ? typeof args.query === 'string' && /qwen/i.test(args.query) && args.official_only === true
    : typeof args.query === 'string' && /9b/i.test(args.query) },
  { name: 'youtube-metadata-transcript', tool: 'youtube_tool', prompts: [
    `Use the YouTube tool to get metadata for ${youtubeUrl}.`,
    'Use its transcript to summarize the topic in one sentence, without quoting it.',
  ], evidence: (index, output) => index === 0 ? /Rick Astley/i.test(output) : /never gonna|strangers to love/i.test(output),
  validate: (index, args) => index === 0
    ? args.action === 'metadata' && [args.url, args.video_url].includes(youtubeUrl)
    : args.action === 'transcript' && ([args.url, args.video_url].includes(youtubeUrl) || args.video_id === 'dQw4w9WgXcQ') },
  { name: 'pdf-focused-repeat', tool: 'pdf_extract', prompts: [
    `Extract the title and abstract from this PDF: ${pdfUrl}`,
    'From that same PDF, extract passages about positional encoding.',
  ], evidence: (index, output) => index === 0 ? /Attention Is All You Need/i.test(output) : /positional encod/i.test(output),
  validate: (index, args) => args.url === pdfUrl && typeof args.query === 'string' && args.query.trim().length > 0
    && (index === 0 || /position/i.test(args.query)) },
].filter(spec => !selected.size || selected.has(spec.name));
if (!cases.length) throw Error('No matching cases selected');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { model, status: 'running', cases: [], privacy: 'Only static public fixture names, called tool names, argument keys, and boolean checks retained; no result or answer text.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };

let browser, context, page;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const result = { name: spec.name, expected_tool: spec.tool, turns: [], cleanup: false, status: 'running' };
    report.cases.push(result); save();
    let session = '';
    try {
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[web-subtool-followup] ${spec.name}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      page = await context.newPage();
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      for (let index = 0; index < spec.prompts.length; index++) {
        const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
        await page.locator('textarea#message:visible').fill(spec.prompts[index]);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await waiting;
        const events = parseSSE(await response.text());
        await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const starts = events.filter(event => event.type === 'tool_start');
        const outputs = events.filter(event => event.type === 'tool_output');
        const expectedStarts = starts.filter(event => event.tool === spec.tool);
        const expectedOutputs = outputs.filter(event => event.tool === spec.tool);
        const args = parseArgs(expectedStarts[0]);
        const checks = {
          exact_runtime: contract.routing_experiment === routingMode,
          http_ok: response.ok(), clean_route: contract.selection_mode === 'clean_compact_v3_preview',
          search_capability: (contract.active_capabilities || []).includes('search_browser'),
          expected_offered: (contract.offered || []).includes(spec.tool),
          exactly_one_expected_call: starts.length === 1 && expectedStarts.length === 1,
          argument_contract: expectedStarts.length === 1 && spec.validate(index, args),
          exactly_one_successful_output: expectedOutputs.length === 1 && !expectedOutputs[0]?.error && (expectedOutputs[0]?.exit_code == null || expectedOutputs[0]?.exit_code === 0),
          returned_fixture_evidence: expectedOutputs.some(event => spec.evidence(index, String(event.output || ''))),
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        };
        result.turns.push({ index, tools: starts.map(event => event.tool), argument_keys: Object.keys(args).sort(), checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' });
      }
      result.status = result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.status = 'failed'; result.error = String(error).split('\n')[0].slice(0, 400);
    } finally {
      if (page) { await page.close(); page = null; }
      if (session) result.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      if (!result.cleanup) result.status = 'failed';
      save();
    }
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.cases.length === cases.length && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: cases.length, turns: report.cases.reduce((sum, item) => sum + item.turns.length, 0) };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, failures: report.cases.filter(item => item.status !== 'passed') }));
if (report.status !== 'passed') process.exitCode = 1;

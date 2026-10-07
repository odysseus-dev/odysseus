#!/usr/bin/env node
/** Real 7011 historical-chat search followed by a refined search. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/search-chats-followup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const prompts = [
  'Search my past chats for the phrase tool grounding. Return at most three clickable chat titles. Read only.',
  'Search those past chats again, but narrow the query to tool evidence. Return at most three clickable chat titles. Read only.',
];
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { model, status: 'running', turns: [], cleanup: false, privacy: 'No chat titles, transcript matches, result text, or answer text retained.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };

let browser, context, page, session = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: '[search-chats-followup] refined-query', model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  for (let index = 0; index < prompts.length; index++) {
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompts[index]);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
    const contract = events.find(event => event.type === 'turn_contract') || {};
    const starts = events.filter(event => event.type === 'tool_start');
    const outputs = events.filter(event => event.type === 'tool_output' && event.tool === 'search_chats');
    const expected = starts.filter(event => event.tool === 'search_chats');
    const args = parseArgs(expected[0]);
    const checks = {
      http_ok: response.ok(),
      clean_route: contract.selection_mode === 'clean_compact_v3_preview',
      model_choice_route: contract.routing_experiment === 'recent_model_choice',
      memory_capability: (contract.active_capabilities || []).includes('memory'),
      expected_offered: (contract.offered || []).includes('search_chats'),
      exactly_one_expected_call: starts.length === 1 && expected.length === 1,
      argument_contract: typeof args.query === 'string' && (index === 0 ? /grounding/i.test(args.query) : /evidence/i.test(args.query)),
      exactly_one_successful_output: outputs.length === 1 && !outputs[0]?.error && (outputs[0]?.exit_code == null || outputs[0]?.exit_code === 0),
      no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
    };
    report.turns.push({ index, tools: starts.map(event => event.tool), argument_keys: Object.keys(args).sort(), checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' });
    save();
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (context && session) report.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  if (browser) await browser.close();
}
report.status = report.turns.length === prompts.length && report.turns.every(turn => turn.status === 'passed') && report.cleanup ? 'passed' : 'failed';
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, turns: report.turns, cleanup: report.cleanup }));
if (report.status !== 'passed') process.exitCode = 1;

#!/usr/bin/env node
/** Real 7011 Agent UI replay for opening and switching visible tool panels. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = process.env.OWNER || 'sft_alex_creator';
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/ui-panel-followups-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const turns = [
  { prompt: 'Open gallery.', capability: 'ui', panel: '#gallery-modal' },
  { prompt: 'Now open documents.', capability: 'ui', panel: '#doclib-modal' },
  { prompt: 'Go back and open the gallery again.', capability: 'ui', panel: '#gallery-modal' },
  { prompt: 'Open my calendar.', capability: 'ui', panel: '#calendar-modal' },
  { prompt: 'Return to documents.', capability: 'ui', panel: '#doclib-modal' },
];
const report = { run, owner, model, endpoint_id: endpointId, status: 'running', turns: [], cleanup: {}, privacy: 'Static prompts and boolean UI checks only.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const bare = value => String(value || '').replace(/^mcp__[^_]+__/, '');

let browser, context, page, session = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[ui-panel-followups] ${run}`, model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();

  for (const spec of turns) {
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    const composer = page.locator('textarea#message:visible');
    await composer.fill(spec.prompt);
    await composer.press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    const contract = events.find(event => event.type === 'turn_contract') || {};
    const starts = events.filter(event => event.type === 'tool_start').map(event => bare(event.tool));
    const outputs = events.filter(event => event.type === 'tool_output').map(event => ({ tool: bare(event.tool), ok: !event.error && (event.exit_code == null || event.exit_code === 0) }));
    await page.locator(spec.panel).waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
    const visible = await page.locator(spec.panel).isVisible().catch(() => false);
    const checks = {
      http_ok: response.ok(),
      clean_route: contract.selection_mode === 'clean_compact_v3_preview',
      ui_capability: (contract.active_capabilities || contract.capabilities || []).includes(spec.capability),
      ui_control_called: starts.includes('ui_control'),
      ui_control_succeeded: outputs.some(item => item.tool === 'ui_control' && item.ok),
      requested_panel_visible: visible,
      no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
    };
    report.turns.push({ prompt: spec.prompt, panel: spec.panel, tools: starts, checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' });
    save();
    if (visible) {
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);
    }
  }
  report.status = report.turns.length === turns.length && report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (session && context) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  if (browser) await browser.close();
  if (!report.cleanup.session) report.status = 'failed';
  save();
}
report.summary = { passed: report.turns.filter(turn => turn.status === 'passed').length, total: turns.length };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, turns: report.turns.map(turn => ({ prompt: turn.prompt, status: turn.status, checks: turn.checks })) }));
if (report.status !== 'passed') process.exitCode = 1;

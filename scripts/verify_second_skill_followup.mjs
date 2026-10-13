#!/usr/bin/env node
/** Real 7011 skills list -> view second listed skill replay (read-only). */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = process.env.OWNER || 'sft_alex_creator';
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/second-skill-followup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const digest = value => crypto.createHash('sha256').update(String(value)).digest('hex').slice(0, 16);
const report = { model, status: 'running', turns: [], cleanup: false, privacy: 'No skill names or contents are retained; only hashes and boolean checks.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const unwrap = raw => {
  let value = String(raw || '');
  for (let i = 0; i < 3; i++) {
    try {
      const parsed = JSON.parse(value);
      const nested = parsed && typeof parsed === 'object' && ['stdout', 'results', 'response', 'output', 'content'].map(key => parsed[key]).find(item => typeof item === 'string' && item.trim());
      if (!nested) break;
      value = nested;
    } catch { break; }
  }
  return value;
};
const parseArgs = event => {
  try { return JSON.parse(event?.command || '{}'); } catch { return {}; }
};

let browser, context, page, session = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: '[second-skill-followup] read-only', model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  const send = async prompt => {
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
    return { response, events, contract: events.find(event => event.type === 'turn_contract') || {} };
  };

  const listed = await send('List my first three skills. Preserve their exact names and order. Read only.');
  const listStarts = listed.events.filter(event => event.type === 'tool_start');
  const listOutputs = listed.events.filter(event => event.type === 'tool_output');
  const listText = listOutputs.map(event => unwrap(event.output)).join('\n');
  const names = [...listText.matchAll(/^- \*\*([^*]+)\*\*/gm)].map(match => match[1].trim());
  const target = names[1] || '';
  report.list_diagnostics = { characters: listText.length, lines: listText.split('\n').length,
    reports_empty: /No skills yet/i.test(listText),
    bullet_names: names.length, json_shaped: listText.trim().startsWith('{') };
  report.turns.push({ name: 'list', target_hash: target ? digest(target) : null, item_count: names.length, checks: {
    http_ok: listed.response.ok(), skills_capability: (listed.contract.active_capabilities || []).includes('skills'),
    exactly_one_list_call: listStarts.length === 1 && listStarts[0]?.tool === 'manage_skills' && parseArgs(listStarts[0]).action === 'list',
    exactly_one_successful_output: listOutputs.length === 1 && !listOutputs[0]?.error && (listOutputs[0]?.exit_code == null || listOutputs[0]?.exit_code === 0),
    at_least_two_items: names.length >= 2, no_stream_error: !listed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});

  const viewed = await send('Show the second skill from that list. Read only.');
  const viewStarts = viewed.events.filter(event => event.type === 'tool_start');
  const viewOutputs = viewed.events.filter(event => event.type === 'tool_output');
  const viewArgs = parseArgs(viewStarts[0]);
  report.turns.push({ name: 'view-second', target_hash: target ? digest(target) : null, called_name_hash: viewArgs.name ? digest(viewArgs.name) : null, checks: {
    target_resolved: !!target, http_ok: viewed.response.ok(), skills_capability: (viewed.contract.active_capabilities || []).includes('skills'),
    exactly_one_view_call: viewStarts.length === 1 && viewStarts[0]?.tool === 'manage_skills' && viewArgs.action === 'view',
    exact_second_skill: !!target && viewArgs.name === target,
    exactly_one_successful_output: viewOutputs.length === 1 && !viewOutputs[0]?.error && (viewOutputs[0]?.exit_code == null || viewOutputs[0]?.exit_code === 0),
    content_returned: unwrap(viewOutputs[0]?.output).trim().length > 0,
    no_stream_error: !viewed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  for (const turn of report.turns) turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
  report.status = report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context && session) report.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  if (browser) await browser.close();
  if (!report.cleanup) report.status = 'failed';
  save();
}
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, turns: report.turns }));
if (report.status !== 'passed') process.exitCode = 1;

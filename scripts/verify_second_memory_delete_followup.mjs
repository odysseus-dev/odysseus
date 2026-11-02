#!/usr/bin/env node
/** Real 7011 memory search -> forget the second result replay. */
import crypto from 'node:crypto';
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
const marker = `ody-memory-second-${crypto.randomUUID()}`;
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/second-memory-delete-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { marker, model, status: 'running', turns: [], cleanup: {}, privacy: 'Static synthetic memory identifiers/text and boolean checks only.' };
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
      const nested = parsed && typeof parsed === 'object' && ['results', 'response', 'output', 'content', 'stdout'].map(key => parsed[key]).find(item => typeof item === 'string' && item.trim());
      if (!nested) break;
      value = nested;
    } catch { break; }
  }
  return value;
};

let browser, context, page, session = '';
const memoryIds = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const sessionResponse = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[second-memory-delete] ${marker}`, model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!sessionResponse.ok()) throw Error(`Session create HTTP ${sessionResponse.status()}`);
  session = (await sessionResponse.json()).id;
  for (const suffix of ['alpha', 'beta']) {
    const response = await context.request.post(`${base}/api/memory/add`, { data: {
      text: `${marker} ${suffix}`, category: 'fact', source: 'eval', session_id: session,
    }});
    if (!response.ok()) throw Error(`Memory create HTTP ${response.status()}`);
  }
  const allBefore = (await (await context.request.get(`${base}/api/memory`)).json()).memory || [];
  memoryIds.push(...allBefore.filter(item => String(item.text || '').startsWith(marker)).map(item => item.id));
  if (memoryIds.length !== 2) throw Error(`Expected two synthetic memories, found ${memoryIds.length}`);

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
  const listed = await send(`Search my memories for ${marker}. List all matches.`);
  const searchStarts = listed.events.filter(event => event.type === 'tool_start');
  const searchOutputs = listed.events.filter(event => event.type === 'tool_output');
  const output = listed.events.filter(event => event.type === 'tool_output').map(event => unwrap(event.output)).join('\n');
  const orderedPrefixes = [...output.matchAll(/`([0-9a-f]{8})`/ig)].map(match => match[1]);
  const target = orderedPrefixes[1] ? (memoryIds.find(id => id.startsWith(orderedPrefixes[1])) || '') : '';
  report.turns.push({ name: 'search', contract: listed.contract, tool_events: listed.events.filter(event => ['tool_start', 'tool_output'].includes(event.type)).map(event => ({ type: event.type, tool: event.tool, command: event.command, output: event.output, exit_code: event.exit_code, error: event.error })), ordered_prefixes: orderedPrefixes, checks: {
    http_ok: listed.response.ok(), memory_capability: (listed.contract.active_capabilities || []).includes('memory'),
    exact_runtime: listed.contract.routing_experiment === routingMode,
    exactly_one_memory_call: searchStarts.length === 1 && searchStarts[0]?.tool === 'manage_memory',
    exactly_one_successful_output: searchOutputs.length === 1 && searchOutputs[0]?.tool === 'manage_memory' && !searchOutputs[0]?.error && (searchOutputs[0]?.exit_code == null || searchOutputs[0]?.exit_code === 0),
    both_memories_listed: orderedPrefixes.filter(prefix => memoryIds.some(id => id.startsWith(prefix))).length === 2,
    no_stream_error: !listed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  if (!target || orderedPrefixes.length !== 2 || !orderedPrefixes.every(prefix => memoryIds.some(id => id.startsWith(prefix)))) {
    throw Error('PRECONDITION: list did not resolve exactly the two disposable memories; deletion not attempted');
  }
  const removed = await send('Forget the second memory from that list.');
  const starts = removed.events.filter(event => event.type === 'tool_start');
  const outputs = removed.events.filter(event => event.type === 'tool_output');
  const after = (await (await context.request.get(`${base}/api/memory`)).json()).memory || [];
  report.turns.push({ name: 'delete-second', contract: removed.contract, target_id: target, tools: starts.map(event => event.tool), tool_events: removed.events.filter(event => ['tool_start', 'tool_output'].includes(event.type)).map(event => ({ type: event.type, tool: event.tool, command: event.command, output: event.output, exit_code: event.exit_code, error: event.error })), checks: {
    target_resolved: !!target, http_ok: removed.response.ok(), memory_capability: (removed.contract.active_capabilities || []).includes('memory'),
    exact_runtime: removed.contract.routing_experiment === routingMode,
    exact_memory_delete: !!target && starts.some(event => event.tool === 'manage_memory' && /delete/i.test(event.command || '') && String(event.command || '').includes(target.slice(0, 8))),
    delete_succeeded: outputs.some(event => event.tool === 'manage_memory' && !event.error && (event.exit_code == null || event.exit_code === 0)),
    second_memory_deleted: !!target && !after.some(item => item.id === target),
    other_memory_preserved: memoryIds.filter(id => id !== target).every(id => after.some(item => item.id === id)),
    no_stream_error: !removed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  for (const turn of report.turns) turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
  report.status = report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context) {
    for (const id of memoryIds) {
      const response = await context.request.delete(`${base}/api/memory/${encodeURIComponent(id)}`);
      report.cleanup[`memory:${id}`] = response.ok() || response.status() === 404;
    }
    if (session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  }
  if (browser) await browser.close();
  if (Object.values(report.cleanup).some(value => !value)) report.status = 'failed';
  save();
}
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, turns: report.turns }));
if (report.status !== 'passed') process.exitCode = 1;

#!/usr/bin/env node
/** Real 7011 document list -> read the second result replay. */
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
const marker = `ody-doc-second-${crypto.randomUUID()}`;
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/second-document-read-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { marker, model, status: 'running', turns: [], cleanup: {}, privacy: 'Static synthetic document identifiers/content and boolean checks only.' };
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
      const nested = parsed && typeof parsed === 'object' && ['results', 'response', 'output', 'content'].map(key => parsed[key]).find(item => typeof item === 'string' && item.trim());
      if (!nested) break;
      value = nested;
    } catch { break; }
  }
  return value;
};

let browser, context, page, session = '';
const docs = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const sessionResponse = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[second-document-read] ${marker}`, model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!sessionResponse.ok()) throw Error(`Session create HTTP ${sessionResponse.status()}`);
  session = (await sessionResponse.json()).id;
  for (const [suffix, code] of [['alpha', 'ALPHA-731'], ['beta', 'BETA-924']]) {
    const response = await context.request.post(`${base}/api/document`, { data: {
      session_id: session, title: `${marker}-${suffix}`, language: 'markdown', content: `# Synthetic fixture\n\nVerification code: ${code}\n`,
    }});
    if (!response.ok()) throw Error(`Document create HTTP ${response.status()}`);
    docs.push({ id: (await response.json()).id, suffix, code });
  }
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
  const listed = await send(`List documents containing ${marker}.`);
  const output = listed.events.filter(event => event.type === 'tool_output').map(event => unwrap(event.output)).join('\n');
  const orderedIds = [...output.matchAll(/#document-([0-9a-f-]{36})/ig)].map(match => match[1]);
  const target = docs.find(doc => doc.id === orderedIds[1]);
  report.turns.push({ name: 'list', ordered_ids: orderedIds, checks: {
    http_ok: listed.response.ok(), documents_capability: (listed.contract.active_capabilities || []).includes('documents'),
    model_choice_route: listed.contract.routing_experiment === 'recent_model_choice',
    both_documents_listed: orderedIds.filter(id => docs.some(doc => doc.id === id)).length === 2,
    no_stream_error: !listed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  if (!target || orderedIds.length !== 2) throw Error('PRECONDITION: exact two-document list required before ordinal replay');
  const read = await send('Read the second document from that list. What is its verification code?');
  const starts = read.events.filter(event => event.type === 'tool_start');
  const outputs = read.events.filter(event => event.type === 'tool_output');
  const visible = await page.locator('#chat-history .msg-ai').last().innerText().catch(() => '');
  report.turns.push({ name: 'read-second', target_id: target?.id || '', tools: starts.map(event => event.tool), checks: {
    target_resolved: !!target, http_ok: read.response.ok(), documents_capability: (read.contract.active_capabilities || []).includes('documents'),
    model_choice_route: read.contract.routing_experiment === 'recent_model_choice',
    exact_document_read: !!target && starts.some(event => event.tool === 'manage_documents' && String(event.command || '').includes(target.id) && /"action"\s*:\s*"(?:read|view|open|get)"/i.test(event.command || '')),
    read_succeeded: outputs.some(event => event.tool === 'manage_documents' && !event.error && (event.exit_code == null || event.exit_code === 0)),
    exact_code_answered: !!target && visible.includes(target.code),
    neighboring_code_absent: !!target && docs.filter(doc => doc.id !== target.id).every(doc => !visible.includes(doc.code)),
    no_stream_error: !read.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  for (const turn of report.turns) turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
  report.status = report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context) {
    for (const doc of docs) {
      const response = await context.request.delete(`${base}/api/document/${encodeURIComponent(doc.id)}`);
      report.cleanup[`document:${doc.id}`] = response.ok() || response.status() === 404;
    }
    if (session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  }
  if (browser) await browser.close();
  if (Object.values(report.cleanup).some(value => !value)) report.status = 'failed';
  save();
}
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, turns: report.turns }));
if (report.status !== 'passed') process.exitCode = 1;

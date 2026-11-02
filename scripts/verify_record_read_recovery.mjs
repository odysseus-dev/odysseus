#!/usr/bin/env node
/** Real Agent UI: missing record -> corrected identity -> evidence-only recall. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const model = 'odysseus-qwen3.5-tools-pre-heretic';
const selected = new Set((process.env.FAMILIES || 'notes,documents').split(','));
const specs = [
  {family: 'notes', noun: 'note', tool: 'manage_notes', api: '/api/notes', bodyKey: 'content', actions: ['view', 'read', 'get']},
  {family: 'documents', noun: 'document', tool: 'manage_documents', api: '/api/document', bodyKey: 'current_content', actions: ['read', 'view', 'open', 'get']},
].filter(s => selected.has(s.family));
if (!specs.length) throw Error('No matching families');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, v]) => v?.username === owner)?.[0];
if (!token) throw Error('No SFT session');
const reportPath = path.join(root, `reports/record-read-recovery-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
const report = {status: 'running', model, routing: 'recent_model_choice', cases: [],
  privacy: 'Disposable SFT records only. No raw account data, answers, IDs, or authentication retained.'};
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const data = frame.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
  if (!data || data === '[DONE]') return [];
  try { return [JSON.parse(data)]; } catch { return [{type: 'invalid_sse'}]; }
});
const argsOf = e => { try { return JSON.parse(e.command || '{}'); } catch { return {}; } };
let browser, context;
try {
  browser = await chromium.launch({headless: true, args: ['--no-proxy-server']});
  context = await browser.newContext({serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': 'recent_model_choice',
  }});
  await context.addCookies([{name: 'odysseus_session', value: token, url: base}]);
  const session = async label => {
    const response = await context.request.post(`${base}/api/session`, {multipart: {
      name: `[record-read-recovery] ${label}`, model, endpoint_id: '1d1022ef',
      endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(), skip_validation: 'true', rag: 'false',
    }});
    if (!response.ok()) throw Error(`Session create HTTP ${response.status()}`);
    return (await response.json()).id;
  };
  for (const spec of specs) {
    const result = {family: spec.family, status: 'running', turns: [], cleanup: {record: false, sessions: false}};
    report.cases.push(result); save();
    const title = `read-fixture-${crypto.randomUUID()}`;
    const code = `amber-${crypto.randomUUID().slice(0, 8)}`;
    const delay = crypto.randomInt(17, 48);
    const body = `Recovery code: ${code}\nRetry delay: ${delay} seconds.\nMaximum attempts: 6.`;
    const missing = crypto.randomUUID();
    let id, page, seededSession, chat;
    let sourceVerified = false;
    try {
      seededSession = await session('fixture storage');
      const created = await context.request.post(`${base}${spec.api}`, {data: {
        title, content: body, session_id: seededSession,
        ...(spec.family === 'documents' ? {language: 'markdown'} : {source: 'eval'}),
      }});
      if (!created.ok()) throw Error(`Fixture create HTTP ${created.status()}`);
      id = (await created.json()).id;
      const original = await (await context.request.get(`${base}${spec.api}/${id}`)).json();
      if (original.title !== title || original[spec.bodyKey] !== body) throw Error('Fixture mismatch');
      if ((await context.request.get(`${base}${spec.api}/${missing}`)).status() !== 404) throw Error('Missing-ID precondition failed');
      chat = await session('read and recover');
      page = await context.newPage();
      await page.goto(`${base}/#${chat}`, {waitUntil: 'domcontentloaded'});
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, chat, {timeout: 30000});
      if (await page.locator('#mode-agent-btn').getAttribute('aria-pressed') !== 'true') await page.locator('#mode-agent-btn').click();
      const prompts = [
        `Read my ${spec.noun} with ID ${missing}. Tell me whether it exists. Do not create anything or substitute another record.`,
        `Sorry, I meant ID ${id}. Read that one and tell me its recovery code and retry delay.`,
        'How many seconds was the delay? Answer from what you just read; do not change or rerun anything.',
      ];
      for (let index = 0; index < prompts.length; index++) {
        const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', {timeout: 120000});
        await page.locator('textarea#message:visible').fill(prompts[index]);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await waiting;
        const submitted = response.request().postData() || '';
        const events = parseSSE(await response.text());
        await page.waitForFunction(() => !document.querySelector('#chat-history .streaming'), null, {timeout: 15000}).catch(() => {});
        const contract = events.find(e => e.type === 'turn_contract') || {};
        const starts = events.filter(e => e.type === 'tool_start');
        const outputs = events.filter(e => e.type === 'tool_output');
        const args = argsOf(starts[0] || {});
        const target = args.id || args.document_id || args.uid || args.note_id;
        const final = events.filter(e => e.type === 'final_response').map(e => e.content || '').join('')
          || events.filter(e => typeof e.delta === 'string').map(e => e.delta).join('');
        const displayed = await page.locator('#chat-history .msg-ai .stream-content').last().innerText({timeout: 5000}).catch(() => '');
        const matches = text => index === 0 ? /not found|does(?:n.t| not) exist|could(?:n.t| not) find|no .*found|unable to find/i.test(text)
          : index === 1 ? text.includes(code) && new RegExp(`\\b${delay}\\b`).test(text)
          : new RegExp(`\\b${delay}\\s*(?:seconds|s\\b)`, 'i').test(text);
        if (index === 1) sourceVerified = outputs.some(e => e.tool === spec.tool && !e.error && String(e.output).includes(code) && String(e.output).includes(String(delay)));
        const state = await (await context.request.get(`${base}${spec.api}/${id}`)).json();
        const checks = {
          http_ok: response.ok(), clean_route: contract.selection_mode === 'clean_compact_v3_preview',
          model_choice: contract.routing_experiment === 'recent_model_choice',
          no_injected_fixture_answer: !submitted.includes(code),
          offered: index === 2 || (contract.offered || []).includes(spec.tool),
          exact_call: index === 2 ? starts.length === 0 : starts.length === 1 && starts[0].tool === spec.tool
            && spec.actions.includes(args.action) && target === (index === 0 ? missing : id),
          execution_outcome: index === 2 ? outputs.length === 0 : outputs.length === 1
            && (index === 0 ? outputs[0].error === true && outputs[0].execution_attempted === true && outputs[0].blocked === false
              : !outputs[0].error && outputs[0].exit_code === 0),
          grounded_source: index === 0 || sourceVerified,
          final_evidence: matches(final), rendered_evidence: matches(displayed),
          unchanged_record: state.title === title && state[spec.bodyKey] === body,
          no_stream_error: !events.some(e => ['error', 'invalid_sse'].includes(e.type)),
        };
        result.turns.push({index, tools: starts.map(e => e.tool), argument_keys: Object.keys(args), checks,
          status: Object.values(checks).every(Boolean) ? 'passed' : 'failed'}); save();
      }
      result.status = result.turns.every(t => t.status === 'passed') ? 'passed' : 'failed';
    } catch (error) { result.status = 'failed'; result.error = String(error).split('\n')[0].slice(0, 300); }
    finally {
      if (page) await page.close();
      try {
        if (id) {
          const row = await (await context.request.get(`${base}${spec.api}/${id}`)).json();
          if (row.title !== title) throw Error('Refuse non-fixture cleanup');
          const deleted = await context.request.delete(`${base}${spec.api}/${id}`);
          if (!deleted.ok()) throw Error('Fixture cleanup failed');
          const checked = await context.request.get(`${base}${spec.api}/${id}`);
          result.cleanup.record = checked.status() === 404 || (spec.family === 'documents' && (await checked.json()).is_active === false);
        }
        result.cleanup.sessions = true;
        for (const sid of [chat, seededSession].filter(Boolean)) {
          if (!(await context.request.delete(`${base}/api/session/${sid}`)).ok()) result.cleanup.sessions = false;
        }
      } catch { result.cleanup.error = true; }
      if (!result.cleanup.record || !result.cleanup.sessions) result.status = 'failed';
      save();
    }
  }
} catch (error) { report.error = String(error).split('\n')[0].slice(0, 300); }
finally { if (browser) await browser.close(); }
report.status = report.cases.length === specs.length && report.cases.every(c => c.status === 'passed') ? 'passed' : 'failed';
save();
console.log(JSON.stringify({report: path.relative(root, reportPath), status: report.status, cases: report.cases}));
if (report.status !== 'passed') process.exitCode = 1;

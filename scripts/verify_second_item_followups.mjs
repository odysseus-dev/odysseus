#!/usr/bin/env node
/** Real 7011 followups that mutate the second item from a synthetic list. */
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
const marker = `ody-second-${crypto.randomUUID()}`;
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/second-item-followups-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { run, marker, owner, model, status: 'running', cases: [], cleanup: {}, privacy: 'Synthetic fixture IDs, static prompts, tool names, and boolean checks only.' };
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
      if (!parsed || typeof parsed !== 'object') break;
      const nested = ['results', 'response', 'output', 'content'].map(key => parsed[key]).find(item => typeof item === 'string' && item.trim());
      if (!nested) break;
      value = nested;
    } catch { break; }
  }
  return value;
};

let browser, context;
const sessions = [], taskIds = [], eventIds = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const family of ['tasks', 'calendar']) {
    const sessionResponse = await context.request.post(`${base}/api/session`, { multipart: {
      name: `[second-item] ${family}-${marker}`, model, endpoint_id: endpointId,
      endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
    }});
    if (!sessionResponse.ok()) throw Error(`${family} session create HTTP ${sessionResponse.status()}`);
    const session = (await sessionResponse.json()).id; sessions.push(session);
    let seeded = [];
    if (family === 'tasks') {
      for (const suffix of ['alpha', 'beta']) {
        const response = await context.request.post(`${base}/api/tasks`, { data: {
          name: `${marker}-${suffix}`, prompt: `Synthetic ${suffix} task`, task_type: 'llm', schedule: 'daily', scheduled_time: suffix === 'alpha' ? '08:00' : '09:00',
        }});
        if (!response.ok()) throw Error(`Task create HTTP ${response.status()}`);
        const body = await response.json(); taskIds.push(body.id || body.task?.id); seeded.push(body.id || body.task?.id);
      }
    } else {
      for (const [suffix, hour] of [['alpha', '10'], ['beta', '12']]) {
        const response = await context.request.post(`${base}/api/calendar/events`, { data: {
          summary: `${marker}-${suffix}`, dtstart: `2030-02-01T${hour}:00:00Z`, dtend: `2030-02-01T${Number(hour) + 1}:00:00Z`, description: `Synthetic ${suffix} event`,
        }});
        if (!response.ok()) throw Error(`Event create HTTP ${response.status()}`);
        const id = (await response.json()).uid; eventIds.push(id); seeded.push(id);
      }
    }

    const page = await context.newPage();
    const item = { family, status: 'running', turns: [], seeded };
    report.cases.push(item); save();
    try {
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
      const listPrompt = family === 'tasks'
        ? `List scheduled tasks containing ${marker}.`
        : `List calendar events from 2030-02-01 through 2030-02-02 containing ${marker}.`;
      const listed = await send(listPrompt);
      const listOutput = listed.events.filter(event => event.type === 'tool_output').map(event => unwrap(event.output)).join('\n');
      const ordered = family === 'tasks'
        ? [...listOutput.matchAll(/\(([0-9a-f-]{36})\)\s+—/ig)].map(match => match[1])
        : [...listOutput.matchAll(/#event-([0-9a-f-]{36})/ig)].map(match => match[1]);
      const target = ordered[1] || '';
      const expectedSet = new Set(seeded);
      item.turns.push({ name: 'list', output: listOutput, tool_events: listed.events.filter(event => ['tool_start', 'tool_output'].includes(event.type)).map(event => ({ type: event.type, tool: event.tool, command: event.command, output: event.output, exit_code: event.exit_code })), ordered_ids: ordered, checks: {
        http_ok: listed.response.ok(), correct_capability: (listed.contract.active_capabilities || []).includes(family),
        both_synthetic_items_listed: ordered.filter(id => expectedSet.has(id)).length === 2,
        no_stream_error: !listed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
      }});
      const removed = await send(`Delete the second ${family === 'tasks' ? 'task' : 'event'} from that list.`);
      const deleteOutputs = removed.events.filter(event => event.type === 'tool_output');
      const deleteStarts = removed.events.filter(event => event.type === 'tool_start');
      const mutationActions = new Set(['delete', 'delete_event', 'remove', 'cancel']);
      const pendingStarts = new Map();
      const successfulDeleteOutputs = [];
      for (const event of removed.events) {
        if (event.type === 'tool_start') {
          const queue = pendingStarts.get(event.tool) || [];
          queue.push(event);
          pendingStarts.set(event.tool, queue);
          continue;
        }
        if (event.type !== 'tool_output') continue;
        const start = (pendingStarts.get(event.tool) || []).shift();
        if (!start || event.error || (event.exit_code != null && event.exit_code !== 0)) continue;
        try {
          const command = typeof start.command === 'string' ? JSON.parse(start.command) : start.command;
          if (mutationActions.has(String(command?.action || '').toLowerCase())) {
            successfulDeleteOutputs.push(event);
          }
        } catch (_) {}
      }
      const remaining = [];
      for (const id of seeded) {
        const response = await context.request.get(`${base}${family === 'tasks' ? '/api/tasks/' : '/api/calendar/events/'}${encodeURIComponent(id)}`);
        if (response.ok()) remaining.push(id);
      }
      item.turns.push({ name: 'delete-second', target_id: target, tools: deleteStarts.map(event => event.tool), tool_events: removed.events.filter(event => ['tool_start', 'tool_output'].includes(event.type)).map(event => ({ type: event.type, tool: event.tool, command: event.command, output: event.output, exit_code: event.exit_code, error: event.error })), checks: {
        target_resolved: expectedSet.has(target), http_ok: removed.response.ok(),
        correct_capability: (removed.contract.active_capabilities || []).includes(family),
        one_successful_delete: successfulDeleteOutputs.length === 1,
        second_item_deleted: !!target && !remaining.includes(target),
        other_item_preserved: seeded.filter(id => id !== target).every(id => remaining.includes(id)),
        no_stream_error: !removed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
      }});
      for (const turn of item.turns) turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
      item.status = item.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      item.status = 'failed'; item.error = String(error).split('\n')[0].slice(0, 500);
    } finally {
      await page.close(); save();
    }
  }
  report.status = report.cases.length === 2 && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (context) {
    for (const id of taskIds.filter(Boolean)) {
      const response = await context.request.delete(`${base}/api/tasks/${encodeURIComponent(id)}`);
      report.cleanup[`task:${id}`] = response.ok() || response.status() === 404;
    }
    for (const id of eventIds.filter(Boolean)) {
      const response = await context.request.delete(`${base}/api/calendar/events/${encodeURIComponent(id)}`);
      report.cleanup[`event:${id}`] = response.ok() || response.status() === 404;
    }
    for (const id of sessions) report.cleanup[`session:${id}`] = (await context.request.delete(`${base}/api/session/${encodeURIComponent(id)}`)).ok();
  }
  if (browser) await browser.close();
  if (Object.values(report.cleanup).some(value => !value)) report.status = 'failed';
  save();
}
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: 2 };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, cases: report.cases }));
if (report.status !== 'passed') process.exitCode = 1;

#!/usr/bin/env node
/** Real 7011 Agent UI replay for rendered note/calendar links and navigation. */
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
const marker = `ody-link-${crypto.randomUUID()}`;
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/entity-link-navigation-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const report = { run, marker, owner, model, endpoint_id: endpointId, status: 'running', cases: [], cleanup: {}, privacy: 'Only exact synthetic fixture identifiers, static prompts, and boolean checks.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page, session = '', noteId = '', eventUid = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const createdSession = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[entity-link-navigation] ${marker}`, model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!createdSession.ok()) throw Error(`Session create HTTP ${createdSession.status()}`);
  session = (await createdSession.json()).id;
  const createdNote = await context.request.post(`${base}/api/notes`, { data: {
    title: `${marker} note`, content: `Synthetic link fixture ${marker}`, note_type: 'note', source: 'eval', session_id: session,
  }});
  if (!createdNote.ok()) throw Error(`Note create HTTP ${createdNote.status()}`);
  noteId = (await createdNote.json()).id;
  const createdEvent = await context.request.post(`${base}/api/calendar/events`, { data: {
    summary: `${marker} event`, dtstart: '2030-01-01T10:00:00Z', dtend: '2030-01-01T11:00:00Z', description: `Synthetic link fixture ${marker}`,
  }});
  if (!createdEvent.ok()) throw Error(`Event create HTTP ${createdEvent.status()}`);
  eventUid = (await createdEvent.json()).uid;
  report.fixtures = { note_id: noteId, event_uid: eventUid };
  save();

  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();

  const send = async prompt => {
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    const composer = page.locator('textarea#message:visible');
    await composer.fill(prompt);
    await composer.press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
    return { response, events, contract: events.find(event => event.type === 'turn_contract') || {} };
  };

  const noteTurn = await send(`List my notes containing ${marker}.`);
  const noteAnchor = page.locator(`#chat-history .msg-ai a[href="#note-${noteId}"]`).last();
  await noteAnchor.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
  const noteChecks = {
    http_ok: noteTurn.response.ok(), clean_route: noteTurn.contract.selection_mode === 'clean_compact_v3_preview',
    notes_capability: (noteTurn.contract.active_capabilities || []).includes('notes'),
    exact_anchor_rendered: await noteAnchor.isVisible().catch(() => false),
    no_stream_error: !noteTurn.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  };
  const noteRenderedHrefs = await page.locator('#chat-history .msg-ai a[href]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')));
  const noteCanonical = noteTurn.events.filter(event => event.type === 'final_response').map(event => event.content || event.response || '').join('\n');
  const noteVisibleText = await page.locator('#chat-history .msg-ai').last().innerText().catch(() => '');
  const noteToolEvents = noteTurn.events.filter(event => ['tool_start', 'tool_output'].includes(event.type)).map(event => ({ type: event.type, tool: event.tool, command: event.command, output: event.output, exit_code: event.exit_code }));
  if (noteChecks.exact_anchor_rendered) await noteAnchor.click();
  await page.locator(`#notes-pane .note-card[data-note-id="${noteId}"]`).waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
  noteChecks.note_panel_opened = await page.locator('#notes-pane').isVisible().catch(() => false);
  noteChecks.correct_note_visible = await page.locator(`#notes-pane .note-card[data-note-id="${noteId}"]`).isVisible().catch(() => false);
  report.cases.push({ name: 'note-result-link', contract: noteTurn.contract, event_types: noteTurn.events.map(event => event.type), tool_events: noteToolEvents, rendered_hrefs: noteRenderedHrefs, canonical_response: noteCanonical, visible_text: noteVisibleText, checks: noteChecks, status: Object.values(noteChecks).every(Boolean) ? 'passed' : 'failed' }); save();
  if (noteChecks.note_panel_opened) await page.keyboard.press('Escape');

  const eventTurn = await send(`List my calendar events from 2030-01-01 through 2030-01-02 containing ${marker}.`);
  const eventAnchor = page.locator(`#chat-history .msg-ai a[href="#event-${eventUid}"]`).last();
  await eventAnchor.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
  const eventChecks = {
    http_ok: eventTurn.response.ok(), clean_route: eventTurn.contract.selection_mode === 'clean_compact_v3_preview',
    calendar_capability: (eventTurn.contract.active_capabilities || []).includes('calendar'),
    exact_anchor_rendered: await eventAnchor.isVisible().catch(() => false),
    no_stream_error: !eventTurn.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  };
  if (eventChecks.exact_anchor_rendered) await eventAnchor.click();
  await page.locator(`#calendar-modal [data-uid="${eventUid}"]`).first().waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
  eventChecks.calendar_opened = await page.locator('#calendar-modal').isVisible().catch(() => false);
  eventChecks.correct_event_visible = await page.locator(`#calendar-modal [data-uid="${eventUid}"]`).first().isVisible().catch(() => false);
  eventChecks.correct_event_highlighted = await page.locator(`#calendar-modal [data-uid="${eventUid}"].cal-event-link-target`).first().isVisible().catch(() => false);
  report.cases.push({ name: 'calendar-result-link', checks: eventChecks, status: Object.values(eventChecks).every(Boolean) ? 'passed' : 'failed' });
  report.status = report.cases.length === 2 && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context) {
    if (noteId) {
      const removed = await context.request.delete(`${base}/api/notes/${encodeURIComponent(noteId)}`);
      report.cleanup.note = removed.ok() || removed.status() === 404;
    }
    if (eventUid) {
      const removed = await context.request.delete(`${base}/api/calendar/events/${encodeURIComponent(eventUid)}`);
      report.cleanup.event = removed.ok() || removed.status() === 404;
    }
    if (session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  }
  if (browser) await browser.close();
  if (!report.cleanup.note || !report.cleanup.event || !report.cleanup.session) report.status = 'failed';
  save();
}
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: 2 };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary, cases: report.cases }));
if (report.status !== 'passed') process.exitCode = 1;

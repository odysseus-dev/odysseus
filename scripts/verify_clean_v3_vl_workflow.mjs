#!/usr/bin/env node
/** Dashboard screenshot -> interpretation -> note -> tool/image comparison. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const routingMode = 'recent_model_choice';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const fixture = path.join(root, 'tests/fixtures/vl/quarterly-dashboard.png');
const run = new Date().toISOString().replace(/[:.]/g, '-');
const marker = `vl-workflow-${crypto.randomUUID()}`;
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/clean-v3-vl-workflow-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { run, owner, marker, fixture: path.relative(root, fixture), status: 'running', turns: [], privacy: 'Synthetic dashboard and UUID-only note; existing private rows and raw tool output are not retained.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const canonical = value => String(value || '').replace(/^mcp__email__/, '');
const noLeak = value => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(String(value || ''));
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page, session, note;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[clean-v3-vl-workflow] ${marker}`, model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  if (await page.locator('#web-toggle').isChecked()) await page.locator('#web-toggle-btn').click();
  if (await page.locator('#bash-toggle').isChecked()) await page.locator('#bash-toggle-btn').click();

  const send = async prompt => {
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await responsePromise;
    const events = parseSSE(await response.text());
    const contract = events.find(x => x.type === 'turn_contract');
    if (contract?.routing_experiment !== routingMode) throw Error('Wrong model-specific runtime');
    const starts = events.filter(x => x.type === 'tool_start').map(x => canonical(x.tool));
    const outputs = events.filter(x => x.type === 'tool_output').map(x => ({ tool: canonical(x.tool), exit_code: x.exit_code ?? null, error: Boolean(x.error) }));
    const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
    return { response, contract, starts, outputs, final };
  };

  await page.locator('#file-input').setInputFiles(fixture);
  await page.locator('#attach-strip .thumb').waitFor({ state: 'visible' });
  const visual = await send('Inspect this dashboard screenshot. Which quarter has the highest sales, what is its value, how much higher is it than Q1, and what are the build status and API latency?');
  const visualChecks = {
    http_ok: visual.response.ok(), clean_route: visual.contract?.selection_mode === 'clean_compact_v3_preview',
    no_tools: visual.starts.length === 0, no_reasoning_leak: noLeak(visual.final),
    chart_grounded: /q3/i.test(visual.final) && /55/.test(visual.final) && /35/.test(visual.final),
    screenshot_grounded: /healthy/i.test(visual.final) && /142/.test(visual.final),
  };
  report.turns.push({ kind: 'screenshot-chart', image_rehydration: visual.contract?.image_rehydration ?? null, attachment_reference_count: visual.contract?.attachment_reference_count ?? null, image_context_count: visual.contract?.multimodal_image_count ?? null, tools: visual.starts, final_chars: visual.final.length, checks: visualChecks, status: Object.values(visualChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const write = await send(`Create a note titled ${marker}-note summarizing the chart's highest quarter and its value, its margin above Q1, and the build status.`);
  const notesResponse = await context.request.get(`${base}/api/notes`);
  note = (await notesResponse.json()).notes?.find(
    x => String(x.title || '').toLocaleLowerCase() === `${marker}-note`.toLocaleLowerCase());
  let noteBody = '';
  if (note) {
    const noteResponse = await context.request.get(`${base}/api/notes/${encodeURIComponent(note.id)}`);
    const body = await noteResponse.json();
    noteBody = String(body.content ?? body.note?.content ?? '');
  }
  const writeChecks = {
    http_ok: write.response.ok(), clean_route: write.contract?.selection_mode === 'clean_compact_v3_preview',
    notes_only: write.starts.length >= 1 && write.starts.every(x => x === 'manage_notes'),
    tool_success: write.outputs.some(x => x.tool === 'manage_notes' && !x.error && (x.exit_code == null || x.exit_code === 0)),
    persisted: Boolean(note), persisted_q3: /q3/i.test(noteBody), persisted_55: /55/.test(noteBody),
    persisted_margin_35: /35/.test(noteBody), persisted_healthy: /healthy/i.test(noteBody),
    no_reasoning_leak: noLeak(write.final),
  };
  report.turns.push({ kind: 'image-to-note', image_rehydration: write.contract?.image_rehydration ?? null, attachment_reference_count: write.contract?.attachment_reference_count ?? null, image_context_count: write.contract?.multimodal_image_count ?? null, tools: write.starts, outputs: write.outputs, synthetic_note_content: noteBody.slice(0, 500), final_chars: write.final.length, checks: writeChecks, status: Object.values(writeChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const compare = await send('Read that saved note and compare it with the dashboard image. Is the note accurate? Mention the highest quarter and margin.');
  const compareChecks = {
    http_ok: compare.response.ok(), clean_route: compare.contract?.selection_mode === 'clean_compact_v3_preview',
    notes_only: compare.starts.every(x => x === 'manage_notes'),
    tool_success: compare.outputs.every(x => !x.error && (x.exit_code == null || x.exit_code === 0)),
    compared: /accurate|correct|yes/i.test(compare.final) && /q3/i.test(compare.final) && /35/.test(compare.final),
    no_reasoning_leak: noLeak(compare.final),
  };
  report.turns.push({ kind: 'tool-result-to-image-comparison', image_rehydration: compare.contract?.image_rehydration ?? null, attachment_reference_count: compare.contract?.attachment_reference_count ?? null, image_context_count: compare.contract?.multimodal_image_count ?? null, tools: compare.starts, outputs: compare.outputs, synthetic_answer: compare.final.slice(0, 500), final_chars: compare.final.length, checks: compareChecks, status: Object.values(compareChecks).every(Boolean) ? 'passed' : 'failed' });
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (note && context) {
    const removed = await context.request.delete(`${base}/api/notes/${encodeURIComponent(note.id)}`);
    const checked = await context.request.get(`${base}/api/notes/${encodeURIComponent(note.id)}`);
    report.note_cleanup = { removed: removed.ok() && checked.status() === 404 };
  }
  if (session && context) report.session_cleanup = { removed: (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok() };
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.turns.length === 3 && report.turns.every(x => x.status === 'passed') && report.note_cleanup?.removed && report.session_cleanup?.removed ? 'passed' : 'failed';
report.summary = { passed: report.turns.filter(x => x.status === 'passed').length, total: 3 };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

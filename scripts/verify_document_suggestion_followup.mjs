#!/usr/bin/env node
/** Real 7011 active-document suggestion -> referential suggestion replay. */
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
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/document-suggestion-followup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const original = '# Review fixture\n\nThis sentence is very very long and it has unnecessary words.\n\nThe final sentence is also somewhat verbose and lengthy.\n';
const report = { model, status: 'running', turns: [], cleanup: {}, privacy: 'Only static synthetic content and boolean checks; no user document data or suggestion text retained.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };

let browser, context, page, session = '', docId = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: '[document-suggestion-followup] synthetic', model, endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  const doc = await context.request.post(`${base}/api/document`, { data: {
    session_id: session, title: '[fixture] suggestion followup', language: 'markdown', content: original,
  }, timeout: 90000 });
  if (!doc.ok()) throw Error(`Document create HTTP ${doc.status()}`);
  docId = (await doc.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  await page.waitForFunction(id => window.documentModule?.getCurrentDocId?.() === id, docId, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  const prompts = [
    'Review this open document and create one inline suggestion to improve the first sentence. Do not apply the change.',
    'Add another inline suggestion for the final sentence. Keep the first suggestion pending and do not apply either change.',
  ];
  const requestedPassages = [
    'This sentence is very very long and it has unnecessary words.',
    'The final sentence is also somewhat verbose and lengthy.',
  ];
  let priorSuggestions = [];
  for (let index = 0; index < prompts.length; index++) {
    const beforeResponse = await context.request.get(`${base}/api/document/${encodeURIComponent(docId)}`);
    const beforeContent = beforeResponse.ok() ? String((await beforeResponse.json()).current_content || '') : '';
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompts[index]);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 }).catch(() => {});
    const contract = events.find(event => event.type === 'turn_contract') || {};
    const starts = events.filter(event => event.type === 'tool_start');
    const outputs = events.filter(event => event.type === 'tool_output');
    const suggestionEvents = events.filter(event => event.type === 'doc_suggestions');
    const args = parseArgs(starts[0]);
    const fetched = await context.request.get(`${base}/api/document/${encodeURIComponent(docId)}`);
    const current = fetched.ok() ? String((await fetched.json()).current_content || '') : '';
    const pendingSuggestions = await page.evaluate(id => {
      try { return JSON.parse(localStorage.getItem(`odysseus-suggestions-${id}`) || '[]'); } catch { return []; }
    }, docId);
    const pendingCount = pendingSuggestions.length;
    const checks = {
      http_ok: response.ok(), clean_route: contract.selection_mode === 'clean_compact_v3_preview',
      exact_runtime: contract.routing_experiment === routingMode,
      documents_capability: (contract.active_capabilities || []).includes('documents'),
      exactly_one_suggestion_call: starts.length === 1 && starts[0]?.tool === 'suggest_document',
      valid_suggestion_arguments: Array.isArray(args.suggestions) && args.suggestions.length >= 1 && args.suggestions.every(item => item?.find && item?.replace && item?.reason),
      requested_passage_only: Array.isArray(args.suggestions) && args.suggestions.length === 1
        && args.suggestions.every(item => typeof item.find === 'string' && item.find.trim().length > 5
          && requestedPassages[index].includes(item.find.trim())),
      exactly_one_successful_output: outputs.length === 1 && outputs[0]?.tool === 'suggest_document' && !outputs[0]?.error && (outputs[0]?.exit_code == null || outputs[0]?.exit_code === 0),
      suggestion_event_for_active_doc: suggestionEvents.length === 1 && suggestionEvents[0]?.doc_id === docId && Array.isArray(suggestionEvents[0]?.suggestions) && suggestionEvents[0].suggestions.length >= 1,
      document_unchanged: current === beforeContent,
      original_semantics_preserved: current.trimEnd() === original.trimEnd(),
      pending_suggestion_visible: pendingCount >= index + 1,
      suggestion_card_visible: await page.locator('.doc-suggestion-card:visible').count() > 0,
      previous_suggestions_preserved: priorSuggestions.every(previous => pendingSuggestions.some(current =>
        current.id === previous.id && current.find === previous.find && current.replace === previous.replace && current.reason === previous.reason)),
      no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
    };
    report.turns.push({ index, tools: starts.map(event => event.tool), argument_keys: Object.keys(args).sort(), suggestion_event_count: suggestionEvents.length, pending_count: pendingCount, before_length: beforeContent.length, after_length: current.length, checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' });
    priorSuggestions = pendingSuggestions;
  }
  report.status = report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context && docId) report.cleanup.document = (await context.request.delete(`${base}/api/document/${encodeURIComponent(docId)}`)).ok();
  if (context && session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  if (browser) await browser.close();
  if (!report.cleanup.document || !report.cleanup.session) report.status = 'failed';
  save();
}
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, turns: report.turns }));
if (report.status !== 'passed') process.exitCode = 1;

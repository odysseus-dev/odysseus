#!/usr/bin/env node
/** Real 7011 email search -> read first result; no mailbox content retained. */
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
const operation = process.env.EMAIL_OPERATION || 'search';
if (!['search', 'list'].includes(operation)) throw Error('EMAIL_OPERATION must be search or list');
const listing = operation === 'list';
const collectionTool = listing ? 'list_emails' : 'search_emails';
if (!['sft_alex_creator', 'pewds'].includes(owner)) throw Error('Unapproved audit account');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/email-${listing ? 'list-date' : 'search-read'}-followup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const digest = value => crypto.createHash('sha256').update(String(value)).digest('hex').slice(0, 16);
const report = { model, operation, status: 'running', turns: [], cleanup: false, privacy: 'No account, sender, subject, body, UID, tool output, or answer text retained; identifiers are hashed.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const canonical = value => String(value || '').replace(/^mcp__email__/, '');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const parseArgs = event => { try { return JSON.parse(event?.command || '{}'); } catch { return {}; } };
const unwrap = raw => {
  let value = String(raw || '');
  for (let index = 0; index < 3; index++) {
    try {
      const parsed = JSON.parse(value);
      const nested = parsed && typeof parsed === 'object' && ['results', 'response', 'output', 'stdout', 'content'].map(key => parsed[key]).find(item => typeof item === 'string');
      if (nested == null) break;
      value = nested;
    } catch { break; }
  }
  return value;
};

let browser, context, page, session = '';
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: '[email-search-read-followup] private', model, endpoint_id: endpointId,
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

  const searched = await send(listing
    ? 'List my latest three inbox emails with sender and subject. Read only.'
    : 'Search my emails for Amazon. Return at most three matching sender and subject lines.');
  const searchStarts = searched.events.filter(event => event.type === 'tool_start');
  const searchOutputs = searched.events.filter(event => event.type === 'tool_output');
  const searchArgs = parseArgs(searchStarts[0]);
  const rawSearchOutput = searchOutputs.map(event => unwrap(event.output)).join('\n');
  // Opt-in diagnosis prints only a failed tool's message, never mailbox rows.
  if (process.env.DIAGNOSE_ERRORS === 'true' && searchOutputs.some(event => event.error)) {
    console.error(rawSearchOutput.slice(0, 300));
  }
  const resultUids = [...rawSearchOutput.matchAll(/^\s*UID:\s*(\S+)/gmi)].map(match => match[1]);
  const firstFolder = rawSearchOutput.match(/^\s*Folder:\s*(.+)$/mi)?.[1]?.trim();
  const firstAccount = rawSearchOutput.match(/^\s*Account:\s*(.+)$/mi)?.[1]?.trim();
  const searchMetrics = searched.events.find(event => event.type === 'metrics') || {};
  const savedSearchTurn = (searchMetrics.data || searchMetrics).clean_v3_turn || [];
  const retainedResults = savedSearchTurn.filter(message => message.role === 'tool').map(message => String(message.content || '')).join('\n');
  report.search_history = {saved_tool_results: savedSearchTurn.filter(message => message.role === 'tool').length,
    all_search_uids_retained: resultUids.length > 0 && resultUids.every(uid => retainedResults.includes(uid)),
    saved_turn_chars: JSON.stringify(savedSearchTurn).length};
  if (process.env.DIAGNOSE_SHAPE === 'true') {
    let parsed; try { parsed = JSON.parse(rawSearchOutput); } catch {}
    console.error(JSON.stringify({line_count: rawSearchOutput.split('\n').length,
      escaped_newlines: rawSearchOutput.includes('\\n'),
      json_shape: Array.isArray(parsed) ? 'array' : parsed && typeof parsed === 'object' ? Object.keys(parsed) : typeof parsed,
      uid_prefixes: [...rawSearchOutput.matchAll(/([^\n]{0,20})UID[:\s]/gi)].map(match => match[1].replace(/[\p{L}\p{N}]/gu, 'x')),
    }));
  }
  const zeroResults = /(?:\bfound\s+0\b|\bno\b.{0,30}\bemails?\b|\bemails?\b.{0,20}\bnot\s+found\b|\bdid\s+not\s+find\b)/i.test(rawSearchOutput);
  const unavailable = /\b(?:unavailable|connection\s+refused|not\s+configured|failed|error)\b/i.test(rawSearchOutput);
  const positiveCount = /\bfound\s+[1-9]\d*\s+emails?\b/i.test(rawSearchOutput);
  report.turns.push({ name: operation, tools: searchStarts.map(event => canonical(event.tool)), argument_keys: Object.keys(searchArgs).sort(), result_chars: rawSearchOutput.length, zero_results: zeroResults, unavailable, positive_count: positiveCount, checks: {
    http_ok: searched.response.ok(), email_capability: (searched.contract.active_capabilities || []).includes('email'),
    model_choice_route: searched.contract.routing_experiment === 'recent_model_choice',
    exactly_one_collection_call: searchStarts.length === 1 && canonical(searchStarts[0]?.tool) === collectionTool,
    query_or_inbox_scope: listing ? (searchArgs.folder || 'INBOX') === 'INBOX'
      : typeof searchArgs.query === 'string' && searchArgs.query.trim().length > 0,
    requested_count_limit: resultUids.length <= 3,
    exactly_one_successful_output: searchOutputs.length === 1 && !searchOutputs[0]?.error && (searchOutputs[0]?.exit_code == null || searchOutputs[0]?.exit_code === 0),
    result_has_identifier: /\bUID\b|\buid\b|email-[A-Za-z0-9_-]+/.test(rawSearchOutput),
    no_stream_error: !searched.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});

  if (!resultUids.length || zeroResults || unavailable) throw Error('PRECONDITION: no verified email search identifiers; first-result read not testable');
  if (!listing) {
  const read = await send('Read the first email from those search results and summarize it briefly.');
  const readStarts = read.events.filter(event => event.type === 'tool_start');
  const readOutputs = read.events.filter(event => event.type === 'tool_output');
  const readArgs = parseArgs(readStarts[0]);
  const uid = String(readArgs.uid || '');
  const successfulReads = readOutputs.filter(event => canonical(event.tool) === 'read_email'
    && !event.error && (event.exit_code == null || event.exit_code === 0));
  report.read_outcome = {successful_reads: successfulReads.length,
    recovered_after_errors: successfulReads.length > 0 && readOutputs.some(event => event.error),
    attempts: readStarts.filter(event => canonical(event.tool) === 'read_email').length};
  report.turns.push({ name: 'read-first-result', tools: readStarts.map(event => canonical(event.tool)), uid_hash: uid ? digest(uid) : null, argument_keys: Object.keys(readArgs).sort(),
    proposals: readStarts.filter(event => canonical(event.tool) === 'read_email').map(event => {
      const args = parseArgs(event);
      const value = String(args.uid || args.message_id || '');
      return {argument_keys: Object.keys(args).sort(), identifier_is_first_search_uid: value === resultUids[0], identifier_is_any_search_uid: resultUids.includes(value),
        identifier_nonempty: value.trim().length > 0,
        folder_matches_first_result: !!firstFolder && (args.folder || 'INBOX') === firstFolder,
        account_from_first_result: !!args.account && !!firstAccount && firstAccount.includes(args.account),
        identifier_present_in_search_output: !!value && rawSearchOutput.includes(value),
        identifier_is_numeric: /^\d+$/.test(value), identifier_is_rfc_shape: /^<[^<>\s]+@[^<>\s]+>$/.test(value)};
    }),
    failure_categories: readOutputs.filter(event => event.error).map(event => {
      const error = String(event.output || event.error);
      if (/connection\s+refused/i.test(error)) return 'connection_refused';
      if (/timed?\s*out|timeout/i.test(error)) return 'timeout';
      if (/authentication\s+failed|login\s+failed/i.test(error)) return 'authentication_failed';
      if (/no UID or Message-ID|uid.*required|required.*uid/i.test(error)) return 'missing_identifier';
      if (/not found/i.test(error)) return 'identifier_not_found';
      return 'other_execution_error';
    }), checks: {
    http_ok: read.response.ok(), email_capability: (read.contract.active_capabilities || []).includes('email'),
    model_choice_route: read.contract.routing_experiment === 'recent_model_choice',
    exactly_one_read_call: readStarts.length === 1 && canonical(readStarts[0]?.tool) === 'read_email',
    exact_first_uid: !!uid && uid === resultUids[0],
    exact_first_folder: !!firstFolder && (readArgs.folder || 'INBOX') === firstFolder,
    exactly_one_successful_output: readOutputs.length === 1 && canonical(readOutputs[0]?.tool) === 'read_email' && !readOutputs[0]?.error && (readOutputs[0]?.exit_code == null || readOutputs[0]?.exit_code === 0),
    no_stream_error: !read.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
  }});
  }
  if (listing || process.env.CHECK_DATE_REFINEMENT === 'true') {
    const listedDates = [...rawSearchOutput.matchAll(/^\s*Date:\s*(.+)$/gmi)].map(match => Date.parse(match[1]));
    if (!Number.isFinite(listedDates[0])) throw Error('PRECONDITION: first search result has no parseable date');
    const firstDate = new Date(listedDates[0]);
    const dateFrom = new Date(Date.UTC(firstDate.getUTCFullYear(), firstDate.getUTCMonth(), 1)).toISOString();
    const dateTo = new Date(Date.UTC(firstDate.getUTCFullYear(), firstDate.getUTCMonth() + 1, 1)).toISOString();
    const refined = await send(`${listing ? 'List' : 'Search'} those emails again, restricted to dates from ${dateFrom} inclusive to ${dateTo} exclusive. Read only.`);
    const starts = refined.events.filter(event => event.type === 'tool_start');
    const outputs = refined.events.filter(event => event.type === 'tool_output');
    const args = parseArgs(starts[0]);
    const text = outputs.map(event => unwrap(event.output)).join('\n');
    const dates = [...text.matchAll(/^\s*Date:\s*(.+)$/gmi)].map(match => Date.parse(match[1]));
    report.turns.push({name: 'date-refinement', tools: starts.map(event => canonical(event.tool)),
      argument_keys: Object.keys(args).sort(), returned_dates: dates.length, checks: {
        http_ok: refined.response.ok(),
        model_choice_route: refined.contract.routing_experiment === 'recent_model_choice',
        collection_executed: starts.length === 1 && canonical(starts[0].tool) === collectionTool,
        query_or_folder_retained: listing ? (args.folder || 'INBOX') === (searchArgs.folder || 'INBOX')
          : /amazon/i.test(String(args.query || '')),
        exact_interval: Date.parse(args.date_from) === Date.parse(dateFrom) && Date.parse(args.date_to) === Date.parse(dateTo),
        successful_output: outputs.length === 1 && !outputs[0].error && (outputs[0].exit_code == null || outputs[0].exit_code === 0),
        dated_evidence_present: dates.length > 0,
        returned_dates_in_range: dates.length > 0 && dates.every(date => date >= Date.parse(dateFrom) && date < Date.parse(dateTo)),
        no_stream_error: !refined.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
      }});
    if (listing) {
      const limited = await send('Keep that same date interval, but show at most two emails. Read only.');
      const starts = limited.events.filter(event => event.type === 'tool_start');
      const outputs = limited.events.filter(event => event.type === 'tool_output');
      const args = parseArgs(starts[0]);
      const text = outputs.map(event => unwrap(event.output)).join('\n');
      const dates = [...text.matchAll(/^\s*Date:\s*(.+)$/gmi)].map(match => Date.parse(match[1]));
      report.turns.push({name: 'count-refinement', tools: starts.map(event => canonical(event.tool)),
        argument_keys: Object.keys(args).sort(), returned_dates: dates.length, checks: {
          http_ok: limited.response.ok(),
          model_choice_route: limited.contract.routing_experiment === 'recent_model_choice',
          list_executed: starts.length === 1 && canonical(starts[0].tool) === 'list_emails',
          folder_retained: (args.folder || 'INBOX') === (searchArgs.folder || 'INBOX'),
          exact_interval: Date.parse(args.date_from) === Date.parse(dateFrom) && Date.parse(args.date_to) === Date.parse(dateTo),
          successful_output: outputs.length === 1 && !outputs[0].error && (outputs[0].exit_code == null || outputs[0].exit_code === 0),
          requested_count: dates.length > 0 && dates.length <= 2,
          dates_in_range: dates.length > 0 && dates.every(date => date >= Date.parse(dateFrom) && date < Date.parse(dateTo)),
          no_stream_error: !limited.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        }});
    }
  }
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

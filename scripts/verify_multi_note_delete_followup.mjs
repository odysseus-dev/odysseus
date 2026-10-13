#!/usr/bin/env node
/** Real 7011 list -> referential multi-delete replay using only synthetic notes. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import {AMBIGUOUS_CASES,expectedNoteTitles,compareNoteState} from './note_test_oracle.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const routingMode = process.env.ROUTING_MODE || 'baseline';
const followupCase = process.env.FOLLOWUP_CASE || 'original';
const plainTitles = process.env.TITLE_STYLE === 'plain';
const auditedFlow=process.env.AUDITED_FLOW==='true';
const followups = {
  duplicate_titles: 'Delete the hf_fixture ones from that list.',
  original: 'delete japan today and groceries from that list',
  quoted: 'Delete the three notes named "Japan", "Today", and "Groceries" from that list.',
  reversed: 'delete groceries japan and today from that list',
  all_three: 'Delete all three notes from that list.',
  negative: 'Do not delete any of those notes. Just tell me their titles.',
  typo: 'plz delte japan today n groceries frm that list',
  subset: 'Delete Japan and Groceries from that list; keep Today.',
  keep_all: 'Keep all three notes. Do not change or delete anything.',
  contrast: 'Do not delete Japan or Today. Delete only Groceries.',
  drinks: 'remove milk tea and coffee from that list',
  schedule_words: 'remove work tomorrow and weekend from that list',
  explicit_ids: 'Delete all three listed notes using their exact IDs.',
  quoted_typo: 'plz delte "Japan", "Today", and "Groceries" frm those notes',
  single: 'Remove only the note titled Today. Leave the other two alone.',
  except_one: 'Delete the notes in that list except Japan.',
  punctuated: 'Remove these notes: Japan; Today; Groceries.',
  neutral: 'Delete the notes titled "Harbor", "Orchid", and "Lantern" from that list.',
  neutral_typo: 'plz delte the notes "Harbor", "Orchid", and "Lantern" frm that list',
  user_punctuation: 'remove the groceries , japan , today note',
};
if (!Object.hasOwn(followups, followupCase)) throw Error('Unknown followup case');
const fixtureTitles = followupCase === 'drinks' ? ['Milk','Tea','Coffee']
  : followupCase === 'duplicate_titles' ? ['hf_fixture', 'hf_fixture', 'Keep']
  : followupCase === 'schedule_words' ? ['Tomorrow','Work','Weekend']
  : ['neutral','neutral_typo'].includes(followupCase) ? ['Harbor','Orchid','Lantern']
  : ['Groceries','Japan','Today'];
const expectedTitles = expectedNoteTitles(followupCase,fixtureTitles);
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/multi-note-delete-followup-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === 'sft_alex_creator')?.[0];
if (!token) throw Error('Dedicated SFT account has no active session');
const marker = `ody-multinote-${crypto.randomUUID()}`;
const report = { marker, routing_mode: routingMode, followup_case: followupCase, title_style: plainTitles ? 'plain' : 'prefixed', status: 'running', turns: [], cleanup: {}, privacy: 'Synthetic note details and sanitized model reply when explicitly audited.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page, session = '';
const noteIds = [];
const snapshotNotes=async()=>{
  const responses=await Promise.all((auditedFlow?['false','true']:['false']).map(archived=>
    context.request.get(`${base}/api/notes?archived=${archived}`)));
  if(responses.some(r=>!r.ok())) throw Error('Cannot snapshot complete note state');
  const rows=(await Promise.all(responses.map(r=>r.json()))).flatMap(r=>r.notes || []);
  if(new Set(rows.map(r=>r.id)).size!==rows.length) throw Error('Inconsistent active/archived snapshot');
  return rows;
};
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode,
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[multi-note-followup] ${marker}`, model,
    endpoint_id: process.env.ENDPOINT_ID || '1d1022ef',
    endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(),
    skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  if (plainTitles) {
    const snapshot = await context.request.get(`${base}/api/notes`);
    if (!snapshot.ok()) throw Error('PRECONDITION: cannot check title collisions');
    if (((await snapshot.json()).notes || []).some(n => fixtureTitles.map(t => t.toLowerCase()).includes(String(n.title || '').trim().toLowerCase())))
      throw Error('PRECONDITION: plain fixture title already exists; no fixtures created');
  }
  for (const suffix of fixtureTitles) {
    const response = await context.request.post(`${base}/api/notes`, { data: {
      title: plainTitles ? suffix : `${marker} ${suffix}`, content: `Synthetic ${suffix} note for ${marker}`,
      label: 'ody-multinote-fixture',
      note_type: 'note', source: 'eval', session_id: session,
    }});
    if (!response.ok()) throw Error(`Note create HTTP ${response.status()}`);
    noteIds.push((await response.json()).id);
  }
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  const send = async prompt => {
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await pending;
    const events = parseSSE(await response.text());
    const contract = events.find(event => event.type === 'turn_contract') || {};
    const starts = events.filter(event => event.type === 'tool_start').map(event => ({ tool: event.tool, command: event.command || '' }));
    const outputs = events.filter(event => event.type === 'tool_output').map(event => ({ tool: event.tool, ok: !event.error && (event.exit_code == null || event.exit_code === 0), command: event.command || '' }));
    return { response, events, contract, starts, outputs };
  };
  const calendar = await send('List my next three calendar events.');
  report.turns.push({name: 'calendar', checks: {
    correct_mode: calendar.contract.routing_experiment === routingMode,
    executed: calendar.outputs.some(x => x.tool === 'manage_calendar' && x.ok),
  }});
  const beforeRows = await snapshotNotes();
  const untouchedIds = beforeRows.filter(n => !noteIds.includes(n.id)).map(n => n.id);
  const unrelatedUnchanged=rows=>compareNoteState(beforeRows.filter(n=>untouchedIds.includes(n.id)),
    rows.filter(n=>!noteIds.includes(n.id))).unchanged;
  const listed = await send(`List my notes containing ${marker}. Return all three titles.`);
  const listedText = listed.events.filter(e => e.type === 'tool_output').map(e => String(e.output || '')).join('\n');
  const listedMetrics=listed.events.findLast(e=>e.type==='metrics') || {};
  const listedSaved=(listedMetrics.data || listedMetrics).clean_v3_turn || [];
  const listedEvidence=listedSaved.find(m=>m.role==='tool' && noteIds.every(id=>String(m.content).includes(id)));
  if(listedEvidence) report.prior_note_evidence={call_id:listedEvidence.tool_call_id,
    content_sha256:crypto.createHash('sha256').update(JSON.stringify(listedEvidence.content)).digest('hex'),
    content_chars:String(listedEvidence.content).length};
  if (!noteIds.every(id => listedText.includes(id))) {
    throw Error('PRECONDITION: list did not return all three synthetic IDs; deletion replay skipped');
  }
  report.turns.push({
    name: 'list', tools: listed.starts.map(item => item.tool),
    checks: {
      http_ok: listed.response.ok(), clean_route: listed.contract.selection_mode === 'clean_compact_v3_preview',
      notes_capability: (listed.contract.active_capabilities || []).includes('notes'),
      listed: listed.outputs.some(item => item.tool === 'manage_notes' && item.ok),
      no_stream_error: !listed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
    },
  });
  const removed = await send(followups[followupCase]);
  const deleteCalls = removed.outputs.filter(item => item.tool === 'manage_notes' && item.ok && /"action"\s*:\s*"delete"/i.test(item.command));
  const remaining = await snapshotNotes();
  report.turns.push({
    name: 'delete-followup', tools: removed.starts.map(item => item.tool), delete_calls: deleteCalls.length,
    checks: {
      http_ok: removed.response.ok(), clean_route: removed.contract.selection_mode === 'clean_compact_v3_preview',
      notes_offered: (removed.contract.offered || []).includes('manage_notes'),
      exact_requested_targets: fixtureTitles.every((title,index) =>
        expectedTitles.includes(title) === !remaining.some(note => note.id === noteIds[index])),
      unrelated_notes_preserved: unrelatedUnchanged(remaining),
      no_stream_error: !removed.events.some(event => ['error', 'invalid_sse'].includes(event.type)),
    },
  });
  report.turns[report.turns.length - 1].offered = removed.contract.offered;
  report.turns[report.turns.length - 1].remaining_fixture_titles = remaining
    .filter(note => noteIds.includes(note.id)).map(note => note.title);
  report.outcome = {
    deleted_fixtures: noteIds.filter(id => !remaining.some(note => note.id === id)).length,
    unrelated_preserved: unrelatedUnchanged(remaining),
    expected_deleted: expectedTitles.length,
    exact_requested_targets: fixtureTitles.every((title,index) =>
      expectedTitles.includes(title) === !remaining.some(note => note.id === noteIds[index])),
  };
  report.outcome.passed = report.outcome.deleted_fixtures === report.outcome.expected_deleted
    && report.outcome.unrelated_preserved && report.outcome.exact_requested_targets;
  // Passive diagnostics only: no prompts, routing, or scoring changes.
  const removalMetrics = removed.events.findLast(e => e.type === 'metrics') || {};
  const metricData = removalMetrics.data || removalMetrics;
  const savedTurn = metricData.clean_v3_turn || [];
  report.diagnostics = {
    agent_rounds: metricData.agent_rounds,
    input_tokens: metricData.input_tokens,
    injected_tokens: metricData.injected_tokens,
    response_time: metricData.response_time,
    ttft: metricData.time_to_first_token,
    model_messages: savedTurn.filter(m => m.role === 'assistant').map(m => ({
      tool_calls: (m.tool_calls || []).length,
      mentions: fixtureTitles.filter(s => String(m.content || '').toLowerCase().includes(s.toLowerCase())),
    })),
    terminal_events: removed.events.map(e => e.type).filter(t =>
      ['rounds_exhausted','budget_exceeded','loop_breaker_triggered','completion_recovery'].includes(t)),
  };
  if (process.env.AUDIT_FINAL === 'true') {
    report.diagnostics.sanitized_final = String(savedTurn.filter(m => m.role === 'assistant').at(-1)?.content || '')
      .replaceAll(marker, '[fixture]').replace(/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}/gi, '[id]').slice(0, 700);
  }
  report.turns[report.turns.length - 1].policy_decisions =
    removalMetrics.data?.policy_decisions || removalMetrics.policy_decisions || [];
  report.turns[report.turns.length - 1].proposals = removed.events
    .filter(e => e.type === 'model_tool_proposal').map(e => {
      let args; try { args = JSON.parse(e.function?.arguments || '{}'); } catch { args = {}; }
      return {tool: e.function?.name, round: e.round, action: args.action,
        target_suffix: beforeRows.find(n => noteIds.includes(n.id) &&
          (n.id === (args.id || args.uid) || n.title?.toLowerCase() === String(args.title || '').toLowerCase()))?.title?.replace(marker, '').trim() || null,
        argument_keys: Object.keys(args), target_is_fixture: noteIds.includes(args.id || args.uid)};
    });
  report.turns[report.turns.length - 1].errors = removed.events
    .filter(e => e.type === 'tool_output' && e.error)
    .map(e => ({tool: e.tool, argument_keys: Object.keys(JSON.parse(e.command || '{}')),
      category: /not offered|not permitted/.test(String(e.output)) ? 'not_offered' : 'validation_or_execution'}));
  if(auditedFlow) {
    const wantedIds=noteIds.filter((id,i)=>expectedTitles.includes(fixtureTitles[i]));
    const initialState=compareNoteState(beforeRows,remaining,wantedIds);
    const summarizeTurn=turn=>{
      const metric=turn.events.findLast(e=>e.type==='metrics') || {};
      const data=metric.data || metric;
      const final=String((data.clean_v3_turn || []).filter(m=>m.role==='assistant').at(-1)?.content || '');
      const deleteTargets=turn.starts.filter(c=>c.tool==='manage_notes').flatMap(c=>{
        let args;try {args=JSON.parse(c.command);} catch {return [];}
        if(!['delete','remove'].includes(args.action)) return [];
        const id=String(args.id || args.note_id || args.noteId || '').trim();
        const records=beforeRows.filter(n=>noteIds.includes(n.id));
        const target=(id && records.find(n=>n.id.startsWith(id))) || records.find(n=>
          n.title.toLowerCase()===String(args.title || args.query || args.text || '').trim().toLowerCase());
        return [target?.title || '[unresolved]'];
      });
      return {final:final.replaceAll(marker,'[fixture]').replace(/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}/gi,'[id]').slice(0,1500),
        model_rounds:data.agent_rounds,seconds:data.response_time,
        attempted_delete_targets:deleteTargets,
        duplicate_resolved_targets:deleteTargets.filter((t,i)=>t!=='[unresolved]' && deleteTargets.indexOf(t)!==i).length,
        call_count:turn.starts.length,tool_error_count:turn.outputs.filter(o=>!o.ok).length,
        clean_completion:turn.response.ok() && turn.events.some(e=>e.type==='metrics') &&
          !turn.events.some(e=>['error','invalid_sse','rounds_exhausted','budget_exceeded'].includes(e.type))};
    };
    report.audited={rubric:'note-flow-v2',kind:AMBIGUOUS_CASES.has(followupCase)?'ambiguous':'explicit_or_control',
      state_scope:'active_and_archived',
      initial_state:initialState,initial_no_changes:compareNoteState(beforeRows,remaining).unchanged,
      initial_response:summarizeTurn(removed),clarification_sent:false,
      semantic_review:'pending_human_review_not_regex_scored'};
    if(!report.outcome.unrelated_preserved || initialState.modified_count || initialState.added_count)
      throw Error('Unexpected state change: stop before any clarification');
    if(AMBIGUOUS_CASES.has(followupCase) && !initialState.exact) {
      const prompt=`I mean the separate notes titled ${fixtureTitles.map(t=>JSON.stringify(t)).join(', ')}. Delete any of those still present from that list; leave all other notes unchanged.`;
      const clarified=await send(prompt);
      const afterRows=await snapshotNotes();
      report.audited.clarification_sent=true;
      report.audited.clarified_response=summarizeTurn(clarified);
      report.audited.final_state=compareNoteState(beforeRows,afterRows,wantedIds);
      report.outcome.unrelated_preserved=unrelatedUnchanged(afterRows);
      if(!report.outcome.unrelated_preserved || report.audited.final_state.modified_count || report.audited.final_state.added_count)
        throw Error('Unexpected state change after clarification');
    } else report.audited.final_state=initialState;
  }
  for (const turn of report.turns) turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
  report.status = report.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
  if(auditedFlow) {report.initial_checks_status=report.status;report.status='measured_pending_semantic_review';}
} catch (error) {
  report.status = 'failed'; report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (context) {
    for (const id of noteIds) {
      const response = await context.request.delete(`${base}/api/notes/${encodeURIComponent(id)}`);
      if (response.ok() || response.status() === 404) report.cleanup[id] = true;
    }
    if (session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
  }
  if (browser) await browser.close();
  save();
}
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, outcome: report.outcome, turns: report.turns.map(turn => ({ name: turn.name, status: turn.status, checks: turn.checks })) }));
if (!['passed','measured_pending_semantic_review'].includes(report.status)) process.exitCode = 1;

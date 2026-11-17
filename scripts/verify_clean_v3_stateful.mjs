#!/usr/bin/env node
/** Reversible create -> API verify -> referential correction -> verify flows. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const owner = 'sft_alex_creator';
const routingMode = 'recent_model_choice';
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/clean-v3-stateful-${run}.json`));
const selected = new Set((process.env.FAMILIES || '').split(',').map(x => x.trim()).filter(Boolean));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep)) throw Error('Report must be under reports/');
if (fs.existsSync(reportPath)) throw Error('Report exists; refuse overwrite');

const authSessions = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(authSessions).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const marker = `stateful-${crypto.randomUUID()}`;
const report = {
  run, owner, status: 'running', marker, flows: [],
  privacy: 'Synthetic UUID artifacts only. Prompts, tool output, account data, and existing rows are not retained.',
};
fs.mkdirSync(path.dirname(reportPath), { recursive: true });
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
save();

const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const canonical = name => String(name || '').replace(/^mcp__email__/, '');
const noLeak = text => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(String(text || ''));
const skillSteps = values => (values || []).map(value => String(value).trim().toLowerCase().replace(/[.!]+$/, ''));
const failureCategory = event => {
  const output = String(event?.output || '').toLowerCase();
  if (!event?.error && (event?.exit_code == null || event.exit_code === 0)) return null;
  if (output.includes('not offered or permitted') || output.includes('denied')) return 'policy_denied';
  if (output.includes('old_string is ambiguous')) return 'ambiguous_patch';
  if (output.includes('old_string not found')) return 'patch_text_not_found';
  if (output.includes('invalid') || output.includes('required')) return 'invalid_arguments';
  if (output.includes('not found')) return 'target_not_found';
  return 'execution_error';
};

async function json(request, method, url, data) {
  const response = await request.fetch(`${base}${url}`, { method, data, timeout: 15000 });
  let body = null;
  try { body = await response.json(); } catch {}
  return { response, body };
}

const flows = [
  {
    family: 'checklists', tool: 'manage_notes',
    create: `Create a checklist note titled ${marker}-checklist with these unchecked items in order: tea, rice, apples.`,
    revise: 'Mark the second item as done.',
    remove: 'Delete that checklist note.',
    locate: async request => (await json(request, 'GET', '/api/notes')).body?.notes?.find(
      x => String(x.title || '').toLowerCase() === `${marker}-checklist`.toLowerCase()),
    createCheck: row => row.note_type === 'checklist'
      && JSON.stringify((row.items || []).map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', false], ['apples', false]]),
    verify: async (request, row) => JSON.stringify(
      ((await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).body?.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
      === JSON.stringify([['tea', false], ['rice', true], ['apples', false]]),
    readState: async (request, row) => (await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).body,
    followups: [
      {prompt: 'Keep the second item checked.', allowNoop: true, verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', true], ['apples', false]])},
      {prompt: 'Actually uncheck that same item.', verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', false], ['apples', false]])},
      {prompt: 'Add bread at the end of that checklist; leave the other items unchanged.', verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', false], ['apples', false], ['bread', false]])},
      {prompt: 'Mark the third item as done.', verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', false], ['apples', true], ['bread', false]])},
      {prompt: 'Remove only the second item from that checklist. Keep all the other items and their checked states unchanged.', verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['apples', true], ['bread', false]])},
      {prompt: 'Undo only that removal, putting the item back in its original position and state.', verify: row => JSON.stringify((row.items || [])
        .map(x => [x.text.toLowerCase(), Boolean(x.done)]))
        === JSON.stringify([['tea', false], ['rice', false], ['apples', true], ['bread', false]])},
    ],
    absent: async (request, row) => (await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).response.status() === 404,
    cleanup: async (request, row) => {
      await json(request, 'DELETE', `/api/notes/${encodeURIComponent(row.id)}`);
      return (await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).response.status() === 404;
    },
    cleanupExtras: async request => {
      const rows = (await json(request, 'GET', '/api/notes')).body?.notes || [];
      const extras = rows.filter(row => row.owner === owner
        && String(row.title || '').toLowerCase().includes(marker.toLowerCase()));
      for (const row of extras) {
        const url = `/api/notes/${encodeURIComponent(row.id)}`;
        await json(request, 'DELETE', url);
        if ((await json(request, 'GET', url)).response.status() !== 404) return false;
      }
      return true;
    },
  },
  {
    family: 'calendar', tool: 'manage_calendar',
    create: `Create a calendar event titled ${marker}-event on 2030-01-01 from 00:00 to 01:00 UTC.`,
    revise: `Change its title to ${marker}-event-revised.`,
    remove: 'Delete that event.',
    locate: async request => (await json(request, 'GET', '/api/calendar/events?start=2029-12-31T00%3A00%3A00Z&end=2030-01-02T00%3A00%3A00Z')).body?.events?.find(x => x.summary === `${marker}-event`),
    verify: async (request, row) => (await json(request, 'GET', `/api/calendar/events/${encodeURIComponent(row.uid)}`)).body?.event?.summary === `${marker}-event-revised`,
    absent: async (request, row) => (await json(request, 'GET', `/api/calendar/events/${encodeURIComponent(row.uid)}`)).response.status() === 404,
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/calendar/events/${encodeURIComponent(row.uid)}`);
      const checked = await json(request, 'GET', `/api/calendar/events/${encodeURIComponent(row.uid)}`);
      return removed.response.ok() && checked.response.status() === 404;
    },
  },
  {
    family: 'notes', tool: 'manage_notes',
    create: `Create a note titled ${marker}-note with content alpha-state.`,
    revise: `Change its title to ${marker}-note-revised.`,
    remove: 'Delete that note.',
    // Titles are user-facing natural language. Capitalization changes do not
    // alter the requested note identity or CRUD semantics, so keep this
    // functional verifier case-insensitive while retaining the UUID marker.
    locate: async request => (await json(request, 'GET', '/api/notes')).body?.notes?.find(
      x => String(x.title || '').toLocaleLowerCase() === `${marker}-note`.toLocaleLowerCase()),
    verify: async (request, row) => String(
      (await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).body?.title || ''
    ).toLocaleLowerCase() === `${marker}-note-revised`.toLocaleLowerCase(),
    absent: async (request, row) => (await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`)).response.status() === 404,
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/notes/${encodeURIComponent(row.id)}`);
      const checked = await json(request, 'GET', `/api/notes/${encodeURIComponent(row.id)}`);
      return removed.response.ok() && checked.response.status() === 404;
    },
  },
  {
    family: 'tasks', tool: 'manage_tasks',
    create: `Create a one-off scheduled task named ${marker}-task for 2030-01-01 at 00:00 UTC. Its prompt is: say ${marker}-needle.`,
    search: `Search my tasks for ${marker}-needle in their instructions.`,
    revise: `Rename that task to ${marker}-task-revised.`,
    remove: 'Delete that task.',
    followups: [
      { prompt: 'Move that task to 2030-01-02 at 00:00 UTC.', verify: row =>
        Date.parse(row.scheduled_date) === Date.parse('2030-01-02T00:00:00Z')
        && Date.parse(row.next_run) === Date.parse('2030-01-02T00:00:00Z') },
      { prompt: 'Pause it.', verify: row => row.status === 'paused' },
      { prompt: 'Resume it on that same schedule.', verify: row => row.status === 'active'
        && Date.parse(row.next_run) === Date.parse('2030-01-02T00:00:00Z') },
    ],
    locate: async request => (await json(request, 'GET', '/api/tasks')).body?.tasks?.find(x => x.name === `${marker}-task`),
    verify: async (request, row) => (await json(request, 'GET', `/api/tasks/${encodeURIComponent(row.id)}`)).body?.name === `${marker}-task-revised`,
    absent: async (request, row) => (await json(request, 'GET', `/api/tasks/${encodeURIComponent(row.id)}`)).response.status() === 404,
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/tasks/${encodeURIComponent(row.id)}`);
      const checked = await json(request, 'GET', `/api/tasks/${encodeURIComponent(row.id)}`);
      return removed.response.ok() && checked.response.status() === 404;
    },
  },
  {
    family: 'documents', tool: 'create_document', reviseTools: ['edit_document'],
    removeTools: ['manage_documents'],
    create: `Create a markdown document titled ${marker}-document containing exactly these three lines:\nFirst: alpha-state\nSecond: alpha-state\nKeep: violet-72`,
    revise: 'In that document, change only the second line to Second: beta-state. Leave the first and third lines unchanged.',
    remove: 'Delete that document.',
    locate: async request => {
      const row = (await json(request, 'GET', `/api/documents/library?search=${encodeURIComponent(marker)}&limit=20`)).body?.documents?.find(x => x.title === `${marker}-document`);
      return row ? (await json(request, 'GET', `/api/document/${encodeURIComponent(row.id)}`)).body : null;
    },
    createCheck: row => String(row.current_content || '').trim() === 'First: alpha-state\nSecond: alpha-state\nKeep: violet-72',
    verify: async (request, row) => String((await json(request, 'GET', `/api/document/${encodeURIComponent(row.id)}`)).body?.current_content || '').trim()
      === 'First: alpha-state\nSecond: beta-state\nKeep: violet-72',
    readState: async (request, row) => (await json(request, 'GET', `/api/document/${encodeURIComponent(row.id)}`)).body,
    followups: [
      {prompt: 'Undo only that last edit.', tools: ['edit_document', 'update_document'],
        verify: row => String(row.current_content || '').trim() === 'First: alpha-state\nSecond: alpha-state\nKeep: violet-72'},
      {prompt: 'Now change the first line to First: gamma-state and the second line to Second: delta-state. Keep the third line unchanged.',
        tools: ['edit_document'], verify: row => String(row.current_content || '').trim()
          === 'First: gamma-state\nSecond: delta-state\nKeep: violet-72'},
    ],
    absent: async (request, row) => {
      const checked = await json(request, 'GET', `/api/documents/library?search=${encodeURIComponent(marker)}&limit=20`);
      return !checked.body?.documents?.some(x => x.id === row.id);
    },
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/document/${encodeURIComponent(row.id)}`);
      const checked = await json(request, 'GET', `/api/documents/library?search=${encodeURIComponent(marker)}&limit=20`);
      return removed.response.ok() && !checked.body?.documents?.some(x => x.id === row.id);
    },
  },
  {
    family: 'memory', tool: 'manage_memory',
    create: `Remember this exact preference: ${marker}-memory alpha-state.`,
    revise: `Change that memory to say: ${marker}-memory beta-state.`,
    remove: 'Forget that memory.',
    locate: async request => (await json(request, 'GET', '/api/memory')).body?.memory?.find(x => String(x.text || '').includes(`${marker}-memory alpha-state`)),
    verify: async (request, row) => String((await json(request, 'GET', `/api/memory/${encodeURIComponent(row.id)}`)).body?.memory?.text || '').includes(`${marker}-memory beta-state`),
    absent: async (request, row) => (await json(request, 'GET', `/api/memory/${encodeURIComponent(row.id)}`)).response.status() === 404,
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/memory/${encodeURIComponent(row.id)}`);
      const checked = await json(request, 'GET', `/api/memory/${encodeURIComponent(row.id)}`);
      return removed.response.ok() && checked.response.status() === 404;
    },
  },
  {
    family: 'skills', tool: 'manage_skills',
    create: `Create a draft skill named ${marker}-skill. Description: alpha-state helper. Use it for synthetic verification. Procedure: report alpha-state. Verification: confirm alpha-state appears.`,
    revise: 'Change that skill description from alpha-state helper to beta-state helper.',
    remove: 'Delete that skill.',
    locate: async request => (await json(request, 'GET', '/api/skills')).body?.skills?.find(x => x.name === `${marker}-skill`),
    verify: async (request, row) => (await json(request, 'GET', '/api/skills')).body?.skills?.some(x => x.name === row.name && x.description === 'beta-state helper'),
    readState: async (request, row) => (await json(request, 'GET', '/api/skills')).body?.skills?.find(x => x.name === row.name),
    followups: [
      {prompt: 'In that same skill, replace the procedure step report alpha-state with report gamma-state. Leave its description and verification unchanged.',
        verify: (row, original) => row.description === 'beta-state helper'
          && JSON.stringify(skillSteps(row.procedure)) === JSON.stringify(['report gamma-state'])
          && JSON.stringify(row.verification) === JSON.stringify(original.verification)},
      {prompt: 'Undo only that last procedure change; keep the description change.',
        verify: (row, original) => row.description === 'beta-state helper'
          && JSON.stringify(skillSteps(row.procedure)) === JSON.stringify(skillSteps(original.procedure))
          && JSON.stringify(row.verification) === JSON.stringify(original.verification)},
    ],
    absent: async (request, row) => !(await json(request, 'GET', '/api/skills')).body?.skills?.some(x => x.name === row.name),
    cleanup: async (request, row) => {
      const removed = await json(request, 'DELETE', `/api/skills/${encodeURIComponent(row.name)}`);
      const checked = await json(request, 'GET', '/api/skills');
      return removed.response.ok() && !checked.body?.skills?.some(x => x.name === row.name);
    },
  },
].filter(flow => !selected.size || selected.has(flow.family));

let browser, context;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);

  for (const spec of flows) {
    const flow = { family: spec.family, status: 'running', turns: [], cleanup: null };
    report.flows.push(flow); save();
    let page, session, artifact;
    try {
      const createdResponse = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[clean-v3-stateful] ${spec.family} ${marker}`,
        model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
      }});
      if (!createdResponse.ok()) throw Error(`session create HTTP ${createdResponse.status()}`);
      session = (await createdResponse.json()).id;
      page = await context.newPage();
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();

      const send = async (prompt, allowed, allowNoop = false) => {
        const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
        await page.locator('textarea#message:visible').fill(prompt);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await responsePromise;
        const events = parseSSE(await response.text());
        const contract = events.find(x => x.type === 'turn_contract');
        const starts = events.filter(x => x.type === 'tool_start').map(x => canonical(x.tool));
        const outputs = events.filter(x => x.type === 'tool_output').map(x => {
          let command = x.command;
          if (typeof command === 'string') {
            try { command = JSON.parse(command); } catch { command = {}; }
          }
          return {
          tool: canonical(x.tool), action: String(command?.action || command?.command || ''),
          argument_keys: Object.keys(command || {}).sort(),
          exit_code: x.exit_code ?? null, error: Boolean(x.error), failure: failureCategory(x),
          ...(spec.family === 'skills' && command?.name === `${marker}-skill`
            && (x.error || (x.exit_code != null && x.exit_code !== 0))
            ? {fixture_error: String(x.output || '').replaceAll(marker, 'fixture').split('\n')[0].slice(0, 240)} : {}),
        }; });
        const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
        // An already-satisfied state need not be written again. Only this
        // explicit no-op case permits no call; saved state is still checked.
        const acknowledgedNoop = allowNoop && starts.length === 0 && final.trim().length > 0
          && !/\b(?:cannot|can't|unable|unchecked|undone)\b/i.test(final);
        const metrics = events.find(x => x.type === 'metrics')?.data || {};
        const turn = {
          route: contract?.selection_mode || null,
          capabilities: contract?.active_capabilities || contract?.capabilities || [],
          offered: contract?.offered || [], tools: starts, outputs, final_chars: final.length,
          final_kind: /(?:can(?:not|'t)|unable|not available|no changes)/i.test(final) ? 'denial' : 'answer',
          policy: (metrics.policy_decisions || []).map(x => ({ tool: canonical(x.tool), reason: x.reason })),
          first_attempt_clean: outputs.every(x => !x.error && (x.exit_code == null || x.exit_code === 0)),
          checks: {
            http_ok: response.ok(), clean_route: contract?.selection_mode === 'clean_compact_v3_preview',
            exact_runtime: contract?.routing_experiment === routingMode,
            expected_tool: acknowledgedNoop || starts.some(name => allowed.includes(name)),
            tool_success: acknowledgedNoop || outputs.some(x => allowed.includes(x.tool) && !x.error && (x.exit_code == null || x.exit_code === 0)),
            no_reasoning_leak: noLeak(final),
            visible_answer: final.trim().length > 0,
          },
        };
        turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
        flow.turns.push(turn); save();
        if (turn.status !== 'passed') throw Error(`${spec.family} model turn failed`);
        return events;
      };

      await send(spec.create, [spec.tool]);
      artifact = await spec.locate(context.request);
      flow.create_verified = Boolean(artifact);
      if (!artifact) throw Error(`${spec.family} artifact not found after create`);
      if (spec.createCheck && !spec.createCheck(artifact)) throw Error('Created fixture state does not match request');
      if (spec.family === 'tasks') {
        flow.schedule_observed = Object.fromEntries(['schedule', 'scheduled_date', 'scheduled_time', 'next_run', 'run_count', 'status'].map(key => [key, artifact[key]]));
        flow.schedule_verified = artifact.schedule === 'once'
          && Date.parse(artifact.scheduled_date) === Date.parse('2030-01-01T00:00:00Z')
          && artifact.run_count === 0;
        if (!flow.schedule_verified) throw Error('Task schedule did not match the requested future one-off');
      }
      if (spec.family === 'calendar') {
        flow.schedule_verified = Date.parse(artifact.dtstart) === Date.parse('2030-01-01T00:00:00Z')
          && Date.parse(artifact.dtend) === Date.parse('2030-01-01T01:00:00Z');
        if (!flow.schedule_verified) throw Error('Calendar event interval did not match request');
      }
      flow.artifact_id = artifact.id || artifact.name;
      save();

      if (spec.search) {
        const found = await send(spec.search, [spec.tool]);
        flow.search_verified = found.some(event => event.type === 'tool_output' && String(event.output || '').includes(artifact.id));
        if (!flow.search_verified) throw Error('Instruction-only task search missed the created fixture');
      }
      await send(spec.revise, spec.reviseTools || [spec.tool]);
      flow.revision_verified = await spec.verify(context.request, artifact);
      if (!flow.revision_verified) throw Error(`${spec.family} correction not verified`);
      for (const followup of spec.followups || []) {
        await send(followup.prompt, followup.tools || [spec.tool], Boolean(followup.allowNoop));
        const row = spec.readState ? await spec.readState(context.request, artifact)
          : (await json(context.request, 'GET', `/api/tasks/${encodeURIComponent(artifact.id)}`)).body;
        const verified = followup.verify(row || {}, artifact) && (spec.family !== 'tasks' || row?.run_count === 0);
        (flow.followups_verified ||= []).push(verified);
        if (!verified) throw Error(`${spec.family} follow-up saved state did not match request`);
      }
      await send(spec.remove, spec.removeTools || [spec.tool]);
      flow.deletion_verified = await spec.absent(context.request, artifact);
      if (!flow.deletion_verified) throw Error(`${spec.family} deletion not verified`);
      flow.status = 'passed';
    } catch (error) {
      flow.status = 'failed'; flow.failure_layer = flow.turns.some(x => x.status === 'failed') ? 'model/policy/execution' : 'verification';
      flow.error = String(error).split('\n')[0].slice(0, 400);
    } finally {
      // A create can succeed before the response/replay fails. Still discover
      // and clean its exact UUID-marked fixture, never unrelated account rows.
      if (!artifact) artifact = await spec.locate(context.request);
      if (artifact) {
        try { flow.cleanup = { removed: await spec.absent(context.request, artifact) || await spec.cleanup(context.request, artifact) }; }
        catch (error) { flow.cleanup = { removed: false, error: String(error).split('\n')[0].slice(0, 300) }; }
        if (!flow.cleanup.removed) flow.status = 'failed';
      }
      if (session) {
        if (spec.cleanupExtras) {
          try { flow.extra_fixture_cleanup = await spec.cleanupExtras(context.request); }
          catch { flow.extra_fixture_cleanup = false; }
          if (!flow.extra_fixture_cleanup) flow.status = 'failed';
        }
        const removed = await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`);
        flow.session_cleanup = { http: removed.status(), removed: removed.ok() };
        if (!removed.ok()) flow.status = 'failed';
      }
      if (page) await page.close();
      save();
    }
  }
} catch (error) {
  // Playwright errors can include request cookies in their multiline call log.
  // Persist only the first-line cause, never the raw exception/stack.
  report.error = String(error).split('\n')[0].slice(0, 300);
} finally {
  if (browser) await browser.close();
}

report.status = !report.error && report.flows.length === flows.length && report.flows.every(flow => flow.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.flows.filter(x => x.status === 'passed').length, total: report.flows.length, cleanups: report.flows.filter(x => x.cleanup?.removed).length };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

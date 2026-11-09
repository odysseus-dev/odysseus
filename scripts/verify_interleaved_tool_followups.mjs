#!/usr/bin/env node
/** Real 7011 three-turn A -> B -> A follow-up and contract-isolation replay. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';
import { capabilityAvailable, skillDetailEvidence } from './tool_followup_oracle.mjs';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = process.env.OWNER || 'sft_alex_creator';
const routingMode = process.env.ROUTING_MODE || 'baseline';
const expectCleanRoute = process.env.EXPECT_CLEAN_ROUTE !== 'false';
const expectRoutingMetadata = process.env.EXPECT_ROUTING_METADATA !== 'false';
if (!['baseline', 'recent', 'all', 'default'].includes(routingMode)) throw Error('Invalid routing mode');
const expectedMode = routingMode === 'default' ? 'recent_model_choice' : routingMode;
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/interleaved-followups-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const selected = new Set((process.env.CHAINS || '').split(',').map(x => x.trim()).filter(Boolean));
const keepSession = process.env.KEEP_SESSION === 'true';
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const chains = [
  { name: 'shell-failure-recovery', publicAnswerAudit: true,
    expectedExitCodes: {0: 7, 2: 0},
    noToolTurns: [1],
    expectedAnswers: [['PHASE_ONE_DONE', 'CHECK_FAILED', '7'], ['7'], ['RECOVERY_OK']], turns: [
    ["Use bash to run this read-only test once: printf 'PHASE_ONE_DONE\\n'; printf 'CHECK_FAILED\\n' >&2; exit 7\nReport stdout, stderr, and the exit code. Do not retry automatically.", 'shell_files', ['bash'], false, true],
    ['What was its exit code? Do not run anything again.', 'shell_files', [], false, true],
    ["Now run this corrected read-only command and show its output: printf 'RECOVERY_OK\\n'", 'shell_files', ['bash'], false, true],
  ]},
  { name: 'shell-output-followup', publicAnswerAudit: true,
    expectedAnswers: [['FOLLOWUP_SHELL_OK'], ['FOLLOWUP_SHELL_OK'], ['FOLLOWUP_SHELL_OK']], turns: [
    ["Use bash to run this read-only command and report its output: printf 'FOLLOWUP_SHELL_OK\\n'", 'shell_files', ['bash'], false, true],
    ['What did it print? Quote the output.', 'shell_files', [], false, true],
    ['Run it again and show the output, not just whether it succeeded.', 'shell_files', ['bash'], false, true],
  ]},
  { name: 'browser-keyboard-followup', publicAnswerAudit: true,
    expectedAnswers: [['Cedar', 'Harbor', '219', '349'], ['Cedar', '219']], turns: [
    ['Use the private browser to open http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html, fill the search field with orange, and submit with the Enter key. Report the sofa names and prices.', 'search_browser', ['private_browser'], false, false],
    ['Which of those costs less?', 'search_browser', [], false, false],
  ]},
  { name: 'typo-calendar-notes-calendar', turns: [
    ['show my calnedar for this week', 'calendar', ['manage_calendar'], false, false],
    ['now show my notse', 'notes', ['manage_notes'], false, false],
    ['back to calnedar whats on next week?', 'calendar', ['manage_calendar'], false, false],
  ]},
  { name: 'typo-email-notes-email', turns: [
    ['whats my emaol adress?', 'email', ['list_email_accounts'], false, false],
    ['show my notse now', 'notes', ['manage_notes'], false, false],
    ['back to emaol show 2 latest', 'email', ['list_emails'], false, false],
  ]},
  { name: 'web-toggle-revocation', publicAnswerAudit: true,
    deniedTools: {1: ['web_search', 'web_fetch']}, turns: [
      ['Search the web for the official IANA reserved domains page. Return the source.', 'search_browser', ['web_search'], true, false],
      ['Search again for that same page.', 'search_browser', [], false, false],
      ['Search again for that same page.', 'search_browser', ['web_search'], true, false],
      ['Web search is enabled again. Search for that same page.', 'search_browser', ['web_search'], true, false],
    ]},
  { name: 'shell-toggle-revocation', publicAnswerAudit: true,
    expectedAnswers: [['TOGGLE_SHELL_OK'], [], ['TOGGLE_SHELL_OK'], ['TOGGLE_SHELL_OK']],
    deniedTools: {1: ['bash']}, turns: [
    ["Use bash to run this read-only command and report its output: printf 'TOGGLE_SHELL_OK\\n'", 'shell_files', ['bash'], false, true],
    ['Run that same read-only shell command again.', 'shell_files', [], false, false],
    ['Run that same read-only shell command again.', 'shell_files', ['bash'], false, true],
    ['Bash is enabled again. Run that same read-only shell command.', 'shell_files', ['bash'], false, true],
  ]},
  { name: 'browser-link-followup', publicAnswerAudit: true,
    expectedAnswers: [['Example Domain'], ['Example Domains']], turns: [
    ['Open https://example.com in the private browser and report its heading.', 'search_browser', ['private_browser'], false, false],
    ['Return to that browser page, open the Learn more link, and report the destination heading.', 'search_browser', ['private_browser'], false, false],
  ]},
  { name: 'browser-controlled-overlay', publicAnswerAudit: true,
    expectedAnswers: [['Cedar', 'Harbor', '219', '349'], ['Cedar', '219'], ['Cedar', '219']], turns: [
    ['Go to http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html?overlay=delayed and find orange sofas. Give their names and prices. Do not accept optional cookies.', 'search_browser', ['private_browser'], false, false],
    ['Which of those is cheaper?', 'search_browser', [], false, false],
    ['Try again on that page and compare the prices.', 'search_browser', ['private_browser'], false, false],
  ]},
  { name: 'browser-controlled-catalog', publicAnswerAudit: true,
    expectedAnswers: [['Cedar', 'Harbor', '219', '349'], ['Cedar', '219']], turns: [
    ['Go to http://127.0.0.1:7011/static/test-fixtures/browser-catalog.html and find orange sofas. Give their names and prices.', 'search_browser', ['private_browser'], false, false],
    ['Which of those is cheaper?', 'search_browser', [], false, false],
  ]},
  { name: 'browser-nitori-domain', publicAnswerAudit: true, turns: [
    ['Go to nitori.jp and find orange couch', 'search_browser', ['private_browser'], false, false],
  ]},
  { name: 'browser-navigation-wording', publicAnswerAudit: true, turns: [
    ['Go to ikea and find sofa yelloe', 'search_browser', ['private_browser'], false, false],
    ['Go to ikea.com find a yellow sofa', 'search_browser', ['private_browser'], false, false],
  ]},
  { name: 'greeting-url-question', publicAnswerAudit: true, turns: [
    ['Yo', null, [], false, false],
    ['Where', null, [], false, false],
    ['https://consumerrights.wiki/w/Sony_PlayStation_digital_game_ownership_lawsuit whays this web', 'search_browser', ['web_fetch'], true, false],
    ['What else?', 'search_browser', [], true, false],
  ]},
  { name: 'url-typo-question', publicAnswerAudit: true, turns: [
    ['https://consumerrights.wiki/w/Sony_PlayStation_digital_game_ownership_lawsuit whays this web', 'search_browser', ['web_fetch'], true, false],
    ['What else?', 'search_browser', [], true, false],
  ]},
  { name: 'url-only', publicAnswerAudit: true, turns: [
    ['https://consumerrights.wiki/w/Sony_PlayStation_digital_game_ownership_lawsuit', 'search_browser', ['web_fetch'], true, false],
  ]},
  { name: 'url-suffix-summary', publicAnswerAudit: true, turns: [
    ['https://consumerrights.wiki/w/Sony_PlayStation_digital_game_ownership_lawsuit summarize', 'search_browser', ['web_fetch'], true, false],
  ]},
  { name: 'youtube-summary', publicAnswerAudit: true, turns: [
    ['Summarize this video https://youtu.be/jNQXAC9IVRw', 'search_browser', ['youtube_tool'], true, false],
  ]},
  { name: 'url-summary-followup', publicAnswerAudit: true, turns: [
    ['Can u summarize this https://investors.bendingspoons.com/newsroom/bending-spoons-agrees-to-acquire-miro', 'search_browser', ['web_fetch'], true, false],
    ['What else', 'search_browser', [], true, false],
  ]},
  { name: 'email-latest-notes', turns: [
    ['whats my email?', 'email', ['list_email_accounts'], false, false],
    ['whats my 5 latest', 'email', ['list_emails'], false, false],
    ['what about my notes', 'notes', ['manage_notes'], false, false],
  ]},
  { name: 'calendar-notes-calendar', turns: [
    ['List my next three calendar events with their times.', 'calendar', ['manage_calendar'], false, false],
    ['Now list my first three notes.', 'notes', ['manage_notes'], false, false],
    ['What time was the second calendar event from earlier? Check my calendar again.', 'calendar', ['manage_calendar'], false, false],
  ]},
  { name: 'notes-tasks-notes', turns: [
    ['List my first three notes.', 'notes', ['manage_notes'], false, false],
    ['Now list my first three scheduled tasks and statuses.', 'tasks', ['manage_tasks'], false, false],
    ['Open the second note from the earlier note list.', 'notes', ['manage_notes'], false, false],
  ]},
  { name: 'tasks-memory-tasks', turns: [
    ['List my first three scheduled tasks and statuses.', 'tasks', ['manage_tasks'], false, false],
    ['Now list my first three saved memories.', 'memory', ['manage_memory'], false, false],
    ['What is the status of the second scheduled task from earlier? Check it again.', 'tasks', ['manage_tasks'], false, false],
  ]},
  { name: 'documents-skills-documents', turns: [
    ['List my first three documents.', 'documents', ['manage_documents'], false, false],
    ['Now list my first three skills.', 'skills', ['manage_skills'], false, false],
    ['Read the second document from the earlier document list and summarize it.', 'documents', ['manage_documents'], false, false],
  ]},
  { name: 'skills-cookbook-skills', turns: [
    ['List my first three skills.', 'skills', ['manage_skills'], false, false],
    ['Now list configured Cookbook servers and their status.', 'cookbook_admin', ['list_cookbook_servers'], false, false],
    ['Show the second skill from the earlier skill list.', 'skills', ['manage_skills'], false, false],
    ['Now read its full procedure and verification steps. Do not execute the procedure.', 'skills', ['manage_skills'], false, false],
  ]},
  { name: 'cookbook-calendar-cookbook', turns: [
    ['List configured Cookbook servers and their status.', 'cookbook_admin', ['list_cookbook_servers'], false, false],
    ['Now list my next three calendar events.', 'calendar', ['manage_calendar'], false, false],
    ['Which Cookbook server from earlier is the default? Check the server list again.', 'cookbook_admin', ['list_cookbook_servers'], false, false],
  ]},
  { name: 'email-calendar-email', turns: [
    ['List my latest three inbox emails with sender and subject.', 'email', ['list_emails'], false, false],
    ['Now list my next three calendar events.', 'calendar', ['manage_calendar'], false, false],
    ['Read the second email from the earlier inbox list and summarize it.', 'email', ['read_email'], false, false],
  ]},
  { name: 'search-notes-search', turns: [
    ['Search the web for the official IANA reserved domains page. Return the source.', 'search_browser', ['web_search'], true, false],
    ['Now list my first three notes.', 'notes', ['manage_notes'], false, false],
    ['Open the first web result from earlier and summarize it.', 'search_browser', ['web_fetch'], true, false],
  ]},
  { name: 'browser-notes-browser', turns: [
    ['Open https://example.com in the private browser and report its heading.', 'search_browser', ['private_browser'], false, false],
    ['Now list my first three notes.', 'notes', ['manage_notes'], false, false],
    ['Return to that browser page, open the Learn more link, and report the destination heading.', 'search_browser', ['private_browser'], false, false],
  ]},
  { name: 'shell-notes-shell', turns: [
    ["Use bash to run this read-only command and report its output: printf 'INTERLEAVED_SHELL_OK\\n'", 'shell_files', ['bash'], false, true],
    ['Now list my first three notes.', 'notes', ['manage_notes'], false, false],
    ['Run that same read-only shell command again and report its output.', 'shell_files', ['bash'], false, true],
  ]},
].filter(chain => !selected.size || selected.has(chain.name));
if (!chains.length) throw Error('No matching chains selected');

const report = {
  run, owner, model, routing_mode: routingMode, endpoint_id: endpointId, status: 'running', chains: [],
  privacy: 'Public-only chains retain answer text and bounded public tool diagnostics. Mixed/private chains retain checks and argument keys, not private outputs or answers.',
};
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const bare = value => String(value || '').replace(/^mcp__email__/, '');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
const noLeak = text => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(String(text || ''));

let browser, context, page;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'Accept-Encoding': 'identity',
    ...(routingMode === 'default' ? {} : {'x-odysseus-routing-experiment': routingMode}),
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of chains) {
    const chain = { name: spec.name, status: 'running', turns: [], cleanup: false };
    report.chains.push(chain); save();
    let session;
    let previousEmailUids = [];
    let previousSkillRows = [];
    let previousSkillDetail = '';
    try {
      const createStarted = performance.now();
      let created;
      try {
        created = await context.request.post(`${base}/api/session`, { multipart: {
          name: `[interleaved-followup] ${spec.name}-${run}`, model, endpoint_id: endpointId,
          endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
        }});
      } finally {
        chain.session_create_ms = Math.round(performance.now() - createStarted);
        save();
      }
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      page = await context.newPage();
      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(String(error).split('\n')[0].slice(0, 300)));
      page.on('console', message => {
        if (message.type() === 'error') pageErrors.push(message.text().slice(0, 300));
      });
      const historyReady = page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.pathname.startsWith('/api/history') && r.request().method() === 'GET';
      }, { timeout: 30000 }).catch(() => null);
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, session);
      await historyReady;
      await page.waitForFunction(id => {
        const history = document.querySelector('#chat-history');
        return window.__odysseusSessionReadyId === id
          && history
          && !history.querySelector('.session-loading-state')
          && !history.classList.contains('no-animate')
          && getComputedStyle(history).opacity === '1';
      }, session, { timeout: 30000 });
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      for (let index = 0; index < spec.turns.length; index++) {
        const [prompt, capability, expected, web, shell] = spec.turns[index];
        if (expected.includes('read_email') && previousEmailUids.length < 2) {
          throw Error('PRECONDITION: fewer than two verified email results; ordinal replay is invalid');
        }
        if (await page.locator('#web-toggle').isChecked() !== web) await page.locator('#web-toggle-btn').click();
        if (await page.locator('#bash-toggle').isChecked() !== shell) await page.locator('#bash-toggle-btn').click();
        const toggleStateBeforeSend = {web: await page.locator('#web-toggle').isChecked(),
          shell: await page.locator('#bash-toggle').isChecked()};
        const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
        const beforeUsers = await page.locator('#chat-history .msg-user').count();
        await page.locator('textarea#message:visible').fill(prompt);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await waiting;
        const submitted = response.request().postData() || '';
        const submittedToggle = name => {
          const match = submitted.match(new RegExp(`name="${name}"\\r?\\n\\r?\\n(true|false)`));
          return match ? match[1] === 'true' : null;
        };
        const events = parseSSE(await response.text());
        await page.waitForFunction(n => document.querySelectorAll('#chat-history .msg-user').length === n && !document.querySelector('#chat-history .streaming'), beforeUsers + 1, { timeout: 15000 }).catch(() => {});
        const contract = events.find(x => x.type === 'turn_contract') || {};
        const metrics = events.findLast(x => x.type === 'metrics') || {};
        const startEvents = events.filter(x => x.type === 'tool_start');
        const starts = startEvents.map(x => bare(x.tool));
        const calls = startEvents.map(x => {
          const raw = x.command ?? x.arguments ?? x.args ?? {};
          let args = raw;
          if (typeof raw === 'string') { try { args = JSON.parse(raw); } catch { args = {}; } }
          const uid = String(args?.uid || '');
          return {
            tool: bare(x.tool),
            argument_keys: Object.keys(args || {}).sort(),
            previous_email_uid_count: previousEmailUids.length,
            email_uid_ordinal: uid ? (previousEmailUids.indexOf(uid) + 1 || null) : null,
            email_account_present: Boolean(args?.account),
            ...(spec.name === 'skills-cookbook-skills' && bare(x.tool) === 'manage_skills'
              ? {skill_action: args?.action || null,
                skill_matches_second: Boolean(previousSkillRows[1] && (args?.name || args?.skill_id) === previousSkillRows[1].name)} : {}),
          };
        });
        const outputs = events.filter(x => x.type === 'tool_output').map(x => {
          const detail = String(x.output || x.error_message || '');
          const backendError = /EMAIL ACCOUNT ERRORS|connection refused|connection timed out/i.test(detail);
          const ok = !backendError && !x.error && (x.exit_code == null || x.exit_code === 0);
          const failure_category = ok ? null
            : /not found|no such|unknown (?:uid|id)|does not exist/i.test(detail) ? 'not_found'
            : /invalid|missing|required|argument|json|parse/i.test(detail) ? 'invalid_arguments'
            : /covered by|obscured by|blocking (?:dialog|overlay)|dismiss or interact with the covering/i.test(detail) ? 'interaction_blocked'
            : /connection|unavailable|timeout|refused/i.test(detail) ? 'backend_unavailable'
            : /permission|not offered|not permitted|denied/i.test(detail) ? 'permission_denied'
            : 'other';
          return { tool: bare(x.tool), ok, failure_category,
            ...(bare(x.tool) === 'web_search' ? {evidence_status: x.evidence_status || null} : {}) };
        });
        for (const output of events.filter(x => x.type === 'tool_output' && bare(x.tool) === 'list_emails' && !x.error)) {
          let detail = String(output.output || '');
          try { detail = JSON.parse(detail).stdout || detail; } catch {}
          previousEmailUids = [...detail.matchAll(/^\s*UID:\s*(\S+)/gmi)].map(match => match[1]);
        }
        const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
        const recoveredBrowserInteraction = events.some((event, eventIndex) => {
          if (event.type !== 'tool_output' || bare(event.tool) !== 'private_browser') return false;
          const detail = String(event.output || event.error_message || '');
          const blocked = /covered by|obscured by|blocking (?:dialog|overlay)|dismiss or interact with the covering/i.test(detail);
          if (!blocked) return false;
          return events.slice(eventIndex + 1).some(later => (
            later.type === 'tool_output'
            && bare(later.tool) === 'private_browser'
            && !later.error
            && (later.exit_code == null || later.exit_code === 0)
          ));
        });
        const prefetchedWebSources = events
          .filter(x => x.type === 'web_sources')
          .flatMap(x => Array.isArray(x.data) ? x.data : [])
          .filter(source => source?.acquisition === 'automatic_url_fetch');
        const prefetchedYoutubeSources = events
          .filter(x => x.type === 'web_sources')
          .flatMap(x => Array.isArray(x.data) ? x.data : [])
          .filter(source => source?.acquisition === 'automatic_youtube_context');
        const exactUrlPrefetched = expected.includes('web_fetch')
          && prefetchedWebSources.length > 0;
        const youtubePrefetched = expected.includes('youtube_tool')
          && prefetchedYoutubeSources.length > 0;
        if (spec.name === 'skills-cookbook-skills' && index === 0) {
          // Compare in memory only: never retain private skill names/content.
          previousSkillRows = events.filter(x => x.type === 'tool_output' && bare(x.tool) === 'manage_skills')
            .flatMap(x => {
              let detail = String(x.output || '');
              try { const parsed = JSON.parse(detail); detail = parsed.stdout || parsed.results || detail; } catch {}
              return [...detail.matchAll(/^- \*\*([^*]+)\*\*[^\n]*?:\s*([^\n]*)/gm)]
                .map(match => ({name: match[1], description: match[2]}));
            }).filter(row => final.includes(row.name))
            .sort((a, b) => final.indexOf(a.name) - final.indexOf(b.name));
        }
        const offered = (contract.offered || []).map(bare);
        const priorCapability = index > 0 ? spec.turns[index - 1][1] : null;
        const priorFamilyTools = priorCapability ? {
          calendar: ['manage_calendar'], notes: ['manage_notes'], tasks: ['manage_tasks'], memory: ['manage_memory'],
          documents: ['manage_documents', 'create_document', 'edit_document'], skills: ['manage_skills'],
          cookbook_admin: ['list_cookbook_servers'], email: ['list_emails', 'read_email', 'search_emails', 'list_email_accounts'],
          search_browser: ['web_search', 'web_fetch', 'private_browser', 'pdf_extract', 'youtube_tool'], shell_files: ['bash'],
        }[priorCapability] || [] : [];
        const afterUsers = await page.locator('#chat-history .msg-user').count();
        if (spec.name === 'skills-cookbook-skills' && index >= 2 && previousSkillRows[1]) {
          for (const event of events.filter(x => x.type === 'tool_output' && bare(x.tool) === 'manage_skills'
            && !x.error && (x.exit_code == null || x.exit_code === 0))) {
            let args = event.command || {};
            if (typeof args === 'string') { try { args = JSON.parse(args); } catch { args = {}; } }
            if (args.action === 'view' && (args.name || args.skill_id) === previousSkillRows[1].name) {
              previousSkillDetail = String(event.output || '');
            }
          }
        }
        const detailEvidence = skillDetailEvidence(previousSkillDetail, final);
        const reusedSkillDetail = spec.name === 'skills-cookbook-skills' && index === 3
          && starts.length === 0 && detailEvidence.covered;
        const reusedSkillSummary = spec.name === 'skills-cookbook-skills' && index === 2 && starts.length === 0
          && Boolean(previousSkillRows[1]?.description && final.includes(previousSkillRows[1].name)
            && final.includes(previousSkillRows[1].description))
          && !/\b(?:cannot|can't|unable|not available|don't have|do not have)\b/i.test(final);
        const domClasses = afterUsers === beforeUsers ? await page.locator('#chat-history > *').evaluateAll(nodes =>
          nodes.slice(-8).map(node => String(node.className || node.tagName || '').slice(0, 120))
        ) : [];
        const checks = {
          experiment_selected: !expectRoutingMetadata || contract.routing_experiment === expectedMode,
          http_ok: response.ok(), terminal: response.ok() && !events.some(x => x.type === 'invalid_sse'),
          clean_route: !expectCleanRoute || contract.selection_mode === 'clean_compact_v3_preview',
          capability: !expectRoutingMetadata || Boolean(spec.deniedTools?.[index]) || capabilityAvailable(contract, capability, expected)
            || (spec.noToolTurns?.includes(index) && starts.length === 0)
            // An intentionally ambiguous continuation can use the retained
            // family without the classifier guessing a fresh active topic.
            || (!expected.length && index > 0 && routingMode !== 'baseline'
                && priorCapability === capability && priorFamilyTools.some(name => offered.includes(name))),
          expected_offered: !expectRoutingMetadata || !expected.length || expected.some(name => offered.includes(name)), expected_called: reusedSkillSummary || reusedSkillDetail || exactUrlPrefetched || youtubePrefetched || !expected.length || expected.some(name => starts.includes(name)),
          expected_execution_outcome: spec.expectedExitCodes?.[index] !== undefined
            ? events.filter(e => e.type === 'tool_output' && expected.includes(bare(e.tool))).length === 1
              && events.some(e => e.type === 'tool_output' && expected.includes(bare(e.tool)) && e.exit_code === spec.expectedExitCodes[index])
            : reusedSkillSummary || reusedSkillDetail || exactUrlPrefetched || youtubePrefetched || !expected.length || outputs.some(x => expected.includes(x.tool) && x.ok),
          requested_execution_count: spec.name !== 'shell-failure-recovery' || starts.length === (index === 1 ? 0 : 1),
          failed_execution_provenance: !(spec.expectedExitCodes?.[index] > 0)
            || events.some(e => e.type === 'tool_output' && expected.includes(bare(e.tool))
              && e.exit_code === spec.expectedExitCodes[index] && e.execution_attempted === true && e.blocked !== true),
          saved_failure_status: !expectCleanRoute || !(spec.expectedExitCodes?.[index] > 0)
            || (metrics.data?.clean_v3_turn || metrics.clean_v3_turn || []).some(m => {
              if (m.role !== 'tool') return false;
              try { return JSON.parse(m.content).exit_code === spec.expectedExitCodes[index]; } catch { return false; }
            }),
          exact_skill_detail_reference: spec.name !== 'skills-cookbook-skills' || index !== 3
            || reusedSkillDetail || calls.some(call => call.tool === 'manage_skills' && call.skill_action === 'view' && call.skill_matches_second),
          skill_detail_answer_evidence: spec.name !== 'skills-cookbook-skills' || index !== 3 || detailEvidence.covered,
          no_prior_family_leak: !expectRoutingMetadata || routingMode !== 'baseline' || index === 0 || priorCapability === capability
            || offered.every(name => !priorFamilyTools.includes(name) || expected.includes(name)),
          one_user_turn: afterUsers === beforeUsers + 1,
          visible_answer: final.trim().length > 0, no_reasoning_leak: noLeak(final),
          no_canned_failure: Boolean(spec.deniedTools?.[index]) || !/can[’']?t perform that operation|no changes were made|currently permitted tools/i.test(final),
          no_tool_errors: outputs.every(item => item.ok || (
            spec.deniedTools?.[index]?.includes(item.tool)
            && item.failure_category === 'permission_denied' && starts.length === 0)
            || (item.tool === 'private_browser'
              && item.failure_category === 'interaction_blocked'
              && recoveredBrowserInteraction)
            || (spec.expectedExitCodes?.[index] > 0 && expected.includes(item.tool)
              && events.some(e => e.type === 'tool_output' && bare(e.tool) === item.tool && e.exit_code === spec.expectedExitCodes[index]))),
          expected_answer_evidence: !spec.expectedAnswers
            || spec.expectedAnswers[index].every(value => final.toLowerCase().includes(value.toLowerCase())),
          disabled_tools_absent: !spec.deniedTools?.[index]
            || spec.deniedTools[index].every(name => !offered.includes(name)),
          disabled_request_not_executed: !spec.deniedTools?.[index] || starts.length === 0,
          submitted_shell_matches_toggle: submittedToggle('allow_bash') === toggleStateBeforeSend.shell,
          submitted_web_matches_toggle: submittedToggle('allow_web_search') === toggleStateBeforeSend.web,
          disabled_request_explained: !spec.deniedTools?.[index]
            || /disabled|not enabled|turn.{0,10}on|enable|can[’']?t|cannot|permission|turned off/i.test(final),
        };
        const turn = { index, capability, expected, web, shell, offered, tools: starts, calls, outputs,
          classifier_capabilities: contract.active_capabilities || contract.capabilities || [],
          toggle_state_before_send: toggleStateBeforeSend,
          submitted_toggles: {allow_bash: submittedToggle('allow_bash'), allow_web_search: submittedToggle('allow_web_search')},
          proposals: events.filter(x => x.type === 'model_tool_proposal').map(x => {
            let args; try { args = JSON.parse(x.function?.arguments || '{}'); } catch { args = null; }
            return {round: x.round, tool: bare(x.function?.name), valid_json: args !== null,
              argument_keys: args && typeof args === 'object' ? Object.keys(args).sort() : []};
          }),
          recovered: events.some(x => x.type === 'completion_recovery') || outputs.some(x => !x.ok),
          metrics: Object.fromEntries(['input_tokens', 'output_tokens', 'injected_tokens',
            'time_to_first_token', 'response_time'].map(key => [key, metrics[key] ?? metrics.data?.[key] ?? null])),
          user_count_before: beforeUsers, user_count_after: afterUsers,
          prefetched_web_sources: prefetchedWebSources.length,
          prefetched_youtube_sources: prefetchedYoutubeSources.length,
          dom_classes_on_user_mismatch: domClasses,
          page_errors: pageErrors.splice(0),
          unavailable: contract.unavailable || [], checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' };
        if (spec.publicAnswerAudit) {
          // Only explicitly public-only chains retain bounded answer/tool traces.
          turn.public_answer = final;
          turn.semantic_review = 'pending';
          turn.public_tool_diagnostics = events.filter(event => event.type === 'tool_output'
            && (['private_browser', 'web_fetch', 'web_search', 'youtube_tool'].includes(bare(event.tool))
              || (['shell-toggle-revocation', 'shell-output-followup', 'shell-failure-recovery'].includes(spec.name) && bare(event.tool) === 'bash')))
            .map(event => ({tool: bare(event.tool),
              ...(spec.name.startsWith('browser-controlled-') || ['browser-link-followup', 'browser-keyboard-followup', 'browser-navigation-wording', 'web-toggle-revocation'].includes(spec.name) ? {command: event.command} : {}),
              observation_chars: String(event.output || '').length,
              exit_code: event.exit_code ?? null,
              execution_attempted: event.execution_attempted ?? null,
              blocked: event.blocked ?? null,
              observation_truncated: /\[.*truncated/i.test(String(event.output || '')),
              dialog_lines: String(event.output || '').split('\n').filter(line =>
                /\bdialog\b|\bbutton\b.*(?:cookie|consent|accept|reject|拒否|同意)/i.test(line)).slice(0, 12).map(line => line.slice(0, 180)),
              output: String(event.output || event.error || '').slice(0, 2200)}));
        }
        if (spec.name === 'skills-cookbook-skills' && index >= 2) {
          const target = previousSkillRows[1];
          turn.skill_followup_audit = {
            initial_named_rows: previousSkillRows.length,
            second_identity_in_answer: Boolean(target && final.includes(target.name)),
            prior_description_in_answer: Boolean(target?.description && final.includes(target.description)),
            explicit_inability: /\b(?:cannot|can't|unable|not available|don't have|do not have)\b/i.test(final),
            answer_chars: final.length,
            verified_summary_reuse: reusedSkillSummary,
            verified_detail_reuse: reusedSkillDetail,
            source_steps: detailEvidence.steps,
            all_source_steps_in_answer: detailEvidence.covered,
          };
        }
        chain.turns.push(turn); save();
      }
      chain.status = chain.turns.length === spec.turns.length && chain.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      chain.status = 'failed'; chain.error = String(error).split('\n')[0].slice(0, 400);
      chain.infrastructure_failure = /PRECONDITION|Timeout|ECONN|HTTP 5/.test(chain.error);
    } finally {
      if (page) { await page.close(); page = null; }
      if (session && keepSession) {
        chain.debug_session = session;
        chain.cleanup = true;
      } else if (session) {
        chain.cleanup = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      }
      if (!chain.cleanup) chain.status = 'failed';
      save();
    }
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.chains.length === chains.length && report.chains.every(chain => chain.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.chains.filter(chain => chain.status === 'passed').length, total: chains.length, turns: report.chains.reduce((n, chain) => n + chain.turns.length, 0) };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

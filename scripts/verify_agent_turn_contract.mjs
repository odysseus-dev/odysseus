#!/usr/bin/env node
/** Real 7011 DOM → chat_stream → SSE → persisted history verification.
 * node scripts/verify_agent_turn_contract.mjs --families notes --max-turns 4
 * node scripts/verify_agent_turn_contract.mjs --max-turns 80 --total-ms 900000
 * --base-url http://<configured-url>:7011 --preflight-only true checks auth/DOM, no chats.
 * --families all includes supplemental theme/research/sessions/contacts/browser probes.
 * No app imports, fixture seeding, personal auth, cleanup deletes, approvals,
 * model launches, or provider configuration writes. New test chats are retained.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const options = new Map();
for (let i = 0; i < args.length; i += 2) {
  if (!args[i].startsWith('--') || !args[i + 1]) throw Error('Options require --name value');
  options.set(args[i].slice(2), args[i + 1]);
}
const known = new Set(['families', 'max-turns', 'total-ms', 'turn-ms', 'cookie-file', 'endpoint', 'endpoint-id', 'model', 'report', 'matrix-only', 'email-process', 'base-url', 'preflight-only', 'self-test', 'analyze-report', 'pairs', 'email-metadata-only', 'sample-stream', 'picker-route']);
for (const k of options.keys()) if (!known.has(k)) throw Error(`Unknown option ${k}`);
const opt = (k, fallback) => options.get(k) ?? fallback;
const number = (k, fallback, max) => {
  const n = Number(opt(k, fallback));
  if (!Number.isInteger(n) || n < 1 || n > max) throw Error(`Invalid ${k}: ${n}`);
  return n;
};
const baseURL = new URL(opt('base-url', 'http://127.0.0.1:7011'));
if (baseURL.protocol !== 'http:' || baseURL.port !== '7011' || baseURL.pathname !== '/' || baseURL.search || baseURL.hash || baseURL.username || baseURL.password
  || !/^(?:127\.0\.0\.1|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})$/.test(baseURL.hostname)) throw Error('Base URL must be loopback or Tailscale HTTP port 7011');
const base = baseURL.origin;
const preflightOnly = opt('preflight-only', 'false') === 'true';
const emailMetadataOnly = opt('email-metadata-only', 'false') === 'true';
const emailPrompts = ['List my email accounts.', 'Show my email accounts.'];
// Force direct connections for both Playwright's Node HTTP client and Chromium.
// Do not record proxy URLs: they may contain credentials.
const inheritedProxyKeys = Object.keys(process.env).filter(k => /^(https?_proxy|all_proxy|no_proxy)$/i.test(k));
for (const k of inheritedProxyKeys) delete process.env[k];
process.env.NO_PROXY = '*';
process.env.no_proxy = '*';
const owner = 'sft_alex_creator';
const data = (process.env.ODYSSEUS_DATA_DIR || path.join(root, 'data'));
const turnMs = number('turn-ms', 45000, 120000);
const totalMs = number('total-ms', 600000, 1800000);
const maxTurns = number('max-turns', 80, 120);
// Explicit core matrix: shell_files is the tenth family; theme is supplemental.
const families = {
  notes: ['List my notes. Return at most three titles.', ['manage_notes']],
  calendar: ['List my calendar events. Return at most three titles.', ['manage_calendar']],
  email: ['List my email accounts. Return only their names.', ['list_email_accounts']],
  tasks: ['List my scheduled tasks. Return at most three names and statuses.', ['manage_tasks']],
  documents: ['List my documents. Return at most three titles.', ['manage_documents']],
  memory: ['List my saved memories. Return at most three short entries.', ['manage_memory']],
  skills: ['List my skills. Return at most three names.', ['manage_skills']],
  cookbook: ['List configured Cookbook servers. Return only names and status.', ['list_cookbook_servers']],
  search: ['Search the web for GPT-4. Return one official source link.', ['web_search']],
  shell_files: ["Use bash to run this read-only command and report its actual marker and hostname output:\n```sh\nprintf '%s\\n' ODY_SHELL_FILES_READONLY; cat /etc/hostname\n```", ['bash']],
  theme: ['Open the theme settings panel.', ['ui_control']],
  research: ['List my saved research reports. Return at most three titles.', ['manage_research']],
  sessions: ['List my chat sessions. Return at most three names.', ['list_sessions']],
  contacts: ['List my contacts. Return at most three names.', ['manage_contact']],
  notes_search: ['Search my notes for weekly review. Return at most three matching titles.', ['manage_notes']],
  browser: ['Open https://example.com in the private browser and report its heading.', ['private_browser']],
  typo_notes: ['Show my noes.', ['manage_notes']],
  typo_calendar: ["What's my caledar this week?", ['manage_calendar']],
  typo_email: ['What emil accounts do I have?', ['list_email_accounts']],
  typo_tasks: ['List my scheduled taks.', ['manage_tasks']],
  typo_documents: ['List my documnts.', ['manage_documents']],
  typo_memory: ['List my saved memo ries.', ['manage_memory']],
  typo_skills: ['List my skils.', ['manage_skills']],
  typo_cookbook: ['Show cookbok servers.', ['list_cookbook_servers']],
  typo_search: ['Seach the web for the official Python packaging guide.', ['web_search']],
  typo_shell_files: ['Use bssh to run this read-only command: pwd', ['bash']],
  news_followup: ['Latest news in Japan', ['web_search'], 'Tell me more about the flooding?'],
  ambiguous_calendar: ['List my calendar events. Return at most three titles and times.', ['manage_calendar'], 'What time was the second one again?'],
  ambiguous_notes: ['List my notes. Return at most three titles.', ['manage_notes'], 'Show me the second one again.'],
  ambiguous_tasks: ['List my scheduled tasks. Return at most three names and statuses.', ['manage_tasks'], 'What is the status of the second one?'],
  ambiguous_documents: ['List my documents. Return at most three titles.', ['manage_documents'], 'Read the second document and summarize it.', ['manage_documents'], ['documents', 'documents']],
  ambiguous_skills: ['List my skills. Return at most three names.', ['manage_skills'], 'Show me the second skill.', ['manage_skills'], ['skills', 'skills']],
  cookbook_detail: ['List configured Cookbook servers. Return only names and status.', ['list_cookbook_servers'], 'Which one is the default server?'],
  email_inbox: ['List my latest three emails.', ['list_emails'], 'Read the second email and summarize it.', ['read_email'], ['email', 'email']],
  search_open_result: ['Search the web for the official Python packaging guide. Return one official link.', ['web_search'], 'Open that official result and summarize its main recommendation.', ['web_fetch'], ['search_browser', 'search_browser']],
  browser_navigation: ['Open https://example.com in the private browser and report its heading.', ['private_browser'], 'Open the More information link from that page and report the destination heading.', ['private_browser'], ['search_browser', 'search_browser']],
  search_ai: ['Latest news in AI?', ['web_search']],
  search_quantum: ['Any latest info on quantum physics', ['web_search']],
  search_history: ['What year did Ethiopia become independent?', [], 'Can you search'],
  search_comparison: ['What country has best meat?', [], 'Can you look up'],
  search_to_notes: ['look up news in germany', ['web_search'], 'whats my notes', ['manage_notes'], ['search_browser', 'notes']],
  notes_to_search: ['Show my notes. Return at most three titles.', ['manage_notes'], 'seach current stock mraket news', ['web_search'], ['notes', 'search_browser']],
  calendar_to_notes: ['List my calendar events.', ['manage_calendar'], 'now show my noes', ['manage_notes'], ['calendar', 'notes']],
  email_to_calendar_schedule: ['List my email accounts.', ['list_email_accounts'], 'whats my schedule this week?', ['manage_calendar'], ['email', 'calendar']],
  greeting_to_notes: ['hi', [], 'whats my notes', ['manage_notes'], [null, 'notes']],
  browser_to_notes: ['Open https://example.com in the private browser and report its heading.', ['private_browser'], 'Now show my notes. Return at most three titles.', ['manage_notes'], ['search_browser', 'notes']],
};
const core = Object.keys(families).slice(0, 10);
const listFamilies = new Set(['notes', 'calendar', 'email', 'tasks', 'documents', 'memory', 'skills', 'cookbook', 'research', 'sessions', 'contacts']);
for (const family of ['notes', 'calendar', 'email', 'tasks', 'documents', 'memory', 'skills', 'cookbook']) listFamilies.add(`typo_${family}`);
for (const family of ['ambiguous_calendar', 'ambiguous_notes']) listFamilies.add(family);
const webTools = new Set(['web_search', 'web_fetch', 'private_browser', 'youtube_tool']);
const capabilityName = family => ({ search: 'search_browser', browser: 'search_browser', cookbook: 'cookbook_admin', contacts: 'contacts', notes_search: 'notes',
  typo_notes: 'notes', typo_calendar: 'calendar', typo_email: 'email', typo_tasks: 'tasks', typo_documents: 'documents', typo_memory: 'memory',
  typo_skills: 'skills', typo_cookbook: 'cookbook_admin', typo_search: 'search_browser', typo_shell_files: 'shell_files',
  news_followup: 'search_browser', search_ai: 'search_browser', search_quantum: 'search_browser', search_history: 'search_browser', search_comparison: 'search_browser', ambiguous_calendar: 'calendar', ambiguous_notes: 'notes',
  ambiguous_tasks: 'tasks', ambiguous_documents: 'documents', ambiguous_skills: 'skills', cookbook_detail: 'cookbook_admin',
  email_inbox: 'email', search_open_result: 'search_browser', browser_navigation: 'search_browser', browser_to_notes: 'search_browser' }[family] || family);
const selected = opt('families', core.join(',')) === 'all' ? Object.keys(families) : opt('families', core.join(',')).split(',');
for (const f of selected) if (!families[f]) throw Error(`Unknown family ${f}`);
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(root, opt('report', `reports/agent-turn-contract-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep)) throw Error('Reports must be under reports/');
const availablePairs = selected.flatMap(family => ['00', '01', '10', '11'].map(combo => ({ family, combo })));
const requestedPairs = options.has('pairs') ? opt('pairs').split(',') : null;
if (requestedPairs) for (const pair of requestedPairs) if (!availablePairs.some(p => `${p.family}:${p.combo}` === pair)) throw Error(`Unknown selected pair ${pair}`);
const matrix = availablePairs.filter(p => !requestedPairs || requestedPairs.includes(`${p.family}:${p.combo}`));
const report = { run, base, owner, core_source: 'User-required ten families; shell_files executes bash reading /etc/hostname; theme is supplemental',
  core_families: core, network: { proxy: 'disabled', inherited_proxy_keys: inheritedProxyKeys, chromium: '--no-proxy-server', no_proxy: '*' },
  search_probe_query: 'GPT-4', search_probe_basis: 'Alternate public query; changed probe, not a corrected or proven seeded fixture. Original IANA failure retained: irrelevant returned search data, cause unresolved.',
  email_scope: emailMetadataOnly ? 'Exact account-metadata prompts only; referential email followup untested; automatic /api/email reads blocked' : 'Email requires separately verified runtime fixture mode',
  limits: { maxTurns, turnMs, totalMs }, matrix, planned_turns: matrix.length * 2,
  sessions: [], turns: [], blocked: [], guarded_requests: [], not_run: [], status: 'running',
  limitations: ['Prompts and browser request guards are not a server-side tool sandbox.',
    'Only test chats are created; fixture rows are not seeded or deleted.',
    'List-family followups are referential and require the same family tool; search/browser followups summarize without new tools.',
    'No claim of full family coverage when cases are blocked or budget-limited.',
    'shell_files requires real bash output; SFT policy refusal is a failure, never a substitute pass. Dedicated file tools may remain disabled.'] };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
const check = (condition, message) => { if (!condition) throw Error(message); };
const bounded = async (promise, ms, name) => {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(`${name} timeout (${ms}ms)`)), ms); })]); }
  finally { clearTimeout(timer); }
};
const normalize = s => String(s || '').replace(/\s+/g, ' ').trim();
const countWords = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
function boundedSubsetFromHistory(prompt, answer, prior) {
  const match = String(prompt || '').match(/\bat most\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b/i);
  if (!match || !answer || !prior) return false;
  const limit = /^\d+$/.test(match[1]) ? Number(match[1]) : countWords[match[1].toLowerCase()];
  const tail = String(answer).includes(':') ? String(answer).split(':').slice(1).join(':') : String(answer);
  let items = tail.split(/\n/).map(s => s.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter(Boolean);
  if (items.length === 1 && items[0].includes(',')) items = items[0].split(',').map(s => s.trim()).filter(Boolean);
  const comparable = value => normalize(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  items = items.map(s => comparable(s).replace(/[.;]+$/, '')).filter(Boolean);
  const haystack = comparable(prior);
  return items.length > 0 && items.length <= limit && items.every(item => haystack.includes(item));
}
// Conservative fixture-specific detection, including a preamble below a thinking label.
const noVisibleLeak = text => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|(?:^|\n)\s*(?:The user (?:wants|is asking|requests)\b|Analyze the Request:)/i.test(text);
// Playwright errors may embed request headers (including session cookies).
const safeError = error => String(error).split('\n')[0].replace(/odysseus_session=[^\s;]+/g, 'odysseus_session=[REDACTED]');
const bare = s => String(s || '').replace(/^mcp__email__/, '');
function parseSSE(body) {
  return body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
    const raw = frame.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
    if (!raw) return [];
    if (raw === '[DONE]') return [{ type: 'done' }];
    try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
  });
}
function formFields(request) {
  const body = request.postData() || '';
  const fields = {};
  for (const name of ['session', 'session_id', 'mode', 'allow_web_search', 'use_web', 'use_research', 'plan_mode', 'allow_bash', 'endpoint_id', 'model', 'thinking_mode']) {
    fields[name] = body.match(new RegExp(`name="${name}"\\r?\\n\\r?\\n([^\\r\\n]*)`))?.[1] ?? null;
  }
  return fields;
}
async function snapshot(page) {
  return page.locator('#chat-history').evaluate(el => {
    const visible = n => !!(n.getClientRects().length) && getComputedStyle(n).visibility !== 'hidden';
    const users = [...el.querySelectorAll('.msg-user')].filter(visible);
    const last = users.at(-1);
    const after = n => last && !!(last.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING);
    const bubbles = [...el.querySelectorAll('.msg-ai')].filter(n => visible(n) && after(n));
    return { users: users.length,
      bubbles: bubbles.map(n => ({ text: (n.querySelector('.body')?.innerText || '').trim(), raw: n.dataset.raw || '', db_id: n.dataset.dbId || '' })),
      anchors: [...el.querySelectorAll('a[href]')].filter(n => visible(n) && after(n)).map(n => ({ text: n.innerText, href: n.getAttribute('href') })),
      tool_cards: [...el.querySelectorAll('.agent-thread')].filter(n => visible(n) && after(n)).length,
      streaming: el.querySelectorAll('.streaming').length };
  });
}
let browser;
let context;
let stopTimer;
let activeTurn;
let attempted = 0;
const started = Date.now();
try {
  if (options.has('analyze-report')) {
    const sourcePath = path.resolve(root, opt('analyze-report'));
    check(sourcePath.startsWith(path.join(root, 'reports') + path.sep) && sourcePath !== reportPath, 'Analysis needs a distinct source report under reports/');
    const source = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
    Object.assign(report, source);
    report.blocked = (source.blocked || []).filter(item => !(
      item.request === '/api/client-perf'
      && item.method === 'POST'
      && item.reason === 'Browser write guard'
    ));
    report.analysis = { source: sourcePath, at: new Date().toISOString(), source_status: source.status,
      method: 'Offline re-score of captured DOM; no browser or inference requests. Original checks retained; harmless blocked client performance telemetry is reclassified as guarded.',
      notes_limit_attribution: 'Existing canonical deterministic-summary shortcut; harness functional failure, not attributed to model.' };
    report.turns = source.turns.map(t => {
      const contract = t.sse?.audits.find(e => e.type === 'turn_contract');
      const shape = contract && ['required', 'offered', 'executable', 'capabilities'].every(k => Array.isArray(contract[k]));
      const evidence = { contract_captured: !!contract,
        set_invariant: !!shape && contract.required.every(n => contract.offered.includes(n)) && contract.offered.every(n => contract.executable.includes(n)),
        capability: !!shape && (!t.expected_capability || contract.capabilities.includes(t.expected_capability)),
        forbidden_offers_absent: !!shape && contract.offered.every(n => !(t.forbidden_tools || []).includes(bare(n))),
        execution_within_offered: !!shape && (t.sse?.tools || []).filter(e => e.type === 'tool_start').every(e => contract.offered.map(bare).includes(bare(e.tool))) };
      if (!t.dom || !t.checks) return { ...t, contract_evidence_analysis: evidence };
      const checks = { ...t.checks, no_visible_leak: noVisibleLeak(t.dom.bubbles.map(b => b.text).join('\n')) };
      return { ...t, contract_evidence_analysis: evidence, original_checks: t.checks, original_status: t.status, checks,
        status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' };
    });
    attempted = source.attempted_turns ?? source.turns.length;
    report.status = source.status === 'running' ? 'analysis-in-progress'
      : report.not_run.length || report.blocked.length ? 'incomplete'
      : report.turns.every(t => t.status === 'passed') ? 'passed' : 'failed';
  } else if (opt('self-test', 'false') === 'true') {
    check(core.length === 10 && core[9] === 'shell_files' && !core.includes('theme'), 'Core matrix mismatch');
    check(matrix.length === (requestedPairs ? new Set(requestedPairs).size : selected.length * 4), 'Toggle matrix mismatch');
    check(parseSSE('data: {"type":"tool_start","tool":"bash"}\r\n\r\ndata: [DONE]\r\n\r\n').at(-1).type === 'done', 'SSE framing regression');
    check(parseSSE('data: broken\n\n')[0].type === 'invalid_sse', 'Malformed SSE must fail');
    check(formFields({ postData: () => 'name="allow_web_search"\r\n\r\nfalse\r\n' }).allow_web_search === 'false', 'Toggle field parser');
    check(!safeError('Timeout\n cookie: odysseus_session=secret').includes('secret'), 'Error redaction');
    check(!noVisibleLeak('View thinking process\n\nThe user wants a list of emails'), 'Fixture reasoning preamble detection');
    check(noVisibleLeak('Here are your three notes.'), 'Normal fixture answer must not trigger leakage');
    check(boundedSubsetFromHistory('List those again, at most three.', 'Servers: kierkegaard, Odysseus, Ajax.', 'Servers: kierkegaard (local), Odysseus, Ajax, kierk.'), 'Grounded bounded subset detection');
    check(boundedSubsetFromHistory('List those again, at most three.', '- Search seed prompts [Pinned]', '- [opaque-id] **Search seed prompts** [PINNED]'), 'Grounded structured tool-output detection');
    check(!boundedSubsetFromHistory('List those again, at most two.', 'Servers: kierkegaard, Odysseus, Ajax.', 'Servers: kierkegaard, Odysseus, Ajax.'), 'Bounded subset limit enforcement');
    report.self_tests = 11; report.status = 'self-test-passed';
  } else if (opt('matrix-only', 'false') === 'true') {
    report.status = 'matrix-only';
  } else {
    const cookieFile = opt('cookie-file', `${data}/sessions.json`);
    const sessions = JSON.parse(fs.readFileSync(cookieFile, 'utf8'));
    const token = Object.entries(sessions).find(([, v]) => v?.username === owner)?.[0];
    check(token, `No existing ${owner} auth session; refusing personal fallback`);
    const endpoint = opt("endpoint", '') || (() => { throw new Error("--endpoint is required"); })();
    const endpointURL = new URL(endpoint);
    check(endpointURL.protocol === 'http:' && (/^(127\.|10\.|192\.168\.|100\.)/.test(endpointURL.hostname)), 'Only explicit local/private inference endpoints allowed');
    report.model = { endpoint, endpoint_id: opt('endpoint-id', 'preheret'), model: opt('model', 'odysseus-qwen3.5-tools-pre-heretic') };
    browser = await chromium.launch({ headless: true, timeout: 15000, args: ['--no-proxy-server'] });
    context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
    context.setDefaultTimeout(10000);
    await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
    const statusRes = await context.request.get(`${base}/api/auth/status`, { timeout: 10000 });
    const status = await statusRes.json();
    check(status.authenticated && status.username === owner, 'Authenticated identity mismatch');
    report.auth = { username: status.username, authenticated: status.authenticated, is_admin: status.is_admin };
    const versionRes = await context.request.get(`${base}/api/version`, { timeout: 5000 });
    report.deployment = versionRes.ok() ? await versionRes.json() : { status: versionRes.status() };
    const fixture = JSON.parse(fs.readFileSync(`${data}/fixture_email_messages.json`, 'utf8'));
    report.fixture_email_rows = fixture.messages.filter(m => m.owner === owner).length;
    let emailSafe = false;
    if (options.has('email-process')) {
      const pid = opt('email-process'); check(/^\d+$/.test(pid), 'Invalid email PID');
      const env = fs.readFileSync(`/proc/${pid}/environ`, 'utf8').split('\0');
      emailSafe = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').includes('email_server.py')
        && env.includes('ODYSSEUS_EMAIL_FIXTURE=1') && env.includes(`ODYSSEUS_DATA_DIR=${data}`)
        && report.fixture_email_rows > 0 && fixture.messages.every(m => m.owner);
    }
    report.email_fixture_runtime_verified = emailSafe;
    // Observe the original request; never inject mode/toggle fields or fake SSE.
    await context.route('**/*', async route => {
      const req = route.request(); const url = new URL(req.url());
      if (url.origin === base && url.pathname.startsWith('/api/email/')) {
        report.guarded_requests.push({ request: url.pathname, method: req.method(), reason: 'No mailbox network calls: block automatic email UI requests' });
        await route.abort('blockedbyclient'); return;
      }
      const writing = !['GET', 'HEAD', 'OPTIONS'].includes(req.method());
      const allowed = !preflightOnly && url.origin === base && (url.pathname === '/api/session' && req.method() === 'POST'
        || url.pathname === '/api/chat_stream' && req.method() === 'POST'
        || report.sessions.some(s => url.pathname === `/api/session/${s.id}`) && ['PUT', 'PATCH'].includes(req.method())
        || report.sessions.some(s => url.pathname === `/api/session/${s.id}/generation-settings`) && req.method() === 'POST');
      if (writing && !allowed) {
        const guarded = { request: url.pathname, method: req.method(), reason: 'Browser write guard' };
        report.guarded_requests.push(guarded);
        // Expected background writes are intentionally suppressed, not missing tests.
        // Keep unexpected blocked requests visible as readiness blockers.
        if (!['/api/activity/heartbeat', '/api/calendar/sync', '/api/tasks/notification-logs', '/api/client-perf'].includes(url.pathname)) report.blocked.push(guarded);
        await route.abort('blockedbyclient'); return;
      }
      if (url.pathname === '/api/chat_stream' && activeTurn) activeTurn.requests.push(formFields(req));
      await route.continue();
    });
    let page = await context.newPage();
    const observePage = p => p.on('pageerror', error => { if (activeTurn) (activeTurn.page_errors ||= []).push(error.message); });
    observePage(page);
    stopTimer = setTimeout(() => { report.blocked.push({ reason: 'Global deadline; browser closed, server cancellation not guaranteed' }); void browser.close().catch(() => {}); }, Math.max(1, totalMs - (Date.now() - started)));
    if (preflightOnly) {
      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForFunction(() => window.sessionModule?.loadSessions && window.chatModule);
      if (!report.loaded_scripts) report.loaded_scripts = await page.locator('script[src]').evaluateAll(nodes => nodes.map(n => n.getAttribute('src')));
      report.dom_preflight = {};
      for (const selector of ['textarea#message:visible', '#chat-history', '#mode-agent-btn', '#web-toggle', '#web-toggle-btn', '#bash-toggle', '#bash-toggle-btn']) {
        report.dom_preflight[selector] = await page.locator(selector).count();
        check(report.dom_preflight[selector] === 1, `Missing or duplicated DOM anchor ${selector}`);
      }
    }
    for (const item of preflightOnly ? [] : matrix) {
      if (attempted >= maxTurns || Date.now() - started + turnMs * Math.min(2, maxTurns - attempted) > totalMs) {
        report.not_run.push({ ...item, reason: 'Call/time budget' }); continue;
      }
      if (item.family === 'email' && !emailSafe && !emailMetadataOnly) {
        report.blocked.push({ ...item, reason: 'Email runtime fixture mode not proven; no email turn sent' }); continue;
      }
      let caseSession;
      try {
      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForFunction(() => window.sessionModule?.loadSessions && window.chatModule);
      if (!report.loaded_scripts) report.loaded_scripts = await page.locator('script[src]').evaluateAll(nodes => nodes.map(n => n.getAttribute('src')));
      const id = await page.evaluate(async ({ name, model }) => {
        const body = new FormData();
        for (const [k, v] of Object.entries({ name, endpoint_url: model.endpoint, endpoint_id: model.endpoint_id, model: model.model, skip_validation: 'true', rag: 'false' })) body.append(k, v);
        const res = await fetch('/api/session', { method: 'POST', body, signal: AbortSignal.timeout(10000) });
        if (!res.ok) throw Error(`Session creation HTTP ${res.status}`);
        return (await res.json()).id;
      }, { name: `[verify-agent-contract ${run}] ${item.family}-${item.combo}`, model: opt('picker-route', 'false') === 'true'
        ? { ...report.model, endpoint: endpoint, endpoint_id: 'preheret' } : report.model });
      check(id, 'Missing session ID'); caseSession = id; report.sessions.push({ ...item, id }); save();
      await page.evaluate(async sid => { await window.sessionModule.loadSessions(); await window.sessionModule.selectSession(sid, { showLoading: false }); }, id);
      await page.waitForFunction(sid => window.sessionModule.getCurrentSessionId() === sid, id);
      if (opt('picker-route', 'false') === 'true') {
        check(report.model.endpoint_id === 'cleanv3', 'Picker test requires the cleanv3 target');
        await page.locator('#model-picker-btn').click();
        await page.locator('#model-picker-search').fill('No-RAG preview');
        const target = page.locator('#model-picker-menu .model-switch-item').filter({ hasText: 'Tools v3 — No-RAG preview' }).first();
        await target.waitFor({ state: 'visible' });
        await target.click();
        await page.waitForFunction(() => !window.__odysseusModelSwitchPromise);
        await page.waitForFunction(() => document.querySelector('#model-picker-label')?.textContent.includes('No-RAG preview'));
        report.sessions.at(-1).picker_click_verified = true;
      }
      await page.locator('#chat-context-pill:not(.loading)').click();
      const thinkingSwitch = page.locator('.chat-context-popup .chat-context-toggle-row').filter({ hasText: 'Thinking' }).locator('[role="switch"]');
      const originalThinking = await thinkingSwitch.getAttribute('aria-checked');
      check(['true', 'false'].includes(originalThinking), 'Cannot establish UI thinking state');
      if (originalThinking === 'true') {
        const updated = page.waitForResponse(r => new URL(r.url()).pathname === `/api/session/${id}/generation-settings` && r.request().method() === 'POST');
        updated.catch(() => {});
        await thinkingSwitch.click();
        check((await updated).ok(), 'Test-session thinking-off update failed');
      }
      check(await thinkingSwitch.getAttribute('aria-checked') === 'false', 'UI thinking switch must be off');
      const generationResponse = await context.request.get(`${base}/api/session/${id}/context`, { timeout: 10000 });
      check(generationResponse.ok(), 'Cannot read test-session generation settings');
      const generation = await generationResponse.json();
      check(generation.thinking_mode === 'off', 'Stored test-session thinking mode must be off');
      const generationEvidence = { thinking_mode: generation.thinking_mode, ui_thinking_before: originalThinking,
        ui_thinking_after: 'false', changed_test_session_only: originalThinking === 'true',
        temperature_override: generation.temperature_override, max_tokens_override: generation.max_tokens_override };
      report.sessions.at(-1).generation_settings = generationEvidence;
      await page.locator('textarea#message:visible').click();
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      for (const [toggle, button] of [['research-toggle', 'research-toggle-btn'], ['rag-toggle', 'rag-indicator-btn'], ['bash-toggle', 'bash-toggle-btn']]) {
        const el = page.locator(`#${toggle}`);
        if (await el.count() && await el.isChecked()) {
          await page.locator(`#${button}`).click();
          check(!await el.isChecked(), `Could not disable ${toggle}`);
        }
      }
      if (['shell_files', 'typo_shell_files'].includes(item.family) && !await page.locator('#bash-toggle').isChecked()) {
        await page.locator('#bash-toggle-btn').click();
        check(await page.locator('#bash-toggle').isChecked(), 'Shell toggle did not enable');
      }
      for (let turn = 0; turn < 2; turn++) {
        if (attempted >= maxTurns) { report.not_run.push({ ...item, turn, reason: 'Call budget' }); break; }
        const web = item.combo[turn] === '1';
        if (await page.locator('#web-toggle').isChecked() !== web) await page.locator('#web-toggle-btn').click();
        check(await page.locator('#web-toggle').isChecked() === web, 'Web toggle click did not update checkbox');
        const regression = ['search_ai', 'search_quantum', 'search_history', 'search_comparison', 'greeting_to_notes'].includes(item.family);
        const prompt = item.family === 'email' && emailMetadataOnly ? emailPrompts[turn] : turn === 0 ? (regression ? families[item.family][0] : `${families[item.family][0]} Read-only inspection; do not change data or send messages. Keep the answer concise.`)
          : families[item.family][2] ? families[item.family][2]
          : listFamilies.has(item.family) ? 'List those again, at most three. Read-only; do not change data or send messages.'
          : ['shell_files', 'typo_shell_files'].includes(item.family) ? 'Run that same read-only command again and report its actual output.'
          : 'Summarize your preceding result in one sentence. Do not use any tools.';
        const inheritedTool = turn === 1 && (families[item.family][3] || (listFamilies.has(item.family) && item.family !== 'ambiguous_calendar')
          || ['shell_files', 'typo_shell_files', 'news_followup', 'search_history', 'search_comparison'].includes(item.family));
        const expected = turn === 1 && families[item.family][3] ? families[item.family][3] : regression && inheritedTool ? ['web_search'] : (turn === 1 && item.family === 'ambiguous_calendar')
          || turn === 1 && !inheritedTool || ['search', 'typo_search'].includes(item.family) && !web ? [] : families[item.family][1];
        const current = { ...item, turn, session_id: id, web, prompt, expected_tools: expected,
          generation_settings: generationEvidence,
          expected_capability: (turn === 0 || inheritedTool) && expected.length ? (families[item.family][4]?.[turn] || capabilityName(item.family)) : null,
          forbidden_tools: ['notes', 'calendar', 'email', 'tasks', 'documents', 'memory', 'skills', 'cookbook', 'shell_files'].includes(item.family) ? [...webTools] : [],
          followup_contract: turn === 0 ? null : item.family === 'email' && emailMetadataOnly ? 'explicit-account-metadata; referential-untested' : inheritedTool ? 'inherited-read-only-capability' : 'summarize-no-tools', requests: [], status: 'running' };
        activeTurn = current; report.turns.push(current); attempted++; save();
        const turnStart = Date.now();
        try {
          await bounded((async () => {
            const before = await page.locator('#chat-history .msg-user').count();
            if (opt('sample-stream', 'false') === 'true') await page.evaluate(expectedUsers => {
              clearInterval(window.__verifyLengthTimer);
              window.__verifyRoundOneObserver?.disconnect();
              const started = performance.now();
              window.__verifyLengthSamples = [];
              window.__verifyRoundOneIdentity = { initial_seen: false, first_token_seen: false, replaced_before_first_token: false, same_node_at_first_token: false };
              window.__verifyInitialRoundBubble = null;
              const inspectRoundOne = () => {
                const root = document.querySelector('#chat-history');
                const users = root?.querySelectorAll('.msg-user');
                if (!users || users.length < expectedUsers) return;
                const user = users[users.length - 1];
                const bubbles = [...root.querySelectorAll('.msg-ai')].filter(node => user.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
                const latest = bubbles.at(-1) || null;
                if (!window.__verifyInitialRoundBubble && latest) {
                  window.__verifyInitialRoundBubble = latest;
                  window.__verifyRoundOneIdentity.initial_seen = true;
                }
                const first = window.__verifyInitialRoundBubble;
                const hasFirstToken = bubbles.some(node => String(node.querySelector('.stream-content')?.textContent || '').length > 0);
                if (first && !first.isConnected && !window.__verifyRoundOneIdentity.first_token_seen) {
                  window.__verifyRoundOneIdentity.replaced_before_first_token = true;
                }
                if (hasFirstToken && !window.__verifyRoundOneIdentity.first_token_seen) {
                  window.__verifyRoundOneIdentity.first_token_seen = true;
                  window.__verifyRoundOneIdentity.same_node_at_first_token = first === latest;
                }
              };
              window.__verifyRoundOneObserver = new MutationObserver(inspectRoundOne);
              window.__verifyRoundOneObserver.observe(document.querySelector('#chat-history'), { childList: true, subtree: true, characterData: true });
              window.__verifyLengthTimer = setInterval(() => {
                inspectRoundOne();
                const root = document.querySelector('#chat-history');
                const users = root?.querySelectorAll('.msg-user');
                if (!users || users.length < expectedUsers) return;
                const user = users[users.length - 1];
                let length = 0;
                for (const body of root.querySelectorAll('.msg-ai .body')) {
                  if (!(user.compareDocumentPosition(body) & Node.DOCUMENT_POSITION_FOLLOWING) || !body.getClientRects().length) continue;
                  length += body.innerText.length;
                }
                // Telemetry stores no response text; cap memory for interrupted turns.
                if (window.__verifyLengthSamples.length < 1000) window.__verifyLengthSamples.push({ ms: Math.round(performance.now() - started), length });
              }, 200);
            }, before + 1);
            const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: turnMs });
            // Attach rejection handler before interacting; no dangling rejection on UI failure.
            responsePromise.catch(() => {});
            await page.locator('textarea#message:visible').fill(prompt);
            await page.locator('textarea#message:visible').press('Enter');
            const response = await responsePromise;
            current.http = { status: response.status(), headers_ms: Date.now() - turnStart,
              headers: Object.fromEntries(Object.entries(response.headers()).filter(([k]) => ['content-type', 'content-encoding', 'cache-control', 'x-accel-buffering', 'x-odysseus-run-id'].includes(k))) };
            save();
            if (!response.ok()) {
              current.http.error_body = (await response.text()).slice(0, 1000);
              throw Error(`Chat HTTP ${response.status()} before SSE/DOM validation`);
            }
            check(/text\/event-stream/.test(response.headers()['content-type'] || ''), 'Chat response is not SSE');
            const events = parseSSE(await response.text());
            current.response_complete_ms = Date.now() - turnStart;
            current.sse = { events: events.length, types: events.reduce((a, e) => { a[e.type || 'delta'] = (a[e.type || 'delta'] || 0) + 1; return a; }, {}),
              tools: events.filter(e => ['tool_start', 'tool_output'].includes(e.type)).map(e => ({ type: e.type, tool: e.tool, exit_code: e.exit_code, command: e.command, output: e.output, error: e.error })),
              metrics: events.filter(e => e.type === 'metrics'),
              audits: events.filter(e => /contract|routing|resolution/i.test(e.type || '')),
              mode_and_model: events.filter(e => ['turn_mode', 'model_info'].includes(e.type)),
              final: events.filter(e => e.type === 'final_response').map(e => e.content || ''),
              errors: events.filter(e => ['error', 'invalid_sse', 'tool_approval_required'].includes(e.type)) };
            // SSE audits survive DOM failures; stale classes are a separate UI check.
            current.contract = current.sse.audits.find(e => e.type === 'turn_contract') || null;
            save();
            if (item.family === 'email' && emailMetadataOnly) {
              const offered = current.contract?.offered?.map(bare);
              check(Array.isArray(offered) && offered.includes('list_email_accounts') && offered.every(n => ['list_email_accounts', 'ask_user', 'update_plan'].includes(n)), 'EMAIL_SAFETY: actual offered tools exceed verified metadata-only scope');
            }
            try { await page.waitForFunction(() => !document.querySelector('#chat-history .streaming'), null, { timeout: 10000 }); }
            catch (error) { current.dom_settle_error = safeError(error); }
            current.dom = await snapshot(page);
            const turnMetrics = page.locator('#chat-history .response-metrics').last();
            if (await turnMetrics.count()) {
              current.metrics_ui = { footer: (await turnMetrics.innerText()).trim() };
              await turnMetrics.click();
              const popup = page.locator('body > .ctx-popup').last();
              if (await popup.count()) current.metrics_ui.details = (await popup.innerText()).trim();
              await page.keyboard.press('Escape');
            }
            if (opt('sample-stream', 'false') === 'true') {
              current.round_one_identity = await page.evaluate(() => {
                window.__verifyRoundOneObserver?.disconnect();
                return window.__verifyRoundOneIdentity || null;
              });
            }
            const historyRes = await context.request.get(`${base}/api/history/${encodeURIComponent(id)}`, { timeout: 10000 });
            check(historyRes.ok(), 'History request failed');
            const history = (await historyRes.json()).history || [];
            const lastUser = history.map(r => r.role).lastIndexOf('user');
            const assistants = history.slice(lastUser + 1).filter(r => r.role === 'assistant');
            current.history = assistants.map(r => ({ content: r.content, tool_events: r.tool_events || r.metadata?.tool_events || [], metadata: { actual_model: r.metadata?.actual_model, requested_model: r.metadata?.requested_model } }));
            const text = current.dom.bubbles.map(b => b.text).filter(Boolean);
            const canonicalRaw = assistants.at(-1)?.content || '';
            const canonical = normalize(canonicalRaw);
            const tools = current.sse.tools.filter(e => e.type === 'tool_start').map(e => bare(e.tool));
            const contract = current.sse.audits.find(e => e.type === 'turn_contract');
            current.contract = contract || null;
            const contractShape = contract && ['capabilities', 'required', 'offered', 'executable'].every(k => Array.isArray(contract[k]));
            const dupParagraphs = text.flatMap(t => t.split(/\n\s*\n/).map(normalize)).filter(t => t.length >= 60);
            const cleanPreview = contract?.selection_mode === 'clean_compact_v3_preview';
            const priorTurn = report.turns.find(t => t.session_id === id && t.turn === 0 && t !== current);
            const priorEvidence = [priorTurn?.history?.at(-1)?.content,
              ...(priorTurn?.history?.at(-1)?.tool_events || []).map(event => event.output)].filter(Boolean);
            const repeatedReadFromHistory = cleanPreview && turn === 1 && inheritedTool && tools.length === 0
              && !!canonical && priorEvidence.some(evidence =>
                canonical === normalize(evidence) || boundedSubsetFromHistory(prompt, canonicalRaw, evidence));
            current.grounding_evidence = turn === 1 && inheritedTool ? {
              clean_preview: cleanPreview,
              no_new_tool: tools.length === 0,
              canonical_answer: Boolean(canonical),
              prior_evidence_count: priorEvidence.length,
              evidence_matches: priorEvidence.map(evidence =>
                canonical === normalize(evidence) || boundedSubsetFromHistory(prompt, canonicalRaw, evidence)),
              accepted: repeatedReadFromHistory,
            } : null;
            const expectedToolObserved = expected.length
              ? tools.some(t => expected.includes(t))
                || (inheritedTool && current.expected_capability === 'search_browser' && tools.some(t => webTools.has(t)))
              : tools.length === 0;
            current.checks = {
              http_ok: response.ok(), sse_type: /text\/event-stream/.test(response.headers()['content-type'] || ''),
              terminal: events.some(e => e.type === 'done'), no_sse_errors: current.sse.errors.length === 0,
              one_post: current.requests.length === 1,
              agent_request: current.requests[0]?.mode === 'agent',
              thinking_off: current.generation_settings.thinking_mode === 'off' && current.generation_settings.ui_thinking_after === 'false' && current.requests[0]?.thinking_mode !== 'on',
              web_request: current.requests[0]?.allow_web_search === String(web),
              no_presearch_or_research: current.requests[0]?.use_web !== 'true' && current.requests[0]?.use_research !== 'true',
              session_request: [current.requests[0]?.session, current.requests[0]?.session_id].includes(id),
              one_new_user: current.dom.users === before + 1,
              dom_idle: current.dom.streaming === 0,
              visible_answer: text.length > 0,
              no_canned_failure: !/currently permitted tools|can[’']?t perform that operation in this preview|no changes were made|search query likely needs better terms|not enough clear evidence|model provider returned no usable output/i.test(canonical),
              no_duplicate_bubbles: new Set(text.map(normalize)).size === text.length,
              no_duplicate_paragraphs: new Set(dupParagraphs).size === dupParagraphs.length,
              history_answer_visible: !!canonical && current.dom.bubbles.some(b => normalize(b.raw) === canonical || normalize(b.text) === canonical),
              expected_tool: repeatedReadFromHistory || expectedToolObserved,
              no_forbidden_tools: tools.every(t => !current.forbidden_tools.includes(t)),
              contract_audit_captured: current.sse.audits.some(e => /contract/i.test(e.type || '')),
              requested_preview_active: report.model.endpoint_id !== 'cleanv3' || current.contract?.selection_mode === 'clean_compact_v3_preview',
              contract_set_invariant: !!contractShape && contract.required.every(t => contract.offered.includes(t)) && contract.offered.every(t => contract.executable.includes(t)),
              contract_family: !!contractShape && (!current.expected_capability || contract.capabilities.includes(current.expected_capability)),
              contract_no_forbidden_offers: !!contractShape && contract.offered.every(t => cleanPreview
                ? (web || item.family.startsWith('browser') || !['web_search', 'web_fetch', 'private_browser', 'youtube_tool', 'pdf_extract', 'search_hf_models'].includes(bare(t)))
                : !current.forbidden_tools.includes(bare(t))),
              executed_within_contract: !!contractShape && tools.every(t => contract.offered.map(bare).includes(t)),
              at_most_three_notes: item.family !== 'notes' || new Set(current.dom.anchors.filter(a => a.href.startsWith('#note-') && a.text.trim()).map(a => a.href)).size <= 3,
              shell_request: item.family !== 'shell_files' || current.requests[0]?.allow_bash === 'true',
              shell_executed: item.family !== 'shell_files' || current.sse.tools.some(e => e.type === 'tool_output' && bare(e.tool) === 'bash' && e.exit_code === 0 && String(e.output).includes('ODY_SHELL_FILES_READONLY')),
              tool_success: current.sse.tools.every(e => e.exit_code == null || e.exit_code === 0),
              no_visible_leak: noVisibleLeak(text.join('\n')),
              anchors_resolved: current.dom.anchors.every(a => !!a.href && !/^javascript:/i.test(a.href) && !/__PLACEHOLDER__|undefined/.test(a.href)),
              followup_grounded: repeatedReadFromHistory || turn === 0 || (inheritedTool ? expectedToolObserved : !/no preceding|no previous|no prior/i.test(text.join(' ')) && text.some(t => normalize(t).length > 0)),
              first_round_node_stable: opt('sample-stream', 'false') !== 'true' || !!(
                current.round_one_identity?.initial_seen
                && current.round_one_identity?.first_token_seen
                && !current.round_one_identity?.replaced_before_first_token
                && (tools.length > 0 || current.round_one_identity?.same_node_at_first_token)
              ),
            };
            current.status = Object.values(current.checks).every(Boolean) ? 'passed' : 'failed';
          })(), turnMs, 'Turn');
        } catch (error) {
          current.status = 'failed'; current.error = safeError(error);
          current.failure_stage = current.http?.status >= 400 ? 'server-http' : current.sse ? 'dom-or-history' : 'request-or-stream';
          throw error;
        } finally {
          if (opt('sample-stream', 'false') === 'true') {
            try {
              current.visible_length_samples = await bounded(page.evaluate(() => { clearInterval(window.__verifyLengthTimer); return window.__verifyLengthSamples || []; }), 1500, 'Length samples');
              const beforeEnd = current.visible_length_samples.filter(s => s.ms < (current.response_complete_ms || 0));
              current.intermediate_visible_growth = beforeEnd.some((s, i) => i > 0 && beforeEnd[i - 1].length > 0 && s.length > beforeEnd[i - 1].length);
            } catch (error) { current.length_sample_error = safeError(error); }
          }
          current.elapsed_ms = Date.now() - turnStart; save();
          console.log(JSON.stringify({ family: item.family, combo: item.combo, turn, status: current.status, failed: Object.entries(current.checks || {}).filter(([, v]) => !v).map(([k]) => k), error: current.error }));
        }
        if (turn === 0 && opt('picker-route', 'false') === 'true') {
          // selectSession is used by this driver without router navigation;
          // reload the actual chat URL, as a user does from its permalink.
          await page.evaluate(sid => history.replaceState(null, '', `/#${sid}`), id);
          await page.reload({ waitUntil: 'domcontentloaded' });
          await page.waitForFunction(sid => window.sessionModule?.getCurrentSessionId() === sid, id, { timeout: 20000 });
          await page.waitForFunction(() => document.querySelector('#model-picker-label')?.textContent.includes('No-RAG preview'));
          const savedFirstAnswer = current.history?.at(-1)?.content || '';
          await page.waitForFunction(expected => [...document.querySelectorAll('#chat-history .msg-ai')]
            .some(n => (n.dataset.raw || n.querySelector('.body')?.textContent || '').trim() === expected.trim()), savedFirstAnswer);
          await page.locator('#chat-context-pill:not(.loading)').waitFor({ state: 'visible' });
          report.sessions.at(-1).picker_reload_verified = true;
          save();
        }
      }
      } catch (error) {
        const reason = safeError(error);
        if (reason.includes('EMAIL_SAFETY:')) throw error;
        let inactive = !caseSession;
        let streamStatus = { status: 'no-session-created' };
        if (caseSession) {
          try {
            const statusResponse = await context.request.get(`${base}/api/chat/stream_status/${encodeURIComponent(caseSession)}`, { timeout: 5000 });
            streamStatus = statusResponse.status() === 404 ? { status: 'no-active-stream', http: 404 } : await statusResponse.json();
            inactive = statusResponse.status() === 404 || statusResponse.ok() && ['done', 'error'].includes(streamStatus.status);
          } catch (statusError) { streamStatus = { status: 'unknown', error: safeError(statusError) }; }
        }
        // Task-authorized cleanup: only this created session and its captured run ID.
        if (!inactive && streamStatus.status === 'streaming' && report.sessions.some(s => s.id === caseSession)
          && activeTurn?.session_id === caseSession && activeTurn.http?.headers?.['x-odysseus-run-id']) {
          const runId = activeTurn.http.headers['x-odysseus-run-id'];
          const stopped = await context.request.post(`${base}/api/chat/stop/${encodeURIComponent(caseSession)}`, {
            headers: { 'X-Odysseus-Run-Id': runId }, timeout: 5000 });
          const cleanup = { session_id: caseSession, run_id: runId, http: stopped.status(), result: await stopped.json() };
          for (let attempt = 0; attempt < 5; attempt++) {
            const verification = await context.request.get(`${base}/api/chat/stream_status/${encodeURIComponent(caseSession)}`, { timeout: 3000 });
            cleanup.verified_status_http = verification.status();
            if (verification.status() === 404) { inactive = true; streamStatus = { status: 'no-active-stream', http: 404 }; break; }
            await new Promise(resolve => setTimeout(resolve, 200));
          }
          activeTurn.exact_run_cleanup = cleanup;
        }
        report.blocked.push({ ...item, reason, session_id: caseSession, stream_status: streamStatus, safe_to_continue: inactive });
        save();
        if (!inactive) throw Error(`Cannot continue safely: active/unknown stream for ${caseSession}`);
        // Cancel any outstanding client-side UI work before the independent pair.
        await page.close().catch(() => {});
        page = await context.newPage(); observePage(page); activeTurn = undefined;
        console.log(JSON.stringify({ ...item, status: 'case-failed-continuing', reason, stream_status: streamStatus.status }));
      }
    }
    report.status = preflightOnly ? 'preflight-passed' : report.not_run.length || report.blocked.length ? 'incomplete' : report.turns.every(t => t.status === 'passed') ? 'passed' : 'failed';
  }
} catch (error) {
  report.status = 'blocked'; report.blocked.push({ reason: safeError(error) });
} finally {
  clearTimeout(stopTimer);
  if (browser) await bounded(browser.close(), 10000, 'Browser close').catch(() => {});
  if (!options.has('analyze-report')) report.elapsed_ms = Date.now() - started;
  report.attempted_turns = attempted;
  report.unattempted_turns = report.planned_turns - attempted;
  const coverageMatrix = options.has('analyze-report') ? report.matrix : matrix;
  report.coverage = coverageMatrix.flatMap(item => [0, 1].map(turn => {
    const result = report.turns.find(t => t.family === item.family && t.combo === item.combo && t.turn === turn);
    const skipped = report.blocked.find(t => t.family === item.family && t.combo === item.combo)
      || report.not_run.find(t => t.family === item.family && t.combo === item.combo);
    return { ...item, turn, status: result?.status || (skipped && report.blocked.includes(skipped) ? 'blocked' : 'not-run'),
      reason: result?.error || skipped?.reason || (!result ? `Run status: ${report.status}` : undefined),
      failed_checks: Object.entries(result?.checks || {}).filter(([, value]) => !value).map(([name]) => name) };
  }));
  report.coverage_counts = report.coverage.reduce((counts, row) => { counts[row.status] = (counts[row.status] || 0) + 1; return counts; }, {});
  save();
  console.log(JSON.stringify({ status: report.status, attempted, report: reportPath }));
  process.exitCode = ['passed', 'matrix-only', 'self-test-passed', 'preflight-passed'].includes(report.status) ? 0 : 1;
}

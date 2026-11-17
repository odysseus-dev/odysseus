#!/usr/bin/env node
/** Compact real-UI tool-family matrix for enabled non-Odysseus models. */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const db = (process.env.ODYSSEUS_DB_PATH || path.join(root, "data", "app.db"));
const sessionsFile = process.env.ODYSSEUS_PATH || (() => { throw new Error("ODYSSEUS_PATH is required"); })();
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/regular-model-tools-${run}.json`));
const requested = new Set((process.env.MODELS || '').split(',').map(x => x.trim()).filter(Boolean));
const workers = Math.max(1, Math.min(4, Number(process.env.WORKERS || 2)));
const turnTimeout = Math.max(15000, Math.min(120000, Number(process.env.TURN_TIMEOUT_MS || 60000)));
const profile = ['conversation', 'switchback'].includes(process.env.PROFILE)
  ? process.env.PROFILE : 'baseline';
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep)) throw Error('Report must be under reports/');

const requestedFamilies = new Set((process.env.FAMILIES || '').split(',').map(x => x.trim()).filter(Boolean));
const scenarios = [
  ['notes', 'List my notes. Return at most three titles. Read only.', 'Lst my noets. Return at most three titles. Read only.', 'From that read-only result, repeat the first title exactly. Do not call or change any tools.', ['manage_notes']],
  ['calendar', 'List my calendar events. Return at most three titles and times. Read only.', 'Lst my calndar events. Return at most three titles and times. Read only.', 'From that read-only result, when is the first one? Do not call or change any tools.', ['manage_calendar']],
  ['email', 'List my configured email accounts. Return only their names. Read only.', 'Lst my configured emial accounts. Return only their names. Read only.', 'From that read-only result, what is the first account called? Do not call or change any tools.', ['list_email_accounts']],
  ['tasks', 'List my scheduled tasks. Return at most three names and statuses. Read only.', 'Lst my scheduled taks. Return at most three names and statuses. Read only.', 'From that read-only result, what status does the first one have? Do not call or change any tools.', ['manage_tasks']],
  ['documents', 'List my documents. Return at most three titles. Read only.', 'Lst my documnts. Return at most three titles. Read only.', 'From that read-only result, repeat the first listed title exactly. Do not call or change any tools.', ['manage_documents']],
  ['memory', 'List my saved memories. Return at most three short entries. Read only.', 'Lst my saved memries. Return at most three short entries. Read only.', 'From that read-only result, repeat the first one briefly. Do not call or change any tools.', ['manage_memory']],
  ['skills', 'List my saved skills. Return at most three names. Read only.', 'Lst my saved skils. Return at most three names. Read only.', 'From that read-only result, what is the first one called? Do not call or change any tools.', ['manage_skills']],
  ['cookbook_admin', 'List configured Cookbook servers. Return only names and status. Read only.', 'Lst configured Cookbok servers. Return only names and status. Read only.', 'From that read-only result, is the first one online? Do not call or change any tools.', ['list_cookbook_servers']],
  ['search_browser', 'Search the web for the official Python packaging guide. Return one official link.', 'Serch the weeb for the official Python packaging guide. Return one official link.', 'Tell me more about that official result.', ['web_search', 'web_fetch']],
  ['shell_files', "Use bash to run this read-only command and report its output: printf '%s\\n' REGULAR_MODEL_SHELL_OK", "Use bsah to run this read-only command and report its output: printf '%s\\n' REGULAR_MODEL_SHELL_OK", 'From that read-only result, repeat the exact output. Do not call or change any tools.', ['bash']],
].filter(([family]) => !requestedFamilies.size || requestedFamilies.has(family));

const bare = value => String(value || '').replace(/^mcp__email__/, '');
const familyTools = {
  notes: ['manage_notes'], calendar: ['manage_calendar'],
  email: ['list_email_accounts', 'list_emails', 'search_emails', 'read_email', 'download_attachment', 'scan_email_unsubscribes', 'scan_spam', 'unsubscribe_email', 'send_email', 'reply_to_email', 'draft_email', 'draft_email_reply', 'ai_draft_email_reply', 'bulk_email', 'block_sender', 'manage_email_state', 'archive_email', 'delete_email', 'mark_email_read', 'resolve_contact', 'manage_contact'],
  tasks: ['manage_tasks'],
  documents: ['manage_documents', 'create_document', 'edit_document', 'update_document', 'suggest_document'],
  memory: ['manage_memory', 'search_chats'], skills: ['manage_skills'],
  cookbook_admin: ['download_model', 'serve_model', 'serve_preset', 'list_serve_presets', 'list_served_models', 'stop_served_model', 'tail_serve_output', 'list_downloads', 'cancel_download', 'list_cached_models', 'list_cookbook_servers', 'adopt_served_model', 'list_models', 'manage_settings', 'manage_endpoints', 'manage_mcp', 'manage_webhooks', 'manage_tokens', 'api_call', 'app_api', 'list_sessions', 'manage_session', 'create_session', 'send_to_session', 'chat_with_model'],
  search_browser: ['web_search', 'web_fetch', 'private_browser', 'youtube_tool', 'search_hf_models', 'pdf_extract'],
  shell_files: ['bash', 'python', 'read_file', 'write_file', 'edit_file', 'apply_patch', 'grep', 'glob', 'ls', 'get_workspace', 'manage_bg_jobs', 'inspect_media', 'transcribe_media'],
};
const safeText = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 600);
const noLeak = value => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(String(value || ''));
const save = report => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});
async function bounded(promise, ms, label) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(Error(`${label} timeout (${ms}ms)`)), ms); }),
    ]);
  } finally { clearTimeout(timer); }
}

function inventory() {
  const sql = `SELECT id,name,base_url,endpoint_kind,pinned_models,cached_models,model_tool_modes
    FROM model_endpoints WHERE is_enabled=1 ORDER BY name`;
  const rows = JSON.parse(execFileSync('sqlite3', ['-readonly', '-json', db, sql], { encoding: 'utf8' }) || '[]');
  return rows.flatMap(endpoint => {
    const pinned = JSON.parse(endpoint.pinned_models || '[]');
    const cached = JSON.parse(endpoint.cached_models || '[]');
    const models = pinned.length ? pinned : endpoint.endpoint_kind === 'local' ? cached : [];
    return models.filter(model => !/^odysseus-qwen3\.5-tools-pre-heretic$/i.test(model)).map(model => ({
      endpoint_id: endpoint.id, endpoint_name: endpoint.name, endpoint_kind: endpoint.endpoint_kind,
      endpoint_base: endpoint.base_url.replace(/\/$/, ''),
      endpoint_url: `${endpoint.base_url.replace(/\/$/, '')}/chat/completions`, model,
      configured_tool_mode: JSON.parse(endpoint.model_tool_modes || '{}')[model] || 'default',
      declared_limitation: /(?:^|\/)gpt-5-image$/i.test(model) ? 'image-generation model; chat tool use unsupported' : null,
    }));
  }).filter(item => !requested.size || requested.has(item.model));
}

const models = inventory();
const report = {
  run, base, owner, status: 'running', workers,
  profile,
  scope: 'Pinned enabled non-Odysseus models; local endpoints use visible cached models when no pins exist.',
  privacy: 'No prompts, tool outputs, private rows, API keys, or cookies are retained. Only bounded final text and tool/status metadata.',
  scenarios: scenarios.map(([family]) => family), inventory: models, models: [],
};
fs.mkdirSync(path.dirname(reportPath), { recursive: true });
save(report);

async function setToggle(page, id, wanted) {
  const box = page.locator(`#${id}`);
  if (!await box.count()) throw Error(`Missing toggle ${id}`);
  if (await box.isChecked() !== wanted) await page.locator(`#${id === 'rag-toggle' ? 'rag-indicator-btn' : `${id}-btn`}`).click();
  if (await box.isChecked() !== wanted) throw Error(`Could not set ${id}=${wanted}`);
}

async function runModel(model, token) {
  const result = { ...model, status: 'running', turns: [], cleanup: null };
  report.models.push(result); save(report);
  if (model.declared_limitation) {
    result.status = 'unsupported'; result.reason = model.declared_limitation; save(report); return;
  }
  if (model.endpoint_kind === 'local') {
    try {
      const probe = await fetch(`${model.endpoint_base}/models`, { signal: AbortSignal.timeout(5000) });
      if (!probe.ok) throw Error(`HTTP ${probe.status}`);
    } catch (error) {
      result.status = 'unavailable'; result.reason = `local endpoint preflight failed: ${String(error).split('\n')[0].slice(0, 200)}`;
      save(report); return;
    }
  }
  let browser, context, page, session;
  try {
    browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
    context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
    await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
    page = await context.newPage();
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForFunction(() => window.sessionModule?.loadSessions && window.chatModule);
    session = await page.evaluate(async model => {
      const body = new FormData();
      for (const [key, value] of Object.entries({ name: `[regular-tool-matrix] ${model.endpoint_name} ${model.model}`, endpoint_url: model.endpoint_url, endpoint_id: model.endpoint_id, model: model.model, skip_validation: 'true', rag: 'true' })) body.append(key, value);
      const response = await fetch('/api/session', { method: 'POST', body });
      if (!response.ok) throw Error(`session create HTTP ${response.status}`);
      return (await response.json()).id;
    }, model);
    result.session = session; save(report);
    await page.evaluate(async id => { await window.sessionModule.loadSessions(); await window.sessionModule.selectSession(id, { showLoading: false }); }, session);
    await page.waitForFunction(id => window.sessionModule.getCurrentSessionId() === id, session);
    const agent = page.locator('#mode-agent-btn');
    if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
    await setToggle(page, 'rag-toggle', false);
    await setToggle(page, 'web-toggle', false);
    await setToggle(page, 'bash-toggle', false);

    const executeTurn = async ({ family, prompt, expected, kind, requireTool, forbidTool = false }) => {
      const turn = { family, kind, status: 'running' }; result.turns.push(turn); save(report);
      let activeRunId = null;
      try {
        if (kind === 'request') {
          await setToggle(page, 'web-toggle', family === 'search_browser');
          await setToggle(page, 'bash-toggle', family === 'shell_files');
        }
        const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chat_stream' && response.request().method() === 'POST', { timeout: turnTimeout });
        await page.locator('textarea#message:visible').fill(prompt);
        await page.locator('textarea#message:visible').press('Enter');
        const response = await responsePromise;
        activeRunId = response.headers()['x-odysseus-run-id'] || null;
        turn.http = response.status();
        const events = parseSSE(await bounded(response.text(), turnTimeout, 'response body'));
        const contract = events.find(event => event.type === 'turn_contract');
        const starts = events.filter(event => event.type === 'tool_start').map(event => bare(event.tool));
        const outputs = events.filter(event => event.type === 'tool_output').map(event => ({ tool: bare(event.tool), exit_code: event.exit_code ?? null, has_error: Boolean(event.error) }));
        const final = events.filter(event => event.type === 'final_response').map(event => event.content || '').join('')
          || events.filter(event => typeof event.delta === 'string').map(event => event.delta).join('');
        try { await page.waitForFunction(() => !document.querySelector('#chat-history .streaming'), null, { timeout: 10000 }); } catch {}
        const dom = await page.locator('#chat-history').evaluate(root => {
          const visible = node => Boolean(node.getClientRects().length) && getComputedStyle(node).visibility !== 'hidden';
          const users = [...root.querySelectorAll('.msg-user')].filter(visible);
          const lastUser = users.at(-1);
          const after = node => lastUser && Boolean(lastUser.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
          const answers = [...root.querySelectorAll('.msg-ai')].filter(node => visible(node) && after(node));
          const tools = [...root.querySelectorAll('.agent-thread')].filter(node => visible(node) && after(node));
          return {
            answer_bubbles: answers.length,
            answer_text: answers.map(node => node.innerText || '').join('\n'),
            tool_cards: tools.length,
            tool_text_chars: tools.reduce((sum, node) => sum + (node.innerText || '').length, 0),
            streaming: root.querySelectorAll('.streaming').length,
          };
        });
        turn.route = contract?.selection_mode || null;
        turn.schema_mode = contract?.schema_mode || model.configured_tool_mode;
        turn.event_types = events.reduce((counts, event) => { const key = event.type || 'delta'; counts[key] = (counts[key] || 0) + 1; return counts; }, {});
        turn.tools = starts;
        turn.outputs = outputs;
        turn.final_chars = String(final || '').length;
        turn.dom = { answer_bubbles: dom.answer_bubbles, answer_chars: dom.answer_text.length, tool_cards: dom.tool_cards, tool_text_chars: dom.tool_text_chars, streaming: dom.streaming };
        turn.contract = contract ? {
          required_count: contract.required?.length ?? null,
          offered_count: contract.offered?.length ?? null,
          executable_count: contract.executable?.length ?? null,
          expected_offered: expected.some(name => (contract.offered || []).map(bare).includes(name)),
          expected_executable: expected.some(name => (contract.executable || []).map(bare).includes(name)),
        } : null;
        turn.checks = {
          http_ok: response.ok(), sse_valid: !events.some(event => event.type === 'invalid_sse'),
          legacy_route: Boolean(contract) && contract.selection_mode !== 'clean_compact_v3_preview',
          expected_family_offered: !requireTool || turn.contract?.expected_offered !== false,
          expected_tool: !requireTool || expected.some(name => starts.includes(name)),
          no_unrelated_tool: forbidTool ? starts.length === 0 : starts.every(name => (familyTools[family] || expected).includes(name)),
          tool_completed: !requireTool || outputs.some(output => expected.includes(output.tool)),
          tool_success: outputs.every(output => !output.has_error && (output.exit_code == null || output.exit_code === 0)),
          visible_answer: Boolean(safeText(final) || safeText(dom.answer_text)),
          no_reasoning_leak: noLeak(final) && noLeak(dom.answer_text),
          no_preview_refusal: !/can(?:not|'t) perform that operation in this preview/i.test(`${final}\n${dom.answer_text}`),
        };
        turn.status = Object.values(turn.checks).every(Boolean) ? 'passed' : 'failed';
        if (turn.status === 'failed') {
          const offered = turn.contract?.expected_offered;
          turn.failure_layer = !turn.checks.http_ok || !turn.checks.sse_valid ? 'transport'
            : !turn.checks.legacy_route ? 'route'
            : offered === false ? 'contract'
            : !turn.checks.expected_tool ? 'model'
            : !turn.checks.no_unrelated_tool ? 'model/family-continuity'
            : !turn.checks.tool_completed || !turn.checks.tool_success ? 'execution/backend'
            : !turn.checks.visible_answer || !turn.checks.no_reasoning_leak ? 'answer/rendering'
            : 'unknown';
        }
      } catch (error) {
        turn.status = 'failed'; turn.error = String(error).split('\n')[0].slice(0, 500);
        if (/timeout/i.test(turn.error)) {
          turn.failure_layer = 'transport/termination';
          result.aborted_after = family;
          if (session && activeRunId) {
            try {
              const stopped = await context.request.post(`${base}/api/chat/stop/${encodeURIComponent(session)}`, {
                headers: { 'X-Odysseus-Run-Id': activeRunId }, timeout: 5000,
              });
              turn.stop = { http: stopped.status(), ok: stopped.ok() };
            } catch (stopError) { turn.stop = { ok: false, error: String(stopError).split('\n')[0].slice(0, 200) }; }
          }
        }
      }
      save(report);
      return turn;
    };

    let expectedTurns;
    if (profile === 'switchback') {
      const steps = [
        { family: 'notes', prompt: 'List my notes. Return at most three titles. Read only.', expected: ['manage_notes'], kind: 'request', requireTool: true },
        { family: 'calendar', prompt: 'List my calendar events. Return at most three titles and times. Read only.', expected: ['manage_calendar'], kind: 'request', requireTool: true },
        { family: 'notes', prompt: 'Back to my notes: repeat the first title from the earlier result. Do not call or change any tools.', expected: ['manage_notes'], kind: 'family_return', requireTool: false, forbidTool: true },
        { family: 'search_browser', prompt: 'Search the web for the official Python packaging guide. Return one official link.', expected: ['web_search'], kind: 'request', requireTool: true },
        { family: 'search_browser', prompt: 'Open that official result and read the page. Tell me its main packaging recommendation.', expected: ['web_fetch'], kind: 'explicit_page_inspection', requireTool: true },
        { family: 'calendar', prompt: 'Back to my calendar: repeat when the first event occurs. Do not call or change any tools.', expected: ['manage_calendar'], kind: 'family_return', requireTool: false, forbidTool: true },
      ];
      expectedTurns = steps.length;
      for (const step of steps) {
        await executeTurn(step);
        if (result.aborted_after) break;
      }
    } else {
      for (const [family, baselinePrompt, typoPrompt, followupPrompt, expected] of scenarios) {
        const prompt = profile === 'conversation' ? typoPrompt : baselinePrompt;
        const request = await executeTurn({ family, prompt, expected, kind: 'request', requireTool: true });
        if (result.aborted_after) break;
        if (profile === 'conversation' && request.status === 'passed') {
          const searchFollowup = family === 'search_browser';
          // “Tell me more” may be answered from the already returned search
          // evidence or may fetch the linked page. Both are valid; the
          // switchback profile explicitly requires page inspection.
          await executeTurn({ family, prompt: followupPrompt, expected, kind: 'ambiguous_followup', requireTool: false, forbidTool: !searchFollowup });
          if (result.aborted_after) break;
        }
      }
      expectedTurns = scenarios.length * (profile === 'conversation' ? 2 : 1);
    }
    result.passed = result.turns.filter(turn => turn.status === 'passed').length;
    result.status = result.passed === expectedTurns ? 'passed' : 'failed';
  } catch (error) {
    result.status = 'failed'; result.error = String(error).split('\n')[0].slice(0, 500);
  } finally {
    if (session && context) {
      try {
        const removed = await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`);
        result.cleanup = { http: removed.status(), removed: removed.ok() };
        if (!removed.ok()) result.status = 'failed';
      } catch (error) { result.cleanup = { removed: false, error: String(error).split('\n')[0].slice(0, 300) }; result.status = 'failed'; }
    }
    if (browser) await browser.close();
    save(report);
  }
}

const authSessions = JSON.parse(fs.readFileSync(sessionsFile, 'utf8'));
const token = Object.entries(authSessions).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
let cursor = 0;
await Promise.all(Array.from({ length: Math.min(workers, models.length || 1) }, async () => {
  while (cursor < models.length) await runModel(models[cursor++], token);
}));
report.status = report.models.every(item => ['passed', 'unsupported'].includes(item.status)) ? 'passed' : 'failed';
report.summary = {
  models: report.models.length, passed_models: report.models.filter(item => item.status === 'passed').length,
  unsupported_models: report.models.filter(item => item.status === 'unsupported').length,
  unavailable_models: report.models.filter(item => item.status === 'unavailable').length,
  failed_models: report.models.filter(item => item.status === 'failed').length,
  passed_turns: report.models.flatMap(item => item.turns || []).filter(turn => turn.status === 'passed').length,
  total_turns: report.models.flatMap(item => item.turns || []).length,
};
save(report);
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

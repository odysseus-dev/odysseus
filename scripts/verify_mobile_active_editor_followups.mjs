#!/usr/bin/env node
/** Real 7011 mobile Agent UI replay for referential edits to one open document. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const model = process.env.MODEL || 'odysseus-qwen3.5-tools-pre-heretic';
const owner = 'sft_alex_creator';
const routingMode = process.env.ROUTING_MODE || 'recent';
const expectedRoutingMode = routingMode === 'recent' ? 'recent_model_choice' : routingMode;
const expectCleanRoute = process.env.EXPECT_CLEAN_ROUTE !== 'false';
const expectExactRouting = process.env.EXPECT_EXACT_ROUTING !== 'false';
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/mobile-active-editor-followups-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);

const cases = [
  {
    name: 'open-email-draft', title: '[mobile fixture] Meeting reply', language: 'email',
    content: 'To: test@example.com\nSubject: Re: Meeting\nIn-Reply-To: <mobile-fixture@example.com>\nReferences: <mobile-fixture@example.com>\nX-Source-UID: 999996\n---\n\n---------- Previous message ----------\nCan you confirm the meeting time?\n',
    turns: [
      ['Write reply to this email saying 8am works for me.', ['8am works'], []],
      ['Make that reply warmer and mention Friday.', ['8am', 'Friday'], []],
      ['Shorten it but keep 8am and Friday.', ['8am', 'Friday'], []],
    ],
    preserve: ['To:', 'Subject:', 'In-Reply-To:', 'References:', 'X-Source-UID:', '---'],
  },
  {
    name: 'open-markdown-document', title: '[mobile fixture] Launch status', language: 'markdown',
    content: '# Project status\n\nThe launch is scheduled for Monday.\n',
    turns: [
      ['In this open document, change Monday to Tuesday.', ['Tuesday'], ['Monday']],
      ['Now add a final line saying QA is complete.', ['Tuesday', 'QA is complete'], []],
      ['Change that final line to say QA is pending.', ['Tuesday', 'QA is pending'], ['QA is complete']],
    ],
    preserve: ['Project status'],
  },
];

const report = { run, owner, model, endpoint_id: endpointId, status: 'running', cases: [], privacy: 'Synthetic fixture prompts/checks and document-tool diagnostics only; no real-user documents.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const bare = value => String(value || '').replace(/^mcp__email__/, '');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity', 'x-odysseus-routing-experiment': routingMode },
  });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const spec of cases) {
    const result = { name: spec.name, status: 'running', mobile: true, turns: [], cleanup: { document: false, session: false } };
    report.cases.push(result); save();
    let session = '', docId = '';
    try {
      const created = await context.request.post(`${base}/api/session`, { multipart: {
        name: `[mobile-active-editor] ${spec.name}-${run}`, model, endpoint_id: endpointId,
        endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
      }});
      if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
      session = (await created.json()).id;
      const doc = await context.request.post(`${base}/api/document`, { data: {
        session_id: session, title: spec.title, language: spec.language, content: spec.content,
      }, timeout: 90000 });
      if (!doc.ok()) throw Error(`Document create HTTP ${doc.status()}`);
      docId = (await doc.json()).id;

      page = await context.newPage();
      await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session, { timeout: 30000 });
      await page.waitForFunction(id => window.documentModule?.getCurrentDocId?.() === id, docId, { timeout: 30000 });
      const agent = page.locator('#mode-agent-btn');
      if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
      let previous = spec.content;
      for (let index = 0; index < spec.turns.length; index++) {
        const [prompt, includes, excludes] = spec.turns[index];
        const waiting = page.waitForResponse(
          r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST',
          { timeout: 120000 },
        ).catch(error => ({ waitError: error }));
        const composer = page.locator('textarea#message:visible');
        if (!await composer.isVisible()) {
          let dismissed = false;
          for (const selector of ['#doc-mobile-grabber:visible', '#doc-close-btn:visible']) {
            const dismissEditor = page.locator(selector);
            if (!await dismissEditor.isVisible()) continue;
            await dismissEditor.tap();
            dismissed = true;
            break;
          }
          if (!dismissed) throw Error('Open mobile editor has no visible dismiss control');
          await composer.waitFor({ state: 'visible', timeout: 30000 });
        }
        await composer.tap();
        await page.waitForFunction(() => !document.querySelector('textarea#message')?.hasAttribute('readonly'));
        await composer.fill(prompt);
        await composer.press('Enter');
        const response = await waiting;
        if (response.waitError) throw response.waitError;
        const events = parseSSE(await response.text());
        const contract = events.find(event => event.type === 'turn_contract') || {};
        const calls = events.filter(event => event.type === 'tool_start').map(event => bare(event.tool));
        const outputs = events.filter(event => event.type === 'tool_output').map(event => ({ tool: bare(event.tool), ok: !event.error && (event.exit_code == null || event.exit_code === 0) }));
        const fetched = await context.request.get(`${base}/api/document/${encodeURIComponent(docId)}`);
        const current = fetched.ok() ? String((await fetched.json()).current_content || '') : '';
        const checks = {
          http_ok: response.ok(),
          clean_route: !expectCleanRoute || contract.selection_mode === 'clean_compact_v3_preview',
          exact_runtime: !expectExactRouting || contract.routing_experiment === expectedRoutingMode,
          request_has_fixture_editor: response.request().postData()?.includes(docId) || false,
          documents_capability: (contract.active_capabilities || contract.capabilities || []).includes('documents'),
          same_open_editor: await page.evaluate(id => window.documentModule?.getChatDocumentId?.() === id, docId),
          document_tool_called: calls.some(name => ['update_document', 'edit_document', 'suggest_document'].includes(name)),
          document_tool_succeeded: outputs.some(item => ['update_document', 'edit_document', 'suggest_document'].includes(item.tool) && item.ok),
          no_replacement_document: !calls.includes('create_document'), changed: current !== previous,
          required_text: includes.every(text => current.toLowerCase().includes(text.toLowerCase())),
          removed_text: excludes.every(text => !current.toLowerCase().includes(text.toLowerCase())),
          preserved_envelope: spec.preserve.every(text => current.includes(text)),
          no_stream_error: !events.some(event => ['error', 'invalid_sse'].includes(event.type)),
        };
        const turn = { index, prompt, tools: calls, checks,
          diagnostics: {
            request_has_fixture_editor: response.request().postData()?.includes(docId) || false,
            editor_id_after: await page.evaluate(id => {
              const active = window.documentModule?.getCurrentDocId?.();
              return !active ? 'none' : active === id ? 'fixture' : 'other';
            }, docId),
            document_events: events.filter(event => ['tool_start', 'tool_output'].includes(event.type)
              && ['update_document', 'edit_document', 'suggest_document'].includes(bare(event.tool)))
              .map(event => ({ type: event.type, tool: event.tool, command: event.command,
                output: event.output, error: event.error, exit_code: event.exit_code })),
          }, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' };
        result.turns.push(turn); previous = current; save();
      }
      result.status = result.turns.length === spec.turns.length && result.turns.every(turn => turn.status === 'passed') ? 'passed' : 'failed';
    } catch (error) {
      result.status = 'failed'; result.error = String(error).split('\n')[0].slice(0, 400);
    } finally {
      if (page) { await page.close(); page = null; }
      if (docId) result.cleanup.document = (await context.request.delete(`${base}/api/document/${encodeURIComponent(docId)}`)).ok();
      if (session) result.cleanup.session = (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok();
      if (!result.cleanup.document || !result.cleanup.session) result.status = 'failed';
      save();
    }
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 400);
} finally {
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.cases.length === cases.length && report.cases.every(item => item.status === 'passed') ? 'passed' : 'failed';
report.summary = { passed: report.cases.filter(item => item.status === 'passed').length, total: cases.length, turns: report.cases.reduce((sum, item) => sum + item.turns.length, 0) };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

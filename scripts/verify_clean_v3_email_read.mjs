#!/usr/bin/env node
/** Production-path email read checks through authenticated 7011; no message data retained. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/clean-v3-email-read-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { run, owner, status: 'running', turns: [], privacy: 'No account names, addresses, subjects, bodies, tool output, prompts, or answer text retained.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const canonical = value => String(value || '').replace(/^mcp__email__/, '');
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page, session;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[clean-v3-email-read] ${run}`, model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: endpointId,
    endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
  session = (await created.json()).id;
  page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForFunction(id => window.sessionModule?.getCurrentSessionId() === id, session);
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  if (await page.locator('#web-toggle').isChecked()) await page.locator('#web-toggle-btn').click();
  if (await page.locator('#bash-toggle').isChecked()) await page.locator('#bash-toggle-btn').click();

  const cases = [
    ['List my connected email accounts. Return only their display names.', ['list_email_accounts']],
    ['Show my latest three inbox emails. Return only sender and subject.', ['list_emails']],
    ['Read the first email from that list and summarize it briefly.', ['read_email']],
  ];
  for (const [prompt, expected] of cases) {
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await responsePromise;
    const events = parseSSE(await response.text());
    const contract = events.find(x => x.type === 'turn_contract');
    const starts = events.filter(x => x.type === 'tool_start').map(x => canonical(x.tool));
    const outputs = events.filter(x => x.type === 'tool_output').map(x => ({ tool: canonical(x.tool), exit_code: x.exit_code ?? null, error: Boolean(x.error) }));
    const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
    const checks = {
      http_ok: response.ok(), clean_route: contract?.selection_mode === 'clean_compact_v3_preview',
      expected_tool: starts.some(name => expected.includes(name)),
      tool_success: outputs.some(x => expected.includes(x.tool) && !x.error && (x.exit_code == null || x.exit_code === 0)),
      visible_answer: final.trim().length > 0,
      no_reasoning_leak: !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(final),
      no_cross_family_tool: starts.every(name => ['list_email_accounts', 'list_emails', 'read_email', 'search_emails'].includes(name)),
    };
    report.turns.push({ expected, tools: starts, outputs, final_chars: final.length, checks, status: Object.values(checks).every(Boolean) ? 'passed' : 'failed' });
    save();
  }
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 300);
} finally {
  if (session && context) report.session_cleanup = { removed: (await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`)).ok() };
  if (page) await page.close();
  if (browser) await browser.close();
}
report.status = report.turns.length === 3 && report.turns.every(x => x.status === 'passed') && report.session_cleanup?.removed ? 'passed' : 'failed';
report.summary = { passed: report.turns.filter(x => x.status === 'passed').length, total: 3 };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

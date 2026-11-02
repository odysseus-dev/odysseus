/** Model-driven research launch from Agent chat; cancel only this test's jobs. */
import fs from 'node:fs';
import { chromium } from 'playwright';
const base = 'http://127.0.0.1:7011';
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, v]) => v?.username === 'sft_alex_creator')?.[0];
if (!token) throw Error('SFT login missing');
const reportPath = new URL(`../reports/research-chat-launch-${Date.now()}.json`, import.meta.url);
const report = { status: 'running', cases: [], cleanup: {} };
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
const jobs = new Set(), chats = new Set();
let browser, context;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  for (const prompt of ['research ai info', 'researhc ai info']) {
    const created = await context.request.post(`${base}/api/session`, { multipart: {
      name: `[research-launch-test] ${Date.now()}`, model: 'odysseus-qwen3.5-tools-pre-heretic',
      endpoint_id: '1d1022ef', endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(),
      skip_validation: 'true', rag: 'false',
    } });
    if (!created.ok()) throw Error(`Session create ${created.status()}`);
    const id = (await created.json()).id;
    chats.add(id);
    const page = await context.newPage();
    await page.goto(`${base}/#${id}`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(id => window.__odysseusSessionReadyId === id, id);
    const agent = page.locator('#mode-agent-btn');
    if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(`${prompt}. Limit the research job to one round.`);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await pending;
    const events = (await response.text()).replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
      const raw = frame.split('\n').filter(s => s.startsWith('data:')).map(s => s.slice(5).trimStart()).join('\n');
      return raw && raw !== '[DONE]' ? [JSON.parse(raw)] : [];
    });
    const outputs = events.filter(e => e.type === 'tool_output' && e.tool === 'trigger_research');
    for (const e of outputs.filter(e => !e.error && e.exit_code === 0)) {
      for (const m of String(e.output).matchAll(/#research-([A-Za-z0-9_-]+)/g)) jobs.add(m[1]);
    }
    const notice = events.find(e => e.type === 'ui_control' && e.data?.ui_event === 'research_started');
    const sid = notice?.data?.research_session_id;
    if (sid) jobs.add(sid);
    const contract = events.find(e => e.type === 'turn_contract') || {};
    const deltas = events.map(e => e.delta || '').join('');
    const checks = {
      http_ok: response.ok(),
      offered: (contract.offered || []).includes('trigger_research'),
      model_selected: outputs.length === 1 && !outputs[0].error && outputs[0].exit_code === 0,
      ui_notice: Boolean(sid),
      streamed_link: Boolean(sid && deltas.includes(`](#research-${sid})`)),
    };
    if (sid) {
      await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 20000 });
      const link = page.locator(`#chat-history .msg-ai a[href="#research-${sid}"]`).last();
      checks.rendered_link = await link.isVisible();
      if (checks.rendered_link) await link.click();
      const card = page.locator(`[data-job-id="${sid}"]`).first();
      await card.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
      checks.correct_job_card = await card.isVisible();
      const status = await context.request.get(`${base}/api/research/status/${sid}`);
      checks.owner_can_read_job = status.ok();
      report.cleanup[sid] = { cancel_http: (await context.request.post(`${base}/api/research/cancel/${sid}`)).status() };
    }
    report.cases.push({ prompt, checks, tools: outputs.map(e => ({ command: e.command, error: e.error, exit_code: e.exit_code })), passed: Object.values(checks).every(Boolean) });
    save();
    await page.close();
  }
  report.status = report.cases.every(c => c.passed) ? 'passed' : 'failed';
} catch (e) { report.status = 'failed'; report.error = String(e).slice(0, 600); }
finally {
  if (context) {
    for (const sid of jobs) {
      const cancel = await context.request.post(`${base}/api/research/cancel/${sid}`);
      const removed = await context.request.delete(`${base}/api/research/${sid}`);
      report.cleanup[sid] = { ...(report.cleanup[sid] || {}), cancel_http: cancel.status(), delete_http: removed.status() };
      if (!cancel.ok() || !removed.ok()) report.status = 'failed';
    }
    for (const id of chats) report.cleanup[id] = { chat_deleted: (await context.request.delete(`${base}/api/session/${id}`)).ok() };
  }
  if (browser) await browser.close();
  save();
}
console.log(JSON.stringify({ report: reportPath.pathname, ...report }));
if (report.status !== 'passed') process.exitCode = 1;

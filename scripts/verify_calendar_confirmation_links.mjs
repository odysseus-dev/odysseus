/** Real Agent UI create/update link replay; disposable SFT records only. */
import crypto from 'node:crypto';
import fs from 'node:fs';
import { chromium } from 'playwright';

const base = 'http://127.0.0.1:7011';
const marker = `ody-calendar-link-${crypto.randomUUID()}`;
const reportPath = new URL(`../reports/calendar-confirmation-links-${Date.now()}.json`, import.meta.url);
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, v]) => v?.username === 'sft_alex_creator')?.[0];
if (!token) throw Error('SFT login missing');
const report = { marker, cases: [], cleanup: {}, status: 'running' };
let browser, context, session;
const fixtureIds = new Set();
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
const sse = text => text.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(s => s.startsWith('data:')).map(s => s.slice(5).trimStart()).join('\n');
  return !raw || raw === '[DONE]' ? [] : [JSON.parse(raw)];
});
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: {
    'x-odysseus-routing-experiment': 'recent_model_choice',
  } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: marker, model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: '1d1022ef',
    endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(), skip_validation: 'true', rag: 'false',
  } });
  if (!created.ok()) throw Error(`Session create ${created.status()}`);
  session = (await created.json()).id;
  const page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  for (const [name, prompt, action] of [
    ['create', `Add a calendar event titled ${marker} on January 1, 2030 at 9 AM.`, 'create_event'],
    ['update', 'Move that event to 10 AM on the same day.', 'update_event'],
  ]) {
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await pending;
    const events = sse(await response.text());
    const outputs = events.filter(e => e.type === 'tool_output' && e.tool === 'manage_calendar');
    for (const e of outputs) {
      if (!e.error && e.exit_code === 0 && String(e.output).includes(marker)) {
        for (const m of String(e.output).matchAll(/#event-([A-Za-z0-9_-]+)/g)) fixtureIds.add(m[1]);
      }
    }
    const uid = [...fixtureIds][0];
    if (!uid) throw Error('No successful synthetic event creation evidence');
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 20000 });
    const bubble = page.locator('#chat-history .msg-ai').last();
    const link = bubble.locator(`a[href="#event-${uid}"]`);
    const streamed = events.map(e => e.delta || '').join('');
    const metrics = events.find(e => e.type === 'metrics')?.data || {};
    const contract = events.find(e => e.type === 'turn_contract') || {};
    const checks = {
      http_ok: response.ok(),
      model_specific_route: contract.selection_mode === 'clean_compact_v3_preview',
      successful_action: outputs.some(e => !e.error && e.exit_code === 0 && JSON.parse(e.command || '{}').action === action),
      streamed_link: streamed.includes(`](#event-${uid})`),
      saved_link: (metrics.clean_v3_turn?.at(-1)?.content || '').includes(`](#event-${uid})`),
      one_visible_link: await link.count() === 1 && await link.first().isVisible(),
      no_replacement: !events.some(e => e.type === 'final_response'),
    };
    if (checks.one_visible_link) {
      await link.click();
      const target = page.locator(`#calendar-modal [data-uid="${uid}"].cal-event-link-target`).first();
      await target.waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
      checks.click_opens_exact_event = await target.isVisible();
      await page.keyboard.press('Escape');
    } else checks.click_opens_exact_event = false;
    report.cases.push({ name, checks, passed: Object.values(checks).every(Boolean) });
    save();
  }
  report.status = report.cases.every(c => c.passed) ? 'passed' : 'failed';
} catch (e) {
  report.status = 'failed'; report.error = String(e).slice(0, 600);
} finally {
  if (context) {
    for (const uid of fixtureIds) {
      const removed = await context.request.delete(`${base}/api/calendar/events/${encodeURIComponent(uid)}`);
      report.cleanup[uid] = removed.ok() || removed.status() === 404;
    }
    if (session) report.cleanup.session = (await context.request.delete(`${base}/api/session/${session}`)).ok();
  }
  if (browser) await browser.close();
  if (!Object.values(report.cleanup).every(Boolean)) report.status = 'failed';
  save();
}
console.log(JSON.stringify({ report: reportPath.pathname, ...report }));
if (report.status !== 'passed') process.exitCode = 1;

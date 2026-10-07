/** Real research completion → origin chat → model follow-up; SFT account only. */
import fs from 'node:fs';
import { chromium } from 'playwright';
const base = 'http://127.0.0.1:7011';
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, v]) => v?.username === 'sft_alex_creator')?.[0];
if (!token) throw Error('SFT login missing');
const reportPath = new URL(`../reports/background-research-chat-${Date.now()}.json`, import.meta.url);
const report = { status: 'running', checks: {}, cleanup: {} };
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
const parse = text => text.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(s => s.startsWith('data:')).map(s => s.slice(5).trimStart()).join('\n');
  return raw && raw !== '[DONE]' ? [JSON.parse(raw)] : [];
});
let browser, context, session, job;
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'x-odysseus-routing-experiment': 'recent_model_choice' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: `[background-research-test] ${Date.now()}`, model: 'odysseus-qwen3.5-tools-pre-heretic',
    endpoint_id: '1d1022ef', endpoint_url: process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })(), skip_validation: 'true', rag: 'false',
  } });
  if (!created.ok()) throw Error(`Session create ${created.status()}`);
  session = (await created.json()).id;
  const page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
  const send = async prompt => {
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await pending;
    const events = parse(await response.text());
    await page.waitForFunction(() => !document.querySelector('#chat-history .msg-ai.streaming'), null, { timeout: 15000 });
    return events;
  };
  const start = await send('Research the official Python documentation on list versus tuple mutability.');
  const rows = (await (await context.request.get(`${base}/api/research/chat-jobs/${session}`)).json()).jobs;
  job = rows[0]?.id;
  if (!job) throw Error('No chat-bound research job');
  report.job_id = job;
  report.checks.research_started = start.some(e => e.type === 'tool_output' && e.tool === 'trigger_research' && !e.error && e.exit_code === 0);
  report.checks.quick_default_two_rounds = rows[0].rounds === 2;
  report.checks.foreground_released_before_completion = rows[0].status !== 'delivered';
  const chat = await send('While that runs, what is two plus two? Answer briefly.');
  report.checks.can_chat_while_running = /\b4\b|\bfour\b/i.test(chat.map(e => e.delta || e.content || '').join(''));
  await page.evaluate(() => { window.__bgTestFirstBubble = document.querySelector('#chat-history .msg'); });
  save();
  const deadline = Date.now() + 300000;
  let delivered;
  while (Date.now() < deadline) {
    const all = (await (await context.request.get(`${base}/api/research/chat-jobs/${session}`)).json()).jobs;
    delivered = all.find(j => j.id === job && j.status === 'delivered');
    if (delivered) break;
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  if (!delivered) throw Error('Research did not return within five-minute test budget');
  const messageId = delivered.message.metadata._db_id;
  report.delivered_summary = delivered.message.content;
  const historyResponse = await context.request.get(`${base}/api/history/${session}`);
  const history = (await historyResponse.json()).history || [];
  const evidence = history.find(m => m.metadata?.background_job_id === job)?.metadata?.background_tool_result;
  report.report_excerpt = String(evidence?.report || '').slice(0, 6000);
  report.source_count = evidence?.sources?.length || 0;
  save();
  const bubble = page.locator(`#chat-history [data-db-id="${messageId}"]`);
  await bubble.waitFor({ state: 'visible', timeout: 15000 });
  report.checks.automatic_chat_delivery = await bubble.count() === 1;
  report.checks.transcript_not_rebuilt = await page.evaluate(() => window.__bgTestFirstBubble === document.querySelector('#chat-history .msg'));
  report.checks.summary_discusses_findings = /list/i.test(delivered.message.content) && /tuple/i.test(delivered.message.content)
    && /mutab/i.test(delivered.message.content) && !/could not generate/i.test(delivered.message.content);
  report.checks.report_link = delivered.message.content.includes(`](#research-${job})`);
  await new Promise(resolve => setTimeout(resolve, 6500));
  report.checks.repeat_poll_no_duplicate = await bubble.count() === 1;
  const followup = await send('Based on that research, which one can be changed in place?');
  const text = followup.map(e => e.delta || e.content || '').join('');
  report.checks.grounded_followup = /list/i.test(text) && /mutab|chang/i.test(text);
  report.checks.followup_no_new_job = !followup.some(e => e.type === 'tool_output' && e.tool === 'trigger_research');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  await new Promise(resolve => setTimeout(resolve, 3500));
  report.checks.reload_no_duplicate = await page.locator(`#chat-history [data-db-id="${messageId}"]`).count() === 1;
  report.status = Object.values(report.checks).every(Boolean) ? 'passed' : 'failed';
} catch (e) { report.status = 'failed'; report.error = String(e).slice(0, 700); }
finally {
  if (context) {
    if (job) {
      report.cleanup.cancel = (await context.request.post(`${base}/api/research/cancel/${job}`)).status();
      report.cleanup.report_deleted = (await context.request.delete(`${base}/api/research/${job}`)).ok();
    }
    if (session) report.cleanup.chat_deleted = (await context.request.delete(`${base}/api/session/${session}`)).ok();
  }
  if (browser) await browser.close();
  save();
}
console.log(JSON.stringify({ report: reportPath.pathname, ...report }));
if (report.status !== 'passed') process.exitCode = 1;

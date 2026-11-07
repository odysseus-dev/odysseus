#!/usr/bin/env node
/** Real 7011 image attachment -> answer -> reload -> image follow-up check. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const routingMode = 'recent_model_choice';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const fixture = path.resolve(process.env.FIXTURE_PATH || path.join(root, 'tests/fixtures/vl/basic-shapes.png'));
const reportPath = process.env.REPORT_PATH
  ? path.resolve(process.env.REPORT_PATH)
  : path.join(root, `reports/clean-v3-vl-live-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep)) throw Error('Report must be under reports/');
if (fs.existsSync(reportPath)) throw Error('Report exists; refuse overwrite');
const authSessions = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })()));
const token = Object.entries(authSessions).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error('Dedicated SFT account has no active auth session');
const report = { status: 'running', owner, fixture: path.relative(root, fixture), turns: [], checks: {}, cleanup: null };
const save = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
let browser, context, session;

const parseEvents = async response => (await response.text())
  .split(/\r?\n\r?\n/)
  .filter(line => line.startsWith('data: ') && line.slice(6) !== '[DONE]')
  .map(line => JSON.parse(line.slice(6)));

try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block',
    ...(process.env.MOBILE === 'true' ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : {}),
    extraHTTPHeaders: { 'x-odysseus-routing-experiment': routingMode } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const created = await context.request.post(`${base}/api/session`, { multipart: {
    name: '[clean-v3-vl] basic shapes',
    model: 'odysseus-qwen3.5-tools-pre-heretic',
    endpoint_id: endpointId,
    endpoint_url: endpointUrl,
    skip_validation: 'true', rag: 'false',
  }});
  if (!created.ok()) throw Error(`session create ${created.status()}`);
  session = (await created.json()).id;
  report.session = session; save();

  const page = await context.newPage();
  await page.goto(`${base}/#${session}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  const agent = page.locator('#mode-agent-btn');
  if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();

  const send = async prompt => {
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 90000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await responsePromise;
    const events = await parseEvents(response);
    const text = events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
    const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || text;
    const contract = events.find(x => x.type === 'turn_contract');
    if (contract?.routing_experiment !== routingMode) throw Error('Wrong model-specific runtime');
    const turn = {
      prompt, http: response.status(), selection_mode: contract?.selection_mode,
      image_context_count: contract?.multimodal_image_count,
      image_rehydration: contract?.image_rehydration,
      final, tools: events.filter(x => x.type === 'tool_output').map(x => ({ tool: x.tool, exit_code: x.exit_code, error: x.error })),
    };
    report.turns.push(turn); save();
    return turn;
  };

  await page.locator('#file-input').setInputFiles(fixture);
  if (process.env.MOBILE === 'true') {
    // Mobile intentionally asks the user to crop or keep the original first.
    await page.locator('.attach-crop-overlay [data-action="original"]').click();
  }
  await page.locator('#attach-strip .thumb').waitFor({ state: 'visible' }).catch(async error => {
    report.attachment_diagnostics = await page.evaluate(() => ({
      strip_count: document.querySelectorAll('#attach-strip').length,
      thumb_count: document.querySelectorAll('#attach-strip .thumb').length,
      strip_display: document.querySelector('#attach-strip') && getComputedStyle(document.querySelector('#attach-strip')).display,
      body_classes: document.body.className,
    }));
    await page.screenshot({ path: reportPath.replace(/\.json$/, '.png') });
    throw error;
  });
  const first = await send('Read the image. State the exact heading and describe the left and right shapes with their colors.');
  const firstText = first.final.toLowerCase();
  if (first.selection_mode !== 'clean_compact_v3_preview') throw Error('First turn did not use clean v3');
  report.checks.first_turn_route = true;
  report.checks.ocr = firstText.includes('odysseus 42');
  report.checks.visual_objects = ['red', 'circle', 'blue', 'square'].every(required => firstText.includes(required));
  if (!report.checks.visual_objects) throw Error('First answer missed one or more visual objects');

  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForFunction(id => window.__odysseusSessionReadyId === id, session);
  await page.waitForFunction(() => document.querySelectorAll('#chat-history .msg').length >= 2);
  const second = await send('What color was the shape on the right?');
  if (second.selection_mode !== 'clean_compact_v3_preview') throw Error('Follow-up did not use clean v3');
  report.checks.reload_followup_route = true;
  report.checks.reload_followup_grounding = /\bblue\b/i.test(second.final);
  if (!report.checks.reload_followup_grounding) throw Error('Image follow-up was not grounded in the prior image');
  const third = await send('Look at the original image again very carefully. What exact letters and number are in the heading?');
  if (third.selection_mode !== 'clean_compact_v3_preview') throw Error('OCR retry did not use clean v3');
  report.checks.ocr_retry_route = true;
  report.checks.ocr_retry_grounding = /odysseus\s*42/i.test(third.final);
  const fourth = await send('Use the OCR tool to extract the heading text from the attached image, not its filename.');
  report.checks.explicit_ocr_called = fourth.tools.some(tool => tool.tool === 'extract_text' && tool.exit_code === 0);
  report.checks.explicit_ocr_grounding = /odysseus\s*42/i.test(fourth.final);
  const fifth = await send('Run OCR on that same image again, but return only the number this time.');
  report.checks.numeric_ocr_called = fifth.tools.some(tool => tool.tool === 'extract_text' && tool.exit_code === 0);
  report.checks.numeric_ocr_grounding = /\b42\b/.test(fifth.final);
  report.checks.no_tool_errors = report.turns.every(turn => turn.tools.every(tool => !tool.error && tool.exit_code === 0));
  report.status = Object.values(report.checks).every(Boolean) ? 'passed' : 'partial';
} catch (error) {
  report.status = 'failed';
  report.error = `${error.name}: ${error.message}`;
} finally {
  if (session && context) {
    const removed = await context.request.delete(`${base}/api/session/${encodeURIComponent(session)}`);
    report.cleanup = { session, status: removed.status(), removed: removed.ok() };
    if (!report.cleanup.removed) report.status = 'failed';
  }
  save();
  if (browser) await browser.close();
}

console.log(JSON.stringify({ status: report.status, turns: report.turns.map(t => ({ mode: t.selection_mode, final: t.final })), cleanup: report.cleanup, error: report.error }));
if (report.status !== 'passed') process.exitCode = 1;

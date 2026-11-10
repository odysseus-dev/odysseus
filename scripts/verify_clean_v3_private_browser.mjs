#!/usr/bin/env node
/** Deliberate private-browser permission, typed follow-up warmth, and isolation. */
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const base = process.env.BASE_URL || 'http://127.0.0.1:7011';
const owner = 'sft_alex_creator';
const endpointId = process.env.ENDPOINT_ID || '1d1022ef';
const endpointUrl = process.env.ENDPOINT_URL || (() => { throw new Error("ENDPOINT_URL is required"); })();
const run = new Date().toISOString().replace(/[:.]/g, '-');
const reportPath = path.resolve(process.env.REPORT_PATH || path.join(root, `reports/clean-v3-private-browser-${run}.json`));
if (!reportPath.startsWith(path.join(root, 'reports') + path.sep) || fs.existsSync(reportPath)) throw Error('Report path must be new and under reports/');
const auth = JSON.parse(fs.readFileSync(process.env.ODYSSEUS_AUTH_SESSION_FILE || (() => { throw new Error("ODYSSEUS_AUTH_SESSION_FILE is required"); })(), 'utf8'));
const token = Object.entries(auth).find(([, value]) => value?.username === owner)?.[0];
if (!token) throw Error(`No active ${owner} session`);
const report = { run, owner, status: 'running', turns: [], cleanup: [], privacy: 'Public example.com only; report stores sanitized contract and status fields.' };
const save = () => { fs.mkdirSync(path.dirname(reportPath), { recursive: true }); fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n'); };
save();
const bare = value => String(value || '').replace(/^mcp__email__/, '');
const noLeak = value => !/<think>|Thinking Process:|UNTRUSTED SOURCE DATA|Analyze the Request:/i.test(String(value || ''));
const parseSSE = body => body.replace(/\r\n/g, '\n').split('\n\n').flatMap(frame => {
  const raw = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!raw || raw === '[DONE]') return [];
  try { return [JSON.parse(raw)]; } catch { return [{ type: 'invalid_sse' }]; }
});

let browser, context, page;
const sessions = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  context = await browser.newContext({ serviceWorkers: 'block', extraHTTPHeaders: { 'Accept-Encoding': 'identity' } });
  await context.addCookies([{ name: 'odysseus_session', value: token, url: base }]);
  const makeSession = async suffix => {
    const created = await context.request.post(`${base}/api/session`, { multipart: {
      name: `[clean-v3-private-browser] ${suffix}-${run}`, model: 'odysseus-qwen3.5-tools-pre-heretic', endpoint_id: endpointId,
      endpoint_url: endpointUrl, skip_validation: 'true', rag: 'false',
    }});
    if (!created.ok()) throw Error(`Session create HTTP ${created.status()}`);
    const id = (await created.json()).id;
    sessions.push(id);
    return id;
  };
  const openSession = async id => {
    if (page) await page.close();
    page = await context.newPage();
    await page.goto(`${base}/#${id}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForFunction(value => window.sessionModule?.getCurrentSessionId() === value, id);
    const agent = page.locator('#mode-agent-btn');
    if (await agent.getAttribute('aria-pressed') !== 'true') await agent.click();
    if (await page.locator('#web-toggle').isChecked()) await page.locator('#web-toggle-btn').click();
    if (await page.locator('#bash-toggle').isChecked()) await page.locator('#bash-toggle-btn').click();
  };
  const send = async prompt => {
    const waiting = page.waitForResponse(r => new URL(r.url()).pathname === '/api/chat_stream' && r.request().method() === 'POST', { timeout: 120000 });
    await page.locator('textarea#message:visible').fill(prompt);
    await page.locator('textarea#message:visible').press('Enter');
    const response = await waiting;
    const events = parseSSE(await response.text());
    const contract = events.find(x => x.type === 'turn_contract') || {};
    const tools = events.filter(x => x.type === 'tool_start').map(x => bare(x.tool));
    const outputs = events.filter(x => x.type === 'tool_output').map(x => ({ tool: bare(x.tool), command: x.command || '', exit_code: x.exit_code ?? null, error: Boolean(x.error) }));
    const final = events.filter(x => x.type === 'final_response').map(x => x.content || '').join('') || events.filter(x => typeof x.delta === 'string').map(x => x.delta).join('');
    return { response, contract, tools, outputs, final };
  };

  const browserSession = await makeSession('deliberate');
  await openSession(browserSession);
  const opened = await send('Browse https://example.com and take a snapshot. Report the rendered page heading.');
  await page.locator('.private-browser-preview-img[src^="data:image/"]').last().waitFor({ state: 'visible', timeout: 10000 });
  const screenshotState = await page.locator('.private-browser-preview-img[src^="data:image/"]').last().evaluate(img => ({
    complete: img.complete,
    naturalWidth: img.naturalWidth,
    sourceLength: img.getAttribute('src')?.length || 0,
  }));
  const openChecks = {
    http_ok: opened.response.ok(), clean_route: opened.contract.selection_mode === 'clean_compact_v3_preview',
    offered_private_browser: (opened.contract.offered || []).some(x => bare(x) === 'private_browser'),
    browser_only: opened.tools.length >= 1 && opened.tools.every(x => x === 'private_browser'),
    tool_success: opened.outputs.some(x => x.tool === 'private_browser' && !x.error && (x.exit_code == null || x.exit_code === 0)),
    screenshot_visible: screenshotState.complete && screenshotState.naturalWidth > 0 && screenshotState.sourceLength > 100,
    grounded: /example domain/i.test(opened.final), no_reasoning_leak: noLeak(opened.final),
  };
  report.turns.push({ kind: 'domain-browse-snapshot-web-off', tools: opened.tools, outputs: opened.outputs, offered_private_browser: openChecks.offered_private_browser, checks: openChecks, status: Object.values(openChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const persistedAfterOpen = await context.request.get(`${base}/api/history/${encodeURIComponent(browserSession)}`);
  const persistedBody = await persistedAfterOpen.json();
  report.typed_evidence_after_open = (persistedBody.history || []).slice(-3).map(item => ({
    role: item.role,
    tools: (item.metadata?.tool_events || []).map(event => ({
      tool: bare(event.tool), exit_code: event.exit_code ?? null, error: Boolean(event.error),
    })),
  }));
  save();

  const follow = await send('What heading is visible on that page? Check the current page before answering.');
  const followChecks = {
    http_ok: follow.response.ok(), clean_route: follow.contract.selection_mode === 'clean_compact_v3_preview',
    warm_private_browser: (follow.contract.offered || []).some(x => bare(x) === 'private_browser'),
    browser_only: follow.tools.length >= 1 && follow.tools.every(x => x === 'private_browser'),
    tool_success: follow.outputs.some(x => x.tool === 'private_browser' && !x.error && (x.exit_code == null || x.exit_code === 0)),
    grounded: /example domain/i.test(follow.final), no_reasoning_leak: noLeak(follow.final),
  };
  report.turns.push({ kind: 'typed-evidence-follow-up-web-off', tools: follow.tools, outputs: follow.outputs, offered_private_browser: followChecks.warm_private_browser, offered: (follow.contract.offered || []).map(bare), unavailable: follow.contract.unavailable || [], active_capabilities: follow.contract.active_capabilities || [], checks: followChecks, status: Object.values(followChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const mapsSession = await makeSession('plain-open-preview');
  await openSession(mapsSession);
  const maps = await send('Browse Google Maps and find the closest coffee shop to Todoroki Station.');
  await page.locator('.private-browser-preview-img[src^="data:image/"]').last().waitFor({ state: 'visible', timeout: 10000 });
  const mapsScreenshot = await page.locator('.private-browser-preview-img[src^="data:image/"]').last().evaluate(img => ({
    complete: img.complete,
    naturalWidth: img.naturalWidth,
    sourceLength: img.getAttribute('src')?.length || 0,
  }));
  const mapsChecks = {
    http_ok: maps.response.ok(),
    browser_used: maps.tools.includes('private_browser'),
    screenshot_visible: mapsScreenshot.complete && mapsScreenshot.naturalWidth > 0 && mapsScreenshot.sourceLength > 100,
    no_reasoning_leak: noLeak(maps.final),
  };
  report.turns.push({ kind: 'plain-open-renders-screenshot', tools: maps.tools, checks: mapsChecks, status: Object.values(mapsChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const menu = await send('Which one has a grilled cheese sandwich on the menu?');
  const menuCommands = menu.outputs.map(item => String(item.command || '').toLowerCase());
  const menuChecks = {
    http_ok: menu.response.ok(),
    web_followup_used: menu.tools.some(tool => ['private_browser', 'web_search', 'web_fetch'].includes(tool)),
    prior_subject_retained: menuCommands.some(command => /todoroki|coffee shop|peak by swell|yeti roastery|toe coffee/.test(command)),
    no_reasoning_leak: noLeak(menu.final),
  };
  report.turns.push({ kind: 'maps-result-property-followup', tools: menu.tools, commands: menuCommands, checks: menuChecks, status: Object.values(menuChecks).every(Boolean) ? 'passed' : 'failed' }); save();

  const searchSession = await makeSession('ordinary-web');
  await openSession(searchSession);
  await page.locator('#web-toggle-btn').click();
  const search = await send('Search the web for the official Python Packaging User Guide and give me its URL.');
  const searchChecks = {
    http_ok: search.response.ok(), clean_route: search.contract.selection_mode === 'clean_compact_v3_preview',
    private_browser_absent: !(search.contract.offered || []).some(x => bare(x) === 'private_browser'),
    no_private_browser_call: search.tools.every(x => x !== 'private_browser'),
    search_used: search.tools.some(x => x === 'web_search'), no_reasoning_leak: noLeak(search.final),
  };
  report.turns.push({ kind: 'ordinary-web-does-not-grant-browser', tools: search.tools, offered_private_browser: !searchChecks.private_browser_absent, checks: searchChecks, status: Object.values(searchChecks).every(Boolean) ? 'passed' : 'failed' });
} catch (error) {
  report.error = String(error).split('\n')[0].slice(0, 500);
} finally {
  if (page) await page.close();
  if (context) {
    for (const id of sessions) {
      const removed = await context.request.delete(`${base}/api/session/${encodeURIComponent(id)}`);
      report.cleanup.push({ removed: removed.ok() });
    }
  }
  if (browser) await browser.close();
}
report.status = report.turns.length === 5 && report.turns.every(x => x.status === 'passed') && report.cleanup.length === sessions.length && report.cleanup.every(x => x.removed) ? 'passed' : 'failed';
report.summary = { passed: report.turns.filter(x => x.status === 'passed').length, total: 5 };
save();
console.log(JSON.stringify({ report: path.relative(root, reportPath), status: report.status, summary: report.summary }));
if (report.status !== 'passed') process.exitCode = 1;

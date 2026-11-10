import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

// Execute the production browser entry functions with local network/session
// adapters. These are behavioral DOM tests, not assertions about source text.
const renderer = await readFile(new URL('../static/js/chatRenderer.js', import.meta.url), 'utf8');
const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const addMessage = renderer.slice(renderer.indexOf('export function addMessage('), renderer.indexOf('\nconst chatRenderer =')).replace('export ', '');
const resume = chat.slice(chat.indexOf('export async function resumeStream('), chat.indexOf('\n  /**\n   * Check for background streams')).replace('export ', '');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function setup() {
  const page = await browser.newPage();
  await page.route('http://render.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<main id="chat-history"></main><button class="send-btn">Send</button>' });
    if (path === '/static/js/ui.js') return route.fulfill({ contentType: 'text/javascript', body: 'export default window.uiModule;' });
    if (!path.startsWith('/static/')) return route.abort();
    const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
    return route.fulfill({ contentType: 'text/javascript', body: source });
  });
  await page.goto('http://render.test/');
  await page.evaluate(async ({ addMessage, resume }) => {
    const noop = () => {};
    const esc = value => { const node = document.createElement('div'); node.textContent = String(value ?? ''); return node.innerHTML; };
    Object.assign(window, {
      uiModule: { esc, scrollHistory: noop, showToast: value => window.toasts.push(value), showError: error => { throw Error(error); }, el: id => document.getElementById(id), captureHistoryScroll: () => ({ y: scrollY }), restoreHistoryScroll: s => scrollTo(0, s.y) },
      hideWelcomeScreen: noop, resolveDocumentPlaceholderLinks: x => x,
      replyModelPair: () => ({ actualModel: 'test', requestedModel: 'test' }),
      modelRouteLabel: () => 'test', sameModelName: () => true, applyModelColor: noop,
      roleTimestamp: () => document.createElement('span'),
      createMsgFooter: () => { const node = document.createElement('div'); node.className = 'msg-footer'; node.textContent = 'Copy'; return node; },
      displayMetrics: (node, metrics) => { node.dataset.metricsOwner = metrics.render_owner || ''; },
      _suppressRawToolOutput: () => false, safeToolScreenshotSrc: () => '', _isPrivateBrowserTool: () => false,
      _toolDisplayInfo: () => ({}), renderToolIcon: () => '',
      buildSourcesBox: () => '<div class="sources-section">Sources</div>',
      buildFindingsBox: () => '<div class="sources-section">Findings</div>',
      buildRagSourcesBox: () => '<details class="rag-sources"><summary>Documents</summary></details>',
      API_BASE: '', hasActiveStream: () => false,
      _streamRunIds: new Map(), _resumingStreams: new Set(), _backgroundStreams: new Map(),
      updateSubmitButton: noop, _shortModel: x => x, _applyModelColor: noop,
      _streamDisplayText: x => x, createTerminalStreamError: x => Error(x.message || 'Stream error'),
      _finishDocumentWritingStatus: noop,
      spinnerModule: { create: () => { const node = document.createElement('span'); return { createElement: () => node, start: noop, destroy: () => node.remove() }; } },
      reloads: 0, currentSession: 's', savedHistory: [], labels: [], toasts: [],
      _setRoleModelLabel: (role, requested, actual) => { window.labels.push({ requested, actual }); role.textContent = requested + ' -> ' + actual; },
      _metricsCostRecordId: () => 'test-run',
      sessionModule: { getCurrentSessionId: () => window.currentSession, getSessions: () => [{ id: 's', model: 'test' }], markStreaming: noop, clearStreaming: noop, selectSession: () => { window.reloads++; }, loadSessions: () => { window.reloads++; } },
    });
    window.markdownModule = (await import('/static/js/markdown.js')).default;
    markdownModule.renderMermaid = undefined;
    window.createTurnRendering = (await import('/static/js/turnRendering.js')).createTurnRendering;
    window.startsContinuationRound = (await import('/static/js/turnRendering.js')).startsContinuationRound;
    window.applyModelRouteEventState = (await import('/static/js/chatModelProvenance.js')).applyModelRouteEventState;
    window.createTerminalStreamError = (await import('/static/js/chatStreamErrors.js')).createTerminalStreamError;
    window.generatedImageResult = (await import('/static/js/generatedImageResult.js')).generatedImageResult;
    window.addMessage = (0, eval)('(' + addMessage + ')');
    window.resumeStream = (0, eval)('(' + resume + ')');
    window.chatRenderer = { addMessage: window.addMessage, recordSessionMetricsCost: noop, buildSourcesBox, buildFindingsBox, buildRagSourcesBox };
    let controller;
    const stream = new ReadableStream({ start(value) { controller = value; } });
    window.send = event => controller.enqueue(new TextEncoder().encode('data: ' + (typeof event === 'string' ? event : JSON.stringify(event)) + '\n\n'));
    window.sendError = event => controller.enqueue(new TextEncoder().encode('event: error\ndata: ' + JSON.stringify(event) + '\n\n'));
    window.fetch = async url => String(url).includes('/api/chat/resume/')
      ? new Response(stream, { headers: { 'X-Odysseus-Run-Id': 'run-1' } })
      : new Response(JSON.stringify({ history: window.savedHistory }));
  }, { addMessage, resume });
  return page;
}

test('history scoped ownership omits drafts for both owners, retains reasoning and tools, places answer last', async () => {
  const page = await setup();
  try {
    for (const render_owner of ['structured', 'streamed']) {
      const result = await page.evaluate(render_owner => {
        document.querySelector('#chat-history').replaceChildren();
        const metadata = { _fromHistory: true, _db_id: 42, render_owner, replacement_scope: 'turn', round_texts: ['<think>Lookup reasoning</think>Draft notes', 'Outdated answer'], tool_events: [{ round: 1, tool: 'manage_notes', output: 'Tool evidence', exit_code: 0 }] };
        const original = JSON.stringify(metadata);
        addMessage('assistant', '[Canonical note](#note-42)', 'test', metadata);
        const root = document.querySelector('#chat-history');
        return { text: root.textContent, link: root.querySelector('.body a')?.getAttribute('href'), unchanged: original === JSON.stringify(metadata), order: [...root.children].map(x => x.className), finalRaw: root.lastElementChild.dataset.raw };
      }, render_owner);
      assert.doesNotMatch(result.text, /Draft notes|Outdated answer/);
      assert.match(result.text, /Lookup reasoning[\s\S]*Tool evidence[\s\S]*Canonical note/);
      assert.equal(result.link, '#note-42');
      assert.equal(result.unchanged, true);
      assert.match(result.order.at(-1), /msg-ai/);
      assert.equal(result.finalRaw, '[Canonical note](#note-42)');
    }
  } finally { await page.close(); }
});

test('legacy history keeps round prose without explicit replacement scope', async () => {
  const page = await setup();
  try {
    const text = await page.evaluate(() => {
      addMessage('assistant', 'Canonical', 'test', { _fromHistory: true, round_texts: ['Preamble', 'Old answer'], tool_events: [{ round: 1, tool: 'manage_notes' }] });
      return document.querySelector('#chat-history').textContent;
    });
    assert.match(text, /Preamble[\s\S]*Canonical/);
  } finally { await page.close(); }
});

async function startReplay(page) {
  await page.evaluate(() => { window.running = resumeStream('s'); });
  await page.waitForSelector('.msg-ai');
}

test('resume tool-only final is visible, scoped, and keeps the open timeline at completion', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    await page.evaluate(() => {
      send({ type: 'tool_start', tool: 'manage_notes', command: '{}' });
      send({ type: 'tool_output', tool: 'manage_notes', output: 'Evidence', exit_code: 0 });
    });
    await page.waitForSelector('.agent-thread-node:not(.running)');
    await page.evaluate(() => {
      window.thread = document.querySelector('.agent-thread');
      thread.querySelector('.agent-thread-node').classList.add('open');
      send({ type: 'final_response', content: '[Canonical](#note-42)', render_owner: 'structured', replacement_scope: 'turn' });
    });
    await page.waitForSelector('.body a[href="#note-42"]', { state: 'visible' });
    const result = await page.evaluate(async () => {
      window.link = document.querySelector('.body a');
      window.savedHistory = [{ role: 'assistant', content: '[Canonical](#note-42)', metadata: { _db_id: 42, render_owner: 'structured', replacement_scope: 'turn', round_texts: ['Stale draft', 'Canonical'], tool_events: [{ tool: 'manage_notes' }] } }];
      send({ type: 'message_saved', id: 42, render_owner: 'structured' });
      send('[DONE]');
      await running;
      return { sameThread: thread === document.querySelector('.agent-thread'), sameLink: link === document.querySelector('.body a'), open: thread.querySelector('.agent-thread-node').classList.contains('open'), reloads, count: document.querySelectorAll('.body a').length, id: link.closest('.msg-ai').dataset.dbId, text: document.querySelector('#chat-history').innerText };
    });
    assert.deepEqual({ ...result, text: undefined }, { sameThread: true, sameLink: true, open: true, reloads: 0, count: 1, id: '42', text: undefined });
    assert.doesNotMatch(result.text, /Stale draft/);
  } finally { await page.close(); }
});

test('resume preserves legitimate scoped synthesis and rejects unscoped conflicting prose', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    const result = await page.evaluate(async () => {
      send({ delta: 'Earlier draft', render_owner: 'streamed' });
      send({ type: 'agent_step', round: 2 });
      send({ type: 'final_response', content: 'Intermediate notes', render_owner: 'structured', replacement_scope: 'turn' });
      send({ delta: 'Unscoped conflict', render_owner: 'streamed' });
      send({ delta: 'Reasoning', thinking: true, render_owner: 'structured' });
      send({ delta: 'Legitimate ', render_owner: 'streamed', replacement_scope: 'turn' });
      send({ delta: 'synthesis', render_owner: 'streamed' });
      send('[DONE]');
      await running;
      return { text: document.querySelector('#chat-history').innerText, reloads };
    });
    assert.match(result.text, /Legitimate synthesis/);
    assert.doesNotMatch(result.text, /Earlier draft|Intermediate notes|Unscoped conflict|Reasoning/);
    assert.equal(result.reloads, 0);
  } finally { await page.close(); }
});

test('resume fallback and provider alias remain visible without a history reload', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    const result = await page.evaluate(async () => {
      send({ type: 'fallback', selected_model: 'selected-model', answered_by: 'fallback-model', reason: '429' });
      send({ type: 'model_actual', model: 'provider/fallback-alias' });
      send({ delta: 'hello' });
      send('[DONE]');
      await running;
      return { labels, toasts, reloads, holders: document.querySelectorAll('.msg-ai').length, role: document.querySelector('.role').textContent, text: document.querySelector('.body').innerText };
    });
    assert.deepEqual(result.labels, [{ requested: 'selected-model', actual: 'fallback-model' }, { requested: 'selected-model', actual: 'provider/fallback-alias' }]);
    assert.deepEqual(result.toasts, ['Fallback: selected-model failed — answered by fallback-model']);
    assert.equal(result.reloads, 0);
    assert.equal(result.holders, 1);
    assert.equal(result.text, 'hello');
    assert.match(result.role, /provider\/fallback-alias/);
  } finally { await page.close(); }
});

test('resume preoutput provider error stays visible as escaped text without reload', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    const result = await page.evaluate(async () => {
      sendError({ status: 401, error: 'invalid key <img src=x>' });
      send('[DONE]');
      await running;
      return { text: document.querySelector('.body').innerText, images: document.querySelectorAll('img').length, reloads };
    });
    assert.deepEqual(result, { text: '[Error: invalid key <img src=x>]', images: 0, reloads: 0 });
  } finally { await page.close(); }
});

test('resume terminal failure reconciles exact saved partial without reloading tool timeline', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    const result = await page.evaluate(async () => {
      window.savedHistory = [
        { role: 'assistant', content: 'Correct partial [Agent stopped]', metadata: { _db_id: 42, render_owner: 'structured', replacement_scope: 'turn' } },
        { role: 'assistant', content: 'Wrong later answer', metadata: { _db_id: 43 } },
      ];
      send({ delta: 'Partial' });
      send({ type: 'message_saved', id: 42 });
      send({ type: 'agent_terminal', data: { failure: { status: 429 } } });
      sendError({ status: 429, error: 'Rate limited' });
      send('[DONE]');
      await running;
      return { text: document.querySelector('.body').innerText, id: document.querySelector('.msg-ai').dataset.dbId, reloads };
    });
    assert.deepEqual(result, { text: 'Correct partial [Agent stopped]', id: '42', reloads: 0 });
  } finally { await page.close(); }
});

test('resume stable and complete events clear all round markers while preserving streamed prose and tools', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    await page.evaluate(() => {
      send({ delta: 'First streamed round' });
      send({ type: 'tool_start', tool: 'manage_notes' });
      send({ type: 'tool_output', tool: 'manage_notes', output: 'Evidence', exit_code: 0 });
      send({ type: 'agent_step', round: 2 });
      send({ delta: 'Final streamed round' });
    });
    await page.waitForFunction(() => document.querySelector('#chat-history').innerText.includes('Final streamed round'));
    await page.evaluate(() => {
      window.prose = [...document.querySelectorAll('.body p')];
      window.thread = document.querySelector('.agent-thread');
      send({ type: 'stable' });
    });
    await page.waitForFunction(() => !document.querySelector('#chat-history .streaming'));
    const result = await page.evaluate(async () => {
      thread.classList.add('streaming');
      send({ type: 'complete' });
      send('[DONE]');
      await running;
      return { sameProse: prose.every(p => p.isConnected), sameThread: thread === document.querySelector('.agent-thread'), markers: document.querySelectorAll('#chat-history .streaming').length, reloads };
    });
    assert.deepEqual(result, { sameProse: true, sameThread: true, markers: 0, reloads: 0 });
  } finally { await page.close(); }
});

test('resume structured final and completion remove all transient round bubbles and streaming markers', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    await page.evaluate(() => {
      send({ delta: 'Transient draft' });
      send({ type: 'agent_step', round: 2 });
      send({ delta: 'Another transient draft' });
      send({ type: 'final_response', content: '[Canonical](#note-42)', render_owner: 'structured', replacement_scope: 'turn' });
    });
    await page.waitForSelector('.body a[href="#note-42"]', { state: 'visible' });
    const result = await page.evaluate(async () => {
      const stableMarkers = document.querySelectorAll('#chat-history .streaming').length;
      const link = document.querySelector('.body a');
      send('[DONE]');
      await running;
      return { stableMarkers, markers: document.querySelectorAll('#chat-history .streaming').length, bodies: document.querySelectorAll('.msg-ai .body').length, same: link === document.querySelector('.body a'), text: document.querySelector('#chat-history').innerText };
    });
    assert.equal(result.stableMarkers, 0);
    assert.equal(result.markers, 0);
    assert.equal(result.bodies, 1);
    assert.equal(result.same, true);
    assert.doesNotMatch(result.text, /Transient|transient/);
  } finally { await page.close(); }
});

test('resume error clears earlier and current streaming markers without dropping partial prose', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    const result = await page.evaluate(async () => {
      send({ delta: 'First partial' });
      send({ type: 'agent_step', round: 2 });
      send({ delta: 'Second partial' });
      sendError({ status: 500, error: 'Provider failure' });
      send('[DONE]');
      await running;
      return { markers: document.querySelectorAll('#chat-history .streaming').length, text: document.querySelector('#chat-history').innerText, reloads };
    });
    assert.equal(result.markers, 0);
    assert.equal(result.reloads, 0);
    assert.match(result.text, /First partial[\s\S]*Second partial[\s\S]*Provider failure/);
  } finally { await page.close(); }
});

test('resume keeps the same initial bubble through preparation and round-one status', async () => {
  const page = await setup();
  try {
    await startReplay(page);
    await page.evaluate(() => {
      window.initialBubble = document.querySelector('.msg-ai');
      send({type: 'agent_step', stage: 'email_task_scope'});
      send({type: 'agent_step', round: 1});
      send({delta: 'Hello'});
    });
    await page.waitForFunction(() => document.querySelector('#chat-history').textContent.includes('Hello'));
    const result = await page.evaluate(async () => {
      const same = initialBubble === document.querySelector('.msg-ai');
      const count = document.querySelectorAll('.msg-ai').length;
      send('[DONE]');
      await running;
      return {same, count};
    });
    assert.deepEqual(result, {same: true, count: 1});
  } finally { await page.close(); }
});

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

// Run the complete production submit function with local service adapters.
// This keeps its try/catch scopes and request-owned DOM references intact.
const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const submit = chat.slice(chat.indexOf('  export async function handleChatSubmit('), chat.indexOf('\n  /**\n   * Abort current chat request')).replace('export ', '');
let browser;
before(async () => { browser = await chromium.launch({ headless: true }); });
after(async () => { await browser?.close(); });

async function setup(options = {}) {
  const page = await browser.newPage();
  await page.route('http://request.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<main id="chat-history"></main><textarea id="message">Hello</textarea><button class="send-btn">Send</button><input id="web-toggle" type="checkbox"><input id="research-toggle" type="checkbox"><input id="bash-toggle" type="checkbox"><input id="incognito-toggle" type="checkbox">' });
    if (path === '/static/js/ui.js') return route.fulfill({ contentType: 'text/javascript', body: 'export default window.uiModule;' });
    if (!path.startsWith('/static/')) return route.fulfill({ status: 404 });
    return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('..' + path, import.meta.url), 'utf8') });
  });
  await page.goto('http://request.test/');
  await page.evaluate(async ({ submit, options }) => {
    const noop = () => {};
    const esc = value => { const el = document.createElement('span'); el.textContent = String(value ?? ''); return el.innerHTML; };
    const emptyMap = () => new Map();
    Object.assign(window, {
      uiModule: { el: id => document.getElementById(id), esc, setAutoScroll: noop, scrollHistoryInstant: noop, scrollHistory: noop, showError: error => { throw Error(error); } },
      sessionModule: { getCurrentSessionId: () => 's', getSessions: () => [{ id: 's', model: 'test' }], getCurrentModel: () => 'test', clearStreamComplete: noop, markStreaming: noop, loadSessions: noop },
      fileHandlerModule: { getPendingCount: () => options.attachments ? 1 : 0, getPendingInfo: () => [{ name: 'photo.png', mime: 'image/png' }], uploadPending: async () => ['photo-id'] },
      Storage: { loadToggleState: () => window.toggles, get: () => '', setToggle: noop }, toggles: options.toggles || { mode: 'chat' },
      presetsModule: { getSelectedPreset: () => null }, slashCommands: { getSetupMode: () => null },
      documentModule: null, API_BASE: '', API_KEY_RE: /THIS_IS_A_SECRET/,
      isCommand: () => false, _adoptOpenedSessionBeforeAutoCreate: async () => {},
      _createChatSendPerf: () => ({ mark: noop, report: noop }), _setForegroundChatBusy: noop, _syncForegroundStreamGlobals: noop,
      _touchStreamActivity: noop, updateSubmitButton: noop, _bestKnownStreamModel: () => 'test', _modelRouteLabel: () => 'test', _applyModelColor: noop,
      _clearResearchTimer: noop, _drainQueuedAgentRequests: noop, _stopExactRun: () => true,
      _pendingToolApproval: null, _pendingApprovedPlan: '', _pendingRegenAttachments: null, _pendingContinue: null,
      _displayOverride: null, _hideUserBubble: !!options.regenerate, _autoNudges: 0, _autoContinuePending: false,
      _sendInFlight: false, isStreaming: false, currentAccumulated: '', currentHolder: null, currentSpinner: null, currentAbort: null, _streamSessionId: null, _webLockRelease: null,
      _streamGenerations: emptyMap(), _streamRunIds: emptyMap(), _sendStates: emptyMap(), _activeStreams: emptyMap(), _pendingRunStops: emptyMap(), _selectedNoteIdsBySession: emptyMap(), _backgroundStreams: emptyMap(),
      _terminalSavedStreams: new Set(), _researchingStreamIds: new Set(),
      DEFAULT_TIMEOUT_MS: 120000, RESEARCH_TIMEOUT_MS: 360000, RUN_ID_ABORT_GRACE_MS: 2000,
      spinnerModule: { create() { const node = document.createElement('span'); node.textContent = 'Processing request'; return { element: node, createElement: () => node, start: noop, updateMessage: noop, destroy() { node.remove(); } }; } },
      chatRenderer: { updateMessageAttachments: noop },
      addMessage(role, text) {
        const node = document.createElement('div'); node.className = 'msg msg-' + role;
        const attachment = options.preview
          ? '<div class="attach-image-preview" data-file-id="photo-id"><img alt="photo.png"><button class="attach-ocr-btn">Caption</button><div class="attach-image-name">photo.png</div></div>'
          : '<div class="attach-card" data-name="photo.png">photo.png</div>';
        node.innerHTML = '<div class="body">' + esc(text) + (options.attachments ? '<div class="attach-cards">' + attachment + '</div>' : '') + '</div>';
        document.getElementById('chat-history').appendChild(node); return node;
      },
      _streamDisplayText: x => x, _suppressThinkingForPersona: () => false,
    });
    window.markdownModule = (await import('/static/js/markdown.js')).default;
    Object.assign(window, await import('/static/js/liveThinkingThrottle.js'));
    Object.assign(window, await import('/static/js/turnRendering.js'));
    window.handleChatSubmit = (0, eval)('(' + submit + ')');
    const nativeSetTimeout = window.setTimeout.bind(window);
    window.setTimeout = (fn, ms, ...args) => {
      if (ms === DEFAULT_TIMEOUT_MS || ms === RESEARCH_TIMEOUT_MS) { window.fireTimeout = fn; window.timeoutMs = ms; return -1; }
      return nativeSetTimeout(fn, ms, ...args);
    };
    window.fetch = async (url, init) => {
      if (url !== '/api/chat_stream') throw Error('unexpected request ' + url);
      window.sentMode = init.body.get('mode');
      if (options.timeout) {
        // A mode change while waiting must not change this request's error.
        window.toggles = { mode: options.toggles?.mode === 'agent' ? 'chat' : 'agent' };
        return new Promise((resolve, reject) => {
          init.signal.addEventListener('abort', () => reject(new DOMException('Timed out', 'AbortError')));
          fireTimeout();
        });
      }
      let controller;
      const stream = new ReadableStream({ start(value) { controller = value; } });
      window.send = event => controller.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(event) + '\n\n'));
      return new Response(stream);
    };
    if (options.regenerate) {
      window.originalUser = addMessage('user', 'Original photo');
      _pendingRegenAttachments = ['photo-id'];
    }
  }, { submit, options });
  return page;
}

for (const [label, toggles, message, expected, timeoutMs] of [
  ['chat', { mode: 'chat' }, 'Hello', 'Response timed out. Try again.', 120000],
  ['agent', { mode: 'agent' }, 'Hello', 'Agent response timed out.', 360000],
  ['plan', { mode: 'chat', plan_mode: true }, 'Hello', 'Agent response timed out.', 360000],
  ['workspace intent', { mode: 'chat' }, 'Edit example.py', 'Agent response timed out.', 360000],
]) {
  test(`${label} timeout preserves captured mode and settles its empty bubble`, async () => {
    const page = await setup({ timeout: true, toggles });
    try {
      await page.locator('#message').fill(message);
      const result = await page.evaluate(async () => {
        await handleChatSubmit({ preventDefault() {} });
        return { text: document.querySelector('.msg-ai .body').innerText, timeoutMs, active: _activeStreams.size, enabled: !document.querySelector('#message').disabled };
      });
      assert.ok(result.text.includes(expected), result.text);
      assert.equal(result.timeoutMs, timeoutMs);
      assert.equal(result.active, 0);
      assert.equal(result.enabled, true);
    } finally { await page.close(); }
  });
}

for (const [label, regenerate, preview] of [['new file chip', false, false], ['optimistic image preview', false, true], ['regenerated image preview', true, true]]) {
  test(`attachment SSE updates its ${label} after assistant insertion`, async () => {
    const page = await setup({ attachments: true, regenerate, preview });
    try {
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.evaluate(() => { window.running = handleChatSubmit({ preventDefault() {} }); });
      await page.waitForFunction(() => typeof window.send === 'function');
      await page.evaluate(() => {
        window.targetUser = document.querySelector('.msg-user');
        window.originalPreview = targetUser.querySelector('.attach-image-preview');
        window.captionButton = targetUser.querySelector('.attach-ocr-btn');
        // A later turn must not steal the earlier request's attachment event.
        window.laterUser = addMessage('user', 'Later photo');
        laterUser.querySelector('.attach-cards').innerHTML = '<div class="attach-card" data-name="photo.png">Later file</div>';
        send({ type: 'attachments', data: [{ id: 'photo-id', name: 'photo.png', mime: 'image/png' }] });
      });
      await page.waitForSelector('.attach-image-preview');
      await page.evaluate(() => send({ type: 'attachments', data: [{ id: 'photo-id', name: 'photo.png', mime: 'image/png', vision_model: 'provider/vision-small' }] }));
      await page.waitForSelector('.attach-vision-model');
      const result = await page.evaluate(() => ({
        previews: targetUser.querySelectorAll('.attach-image-preview').length,
        vision: targetUser.querySelector('.attach-vision-model')?.textContent,
        laterPreviews: laterUser.querySelectorAll('.attach-image-preview').length,
        firstRole: targetUser.nextElementSibling.className,
        preservedPreview: !originalPreview || originalPreview === targetUser.querySelector('.attach-image-preview'),
        preservedCaption: !captionButton || captionButton === targetUser.querySelector('.attach-ocr-btn'),
      }));
      assert.equal(result.previews, 1);
      assert.equal(result.vision, 'Vision: vision-small');
      assert.equal(result.laterPreviews, 0);
      assert.match(result.firstRole, /msg-ai/);
      assert.equal(result.preservedPreview, true);
      assert.equal(result.preservedCaption, true);
      assert.deepEqual(errors, []);
    } finally { await page.close(); }
  });
}

test('read aloud uses the display reasoning parser and retains ordinary reply formatting', async () => {
  const page = await setup();
  try {
    const result = await page.evaluate(async () => {
      // Settings disable availability probing; extraction itself remains real.
      window.fetch = async () => new Response(JSON.stringify({ tts_enabled: false }));
      const { AITTSManager } = await import('/static/js/tts-ai.js');
      const manager = new AITTSManager();
      return [
        '<think>private thought</think>Hello **world**.',
        '<think time="24.5">private thought</think>### Hello **world**.',
        '<thinking time="2">private thought</thinking>Hello **world**.',
        '<thought>private thought</thought>Hello **world**.',
        '<mm:think>private thought</mm:think>Hello **world**.',
        'Thinking: private thought\n\nHello **world**.',
        '<|channel>thought\nprivate thought<channel|>Hello **world**.',
        '<think time="2">only private thought</think>',
        '### Heading\nA [link](https://example.test) and <code>secret code</code>.',
      ].map(input => manager.extractPlainText(input));
    });
    assert.deepEqual(result, [
      'Hello world.', 'Hello world.', 'Hello world.', 'Hello world.', 'Hello world.', 'Hello world.', 'Hello world.', '', 'Heading\nA link and .',
    ]);
  } finally { await page.close(); }
});

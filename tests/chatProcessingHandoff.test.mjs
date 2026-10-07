import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const renderCode = chat.slice(chat.indexOf('      function _finishProcessingWhenVisible('), chat.indexOf('      let _nextIsError = false;'));

test('processing remains until visible reply content replaces it in the same bubble', async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.route('http://handoff.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({contentType: 'text/html', body: '<main><div class="msg-ai"><div class="body"><span class="ai-spinner">Processing request</span></div></div></main>'});
      const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
      return route.fulfill({contentType: 'text/javascript', body: source});
    });
    await page.goto('http://handoff.test/');
    const result = await page.evaluate(async renderCode => {
      const roundHolder = document.querySelector('.msg-ai');
      const spinnerNode = document.querySelector('.ai-spinner');
      const spinner = {element: spinnerNode, destroy() { this.element.remove(); this.element = null; }};
      Object.assign(window, {
        roundHolder, streamSessionId: 'chat', _renderStream: null,
        sessionModule: {getCurrentSessionId: () => 'chat'},
        _suppressThinkingForPersona: () => false, _docFenceOpened: false,
        uiModule: {scrollHistory() {}},
        markdownModule: {
          squashOutsideCode: text => text,
          processWithThinking: text => text.trim() ? `<p>${text}</p>` : '',
        },
        _ensureStreamLayout(body) {
          let content = body.querySelector('.stream-content');
          if (!content) {
            content = document.createElement('div');
            content.className = 'stream-content';
            body.appendChild(content);
          }
          return content;
        },
        createStreamRenderer: (await import('/static/js/streamingRenderer.js')).createStreamRenderer,
      });
      const render = new Function('spinner', renderCode + '\nreturn _renderStream;')(spinner);
      render({knownNormal: true, displayText: '\n'});
      const waiting = spinnerNode.isConnected && roundHolder.innerText.includes('Processing request');
      render({knownNormal: true, displayText: '\nHello'});
      const visible = document.querySelector('.stream-content');
      return {waiting, sameBubble: roundHolder === document.querySelector('.msg-ai'),
        spinnerRemoved: !spinnerNode.isConnected, answer: visible.innerText.trim(),
        initialFade: !!visible.querySelector('.token-new')};
    }, renderCode);
    assert.deepEqual(result, {waiting: true, sameBubble: true, spinnerRemoved: true, answer: 'Hello', initialFade: false});
  } finally { await browser.close(); }
});

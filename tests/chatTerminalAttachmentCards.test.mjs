// The end-of-turn re-render (final_response, or the saved answer after a
// tool turn) replaces the reply body. A create_pdf download card in that
// body must survive it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const renderCode = chat.slice(
  chat.indexOf('      function _terminalAnswerHtml('),
  chat.indexOf('      async function _replaceLiveTurnWithSavedAssistantMessage('),
);
const PDF = { id: 'a'.repeat(32) + '.pdf', name: 'Gitea_setup.pdf', mime: 'application/pdf', size: 68962 };

async function renderTerminal(args) {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route('http://terminal.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') {
        return route.fulfill({ contentType: 'text/html', body: '<main id="box"><span id="start"></span><div class="msg-ai"><div class="body"><p>streamed</p></div></div></main>' });
      }
      if (path.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: '{}' });
      const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
      return route.fulfill({ contentType: 'text/javascript', body: source });
    });
    await page.goto('http://terminal.test/');
    return await page.evaluate(async ({ renderCode, args, pdf }) => {
      const { createTurnRendering } = await import('/static/js/turnRendering.js');
      const chatRenderer = (await import('/static/js/chatRenderer.js')).default;
      const roundHolder = document.querySelector('.msg-ai');
      const holder = { _attachments: args.live ? [pdf] : undefined };
      const _turnRendering = createTurnRendering({ root: document.getElementById('box'), start: document.getElementById('start') });
      const markdownModule = {
        processWithThinking: text => `<p>${text}</p>`,
        squashOutsideCode: text => text,
        normalizeThinkingMarkup: text => text,
      };
      const run = new Function(
        'roundHolder', 'holder', '_turnRendering', 'markdownModule', 'chatRenderer',
        `const _sourcesExpanded = false, _sourcesData = null, _sourcesType = '', _findingsData = null, _docFenceOpened = false;
         const _streamDisplayText = text => text;
         const _ensureVisibleRoundForDelta = () => {};
         ${renderCode}
         return _renderTerminalAnswer;`,
      );
      const renderTerminalAnswer = run(roundHolder, holder, _turnRendering, markdownModule, chatRenderer);
      renderTerminalAnswer('The PDF covers the Gitea setup.', '42', undefined, undefined,
        ...(args.saved ? [[pdf]] : []));
      const body = roundHolder.querySelector('.body');
      return { text: body.textContent, cards: body.querySelectorAll('a.attach-card').length };
    }, { renderCode, args, pdf: PDF });
  } finally {
    await browser.close();
  }
}

test('the card from the live attachments event survives the final re-render', async () => {
  const result = await renderTerminal({ live: true });
  assert.match(result.text, /The PDF covers the Gitea setup\./);
  assert.equal(result.cards, 1);
});

test('the card from the saved message survives the reconciliation re-render', async () => {
  const result = await renderTerminal({ saved: true });
  assert.equal(result.cards, 1);
});

test('replies without files get no card', async () => {
  const result = await renderTerminal({});
  assert.equal(result.cards, 0);
});

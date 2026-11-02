import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const chat = await readFile(new URL('../static/js/chat.js', import.meta.url), 'utf8');
const documentSource = await readFile(new URL('../static/js/document.js', import.meta.url), 'utf8');
const start = chat.indexOf('    let _ttftDisplayTimer = null;');
const end = chat.indexOf('    const clearFirstTokenWaitTimers =', start);
assert.ok(start >= 0 && end > start);
const statusCode = chat.slice(start, end);
const finishStart = chat.indexOf('      let finishEditorButton = null;');
const finishEnd = chat.indexOf('      let roundFinalized = false;', finishStart);
assert.ok(finishStart >= 0 && finishEnd > finishStart);
const finishCode = chat.slice(finishStart, finishEnd);

test('editor counts use the existing agent status spinner and keep ticking', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route('http://progress.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<main></main>' });
      const source = await readFile(new URL('..' + path, import.meta.url), 'utf8');
      return route.fulfill({ contentType: 'text/javascript', body: source });
    });
    await page.goto('http://progress.test/');
    const result = await page.evaluate(async code => {
      const { create } = await import('/static/js/spinner.js');
      const spinner = create('Processing request', 'right', 'wave');
      document.querySelector('main').appendChild(spinner.createElement());
      const _ttftStartedAt = performance.now() - 54000;
      const setup = eval(code + `
        _editorProgress = {kind:'suggestions', phase:'preparing'};
        _startTtftDisplay();
        const preparing = spinner.element.textContent;
        _editorProgress = {kind:'suggestions', phase:'drafting', proposed:9};
        const agentClass = spinner.element.classList.contains('ai-spinner');
        window.stopStatus = _stopTtftDisplay;
        ({preparing, agentClass});
      `);
      await new Promise(resolve => setTimeout(resolve, 220));
      const proposed = spinner.element.textContent;
      window.stopStatus();
      return { ...setup, proposed };
    }, statusCode);
    assert.match(result.preparing, /Reviewing · 54\.\ds/);
    assert.match(result.proposed, /9 proposed · 54\.\ds/);
    assert.equal(result.agentClass, true);
    assert.equal(documentSource.includes('doc-ai-progress'), false);
  } finally {
    await browser.close();
  }
});

test('finish action uses the exact run and keeps the agent thread visible', async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const seen = [];
    await page.route('http://progress.test/**', async route => {
      if (new URL(route.request().url()).pathname === '/') {
        return route.fulfill({ contentType: 'text/html', body: '<main></main>' });
      }
      seen.push({ url: route.request().url(), runId: route.request().headers()['x-odysseus-run-id'] });
      return route.fulfill({ contentType: 'application/json', body: '{"accepted":true}' });
    });
    await page.goto('http://progress.test/');
    const result = await page.evaluate(async code => {
      const lastToolThread = document.createElement('div');
      lastToolThread.className = 'agent-thread';
      document.querySelector('main').appendChild(lastToolThread);
      const streamSessionId = 'editor-session';
      const _streamRunIds = new Map([[streamSessionId, 'exact-run']]);
      const API_BASE = '';
      const uiModule = { showError() { throw new Error('finish failed'); } };
      eval(code + '\nofferFinishEditorTurn();');
      const button = lastToolThread.querySelector('button');
      const agentStyle = button.classList.contains('continue-btn') && button.classList.contains('resume-btn');
      button.click();
      await new Promise(resolve => setTimeout(resolve, 60));
      return { agentStyle, text: button.textContent, threadVisible: lastToolThread.isConnected };
    }, finishCode);
    assert.deepEqual(result, { agentStyle: true, text: 'Finishing…', threadVisible: true });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].runId, 'exact-run');
    assert.match(seen[0].url, /\/api\/chat\/finish\/editor-session$/);
  } finally {
    await browser.close();
  }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');

async function setup(t, routeHandler) {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'integrated module graph must not throw'));
  const requests = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    requests.push({ path: new URL(request.url()).pathname, method: request.method(), body: request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null });
    if (await routeHandler?.(route, requests.at(-1))) return;
    await route.fulfill({ json: { version_count: 2 } });
  });
  await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"><textarea id="message"></textarea></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js');
    editor.init('');
    window.state = await import('/static/js/document/application/documentState.js');
    window.preview = await import('/static/js/document/ui/previewModes.js');
  });
  return { page, requests };
}

async function inject(page, id, content, language = 'markdown', title = `${id} title`) {
  await page.evaluate(async data => editor.injectFreshDoc(data), {
    id, title, language, current_content: content, version_count: 2,
  });
}

async function edit(page, content) {
  await page.evaluate(value => {
    preview._setMarkdownPreviewActive(false);
    const ta = document.getElementById('doc-editor-textarea');
    ta.value = value;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }, content);
}

test('ownership: rapid minimize/reopen preserves cached snapshots and outgoing PUT', async t => {
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method !== 'PUT') return false;
    await held;
    await route.fulfill({ json: { version_count: 3 } });
    return true;
  });
  await inject(page, 'a', 'A initial');
  await inject(page, 'b', 'B original');
  for (let iteration = 0; iteration < 4; iteration++) {
    await page.evaluate(() => editor.loadDocument('a'));
    await edit(page, `A latest ${iteration}`);
    await page.evaluate(async () => { editor.closePanel('down'); await editor.loadDocument('b'); });
    const snapshot = await page.evaluate(() => ({
      a: { content: state.docs.get('a').content, title: state.docs.get('a').title, language: state.docs.get('a').language },
      b: state.docs.get('b').content,
    }));
    assert.deepEqual(snapshot, { a: { content: `A latest ${iteration}`, title: 'a title', language: 'markdown' }, b: 'B original' });
  }
  assert(requests.some(request => request.method === 'PUT'));
  release();
  await page.waitForFunction(() => state.docs.get('a')._saveState === 'saved');
  const puts = requests.filter(request => request.method === 'PUT');
  assert(puts.length > 0);
  assert(puts.every(request => request.path === '/api/document/a' && request.body.content.startsWith('A latest ')));
  assert.equal(puts.at(-1).body.content, 'A latest 3');
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'B original');
});

test('email: real envelope input, clearing recipients, local restoration and autosave', async t => {
  const original = 'To: old@example.com\nCc: oldcc@example.com\nBcc: oldbcc@example.com\nSubject: Draft\nX-Source-UID: 42\nX-Source-Folder: INBOX\n---\nBody';
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method !== 'GET' || request.path !== '/api/document/email') return false;
    await route.fulfill({ json: { id: 'email', language: 'email', current_content: original, title: 'Draft' } });
    return true;
  });
  await inject(page, 'email', original, 'email');
  for (const [field, value] of [['to', 'new@example.com'], ['cc', 'cc@example.com'], ['bcc', 'bcc@example.com'], ['subject', 'New subject']]) {
    await page.evaluate(({ field, value }) => {
      const input = document.getElementById(`doc-email-${field}`);
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }, { field, value });
  }
  assert.equal(await page.evaluate(() => state.docs.get('email')._saveState), 'dirty');
  await page.waitForFunction(() => state.docs.get('email')._saveState === 'saved');
  const first = requests.filter(request => request.method === 'PUT').at(-1);
  assert.match(first.body.content, /To: new@example.com/);
  assert.match(first.body.content, /Cc: cc@example.com/);
  assert.match(first.body.content, /Bcc: bcc@example.com/);
  assert.match(first.body.content, /Subject: New subject/);
  await page.locator('[data-cc-close]').first().evaluate(button => button.click());
  await page.waitForFunction(() => state.docs.get('email')._saveState === 'saved');
  assert.doesNotMatch(requests.filter(request => request.method === 'PUT').at(-1).body.content, /\n(?:Cc|Bcc):/);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|42|'))));
  assert.equal(stored.to, 'new@example.com');
  assert.equal(stored.subject, 'New subject');
  assert.equal(stored.cc, '');
  assert.equal(stored.bcc, '');
  // Reload the actual module graph against an older server snapshot.
  await page.reload();
  await page.setContent(`${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"></main>`);
  await page.evaluate(async () => {
    window.editor = await import('/static/js/document.js'); editor.init('');
    await editor.loadDocument('email');
  });
  assert.equal(await page.locator('#doc-email-to').inputValue(), 'new@example.com');
  assert.equal(await page.locator('#doc-email-subject').inputValue(), 'New subject');
  assert.equal(await page.locator('#doc-email-cc').inputValue(), '');
  assert.equal(await page.locator('#doc-email-bcc').inputValue(), '');
});

test('email: Markdown preview shortcut executes integrated callback', async t => {
  const { page } = await setup(t);
  await inject(page, 'a', '# Markdown');
  await page.evaluate(() => preview._setMarkdownPreviewActive(false));
  await page.keyboard.press('Control+Alt+m');
  assert.equal(await page.locator('#doc-md-preview').evaluate(el => el.style.display !== 'none'), true);
});

test('titles: source work retains A identity across navigation', async t => {
  const { page, requests } = await setup(t);
  await inject(page, 'a', 'initial', 'markdown', '');
  await inject(page, 'b', 'B correct body', 'markdown', '');
  await page.evaluate(() => editor.loadDocument('a'));
  await edit(page, 'Title belonging to A');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.waitForFunction(() => state.docs.get('a').title === 'Title belonging to A');
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert(requests.some(request => request.path === '/api/document/a' && request.body?.title === 'Title belonging to A'));
  assert(!requests.some(request => request.path === '/api/document/b' && request.body?.title));
});

test('titles: image work is cancelled at disposal and repeated mount remains usable', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.path !== '/api/upload') return false;
    await route.fulfill({ json: { files: [{ id: 'uploaded', name: 'image.png' }] } });
    return true;
  });
  await page.clock.install();
  await inject(page, 'a', 'initial', 'markdown', '');
  await inject(page, 'b', 'B correct body', 'markdown', '');
  await page.evaluate(() => editor.loadDocument('a'));
  await edit(page, 'Image title from A');
  await page.evaluate(async () => {
    const images = await import('/static/js/document/ui/editor/markdownImages.js');
    await images._uploadMarkdownImages([new File(['image'], 'image.png', { type: 'image/png' })]);
    editor.closePanel('down');
    await editor.loadDocument('b');
  });
  await page.clock.runFor(900);
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert(!requests.some(request => request.method === 'PATCH' && request.body?.title));
  assert.equal(await page.evaluate(async () => {
    const source = await import('/static/js/document/ui/editor/sourceEditor.js');
    return source._autoTitleDebounce;
  }), null);
  await edit(page, 'Valid title after reopen');
  await page.clock.runFor(900);
  await page.waitForFunction(() => state.docs.get('b').title === 'Valid title after reopen');
});

import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');

async function setup(t, routeHandler, options = {}) {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 }, ...options });
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
  await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1">${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"><textarea id="message"></textarea></main>`);
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
  await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1">${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"></main>`);
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

for (const lifecycle of ['mounted', 'minimized', 'navigation', 'detached', 'successful deletion']) {
  test(`pdf rollback: ${lifecycle} retains edits with document-level persistence`, async t => {
    let releaseDelete;
    let deleteStarted;
    const gate = new Promise(resolve => { releaseDelete = resolve; });
    const started = new Promise(resolve => { deleteStarted = resolve; });
    const { page, requests } = await setup(t, async (route, request) => {
      if (request.path.endsWith('/render-pages')) {
        await route.fulfill({ json: { pages: [{ page: 1, width: 600, height: 800, fields: [{ name: 'Name', type: 'text', rect_px: [40, 40, 220, 80], value: 'Ada' }] }] } });
        return true;
      }
      if (request.method === 'DELETE') {
        deleteStarted();
        await gate;
        await route.fulfill({ status: lifecycle === 'successful deletion' ? 200 : 500, json: {} });
        return true;
      }
      return false;
    });
    await page.clock.install();
    await inject(page, 'b', 'B untouched');
    await inject(page, 'pdf-a', '<!-- pdf_form_source upload_id="source" -->\n- Name:** Ada <!-- field=Name type=text -->\n');
    await page.evaluate(async () => {
      window.pdf = await import('/static/js/document/ui/pdfTools.js');
      window.flows = await import('/static/js/document/ui/documentFlows.js');
      const host = await import('/static/js/document/ui/host.js');
      host.uiModule.styledConfirm = async () => true;
      await pdf._setPdfViewActive(true);
    });
    await page.locator('[data-field-name="Name"]').fill('Pending PDF edit');
    await page.evaluate(() => { window.deleting = flows.deleteActiveDocument(); });
    await started;
    if (lifecycle === 'navigation') await page.evaluate(() => editor.loadDocument('b'));
    if (lifecycle === 'detached') await page.evaluate(() => flows.closeTab('pdf-a'));
    if (lifecycle === 'minimized' || lifecycle === 'successful deletion') {
      await page.evaluate(() => editor.closePanel('down'));
      await page.clock.runFor(250);
      assert.equal(await page.locator('#doc-editor-pane').count(), 0);
    }
    releaseDelete();
    await page.evaluate(() => window.deleting);
    await page.clock.runFor(1200);
    if (lifecycle === 'successful deletion') {
      assert.equal(await page.evaluate(() => state.docs.has('pdf-a') || state._closingDocs.has('pdf-a')), false);
      assert.equal(requests.filter(request => request.method === 'PUT' && request.path === '/api/document/pdf-a').length, 0);
    } else {
      await page.waitForFunction(() => {
        const doc = state.docs.get('pdf-a') || state._closingDocs.get('pdf-a');
        return !doc || doc._saveState === 'saved';
      });
      const writes = requests.filter(request => request.method === 'PUT' && request.path === '/api/document/pdf-a');
      assert(writes.length > 0, 'failed deletion must issue a real recovery PUT');
      assert(writes.every(request => request.body.content.includes('Pending PDF edit')));
      assert.equal(await page.evaluate(() => state.docs.get('b').content), 'B untouched');
    }
  });
}

test('facade: all six public email methods invoke stable implementations', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.path !== '/api/document' || request.method !== 'POST') return false;
    await route.fulfill({ json: { id: 'draft', title: request.body.title, language: 'email', current_content: request.body.content } });
    return true;
  });
  await page.evaluate(async () => {
    state.setLastSessionId('session');
    for (const api of [editor, editor.default]) {
      const id = await api.openEmailDraft({ to: 'to@example.com', subject: 'Subject', sourceUid: '42', sourceFolder: 'INBOX' });
      if (id !== 'draft' || api.findEmailDocId('42', 'INBOX') !== id || api.getActiveEmailComposerContext().docId !== id) throw new Error('Facade email identity mismatch');
      api.focusEmailReplyBody();
      await api.replaceEmailReplyBody(id, 'Replacement body', { force: true });
      await api.ensureEmailDraftEnvelope(id, 'To: to@example.com\nSubject: Subject\nReferences: <ref>\nX-Source-UID: 42\n---\nReplacement body');
    }
  });
  assert.equal(requests.filter(request => request.method === 'POST' && request.path === '/api/document').length, 2);
  assert.match(await page.evaluate(() => state.docs.get('draft').content), /Replacement body/);
});

test('language: selector transitions persist metadata and delayed responses preserve identity', async t => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method === 'PATCH' && request.body?.language === 'python') {
      await gate;
      await route.fulfill({ json: {} });
      return true;
    }
    return false;
  });
  await inject(page, 'b', 'B original', 'markdown');
  await inject(page, 'a', '# Heading', 'markdown');
  await page.locator('#doc-language-select').selectOption('python');
  await page.evaluate(() => editor.loadDocument('b'));
  release();
  await page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().endsWith('/api/document/a'));
  assert.equal(await page.evaluate(() => state.docs.get('a').language), 'python');
  assert.equal(await page.evaluate(() => state.docs.get('b').language), 'markdown');
  for (const language of ['richtext', 'markdown', 'html', 'csv', 'email', 'markdown']) {
    await page.locator('#doc-language-select').selectOption(language);
    await page.waitForFunction(language => state.docs.get('b').language === language, language);
  }
  assert(requests.filter(request => request.method === 'PATCH' && request.body?.language).length >= 7);
});

test('compare: actual header action displays both versions in diff mode', async t => {
  const { page } = await setup(t, async (route, request) => {
    if (!request.path.endsWith('/versions')) return false;
    await route.fulfill({ json: [{ version_number: 2, content: 'new content' }, { version_number: 1, content: 'old content' }] });
    return true;
  });
  await inject(page, 'a', 'new content');
  await page.locator('#doc-diff-toggle-btn').click();
  await page.waitForFunction(async () => (await import('/static/js/document/ui/diffViewer.js'))._diffModeActive);
  const content = await page.locator('#doc-editor-code').textContent();
  assert.match(content, /old content/);
  assert.match(content, /new content/);
});

test('panel: default divider collapse closes and can reopen repeatedly', async t => {
  const { page } = await setup(t);
  await inject(page, 'a', 'body');
  for (let count = 0; count < 3; count++) {
    await page.locator('.doc-divider-collapse').evaluate(button => { delete button.dataset.mode; button.click(); });
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), false);
    await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
    await page.evaluate(() => editor.loadDocument('a'));
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), true);
  }
});

test('autodetect: shipped highlighting library persists SVG metadata', async t => {
  const { page, requests } = await setup(t);
  await page.addScriptTag({ url: `${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/lib/highlight.min.js` });
  await inject(page, 'detect', '', '');
  await page.evaluate(async () => {
    const source = await import('/static/js/document/ui/editor/sourceEditor.js');
    document.getElementById('doc-editor-textarea').value = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="20" height="20"/></svg>';
    source.attemptAutoDetect();
  });
  await page.waitForFunction(() => state.docs.get('detect').language === 'svg');
  assert(requests.some(request => request.path === '/api/document/detect' && request.body?.language === 'svg'));
});

test('mobile: grabber click and synthetic touch dismissal preserve dock and reopen', async t => {
  const { page } = await setup(t, null, { hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  await inject(page, 'a', 'Mobile body');
  for (const action of ['click', 'swipe', 'click', 'swipe']) {
    if (action === 'click') await page.locator('#doc-mobile-grabber').tap();
    else await page.evaluate(() => {
      const el = document.getElementById('doc-mobile-grabber');
      const touch = y => new Touch({ identifier: 1, target: el, clientX: 100, clientY: y });
      el.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [touch(30)] }));
      el.dispatchEvent(new TouchEvent('touchmove', { bubbles: true, cancelable: true, touches: [touch(240)] }));
      el.dispatchEvent(new TouchEvent('touchend', { bubbles: true, touches: [], changedTouches: [touch(240)] }));
    });
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), false);
    assert.equal(await page.evaluate(async () => (await import('/static/js/modalManager.js')).isMinimized('doc-panel')), true);
    await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
    await page.evaluate(() => editor.loadDocument('a'));
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), true);
  }
});

test('selection compatibility: writes delegate to selection owner', async t => {
  const { page } = await setup(t);
  await inject(page, 'a', 'body');
  assert.equal(await page.evaluate(async () => {
    const selections = await import('/static/js/document/ui/selection/selectionHighlights.js');
    const replacement = [];
    globalThis._selections = replacement;
    editor.init('');
    return selections._selections === replacement && globalThis._selections === replacement;
  }), true);
});

test('images: real rich image selection owns toolbar positioning and disposal', async t => {
  const { page } = await setup(t);
  await inject(page, 'a', '<p>Image</p><img class="richtext-image" src="/static/favicon.svg" alt="Example">', 'richtext');
  await page.locator('#doc-email-richbody img').click();
  await page.waitForFunction(() => !!document.querySelector('.doc-rich-image-toolbar')?.style.top);
  assert.equal(await page.locator('#doc-email-richbody img').getAttribute('data-editor-image-selected'), 'true');
  await page.evaluate(() => editor.closePanel('down'));
  await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
  assert.equal(await page.locator('.doc-rich-image-toolbar').count(), 0);
});

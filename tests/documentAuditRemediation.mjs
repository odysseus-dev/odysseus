import assert from 'node:assert/strict';
import os from 'node:os';
import test, { after, describe } from 'node:test';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const styles = (await fs.readFile('static/index.html', 'utf8')).match(/<link[^>]+rel="stylesheet"[^>]*>/g).join('');
const defaultConcurrency = Math.min(4, Math.max(1, os.availableParallelism()));
const parsedConcurrency = Number.parseInt(process.env.ODYSSEUS_DOC_AUDIT_CONCURRENCY || '', 10);
const testConcurrency = Number.isInteger(parsedConcurrency) && parsedConcurrency > 0
  ? parsedConcurrency
  : defaultConcurrency;

let sharedBrowserPromise = null;

async function getSharedBrowser() {
  if (!sharedBrowserPromise) {
    sharedBrowserPromise = chromium.launch({ headless: true });
  }
  const pending = sharedBrowserPromise;
  const browser = await pending;
  if (!browser.isConnected()) {
    if (sharedBrowserPromise === pending) {
      sharedBrowserPromise = chromium.launch({ headless: true });
    }
    return sharedBrowserPromise;
  }
  return browser;
}

after(async () => {
  if (sharedBrowserPromise) {
    const pending = sharedBrowserPromise;
    sharedBrowserPromise = null;
    const browser = await pending;
    await browser.close();
  }
});

async function setup(t, routeHandler, options = {}) {
  const browser = await getSharedBrowser();
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 }, ...options });
  t.after(() => context.close());
  const page = await context.newPage();
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

describe('document audit remediation', { concurrency: testConcurrency }, () => {
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
    assert.equal(await page.evaluate(() => state.docs.get('a')._saveState), 'dirty');
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
  await page.clock.install();
  await inject(page, 'email', original, 'email');
  for (const [field, value] of [['to', 'new@example.com'], ['cc', 'cc@example.com'], ['bcc', 'bcc@example.com'], ['subject', 'New subject']]) {
    await page.locator(`#doc-email-${field}`).fill(value);
  }
  assert.equal(await page.evaluate(() => state.docs.get('email')._saveState), 'dirty');
  await page.clock.fastForward(1000);
  await page.waitForFunction(() => state.docs.get('email')._saveState === 'saved');
  const first = requests.filter(request => request.method === 'PUT').at(-1);
  assert.match(first.body.content, /To: new@example.com/);
  assert.match(first.body.content, /Cc: cc@example.com/);
  assert.match(first.body.content, /Bcc: bcc@example.com/);
  assert.match(first.body.content, /Subject: New subject/);
  await page.locator('[data-cc-close]').first().evaluate(button => button.click());
  await page.clock.fastForward(1000);
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
  await page.clock.install();
  await inject(page, 'a', 'initial', 'markdown', '');
  await inject(page, 'b', 'B correct body', 'markdown', '');
  await page.evaluate(() => editor.loadDocument('a'));
  await edit(page, 'Title belonging to A');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.clock.fastForward(700);
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
  await page.clock.fastForward(900);
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert(!requests.some(request => request.method === 'PATCH' && request.body?.title));
  assert.equal(await page.evaluate(async () => {
    const source = await import('/static/js/document/ui/editor/sourceEditor.js');
    return source._autoTitleDebounce;
  }), null);
  await edit(page, 'Valid title after reopen');
  await page.clock.fastForward(900);
  await page.waitForFunction(() => state.docs.get('b').title === 'Valid title after reopen');
});

for (const lifecycle of ['mounted', 'minimized', 'navigation', 'detached', 'session', 'successful deletion']) {
  test(`pdf rollback: ${lifecycle} retains edits with document-level persistence`, async t => {
    let releaseDelete;
    let deleteStarted;
    const gate = new Promise(resolve => { releaseDelete = resolve; });
    const started = new Promise(resolve => { deleteStarted = resolve; });
    const { page, requests } = await setup(t, async (route, request) => {
      if (request.path === '/api/documents/new') { await route.fulfill({ json: [] }); return true; }
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
    if (lifecycle === 'session') await page.evaluate(() => {
      state.docs.get('pdf-a').sessionId = 'old';
      return editor.loadSessionDocs('new');
    });
    if (lifecycle === 'minimized' || lifecycle === 'successful deletion') {
      await page.evaluate(() => editor.closePanel('down'));
      await page.clock.fastForward(250);
      assert.equal(await page.locator('#doc-editor-pane').count(), 0);
    }
    releaseDelete();
    await page.evaluate(() => window.deleting);
    await page.clock.fastForward(1200);
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
  for (const language of ['richtext', 'markdown', 'docx', 'markdown', 'html', 'csv', 'email', 'markdown']) {
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
  await page.locator('#doc-diff-toolbar').waitFor({ state: 'visible' });
  const content = await page.locator('#doc-editor-code').textContent();
  assert.match(content, /old content/);
  assert.match(content, /new content/);
});

test('panel: default divider collapse closes and can reopen repeatedly', async t => {
  const { page } = await setup(t);
  await page.clock.install();
  await inject(page, 'a', 'body');
  for (let count = 0; count < 3; count++) {
    await page.locator('.doc-divider-collapse').evaluate(button => { delete button.dataset.mode; button.click(); });
    assert.equal(await page.evaluate(() => editor.isPanelOpen()), false);
    await page.clock.fastForward(250);
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
  await page.clock.install();
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
    await page.clock.fastForward(250);
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

test('AI ownership: new, streamed and finalized panes preserve subsequent manual drafts', async t => {
  const { page, requests } = await setup(t);
  await page.evaluate(() => editor.handleDocUpdate({ doc_id: 'ai', title: 'AI document', language: 'markdown', content: 'AI created body', version: 1 }));
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'AI created body');
  await edit(page, 'Manual draft after AI creation');
  await page.evaluate(() => editor.saveDocument({ docId: 'ai', silent: true }));
  assert.equal(requests.filter(request => request.path === '/api/document/ai' && request.method === 'PUT').at(-1).body.content, 'Manual draft after AI creation');
  await page.evaluate(() => {
    editor.streamDocOpen('Stream document', 'markdown');
    editor.streamDocDelta('Stream body');
    editor.handleDocUpdate({ doc_id: 'final', title: 'Stream document', language: 'markdown', content: 'Stream body', version: 1 });
  });
  assert.equal(await page.locator('#doc-editor-pane').getAttribute('data-active-document-id'), 'final');
  await edit(page, 'Manual draft after finalization');
  await page.evaluate(() => editor.saveDocument({ docId: 'final', silent: true }));
  assert.equal(requests.filter(request => request.path === '/api/document/final' && request.method === 'PUT').at(-1).body.content, 'Manual draft after finalization');
});

test('attachments: forwarded staging completion persists its initiating email', async t => {
  let release;
  let started;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { started = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (!request.path.includes('/compose-from-attachment/')) return false;
    started(); await gate;
    await route.fulfill({ json: { success: true, token: 'attachment-a', filename: 'file.txt', size: 4 } });
    return true;
  });
  await page.clock.install();
  await inject(page, 'b', 'To: b@example.com\nSubject: B\n---\nB body', 'email');
  await inject(page, 'a', 'To: a@example.com\nSubject: A\n---\nA body', 'email');
  await page.evaluate(async () => {
    const attachments = await import('/static/js/document/ui/email/attachments.js');
    window.staging = attachments._stageForwardedSourceAttachments({ forwardAttachments: true, sourceUid: '42', sourceFolder: 'INBOX', attachments: [{ index: 0, filename: 'file.txt' }] });
  });
  await pending;
  await page.evaluate(() => editor.loadDocument('b'));
  release(); await page.evaluate(() => window.staging);
  await page.clock.fastForward(1000);
  await page.waitForFunction(() => state.docs.get('a')._saveState === 'saved');
  assert.deepEqual(await page.evaluate(() => ({ a: state.docs.get('a')._composeAtts.map(a => a.token), b: state.docs.get('b')._composeAtts || [] })), { a: ['attachment-a'], b: [] });
  assert(requests.some(request => request.method === 'PUT' && request.path === '/api/document/a'));
  assert(!requests.some(request => request.method === 'PUT' && request.path === '/api/document/b'));
});

test('email: mark-unread completion cannot discard a newly active document', async t => {
  let release;
  let started;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { started = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (!request.path.includes('/mark-unread/')) return false;
    started(); await gate; await route.fulfill({ json: {} }); return true;
  });
  await inject(page, 'b', 'B body');
  await inject(page, 'a', 'To: a@example.com\nSubject: A\nX-Source-UID: 42\n---\nA body', 'email');
  await page.evaluate(async () => {
    const workflow = await import('/static/js/document/ui/email/sendWorkflow.js');
    window.markingUnread = workflow._markUnreadAndClose();
  });
  await pending;
  await page.evaluate(() => editor.loadDocument('b'));
  release(); await page.evaluate(() => window.markingUnread);
  assert.equal(await page.evaluate(() => state.docs.has('b') && state.activeDocId === 'b'), true);
  assert(!requests.some(request => request.method === 'DELETE' && request.path === '/api/document/b'));
});

test('format: form-backed PDF selector preserves underlying language metadata', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (!request.path.endsWith('/render-pages')) return false;
    await route.fulfill({ json: { pages: [{ page: 1, width: 600, height: 800, fields: [] }] } }); return true;
  });
  await inject(page, 'form', '<!-- pdf_form_source upload_id="source" -->\n- Name:** Ada <!-- field=Name type=text -->\n');
  await page.locator('#doc-language-select').selectOption('pdf');
  await page.waitForFunction(() => document.getElementById('doc-pdf-view')?.dataset.renderedDocId === 'form');
  await page.locator('#doc-language-select').selectOption('markdown');
  assert.equal(await page.evaluate(() => state.docs.get('form').language), 'markdown');
  assert(!requests.some(request => request.method === 'PATCH' && request.body?.language === 'pdf'));
});

test('email: an older save response cannot acknowledge a later envelope revision', async t => {
  let release;
  let started;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { started = resolve; });
  let first = true;
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method !== 'PUT' || !first) return false;
    first = false; started(); await gate;
    await route.fulfill({ json: { version_count: 2 } }); return true;
  });
  await page.clock.install();
  await inject(page, 'email', 'To: old@example.com\nSubject: Old subject\n---\nBody', 'email');
  await page.locator('#doc-email-to').fill('new@example.com');
  await page.clock.fastForward(850);
  await pending;
  await page.locator('#doc-email-subject').fill('Later subject');
  release();
  await page.evaluate(() => state.docs.get('email')._saveQueueTail);
  assert.equal(await page.evaluate(() => state.docs.get('email')._saveState), 'dirty');
  await page.clock.fastForward(850);
  await page.waitForFunction(() => state.docs.get('email')._saveState === 'saved');
  const puts = requests.filter(request => request.method === 'PUT');
  assert.equal(puts.length, 2);
  assert.match(puts[0].body.content, /Subject: Old subject/);
  assert.match(puts[1].body.content, /Subject: Later subject/);
});

test('images: upload completion cannot insert into another document', async t => {
  let release;
  let started;
  const gate = new Promise(resolve => { release = resolve; });
  const pending = new Promise(resolve => { started = resolve; });
  const { page } = await setup(t, async (route, request) => {
    if (request.path !== '/api/upload') return false;
    started(); await gate;
    await route.fulfill({ json: { files: [{ id: 'uploaded', name: 'a.png' }] } }); return true;
  });
  await inject(page, 'b', 'B original');
  await inject(page, 'a', 'A original');
  await page.evaluate(async () => {
    const images = await import('/static/js/document/ui/editor/markdownImages.js');
    window.uploading = images._uploadMarkdownImages([new File(['image'], 'a.png', { type: 'image/png' })]);
  });
  await pending;
  await page.evaluate(() => editor.loadDocument('b'));
  release(); await page.evaluate(() => window.uploading);
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'B original');
  assert.equal(await page.evaluate(() => state.docs.get('a').content), 'A original');
  assert.equal(await page.evaluate(() => state.docs.get('b').content), 'B original');
});

test('lifecycle: rich text, email and PDF remounts release global listeners', async t => {
  const { page } = await setup(t, async (route, request) => {
    if (!request.path.endsWith('/render-pages')) return false;
    await route.fulfill({ json: { pages: [{ page: 1, width: 600, height: 800, fields: [] }] } }); return true;
  });
  await page.clock.install();
  await inject(page, 'rich', '<p>Rich body</p>', 'richtext');
  await inject(page, 'email', 'To: to@example.com\nSubject: Email\n---\nBody', 'email');
  await inject(page, 'pdf', '<!-- pdf_form_source upload_id="source" -->\n- Name:** Ada <!-- field=Name type=text -->\n');
  const cycle = async () => {
    for (const id of ['rich', 'email', 'pdf']) {
      await page.evaluate(async id => {
        editor.init('');
        await editor.loadDocument(id);
        if (id === 'pdf') await (await import('/static/js/document/ui/pdfTools.js'))._setPdfViewActive(true);
        editor.closePanel('down');
      }, id);
      await page.clock.fastForward(250);
      assert.equal(await page.locator('#doc-editor-pane').count(), 0);
    }
    // spinner.js permits a 2000 ms grace for loading rows removed before
    // their first connected frame. Drain that bounded owner cleanup with the
    // controlled clock before comparing quiescent listener counts.
    await page.clock.fastForward(2100);
  };
  const cdp = await page.context().newCDPSession(page);
  const counts = async () => (await cdp.send('Runtime.evaluate', {
    expression: 'Object.fromEntries([document, window, window.visualViewport].filter(Boolean).map((target,index) => [index,Object.fromEntries(Object.entries(getEventListeners(target)).map(([type,list]) => [type,list.length]))]))',
    includeCommandLineAPI: true, returnByValue: true,
  })).result.value;
  await cycle();
  const before = await counts();
  await cycle();
  await cycle();
  assert.deepEqual(await counts(), before, 'repeated initialization and pane disposal preserve listener counts');
});

test('email: rapid drafts retain independent local snapshots', async t => {
  const { page } = await setup(t);
  await page.clock.install();
  await inject(page, 'b', 'To: b@example.com\nSubject: B\nX-Source-UID: 43\n---\nB body', 'email');
  await inject(page, 'a', 'To: a@example.com\nSubject: A\nX-Source-UID: 42\n---\nA body', 'email');
  await page.locator('#doc-email-subject').fill('A latest subject');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.locator('#doc-email-subject').fill('B latest subject');
  await page.clock.fastForward(1000);
  const subjects = await page.evaluate(() => ['42', '43'].map(uid => JSON.parse(localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent(`INBOX|${uid}|`)))?.subject));
  assert.deepEqual(subjects, ['A latest subject', 'B latest subject']);
});

test('email: explicit draft clearing cancels its captured storage write', async t => {
  const { page } = await setup(t);
  await page.clock.install();
  await inject(page, 'a', 'To: a@example.com\nSubject: A\nX-Source-UID: 42\n---\nA body', 'email');
  await page.locator('#doc-email-subject').fill('Pending subject');
  await page.evaluate(async () => {
    const drafts = await import('/static/js/document/ui/email/localDraft.js');
    const persistence = await import('/static/js/document/application/persistence.js');
    drafts._clearEmailLocalDraft('42', 'INBOX', '');
    state.markDeleted('a'); persistence._cancelAutoSave('a');
    state.removeDocument('a'); state.setActiveDocId(null);
  });
  await page.clock.fastForward(1000);
  assert.equal(await page.evaluate(() => localStorage.getItem('odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|42|'))), null);
});

test('email: delayed initial focus cannot redirect recipient typing into the body', async t => {
  const { page } = await setup(t);
  await page.clock.install();
  await inject(page, 'email', 'To: old@example.com\nSubject: Subject\n---\nBody', 'email');
  await page.locator('#doc-email-to').focus();
  await page.clock.fastForward(60);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'doc-email-to');
  await page.keyboard.type('recipient@example.com');
  assert.equal(await page.locator('#doc-email-richbody').textContent(), 'Body');
});

test('compare: deferred accepted-change preview cannot change another document', async t => {
  const { page } = await setup(t);
  await inject(page, 'b', 'print("B original")', 'python');
  await inject(page, 'a', 'old Markdown');
  await page.clock.install();
  await page.clock.pauseAt(new Date());
  await page.evaluate(async () => {
    editor.default.enterDiffMode('old Markdown', 'new Markdown');
    document.getElementById('doc-diff-accept-all-btn').click();
    await editor.loadDocument('b');
  });
  await page.clock.fastForward(40);
  assert.equal(await page.evaluate(() => state.docs.get('b')._markdownPreviewActive === true), false);
  assert.equal(await page.locator('#doc-editor-textarea').inputValue(), 'print("B original")');
  assert.equal(await page.locator('#doc-editor-wrap').isVisible(), true);
  await page.evaluate(async () => {
    await editor.loadDocument('a');
    editor.default.enterDiffMode('new Markdown', 'accepted Markdown');
    document.getElementById('doc-diff-accept-all-btn').click();
  });
  await page.clock.fastForward(40);
  assert.equal(await page.evaluate(() => state.docs.get('a')._markdownPreviewActive), true);
  assert.equal(await page.evaluate(() => state.docs.get('b')._markdownPreviewActive === true), false);
});

test('compare: stale versions response after navigation does not activate diff or overwrite target across exit paths', async t => {
  for (const exitPath of ['accept-all', 'reject-all', 'toggle-off', 'navigate-c', 'minimize', 'escape']) {
    await t.test(exitPath, async t => {
      let releaseVersions;
      let markVersionsStarted;
      const heldVersions = new Promise(resolve => { releaseVersions = resolve; });
      const versionsStarted = new Promise(resolve => { markVersionsStarted = resolve; });
      const { page, requests } = await setup(t, async (route, request) => {
        if (request.method === 'GET' && request.path === '/api/document/a/versions') {
          markVersionsStarted();
          await heldVersions;
          await route.fulfill({ json: [{ content: 'A latest' }, { content: 'A previous' }] });
          return true;
        }
        if (request.method === 'GET' && request.path === '/api/document/b/versions') {
          await route.fulfill({ json: [{ content: 'B own content' }] });
          return true;
        }
        return false;
      });
      await inject(page, 'b', 'B own content', 'text', 'B Title');
      await inject(page, 'c', 'C own content', 'text', 'C Title');
      await inject(page, 'a', 'A latest', 'text', 'A Title');

      await page.locator('#doc-diff-toggle-btn').click();
      await versionsStarted;

      await page.evaluate(() => editor.loadDocument('b'));
      assert.equal(await page.evaluate(() => state.activeDocId), 'b');

      releaseVersions();
      await page.waitForTimeout(50);

      const diffState = await page.evaluate(async () => {
        const diff = await import('/static/js/document/ui/diffViewer.js');
        const toolbar = document.getElementById('doc-diff-toolbar');
        return {
          active: diff._diffModeActive,
          bContent: state.docs.get('b').content,
          editorValue: document.getElementById('doc-editor-textarea')?.value,
          toolbarVisible: Boolean(toolbar && getComputedStyle(toolbar).display !== 'none'),
          codeText: document.getElementById('doc-editor-code')?.textContent || '',
        };
      });
      assert.equal(diffState.active, false);
      assert.equal(diffState.bContent, 'B own content');
      assert.equal(diffState.editorValue, 'B own content');
      assert.equal(diffState.toolbarVisible, false);
      assert.equal(diffState.codeText.includes('A previous'), false);

      if (exitPath === 'accept-all') {
        await page.evaluate(() => document.getElementById('doc-diff-accept-all-btn')?.click());
      } else if (exitPath === 'reject-all') {
        await page.evaluate(() => document.getElementById('doc-diff-reject-all-btn')?.click());
      } else if (exitPath === 'toggle-off') {
        await page.locator('#doc-diff-toggle-btn').click();
      } else if (exitPath === 'navigate-c') {
        await page.evaluate(() => editor.loadDocument('c'));
      } else if (exitPath === 'minimize') {
        await page.evaluate(() => editor.closePanel('down'));
      } else if (exitPath === 'escape') {
        await page.keyboard.press('Escape');
      }
      await page.waitForTimeout(50);

      assert.equal(await page.evaluate(() => state.docs.get('b').content), 'B own content');
      const puts = requests.filter(r => r.method === 'PUT' && (r.path === '/api/document/b' || r.path === '/api/document/c'));
      assert.deepEqual(puts, []);
    });
  }
});

test('compare: rapid A -> B -> A switch invalidates stale request, while normal compare on A works', async t => {
  let releaseFirst;
  let firstStarted;
  let versionCall = 0;
  const heldFirst = new Promise(resolve => { releaseFirst = resolve; });
  const firstInFlight = new Promise(resolve => { firstStarted = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method === 'GET' && request.path === '/api/document/a/versions') {
      versionCall += 1;
      if (versionCall === 1) {
        firstStarted();
        await heldFirst;
        await route.fulfill({ json: [{ content: 'A new' }, { content: 'A stale old' }] });
        return true;
      }
      await route.fulfill({ json: [{ content: 'A new' }, { content: 'A valid old' }] });
      return true;
    }
    return false;
  });
  await inject(page, 'b', 'B original', 'text', 'B Title');
  await inject(page, 'a', 'A new', 'text', 'A Title');

  await page.locator('#doc-diff-toggle-btn').click();
  await firstInFlight;
  await page.evaluate(async () => {
    await editor.loadDocument('b');
    await editor.loadDocument('a');
  });
  releaseFirst();
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(async () => (await import('/static/js/document/ui/diffViewer.js'))._diffModeActive), false);

  await page.locator('#doc-diff-toggle-btn').click();
  await page.waitForFunction(async () => (await import('/static/js/document/ui/diffViewer.js'))._diffModeActive === true);
  const diffInfo = await page.evaluate(() => {
    const toolbar = document.getElementById('doc-diff-toolbar');
    return {
      toolbarVisible: Boolean(toolbar && getComputedStyle(toolbar).display !== 'none'),
      codeText: document.getElementById('doc-editor-code')?.textContent || '',
    };
  });
  assert.equal(diffInfo.toolbarVisible, true);
  assert.equal(diffInfo.codeText.includes('A valid old'), true);
  assert.equal(diffInfo.codeText.includes('A new'), true);

  await page.locator('#doc-diff-accept-all-btn').click();
  await page.waitForFunction(async () => (await import('/static/js/document/ui/diffViewer.js'))._diffModeActive === false);
  assert.equal(await page.evaluate(() => state.docs.get('a').content), 'A new');
  assert.equal(requests.filter(r => r.method === 'PUT' && r.path === '/api/document/b').length, 0);
});

test('compare: stale version-fetch errors do not surface after navigation while active fetch errors report properly', async t => {
  let releaseFail;
  let failStarted;
  const heldFail = new Promise(resolve => { releaseFail = resolve; });
  const failInFlight = new Promise(resolve => { failStarted = resolve; });
  let mode = 'held-fail';
  const { page } = await setup(t, async (route, request) => {
    if (request.method === 'GET' && request.path.endsWith('/versions')) {
      if (mode === 'held-fail') {
        failStarted();
        await heldFail;
        await route.fulfill({ status: 500, json: { error: 'boom' } });
        return true;
      }
      await route.fulfill({ status: 500, json: { error: 'boom' } });
      return true;
    }
    return false;
  });
  await inject(page, 'b', 'B body', 'text', 'B');
  await inject(page, 'a', 'A body', 'text', 'A');
  await page.evaluate(async () => {
    const host = await import('/static/js/document/ui/host.js');
    window.__errors = [];
    host.uiModule.showError = msg => window.__errors.push(msg);
  });

  await page.locator('#doc-diff-toggle-btn').click();
  await failInFlight;
  await page.evaluate(() => editor.loadDocument('b'));
  releaseFail();
  await page.waitForTimeout(50);
  assert.deepEqual(await page.evaluate(() => window.__errors), []);

  mode = 'active-fail';
  await page.locator('#doc-diff-toggle-btn').click();
  await page.waitForFunction(() => window.__errors.length === 1);
  assert.deepEqual(await page.evaluate(() => window.__errors), ['Failed to load version history']);
});

test('titles: rich-text A to Markdown B retains A identity and leaves B untitled', async t => {
  const { page, requests } = await setup(t);
  await page.clock.install();
  await inject(page, 'b', 'B markdown body', 'markdown', '');
  await inject(page, 'a', '', 'richtext', '');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Rich Title A');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.clock.fastForward(700);
  await page.waitForFunction(() => state.docs.get('a').title === 'Rich Title A');
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert.match(await page.evaluate(() => state.docs.get('a').content), /Rich Title A/);
  assert(requests.some(r => r.method === 'PATCH' && r.path === '/api/document/a' && r.body?.title === 'Rich Title A'));
  assert(!requests.some(r => r.method === 'PATCH' && r.path === '/api/document/b' && r.body?.title));
});

test('titles: rich-text A to rich-text B never applies A title to B', async t => {
  const { page, requests } = await setup(t);
  await page.clock.install();
  await inject(page, 'b', 'B rich initial', 'richtext', '');
  await inject(page, 'a', '', 'richtext', '');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Alpha Rich Title');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.clock.fastForward(700);
  await page.waitForFunction(() => state.docs.get('a').title === 'Alpha Rich Title');
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert(!requests.some(r => r.method === 'PATCH' && r.path === '/api/document/b' && r.body?.title));

  await page.locator('#doc-email-richbody').fill('');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Beta Own Title');
  await page.clock.fastForward(700);
  await page.waitForFunction(() => state.docs.get('b').title === 'Beta Own Title');
  assert.equal(await page.evaluate(() => state.docs.get('b').title), 'Beta Own Title');
  assert(!requests.some(r => r.method === 'PATCH' && r.path === '/api/document/b' && r.body?.title === 'Alpha Rich Title'));
});

test('titles: rich-text A minimized before B opened cancels pending title timer', async t => {
  const { page, requests } = await setup(t);
  await page.clock.install();
  await inject(page, 'b', 'B content', 'richtext', '');
  await inject(page, 'a', '', 'richtext', '');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Minimized Rich Title');
  await page.evaluate(() => editor.closePanel('down'));
  await page.clock.fastForward(900);
  assert.equal(await page.evaluate(async () => (await import('/static/js/document/ui/editor/sourceEditor.js'))._autoTitleDebounce), null);
  assert(!requests.some(r => r.method === 'PATCH' && r.body?.title));
  await page.evaluate(() => editor.loadDocument('b'));
  await page.clock.fastForward(900);
  assert.equal(await page.evaluate(async () => (await import('/static/js/document/ui/editor/sourceEditor.js'))._autoTitleDebounce), null);
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert.equal(await page.evaluate(() => state.docs.get('a').title), '');
  assert.match(await page.evaluate(() => state.docs.get('a').content), /Minimized Rich Title/);
  assert(!requests.some(r => r.method === 'PATCH' && r.body?.title));
});

test('titles: rich-text rapid switching and deletion guard stale callbacks, normal auto-title works, and remounts do not duplicate timers', async t => {
  const { page, requests } = await setup(t);
  await page.clock.install();
  await inject(page, 'b', 'B untitled', 'richtext', '');
  await inject(page, 'a', '', 'richtext', '');

  // Rapid switch A -> B -> A -> B cancels earlier A timer and applies latest A text only to A
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('First Draft');
  await page.evaluate(async () => {
    await editor.loadDocument('b');
    await editor.loadDocument('a');
  });
  await page.locator('#doc-email-richbody').fill('Final Draft A');
  await page.locator('#doc-email-richbody').dispatchEvent('input');
  await page.evaluate(() => editor.loadDocument('b'));
  await page.clock.fastForward(700);
  await page.waitForFunction(() => state.docs.get('a').title === 'Final Draft A');
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');
  assert(!requests.some(r => r.method === 'PATCH' && r.path === '/api/document/b' && r.body?.title));

  // Closing/removing A before its title timer fires cancels auto-title via record identity
  await inject(page, 'temp', '', 'richtext', '');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Temp Removed Doc');
  await page.evaluate(async () => {
    state.markDeleted('temp');
    state.removeDocument('temp');
    await editor.loadDocument('b');
  });
  await page.clock.fastForward(700);
  assert(!requests.some(r => r.method === 'PATCH' && r.path === '/api/document/temp' && r.body?.title));
  assert.equal(await page.evaluate(() => state.docs.get('b').title), '');

  // Repeated minimize/reopen cycles still allow single normal rich-text auto-title without duplicate PATCHes
  for (let i = 0; i < 3; i++) {
    await page.evaluate(async () => {
      editor.closePanel('down');
      await editor.loadDocument('b');
    });
    await page.clock.fastForward(250);
  }
  await page.locator('#doc-email-richbody').fill('');
  await page.locator('#doc-email-richbody').click();
  await page.keyboard.type('Normal Rich Title');
  await page.clock.fastForward(700);
  await page.waitForFunction(() => state.docs.get('b').title === 'Normal Rich Title');
  const bTitlePatches = requests.filter(r => r.method === 'PATCH' && r.path === '/api/document/b' && r.body?.title === 'Normal Rich Title');
  assert.equal(bTitlePatches.length, 1);
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Normal Rich Title');
});

test('titles: AI-created document without explicit title preserves derived title across tab switching', async t => {
  let releasePatch;
  const heldPatch = new Promise(resolve => { releasePatch = resolve; });
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method === 'PATCH' && request.path === '/api/document/ai1') {
      await heldPatch;
      await route.fulfill({ json: { status: 'ok', title: request.body?.title } });
      return true;
    }
    if (request.method === 'PATCH' && request.path === '/api/document/ai2') {
      await route.fulfill({ json: { status: 'ok', title: request.body?.title } });
      return true;
    }
    return false;
  });

  await inject(page, 'prev', 'Previous doc', 'markdown', 'Previous Title');
  await page.evaluate(() => {
    editor.handleDocUpdate({
      doc_id: 'ai1',
      content: '# Executive Summary\n\nBody text.',
      language: 'markdown',
      version: 1,
      title: '',
    });
  });

  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Executive Summary');
  releasePatch();
  await page.waitForFunction(() => state.docs.get('ai1')?.title === 'Executive Summary');

  const tabTitle = id => page.evaluate(docId => {
    const tab = document.querySelector(`.doc-tab[data-doc-id="${docId}"] .doc-tab-title`);
    return tab?.textContent || '';
  }, id);
  assert.equal(await tabTitle('ai1'), 'Executive Summary');

  await page.evaluate(() => editor.loadDocument('prev'));
  assert.equal(await page.evaluate(() => state.docs.get('ai1').title), 'Executive Summary');
  assert.equal(await tabTitle('ai1'), 'Executive Summary');

  await page.evaluate(() => editor.loadDocument('ai1'));
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Executive Summary');
  assert.equal(await tabTitle('ai1'), 'Executive Summary');
  assert.equal(await page.evaluate(() => state.docs.get('ai1').title), 'Executive Summary');
  assert.equal(await page.evaluate(() => state.docs.get('ai1').content), '# Executive Summary\n\nBody text.');
  assert(requests.some(r => r.method === 'PATCH' && r.path === '/api/document/ai1' && r.body?.title === 'Executive Summary'));

  await page.evaluate(() => editor.closePanel('down'));
  await page.locator('#doc-editor-pane').waitFor({ state: 'detached' });
  await page.evaluate(() => {
    editor.handleDocUpdate({
      doc_id: 'ai2',
      content: '# Closed Start Report\n\nBody',
      language: 'markdown',
      version: 1,
    });
  });
  await page.waitForFunction(() => state.docs.get('ai2')?.title === 'Closed Start Report');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Closed Start Report');
  await page.evaluate(async () => {
    await editor.loadDocument('prev');
    await editor.loadDocument('ai2');
  });
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Closed Start Report');
  assert.equal(await tabTitle('ai2'), 'Closed Start Report');
});

test('facade: importing and running document module publishes only the documented window globals and exports', async t => {
  const browser = await getSharedBrowser();
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, [], 'integrated module graph must not throw'));
  await page.route('**/api/**', route => route.fulfill({ json: { version_count: 1 } }));
  await page.goto(`${process.env.ODYSSEUS_TEST_STATIC_ORIGIN}/static/js/documentStats.js`);
  await page.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1">${styles}<div id="toast"></div><aside id="sidebar"></aside><main id="chat-container"><textarea id="message"></textarea></main>`);
  const result = await page.evaluate(async () => {
    await import('/static/js/ui.js?v=20260916largetoolscroll1');
    await import('/static/js/sessions.js');
    await import('/static/js/markdown.js');
    await import('/static/js/documentLibrary.js?v=20260911librarybulkdelete1');
    await import('/static/js/modalManager.js');
    const before = new Set(Object.getOwnPropertyNames(window));
    const mod = await import('/static/js/document.js');
    mod.init('');
    await mod.injectFreshDoc({
      id: 'pub-email',
      title: 'Email Pub Check',
      language: 'email',
      current_content: 'To: a@example.com\nSubject: Hi\n---\nBody',
      version_count: 1,
    });
    const after = Object.getOwnPropertyNames(window);
    return {
      addedKeys: after.filter(k => !before.has(k) && !/^\d+$/.test(k)).sort(),
      namedExportCount: Object.keys(mod).filter(k => k !== 'default').length,
      defaultKeyCount: Object.keys(mod.default).length,
    };
  });
  assert.equal(result.namedExportCount, 30);
  assert.equal(result.defaultKeyCount, 39);
  assert.deepEqual(result.addedKeys, [
    '_selections',
    'documentModule',
    'markdownModule',
    'odysseusEmailCaretIntent',
    'odysseusEmailSendIntent',
    'uiModule',
  ]);
});

test('NB-F1: stale automatic-title response cannot overwrite newer manual title or corrupt tabs', async t => {
  let releaseAutoPatch;
  const heldAutoPatch = new Promise(resolve => { releaseAutoPatch = resolve; });
  let autoPatchCount = 0;
  let manualPatchCount = 0;
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method === 'PATCH' && request.path === '/api/document/doc-title-a') {
      if (request.body?.title === 'Auto Title A') {
        assert.equal(request.body?.if_untitled, true);
        autoPatchCount++;
        await heldAutoPatch;
        await route.fulfill({ json: { status: 'ok', title: 'Auto Title A' } });
        return true;
      }
      if (request.body?.title === 'Manual Title A') {
        assert.equal(request.body?.if_untitled, undefined);
        manualPatchCount++;
        await route.fulfill({ json: { status: 'ok', title: 'Manual Title A' } });
        return true;
      }
      if (request.body?.title === 'Manual Second' || request.body?.title === 'Manual Final') {
        await route.fulfill({ json: { status: 'ok', title: request.body?.title } });
        return true;
      }
    }
    if (request.method === 'PATCH' && request.path === '/api/document/doc-nav-b') {
      await route.fulfill({ json: { status: 'ok', title: request.body?.title } });
      return true;
    }
    if (request.method === 'PATCH' && request.path === '/api/document/doc-fail-c') {
      await route.fulfill({ status: 500, json: { error: 'Server error' } });
      return true;
    }
    return false;
  });

  // 1. Inject doc A with Untitled title
  await inject(page, 'doc-title-a', '# Auto Title A\nSome body', 'markdown', 'Untitled');
  await page.evaluate(async () => {
    const { autoTitleFromContent } = await import('/static/js/document/ui/documentActions.js');
    autoTitleFromContent('# Auto Title A\nSome body', 'doc-title-a');
  });

  // Verify auto title PATCH is sent and in flight
  await page.waitForFunction(() => state.docs.get('doc-title-a')._titleRequestSeq === 1);
  assert.equal(autoPatchCount, 1);

  // 2. User manually edits the title while auto title is in flight
  await page.evaluate(async () => {
    const { updateTitle } = await import('/static/js/document/ui/documentActions.js');
    state.docs.get('doc-title-a').title = 'Manual Title A';
    document.getElementById('doc-title-input').value = 'Manual Title A';
    await updateTitle('doc-title-a', 'Manual Title A');
  });

  // Verify manual title update completed
  assert.equal(manualPatchCount, 1);
  assert.equal(await page.evaluate(() => state.docs.get('doc-title-a').title), 'Manual Title A');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Manual Title A');

  // 3. Older automatic-title response arrives afterward
  releaseAutoPatch();
  await page.evaluate(() => new Promise(r => setTimeout(r, 50)));

  // 4. Assert: document record, title input, tab label, and persisted title remain manual
  assert.equal(await page.evaluate(() => state.docs.get('doc-title-a').title), 'Manual Title A');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Manual Title A');
  const tabA = await page.evaluate(() => document.querySelector('.doc-tab[data-doc-id="doc-title-a"] .doc-tab-title')?.textContent || '');
  assert.equal(tabA, 'Manual Title A');

  // 5. Document navigation during request:
  await inject(page, 'doc-nav-b', '# Auto Nav B\nContent', 'markdown', 'Untitled');
  await page.evaluate(async () => {
    const { autoTitleFromContent } = await import('/static/js/document/ui/documentActions.js');
    autoTitleFromContent('# Auto Nav B\nContent', 'doc-nav-b');
    // Navigate back to doc-title-a
    await editor.loadDocument('doc-title-a');
  });
  await page.waitForFunction(() => state.docs.get('doc-nav-b')?.title === 'Auto Nav B');
  // Doc A remains unaffected
  assert.equal(await page.evaluate(() => state.docs.get('doc-title-a').title), 'Manual Title A');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Manual Title A');

  // 6. Failed automatic title PATCH:
  await inject(page, 'doc-fail-c', '# Fail Doc\nContent', 'markdown', 'Untitled');
  await page.evaluate(async () => {
    const { autoTitleFromContent } = await import('/static/js/document/ui/documentActions.js');
    autoTitleFromContent('# Fail Doc\nContent', 'doc-fail-c');
  });
  await page.evaluate(() => new Promise(r => setTimeout(r, 50)));
  // Untitled is preserved cleanly without thrown error
  assert.equal(await page.evaluate(() => state.docs.get('doc-fail-c').title), 'Untitled');

  // 7. Repeated manual title edits sequencing:
  await page.evaluate(async () => {
    await editor.loadDocument('doc-title-a');
    const { updateTitle } = await import('/static/js/document/ui/documentActions.js');
    await updateTitle('doc-title-a', 'Manual Second');
    await updateTitle('doc-title-a', 'Manual Final');
  });
  assert.equal(await page.evaluate(() => state.docs.get('doc-title-a').title), 'Manual Final');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Manual Final');
});

test('NB-F2: email draft response targets only originating document across navigation, deletion and repeated saves', async t => {
  let releaseDraftA;
  const heldDraftA = new Promise(resolve => { releaseDraftA = resolve; });
  let draftRequests = [];
  const { page } = await setup(t, async (route, request) => {
    if (request.method === 'POST' && request.path === '/api/email/draft') {
      draftRequests.push(request.body);
      if (request.body?.subject === 'Email A Subject') {
        await heldDraftA;
        await route.fulfill({ json: { success: true, draft_uid: 'uid-draft-a-99', draft_folder: 'Drafts' } });
        return true;
      }
      if (request.body?.subject === 'Email Del Subject') {
        await new Promise(r => setTimeout(r, 80));
        await route.fulfill({ json: { success: true, draft_uid: 'uid-draft-del-1', draft_folder: 'Drafts' } });
        return true;
      }
      if (request.body?.subject === 'Email Normal Subject') {
        await route.fulfill({ json: { success: true, draft_uid: 'uid-normal-101', draft_folder: 'Drafts' } });
        return true;
      }
      if (request.body?.subject === 'Email Fail Subject') {
        await route.fulfill({ json: { success: false, error: 'Mailbox full' } });
        return true;
      }
      await route.fulfill({ json: { success: true, draft_uid: 'uid-gen-1', draft_folder: 'Drafts' } });
      return true;
    }
    return false;
  });

  const emailAContent = 'To: recip@example.com\nSubject: Email A Subject\n---\nHello from Email A';

  await inject(page, 'email-a', emailAContent, 'email', 'Email A Subject');
  await inject(page, 'doc-b', '# Doc B\nText', 'markdown', 'Doc B');

  // Load email A and start saving draft
  await page.evaluate(() => editor.loadDocument('email-a'));
  await page.evaluate(async () => {
    const { _saveDraft } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _saveDraft();
  });

  // Verify draft request was dispatched
  await page.waitForFunction(() => Boolean(document.getElementById('doc-email-draft-btn')?.disabled));

  // Navigate away to doc-b while email A draft save is pending
  await page.evaluate(() => editor.loadDocument('doc-b'));
  assert.equal(await page.evaluate(() => state.activeDocId), 'doc-b');

  // Release email A draft response
  releaseDraftA();
  await page.evaluate(() => new Promise(r => setTimeout(r, 80)));

  // Assert: Doc B received NO draft UID or folder metadata!
  const docBMeta = await page.evaluate(() => ({
    uid: state.docs.get('doc-b')._emailDraftUid,
    folder: state.docs.get('doc-b')._emailDraftFolder,
  }));
  assert.equal(docBMeta.uid, undefined);
  assert.equal(docBMeta.folder, undefined);

  // Assert: Email A received the correct draft UID and folder metadata
  const docAMeta = await page.evaluate(() => ({
    uid: state.docs.get('email-a')._emailDraftUid,
    folder: state.docs.get('email-a')._emailDraftFolder,
  }));
  assert.equal(docAMeta.uid, 'uid-draft-a-99');
  assert.equal(docAMeta.folder, 'Drafts');

  // Originating document deletion while draft save is pending:
  await inject(page, 'email-del', 'To: x@example.com\nSubject: Email Del Subject\n---\nDel test', 'email', 'Del');
  await page.evaluate(() => editor.loadDocument('email-del'));
  await page.evaluate(async () => {
    const { _saveDraft } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _saveDraft();
    state.markDeleted('email-del');
    state.removeDocument('email-del');
    await editor.loadDocument('doc-b');
  });
  await page.evaluate(() => new Promise(r => setTimeout(r, 120)));
  // Doc B remains completely untouched
  assert.equal(await page.evaluate(() => state.docs.get('doc-b')._emailDraftUid), undefined);

  // Normal save without navigation:
  await inject(page, 'email-norm', 'To: n@example.com\nSubject: Email Normal Subject\n---\nNormal', 'email', 'Norm');
  await page.evaluate(() => editor.loadDocument('email-norm'));
  await page.evaluate(async () => {
    const { _saveDraft } = await import('/static/js/document/ui/email/sendWorkflow.js');
    await _saveDraft();
  });
  assert.equal(await page.evaluate(() => state.docs.get('email-norm')._emailDraftUid), 'uid-normal-101');
  assert.equal(await page.evaluate(() => state.docs.get('email-norm')._emailDraftFolder), 'Drafts');

  // Failed draft save does not corrupt metadata:
  await inject(page, 'email-fail', 'To: f@example.com\nSubject: Email Fail Subject\n---\nFail', 'email', 'Fail');
  await page.evaluate(() => editor.loadDocument('email-fail'));
  await page.evaluate(async () => {
    const { _saveDraft } = await import('/static/js/document/ui/email/sendWorkflow.js');
    await _saveDraft();
  });
  assert.equal(await page.evaluate(() => state.docs.get('email-fail')._emailDraftUid), undefined);
});

test('NB-F3: scheduled email completion closes only originating email across navigation, deletion and failure', async t => {
  let releaseScheduleA;
  const heldScheduleA = new Promise(resolve => { releaseScheduleA = resolve; });
  let scheduleRequests = [];
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method === 'POST' && request.path === '/api/email/schedule') {
      scheduleRequests.push(request.body);
      if (request.body?.subject === 'Sched A') {
        await heldScheduleA;
        await route.fulfill({ json: { success: true } });
        return true;
      }
      if (request.body?.subject === 'Sched Normal') {
        await route.fulfill({ json: { success: true } });
        return true;
      }
      if (request.body?.subject === 'Sched Reply') {
        await route.fulfill({ json: { success: true } });
        return true;
      }
      if (request.body?.subject === 'Sched Fail') {
        await route.fulfill({ json: { success: false, error: 'Schedule rejected' } });
        return true;
      }
      await route.fulfill({ json: { success: true } });
      return true;
    }
    if (request.method === 'DELETE' && request.path.startsWith('/api/document/')) {
      await route.fulfill({ json: { success: true } });
      return true;
    }
    return false;
  });

  const emailSchedAContent = 'To: test@example.com\nSubject: Sched A\n---\nSched A body';
  await inject(page, 'email-sched-a', emailSchedAContent, 'email', 'Sched A');
  await inject(page, 'email-reply-b', 'To: recip-b@example.com\nSubject: Reply B\n---\nReply B body', 'email', 'Doc Reply B');

  // Seed localStorage for reply draft B
  const draftBKey = 'odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|uid-reply-b-999|<msg-b-orig@example.com>');
  const draftBData = {
    to: 'recip-b@example.com',
    subject: 'Reply B',
    sourceUid: 'uid-reply-b-999',
    sourceFolder: 'INBOX',
    inReplyTo: '<msg-b-orig@example.com>',
    body: 'Pre-existing draft body for reply B',
    updatedAt: Date.now(),
  };
  await page.evaluate(({ key, payload }) => {
    localStorage.setItem(key, JSON.stringify(payload));
  }, { key: draftBKey, payload: draftBData });

  // 1. Open schedule modal for fresh composition email A (no sourceUid)
  await page.evaluate(() => editor.loadDocument('email-sched-a'));
  await page.evaluate(async () => {
    const { _scheduleSend } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _scheduleSend();
  });

  // Verify modal is displayed
  await page.locator('.schedule-send-modal').waitFor({ state: 'visible' });

  // 2. Click confirm schedule, initiating the held fetch
  await page.locator('#sched-confirm').click();
  await page.waitForFunction(() => Boolean(document.querySelector('.schedule-send-modal')));

  // Navigate to document B while scheduling is in flight
  // Populate email-reply-b inputs in the DOM (including sourceUid)
  await page.evaluate(() => {
    editor.loadDocument('email-reply-b');
    const uidInput = document.getElementById('doc-email-source-uid');
    if (uidInput) uidInput.value = 'uid-reply-b-999';
    const folderInput = document.getElementById('doc-email-source-folder');
    if (folderInput) folderInput.value = 'INBOX';
    const replyInput = document.getElementById('doc-email-in-reply-to');
    if (replyInput) replyInput.value = '<msg-b-orig@example.com>';
  });
  assert.equal(await page.evaluate(() => state.activeDocId), 'email-reply-b');
  assert.equal(await page.evaluate(() => state.isOpen), true);

  // 3. Schedule request for A succeeds
  releaseScheduleA();
  await page.evaluate(() => new Promise(r => setTimeout(r, 80)));

  // 4. Verify document B remains open and active!
  assert.equal(await page.evaluate(() => state.isOpen), true);
  assert.equal(await page.evaluate(() => state.activeDocId), 'email-reply-b');
  assert.equal(await page.locator('#doc-editor-pane').isVisible(), true);
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Doc Reply B');

  // CRITICAL NB-F3-Edge ASSERTION:
  // Document B's reply draft in localStorage MUST REMAIN INTACT!
  const bDraftInStorage = await page.evaluate(k => localStorage.getItem(k), draftBKey);
  assert.notEqual(bDraftInStorage, null, 'Document B local reply draft must not be cleared when fresh compose A is scheduled');
  const parsedBDraft = JSON.parse(bDraftInStorage);
  assert.equal(parsedBDraft.body, 'Pre-existing draft body for reply B');

  // 5. Verify email A is closed/removed from tabs and documents map
  assert.equal(await page.evaluate(() => state.docs.has('email-sched-a')), false);
  const tabA = await page.evaluate(() => document.querySelector('.doc-tab[data-doc-id="email-sched-a"]'));
  assert.equal(tabA, null);
  const tabB = await page.evaluate(() => document.querySelector('.doc-tab[data-doc-id="email-reply-b"]'));
  assert.notEqual(tabB, null);

  // Verify DELETE request was sent for email-sched-a, NOT for email-reply-b
  const deletes = requests.filter(r => r.method === 'DELETE');
  assert(deletes.some(r => r.path === '/api/document/email-sched-a'));
  assert(!deletes.some(r => r.path === '/api/document/email-reply-b'));

  // 6. Schedule an actual reply (where sourceUid is set):
  // Should clear its OWN local draft on successful schedule!
  const draftReplyKey = 'odysseus.email.replyDraft.v1:' + encodeURIComponent('INBOX|uid-r-111|<msg-r@example.com>');
  await page.evaluate(k => {
    localStorage.setItem(k, JSON.stringify({ body: 'Reply draft to clear' }));
  }, draftReplyKey);

  await inject(page, 'email-sched-reply', 'To: r@example.com\nSubject: Sched Reply\n---\nReply body', 'email', 'Sched Reply');
  await page.evaluate(() => {
    editor.loadDocument('email-sched-reply');
    const uidInput = document.getElementById('doc-email-source-uid');
    if (uidInput) uidInput.value = 'uid-r-111';
    const folderInput = document.getElementById('doc-email-source-folder');
    if (folderInput) folderInput.value = 'INBOX';
    const replyInput = document.getElementById('doc-email-in-reply-to');
    if (replyInput) replyInput.value = '<msg-r@example.com>';
  });
  await page.evaluate(async () => {
    const { _scheduleSend } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _scheduleSend();
  });
  await page.locator('.schedule-send-modal').waitFor({ state: 'visible' });
  await page.locator('#sched-confirm').click();
  await page.evaluate(() => new Promise(r => setTimeout(r, 80)));
  // Cleanly closed
  assert.equal(await page.evaluate(() => state.docs.has('email-sched-reply')), false);
  // And its own draft was cleared from localStorage
  const replyDraftInStorage = await page.evaluate(k => localStorage.getItem(k), draftReplyKey);
  assert.equal(replyDraftInStorage, null, 'Originating reply local draft should be cleared upon successful scheduling');

  // 7. Schedule without navigation (normal path):
  await inject(page, 'email-sched-norm', 'To: norm@example.com\nSubject: Sched Normal\n---\nNorm body', 'email', 'Sched Normal');
  await page.evaluate(() => editor.loadDocument('email-sched-norm'));
  await page.evaluate(async () => {
    const { _scheduleSend } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _scheduleSend();
  });
  await page.locator('.schedule-send-modal').waitFor({ state: 'visible' });
  await page.locator('#sched-confirm').click();
  await page.evaluate(() => new Promise(r => setTimeout(r, 80)));
  // Cleanly closed and transitioned to email-reply-b
  assert.equal(await page.evaluate(() => state.docs.has('email-sched-norm')), false);
  assert.equal(await page.evaluate(() => state.activeDocId), 'email-reply-b');

  // 8. Failed schedule request:
  await inject(page, 'email-sched-fail', 'To: fail@example.com\nSubject: Sched Fail\n---\nFail body', 'email', 'Sched Fail');
  await page.evaluate(() => editor.loadDocument('email-sched-fail'));
  await page.evaluate(async () => {
    const { _scheduleSend } = await import('/static/js/document/ui/email/sendWorkflow.js');
    _scheduleSend();
  });
  await page.locator('.schedule-send-modal').waitFor({ state: 'visible' });
  await page.locator('#sched-confirm').click();
  await page.evaluate(() => new Promise(r => setTimeout(r, 80)));
  // Document remains open and intact on failure
  assert.equal(await page.evaluate(() => state.docs.has('email-sched-fail')), true);
  assert.equal(await page.evaluate(() => state.activeDocId), 'email-sched-fail');
});

test('pdf lifecycle: reopen preserves proximity controls, clears mirror nodes, and isolates docs', async t => {
  const { page } = await setup(t, async (route, request) => {
    if (!request.path.endsWith('/render-pages')) return false;
    await route.fulfill({
      json: {
        pages: [{
          page: 1,
          width: 600,
          height: 800,
          image: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mNk+M/wHwAF/gL+AvzZ8QAAAABJRU5ErkJggg==',
          fields: [],
        }],
      },
    });
    return true;
  });

  const pdfA = '<!-- pdf_source upload_id="doc-a" -->\n\n## Annotations\n\n- Alpha <!-- annotation id=ann-a page=1 x=20.00 y=20.00 w=25.00 h=6.00 kind=text lh=1.50 fs=14.0 -->\n';
  const pdfB = '<!-- pdf_source upload_id="doc-b" -->\n\n## Annotations\n\n- Beta <!-- annotation id=ann-b page=1 x=30.00 y=30.00 w=25.00 h=6.00 kind=text lh=1.50 fs=14.0 -->\n';
  await inject(page, 'pdf-a', pdfA);
  await inject(page, 'pdf-b', pdfB);

  const openAndMeasureProximity = async docId => {
    await page.evaluate(async id => {
      editor.init('');
      await editor.loadDocument(id);
      const pdfTools = await import('/static/js/document/ui/pdfTools.js');
      await pdfTools._setPdfViewActive(true);
    }, docId);
    await page.waitForSelector('.pdf-annotation-wrap');
    await page.waitForFunction(() => {
      const ed = document.getElementById('doc-editor-pane');
      return ed && getComputedStyle(ed).opacity === '1';
    });
    const box = await page.locator('.pdf-annotation-wrap').first().boundingBox();
    // Move away first so reopening always produces a real pointer transition into the proximity buffer.
    await page.mouse.move(1, 1);
    await page.mouse.move(Math.max(1, box.x - 12), box.y + box.height / 2);
    await page.waitForTimeout(80);
    return page.evaluate(() => ({
      controlsVisible: Array.from(document.querySelectorAll('.pdf-annotation-menu-btn'))
        .filter(btn => btn.style.display !== 'none').length,
      mirrorCount: document.body.querySelectorAll('.pdf-annotation-measure').length,
    }));
  };

  // Cycle 1: proximity controls work and mirror is cleaned up on minimize/close
  const firstMount = await openAndMeasureProximity('pdf-a');
  assert(firstMount.controlsVisible >= 1, 'initial PDF mount must reveal annotation controls on proximity');
  await page.evaluate(() => editor.closePanel('down'));
  await page.waitForFunction(() => !document.getElementById('doc-editor-pane'));
  const mirrorsAfterClose = await page.evaluate(() => document.body.querySelectorAll('.pdf-annotation-measure').length);
  assert.equal(mirrorsAfterClose, 0, 'closing PDF panel must remove any body measure mirror');

  // Cycle 2: reopened pane must rewire proximity on the new #doc-pdf-view node
  const reopenMount = await openAndMeasureProximity('pdf-a');
  assert(reopenMount.controlsVisible >= 1, 'reopened PDF pane must rewire proximity controls');

  // Cross-doc isolation: cleaning up pdf-a must not delete pdf-b state or save timers
  const isolation = await page.evaluate(async () => {
    const pdfPersistence = await import('/static/js/document/ui/pdf/persistence.js');
    const pdfTools = await import('/static/js/document/ui/pdfTools.js');
    pdfPersistence._pdfPaneAnnotationsByDoc.set('pdf-b', [{ id: 'ann-b', page: 1, x: 30, y: 30, w: 25, h: 6 }]);
    const dummyTimer = setTimeout(() => {}, 60000);
    pdfPersistence._pdfPaneSaveTimers.set('pdf-b', dummyTimer);

    pdfTools._cleanupPdfDocState('pdf-a');
    const bStateIntact = pdfPersistence._pdfPaneAnnotationsByDoc.has('pdf-b') && pdfPersistence._pdfPaneSaveTimers.has('pdf-b');
    const aCleared = !pdfPersistence._pdfPaneAnnotationsByDoc.has('pdf-a');
    const mirrorsAfterCleanup = document.body.querySelectorAll('.pdf-annotation-measure').length;

    clearTimeout(dummyTimer);
    pdfPersistence._pdfPaneSaveTimers.delete('pdf-b');
    pdfPersistence._pdfPaneAnnotationsByDoc.clear();
    return { bStateIntact, aCleared, mirrorsAfterCleanup };
  });

  assert.equal(isolation.bStateIntact, true, 'disposing pdf-a must not disrupt pdf-b persistence state or timer');
  assert.equal(isolation.aCleared, true, 'disposing pdf-a must clear pdf-a state');
  assert.equal(isolation.mirrorsAfterCleanup, 0, 'disposing active PDF doc must clear mirror element');
});

test('pdf lifecycle: switching to markdown releases transient PDF resources without accumulation', async t => {
  const { page } = await setup(t, async (route, request) => {
    if (!request.path.endsWith('/render-pages')) return false;
    await route.fulfill({
      json: {
        pages: [{
          page: 1,
          width: 600,
          height: 800,
          image: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR42mNk+M/wHwAF/gL+AvzZ8QAAAABJRU5ErkJggg==',
          fields: [],
        }],
      },
    });
    return true;
  });

  // This scenario edits Markdown B through the source textarea, so it pins the
  // Markdown source editor instead of the visual default.
  await page.evaluate(() => localStorage.setItem('odysseus-doc-md-editor-mode', 'markdown'));
  await page.evaluate(() => {
    const active = { docPointerdown: 0, docKeydown: 0, paneMousemove: 0, paneMouseleave: 0 };
    const tracked = new WeakMap();
    const origAdd = EventTarget.prototype.addEventListener;
    const origRemove = EventTarget.prototype.removeEventListener;
    const counterKey = (target, type) => {
      if (target === document && type === 'pointerdown') return 'docPointerdown';
      if (target === document && type === 'keydown') return 'docKeydown';
      if (target instanceof Element && target.id === 'doc-pdf-view' && type === 'mousemove') return 'paneMousemove';
      if (target instanceof Element && target.id === 'doc-pdf-view' && type === 'mouseleave') return 'paneMouseleave';
      return null;
    };
    EventTarget.prototype.addEventListener = function (type, listener, options) {
      const key = counterKey(this, type);
      if (key && listener && !(typeof options === 'object' && options?.signal?.aborted)) {
        let byTarget = tracked.get(this);
        if (!byTarget) { byTarget = new Map(); tracked.set(this, byTarget); }
        const capture = Boolean(typeof options === 'boolean' ? options : options?.capture);
        const entryKey = `${type}:${capture}`;
        let set = byTarget.get(entryKey);
        if (!set) { set = new Set(); byTarget.set(entryKey, set); }
        if (!set.has(listener)) {
          set.add(listener);
          active[key]++;
          if (typeof options === 'object' && options?.signal) {
            options.signal.addEventListener('abort', () => {
              if (set.delete(listener)) active[key]--;
            }, { once: true });
          }
        }
      }
      return origAdd.call(this, type, listener, options);
    };
    EventTarget.prototype.removeEventListener = function (type, listener, options) {
      const key = counterKey(this, type);
      if (key && listener) {
        const capture = Boolean(typeof options === 'boolean' ? options : options?.capture);
        const set = tracked.get(this)?.get(`${type}:${capture}`);
        if (set?.delete(listener)) active[key]--;
      }
      return origRemove.call(this, type, listener, options);
    };
    window.__pdfNavTracker = active;
  });

  const pdfA = '<!-- pdf_source upload_id="nav-doc-a" -->\n\n## Annotations\n\n- Alpha <!-- annotation id=ann-nav page=1 x=20.00 y=20.00 w=25.00 h=6.00 kind=text lh=1.50 fs=14.0 -->\n';
  await inject(page, 'pdf-nav-a', pdfA);
  await inject(page, 'md-nav-b', '# Markdown B\nInitial body');

  const verifyPdfActiveAndMirror = async expectedText => {
    await page.waitForSelector('.pdf-annotation-wrap');
    await page.waitForFunction(() => {
      const ed = document.getElementById('doc-editor-pane');
      const pdfPane = document.getElementById('doc-pdf-view');
      return ed && getComputedStyle(ed).opacity === '1'
        && pdfPane && pdfPane.style.display !== 'none'
        && document.body.querySelectorAll('.pdf-annotation-measure').length === 1;
    });
    const box = await page.locator('.pdf-annotation-wrap').first().boundingBox();
    await page.mouse.move(1, 1);
    await page.mouse.move(Math.max(1, box.x - 12), box.y + box.height / 2);
    await page.waitForTimeout(60);
    const controlsVisible = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.pdf-annotation-menu-btn'))
        .filter(btn => btn.style.display !== 'none').length
    );
    assert(controlsVisible >= 1, 'proximity hover must reveal PDF annotation controls');
    if (expectedText !== undefined) {
      assert.equal(await page.locator('.pdf-annotation-wrap textarea').first().inputValue(), expectedText);
    }
    await page.locator('.pdf-annotation-menu-btn').first().click();
    assert.equal(
      await page.evaluate(() => document.querySelector('.pdf-annotation-text-menu')?.style.display !== 'none'),
      true,
      'annotation text menu button must open menu'
    );
    await page.mouse.click(box.x + box.width + 40, box.y + 5);
    assert.equal(
      await page.evaluate(() => document.querySelector('.pdf-annotation-text-menu')?.style.display),
      'none',
      'document pointerdown outside menu must dismiss menu'
    );
  };

  // Step 1: Open PDF A, activate controls, trigger mirror measurement, and edit annotation
  await page.evaluate(() => editor.loadDocument('pdf-nav-a'));
  await verifyPdfActiveAndMirror('Alpha');
  const ta = page.locator('.pdf-annotation-wrap textarea').first();
  await ta.fill('Alpha edited');
  await page.locator('#doc-pdf-add-text-btn').click();
  assert.equal(
    await page.evaluate(() => document.getElementById('doc-pdf-view')?.style.cursor),
    'crosshair',
    'activating text drop mode must set crosshair cursor'
  );

  // Step 2: Switch to Markdown B without closing the panel and verify PDF resources are released
  await page.evaluate(() => { window.__initialPdfNavPane = document.getElementById('doc-editor-pane'); });
  await page.evaluate(() => editor.loadDocument('md-nav-b'));
  await page.waitForFunction(() => {
    const pdfPane = document.getElementById('doc-pdf-view');
    const wrap = document.getElementById('doc-editor-wrap');
    return pdfPane && pdfPane.style.display === 'none' && wrap && wrap.style.display !== 'none';
  });

  const afterFirstSwitch = await page.evaluate(async () => {
    const pdfTools = await import('/static/js/document/ui/pdfTools.js');
    const pdfPane = document.getElementById('doc-pdf-view');
    return {
      samePane: document.getElementById('doc-editor-pane') === window.__initialPdfNavPane,
      mirrorCount: document.body.querySelectorAll('.pdf-annotation-measure').length,
      proximityWired: pdfTools._pdfPaneProximityWired,
      menuDismissWired: pdfTools._pdfAnnotationMenuDismissWired,
      pdfCursor: pdfPane?.style.cursor || '',
      pdfSavePillCount: pdfPane?.querySelectorAll('#doc-pdf-save-pill').length || 0,
      pdfChildCount: pdfPane?.children.length || 0,
      pdfViewStateA: pdfTools._pdfViewState.get('pdf-nav-a'),
      tracker: { ...window.__pdfNavTracker },
    };
  });
  assert.equal(afterFirstSwitch.samePane, true, 'switching documents must keep the same panel mounted');
  assert.equal(afterFirstSwitch.mirrorCount, 0, 'leaving PDF mode must remove .pdf-annotation-measure');
  assert.equal(afterFirstSwitch.proximityWired, false, 'leaving PDF mode must unwire pane proximity listeners');
  assert.equal(afterFirstSwitch.menuDismissWired, false, 'leaving PDF mode must unwire document menu-dismiss listeners');
  assert.equal(afterFirstSwitch.pdfCursor, '', 'leaving PDF mode must clear PDF drop mode cursor');
  assert.equal(afterFirstSwitch.pdfSavePillCount, 1, 'leaving PDF mode must preserve #doc-pdf-save-pill');
  assert.equal(afterFirstSwitch.pdfChildCount, 1, 'leaving PDF mode must clear rendered PDF pages');
  assert.notEqual(afterFirstSwitch.pdfViewStateA, false, 'document switch must preserve PDF A view mode state');

  // Verify Markdown B remains interactive and isolated from PDF A
  await page.locator('#doc-editor-textarea').fill('# Markdown B\nEdited while PDF hidden');
  const bState = await page.evaluate(() => ({
    bContent: state.docs.get('md-nav-b')?.content,
    aContent: state.docs.get('pdf-nav-a')?.content,
  }));
  assert.equal(bState.bContent, '# Markdown B\nEdited while PDF hidden');
  assert.match(bState.aContent || '', /Alpha edited/);

  const mdBaselineTracker = afterFirstSwitch.tracker;

  // Steps 3-4: Switch back to PDF A and repeat transitions verifying zero listener or DOM accumulation
  for (let cycle = 0; cycle < 3; cycle++) {
    await page.evaluate(() => editor.loadDocument('pdf-nav-a'));
    await verifyPdfActiveAndMirror('Alpha edited');
    const onPdf = await page.evaluate(async () => {
      const pdfTools = await import('/static/js/document/ui/pdfTools.js');
      return {
        mirrorCount: document.body.querySelectorAll('.pdf-annotation-measure').length,
        proximityWired: pdfTools._pdfPaneProximityWired,
        menuDismissWired: pdfTools._pdfAnnotationMenuDismissWired,
        savePillCount: document.querySelectorAll('#doc-pdf-save-pill').length,
        tracker: { ...window.__pdfNavTracker },
      };
    });
    assert.equal(onPdf.mirrorCount, 1, `cycle ${cycle}: PDF A must have exactly 1 measurement mirror`);
    assert.equal(onPdf.proximityWired, true, `cycle ${cycle}: PDF A must rewire proximity listeners`);
    assert.equal(onPdf.menuDismissWired, true, `cycle ${cycle}: PDF A must rewire menu-dismiss listeners`);
    assert.equal(onPdf.savePillCount, 1, `cycle ${cycle}: #doc-pdf-save-pill must remain unique`);
    assert.equal(onPdf.tracker.docPointerdown - mdBaselineTracker.docPointerdown, 1, `cycle ${cycle}: no document pointerdown accumulation`);
    assert.equal(onPdf.tracker.docKeydown - mdBaselineTracker.docKeydown, 1, `cycle ${cycle}: no document keydown accumulation`);
    assert.equal(onPdf.tracker.paneMousemove - mdBaselineTracker.paneMousemove, 1, `cycle ${cycle}: no pane mousemove accumulation`);
    assert.equal(onPdf.tracker.paneMouseleave - mdBaselineTracker.paneMouseleave, 1, `cycle ${cycle}: no pane mouseleave accumulation`);

    await page.evaluate(() => editor.loadDocument('md-nav-b'));
    await page.waitForFunction(() => {
      const pdfPane = document.getElementById('doc-pdf-view');
      const wrap = document.getElementById('doc-editor-wrap');
      return pdfPane && pdfPane.style.display === 'none' && wrap && wrap.style.display !== 'none';
    });
    const backOnMd = await page.evaluate(async () => {
      const pdfTools = await import('/static/js/document/ui/pdfTools.js');
      return {
        mirrorCount: document.body.querySelectorAll('.pdf-annotation-measure').length,
        proximityWired: pdfTools._pdfPaneProximityWired,
        menuDismissWired: pdfTools._pdfAnnotationMenuDismissWired,
        bVal: document.getElementById('doc-editor-textarea')?.value,
        tracker: { ...window.__pdfNavTracker },
      };
    });
    assert.equal(backOnMd.mirrorCount, 0, `cycle ${cycle}: switching back to Markdown B must clear mirror`);
    assert.equal(backOnMd.proximityWired, false, `cycle ${cycle}: switching back to Markdown B must clear proximity`);
    assert.equal(backOnMd.menuDismissWired, false, `cycle ${cycle}: switching back to Markdown B must clear menu-dismiss`);
    assert.equal(backOnMd.bVal, '# Markdown B\nEdited while PDF hidden', `cycle ${cycle}: Markdown B draft preserved`);
    assert.deepEqual(backOnMd.tracker, mdBaselineTracker, `cycle ${cycle}: all transient PDF listeners returned to baseline`);
  }
});

test('inline tab rename restores the persisted title after a rejected PATCH', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (request.method !== 'PATCH') return false;
    await route.fulfill({ status: 500, json: { detail: 'Rejected rename' } });
    return true;
  });
  await inject(page, 'rename', 'Body', 'markdown', 'Original');
  await page.locator('.doc-tab[data-doc-id="rename"] .doc-tab-title').dblclick();
  const input = page.locator('.doc-tab-title-input');
  await input.fill('Rejected');
  await input.press('Enter');
  await page.waitForFunction(() => state.docs.get('rename')._titleRequestSeq === 1
    && !state.docs.get('rename')._titleAbortController);
  assert.equal(requests.find(request => request.method === 'PATCH').body.expected_title, 'Original');
  assert.equal(await page.evaluate(() => state.docs.get('rename').title), 'Original');
  assert.equal(await page.locator('#doc-title-input').inputValue(), 'Original');
  assert.equal(await page.locator('.doc-tab[data-doc-id="rename"] .doc-tab-title').textContent(), 'Original');
});

test('automatic creation completed after navigation preserves document identity and content', async t => {
  let release;
  let started;
  const held = new Promise(resolve => { release = resolve; });
  const creating = new Promise(resolve => { started = resolve; });
  t.after(() => release());
  const { page } = await setup(t, async (route, request) => {
    if (request.method !== 'POST' || request.path !== '/api/document') return false;
    started();
    await held;
    await route.fulfill({ json: { id: 'created-a', title: '', language: 'richtext', current_content: 'Draft A', session_id: 'session-1' } });
    return true;
  });
  await inject(page, 'existing-b', 'Private B');
  await page.evaluate(async () => {
    state.setLastSessionId('session-1');
    state.setActiveDocId(null);
    document.getElementById('doc-editor-textarea').value = 'Draft A';
    const flows = await import('/static/js/document/ui/documentFlows.js');
    window.pendingCreation = flows._autoCreateFromInput('Draft A');
  });
  await creating;
  await page.evaluate(() => editor.loadDocument('existing-b'));
  release();
  await page.evaluate(() => window.pendingCreation);
  assert.deepEqual(await page.evaluate(() => ({
    active: state.activeDocId,
    a: state.docs.get('created-a').content,
    b: state.docs.get('existing-b').content,
    session: state.docs.get('created-a').sessionId,
    editor: document.getElementById('doc-editor-textarea').value,
    tabCount: document.querySelectorAll('.doc-tab[data-doc-id="created-a"]').length,
  })), { active: 'existing-b', a: 'Draft A', b: 'Private B', session: 'session-1', editor: 'Private B', tabCount: 1 });
});

test('email HTML conversion decodes HTML5 references once without executing untrusted content', async t => {
  const { page } = await setup(t);
  const result = await page.evaluate(async () => {
    const { _htmlToPlainText } = await import('/static/js/document/domain/documentKinds.js');
    const { _sanitizeOutgoingEmailBody } = await import('/static/js/document/ui/email/localDraft.js');
    window.entityExecuted = false;
    const input = '<p>&Lt; &Gt; &Not; &NotEqualTilde; &#128; &eacute; &copy</p>';
    const literal = '<p>Use &lt;b&gt;bold&lt;/b&gt; literally</p><script>window.entityExecuted=true</script>';
    return {
      text: _htmlToPlainText(input),
      literal: _sanitizeOutgoingEmailBody(literal),
      malformed: _sanitizeOutgoingEmailBody('<p title=">unfinished'),
      executed: window.entityExecuted,
    };
  });
  assert.deepEqual(result, {
    text: '≪ ≫ ⫬ ≂̸ € é ©',
    literal: 'Use <b>bold</b> literally',
    malformed: '<p title=">unfinished',
    executed: false,
  });
});

test('email actions preserve literal tags from the rich editor in draft, send, and schedule payloads', async t => {
  const { page, requests } = await setup(t, async (route, request) => {
    if (!['/api/email/draft', '/api/email/send', '/api/email/schedule'].includes(request.path)) return false;
    await route.fulfill({ json: { success: true, draft_uid: 'draft-1', draft_folder: 'Drafts', sent_uid: 'sent-1' } });
    return true;
  });
  for (const action of ['draft', 'send', 'schedule']) {
    await inject(page, `${action}-literals`, 'To: alice@example.com\nSubject: Literal text\n---\nBody', 'email');
    const expected = await page.evaluate(() => {
      const rich = document.getElementById('doc-email-richbody');
      rich.innerHTML = '<p>Use &lt;b&gt;bold&lt;/b&gt; literally</p><p><br></p><p><br></p><p>a&lt;b holds.</p>';
      return rich.innerText.trim().replace(/\n{3,}/g, '\n\n');
    });
    assert.match(expected, /<b>bold<\/b>/);
    const submitted = page.waitForResponse(response => response.url().endsWith(`/api/email/${action}`));
    await page.evaluate(async action => {
      const workflow = await import('/static/js/document/ui/email/sendWorkflow.js');
      if (action === 'draft') await workflow._saveDraft();
      else if (action === 'send') await workflow._sendEmail();
      else await workflow._scheduleSend();
    }, action);
    if (action === 'schedule') await page.locator('#sched-confirm').click();
    await submitted;
    const payload = requests.filter(request => request.path === `/api/email/${action}`).at(-1)?.body;
    assert.ok(payload, `${action} must submit its request`);
    assert.equal(payload.body, expected, `${action} must preserve its plain-text mirror`);
    if (action !== 'schedule') {
      assert.match(payload.body_html, /&lt;b&gt;bold&lt;\/b&gt;/);
      assert.doesNotMatch(payload.body_html, /<b>bold<\/b>/);
    }
  }
});
});

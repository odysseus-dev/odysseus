// tests/documentUpdate.behavior.mjs
/**
 * Focused behavior tests for ui/aiUpdates/documentUpdate.
 * The suite evaluates the real source with a linker and invokes the public
 * owner export plus private helpers exposed only through a test-only export.
 * Run through tests/documentUpdate.test.mjs, which enables vm modules.
 */

import test, { describe, it, before, beforeEach, mock } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';

import { diffLines as realDiffLines } from '../frontend/src/features/documents/ui/diffViewer.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const featuresDir = path.join(repoRoot, 'frontend/src/features/documents');
const entryPath = path.join(featuresDir, 'ui/aiUpdates/documentUpdate.js');

const MOCK_FILES = new Set([
  'host.js',
  'sourceEditor.js',
  'composerFields.js',
  'richBodyWiring.js',
  'suggestions.js',
  'previewModes.js',
  'pdfTools.js',
  'diffViewer.js',
  'localDraft.js',
  'liveStream.js',
  'versionHistory.js',
  'documentStatus.js',
  'documentActions.js',
  'documentSession.js',
]);

function classList() {
  const classes = new Set();
  return {
    add(...names) { names.forEach(name => classes.add(name)); },
    remove(...names) { names.forEach(name => classes.delete(name)); },
    contains(name) { return classes.has(name); },
    toggle(name, force) {
      if (force === undefined) {
        if (classes.has(name)) classes.delete(name);
        else classes.add(name);
        return classes.has(name);
      }
      if (force) classes.add(name);
      else classes.delete(name);
      return classes.has(name);
    },
  };
}

function makeElement(id) {
  const el = {
    id,
    value: '',
    disabled: false,
    readOnly: false,
    textContent: '',
    innerHTML: '',
    className: '',
    srcdoc: '',
    style: {},
    classList: classList(),
    dataset: {},
    parentElement: null,
    children: [],
    offsetHeight: 1,
    offsetWidth: 1,
    appendChild(child) {
      this.children.push(child);
      child.parentElement = this;
      return child;
    },
    remove() {
      const parent = this.parentElement;
      if (!parent) return;
      parent.children = parent.children.filter(child => child !== this);
      this.parentElement = null;
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  return el;
}

function installDom() {
  const elements = new Map();
  const textarea = makeElement('doc-editor-textarea');
  const editorArea = makeElement('doc-editor-area');
  editorArea.appendChild(textarea);
  const ids = [
    'doc-editor-wrap',
    'doc-editor-pane',
    'overflow-doc-btn',
    'doc-indicator-btn',
    'doc-version-badge',
    'doc-title-input',
    'doc-language-select',
    'doc-md-toolbar',
    'doc-html-preview',
    'doc-csv-preview',
  ];
  for (const id of ids) elements.set(id, makeElement(id));
  elements.get('doc-editor-pane').isConnected = true;
  elements.set('doc-editor-textarea', textarea);
  elements.set('doc-editor-area', editorArea);
  elements.get('doc-csv-preview').style.display = 'none';

  const documentMock = {
    getElementById(id) { return elements.get(id) || null; },
    createElement() { return makeElement(''); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
    removeEventListener() {},
    body: makeElement('body'),
  };
  globalThis.document = documentMock;
  globalThis.requestAnimationFrame = (cb) => { cb(); return 1; };
  globalThis.localStorage = {
    getItem() { return null; },
    setItem() {},
    removeItem() {},
  };
  return { elements, textarea, editorArea };
}

const calls = {
  sessionId: 'sess-1',
  streamResult: null,
  exitDiff: [],
  showEmail: [],
  clearDraft: [],
  showRich: [],
  refreshPreview: [],
  setMarkdown: [],
  themed: [],
  autoTitle: [],
  restoreSuggestions: [],
  markdownVisible: false,
  htmlPreviewActive: false,
  diffModeActive: false,
  formBacked: false,
  suspend: 0,
  closeVersion: 0,
  placeholder: 0,
  syncHl: 0,
  autoDetect: 0,
  toggleHtml: 0,
  toggleCsv: 0,
  refreshPdf: 0,
  openPanel: 0,
  renderTabs: 0,
  syncIndicator: 0,
  syncHeader: 0,
};

function resetCalls() {
  calls.sessionId = 'sess-1';
  calls.streamResult = null;
  calls.exitDiff = [];
  calls.showEmail = [];
  calls.clearDraft = [];
  calls.showRich = [];
  calls.refreshPreview = [];
  calls.setMarkdown = [];
  calls.themed = [];
  calls.autoTitle = [];
  calls.restoreSuggestions = [];
  calls.markdownVisible = false;
  calls.htmlPreviewActive = false;
  calls.diffModeActive = false;
  calls.formBacked = false;
  calls.suspend = 0;
  calls.closeVersion = 0;
  calls.placeholder = 0;
  calls.syncHl = 0;
  calls.autoDetect = 0;
  calls.toggleHtml = 0;
  calls.toggleCsv = 0;
  calls.refreshPdf = 0;
  calls.openPanel = 0;
  calls.renderTabs = 0;
  calls.syncIndicator = 0;
  calls.syncHeader = 0;
}

const pdfViewState = new Map();
const syntheticModules = new Map();

function mockEvaluate(fileName, module) {
  const set = (name, value) => module.setExport(name, value);
  if (fileName === 'host.js') {
    set('sessionModule', {
      getCurrentSessionId() { return calls.sessionId || ''; },
    });
    set('markdownModule', {
      sanitizeAllowedHtml: value => String(value || ''),
      mdToHtml: value => String(value || ''),
    });
    return;
  }
  if (fileName === 'documentSession.js') {
    set('persistMarkdownDraft', () => true);
    return;
  }
  if (fileName === 'diffViewer.js') {
    set('diffLines', (...args) => realDiffLines(...args));
    set('exitDiffMode', (...args) => { calls.exitDiff.push(args); });
    set('_diffModeActive', calls.diffModeActive);
    return;
  }
  if (fileName === 'previewModes.js') {
    set('_setMarkdownPreviewActive', (...args) => { calls.setMarkdown.push(args); });
    set('_isMarkdownPreviewVisible', () => calls.markdownVisible);
    set('toggleCsvPreview', () => { calls.toggleCsv += 1; });
    set('toggleHtmlPreview', () => { calls.toggleHtml += 1; });
    set('_themedRenderSrcdoc', (...args) => { calls.themed.push(args); return 'srcdoc'; });
    set('_htmlPreviewActive', calls.htmlPreviewActive);
    return;
  }
  if (fileName === 'sourceEditor.js') {
    set('syncHighlighting', () => { calls.syncHl += 1; });
    set('attemptAutoDetect', () => { calls.autoDetect += 1; });
    set('_refreshMarkdownPreviewIfVisible', (...args) => { calls.refreshPreview.push(args); return false; });
    return;
  }
  if (fileName === 'composerFields.js') {
    set('_showEmailFields', (...args) => { calls.showEmail.push(args); });
    return;
  }
  if (fileName === 'richBodyWiring.js') {
    set('_showRichTextEditor', (...args) => { calls.showRich.push(args); });
    set('_showMarkdownVisualEditor', () => {});
    set('_hideMarkdownVisualEditor', () => {});
    return;
  }
  if (fileName === 'suggestions.js') {
    set('suspendSuggestions', () => { calls.suspend += 1; });
    set('_restoreSuggestionsFromStorage', (docId) => { calls.restoreSuggestions.push(docId); });
    return;
  }
  if (fileName === 'localDraft.js') {
    set('_clearEmailLocalDraft', (...args) => { calls.clearDraft.push(args); });
    set('_persistEmailLocalDraftSoon', () => {});
    return;
  }
  if (fileName === 'pdfTools.js') {
    set('_isFormBackedDoc', () => calls.formBacked);
    set('_pdfViewState', pdfViewState);
    set('_refreshPdfPreviewIframe', () => { calls.refreshPdf += 1; });
    return;
  }
  if (fileName === 'liveStream.js') {
    set('streamDocFinalize', () => calls.streamResult);
    return;
  }
  if (fileName === 'versionHistory.js') {
    set('_closeVersionPanel', () => { calls.closeVersion += 1; });
    return;
  }
  if (fileName === 'documentStatus.js') {
    set('_syncEditorPlaceholder', () => { calls.placeholder += 1; });
    return;
  }
  if (fileName === 'documentActions.js') {
    set('autoTitleFromContent', (...args) => { calls.autoTitle.push(args); });
    return;
  }
  throw new Error(`No mock exports for ${fileName}`);
}

function publishLiveBindings() {
  const diff = syntheticModules.get('diffViewer.js');
  const preview = syntheticModules.get('previewModes.js');
  if (diff) diff.setExport('_diffModeActive', calls.diffModeActive);
  if (preview) preview.setExport('_htmlPreviewActive', calls.htmlPreviewActive);
}

async function loadDocumentUpdate() {
  const cache = new Map();

  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const resolved = specifier.startsWith('.')
      ? path.resolve(path.dirname(parent), specifier)
      : null;
    const fileName = resolved ? path.basename(resolved) : specifier;
    const url = resolved ? pathToFileURL(resolved).href : `synthetic:${specifier}`;
    if (cache.has(url)) return cache.get(url);

    const useMock = !resolved || MOCK_FILES.has(fileName) || !fs.existsSync(resolved);
    if (useMock) {
      const exportNames = {
        'host.js': ['sessionModule', 'markdownModule'],
        'diffViewer.js': ['diffLines', 'exitDiffMode', '_diffModeActive'],
        'previewModes.js': ['_setMarkdownPreviewActive', '_isMarkdownPreviewVisible', 'toggleCsvPreview', 'toggleHtmlPreview', '_themedRenderSrcdoc', '_htmlPreviewActive'],
        'sourceEditor.js': ['syncHighlighting', 'attemptAutoDetect', '_refreshMarkdownPreviewIfVisible'],
        'composerFields.js': ['_showEmailFields'],
        'richBodyWiring.js': ['_showRichTextEditor', '_showMarkdownVisualEditor', '_hideMarkdownVisualEditor'],
        'suggestions.js': ['suspendSuggestions', '_restoreSuggestionsFromStorage'],
        'pdfTools.js': ['_isFormBackedDoc', '_pdfViewState', '_refreshPdfPreviewIframe'],
        'localDraft.js': ['_clearEmailLocalDraft', '_persistEmailLocalDraftSoon'],
        'liveStream.js': ['streamDocFinalize'],
        'versionHistory.js': ['_closeVersionPanel'],
        'documentStatus.js': ['_syncEditorPlaceholder'],
        'documentActions.js': ['autoTitleFromContent'],
        'documentSession.js': ['persistMarkdownDraft'],
      }[fileName];
      if (!exportNames) {
        throw new Error(`Unexpected unresolved import ${specifier} from ${parent}`);
      }
      const mod = new SyntheticModule(exportNames, function evaluate() {
        mockEvaluate(fileName, this);
      }, { identifier: url });
      cache.set(url, mod);
      syntheticModules.set(fileName, mod);
      await mod.link(linker);
      return mod;
    }

    const src = fs.readFileSync(resolved, 'utf8');
    const mod = new SourceTextModule(src, {
      identifier: url,
      initializeImportMeta(meta) { meta.url = url; },
    });
    cache.set(url, mod);
    await mod.link(linker);
    return mod;
  }

  const entryUrl = pathToFileURL(entryPath).href;
  const src = fs.readFileSync(entryPath, 'utf8');
  const entrySource = src + '\nexport { lineDiff, animateDocChange };';
  const entry = new SourceTextModule(entrySource, {
    identifier: entryUrl,
    initializeImportMeta(meta) { meta.url = entryUrl; },
  });
  cache.set(entryUrl, entry);
  await entry.link(linker);
  await entry.evaluate();

  const stateUrl = pathToFileURL(path.join(featuresDir, 'application/documentState.js')).href;
  const commandUrl = pathToFileURL(path.join(featuresDir, 'application/documentCommands.js')).href;
  return {
    api: entry.namespace,
    state: cache.get(stateUrl).namespace,
    commands: cache.get(commandUrl).namespace,
  };
}

function emailContent({ to = 'ada@example.com', subject = 'Hello', inReplyTo = '', uid = '', folder = 'INBOX', body = '' } = {}) {
  const lines = [`To: ${to}`, `Subject: ${subject}`];
  if (inReplyTo) lines.push(`In-Reply-To: ${inReplyTo}`);
  if (uid) lines.push(`X-Source-UID: ${uid}`);
  if (folder) lines.push(`X-Source-Folder: ${folder}`);
  return `${lines.join('\n')}\n---\n${body}`;
}

let env;
let dom;

describe('documentUpdate', () => {
  before(async () => {
    dom = installDom();
    env = await loadDocumentUpdate();
    env.commands.bindDocumentCommands({
      openPanel() { calls.openPanel += 1; },
      closePanel() {},
      switchToDoc() {},
      saveCurrentToMap() {},
      renderTabs() { calls.renderTabs += 1; },
      _syncDocIndicator() { calls.syncIndicator += 1; },
      _syncHeaderActions() { calls.syncHeader += 1; },
      showEmptyState() {},
      _showLoadingOverlay() {},
      _hideLoadingOverlay() {},
      _minimizeNotesForDocumentOpen() {},
      _ensureDocChipRegistered() {},
    });
  });

  beforeEach(() => {
    resetCalls();
    pdfViewState.clear();
    publishLiveBindings();
    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    env.state.setPanelOpen(true);
    dom.textarea.value = '';
    dom.textarea.disabled = true;
    dom.textarea.readOnly = false;
    dom.editorArea.children = [dom.textarea];
    dom.textarea.parentElement = dom.editorArea;
    for (const el of dom.elements.values()) {
      el.className = '';
      el.textContent = '';
      el.innerHTML = '';
      el.value = el.id === 'doc-editor-textarea' ? dom.textarea.value : '';
    }
    dom.elements.get('doc-csv-preview').style.display = 'none';
    dom.elements.get('overflow-doc-btn').classList = classList();
    dom.elements.get('doc-indicator-btn').classList = classList();
    dom.elements.get('doc-editor-wrap').classList = classList();
    globalThis.requestAnimationFrame = (cb) => { cb(); return 1; };
  });

  it('maps diffLines entries and refuses diffs over the cell limit', () => {
    assert.deepEqual(env.api.lineDiff('alpha\nbeta', 'alpha\ngamma'), [
      { type: 'same', text: 'alpha' },
      { type: 'del', text: 'beta' },
      { type: 'add', text: 'gamma' },
    ]);
    assert.deepEqual(env.api.lineDiff('only', 'only'), [
      { type: 'same', text: 'only' },
    ]);
    assert.equal(env.api.lineDiff(`${'x\n'.repeat(800)}`, `${'y\n'.repeat(800)}`), null);
  });

  it('skips animation when the textarea is missing, the text is unchanged, or the diff is too large', async () => {
    dom.elements.delete('doc-editor-textarea');
    assert.equal(await env.api.animateDocChange('a', 'b'), false);
    dom.elements.set('doc-editor-textarea', dom.textarea);

    assert.equal(await env.api.animateDocChange('same', 'same'), true);
    assert.equal(dom.textarea.value, '');
    assert.equal(calls.syncHl, 0);

    assert.equal(await env.api.animateDocChange(`${'x\n'.repeat(800)}`, `${'y\n'.repeat(800)}`), false);
    assert.equal(dom.textarea.readOnly, false);
  });

  it('shows a line diff and then applies the saved text', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
      const pending = env.api.animateDocChange('alpha', 'beta');
      const overlay = dom.editorArea.children.find(child => child.className === 'doc-diff-overlay');
      assert.ok(overlay);
      assert.match(overlay.children[0].innerHTML, /−1/);
      assert.match(overlay.children[0].innerHTML, /\+1/);
      assert.equal(dom.textarea.readOnly, true);
      assert.equal(dom.elements.get('doc-editor-wrap').classList.contains('animating'), true);

      await mock.timers.tick(2500);
      await mock.timers.tick(400);
      assert.equal(await pending, true);
      assert.equal(dom.textarea.value, 'beta');
      assert.equal(dom.textarea.readOnly, false);
      assert.equal(calls.syncHl, 2);
      assert.equal(dom.editorArea.children.includes(overlay), false);
      assert.equal(dom.elements.get('doc-editor-wrap').classList.contains('animating'), false);
    } finally {
      mock.timers.reset();
    }
  });

  it('cancels an in-flight animation before a no-op change returns', async () => {
    mock.timers.enable({ apis: ['setTimeout'] });
    try {
      const pending = env.api.animateDocChange('alpha', 'beta');
      assert.equal(await env.api.animateDocChange('beta', 'beta'), true);
      await mock.timers.tick(2500);
      assert.equal(await pending, false);
      assert.equal(dom.textarea.value, '');
      assert.equal(dom.textarea.readOnly, false);
    } finally {
      mock.timers.reset();
    }
  });

  it('creates a document, opens a closed panel, and bumps navigation once', () => {
    env.state.setPanelOpen(false);
    const before = env.state._documentNavigationGeneration;
    env.api.handleDocUpdate({
      doc_id: 'doc-1',
      content: 'Hello\nworld',
      version: 2,
      title: 'Notes',
      language: 'markdown',
    });
    const doc = env.state.docs.get('doc-1');
    assert.equal(doc.content, 'Hello\nworld');
    assert.equal(doc._persistedContent, doc.content, 'a newly received document uses the server text as its recovery baseline');
    assert.equal(doc.title, 'Notes');
    assert.equal(doc.language, 'markdown');
    assert.equal(doc.version, 2);
    assert.equal(doc.sessionId, 'sess-1');
    assert.equal(env.state.activeDocId, 'doc-1');
    assert.equal(env.state._documentNavigationGeneration, before + 1);
    assert.equal(calls.openPanel, 1);
    assert.equal(calls.suspend, 1);
    assert.equal(calls.closeVersion, 1);
    assert.equal(calls.renderTabs, 1);
    assert.equal(calls.syncIndicator, 1);
    assert.equal(calls.syncHeader, 1);
    assert.equal(calls.placeholder, 1);
    assert.deepEqual(calls.restoreSuggestions, ['doc-1']);
    assert.equal(calls.autoTitle.length, 0);
    assert.equal(dom.elements.get('doc-version-badge').textContent, 'v2');
    assert.equal(dom.textarea.value, 'Hello\nworld');
    assert.equal(dom.textarea.disabled, false);
    assert.equal(dom.elements.get('doc-title-input').value, 'Notes');
    assert.equal(dom.elements.get('doc-language-select').value, 'markdown');
    assert.equal(dom.elements.get('overflow-doc-btn').classList.contains('has-docs'), true);
    assert.equal(dom.elements.get('doc-indicator-btn').classList.contains('visible'), true);
    assert.equal(calls.exitDiff.length, 0);
  });

  it('reuses an empty untitled document in the current session', () => {
    env.state.registerDocument({
      id: 'blank',
      title: 'Untitled',
      language: '',
      content: '   ',
      version: 1,
      sessionId: 'sess-1',
    });
    env.api.handleDocUpdate({
      doc_id: 'server-new',
      content: '# Draft',
      version: 1,
      title: '',
      language: 'markdown',
    });
    assert.equal(env.state.docs.has('server-new'), false);
    assert.equal(env.state.docs.get('blank').content, '# Draft');
    assert.equal(env.state.activeDocId, 'blank');
    assert.deepEqual(calls.autoTitle, [['# Draft', 'blank']]);
  });

  it('matches an open email by source identity and keeps its quote', () => {
    const quoted = emailContent({
      uid: 'uid-1',
      inReplyTo: '<m1>',
      body: `Earlier reply\n\n---------- Previous message ----------\nquoted history`,
    });
    env.state.registerDocument({
      id: 'mail-1',
      title: 'test',
      language: 'email',
      content: quoted,
      version: 4,
      sessionId: 'sess-1',
    });
    env.state.setActiveDocId('mail-1');
    dom.textarea.value = quoted;
    const incoming = emailContent({
      uid: 'uid-1',
      inReplyTo: '<m1>',
      subject: 'test',
      body: 'Updated reply',
    });
    env.api.handleDocUpdate({
      doc_id: 'server-other',
      content: incoming,
      version: 5,
      title: 'test',
      language: 'email',
    });
    assert.equal(env.state.docs.has('server-other'), false);
    const saved = env.state.docs.get('mail-1').content;
    assert.match(saved, /Updated reply/);
    assert.match(saved, /---------- Previous message ----------\nquoted history/);
    assert.equal(env.state.docs.get('mail-1').version, 5);
    assert.equal(env.state.docs.get('mail-1').language, 'email');
    assert.equal(calls.showEmail.length >= 1, true);
    assert.deepEqual(calls.clearDraft[0], ['uid-1', 'INBOX', '<m1>']);
    assert.equal(calls.showEmail.at(-1)[1].applyLocalDraft, false);
  });

  it('does not reuse a different email just because the subject matches', () => {
    env.state.registerDocument({
      id: 'mail-1',
      title: 'test',
      language: 'email',
      content: emailContent({ uid: 'uid-1', body: 'one' }),
      version: 1,
      sessionId: 'sess-1',
    });
    env.api.handleDocUpdate({
      doc_id: 'mail-2',
      content: emailContent({ uid: 'uid-2', subject: 'test', body: 'two' }),
      version: 1,
      title: 'test',
      language: 'email',
    });
    assert.equal(env.state.docs.get('mail-1').content.includes('two'), false);
    assert.match(env.state.docs.get('mail-2').content, /two/);
    assert.equal(env.state.activeDocId, 'mail-2');
  });

  it('migrates a streaming temp document onto the saved id', () => {
    calls.streamResult = '_streaming_1';
    env.state.registerDocument({
      id: '_streaming_1',
      title: '',
      language: 'markdown',
      content: 'partial',
      version: 1,
      sessionId: 'sess-1',
    });
    env.state.setActiveDocId('_streaming_1');
    env.api.handleDocUpdate({
      doc_id: 'real-9',
      content: 'finished',
      version: 3,
      title: 'Streamed',
      language: 'markdown',
    });
    assert.equal(env.state.docs.has('_streaming_1'), false);
    assert.equal(env.state.docs.get('real-9').content, 'finished');
    assert.equal(env.state.docs.get('real-9').title, 'Streamed');
    assert.equal(env.state.docs.get('real-9').version, 3);
    assert.equal(env.state.activeDocId, 'real-9');
  });

  it('discards a pending diff against the previously active document', () => {
    env.state.registerDocument({
      id: 'current',
      title: 'Current',
      language: 'markdown',
      content: 'old',
      version: 1,
      sessionId: 'sess-1',
    });
    env.state.setActiveDocId('current');
    calls.diffModeActive = true;
    publishLiveBindings();
    env.api.handleDocUpdate({
      doc_id: 'next',
      content: 'fresh',
      version: 1,
      title: 'Next',
      language: 'markdown',
    });
    assert.equal(calls.exitDiff.length, 1);
    assert.equal(calls.exitDiff[0][0], true);
    assert.deepEqual(calls.exitDiff[0][1], { persist: true });

    calls.exitDiff = [];
    calls.diffModeActive = true;
    publishLiveBindings();
    env.state.setActiveDocId('next');
    env.api.handleDocUpdate({
      doc_id: 'next',
      content: 'fresh-2',
      version: 2,
      title: 'Next',
      language: 'markdown',
    });
    assert.deepEqual(calls.exitDiff[0][1], { persist: false });
  });

  it('writes an already-saved plain edit directly into the textarea', () => {
    env.state.registerDocument({
      id: 'doc-1',
      title: 'Notes',
      language: 'markdown',
      content: 'before',
      version: 1,
      sessionId: 'sess-1',
    });
    env.state.setActiveDocId('doc-1');
    dom.textarea.value = 'before';
    env.api.handleDocUpdate({
      doc_id: 'doc-1',
      content: 'after',
      version: 2,
      title: 'Notes',
      language: 'markdown',
    });
    assert.equal(dom.textarea.value, 'after');
    assert.equal(env.state.docs.get('doc-1').content, 'after');
    assert.equal(calls.syncHl, 1);
    assert.equal(calls.showRich.length, 0);
    assert.equal(calls.showEmail.length, 0);
    assert.equal(dom.elements.get('doc-editor-wrap').classList.contains('doc-updated-flash'), true);
  });

  it('routes rich text, svg, csv, and streamed markdown to their preview paths', () => {
    const queued = [];
    globalThis.requestAnimationFrame = (cb) => { queued.push(cb); return queued.length; };

    env.state.registerDocument({
      id: 'rich-1',
      title: 'Rich',
      language: 'richtext',
      content: '<p>old</p>',
      version: 1,
      sessionId: 'sess-1',
    });
    env.state.setActiveDocId('rich-1');
    env.api.handleDocUpdate({
      doc_id: 'rich-1',
      content: '<p>new</p>',
      version: 2,
      title: 'Rich',
      language: 'richtext',
    });
    assert.equal(calls.showRich.length, 1);
    assert.equal(calls.showRich[0][0].content, '<p>new</p>');
    assert.equal(dom.textarea.value, '');

    calls.htmlPreviewActive = false;
    publishLiveBindings();
    env.api.handleDocUpdate({
      doc_id: 'svg-1',
      content: '<svg></svg>',
      version: 1,
      title: 'Picture',
      language: 'svg',
    });
    assert.equal(calls.toggleHtml, 0);
    queued.shift()();
    assert.equal(calls.toggleHtml, 1);

    calls.htmlPreviewActive = true;
    publishLiveBindings();
    const beforeToggle = calls.toggleHtml;
    env.api.handleDocUpdate({
      doc_id: 'svg-1',
      content: '<svg id="b"></svg>',
      version: 2,
      title: 'Picture',
      language: 'svg',
    });
    queued.shift()();
    assert.equal(calls.toggleHtml, beforeToggle);
    assert.deepEqual(calls.themed.at(-1), ['<svg id="b"></svg>', 'svg']);
    assert.equal(dom.elements.get('doc-html-preview').srcdoc, 'srcdoc');

    env.api.handleDocUpdate({
      doc_id: 'csv-1',
      content: 'a,b',
      version: 1,
      title: 'Table',
      language: 'csv',
    });
    queued.shift()();
    assert.equal(calls.toggleCsv, 1);

    calls.streamResult = 'stream-doc';
    env.api.handleDocUpdate({
      doc_id: 'md-stream',
      content: '# Live',
      version: 1,
      title: 'Live',
      language: 'markdown',
    });
    queued.shift()();
    assert.deepEqual(calls.setMarkdown.at(-1), [true, { remember: true }]);
  });

  it('refreshes a visible form-backed preview for the active document', () => {
    calls.formBacked = true;
    pdfViewState.set('pdf-1', true);
    env.api.handleDocUpdate({
      doc_id: 'pdf-1',
      content: '<!-- pdf -->',
      version: 1,
      title: 'Form',
      language: 'markdown',
    });
    assert.equal(calls.refreshPdf, 1);

    calls.refreshPdf = 0;
    pdfViewState.set('pdf-1', false);
    env.api.handleDocUpdate({
      doc_id: 'pdf-1',
      content: '<!-- pdf 2 -->',
      version: 2,
      title: 'Form',
      language: 'markdown',
    });
    assert.equal(calls.refreshPdf, 0);
  });
});

test('documentUpdate exposes its public owner handler and test-seam private helpers', () => {
  const src = fs.readFileSync(entryPath, 'utf8');
  assert.match(src, /let _animationCancel = null/);
  assert.match(src, /function lineDiff\(oldText, newText\)/);
  assert.match(src, /async function animateDocChange\(oldText, newText\)/);
  assert.match(src, /export function handleDocUpdate\(data\)/);
  assert.equal(typeof env.api.handleDocUpdate, 'function');
  assert.equal(typeof env.api.lineDiff, 'function');
  assert.equal(typeof env.api.animateDocChange, 'function');
});

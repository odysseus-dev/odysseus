// tests/documentLiveStream.behavior.mjs
/**
 * Behavior tests for ui/aiUpdates/liveStream.
 * Run through tests/documentLiveStream.test.mjs.
 */

import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const featuresDir = path.join(repoRoot, 'frontend/src/features/documents');
const entryPath = path.join(featuresDir, 'ui/aiUpdates/liveStream.js');

const calls = {
  sessionId: 'sess-1',
  diffModeActive: false,
  openPanel: 0,
  renderTabs: 0,
  saveCurrent: 0,
  syncIndicator: 0,
  syncHeader: 0,
  flush: [],
  releasePdf: [],
  exitDiff: [],
  activeAtExitDiff: [],
  closeVersion: 0,
  suspend: 0,
  restoreSuggestions: [],
  exitHtml: 0,
  showEmail: [],
  hideEmail: 0,
  syncStreamingEmail: [],
  renderEmailBody: [],
  syncHl: 0,
  autoDetect: 0,
  lineNumbers: [],
  placeholder: 0,
  overflow: 0,
  cancelledFrames: 0,
};

function resetCalls() {
  calls.sessionId = 'sess-1';
  calls.diffModeActive = false;
  calls.openPanel = 0;
  calls.renderTabs = 0;
  calls.saveCurrent = 0;
  calls.syncIndicator = 0;
  calls.syncHeader = 0;
  calls.flush = [];
  calls.releasePdf = [];
  calls.exitDiff = [];
  calls.activeAtExitDiff = [];
  calls.closeVersion = 0;
  calls.suspend = 0;
  calls.restoreSuggestions = [];
  calls.exitHtml = 0;
  calls.showEmail = [];
  calls.hideEmail = 0;
  calls.syncStreamingEmail = [];
  calls.renderEmailBody = [];
  calls.syncHl = 0;
  calls.autoDetect = 0;
  calls.lineNumbers = [];
  calls.placeholder = 0;
  calls.overflow = 0;
  calls.cancelledFrames = 0;
}

function classList(initial = []) {
  const classes = new Set(initial);
  return {
    add(...names) { names.forEach(name => classes.add(name)); },
    remove(...names) { names.forEach(name => classes.delete(name)); },
    contains(name) { return classes.has(name); },
  };
}

const elements = new Map();
const frames = new Map();
let nextFrame = 1;

function makeElement(id) {
  const el = {
    id,
    value: '',
    textContent: '',
    className: '',
    disabled: false,
    scrollTop: 0,
    scrollHeight: 80,
    style: {},
    dataset: {},
    classList: classList(),
    parentElement: null,
    children: [],
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      if (child.id) elements.set(child.id, child);
      return child;
    },
    remove() {
      if (this.parentElement) {
        this.parentElement.children = this.parentElement.children.filter(child => child !== this);
      }
      if (this.id) elements.delete(this.id);
      this.removed = true;
    },
  };
  return el;
}

function installDom() {
  elements.clear();
  const toolbar = makeElement('doc-md-toolbar');
  toolbar._syncOverflow = () => { calls.overflow += 1; };
  const pane = makeElement('doc-editor-pane');
  pane.isConnected = true;
  const title = makeElement('doc-title-input');
  const lang = makeElement('doc-language-select');
  const toggle = makeElement('overflow-doc-btn');
  const indicatorBtn = makeElement('doc-indicator-btn');
  const badge = makeElement('doc-version-badge');
  const textarea = makeElement('doc-editor-textarea');
  const stream = makeElement('doc-stream-indicator');
  stream.style.display = 'none';
  const code = makeElement('doc-editor-code');
  const codeParent = makeElement('doc-editor-code-parent');
  code.parentElement = codeParent;
  for (const el of [pane, toolbar, title, lang, toggle, indicatorBtn, badge, textarea, stream, code]) {
    elements.set(el.id, el);
  }
  globalThis.document = {
    getElementById(id) { return elements.get(id) || null; },
    createElement() { return makeElement(''); },
  };
  globalThis.getComputedStyle = () => ({ lineHeight: '20px' });
  globalThis.requestAnimationFrame = (callback) => {
    const id = nextFrame++;
    frames.set(id, callback);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => {
    if (frames.delete(id)) calls.cancelledFrames += 1;
  };
  return { pane, toolbar, title, lang, toggle, indicatorBtn, badge, textarea, stream, code, codeParent };
}

function drainFrames(limit = 40) {
  let steps = 0;
  while (frames.size && steps < limit) {
    const id = Math.min(...frames.keys());
    const callback = frames.get(id);
    frames.delete(id);
    callback();
    steps += 1;
  }
  return steps;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const syntheticModules = new Map();

function mockEvaluate(fileName, module) {
  const set = (name, value) => module.setExport(name, value);
  if (fileName === 'host.js') {
    set('sessionModule', { getCurrentSessionId() { return calls.sessionId || ''; } });
    return;
  }
  if (fileName === 'diffViewer.js') {
    set('exitDiffMode', (...args) => {
      calls.exitDiff.push(args);
      calls.activeAtExitDiff.push(live.activeDocId());
    });
    set('_diffModeActive', calls.diffModeActive);
    return;
  }
  if (fileName === 'sourceEditor.js') {
    set('syncHighlighting', () => { calls.syncHl += 1; });
    set('attemptAutoDetect', () => { calls.autoDetect += 1; });
    set('updateLineNumbers', (text) => { calls.lineNumbers.push(text); });
    return;
  }
  if (fileName === 'composerFields.js') {
    set('_showEmailFields', (...args) => { calls.showEmail.push(args); });
    set('_hideEmailFields', () => { calls.hideEmail += 1; });
    set('_syncStreamingEmailFields', (...args) => { calls.syncStreamingEmail.push(args); });
    return;
  }
  if (fileName === 'emailBody.js') {
    set('_renderStreamingEmailBody', (...args) => { calls.renderEmailBody.push(args); });
    return;
  }
  if (fileName === 'versionHistory.js') {
    set('_closeVersionPanel', () => { calls.closeVersion += 1; });
    return;
  }
  if (fileName === 'richBodyWiring.js') {
    set('_hideMarkdownVisualEditor', () => {});
    return;
  }
  if (fileName === 'documentStatus.js') {
    set('_syncEditorPlaceholder', () => { calls.placeholder += 1; });
    return;
  }
  if (fileName === 'suggestions.js') {
    set('suspendSuggestions', () => { calls.suspend += 1; });
    set('_restoreSuggestionsFromStorage', (docId) => { calls.restoreSuggestions.push(docId); });
    return;
  }
  if (fileName === 'previewModes.js') {
    set('exitHtmlPreview', () => { calls.exitHtml += 1; });
    return;
  }
  if (fileName === 'pdfTools.js') {
    set('_isFormBackedDoc', (content) => String(content || '').includes('%PDF'));
    set('_releasePdfPane', (docId) => { calls.releasePdf.push(docId); });
    return;
  }
  if (fileName === 'persistence.js') {
    set('_flushAutoSave', (docId) => { calls.flush.push(docId); });
    return;
  }
  throw new Error(`No mock exports for ${fileName}`);
}

const MOCK_EXPORTS = {
  'host.js': ['sessionModule'],
  'diffViewer.js': ['exitDiffMode', '_diffModeActive'],
  'sourceEditor.js': ['syncHighlighting', 'attemptAutoDetect', 'updateLineNumbers'],
  'composerFields.js': ['_showEmailFields', '_hideEmailFields', '_syncStreamingEmailFields'],
  'emailBody.js': ['_renderStreamingEmailBody'],
  'versionHistory.js': ['_closeVersionPanel'],
  'richBodyWiring.js': ['_hideMarkdownVisualEditor'],
  'documentStatus.js': ['_syncEditorPlaceholder'],
  'suggestions.js': ['suspendSuggestions', '_restoreSuggestionsFromStorage'],
  'previewModes.js': ['exitHtmlPreview'],
  'pdfTools.js': ['_isFormBackedDoc', '_releasePdfPane'],
  'persistence.js': ['_flushAutoSave'],
};

const live = { activeDocId() { return null; } };

function publishLiveBindings() {
  const diff = syntheticModules.get('diffViewer.js');
  if (diff) diff.setExport('_diffModeActive', calls.diffModeActive);
}

async function loadLiveStream() {
  const cache = new Map();
  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const resolved = specifier.startsWith('.')
      ? path.resolve(path.dirname(parent), specifier)
      : null;
    const fileName = resolved ? path.basename(resolved) : specifier;
    const url = resolved ? pathToFileURL(resolved).href : `synthetic:${specifier}`;
    if (cache.has(url)) return cache.get(url);
    const useMock = !resolved || Object.hasOwn(MOCK_EXPORTS, fileName) || !fs.existsSync(resolved);
    if (useMock) {
      const exportNames = MOCK_EXPORTS[fileName];
      if (!exportNames) throw new Error(`Unexpected unresolved import ${specifier} from ${parent}`);
      const mod = new SyntheticModule(exportNames, function evaluate() {
        mockEvaluate(fileName, this);
      }, { identifier: url });
      cache.set(url, mod);
      syntheticModules.set(fileName, mod);
      await mod.link(linker);
      return mod;
    }
    const mod = new SourceTextModule(fs.readFileSync(resolved, 'utf8'), {
      identifier: url,
      initializeImportMeta(meta) { meta.url = url; },
    });
    cache.set(url, mod);
    await mod.link(linker);
    return mod;
  }

  const entryUrl = pathToFileURL(entryPath).href;
  const entrySource = fs.readFileSync(entryPath, 'utf8') + '\nexport { _syncStreamDocChrome, _animateDocEdit };';
  const entry = new SourceTextModule(entrySource, {
    identifier: entryUrl,
    initializeImportMeta(meta) { meta.url = entryUrl; },
  });
  cache.set(entryUrl, entry);
  await entry.link(linker);
  await entry.evaluate();
  return {
    api: entry.namespace,
    state: cache.get(pathToFileURL(path.join(featuresDir, 'application/documentState.js')).href).namespace,
    commands: cache.get(pathToFileURL(path.join(featuresDir, 'application/documentCommands.js')).href).namespace,
  };
}

let env;
let dom;

function streamingDoc() {
  return [...env.state.docs.values()].find(doc => String(doc.id).startsWith('_streaming_')) || null;
}

describe('liveStream', () => {
  before(async () => {
    dom = installDom();
    env = await loadLiveStream();
    live.activeDocId = () => env.state.activeDocId;
    env.commands.bindDocumentCommands({
      openPanel() { calls.openPanel += 1; },
      closePanel() {},
      switchToDoc() {},
      saveCurrentToMap() { calls.saveCurrent += 1; },
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
    env.api.streamDocFinalize();
    frames.clear();
    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    env.state.setPanelOpen(false);
    dom.textarea.value = 'stale';
    dom.textarea.disabled = true;
    dom.textarea.scrollTop = 0;
    dom.title.value = '';
    dom.lang.value = '';
    dom.badge.textContent = '';
    dom.stream.style.display = 'none';
    dom.code.textContent = '';
    dom.codeParent.children = [];
    dom.toolbar.style.display = 'none';
    dom.toolbar.dataset.mode = '';
    dom.toggle.classList = classList(['toolbar-collapsed']);
    dom.indicatorBtn.classList = classList();
    elements.delete('doc-stream-cursor');
    resetCalls();
    publishLiveBindings();
  });

  it('opens a new streaming document and shows editor chrome', () => {
    const generation = env.state._documentNavigationGeneration;
    env.api.streamDocOpen('Draft', 'markdown');
    drainFrames();
    const doc = streamingDoc();
    assert.ok(doc);
    assert.equal(env.state.activeDocId, doc.id);
    assert.equal(dom.pane.dataset.activeDocumentId, doc.id);
    assert.equal(doc.title, 'Draft');
    assert.equal(doc.language, 'markdown');
    assert.equal(doc.content, '');
    assert.equal(doc.version, 1);
    assert.equal(doc.sessionId, 'sess-1');
    assert.equal(env.state._documentNavigationGeneration, generation + 1);
    assert.equal(env.state.isOpen, false);
    assert.equal(calls.openPanel, 1);
    assert.equal(calls.closeVersion, 1);
    assert.equal(calls.exitHtml, 1);
    assert.equal(calls.suspend, 1);
    assert.equal(calls.hideEmail, 1);
    assert.equal(calls.placeholder, 1);
    assert.equal(calls.syncHl, 1);
    assert.deepEqual(calls.restoreSuggestions, [doc.id]);
    assert.equal(calls.renderTabs, 1);
    assert.equal(calls.syncIndicator, 1);
    assert.equal(calls.syncHeader, 1);
    assert.equal(calls.overflow, 1);
    assert.equal(dom.textarea.disabled, false);
    assert.equal(dom.textarea.value, '');
    assert.equal(dom.title.value, 'Draft');
    assert.equal(dom.lang.value, 'markdown');
    assert.equal(dom.badge.textContent, 'v1');
    assert.equal(dom.stream.style.display, '');
    assert.equal(dom.toggle.style.display, '');
    assert.equal(dom.toggle.classList.contains('toolbar-collapsed'), false);
    assert.equal(dom.toggle.classList.contains('has-docs'), true);
    assert.equal(dom.indicatorBtn.classList.contains('visible'), true);
    assert.equal(dom.toolbar.style.display, '');
    assert.equal(dom.toolbar.dataset.mode, 'md');
    assert.equal(typeof env.api.streamDocFinalize, 'function');
  });

  it('reuses an in-progress stream instead of creating a second document', () => {
    env.api.streamDocOpen('Draft', 'markdown');
    drainFrames();
    const first = streamingDoc();
    resetCalls();
    publishLiveBindings();
    env.api.streamDocOpen('Renamed', 'python');
    drainFrames();
    assert.equal(streamingDoc().id, first.id);
    assert.equal(env.state.docs.size, 1);
    assert.equal(first.title, 'Renamed');
    assert.equal(first.language, 'python');
    assert.equal(dom.title.value, 'Renamed');
    assert.equal(dom.lang.value, 'python');
    assert.equal(calls.suspend, 0);
    assert.equal(calls.openPanel, 0);
    assert.equal(calls.renderTabs, 1);
    assert.equal(calls.showEmail.length, 0);
  });

  it('updates a background stream without taking over the active document', () => {
    env.api.streamDocOpen('Draft', 'markdown');
    const first = streamingDoc();
    env.state.registerDocument({ id: 'other', title: 'Other', language: 'markdown', content: 'x', version: 1, sessionId: 'sess-1' });
    env.state.setActiveDocId('other');
    resetCalls();
    env.api.streamDocOpen('Renamed', 'markdown');
    assert.equal(env.state.activeDocId, 'other');
    assert.equal(first.title, 'Renamed');
    assert.equal(dom.title.value, 'Draft');
    assert.equal(calls.renderTabs, 1);
    assert.equal(calls.suspend, 0);
    assert.equal(calls.saveCurrent, 0);
  });

  it('reuses a same-title document in the current session', () => {
    env.state.setPanelOpen(true);
    env.state.registerDocument({ id: 'notes', title: 'Notes', language: 'markdown', content: 'kept', version: 3, sessionId: 'sess-1' });
    env.state.registerDocument({ id: 'other', title: 'Other', language: 'markdown', content: 'typed', version: 1, sessionId: 'sess-1' });
    env.state.setActiveDocId('other');
    env.api.streamDocOpen('Notes', 'markdown');
    drainFrames();
    assert.equal(streamingDoc(), null);
    assert.equal(env.state.activeDocId, 'notes');
    assert.equal(env.state.docs.size, 2);
    assert.equal(calls.saveCurrent, 1);
    assert.deepEqual(calls.flush, ['other']);
    assert.deepEqual(calls.releasePdf, ['other']);
    assert.equal(calls.openPanel, 0);
    assert.equal(dom.textarea.value, '');
    assert.equal(env.state.docs.get('notes').content, 'kept');
  });

  it('does not reuse the same title from another session', () => {
    env.state.registerDocument({ id: 'foreign', title: 'Notes', language: 'markdown', content: 'other', version: 1, sessionId: 'sess-2' });
    env.api.streamDocOpen('Notes', 'markdown');
    const created = streamingDoc();
    assert.ok(created);
    assert.notEqual(created.id, 'foreign');
    assert.equal(env.state.docs.has('foreign'), true);
  });

  it('skips the previous-document save when that document is already active', () => {
    env.state.setPanelOpen(true);
    env.state.registerDocument({ id: 'notes', title: 'Notes', language: 'markdown', content: 'kept', version: 1, sessionId: 'sess-1' });
    env.state.setActiveDocId('notes');
    env.api.streamDocOpen('Notes', 'markdown');
    assert.equal(env.state.activeDocId, 'notes');
    assert.equal(calls.saveCurrent, 0);
    assert.deepEqual(calls.flush, []);
    assert.deepEqual(calls.releasePdf, []);
  });

  it('discards an open diff before changing the active document', () => {
    env.state.registerDocument({ id: 'current', title: 'Current', language: 'markdown', content: 'old', version: 1, sessionId: 'sess-1' });
    env.state.setActiveDocId('current');
    calls.diffModeActive = true;
    publishLiveBindings();
    env.api.streamDocOpen('Fresh', 'markdown');
    assert.deepEqual(calls.exitDiff, [[true]]);
    assert.deepEqual(calls.activeAtExitDiff, ['current']);
    assert.equal(env.state.activeDocId, streamingDoc().id);
  });

  it('shows email fields while streaming an email', () => {
    env.api.streamDocOpen('Reply', 'email');
    const doc = streamingDoc();
    assert.equal(calls.hideEmail, 0);
    assert.equal(calls.showEmail.length, 1);
    assert.equal(calls.showEmail[0][0], doc);
    assert.equal(dom.lang.value, 'email');
  });

  it('applies streamed text, scrolls, and debounces highlighting', async () => {
    env.api.streamDocOpen('Draft', 'markdown');
    const doc = streamingDoc();
    resetCalls();
    env.api.streamDocDelta('hello');
    assert.equal(doc.content, 'hello');
    assert.equal(dom.textarea.value, 'hello');
    assert.equal(dom.textarea.scrollTop, dom.textarea.scrollHeight);
    assert.equal(dom.code.textContent, 'hello\n');
    assert.deepEqual(calls.lineNumbers, ['hello']);
    assert.equal(elements.get('doc-stream-cursor').textContent, '\u258F');
    assert.equal(calls.syncHl, 0);
    await wait(250);
    assert.equal(calls.syncHl, 1);
  });

  it('keeps a background stream in the map without painting the textarea', () => {
    env.api.streamDocOpen('Draft', 'markdown');
    const doc = streamingDoc();
    env.state.setActiveDocId('away');
    resetCalls();
    env.api.streamDocDelta('hidden');
    assert.equal(doc.content, 'hidden');
    assert.equal(dom.textarea.value, '');
    assert.equal(calls.lineNumbers.length, 0);
  });

  it('updates email fields without writing the source textarea', () => {
    env.api.streamDocOpen('Reply', 'email');
    const doc = streamingDoc();
    resetCalls();
    env.api.streamDocDelta('To: a@b.c\n---\nHello');
    assert.equal(doc.content, 'To: a@b.c\n---\nHello');
    assert.equal(dom.textarea.value, '');
    assert.equal(calls.syncStreamingEmail.length, 1);
    assert.equal(calls.syncStreamingEmail[0][0], doc);
    assert.equal(calls.lineNumbers.length, 0);
  });

  it('does nothing when a delta arrives before a stream starts', () => {
    env.api.streamDocDelta('orphan');
    assert.equal(env.state.docs.size, 0);
    assert.equal(dom.textarea.value, 'stale');
    assert.equal(calls.syncHl, 0);
  });

  it('finalizes the stream, drops the debounced highlight, and renders an email body', async () => {
    env.api.streamDocOpen('Reply', 'markdown');
    const doc = streamingDoc();
    resetCalls();
    env.api.streamDocDelta('partial');
    assert.equal(calls.syncHl, 0);
    doc.language = 'email';
    doc.content = 'To: a@b.c\nSubject: Hi\n---\nHello body';
    const cursor = elements.get('doc-stream-cursor');
    const finished = env.api.streamDocFinalize();
    assert.equal(finished, doc.id);
    assert.equal(env.api.streamDocFinalize(), null);
    assert.equal(calls.renderEmailBody.length, 1);
    assert.equal(calls.renderEmailBody[0][0], 'Hello body');
    assert.deepEqual(calls.renderEmailBody[0][1], { immediate: true });
    assert.equal(dom.stream.style.display, 'none');
    assert.equal(cursor.removed, true);
    assert.equal(calls.syncHl, 1);
    assert.equal(calls.autoDetect, 1);
    await wait(250);
    assert.equal(calls.syncHl, 1);
    env.api.streamDocDelta('later');
    assert.equal(doc.content, 'To: a@b.c\nSubject: Hi\n---\nHello body');
  });

  it('animates a regional edit through deletion and insertion', () => {
    dom.textarea.value = 'abcdef';
    dom.stream.style.display = 'none';
    env.api._animateDocEdit(dom.textarea, 'abXYZ');
    const steps = drainFrames();
    assert.ok(steps >= 4);
    assert.equal(dom.textarea.value, 'abXYZ');
    assert.equal(dom.code.textContent, 'abXYZ\n');
    assert.equal(dom.stream.style.display, 'none');
    assert.equal(elements.has('doc-stream-cursor'), false);
    assert.equal(calls.syncHl, 1);
    assert.equal(calls.lineNumbers.at(-1), 'abXYZ');
  });

  it('cancels an in-flight edit animation when a new one starts', () => {
    dom.textarea.value = 'one';
    env.api._animateDocEdit(dom.textarea, 'two');
    assert.equal(frames.size, 1);
    env.api._animateDocEdit(dom.textarea, 'three');
    assert.equal(calls.cancelledFrames, 1);
    assert.equal(frames.size, 1);
    drainFrames();
    assert.equal(dom.textarea.value, 'three');
  });

  it('sets toolbar mode for markdown, pdf, code, and plain documents', () => {
    env.api._syncStreamDocChrome(null);
    assert.equal(calls.syncHeader, 0);

    env.api._syncStreamDocChrome({ language: 'markdown', content: '' });
    drainFrames();
    assert.equal(dom.toolbar.style.display, '');
    assert.equal(dom.toolbar.dataset.mode, 'md');
    assert.equal(calls.overflow, 1);
    assert.equal(calls.syncHeader, 1);

    env.api._syncStreamDocChrome({ language: 'text', content: '%PDF-1.7' });
    assert.equal(dom.toolbar.dataset.mode, 'pdf');

    env.api._syncStreamDocChrome({ language: 'python', content: 'print(1)' });
    assert.equal(dom.toolbar.dataset.mode, 'code');

    env.api._syncStreamDocChrome({ language: 'text', content: 'plain' });
    assert.equal(dom.toolbar.style.display, 'none');
    assert.equal(dom.toolbar.dataset.mode, '');
  });

  it('keeps the bounded live-stream export surface', () => {
    const source = fs.readFileSync(entryPath, 'utf8');
    const ownerExports = [...source.matchAll(/^export function (\w+)/gm)].map(match => match[1]).sort();
    assert.deepEqual(ownerExports, ['streamDocDelta', 'streamDocFinalize', 'streamDocOpen']);
    assert.equal(typeof env.api.streamDocFinalize, 'function');
    assert.equal(typeof env.api.streamDocOpen, 'function');
    assert.equal(typeof env.api.streamDocDelta, 'function');
  });
});

// tests/documentVersionHistory.behavior.mjs
/**
 * Behavior tests for ui/history/versionHistory.
 * Run through tests/documentVersionHistory.test.mjs.
 */

import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const featuresDir = path.join(repoRoot, 'frontend/src/features/documents');
const entryPath = path.join(featuresDir, 'ui/history/versionHistory.js');

const calls = {
  saveCurrent: 0,
  switchTo: [],
  cancelSave: [],
  scheduleSave: [],
  saveState: [],
  releasePdf: [],
  exitDiff: [],
  syncHl: 0,
  errors: [],
  toasts: [],
};

function resetCalls() {
  calls.saveCurrent = 0;
  calls.switchTo = [];
  calls.cancelSave = [];
  calls.scheduleSave = [];
  calls.saveState = [];
  calls.releasePdf = [];
  calls.exitDiff = [];
  calls.syncHl = 0;
  calls.errors = [];
  calls.toasts = [];
}

function classList(initial = []) {
  const classes = new Set(initial);
  return {
    add(...names) { names.forEach(name => classes.add(name)); },
    remove(...names) { names.forEach(name => classes.delete(name)); },
    contains(name) { return classes.has(name); },
  };
}

function makeElement(id) {
  return {
    id,
    value: '',
    readOnly: false,
    innerHTML: '',
    style: {},
    classList: classList(),
    parentElement: null,
    children: [],
    contains() { return false; },
    focus() { this.focused = true; },
    appendChild(child) { child.parentElement = this; return child; },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

let listeners = [];
let dom;

function installDom() {
  listeners = [];
  const panel = makeElement('doc-version-panel');
  panel.classList.add('hidden');
  const pane = makeElement('doc-editor-pane');
  const textarea = makeElement('doc-editor-textarea');
  const list = makeElement('doc-version-list');
  const sidebar = makeElement('sidebar');
  const elements = new Map([
    ['doc-version-panel', panel],
    ['doc-editor-pane', pane],
    ['doc-editor-textarea', textarea],
    ['doc-version-list', list],
    ['sidebar', sidebar],
  ]);
  const body = makeElement('body');
  panel.parentElement = body;
  globalThis.document = {
    body,
    getElementById(id) { return elements.get(id) || null; },
    addEventListener(type, handler, capture) { listeners.push({ type, handler, capture }); },
    removeEventListener(type, handler) {
      listeners = listeners.filter(entry => entry.handler !== handler || entry.type !== type);
    },
    querySelector() { return null; },
  };
  globalThis.window = { innerWidth: 500 };
  globalThis.CSS = { escape(value) { return String(value); } };
  return { panel, pane, textarea, list };
}

const MOCKS = {
  'host.js': {
    names: ['uiModule', 'markdownModule'],
    evaluate(module) {
      module.setExport('uiModule', {
        showError(message) { calls.errors.push(message); },
        showToast(message) { calls.toasts.push(message); },
      });
      module.setExport('markdownModule', {
        sanitizeAllowedHtml(value) { return String(value || ''); },
        mdToHtml(value) { return String(value || ''); },
      });
    },
  },
  'diffViewer.js': {
    names: ['exitDiffMode', '_diffModeActive'],
    evaluate(module) {
      module.setExport('exitDiffMode', (...args) => { calls.exitDiff.push(args); });
      module.setExport('_diffModeActive', false);
    },
  },
  'sourceEditor.js': {
    names: ['syncHighlighting'],
    evaluate(module) {
      module.setExport('syncHighlighting', () => { calls.syncHl += 1; });
    },
  },
  'pdfTools.js': {
    names: ['_releasePdfPane'],
    evaluate(module) {
      module.setExport('_releasePdfPane', (docId) => { calls.releasePdf.push(docId); });
    },
  },
  'persistence.js': {
    names: ['_cancelAutoSave', '_scheduleAutoSave', '_setDocumentSaveState', '_markDocumentDirty'],
    evaluate(module) {
      module.setExport('_cancelAutoSave', (...args) => { calls.cancelSave.push(args); });
      module.setExport('_scheduleAutoSave', (...args) => { calls.scheduleSave.push(args); });
      module.setExport('_setDocumentSaveState', (...args) => { calls.saveState.push(args); });
      module.setExport('_markDocumentDirty', () => {});
    },
  },
};

async function loadVersionHistory() {
  const cache = new Map();
  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const resolved = specifier.startsWith('.')
      ? path.resolve(path.dirname(parent), specifier)
      : null;
    const fileName = resolved ? path.basename(resolved) : specifier;
    const url = resolved ? pathToFileURL(resolved).href : `synthetic:${specifier}`;
    if (cache.has(url)) return cache.get(url);
    const mock = MOCKS[fileName];
    if (!resolved || mock || !fs.existsSync(resolved)) {
      if (!mock) throw new Error(`Unexpected import ${specifier} from ${parent}`);
      const mod = new SyntheticModule(mock.names, function evaluate() {
        mock.evaluate(this);
      }, { identifier: url });
      cache.set(url, mod);
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
  const entrySource = fs.readFileSync(entryPath, 'utf8') + '\nexport { previewVersion, restoreVersion };';
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

describe('versionHistory', () => {
  before(async () => {
    dom = installDom();
    env = await loadVersionHistory();
    env.commands.bindDocumentCommands({
      openPanel() {},
      closePanel() {},
      switchToDoc(...args) { calls.switchTo.push(args); },
      saveCurrentToMap() { calls.saveCurrent += 1; },
      renderTabs() {},
      _syncDocIndicator() {},
      _syncHeaderActions() {},
      showEmptyState() {},
      _showLoadingOverlay() {},
      _hideLoadingOverlay() {},
      _minimizeNotesForDocumentOpen() {},
      _ensureDocChipRegistered() {},
    });
    env.state.setApiBase('http://app');
  });

  beforeEach(() => {
    resetCalls();
    listeners = [];
    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    env.state.setPanelOpen(true);
    env.state.registerDocument({
      id: 'doc-1',
      title: 'Notes',
      language: 'markdown',
      content: 'current',
      _persistedContent: 'current',
      version: 1,
      sessionId: 'sess',
    });
    dom.textarea.value = 'current';
    dom.textarea.readOnly = false;
    dom.panel.classList.add('hidden');
    dom.list.innerHTML = '';
    globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  });

  it('does not open history without an active document', async () => {
    env.state.setActiveDocId(null);
    let fetched = false;
    globalThis.fetch = async () => { fetched = true; return { ok: true, json: async () => [] }; };
    await env.api.toggleVersionHistory();
    assert.equal(fetched, false);
    assert.equal(calls.saveCurrent, 0);
  });

  it('loads versions, escapes summaries, and closes from an outside click', async () => {
    env.state.setActiveDocId('doc-1');
    globalThis.fetch = async (url) => {
      assert.equal(url, 'http://app/api/document/doc-1/versions');
      return {
        ok: true,
        json: async () => ([
          { version_number: 3, content: 'next', source: 'ai', created_at: '2026-01-02T00:00:00Z', summary: '<b>hi' },
          { version_number: 2, content: 'prev', source: 'user', created_at: '2026-01-01T00:00:00Z', summary: '' },
        ]),
      };
    };
    await env.api.toggleVersionHistory();
    assert.equal(calls.saveCurrent, 1);
    assert.equal(dom.panel.classList.contains('hidden'), false);
    assert.match(dom.list.innerHTML, /v3/);
    assert.match(dom.list.innerHTML, /&lt;b&gt;hi/);
    assert.match(dom.list.innerHTML, /doc-version-restore/);
    assert.equal(listeners.some(entry => entry.type === 'click' && entry.capture === true), true);

    listeners.find(entry => entry.type === 'click').handler({
      target: { closest() { return null; } },
    });
    assert.equal(dom.panel.classList.contains('hidden'), true);
    assert.equal(env.api._versionHistory, null);
    assert.equal(listeners.some(entry => entry.type === 'click'), false);
  });

  it('drops a preview that loses the history generation', async () => {
    env.state.setActiveDocId('doc-1');
    globalThis.fetch = async () => ({ ok: true, json: async () => [] });
    await env.api.toggleVersionHistory();
    let release;
    globalThis.fetch = () => new Promise(resolve => {
      release = () => resolve({ ok: true, json: async () => ({ content: 'late preview' }) });
    });
    const pending = env.api.previewVersion(2);
    env.api._closeVersionPanel();
    release();
    await pending;
    assert.equal(dom.textarea.value, 'current');
    assert.equal(dom.textarea.readOnly, false);
    assert.equal(calls.syncHl, 0);
  });

  it('restores an unchanged document and keeps a document that changed while restoring', async () => {
    env.state.setActiveDocId('doc-1');
    globalThis.fetch = async () => ({ ok: true, json: async () => [] });
    await env.api.toggleVersionHistory();

    globalThis.fetch = async (url, opts) => {
      if (String(url).endsWith('/versions')) return { ok: true, json: async () => [] };
      assert.equal(url, 'http://app/api/document/doc-1/restore/2');
      assert.equal(opts.method, 'POST');
      return { ok: true, json: async () => ({ version_count: 4, current_content: 'restored', content: 'restored' }) };
    };
    await env.api.restoreVersion(2);
    const doc = env.state.docs.get('doc-1');
    assert.equal(doc.content, 'restored');
    assert.equal(doc._persistedContent, 'restored');
    assert.equal(doc.version, 4);
    assert.deepEqual(calls.saveState[0], ['saved', 'doc-1']);
    assert.deepEqual(calls.switchTo[0][0], 'doc-1');
    assert.equal(calls.switchTo[0][1].captureCurrent, false);
    assert.deepEqual(calls.releasePdf, ['doc-1']);
    assert.deepEqual(calls.toasts, ['Restored to v2']);
    assert.equal(calls.cancelSave.length > 0, true);

    doc.content = 'restored';
    calls.switchTo = [];
    calls.saveState = [];
    calls.scheduleSave = [];
    globalThis.fetch = async (url) => {
      if (String(url).endsWith('/versions')) return { ok: true, json: async () => [] };
      doc.content = 'typed during restore';
      return { ok: true, json: async () => ({ version_count: 5, current_content: 'server', content: 'server' }) };
    };
    await env.api.restoreVersion(2);
    assert.equal(doc.content, 'typed during restore');
    assert.equal(doc._persistedContent, 'server');
    assert.deepEqual(calls.saveState[0], ['dirty', 'doc-1']);
    assert.equal(calls.scheduleSave.length, 1);
    assert.equal(calls.switchTo.length, 0);

    globalThis.fetch = async () => ({ ok: false, status: 503 });
    await env.api.restoreVersion(2);
    assert.equal(doc.content, 'typed during restore');
    assert.equal(doc._persistedContent, 'server');
  });

  it('shows a load failure without replacing a newer history view', async () => {
    env.state.setActiveDocId('doc-1');
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
    await env.api.toggleVersionHistory();
    assert.match(dom.list.innerHTML, /Failed to load versions/);

    globalThis.fetch = async () => { throw new Error('offline'); };
    await env.api.previewVersion(1);
    assert.deepEqual(calls.errors, ['Failed to preview version']);
  });
});

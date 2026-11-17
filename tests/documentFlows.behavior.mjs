import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';
import { installDom, mount, clearDom } from './documentLaneDom.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const entryPath = path.join(root, 'frontend/src/features/documents/ui/documentFlows.js');
const entrySource = fs.readFileSync(entryPath, 'utf8');
const calls = { switch: [], open: 0, close: 0, overlays: 0, errors: [], tabs: 0, adds: [] };
const docs = new Map();
const inFlightDeletes = new Map();
const deletedIds = new Set();
const closingDocs = new Map();
let generation = 0;
let active = null;
let open = false;
let apiBase = 'https://odysseus.test';
let stateModule;

const stateValues = {
  API_BASE: apiBase, _closingDocs: closingDocs, _deletedDocIds: new Set(), _documentNavigationGeneration: generation,
  _inFlightDeletes: inFlightDeletes, _lastSessionId: 'session-1', _minimizedDocId: null, activeDocId: active,
  docs, isOpen: open,
  beginDelete(id, task) { inFlightDeletes.set(id, task); }, endDelete(id) { inFlightDeletes.delete(id); },
  bumpNavigationGeneration() { generation += 1; stateModule.setExport('_documentNavigationGeneration', generation); return generation; },
  markDeleted(id) { deletedIds.add(id); }, registerDocument(doc) { docs.set(doc.id, doc); }, removeDocument(id) { docs.delete(id); },
  releaseClosingDraft(id) { return closingDocs.delete(id); }, retainClosingDraft(id, doc) { closingDocs.set(id, doc); },
  setActiveDocId(id) { active = id; stateModule.setExport('activeDocId', id); }, setLastSessionId(id) { stateModule.setExport('_lastSessionId', id); },
  setMinimizedDocId(id) { stateModule.setExport('_minimizedDocId', id); }, setPanelOpen(value) { open = value; stateModule.setExport('isOpen', value); },
};
const commands = {
  _ensureDocChipRegistered() {}, _hideLoadingOverlay() { calls.overlays--; }, _minimizeNotesForDocumentOpen() {},
  _showLoadingOverlay() { calls.overlays++; }, _syncDocIndicator() {}, closePanel() { calls.close++; open = false; },
  openPanel() { calls.open++; open = true; stateModule.setExport('isOpen', true); }, renderTabs() { calls.tabs++; }, saveCurrentToMap() {}, showEmptyState() {},
  switchToDoc(id) { calls.switch.push(id); active = id; stateModule.setExport('activeDocId', id); calls.overlays--; },
};
const generic = {
  '_docActiveKey': id => `active-${id}`, '_docMinimizedKey': id => `min-${id}`, '_docOpenKey': id => `open-${id}`,
  '_forgetActiveDoc'() {}, '_markDocVisibleState'() {}, addDocToTabs(doc, sessionId) { const d = { ...doc, sessionId }; docs.set(d.id, d); calls.adds.push(d.id); },
  '_cancelAutoSave'() {}, '_pendingAutoSaves': new Map(), '_saveClosingDraft'() {}, '_scheduleAutoSave'() {}, saveDocument: async () => {},
  '_isRichTextLang': () => false, '_parseEmailHeader': () => ({}), autoTitleFromContent() {}, attemptAutoDetect() {}, syncHighlighting() {},
  '_clearEmailLocalDraft'() {}, '_closeVersionPanel'() {}, Modals: { isRegistered: () => false, isMinimized: () => false, unregister() {}, minimize() {} },
  sessionModule: { getCurrentSessionId: () => 'session-1' }, uiModule: { showError: m => calls.errors.push(m), showToast() {}, styledConfirm: async () => true },
  _cancelPdfPaneSave() {}, _cleanupPdfDocState() {}, _hasPendingPdfPaneSave: () => false, _releasePdfPane() {}, _schedulePdfPaneSave() {}, _syncPdfPaneToDoc() {},
  _emailRichbodyActive: () => null, _richTextContentToHtml: x => x, _syncEmailRichbody() {}, _syncEditorPlaceholder() {}, suspendSuggestions() {},
};

async function loadModule() {
  const cache = new Map();
  async function linker(specifier, parentModule) {
    const parent = fileURLToPath(parentModule.identifier);
    const target = path.resolve(path.dirname(parent), specifier);
    const name = path.basename(target), url = pathToFileURL(target).href;
    if (cache.has(url)) return cache.get(url);
    let exports;
    if (name === 'documentState.js') exports = stateValues;
    else if (name === 'documentCommands.js') exports = commands;
    else {
      const escaped = specifier.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const clause = entrySource.match(new RegExp(`import\\s*\\{([^}]+)\\}\\s*from\\s*['"]${escaped}['"]`));
      const names = clause ? clause[1].split(',').map(x => x.trim().split(/\\s+as\\s+/)[0]) : [];
      exports = Object.fromEntries(names.map(key => [key, key in generic ? generic[key] : (() => {})]));
    }
    const names = Object.keys(exports);
    const mod = new SyntheticModule(names, function init() { for (const key of names) this.setExport(key, exports[key]); }, { identifier: url });
    cache.set(url, mod); if (name === 'documentState.js') stateModule = mod; await mod.link(linker); return mod;
  }
  const url = pathToFileURL(entryPath).href;
  const entry = new SourceTextModule(fs.readFileSync(entryPath, 'utf8'), { identifier: url });
  await entry.link(linker); await entry.evaluate(); return entry.namespace;
}

let api;
describe('documentFlows navigation', () => {
  before(async () => { installDom(); api = await loadModule(); });
  beforeEach(() => {
    clearDom(); docs.clear(); inFlightDeletes.clear(); deletedIds.clear(); calls.switch.length = 0; calls.adds.length = 0; calls.errors.length = 0; calls.open = calls.close = calls.tabs = calls.overlays = 0;
    generation = 0; active = null; open = false; stateModule.setExport('activeDocId', null); stateModule.setExport('isOpen', false);
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ id: 'loaded', session_id: 'session-1', is_active: true, content: 'fresh' }) });
  });
  it('loads a document and switches only after a successful response', async () => {
    const result = await api.loadDocument('loaded');
    assert.equal(result, true);
    assert.deepEqual(calls.adds, ['loaded']);
    assert.deepEqual(calls.switch, ['loaded']);
    assert.equal(calls.overlays, 0);
  });
  it('ignores a stale navigation response without registering its document', async () => {
    let resolveFetch;
    globalThis.fetch = () => new Promise(resolve => { resolveFetch = resolve; });
    const pending = api.loadDocument('stale');
    stateValues.bumpNavigationGeneration();
    resolveFetch({ ok: true, json: async () => ({ id: 'stale', session_id: 'session-1' }) });
    assert.equal(await pending, false);
    assert.equal(docs.has('stale'), false);
    assert.deepEqual(calls.switch, []);
  });

  it('preserves text typed into the empty editor while document creation is pending', async () => {
    const textarea = mount('doc-editor-textarea', 'textarea', { value: 'typed while waiting', disabled: true, focus() { this.focused = true; } });
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ id: 'created', session_id: 'session-1' }) });
    await api.createDocument('session-1');
    assert.equal(docs.get('created').content, 'typed while waiting');
    assert.equal(textarea.value, 'typed while waiting');
    assert.equal(textarea.focused, true);
    assert.deepEqual(calls.switch, ['created']);
  });
  it('reconciles document created during navigation without stealing focus or orphaning document', async () => {
    let resolveCreate;
    globalThis.fetch = () => new Promise(resolve => { resolveCreate = resolve; });
    const pendingCreate = api.createDocument('session-1');
    stateValues.bumpNavigationGeneration();
    resolveCreate({ ok: true, json: async () => ({ id: 'nav-raced', session_id: 'session-1', title: 'New Doc' }) });
    await pendingCreate;
    assert.equal(docs.has('nav-raced'), true);
    assert.equal(calls.adds.includes('nav-raced'), true);
    assert.equal(calls.switch.includes('nav-raced'), false);
    assert.ok(calls.tabs > 0);
  });
  it('registers stale automatic creation with its own content after switching documents', async () => {
    const textarea = mount('doc-editor-textarea', 'textarea', { value: 'Draft A' });
    let resolveCreate;
    globalThis.fetch = () => new Promise(resolve => { resolveCreate = resolve; });
    const pending = api._autoCreateFromInput('Draft A');
    stateValues.bumpNavigationGeneration();
    stateValues.setActiveDocId('doc-b');
    docs.set('doc-b', { id: 'doc-b', content: 'Private B', sessionId: 'session-1' });
    textarea.value = 'Private B';
    resolveCreate({ ok: true, json: async () => ({ id: 'doc-a', content: 'Draft A', session_id: 'session-1' }) });
    await pending;
    assert.equal(docs.get('doc-a').content, 'Draft A');
    assert.equal(docs.get('doc-a').sessionId, 'session-1');
    assert.equal(docs.get('doc-b').content, 'Private B');
    assert.equal(textarea.value, 'Private B');
    assert.equal(stateModule.namespace.activeDocId, 'doc-b');
    assert.deepEqual(calls.switch, []);
    assert.deepEqual(calls.adds, ['doc-a']);
  });
  it('quietly closes a nonempty tab and clears the active document when no tab remains', async () => {
    docs.set('close-me', { id: 'close-me', content: 'keep this draft', sessionId: 'session-1' });
    active = 'close-me'; stateModule.setExport('activeDocId', active);
    await api.closeTab('close-me');
    assert.equal(docs.has('close-me'), false);
    assert.equal(stateModule.namespace.activeDocId, null);
    assert.equal(calls.close, 1);
    assert.equal(deletedIds.has('close-me'), false);
  });
  it('deletes through the authoritative tombstone and document-removal operations', async () => {
    docs.set('delete-me', { id: 'delete-me', title: 'Delete me', content: '', sessionId: 'session-1' });
    active = 'delete-me'; stateModule.setExport('activeDocId', active);
    await api.deleteActiveDocument();
    assert.equal(docs.has('delete-me'), false);
    assert.equal(deletedIds.has('delete-me'), true);
    assert.equal(inFlightDeletes.has('delete-me'), false);
    assert.equal(calls.close, 1);
  });
  it('clears the loading overlay and reports a not-found response', async () => {
    globalThis.fetch = async () => ({ ok: false, status: 404 });
    assert.equal(await api.loadDocument('missing'), false);
    assert.equal(calls.overlays, 0);
    assert.match(calls.errors[0], /Document not found/);
  });
});

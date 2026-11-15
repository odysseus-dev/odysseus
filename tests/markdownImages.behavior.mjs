import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';
import { installDom, mount, clearDom } from './documentLaneDom.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const entryPath = path.join(root, 'frontend/src/features/documents/ui/editor/markdownImages.js');
const calls = { autosave: [], errors: [], toasts: [], rich: [], previews: [], titles: [], lines: [] };
const state = { API_BASE: 'https://odysseus.test', activeDocId: 'doc-1', language: 'markdown', emailRich: false };
const ui = { showError: value => calls.errors.push(value), showToast: value => calls.toasts.push(value) };
const mockExports = {
  'documentState.js': { API_BASE: state.API_BASE, activeDocId: state.activeDocId },
  'persistence.js': { _scheduleAutoSave: (...args) => calls.autosave.push(args) },
  'documentKinds.js': {
    _isMarkdownImageFile: file => file.type?.startsWith('image/'),
    _isRichTextLang: language => language === 'richtext',
    _markdownImageAlt: name => String(name || '').replace(/[\[\]]/g, ''),
  },
  'documentActions.js': { autoTitleFromContent: value => calls.titles.push(value) },
  'host.js': { uiModule: ui },
  'documentStatus.js': { _activeDocLanguage: () => state.language },
  'imageOps.js': { _insertRichTextImages: files => calls.rich.push(files) },
  'richBodySync.js': { _emailRichbodyActive: () => state.emailRich },
  'sourceEditor.js': {
    scheduleSourceEditorWork: (...args) => calls.titles.push(args),
    _refreshMarkdownPreviewIfVisible: (...args) => calls.previews.push(args),
    _replaceRange(ta, start, end, value) {
      ta.value = ta.value.slice(0, start) + value + ta.value.slice(end);
      ta.selectionStart = ta.selectionEnd = start + value.length;
    },
    syncHighlighting() {}, updateLineNumbers: value => calls.lines.push(value),
  },
};

async function loadModule() {
  const cache = new Map();
  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const target = path.resolve(path.dirname(parent), specifier);
    const name = path.basename(target);
    const url = pathToFileURL(target).href;
    if (cache.has(url)) return cache.get(url);
    if (mockExports[name]) {
      const values = mockExports[name];
      const names = Object.keys(values);
      const mod = new SyntheticModule(names, function init() {
        for (const key of names) this.setExport(key, values[key]);
      }, { identifier: url });
      cache.set(url, mod);
      await mod.link(linker);
      return mod;
    }
    if (!fs.existsSync(target)) throw new Error(`Unexpected import ${specifier}`);
    const mod = new SourceTextModule(fs.readFileSync(target, 'utf8'), { identifier: url });
    cache.set(url, mod);
    await mod.link(linker);
    return mod;
  }
  const url = pathToFileURL(entryPath).href;
  const entry = new SourceTextModule(fs.readFileSync(entryPath, 'utf8'), { identifier: url });
  await entry.link(linker);
  await entry.evaluate();
  return entry.namespace;
}

let api;
describe('markdownImages', () => {
  before(async () => { installDom(); api = await loadModule(); });
  beforeEach(() => {
    clearDom();
    mount('doc-editor-pane', 'div', { isConnected: true, dataset: { activeDocumentId: 'doc-1' } });
    for (const key of Object.keys(calls)) calls[key].length = 0;
    state.language = 'markdown'; state.emailRich = false;
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ files: [{ id: 'up 1', name: 'map[1].png' }] }) });
  });

  it('uploads markdown images and schedules insertion autosave', async () => {
    const ta = mount('doc-editor-textarea', 'textarea', { value: 'before after', selectionStart: 7, selectionEnd: 7, scrollHeight: 80, focus() { this.focused = true; } });
    mount('doc-editor-code', 'pre', { dataset: {} });
    const file = { type: 'image/png', name: 'map[1].png' };
    await api._uploadMarkdownImages([file]);
    assert.match(ta.value, /!\[map1\.png\]\(\/api\/upload\/up%201\)/);
    assert.equal(ta.selectionStart, ta.selectionEnd);
    assert.equal(ta.focused, true);
    assert.deepEqual(calls.autosave, [['doc-1', 800]]);
    assert.deepEqual(calls.toasts, ['Image inserted']);
    assert.equal(calls.previews.length, 1);
    assert.deepEqual(calls.titles, [[ta.value, 'doc-1']]);
  });

  it('rejects missing or unsupported image selections before upload', async () => {
    await api._uploadMarkdownImages([]);
    assert.deepEqual(calls.errors, ['Choose an image file']);
    state.language = 'python';
    await api._uploadMarkdownImages([{ type: 'image/png', name: 'x.png' }]);
    assert.equal(calls.errors[1], 'Switch the document to Markdown or Rich Text before inserting images');
    assert.equal(calls.autosave.length, 0);
  });

  it('reports upload failures and empty upload responses', async () => {
    globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: 'denied' }) });
    await api._uploadMarkdownImages([{ type: 'image/png', name: 'x.png' }]);
    assert.deepEqual(calls.errors, ['Failed to insert image']);
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ files: [] }) });
    await api._uploadMarkdownImages([{ type: 'image/png', name: 'x.png' }]);
    assert.equal(calls.errors.length, 2);
    assert.equal(calls.autosave.length, 0);
  });

  it('routes rich text image uploads to the rich editor insertion operation', async () => {
    state.language = 'richtext';
    await api._uploadMarkdownImages([{ type: 'image/png', name: 'x.png' }]);
    assert.equal(calls.rich.length, 1);
    assert.equal(calls.autosave.length, 0);
  });
});

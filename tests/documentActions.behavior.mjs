/**
 * Behavior tests for ui/documentActions.
 * Run through tests/documentActions.test.mjs.
 */

import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';
import { installDom, mount, clearDom } from './documentLaneDom.mjs';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const featuresDir = path.join(repoRoot, 'frontend/src/features/documents');
const entryPath = path.join(featuresDir, 'ui/documentActions.js');

const calls = {
  tabs: 0,
  htmlPreview: 0,
  copied: [],
  toasts: [],
  runner: [],
  fetches: [],
};

function resetCalls() {
  calls.tabs = 0;
  calls.htmlPreview = 0;
  calls.copied = [];
  calls.toasts = [];
  calls.runner = [];
  calls.fetches = [];
}

const host = {
  uiModule: {
    async copyToClipboard(value) { calls.copied.push(value); },
    showToast(message) { calls.toasts.push(message); },
  },
  codeRunnerModule: {
    runServer(code, panel, lang) { calls.runner.push({ kind: 'server', code, panel, lang }); },
    runJavaScript(code, panel) { calls.runner.push({ kind: 'javascript', code, panel }); },
  },
};

async function loadDocumentActions() {
  const cache = new Map();
  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const resolved = specifier.startsWith('.')
      ? path.resolve(path.dirname(parent), specifier)
      : null;
    const fileName = resolved ? path.basename(resolved) : specifier;
    const url = resolved ? pathToFileURL(resolved).href : `synthetic:${specifier}`;
    if (cache.has(url)) return cache.get(url);
    if (fileName === 'host.js') {
      const mod = new SyntheticModule(['uiModule', 'codeRunnerModule'], function evaluate() {
        this.setExport('uiModule', host.uiModule);
        this.setExport('codeRunnerModule', host.codeRunnerModule);
      }, { identifier: url });
      cache.set(url, mod);
      await mod.link(linker);
      return mod;
    }
    if (fileName === 'previewModes.js') {
      const mod = new SyntheticModule(['toggleHtmlPreview'], function evaluate() {
        this.setExport('toggleHtmlPreview', () => { calls.htmlPreview += 1; });
      }, { identifier: url });
      cache.set(url, mod);
      await mod.link(linker);
      return mod;
    }
    if (!resolved || !fs.existsSync(resolved)) {
      throw new Error(`Unexpected unresolved import ${specifier} from ${parent}`);
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
  const entry = new SourceTextModule(fs.readFileSync(entryPath, 'utf8'), {
    identifier: entryUrl,
    initializeImportMeta(meta) { meta.url = entryUrl; },
  });
  cache.set(entryUrl, entry);
  await entry.link(linker);
  await entry.evaluate();
  const stateUrl = pathToFileURL(path.join(featuresDir, 'application/documentState.js')).href;
  const commandsUrl = pathToFileURL(path.join(featuresDir, 'application/documentCommands.js')).href;
  return {
    api: entry.namespace,
    state: cache.get(stateUrl).namespace,
    commands: cache.get(commandsUrl).namespace,
  };
}

let env;

describe('documentActions', () => {
  before(async () => {
    installDom();
    env = await loadDocumentActions();
    env.commands.bindDocumentCommands({
      openPanel() {},
      closePanel() {},
      switchToDoc() {},
      saveCurrentToMap() {},
      renderTabs() { calls.tabs += 1; },
      _syncDocIndicator() {},
      _syncHeaderActions() {},
      showEmptyState() {},
      _showLoadingOverlay() {},
      _hideLoadingOverlay() {},
      _minimizeNotesForDocumentOpen() {},
      _ensureDocChipRegistered() {},
    });
  });

  beforeEach(() => {
    resetCalls();
    clearDom();
    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    env.state.setApiBase('https://odysseus.test');
    globalThis.fetch = async (url, options) => {
      calls.fetches.push({ url, options });
      return { ok: true, json: async () => ({}) };
    };
  });

  it('derives a markdown heading and patches the untitled document', async () => {
    env.state.registerDocument({ id: 'doc-1', title: 'Untitled', language: 'markdown' });
    env.state.setActiveDocId('doc-1');
    const title = mount('doc-title-input', 'input');
    await env.api.autoTitleFromContent('# Voyage home\n\nThe rest.', 'doc-1');
    assert.equal(calls.fetches.length, 1);
    assert.equal(calls.fetches[0].url, 'https://odysseus.test/api/document/doc-1');
    assert.equal(JSON.parse(calls.fetches[0].options.body).title, 'Voyage home');
    assert.equal(env.state.docs.get('doc-1').title, 'Voyage home');
    assert.equal(title.value, 'Voyage home');
    assert.equal(calls.tabs, 1);
  });

  it('keeps an existing title and ignores empty content', async () => {
    env.state.registerDocument({ id: 'doc-1', title: 'Kept', language: 'markdown' });
    env.state.setActiveDocId('doc-1');
    await env.api.autoTitleFromContent('# Replacement', 'doc-1');
    await env.api.autoTitleFromContent('   ', 'doc-1');
    assert.equal(calls.fetches.length, 0);
    assert.equal(env.state.docs.get('doc-1').title, 'Kept');
  });

  it('falls back to an HTML heading and then a short first line', async () => {
    env.state.registerDocument({ id: 'doc-2', title: '', language: 'html' });
    await env.api.autoTitleFromContent('<h2>HTML title</h2>', 'doc-2');
    assert.equal(env.state.docs.get('doc-2').title, 'HTML title');

    env.state.registerDocument({ id: 'doc-3', title: 'Untitled', language: 'text' });
    await env.api.autoTitleFromContent('\n\nA short line\nmore', 'doc-3');
    assert.equal(env.state.docs.get('doc-3').title, 'A short line');
  });

  it('truncates a long generated title', async () => {
    env.state.registerDocument({ id: 'doc-4', title: 'Untitled', language: 'markdown' });
    await env.api.autoTitleFromContent(`# ${'A'.repeat(60)}`, 'doc-4');
    assert.equal(env.state.docs.get('doc-4').title, `${'A'.repeat(48)}...`);
  });

  it('rolls back an optimistic tab rename to the acknowledged title, including an empty title', async () => {
    for (const previous of ['Original', '']) {
      const doc = { id: 'rename', title: 'Rejected', language: 'markdown' };
      env.state.registerDocument(doc);
      env.state.setActiveDocId(doc.id);
      const input = mount('doc-title-input', 'input', { value: 'Rejected' });
      globalThis.fetch = async () => ({ ok: false, status: 500 });
      await env.api.updateTitle(doc.id, 'Rejected', { expectedTitle: previous });
      assert.equal(doc.title, previous);
      assert.equal(input.value, previous);
    }
  });

  it('serializes manual renames and rolls back to the last successful write', async () => {
    for (const firstSucceeds of [false, true]) {
      for (const secondSucceeds of [false, true]) {
        const doc = { id: 'rename', title: 'First', language: 'markdown' };
        env.state.registerDocument(doc);
        env.state.setActiveDocId(doc.id);
        mount('doc-title-input', 'input', { value: 'Second' });
        const pending = [];
        globalThis.fetch = (_url, options) => new Promise(resolve => pending.push({ options, resolve }));
        const first = env.api.updateTitle(doc.id, 'First', { expectedTitle: 'Original' });
        doc.title = 'Second';
        const second = env.api.updateTitle(doc.id, 'Second', { expectedTitle: 'First' });
        await Promise.resolve();
        assert.equal(pending.length, 1, 'only one manual PATCH may be in flight');
        assert.equal(pending[0].options.signal.aborted, false);
        await env.api.autoTitleFromContent('# Automatic', doc.id);
        assert.equal(pending.length, 1, 'automatic titling cannot interrupt manual edits');
        pending[0].resolve({ ok: firstSucceeds, status: 500, json: async () => ({ title: 'First' }) });
        await first;
        await Promise.resolve();
        assert.equal(pending.length, 2);
        assert.equal(JSON.parse(pending[1].options.body).expected_title, firstSucceeds ? 'First' : 'Original');
        assert.equal(doc.title, 'Second', 'earlier completion cannot restore a stale UI title');
        pending[1].resolve({ ok: secondSucceeds, status: 500, json: async () => ({ title: 'Second' }) });
        await second;
        assert.equal(doc.title, secondSucceeds ? 'Second' : (firstSucceeds ? 'First' : 'Original'));
        assert.equal(doc._titleWrites, undefined, 'the per-document queue is released');
      }
    }
  });

  it('suppresses automatic titling while a manual clear is pending', async () => {
    const doc = { id: 'clear', title: '', language: 'markdown' };
    env.state.registerDocument(doc);
    let resolvePatch;
    globalThis.fetch = () => new Promise(resolve => { resolvePatch = resolve; });
    const clear = env.api.updateTitle(doc.id, '', { expectedTitle: 'Original' });
    await Promise.resolve();
    await env.api.autoTitleFromContent('# Automatic', doc.id);
    assert.equal(doc._titleRequestSeq, 1);
    resolvePatch({ ok: true, json: async () => ({ title: '' }) });
    await clear;
    assert.equal(doc.title, '');
    assert.equal(doc._titleWrites, undefined);
  });

  it('updates the active language through the document record', async () => {
    env.state.registerDocument({ id: 'doc-1', title: 'Notes', language: 'markdown' });
    env.state.setActiveDocId('doc-1');
    mount('doc-language-select', 'select', { value: 'python' });
    await env.api.updateLanguage();
    assert.equal(JSON.parse(calls.fetches[0].options.body).language, 'python');
    assert.equal(env.state.docs.get('doc-1').language, 'python');
    assert.equal(calls.tabs, 1);
  });

  it('copies through the UI module and falls back to the clipboard', async () => {
    const textarea = mount('doc-editor-textarea', 'textarea', { value: 'alpha' });
    await env.api.copyDocument();
    assert.deepEqual(calls.copied, ['alpha']);
    assert.deepEqual(calls.toasts, ['Copied to clipboard']);

    host.uiModule.copyToClipboard = undefined;
    textarea.value = 'beta';
    let written = '';
    globalThis.navigator.clipboard.writeText = async (value) => { written = value; };
    await env.api.copyDocument();
    assert.equal(written, 'beta');
    host.uiModule.copyToClipboard = async (value) => { calls.copied.push(value); };
  });

  it('runs renderable, JavaScript, and unsupported sources', async () => {
    const textarea = mount('doc-editor-textarea', 'textarea', { value: '' });
    mount('doc-editor-wrap', 'div');
    mount('doc-language-select', 'select', { value: 'html' });
    env.api.runDocument();
    const output = globalThis.document.getElementById('doc-run-output');
    assert.match(output.innerHTML, /Nothing to run/);

    textarea.value = '<p>Hi</p>';
    env.api.runDocument();
    assert.equal(calls.htmlPreview, 1);
    assert.equal(output.style.display, 'none');

    mount('doc-language-select', 'select', { value: 'javascript' });
    textarea.value = 'console.log(1)';
    env.api.runDocument();
    assert.equal(calls.runner.at(-1).kind, 'javascript');

    globalThis.document.getElementById('doc-language-select').value = 'ruby';
    env.api.runDocument();
    assert.match(output.innerHTML, /Unsupported language/);
  });
});

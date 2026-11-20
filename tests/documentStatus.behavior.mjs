// tests/documentStatus.behavior.mjs
/**
 * Behavior tests for ui/status/documentStatus.
 * Run through tests/documentStatus.test.mjs.
 */

import { describe, it, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { SourceTextModule, SyntheticModule } from 'node:vm';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const featuresDir = path.join(repoRoot, 'frontend/src/features/documents');
const entryPath = path.join(featuresDir, 'ui/status/documentStatus.js');

const calls = {
  dismiss: null,
  dismissCloses: 0,
  portalZ: 0,
  statsTexts: [],
  outlines: [],
  cancelledFrames: 0,
  commands: { undo: false, redo: false },
  commandThrows: false,
  selection: null,
};

function resetCalls() {
  calls.dismiss = null;
  calls.dismissCloses = 0;
  calls.portalZ = 0;
  calls.statsTexts = [];
  calls.outlines = [];
  calls.cancelledFrames = 0;
  calls.commands = { undo: false, redo: false };
  calls.commandThrows = false;
  calls.selection = null;
}

const elements = new Map();
const frames = new Map();
let nextFrame = 1;
const originalSetTimeout = globalThis.setTimeout;

function createClassState() {
  const classes = new Set();
  let className = '';
  const sync = () => { className = [...classes].join(' '); };
  return {
    add(...names) { names.forEach(name => name && classes.add(name)); sync(); },
    remove(...names) { names.forEach(name => classes.delete(name)); sync(); },
    contains(name) { return classes.has(name); },
    toggle(name, force) {
      const next = force === undefined ? !classes.has(name) : Boolean(force);
      if (next) classes.add(name);
      else classes.delete(name);
      sync();
      return next;
    },
    get className() { return className; },
    set className(value) {
      classes.clear();
      String(value || '').split(/\s+/).filter(Boolean).forEach(name => classes.add(name));
      sync();
    },
  };
}

function matches(el, selector) {
  const sel = selector.trim();
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
  return el.tagName.toLowerCase() === sel.toLowerCase();
}

function queryAll(root, selector) {
  const parts = selector.split(',').map(part => part.trim()).filter(Boolean);
  const found = [];
  function walk(node) {
    for (const child of node.children || []) {
      if (parts.some(part => matches(child, part))) found.push(child);
      walk(child);
    }
  }
  walk(root);
  return found;
}

function makeElement(tag = 'div') {
  const el = {
    tagName: String(tag).toUpperCase(),
    type: '',
    title: '',
    value: '',
    textContent: '',
    innerText: '',
    placeholder: '',
    disabled: false,
    tabIndex: 0,
    style: { display: '' },
    dataset: {},
    parentElement: null,
    children: [],
    attributes: {},
    listeners: [],
    isConnected: false,
    selectionStart: 0,
    selectionEnd: 0,
    clientHeight: 180,
    scrollTop: 0,
    scrollHeight: 200,
    offsetWidth: 10,
    getBoundingClientRect() {
      return { left: 20, top: 30, bottom: 54, right: 80, width: 60, height: 24 };
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(type, handler) { this.listeners.push({ type, handler }); },
    removeEventListener(type, handler) {
      this.listeners = this.listeners.filter(entry => entry.type !== type || entry.handler !== handler);
    },
    querySelector(selector) { return queryAll(this, selector)[0] || null; },
    querySelectorAll(selector) { return queryAll(this, selector); },
    contains(node) {
      let current = node;
      while (current) {
        if (current === this) return true;
        current = current.parentElement;
      }
      return false;
    },
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      child.isConnected = this.isConnected;
      return child;
    },
    replaceChildren(...nodes) {
      for (const child of this.children) {
        child.parentElement = null;
        child.isConnected = false;
      }
      this.children = [];
      nodes.forEach(node => this.appendChild(node));
    },
    remove() {
      if (this.parentElement) {
        this.parentElement.children = this.parentElement.children.filter(child => child !== this);
      }
      this.parentElement = null;
      this.isConnected = false;
      this.removed = true;
    },
    focus() { document.activeElement = this; this.focused = true; },
    scrollIntoView(options) { this.scrolled = options; },
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
  };
  const classes = createClassState();
  el.classList = classes;
  Object.defineProperty(el, 'className', {
    get() { return classes.className; },
    set(value) { classes.className = value; },
  });
  let id = '';
  Object.defineProperty(el, 'id', {
    get() { return id; },
    set(value) {
      if (id && elements.get(id) === el) elements.delete(id);
      id = value || '';
      if (id) elements.set(id, el);
    },
  });
  Object.defineProperty(el, 'innerHTML', {
    get() { return this._html || ''; },
    set(html) {
      this._html = String(html || '');
      this.children = [];
      const pattern = /<(\/?)([a-z0-9]+)([^>]*)>|([^<]+)/gi;
      const stack = [this];
      for (const match of this._html.matchAll(pattern)) {
        if (match[4]) {
          if (match[4].trim()) stack.at(-1).textContent += match[4];
          continue;
        }
        if (match[1] === '/') {
          if (stack.length > 1) stack.pop();
          continue;
        }
        const child = makeElement(match[2]);
        const classMatch = (match[3] || '').match(/class="([^"]*)"/);
        if (classMatch) child.className = classMatch[1];
        stack.at(-1).appendChild(child);
        stack.push(child);
      }
    },
  });
  return el;
}

let dom;

function installDom() {
  elements.clear();
  const body = makeElement('body');
  body.isConnected = true;
  const textarea = makeElement('textarea');
  textarea.id = 'doc-editor-textarea';
  const lang = makeElement('select');
  lang.id = 'doc-language-select';
  const save = makeElement('button');
  save.id = 'doc-footer-copy-btn';
  const saveLabel = makeElement('span');
  saveLabel.className = 'doc-save-button-label';
  save.appendChild(saveLabel);
  const undo = makeElement('button');
  undo.id = 'doc-undo-btn';
  const redo = makeElement('button');
  redo.id = 'doc-redo-btn';
  const pdf = makeElement('div');
  pdf.id = 'doc-pdf-view';
  pdf.style.display = 'none';
  const rich = makeElement('div');
  rich.id = 'doc-email-richbody';
  rich.style.display = 'none';
  const preview = makeElement('div');
  preview.id = 'doc-md-preview';
  preview.style.display = 'none';
  const stats = makeElement('div');
  stats.id = 'doc-stats';
  for (const id of [
    'doc-stats-count',
    'doc-stats-unit',
    'doc-stats-scope',
    'doc-stats-words',
    'doc-stats-characters',
    'doc-stats-characters-no-spaces',
    'doc-stats-lines',
    'doc-stats-reading',
  ]) {
    const node = makeElement('span');
    node.id = id;
    stats.appendChild(node);
  }
  const statsBtn = makeElement('button');
  statsBtn.id = 'doc-stats-btn';
  stats.appendChild(statsBtn);
  const outlineBtn = makeElement('button');
  outlineBtn.id = 'doc-outline-toolbar-btn';
  const wrap = makeElement('div');
  wrap.id = 'doc-editor-wrap';
  const pre = makeElement('pre');
  pre.id = 'doc-editor-highlight';
  globalThis.document = {
    body,
    activeElement: null,
    getElementById(id) { return elements.get(id) || null; },
    createElement(tag) { return makeElement(tag); },
    createRange() {
      return { selectNodeContents() {}, collapse() {} };
    },
    queryCommandEnabled(command) {
      if (calls.commandThrows) throw new Error('unsupported');
      return Boolean(calls.commands[command]);
    },
  };
  const windowListeners = [];
  globalThis.window = {
    innerWidth: 1000,
    innerHeight: 800,
    visualViewport: {
      addEventListener(type, handler) { windowListeners.push({ type, handler, viewport: true }); },
      removeEventListener(type, handler) {
        const index = windowListeners.findIndex(entry => entry.handler === handler && entry.type === type);
        if (index >= 0) windowListeners.splice(index, 1);
      },
    },
    addEventListener(type, handler) { windowListeners.push({ type, handler }); },
    removeEventListener(type, handler) {
      const index = windowListeners.findIndex(entry => entry.handler === handler && entry.type === type && !entry.viewport);
      if (index >= 0) windowListeners.splice(index, 1);
    },
    getSelection() { return calls.selection; },
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
  dom = { body, textarea, lang, save, saveLabel, undo, redo, pdf, rich, preview, stats, statsBtn, outlineBtn, wrap, pre, windowListeners };
}

function drainFrames(limit = 20) {
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

function parseOutline(text) {
  const source = String(text || '');
  const lines = source.split('\n');
  const entries = [];
  lines.forEach((line, index) => {
    const match = /^(#{1,6})\s+(.*)$/.exec(line);
    if (!match) return;
    const start = lines.slice(0, index).join('\n').length + (index ? 1 : 0);
    entries.push({
      level: match[1].length,
      text: match[2],
      start,
      end: start + line.length,
      line: index + 1,
    });
  });
  calls.outlines.push(source);
  return entries;
}

const syntheticModules = new Map();

function mockEvaluate(fileName, module) {
  const set = (name, value) => module.setExport(name, value);
  if (fileName === 'host.js') {
    set('bindMenuDismiss', (menu, onClose, shouldDismiss) => {
      calls.dismiss = { menu, shouldDismiss };
      return () => {
        calls.dismissCloses += 1;
        onClose();
      };
    });
    set('getDocumentStats', (text) => {
      calls.statsTexts.push(text);
      const raw = String(text || '');
      const words = raw.trim() ? raw.trim().split(/\s+/).length : 0;
      return {
        words,
        characters: raw.length,
        charactersNoSpaces: raw.replace(/\s/g, '').length,
        lines: raw ? raw.split('\n').length : 0,
        readingMinutes: words ? 1 : 0,
      };
    });
    set('parseMarkdownOutline', parseOutline);
    set('topPortalZ', () => {
      calls.portalZ += 1;
      return 40;
    });
    set('markdownModule', {
      sanitizeAllowedHtml(value) { return String(value || ''); },
      mdToHtml(value) { return String(value || ''); },
    });
    return;
  }
  throw new Error(`No mock exports for ${fileName}`);
}

async function loadDocumentStatus() {
  const cache = new Map();
  async function linker(specifier, referencingModule) {
    const parent = fileURLToPath(referencingModule.identifier);
    const resolved = specifier.startsWith('.')
      ? path.resolve(path.dirname(parent), specifier)
      : null;
    const fileName = resolved ? path.basename(resolved) : specifier;
    const url = resolved ? pathToFileURL(resolved).href : `synthetic:${specifier}`;
    if (cache.has(url)) return cache.get(url);
    const useMock = fileName === 'host.js' || !resolved || !fs.existsSync(resolved);
    if (useMock) {
      if (fileName !== 'host.js') throw new Error(`Unexpected unresolved import ${specifier} from ${parent}`);
      const mod = new SyntheticModule(
        ['bindMenuDismiss', 'getDocumentStats', 'parseMarkdownOutline', 'topPortalZ', 'markdownModule'],
        function evaluate() { mockEvaluate(fileName, this); },
        { identifier: url },
      );
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
  const entrySource = fs.readFileSync(entryPath, 'utf8') + '\nexport { _normalizeRichStatsText, _documentStatsSource, _flashOutlineTarget, _jumpToDocumentOutlineEntry, _renderDocumentOutlineMenu };';
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
  };
}

let env;

function listener(target, type) {
  return target.listeners.find(entry => entry.type === type)?.handler;
}

describe('documentStatus', () => {
  before(async () => {
    installDom();
    env = await loadDocumentStatus();
  });

  beforeEach(() => {
    env.api._closeDocumentOutline();
    frames.clear();
    globalThis.setTimeout = originalSetTimeout;
    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    for (const id of [...env.state._closingDocs.keys()]) env.state.releaseClosingDraft(id);
    dom.textarea.value = '';
    dom.textarea.selectionStart = 0;
    dom.textarea.selectionEnd = 0;
    dom.lang.value = '';
    dom.save.dataset.saveState = '';
    dom.save.title = '';
    dom.save.attributes['aria-label'] = '';
    dom.saveLabel.textContent = '';
    dom.undo.disabled = false;
    dom.redo.disabled = false;
    dom.pdf.style.display = 'none';
    dom.rich.style.display = 'none';
    dom.rich.innerText = '';
    dom.rich.children = [];
    dom.preview.style.display = 'none';
    dom.preview.children = [];
    dom.statsBtn.className = '';
    dom.statsBtn.title = '';
    dom.outlineBtn.style.display = '';
    dom.outlineBtn.className = '';
    dom.outlineBtn.attributes['aria-expanded'] = 'false';
    dom.body.children = [];
    dom.windowListeners.length = 0;
    document.activeElement = null;
    resetCalls();
  });

  it('sets the editor placeholder from the language select', () => {
    elements.delete('doc-editor-textarea');
    assert.doesNotThrow(() => env.api._syncEditorPlaceholder());
    elements.set('doc-editor-textarea', dom.textarea);

    dom.lang.value = 'markdown';
    env.api._syncEditorPlaceholder();
    assert.equal(dom.textarea.placeholder, 'Write in Markdown...');

    dom.lang.value = 'email';
    env.api._syncEditorPlaceholder();
    assert.equal(dom.textarea.placeholder, 'Write your email...');

    dom.lang.value = 'python';
    env.api._syncEditorPlaceholder();
    assert.equal(dom.textarea.placeholder, 'Write python code...');

    dom.lang.value = '';
    env.api._syncEditorPlaceholder();
    assert.equal(dom.textarea.placeholder, 'Start typing or paste text to create a document...');
  });

  it('renders save state from the active document and a closing draft', () => {
    elements.delete('doc-footer-copy-btn');
    assert.doesNotThrow(() => env.api._renderDocumentSaveState());
    elements.set('doc-footer-copy-btn', dom.save);

    env.api._renderDocumentSaveState();
    assert.equal(dom.save.dataset.saveState, 'saved');
    assert.equal(dom.saveLabel.textContent, 'Saved');
    assert.equal(dom.save.title, 'All changes saved (Ctrl+S)');
    assert.equal(dom.save.getAttribute('aria-label'), 'Saved');

    const doc = { id: 'doc-1', language: 'markdown', content: 'a', _saveState: 'dirty' };
    env.state.registerDocument(doc);
    env.state.setActiveDocId('doc-1');
    env.api._renderDocumentSaveState();
    assert.equal(dom.save.dataset.saveState, 'dirty');
    assert.equal(dom.saveLabel.textContent, 'Unsaved');

    env.state.clearDocuments();
    env.state.retainClosingDraft('doc-1', { id: 'doc-1', _saveState: 'error' });
    env.api._renderDocumentSaveState();
    assert.equal(dom.save.dataset.saveState, 'error');
    assert.equal(dom.saveLabel.textContent, 'Save failed');
    assert.equal(dom.save.title, 'Document could not be saved (Ctrl+S)');
  });

  it('synchronizes undo and redo controls, including the pdf pane', () => {
    env.api._setDocumentHistoryControlState(true, false);
    assert.equal(dom.undo.disabled, false);
    assert.equal(dom.undo.getAttribute('aria-disabled'), 'false');
    assert.equal(dom.redo.disabled, true);
    assert.equal(dom.redo.getAttribute('aria-disabled'), 'true');

    calls.commands = { undo: true, redo: true };
    env.api._syncDocumentHistoryControlsNow();
    assert.equal(dom.undo.disabled, false);
    assert.equal(dom.redo.disabled, false);

    calls.commandThrows = true;
    env.api._syncDocumentHistoryControlsNow();
    assert.equal(dom.undo.disabled, true);
    assert.equal(dom.redo.disabled, true);

    calls.commandThrows = false;
    calls.commands = { undo: false, redo: false };
    dom.pdf.style.display = '';
    env.api._syncDocumentHistoryControlsNow();
    assert.equal(dom.undo.disabled, false);
    assert.equal(dom.redo.disabled, true);
  });

  it('schedules one history frame and one stats frame', () => {
    calls.commands = { undo: true, redo: false };
    env.api._scheduleDocumentHistoryControls();
    env.api._scheduleDocumentHistoryControls();
    assert.equal(frames.size, 1);
    assert.equal(calls.cancelledFrames, 0);
    drainFrames();
    assert.equal(dom.undo.disabled, false);
    assert.equal(dom.redo.disabled, true);

    dom.textarea.value = 'hello world';
    env.api._scheduleDocumentStats();
    env.api._scheduleDocumentStats();
    assert.equal(calls.cancelledFrames, 1);
    assert.equal(frames.size, 1);
    drainFrames();
    assert.deepEqual(calls.statsTexts, ['hello world']);
    assert.equal(elements.get('doc-stats-count').textContent, '2');
    assert.equal(elements.get('doc-stats-unit').textContent, ' words');
    assert.equal(elements.get('doc-stats-scope').textContent, 'Document');
    assert.equal(dom.statsBtn.classList.contains('doc-stats-selected'), false);
  });

  it('counts a textarea selection and a rich-text selection', () => {
    dom.textarea.value = 'hello world';
    dom.textarea.selectionStart = 0;
    dom.textarea.selectionEnd = 5;
    env.api._renderDocumentStats();
    assert.deepEqual(calls.statsTexts, ['hello']);
    assert.equal(elements.get('doc-stats-scope').textContent, 'Selection');
    assert.equal(elements.get('doc-stats-unit').textContent, ' selected');
    assert.equal(dom.statsBtn.classList.contains('doc-stats-selected'), true);
    assert.equal(dom.statsBtn.title, '1 words selected');

    dom.rich.style.display = '';
    dom.rich.innerText = 'a\u00a0b\n\n\n';
    calls.selection = {
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt() { return { commonAncestorContainer: dom.rich }; },
      toString() { return 'picked'; },
    };
    const source = env.api._documentStatsSource();
    assert.equal(source.selected, true);
    assert.equal(source.text, 'picked');
    assert.equal(env.api._normalizeRichStatsText('a\u00a0b\n\n\n'), 'a b');
  });

  it('reads the active document language', () => {
    env.state.registerDocument({ id: 'doc-1', language: 'Markdown', content: '' });
    env.state.setActiveDocId('doc-1');
    dom.lang.value = 'python';
    assert.equal(env.api._activeDocLanguage(), 'markdown');

    env.state.clearDocuments();
    env.state.setActiveDocId(null);
    dom.lang.value = 'RichText';
    assert.equal(env.api._activeDocLanguage(), 'richtext');
    dom.lang.value = '';
    assert.equal(env.api._activeDocLanguage(), '');
  });

  it('builds markdown, preview, and rich-text outline entries', () => {
    assert.deepEqual(env.api._documentOutlineEntries(), []);
    assert.equal(env.api._documentOutlineSupported(), false);

    env.state.registerDocument({ id: 'doc-1', language: 'markdown', content: '# Stored' });
    env.state.setActiveDocId('doc-1');
    dom.textarea.value = '# Title\n\n## Next';
    assert.equal(env.api._documentOutlineSupported(), true);
    const markdown = env.api._documentOutlineEntries();
    assert.deepEqual(markdown.map(entry => entry.text), ['Title', 'Next']);
    assert.equal(markdown[0].node, undefined);

    const first = makeElement('h1');
    first.textContent = 'Title';
    const second = makeElement('h2');
    second.textContent = 'Next';
    dom.preview.appendChild(first);
    dom.preview.appendChild(second);
    dom.preview.style.display = '';
    const previewed = env.api._documentOutlineEntries();
    assert.equal(previewed[0].node, first);
    assert.equal(previewed[0].mode, 'preview');
    assert.equal(previewed[1].node, second);

    env.state.docs.get('doc-1').language = 'richtext';
    const heading = makeElement('h2');
    heading.innerText = 'Rich heading';
    heading.textContent = 'Rich heading';
    const blank = makeElement('h3');
    dom.rich.style.display = '';
    dom.rich.appendChild(heading);
    dom.rich.appendChild(blank);
    const rich = env.api._documentOutlineEntries();
    assert.equal(rich.length, 1);
    assert.equal(rich[0].text, 'Rich heading');
    assert.equal(rich[0].level, 2);
    assert.equal(rich[0].mode, 'richtext');
    assert.equal(rich[0].node, heading);
  });

  it('opens, navigates, and closes the outline menu', () => {
    env.state.registerDocument({ id: 'doc-1', language: 'python', content: '' });
    env.state.setActiveDocId('doc-1');
    env.api._openDocumentOutline();
    assert.equal(dom.body.children.length, 0);
    assert.equal(calls.dismiss, null);

    env.state.docs.get('doc-1').language = 'markdown';
    dom.textarea.value = '# Title\n## Next';
    env.api._openDocumentOutline();
    drainFrames();
    const menu = dom.body.children[0];
    assert.equal(menu.id, 'doc-outline-menu');
    assert.equal(menu.style.width, '320px');
    assert.equal(menu.style.left, '20px');
    assert.equal(menu.style.top, '60px');
    assert.equal(menu.style.zIndex, '40');
    assert.equal(calls.portalZ, 1);
    assert.equal(dom.outlineBtn.classList.contains('is-active'), true);
    assert.equal(dom.outlineBtn.getAttribute('aria-expanded'), 'true');
    assert.equal(menu.querySelector('.doc-outline-count').textContent, '2');
    const items = menu.querySelectorAll('.doc-outline-item');
    assert.equal(items.length, 2);
    assert.equal(items[0].querySelector('.doc-outline-label').textContent, 'Title');
    assert.equal(document.activeElement, items[0]);
    assert.equal(dom.windowListeners.some(entry => entry.type === 'resize'), true);

    listener(menu, 'keydown')({ key: 'ArrowDown', preventDefault() { this.prevented = true; } });
    assert.equal(document.activeElement, items[1]);

    listener(items[0], 'click')();
    assert.equal(dom.textarea.selectionStart, 0);
    assert.equal(dom.textarea.selectionEnd, '# Title'.length);
    assert.equal(calls.dismissCloses, 1);
    assert.equal(menu.removed, true);
    assert.equal(dom.outlineBtn.classList.contains('is-active'), false);
    assert.equal(dom.outlineBtn.getAttribute('aria-expanded'), 'false');
    assert.equal(dom.windowListeners.length, 0);
  });

  it('jumps to a connected heading or a source range', () => {
    const heading = makeElement('h1');
    dom.rich.style.display = '';
    dom.rich.isConnected = true;
    dom.rich.appendChild(heading);
    calls.selection = { removeAllRanges() { this.cleared = true; }, addRange() { this.added = true; } };
    env.api._jumpToDocumentOutlineEntry({ node: heading, mode: 'richtext' });
    assert.deepEqual(heading.scrolled, { behavior: 'smooth', block: 'center' });
    assert.equal(heading.classList.contains('doc-outline-target'), true);
    assert.equal(calls.selection.added, true);
    assert.equal(dom.rich.focused, true);

    const scheduled = [];
    globalThis.setTimeout = (callback, delay) => {
      scheduled.push({ callback, delay });
      return scheduled.length;
    };
    env.api._flashOutlineTarget(heading);
    assert.equal(scheduled[0].delay, 1100);
    scheduled[0].callback();
    assert.equal(heading.classList.contains('doc-outline-target'), false);
    globalThis.setTimeout = originalSetTimeout;

    env.api._jumpToDocumentOutlineEntry({ node: { isConnected: false }, start: 2, end: 5, line: 4 });
    assert.equal(dom.textarea.selectionStart, 2);
    assert.equal(dom.textarea.selectionEnd, 5);
    assert.equal(dom.textarea.scrollTop, 20);
    assert.equal(dom.pre.scrollTop, 20);
    assert.equal(dom.wrap.classList.contains('doc-outline-target'), true);
  });

  it('refreshes outline visibility and rerenders an open menu', () => {
    env.state.registerDocument({ id: 'doc-1', language: 'markdown', content: '' });
    env.state.setActiveDocId('doc-1');
    dom.textarea.value = '# Title';
    env.api._openDocumentOutline();
    const menu = dom.body.children[0];
    dom.textarea.value = '# Title\n## Next';
    env.api._refreshDocumentOutline();
    assert.equal(dom.outlineBtn.style.display, '');
    assert.equal(menu.querySelectorAll('.doc-outline-item').length, 2);

    env.state.docs.get('doc-1').language = 'python';
    env.api._refreshDocumentOutline();
    assert.equal(dom.outlineBtn.style.display, 'none');
    assert.equal(menu.removed, true);
    assert.equal(calls.dismissCloses, 1);
  });

  it('exports only the status module surface', () => {
    const source = fs.readFileSync(entryPath, 'utf8');
    const exported = [...source.matchAll(/^export function (\w+)/gm)].map(match => match[1]).sort();
    assert.deepEqual(exported, [
      '_activeDocLanguage',
      '_closeDocumentOutline',
      '_documentOutlineEntries',
      '_documentOutlineSupported',
      '_openDocumentOutline',
      '_refreshDocumentOutline',
      '_renderDocumentSaveState',
      '_renderDocumentStats',
      '_scheduleDocumentHistoryControls',
      '_scheduleDocumentOutline',
      '_scheduleDocumentStats',
      '_setDocumentHistoryControlState',
      '_syncDocumentHistoryControlsNow',
      '_syncEditorPlaceholder',
    ]);
    assert.equal(typeof env.api._normalizeRichStatsText, 'function');
    assert.equal(typeof env.api._renderDocumentOutlineMenu, 'function');
  });
});

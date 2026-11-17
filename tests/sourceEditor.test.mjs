// tests/documents/source/sourceEditor.test.mjs
/**
 * Focused behavioral and regression tests for the Document Editor Source Editing subsystem.
 * Covers range replacement, markdown post-processing, syntax highlighting sync,
 * line-number measurement and rendering, auto-detection, and preview interactions.
 */

import test, { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  activeDocId,
  setActiveDocId,
  docs,
  registerDocument,
  clearDocuments
} from '../frontend/src/features/documents/application/documentState.js';

import {
  bindDocumentCommands,
  _resetDocumentCommandsForTesting
} from '../frontend/src/features/documents/application/documentCommands.js';

import {
  _autoDetectDebounce,
  _autoTitleDebounce,
  _handleMarkdownPreviewClickHint,
  _hlDebounce,
  _lineNumberContentEl,
  _lineNumberObservedTextarea,
  _lineNumberResizeObserver,
  _lineNumberResizeRaf,
  _refreshMarkdownPreviewIfVisible,
  _replaceRange,
  attemptAutoDetect,
  syncGutterScroll,
  syncHighlighting,
  updateLineNumbers
} from '../frontend/src/features/documents/ui/editor/sourceEditor.js';

// Minimal DOM Element Mock
class MockElement {
  constructor(tagName = 'div') {
    this.tagName = tagName.toUpperCase();
    this.nodeType = 1;
    this.children = [];
    this.parentElement = null;
    this.parentNode = null;
    this.style = {};
    this.dataset = {};
    this.classList = new Set();
    this.classList.toggle = (cls, force) => {
      if (force === undefined) {
        if (this.classList.has(cls)) this.classList.delete(cls);
        else this.classList.add(cls);
      } else if (force) {
        this.classList.add(cls);
      } else {
        this.classList.delete(cls);
      }
    };
    this.attributes = new Map();
    this._value = '';
    this._textContent = '';
    this.clientWidth = 800;
    this.clientHeight = 600;
    this.scrollHeight = 600;
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.eventListeners = new Map();
  }

  get value() { return this._value; }
  set value(v) { this._value = String(v ?? ''); }

  get textContent() {
    if (this.children.length === 0) return this._textContent;
    return this.children.map(c => c.textContent).join('');
  }
  set textContent(v) {
    this._textContent = String(v ?? '');
    this.children = [];
  }

  get className() { return Array.from(this.classList).join(' '); }
  set className(v) {
    this.classList = new Set((v || '').split(/\s+/).filter(Boolean));
    this.classList.toggle = (cls, force) => {
      if (force === undefined) {
        if (this.classList.has(cls)) this.classList.delete(cls);
        else this.classList.add(cls);
      } else if (force) {
        this.classList.add(cls);
      } else {
        this.classList.delete(cls);
      }
    };
  }

  setAttribute(k, v) { this.attributes.set(k, String(v)); }
  getAttribute(k) { return this.attributes.get(k) || null; }
  removeAttribute(k) { this.attributes.delete(k); }

  appendChild(child) {
    if (child.nodeType === 11) { // DocumentFragment
      const children = [...child.children];
      for (const c of children) this.appendChild(c);
      child.children = [];
      return child;
    }
    child.parentElement = this;
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  prepend(child) {
    child.parentElement = this;
    child.parentNode = this;
    this.children.unshift(child);
  }

  replaceChild(newChild, oldChild) {
    const idx = this.children.indexOf(oldChild);
    if (idx !== -1) {
      if (newChild.nodeType === 11) {
        const toInsert = [...newChild.children];
        this.children.splice(idx, 1, ...toInsert);
        for (const c of toInsert) {
          c.parentElement = this;
          c.parentNode = this;
        }
        newChild.children = [];
      } else {
        newChild.parentElement = this;
        newChild.parentNode = this;
        this.children[idx] = newChild;
      }
    }
  }

  querySelector(sel) {
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      for (const c of this.children) {
        if (c.classList && c.classList.has(cls)) return c;
        const sub = c.querySelector(sel);
        if (sub) return sub;
      }
    }
    return null;
  }

  querySelectorAll(sel) {
    const matches = [];
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      const walk = (el) => {
        for (const c of el.children) {
          if (c.classList && c.classList.has(cls)) matches.push(c);
          walk(c);
        }
      };
      walk(this);
    }
    return matches;
  }

  addEventListener(type, fn) {
    if (!this.eventListeners.has(type)) this.eventListeners.set(type, []);
    this.eventListeners.get(type).push(fn);
  }

  dispatchEvent(event) {
    const list = this.eventListeners.get(event.type) || [];
    for (const fn of list) fn(event);
    return true;
  }

  focus() { this._focused = true; }
}

class MockTextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = text;
    this.parentElement = null;
    this.parentNode = null;
  }
}

class MockDocumentFragment {
  constructor() {
    this.nodeType = 11;
    this.children = [];
  }
  appendChild(c) {
    this.children.push(c);
  }
}

describe('Source Editor Subsystem', () => {
  let originalWindow;
  let originalDocument;
  let originalGetComputedStyle;
  let elementsById;
  let commandsCalled;

  beforeEach(() => {
    elementsById = new Map();
    commandsCalled = [];

    // Register test document commands port
    _resetDocumentCommandsForTesting();
    bindDocumentCommands({
      openPanel: () => {},
      closePanel: () => {},
      switchToDoc: () => {},
      saveCurrentToMap: () => {},
      renderTabs: () => {},
      _syncDocIndicator: () => {},
      _syncHeaderActions: () => { commandsCalled.push('_syncHeaderActions'); },
      showEmptyState: () => {},
      _showLoadingOverlay: () => {},
      _hideLoadingOverlay: () => {},
      _minimizeNotesForDocumentOpen: () => {},
      _ensureDocChipRegistered: () => {}
    }, { idempotent: true });

    clearDocuments();
    setActiveDocId(null);

    // Mock document and window
    originalWindow = globalThis.window;
    originalDocument = globalThis.document;
    originalGetComputedStyle = globalThis.getComputedStyle;

    globalThis.document = {
      getElementById: (id) => elementsById.get(id) || null,
      createElement: (tag) => new MockElement(tag),
      createTextNode: (text) => new MockTextNode(text),
      createDocumentFragment: () => new MockDocumentFragment(),
      createTreeWalker: (root, filter) => {
        const nodes = [];
        const walk = (el) => {
          for (const c of el.children) {
            if (c.nodeType === 3) nodes.push(c);
            else walk(c);
          }
        };
        walk(root);
        let index = -1;
        return {
          nextNode: () => {
            index++;
            return index < nodes.length ? nodes[index] : null;
          },
          get currentNode() { return nodes[index] || null; }
        };
      },
      execCommand: () => false,
      body: new MockElement('body')
    };

    globalThis.NodeFilter = { SHOW_TEXT: 4 };
    globalThis.Event = class {
      constructor(type, options = {}) {
        this.type = type;
        this.bubbles = Boolean(options.bubbles);
      }
    };

    globalThis.getComputedStyle = (el) => ({
      fontFamily: 'monospace',
      fontSize: '11px',
      fontWeight: '400',
      fontStyle: 'normal',
      lineHeight: '16px',
      letterSpacing: '0px',
      tabSize: '4',
      fontFeatureSettings: 'normal',
      fontVariantLigatures: 'none',
      fontKerning: 'none',
      textRendering: 'geometricPrecision',
      whiteSpace: 'pre-wrap',
      wordWrap: 'break-word',
      overflowWrap: 'break-word',
      paddingLeft: '48px',
      paddingRight: '12px'
    });

    globalThis.window = {
      hljs: null,
      addEventListener: () => {},
      removeEventListener: () => {}
    };
  });

  afterEach(() => {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
    globalThis.getComputedStyle = originalGetComputedStyle;
    _resetDocumentCommandsForTesting();
    clearDocuments();
  });

  describe('Range Replacement (_replaceRange)', () => {
    it('executes native insertText command and preserves selection', () => {
      const ta = new MockElement('textarea');
      ta.value = 'Hello world!';
      let execCalled = false;

      globalThis.document.execCommand = (cmd, showUI, val) => {
        if (cmd === 'insertText') {
          execCalled = true;
          ta.value = 'Hello beautiful world!';
          return true;
        }
        return false;
      };

      _replaceRange(ta, 6, 11, 'beautiful world');
      assert.equal(execCalled, true);
      assert.equal(ta.value, 'Hello beautiful world!');
      assert.equal(ta._focused, true);
    });

    it('falls back to string splice and dispatches input event when execCommand fails', () => {
      const ta = new MockElement('textarea');
      ta.value = 'Foo BAR Baz';
      let inputDispatched = false;
      ta.addEventListener('input', (ev) => {
        inputDispatched = true;
        assert.equal(ev.bubbles, true);
      });

      globalThis.document.execCommand = () => false;

      _replaceRange(ta, 4, 7, 'REPLACED');
      assert.equal(ta.value, 'Foo REPLACED Baz');
      assert.equal(ta.selectionStart, 12);
      assert.equal(ta.selectionEnd, 12);
      assert.equal(inputDispatched, true);
    });

    it('handles insertion at beginning and end of content correctly', () => {
      const ta = new MockElement('textarea');
      ta.value = 'Content';
      globalThis.document.execCommand = () => false;

      // Prefix insertion
      _replaceRange(ta, 0, 0, 'Prefix: ');
      assert.equal(ta.value, 'Prefix: Content');

      // Suffix insertion
      _replaceRange(ta, ta.value.length, ta.value.length, ' [Done]');
      assert.equal(ta.value, 'Prefix: Content [Done]');
    });
  });

  describe('Syntax Highlighting and Synchronization (syncHighlighting)', () => {
    it('synchronizes textarea content and trailing newline to code element', () => {
      const ta = new MockElement('textarea');
      ta.value = 'console.log("test");';
      const code = new MockElement('code');
      const wrap = new MockElement('div');
      const langSelect = new MockElement('select');
      langSelect.value = 'javascript';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-editor-code', code);
      elementsById.set('doc-editor-wrap', wrap);
      elementsById.set('doc-language-select', langSelect);

      globalThis.window.hljs = {
        getLanguage: (l) => l === 'javascript',
        highlightElement: (el) => { el.dataset.highlighted = 'yes'; }
      };

      syncHighlighting();
      assert.equal(code.textContent, 'console.log("test");\n');
      assert.equal(code.className, 'language-javascript');
      assert.equal(wrap.classList.has('doc-code-syntax'), true);
      assert.equal(ta.wrap, 'off');
    });

    it('preserves code element when inline diff is active', () => {
      const ta = new MockElement('textarea');
      ta.value = 'New text that should not overwrite diff';
      const code = new MockElement('code');
      code.dataset.hasDiff = 'true';
      code.textContent = 'Existing Diff Markup';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-editor-code', code);

      syncHighlighting();
      assert.equal(code.textContent, 'Existing Diff Markup');
    });

    it('maps svg language to xml grammar for hljs', () => {
      const ta = new MockElement('textarea');
      ta.value = '<svg></svg>';
      const code = new MockElement('code');
      const wrap = new MockElement('div');
      const langSelect = new MockElement('select');
      langSelect.value = 'svg';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-editor-code', code);
      elementsById.set('doc-editor-wrap', wrap);
      elementsById.set('doc-language-select', langSelect);

      globalThis.window.hljs = {
        getLanguage: (l) => l === 'xml',
        highlightElement: () => {}
      };

      syncHighlighting();
      assert.equal(code.className, 'language-xml');
      assert.equal(wrap.classList.has('doc-code-syntax'), true);
    });

    it('sets wrap to soft for markdown without syntax highlight class', () => {
      const ta = new MockElement('textarea');
      ta.value = '# Title';
      const code = new MockElement('code');
      const wrap = new MockElement('div');
      const langSelect = new MockElement('select');
      langSelect.value = 'markdown';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-editor-code', code);
      elementsById.set('doc-editor-wrap', wrap);
      elementsById.set('doc-language-select', langSelect);

      syncHighlighting();
      assert.equal(wrap.classList.has('doc-code-syntax'), false);
      assert.equal(ta.wrap, 'soft');
    });
  });

  describe('Line Number Rendering and Gutter Sync', () => {
    it('creates and reuses line-number content element', () => {
      const gutter = new MockElement('div');
      const content1 = _lineNumberContentEl(gutter);
      assert.equal(content1.className, 'doc-line-number-content');
      assert.equal(gutter.children.length, 1);

      const content2 = _lineNumberContentEl(gutter);
      assert.equal(content1, content2);
    });

    it('renders correct line number rows for multiline text', () => {
      const ta = new MockElement('textarea');
      const gutter = new MockElement('div');
      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-line-numbers', gutter);

      updateLineNumbers('Line 1\nLine 2\nLine 3');

      const inner = gutter.querySelector('.doc-line-number-content');
      assert.ok(inner);
      assert.equal(inner.children.length, 3);
      assert.equal(inner.children[0].textContent, '1');
      assert.equal(inner.children[1].textContent, '2');
      assert.equal(inner.children[2].textContent, '3');
    });

    it('bypasses rerendering when text, dimensions, and style signature match', () => {
      const ta = new MockElement('textarea');
      const gutter = new MockElement('div');
      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-line-numbers', gutter);

      updateLineNumbers('Line 1\nLine 2');
      const inner = gutter.querySelector('.doc-line-number-content');
      const firstChildRef = inner.children[0];

      // Second call without modifications
      updateLineNumbers('Line 1\nLine 2');
      assert.equal(inner.children[0], firstChildRef);

      // Force rerender replaces children
      updateLineNumbers('Line 1\nLine 2', true);
      assert.notEqual(inner.children[0], firstChildRef);
    });

    it('synchronizes gutter scroll with textarea scrollTop', () => {
      const ta = new MockElement('textarea');
      const gutter = new MockElement('div');
      ta.scrollTop = 125;
      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-line-numbers', gutter);

      syncGutterScroll();
      const inner = gutter.querySelector('.doc-line-number-content');
      assert.equal(inner.style.transform, 'translateY(-125px)');
    });
  });

  describe('Language Auto-Detection (attemptAutoDetect)', () => {
    it('bails early when active document has userSetLanguage', () => {
      const doc = {
        id: 'doc-user',
        title: 'User Set',
        language: 'markdown',
        userSetLanguage: true
      };
      registerDocument(doc);
      setActiveDocId('doc-user');

      globalThis.window.hljs = { highlightAuto: () => ({ language: 'python', relevance: 20 }) };
      const ta = new MockElement('textarea');
      ta.value = 'import sys\nimport os\nfor i in range(100): print(i)';
      elementsById.set('doc-editor-textarea', ta);

      attemptAutoDetect();
      assert.equal(doc.language, 'markdown');
    });

    it('detects SVG markup heuristic and switches language', (t) => {
      const doc = {
        id: 'doc-svg',
        title: 'Graphic',
        language: 'markdown'
      };
      registerDocument(doc);
      setActiveDocId('doc-svg');

      globalThis.window.hljs = { getLanguage: () => true };
      const ta = new MockElement('textarea');
      ta.value = '<?xml version="1.0"?>\n<svg width="100" height="100"><circle cx="50" cy="50" r="40"/></svg>';
      const langSelect = new MockElement('select');
      langSelect.value = 'markdown';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-language-select', langSelect);

      const requests = [];
      t.mock.method(globalThis, 'fetch', async (url, options) => {
        requests.push({ url, ...options });
        return { ok: true };
      });

      attemptAutoDetect();
      assert.equal(doc.language, 'svg');
      assert.equal(langSelect.value, 'svg');
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, `/api/document/${doc.id}`);
      assert.equal(requests[0].method, 'PATCH');
      assert.deepEqual(JSON.parse(requests[0].body), { language: doc.language });
      assert.ok(commandsCalled.includes('_syncHeaderActions'));
    });

    it('detects programming language via hljs.highlightAuto when above relevance threshold', (t) => {
      const doc = {
        id: 'doc-py',
        title: 'Script',
        language: 'markdown'
      };
      registerDocument(doc);
      setActiveDocId('doc-py');

      globalThis.window.hljs = {
        getLanguage: () => true,
        highlightAuto: (sample) => ({ language: 'python', relevance: 12 })
      };

      const ta = new MockElement('textarea');
      ta.value = 'def compute_something():\n    return [x*2 for x in range(100)]\nprint(compute_something())';
      const langSelect = new MockElement('select');
      langSelect.value = 'markdown';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-language-select', langSelect);

      const requests = [];
      t.mock.method(globalThis, 'fetch', async (url, options) => {
        requests.push({ url, ...options });
        return { ok: true };
      });

      attemptAutoDetect();
      assert.equal(doc.language, 'python');
      assert.equal(langSelect.value, 'python');
      assert.equal(requests.length, 1);
      assert.equal(requests[0].url, `/api/document/${doc.id}`);
      assert.equal(requests[0].method, 'PATCH');
      assert.deepEqual(JSON.parse(requests[0].body), { language: doc.language });
    });

    it('ignores auto-detection when hljs relevance is below threshold', () => {
      const doc = {
        id: 'doc-low-rel',
        title: 'Ambiguous',
        language: 'markdown'
      };
      registerDocument(doc);
      setActiveDocId('doc-low-rel');

      globalThis.window.hljs = {
        getLanguage: () => true,
        highlightAuto: () => ({ language: 'python', relevance: 3 }) // Below AUTO_DETECT_MIN_RELEVANCE (8)
      };

      const ta = new MockElement('textarea');
      ta.value = 'a = 1\nb = 2\nc = a + b\nprint(c)\nmore text here to exceed min chars';
      const langSelect = new MockElement('select');
      langSelect.value = 'markdown';

      elementsById.set('doc-editor-textarea', ta);
      elementsById.set('doc-language-select', langSelect);

      attemptAutoDetect();
      assert.equal(doc.language, 'markdown');
    });
  });

  describe('Markdown Preview Interaction (_handleMarkdownPreviewClickHint)', () => {
    it('throttles click hints and triggers toast after rapid clicks', () => {
      const doc = { id: 'doc-hint', language: 'markdown' };
      registerDocument(doc);
      setActiveDocId('doc-hint');

      // Mock preview visibility
      const preview = new MockElement('div');
      preview.style.display = 'block';
      elementsById.set('doc-md-preview', preview);

      let toastMessage = null;
      globalThis.uiModule = {
        showToast: (msg) => { toastMessage = msg; }
      };

      // Click 1 and 2: no toast
      _handleMarkdownPreviewClickHint();
      _handleMarkdownPreviewClickHint();
      assert.equal(toastMessage, null);

      // Click 3: triggers toast
      _handleMarkdownPreviewClickHint();
      assert.ok(toastMessage);
      assert.ok(toastMessage.includes('Preview is read-only'));
    });
  });
});

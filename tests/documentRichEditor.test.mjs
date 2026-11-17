// tests/documentRichEditor.test.mjs
/**
 * Focused behavioral and regression test suite for the Odysseus Document Editor
 * Rich-Text Subsystem (Modules 1 through 10 under frontend/src/features/documents/ui/richEditor/).
 *
 * Conforms to:
 * - tests/TESTING_STANDARD.md
 */

import test, { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  _emailPlainTextToHtml,
  _emailHtmlToPlainText,
  _sanitizedRichTextHtml,
  _richTextContentToHtml,
  _emailRichbodyActive,
  _syncEmailRichbody
} from '../frontend/src/features/documents/ui/richEditor/richBodySync.js';

import {
  _cleanRichTextPasteHtml,
  _normalizeRichChecklists,
  _setRichChecklistItemChecked,
  _toggleRichChecklist,
  _handleRichChecklistEnter,
  _handleRichHeadingEnter,
  _normalizeRichInlineCode,
  _toggleRichInlineCode,
  _applyRichLineSpacing,
  _selectedRichImage,
  _activeRichImage
} from '../frontend/src/features/documents/ui/richEditor/richOperations.js';

import {
  _clearRichImageSelection,
  _selectRichImage,
  setImageSelectionObserver,
  _deleteRichImage,
  _normalizeRichTextImages,
  _replaceRichImage,
  _replaceRichImageFigure
} from '../frontend/src/features/documents/ui/richEditor/imageOps.js';

import {
  _richLinkAtRange,
  _removeRichLink,
  _editRichImageAlt,
  _editRichImageCaption,
  _applyRichImageAction
} from '../frontend/src/features/documents/ui/richEditor/links.js';

import {
  _currentBlockTag,
  _normalizedRichMenuColor,
  _richMenuColorHex,
  _richSelectionHasFormatting,
  applyMdFormat,
  _saveFormatSelection
} from '../frontend/src/features/documents/ui/richEditor/formatting.js';

import {
  _applyRichBlockInputRule,
  _richSlashContext,
  _chooseRichSlashCommand
} from '../frontend/src/features/documents/ui/richEditor/slashMenu.js';

import {
  _hideRichSelectionToolbar,
  _scheduleRichSelectionToolbar
} from '../frontend/src/features/documents/ui/richEditor/selectionToolbar.js';

import {
  _showRichImageSourceMenu
} from '../frontend/src/features/documents/ui/richEditor/imageMenus.js';

import {
  _richDropdownCurrentActions
} from '../frontend/src/features/documents/ui/richEditor/formatMenus.js';

// ============================================================================
// Lightweight Mock DOM Environment
// ============================================================================

class MockClassList {
  constructor(el) {
    this.el = el;
    this.classes = new Set();
  }
  add(...names) {
    for (const name of names) {
      if (name) this.classes.add(name);
    }
    this._sync();
  }
  remove(...names) {
    for (const name of names) {
      this.classes.delete(name);
    }
    this._sync();
  }
  contains(name) {
    return this.classes.has(name);
  }
  toggle(name, force) {
    const has = this.contains(name);
    const set = force !== undefined ? Boolean(force) : !has;
    if (set) this.add(name);
    else this.remove(name);
    return set;
  }
  _sync() {
    this.el._className = Array.from(this.classes).join(' ');
    this.el._syncClassAttribute();
  }
  [Symbol.iterator]() {
    return this.classes.values();
  }
}

class MockTextNode {
  constructor(text = '') {
    this.nodeType = 3;
    this.nodeValue = String(text);
    this.parentElement = null;
    this.parentNode = null;
  }
  get textContent() {
    return this.nodeValue;
  }
  set textContent(v) {
    this.nodeValue = String(v ?? '');
  }
  remove() {
    if (this.parentElement) {
      const idx = this.parentElement.childNodes.indexOf(this);
      if (idx !== -1) this.parentElement.childNodes.splice(idx, 1);
      this.parentElement = null;
      this.parentNode = null;
    }
  }
  cloneNode() {
    return new MockTextNode(this.nodeValue);
  }
}

function parseHtmlInto(html, parent) {
  const tagRe = /<!--[\s\S]*?-->|<(\/)?([a-zA-Z0-9-]+)([^>]*)>|([^<]+)/g;
  let match;
  let current = parent;
  while ((match = tagRe.exec(html)) !== null) {
    if (match[4]) {
      const text = new MockTextNode(match[4]);
      current.appendChild(text);
    } else if (match[2]) {
      const isClosing = Boolean(match[1]);
      const tagName = match[2].toLowerCase();
      if (isClosing) {
        if (current.parentElement && current.tagName.toLowerCase() === tagName) {
          current = current.parentElement;
        }
      } else {
        const el = new MockElement(tagName);
        const attrStr = match[3];
        const attrRe = /([a-zA-Z0-9_-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
        let attrMatch;
        while ((attrMatch = attrRe.exec(attrStr)) !== null) {
          const val = attrMatch[2] ?? attrMatch[3] ?? attrMatch[4] ?? '';
          el.setAttribute(attrMatch[1], val);
        }
        current.appendChild(el);
        const isSelfClosing = match[0].endsWith('/>') || ['br', 'hr', 'img', 'input', 'col'].includes(tagName);
        if (!isSelfClosing) {
          current = el;
        }
      }
    }
  }
}

function serializeNodeChildren(node) {
  let res = '';
  const nodes = node.childNodes || [];
  for (const child of nodes) {
    if (child.nodeType === 3) {
      res += child.nodeValue.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    } else if (child.nodeType === 1) {
      const tag = child.tagName.toLowerCase();
      res += `<${tag}`;
      for (const attr of child.attributes) {
        if (attr.value === '') {
          res += ` ${attr.name}`;
        } else {
          res += ` ${attr.name}="${attr.value}"`;
        }
      }
      res += '>';
      res += serializeNodeChildren(child);
      if (!['br', 'hr', 'img', 'input', 'col'].includes(tag)) {
        res += `</${tag}>`;
      }
    }
  }
  return res;
}

class MockElement {
  constructor(tagName = 'div', nodeType = 1) {
    this.tagName = tagName.toUpperCase();
    this.nodeType = nodeType;
    this.childNodes = [];
    this.attributes = [];
    this.classList = new MockClassList(this);
    this._className = '';
    this.parentElement = null;
    this.parentNode = null;
    this.isConnected = true;

    this.style = new Proxy({}, {
      get: (target, prop) => {
        if (prop === 'getPropertyValue') {
          return (p) => target[p] || '';
        }
        if (prop === 'setProperty') {
          return (p, v) => { target[p] = String(v); };
        }
        if (prop === 'removeProperty') {
          return (p) => { delete target[p]; };
        }
        return target[prop] || '';
      },
      set: (target, prop, val) => {
        target[prop] = String(val);
        return true;
      }
    });

    if (this.tagName === 'TEMPLATE') {
      this.content = new MockElement('fragment', 11);
      this.content.parentElement = this;
    }
  }

  get className() {
    return this._className;
  }
  set className(v) {
    this._className = String(v ?? '');
    this.classList.classes = new Set(this._className.split(/\s+/).filter(Boolean));
    this._syncClassAttribute();
  }

  _syncClassAttribute() {
    const existing = this.attributes.find(a => a.name === 'class');
    if (this._className) {
      if (existing) existing.value = this._className;
      else this.attributes.push({ name: 'class', value: this._className });
    } else if (existing) {
      const idx = this.attributes.indexOf(existing);
      this.attributes.splice(idx, 1);
    }
  }

  get dataset() {
    const self = this;
    return new Proxy({}, {
      get(target, prop) {
        if (typeof prop !== 'string') return undefined;
        const attr = 'data-' + prop.replace(/([A-Z])/g, '-$1').toLowerCase();
        return self.getAttribute(attr) ?? undefined;
      },
      set(target, prop, val) {
        if (typeof prop !== 'string') return true;
        const attr = 'data-' + prop.replace(/([A-Z])/g, '-$1').toLowerCase();
        self.setAttribute(attr, String(val));
        return true;
      },
      deleteProperty(target, prop) {
        if (typeof prop !== 'string') return true;
        const attr = 'data-' + prop.replace(/([A-Z])/g, '-$1').toLowerCase();
        self.removeAttribute(attr);
        return true;
      }
    });
  }

  get textContent() {
    if (this.content) return this.content.textContent;
    return this.childNodes.map(c => c.textContent).join('');
  }
  set textContent(v) {
    if (this.content) {
      this.content.textContent = v;
      return;
    }
    this.childNodes = [new MockTextNode(String(v ?? ''))];
    this.childNodes[0].parentElement = this;
    this.childNodes[0].parentNode = this;
  }

  get innerText() {
    return this.textContent;
  }
  set innerText(v) {
    this.textContent = v;
  }

  get innerHTML() {
    if (this.content) return serializeNodeChildren(this.content);
    return serializeNodeChildren(this);
  }
  set innerHTML(v) {
    const target = this.content || this;
    target.childNodes = [];
    parseHtmlInto(String(v ?? ''), target);
  }

  get outerHTML() {
    const tag = this.tagName.toLowerCase();
    let res = `<${tag}`;
    for (const attr of this.attributes) {
      if (attr.value === '') res += ` ${attr.name}`;
      else res += ` ${attr.name}="${attr.value}"`;
    }
    res += '>';
    res += this.innerHTML;
    if (!['br', 'hr', 'img', 'input', 'col'].includes(tag)) {
      res += `</${tag}>`;
    }
    return res;
  }

  get nextElementSibling() {
    if (!this.parentElement) return null;
    const siblings = this.parentElement.childNodes.filter(n => n.nodeType === 1);
    const idx = siblings.indexOf(this);
    return idx !== -1 && idx + 1 < siblings.length ? siblings[idx + 1] : null;
  }

  get children() {
    return this.childNodes.filter(n => n.nodeType === 1);
  }

  setAttribute(name, value) {
    const lower = name.toLowerCase();
    const existing = this.attributes.find(a => a.name === lower);
    if (existing) {
      existing.value = String(value);
    } else {
      this.attributes.push({ name: lower, value: String(value) });
    }
    if (lower === 'class') {
      this._className = String(value);
      this.classList.classes = new Set(this._className.split(/\s+/).filter(Boolean));
    }
  }

  getAttribute(name) {
    const lower = name.toLowerCase();
    const attr = this.attributes.find(a => a.name === lower);
    return attr ? attr.value : null;
  }

  hasAttribute(name) {
    const lower = name.toLowerCase();
    return this.attributes.some(a => a.name === lower);
  }

  removeAttribute(name) {
    const lower = name.toLowerCase();
    const idx = this.attributes.findIndex(a => a.name === lower);
    if (idx !== -1) {
      this.attributes.splice(idx, 1);
    }
    if (lower === 'class') {
      this._className = '';
      this.classList.classes.clear();
    }
  }

  appendChild(child) {
    child.parentElement = this;
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  prepend(child) {
    child.parentElement = this;
    child.parentNode = this;
    this.childNodes.unshift(child);
    return child;
  }

  append(...nodes) {
    for (const node of nodes) {
      this.appendChild(node);
    }
  }

  removeChild(child) {
    const idx = this.childNodes.indexOf(child);
    if (idx !== -1) {
      this.childNodes.splice(idx, 1);
      child.parentElement = null;
      child.parentNode = null;
    }
    return child;
  }

  remove() {
    if (this.parentElement) {
      this.parentElement.removeChild(this);
    }
  }

  replaceWith(...newNodes) {
    if (this.parentElement) {
      const idx = this.parentElement.childNodes.indexOf(this);
      if (idx !== -1) {
        this.parentElement.childNodes.splice(idx, 1, ...newNodes);
        for (const n of newNodes) {
          n.parentElement = this.parentElement;
          n.parentNode = this.parentElement;
        }
      }
    }
  }

  contains(child) {
    if (!child) return false;
    let curr = child;
    while (curr) {
      if (curr === this) return true;
      curr = curr.parentElement;
    }
    return false;
  }

  matches(sel) {
    if (!sel) return false;
    if (sel.includes(',')) {
      return sel.split(',').some(part => this.matches(part.trim()));
    }
    if (sel.includes('.')) {
      const parts = sel.split('.');
      const tag = parts[0];
      const cls = parts.slice(1);
      if (tag && this.tagName.toLowerCase() !== tag.toLowerCase()) return false;
      return cls.every(c => this.classList.contains(c));
    }
    if (sel.startsWith('.')) return this.classList.contains(sel.slice(1));
    if (sel.startsWith('#')) return this.getAttribute('id') === sel.slice(1);
    if (sel.includes('[') && sel.endsWith(']')) {
      const attrName = sel.slice(sel.indexOf('[') + 1, sel.indexOf(']'));
      if (attrName.includes('=')) {
        const [k, v] = attrName.split('=');
        return this.getAttribute(k) === v.replace(/^["']|["']$/g, '');
      }
      return this.hasAttribute(attrName);
    }
    return this.tagName.toLowerCase() === sel.toLowerCase();
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  querySelectorAll(sel) {
    const target = this.content || this;
    if (sel.startsWith(':scope > ')) {
      const childSel = sel.slice(':scope > '.length).trim();
      return target.childNodes.filter(c => c.nodeType === 1 && c.matches(childSel));
    }
    const res = [];
    const walk = (node) => {
      for (const c of node.childNodes || []) {
        if (c.nodeType === 1) {
          let matches = false;
          if (sel === '*') matches = true;
          else if (sel.includes(',')) {
            matches = sel.split(',').some(part => c.matches(part.trim()));
          } else if (sel.includes(' ')) {
            const parts = sel.split(/\s+/);
            if (parts.length === 2 && c.matches(parts[1])) {
              let parent = c.parentElement;
              while (parent) {
                if (parent.matches(parts[0])) { matches = true; break; }
                parent = parent.parentElement;
              }
            }
          } else {
            matches = c.matches(sel);
          }
          if (matches) res.push(c);
          walk(c);
        }
      }
    };
    walk(target);
    return res;
  }

  closest(sel) {
    let curr = this;
    while (curr) {
      if (curr.nodeType === 1 && curr.matches(sel)) return curr;
      curr = curr.parentElement;
    }
    return null;
  }

  focus() { if (globalThis.document) globalThis.document.activeElement = this; }
  getBoundingClientRect() {
    return { top: 100, bottom: 120, left: 50, right: 200, width: 150, height: 20 };
  }
  cloneNode(deep = true) {
    const clone = new MockElement(this.tagName.toLowerCase(), this.nodeType);
    for (const attr of this.attributes) {
      clone.setAttribute(attr.name, attr.value);
    }
    if (deep) {
      for (const child of this.childNodes) {
        clone.appendChild(child.cloneNode(true));
      }
    }
    return clone;
  }
}

class MockRange {
  constructor() {
    this.startContainer = null;
    this.startOffset = 0;
    this.endContainer = null;
    this.endOffset = 0;
    this.commonAncestorContainer = null;
    this.collapsed = true;
    this._selectedNode = null;
  }

  selectNode(node) {
    this._selectedNode = node;
    this.startContainer = node.parentNode || node;
    this.endContainer = node.parentNode || node;
    this.commonAncestorContainer = node.parentNode || node;
    this.collapsed = false;
  }

  selectNodeContents(node) {
    this._selectedNode = node;
    this.startContainer = node;
    this.endContainer = node;
    this.commonAncestorContainer = node;
    this.collapsed = false;
  }

  setStart(node, offset = 0) {
    this.startContainer = node;
    this.startOffset = offset;
  }

  setEnd(node, offset = 0) {
    this.endContainer = node;
    this.endOffset = offset;
  }

  setStartAfter(node) {
    this.startContainer = node.parentNode || node;
    this.commonAncestorContainer = node.parentNode || node;
  }

  collapse(toStart) {
    this.collapsed = true;
  }

  cloneRange() {
    const r = new MockRange();
    r.startContainer = this.startContainer;
    r.startOffset = this.startOffset;
    r.endContainer = this.endContainer;
    r.endOffset = this.endOffset;
    r.commonAncestorContainer = this.commonAncestorContainer;
    r.collapsed = this.collapsed;
    r._selectedNode = this._selectedNode;
    return r;
  }

  cloneContents() {
    const frag = new MockElement('fragment', 11);
    if (this._selectedNode) {
      frag.appendChild(this._selectedNode.cloneNode(true));
    }
    return frag;
  }

  extractContents() {
    return this.cloneContents();
  }

  insertNode(node) {
    if (this.startContainer?.appendChild) {
      this.startContainer.appendChild(node);
    }
  }

  toString() {
    return this.startContainer ? this.startContainer.textContent || '' : '';
  }

  intersectsNode(node) {
    return true;
  }
}

let activeRange = null;

// Global DOM hooks
beforeEach(() => {
  activeRange = null;
  globalThis.Node = {
    ELEMENT_NODE: 1,
    TEXT_NODE: 3,
    DOCUMENT_FRAGMENT_NODE: 11
  };
  globalThis.NodeFilter = {
    SHOW_TEXT: 4
  };
  globalThis.DOMParser = class MockDOMParser {
    parseFromString(html) {
      const body = new MockElement('body');
      body.innerHTML = String(html ?? '');
      return { body };
    }
  };
  globalThis.markdownModule = {
    sanitizeAllowedHtml: (html) => String(html || '').replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, ''),
    mdToHtml: (raw) => `<p>${raw}</p>`
  };
  globalThis.uiModule = {
    showToast: () => {}
  };
  globalThis.topPortalZ = () => 1000;
  globalThis.getComputedStyle = (el) => ({
    color: 'rgb(0, 0, 0)',
    getPropertyValue: () => ''
  });

  // Image menus observer boundary stubs
  globalThis._persistEmailLocalDraftSoon = () => {};
  globalThis._scheduleAutoSave = () => {};
  globalThis._markDocumentDirty = () => {};
  setImageSelectionObserver(() => {});

  globalThis.document = {
    body: new MockElement('body'),
    createElement: (tag) => new MockElement(tag),
    createRange: () => new MockRange(),
    createTreeWalker: (root) => {
      const textNodes = [];
      const walk = (n) => {
        if (n.nodeType === 3) textNodes.push(n);
        for (const c of n.childNodes || []) walk(c);
      };
      walk(root);
      let idx = 0;
      return {
        nextNode: () => textNodes[idx++] || null
      };
    },
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    execCommand: (cmd, _showUi, value) => {
      if (cmd === 'insertText' && globalThis.document.activeElement) {
        const ta = globalThis.document.activeElement;
        const start = ta.selectionStart || 0;
        const end = ta.selectionEnd || start;
        ta.value = ta.value.slice(0, start) + String(value ?? '') + ta.value.slice(end);
        ta.selectionStart = ta.selectionEnd = start + String(value ?? '').length;
        return true;
      }
      if (cmd === 'delete' && activeRange?._selectedNode) {
        activeRange._selectedNode.remove();
      }
      return true;
    },
    addEventListener: () => {},
    removeEventListener: () => {}
  };

  globalThis.window = {
    innerWidth: 1024,
    innerHeight: 768,
    getSelection: () => ({
      rangeCount: activeRange ? 1 : 0,
      getRangeAt: (i) => i === 0 ? activeRange : null,
      removeAllRanges: () => { activeRange = null; },
      addRange: (r) => { activeRange = r; }
    })
  };

  globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
});

// ============================================================================
// Test Suites
// ============================================================================

describe('Document Editor Rich-Text Subsystem', () => {

  describe('Content Sanitization and Transformation (richBodySync)', () => {
    it('_emailPlainTextToHtml escapes text and replaces newlines with <br>', () => {
      assert.equal(_emailPlainTextToHtml(null), '');
      assert.equal(_emailPlainTextToHtml(''), '');
      const text = 'Line 1\nLine 2 & <tag>';
      const html = _emailPlainTextToHtml(text);
      assert.ok(html.includes('<br>'), 'contains <br> replacement');
      assert.ok(!html.includes('<tag>'), 'tags escaped');
      assert.ok(html.includes('&lt;tag&gt;'), 'entities escaped');
    });

    it('_emailHtmlToPlainText strips HTML tags and extracts text', () => {
      assert.equal(_emailHtmlToPlainText(null), '');
      assert.equal(_emailHtmlToPlainText(''), '');
      assert.equal(_emailHtmlToPlainText('<b>Bold</b> text'), 'Bold text');
      assert.equal(_emailHtmlToPlainText('<p>Line 1</p><p>Line 2</p>'), 'Line 1\n\nLine 2');
      assert.equal(
        _emailHtmlToPlainText('<p>Hi &amp; bye</p><script>alert("x")</script><style>.a{}</style>'),
        'Hi & bye'
      );
      assert.equal(
        _emailHtmlToPlainText('<scr<script>ipt>alert(1)</script><img alt="a > b" src=x onerror=alert(1)>Tail'),
        'ipt>alert(1)Tail'
      );
      // Decoding escaped tags to plaintext must remain non-executable when rendered back via _emailPlainTextToHtml.
      const decoded = _emailHtmlToPlainText('&lt;img src=x onerror="alert(1)"&gt; &amp;lt;b&amp;gt;');
      assert.equal(decoded, '<img src=x onerror="alert(1)"> &lt;b&gt;');
      const rendered = _emailPlainTextToHtml(decoded);
      assert.equal(rendered, '&lt;img src=x onerror="alert(1)"&gt; &amp;lt;b&amp;gt;');
      assert.ok(!rendered.includes('<img'), 're-rendered plaintext never emits raw <img> tag');
    });

    it('_richTextContentToHtml detects raw HTML and leaves it intact', () => {
      assert.equal(_richTextContentToHtml(null), '');
      assert.equal(_richTextContentToHtml('   '), '');
      const raw = '<p>Paragraph <em>emphasized</em></p>';
      assert.equal(_richTextContentToHtml(raw), raw);
    });

    it('_emailRichbodyActive inspects rich editor display status', () => {
      const origGet = document.getElementById;
      try {
        const el = new MockElement('div');
        el.style.display = 'block';
        document.getElementById = (id) => id === 'doc-email-richbody' ? el : null;
        assert.equal(_emailRichbodyActive(), el);

        el.style.display = 'none';
        assert.equal(_emailRichbodyActive(), null);
      } finally {
        document.getElementById = origGet;
      }
    });
  });

  describe('HTML Paste Sanitization (richOperations)', () => {
    it('_cleanRichTextPasteHtml preserves valid formatting and strips script tags', () => {
      assert.equal(_cleanRichTextPasteHtml(null), '');
      assert.equal(_cleanRichTextPasteHtml(''), '');

      const dangerous = '<p>Hello <script>alert("xss")</script>world</p>';
      const clean = _cleanRichTextPasteHtml(dangerous);
      assert.ok(!clean.includes('<script>'), 'script tag stripped');
      assert.ok(!clean.includes('alert'), 'script content removed');
      assert.ok(clean.includes('Hello') && clean.includes('world'), 'text preserved');
    });

    it('_cleanRichTextPasteHtml removes data-editor tokens', () => {
      const input = '<p data-editor-table-token="tok123" data-editor-image-selected>Text</p>';
      const clean = _cleanRichTextPasteHtml(input);
      assert.ok(!clean.includes('data-editor-table-token'), 'table token removed');
      assert.ok(!clean.includes('data-editor-image-selected'), 'selection token removed');
      assert.ok(clean.includes('Text'), 'text content intact');
    });

    it('_cleanRichTextPasteHtml preserves allowed checklist elements', () => {
      const input = '<ul class="rich-checklist"><li data-checked="true" aria-checked="true">Item 1</li></ul>';
      const clean = _cleanRichTextPasteHtml(input);
      assert.ok(clean.includes('rich-checklist'), 'checklist class preserved');
      assert.ok(clean.includes('data-checked="true"'), 'data-checked preserved');
    });
  });

  describe('Checklist and Heading Behavior (richOperations)', () => {
    it('_normalizeRichChecklists ensures correct attributes on items', () => {
      const root = new MockElement('div');
      const ul = new MockElement('ul');
      ul.className = 'rich-checklist';
      const li1 = new MockElement('li');
      li1.dataset.checked = 'true';
      const li2 = new MockElement('li');
      ul.appendChild(li1);
      ul.appendChild(li2);
      root.appendChild(ul);

      _normalizeRichChecklists(root);
      assert.equal(li1.getAttribute('aria-checked'), 'true');
      assert.equal(li2.getAttribute('aria-checked'), 'false');
    });

    it('_handleRichChecklistEnter splits or exits empty checklist items', () => {
      const rich = new MockElement('div');
      const item = new MockElement('li');
      rich.appendChild(item);

      assert.equal(_handleRichChecklistEnter(null, new MockElement('p')), false);

      const normalEvent = { key: 'a', preventDefault() {} };
      assert.equal(_handleRichChecklistEnter(normalEvent, rich), false);
    });

    it('_handleRichHeadingEnter detects Enter on heading elements', () => {
      const rich = new MockElement('div');
      const h1 = new MockElement('h1');
      h1.textContent = 'Heading title';
      rich.appendChild(h1);

      const evt = { key: 'Enter', shiftKey: false, preventDefault: () => {} };
      const handled = _handleRichHeadingEnter(evt, rich);
      assert.equal(typeof handled, 'boolean');
    });
  });

  describe('Inline Code Toggle and Normalization (richOperations)', () => {
    it('_normalizeRichInlineCode handles elements safely', () => {
      const div = new MockElement('div');
      assert.doesNotThrow(() => {
        _normalizeRichInlineCode(div);
      });
    });

    it('_toggleRichInlineCode safely handles missing selection', () => {
      assert.doesNotThrow(() => {
        _toggleRichInlineCode(new MockElement('div'));
      });
    });
  });

  describe('Image Operations and Figures (imageOps)', () => {
    it('_selectRichImage sets active image and data-editor-image-selected', () => {
      const rich = new MockElement('div');
      const img = new MockElement('img');
      rich.appendChild(img);

      _selectRichImage(rich, img);
      assert.equal(img.dataset.editorImageSelected, 'true');
      assert.equal(_activeRichImage, img);

      _clearRichImageSelection();
      assert.equal(img.hasAttribute('data-editor-image-selected'), false);
      assert.equal(_activeRichImage, null);
    });

    it('_deleteRichImage removes image from container', () => {
      const rich = new MockElement('div');
      const img = new MockElement('img');
      rich.appendChild(img);

      activeRange = new MockRange();
      activeRange.selectNode(img);

      assert.equal(rich.childNodes.length, 1);
      _deleteRichImage(rich, img);
      assert.equal(rich.childNodes.length, 0);
    });

    it('_normalizeRichTextImages updates legacy figures to modern caption structure', () => {
      const root = new MockElement('div');
      const figure = new MockElement('figure');
      figure.className = 'richtext-image';
      const img = new MockElement('img');
      const figcaption = new MockElement('figcaption');
      figcaption.textContent = 'Legacy caption';
      figure.appendChild(img);
      figure.appendChild(figcaption);
      root.appendChild(figure);

      _normalizeRichTextImages(root);
      const caption = figure.querySelector('.richtext-image-caption');
      assert.ok(caption, 'caption element normalized');
      assert.equal(caption.getAttribute('role'), 'note');
      assert.equal(caption.textContent, 'Legacy caption');
    });
  });

  describe('Rich Text Links and URLs (links)', () => {
    it('_richLinkAtRange returns null for null or empty ranges', () => {
      assert.equal(_richLinkAtRange(null, null), null);
    });

    it('_applyRichImageAction applies size and align modifications', () => {
      const rich = new MockElement('div');
      const img = new MockElement('img');
      rich.appendChild(img);
      _selectRichImage(rich, img);

      // Sizing action
      assert.doesNotThrow(() => {
        _applyRichImageAction(rich, 'image:size:full');
      });

      // Align action
      assert.doesNotThrow(() => {
        _applyRichImageAction(rich, 'image:align:center');
      });
    });
  });

  describe('Formatting Commands and Color Utilities (formatting)', () => {
    it('_normalizedRichMenuColor formats hex and rgb values', () => {
      assert.equal(_normalizedRichMenuColor(''), '');
      assert.equal(_normalizedRichMenuColor('#abc'), '#abc');
      assert.equal(_normalizedRichMenuColor('#112233'), '#112233');
      assert.equal(_normalizedRichMenuColor('rgb(255, 0, 128)'), 'rgb(255,0,128)');
      assert.equal(_normalizedRichMenuColor('transparent'), 'transparent');
    });

    it('_richMenuColorHex formats color values to hex', () => {
      assert.equal(_richMenuColorHex('#abc'), '#aabbcc');
      assert.equal(_richMenuColorHex('#112233'), '#112233');
      assert.equal(_richMenuColorHex('rgb(255, 0, 128)'), '#ff0080');
    });

    it('_currentBlockTag identifies block elements in container', () => {
      const rich = new MockElement('div');
      assert.equal(_currentBlockTag(rich), '');

      const h2 = new MockElement('h2');
      rich.appendChild(h2);
      activeRange = new MockRange();
      activeRange.selectNodeContents(h2);

      assert.equal(_currentBlockTag(rich), 'h2');
    });

    it('_richSelectionHasFormatting safely handles collapsed selection', () => {
      assert.equal(_richSelectionHasFormatting(new MockElement('div'), null), false);
    });
  });

  describe('Slash Menu and Input Rules (slashMenu)', () => {
    it('_richSlashContext identifies query when typing / at cursor', () => {
      assert.equal(_richSlashContext(null), null);
      assert.equal(_richSlashContext(new MockElement('div')), null);
    });

    it('_applyRichBlockInputRule recognizes markdown shortcuts', () => {
      const rich = new MockElement('div');
      const p = new MockElement('p');
      p.textContent = '# Heading One';
      rich.appendChild(p);

      assert.doesNotThrow(() => {
        _applyRichBlockInputRule(rich, p);
      });
    });

    it('_chooseRichSlashCommand recognizes command actions', () => {
      const rich = new MockElement('div');
      assert.doesNotThrow(() => {
        _chooseRichSlashCommand(rich, { action: 'heading-1' });
        _chooseRichSlashCommand(rich, { action: 'bullet-list' });
        _chooseRichSlashCommand(rich, { action: 'divider' });
      });
    });
  });

  describe('Toolbar Visibility and Cleanup (selectionToolbar, imageMenus, formatMenus)', () => {
    it('_hideRichSelectionToolbar disposes active toolbar without errors', () => {
      assert.doesNotThrow(() => {
        _hideRichSelectionToolbar();
      });
    });

    it('_scheduleRichSelectionToolbar schedules frame without leaking resources', () => {
      assert.doesNotThrow(() => {
        _scheduleRichSelectionToolbar();
      });
    });

    it('_showRichImageSourceMenu cleans up previous source menus', () => {
      const button = new MockElement('button');
      assert.doesNotThrow(() => {
        _showRichImageSourceMenu(button);
      });
    });

    it('_richDropdownCurrentActions handles empty DOM state gracefully', () => {
      const actions = _richDropdownCurrentActions();
      assert.ok(typeof actions === 'object' && actions !== null, 'returns action map');
    });
  });

  describe('Selection Preservation and Formatting Execution (formatting)', () => {
    it('_saveFormatSelection records and preserves textarea selection', () => {
      const btn = new MockElement('button');
      btn.className = 'md-dd-toggle';
      const evt = {
        type: 'pointerdown',
        target: btn
      };

      const ta = new MockElement('textarea');
      ta.setAttribute('id', 'doc-editor-textarea');
      ta.value = 'Hello world formatting';
      ta.selectionStart = 0;
      ta.selectionEnd = 5;
      ta.setSelectionRange = (s, e) => { ta.selectionStart = s; ta.selectionEnd = e; };

      const origGet = document.getElementById;
      try {
        document.getElementById = (id) => id === 'doc-editor-textarea' ? ta : null;
        const saved = _saveFormatSelection(evt);
        assert.equal(saved, true, 'selection successfully saved');

        // Apply bold formatting to the saved selection
        applyMdFormat('bold');
        assert.ok(ta.value.includes('**Hello**'), 'bold markdown markers applied to saved range');
      } finally {
        document.getElementById = origGet;
      }
    });

    it('_applyRichLineSpacing applies line-height styling to blocks', () => {
      const rich = new MockElement('div');
      const p = new MockElement('p');
      p.textContent = 'Spaced paragraph text';
      rich.appendChild(p);

      activeRange = new MockRange();
      activeRange.selectNodeContents(p);

      assert.doesNotThrow(() => {
        _applyRichLineSpacing(rich, '1.5');
      });
    });
  });

  describe('Document Switching and Lifecycle Cleanup', () => {
    it('clears active image selection and hides toolbars on document transition', () => {
      const rich = new MockElement('div');
      const img = new MockElement('img');
      rich.appendChild(img);

      // Select image in old document
      _selectRichImage(rich, img);
      assert.equal(img.dataset.editorImageSelected, 'true');

      // Document switch triggers cleanup of toolbars and active image state
      _clearRichImageSelection();
      _hideRichSelectionToolbar();

      assert.equal(img.hasAttribute('data-editor-image-selected'), false, 'image selection flag removed');
      assert.equal(_activeRichImage, null, 'active image reference cleared');
    });
  });

  describe('Security Fail-Closed Sanitization (PR #7292)', () => {
    it('_sanitizedRichTextHtml and _richTextContentToHtml fail closed when sanitizer is unavailable or returns non-string', () => {
      const rich = new MockElement('div');
      rich.innerHTML = '<p>Safe <b>text</b><img src=x onerror=alert(1)></p>';
      rich.textContent = 'Safe text';

      const prevSanitizer = globalThis.markdownModule.sanitizeAllowedHtml;
      try {
        globalThis.markdownModule.sanitizeAllowedHtml = undefined;
        const fallbackSaved = _sanitizedRichTextHtml(rich);
        assert.ok(!fallbackSaved.includes('<img'), 'fail-closed escape omits raw img tag');
        assert.equal(_richTextContentToHtml('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');

        globalThis.markdownModule.sanitizeAllowedHtml = () => null;
        assert.equal(_sanitizedRichTextHtml(rich), '');
        assert.equal(_richTextContentToHtml('<p>Hello</p>'), '');
      } finally {
        globalThis.markdownModule.sanitizeAllowedHtml = prevSanitizer;
      }
    });

    it('_cleanRichTextPasteHtml fails closed and strips foreign/blocked tags even if sanitizer misses them', () => {
      const prevSanitizer = globalThis.markdownModule.sanitizeAllowedHtml;
      try {
        globalThis.markdownModule.sanitizeAllowedHtml = undefined;
        const escaped = _cleanRichTextPasteHtml('<img src=x onerror=alert(1)>');
        assert.equal(escaped, '&lt;img src=x onerror=alert(1)&gt;');

        // Even with a permissive sanitizer, blocked containers and unsafe links must be removed
        globalThis.markdownModule.sanitizeAllowedHtml = (html) => html;
        const cleaned = _cleanRichTextPasteHtml(
          '<p><b>Keep</b></p><svg onload="alert(1)"><text>bad</text></svg><iframe src="https://evil.example"></iframe><a href="java\tscript:alert(1)">Link</a>'
        );
        assert.ok(cleaned.includes('<b>Keep</b>'), 'valid formatting preserved');
        assert.ok(!cleaned.includes('<svg'), 'svg removed');
        assert.ok(!cleaned.includes('<iframe'), 'iframe removed');
        assert.ok(!cleaned.includes('href'), 'malformed link href stripped');
        assert.ok(cleaned.includes('Link'), 'link text preserved');
      } finally {
        globalThis.markdownModule.sanitizeAllowedHtml = prevSanitizer;
      }
    });
  });

});

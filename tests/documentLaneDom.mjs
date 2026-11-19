/**
 * Minimal DOM for Lane B document-editor behavior tests.
 */

const elements = new Map();

function createClassList() {
  const names = new Set();
  return {
    add(...values) { values.forEach(name => name && names.add(name)); },
    remove(...values) { values.forEach(name => names.delete(name)); },
    contains(name) { return names.has(name); },
    toggle(name, force) {
      const next = force === undefined ? !names.has(name) : Boolean(force);
      if (next) names.add(name);
      else names.delete(name);
      return next;
    },
  };
}

export function createElement(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    id: '',
    className: '',
    textContent: '',
    innerHTML: '',
    innerText: '',
    value: '',
    title: '',
    hidden: false,
    style: {},
    dataset: {},
    classList: createClassList(),
    children: [],
    parentElement: null,
    attributes: new Map(),
    listeners: [],
    setAttribute(name, value) { this.attributes.set(name, String(value)); },
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; },
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    insertBefore(child, before) {
      child.parentElement = this;
      const index = this.children.indexOf(before);
      if (index < 0) this.children.push(child);
      else this.children.splice(index, 0, child);
      return child;
    },
    remove() {
      if (this.parentElement) {
        this.parentElement.children = this.parentElement.children.filter(item => item !== this);
      }
      this.parentElement = null;
    },
    after(sibling) {
      const parent = this.parentElement;
      if (!parent) return;
      const index = parent.children.indexOf(this);
      sibling.parentElement = parent;
      parent.children.splice(index + 1, 0, sibling);
    },
    closest() { return null; },
    querySelector(selector) {
      return queryAll(this, selector)[0] || null;
    },
    querySelectorAll(selector) {
      return queryAll(this, selector);
    },
    addEventListener(type, handler) {
      this.listeners.push({ type, handler });
    },
    removeEventListener(type, handler) {
      this.listeners = this.listeners.filter(entry => entry.type !== type || entry.handler !== handler);
    },
    click() {
      for (const entry of [...this.listeners]) {
        if (entry.type === 'click') entry.handler({ stopPropagation() {}, preventDefault() {}, target: this });
      }
    },
    focus() { this.focused = true; },
    getBoundingClientRect() {
      return { top: 0, bottom: 20, left: 0, right: 40, width: 40, height: 20 };
    },
  };
  return el;
}

function matches(el, selector) {
  const sel = selector.trim();
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
  return el.tagName.toLowerCase() === sel.toLowerCase();
}

function queryAll(root, selector) {
  const found = [];
  function walk(node) {
    for (const child of node.children || []) {
      if (matches(child, selector)) found.push(child);
      walk(child);
    }
  }
  walk(root);
  return found;
}

function findById(root, id) {
  if (!root) return null;
  if (root.id === id) return root;
  for (const child of root.children || []) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

function installGlobal(name, value) {
  try {
    globalThis[name] = value;
  } catch {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
}

export function installDom() {
  elements.clear();
  const body = createElement('body');
  body.id = '';
  const documentElement = createElement('html');
  const document = {
    body,
    documentElement,
    getElementById(id) { return elements.get(id) || findById(body, id); },
    createElement(tag) { return createElement(tag); },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const window = {
    document,
    innerWidth: 1200,
    innerHeight: 800,
    addEventListener() {},
    removeEventListener() {},
    getSelection() { return null; },
    getComputedStyle() { return { display: 'block', visibility: 'visible', opacity: '1' }; },
  };
  installGlobal('document', document);
  installGlobal('window', window);
  installGlobal('navigator', { clipboard: { writeText: async () => {} } });
  installGlobal('localStorage', {
    store: new Map(),
    getItem(key) { return this.store.has(key) ? this.store.get(key) : null; },
    setItem(key, value) { this.store.set(key, String(value)); },
    removeItem(key) { this.store.delete(key); },
  });
  installGlobal('getComputedStyle', () => ({ display: 'block', visibility: 'visible', opacity: '1' }));
  installGlobal('CSS', { highlights: new Map() });
  installGlobal('Highlight', class Highlight {
    constructor(...ranges) { this.ranges = ranges; }
  });
  return { elements, body };
}

export function mount(id, tag = 'div', extras = {}) {
  const el = createElement(tag);
  el.id = id;
  Object.assign(el, extras);
  elements.set(id, el);
  globalThis.document.body.appendChild(el);
  return el;
}

export function clearDom() {
  elements.clear();
  if (globalThis.document?.body) globalThis.document.body.children = [];
}

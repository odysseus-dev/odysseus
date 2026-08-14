const fs = require('fs');
const path = require('path');
const vm = require('vm');

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.filter(Boolean).forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    if (force === undefined) force = !this.contains(name);
    force ? this.add(name) : this.remove(name);
    return force;
  }
}

class Style {
  constructor() { this.values = {}; this.display = ''; this.cssText = ''; }
  setProperty(name, value) { this.values[name] = value; this[name] = value; }
  removeProperty(name) { delete this.values[name]; delete this[name]; }
}

class Element {
  constructor(tagName, documentRef) {
    this.tagName = String(tagName || 'div').toUpperCase();
    this.ownerDocument = documentRef;
    this.children = [];
    this.parentElement = null;
    this.attributes = {};
    this.dataset = {};
    this.classList = new ClassList();
    this.style = new Style();
    this._listeners = new Map();
    this._innerHTML = '';
  }
  set id(value) { this.attributes.id = String(value); }
  get id() { return this.attributes.id || ''; }
  set className(value) {
    this.classList.values = new Set(String(value || '').split(/\s+/).filter(Boolean));
  }
  get className() { return Array.from(this.classList.values).join(' '); }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (!this._innerHTML) {
      this.children.forEach(child => { child.parentElement = null; });
      this.children = [];
    }
  }
  get innerHTML() { return this._innerHTML; }
  setAttribute(name, value) {
    const text = String(value);
    this.attributes[name] = text;
    if (name === 'class') this.className = text;
    if (name.startsWith('data-')) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
      this.dataset[key] = text;
    }
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  addEventListener(type, handler, options = {}) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push({ handler, once: !!options.once });
  }
  removeEventListener(type, handler) {
    const entries = this._listeners.get(type) || [];
    this._listeners.set(type, entries.filter(entry => entry.handler !== handler));
  }
  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    const entries = [...(this._listeners.get(event.type) || [])];
    for (const entry of entries) {
      entry.handler.call(this, event);
      if (entry.once) this.removeEventListener(event.type, entry.handler);
    }
  }
  click() {
    this.dispatchEvent({ type: 'click', preventDefault() {}, stopPropagation() {} });
  }
  matches(selector) { return matchesSelector(this, selector); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  closest(selector) {
    let node = this;
    while (node) {
      if (matchesSelector(node, selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
}

function simpleMatch(element, selector) {
  const text = selector.trim();
  if (!text) return false;
  const id = text.match(/#([\w-]+)/);
  if (id && element.id !== id[1]) return false;
  for (const match of text.matchAll(/\.([\w-]+)/g)) {
    if (!element.classList.contains(match[1])) return false;
  }
  for (const match of text.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    const actual = element.getAttribute(match[1]);
    if (actual === null) return false;
    if (match[2] !== undefined && actual !== match[2]) return false;
  }
  return true;
}

function matchesSelector(element, selector) {
  return selector.split(',').some(part => simpleMatch(element, part));
}

function descendants(root) {
  const result = [];
  const visit = node => {
    for (const child of node.children || []) {
      result.push(child);
      visit(child);
    }
  };
  visit(root);
  return result;
}

function queryAll(root, selector) {
  const selectors = selector.split(',').map(item => item.trim()).filter(Boolean);
  const nodes = descendants(root);
  const result = [];
  for (const candidate of nodes) {
    if (selectors.some(sel => simpleMatch(candidate, sel))) result.push(candidate);
  }
  return result;
}

class DocumentShim {
  constructor() {
    this.body = new Element('body', this);
    this.head = new Element('head', this);
    this.listeners = new Map();
  }
  createElement(tag) { return new Element(tag, this); }
  getElementById(id) {
    return [this.body, this.head, ...descendants(this.body), ...descendants(this.head)]
      .find(node => node.id === id) || null;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    return [...queryAll(this.body, selector), ...queryAll(this.head, selector)];
  }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  removeEventListener(type, handler) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler));
  }
  dispatch(type, event) {
    for (const handler of [...(this.listeners.get(type) || [])]) handler(event);
  }
}

function buildFixture(document) {
  const modal = document.createElement('div');
  modal.id = 'settings-modal';
  document.body.appendChild(modal);

  const header = document.createElement('div');
  header.className = 'modal-header';
  modal.appendChild(header);

  const close = document.createElement('button');
  close.className = 'close-btn';
  header.appendChild(close);

  const content = document.createElement('div');
  content.className = 'settings-modal-content modal-content';
  modal.appendChild(content);

  const nav = document.createElement('div');
  content.appendChild(nav);
  const panels = document.createElement('div');
  content.appendChild(panels);

  const makeTab = (id, active = false) => {
    const button = document.createElement('button');
    button.setAttribute('data-settings-tab', id);
    if (active) button.classList.add('active');
    nav.appendChild(button);
    const panel = document.createElement('section');
    panel.setAttribute('data-settings-panel', id);
    if (!active) panel.classList.add('hidden');
    panels.appendChild(panel);
    return { button, panel };
  };

  const services = makeTab('services', true);
  const appearance = makeTab('appearance');
  const system = makeTab('system');

  return { modal, header, close, content, services, appearance, system };
}

function moduleSource(relativePath) {
  return fs.readFileSync(path.join(__dirname, '../../static/js/settings', relativePath), 'utf8')
    .replace(/^import\s+.*;\s*$/gm, '')
    .replace(/\bexport\s+/g, '');
}

(function runTests() {
  const document = new DocumentShim();
  const fixture = buildFixture(document);
  const dragCalls = [];
  const dockCalls = [];
  const removedWindowListeners = [];

  const context = {
    console,
    document,
    window: {
      removeEventListener: (...args) => removedWindowListeners.push(args),
      addEventListener() {},
    },
    makeWindowDraggable: (...args) => dragCalls.push(args),
    clearDockSide: (...args) => dockCalls.push(args),
    setTimeout: callback => { callback(); return 1; },
    WeakSet,
  };
  vm.createContext(context);
  vm.runInContext(moduleSource('navigation.js'), context, { filename: 'navigation.js' });
  vm.runInContext(moduleSource('lifecycle.js'), context, { filename: 'lifecycle.js' });
  vm.runInContext(moduleSource('dom.js'), context, { filename: 'dom.js' });

  const results = [];
  const check = (test, pass, detail = '') => results.push({ test, pass: Boolean(pass), detail });

  check('byId resolves elements through the production DOM helper', context.byId('settings-modal') === fixture.modal);

  context.activateSettingsPanel(fixture.modal, 'appearance');
  check(
    'activateSettingsPanel switches sidebar and panel state together',
    fixture.appearance.button.classList.contains('active')
      && !fixture.appearance.panel.classList.contains('hidden')
      && !fixture.services.button.classList.contains('active')
      && fixture.services.panel.classList.contains('hidden'),
  );
  check('getActiveSettingsTab reports the active panel', context.getActiveSettingsTab(fixture.modal) === 'appearance');

  let activated = null;
  let delegated = null;
  context.bindSettingsNavigation(fixture.modal, {
    openAdminTab(tab) { delegated = tab; return tab === 'system'; },
    onPanelActivated(tab) { activated = tab; },
  });
  fixture.services.button.click();
  check(
    'normal navigation activates locally and notifies the coordinator',
    activated === 'services' && fixture.services.button.classList.contains('active'),
  );
  activated = null;
  fixture.system.button.click();
  check(
    'admin navigation delegates without performing a second local activation',
    delegated === 'system' && activated === null && fixture.services.button.classList.contains('active'),
  );

  context.bindSettingsDrag(fixture.modal);
  check(
    'drag binding preserves the existing Settings drag contract',
    dragCalls.length === 1
      && dragCalls[0][0] === fixture.modal
      && dragCalls[0][1].content === fixture.content
      && dragCalls[0][1].header === fixture.header
      && dragCalls[0][1].enableDock === true
      && dragCalls[0][1].skipSelector === 'button, input, select, .theme-opacity-wrap',
  );

  let disconnected = 0;
  fixture.modal.classList.add('modal-left-docked');
  fixture.content.style.setProperty('left', '123px');
  fixture.content.dataset._tileZone = 'left';
  fixture.content._leftDockNavObs = {
    navObs: { disconnect() { disconnected += 1; } },
    reanchor() {},
  };
  context.resetSettingsWindowPlacement(fixture.modal);
  check(
    'window placement reset clears docking observers and inline placement',
    !fixture.modal.classList.contains('modal-left-docked')
      && dockCalls.some(call => call[0] === 'left' && call[1] === fixture.modal)
      && disconnected === 1
      && !('_tileZone' in fixture.content.dataset)
      && fixture.content.style.left === undefined
      && removedWindowListeners.some(call => call[0] === 'resize'),
  );

  fixture.modal.classList.add('modal-right-docked', 'hidden');
  context.showSettingsModal(fixture.modal);
  check(
    'showSettingsModal restores a hidden modal before showing it',
    !fixture.modal.classList.contains('hidden')
      && !fixture.modal.classList.contains('modal-right-docked')
      && dockCalls.some(call => call[0] === 'right'),
  );

  let closeCount = 0;
  context.bindSettingsClose(fixture.modal, {
    closeSettings() { closeCount += 1; },
    isTouchInsideModal() { return false; },
  });

  const form = document.createElement('div');
  form.id = 'unified-intg-form';
  form.style.display = '';
  form.appendChild(document.createElement('input'));
  fixture.content.appendChild(form);
  document.dispatch('keydown', {
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });
  check(
    'Escape closes an inner integration editor before closing Settings',
    form.style.display === 'none' && form.children.length === 0 && closeCount === 0,
  );

  document.dispatch('keydown', {
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });
  check('Escape closes Settings when no nested flow is active', closeCount === 1);

  const popover = document.createElement('div');
  popover.setAttribute('data-popover-open', '1');
  popover.style.display = 'block';
  fixture.content.appendChild(popover);
  document.dispatch('keydown', {
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });
  check('Escape leaves Settings open while a transient popover is active', closeCount === 1);
  popover.classList.add('hidden');

  context.hideSettingsModal(fixture.modal);
  check(
    'hideSettingsModal preserves the closing animation fallback semantics',
    fixture.modal.classList.contains('hidden') && !fixture.content.classList.contains('modal-closing'),
  );

  console.log(JSON.stringify(results));
  if (results.some(result => !result.pass)) process.exitCode = 1;
})();

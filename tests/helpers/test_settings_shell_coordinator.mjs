import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
);

const JS = path.join(REPO, 'static/js');
const SETTINGS_JS = path.join(JS, 'settings.js');

const REAL_MODULES = new Set([
  SETTINGS_JS,
  path.join(JS, 'settings/dom.js'),
  path.join(JS, 'settings/registry.js'),
  path.join(JS, 'settings/search.js'),
  path.join(JS, 'settings/sidebar.js'),
  path.join(JS, 'settings/navigation.js'),
  path.join(JS, 'settings/lifecycle.js'),
]);

const realModulesLoaded = new Set();


class ClassList {
  constructor(values = []) {
    this.values = new Set(values);
  }

  add(...names) {
    names.filter(Boolean).forEach(name => this.values.add(name));
  }

  remove(...names) {
    names.forEach(name => this.values.delete(name));
  }

  contains(name) {
    return this.values.has(name);
  }

  toggle(name, force) {
    const enabled = force === undefined
      ? !this.contains(name)
      : Boolean(force);

    enabled ? this.add(name) : this.remove(name);
    return enabled;
  }
}


class Style {
  constructor() {
    this.display = '';
    this.cssText = '';
    this.values = {};
  }

  setProperty(name, value) {
    this.values[name] = value;
    this[name] = value;
  }

  removeProperty(name) {
    delete this.values[name];
    delete this[name];
  }
}


function dataKey(name) {
  return name
    .slice(5)
    .replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}


function simpleMatch(element, selector) {
  const text = String(selector || '').trim();
  if (!text) return false;

  const tag = text.match(/^[a-zA-Z][\w-]*/);
  if (tag && element.tagName !== tag[0].toUpperCase()) {
    return false;
  }

  const id = text.match(/#([\w-]+)/);
  if (id && element.id !== id[1]) {
    return false;
  }

  for (const match of text.matchAll(/\.([\w-]+)/g)) {
    if (!element.classList.contains(match[1])) {
      return false;
    }
  }

  for (const match of text.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    const actual = element.getAttribute(match[1]);

    if (actual === null) {
      return false;
    }

    if (match[2] !== undefined && actual !== match[2]) {
      return false;
    }
  }

  return true;
}


function descendants(root) {
  const result = [];

  function visit(node) {
    for (const child of node.children || []) {
      result.push(child);
      visit(child);
    }
  }

  visit(root);
  return result;
}


function queryAll(root, selector) {
  const selectors = String(selector)
    .split(',')
    .map(value => value.trim())
    .filter(Boolean);

  return descendants(root).filter(
    node => selectors.some(value => simpleMatch(node, value)),
  );
}


class Element {
  constructor(tagName = 'div', documentRef = null) {
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = documentRef;

    this.children = [];
    this.parentElement = null;

    this.attributes = {};
    this.dataset = {};

    this.classList = new ClassList();
    this.style = new Style();

    this._listeners = new Map();

    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.selected = false;
    this.hidden = false;

    this.textContent = '';
    this.innerHTML = '';

    this.options = [];
    this.selectedOptions = [];
    this.files = [];

    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 100;
    this.clientWidth = 100;
  }

  set id(value) {
    this.attributes.id = String(value);
  }

  get id() {
    return this.attributes.id || '';
  }

  set className(value) {
    this.classList = new ClassList(
      String(value || '').split(/\s+/).filter(Boolean),
    );
  }

  get className() {
    return [...this.classList.values].join(' ');
  }

  setAttribute(name, value) {
    const text = String(value);
    this.attributes[name] = text;

    if (name === 'class') {
      this.className = text;
    }

    if (name.startsWith('data-')) {
      this.dataset[dataKey(name)] = text;
    }
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  removeAttribute(name) {
    delete this.attributes[name];

    if (name.startsWith('data-')) {
      delete this.dataset[dataKey(name)];
    }
  }

  toggleAttribute(name, force) {
    const enabled = force === undefined
      ? this.getAttribute(name) === null
      : Boolean(force);

    if (enabled) this.setAttribute(name, '');
    else this.removeAttribute(name);

    return enabled;
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    children.forEach(child => this.appendChild(child));
  }

  prepend(...children) {
    for (const child of [...children].reverse()) {
      child.parentElement = this;
      this.children.unshift(child);
    }
  }

  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }

  insertBefore(child, before) {
    const index = this.children.indexOf(before);

    if (index === -1) {
      return this.appendChild(child);
    }

    child.parentElement = this;
    this.children.splice(index, 0, child);
    return child;
  }

  insertAdjacentHTML() {}

  remove() {
    if (!this.parentElement) return;

    this.parentElement.children =
      this.parentElement.children.filter(child => child !== this);

    this.parentElement = null;
  }

  addEventListener(type, handler, options = {}) {
    if (!this._listeners.has(type)) {
      this._listeners.set(type, []);
    }

    this._listeners.get(type).push({
      handler,
      once: Boolean(options?.once),
    });
  }

  removeEventListener(type, handler) {
    const listeners = this._listeners.get(type) || [];

    this._listeners.set(
      type,
      listeners.filter(entry => entry.handler !== handler),
    );
  }

  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;

    for (const entry of [...(this._listeners.get(event.type) || [])]) {
      entry.handler.call(this, event);

      if (entry.once) {
        this.removeEventListener(event.type, entry.handler);
      }
    }

    return true;
  }

  click() {
    this.dispatchEvent({
      type: 'click',
      target: this,
      preventDefault() {},
      stopPropagation() {},
    });
  }

  querySelector(selector) {
    const result = queryAll(this, selector)[0];

    if (result) return result;

    const id = String(selector).match(/^#([\w-]+)$/);
    if (id && this.ownerDocument) {
      return this.ownerDocument.getElementById(id[1]);
    }

    return null;
  }

  querySelectorAll(selector) {
    return queryAll(this, selector);
  }

  matches(selector) {
    return String(selector)
      .split(',')
      .some(value => simpleMatch(this, value));
  }

  closest(selector) {
    let node = this;

    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }

    return null;
  }

  focus() {}
  blur() {}
  select() {}
  scrollIntoView() {}
  setSelectionRange() {}

  getBoundingClientRect() {
    return {
      top: 0,
      left: 0,
      right: 100,
      bottom: 100,
      width: 100,
      height: 100,
    };
  }
}


class DocumentShim {
  constructor() {
    this._generated = new Map();
    this._listeners = new Map();

    // Pre-existing Settings initializers sometimes inspect an element's
    // parent container. Generated fallback elements therefore live under one
    // inert root instead of being detached DOM nodes.
    this._generatedRoot = new Element('div', this);

    this.readyState = 'loading';

    this.body = new Element('body', this);
    this.head = new Element('head', this);
    this.documentElement = new Element('html', this);
  }

  createElement(tagName) {
    return new Element(tagName, this);
  }

  createDocumentFragment() {
    return new Element('fragment', this);
  }

  addEventListener(type, handler, options = {}) {
    if (!this._listeners.has(type)) {
      this._listeners.set(type, []);
    }

    this._listeners.get(type).push({
      handler,
      once: Boolean(options?.once),
    });
  }

  removeEventListener(type, handler) {
    const listeners = this._listeners.get(type) || [];

    this._listeners.set(
      type,
      listeners.filter(entry => entry.handler !== handler),
    );
  }

  getElementById(id) {
    const real = [
      this.body,
      this.head,
      ...descendants(this.body),
      ...descendants(this.head),
    ].find(node => node.id === id);

    if (real) return real;

    if (!this._generated.has(id)) {
      const generated = new Element('div', this);
      generated.id = id;
      this._generatedRoot.appendChild(generated);
      this._generated.set(id, generated);
    }

    return this._generated.get(id);
  }

  querySelector(selector) {
    const result = this.querySelectorAll(selector)[0];
    if (result) return result;

    const id = String(selector).match(/^#([\w-]+)$/);
    return id ? this.getElementById(id[1]) : null;
  }

  querySelectorAll(selector) {
    return [
      ...queryAll(this.body, selector),
      ...queryAll(this.head, selector),
    ];
  }

  getElementsByClassName(name) {
    return this.querySelectorAll(`.${name}`);
  }

  getElementsByTagName(name) {
    return this.querySelectorAll(name);
  }
}


function makeTab(document, nav, panels, name, active = false) {
  const button = document.createElement('button');
  button.setAttribute('data-settings-tab', name);

  if (active) {
    button.classList.add('active');
  }

  nav.appendChild(button);

  const panel = document.createElement('section');
  panel.setAttribute('data-settings-panel', name);

  if (!active) {
    panel.classList.add('hidden');
  }

  panels.appendChild(panel);

  return { button, panel };
}


function buildFixture(document) {
  const modal = document.createElement('div');
  modal.id = 'settings-modal';
  modal.classList.add('hidden');
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

  const sidebar = document.createElement('div');
  sidebar.className = 'settings-sidebar';
  content.appendChild(sidebar);

  const sidebarToggle = document.createElement('button');
  sidebarToggle.id = 'settings-sidebar-toggle';
  sidebar.appendChild(sidebarToggle);

  const sidebarHandle = document.createElement('div');
  sidebarHandle.id = 'settings-sidebar-resize-handle';
  sidebar.appendChild(sidebarHandle);

  const sidebarContent = document.createElement('div');
  sidebarContent.className = 'settings-sidebar-content';
  sidebar.appendChild(sidebarContent);

  const finder = document.createElement('div');
  finder.className = 'settings-nav-search-wrap';
  sidebarContent.appendChild(finder);

  const searchInput = document.createElement('input');
  searchInput.id = 'settings-nav-search';
  finder.appendChild(searchInput);

  const searchResults = document.createElement('div');
  searchResults.id = 'settings-nav-search-results';
  searchResults.className = 'settings-nav-search-results';
  searchResults.classList.add('hidden');
  finder.appendChild(searchResults);

  const panels = document.createElement('div');
  panels.className = 'settings-panels';
  content.appendChild(panels);

  const panelIds = [
    'services',
    'added-models',
    'ai',
    'search',
    'integrations',
    'email',
    'reminders',
    'appearance',
    'shortcuts',
    'account',
    'tools',
    'users',
    'system',
  ];

  const settingsPanels = {};

  panelIds.forEach((name, index) => {
    const button = document.createElement('button');
    button.setAttribute('data-settings-tab', name);

    if (index === 0) {
      button.classList.add('active');
    }

    sidebarContent.appendChild(button);

    const panel = document.createElement('section');
    panel.setAttribute('data-settings-panel', name);

    if (index !== 0) {
      panel.classList.add('hidden');
    }

    panels.appendChild(panel);

    settingsPanels[name] = {
      button,
      panel,
    };
  });

  return {
    modal,
    content,
    sidebar,
    sidebarToggle,
    sidebarHandle,
    searchInput,
    searchResults,
    services: settingsPanels.services,
    appearance: settingsPanels.appearance,
    ai: settingsPanels.ai,
    system: settingsPanels.system,
    settingsPanels,
  };
}


function functionProxy(overrides = {}) {
  return new Proxy(overrides, {
    get(target, property) {
      if (property in target) {
        return target[property];
      }

      return () => [];
    },
  });
}


const document = new DocumentShim();
const fixture = buildFixture(document);

const sandbox = {
  console,
  document,

  location: {
    search: '',
    pathname: '/',
    hash: '',
    origin: 'http://localhost',
  },

  history: {
    replaceState() {},
  },

  adminModule: null,

  navigator: {
    userAgent: 'node-settings-shell-test',
    clipboard: {
      async writeText() {},
    },
  },

  CSS: {
    escape(value) {
      return String(value);
    },
  },

  localStorage: {
    getItem() { return null; },
    setItem() {},
    removeItem() {},
  },

  sessionStorage: {
    getItem() { return null; },
    setItem() {},
    removeItem() {},
  },

  MutationObserver: class {
    observe() {}
    disconnect() {}
  },

  ResizeObserver: class {
    observe() {}
    disconnect() {}
  },

  IntersectionObserver: class {
    observe() {}
    disconnect() {}
  },

  CustomEvent: class {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  },

  Event: class {
    constructor(type) {
      this.type = type;
    }
  },

  Option: class extends Element {
    constructor(text = '', value = '') {
      super('option', document);
      this.textContent = text;
      this.value = value;
    }
  },

  Image: class extends Element {
    constructor() {
      super('img', document);
    }
  },

  URL,
  URLSearchParams,
  TextEncoder,
  TextDecoder,
  AbortController,

  fetch() {
    // Keep unrelated asynchronous Settings initializers suspended. The shell
    // behavior asserted below is synchronous and intentionally backend-free.
    return new Promise(() => {});
  },

  requestAnimationFrame(callback) {
    callback?.();
    return 1;
  },

  cancelAnimationFrame() {},

  setTimeout(callback, delay) {
    // Preserve lifecycle.js's close-animation fallback without introducing
    // real delays into the test.
    if (delay === 250) {
      callback?.();
    }

    return 1;
  },

  clearTimeout() {},

  setInterval() {
    return 1;
  },

  clearInterval() {},

  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {},

  matchMedia() {
    return {
      matches: false,
      addEventListener() {},
      removeEventListener() {},
    };
  },

  getComputedStyle() {
    return {};
  },

  innerWidth: 1280,
  innerHeight: 720,

  alert() {},
  confirm() { return true; },
  prompt() { return ''; },
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;

const context = vm.createContext(sandbox);


const uiModule = functionProxy({
  esc(value) {
    return String(value ?? '');
  },

  isTouchInsideModal() {
    return false;
  },
});

const searchModule = functionProxy();


const STUBS = new Map([
  [
    path.join(JS, 'ui.js'),
    { default: uiModule },
  ],
  [
    path.join(JS, 'search.js'),
    { default: searchModule },
  ],
  [
    path.join(JS, 'modelSort.js'),
    {
      sortModelIds(values) {
        return values || [];
      },
    },
  ],
  [
    path.join(JS, 'providers.js'),
    {
      providerLogo() {
        return '';
      },
    },
  ],
  [
    path.join(JS, 'platform.js'),
    {
      isAltGrEvent() {
        return false;
      },
    },
  ],
  [
    path.join(JS, 'escMenuStack.js'),
    {
      bindMenuDismiss() {},
    },
  ],
  [
    path.join(JS, 'appConfig.js'),
    {
      invalidateSettings() {},
    },
  ],
  [
    path.join(JS, 'windowDrag.js'),
    {
      makeWindowDraggable() {},
    },
  ],
  [
    path.join(JS, 'modalSnap.js'),
    {
      clearDockSide() {},
    },
  ],
]);


const moduleCache = new Map();


function resolveImport(specifier, parent) {
  if (!specifier.startsWith('.')) {
    throw new Error(`Unexpected non-relative import: ${specifier}`);
  }

  return path.resolve(
    path.dirname(parent),
    specifier,
  );
}


function syntheticModule(identifier, exports) {
  return new vm.SyntheticModule(
    Object.keys(exports),
    function initialize() {
      for (const [name, value] of Object.entries(exports)) {
        this.setExport(name, value);
      }
    },
    {
      context,
      identifier,
    },
  );
}


async function loadModule(filename) {
  const resolved = path.resolve(filename);

  if (moduleCache.has(resolved)) {
    return moduleCache.get(resolved);
  }

  if (STUBS.has(resolved)) {
    const module = syntheticModule(
      resolved,
      STUBS.get(resolved),
    );

    moduleCache.set(resolved, module);
    return module;
  }

  if (!REAL_MODULES.has(resolved)) {
    throw new Error(
      `Unexpected real module requested by coordinator smoke: ${resolved}`,
    );
  }

  realModulesLoaded.add(resolved);

  const source = fs.readFileSync(resolved, 'utf8');

  const module = new vm.SourceTextModule(source, {
    context,
    identifier: resolved,

    importModuleDynamically: async specifier => {
      const target = resolveImport(specifier, resolved);

      if (target === path.join(JS, 'presets.js')) {
        const dynamic = syntheticModule(target, {
          openCustomPresetModal() {},
          default: {},
        });

        await dynamic.link(() => {});
        await dynamic.evaluate();

        return dynamic;
      }

      throw new Error(
        `Unexpected dynamic import from Settings smoke: ${specifier}`,
      );
    },
  });

  moduleCache.set(resolved, module);
  return module;
}


async function linker(specifier, referencingModule) {
  const target = resolveImport(
    specifier,
    referencingModule.identifier,
  );

  return loadModule(target);
}


function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}


// ---------------------------------------------------------------------------
// Load the real settings.js coordinator.
//
// Its new Settings shell dependencies are real files too. Only unrelated
// pre-existing dependencies are synthetic stubs.
// ---------------------------------------------------------------------------

const coordinator = await loadModule(SETTINGS_JS);

await coordinator.link(linker);
await coordinator.evaluate();

const settings = coordinator.namespace;

assert(
  typeof settings.open === 'function',
  'real settings.js did not export open()',
);

assert(
  typeof settings.close === 'function',
  'real settings.js did not export close()',
);


// A broken path/export in any new shell module must fail during native ESM
// linking before these assertions can run.
for (const required of REAL_MODULES) {
  assert(
    realModulesLoaded.has(required),
    `real ESM graph did not load ${path.relative(REPO, required)}`,
  );
}


// First open drives initAll() and therefore the real shell bindings.
settings.open('services');

assert(
  !fixture.modal.classList.contains('hidden'),
  'first open() did not show Settings',
);

assert(
  fixture.services.button.classList.contains('active'),
  'first open("services") did not activate Services',
);


// #6040 coordinator integration: initAll() must bind the real finder and
// sidebar controllers, not merely make their modules link successfully.
assert(
  (fixture.searchInput._listeners.get('input') || []).length > 0,
  'initAll() did not bind the Settings finder',
);

assert(
  (fixture.sidebarToggle._listeners.get('click') || []).length > 0
    && (fixture.sidebarHandle._listeners.get('pointerdown') || []).length > 0,
  'initAll() did not bind the Settings sidebar controls',
);

assert(
  fixture.sidebar.style.values['--settings-sidebar-width'] === '220px',
  'first coordinator initialization did not apply the 220px sidebar default',
);


// Exercise settings.js -> bindSettingsSearch() -> real registry.js.
fixture.searchInput.value = 'theme';
fixture.searchInput.dispatchEvent({
  type: 'input',
  preventDefault() {},
  stopPropagation() {},
});

const coordinatorSearchResult = fixture.searchResults.querySelector(
  '[data-settings-search-result]',
);

assert(
  coordinatorSearchResult?.dataset?.settingsSearchResult === 'appearance',
  'coordinator-bound finder did not resolve "theme" to Appearance',
);


// Navigation must travel through bindSettingsNavigation() installed by
// initAll(), then invoke settings.js's coordinator callback.
fixture.appearance.button.click();

assert(
  fixture.appearance.button.classList.contains('active'),
  'navigation click did not activate Appearance',
);

assert(
  !fixture.appearance.panel.classList.contains('hidden'),
  'navigation click did not show Appearance panel',
);

assert(
  document.body.classList.contains('settings-appearance-open'),
  'navigation callback did not apply Appearance coordinator state',
);


// Direct public open() after initialization must still coordinate activation.
settings.open('ai');

assert(
  fixture.ai.button.classList.contains('active'),
  'direct open("ai") did not activate AI',
);

assert(
  !fixture.ai.panel.classList.contains('hidden'),
  'direct open("ai") did not show AI panel',
);

assert(
  !document.body.classList.contains('settings-appearance-open'),
  'direct open("ai") did not clear Appearance coordinator state',
);


// Public close() must route through the real lifecycle module.
settings.close();

assert(
  fixture.modal.classList.contains('hidden'),
  'close() did not hide Settings',
);

assert(
  !document.body.classList.contains('settings-appearance-open'),
  'close() left Appearance coordinator state behind',
);


// initAll() starts some existing async panel initializers without awaiting
// them. Give already-ready continuations a chance to run before declaring the
// smoke successful, so late coordinator/setup exceptions still fail the test.
await Promise.resolve();
await new Promise(resolve => setImmediate(resolve));

console.log(JSON.stringify({
  realEsmGraph: true,
  initialization: true,
  finderBinding: true,
  sidebarBinding: true,
  navigationCallback: true,
  directOpen: true,
  directClose: true,
}));

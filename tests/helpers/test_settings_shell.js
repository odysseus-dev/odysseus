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
    this._textContent = '';
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
  set textContent(value) {
    this._textContent = String(value ?? '');
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
  }
  get textContent() {
    return this._textContent + this.children.map(child => child.textContent).join('');
  }
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
  replaceChildren(...children) {
    this.children.forEach(child => { child.parentElement = null; });
    this.children = [];
    children.forEach(child => this.appendChild(child));
  }
  contains(candidate) {
    if (candidate === this) return true;
    return descendants(this).includes(candidate);
  }
  getBoundingClientRect() {
    const width = Number.parseFloat(
      this.style.values['--settings-sidebar-width']
        || this.style.width
        // This is only a desktop-mode fixture (>620px), not a CSS assertion.
        || (this.classList.contains('settings-modal-content') ? '800' : '220'),
    );
    return { width, height: 600, left: 0, right: width, top: 0, bottom: 600 };
  }
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
  nav.className = 'settings-sidebar';
  content.appendChild(nav);

  const sidebarToggle = document.createElement('button');
  sidebarToggle.id = 'settings-sidebar-toggle';
  nav.appendChild(sidebarToggle);

  const sidebarHandle = document.createElement('div');
  sidebarHandle.id = 'settings-sidebar-resize-handle';
  nav.appendChild(sidebarHandle);

  const sidebarContent = document.createElement('div');
  sidebarContent.className = 'settings-sidebar-content';
  nav.appendChild(sidebarContent);

  const finder = document.createElement('div');
  sidebarContent.appendChild(finder);

  const searchInput = document.createElement('input');
  searchInput.id = 'settings-nav-search';
  finder.appendChild(searchInput);

  const searchResults = document.createElement('div');
  searchResults.id = 'settings-nav-search-results';
  searchResults.classList.add('hidden');
  finder.appendChild(searchResults);

  const panels = document.createElement('div');
  content.appendChild(panels);

  const makeTab = (id, active = false) => {
    const button = document.createElement('button');
    button.setAttribute('data-settings-tab', id);
    if (active) button.classList.add('active');
    sidebarContent.appendChild(button);
    const panel = document.createElement('section');
    panel.setAttribute('data-settings-panel', id);
    if (!active) panel.classList.add('hidden');
    panels.appendChild(panel);
    return { button, panel };
  };

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

  const settingsPanels = Object.fromEntries(
    panelIds.map((id, index) => [id, makeTab(id, index === 0)]),
  );

  return {
    modal,
    header,
    close,
    content,
    services: settingsPanels.services,
    appearance: settingsPanels.appearance,
    system: settingsPanels.system,
    settingsPanels,
    searchInput,
    searchResults,
    sidebar: nav,
    sidebarToggle,
    sidebarHandle,
  };
}

function moduleSource(relativePath) {
  let source = fs.readFileSync(
    path.join(__dirname, '../../static/js/settings', relativePath),
    'utf8',
  );

  // The production files are real ES modules. This lightweight VM harness
  // removes imports because dependencies are loaded into the same context,
  // but each module still needs its own lexical scope so private const/let
  // bindings do not collide across modules.
  source = source.replace(/^\s*import[\s\S]*?;\s*$/gm, '');

  // Preserve exported API on globalThis while keeping all non-exported
  // bindings private inside the module block below.
  source = source
    .replace(
      /\bexport\s+function\s+([A-Za-z_$][\w$]*)\s*\(/g,
      'globalThis.$1 = function $1(',
    )
    .replace(
      /\bexport\s+const\s+([A-Za-z_$][\w$]*)\s*=/g,
      'globalThis.$1 =',
    )
    .replace(
      /\bexport\s+let\s+([A-Za-z_$][\w$]*)\s*=/g,
      'globalThis.$1 =',
    )
    .replace(
      /\bexport\s+var\s+([A-Za-z_$][\w$]*)\s*=/g,
      'globalThis.$1 =',
    );

  return `{\n${source}\n}`;
}

(function runTests() {
  const document = new DocumentShim();
  const fixture = buildFixture(document);
  const dragCalls = [];
  const dockCalls = [];
  const removedWindowListeners = [];

  const storage = new Map();
  const context = {
    console,
    document,
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
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
  vm.runInContext(moduleSource('registry.js'), context, { filename: 'registry.js' });
  vm.runInContext(moduleSource('search.js'), context, { filename: 'search.js' });
  vm.runInContext(moduleSource('sidebar.js'), context, { filename: 'sidebar.js' });
  vm.runInContext(moduleSource('navigation.js'), context, { filename: 'navigation.js' });
  vm.runInContext(moduleSource('lifecycle.js'), context, { filename: 'lifecycle.js' });
  vm.runInContext(moduleSource('dom.js'), context, { filename: 'dom.js' });

  const results = [];
  const check = (test, pass, detail = '') => results.push({ test, pass: Boolean(pass), detail });

  const registryPanelIds = vm.runInContext(
    'SETTINGS_PANELS.map(panel => panel.id).join(",")',
    context,
  );
  const registryGroupIds = vm.runInContext(
    'SETTINGS_GROUPS.map(group => group.id).join(",")',
    context,
  );

  check(
    'Settings registry preserves the existing sidebar panel order',
    registryPanelIds === [
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
    ].join(','),
  );

  check(
    'Settings registry defines the intended information-architecture groups',
    registryGroupIds === [
      'models',
      'communications',
      'experience',
      'account',
      'administration',
    ].join(','),
  );

  check(
    'Settings registry keeps services, models, integrations and admin panels on the existing admin controller',
    ['services', 'added-models', 'integrations', 'tools', 'users', 'system']
      .every(id => context.isAdminManagedSettingsTab(id))
      && ['ai', 'search', 'email', 'reminders', 'appearance', 'shortcuts', 'account']
        .every(id => !context.isAdminManagedSettingsTab(id)),
  );

  check(
    'Settings registry distinguishes admin-only visibility from admin-controlled routing',
    ['tools', 'users', 'system'].every(id => context.isAdminOnlySettingsTab(id))
      && ['services', 'added-models', 'integrations']
        .every(id => !context.isAdminOnlySettingsTab(id)),
  );

  check(
    'Settings registry provides search metadata without owning search UI',
    context.getSettingsPanelSearchText('appearance').includes('theme')
      && context.getSettingsPanelSearchText('email').includes('smtp')
      && context.getSettingsPanelSearchText('missing') === '',
  );

  check(
    'Settings registry exposes group membership in sidebar order',
    context.getSettingsPanelsForGroup('communications')
      .map(panel => panel.id)
      .join(',') === 'integrations,email,reminders',
  );

  check(
    'Settings registry matches every production-style tab and panel in the DOM fixture',
    context.getSettingsRegistryIssues(fixture.modal).length === 0,
  );

  check(
    'Settings search resolves metadata terms in registry order',
    context.searchSettingsPanels('provider', { isAdmin: true })
      .map(panel => panel.id)
      .join(',') === 'services,added-models,search',
  );

  check(
    'Settings search excludes admin-only panels for non-admin users',
    context.searchSettingsPanels('agent tools', { isAdmin: false }).length === 0
      && context.searchSettingsPanels('agent tools', { isAdmin: true })
        .map(panel => panel.id)
        .join(',') === 'tools',
  );

  check(
    'Settings search requires all query terms',
    context.searchSettingsPanels('appearance theme', { isAdmin: false })
      .map(panel => panel.id)
      .join(',') === 'appearance',
  );

  let searchedPanel = null;
  context.bindSettingsSearch(fixture.modal, {
    isAdmin: () => false,
    openPanel(tab) { searchedPanel = tab; },
  });

  fixture.searchInput.value = 'theme';
  fixture.searchInput.dispatchEvent({
    type: 'input',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder renders matching production registry results',
    !fixture.searchResults.classList.contains('hidden')
      && fixture.searchResults.querySelectorAll('[data-settings-search-result]').length === 1
      && fixture.searchResults.querySelector('[data-settings-search-result]').dataset.settingsSearchResult === 'appearance',
  );

  const finderOutsideTarget = document.createElement('div');
  fixture.content.appendChild(finderOutsideTarget);

  fixture.modal.dispatchEvent({
    type: 'mousedown',
    target: finderOutsideTarget,
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder click-away hides results while retaining the query',
    fixture.searchInput.value === 'theme'
      && fixture.searchResults.classList.contains('hidden'),
  );

  fixture.searchInput.dispatchEvent({
    type: 'focus',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder refocus restores results for an unchanged retained query',
    fixture.searchInput.value === 'theme'
      && !fixture.searchResults.classList.contains('hidden')
      && fixture.searchResults.querySelectorAll('[data-settings-search-result]').length === 1,
  );

  fixture.searchInput.dispatchEvent({
    type: 'keydown',
    key: 'Enter',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder Enter opens the first result and resets the finder',
    searchedPanel === 'appearance'
      && fixture.searchInput.value === ''
      && fixture.searchResults.classList.contains('hidden'),
  );

  searchedPanel = null;
  fixture.searchInput.value = 'agent tools';
  fixture.searchInput.dispatchEvent({
    type: 'input',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder does not expose admin-only results to non-admin users',
    fixture.searchResults.querySelectorAll('[data-settings-search-result]').length === 0
      && fixture.searchResults.textContent !== '',
  );

  fixture.searchInput.value = 'theme';
  fixture.searchInput.dispatchEvent({
    type: 'input',
    preventDefault() {},
    stopPropagation() {},
  });
  fixture.searchInput.dispatchEvent({
    type: 'keydown',
    key: 'Escape',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'Settings finder Escape clears results without closing Settings',
    fixture.searchInput.value === ''
      && fixture.searchResults.classList.contains('hidden'),
  );

  context.setSettingsSidebarWidth(fixture.modal, 280);
  check(
    'Settings sidebar width is clamped and applied through the production controller',
    fixture.sidebar.style.values['--settings-sidebar-width'] === '280px',
  );

  const widthStorageKey = 'odysseus-settings-sidebar-width';

  storage.clear();
  const firstRunSidebar = buildFixture(document);
  context.bindSettingsSidebar(firstRunSidebar.modal);
  check(
    'Settings sidebar first bind uses the declared 220px default when storage is empty',
    firstRunSidebar.sidebar.style.values['--settings-sidebar-width'] === '220px',
  );

  storage.clear();
  storage.set(widthStorageKey, '276');
  const storedSidebar = buildFixture(document);
  context.bindSettingsSidebar(storedSidebar.modal);
  check(
    'Settings sidebar first bind restores a valid stored width',
    storedSidebar.sidebar.style.values['--settings-sidebar-width'] === '276px',
  );

  storage.clear();
  storage.set(widthStorageKey, 'not-a-number');
  const malformedSidebar = buildFixture(document);
  context.bindSettingsSidebar(malformedSidebar.modal);
  check(
    'Settings sidebar first bind falls back to default for malformed storage',
    malformedSidebar.sidebar.style.values['--settings-sidebar-width'] === '220px',
  );

  storage.clear();
  storage.set(widthStorageKey, '10');
  const minimumSidebar = buildFixture(document);
  context.bindSettingsSidebar(minimumSidebar.modal);

  storage.clear();
  storage.set(widthStorageKey, '999');
  const maximumSidebar = buildFixture(document);
  context.bindSettingsSidebar(maximumSidebar.modal);

  check(
    'Settings sidebar first bind clamps stored widths to declared bounds',
    minimumSidebar.sidebar.style.values['--settings-sidebar-width'] === '150px'
      && maximumSidebar.sidebar.style.values['--settings-sidebar-width'] === '340px',
  );

  context.setSettingsSidebarCollapsed(fixture.modal, true);
  check(
    'Settings sidebar collapse leaves the compact navigation rail state active',
    fixture.sidebar.classList.contains('settings-sidebar-collapsed')
      && fixture.sidebarToggle.getAttribute('aria-label') === 'Expand settings navigation',
  );

  context.setSettingsSidebarCollapsed(fixture.modal, false, { width: 260 });
  check(
    'Settings sidebar expansion restores the requested expanded width',
    !fixture.sidebar.classList.contains('settings-sidebar-collapsed')
      && fixture.sidebar.style.values['--settings-sidebar-width'] === '260px',
  );

  // Keyboard behavior must be exercised on a fixture that went through the
  // real binding path; direct controller calls above intentionally do not bind
  // event listeners.
  storage.clear();
  const keyboardSidebar = buildFixture(document);
  context.bindSettingsSidebar(keyboardSidebar.modal);

  context.setSettingsSidebarWidth(
    keyboardSidebar.modal,
    150,
    { persist: false },
  );

  keyboardSidebar.sidebarHandle.dispatchEvent({
    type: 'keydown',
    key: 'ArrowLeft',
    preventDefault() {},
    stopPropagation() {},
  });

  check(
    'ArrowLeft at the sidebar minimum collapses the rail instead of getting stuck at 150px',
    keyboardSidebar.sidebar.classList.contains('settings-sidebar-collapsed'),
  );

  context.setSettingsSidebarCollapsed(
    keyboardSidebar.modal,
    false,
    { width: 220, persist: false },
  );

  check(
    'resizable Settings separator exposes its current ARIA range and value',
    keyboardSidebar.sidebarHandle.getAttribute('aria-valuemin') === '150'
      && keyboardSidebar.sidebarHandle.getAttribute('aria-valuemax') === '340'
      && keyboardSidebar.sidebarHandle.getAttribute('aria-valuenow') === '220',
  );

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

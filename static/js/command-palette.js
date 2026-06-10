// ============================================
// Command palette — "Search Everywhere" modal
// ============================================
// Double-Shift (or the search_everywhere keybind) opens a fuzzy palette over
// the command registry: tool windows, actions and individual settings.
// Mirrors search-chat.js structure. DOM focus stays on the input; selection
// is virtual via aria-activedescendant.
//
// Esc dismissal goes through the existing arbiter: registered in escMenuStack
// on open (LIFO) AND short-circuited by a highest-priority branch in ui.js's
// _odyEscExpandGuard via window._odyCmdPalette — never a competing bubbling
// listener.

import { fuzzyFilter, highlightRuns } from './fuzzy.js';
import * as registry from './command-registry.js';
import uiModule from './ui.js';
import { registerMenuDismiss } from './escMenuStack.js';

const SECTION_ORDER = ['Windows', 'Settings', 'Actions'];
const TABS = ['all', ...SECTION_ORDER]; // category tabs; Tab key cycles them
const DEBOUNCE_MS = 120;

let _results = [];      // flat [{item, score, positions}] in rendered order
let _active = -1;
let _activeTab = 'all';
let _returnFocus = null;
let _unregEsc = () => {};
let _debounce = null;

function el(id) { return document.getElementById(id); }

// Build the title element from runs via textContent / DOM nodes ONLY —
// titles include DOM-scraped user-influenced text (settings-index.js), so raw
// innerHTML concatenation would be an XSS hole.
function _buildTitle(title, positions) {
  const span = document.createElement('span');
  span.className = 'cmd-palette-title';
  for (const run of highlightRuns(title, positions)) {
    if (run.hl) {
      const mark = document.createElement('mark');
      mark.className = 'search-highlight';
      mark.textContent = run.text;
      span.appendChild(mark);
    } else {
      span.appendChild(document.createTextNode(run.text));
    }
  }
  return span;
}

export function isOpen() {
  const o = el('cmd-palette-overlay');
  return !!o && !o.hidden;
}

export function open() {
  const o = el('cmd-palette-overlay');
  if (!o || !o.hidden) return;
  _returnFocus = document.activeElement;
  o.hidden = false;
  document.body.classList.add('cmd-palette-scroll-lock');
  const input = el('cmd-palette-input');
  input.value = '';
  setTab('all'); // fresh open always starts on All (also renders)
  input.focus();
  // LIFO Esc registration — the ui.js arbiter pops this before any modal.
  _unregEsc = registerMenuDismiss(() => close());
}

/** @param {boolean} restoreFocus - true on dismissal (Esc / overlay click); false after a successful activate, where focus belongs to the performed command. */
export function close(restoreFocus = true) {
  const o = el('cmd-palette-overlay');
  if (!o || o.hidden) return;
  o.hidden = true;
  document.body.classList.remove('cmd-palette-scroll-lock');
  if (_debounce) { clearTimeout(_debounce); _debounce = null; }
  _unregEsc();
  _unregEsc = () => {};
  if (restoreFocus && _returnFocus && typeof _returnFocus.focus === 'function') {
    try { _returnFocus.focus(); } catch {}
  }
  _returnFocus = null;
  _results = [];
  _active = -1;
}

// Switch the category tab (clicks + Tab-key cycling) and re-render with the
// current query.
function setTab(tab) {
  _activeTab = TABS.includes(tab) ? tab : 'all';
  el('cmd-palette-tabs').querySelectorAll('.cmd-palette-tab').forEach(btn => {
    const on = btn.dataset.tab === _activeTab;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  render(el('cmd-palette-input').value.trim());
}

function render(q) {
  const list = el('cmd-palette-list');
  const input = el('cmd-palette-input');
  list.textContent = '';
  _results = [];
  _active = -1;

  let items = registry.getItems();
  if (_activeTab !== 'all') items = items.filter(it => it.section === _activeTab);
  const matches = fuzzyFilter(q, items,
    it => it.title + ' ' + (it.keywords || []).join(' '));

  if (!matches.length) {
    const empty = document.createElement('div');
    empty.className = 'cmd-palette-empty';
    empty.textContent = _activeTab === 'all'
      ? `No matches for "${q}"`
      : `No matches for "${q}" in ${_activeTab}`;
    list.appendChild(empty);
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    return;
  }

  // Group by section; headers render ONLY for sections with ≥1 result.
  const groups = new Map();
  for (const m of matches) {
    const s = m.item.section || 'Other';
    if (!groups.has(s)) groups.set(s, []);
    groups.get(s).push(m);
  }
  const order = [
    ...SECTION_ORDER.filter(s => groups.has(s)),
    ...[...groups.keys()].filter(s => !SECTION_ORDER.includes(s)),
  ];

  let idx = 0;
  for (const section of order) {
    if (_activeTab === 'all') { // headers are noise on a single-category tab
      const header = document.createElement('div');
      header.className = 'search-group-header';
      header.setAttribute('role', 'presentation');
      header.textContent = section;
      list.appendChild(header);
    }
    for (const m of groups.get(section)) {
      const row = document.createElement('div');
      // Reuse the chat-search row styling (CONTRIBUTING: no parallel widgets).
      row.className = 'search-result-item';
      row.id = 'cmd-palette-opt-' + idx;
      row.setAttribute('role', 'option');
      row.setAttribute('aria-selected', 'false');
      row.dataset.index = String(idx);
      row.appendChild(_buildTitle(m.item.title, m.positions));
      row.addEventListener('click', () => {
        setActive(Number(row.dataset.index));
        activate();
      });
      list.appendChild(row);
      _results.push(m);
      idx++;
    }
  }
  input.setAttribute('aria-expanded', 'true');
  setActive(0);
}

function setActive(i) {
  if (!_results.length) return;
  _active = Math.max(0, Math.min(i, _results.length - 1));
  const list = el('cmd-palette-list');
  list.querySelectorAll('.search-result-item').forEach(row => {
    const sel = Number(row.dataset.index) === _active;
    row.classList.toggle('selected', sel);
    row.setAttribute('aria-selected', sel ? 'true' : 'false');
    if (sel) row.scrollIntoView({ block: 'nearest' });
  });
  el('cmd-palette-input').setAttribute('aria-activedescendant', 'cmd-palette-opt-' + _active);
}

function move(delta) { setActive(_active + delta); }

function activate() {
  const m = _results[_active];
  if (!m) return;
  let ok = false;
  try {
    ok = m.item.perform() !== false;
  } catch (err) {
    console.error('command-palette: perform() failed for', m.item.id, err);
  }
  // Close ONLY on success; a failure keeps the palette open with a visible
  // toast (palette z-index 50000 sits below the 99999 toast layer).
  if (ok) close(false);
  else uiModule.showToast(`Couldn't open ${m.item.title}`);
}

function handleKeydown(e) {
  // Esc deliberately NOT handled here — the ui.js arbiter owns it.
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
  else if (e.key === 'Home') { e.preventDefault(); setActive(0); }
  else if (e.key === 'End') { e.preventDefault(); setActive(_results.length - 1); }
  else if (e.key === 'Enter') { e.preventDefault(); activate(); }
  else if (e.key === 'Tab') {
    // JetBrains parity: Tab / Shift+Tab cycle the category tabs. Focus never
    // leaves the input, so this doubles as the focus trap.
    e.preventDefault();
    const dir = e.shiftKey ? -1 : 1;
    const next = (TABS.indexOf(_activeTab) + dir + TABS.length) % TABS.length;
    setTab(TABS[next]);
  }
}

function handleInput(e) {
  if (_debounce) clearTimeout(_debounce);
  const q = e.target.value.trim();
  _debounce = setTimeout(() => render(q), DEBOUNCE_MS);
}

export function init() {
  const input = el('cmd-palette-input');
  const overlay = el('cmd-palette-overlay');
  if (!input || !overlay) return;
  input.addEventListener('input', handleInput);
  input.addEventListener('keydown', handleKeydown);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
  el('cmd-palette-tabs').querySelectorAll('.cmd-palette-tab').forEach(btn => {
    btn.addEventListener('click', () => { setTab(btn.dataset.tab); input.focus(); });
  });
  // Highest-priority hook for the ui.js Escape arbiter (see _odyEscExpandGuard):
  // it must close the palette before its hovered-window/modal handling runs.
  window._odyCmdPalette = { isOpen, close };
}

const commandPaletteModule = { init, open, close, isOpen };
export default commandPaletteModule;

// ============================================
// Command registry — items for Search Everywhere
// ============================================
// Declarative registry of everything the command palette can navigate to or
// run. Pure data + lazy perform() closures: DOM is only touched inside
// perform() (invoked on selection) and inside the getItems() visibility
// filter, so the module imports cleanly under bare node for tests.
//
// Item schema: { id, title, keywords: [], section, perform() }
//  - perform() MUST throw (or return false) when its target is missing —
//    never silently no-op. The palette turns that into a visible toast.

import { _WINDOW_TRIGGERS } from './keyboard-shortcuts.js';

// Human title + search keywords per tool window (keyed by modal id, matching
// the sidebar labels in index.html).
const _WINDOW_META = {
  'settings-modal':        { title: 'Settings',      keywords: ['preferences', 'options', 'config'] },
  'theme-modal':           { title: 'Theme',         keywords: ['colors', 'appearance', 'editor'] },
  'tasks-modal':           { title: 'Tasks',         keywords: ['todo', 'assistant', 'schedule'] },
  'notes-panel':           { title: 'Notes',         keywords: ['scratchpad', 'memo'] },
  'memory-modal':          { title: 'Brain',         keywords: ['memory', 'knowledge'] },
  'doclib-modal':          { title: 'Library',       keywords: ['documents', 'docs', 'archive'] },
  'gallery-modal':         { title: 'Gallery',       keywords: ['images', 'pictures', 'photos'] },
  'research-overlay':      { title: 'Deep Research', keywords: ['search', 'web', 'investigate'] },
  'cookbook-modal':        { title: 'Cookbook',      keywords: ['recipes', 'models', 'serve'] },
  'compare-model-overlay': { title: 'Compare',       keywords: ['models', 'side by side', 'diff'] },
  'calendar-modal':        { title: 'Calendar',      keywords: ['events', 'schedule', 'dates'] },
  'email-lib-modal':       { title: 'Email',         keywords: ['inbox', 'mail', 'messages'] },
};

// Click the element with this id; throw when absent so failures surface
// as a palette toast instead of a silent no-op.
function _clickTrigger(triggerId) {
  const el = document.getElementById(triggerId);
  if (!el) throw new Error('missing trigger ' + triggerId);
  el.click();
  return true;
}

function _buildWindowItems() {
  const items = Object.keys(_WINDOW_TRIGGERS).map(modalId => {
    const meta = _WINDOW_META[modalId] || { title: modalId, keywords: [] };
    const triggerId = _WINDOW_TRIGGERS[modalId];
    return {
      id: 'window:' + modalId,
      title: meta.title,
      keywords: meta.keywords,
      section: 'Windows',
      triggerId, // used by the getItems() existence/visibility filter
      perform() { return _clickTrigger(triggerId); },
    };
  });
  // Presets lives in the composer overflow menu, not the sidebar tools list.
  items.push({
    id: 'window:presets',
    title: 'Presets',
    keywords: ['prompts', 'templates'],
    section: 'Windows',
    triggerId: 'overflow-preset-btn',
    perform() { return _clickTrigger('overflow-preset-btn'); },
  });
  return items;
}

function _buildActionItems() {
  return [
    {
      id: 'action:new-chat',
      title: 'New Chat',
      keywords: ['session', 'conversation', 'create'],
      section: 'Actions',
      perform() { return _clickTrigger('sidebar-new-chat-btn'); },
    },
    {
      id: 'action:toggle-sidebar',
      title: 'Toggle Sidebar',
      keywords: ['hide', 'show', 'collapse', 'panel'],
      section: 'Actions',
      // Mirrors the kb.toggle_sidebar branch in keyboard-shortcuts.js.
      perform() {
        const sb = document.getElementById('sidebar');
        if (!sb) throw new Error('missing trigger sidebar');
        const ir = document.getElementById('icon-rail');
        if (!sb.classList.contains('hidden')) {
          sb.classList.add('hidden');
        } else {
          if (ir) ir.classList.remove('rail-hidden');
          sb.classList.remove('hidden');
        }
        if (typeof window !== 'undefined' && typeof window.syncRailSide === 'function') window.syncRailSide();
        return true;
      },
    },
    {
      id: 'action:incognito',
      title: 'Toggle Incognito (Nobody mode)',
      keywords: ['private', 'no history', 'nobody'],
      section: 'Actions',
      perform() { return _clickTrigger('incognito-btn'); },
    },
    {
      id: 'action:focus-input',
      title: 'Focus Message Input',
      keywords: ['type', 'compose', 'chat box'],
      section: 'Actions',
      perform() {
        const inp = document.getElementById('message');
        if (!inp) throw new Error('missing trigger message');
        inp.focus();
        return true;
      },
    },
    {
      id: 'action:search-chats',
      title: 'Search Chats',
      keywords: ['find', 'conversations', 'history', 'ctrl+k'],
      section: 'Actions',
      perform() { return _clickTrigger('sidebar-search-btn'); },
    },
  ];
}

// Log loudly (don't throw) when an item is malformed, so a bad entry is
// caught in dev instead of failing silently at palette-open time.
function _validate(items) {
  for (const it of items) {
    if (!it || !it.id || !it.title || typeof it.perform !== 'function') {
      console.error('command-registry: invalid item', it);
    }
  }
  return items;
}

let _staticItems = null;
const _providers = [];

export function getStaticItems() {
  if (!_staticItems) _staticItems = _validate([..._buildWindowItems(), ..._buildActionItems()]);
  return _staticItems;
}

/** Register extra items (e.g. from tests or future modules). */
export function register(items) {
  getStaticItems().push(..._validate(items));
}

/** Register a provider: a fn returning an items array, called per getItems(). */
export function registerProvider(fn) {
  _providers.push(fn);
}

// A tool trigger is "available" unless it is missing or hidden for a reason
// OTHER than the collapsed sidebar/icon-rail: tools still open fine while the
// sidebar is closed (click handlers fire on display:none elements), and the
// palette is most useful exactly then. Catches el.hidden, .hidden classes and
// the inline style.display='none' used by the Customize-UI visibility prefs.
function _isTriggerAvailable(el) {
  if (!el) return false;
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    if (n.id === 'sidebar' || n.id === 'icon-rail') continue;
    if (n.hidden) return false;
    if (n.classList && n.classList.contains('hidden')) return false;
    if (n.style && n.style.display === 'none') return false;
  }
  return true;
}

/**
 * All currently-available items: static items (tool windows filtered by
 * trigger existence + visibility — hidden/unconfigured tools must not
 * appear) plus everything from registered providers.
 */
export function getItems() {
  const statics = getStaticItems().filter(it =>
    !it.triggerId || _isTriggerAvailable(document.getElementById(it.triggerId)));
  return [...statics, ..._providers.flatMap(fn => fn())];
}

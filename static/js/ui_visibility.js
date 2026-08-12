// static/js/ui_visibility.js
//
// Per-item visibility for the sidebar and collapsed icon rail. Drives the
// Settings → Appearance ("Customize UI") checkboxes, persisted in localStorage
// under `odysseus-ui-visibility` (loaded/saved by app.js).
//
// Each key maps to the CSS selector(s) it controls. Tool/section selectors pair
// the full-sidebar element with its #rail-* launcher so a tab hidden in the
// full view also hides when the sidebar is minimized to the icon rail (id
// mapping mirrors _railToolMap in app.js; #tool-library-btn ↔ #rail-archive).

// Selector map: UI customization key → CSS selector(s) for its target(s).
export const UI_VIS_MAP = {
  'sidebar-brand':       '.sidebar-brand-title',
  'sidebar-new-chat':    '#sidebar-new-chat-btn',
  'sidebar-search':      '#sidebar-search-btn',
  'sessions-section':   '#sessions-section',
  'email-section':       '#email-section, #rail-email',
  'tools-section':       '#tools-section',
  // Per-tool entries pair the sidebar button with its rail launcher.
  'tool-calendar':       '#tool-calendar-btn, #rail-calendar',
  'tool-compare':        '#tool-compare-btn, #rail-compare',
  'tool-cookbook':       '#tool-cookbook-btn, #rail-cookbook',
  'tool-research':       '#tool-research-btn, #rail-research',
  'tool-gallery':        '#tool-gallery-btn, #rail-gallery',
  'tool-library':        '#tool-library-btn, #rail-archive',
  'tool-memory':         '#tool-memory-btn, #rail-memory',
  'tool-notes':          '#tool-notes-btn, #rail-notes',
  'tool-tasks':          '#tool-tasks-btn, #rail-tasks',
  'tool-theme':          '#tool-theme-btn, #rail-theme',
  'user-bar':            '#user-bar-profile',
  'sidebar-settings-btn':'#user-bar-settings',
  'chat-meta':           '.chat-meta-overlay',
  'welcome-text':        '.welcome-name, .welcome-sub, #welcome-tip',
  'incognito-btn':       '.incognito-btn',
  'web-toggle-btn':      '#web-toggle-btn',
  'doc-toggle-btn':      '#overflow-doc-btn',
  'rag-toggle-btn':      '#overflow-rag-btn',
  'bash-toggle-btn':     '#bash-toggle-btn',
  'overflow-plus-btn':   '.overflow-wrapper',
  'mode-toggle':         '.mode-toggle',
  'preset-mini-btn':     '#overflow-preset-btn',
  'attach-btn':          '#overflow-attach-btn',
  'research-btn':        '#overflow-research-btn',
  'rail-new-chat':       '#rail-new-session',
};

// Keys hidden by default on first run (no localStorage yet).
export const UI_VIS_DEFAULT_OFF = new Set(['rag-toggle-btn', 'text-emojis', 'chat-fullwidth']);

/**
 * Resolve every UI_VIS_MAP selector to visible (true) or hidden (false) for the
 * given saved state. Pure: no DOM, no localStorage — app.js applies the result.
 *
 * A key is visible when its stored value is not `false`, defaulting to on
 * unless it is in UI_VIS_DEFAULT_OFF. Per-tool entries also require the Tools
 * section to be on: hiding Tools hides every tool, mirroring the full sidebar
 * where the #tools-section container already hides them (the rail has no
 * container, so this rule keeps it in sync).
 *
 * @param {Record<string, boolean>} state
 * @returns {Record<string, boolean>} selector → visible
 */
export const resolveVisibility = (state = {}) => {
  const toolsOn = state['tools-section'] !== false;
  const out = {};
  for (const [key, selector] of Object.entries(UI_VIS_MAP)) {
    let visible = key in state ? state[key] !== false : !UI_VIS_DEFAULT_OFF.has(key);
    if (!toolsOn && key.startsWith('tool-')) visible = false;
    out[selector] = visible;
  }
  return out;
};
/**
 * Email keyboard shortcut defaults — shared by settings panel and emailLibrary.
 */

import { resolveKeybind } from './keybindUtils.js';

export const EMAIL_SHORTCUT_DEFAULTS = {
  email_next: 'j',
  email_prev: 'k',
  email_archive: 'e',
  email_reply: 'r',
  email_reply_all: 'a',
  email_forward: 'f',
  email_star: 's',
  email_toggle_read: 'shift+u',
  email_view_source: 'u',
  email_focus_search: '/',
  email_shortcuts_help: 'shift+/',
  email_command_palette: 'ctrl+k',
  email_nav_prev: 'arrowleft',
  email_nav_next: 'arrowright',
  email_delete: 'delete',
  email_select_all: 'ctrl+a',
  email_close: 'escape',
};

/** Labels for Settings → Shortcuts and the in-email help overlay. */
export const EMAIL_SHORTCUT_LABELS = {
  email_next: 'Next message',
  email_prev: 'Previous message',
  email_archive: 'Archive',
  email_reply: 'Reply',
  email_reply_all: 'Reply all',
  email_forward: 'Forward',
  email_star: 'Toggle star',
  email_toggle_read: 'Mark unread / read',
  email_view_source: 'View source',
  email_focus_search: 'Focus search',
  email_shortcuts_help: 'Show keyboard shortcuts',
  email_command_palette: 'Command palette',
  email_nav_prev: 'Previous (arrow)',
  email_nav_next: 'Next (arrow)',
  email_delete: 'Delete message',
  email_select_all: 'Select all in reader',
  email_close: 'Close email / exit selection',
};

/** Actions shown in the in-email shortcuts overlay (subset, user-facing order). */
export const EMAIL_SHORTCUT_OVERLAY_KEYS = [
  'email_next', 'email_prev', 'email_archive', 'email_reply', 'email_reply_all',
  'email_forward', 'email_star', 'email_toggle_read', 'email_view_source', 'email_focus_search',
  'email_command_palette', 'email_close',
];

const _MAIL_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>';

export const EMAIL_SHORTCUT_ICONS = Object.fromEntries(
  Object.keys(EMAIL_SHORTCUT_DEFAULTS).map((k) => [k, _MAIL_ICON]),
);

export function emailKeybind(action) {
  return resolveKeybind(action, EMAIL_SHORTCUT_DEFAULTS);
}

/** Display combo as <kbd>…</kbd> chips (Settings panel style). */
export function formatEmailKeyCaps(combo) {
  if (!combo) return '—';
  return combo.split('+').map((p) => {
    let label;
    if (p === 'ctrl') label = 'Ctrl';
    else if (p === 'alt') label = 'Alt';
    else if (p === 'shift') label = 'Shift';
    else if (p === 'meta') label = 'Cmd';
    else if (p === 'escape') label = 'Esc';
    else if (p === ',') label = ',';
    else if (p === '/') label = '/';
    else if (p === 'space') label = 'Space';
    else if (p === 'arrowleft') label = '←';
    else if (p === 'arrowright') label = '→';
    else if (p === 'arrowup') label = '↑';
    else if (p === 'arrowdown') label = '↓';
    else if (p === 'delete') label = 'Del';
    else label = p.charAt(0).toUpperCase() + p.slice(1);
    return `<kbd>${label}</kbd>`;
  }).join('');
}

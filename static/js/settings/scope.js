// Scope presentation for Settings cards.
//
// Each registry section may declare a scope (see SETTINGS_SECTION_SCOPES in
// registry.js). This module labels those cards so people can tell whether a
// change affects only them or everyone, and it locks server-wide cards for
// users who cannot change them, instead of letting them edit a form whose
// save the server will refuse.
//
// It is presentation only: the server authorizes every write.

import { SETTINGS_PANELS } from './registry.js';
import { findSettingsSectionElement } from './navigation.js';

const BADGE_CLASS = 'settings-scope-badge';
const NOTE_CLASS = 'settings-scope-note';
const LOCKED_CLASS = 'settings-scope-locked';
const INERT_MARK = 'data-settings-scope-inert';

/**
 * Decide how a section is presented. Returns null when no label applies
 * (unknown access, or a single-user install without accounts).
 */
export function describeSectionScope(scope, access) {
  if (!scope || !access || access.authEnabled === false) return null;
  if (scope === 'model-default') {
    return access.canManage
      ? {
        badge: 'Server default',
        title: 'Your choice becomes the server default. Users inherit it when "Share defaults with users" is on and they have not picked their own.',
        locked: false,
      }
      : {
        badge: 'Just you',
        title: 'Saved to your account only. Other users are not affected.',
        locked: false,
      };
  }
  if (scope === 'server') {
    return access.canManage
      ? {
        badge: 'All users',
        title: 'Applies to everyone on this server.',
        locked: false,
      }
      : {
        badge: 'Set by admin',
        title: 'Applies to everyone on this server. Only an administrator can change it.',
        locked: true,
        note: 'Only an administrator can change this. It applies to everyone on this server.',
      };
  }
  return null;
}

const CONTROL_SELECTOR = 'input, select, textarea, button, label.admin-switch, [contenteditable="true"]';

function headingOf(card) {
  return card.querySelector('h2');
}

function markInert(node) {
  if (node.hasAttribute('inert')) return;
  node.setAttribute('inert', '');
  node.setAttribute(INERT_MARK, '');
}

function removeScopePresentation(card) {
  card.querySelectorAll(`.${BADGE_CLASS}, .${NOTE_CLASS}`).forEach(node => node.remove());
  card.querySelectorAll(`[${INERT_MARK}]`).forEach(node => {
    node.removeAttribute('inert');
    node.removeAttribute(INERT_MARK);
  });
  card.classList.remove(LOCKED_CLASS);
  card.removeAttribute('data-settings-scope');
}

function applyToCard(card, scope, presentation) {
  removeScopePresentation(card);
  if (!presentation) return;

  card.setAttribute('data-settings-scope', scope);
  const heading = headingOf(card);
  const badge = document.createElement('span');
  badge.className = BADGE_CLASS;
  badge.dataset.scope = presentation.locked ? 'locked' : scope;
  badge.textContent = presentation.badge;
  badge.title = presentation.title;
  if (heading) {
    // Headings that end in a switch use a flex spacer; keep the badge next to
    // the title rather than after the switch.
    const spacer = Array.from(heading.children)
      .find(child => /flex:\s*1\b/.test(child.getAttribute('style') || ''));
    heading.insertBefore(badge, spacer || null);
  } else {
    card.prepend(badge);
  }

  if (!presentation.locked) return;

  card.classList.add(LOCKED_CLASS);
  const note = document.createElement('div');
  note.className = NOTE_CLASS;
  note.setAttribute('role', 'note');
  note.textContent = presentation.note;
  if (heading) heading.after(note);
  else card.prepend(note);

  // Keep the heading, note and description readable; make every control
  // unreachable so nobody starts an edit the server would reject.
  Array.from(card.children).forEach(child => {
    if (child === note) return;
    if (heading && (child === heading || child.contains(heading))) {
      child.querySelectorAll(CONTROL_SELECTOR).forEach(control => {
        if (!control.closest(`.${BADGE_CLASS}`)) markInert(control);
      });
      return;
    }
    if (child.classList.contains('admin-toggle-sub')) return;
    markInert(child);
  });
}

export function applySettingsSectionScopes(modalEl, access) {
  if (!modalEl) return;
  for (const panel of SETTINGS_PANELS) {
    for (const section of panel.sections) {
      if (!section.scope) continue;
      const card = findSettingsSectionElement(modalEl, panel.id, section.id);
      if (!card) continue;
      applyToCard(card, section.scope, describeSectionScope(section.scope, access));
    }
  }
}

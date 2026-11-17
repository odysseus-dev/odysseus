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

function markControlInert(node) {
  if (node.hasAttribute('inert')) return;
  node.setAttribute('inert', '');
  node.setAttribute(INERT_MARK, '');
  if ('disabled' in node) {
    node.disabled = true;
    node.setAttribute('aria-disabled', 'true');
  }
}

function removeScopePresentation(card) {
  if (card._scopeObserver) {
    card._scopeObserver.disconnect();
    delete card._scopeObserver;
  }
  card.querySelectorAll(`.${BADGE_CLASS}, .${NOTE_CLASS}`).forEach(node => node.remove());
  card.querySelectorAll(`[${INERT_MARK}]`).forEach(node => {
    node.removeAttribute('inert');
    node.removeAttribute(INERT_MARK);
    if ('disabled' in node) {
      node.disabled = false;
      node.removeAttribute('aria-disabled');
    }
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

  // Keep the heading, note, labels and descriptions readable; make every control
  // unreachable so nobody starts an edit the server would reject.
  const lockControls = root => {
    if (!root || root.nodeType !== 1) return;
    if (root.matches && root.matches(CONTROL_SELECTOR)) {
      if (!root.closest(`.${BADGE_CLASS}`)) markControlInert(root);
    }
    if (root.querySelectorAll) {
      root.querySelectorAll(CONTROL_SELECTOR).forEach(control => {
        if (!control.closest(`.${BADGE_CLASS}`)) markControlInert(control);
      });
    }
  };

  Array.from(card.children).forEach(child => {
    if (child === note) return;
    if (child.classList?.contains('admin-toggle-sub')) return;
    lockControls(child);
  });

  if (typeof MutationObserver !== 'undefined') {
    const observer = new MutationObserver(mutations => {
      for (const m of mutations) {
        for (const added of m.addedNodes) {
          if (added.nodeType !== 1) continue;
          if (added.classList?.contains(NOTE_CLASS) || added.classList?.contains(BADGE_CLASS)) continue;
          lockControls(added);
        }
      }
    });
    observer.observe(card, { childList: true, subtree: true });
    card._scopeObserver = observer;
  }
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

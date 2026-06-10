/**
 * Shared keybind resolution — explicit '' in saved settings disables a bind.
 */

import { IS_MAC, isAltGrEvent } from './platform.js';
import { readEmailListPickerDebug } from './emailListPickerKeys.js';

export const KEYBINDS_STORAGE_KEY = 'odysseus-user-keybinds';
export const KEY_DEBUG_FLAG = 'odysseus-debug-keys';

export function readStoredKeybinds() {
  try {
    const raw = localStorage.getItem(KEYBINDS_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

export function writeStoredKeybinds(keybinds) {
  try {
    localStorage.setItem(KEYBINDS_STORAGE_KEY, JSON.stringify(keybinds));
  } catch { /* private mode / quota */ }
}

/** Resolved bind: explicit `''` disables; missing key uses default. */
export function resolveKeybind(action, defaults = {}) {
  const stored = readStoredKeybinds();
  if (stored && Object.prototype.hasOwnProperty.call(stored, action)) {
    return stored[action];
  }
  const kb = window._odysseusKeybinds;
  if (kb && Object.prototype.hasOwnProperty.call(kb, action)) return kb[action];
  return defaults[action] ?? '';
}

export function keybindEnabled(action, defaults = {}) {
  return !!resolveKeybind(action, defaults);
}

export function matchesCombo(e, combo, isMac = IS_MAC) {
  if (!combo) return false;
  if (isAltGrEvent(e, isMac)) return false;
  const parts = combo.split('+');
  const needCtrl = parts.includes('ctrl');
  const needAlt = parts.includes('alt');
  const needShift = parts.includes('shift');
  const key = parts.filter((p) => p !== 'ctrl' && p !== 'alt' && p !== 'shift')[0] || '';
  if (needCtrl !== (e.ctrlKey || e.metaKey)) return false;
  if (needAlt !== e.altKey) return false;
  if (needShift !== e.shiftKey) return false;
  return e.key.toLowerCase() === key;
}

/** Whether Esc may dismiss this modal per Settings → Shortcuts. */
export function escMayDismissModal(e, modalId, emailDefaults = {}) {
  if (e.key !== 'Escape') return false;
  const id = String(modalId || '');
  const isEmail = id === 'email-lib-modal' || id.startsWith('email-reader-');
  if (isEmail) {
    if (!keybindEnabled('email_close', emailDefaults)) return false;
    return matchesCombo(e, resolveKeybind('email_close', emailDefaults));
  }
  if (!keybindEnabled('cancel')) return false;
  return matchesCombo(e, resolveKeybind('cancel'));
}

/** Log which handlers see each key — enable with localStorage odysseus-debug-keys=1 */
export function installKeyDebugProbe(emailDefaults = {}) {
  if (localStorage.getItem(KEY_DEBUG_FLAG) !== '1') return;
  if (window._odysseusKeyDebugInstalled) return;
  window._odysseusKeyDebugInstalled = true;

  const interesting = new Set(['Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter']);
  const phaseName = (p) => (p === 1 ? 'capture' : p === 2 ? 'target' : 'bubble');

  document.addEventListener('keydown', (e) => {
    if (!interesting.has(e.key)) return;
    const t = e.target;
    const target = `${t?.tagName || '?'}${t?.id ? `#${t.id}` : ''}`;
    const stored = readStoredKeybinds();
    const emailModal = document.getElementById('email-lib-modal');
    const emailOpen = emailModal && !emailModal.classList.contains('hidden');
    console.log(
      `[key-debug ${phaseName(e.eventPhase)}] ${e.key} → ${target}`,
      {
        cancel: resolveKeybind('cancel'),
        email_close: resolveKeybind('email_close', emailDefaults),
        escMayCloseEmail: escMayDismissModal(e, 'email-lib-modal', emailDefaults),
        escMayCloseDoclib: escMayDismissModal(e, 'doclib-modal'),
        emailModalOpen: emailOpen,
        picker: readEmailListPickerDebug(),
        overlays: {
          cmd: !!document.getElementById('email-cmd-palette'),
          move: !!document.getElementById('email-move-picker'),
          shortcuts: !!document.getElementById('email-shortcuts-overlay'),
          rebind: !!document.querySelector('.shortcut-key.listening'),
        },
        stored_cancel: stored?.cancel,
        stored_email_close: stored?.email_close,
        defaultPrevented: e.defaultPrevented,
      },
    );
  }, true);

  document.addEventListener('keydown', (e) => {
    if (!interesting.has(e.key)) return;
    console.log(`[key-debug bubble] ${e.key} defaultPrevented=${e.defaultPrevented}`);
  }, false);

  console.info('[key-debug] Keyboard probe active. Disable: localStorage.removeItem("odysseus-debug-keys")');
}

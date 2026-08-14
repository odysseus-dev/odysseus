// Settings modal lifecycle primitives.
//
// Panel-specific behavior belongs elsewhere. This module owns only the window
// shell: dragging/docking reset, close semantics, visibility animation, and the
// delegated link that leaves Settings for the Persona editor.

import { makeWindowDraggable } from '../windowDrag.js';
import { clearDockSide } from '../modalSnap.js';

const _dragBound = new WeakSet();
const _closeBound = new WeakSet();
let _promptLinkBound = false;

export function bindSettingsDrag(modalEl) {
  if (!modalEl || _dragBound.has(modalEl)) return;

  const header = modalEl.querySelector('.modal-header');
  const content = modalEl.querySelector('.settings-modal-content');
  if (!header || !content) return;

  _dragBound.add(modalEl);
  makeWindowDraggable(modalEl, {
    content,
    header,
    skipSelector: 'button, input, select, .theme-opacity-wrap',
    enableDock: true,
  });
}

export function resetSettingsWindowPlacement(modalEl) {
  const content = modalEl?.querySelector('.settings-modal-content');
  if (!content) return;

  const hadLeft = modalEl.classList.contains('modal-left-docked');
  const hadRight = modalEl.classList.contains('modal-right-docked');
  modalEl.classList.remove('modal-left-docked', 'modal-right-docked');
  if (hadLeft) clearDockSide('left', modalEl);
  if (hadRight) clearDockSide('right', modalEl);

  if (content._leftDockNavObs) {
    try { content._leftDockNavObs.navObs && content._leftDockNavObs.navObs.disconnect(); } catch (_) {}
    try { window.removeEventListener('resize', content._leftDockNavObs.reanchor); } catch (_) {}
    delete content._leftDockNavObs;
  }

  delete content._preDockSnapshot;
  delete content._dockSide;
  delete content._dockSuspended;
  delete content.dataset._tilePreSnap;
  delete content.dataset._tileZone;

  [
    'position', 'left', 'top', 'right', 'bottom', 'margin', 'transform',
    'width', 'height', 'max-width', 'max-height', 'border-radius', 'transition',
  ].forEach(property => content.style.removeProperty(property));
}

export function bindOpenPromptModalLink({ getModal, closeSettings } = {}) {
  if (_promptLinkBound) return;
  _promptLinkBound = true;

  document.addEventListener('click', async event => {
    const link = event.target?.closest?.('[data-open-prompt-modal]');
    if (!link) return;
    event.preventDefault();

    const settingsModal = typeof getModal === 'function' ? getModal() : null;
    if (
      settingsModal
      && !settingsModal.classList.contains('hidden')
      && typeof closeSettings === 'function'
    ) {
      closeSettings();
    }

    try {
      const module = await import('../presets.js');
      const openPrompt = module.openCustomPresetModal
        || (module.default && module.default.openCustomPresetModal);
      if (typeof openPrompt === 'function') openPrompt();
    } catch (_) {
      const modal = document.getElementById('custom-preset-modal');
      if (modal) modal.classList.remove('hidden');
    }

    const personaTab = document.querySelector(
      '#custom-preset-modal .preset-tab[data-chartab="character"]'
    );
    if (personaTab) personaTab.click();
  });
}

export function bindSettingsClose(modalEl, options = {}) {
  if (!modalEl || _closeBound.has(modalEl)) return;
  _closeBound.add(modalEl);

  const closeSettings = options.closeSettings;
  const isTouchInsideModal = options.isTouchInsideModal;

  const closeButton = modalEl.querySelector('.close-btn');
  closeButton?.addEventListener('click', () => {
    if (typeof closeSettings === 'function') closeSettings();
  });

  modalEl.addEventListener('mousedown', event => {
    if (typeof isTouchInsideModal === 'function' && isTouchInsideModal()) return;
    if (event.target === modalEl && typeof closeSettings === 'function') {
      closeSettings();
    }
  });

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || modalEl.classList.contains('hidden')) return;

    // Esc should dismiss transient popovers before the Settings window.
    const popoverOpen = modalEl.querySelector(
      '#adm-epLocalMoreMenu, #adm-epApiMoreMenu, #adm-provider-menu, #search-provider-menu, [data-popover-open="1"]'
    );
    if (
      popoverOpen
      && popoverOpen.style.display !== 'none'
      && !popoverOpen.classList.contains('hidden')
    ) {
      return;
    }

    // Integration/account editors are nested flows. Close the editor first so
    // an accidental Esc does not discard the entire Settings context.
    const innerForm = modalEl.querySelector('#unified-intg-form, #set-email-accounts-form');
    if (
      innerForm
      && innerForm.style.display !== 'none'
      && innerForm.children.length > 0
    ) {
      event.preventDefault();
      event.stopPropagation();
      innerForm.style.display = 'none';
      innerForm.innerHTML = '';
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    if (typeof closeSettings === 'function') closeSettings();
  });
}

export function showSettingsModal(modalEl) {
  if (!modalEl) return;
  if (modalEl.classList.contains('hidden')) {
    resetSettingsWindowPlacement(modalEl);
  }
  modalEl.classList.remove('hidden');
}

export function hideSettingsModal(modalEl) {
  if (!modalEl) return;

  const content = modalEl.querySelector('.modal-content, .settings-modal-content');
  if (content && !content.classList.contains('modal-closing')) {
    content.classList.add('modal-closing');
    content.addEventListener('animationend', () => {
      modalEl.classList.add('hidden');
      content.classList.remove('modal-closing');
    }, { once: true });
    setTimeout(() => {
      if (!modalEl.classList.contains('hidden')) {
        modalEl.classList.add('hidden');
        content.classList.remove('modal-closing');
      }
    }, 250);
    return;
  }

  modalEl.classList.add('hidden');
}

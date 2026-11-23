// Appearance (interface visibility) and privacy toggles. Extracted from
// settings.js; the modal element and the manage-settings predicate are passed
// in instead of being read from coordinator state.

import uiModule from '../ui.js?v=20260916largetoolscroll1';

/* ═══════════════════════════════════════════
   APPEARANCE TAB
   ═══════════════════════════════════════════ */
export function initAppearance(modalEl, { canManage }) {
  syncAppearanceCheckboxes(modalEl);
  syncPrivacyCheckboxes(modalEl);

  modalEl.querySelectorAll('[data-ui-key]').forEach(function(chk) {
    chk.addEventListener('change', async function() {
      var key = chk.dataset.uiKey;

      if (window.UI_VIS_ADMIN_ONLY && window.UI_VIS_ADMIN_ONLY.has(key) && !chk.checked && !canManage()) {
        chk.checked = true;
        if (uiModule && uiModule.showToast) {
          uiModule.showToast('Only admins can hide Settings.');
        }
        return;
      }

      // Hiding the Settings cog removes the only visible way to re-open this
      // panel. Warn the user and remind them about the `/settings` slash
      // command so they don't lock themselves out.
      if (key === 'sidebar-settings-btn' && !chk.checked) {
        var ok = true;
        try {
          ok = await (uiModule && uiModule.styledConfirm
            ? uiModule.styledConfirm(
                'Hide the Settings cog?\n\nYou can re-open this panel any time by typing /settings in the chat input.',
                { confirmText: 'Hide', cancelText: 'Cancel' }
              )
            : Promise.resolve(window.confirm('Hide the Settings cog?\n\nYou can re-open this panel any time by typing /settings in the chat input.')));
        } catch (_) { ok = false; }
        if (!ok) {
          chk.checked = true;
          return;
        }
        if (uiModule && uiModule.showToast) {
          uiModule.showToast('Settings cog hidden — type /settings to bring it back.', 5000);
        }
      }

      var s = window.loadUIVis();
      s[key] = chk.checked;
      window.saveUIVis(s);
      window.applyUIVis(s);
    });
  });

  modalEl.querySelectorAll('[data-privacy-key]').forEach(function(chk) {
    chk.addEventListener('change', function() {
      if (chk.dataset.privacyKey !== 'sensitive-blur') return;
      localStorage.setItem('odysseus-sensitive-blur', chk.checked ? 'on' : 'off');
      window.dispatchEvent(new CustomEvent('odysseus-sensitive-blur-change', {
        detail: { enabled: chk.checked }
      }));
    });
  });

  // Per-section reset buttons (arrow-circle-back icon in each card's h2).
  // Removes only the keys belonging to this section from the persisted
  // visibility map so other sections keep their user settings.
  modalEl.querySelectorAll('[data-vis-reset]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var card = btn.closest('.admin-card');
      if (!card) return;
      var keys = Array.from(card.querySelectorAll('[data-ui-key]'))
        .map(function(c) { return c.dataset.uiKey; })
        .filter(Boolean);
      if (!keys.length) return;
      var s = window.loadUIVis ? window.loadUIVis() : {};
      keys.forEach(function(k) { delete s[k]; });
      if (window.saveUIVis) window.saveUIVis(s);
      syncAppearanceCheckboxes(modalEl);
      syncPrivacyCheckboxes(modalEl);
      if (window.applyUIVis) window.applyUIVis(s);
    });
  });
}

export function syncAppearanceCheckboxes(modalEl) {
  var s = window.loadUIVis ? window.loadUIVis() : {};
  var defaultOff = window.UI_VIS_DEFAULT_OFF || new Set();
  modalEl.querySelectorAll('[data-ui-key]').forEach(function(chk) {
    var key = chk.dataset.uiKey;
    chk.checked = key in s ? s[key] !== false : !defaultOff.has(key);
  });
}

function syncPrivacyCheckboxes(modalEl) {
  modalEl.querySelectorAll('[data-privacy-key="sensitive-blur"]').forEach(function(chk) {
    chk.checked = localStorage.getItem('odysseus-sensitive-blur') === 'on';
  });
}

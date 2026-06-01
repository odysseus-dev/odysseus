// langPicker.js — wires the Settings → Appearance language <select> to the
// i18n runtime. Populates from the locale registry, reflects the active
// locale, and switches on change. Kept separate from i18n.js so the runtime
// stays UI-agnostic.
import i18n from './i18n.js';

const SELECT_ID = 'settings-language-select';

async function populate() {
  const sel = document.getElementById(SELECT_ID);
  if (!sel) return;
  await i18n.init();                 // idempotent — ensures registry + active locale resolved
  const reg = await i18n.getRegistry();
  const active = i18n.getLocale();

  sel.innerHTML = '';
  (reg.locales || []).forEach(loc => {
    const opt = document.createElement('option');
    opt.value = loc.code;
    // Show the native name, with the English name in parens when they differ.
    opt.textContent = loc.nativeName && loc.nativeName !== loc.name
      ? `${loc.nativeName} (${loc.name})`
      : (loc.nativeName || loc.name || loc.code);
    if (loc.code === active) opt.selected = true;
    sel.appendChild(opt);
  });

  if (!sel.dataset.bound) {
    sel.addEventListener('change', async () => {
      await i18n.setLocale(sel.value);  // persists (localStorage + server) and applies live
      // Reload so JS-rendered strings (built via i18n.t at render time) and any
      // server-negotiated text re-render in the chosen locale, not just the
      // static [data-i18n] nodes applyTranslations already swapped.
      location.reload();
    });
    sel.dataset.bound = '1';
  }
}

// Keep the select in sync if the locale is changed elsewhere.
document.addEventListener('i18n:change', (e) => {
  const sel = document.getElementById(SELECT_ID);
  if (sel && e.detail && e.detail.locale) sel.value = e.detail.locale;
});

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', populate, { once: true });
} else {
  populate();
}

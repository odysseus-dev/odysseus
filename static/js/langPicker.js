// langPicker.js — populate the language <select> from the locale registry and
// wire it to the i18n runtime. Self-contained: drop a
// `<select id="lang-picker" data-i18n-skip></select>` anywhere in the markup and
// this fills it with the available languages (native names) and persists the
// choice. The option labels are native names (English, 日本語, …) so they are
// marked data-i18n-skip implicitly by living in a skipped <select>.
import i18n from "./i18n.js";

const SELECT_ID = "lang-picker";

async function populate() {
  const sel = document.getElementById(SELECT_ID);
  if (!sel) return;
  await i18n.ready;
  const locales = i18n.locales();
  if (!locales.length) return;
  sel.innerHTML = "";
  for (const loc of locales) {
    const opt = document.createElement("option");
    opt.value = loc.code;
    opt.textContent = loc.nativeName || loc.name || loc.code;
    sel.appendChild(opt);
  }
  sel.value = i18n.getLocale();
  sel.addEventListener("change", () => i18n.setLocale(sel.value));
}

// Keep the control in sync if the locale changes elsewhere.
window.addEventListener("i18n:changed", (e) => {
  const sel = document.getElementById(SELECT_ID);
  if (sel && sel.value !== e.detail.locale) sel.value = e.detail.locale;
});

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", populate, { once: true });
} else {
  populate();
}

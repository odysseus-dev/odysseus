// i18n.js — runtime localization for the Odysseus UI.
//
// Translates the rendered DOM by matching English UI text against source-keyed
// catalogs (static/locales/<code>.json). No per-element annotation is required:
// existing markup and JS-rendered UI are translated as-is. The few genuine
// context collisions opt in with `data-i18n="<dotted.key>"`. Model/user output
// (chat messages, editors, code) is never touched.
//
// Public API (also on window.i18n):
//   i18n.ready            -> Promise resolved once the initial locale is applied
//   i18n.setLocale(code)  -> switch language (persists, updates <html lang/dir>)
//   i18n.getLocale()      -> active code
//   i18n.locales()        -> [{code,name,nativeName,dir}, ...] from the registry
//   i18n.t(src, params)   -> translate a string programmatically
//   i18n.report()         -> in dev mode, the untranslated strings seen so far
//
// Adding a language = drop static/locales/<code>.json + add it to index.json.
// Adding a string   = it shows English automatically; translate it by adding
//                     "<English>": "<translation>" to each locale file.
import { buildMaps, translate, lookupKey, interpolate, normalize } from "./i18n-core.js";

const LS_KEY = "odysseus-locale";
const DEV_KEY = "odysseus-i18n-debug";
const BASE = "en";
const LOCALES_BASE = "/static/locales";

// Subtrees we never auto-translate (and never descend into): model/user content,
// editors, code. `data-i18n` still works inside them if explicitly opted in via
// translateKey(), but the source-string walker stops here.
const SKIP_SELECTOR =
  ".msg, .thinking-content, pre, code, kbd, samp, svg," +
  " [contenteditable], [contenteditable=true], [data-i18n-skip], .cm-editor, .monaco-editor";
// TEXTAREA is intentionally NOT skipped: we translate its placeholder/title/etc.
// but never descend into its text (the typed value) — see walk().
const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT"]);
const ATTRS = ["placeholder", "title", "aria-label", "alt"];
const VALUE_INPUT_TYPES = new Set(["button", "submit", "reset"]);

const state = {
  active: BASE,
  registry: null,
  maps: Object.create(null),     // code -> built maps
  raw: Object.create(null),      // code -> raw catalog json
  dev: false,
  missing: new Set(),
};

// Remember each node's ORIGINAL English so switching locales (incl. back to en)
// always translates from the source, never from an already-translated string.
const origText = new WeakMap();   // Text node   -> original string
const origAttr = new WeakMap();   // Element     -> { attr: original string }

let observer = null;

// ---- catalog loading ------------------------------------------------------

async function fetchJSON(url) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadRegistry() {
  if (state.registry) return state.registry;
  try {
    state.registry = await fetchJSON(`${LOCALES_BASE}/index.json`);
  } catch {
    state.registry = {
      default: BASE, fallback: BASE,
      locales: [{ code: "en", name: "English", nativeName: "English", dir: "ltr" }],
    };
  }
  return state.registry;
}

async function loadCatalog(code) {
  if (state.maps[code]) return state.maps[code];
  let raw = {};
  try {
    raw = await fetchJSON(`${LOCALES_BASE}/${code}.json`);
  } catch {
    raw = {}; // missing/broken catalog must never break the page
  }
  state.raw[code] = raw;
  state.maps[code] = buildMaps(raw);
  return state.maps[code];
}

function dirFor(code) {
  const loc = (state.registry?.locales || []).find((l) => l.code === code);
  return loc?.dir || "ltr";
}

// ---- DOM application ------------------------------------------------------

function shouldSkip(el) {
  return (
    SKIP_TAGS.has(el.tagName) ||
    (el.matches && el.matches(SKIP_SELECTOR))
  );
}

function applyTextNode(tn, maps) {
  if (!origText.has(tn)) {
    const v = tn.nodeValue;
    if (!v || !v.trim()) return;        // pure whitespace — ignore, don't track
    origText.set(tn, v);
  }
  const base = origText.get(tn);
  if (state.active === BASE) {
    if (tn.nodeValue !== base) tn.nodeValue = base;   // restore English
    return;
  }
  const tr = translate(base, maps);
  if (tr == null) {
    if (state.dev) recordMissing(base);
    return;                              // unknown string — leave English
  }
  if (tn.nodeValue !== tr) tn.nodeValue = tr;
}

function applyAttrs(el, maps) {
  let stash = origAttr.get(el);
  for (const attr of ATTRS) {
    if (!el.hasAttribute(attr)) continue;
    if (!stash) { stash = {}; origAttr.set(el, stash); }
    if (!(attr in stash)) stash[attr] = el.getAttribute(attr);
    const base = stash[attr];
    if (!base || !base.trim()) continue;
    const out = state.active === BASE ? base : (translate(base, maps) ?? base);
    if (el.getAttribute(attr) !== out) el.setAttribute(attr, out);
  }
  // value="" only for push-button inputs (never a typed field)
  if (el.tagName === "INPUT" && VALUE_INPUT_TYPES.has(el.type) && el.value) {
    if (!stash) { stash = {}; origAttr.set(el, stash); }
    if (!("value" in stash)) stash.value = el.value;
    const base = stash.value;
    const out = state.active === BASE ? base : (translate(base, maps) ?? base);
    if (el.value !== out) el.value = out;
  }
}

// An attribute changed externally (e.g. app.js re-setting the composer
// placeholder on resize). The observer is suspended during our own writes, so a
// seen change is always external: adopt the new English as the source of truth,
// then translate it. Keeps dynamically-set placeholders/titles/labels localized.
function reapplyAttr(el, attr, maps) {
  if (el.nodeType !== 1 || !ATTRS.includes(attr)) return;
  if (shouldSkip(el) || (el.closest && el.closest(SKIP_SELECTOR))) return;
  let stash = origAttr.get(el);
  if (!stash) { stash = {}; origAttr.set(el, stash); }
  stash[attr] = el.getAttribute(attr);
  const base = stash[attr];
  if (base == null || !base.trim()) return;
  const out = state.active === BASE ? base : (translate(base, maps) ?? base);
  if (el.getAttribute(attr) !== out) el.setAttribute(attr, out);
}

function translateKey(el, maps) {
  const key = el.getAttribute("data-i18n");
  if (!origText.has(el)) origText.set(el, el.textContent);
  const base = origText.get(el);
  const out =
    state.active === BASE ? base : (lookupKey(key, base, maps) ?? base);
  if (el.textContent !== out) el.textContent = out;
}

// Walk an element subtree, applying translations. Skips excluded subtrees.
function walk(el, maps) {
  if (el.nodeType !== 1 || shouldSkip(el)) return;
  if (el.hasAttribute("data-i18n")) {
    translateKey(el, maps);
    applyAttrs(el, maps);
    return;                              // its text is owned by the key
  }
  applyAttrs(el, maps);
  if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") return; // attrs only — never the typed value
  for (const node of el.childNodes) {
    if (node.nodeType === 3) applyTextNode(node, maps);
    else if (node.nodeType === 1) walk(node, maps);
  }
}

function recordMissing(s) {
  const t = normalize(s);
  if (t.length >= 2 && /[A-Za-z]{2,}/.test(t)) state.missing.add(t);
}

// ---- observer (catch JS-rendered UI) -------------------------------------

function startObserver() {
  if (observer) return;
  observer = new MutationObserver((mutations) => {
    if (state.active === BASE && !state.dev) return;
    const maps = state.maps[state.active];
    if (!maps) return;
    suspend();
    for (const mut of mutations) {
      if (mut.type === "attributes") {
        reapplyAttr(mut.target, mut.attributeName, maps);
      } else {
        for (const node of mut.addedNodes) {
          if (node.nodeType === 1) walk(node, maps);
          else if (node.nodeType === 3) applyTextNode(node, maps);
        }
      }
    }
    resume();
  });
  resume();
}

function suspend() { if (observer) observer.disconnect(); }
function resume() {
  if (observer)
    observer.observe(document.body, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ATTRS,
    });
}

// ---- locale switching -----------------------------------------------------

async function setLocale(code) {
  await loadRegistry();
  const known = (state.registry.locales || []).some((l) => l.code === code);
  if (!known) code = BASE;
  const maps = await loadCatalog(code);
  state.active = code;
  document.documentElement.setAttribute("lang", code);
  document.documentElement.setAttribute("dir", dirFor(code));
  try { localStorage.setItem(LS_KEY, code); } catch {}
  suspend();
  if (document.body) walk(document.body, maps);
  resume();
  window.dispatchEvent(new CustomEvent("i18n:changed", { detail: { locale: code } }));
}

function pickInitial(registry) {
  let saved = null;
  try { saved = localStorage.getItem(LS_KEY); } catch {}
  const codes = (registry.locales || []).map((l) => l.code);
  if (saved && codes.includes(saved)) return saved;
  const nav = (navigator.language || "").slice(0, 2).toLowerCase();
  if (codes.includes(nav)) return nav;
  return registry.default || BASE;
}

async function init() {
  try { state.dev = !!localStorage.getItem(DEV_KEY); } catch {}
  if (/[?&]i18ndebug\b/.test(location.search)) state.dev = true;
  const registry = await loadRegistry();
  const initial = pickInitial(registry);
  startObserver();
  if (initial !== BASE) {
    await setLocale(initial);
  } else {
    document.documentElement.setAttribute("lang", BASE);
  }
}

// ---- public API -----------------------------------------------------------

const api = {
  setLocale,
  getLocale: () => state.active,
  locales: () => (state.registry?.locales || []).slice(),
  t: (src, params) => {
    const maps = state.maps[state.active];
    const tr = maps ? translate(src, maps) : null;
    return interpolate(tr ?? src, params);
  },
  report: () => [...state.missing].sort(),
  _state: state,
};
window.i18n = api;
api.ready = (document.readyState === "loading"
  ? new Promise((r) => document.addEventListener("DOMContentLoaded", r, { once: true }))
  : Promise.resolve()
).then(init);

export default api;

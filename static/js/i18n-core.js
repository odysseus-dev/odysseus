// i18n-core.js — pure translation logic, no DOM, no I/O.
//
// Kept separate from i18n.js so it can be unit-tested under Node (see
// tests/i18n_core.test.mjs) and reused without pulling in the browser runtime.
//
// Catalog shape (source-keyed):
//   {
//     "_meta":      { code, name, nativeName, dir },
//     "_overrides": { "<dotted.key>": "<translation>" },   // optional, for collisions
//     "<English source string>": "<translation>",
//     "· {n} msgs":  "· {n} 件"                              // {name} placeholders ok
//   }

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Collapse internal whitespace runs and trim, so "More   Tools\n" matches the
// catalog key "More Tools". Kills the whitespace-variant class of misses.
// Translations carry their own spacing; the original outer whitespace is
// preserved separately by reattach().
export function normalize(s) {
  return typeof s === "string" ? s.replace(/\s+/g, " ").trim() : s;
}

// "Hi {name}" + {name:'Ada'} -> "Hi Ada". Unknown placeholders are left visible.
export function interpolate(str, params) {
  if (!params || typeof str !== "string") return str;
  return str.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

// Turn a source key containing {placeholders} into a matcher so dynamic DOM text
// ("· 3 msgs") can map onto the translated template ("· 3 件").
export function compilePattern(srcKey, target) {
  const names = [];
  let re = "";
  let last = 0;
  const rx = /\{(\w+)\}/g;
  let m;
  while ((m = rx.exec(srcKey))) {
    re += escapeRe(srcKey.slice(last, m.index)) + "(.+?)";
    names.push(m[1]);
    last = m.index + m[0].length;
  }
  re += escapeRe(srcKey.slice(last));
  return { re: new RegExp("^" + re + "$"), names, target, key: srcKey };
}

// catalog -> { source:{src:tr}, overrides:{key:tr}, patterns:[{re,names,target}] }
export function buildMaps(catalog) {
  const source = Object.create(null);
  const patterns = [];
  const overrides = (catalog && catalog._overrides) || Object.create(null);
  for (const k in catalog) {
    if (k === "_meta" || k === "_overrides") continue;
    const v = catalog[k];
    if (typeof v !== "string") continue;
    const nk = normalize(k);
    source[nk] = v;
    if (nk.includes("{")) patterns.push(compilePattern(nk, v));
  }
  return { source, overrides, patterns };
}

// Preserve the original leading/trailing whitespace around a translated core.
function reattach(orig, translated) {
  const lead = orig.match(/^\s*/)[0];
  const tail = orig.match(/\s*$/)[0];
  return lead + translated + tail;
}

/**
 * Translate a rendered text string. Returns the translation, or null if the
 * string is not a known UI string (so callers can leave it untouched).
 * Resolution: exact source match -> placeholder pattern -> null.
 */
export function translate(text, maps) {
  if (!maps || typeof text !== "string") return null;
  const key = normalize(text);
  if (!key) return null;
  const exact = maps.source[key];
  if (exact != null) return reattach(text, exact);
  for (const p of maps.patterns) {
    const m = p.re.exec(key);
    if (m) {
      const params = {};
      p.names.forEach((n, i) => (params[n] = m[i + 1]));
      return reattach(text, interpolate(p.target, params));
    }
  }
  return null;
}

/**
 * Resolve an explicit `data-i18n="<dotted.key>"` opt-in. Used for the handful of
 * context collisions (e.g. "Saved" the verb vs. the adjective). Falls back to a
 * source-string translation of the element's English, then null.
 */
export function lookupKey(key, sourceEnglish, maps) {
  if (!maps) return null;
  const ov = maps.overrides[key];
  if (ov != null) return ov;
  if (sourceEnglish != null) return translate(sourceEnglish, maps);
  return null;
}

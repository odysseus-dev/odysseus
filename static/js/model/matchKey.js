// static/js/model/matchKey.js
//
// Pure helper for matching a model name against a set of known keys. No DOM —
// safe to import anywhere and to unit-test under node.

// Return the most specific (longest) key that is a substring of `name`, or null.
// Returning the first match instead made "gpt-4o-mini" match the shorter
// "gpt-4o" key — billing it at gpt-4o rates (~16x) and showing the wrong
// context window.
//
// Claude keys spell versions with dashes ("claude-opus-4-5"), but some routes
// (OpenRouter: "anthropic/claude-opus-4.5") use dots, which would fall back to
// an older, differently priced key ("claude-opus-4"). Only Claude names are
// normalized: other families ("gpt-4.1") keep dots in their keys. Mirrors
// match_model_key in src/model_pricing.py.
export function matchModelKey(name, keys) {
  let n = (name || '').toLowerCase();
  if (n.includes('claude')) n = n.replace(/(\d)\.(?=\d)/g, '$1-');
  let best = null;
  for (const key of keys) {
    if (n.includes(key) && (best === null || key.length > best.length)) {
      best = key;
    }
  }
  return best;
}

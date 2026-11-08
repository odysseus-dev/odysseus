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
// an older, differently priced key ("claude-opus-4"). So the Claude version
// (only the one right after "claude-") is also tried with dashes, and wins only
// when the plain match is nothing or another Claude key: a name like
// "llama-3.1-8b-claude-3.5-distill" keeps its "llama-3.1" key. Mirrors
// match_model_key in src/model_pricing.py.
const CLAUDE_VERSION_DOT = /(claude-(?:[a-z]+-)?\d+)\.(?=\d)/g;

function longestKey(n, keys) {
  let best = null;
  for (const key of keys) {
    if (n.includes(key) && (best === null || key.length > best.length)) {
      best = key;
    }
  }
  return best;
}

export function matchModelKey(name, keys) {
  const list = Array.from(keys);
  const n = (name || '').toLowerCase();
  let best = longestKey(n, list);
  if (n.includes('claude') && (best === null || best.startsWith('claude'))) {
    const normalized = n.replace(CLAUDE_VERSION_DOT, '$1-');
    if (normalized !== n) {
      const alt = longestKey(normalized, list);
      if (alt !== null && (best === null || alt.length >= best.length)) best = alt;
    }
  }
  return best;
}

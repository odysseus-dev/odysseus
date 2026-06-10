// ============================================
// Fuzzy matcher — fzy-style subsequence scorer
// ============================================
// Pure module, no imports, no DOM. Used by the command palette
// (Search Everywhere) to rank ~150 registry items.

// Scoring constants. Tests pin RELATIVE ordering, not absolute scores,
// so these can be tuned safely.
const SCORE_MATCH = 16;        // every matched query char
const BONUS_CONSECUTIVE = 8;   // match directly follows the previous match
const BONUS_BOUNDARY = 12;     // match starts a word (after space/punct) or camelCase hump
const BONUS_PREFIX = 8;        // match at the very start of the target
const BONUS_SUBSTRING = 4;     // per char when the whole query is one exact substring
const PENALTY_GAP = -1;        // per skipped target char between matches
const PENALTY_LEADING = -0.5;  // per skipped target char before the first match
const MAX_LEADING_PENALTY = -6;

// True when target[i] begins a "word": position 0, after a non-alphanumeric
// separator, or a camelCase lower→upper transition (checked on original case).
function _isBoundary(target, i) {
  if (i === 0) return true;
  const prev = target[i - 1];
  if (!/[a-zA-Z0-9]/.test(prev)) return true;
  return /[a-z]/.test(prev) && /[A-Z]/.test(target[i]);
}

/**
 * Score `query` against `target` as a case-insensitive subsequence.
 * @returns {{score:number, positions:number[]}|null} null when query is not
 *   a subsequence of target. positions index into the ORIGINAL target so
 *   callers can highlight matched chars with case preserved.
 */
export function fuzzyScore(query, target) {
  if (!query) return { score: 0, positions: [] };
  if (!target) return null;
  const q = query.toLowerCase();
  const t = target.toLowerCase();

  // Greedy subsequence walk, preferring boundary starts: for each query char
  // take the next boundary occurrence if one exists ahead, else the next
  // plain occurrence.
  const positions = [];
  let ti = 0;
  for (let qi = 0; qi < q.length; qi++) {
    const c = q[qi];
    let at = -1;
    // After the first char, an immediate consecutive match beats jumping
    // ahead to a boundary (keeps runs together: "cal" → "Cal", not C…a…l).
    if (qi > 0 && t[ti] === c && positions[qi - 1] === ti - 1) {
      at = ti;
    } else {
      for (let i = ti; i < t.length; i++) {
        if (t[i] === c && _isBoundary(target, i)) { at = i; break; }
      }
      if (at === -1) at = t.indexOf(c, ti);
    }
    if (at === -1) return null;
    positions.push(at);
    ti = at + 1;
  }

  let score = 0;
  for (let k = 0; k < positions.length; k++) {
    const p = positions[k];
    score += SCORE_MATCH;
    if (_isBoundary(target, p)) score += BONUS_BOUNDARY;
    if (k === 0) {
      if (p === 0) score += BONUS_PREFIX;
      score += Math.max(MAX_LEADING_PENALTY, p * PENALTY_LEADING);
    } else {
      const gap = p - positions[k - 1] - 1;
      score += gap === 0 ? BONUS_CONSECUTIVE : gap * PENALTY_GAP;
    }
  }
  if (t.includes(q)) score += BONUS_SUBSTRING * q.length;
  return { score, positions };
}

/**
 * Split `text` into highlight runs. Pure helper for the palette renderer:
 * the renderer maps runs to textContent-only DOM nodes (<mark> for hl runs),
 * so item titles — which include DOM-scraped, user-influenced text — can
 * never inject HTML. positions index into the matched string (title +
 * keywords); only those inside `text` produce highlighted runs.
 * @returns {Array<{text:string, hl:boolean}>} runs concatenating to `text`
 */
export function highlightRuns(text, positions) {
  const inText = new Set((positions || []).filter(p => p >= 0 && p < text.length));
  const runs = [];
  for (let i = 0; i < text.length; i++) {
    const hl = inText.has(i);
    const last = runs[runs.length - 1];
    if (last && last.hl === hl) last.text += text[i];
    else runs.push({ text: text[i], hl });
  }
  return runs;
}

/**
 * Filter + rank items. Empty query returns ALL items in registry order
 * (score 0) so the palette can show everything on open.
 * @param {string} query
 * @param {Array} items
 * @param {(item:any)=>string} getText - text to match against
 * @returns {Array<{item:any, score:number, positions:number[]}>} sorted desc
 */
export function fuzzyFilter(query, items, getText) {
  if (!query) return items.map(item => ({ item, score: 0, positions: [] }));
  const out = [];
  for (const item of items) {
    const r = fuzzyScore(query, getText(item));
    if (r) out.push({ item, score: r.score, positions: r.positions });
  }
  // Stable sort by score desc (Array.prototype.sort is stable in modern JS,
  // so equal scores keep registry order).
  out.sort((a, b) => b.score - a.score);
  return out;
}

// frontend/src/features/documents/domain/sourceFidelity.js
/**
 * Pure source-fidelity engine and semantic verification boundary for Markdown editing.
 *
 * Provides Myers difference alignment, 3-way source patching, semantic equivalence
 * verification, and localized block-level source preservation.
 *
 * Result contract:
 * {
 *   status: 'preserved' | 'canonical-fallback' | 'unsupported',
 *   markdown: string,
 *   draft: string,
 *   reason: string | null
 * }
 */

import {
  computeSourceRevision,
  detectLineEnding,
  extractBlockSegments,
  joinSegments,
  segmentMarkdown,
} from './sourceSegmentation.js';

export { computeSourceRevision };

/**
 * Maximum allowable edit distance for Myers difference alignment within one block.
 * When character differences exceed this threshold, the algorithm cleanly reports
 * canonical fallback rather than attempting quadratic or divergent diffing.
 */
export const MAX_EDIT_DISTANCE = 1500;

/**
 * HTML5 void elements that do not require closing tags.
 */
const VOID_ELEMENTS = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

/**
 * Block elements for whitespace trimming in semantic comparison.
 */
const BLOCK_TAGS = new Set([
  'address', 'article', 'aside', 'blockquote', 'dd', 'details', 'dialog', 'div', 'dl', 'dt',
  'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'header', 'hgroup', 'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'table', 'tbody',
  'td', 'tfoot', 'th', 'thead', 'tr', 'ul',
]);

/**
 * Myers greedy difference algorithm (O(ND) time/space).
 * Returns matched index pairs [[aIndex, bIndex], ...] in ascending order,
 * or null when edit distance exceeds `limit`.
 *
 * @param {Array|string} a
 * @param {Array|string} b
 * @param {number} [limit]
 * @returns {Array<[number, number]>|null}
 */
export function matches(a, b, limit = MAX_EDIT_DISTANCE) {
  const n = a.length;
  const m = b.length;
  const max = Math.min(n + m, limit);
  const offset = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace = [];
  let found = -1;

  for (let d = 0; d <= max && found < 0; d++) {
    trace.push(v.slice(offset - d - 1, offset + d + 2));
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1]))
        ? v[offset + k + 1]
        : v[offset + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }
      v[offset + k] = x;
      if (x >= n && y >= m) {
        found = d;
        break;
      }
    }
  }

  if (found < 0) return null;

  const pairs = [];
  let x = n;
  let y = m;
  for (let d = found; d > 0; d--) {
    const prev = trace[d];
    const at = k => prev[k + d + 1];
    const k = x - y;
    const down = k === -d || (k !== d && at(k - 1) < at(k + 1));
    const prevK = down ? k + 1 : k - 1;
    const prevX = at(prevK);
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) {
      x--;
      y--;
      pairs.push([x, y]);
    }
    x = prevX;
    y = prevY;
  }
  while (x > 0 && y > 0) {
    x--;
    y--;
    pairs.push([x, y]);
  }
  return pairs.reverse();
}

/**
 * Align characters of string `a` to matching characters of string `b`.
 * Uses hierarchical two-pass alignment: line-level matching first to narrow hunks,
 * followed by character-level Myers diff within changed line spans.
 *
 * @param {string} a
 * @param {string} b
 * @param {number} [limit]
 * @returns {Int32Array|null} Map where index i in a maps to index in b, or -1.
 */
export function alignText(a, b, limit = MAX_EDIT_DISTANCE) {
  const map = new Int32Array(a.length).fill(-1);
  if (a === b) {
    for (let i = 0; i < a.length; i++) map[i] = i;
    return map;
  }

  const linesA = a.split('\n');
  const linesB = b.split('\n');
  const startsA = [];
  const startsB = [];
  linesA.reduce((at, line) => { startsA.push(at); return at + line.length + 1; }, 0);
  linesB.reduce((at, line) => { startsB.push(at); return at + line.length + 1; }, 0);

  const linePairs = matches(linesA, linesB, limit);
  if (!linePairs) return null;

  let lastA = -1;
  let lastB = -1;

  const matchHunk = (endA, endB) => {
    const fromA = lastA + 1 < linesA.length ? startsA[lastA + 1] : a.length;
    const fromB = lastB + 1 < linesB.length ? startsB[lastB + 1] : b.length;
    const toA = endA < linesA.length ? startsA[endA] : a.length;
    const toB = endB < linesB.length ? startsB[endB] : b.length;
    if (toA <= fromA || toB <= fromB) return true;
    const inner = matches(a.slice(fromA, toA), b.slice(fromB, toB), limit);
    if (!inner) return false;
    for (const [i, j] of inner) {
      map[fromA + i] = fromB + j;
    }
    return true;
  };

  for (const [i, j] of [...linePairs, [linesA.length, linesB.length]]) {
    if (!matchHunk(i, j)) return null;
    if (i < linesA.length) {
      const lineLen = linesA[i].length;
      const sA = startsA[i];
      const sB = startsB[j];
      for (let c = 0; c <= lineLen && sA + c < a.length && sB + c < b.length; c++) {
        map[sA + c] = sB + c;
      }
    }
    lastA = i;
    lastB = j;
  }

  return map;
}

/**
 * Three-way character merge applying an edit from `baseline` to `edited` onto `source`.
 * Keeps original source characters and delimiters for unchanged parts, while
 * incorporating new text from `edited`.
 *
 * @param {string} source
 * @param {string} baseline
 * @param {string} edited
 * @param {number} [limit]
 * @returns {string|null} Merged Markdown string, or null if alignment failed.
 */
export function patchSource(source, baseline, edited, limit = MAX_EDIT_DISTANCE) {
  if (edited === baseline) return source;

  const toSource = alignText(baseline, source, limit);
  const toEdited = alignText(baseline, edited, limit);
  if (!toSource || !toEdited) return null;

  // Record gaps in source before each aligned baseline character
  const gaps = new Map();
  let last = -1;
  let lastShared = -1;

  for (let i = 0; i < baseline.length; i++) {
    if (toSource[i] < 0) continue;
    gaps.set(i, source.slice(last + 1, toSource[i]));
    last = toSource[i];
    lastShared = i;
  }

  if (lastShared < 0) return null;
  const tail = source.slice(last + 1);

  let out = '';
  let typed = 0;
  let keptBefore = false;

  const emitTyped = upTo => {
    out += edited.slice(typed, upTo);
    typed = upTo;
  };

  for (let i = 0; i < baseline.length; i++) {
    const at = toEdited[i];
    if (at >= 0) {
      emitTyped(at);
      typed = at + 1;
    }
    if (toSource[i] < 0) continue;
    const kept = at >= 0;
    if (kept || keptBefore) {
      out += gaps.get(i) || '';
    }
    if (kept) {
      out += source[toSource[i]];
    }
    keptBefore = kept;
  }

  emitTyped(edited.length);
  if (keptBefore) {
    out += tail;
  }
  return out;
}

/**
 * Parse an HTML attribute string into a key-value dictionary.
 * Supports double quotes, single quotes, unquoted values, and boolean attributes.
 * @param {string} rawAttrs
 * @returns {object}
 */
export function parseAttributes(rawAttrs) {
  const attrs = {};
  if (!rawAttrs) return attrs;
  const attrRe = /([a-z0-9_:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/gi;
  let match;
  while ((match = attrRe.exec(rawAttrs))) {
    const name = match[1].toLowerCase();
    const val = match[2] !== undefined ? match[2] : (match[3] !== undefined ? match[3] : (match[4] !== undefined ? match[4] : ''));
    attrs[name] = val;
  }
  return attrs;
}

/**
 * Parse HTML string into flat tag/text tokens.
 * Zero-dependency, pure domain implementation.
 * @param {string} html
 * @returns {Array<{ type: 'open'|'close'|'text', tag?: string, attrs?: object, isVoid?: boolean, text?: string }>}
 */
export function parseHtmlTokens(html) {
  const tokens = [];
  const len = html.length;
  let i = 0;

  while (i < len) {
    if (html[i] === '<') {
      // Comments
      if (html.startsWith('<!--', i)) {
        const endComment = html.indexOf('-->', i + 4);
        if (endComment !== -1) {
          i = endComment + 3;
          continue;
        }
      }

      const closeIdx = html.indexOf('>', i);
      if (closeIdx !== -1) {
        const rawTagContent = html.slice(i + 1, closeIdx).trim();
        i = closeIdx + 1;
        if (rawTagContent.startsWith('/')) {
          const tag = rawTagContent.slice(1).trim().toLowerCase();
          tokens.push({ type: 'close', tag });
        } else {
          const isSelfClosing = rawTagContent.endsWith('/');
          const cleanTagContent = isSelfClosing ? rawTagContent.slice(0, -1).trim() : rawTagContent;
          const firstSpace = cleanTagContent.search(/\s/);
          let tag, rawAttrs;
          if (firstSpace === -1) {
            tag = cleanTagContent.toLowerCase();
            rawAttrs = '';
          } else {
            tag = cleanTagContent.slice(0, firstSpace).toLowerCase();
            rawAttrs = cleanTagContent.slice(firstSpace).trim();
          }
          const attrs = parseAttributes(rawAttrs);
          const isVoid = isSelfClosing || VOID_ELEMENTS.has(tag);
          tokens.push({ type: 'open', tag, attrs, isVoid });
          if (isVoid) {
            tokens.push({ type: 'close', tag, isVoid: true });
          }
        }
        continue;
      }
    }

    let nextOpen = html.indexOf('<', i);
    if (nextOpen === -1) nextOpen = len;
    tokens.push({ type: 'text', text: html.slice(i, nextOpen) });
    i = nextOpen;
  }

  return tokens;
}

/**
 * Decode common HTML entities into raw Unicode characters.
 * @param {string} text
 * @returns {string}
 */
export function decodeHtmlEntities(text) {
  if (!text) return '';
  return text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&nbsp;|\u00a0/g, ' ');
}

/**
 * Normalize an HTML string for semantic equivalence comparison.
 * - Exact characters, whitespace, and newlines inside <pre> and <code> are preserved using nesting depth tracking.
 * - Outside code blocks, whitespace runs are collapsed and &nbsp; is normalized to space.
 * - Tag attributes, CSS style declarations, and class lists are sorted deterministically.
 * - Ephemeral attributes (e.g. data-ody-*, auto-generated IDs) are ignored.
 * - HTML entities are decoded to Unicode.
 *
 * @param {string} html
 * @returns {string}
 */
export function normalizeSemanticHtml(html) {
  if (!html) return '';
  const tokens = parseHtmlTokens(String(html));
  let out = '';
  let codeDepth = 0;

  for (const tok of tokens) {
    if (tok.type === 'open') {
      if (tok.tag === 'pre' || tok.tag === 'code') {
        codeDepth++;
      }
      const keys = Object.keys(tok.attrs || {}).sort();
      let attrStr = '';
      for (const k of keys) {
        if (k.startsWith('data-ody-')) continue;
        if (k === 'id' && (tok.attrs[k].startsWith('mermaid-') || tok.attrs[k].startsWith('ody-'))) continue;
        if (k === 'contenteditable' || k === 'spellcheck') continue;

        let v = tok.attrs[k];
        if (k === 'style') {
          // Sort CSS styles deterministically
          const styleRules = v.split(';')
            .map(s => s.trim())
            .filter(Boolean)
            .map(s => {
              const colonIdx = s.indexOf(':');
              if (colonIdx === -1) return s;
              return `${s.slice(0, colonIdx).trim()}: ${s.slice(colonIdx + 1).trim()}`;
            })
            .sort();
          v = styleRules.join('; ') + (styleRules.length ? ';' : '');
        } else if (k === 'class') {
          // Sort class names deterministically
          v = v.split(/\s+/).map(c => c.trim()).filter(Boolean).sort().join(' ');
        }
        attrStr += ` ${k}="${v}"`;
      }
      out += `<${tok.tag}${attrStr}>`;
    } else if (tok.type === 'close') {
      if (tok.tag === 'pre' || tok.tag === 'code') {
        codeDepth = Math.max(0, codeDepth - 1);
      }
      out += `</${tok.tag}>`;
    } else if (tok.type === 'text') {
      if (codeDepth > 0) {
        out += tok.text;
      } else {
        const decoded = decodeHtmlEntities(tok.text);
        out += decoded.replace(/\s+/g, ' ');
      }
    }
  }

  // Normalize inter-tag whitespace between block tags while preserving inline formatting spaces
  return out
    .replace(/>\s+</g, (match, offset, str) => {
      // Find preceding tag and following tag
      const before = str.slice(0, offset);
      const after = str.slice(offset + match.length);
      const prevTagMatch = before.match(/<\/?([a-z0-9]+)[^>]*>$/i);
      const nextTagMatch = after.match(/^<\/?([a-z0-9]+)[^>]*>/i);
      if (prevTagMatch && nextTagMatch) {
        const t1 = prevTagMatch[1].toLowerCase();
        const t2 = nextTagMatch[1].toLowerCase();
        if (BLOCK_TAGS.has(t1) || BLOCK_TAGS.has(t2)) {
          return '><';
        }
      }
      return match;
    })
    .trim();
}

/**
 * Compare two rendered HTML strings for semantic visual equivalence.
 * Returns true if both render identically in the user-visible surface.
 *
 * @param {string} htmlA
 * @param {string} htmlB
 * @returns {boolean}
 */
export function compareSemanticHtml(htmlA, htmlB) {
  if (htmlA === htmlB) return true;
  return normalizeSemanticHtml(htmlA) === normalizeSemanticHtml(htmlB);
}

/**
 * Preserve the source of an individual Markdown block.
 * Explicitly distinguishes source preservation, verified canonical fallback,
 * and unsupported conversions. Fail-closed contract: missing validation or
 * renderer exceptions never authorize persistence.
 *
 * @param {object} options
 * @param {string} options.source The original stored Markdown for this block.
 * @param {string} options.baseline The canonical Markdown serialized at open/render time.
 * @param {string} options.edited The canonical Markdown serialized after the visual edit.
 * @param {function} [options.render] Function converting Markdown to HTML for equivalence check.
 * @param {function} [options.compareSemantics] Custom semantic comparator (defaults to compareSemanticHtml).
 * @param {function} [options.inspectCompatibility] Function verifying Markdown visual compatibility.
 * @param {number} [options.maxEditDistance] Difference threshold before falling back.
 * @param {boolean} [options.allowCanonicalFallback] Whether to allow canonical fallback if patching fails. Defaults to true.
 * @returns {{ status: 'preserved'|'canonical-fallback'|'unsupported', markdown: string, draft: string, reason: string|null }}
 */
export function preserveBlockSource({
  source,
  baseline,
  edited,
  render = null,
  compareSemantics = compareSemanticHtml,
  inspectCompatibility = null,
  maxEditDistance = MAX_EDIT_DISTANCE,
  allowCanonicalFallback = true,
}) {
  // 1. Identity check: if edited equals baseline, no visual edit occurred
  if (edited === baseline) {
    return {
      status: 'preserved',
      markdown: source,
      draft: edited,
      reason: null,
    };
  }

  // 2. Fail-closed validation requirement: edits must be validated by a renderer
  if (typeof render !== 'function') {
    return {
      status: 'unsupported',
      markdown: source,
      draft: edited,
      reason: 'missing-semantic-validator',
    };
  }

  // 3. Compatibility check on edited input if an inspector is provided
  if (typeof inspectCompatibility === 'function') {
    let compat;
    try {
      compat = inspectCompatibility(edited);
    } catch (err) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: edited,
        reason: 'compatibility-inspection-error',
      };
    }
    if (!compat || !compat.supported) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: edited,
        reason: (compat && compat.reasons && compat.reasons[0]) || 'unsupported-visual-construct',
      };
    }
  }

  // 4. Attempt 3-way source patching
  const patched = patchSource(source, baseline, edited, maxEditDistance);

  // If alignment failed (diff distance exceeded or structural mismatch)
  if (patched === null) {
    if (!allowCanonicalFallback) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: edited,
        reason: 'diff-limit-exceeded',
      };
    }
    // Verify edited text before allowing canonical fallback
    try {
      render(edited);
    } catch (err) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: edited,
        reason: 'render-error',
      };
    }
    return {
      status: 'canonical-fallback',
      markdown: edited,
      draft: edited,
      reason: 'diff-limit-exceeded',
    };
  }

  // 5. Verify semantic equivalence with renderer
  let renderedPatched;
  let renderedEdited;
  try {
    renderedPatched = render(patched);
    renderedEdited = render(edited);
  } catch (err) {
    // Renderer failure must never authorize persistence
    return {
      status: 'unsupported',
      markdown: source,
      draft: edited,
      reason: 'render-error',
    };
  }

  const isEquivalent = compareSemantics(renderedPatched, renderedEdited);
  if (!isEquivalent) {
    if (!allowCanonicalFallback) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: edited,
        reason: 'semantic-divergence',
      };
    }
    return {
      status: 'canonical-fallback',
      markdown: edited,
      draft: edited,
      reason: 'semantic-divergence',
    };
  }

  // If source had non-canonical formatting that was lost during patching
  if (source !== baseline && patched === edited) {
    return {
      status: 'canonical-fallback',
      markdown: edited,
      draft: edited,
      reason: 'source-formatting-lost',
    };
  }

  return {
    status: 'preserved',
    markdown: patched,
    draft: edited,
    reason: null,
  };
}

/**
 * Apply localized edits to a full Markdown document while preserving all
 * untouched blocks and separators byte-for-byte.
 *
 * @param {string} source Original full document Markdown text.
 * @param {Array<object>|object} edits Block edits to apply.
 *   Can be an object map { [blockIndex]: { edited: string, baseline?: string, range?: [number, number], blockId?: string } }
 *   or an array of operations:
 *     { op: 'replace', index: number, edited: string, baseline?: string, range?: [number, number], blockId?: string }
 *     { op: 'delete', index: number, baseline?: string, range?: [number, number], blockId?: string }
 *     { op: 'insert', afterIndex: number, text: string }
 * @param {object} [options]
 * @param {string} [options.expectedRevision] Expected document revision hash. Rejects if mismatched.
 * @param {function} [options.render] Function converting Markdown to HTML for semantic verification.
 * @param {function} [options.compareSemantics] Custom semantic comparator.
 * @param {function} [options.inspectCompatibility] Function verifying visual compatibility.
 * @param {number} [options.maxEditDistance] Difference threshold before falling back.
 * @param {boolean} [options.allowCanonicalFallback] Whether canonical fallback is permitted. Defaults to true.
 * @returns {{
 *   status: 'preserved'|'canonical-fallback'|'unsupported',
 *   markdown: string,
 *   draft: string,
 *   reason: string|null,
 *   blockResults: Array<{ index: number, status: string, reason: string|null }>
 * }}
 */
export function applyLocalizedEdits(source, edits, options = {}) {
  // 1. Check expected revision if provided
  if (options.expectedRevision !== undefined) {
    const currentRev = computeSourceRevision(source);
    if (options.expectedRevision !== currentRev) {
      return {
        status: 'unsupported',
        markdown: source,
        draft: '',
        reason: 'stale-source-revision',
        blockResults: [],
      };
    }
  }

  const segments = segmentMarkdown(source);
  const blockSegments = extractBlockSegments(segments);
  const blockCount = blockSegments.length;

  // 2. Normalize edits into operations list
  let operations = [];
  if (Array.isArray(edits)) {
    operations = edits;
  } else if (edits && typeof edits === 'object') {
    for (const [key, val] of Object.entries(edits)) {
      const idx = Number(key);
      if (typeof val === 'string') {
        operations.push({ op: 'replace', index: idx, edited: val });
      } else if (val && typeof val === 'object') {
        operations.push({
          op: 'replace',
          index: idx,
          edited: val.edited,
          baseline: val.baseline,
          range: val.range,
          blockId: val.blockId,
        });
      }
    }
  }

  // 3. Validate block mappings, ranges, and baselines
  const seenIndices = new Set();
  const compare = options.compareSemantics || compareSemanticHtml;

  for (const op of operations) {
    if (op.op === 'replace' || op.op === 'delete') {
      if (!Number.isInteger(op.index) || op.index < 0 || op.index >= blockCount) {
        return {
          status: 'unsupported',
          markdown: source,
          draft: '',
          reason: `ambiguous-block-mapping: invalid index ${op.index}`,
          blockResults: [],
        };
      }
      if (seenIndices.has(op.index)) {
        return {
          status: 'unsupported',
          markdown: source,
          draft: '',
          reason: `ambiguous-block-mapping: duplicate index ${op.index}`,
          blockResults: [],
        };
      }
      seenIndices.add(op.index);

      const targetSeg = blockSegments[op.index];

      // Validate range if provided
      if (Array.isArray(op.range)) {
        if (targetSeg.startOffset !== op.range[0] || targetSeg.endOffset !== op.range[1]) {
          return {
            status: 'unsupported',
            markdown: source,
            draft: '',
            reason: `ambiguous-block-mapping: range mismatch for block ${op.index}`,
            blockResults: [],
          };
        }
      }

      // Validate blockId if provided
      if (typeof op.blockId === 'string' && targetSeg.blockId !== op.blockId) {
        return {
          status: 'unsupported',
          markdown: source,
          draft: '',
          reason: `ambiguous-block-mapping: blockId mismatch for block ${op.index}`,
          blockResults: [],
        };
      }

      // Validate baseline if provided: must match target raw or render semantically identical
      if (op.baseline != null) {
        let baselineMatches = (op.baseline === targetSeg.raw);
        if (!baselineMatches && typeof options.render === 'function') {
          try {
            const hBase = options.render(op.baseline);
            const hTarget = options.render(targetSeg.raw);
            baselineMatches = compare(hBase, hTarget);
          } catch {
            baselineMatches = false;
          }
        }
        if (!baselineMatches) {
          return {
            status: 'unsupported',
            markdown: source,
            draft: '',
            reason: `ambiguous-block-mapping: baseline mismatch for block ${op.index}`,
            blockResults: [],
          };
        }
      }
    } else if (op.op === 'insert') {
      if (!Number.isInteger(op.afterIndex) || op.afterIndex < -1 || op.afterIndex >= blockCount) {
        return {
          status: 'unsupported',
          markdown: source,
          draft: '',
          reason: `ambiguous-block-mapping: invalid afterIndex ${op.afterIndex}`,
          blockResults: [],
        };
      }
    }
  }

  // 4. Missing semantic validator check for mutating operations
  const hasMutations = operations.some(op => {
    if (op.op === 'delete' || op.op === 'insert') return true;
    if (op.op === 'replace') {
      const targetSeg = blockSegments[op.index];
      const baseline = op.baseline != null ? op.baseline : targetSeg.raw;
      return op.edited !== baseline;
    }
    return false;
  });

  if (hasMutations && typeof options.render !== 'function') {
    return {
      status: 'unsupported',
      markdown: source,
      draft: '',
      reason: 'missing-semantic-validator',
      blockResults: [],
    };
  }

  const blockResults = [];
  let overallStatus = 'preserved';
  let overallReason = null;

  // 5. Process replace and delete operations
  for (const op of operations) {
    if (op.op === 'replace') {
      const targetSeg = blockSegments[op.index];
      const baseline = op.baseline != null ? op.baseline : targetSeg.raw;
      const res = preserveBlockSource({
        source: targetSeg.raw,
        baseline,
        edited: op.edited,
        render: options.render,
        compareSemantics: options.compareSemantics,
        inspectCompatibility: options.inspectCompatibility,
        maxEditDistance: options.maxEditDistance,
        allowCanonicalFallback: options.allowCanonicalFallback,
      });

      blockResults.push({ index: op.index, status: res.status, reason: res.reason });
      if (res.status === 'unsupported') {
        overallStatus = 'unsupported';
        overallReason = res.reason;
      } else if (res.status === 'canonical-fallback' && overallStatus !== 'unsupported') {
        overallStatus = 'canonical-fallback';
        overallReason = overallReason || res.reason;
      }

      targetSeg.modified = true;
      targetSeg.text = res.markdown;
    } else if (op.op === 'delete') {
      const targetSeg = blockSegments[op.index];
      targetSeg.modified = true;
      targetSeg.text = '';

      // Clean up adjacent separator to avoid orphaned double separators
      const segIdx = segments.indexOf(targetSeg);
      if (segIdx + 1 < segments.length && segments[segIdx + 1].type === 'separator') {
        segments[segIdx + 1].modified = true;
        segments[segIdx + 1].text = '';
      } else if (segIdx > 0 && segments[segIdx - 1].type === 'separator') {
        segments[segIdx - 1].modified = true;
        segments[segIdx - 1].text = '';
      }

      blockResults.push({ index: op.index, status: 'preserved', reason: 'deleted' });
    }
  }

  // 6. Handle insert operations with boundary preservation
  const insertOps = operations.filter(o => o.op === 'insert');
  if (insertOps.length > 0) {
    const eol = detectLineEnding(source);
    const standardSep = `${eol}${eol}`;

    for (const op of insertOps) {
      const newBlockSeg = {
        type: 'block',
        blockType: 'paragraph',
        raw: op.text,
        modified: true,
        text: op.text,
      };

      if (op.afterIndex === -1) {
        // Insert at beginning of document
        if (segments.length > 0) {
          segments.unshift(newBlockSeg, { type: 'separator', raw: standardSep });
        } else {
          segments.push(newBlockSeg);
        }
      } else {
        const targetSeg = blockSegments[op.afterIndex];
        const segIdx = segments.indexOf(targetSeg);
        const hasNextSep = (segIdx + 1 < segments.length && segments[segIdx + 1].type === 'separator');

        if (hasNextSep) {
          // An existing separator exists between this block and next block
          // Ensure both before and after inserted block have valid blank line separation
          const nextSep = segments[segIdx + 1];
          if (!nextSep.raw.includes('\n\n') && !nextSep.raw.includes('\r\n\r\n')) {
            nextSep.modified = true;
            nextSep.text = standardSep;
          }
          segments.splice(segIdx + 1, 0, { type: 'separator', raw: standardSep }, newBlockSeg);
        } else {
          // No separator between blocks; insert separators on both sides
          segments.splice(segIdx + 1, 0, { type: 'separator', raw: standardSep }, newBlockSeg, { type: 'separator', raw: standardSep });
        }
      }
      blockResults.push({ index: -1, status: 'preserved', reason: 'inserted' });
    }
  }

  const candidateMarkdown = joinSegments(segments);

  if (overallStatus === 'unsupported') {
    return {
      status: 'unsupported',
      markdown: source,
      draft: candidateMarkdown,
      reason: overallReason,
      blockResults,
    };
  }

  // 7. Verify post-edit boundary isolation
  const verifySegments = segmentMarkdown(candidateMarkdown);
  const verifyBlocks = extractBlockSegments(verifySegments);

  // Check that untouched blocks were not altered or merged
  let vIdx = 0;
  for (let i = 0; i < blockCount; i++) {
    const originalBlock = blockSegments[i];
    const op = operations.find(o => o.index === i);
    if (!op) {
      // Untouched block: find it in verifyBlocks
      const match = verifyBlocks.find(b => b.raw === originalBlock.raw);
      if (!match) {
        // Untouched block was merged or corrupted
        return {
          status: 'unsupported',
          markdown: source,
          draft: candidateMarkdown,
          reason: 'insertion-boundary-collision',
          blockResults,
        };
      }
    }
  }

  return {
    status: overallStatus,
    markdown: candidateMarkdown,
    draft: candidateMarkdown,
    reason: overallReason,
    blockResults,
  };
}

// frontend/src/features/documents/ui/richEditor/markdownVisualSync.js
/**
 * UI bridge between Markdown documents and the visual `#doc-email-richbody` surface.
 * Manages per-user mode preference, compatibility detection/banner rendering,
 * DOM-to-descriptor conversion, non-destructive mode switching, and paste cleanup.
 *
 * Data-integrity contract:
 * - Opening or toggling a document never rewrites its Markdown. Until the visual
 *   content differs semantically from what was rendered, the exact source string is
 *   kept (canonical-baseline comparison).
 * - The visual surface is bound to the document it rendered. Synchronization into any
 *   other document, or from a read-only preview, is refused.
 * - A document is offered in Visual mode only when the codec inspector accepts it and
 *   canonicalizing it changes neither the codec rendering nor the application's
 *   Markdown renderer output, so the first real edit cannot silently rewrite
 *   untouched content.
 */

import { activeDocId, docs } from '../../application/documentState.js';
import {
  inspectMarkdownVisualCompatibility,
  markdownToVisualHtml,
  serializeVisualNodesToBlocks,
  serializeVisualNodesToMarkdown,
  serializeVisualNodesWithFidelity,
} from '../../domain/markdownVisualCodec.js';
import {
  applyLocalizedEdits,
  computeSourceRevision,
  matches,
} from '../../domain/sourceFidelity.js';
import {
  extractBlockSegments,
  segmentMarkdown,
} from '../../domain/sourceSegmentation.js';
import { _isFormBackedDoc } from '../pdf/annotations.js';

export const MD_EDITOR_MODE_KEY = 'odysseus-doc-md-editor-mode';

const REWRITE_REASON = 'formatting the visual editor would rewrite';
const ASSESSMENT_CACHE_LIMIT = 16;
const _assessmentCache = new Map();

let _syncingVisualToTextarea = false;

export function isSyncingVisualToTextarea() {
  return _syncingVisualToTextarea;
}

export function setSyncingVisualToTextarea(val) {
  _syncingVisualToTextarea = Boolean(val);
}

/**
 * Return true if the document is a regular editable Markdown document
 * (not PDF form-backed).
 * @param {object|null|undefined} doc
 * @returns {boolean}
 */
export function isVisualMarkdownDoc(doc) {
  if (!doc) return false;
  const lang = String(doc.language || '').toLowerCase();
  return lang === 'markdown' && !_isFormBackedDoc(doc.content || '');
}

/**
 * Read the persisted global Markdown editor mode preference ('visual' by default).
 * @returns {'visual'|'markdown'}
 */
export function getMarkdownModePreference() {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(MD_EDITOR_MODE_KEY) : null;
    if (raw === 'markdown') return 'markdown';
  } catch (_) {}
  return 'visual';
}

/**
 * Persist the global Markdown editor mode preference when storage is available.
 * @param {'visual'|'markdown'} mode
 */
export function setMarkdownModePreference(mode) {
  const normalized = mode === 'markdown' ? 'markdown' : 'visual';
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(MD_EDITOR_MODE_KEY, normalized);
    }
  } catch (_) {}
}

/**
 * Convert live DOM child nodes inside `root` into normalized descriptor nodes
 * consumed by `serializeVisualNodesToMarkdown`.
 * @param {Node|null} root
 * @returns {Array}
 */
export function domToVisualNodes(root) {
  if (!root || !root.childNodes) return [];
  const out = [];
  const children = root.childNodes;
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    if (child.nodeType === 3) {
      out.push({ type: 'text', text: child.nodeValue || '' });
      continue;
    }
    if (child.nodeType !== 1) continue;

    const tag = String(child.tagName || '').toLowerCase();
    if (!tag || tag === 'script' || tag === 'style') continue;

    const attrs = {};
    if (child.attributes) {
      for (let j = 0; j < child.attributes.length; j++) {
        const attr = child.attributes[j];
        const name = String(attr.name || '').toLowerCase();
        if (name === 'href' || name === 'src' || name === 'alt' || name === 'data-checked' || name === 'data-lang' || name === 'start') {
          attrs[name] = attr.value;
        }
      }
    }

    const classes = child.classList ? Array.from(child.classList) : [];
    const style = {};
    if (child.style) {
      if (child.style.fontWeight) style.fontWeight = child.style.fontWeight;
      if (child.style.fontStyle) style.fontStyle = child.style.fontStyle;
      if (child.style.textDecorationLine) style.textDecorationLine = child.style.textDecorationLine;
      if (child.style.textDecoration) style.textDecoration = child.style.textDecoration;
      if (child.style.fontFamily) style.fontFamily = child.style.fontFamily;
      if (child.style.textAlign) style.textAlign = child.style.textAlign;
    }

    out.push({
      type: 'element',
      tag,
      attrs,
      classes,
      style,
      children: domToVisualNodes(child),
    });
  }
  return out;
}

/**
 * Serialize the Visual Markdown DOM in `rich` back to canonical Markdown.
 * @param {HTMLElement|null} rich
 * @returns {string}
 */
export function visualRichToMarkdown(rich) {
  if (!rich) return '';
  return serializeVisualNodesToMarkdown(domToVisualNodes(rich));
}

export function visualRichToMarkdownWithFidelity(rich) {
  if (!rich) return { markdown: '', lossless: true, reasons: [] };
  return serializeVisualNodesWithFidelity(domToVisualNodes(rich));
}

/**
 * Extract an array of individual canonical Markdown block strings from the live visual DOM.
 * Each top-level element in `rich` represents one visual Markdown block.
 * @param {HTMLElement|null} rich
 * @returns {string[]}
 */
export function extractVisualBlocks(rich) {
  if (!rich) return [];
  return serializeVisualNodesToBlocks(domToVisualNodes(rich));
}

/**
 * Compute the minimal set of block operations (replace, delete, insert) between
 * the baseline canonical blocks and the current visual blocks.
 * @param {string[]} baselineBlocks
 * @param {string[]} currentBlocks
 * @param {Array<object>} [blockSegments]
 * @returns {Array<object>|null}
 */
export function diffBlockOperations(baselineBlocks, currentBlocks, blockSegments = null) {
  if (!Array.isArray(baselineBlocks) || !Array.isArray(currentBlocks)) return null;

  // Identity check
  if (baselineBlocks.length === currentBlocks.length) {
    let same = true;
    for (let i = 0; i < baselineBlocks.length; i++) {
      if (baselineBlocks[i] !== currentBlocks[i]) {
        same = false;
        break;
      }
    }
    if (same) return [];
  }

  // 1-to-1 replacements when block counts match
  if (baselineBlocks.length === currentBlocks.length) {
    const ops = [];
    for (let i = 0; i < baselineBlocks.length; i++) {
      if (baselineBlocks[i] !== currentBlocks[i]) {
        const seg = blockSegments && blockSegments[i];
        ops.push({
          op: 'replace',
          index: i,
          baseline: baselineBlocks[i],
          edited: currentBlocks[i],
          range: seg ? [seg.startOffset, seg.endOffset] : undefined,
          blockId: seg ? seg.blockId : undefined,
        });
      }
    }
    return ops;
  }

  // Different counts: Myers diff alignment
  const pairs = matches(baselineBlocks, currentBlocks);
  if (!pairs) return null;

  const ops = [];
  let lastB = -1;
  let lastC = -1;

  for (const [b, c] of [...pairs, [baselineBlocks.length, currentBlocks.length]]) {
    const gapB = b - lastB - 1;
    const gapC = c - lastC - 1;

    if (gapB === 1 && gapC === 1) {
      const seg = blockSegments && blockSegments[lastB + 1];
      ops.push({
        op: 'replace',
        index: lastB + 1,
        baseline: baselineBlocks[lastB + 1],
        edited: currentBlocks[lastC + 1],
        range: seg ? [seg.startOffset, seg.endOffset] : undefined,
        blockId: seg ? seg.blockId : undefined,
      });
    } else {
      for (let delIdx = lastB + 1; delIdx < b; delIdx++) {
        const seg = blockSegments && blockSegments[delIdx];
        ops.push({
          op: 'delete',
          index: delIdx,
          baseline: baselineBlocks[delIdx],
          range: seg ? [seg.startOffset, seg.endOffset] : undefined,
          blockId: seg ? seg.blockId : undefined,
        });
      }
      for (let insIdx = lastC + 1; insIdx < c; insIdx++) {
        ops.push({
          op: 'insert',
          afterIndex: lastB >= 0 ? lastB : -1,
          text: currentBlocks[insIdx],
        });
      }
    }

    lastB = b;
    lastC = c;
  }

  return ops;
}

function _canonicalizeMarkdown(markdown) {
  if (typeof DOMParser === 'undefined') return null;
  // DOMParser documents are inert: no script execution and no resource loads.
  const parsed = new DOMParser().parseFromString(`<body>${markdownToVisualHtml(markdown)}</body>`, 'text/html');
  return parsed.body ? visualRichToMarkdown(parsed.body) : null;
}

function _comparableAppRendering(html) {
  return String(html ?? '')
    .replace(/\s(?:id|data-id)="(?:mermaid|thinking|collapse)-[^"]*"/g, '')
    .replace(/>\s+</g, '><')
    .trim();
}

function _escapeSignature(markdown) {
  return (String(markdown).match(/\\[!-/:-@[-`{-~]/g) || []).sort().join('');
}

/**
 * Explain why replacing `original` with `canonical` would change the document, or
 * return '' when the replacement is meaning-preserving.
 * @param {string} original
 * @param {string} canonical
 * @returns {string}
 */
function _rewriteReason(rawOriginal, canonical) {
  // Line-ending style carries no meaning (the source textarea also stores LF).
  const original = String(rawOriginal ?? '').replace(/\r\n?/g, '\n');
  if (canonical === original) return '';
  if (markdownToVisualHtml(canonical) !== markdownToVisualHtml(original)) return REWRITE_REASON;
  const renderer = globalThis.markdownModule?.mdToHtml;
  if (typeof renderer === 'function') {
    try {
      const before = _comparableAppRendering(renderer(original, { shortcodes: false }));
      const after = _comparableAppRendering(renderer(canonical, { shortcodes: false }));
      return before === after ? '' : REWRITE_REASON;
    } catch (_) {
      return REWRITE_REASON;
    }
  }
  // Without the application renderer, accept only canonicalization that keeps the
  // same backslash escapes, so unparsed syntax can never turn into escaped text.
  return _escapeSignature(canonical) === _escapeSignature(original) ? '' : REWRITE_REASON;
}

/**
 * Decide whether `markdown` can be edited visually without the first edit silently
 * rewriting untouched content.
 * @param {string} markdown
 * @returns {{ supported: boolean, reasons: string[] }}
 */
export function assessMarkdownVisualSupport(markdown) {
  const src = String(markdown ?? '');
  if (_assessmentCache.has(src)) return _assessmentCache.get(src);
  let result = inspectMarkdownVisualCompatibility(src);
  if (result.supported && src.trim()) {
    const canonical = _canonicalizeMarkdown(src);
    const reason = canonical === null ? '' : _rewriteReason(src, canonical);
    if (reason) result = { supported: false, reasons: [reason] };
  }
  if (_assessmentCache.size >= ASSESSMENT_CACHE_LIMIT) {
    _assessmentCache.delete(_assessmentCache.keys().next().value);
  }
  _assessmentCache.set(src, result);
  return result;
}

/**
 * Determine which editor mode should be shown for a Markdown document without
 * overwriting the user's global preference when a specific document contains
 * non-round-trippable Markdown constructs.
 * @param {object|null|undefined} doc
 * @returns {'visual'|'markdown'}
 */
export function resolveMarkdownEditorMode(doc) {
  if (!isVisualMarkdownDoc(doc)) return 'markdown';
  if (doc?._visualSyncBlocked) return 'visual';
  const compat = assessMarkdownVisualSupport(doc.content || '');
  doc._visualCompatReasons = compat.supported ? [] : compat.reasons;
  if (!compat.supported) return 'markdown';
  const preferred = doc._markdownEditorMode === 'markdown' || doc._markdownEditorMode === 'visual'
    ? doc._markdownEditorMode
    : getMarkdownModePreference();
  doc._markdownEditorMode = preferred;
  return preferred;
}

function _boundDocId(rich) {
  return rich?.dataset?.visualDocId || '';
}

/**
 * Return true when the visual surface currently renders an editable view of `doc`.
 * @param {HTMLElement|null} rich
 * @param {object|null} doc
 */
export function isVisualSurfaceBoundTo(rich, doc) {
  return Boolean(rich && doc && doc.id != null && _boundDocId(rich) === String(doc.id));
}

/**
 * Detach the visual surface from any document so that nothing it shows (for example
 * a read-only version preview) can be synchronized into document content.
 * @param {HTMLElement|null} rich
 */
export function unbindVisualSurface(rich) {
  if (rich?.dataset) delete rich.dataset.visualDocId;
}

/**
 * Return true when `#doc-email-richbody` is currently visible and operating
 * in Visual Markdown mode for the active document.
 * @returns {boolean}
 */
export function isMarkdownVisualModeActive() {
  if (typeof document === 'undefined') return false;
  const rich = document.getElementById('doc-email-richbody');
  if (!rich || rich.style.display === 'none') return false;
  if (!rich.classList.contains('markdown-visual-mode')) return false;
  const doc = activeDocId ? docs.get(activeDocId) : null;
  return isVisualMarkdownDoc(doc);
}

/**
 * Synchronize edits from Visual Markdown DOM back to `doc.content` and `#doc-editor-textarea`.
 * While the serialized visual content still equals the canonical form captured at
 * render time, the exact `doc._visualBaselineMarkdown` source string is preserved.
 * @param {HTMLElement|null} rich
 * @param {{ doc?: object|null }} [opts]
 * @returns {string}
 */
export function syncVisualMarkdownToDoc(rich, { doc = null } = {}) {
  const targetDoc = doc || (activeDocId ? docs.get(activeDocId) : null);
  const ta = typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null;
  if (!rich || !targetDoc || !isVisualMarkdownDoc(targetDoc) || !isVisualSurfaceBoundTo(rich, targetDoc)) {
    return targetDoc?.content || '';
  }

  const { markdown: serialized, lossless, reasons: fidelityReasons } = visualRichToMarkdownWithFidelity(rich);
  const unchanged = typeof targetDoc._visualBaselineMarkdown === 'string'
    && typeof targetDoc._visualBaselineCanonical === 'string'
    && serialized === targetDoc._visualBaselineCanonical;

  let nextMarkdown = targetDoc._visualBaselineMarkdown;

  if (unchanged) {
    nextMarkdown = targetDoc._visualBaselineMarkdown;
  } else {
    if (!lossless) {
      targetDoc._visualCompatReasons = fidelityReasons.length
        ? fidelityReasons
        : ['formatting the visual editor cannot represent in Markdown'];
      targetDoc._visualSyncBlocked = true;
      targetDoc._visualDraftHtml = rich.innerHTML;
      renderMarkdownSyncWarningBanner(targetDoc._visualCompatReasons);
      return targetDoc.content;
    }

    // Attempt localized block-level source preservation
    const originalSource = typeof targetDoc._visualBaselineMarkdown === 'string'
      ? targetDoc._visualBaselineMarkdown
      : (targetDoc.content || '');
    const currentBlocks = extractVisualBlocks(rich);
    const baselineBlocks = targetDoc._visualBaselineBlocks;
    const blockSegments = targetDoc._visualBlockSegments || extractBlockSegments(segmentMarkdown(originalSource));

    let localizedApplied = false;

    if (Array.isArray(baselineBlocks) && Array.isArray(blockSegments)) {
      const ops = diffBlockOperations(baselineBlocks, currentBlocks, blockSegments);
      if (ops && ops.length > 0) {
        const localizedResult = applyLocalizedEdits(originalSource, ops, {
          expectedRevision: targetDoc._visualExpectedRevision,
          render: markdownToVisualHtml,
          inspectCompatibility: inspectMarkdownVisualCompatibility,
          allowCanonicalFallback: true,
        });

        if (localizedResult.status === 'preserved' || localizedResult.status === 'canonical-fallback') {
          nextMarkdown = localizedResult.markdown;
          localizedApplied = true;
        }
      } else if (ops && ops.length === 0) {
        nextMarkdown = originalSource;
        localizedApplied = true;
      }
    }

    if (!localizedApplied) {
      const compat = assessMarkdownVisualSupport(serialized);
      if (!compat.supported) {
        targetDoc._visualCompatReasons = compat.reasons;
        targetDoc._visualSyncBlocked = true;
        targetDoc._visualDraftHtml = rich.innerHTML;
        renderMarkdownSyncWarningBanner(targetDoc._visualCompatReasons);
        return targetDoc.content;
      }
      nextMarkdown = serialized;
    }
  }

  targetDoc._visualCompatReasons = null;
  targetDoc._visualSyncBlocked = false;
  targetDoc._visualDraftHtml = null;
  hideMarkdownSyncWarningBanner();
  targetDoc.content = nextMarkdown;

  // Advance baseline for subsequent visual edits
  targetDoc._visualBaselineMarkdown = nextMarkdown;
  targetDoc._visualExpectedRevision = computeSourceRevision(nextMarkdown);
  targetDoc._visualSegments = segmentMarkdown(nextMarkdown);
  targetDoc._visualBlockSegments = extractBlockSegments(targetDoc._visualSegments);
  targetDoc._visualBaselineCanonical = serialized;
  targetDoc._visualBaselineBlocks = extractVisualBlocks(rich);

  if (ta && ta.value !== nextMarkdown) {
    _syncingVisualToTextarea = true;
    try {
      ta.value = nextMarkdown;
    } finally {
      _syncingVisualToTextarea = false;
    }
  }
  return nextMarkdown;
}

/**
 * Scrub rich-only formatting from already-sanitized paste HTML so pasted content in
 * Visual Markdown mode stays clean and round-trippable.
 * @param {string} cleanedHtml
 * @returns {string}
 */
export function cleanMarkdownPasteHtml(cleanedHtml) {
  const html = String(cleanedHtml || '');
  if (!html || typeof DOMParser === 'undefined') return html;
  const body = new DOMParser().parseFromString(html, 'text/html').body;
  if (!body) return html;

  // Unwrap inline tags that cannot round-trip cleanly into Markdown
  const unwrapTags = new Set(['U', 'SUP', 'SUB']);
  Array.from(body.querySelectorAll('*')).forEach(el => {
    const tag = String(el.tagName || '').toUpperCase();
    if (unwrapTags.has(tag)) {
      el.replaceWith(...Array.from(el.childNodes));
      return;
    }
    if (tag === 'SPAN') {
      const ff = String(el.style?.fontFamily || '');
      if (ff.includes('OdysseusInlineCode')) {
        const code = el.ownerDocument.createElement('code');
        code.textContent = el.textContent || '';
        el.replaceWith(code);
      } else {
        el.replaceWith(...Array.from(el.childNodes));
      }
      return;
    }
    if (tag === 'TD' || tag === 'TH') {
      el.removeAttribute('colspan');
      el.removeAttribute('rowspan');
    }
  });
  return body.innerHTML;
}

/**
 * Render the non-destructive compatibility notice explaining why Visual mode
 * is unavailable for a document that uses advanced or non-round-trippable syntax.
 * @param {string[]} reasons
 */
export function renderMarkdownCompatBanner(reasons = []) {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('doc-visual-compat-banner');
  if (!banner) return;
  const list = Array.isArray(reasons) ? reasons.filter(Boolean) : [];
  if (!list.length) {
    banner.style.display = 'none';
    banner.textContent = '';
    return;
  }
  const summary = list.slice(0, 3).join(', ') + (list.length > 3 ? `, and ${list.length - 3} more` : '');
  banner.textContent = '';

  const iconWrap = document.createElement('span');
  iconWrap.className = 'doc-visual-compat-icon';
  iconWrap.setAttribute('aria-hidden', 'true');
  iconWrap.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

  const msg = document.createElement('span');
  msg.className = 'doc-visual-compat-text';
  msg.textContent = `Opened in Markdown source mode to preserve ${summary}. Visual editing stays disabled for this document so source formatting is not altered.`;

  banner.appendChild(iconWrap);
  banner.appendChild(msg);
  banner.style.display = '';
}

export function hideMarkdownCompatBanner() {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('doc-visual-compat-banner');
  if (!banner) return;
  banner.style.display = 'none';
  banner.textContent = '';
}

export function renderMarkdownSyncWarningBanner(reasons = []) {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('doc-visual-compat-banner');
  if (!banner) return;
  const list = Array.isArray(reasons) ? reasons.filter(Boolean) : [];
  if (!list.length) {
    banner.style.display = 'none';
    banner.textContent = '';
    return;
  }
  const summary = list.slice(0, 3).join(', ') + (list.length > 3 ? `, and ${list.length - 3} more` : '');
  banner.textContent = '';

  const iconWrap = document.createElement('span');
  iconWrap.className = 'doc-visual-compat-icon';
  iconWrap.setAttribute('aria-hidden', 'true');
  iconWrap.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

  const msg = document.createElement('span');
  msg.className = 'doc-visual-compat-text';
  msg.textContent = `Visual formatting cannot be saved to Markdown (${summary}). Fix formatting in the visual editor, or discard visual changes to resume Markdown editing.`;

  const discardBtn = document.createElement('button');
  discardBtn.type = 'button';
  discardBtn.className = 'doc-visual-compat-discard-btn';
  discardBtn.dataset.action = 'discard-visual-draft';
  discardBtn.textContent = 'Discard visual changes';
  discardBtn.title = 'Discard un-syncable visual changes and restore last saved content';
  discardBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const doc = activeDocId ? docs.get(activeDocId) : null;
    if (doc) discardVisualDraft(doc);
  });

  banner.appendChild(iconWrap);
  banner.appendChild(msg);
  banner.appendChild(discardBtn);
  banner.style.display = '';
}

export function hideMarkdownSyncWarningBanner(force = false) {
  if (typeof document === 'undefined') return;
  const banner = document.getElementById('doc-visual-compat-banner');
  if (!banner) return;
  const doc = activeDocId ? docs.get(activeDocId) : null;
  if (!force && doc && !isMarkdownVisualModeActive() && Array.isArray(doc._visualCompatReasons) && doc._visualCompatReasons.length) return;
  banner.style.display = 'none';
  banner.textContent = '';
}

/**
 * Explicitly discard un-syncable visual changes, restore the document's baseline Markdown,
 * and clear the sync block so Markdown editing and saving can resume.
 * @param {object|null} doc
 */
export function discardVisualDraft(doc) {
  if (!doc) return;
  doc._visualSyncBlocked = false;
  doc._visualDraftHtml = null;
  doc._visualCompatReasons = null;
  doc._saveState = 'saved';
  hideMarkdownSyncWarningBanner(true);
  const rich = document.getElementById('doc-email-richbody');
  if (rich && isVisualSurfaceBoundTo(rich, doc)) {
    rich.innerHTML = markdownToVisualHtml(doc.content || '');
    captureVisualBaseline(rich, doc);
  }
  const ta = typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null;
  if (ta) ta.value = doc.content || '';
}

/**
 * Render Markdown source directly into `rich` when entering Visual Markdown mode
 * and bind the surface to `doc`. Call `captureVisualBaseline` after DOM normalizers.
 * @param {HTMLElement} rich
 * @param {object} doc
 */
export function renderMarkdownIntoVisualRich(rich, doc) {
  if (!rich || !doc) return;
  if (doc._visualSyncBlocked && typeof doc._visualDraftHtml === 'string') {
    rich.innerHTML = doc._visualDraftHtml;
    rich.dataset.visualDocId = String(doc.id);
    renderMarkdownSyncWarningBanner(doc._visualCompatReasons);
    return;
  }
  const rawMarkdown = String(doc.content || '');
  rich.innerHTML = markdownToVisualHtml(rawMarkdown);
  rich.dataset.visualDocId = String(doc.id);
  doc._visualBaselineMarkdown = rawMarkdown;
  doc._visualBaselineCanonical = null;
  doc._visualBaselineBlocks = null;
  doc._visualBlockSegments = null;
  doc._visualExpectedRevision = computeSourceRevision(rawMarkdown);
}

/**
 * Record the canonical serialization of the freshly rendered (and normalized) visual
 * DOM, then verify that adopting it would not rewrite the document.
 * @param {HTMLElement} rich
 * @param {object} doc
 * @returns {string} '' when the baseline is safe, otherwise the reason it is not
 */
export function captureVisualBaseline(rich, doc) {
  if (!rich || !doc) return '';
  const rawMarkdown = String(doc._visualBaselineMarkdown ?? doc.content ?? '');
  const segments = segmentMarkdown(rawMarkdown);
  const blockSegments = extractBlockSegments(segments);
  const canonical = visualRichToMarkdown(rich);
  const visualBlocks = extractVisualBlocks(rich);

  doc._visualBaselineCanonical = canonical;
  doc._visualExpectedRevision = computeSourceRevision(rawMarkdown);
  doc._visualSegments = segments;
  doc._visualBlockSegments = blockSegments;
  doc._visualBaselineBlocks = visualBlocks;

  return _rewriteReason(rawMarkdown, canonical);
}

/**
 * Identify which top-level block inside the visual editor contains the current caret,
 * along with its character offset within that block.
 * @param {HTMLElement} rich
 * @returns {{ blockIndex: number, offsetInBlock: number }|null}
 */
export function getVisualCaretBlockInfo(rich) {
  if (!rich || typeof window === 'undefined') return null;
  const selection = window.getSelection?.();
  if (!selection?.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!rich.contains(range.startContainer)) return null;

  let topNode = range.startContainer;
  while (topNode && topNode.parentNode !== rich) {
    topNode = topNode.parentNode;
  }
  if (!topNode) return null;

  const children = Array.from(rich.childNodes);
  let blockIndex = 0;
  let inInlineGroup = false;
  for (let i = 0; i < children.length; i++) {
    const child = children[i];
    const isBlock = child.nodeType === Node.ELEMENT_NODE &&
      /^(P|H[1-6]|UL|OL|BLOCKQUOTE|PRE|TABLE|HR|DIV)$/i.test(child.tagName);
    if (isBlock) {
      if (inInlineGroup) {
        blockIndex++;
        inInlineGroup = false;
      }
      if (child === topNode) break;
      blockIndex++;
    } else {
      inInlineGroup = true;
      if (child === topNode) break;
    }
  }

  let offsetInBlock = 0;
  try {
    const prefix = document.createRange();
    prefix.selectNodeContents(topNode);
    prefix.setEnd(range.startContainer, range.startOffset);
    offsetInBlock = prefix.toString().length;
  } catch (_) {}

  return { blockIndex, offsetInBlock };
}

/**
 * Restore the visual editor caret to the given top-level block index and offset.
 * @param {HTMLElement} rich
 * @param {number} blockIndex
 * @param {number} [offsetInBlock=0]
 * @returns {boolean}
 */
export function restoreVisualCaret(rich, blockIndex, offsetInBlock = 0) {
  if (!rich || !rich.childNodes.length || typeof window === 'undefined') return false;
  const children = Array.from(rich.childNodes);
  const blocks = [];
  let currentGroup = [];
  for (const child of children) {
    const isBlock = child.nodeType === Node.ELEMENT_NODE &&
      /^(P|H[1-6]|UL|OL|BLOCKQUOTE|PRE|TABLE|HR|DIV)$/i.test(child.tagName);
    if (isBlock) {
      if (currentGroup.length) {
        blocks.push(currentGroup[0]);
        currentGroup = [];
      }
      blocks.push(child);
    } else {
      currentGroup.push(child);
    }
  }
  if (currentGroup.length) {
    blocks.push(currentGroup[0]);
  }

  const targetBlock = blocks[Math.min(Math.max(0, blockIndex), blocks.length - 1)];
  if (!targetBlock) return false;

  try {
    rich.focus();
  } catch (_) {}

  const selection = window.getSelection?.();
  if (!selection) return false;

  const walker = document.createTreeWalker(targetBlock, NodeFilter.SHOW_TEXT);
  let remaining = Math.max(0, offsetInBlock);
  let textNode = walker.nextNode();
  let lastText = null;
  while (textNode) {
    lastText = textNode;
    const len = (textNode.nodeValue || '').length;
    if (remaining <= len) {
      const range = document.createRange();
      range.setStart(textNode, remaining);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      return true;
    }
    remaining -= len;
    textNode = walker.nextNode();
  }
  const range = document.createRange();
  if (lastText) {
    range.setStart(lastText, (lastText.nodeValue || '').length);
  } else {
    range.selectNodeContents(targetBlock);
  }
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}


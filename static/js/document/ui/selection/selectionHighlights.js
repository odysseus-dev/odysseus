// frontend/src/features/documents/ui/selection/selectionHighlights.js
/**
 * Editor and rich-text selection highlights.
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _isRichTextLang } from '../../domain/documentKinds.js';
import { _ensureDocPaneMounted, loadDocument } from '../documentFlows.js';
import { _emailRichbodyActive } from '../richEditor/richBodySync.js';
import { isMarkdownVisualModeActive } from '../richEditor/markdownVisualSync.js';
import { _scheduleDocumentStats } from '../status/documentStatus.js';

export let _selections = [];

export function replaceSelections(selections) {
  _selections = selections;
}

const _richSelectionHighlightName = 'doc-ai-selections';

function _richRootText(rich) {
    if (!rich) return '';
    const range = document.createRange();
    range.selectNodeContents(rich);
    return range.toString();
  }

function _richRangeFromOffsets(rich, start, end) {
    if (!rich || start < 0 || end <= start) return null;
    const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
    let offset = 0;
    let startNode = null, startOffset = 0, endNode = null, endOffset = 0;
    let node;
    while ((node = walker.nextNode())) {
      const length = (node.nodeValue || '').length;
      if (!startNode && start >= offset && start < offset + length) {
        startNode = node;
        startOffset = start - offset;
      }
      if (end > offset && end <= offset + length) {
        endNode = node;
        endOffset = end - offset;
        break;
      }
      offset += length;
    }
    if (!startNode || !endNode) return null;
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset);
    return range;
  }

export function updateRichSelectionState(rich) {
    if (!rich || !rich.isConnected || rich.style.display === 'none') return;
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    if (!rich.contains(range.commonAncestorContainer)) return;

    const selectedText = range.toString();
    if (!selectedText.trim()) return;
    const prefix = document.createRange();
    prefix.selectNodeContents(rich);
    prefix.setEnd(range.startContainer, range.startOffset);
    const start = prefix.toString().length;
    const end = start + selectedText.length;
    const fullText = _richRootText(rich);
    const startLine = fullText.slice(0, start).split('\n').length;
    const endLine = fullText.slice(0, end).split('\n').length;
    const docId = activeDocId;
    const entry = { kind: 'rich', text: selectedText, startLine, endLine, start, end, docId };
    const overlapIdx = _selections.findIndex(s => s.kind === 'rich' && (
      (start >= s.start && start <= s.end) || (end >= s.start && end <= s.end)
      || (start <= s.start && end >= s.end)
    ));
    if (overlapIdx >= 0) _selections[overlapIdx] = entry;
    else _selections.push(entry);
    showSelectionBadge();
    renderAllSelectionHighlights();
  }

let _selResizeScheduled = false;

export function _scheduleSelRerender() {
    if (_selResizeScheduled || _selections.length === 0) return;
    _selResizeScheduled = true;
    requestAnimationFrame(() => {
      _selResizeScheduled = false;
      try { renderAllSelectionHighlights(); } catch (_) {}
    });
  }

export let _selResizeObserver = null;

export function disposeSelectionHighlights() {
  _selResizeObserver?.disconnect();
  _selResizeObserver = null;
}

function _ensureSelResizeObserver() {
    if (_selResizeObserver || typeof ResizeObserver === 'undefined') return;
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    _selResizeObserver = new ResizeObserver(_scheduleSelRerender);
    _selResizeObserver.observe(ta);
  }

export function updateSelectionState() {
    // The mirror measurement below uses the textarea's live computed metrics,
    // so pinned selections remain valid with wrapped lines, larger font sizes,
    // mobile widths, and non-fullscreen panes. Older code disabled selection
    // whenever wrapping was detected; increasing the document font made that
    // path fire constantly, so selecting text appeared to stop working.
    _ensureSelResizeObserver();
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;

    if (start === end) {
      // Simple click — don't clear, user might be clicking into chat
      return;
    }

    const text = textarea.value;
    const selectedText = text.substring(start, end);
    const startLine = text.substring(0, start).split('\n').length;
    const endLine = text.substring(0, end).split('\n').length;

    // Check for overlap with existing selection — replace if overlapping
    const overlapIdx = _selections.findIndex(s =>
      (start >= s.start && start <= s.end) || (end >= s.start && end <= s.end) ||
      (start <= s.start && end >= s.end)
    );
    const entry = { text: selectedText, startLine, endLine, start, end };
    if (overlapIdx >= 0) {
      _selections[overlapIdx] = entry;
    } else {
      _selections.push(entry);
    }

    showSelectionBadge();
    renderAllSelectionHighlights();
  }

function showSelectionBadge() {
    let badge = document.getElementById('doc-selection-badge');
    if (isMarkdownVisualModeActive()) {
      if (badge) badge.style.display = 'none';
      return;
    }
    if (!badge) {
      badge = document.createElement('span');
      badge.id = 'doc-selection-badge';
      badge.className = 'doc-selection-badge';
      badge.title = 'Selected regions — type in chat to edit';
      // Sits directly under the formatting toolbar so it reads as part
      // of the toolbar row, not buried in the page header. Falls back
      // to the editor header if the toolbar isn't on screen.
      const toolbar = document.getElementById('doc-md-toolbar');
      if (toolbar && toolbar.parentNode) {
        toolbar.insertAdjacentElement('afterend', badge);
      } else {
        const header = document.querySelector('.doc-editor-header');
        if (header) header.insertBefore(badge, header.firstChild);
      }
    }
    if (_selections.length === 0) {
      badge.style.display = 'none';
      return;
    }
    const labels = _selections.map(s => s.kind === 'rich'
      ? 'Text'
      : (s.startLine === s.endLine ? `L${s.startLine}` : `L${s.startLine}-${s.endLine}`));
    badge.replaceChildren();
    if (_selections.length === 1) {
      badge.append(document.createTextNode(`${labels[0]} selected`));
    } else {
      badge.append(document.createTextNode(`${_selections.length} selections`));
      labels.forEach((selectionLabel, index) => {
        const chip = document.createElement('span');
        chip.className = 'doc-selection-chip';
        chip.textContent = selectionLabel;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'doc-selection-chip-clear';
        remove.title = `Remove ${selectionLabel} selection`;
        remove.setAttribute('aria-label', `Remove ${selectionLabel} selection`);
        remove.textContent = '×';
        remove.addEventListener('click', (e) => {
          e.stopPropagation();
          clearSelectionAt(index);
        });
        chip.appendChild(remove);
        badge.appendChild(chip);
      });
    }
    const clearAll = document.createElement('button');
    clearAll.className = 'doc-selection-clear doc-selection-chip-clear';
    clearAll.title = 'Clear Selection';
    clearAll.setAttribute('aria-label', 'Clear Selection');
    clearAll.textContent = 'Clear Selection';
    badge.appendChild(clearAll);
    badge.style.display = '';
    clearAll.addEventListener('click', (e) => {
      e.stopPropagation();
      clearSelection();
    });
  }

function _isCodeDoc() {
    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    if (!lang) return false;
    // Prose / preview types that should get character-precise highlights.
    const prose = new Set(['markdown', 'md', 'text', 'txt', 'email', 'html', 'csv']);
    return !prose.has(lang);
  }

export function _measurePos(mirror, text, pos) {
    mirror.innerHTML = '';
    if (pos > 0) mirror.appendChild(document.createTextNode(text.substring(0, pos)));
    const marker = document.createElement('span');
    marker.textContent = '​';
    mirror.appendChild(marker);
    const r = marker.getBoundingClientRect();
    const m = mirror.getBoundingClientRect();
    return { x: r.left - m.left, y: r.top - m.top };
  }

function _validateSelections(text) {
    if (_selections.length === 0) return;
    const rich = _emailRichbodyActive();
    const richText = rich ? _richRootText(rich) : '';
    const survivors = [];
    for (const s of _selections) {
      if (s.docId && activeDocId && s.docId !== activeDocId) continue;
      const captured = s.text || '';
      if (!captured) continue;
      const source = s.kind === 'rich' ? richText : text;
      if (s.kind === 'rich' && !rich) continue;
      // Fast path: still at the same offsets.
      if (source.substring(s.start, s.end) === captured) {
        survivors.push(s);
        continue;
      }
      // Re-anchor: find the captured text and pick the occurrence
      // nearest to the old start so multi-match docs don't snap to the
      // wrong one. indexOf scans are cheap for typical doc sizes.
      let best = -1, bestDist = Infinity;
      let from = 0;
      while (true) {
        const idx = source.indexOf(captured, from);
        if (idx === -1) break;
        const dist = Math.abs(idx - s.start);
        if (dist < bestDist) { best = idx; bestDist = dist; }
        from = idx + 1;
      }
      if (best === -1) continue;  // text gone entirely → drop
      const newStart = best;
      const newEnd = best + captured.length;
      survivors.push({
        ...s,
        start: newStart,
        end: newEnd,
        startLine: source.substring(0, newStart).split('\n').length,
        endLine: source.substring(0, newEnd).split('\n').length,
      });
    }
    _selections = survivors;
  }

function renderAllSelectionHighlights() {
    document.querySelectorAll('.doc-selection-rich-clear').forEach(el => el.remove());
    // Delete the persistent CSS highlight before checking whether any
    // selections remain; otherwise clearing the last selection leaves the
    // painted range visible until the next render.
    try { CSS.highlights?.delete(_richSelectionHighlightName); } catch (_) {}
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    // Remove old overlays
    wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || _selections.length === 0) return;

    const text = textarea.value;
    // Pre-render guard: re-anchor or drop selections whose text has
    // shifted (undo, programmatic edits, etc.) so the overlays never
    // draw on the wrong region.
    _validateSelections(text);
    const rich = _emailRichbodyActive();
    const richRanges = rich
      ? _selections.filter(s => s.kind === 'rich').map(s => _richRangeFromOffsets(rich, s.start, s.end)).filter(Boolean)
      : [];
    if (richRanges.length) {
      try {
        if (CSS.highlights && typeof Highlight === 'function') {
          CSS.highlights.set(_richSelectionHighlightName, new Highlight(...richRanges));
        }
      } catch (_) {}
      if (!isMarkdownVisualModeActive()) {
        const richSelections = _selections.filter(s => s.kind === 'rich');
        richRanges.forEach((range, rangeIndex) => {
          const rects = Array.from(range.getClientRects());
          const rect = rects[rects.length - 1];
          if (!rect || (!rect.width && !rect.height)) return;
          const clearBtn = document.createElement('button');
          clearBtn.type = 'button';
          clearBtn.className = 'doc-selection-rich-clear';
          clearBtn.title = 'Remove this selection';
          clearBtn.setAttribute('aria-label', 'Remove this selection');
          clearBtn.textContent = '×';
          clearBtn.style.left = `${Math.max(4, rect.right - 10)}px`;
          clearBtn.style.top = `${Math.max(4, rect.top - 6)}px`;
          const selectionIndex = _selections.indexOf(richSelections[rangeIndex]);
          clearBtn.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            clearSelectionAt(selectionIndex);
          });
          document.body.appendChild(clearBtn);
        });
      }
    }
    const sourceSelections = _selections.filter(s => s.kind !== 'rich');
    if (_selections.length === 0) { showSelectionBadge(); return; }
    if (sourceSelections.length === 0) return;
    const style = getComputedStyle(textarea);
    const paddingTop = parseFloat(style.paddingTop) || 10;
    const paddingLeft = parseFloat(style.paddingLeft) || 48;
    const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

    // Shared mirror for measurement — same box model as the textarea
    // so any measurement we take lines up 1:1 with the rendered text.
    let mirror = document.getElementById('doc-selection-mirror');
    if (!mirror) {
      mirror = document.createElement('div');
      mirror.id = 'doc-selection-mirror';
      // box-sizing:border-box is critical — without it the mirror's
      // actual box width = (width prop) + horizontal padding, which is
      // wider than the textarea's text-render area. Text wraps at a
      // different column inside the mirror, so every measured y-offset
      // drifts from where the real text sits. border-box makes
      // mirror.box = textarea.clientWidth exactly.
      mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;' +
        'white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;box-sizing:border-box;';
      wrap.appendChild(mirror);
    }
    mirror.style.font = style.font;
    mirror.style.padding = style.padding;
    mirror.style.borderWidth = style.borderWidth;
    mirror.style.borderStyle = 'solid';
    mirror.style.borderColor = 'transparent';
    mirror.style.width = textarea.clientWidth + 'px';
    mirror.style.tabSize = style.tabSize;
    mirror.style.letterSpacing = style.letterSpacing;
    mirror.style.wordSpacing = style.wordSpacing;
    mirror.style.textIndent = style.textIndent;

    const codeDoc = _isCodeDoc();
    const scrollTop = textarea.scrollTop;

    for (const sel of sourceSelections) {
      const selectionIndex = _selections.indexOf(sel);
      if (codeDoc) {
        // Line-based: span every line that contains any selected char.
        const beforeStart = text.substring(0, sel.start);
        const lastNewline = beforeStart.lastIndexOf('\n');
        const startLineBegin = lastNewline + 1;
        mirror.textContent = text.substring(0, startLineBegin);
        const startTop = mirror.scrollHeight - paddingTop;

        const afterEnd = text.indexOf('\n', sel.end);
        const endLineEnd = afterEnd === -1 ? text.length : afterEnd;
        mirror.textContent = text.substring(0, endLineEnd);
        const endBottom = mirror.scrollHeight - paddingTop;

        mirror.textContent = '';

        const top = paddingTop + startTop - scrollTop;
        const height = endBottom - startTop || lineHeight;
        const overlay = document.createElement('div');
        overlay.className = 'doc-selection-overlay';
        overlay.style.top = top + 'px';
        overlay.style.left = paddingLeft + 'px';
        overlay.style.right = '0';
        overlay.style.height = height + 'px';
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'doc-selection-overlay-clear';
        clearBtn.title = 'Remove this selection';
        clearBtn.setAttribute('aria-label', 'Remove this selection');
        clearBtn.textContent = '×';
        clearBtn.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          clearSelectionAt(selectionIndex);
        });
        overlay.appendChild(clearBtn);
        wrap.appendChild(overlay);
      } else {
        // Character-precise: measure the actual selection start/end via
        // a marker span. Render one rect for single-line selections, or
        // three rects (first partial, middle full, last partial) for
        // multi-line selections.
        const startPos = _measurePos(mirror, text, sel.start);
        const endPos = _measurePos(mirror, text, sel.end);
        mirror.innerHTML = '';

        const addRect = (top, left, width, height, withClear = false) => {
          const overlay = document.createElement('div');
          overlay.className = 'doc-selection-overlay';
          overlay.style.top = (paddingTop + top - scrollTop) + 'px';
          overlay.style.left = (paddingLeft + left) + 'px';
          if (width != null) overlay.style.width = width + 'px';
          else overlay.style.right = '0';
          overlay.style.height = height + 'px';
          if (withClear) {
            const clearBtn = document.createElement('button');
            clearBtn.type = 'button';
            clearBtn.className = 'doc-selection-overlay-clear';
            clearBtn.title = 'Remove this selection';
            clearBtn.setAttribute('aria-label', 'Remove this selection');
            clearBtn.textContent = '×';
            clearBtn.addEventListener('click', (event) => {
              event.preventDefault();
              event.stopPropagation();
              clearSelectionAt(selectionIndex);
            });
            overlay.appendChild(clearBtn);
          }
          wrap.appendChild(overlay);
        };

        if (Math.abs(endPos.y - startPos.y) < 1) {
          // Single visual line.
          addRect(startPos.y, startPos.x, endPos.x - startPos.x, lineHeight, true);
        } else {
          // First line: from selection start to right edge.
          addRect(startPos.y, startPos.x, null, lineHeight, true);
          // Middle lines (if any): full-width band between the two.
          const middleTop = startPos.y + lineHeight;
          const middleHeight = endPos.y - middleTop;
          if (middleHeight > 0) addRect(middleTop, 0, null, middleHeight);
          // Last line: from left edge to selection end.
          addRect(endPos.y, 0, endPos.x, lineHeight);
        }
      }
    }
  }

export function syncSelectionOverlay() {
    if (_selections.length === 0) return;
    renderAllSelectionHighlights();
  }

export function clearSelection({ preserveCaret = false } = {}) {
    _selections = [];
    try { CSS.highlights?.delete(_richSelectionHighlightName); } catch (_) {}
    document.querySelectorAll('.doc-selection-rich-clear').forEach(el => el.remove());
    // A restored rich-text reference also creates a native browser range so
    // the referenced text is visibly selected. Clear that range with the
    // pinned selection; otherwise document stats keep reporting "selected"
    // after the badge's X has cleared the actual AI-edit context.
    const rich = _emailRichbodyActive();
    const browserSelection = window.getSelection?.();
    // Input already placed the caret after the edit. Clearing pinned AI
    // context must not discard that live insertion point.
    if (!preserveCaret && rich && browserSelection?.rangeCount
        && (rich.contains(browserSelection.anchorNode) || rich.contains(browserSelection.focusNode))) {
      browserSelection.removeAllRanges();
    }
    const badge = document.getElementById('doc-selection-badge');
    if (badge) badge.style.display = 'none';
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());
    _scheduleDocumentStats();
  }

function clearSelectionAt(index) {
    if (index < 0 || index >= _selections.length) return;
    const removed = _selections[index];
    _selections.splice(index, 1);
    if (removed?.kind === 'rich') {
      const rich = _emailRichbodyActive();
      const browserSelection = window.getSelection?.();
      if (rich && browserSelection?.rangeCount
          && (rich.contains(browserSelection.anchorNode) || rich.contains(browserSelection.focusNode))) {
        browserSelection.removeAllRanges();
      }
    }
    renderAllSelectionHighlights();
    showSelectionBadge();
    _scheduleDocumentStats();
  }

export function getSelectionContext() {
    // Markdown Visual mode reads the live selection on demand. Other editors keep
    // sending only selections they recorded, so a selection the user cleared is
    // never resurrected from the surviving browser highlight.
    if (_selections.length === 0 && isMarkdownVisualModeActive()) {
      const rich = _emailRichbodyActive();
      if (rich) updateRichSelectionState(rich);
    }
    if (_selections.length === 0) return null;
    // Re-anchor / drop stale selections before handing them to chat —
    // shipping text from a stale offset would mean the AI sees content
    // from a different region than what the user thinks they highlighted.
    const _ta = document.getElementById('doc-editor-textarea');
    if (_ta) _validateSelections(_ta.value);
    if (_selections.length === 0) return null;
    if (_selections.length === 1) {
      const ctx = _selections[0];
      clearSelection();
      return ctx;
    }
    // Multiple selections — return array
    const ctx = [..._selections];
    clearSelection();
    return ctx;
  }

export async function restoreSelectionReference(reference, options = {}) {
    const requestedDocId = String(options.documentId || '').trim();
    if (requestedDocId) await loadDocument(requestedDocId);
    if (!activeDocId || !docs.has(activeDocId)) return false;

    _ensureDocPaneMounted();
    const doc = docs.get(activeDocId);
    const rich = (_isRichTextLang(doc.language) || isMarkdownVisualModeActive()) ? _emailRichbodyActive() : null;
    const textarea = document.getElementById('doc-editor-textarea');
    const source = rich ? _richRootText(rich) : (textarea?.value || doc.content || '');
    const supplied = Array.isArray(options.selections)
      ? options.selections
      : (options.selections ? [options.selections] : []);
    const restored = [];

    for (const selection of supplied) {
      const selectedText = String(selection?.text || '');
      if (!selectedText) continue;
      const start = source.indexOf(selectedText);
      if (start < 0) continue;
      const end = start + selectedText.length;
      restored.push({
        kind: rich ? 'rich' : undefined,
        text: selectedText,
        start,
        end,
        startLine: source.slice(0, start).split('\n').length,
        endLine: source.slice(0, end).split('\n').length,
      });
    }

    if (!restored.length) {
      const lineMatches = Array.from(String(reference || '').matchAll(/(?:L|lines?)\s*(\d+)(?:\s*[-–]\s*(\d+))?/gi));
      const lines = source.split('\n');
      const lineStart = lineNumber => {
        let offset = 0;
        for (let index = 1; index < lineNumber && index <= lines.length; index++) {
          offset += lines[index - 1].length + 1;
        }
        return offset;
      };
      for (const match of lineMatches) {
        const startLine = Math.max(1, Math.min(lines.length, Number(match[1]) || 1));
        const endLine = Math.max(startLine, Math.min(lines.length, Number(match[2]) || startLine));
        const start = lineStart(startLine);
        const end = lineStart(endLine) + (lines[endLine - 1] || '').length;
        if (end <= start) continue;
        restored.push({
          kind: rich ? 'rich' : undefined,
          text: source.slice(start, end),
          start,
          end,
          startLine,
          endLine,
        });
      }
    }
    if (!restored.length) return false;

    _selections = restored;
    showSelectionBadge();
    renderAllSelectionHighlights();
    const first = restored[0];
    if (rich) {
      const range = _richRangeFromOffsets(rich, first.start, first.end);
      const browserSelection = window.getSelection?.();
      if (range && browserSelection) {
        browserSelection.removeAllRanges();
        browserSelection.addRange(range);
        range.startContainer.parentElement?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
      }
      rich.focus({ preventScroll: true });
    } else if (textarea) {
      textarea.focus({ preventScroll: true });
      textarea.setSelectionRange(first.start, first.end, 'forward');
      const lineHeight = parseFloat(getComputedStyle(textarea).lineHeight) || 18;
      textarea.scrollTop = Math.max(0, (first.startLine - 2) * lineHeight);
      syncSelectionOverlay();
    }
    return true;
  }

let _disposeSelectionResizeListener = null;

function installSelectionResizeListener() {
  if (typeof window === 'undefined') return () => {};
  if (_disposeSelectionResizeListener) return _disposeSelectionResizeListener;
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', _scheduleSelRerender);
  }
  let disposed = false;
  _disposeSelectionResizeListener = () => {
    if (disposed) return;
    disposed = true;
    window.removeEventListener('resize', _scheduleSelRerender);
    _disposeSelectionResizeListener = null;
  };
  return _disposeSelectionResizeListener;
}

installSelectionResizeListener();

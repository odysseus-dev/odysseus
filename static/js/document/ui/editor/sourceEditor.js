// frontend/src/features/documents/ui/editor/sourceEditor.js
/**
 * Source Editor Subsystem.
 * Handles source-editing behavior, Markdown editing, syntax highlighting,
 * line-number rendering, and language auto-detection.
 */

import { activeDocId, docs } from '../../application/documentState.js';
import { _syncHeaderActions } from '../../application/documentCommands.js';
import {
  AUTO_DETECT_MIN_CHARS,
  AUTO_DETECT_SAMPLE_SIZE,
  AUTO_DETECT_MIN_RELEVANCE,
  AUTO_DETECT_DELAY,
  HLJS_TO_DROPDOWN,
  _looksLikeMarkdown
} from '../../domain/documentKinds.js';
import { applyFindMarks } from '../findReplace.js';
import { autoTitleFromContent, updateLanguage } from '../documentActions.js';
import {
  _isMarkdownPreviewVisible,
  _setMarkdownPreviewActive
} from '../previewModes.js';
import { uiModule } from '../host.js';

export let _hlDebounce = null;

export let _autoDetectDebounce = null;

export let _autoTitleDebounce = null;

let _mdPreviewClickTimes = [];

let _mdPreviewHintLastAt = 0;

export function _replaceRange(ta, from, to, text) {
    ta.focus();
    ta.selectionStart = from;
    ta.selectionEnd = to;
    const before = ta.value;
    let ok = false;
    try { ok = document.execCommand('insertText', false, text); } catch (_) { ok = false; }
    // execCommand('insertText') keeps native undo working. It silently no-ops on
    // some mobile browsers though — so ONLY when it changed nothing do we splice
    // the value directly (using the pre-edit value + original range, so we never
    // double-insert). execCommand fires its own input event; the splice path
    // dispatches one manually.
    if (!ok && ta.value === before) {
      ta.value = before.slice(0, from) + text + before.slice(to);
      ta.selectionStart = ta.selectionEnd = from + text.length;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

function _postProcessMarkdown(codeEl) {
    const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    for (const node of textNodes) {
      const text = node.textContent;
      // Skip nodes already inside hljs spans (like [link text] which is .hljs-string)
      if (node.parentElement !== codeEl && node.parentElement.className &&
          /hljs-(string|link|code|section)/.test(node.parentElement.className)) continue;
      // Match standalone [bracketed text] not followed by (url)
      if (/\[[^\]]+\](?!\()/.test(text)) {
        const frag = document.createDocumentFragment();
        let last = 0;
        const re = /\[([^\]]+)\](?!\()/g;
        let m;
        while ((m = re.exec(text)) !== null) {
          if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
          const span = document.createElement('span');
          span.className = 'md-bracket';
          span.textContent = m[0];
          frag.appendChild(span);
          last = re.lastIndex;
        }
        if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
        if (last > 0) node.parentNode.replaceChild(frag, node);
      }
    }
    // Colorize heading # markers inside .hljs-section spans
    codeEl.querySelectorAll('.hljs-section').forEach(span => {
      const text = span.textContent;
      const hashMatch = text.match(/^(#{1,6})\s/);
      if (hashMatch) {
        const marker = document.createElement('span');
        marker.className = 'md-heading-marker';
        marker.textContent = hashMatch[1] + ' ';
        span.textContent = text.slice(hashMatch[0].length);
        span.prepend(marker);
      }
    });
  }

export function syncHighlighting() {
    const textarea = document.getElementById('doc-editor-textarea');
    const codeEl = document.getElementById('doc-editor-code');
    const pre = document.getElementById('doc-editor-highlight');
    if (!textarea || !codeEl) return;

    // Don't overwrite inline diff markers
    if (codeEl.dataset.hasDiff) return;

    const text = textarea.value;
    // Trailing newline prevents scroll mismatch on last line
    codeEl.textContent = text + '\n';

    const lang = document.getElementById('doc-language-select')?.value;
    // hljs has no 'svg' grammar — highlight it as xml (the dropdown value stays
    // 'svg' so the preview/run routing still treats it as renderable markup).
    const _hlLang = lang === 'svg' ? 'xml' : lang;
    const syntaxEnabled = !!(window.hljs?.getLanguage(_hlLang || '')
      && !['markdown', 'text', 'plaintext', 'email', 'richtext'].includes(lang));
    document.getElementById('doc-editor-wrap')?.classList.toggle('doc-code-syntax', syntaxEnabled);
    textarea.wrap = syntaxEnabled ? 'off' : 'soft';
    codeEl.className = _hlLang ? `language-${_hlLang}` : '';
    if (window.hljs && _hlLang) {
      codeEl.removeAttribute('data-highlighted');
      window.hljs.highlightElement(codeEl);
    }
    // Markdown post-processing: colorize standalone [brackets] and heading markers
    if (lang === 'markdown') {
      _postProcessMarkdown(codeEl);
    }

    // Reapply find highlights after hljs rewrote the DOM
    if (codeEl.dataset.findQuery) applyFindMarks(codeEl);

    // Keep scroll in sync
    if (pre) {
      codeEl.style.minHeight = textarea.scrollHeight + 'px';
      pre.scrollTop = textarea.scrollTop;
      pre.scrollLeft = textarea.scrollLeft;
    }

    // Update line numbers
    updateLineNumbers(text);
  }

export let _lineNumberResizeObserver = null;

export let _lineNumberObservedTextarea = null;

export let _lineNumberResizeRaf = null;

export function _lineNumberContentEl(gutter) {
    let inner = gutter.querySelector('.doc-line-number-content');
    if (!inner) {
      inner = document.createElement('div');
      inner.className = 'doc-line-number-content';
      gutter.textContent = '';
      gutter.appendChild(inner);
    }
    return inner;
  }

function _lineNumberStyleSignature(style) {
    return [
      style.fontFamily,
      style.fontSize,
      style.fontWeight,
      style.fontStyle,
      style.lineHeight,
      style.letterSpacing,
      style.tabSize,
      style.fontFeatureSettings,
      style.fontVariantLigatures,
      style.fontKerning,
    ].join('|');
  }

function _textareaTextWidth(textarea, style) {
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    const paddingRight = parseFloat(style.paddingRight) || 0;
    return Math.max(0, textarea.clientWidth - paddingLeft - paddingRight);
  }

function _lineHeightPx(style) {
    const parsed = parseFloat(style.lineHeight);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
    const fontSize = parseFloat(style.fontSize) || 11;
    return fontSize * 1.45;
  }

function _lineNumberMeasureEl(textarea) {
    const wrap = document.getElementById('doc-editor-wrap') || textarea.parentElement || document.body;
    let probe = wrap.querySelector('.doc-line-number-measure');
    if (!probe) {
      probe = document.createElement('textarea');
      probe.className = 'doc-line-number-measure';
      probe.setAttribute('aria-hidden', 'true');
      probe.tabIndex = -1;
      probe.readOnly = true;
      probe.wrap = 'soft';
      wrap.appendChild(probe);
    }
    return probe;
  }

function _syncLineNumberMeasureStyle(probe, style, textWidth) {
    probe.style.width = textWidth + 'px';
    probe.style.fontFamily = style.fontFamily;
    probe.style.fontSize = style.fontSize;
    probe.style.fontWeight = style.fontWeight;
    probe.style.fontStyle = style.fontStyle;
    probe.style.lineHeight = style.lineHeight;
    probe.style.letterSpacing = style.letterSpacing;
    probe.style.tabSize = style.tabSize;
    probe.style.fontFeatureSettings = style.fontFeatureSettings;
    probe.style.fontVariantLigatures = style.fontVariantLigatures;
    probe.style.fontKerning = style.fontKerning;
    probe.style.textRendering = style.textRendering;
    probe.style.whiteSpace = style.whiteSpace;
    probe.style.wordWrap = style.wordWrap;
    probe.style.overflowWrap = style.overflowWrap;
  }

function _measureLineNumberHeights(textarea, lines, textWidth, style) {
    const probe = _lineNumberMeasureEl(textarea);
    _syncLineNumberMeasureStyle(probe, style, textWidth);
    const lineHeight = _lineHeightPx(style);
    return lines.map(line => {
      probe.value = line || ' ';
      const visualRows = Math.max(1, Math.round(probe.scrollHeight / lineHeight));
      return visualRows * lineHeight;
    });
  }

function _renderLineNumberRows(inner, heights) {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < heights.length; i++) {
      const row = document.createElement('div');
      row.className = 'doc-line-number-row';
      row.style.height = `${heights[i]}px`;

      const label = document.createElement('span');
      label.className = 'doc-line-number-label';
      label.textContent = String(i + 1);
      row.appendChild(label);
      frag.appendChild(row);
    }
    inner.textContent = '';
    inner.appendChild(frag);
  }

function _scheduleLineNumberRerender() {
    if (_lineNumberResizeRaf) return;
    const run = () => {
      _lineNumberResizeRaf = null;
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) updateLineNumbers(textarea.value, true);
    };
    if (typeof requestAnimationFrame === 'function') {
      _lineNumberResizeRaf = requestAnimationFrame(run);
    } else {
      run();
    }
  }

function _ensureLineNumberResizeObserver(textarea) {
    if (typeof ResizeObserver === 'undefined') return;
    if (!_lineNumberResizeObserver) {
      _lineNumberResizeObserver = new ResizeObserver(_scheduleLineNumberRerender);
    }
    if (_lineNumberObservedTextarea === textarea) return;
    if (_lineNumberObservedTextarea) {
      _lineNumberResizeObserver.unobserve(_lineNumberObservedTextarea);
    }
    _lineNumberObservedTextarea = textarea;
    _lineNumberResizeObserver.observe(textarea);
  }

function installLineNumberResizeListener() {
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', _scheduleLineNumberRerender);
  }
}
installLineNumberResizeListener();

export function updateLineNumbers(text, force = false) {
    const textarea = document.getElementById('doc-editor-textarea');
    const gutter = document.getElementById('doc-line-numbers');
    if (!textarea || !gutter) return;

    const value = text || '';
    const lines = value.split('\n');
    const inner = _lineNumberContentEl(gutter);
    const style = getComputedStyle(textarea);
    const textWidth = _textareaTextWidth(textarea, style);
    const styleSig = _lineNumberStyleSignature(style);

    _ensureLineNumberResizeObserver(textarea);
    if (
      !force &&
      inner._lineNumberText === value &&
      inner._lineNumberWidth === textWidth &&
      inner._lineNumberStyleSig === styleSig
    ) {
      syncGutterScroll();
      return;
    }

    const heights = _measureLineNumberHeights(textarea, lines, textWidth, style);
    _renderLineNumberRows(inner, heights);
    inner._lineNumberText = value;
    inner._lineNumberWidth = textWidth;
    inner._lineNumberStyleSig = styleSig;
    syncGutterScroll();
  }

export function syncGutterScroll() {
    const textarea = document.getElementById('doc-editor-textarea');
    const gutter = document.getElementById('doc-line-numbers');
    if (textarea && gutter) {
      _lineNumberContentEl(gutter).style.transform = `translateY(${-textarea.scrollTop}px)`;
    }
  }

export function attemptAutoDetect() {
    if (!window.hljs || !activeDocId) return;
    const doc = docs.get(activeDocId);
    if (!doc || doc.userSetLanguage) return;

    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    const text = textarea.value;
    if (text.length < AUTO_DETECT_MIN_CHARS) return;

    // SVG heuristic — a standalone <svg> root (optionally after an XML decl /
    // doctype). hljs would tag this generic "xml"; we want it labeled svg so it
    // routes to the preview iframe with a correct type.
    if (/^\s*(<\?xml[^>]*>\s*)?(<!doctype[^>]*>\s*)?<svg[\s>]/i.test(text)) {
      const langSelect = document.getElementById('doc-language-select');
      if (langSelect && langSelect.value !== 'svg') {
        langSelect.value = 'svg';
        doc.language = 'svg';
        updateLanguage();
        syncHighlighting();
        _syncHeaderActions();
      }
      return;
    }

    // Markdown heuristic first — hljs often fails to detect it
    if (_looksLikeMarkdown(text)) {
      const langSelect = document.getElementById('doc-language-select');
      if (langSelect && langSelect.value !== 'markdown') {
        langSelect.value = 'markdown';
        doc.language = 'markdown';
        updateLanguage();
        syncHighlighting();
        _syncHeaderActions();
        const mdToolbar = document.getElementById('doc-md-toolbar');
        if (mdToolbar) { mdToolbar.style.display = ''; if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow); }
      }
      return;
    }

    const sample = text.slice(0, AUTO_DETECT_SAMPLE_SIZE);
    const result = window.hljs.highlightAuto(sample);

    if (!result.language || result.relevance < AUTO_DETECT_MIN_RELEVANCE) return;

    const mapped = HLJS_TO_DROPDOWN[result.language];
    if (!mapped) return;

    const langSelect = document.getElementById('doc-language-select');
    if (!langSelect || langSelect.value === mapped) return;

    langSelect.value = mapped;
    doc.language = mapped;
    updateLanguage();
    syncHighlighting();
    _syncHeaderActions();

    const mdToolbar2 = document.getElementById('doc-md-toolbar');
    if (mdToolbar2) mdToolbar2.style.display = (mapped === 'markdown') ? '' : 'none';
  }

export function _handleMarkdownPreviewClickHint() {
    if (!_isMarkdownPreviewVisible()) return;
    const lang = ((docs.get(activeDocId)?.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
    if (lang !== 'markdown') return;

    const now = Date.now();
    _mdPreviewClickTimes = _mdPreviewClickTimes.filter(ts => now - ts < 2500);
    _mdPreviewClickTimes.push(now);
    if (_mdPreviewClickTimes.length < 3 || now - _mdPreviewHintLastAt < 5000) return;

    _mdPreviewHintLastAt = now;
    _mdPreviewClickTimes = [];
    if (uiModule?.showToast) {
      uiModule.showToast('Preview is read-only. Click Write to edit the document.', {
        duration: 5000,
        action: 'Write',
        onAction: () => _setMarkdownPreviewActive(false, { remember: true }),
      });
    }
  }

export function _refreshMarkdownPreviewIfVisible(docId, content) {
    if (!_isMarkdownPreviewVisible()) return false;
    const doc = docs.get(docId);
    const lang = ((doc && doc.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
    if (lang !== 'markdown') return false;
    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea) textarea.value = content;
    syncHighlighting();
    _setMarkdownPreviewActive(true, { remember: false });
    return true;
  }

export function disposeSourceEditor(pane) {
  clearTimeout(_hlDebounce);
  clearTimeout(_autoDetectDebounce);
  clearTimeout(_autoTitleDebounce);
  _hlDebounce = null;
  _autoDetectDebounce = null;
  _autoTitleDebounce = null;
  if (pane?.contains(_lineNumberObservedTextarea)) {
    _lineNumberResizeObserver?.disconnect();
    _lineNumberResizeObserver = null;
    _lineNumberObservedTextarea = null;
  }
  if (_lineNumberResizeRaf) cancelAnimationFrame(_lineNumberResizeRaf);
  _lineNumberResizeRaf = null;
}

export function scheduleAutoTitle(value, docId = activeDocId, delay = 600) {
  const doc = docId ? docs.get(docId) : null;
  clearTimeout(_autoTitleDebounce);
  _autoTitleDebounce = null;
  if (!docId) return;
  if (doc && doc.title && doc.title !== 'Untitled') return;
  _autoTitleDebounce = setTimeout(() => {
    _autoTitleDebounce = null;
    if (docId && docs.get(docId) === doc) autoTitleFromContent(value, docId);
  }, delay);
}

export function scheduleSourceEditorWork(value, docId = activeDocId) {
  const pane = document.getElementById('doc-editor-pane');
  const doc = docs.get(docId);
  const ownsPane = () => pane?.isConnected && pane.dataset.activeDocumentId === docId && activeDocId === docId;
  clearTimeout(_hlDebounce);
  _hlDebounce = setTimeout(() => {
    _hlDebounce = null;
    if (ownsPane()) syncHighlighting();
  }, 80);
  clearTimeout(_autoDetectDebounce);
  _autoDetectDebounce = setTimeout(() => {
    _autoDetectDebounce = null;
    if (ownsPane()) attemptAutoDetect();
  }, AUTO_DETECT_DELAY);
  scheduleAutoTitle(value, docId, 600);
}

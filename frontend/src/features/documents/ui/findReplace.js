// static/js/document/findReplace.js
/**
 * In-document find and replace controller.
 * Extracted from document.js for modularity and high performance.
 */

// In-document find (Ctrl+F) state
let _findMatches = [];
let _findMatchRanges = [];
let _findMatchQuery = '';
let _findIdx = -1;
let _richFindRanges = [];
let _richFindFallbackSelection = false;
const _richFindAllName = 'doc-find-results';
const _richFindCurrentName = 'doc-find-current';

let _deps = {};

function _getTextarea() {
  return (_deps.getTextarea && _deps.getTextarea()) || document.getElementById('doc-editor-textarea');
}

function _getWrap() {
  return (_deps.getWrap && _deps.getWrap()) || document.getElementById('doc-editor-wrap');
}

function _getCodeEl() {
  return (_deps.getCodeEl && _deps.getCodeEl()) || document.getElementById('doc-editor-code');
}

function _emailRichbodyActive() {
  if (_deps.isRichActive) return _deps.isRichActive();
  const r = document.getElementById('doc-email-richbody');
  return r && r.style.display !== 'none' ? r : null;
}

function _syncEmailRichbody(rich) {
  if (_deps.syncEmailRichbody) {
    _deps.syncEmailRichbody(rich);
  }
}

function _scheduleEmailRichbodySave() {
  if (_deps.scheduleEmailRichbodySave) {
    _deps.scheduleEmailRichbodySave();
  }
}

function _replaceRange(ta, from, to, text) {
  if (_deps.replaceRange) {
    _deps.replaceRange(ta, from, to, text);
    return;
  }
  ta.focus();
  ta.selectionStart = from;
  ta.selectionEnd = to;
  const before = ta.value;
  let ok = false;
  try { ok = document.execCommand('insertText', false, text); } catch (_) { ok = false; }
  if (!ok && ta.value === before) {
    ta.value = before.slice(0, from) + text + before.slice(to);
    ta.selectionStart = ta.selectionEnd = from + text.length;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

export function _clearRichFindHighlights() {
  try {
    CSS.highlights?.delete(_richFindAllName);
    CSS.highlights?.delete(_richFindCurrentName);
  } catch (_) {}
  if (_richFindFallbackSelection) {
    try { window.getSelection()?.removeAllRanges(); } catch (_) {}
    _richFindFallbackSelection = false;
  }
  _richFindRanges = [];
}

export function _buildRichFindRanges(rich, query) {
  const nodes = [];
  const walker = document.createTreeWalker(rich, NodeFilter.SHOW_TEXT);
  const blockSelector = 'p,div,h1,h2,h3,h4,h5,h6,li,blockquote,pre,td,th';
  let fullText = '';
  let node;
  let previousNode = null;
  let previousBlock = null;
  while ((node = walker.nextNode())) {
    const value = node.nodeValue || '';
    if (!value) continue;
    const closestBlock = node.parentElement?.closest?.(blockSelector);
    const block = closestBlock && rich.contains(closestBlock) ? closestBlock : rich;
    let startsNewSegment = !!nodes.length && block !== previousBlock;
    if (!startsNewSegment && previousNode) {
      try {
        const between = document.createRange();
        between.setStartAfter(previousNode);
        between.setEndBefore(node);
        startsNewSegment = !!between.cloneContents().querySelector?.('br');
      } catch (_) {}
    }
    if (startsNewSegment) fullText += '\n';
    nodes.push({ node, start: fullText.length, end: fullText.length + value.length });
    fullText += value;
    previousNode = node;
    previousBlock = block;
  }
  if (!query || !fullText) return [];

  const lowerText = fullText.toLocaleLowerCase();
  const lowerQuery = query.toLocaleLowerCase();
  const ranges = [];
  let from = 0;
  while (from <= lowerText.length - lowerQuery.length) {
    const hit = lowerText.indexOf(lowerQuery, from);
    if (hit < 0) break;
    const end = hit + query.length;
    const startPart = nodes.find(part => hit >= part.start && hit < part.end);
    const endPart = nodes.find(part => end > part.start && end <= part.end);
    if (startPart && endPart) {
      const range = document.createRange();
      range.setStart(startPart.node, hit - startPart.start);
      range.setEnd(endPart.node, end - endPart.start);
      ranges.push(range);
    }
    from = hit + Math.max(1, query.length);
  }
  return ranges;
}

export function _renderRichFindRanges(rich, ranges, currentIdx) {
  _clearRichFindHighlights();
  _richFindRanges = ranges;
  if (!ranges.length) return;
  let painted = false;
  try {
    if (CSS.highlights && typeof Highlight === 'function') {
      CSS.highlights.set(_richFindAllName, new Highlight(...ranges));
      if (ranges[currentIdx]) {
        CSS.highlights.set(_richFindCurrentName, new Highlight(ranges[currentIdx]));
      }
      painted = true;
    }
  } catch (_) {}

  const current = ranges[currentIdx];
  if (!current) return;
  if (!painted) {
    try {
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(current);
      _richFindFallbackSelection = true;
    } catch (_) {}
  }
  const rect = current.getBoundingClientRect();
  const hostRect = rich.getBoundingClientRect();
  if (rect.top < hostRect.top + 24 || rect.bottom > hostRect.bottom - 24) {
    rich.scrollTop += rect.top - hostRect.top - (hostRect.height / 2) + (rect.height / 2);
  }
}

export function _setReplaceVisible(visible, focus = false) {
  const bar = document.getElementById('doc-find-bar');
  const row = document.getElementById('doc-replace-row');
  const toggle = document.getElementById('doc-find-replace-toggle');
  if (!bar || !row || !toggle) return;
  row.hidden = !visible;
  bar.classList.toggle('replace-open', visible);
  toggle.classList.toggle('is-active', visible);
  toggle.setAttribute('aria-expanded', visible ? 'true' : 'false');
  toggle.setAttribute('aria-label', visible ? 'Hide replace' : 'Show replace');
  toggle.title = visible ? 'Hide replace' : 'Show replace';
  if (visible && focus) {
    const replacement = document.getElementById('doc-replace-input');
    replacement?.focus();
    replacement?.select();
  }
}

export function _openFindBar(showReplace = false) {
  const bar = document.getElementById('doc-find-bar');
  if (!bar) return;
  bar.style.display = 'flex';
  _setReplaceVisible(showReplace);
  document.body.classList.add('doc-find-active');
  const inp = document.getElementById('doc-find-input');
  if (inp) {
    const rich = _emailRichbodyActive();
    const ta = _getTextarea();
    const selection = window.getSelection();
    let selectedText = '';
    if (rich && selection?.rangeCount && rich.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      selectedText = selection.toString();
    } else if (ta && document.activeElement === ta) {
      selectedText = ta.value.slice(ta.selectionStart, ta.selectionEnd);
    }
    selectedText = selectedText.replace(/\s+/g, ' ').trim();
    if (selectedText && selectedText.length <= 120) inp.value = selectedText;
    inp.focus();
    inp.select();
    if (inp.value) _doFind('first', false);
  }
}

export function _closeFindBar() {
  const bar = document.getElementById('doc-find-bar');
  if (bar) bar.style.display = 'none';
  document.body.classList.remove('doc-find-active');
  _clearRichFindHighlights();
  _findMatches = [];
  _findMatchRanges = [];
  _findMatchQuery = '';
  _findIdx = -1;
  const cnt = document.getElementById('doc-find-count');
  if (cnt) cnt.textContent = '';
  const codeEl = _getCodeEl();
  if (codeEl) {
    delete codeEl.dataset.findQuery;
    delete codeEl.dataset.findCurrent;
    applyFindMarks(codeEl);
  }
  renderFindRects([], -1);
  const rich = _emailRichbodyActive();
  if (rich) rich.focus();
  else {
    const ta = _getTextarea();
    if (ta) ta.focus();
  }
}

export function _replaceAllLiteral(text, query, replacement) {
  if (!query) return { text, count: 0 };
  const lowerText = text.toLocaleLowerCase();
  const lowerQuery = query.toLocaleLowerCase();
  const parts = [];
  let cursor = 0;
  let count = 0;
  while (cursor <= text.length - query.length) {
    const hit = lowerText.indexOf(lowerQuery, cursor);
    if (hit < 0) break;
    parts.push(text.slice(cursor, hit), replacement);
    cursor = hit + query.length;
    count += 1;
  }
  if (!count) return { text, count: 0 };
  parts.push(text.slice(cursor));
  return { text: parts.join(''), count };
}

export function _replaceFindCurrent() {
  const query = document.getElementById('doc-find-input')?.value || '';
  const replacementInput = document.getElementById('doc-replace-input');
  if (!query || !replacementInput) return;
  const rich = _emailRichbodyActive();
  const ta = _getTextarea();
  if (rich) {
    const ranges = _buildRichFindRanges(rich, query);
    if (!ranges.length) { _doFind('refresh', false); return; }
    const index = Math.max(0, Math.min(_findIdx, ranges.length - 1));
    const range = ranges[index];
    _clearRichFindHighlights();
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    rich.focus();
    document.execCommand('insertText', false, replacementInput.value);
    _syncEmailRichbody(rich);
    _scheduleEmailRichbodySave();
  } else if (ta) {
    if (!_findMatches.length || _findIdx < 0) _doFind('first', false);
    const match = _findMatches[_findIdx];
    if (match == null) return;
    _replaceRange(ta, match, match + query.length, replacementInput.value);
  }
  _doFind('refresh', false);
  replacementInput.focus();
}

export function _replaceFindAll() {
  const query = document.getElementById('doc-find-input')?.value || '';
  const replacementInput = document.getElementById('doc-replace-input');
  if (!query || !replacementInput) return;
  const replacement = replacementInput.value;
  const rich = _emailRichbodyActive();
  const ta = _getTextarea();
  let count = 0;
  if (rich) {
    const ranges = _buildRichFindRanges(rich, query);
    count = ranges.length;
    if (count) {
      _clearRichFindHighlights();
      rich.focus();
      const selection = window.getSelection();
      // Replace each match in place, last first, with native text insertion.
      // Re-inserting the whole body as HTML let the browser carry the first
      // block's formatting (for example a heading) into every following block;
      // per-match insertion keeps every block intact and stays on the native
      // undo stack (one step per replacement).
      [...ranges].reverse().forEach(range => {
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand('insertText', false, replacement);
      });
      _syncEmailRichbody(rich);
      _scheduleEmailRichbodySave();
    }
  } else if (ta) {
    const result = _replaceAllLiteral(ta.value, query, replacement);
    count = result.count;
    if (count) _replaceRange(ta, 0, ta.value.length, result.text);
  }
  _doFind('refresh', false);
  const cnt = document.getElementById('doc-find-count');
  if (cnt) cnt.textContent = count ? `${count} replaced` : '0 results';
  replacementInput.focus();
}

export function _doFind(dir, focusTextarea) {
  const inp = document.getElementById('doc-find-input');
  const cnt = document.getElementById('doc-find-count');
  if (!inp) return;
  const q = inp.value;
  const codeEl = _getCodeEl();
  const rich = _emailRichbodyActive();
  const ta = _getTextarea();
  if (!q) {
    _findMatches = []; _findMatchRanges = []; _findMatchQuery = ''; _findIdx = -1;
    _clearRichFindHighlights();
    if (cnt) cnt.textContent = '';
    if (codeEl) { delete codeEl.dataset.findQuery; delete codeEl.dataset.findCurrent; applyFindMarks(codeEl); }
    renderFindRects([], -1);
    return;
  }
  if (rich) {
    const ranges = _buildRichFindRanges(rich, q);
    _findMatches = ranges.map((_, index) => index);
    if (!_findMatches.length) {
      _findIdx = -1;
      if (cnt) cnt.textContent = '0 results';
      _renderRichFindRanges(rich, [], -1);
      return;
    }
    if (dir === 'next') {
      _findIdx = _findIdx < _findMatches.length - 1 ? _findIdx + 1 : 0;
    } else if (dir === 'prev') {
      _findIdx = _findIdx > 0 ? _findIdx - 1 : _findMatches.length - 1;
    } else if (dir === 'refresh') {
      _findIdx = Math.max(0, Math.min(_findIdx, _findMatches.length - 1));
    } else {
      _findIdx = 0;
    }
    if (cnt) cnt.textContent = `${_findIdx + 1} / ${_findMatches.length}`;
    _renderRichFindRanges(rich, ranges, _findIdx);
    return;
  }
  if (!ta) return;
  const text = ta.value;
  const lq = q.toLowerCase();
  const lt = text.toLowerCase();
  _findMatches = [];
  let pos = 0;
  while (true) {
    const i = lt.indexOf(lq, pos);
    if (i < 0) break;
    _findMatches.push(i);
    pos = i + Math.max(1, q.length);
  }
  if (_findMatches.length === 0) {
    _findMatchRanges = [];
    _findMatchQuery = q;
    _findIdx = -1;
    if (cnt) cnt.textContent = '0 results';
    if (codeEl) { codeEl.dataset.findQuery = q; delete codeEl.dataset.findCurrent; applyFindMarks(codeEl); }
    renderFindRects([], -1);
    return;
  }
  if (dir === 'next') {
    _findIdx = _findIdx < _findMatches.length - 1 ? _findIdx + 1 : 0;
  } else if (dir === 'prev') {
    _findIdx = _findIdx > 0 ? _findIdx - 1 : _findMatches.length - 1;
  } else if (dir === 'refresh') {
    _findIdx = Math.max(0, Math.min(_findIdx, _findMatches.length - 1));
  } else {
    _findIdx = 0;
  }
  if (cnt) cnt.textContent = `${_findIdx + 1} / ${_findMatches.length}`;
  const matchPos = _findMatches[_findIdx];
  ta.setSelectionRange(matchPos, matchPos + q.length);
  const linesBefore = text.slice(0, matchPos).split('\n').length;
  const lineH = parseFloat(getComputedStyle(ta).lineHeight) || 18;
  ta.scrollTop = Math.max(0, (linesBefore - 3) * lineH);
  if (codeEl) {
    codeEl.dataset.findQuery = q;
    codeEl.dataset.findCurrent = String(_findIdx);
    applyFindMarks(codeEl);
  }
  _findMatchQuery = q;
  _findMatchRanges = _findMatches.map(s => [s, s + q.length]);
  renderFindRects(_findMatchRanges, _findIdx);
  if (focusTextarea) ta.focus();
}

/** Wrap find-matches in the syntax-highlighted overlay with <mark> spans.
 * Walks text nodes so existing hljs spans are preserved. Matches that cross
 * syntax tokens are skipped (rare for user searches). */
export function applyFindMarks(codeEl) {
  if (!codeEl) return;
  codeEl.querySelectorAll('mark.doc-find-mark').forEach(m => {
    const parent = m.parentNode;
    while (m.firstChild) parent.insertBefore(m.firstChild, m);
    parent.removeChild(m);
    parent.normalize();
  });
  const q = codeEl.dataset.findQuery || '';
  if (!q) return;
  const currentIdx = parseInt(codeEl.dataset.findCurrent || '-1', 10);
  const lq = q.toLowerCase();
  let occurrence = 0;
  const walker = document.createTreeWalker(codeEl, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  let n;
  while ((n = walker.nextNode())) nodes.push(n);
  for (const node of nodes) {
    const val = node.nodeValue || '';
    const lv = val.toLowerCase();
    if (!lv.includes(lq)) continue;
    const frag = document.createDocumentFragment();
    let i = 0;
    while (i < val.length) {
      const hit = lv.indexOf(lq, i);
      if (hit < 0) { frag.appendChild(document.createTextNode(val.slice(i))); break; }
      if (hit > i) frag.appendChild(document.createTextNode(val.slice(i, hit)));
      const mark = document.createElement('mark');
      mark.className = 'doc-find-mark' + (occurrence === currentIdx ? ' current' : '');
      mark.textContent = val.slice(hit, hit + q.length);
      frag.appendChild(mark);
      occurrence++;
      i = hit + q.length;
    }
    node.parentNode.replaceChild(frag, node);
  }
}

/**
 * Batched, high-performance measurement and rendering of find highlight rects.
 * Uses a single Range over the mirror text node to compute visual row geometry
 * in one layout pass without thrashing scrollHeight on every match.
 * Updates DOM rects in place on scroll and match changes.
 */
function _sameMatchRanges(a, b) {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i][0] !== b[i][0] || a[i][1] !== b[i][1]) return false;
  }
  return true;
}

export function renderFindRects(matches, currentIdx) {
  const wrap = _getWrap();
  if (!wrap) return;
  const clearExistingRects = () => {
    wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
    const existingMirror = document.getElementById('doc-find-rect-mirror');
    if (existingMirror) existingMirror._findCache = null;
  };
  if (!matches || matches.length === 0) {
    clearExistingRects();
    return;
  }
  const textarea = _getTextarea();
  if (!textarea) return;
  const text = textarea.value;
  if (!text) {
    clearExistingRects();
    return;
  }
  const style = getComputedStyle(textarea);
  const paddingTop = parseFloat(style.paddingTop) || 10;
  const paddingLeft = parseFloat(style.paddingLeft) || 48;
  const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);
  const wrapMode = textarea.wrap === 'off' ? 'off' : 'soft';

  let mirror = document.getElementById('doc-find-rect-mirror');
  if (!mirror) {
    mirror = document.createElement('div');
    mirror.id = 'doc-find-rect-mirror';
    mirror.style.cssText =
      'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;box-sizing:border-box;';
    wrap.appendChild(mirror);
  }

  const clientWidth = textarea.clientWidth;
  const scrollTop = textarea.scrollTop;

  let rowData = null;
  const cache = mirror._findCache;
  if (
    cache &&
    cache.text === text &&
    cache.clientWidth === clientWidth &&
    cache.wrapMode === wrapMode &&
    cache.whiteSpace === style.whiteSpace &&
    cache.font === style.font &&
    cache.fontFamily === style.fontFamily &&
    cache.fontSize === style.fontSize &&
    cache.lineHeight === lineHeight &&
    cache.rawLineHeight === style.lineHeight &&
    cache.padding === style.padding &&
    cache.paddingTop === paddingTop &&
    cache.paddingLeft === paddingLeft &&
    cache.borderWidth === style.borderWidth &&
    cache.tabSize === style.tabSize &&
    cache.letterSpacing === style.letterSpacing &&
    cache.wordSpacing === style.wordSpacing &&
    cache.textIndent === style.textIndent &&
    _sameMatchRanges(cache.matches, matches)
  ) {
    rowData = cache.rows;
  } else {
    mirror.style.whiteSpace = wrapMode === 'off' ? 'pre' : 'pre-wrap';
    mirror.style.wordWrap = wrapMode === 'off' ? 'normal' : 'break-word';
    mirror.style.overflowWrap = wrapMode === 'off' ? 'normal' : 'break-word';
    mirror.style.font = style.font;
    mirror.style.padding = style.padding;
    mirror.style.borderWidth = style.borderWidth;
    mirror.style.borderStyle = 'solid';
    mirror.style.borderColor = 'transparent';
    mirror.style.width = clientWidth + 'px';
    mirror.style.tabSize = style.tabSize;
    mirror.style.letterSpacing = style.letterSpacing;
    mirror.style.wordSpacing = style.wordSpacing;
    mirror.style.textIndent = style.textIndent;

    mirror.textContent = text;
    const textNode = mirror.firstChild;
    if (!textNode) {
      clearExistingRects();
      return;
    }

    const mirrorRect = mirror.getBoundingClientRect();
    const mirrorTop = mirrorRect.top + paddingTop;
    const range = document.createRange();
    const maxLen = textNode.length;
    rowData = new Array(matches.length);

    for (let i = 0; i < matches.length; i++) {
      const [s, e] = matches[i];
      const sClamped = Math.max(0, Math.min(s, maxLen));
      let eClamped = Math.max(sClamped, Math.min(e, maxLen));
      if (sClamped === eClamped && sClamped < maxLen) {
        eClamped = sClamped + 1;
      }
      range.setStart(textNode, sClamped);
      range.setEnd(textNode, eClamped);
      const r = range.getBoundingClientRect();

      const visualRowStart = Math.max(0, Math.floor((r.top - mirrorTop + (lineHeight * 0.25)) / lineHeight));
      const visualRowEnd = Math.max(visualRowStart, Math.floor((r.bottom - mirrorTop - 1) / lineHeight));
      const rowCount = Math.max(1, visualRowEnd - visualRowStart + 1);
      rowData[i] = [visualRowStart, rowCount];
    }

    mirror._findLayoutRecomputes = (mirror._findLayoutRecomputes || 0) + 1;
    mirror._findCache = {
      text,
      clientWidth,
      wrapMode,
      whiteSpace: style.whiteSpace,
      font: style.font,
      fontFamily: style.fontFamily,
      fontSize: style.fontSize,
      lineHeight,
      rawLineHeight: style.lineHeight,
      padding: style.padding,
      paddingTop,
      paddingLeft,
      borderWidth: style.borderWidth,
      tabSize: style.tabSize,
      letterSpacing: style.letterSpacing,
      wordSpacing: style.wordSpacing,
      textIndent: style.textIndent,
      matches,
      rows: rowData,
    };
  }

  const existingRects = wrap.querySelectorAll('.doc-find-rect');
  if (existingRects.length === rowData.length) {
    for (let i = 0; i < rowData.length; i++) {
      const [rowStart, count] = rowData[i];
      const top = paddingTop + rowStart * lineHeight - scrollTop;
      const height = count * lineHeight;
      const rect = existingRects[i];
      rect.style.left = paddingLeft + 'px';
      rect.style.top = top + 'px';
      rect.style.height = height + 'px';
      const isCurrent = i === currentIdx;
      if (rect.classList.contains('current') !== isCurrent) {
        rect.classList.toggle('current', isCurrent);
      }
    }
  } else {
    wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
    const frag = document.createDocumentFragment();
    for (let i = 0; i < rowData.length; i++) {
      const [rowStart, count] = rowData[i];
      const top = paddingTop + rowStart * lineHeight - scrollTop;
      const height = count * lineHeight;
      const rect = document.createElement('div');
      rect.className = 'doc-find-rect' + (i === currentIdx ? ' current' : '');
      rect.style.cssText =
        `position:absolute;left:${paddingLeft}px;right:8px;` +
        `top:${top}px;height:${height}px;` +
        `pointer-events:none;z-index:6;border-radius:2px;`;
      frag.appendChild(rect);
    }
    wrap.appendChild(frag);
  }
}

export function onFindTextareaScroll() {
  if (_findMatches && _findMatches.length) {
    const _q = document.getElementById('doc-find-input')?.value || '';
    if (!_q) return;
    if (_findMatchQuery !== _q || _findMatchRanges.length !== _findMatches.length) {
      _findMatchQuery = _q;
      _findMatchRanges = _findMatches.map(s => [s, s + _q.length]);
    }
    renderFindRects(_findMatchRanges, _findIdx);
  }
}

export function handleFindKeydown(e) {
  const mod = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  if (mod && (key === 'f' || key === 'h')) {
    e.preventDefault();
    e.stopPropagation();
    _openFindBar(key === 'h');
    return true;
  }
  return false;
}

export function getFindMatches() {
  return _findMatches;
}

export function getFindIndex() {
  return _findIdx;
}

let _findResizeObserver = null;

export function initFindReplace(options = {}) {
  _deps = { ...options };

  const ta = _getTextarea();
  if (ta && typeof ResizeObserver !== 'undefined') {
    if (_findResizeObserver) _findResizeObserver.disconnect();
    _findResizeObserver = new ResizeObserver(() => {
      if (_findMatches && _findMatches.length) onFindTextareaScroll();
    });
    _findResizeObserver.observe(ta);
  }

  document.getElementById('doc-find-close')?.addEventListener('click', _closeFindBar);
  document.getElementById('doc-find-next')?.addEventListener('click', () => _doFind('next', true));
  document.getElementById('doc-find-prev')?.addEventListener('click', () => _doFind('prev', true));
  document.getElementById('doc-find-toolbar-btn')?.addEventListener('click', () => _openFindBar(false));
  document.getElementById('doc-find-replace-toggle')?.addEventListener('click', () => {
    const row = document.getElementById('doc-replace-row');
    _setReplaceVisible(Boolean(row?.hidden), Boolean(row?.hidden));
  });
  document.getElementById('doc-replace-current')?.addEventListener('click', _replaceFindCurrent);
  document.getElementById('doc-replace-all')?.addEventListener('click', _replaceFindAll);
  document.getElementById('doc-find-input')?.addEventListener('input', () => _doFind('first', false));
  document.getElementById('doc-find-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); _closeFindBar(); }
    else if (e.key === 'Enter') { e.preventDefault(); _doFind(e.shiftKey ? 'prev' : 'next', false); }
  });
  document.getElementById('doc-replace-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); _closeFindBar(); }
    else if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? _replaceFindAll() : _replaceFindCurrent(); }
  });
}

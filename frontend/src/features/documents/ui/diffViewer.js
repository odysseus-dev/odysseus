// document-reference-compat: uiModule - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: docs - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: activeDocId - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// document-reference-compat: _setMarkdownPreviewActive - Optional standalone fallback guarded by typeof; integrated callers supply explicit init dependencies.
// static/js/document/diffViewer.js
/**
 * Diff mode and review controller for document editor.
 * Extracted from document.js for modularity, testability, and clean separation.
 */

const DIFF_MODE_THRESHOLD = 3; // min changed lines to trigger diff mode

// Diff mode state
export let _diffModeActive = false;
let _diffOldContent = null;
let _diffNewContent = null;
let _diffChunks = [];          // [{id, oldLines, newLines, startLine, resolved, accepted}]
let _diffUnresolvedCount = 0;
let _currentChunkIndex = -1;
let _diffHiddenVisualMode = false;

let _deps = {};

const _raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb) => cb();

function _getTextarea() {
  return (_deps.getTextarea && _deps.getTextarea()) || document.getElementById('doc-editor-textarea');
}

function _getWrap() {
  return (_deps.getWrap && _deps.getWrap()) || document.getElementById('doc-editor-wrap');
}

function _getCodeEl() {
  return (_deps.getCodeEl && _deps.getCodeEl()) || document.getElementById('doc-editor-code');
}

function _getGutter() {
  return (_deps.getGutter && _deps.getGutter()) || document.getElementById('doc-line-numbers');
}

function _getHighlight() {
  return (_deps.getHighlight && _deps.getHighlight()) || document.getElementById('doc-editor-highlight');
}

function _getDiffToggleBtn() {
  return (_deps.getDiffToggleBtn && _deps.getDiffToggleBtn()) || document.getElementById('doc-diff-toggle-btn');
}

let saveDocument = (opts) => {
  if (typeof _deps !== 'undefined' && _deps.saveDocument) return _deps.saveDocument(opts);
  if (typeof window !== 'undefined' && window.documentModule?.saveDocument) {
    return window.documentModule.saveDocument(opts);
  }
};

let syncHighlighting = () => {
  if (typeof _deps !== 'undefined' && _deps.syncHighlighting) return _deps.syncHighlighting();
  if (typeof window !== 'undefined' && window.documentModule?.syncHighlighting) {
    return window.documentModule.syncHighlighting();
  }
};

let updateLineNumbers = (text) => {
  if (typeof _deps !== 'undefined' && _deps.updateLineNumbers) return _deps.updateLineNumbers(text);
  if (typeof window !== 'undefined' && window.documentModule?.updateLineNumbers) {
    return window.documentModule.updateLineNumbers(text);
  }
};

/**
 * Pure LCS line-diff algorithm with prefix and suffix preservation.
 * Compares oldText and newText line by line.
 * Returns array of { type: 'equal'|'insert'|'delete', text: string } or null when over maxCells.
 */
function diffLines(oldText, newText, { maxCells = 500000 } = {}) {
  const oldLines = (oldText || '').split('\n');
  const newLines = (newText || '').split('\n');
  const m = oldLines.length, n = newLines.length;

  let suffix = 0;
  while (suffix < m && suffix < n && oldLines[m - 1 - suffix] === newLines[n - 1 - suffix]) {
    suffix++;
  }

  let prefix = 0;
  while (prefix < (m - suffix) && prefix < (n - suffix) && oldLines[prefix] === newLines[prefix]) {
    prefix++;
  }

  const mMid = m - prefix - suffix;
  const nMid = n - prefix - suffix;

  // Guard against massive inputs that could freeze the UI thread
  if (mMid * nMid > maxCells) return null;

  const entries = [];
  for (let k = 0; k < prefix; k++) {
    entries.push({ type: 'equal', text: oldLines[k] });
  }

  if (mMid > 0 || nMid > 0) {
    const oldMid = oldLines.slice(prefix, m - suffix);
    const newMid = newLines.slice(prefix, n - suffix);
    const dp = Array.from({ length: mMid + 1 }, () => new Uint16Array(nMid + 1));
    for (let i = 1; i <= mMid; i++) {
      for (let j = 1; j <= nMid; j++) {
        dp[i][j] = oldMid[i - 1] === newMid[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }

    const midEntries = [];
    let i = mMid, j = nMid;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && oldMid[i - 1] === newMid[j - 1]) {
        midEntries.push({ type: 'equal', text: oldMid[i - 1] });
        i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        midEntries.push({ type: 'insert', text: newMid[j - 1] });
        j--;
      } else {
        midEntries.push({ type: 'delete', text: oldMid[i - 1] });
        i--;
      }
    }
    midEntries.reverse();
    for (let k = 0; k < midEntries.length; k++) {
      entries.push(midEntries[k]);
    }
  }

  for (let k = m - suffix; k < m; k++) {
    entries.push({ type: 'equal', text: oldLines[k] });
  }

  return entries;
}

/** Line-level LCS diff algorithm used by diff review mode */
function _computeLineDiff(oldText, newText) {
  const raw = diffLines(oldText, newText);
  if (!raw) {
    // Fallback for very large diffs to avoid freezing the UI: treat old as deleted, new as inserted
    const oldLines = (oldText || '').split('\n');
    const newLines = (newText || '').split('\n');
    const entries = [];
    for (const line of oldLines) entries.push({ type: 'delete', line });
    for (const line of newLines) entries.push({ type: 'insert', line });
    return entries;
  }
  return raw.map(e => ({ type: e.type, line: e.text }));
}

/** Group diff entries into chunks (contiguous change blocks) */
function _buildDiffChunks(entries) {
  const chunks = [];
  let chunkId = 0;
  let lineIdx = 0;
  let i = 0;
  while (i < entries.length) {
    const e = entries[i];
    if (e.type === 'equal') {
      lineIdx++;
      i++;
    } else {
      // Gather contiguous non-equal entries into a chunk
      const startLine = lineIdx;
      const oldLines = [], newLines = [];
      while (i < entries.length && entries[i].type !== 'equal') {
        if (entries[i].type === 'delete') oldLines.push(entries[i].line);
        else newLines.push(entries[i].line);
        i++;
      }
      chunks.push({
        id: chunkId++,
        oldLines,
        newLines,
        startLine,
        resolved: false,
        accepted: false,
      });
      lineIdx += oldLines.length + newLines.length;
    }
  }
  return chunks;
}

/** Enter diff mode - show line-level diff for review */
function enterDiffMode(oldContent, newContent, options = {}) {
  if (_diffModeActive) exitDiffMode(true);

  _diffModeActive = true;
  _diffOldContent = oldContent;
  _diffNewContent = newContent;

  const entries = _computeLineDiff(oldContent, newContent);
  _diffChunks = _buildDiffChunks(entries);
  _diffUnresolvedCount = _diffChunks.length;
  _currentChunkIndex = _diffChunks.length > 0 ? 0 : -1;

  if (_diffChunks.length === 0) {
    _diffModeActive = false;
    if (_deps.showToast) {
      _deps.showToast('No changes');
    } else if (typeof uiModule !== 'undefined' && uiModule?.showToast) {
      uiModule.showToast('No changes');
    }
    return;
  }

  const rich = typeof document !== 'undefined' ? document.getElementById('doc-email-richbody') : null;
  _diffHiddenVisualMode = !!(rich && rich.style.display !== 'none' && rich.classList.contains('markdown-visual-mode'));
  if (_diffHiddenVisualMode) _deps.hideMarkdownVisualEditor?.();
  const textarea = _getTextarea();
  if (textarea) textarea.readOnly = true;
  const wrap = _getWrap();
  if (wrap) wrap.classList.add('diff-mode');

  _renderDiffOverlay(entries);
  _renderDiffToolbar();
  _renderDiffGutter();
  _raf(() => {
    _scrollToDiffChunk(_diffChunks[0]?.id);
    _highlightActiveChunk(_diffChunks[0]?.id);
  });

  // Update header button
  const diffBtn = _getDiffToggleBtn();
  if (diffBtn) diffBtn.classList.add('active');
}

/** Render the line-level diff into the code highlight element */
function _renderDiffOverlay(entries) {
  const codeEl = _getCodeEl();
  const gutter = _getGutter();
  if (!codeEl) return;

  codeEl.innerHTML = '';
  let gutterHtml = '';
  let oldNum = 0, newNum = 0;

  // Pre-assign chunk IDs to entries by walking chunks and entries together
  let chunkIdx = 0;
  let entryIdx = 0;
  const entryChunkMap = new Array(entries.length).fill(-1);
  while (entryIdx < entries.length) {
    if (entries[entryIdx].type === 'equal') {
      entryIdx++;
    } else {
      // This is the start of a change block - assign all contiguous non-equal entries to the current chunk
      const cid = chunkIdx < _diffChunks.length ? _diffChunks[chunkIdx].id : -1;
      while (entryIdx < entries.length && entries[entryIdx].type !== 'equal') {
        entryChunkMap[entryIdx] = cid;
        entryIdx++;
      }
      chunkIdx++;
    }
  }

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e.type === 'equal') {
      oldNum++; newNum++;
      const el = document.createElement('span');
      el.className = 'diff-line-equal';
      el.textContent = e.line + '\n';
      codeEl.appendChild(el);
      gutterHtml += newNum + '\n';
    } else if (e.type === 'delete') {
      oldNum++;
      const el = document.createElement('span');
      el.className = 'diff-line-del';
      if (entryChunkMap[i] >= 0) el.dataset.chunkId = entryChunkMap[i];
      el.textContent = e.line + '\n';
      codeEl.appendChild(el);
      gutterHtml += '−\n';
    } else {
      newNum++;
      const el = document.createElement('span');
      el.className = 'diff-line-add';
      if (entryChunkMap[i] >= 0) el.dataset.chunkId = entryChunkMap[i];
      el.textContent = e.line + '\n';
      codeEl.appendChild(el);
      gutterHtml += '+\n';
    }
  }

  if (gutter) gutter.textContent = gutterHtml;
  codeEl.dataset.hasDiff = '1';

  // Sync textarea to show the combined view (old + new interleaved) for scroll sizing
  const textarea = _getTextarea();
  if (textarea) {
    const allLines = entries.map(e => e.line);
    textarea.value = allLines.join('\n') + '\n';
  }
}

/** Render the diff toolbar above the editor */
function _renderDiffToolbar() {
  let toolbar = document.getElementById('doc-diff-toolbar');
  if (toolbar) toolbar.remove();

  toolbar = document.createElement('div');
  toolbar.id = 'doc-diff-toolbar';
  toolbar.className = 'diff-toolbar';

  const status = document.createElement('span');
  status.className = 'diff-toolbar-status';
  status.id = 'diff-toolbar-status';
  _updateDiffStatus(status);

  const prevBtn = document.createElement('button');
  prevBtn.id = 'doc-diff-prev-btn';
  prevBtn.className = 'diff-toolbar-btn diff-toolbar-btn-prev';
  prevBtn.textContent = 'Previous';
  prevBtn.title = 'Previous change';
  prevBtn.addEventListener('click', () => _navigateToDiffChunk('prev'));

  const nextBtn = document.createElement('button');
  nextBtn.id = 'doc-diff-next-btn';
  nextBtn.className = 'diff-toolbar-btn diff-toolbar-btn-next';
  nextBtn.textContent = 'Next';
  nextBtn.title = 'Next change';
  nextBtn.addEventListener('click', () => _navigateToDiffChunk('next'));

  const acceptCurrentBtn = document.createElement('button');
  acceptCurrentBtn.id = 'doc-diff-accept-chunk-btn';
  acceptCurrentBtn.className = 'diff-toolbar-btn diff-toolbar-btn-accept-chunk';
  acceptCurrentBtn.textContent = 'Accept';
  acceptCurrentBtn.title = 'Accept current change';
  acceptCurrentBtn.addEventListener('click', () => {
    const chunk = _getCurrentChunk();
    if (chunk && !chunk.resolved) {
      _resolveChunk(chunk.id, true);
    }
  });

  const rejectCurrentBtn = document.createElement('button');
  rejectCurrentBtn.id = 'doc-diff-reject-chunk-btn';
  rejectCurrentBtn.className = 'diff-toolbar-btn diff-toolbar-btn-reject-chunk';
  rejectCurrentBtn.textContent = 'Reject';
  rejectCurrentBtn.title = 'Reject current change';
  rejectCurrentBtn.addEventListener('click', () => {
    const chunk = _getCurrentChunk();
    if (chunk && !chunk.resolved) {
      _resolveChunk(chunk.id, false);
    }
  });

  const acceptAll = document.createElement('button');
  acceptAll.id = 'doc-diff-accept-all-btn';
  acceptAll.className = 'diff-toolbar-btn diff-toolbar-btn-accept';
  acceptAll.textContent = 'Accept All';
  acceptAll.addEventListener('click', () => _resolveAllChunks(true));

  const rejectAll = document.createElement('button');
  rejectAll.id = 'doc-diff-reject-all-btn';
  rejectAll.className = 'diff-toolbar-btn diff-toolbar-btn-reject';
  rejectAll.textContent = 'Reject All';
  rejectAll.addEventListener('click', () => _resolveAllChunks(false));

  toolbar.appendChild(status);
  toolbar.appendChild(prevBtn);
  toolbar.appendChild(nextBtn);
  toolbar.appendChild(acceptCurrentBtn);
  toolbar.appendChild(rejectCurrentBtn);
  toolbar.appendChild(acceptAll);
  toolbar.appendChild(rejectAll);

  const wrap = _getWrap();
  if (wrap && wrap.parentNode) wrap.parentNode.insertBefore(toolbar, wrap);
}

/** Render per-chunk accept/reject buttons in a gutter overlay */
function _renderDiffGutter() {
  let gutterEl = document.getElementById('doc-diff-gutter');
  if (gutterEl) gutterEl.remove();

  gutterEl = document.createElement('div');
  gutterEl.id = 'doc-diff-gutter';
  gutterEl.className = 'diff-gutter';

  const codeEl = _getCodeEl();
  if (!codeEl) return;

  // Insert chunk action buttons directly next to the first changed line of each chunk
  // This way they scroll naturally with the content
  const renderActions = () => {
    for (const chunk of _diffChunks) {
      if (chunk.resolved) continue;
      const firstEl = codeEl.querySelector(`[data-chunk-id="${chunk.id}"]`);
      if (!firstEl) continue;

      const actions = document.createElement('span');
      actions.className = 'diff-chunk-actions';
      actions.dataset.chunkId = chunk.id;

      const acceptBtn = document.createElement('button');
      acceptBtn.className = 'diff-chunk-btn diff-chunk-btn-accept';
      acceptBtn.title = 'Accept change';
      acceptBtn.innerHTML = '✓';
      acceptBtn.addEventListener('click', (e) => { e.stopPropagation(); _resolveChunk(chunk.id, true); });

      const rejectBtn = document.createElement('button');
      rejectBtn.className = 'diff-chunk-btn diff-chunk-btn-reject';
      rejectBtn.title = 'Reject change';
      rejectBtn.innerHTML = '✗';
      rejectBtn.addEventListener('click', (e) => { e.stopPropagation(); _resolveChunk(chunk.id, false); });

      actions.appendChild(acceptBtn);
      actions.appendChild(rejectBtn);

      // Insert at the start of the first line span
      firstEl.style.position = 'relative';
      firstEl.appendChild(actions);
    }
  };

  _raf(renderActions);
}

/** Update the toolbar status text */
function _updateDiffStatus(statusEl) {
  const el = statusEl || document.getElementById('diff-toolbar-status');
  if (!el) return;
  const resolved = _diffChunks.length - _diffUnresolvedCount;
  el.textContent = `${resolved} / ${_diffChunks.length} changes resolved`;
}

function _scrollToDiffChunk(chunkId) {
  if (chunkId == null) return;
  const firstEl = document.querySelector(`[data-chunk-id="${chunkId}"]`);
  const highlight = _getHighlight();
  const textarea = _getTextarea();
  if (!firstEl || !highlight) return;
  const target = Math.max(0, firstEl.offsetTop - 80);
  highlight.scrollTop = target;
  if (textarea) textarea.scrollTop = target;
  const gutter = _getGutter();
  const lineNums = gutter && (_deps.lineNumberContentEl ? _deps.lineNumberContentEl(gutter) : (gutter.querySelector?.('.doc-line-number-content') || gutter));
  if (lineNums && lineNums.style) lineNums.style.transform = `translateY(${-target}px)`;
}

function _highlightActiveChunk(chunkId) {
  const codeEl = _getCodeEl();
  if (!codeEl) return;
  codeEl.querySelectorAll('.diff-chunk-active').forEach(el => el.classList.remove('diff-chunk-active'));
  if (chunkId != null) {
    codeEl.querySelectorAll(`[data-chunk-id="${chunkId}"]`).forEach(el => el.classList.add('diff-chunk-active'));
  }
}

function _getCurrentChunk() {
  if (_currentChunkIndex >= 0 && _currentChunkIndex < _diffChunks.length) {
    return _diffChunks[_currentChunkIndex];
  }
  return _diffChunks.find(c => !c.resolved) || null;
}

function _navigateToDiffChunk(direction) {
  if (_diffChunks.length === 0) return;
  if (direction === 'prev') {
    _currentChunkIndex = (_currentChunkIndex <= 0) ? _diffChunks.length - 1 : _currentChunkIndex - 1;
  } else {
    _currentChunkIndex = (_currentChunkIndex >= _diffChunks.length - 1) ? 0 : _currentChunkIndex + 1;
  }
  const chunk = _diffChunks[_currentChunkIndex];
  if (chunk) {
    _scrollToDiffChunk(chunk.id);
    _highlightActiveChunk(chunk.id);
  }
}

/** Resolve a single chunk */
function _resolveChunk(chunkId, accept) {
  const chunk = _diffChunks.find(c => c.id === chunkId);
  if (!chunk || chunk.resolved) return;

  chunk.resolved = true;
  chunk.accepted = accept;
  _diffUnresolvedCount--;

  // Fade resolved lines in the overlay
  const codeEl = _getCodeEl();
  if (codeEl) {
    codeEl.querySelectorAll(`[data-chunk-id="${chunkId}"]`).forEach(el => {
      el.classList.add('diff-chunk-resolved');
    });
  }

  // Remove the gutter buttons for this chunk
  const gutterActions = document.querySelector(`.diff-chunk-actions[data-chunk-id="${chunkId}"]`);
  if (gutterActions) gutterActions.remove();

  _updateDiffStatus();

  // Persist partial progress so refresh does not lose individually-resolved chunks
  _applyResolvedChunksToTextarea();
  saveDocument({ silent: true });

  if (_diffUnresolvedCount === 0) {
    setTimeout(() => exitDiffMode(false), 300);
  } else {
    const nextChunk = _diffChunks.find(c => !c.resolved);
    if (nextChunk) {
      _currentChunkIndex = _diffChunks.findIndex(c => c.id === nextChunk.id);
      _raf(() => {
        _scrollToDiffChunk(nextChunk.id);
        _highlightActiveChunk(nextChunk.id);
      });
    }
  }
}

/** Compute current content from old + resolved chunk decisions; unresolved chunks
 *  default to the original (rejected) until the user decides. Updates textarea. */
function _applyResolvedChunksToTextarea() {
  const textarea = _getTextarea();
  if (!textarea) return;
  const entries = _computeLineDiff(_diffOldContent || '', _diffNewContent || '');
  const result = [];
  let chunkIdx = 0;
  let i = 0;
  while (i < entries.length) {
    if (entries[i].type === 'equal') {
      result.push(entries[i].line);
      i++;
    } else {
      const chunk = _diffChunks[chunkIdx++];
      const chunkOld = [], chunkNew = [];
      while (i < entries.length && entries[i].type !== 'equal') {
        if (entries[i].type === 'delete') chunkOld.push(entries[i].line);
        else chunkNew.push(entries[i].line);
        i++;
      }
      // Resolved+accepted -> use new; resolved+rejected OR unresolved -> keep old
      if (chunk && chunk.resolved && chunk.accepted) {
        result.push(...chunkNew);
      } else {
        result.push(...chunkOld);
      }
    }
  }
  textarea.value = result.join('\n');
}

/** Resolve all chunks at once */
function _resolveAllChunks(accept) {
  for (const chunk of _diffChunks) {
    if (!chunk.resolved) {
      chunk.resolved = true;
      chunk.accepted = accept;
    }
  }
  _diffUnresolvedCount = 0;
  exitDiffMode(false);
}

  /** Exit diff mode and apply resolved changes */
  function exitDiffMode(discard, { persist = true } = {}) {
    if (!_diffModeActive) return;
    _diffModeActive = false;
    const acceptedAnyDiffChunk = !discard && _diffChunks.some(chunk => chunk && chunk.resolved && chunk.accepted);

    const textarea = document.getElementById('doc-editor-textarea');
    const codeEl = document.getElementById('doc-editor-code');
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.classList.remove('diff-mode');

    if (discard) {
      // Reject all - restore original content
      if (textarea) textarea.value = _diffOldContent || '';
    } else {
      // Build final content from resolved chunks
      const oldLines = (_diffOldContent || '').split('\n');
      const newLines = (_diffNewContent || '').split('\n');
      const entries = _computeLineDiff(_diffOldContent || '', _diffNewContent || '');

      const result = [];
      let chunkIdx = 0;
      let i = 0;
      while (i < entries.length) {
        if (entries[i].type === 'equal') {
          result.push(entries[i].line);
          i++;
        } else {
          // Find the matching chunk
          const chunk = _diffChunks[chunkIdx++];
          // Skip all entries belonging to this chunk
          const chunkOld = [], chunkNew = [];
          while (i < entries.length && entries[i].type !== 'equal') {
            if (entries[i].type === 'delete') chunkOld.push(entries[i].line);
            else chunkNew.push(entries[i].line);
            i++;
          }
          if (chunk && chunk.accepted) {
            result.push(...chunkNew);
          } else {
            result.push(...chunkOld);
          }
        }
      }
      if (textarea) textarea.value = result.join('\n');
    }

    // Restore editor state
    if (textarea) textarea.readOnly = false;
    if (codeEl) delete codeEl.dataset.hasDiff;

    // Clean up toolbar and any remaining chunk action buttons
    const toolbar = document.getElementById('doc-diff-toolbar');
    if (toolbar) toolbar.remove();
    document.querySelectorAll('.diff-chunk-actions').forEach(el => el.remove());

    // Reset state
    _diffOldContent = null;
    _diffNewContent = null;
    _diffChunks = [];
    _diffUnresolvedCount = 0;
    _currentChunkIndex = -1;

    const diffBtn = document.getElementById('doc-diff-toggle-btn');
    if (diffBtn) diffBtn.classList.remove('active');

    syncHighlighting();
    updateLineNumbers(textarea ? textarea.value : '');
    const restoreVisual = _diffHiddenVisualMode;
    _diffHiddenVisualMode = false;
    if (restoreVisual) {
      const doc = _deps.getActiveDoc?.();
      // The resolved source is authoritative; capture it, then re-render the
      // visual view from it.
      _deps.captureSourceToDoc?.();
      if (!acceptedAnyDiffChunk && doc && _deps.isVisualMarkdownDoc?.(doc) && _deps.resolveMarkdownEditorMode?.(doc) === 'visual') {
        _deps.showMarkdownVisualEditor?.(doc);
        _deps.syncHeaderActions?.();
      }
    }
    if (persist) saveDocument({ silent: true });
    if (acceptedAnyDiffChunk) {
      if (typeof _deps !== 'undefined' && _deps.onAcceptChanges) {
        _deps.onAcceptChanges();
      } else {
        const lang = ((typeof docs !== 'undefined' && docs.get(typeof activeDocId !== 'undefined' ? activeDocId : null)?.language) || document.getElementById('doc-language-select')?.value || '').toLowerCase();
        if (lang === 'markdown') {
          _raf(() => {
            if (typeof _setMarkdownPreviewActive === 'function') {
              _setMarkdownPreviewActive(true, { remember: true });
            }
          });
        }
      }
    }
  }

  /** Check if diff mode is active */
  function isDiffModeActive() { return _diffModeActive; }

export function initDiffViewer(options = {}) {
  _deps = { ...options };
}

export function getDiffChunks() {
  return _diffChunks;
}

export function getDiffUnresolvedCount() {
  return _diffUnresolvedCount;
}

export function getCurrentChunkIndex() {
  return _currentChunkIndex;
}

export {
  DIFF_MODE_THRESHOLD,
  diffLines,
  _computeLineDiff,
  _buildDiffChunks,
  enterDiffMode,
  exitDiffMode,
  isDiffModeActive,
  _renderDiffOverlay,
  _renderDiffToolbar,
  _renderDiffGutter,
  _updateDiffStatus,
  _scrollToDiffChunk,
  _highlightActiveChunk,
  _getCurrentChunk,
  _navigateToDiffChunk,
  _resolveChunk,
  _resolveAllChunks,
  _applyResolvedChunksToTextarea,
};

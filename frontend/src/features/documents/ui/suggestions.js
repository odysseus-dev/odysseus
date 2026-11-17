// static/js/document/suggestions.js
/**
 * Inline Suggestion Cards and Highlights controller.
 * Extracted from document.js for modularity, testability, and clean separation.
 */

let _activeSuggestions = []; // [{ id, find, replace, reason, highlightEl, bubbleEl, cardEl }]
let _suggestionSelectionRange = null;
let _suggestionTotal = 0;
let _suggestionIndex = 0;
let _suggestionDocId = null;
let _suggestionGeneration = 0;

let _deps = {};

function topPortalZ() {
  if (typeof _deps.topPortalZ === 'function') return _deps.topPortalZ();
  if (typeof window !== 'undefined' && typeof window.topPortalZ === 'function') return window.topPortalZ();
  return 10031;
}

function _getTextarea() {
  return (_deps.getTextarea && _deps.getTextarea()) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null);
}

function _getWrap() {
  return (_deps.getWrap && _deps.getWrap()) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-wrap') : null);
}

function _getPane() {
  return (_deps.getPane && _deps.getPane()) || (typeof document !== 'undefined' ? document.querySelector('.doc-editor-pane') : null);
}

function _getCodeEl() {
  return (_deps.getCodeEl && _deps.getCodeEl()) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-code') : null);
}

function _getActiveDocId() {
  if (typeof _deps.getActiveDocId === 'function') return _deps.getActiveDocId();
  if (typeof window !== 'undefined' && window.documentModule?.getCurrentDocId) {
    return window.documentModule.getCurrentDocId();
  }
  return null;
}

function _getDocs() {
  if (typeof _deps.getDocs === 'function') return _deps.getDocs();
  return new Map();
}

function _getStorage() {
  if (typeof _deps.getStorage === 'function') return _deps.getStorage();
  if (_deps.storage) return _deps.storage;
  try {
    if (typeof localStorage !== 'undefined') return localStorage;
  } catch (_) {}
  return null;
}

function _saveDocument(opts) {
  if (typeof _deps.saveDocument === 'function') return _deps.saveDocument(opts);
  if (typeof window !== 'undefined' && window.documentModule?.saveDocument) {
    return window.documentModule.saveDocument(opts);
  }
}

function _syncHighlighting() {
  if (typeof _deps.syncHighlighting === 'function') return _deps.syncHighlighting();
  if (typeof window !== 'undefined' && window.documentModule?.syncHighlighting) {
    return window.documentModule.syncHighlighting();
  }
}

function _updateLineNumbers(text) {
  if (typeof _deps.updateLineNumbers === 'function') return _deps.updateLineNumbers(text);
  if (typeof window !== 'undefined' && window.documentModule?.updateLineNumbers) {
    return window.documentModule.updateLineNumbers(text);
  }
}

const _escHtml = (s) => {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
};

const _esc = (s) => _escHtml(s);

function _selectSuggestionText(textarea, start, end) {
  if (!textarea || start < 0 || end <= start) return;
  const scrollTop = textarea.scrollTop;
  if (typeof textarea.focus === 'function') {
    textarea.focus({ preventScroll: true });
  }
  if (typeof textarea.setSelectionRange === 'function') {
    textarea.setSelectionRange(start, end, 'forward');
  }
  textarea.scrollTop = scrollTop;
  _suggestionSelectionRange = { start, end };
}

function _clearSuggestionTextSelection() {
  const textarea = _getTextarea();
  const range = _suggestionSelectionRange;
  if (
    textarea
    && range
    && textarea.selectionStart === range.start
    && textarea.selectionEnd === range.end
    && typeof textarea.setSelectionRange === 'function'
  ) {
    textarea.setSelectionRange(range.end, range.end);
  }
  _suggestionSelectionRange = null;
}

/** Persist suggestions to localStorage for the active doc */
function _saveSuggestionsToStorage(docId) {
  const activeDocId = docId || _suggestionDocId || _getActiveDocId();
  if (!activeDocId) return;
  const storage = _getStorage();
  if (!storage) return;
  const data = _activeSuggestions.map(s => ({ id: s.id, find: s.find, replace: s.replace, reason: s.reason }));
  try {
    if (data.length) {
      storage.setItem('odysseus-suggestions-' + activeDocId, JSON.stringify(data));
    } else {
      storage.removeItem('odysseus-suggestions-' + activeDocId);
    }
  } catch (err) {
    console.warn('Failed to save suggestions to storage:', err);
  }
}

/** Release view resources while retaining the originating document's queue. */
export function suspendSuggestions() {
  if (_suggestionDocId) _saveSuggestionsToStorage(_suggestionDocId);
  for (const suggestion of _activeSuggestions) {
    suggestion.cardEl?._cleanup?.();
    suggestion.cardEl?.remove?.();
  }
  _clearInlineDiff();
  _clearSuggestionHighlight();
  _activeSuggestions = [];
  _suggestionTotal = 0;
  _suggestionIndex = 0;
  _suggestionDocId = null;
  ++_suggestionGeneration;
}

/** Restore suggestions from localStorage for a doc */
function _restoreSuggestionsFromStorage(docId) {
  try {
    const id = docId || _getActiveDocId();
    if (!id) return;
    if (_suggestionDocId === id && _activeSuggestions.length) return;
    if (_suggestionDocId !== id) suspendSuggestions();
    _suggestionDocId = id;
    const storage = _getStorage();
    if (!storage) return;
    const raw = storage.getItem('odysseus-suggestions-' + id);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!Array.isArray(data) || !data.length) return;
    _activeSuggestions = data.map(s => ({ id: s.id, find: s.find, replace: s.replace, reason: s.reason, cardEl: null }));
    _suggestionTotal = _activeSuggestions.length;
    _suggestionIndex = 0;
    _showCurrentSuggestion();
  } catch (err) {
    console.warn('Failed to restore suggestions from storage:', err);
  }
}

/** Handle doc_suggestions SSE event - show one suggestion at a time.
 *
 *  If a previous batch is already pending approval, NEW suggestions are
 *  appended to the live queue rather than replacing it. The agent (or a
 *  follow-up batch) can keep adding edits while the user reviews; the count
 *  and "n of m" header update on the fly. */
export async function handleDocSuggestions(data) {
  const isDiffActive = typeof _deps.isDiffModeActive === 'function' ? _deps.isDiffModeActive() : Boolean(_deps.isDiffModeActive);
  if (isDiffActive && typeof _deps.exitDiffMode === 'function') {
    _deps.exitDiffMode(true);
  }
  if (!data || !data.suggestions || !data.suggestions.length) return;

  const docs = _getDocs();
  const activeDocId = _getActiveDocId();
  const isOpen = typeof _deps.isOpen === 'function' ? _deps.isOpen() : Boolean(_deps.isOpen);
  const openPanel = () => { if (typeof _deps.openPanel === 'function') _deps.openPanel(); };
  const loadDocument = (id) => { if (typeof _deps.loadDocument === 'function') return _deps.loadDocument(id); };
  const switchToDoc = (id) => { if (typeof _deps.switchToDoc === 'function') return _deps.switchToDoc(id); };
  const uiModule = _deps.uiModule || (typeof _deps.getUiModule === 'function' ? _deps.getUiModule() : null);

  const openedPanel = !isOpen;
  if (openedPanel) openPanel();
  if (data.doc_id && data.doc_id !== activeDocId) {
    if (!docs.has(data.doc_id)) {
      const activated = await loadDocument(data.doc_id);
      if (activated === false) return;
    }
    else switchToDoc(data.doc_id);
  } else if (data.doc_id && openedPanel && docs.has(data.doc_id)) {
    // openPanel creates a fresh editor shell. Rebind it even when the
    // logical active ID did not change, or the shell remains empty and its
    // first autosave can overwrite the real document.
    switchToDoc(data.doc_id);
  }
  if (data.doc_id && _getActiveDocId() !== data.doc_id) {
    console.error('Could not activate suggestion document:', data.doc_id);
    if (uiModule) uiModule.showError('Could not open the document for these suggestions.');
    return;
  }

  const targetDocId = data.doc_id || _getActiveDocId();
  if (!targetDocId || !docs.has(targetDocId)) return;
  if (_suggestionDocId !== targetDocId) _restoreSuggestionsFromStorage(targetDocId);
  const hadPending = _activeSuggestions.length > 0;
  const existingIds = new Set(_activeSuggestions.map(s => s.id));
  const existingFinds = new Set(_activeSuggestions.map(s => s.find));

  // Append new suggestions, skipping any IDs already in the queue so a
  // re-sent batch doesn't duplicate.
  let added = 0;
  for (const sugg of data.suggestions) {
    if (existingIds.has(sugg.id) || existingFinds.has(sugg.find)) continue;
    _activeSuggestions.push({
      id: sugg.id,
      find: sugg.find,
      replace: sugg.replace,
      reason: sugg.reason,
      cardEl: null,
    });
    existingFinds.add(sugg.find);
    added++;
  }
  _suggestionTotal = (_suggestionTotal || 0) + added;

  _saveSuggestionsToStorage(targetDocId);

  // If nothing was pending before, kick off the visual flow. Otherwise the
  // currently-shown suggestion stays on screen and the queue size update is
  // reflected in the next card's header.
  if (!hadPending) {
    _suggestionIndex = 0;
    _showCurrentSuggestion();
  } else {
    // Refresh just the counter in the active card so the user sees the
    // queue grew while they were deliberating.
    const active = typeof document !== 'undefined' ? document.getElementById('doc-suggestion-active') : null;
    if (active) {
      const counter = active.querySelector('.doc-suggestion-counter');
      if (counter) {
        const num = _suggestionTotal - _activeSuggestions.length + 1;
        counter.textContent = `${num} / ${_suggestionTotal}`;
      }
    }
  }
}

const _positionCard = (card, sugg = _activeSuggestions[0]) => {
  const pane = typeof document !== 'undefined' ? (document.querySelector('.doc-editor-pane') || _getPane()) : null;
  const textarea = typeof document !== 'undefined' ? (document.getElementById('doc-editor-textarea') || _getTextarea()) : null;
  if (!card || !pane) return;
  card.style.zIndex = String(Math.max(topPortalZ(), (parseInt(getComputedStyle(pane).zIndex, 10) || 0) + 1));
  if (typeof window !== 'undefined' && window.innerWidth <= 768) {
    const viewport = window.visualViewport;
    const top = viewport?.offsetTop || 0;
    const height = viewport?.height || window.innerHeight;
    card.style.position = 'fixed';
    card.style.left = ((viewport?.offsetLeft || 0) + 8) + 'px';
    card.style.right = 'auto';
    card.style.width = Math.max(0, (viewport?.width || window.innerWidth) - 16) + 'px';
    card.style.maxHeight = Math.max(0, height - 16) + 'px';
    card.style.top = Math.max(top + 8, top + height - card.offsetHeight - 8) + 'px';
    return;
  }
  if (!textarea || !sugg) return;
  const text = textarea.value || '';
  const idx = text.indexOf(sugg.find);
  if (idx < 0) return;

  const linesBefore = text.substring(0, idx).split('\n').length - 1;
  const lineH = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
  const textareaRect = textarea.getBoundingClientRect();
  const paddingTop = parseFloat(getComputedStyle(textarea).paddingTop) || 10;
  const rawTop = textareaRect.top + paddingTop + (linesBefore * lineH) - textarea.scrollTop;
  const winHeight = typeof window !== 'undefined' ? window.innerHeight : 800;
  const winWidth = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const clampedTop = Math.max(60, Math.min(rawTop, winHeight - 220));
  card.style.position = 'fixed';
  card.style.top = clampedTop + 'px';

  const paneRect = pane.getBoundingClientRect();
  const isMobile = typeof window !== 'undefined' && window.innerWidth <= 768;
  if (!isMobile) {
    if (paneRect.right + 270 < winWidth) {
      card.style.left = (paneRect.right + 16) + 'px';
      card.style.right = '';
    } else {
      card.style.left = '';
      card.style.right = (winWidth - paneRect.left + 16) + 'px';
    }
  }

  // Also position the highlight overlay
  _clearSuggestionHighlight();
  _highlightSuggestionText(sugg.find);
};

  /** Render the current suggestion card (one at a time) + inline diff in document */
  function _showCurrentSuggestion() {
    if (_suggestionDocId && _suggestionDocId !== _getActiveDocId()) return;
    const wrap = (typeof _getWrap === 'function' ? _getWrap() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-wrap') : null);
    const pane = (typeof _getPane === 'function' ? _getPane() : null) || (typeof document !== 'undefined' ? document.querySelector('.doc-editor-pane') : null);
    if (!wrap || !pane) return;

    // Remove previous card and inline diff
    const old = document.getElementById('doc-suggestion-active');
    if (old) { if (old._cleanup) old._cleanup(); old.remove(); }
    _clearSuggestionHighlight();
    _clearInlineDiff();

    if (_activeSuggestions.length === 0) {
      _clearSuggestionTextSelection();
      return;
    }

    const sugg = _activeSuggestions[0];
    const remaining = _activeSuggestions.length;
    const num = _suggestionTotal - remaining + 1;

    const textarea = (typeof _getTextarea === 'function' ? _getTextarea() : null) || (typeof document !== 'undefined' ? document.getElementById('doc-editor-textarea') : null);

    // Scroll to the change text
    if (textarea) {
      const text = textarea.value || '';
      const idx = text.indexOf(sugg.find);
      if (idx >= 0) {
        const lineNum = text.substring(0, idx).split('\n').length - 1;
        const lineH = (typeof getComputedStyle === 'function' ? parseFloat(getComputedStyle(textarea).lineHeight) : 20) || 20;
        const target = Math.max(0, lineNum * lineH - ((textarea.clientHeight || 400) / 3));
        textarea.scrollTop = target;
        _selectSuggestionText(textarea, idx, idx + sugg.find.length);
      }
    }

    // Position card next to the highlighted text
    function _positionCard(card) {
      card.style.zIndex = String(Math.max(topPortalZ(), (parseInt(getComputedStyle(pane).zIndex, 10) || 0) + 1));
      if (window.innerWidth <= 768) {
        const viewport = window.visualViewport;
        const top = viewport?.offsetTop || 0;
        const height = viewport?.height || window.innerHeight;
        card.style.position = 'fixed';
        card.style.left = ((viewport?.offsetLeft || 0) + 8) + 'px';
        card.style.right = 'auto';
        card.style.width = Math.max(0, (viewport?.width || window.innerWidth) - 16) + 'px';
        card.style.maxHeight = Math.max(0, height - 16) + 'px';
        card.style.top = Math.max(top + 8, top + height - card.offsetHeight - 8) + 'px';
        return;
      }
      if (!textarea) return;
      const text = textarea.value;
      const idx = text.indexOf(sugg.find);
      if (idx < 0) return;

      const linesBefore = text.substring(0, idx).split('\n').length - 1;
      const lineH = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
      const textareaRect = textarea.getBoundingClientRect();
      const paddingTop = parseFloat(getComputedStyle(textarea).paddingTop) || 10;
      const rawTop = textareaRect.top + paddingTop + (linesBefore * lineH) - textarea.scrollTop;
      const clampedTop = Math.max(60, Math.min(rawTop, window.innerHeight - 220));
      card.style.position = 'fixed';
      card.style.top = clampedTop + 'px';

      const paneRect = pane.getBoundingClientRect();
      const isMobile = window.innerWidth <= 768;
      if (!isMobile) {
        if (paneRect.right + 270 < window.innerWidth) {
          card.style.left = (paneRect.right + 16) + 'px';
          card.style.right = '';
        } else {
          card.style.left = '';
          card.style.right = (window.innerWidth - paneRect.left + 16) + 'px';
        }
      }

      // Also position the highlight overlay
      _clearSuggestionHighlight();
      _highlightSuggestionText(sugg.find);
    }

    // Build the card
    const card = document.createElement('div');
    card.id = 'doc-suggestion-active';
    card.className = 'doc-suggestion-card';

    card.innerHTML = `
      <div class="doc-suggestion-header">
        <div class="doc-suggestion-nav">
          <button class="doc-suggestion-nav-btn doc-suggestion-prev" title="Previous">&lsaquo;</button>
          <span class="doc-suggestion-counter">${num} / ${_suggestionTotal}</span>
          <button class="doc-suggestion-nav-btn doc-suggestion-next" title="Next">&rsaquo;</button>
        </div>
        <button class="doc-suggestion-close close-btn" title="Close all suggestions" aria-label="Close all suggestions"></button>
      </div>
      <div class="doc-suggestion-reason">${_esc(sugg.reason)}</div>
      <div class="doc-suggestion-actions">
        <button class="doc-suggestion-accept">Accept</button>
        <button class="doc-suggestion-accept-all" title="Apply all ${remaining} pending suggestions" aria-label="Accept all ${remaining} pending suggestions">Accept All</button>
        <button class="doc-suggestion-dismiss">Skip</button>
      </div>
    `;

    // Wire buttons
    card.querySelector('.doc-suggestion-close').addEventListener('click', clearAllSuggestions);
    card.querySelector('.doc-suggestion-prev').addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const current = _activeSuggestions.shift();
      _activeSuggestions.push(current);
      const prev = _activeSuggestions.pop();
      _activeSuggestions.unshift(prev);
      _suggestionIndex = (_suggestionIndex - 1 + _suggestionTotal) % _suggestionTotal;
      _showCurrentSuggestion();
    });
    card.querySelector('.doc-suggestion-next').addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const current = _activeSuggestions.shift();
      _activeSuggestions.push(current);
      _suggestionIndex = (_suggestionIndex + 1) % _suggestionTotal;
      _showCurrentSuggestion();
    });
    card.querySelector('.doc-suggestion-accept').addEventListener('click', () => {
      if (!_applySuggestions([sugg]).length) return;
      _activeSuggestions.shift();
      _animateNext();
    });
    card.querySelector('.doc-suggestion-dismiss').addEventListener('click', () => {
      _activeSuggestions.shift();
      _animateNext();
    });
    const acceptAllBtn = card.querySelector('.doc-suggestion-accept-all');
    if (acceptAllBtn) {
      acceptAllBtn.addEventListener('click', () => {
        const applied = new Set(_applySuggestions(_activeSuggestions));
        if (!applied.size) return;
        _activeSuggestions = _activeSuggestions.filter(s => !applied.has(s.id));
        _animateNext();
      });
    }

    sugg.cardEl = card;
    document.body.appendChild(card);

    // Position after a tick so scroll has taken effect
    requestAnimationFrame(() => _positionCard(card));

    // Reposition on scroll/resize so the card stays anchored
    const _reposition = () => { if (card.isConnected) _positionCard(card); };
    if (textarea) textarea.addEventListener('scroll', _reposition);
    window.addEventListener('resize', _reposition);
    window.visualViewport?.addEventListener('resize', _reposition);
    window.visualViewport?.addEventListener('scroll', _reposition);
    // Store cleanup refs on the card
    card._cleanup = () => {
      if (textarea) textarea.removeEventListener('scroll', _reposition);
      window.removeEventListener('resize', _reposition);
      window.visualViewport?.removeEventListener('resize', _reposition);
      window.visualViewport?.removeEventListener('scroll', _reposition);
    };
  }

  /** Show inline diff by modifying the code highlight element directly */
  function _showInlineDiff(findText, replaceText) {
    const codeEl = _getCodeEl();
    const textarea = _getTextarea();
    if (!codeEl || !textarea) return;

    const text = textarea.value || '';
    const idx = text.indexOf(findText);
    if (idx === -1) return;

    const before = text.substring(0, idx);
    const after = text.substring(idx + findText.length);

    // Character-level diff
    let cPre = 0;
    while (cPre < findText.length && cPre < replaceText.length && findText[cPre] === replaceText[cPre]) cPre++;
    let cSuf = 0;
    while (cSuf < (findText.length - cPre) && cSuf < (replaceText.length - cPre) &&
           findText[findText.length - 1 - cSuf] === replaceText[replaceText.length - 1 - cSuf]) cSuf++;

    const commonBefore = findText.substring(0, cPre);
    const commonAfter = findText.substring(findText.length - cSuf);
    const delPart = findText.substring(cPre, findText.length - cSuf);
    const addPart = replaceText.substring(cPre, replaceText.length - cSuf);

    // Replace codeEl content with diff-marked version
    codeEl.innerHTML = '';
    codeEl.appendChild(document.createTextNode(before));
    if (commonBefore) codeEl.appendChild(document.createTextNode(commonBefore));

    if (delPart) {
      const del = document.createElement('span');
      del.className = 'sugg-inline-del';
      del.textContent = delPart;
      codeEl.appendChild(del);
    }
    if (addPart) {
      const add = document.createElement('span');
      add.className = 'sugg-inline-add';
      add.textContent = addPart;
      codeEl.appendChild(add);
    }

    if (commonAfter) codeEl.appendChild(document.createTextNode(commonAfter));
    codeEl.appendChild(document.createTextNode(after + '\n'));

    // Mark that we have an active diff so syncHighlighting doesn't overwrite it
    if (codeEl.dataset) codeEl.dataset.hasDiff = '1';
  }

  /** Clear inline diff - restore normal highlighting */
  function _clearInlineDiff() {
    const codeEl = _getCodeEl();
    if (codeEl && codeEl.dataset && codeEl.dataset.hasDiff) {
      delete codeEl.dataset.hasDiff;
      _syncHighlighting();
    }
  }

  /** Apply exact suggestion matches to the active editor surface, then save once. */
  function _applySuggestions(suggestions) {
    if (_suggestionDocId && _suggestionDocId !== _getActiveDocId()) return [];
    const activeDocId = _getActiveDocId();
    const docs = _getDocs();
    if (!activeDocId || !docs.has(activeDocId)) return [];
    // The textarea is only a hidden mirror for rich-text and email documents.
    // Capture the visible editor first, then replace in the document source.
    if (typeof _deps.saveCurrentToMap === 'function') {
      _deps.saveCurrentToMap();
    }
    const doc = docs.get(activeDocId);
    let content = doc.content || '';
    const applied = [];
    for (const sugg of suggestions) {
      if (!sugg.find || content.split(sugg.find).length !== 2) continue;
      content = content.replace(sugg.find, sugg.replace || '');
      applied.push(sugg.id);
    }
    if (!applied.length) {
      const ui = _deps.uiModule || (typeof _deps.getUiModule === 'function' ? _deps.getUiModule() : null);
      if (ui?.showError) {
        ui.showError('Suggestion no longer matches the document. Refresh suggestions to review it.');
      } else if (typeof _deps.showError === 'function') {
        _deps.showError('Suggestion no longer matches the document. Refresh suggestions to review it.');
      }
      return [];
    }
    doc.content = content;
    const textarea = _getTextarea();
    const isRich = typeof _deps.isRichTextLang === 'function' ? _deps.isRichTextLang(doc.language) : false;
    const isRender = typeof _deps.isRenderLang === 'function' ? _deps.isRenderLang(doc.language) : false;
    const htmlPreviewActive = typeof _deps.isHtmlPreviewActive === 'function' ? _deps.isHtmlPreviewActive() : Boolean(_deps.htmlPreviewActive);

    if (isRich && typeof _deps.showRichTextEditor === 'function') {
      _deps.showRichTextEditor(doc);
    } else if (doc.language === 'email' && typeof _deps.showEmailFields === 'function') {
      _deps.showEmailFields(doc, { applyLocalDraft: false, forceHeaderFields: true });
    } else if (textarea) {
      textarea.value = content;
      _syncHighlighting();
      if (typeof _deps.refreshVisualMarkdownIfActive === 'function') {
        _deps.refreshVisualMarkdownIfActive(doc);
      }
      if (typeof _deps.refreshMarkdownPreviewIfVisible === 'function') {
        _deps.refreshMarkdownPreviewIfVisible(doc.id, content);
      }
      if (htmlPreviewActive && isRender && typeof document !== 'undefined') {
        const iframe = document.getElementById('doc-html-preview');
        if (iframe && typeof _deps.themedRenderSrcdoc === 'function') {
          iframe.srcdoc = _deps.themedRenderSrcdoc(content, doc.language);
        }
      }
    }
    _saveDocument({ silent: true });
    return applied;
  }

  /** Animate transition to next suggestion */
  function _animateNext() {
    _saveSuggestionsToStorage();
    const old = typeof document !== 'undefined' ? document.getElementById('doc-suggestion-active') : null;
    if (old) {
      if (old._cleanup) old._cleanup();
      old.style.transition = 'opacity 0.15s, transform 0.15s';
      old.style.opacity = '0';
      old.style.transform = 'translateY(-10px)';
      const generation = _suggestionGeneration;
      setTimeout(() => {
        old.remove();
        if (generation === _suggestionGeneration) _showCurrentSuggestion();
      }, 150);
    } else {
      _showCurrentSuggestion();
    }
  }

  /** Accept a suggestion - apply the edit */
  function acceptSuggestion(id) {
    const sugg = _activeSuggestions.find(s => s.id === id);
    if (!sugg) return;

    const textarea = _getTextarea();
    if (textarea && sugg.find && typeof textarea.value === 'string' && textarea.value.includes(sugg.find)) {
      textarea.value = textarea.value.replace(sugg.find, sugg.replace);
      const docId = _getActiveDocId();
      const doc = docId ? _getDocs().get(docId) : null;
      if (doc && typeof _deps.refreshVisualMarkdownIfActive === 'function') {
        _deps.refreshVisualMarkdownIfActive(doc);
      }
      _syncHighlighting();
      _saveDocument({ silent: true });
    }

    // Animate card out
    if (sugg.cardEl && sugg.cardEl.style) {
      sugg.cardEl.style.transition = 'opacity 0.2s, transform 0.2s';
      sugg.cardEl.style.opacity = '0';
      sugg.cardEl.style.transform = 'translateX(10px)';
      setTimeout(() => sugg.cardEl?.remove(), 200);
    }

    _activeSuggestions = _activeSuggestions.filter(s => s.id !== id);
    _clearSuggestionHighlight();

    // Remove container if empty
    if (_activeSuggestions.length === 0 && typeof document !== 'undefined') {
      const container = document.getElementById('doc-suggestions-container');
      if (container) container.style.display = 'none';
    }
  }

  /** Dismiss a suggestion - just remove the card */
  function dismissSuggestion(id) {
    const sugg = _activeSuggestions.find(s => s.id === id);
    if (!sugg) return;

    if (sugg.cardEl && sugg.cardEl.style) {
      sugg.cardEl.style.transition = 'opacity 0.15s';
      sugg.cardEl.style.opacity = '0';
      setTimeout(() => sugg.cardEl?.remove(), 150);
    }

    _activeSuggestions = _activeSuggestions.filter(s => s.id !== id);
    _clearSuggestionHighlight();

    if (_activeSuggestions.length === 0 && typeof document !== 'undefined') {
      const container = document.getElementById('doc-suggestions-container');
      if (container) container.style.display = 'none';
    }
  }

  /** Clear all suggestion cards */
  function clearAllSuggestions() {
    _activeSuggestions = [];
    _suggestionTotal = 0;
    _saveSuggestionsToStorage();
    _clearSuggestionHighlight();
    _clearSuggestionTextSelection();
    _clearInlineDiff();
    if (typeof document !== 'undefined') {
      const old = document.getElementById('doc-suggestion-active');
      if (old) { if (old._cleanup) old._cleanup(); old.remove(); }
      const container = document.getElementById('doc-suggestions-container');
      if (container) { container.innerHTML = ''; container.style.display = 'none'; }
    }
    // Restore line numbers
    const ta = _getTextarea();
    if (ta && ta.value) _updateLineNumbers(ta.value);
  }

  /** Highlight the exact text referenced by the active suggestion. */
  function _highlightSuggestionText(findText) {
    _clearSuggestionHighlight();
    const textarea = _getTextarea();
    const wrap = _getWrap();
    if (!textarea || !wrap || typeof getComputedStyle !== 'function' || typeof document === 'undefined') return;

    const text = textarea.value || '';
    const idx = text.indexOf(findText);
    if (idx === -1) return;

    const style = getComputedStyle(textarea);
    const lineHeight = parseFloat(style.lineHeight) || 20;

    let mirror = document.getElementById('doc-selection-mirror');
    if (!mirror) {
      mirror = document.createElement('div');
      mirror.id = 'doc-selection-mirror';
      mirror.style.cssText = 'position:absolute;top:0;left:0;right:0;visibility:hidden;pointer-events:none;' +
        'white-space:pre-wrap;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;box-sizing:border-box;';
      wrap.appendChild(mirror);
    }
    mirror.style.font = style.font;
    mirror.style.padding = style.padding;
    mirror.style.borderWidth = style.borderWidth;
    mirror.style.borderStyle = 'solid';
    mirror.style.borderColor = 'transparent';
    mirror.style.width = (textarea.clientWidth || 800) + 'px';
    mirror.style.tabSize = style.tabSize;
    mirror.style.letterSpacing = style.letterSpacing;
    mirror.style.wordSpacing = style.wordSpacing;
    mirror.style.textIndent = style.textIndent;

    const startPos = _measurePos(mirror, text, idx);
    const endPos = _measurePos(mirror, text, idx + findText.length);
    mirror.innerHTML = '';

    const addRect = (top, left, width, height) => {
      const highlight = document.createElement('div');
      highlight.className = 'doc-suggestion-highlight';
      highlight.style.top = (top - (textarea.scrollTop || 0)) + 'px';
      highlight.style.left = left + 'px';
      if (width == null) highlight.style.right = '8px';
      else highlight.style.width = Math.max(width, 2) + 'px';
      highlight.style.height = height + 'px';
      wrap.appendChild(highlight);
    };

    if (Math.abs(endPos.y - startPos.y) < 1) {
      addRect(startPos.y, startPos.x, endPos.x - startPos.x, lineHeight);
    } else {
      addRect(startPos.y, startPos.x, null, lineHeight);
      const middleTop = startPos.y + lineHeight;
      const middleHeight = endPos.y - middleTop;
      if (middleHeight > 0) addRect(middleTop, parseFloat(style.paddingLeft) || 0, null, middleHeight);
      addRect(endPos.y, parseFloat(style.paddingLeft) || 0, endPos.x - (parseFloat(style.paddingLeft) || 0), lineHeight);
    }

    // Don't auto-scroll here - caller handles scrolling
  }

  /** Remove hover highlight */
  function _clearSuggestionHighlight() {
    const wrap = _getWrap();
    if (wrap && typeof wrap.querySelectorAll === 'function') {
      wrap.querySelectorAll('.doc-suggestion-highlight').forEach(el => el.remove());
    }
  }

  function _measurePos(mirror, text, pos) {
    if (typeof _deps.measurePos === 'function') {
      return _deps.measurePos(mirror, text, pos);
    }
    if (!mirror || typeof document === 'undefined') return { x: 0, y: 0 };
    mirror.innerHTML = '';
    if (pos > 0) mirror.appendChild(document.createTextNode(text.substring(0, pos)));
    const marker = document.createElement('span');
    marker.textContent = '\u200b';
    mirror.appendChild(marker);
    const r = marker.getBoundingClientRect();
    const m = mirror.getBoundingClientRect();
    return { x: r.left - m.left, y: r.top - m.top };
  }

export function initSuggestions(options = {}) {
  _deps = { ..._deps, ...options };
}

export function getActiveSuggestions() {
  return _activeSuggestions;
}

export function setActiveSuggestions(list) {
  _activeSuggestions = Array.isArray(list) ? list : [];
}

export function getSuggestionTotal() {
  return _suggestionTotal;
}

export function setSuggestionTotal(num) {
  _suggestionTotal = typeof num === 'number' ? num : 0;
}

export function getSuggestionIndex() {
  return _suggestionIndex;
}

export function setSuggestionIndex(idx) {
  _suggestionIndex = typeof idx === 'number' ? idx : 0;
}

export {
  _activeSuggestions,
  _suggestionSelectionRange,
  _suggestionTotal,
  _suggestionIndex,
  _selectSuggestionText,
  _clearSuggestionTextSelection,
  _saveSuggestionsToStorage,
  _restoreSuggestionsFromStorage,
  _positionCard,
  _showCurrentSuggestion,
  _showInlineDiff,
  _clearInlineDiff,
  _applySuggestions,
  _animateNext,
  acceptSuggestion,
  dismissSuggestion,
  clearAllSuggestions,
  _highlightSuggestionText,
  _clearSuggestionHighlight,
  _measurePos,
  _esc,
  _escHtml,
};

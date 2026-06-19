// static/js/document.js
/**
 * Document editor module — multi-document tabbed panel alongside chat.
 * Supports multiple open documents with tab switching, per-doc state,
 * and theme-aware styling.
 */


import uiModule from './ui.js';
import sessionModule from './sessions.js';
import emojiPicker from './emojiPicker.js';
import markdownModule from './markdown.js';
import codeRunnerModule from './codeRunner.js';
import { langIcon } from './langIcons.js';
import spinnerModule from './spinner.js';
import { openLibrary, closeLibrary, isLibraryOpen, initLibrary } from './documentLibrary.js';
import signatureModule from './signature.js';
import * as Modals from './modalManager.js';

  let API_BASE = '';
  let isOpen = false;
  let _hlDebounce = null;
  let _isEditingTabTitle = false;
  let _autoDetectDebounce = null;
  let _autoTitleDebounce = null;
  let _autoSaveDebounce = null;
  let _animationInProgress = false;
  let _animationCancel = null;      // function to cancel current animation
  let _htmlPreviewActive = false;   // true when inline HTML preview iframe is showing
  let _emailAccountsCache = null;
  let _emailAccountsCacheAt = 0;
  let _emailHeaderManualExpandUntil = 0;

  // Diff mode state
  let _diffModeActive = false;
  let _diffOldContent = null;
  let _diffNewContent = null;
  let _diffChunks = [];          // [{id, oldLines, newLines, startLine, resolved, accepted}]
  let _diffUnresolvedCount = 0;

  // Language auto-detection config
  const AUTO_DETECT_DELAY = 500;
  const AUTO_DETECT_MIN_CHARS = 30;
  const AUTO_DETECT_MIN_RELEVANCE = 8;
  const AUTO_DETECT_SAMPLE_SIZE = 2000;
  const HLJS_TO_DROPDOWN = {
    python: 'python', javascript: 'javascript', typescript: 'typescript',
    xml: 'html', html: 'html', css: 'css', markdown: 'markdown',
    json: 'json', yaml: 'yaml', bash: 'bash', shell: 'bash',
    sql: 'sql', rust: 'rust', go: 'go', java: 'java', c: 'c', cpp: 'cpp',
    csv: 'csv',
  };

  // Languages rendered in the sandboxed preview iframe. SVG and XML markup
  // render as inline content in an HTML document, so they share the HTML
  // "Run / Preview" path. (hljs maps detected `xml` → `html` already; this also
  // covers the doc being explicitly typed svg/xml.)
  const _isRenderLang = (l) => ['html', 'svg', 'xml'].includes((l || '').toLowerCase());
  // Languages that get the segmented Code / Run-or-View toggle in the toolbar
  // (the same UX as markdown's Edit / Preview switch). CSV's "run" view is the
  // table; Python/JS/etc.'s is the code-run output; HTML/SVG/XML render via
  // the iframe preview.
  const _hasViewToggle = (l) => {
    const lang = (l || '').toLowerCase();
    return [
      'csv', 'python', 'javascript', 'typescript', 'bash', 'sh', 'shell',
      'php', 'ruby', 'sql', 'java', 'go', 'rust',
      'c', 'cpp', 'c++', 'csharp', 'c#',
      'yaml', 'json', 'css',
      'ini', 'toml',
    ].includes(lang) || _isRenderLang(lang);
  };

  async function _getEmailAccountsCached() {
    const now = Date.now();
    if (_emailAccountsCache && (now - _emailAccountsCacheAt) < 30000) return _emailAccountsCache;
    try {
      const res = await fetch(`${API_BASE}/api/email/accounts`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('accounts failed');
      const data = await res.json();
      _emailAccountsCache = Array.isArray(data.accounts) ? data.accounts : [];
    } catch (_) {
      _emailAccountsCache = [];
    }
    _emailAccountsCacheAt = now;
    return _emailAccountsCache;
  }

  function _accountCanSend(account) {
    if (!account || !account.smtp_host || !account.smtp_user) return false;
    return !!(account.has_smtp_password || account.oauth_provider);
  }

  async function _resolveComposeSendAccountId() {
    const activeAccountId = window.__odysseusActiveEmailAccount || null;
    if (!activeAccountId) return null;
    const accounts = await _getEmailAccountsCached();
    const activeAccount = accounts.find(a => String(a.id) === String(activeAccountId));
    if (!activeAccount || _accountCanSend(activeAccount)) return activeAccountId;
    if (uiModule) uiModule.showToast('Selected email account is receive-only; using your SMTP account.');
    return null;
  }

  // Inject tab menu styles immediately (must exist before any hover)
  {
    const s = document.createElement('style');
    s.id = 'doc-tab-menu-styles';
    s.textContent = `.doc-tab-menu-btn{background:none!important;border:none!important;outline:none!important;box-shadow:none!important;color:var(--fg);opacity:0.25;cursor:pointer;padding:2px 4px!important;height:auto!important;line-height:1;transition:opacity .15s;flex-shrink:0;-webkit-appearance:none;appearance:none}.doc-tab-menu-btn:focus,.doc-tab-menu-btn:active{outline:none!important;box-shadow:none!important;background:none!important}.doc-tab:hover .doc-tab-menu-btn{opacity:.5}.doc-tab-menu-btn:hover{opacity:1!important}.doc-tab-dropdown .dropdown-item-compact{padding:6px 8px;border-radius:6px;cursor:pointer;white-space:nowrap;border-bottom:none;display:flex;align-items:center;gap:10px;font-size:11px}.doc-tab-dropdown .dropdown-item-compact:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}.doc-tab-dropdown .dropdown-item-compact .dropdown-icon{width:14px;height:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0;opacity:0.5}.doc-tab-dropdown .dropdown-divider{height:1px;margin:3px 0;background:color-mix(in srgb,var(--border) 40%,transparent)}.doc-tab-action-delete{color:var(--red,#e06c75)!important}.doc-tab-action-delete .dropdown-icon{opacity:0.7!important}`;
    document.head.appendChild(s);
  }

  // Multi-document state
  let activeDocId = null;           // currently visible doc
  let _lastSessionId = '';          // session context for "+" button
  const docs = new Map();           // docId -> { id, title, language, content, version, sessionId }

  const _docOpenKey = (sessionId) => 'odysseus-doc-open-' + sessionId;
  const _docMinimizedKey = (sessionId) => 'odysseus-doc-minimized-' + sessionId;

  function _markDocVisibleState(sessionId, state) {
    if (!sessionId) return;
    if (state === 'open') {
      localStorage.setItem(_docOpenKey(sessionId), '1');
      localStorage.removeItem(_docMinimizedKey(sessionId));
    } else if (state === 'minimized') {
      localStorage.removeItem(_docOpenKey(sessionId));
      localStorage.setItem(_docMinimizedKey(sessionId), '1');
    } else {
      localStorage.removeItem(_docOpenKey(sessionId));
      localStorage.removeItem(_docMinimizedKey(sessionId));
    }
  }

  /** Switch chat to agent mode if not already */
  function _ensureAgentMode() {
    const ab = document.getElementById('mode-agent-btn');
    const cb = document.getElementById('mode-chat-btn');
    if (ab && !ab.classList.contains('active')) {
      ab.click();
    }
  }

  export function init(apiBase) {
    API_BASE = apiBase;
    initLibrary({
      apiBase,
      esc: _esc,
      getDocs: () => docs,
      isOpen: () => isOpen,
      createDocument,
      loadDocument,
      switchToDoc,
      openPanel,
      addDocToTabs,
      syncDocIndicator: _syncDocIndicator,
    });
    _maybeOpenDocFromHash();
    window.addEventListener('hashchange', _maybeOpenDocFromHash);
  }

  /** Update overflow-doc-btn accent indicator, toolbar indicator, and session list icon */
  function _syncDocIndicator() {
    const btn = document.getElementById('overflow-doc-btn');
    // Has docs = at least one non-empty doc in the map
    const hasDocs = docs.size > 0;
    if (btn) btn.classList.toggle('has-docs', hasDocs);
    // Show/hide the toolbar doc indicator when docs exist
    const indicator = document.getElementById('doc-indicator-btn');
    if (indicator) indicator.classList.toggle('visible', hasDocs);
    // Hide overflow menu item when indicator is shown outside
    if (btn) btn.style.display = hasDocs ? 'none' : '';
    // Update session list icon
    const sid = sessionModule?.getCurrentSessionId();
    if (sid && sessionModule.setSessionHasDocs) {
      sessionModule.setSessionHasDocs(sid, hasDocs);
    }
  }

  // ---- Tab bar rendering ----

  function updateArrowVisibility(scrollArea, leftBtn, rightBtn) {
    const atLeft = scrollArea.scrollLeft <= 0;
    const atRight = scrollArea.scrollLeft + scrollArea.clientWidth >= scrollArea.scrollWidth - 1;
    leftBtn.style.display = atLeft ? 'none' : '';
    rightBtn.style.display = atRight ? 'none' : '';
    // Toggle the edge-mask classes so the fade gradient drops to flat on the
    // side that has nothing to scroll to. Without this the left/right fade
    // reads as a permanent shadow even when no arrow is showing.
    scrollArea.classList.toggle('is-at-left', atLeft);
    scrollArea.classList.toggle('is-at-right', atRight);
  }

  // Mobile swipe-to-dismiss for the doc sheet. Mirrors the shared bottom-sheet
  // gesture in ui.js (finger-following drag, velocity-based dismiss, rubber-band
  // on up-drag, spring snap-back) so it feels identical to the other windows —
  // but dismisses through the doc panel's own closePanel() lifecycle.
  function _wireSwipeDismiss(el) {
    if (!el) return;
    const DISMISS_THRESHOLD = 50;    // px
    const VELOCITY_THRESHOLD = 0.3;  // px/ms — fast flick dismisses below threshold
    const RUBBER_RESISTANCE = 0.35;  // resistance when dragging up past origin
    let startY = 0, startX = 0, lastY = 0, lastT = 0, velocity = 0;
    let dragging = false, cancelled = false;
    const getPane = () => document.getElementById('doc-editor-pane');
    let pane = null;

    el.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 768 || e.touches.length !== 1) return;
      pane = getPane();
      if (!pane) return;
      const t = e.touches[0];
      startY = t.clientY; startX = t.clientX; lastY = startY; lastT = e.timeStamp;
      velocity = 0; dragging = false; cancelled = false;
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (cancelled || !pane || window.innerWidth > 768) return;
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - startX);
      const dy = t.clientY - startY;
      if (!dragging) {
        if (dx > 40 && dx > Math.abs(dy) * 2) { cancelled = true; return; } // horizontal → tab scroll
        if (Math.abs(dy) > 8) {
          dragging = true;
          // Clear the open animation — its `both` fill-mode otherwise pins
          // transform and overrides our inline finger-following transform.
          pane.style.animation = 'none';
          pane.style.transition = 'none';
          pane.style.willChange = 'transform';
        } else return;
      }
      const dt = e.timeStamp - lastT;
      if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
      lastY = t.clientY; lastT = e.timeStamp;
      e.preventDefault();
      pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER_RESISTANCE}px)`;
    }, { passive: false });

    const endSwipe = () => {
      if (!dragging || !pane) { pane = null; return; }
      const p = pane; pane = null; dragging = false;
      p.style.willChange = '';
      const dy = lastY - startY;
      const shouldDismiss = dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD);
      if (shouldDismiss) {
        closePanel('down');
      } else {
        p.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
        p.style.transform = '';
        setTimeout(() => { p.style.transition = ''; }, 260);
      }
    };
    el.addEventListener('touchend', endSwipe, { passive: true });
    el.addEventListener('touchcancel', endSwipe, { passive: true });
  }

  function renderTabs() {
    if (_isEditingTabTitle) return;  // Don't rebuild while editing a title
    const tabBar = document.getElementById('doc-tab-bar');
    if (!tabBar) return;

    // Build tab HTML with scroll arrows
    // When doc panel is on right (default), + goes on far left; on left, + goes inside scroll area
    const paneEl = document.querySelector('.doc-editor-pane');
    const isDocLeft = paneEl && paneEl.classList.contains('doc-left');
    let html = '';
    html += '<button class="doc-tab-arrow doc-tab-arrow-left" id="doc-tab-left" title="Scroll left">&#x2039;</button>';
    html += '<div class="doc-tab-scroll" id="doc-tab-scroll">';
    const curSession = sessionModule?.getCurrentSessionId() || '';
    let _anyTab = false;
    for (const [id, doc] of docs) {
      // Only show tabs for the current session
      if (doc.sessionId && curSession && doc.sessionId !== curSession) continue;
      _anyTab = true;
      const isActive = id === activeDocId;
      const title = doc.title || 'Untitled';
      const shortTitle = title.length > 24 ? title.slice(0, 22) + '...' : title;
      const menuBtn = `<button class="doc-tab-menu-btn" data-doc-id="${id}" title="Document actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2.5"/><circle cx="12" cy="12" r="2.5"/><circle cx="12" cy="19" r="2.5"/></svg></button>`;
      const ver = doc.version || doc.version_count || 1;
      const verChip = `<span class="doc-tab-version" data-doc-id="${id}" title="Version history">v${ver}</span>`;
      // Language icon before the title — same family as the meta-line / picker
      // icons. Hidden via :empty CSS when the doc has no useful language.
      const lic = (doc.language && doc.language !== 'text')
        ? langIcon(doc.language, 12, { style: 'opacity:0.65;flex-shrink:0;color:currentColor;margin-right:4px;' })
        : '';
      const langChip = `<span class="doc-tab-lang">${lic}</span>`;
      html += `<div class="doc-tab${isActive ? ' active' : ''}" draggable="true" data-doc-id="${id}" title="${_esc(title)}">
        ${verChip}${langChip}<span class="doc-tab-title">${_esc(shortTitle)}</span>
        <button class="doc-tab-close" data-doc-id="${id}" title="Unlink from chat (kept in the Library)">&times;</button>
      </div>`;
    }
    // Empty state (panel open, no doc yet): show a ghost "Untitled" tab so it's
    // obvious you're in a fresh document rather than staring at a blank pane.
    if (!_anyTab && isOpen && !activeDocId) {
      html += `<div class="doc-tab active doc-tab-ghost" title="New document — start typing"><span class="doc-tab-title">Untitled</span></div>`;
    }
    html += `<button class="doc-tab-new" id="doc-tab-new-btn" title="New document"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>`;
    html += '</div>';
    html += '<button class="doc-tab-arrow doc-tab-arrow-right" id="doc-tab-right" title="Scroll right">&#x203A;</button>';
    tabBar.innerHTML = html;

    // Wire scroll arrows
    const scrollArea = document.getElementById('doc-tab-scroll');
    const leftBtn = document.getElementById('doc-tab-left');
    const rightBtn = document.getElementById('doc-tab-right');
    if (scrollArea && leftBtn && rightBtn) {
      leftBtn.addEventListener('click', () => scrollArea.scrollBy({ left: -120, behavior: 'smooth' }));
      rightBtn.addEventListener('click', () => scrollArea.scrollBy({ left: 120, behavior: 'smooth' }));
      updateArrowVisibility(scrollArea, leftBtn, rightBtn);
      scrollArea.addEventListener('scroll', () => updateArrowVisibility(scrollArea, leftBtn, rightBtn));
    }

    // Mobile: the tab bar doubles as a drag zone — swipe down to dismiss.
    if (!tabBar._swipeWired) { tabBar._swipeWired = true; _wireSwipeDismiss(tabBar); }

    // Bring the clicked tab fully into view — the scroll area has an 18px
    // fade-mask at each edge plus the < / > arrow buttons; without this, the
    // rightmost tab stays partially under the fade so the user can't see its
    // close button or version chip.
    const _scrollTabIntoView = (tab, behavior = 'smooth') => {
      const sa = document.getElementById('doc-tab-scroll');
      if (!sa || !tab) return;
      const EDGE_PAD = 30;
      const tabLeft = tab.offsetLeft;
      const tabRight = tabLeft + tab.offsetWidth;
      const visLeft = sa.scrollLeft + EDGE_PAD;
      const visRight = sa.scrollLeft + sa.clientWidth - EDGE_PAD;
      if (tabRight > visRight) {
        sa.scrollTo({ left: sa.scrollLeft + tabRight - visRight, behavior });
      } else if (tabLeft < visLeft) {
        sa.scrollTo({ left: Math.max(0, sa.scrollLeft + tabLeft - visLeft), behavior });
      }
    };
    // Wire tab clicks (delayed to allow dblclick on title)
    let _tabClickTimer = null;
    tabBar.querySelectorAll('.doc-tab').forEach(tab => {
      tab.addEventListener('click', (e) => {
        // Check if click was on or inside the close/play button
        if (e.target.closest('.doc-tab-close') || e.target.closest('.doc-tab-play') || e.target.closest('.doc-tab-menu-btn') || e.target.closest('.doc-tab-version')) return;
        if (_isEditingTabTitle) return;
        // If clicking the title span, delay to allow dblclick
        if (e.target.classList.contains('doc-tab-title')) {
          clearTimeout(_tabClickTimer);
          _tabClickTimer = setTimeout(() => { switchToDoc(tab.dataset.docId); _scrollTabIntoView(tab); }, 250);
        } else {
          switchToDoc(tab.dataset.docId);
          _scrollTabIntoView(tab);
        }
      });
      tab.addEventListener('dblclick', (e) => {
        clearTimeout(_tabClickTimer);
        const titleSpan = tab.querySelector('.doc-tab-title');
        if (!titleSpan) return;
        e.stopPropagation();
        const docId = tab.dataset.docId;
        const doc = docs.get(docId);
        if (!doc) return;
        startTitleEdit(titleSpan, docId, doc);
      });
    });

    // Wire close buttons — use delegation from tab bar for reliability
    // Remove previous handler to prevent accumulation across renderTabs calls
    if (tabBar._closeHandler) tabBar.removeEventListener('click', tabBar._closeHandler);
    tabBar._closeHandler = (e) => {
      const verBtn = e.target.closest('.doc-tab-version');
      if (verBtn) {
        e.stopPropagation();
        const docId = verBtn.dataset.docId;
        if (docId) { if (docId !== activeDocId) switchToDoc(docId); toggleVersionHistory(); }
        return;
      }
      const playBtn = e.target.closest('.doc-tab-play');
      if (playBtn) {
        e.stopPropagation();
        const docId = playBtn.dataset.docId;
        if (docId) {
          if (docId !== activeDocId) switchToDoc(docId);
          toggleHtmlPreview();
        }
        return;
      }
      const menuBtnEl = e.target.closest('.doc-tab-menu-btn');
      if (menuBtnEl) {
        e.stopPropagation();
        const docId = menuBtnEl.dataset.docId;
        if (docId) showDocTabMenu(menuBtnEl, docId);
        return;
      }
      const closeBtn = e.target.closest('.doc-tab-close');
      if (!closeBtn) return;
      e.stopPropagation();
      const docId = closeBtn.dataset.docId;
      if (docId) closeTab(docId);
    };
    tabBar.addEventListener('click', tabBar._closeHandler);

    // Wire drag-to-reorder
    initTabDragReorder(tabBar);

    // Wire new doc button
    const newBtn = document.getElementById('doc-tab-new-btn');
    if (newBtn) {
      newBtn.addEventListener('click', async () => {
        let sessionId = docs.get(activeDocId)?.sessionId
          || _lastSessionId
          || (sessionModule && sessionModule.getCurrentSessionId());
        if (!sessionId) {
          try {
            sessionId = await _autoCreateSession();
          } catch (e) {
            console.error('Failed to auto-create session for document:', e);
            return;
          }
        }
        createDocument(sessionId);
      });
    }

    // Scroll active tab into view after DOM is laid out
    requestAnimationFrame(() => {
      const at = document.getElementById('doc-tab-scroll')?.querySelector('.doc-tab.active');
      _scrollTabIntoView(at, 'auto');
    });
  }

  /** Start inline editing of a tab title */
  function startTitleEdit(titleSpan, docId, doc) {
    if (_isEditingTabTitle) return;
    _isEditingTabTitle = true;

    const fullTitle = doc.title || '';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'doc-tab-title-input';
    input.value = fullTitle;

    titleSpan.replaceWith(input);
    input.focus();
    input.select();

    function commitEdit() {
      if (!_isEditingTabTitle) return;
      const newTitle = input.value.trim();
      _isEditingTabTitle = false;
      doc.title = newTitle;
      if (docId === activeDocId) {
        const titleInput = document.getElementById('doc-title-input');
        if (titleInput) titleInput.value = newTitle;
      }
      updateTitle(docId, newTitle);
      renderTabs();
    }

    function cancelEdit() {
      _isEditingTabTitle = false;
      renderTabs();
    }

    input.addEventListener('blur', commitEdit);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        input.removeEventListener('blur', commitEdit);
        commitEdit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        input.removeEventListener('blur', commitEdit);
        cancelEdit();
      }
    });
  }

  /** Drag-to-reorder tabs */
  function initTabDragReorder(tabBar) {
    let dragId = null;

    tabBar.querySelectorAll('.doc-tab').forEach(tab => {
      tab.addEventListener('dragstart', (e) => {
        dragId = tab.dataset.docId;
        tab.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });

      tab.addEventListener('dragend', () => {
        tab.classList.remove('dragging');
        dragId = null;
        tabBar.querySelectorAll('.doc-tab').forEach(t => t.classList.remove('drag-over'));
      });

      tab.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (tab.dataset.docId !== dragId) {
          tab.classList.add('drag-over');
        }
      });

      tab.addEventListener('dragleave', () => {
        tab.classList.remove('drag-over');
      });

      tab.addEventListener('drop', (e) => {
        e.preventDefault();
        tab.classList.remove('drag-over');
        const targetId = tab.dataset.docId;
        if (!dragId || dragId === targetId) return;

        // Reorder the docs Map: move dragId before targetId
        const entries = [...docs.entries()];
        const fromIdx = entries.findIndex(([k]) => k === dragId);
        const toIdx = entries.findIndex(([k]) => k === targetId);
        if (fromIdx === -1 || toIdx === -1) return;

        const [moved] = entries.splice(fromIdx, 1);
        entries.splice(toIdx, 0, moved);

        docs.clear();
        for (const [k, v] of entries) docs.set(k, v);

        renderTabs();
      });
    });
  }

  /** Show empty state when no documents exist yet */
  function showEmptyState() {
    activeDocId = null;
    const textarea = document.getElementById('doc-editor-textarea');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');

    if (textarea) textarea.value = '';
    if (textarea) textarea.placeholder = 'Start typing or paste text to create a document...';
    if (textarea) textarea.disabled = false;
    if (langSelect) langSelect.value = '';
    if (badge) badge.textContent = '';
    _hideLoadingOverlay();
    syncHighlighting();
    renderTabs();
  }

  let _loadingSpinner = null;
  function _showLoadingOverlay() {
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    let overlay = wrap.querySelector('.doc-loading-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'doc-loading-overlay';
      wrap.appendChild(overlay);
    }
    overlay.innerHTML = '';
    overlay.style.display = '';
    _loadingSpinner = spinnerModule.create('', 'clean', 'whirlpool');
    const el = _loadingSpinner.createElement();
    overlay.appendChild(el);
    _loadingSpinner.start();
  }

  function _hideLoadingOverlay() {
    if (_loadingSpinner) { _loadingSpinner.destroy(); _loadingSpinner = null; }
    const overlay = document.querySelector('.doc-loading-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  /** Show/hide the unified action button in the header based on current language */
  function _isFormBackedDoc(content) {
    const c = content || '';
    return /<!--\s*pdf_form_source\s+upload_id="[^"]+"/.test(c)
        || /<!--\s*pdf_source\s+upload_id="[^"]+"/.test(c);
  }

  // Force the on-screen keyboard down on touch. Firefox mobile ignores a plain
  // blur, so use the readonly trick (a readonly field shows no keyboard), then
  // drop readonly so the user can type again.
  function _dismissDocKb() {
    if (!(('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) return;
    const ta = document.getElementById('doc-editor-textarea');
    const ae = document.activeElement;
    const el = (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) ? ae : ta;
    if (!el) return;
    try {
      el.setAttribute('readonly', 'readonly');
      el.blur();
      setTimeout(() => { try { el.removeAttribute('readonly'); } catch (_) {} }, 120);
    } catch (_) { try { el.blur(); } catch (_) {} }
  }

  async function _downloadFilledPdf() {
    if (!activeDocId) return;
    _dismissDocKb();   // export shouldn't leave the keyboard up
    await _saveActiveDocBeforeExport();
    try {
      const r = await fetch(`${API_BASE}/api/document/${activeDocId}/export-pdf`);
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || r.statusText);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const cd = r.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^"';]+)/i);
      const _slug = (s) => (s || 'form').replace(/\.pdf$/i, '').replace(/\s+/g, '_').replace(/[^A-Za-z0-9._-]/g, '').replace(/_+/g, '_').replace(/^_|_$/g, '') || 'form';
      a.download = (m && decodeURIComponent(m[1])) || (_slug(docs.get(activeDocId)?.title) + '_annotated.pdf');
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (uiModule) uiModule.showError('Export failed: ' + e.message);
      else alert('Export failed: ' + e.message);
    }
  }

  async function _saveActiveDocBeforeExport() {
    // Flush in-flight edits from BOTH editing surfaces so the server-side
    // export reads the values the user actually sees:
    //  - Markdown view: textarea.value may differ from doc.content if the
    //    user typed but the existing 2s autosave hasn't fired.
    //  - PDF view: there may be a pending debounced _pdfPaneSaveTimer that
    //    hasn't flushed the user's input changes yet.
    if (_pdfPaneSaveTimer) {
      clearTimeout(_pdfPaneSaveTimer);
      await _savePdfPaneToMarkdown();
    }
    const ta = document.getElementById('doc-editor-textarea');
    const doc = docs.get(activeDocId);
    if (!ta || !doc || !activeDocId) return;
    const live = ta.value;
    if (live === doc.content) return;
    try {
      await fetch(`${API_BASE}/api/document/${activeDocId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: live }),
      });
      doc.content = live;
    } catch (e) {
      console.warn('Pre-export save failed:', e);
    }
  }

  async function _openExportPdfModal() {
    if (!activeDocId) return;
    await _saveActiveDocBeforeExport();

    const overlay = document.createElement('div');
    overlay.className = 'modal pdf-export-overlay';
    overlay.style.cssText = 'pointer-events:auto;background:rgba(0,0,0,0.5);backdrop-filter:blur(4px);';
    overlay.innerHTML = `
      <div class="modal-content" style="width:min(780px,94vw);max-height:86vh;">
        <div class="modal-header">
          <h4>Export filled PDF</h4>
          <button id="pdf-export-close" class="modal-close" title="Close">×</button>
        </div>
        <div id="pdf-export-summary" style="font-size:0.78rem;opacity:0.7;margin:0 0 6px;">Loading field values…</div>
        <div id="pdf-export-body" class="modal-body" style="font-size:0.85rem;">
          <div style="opacity:0.6;">Fetching mapping…</div>
        </div>
        <div class="modal-footer" style="display:flex;justify-content:flex-end;gap:8px;padding-top:8px;border-top:1px solid var(--border);margin-top:6px;align-items:center;">
          <span id="pdf-export-status" style="font-size:0.75rem;opacity:0.7;margin-right:auto;"></span>
          <button id="pdf-export-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>
          <button id="pdf-export-download" class="confirm-btn confirm-btn-primary" disabled>Download PDF</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    overlay.querySelector('#pdf-export-close').addEventListener('click', close);
    overlay.querySelector('#pdf-export-cancel').addEventListener('click', close);

    let fields = [];
    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}/export-pdf/preview`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err || res.statusText);
      }
      const data = await res.json();
      fields = data.fields || [];

      const filledNow = data.filled || 0;
      const total = data.total || fields.length;
      overlay.querySelector('#pdf-export-summary').textContent =
        `${filledNow} of ${total} fields filled. Review and adjust below before downloading.`;

      const body = overlay.querySelector('#pdf-export-body');
      body.innerHTML = '';

      // Group by page
      const byPage = new Map();
      for (const f of fields) {
        const p = f.page || 1;
        if (!byPage.has(p)) byPage.set(p, []);
        byPage.get(p).push(f);
      }
      const pages = Array.from(byPage.keys()).sort((a, b) => a - b);

      // Jump bar: page links + scroll-to-top/bottom shortcuts
      const jumpBar = document.createElement('div');
      jumpBar.style.cssText = 'position:sticky;top:0;background:var(--panel);padding:6px 0;margin-bottom:8px;border-bottom:1px solid var(--border);display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:0.72rem;z-index:1;';
      jumpBar.innerHTML = '<span style="opacity:0.6;margin-right:4px;">Jump to:</span>';
      const pageAnchors = {};
      const _smallBtnClass = 'confirm-btn confirm-btn-secondary';
      const _smallBtnStyle = 'padding:2px 8px;font-size:0.72rem;';
      for (const p of pages) {
        const a = document.createElement('button');
        a.textContent = String(p);
        a.title = `Jump to page ${p}`;
        a.className = _smallBtnClass;
        a.style.cssText = _smallBtnStyle;
        a.addEventListener('click', () => pageAnchors[p]?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
        jumpBar.appendChild(a);
      }
      const sep = document.createElement('span');
      sep.style.cssText = 'opacity:0.4;margin:0 4px;';
      sep.textContent = '|';
      jumpBar.appendChild(sep);
      const topBtn = document.createElement('button');
      topBtn.textContent = '↑ Top';
      topBtn.className = _smallBtnClass;
      topBtn.style.cssText = _smallBtnStyle;
      topBtn.addEventListener('click', () => body.scrollTo({ top: 0, behavior: 'smooth' }));
      jumpBar.appendChild(topBtn);
      const botBtn = document.createElement('button');
      botBtn.textContent = '↓ Bottom';
      botBtn.title = 'Jump to the last page (signature fields are usually here)';
      botBtn.className = _smallBtnClass;
      botBtn.style.cssText = _smallBtnStyle;
      botBtn.addEventListener('click', () => body.scrollTo({ top: body.scrollHeight, behavior: 'smooth' }));
      jumpBar.appendChild(botBtn);
      body.appendChild(jumpBar);

      for (const p of pages) {
        const sec = document.createElement('div');
        sec.className = 'pdf-export-section';
        sec.id = `pdf-export-page-${p}`;
        pageAnchors[p] = sec;
        sec.innerHTML = `<div class="pdf-export-section-title">Page ${p}</div>`;
        for (const f of byPage.get(p)) {
          const row = document.createElement('div');
          row.className = 'pdf-export-row';
          const label = document.createElement('label');
          label.textContent = f.label || f.name;
          label.title = `${f.name} (${f.type})`;
          row.appendChild(label);

          const isSignature = f.type === 'signature' || /sign(?:ed|ature)/i.test((f.name || '') + ' ' + (f.label || ''));
          const isDate = f.type === 'text' && /\b(date|dated)\b/i.test(`${f.name || ''} ${f.label || ''}`);
          let input;
          if (isSignature) {
            const wrap = document.createElement('div');
            wrap.style.cssText = 'display:flex;align-items:center;gap:8px;';
            const btn = document.createElement('button');
            btn.className = 'confirm-btn confirm-btn-secondary';
            btn.style.cssText = 'padding:3px 10px;font-size:0.78rem;';
            const thumb = document.createElement('img');
            thumb.style.cssText = 'max-height:32px;max-width:140px;object-fit:contain;border:1px solid var(--border);border-radius:3px;background:#fff;display:none;';
            const clearBtn = document.createElement('button');
            clearBtn.textContent = '×';
            clearBtn.title = 'Remove signature from this field';
            clearBtn.className = 'confirm-btn confirm-btn-secondary';
            clearBtn.style.cssText = 'padding:0 8px;font-size:0.85rem;line-height:1;display:none;';
            const apply = (sig) => {
              wrap.dataset.signatureId = sig.id;
              thumb.src = sig.dataUrl;
              thumb.style.display = '';
              clearBtn.style.display = '';
              btn.textContent = 'Change';
            };
            const clear = () => {
              delete wrap.dataset.signatureId;
              thumb.removeAttribute('src');
              thumb.style.display = 'none';
              clearBtn.style.display = 'none';
              btn.textContent = 'Sign here';
            };
            btn.textContent = 'Sign here';
            btn.addEventListener('click', async () => {
              const sig = await signatureModule.pick();
              if (sig) apply(sig);
            });
            clearBtn.addEventListener('click', clear);
            wrap.appendChild(btn);
            wrap.appendChild(thumb);
            wrap.appendChild(clearBtn);
            wrap.dataset.fieldName = f.name;
            wrap.dataset.fieldType = 'signature';
            const last = signatureModule.getLastUsed && signatureModule.getLastUsed();
            if (last) apply(last);
            input = wrap;
          } else if (isDate) {
            const wrap = document.createElement('div');
            wrap.style.cssText = 'display:flex;gap:6px;align-items:center;';
            const ti = document.createElement('input');
            ti.type = 'text';
            ti.value = f.value == null ? '' : String(f.value);
            ti.className = 'pdf-export-input';
            ti.style.cssText = 'flex:1;';
            ti.dataset.fieldName = f.name;
            ti.dataset.fieldType = f.type;
            const today = document.createElement('button');
            today.textContent = 'Today';
            today.title = "Set to today's date";
            today.className = 'confirm-btn confirm-btn-secondary';
            today.style.cssText = 'padding:3px 8px;font-size:0.72rem;';
            today.addEventListener('click', () => {
              const d = new Date();
              const dd = String(d.getDate()).padStart(2, '0');
              const mm = String(d.getMonth() + 1).padStart(2, '0');
              const yyyy = d.getFullYear();
              ti.value = `${dd}/${mm}/${yyyy}`;
            });
            wrap.appendChild(ti);
            wrap.appendChild(today);
            input = wrap;
          } else if (f.type === 'checkbox') {
            input = document.createElement('input');
            input.type = 'checkbox';
            input.checked = !!f.value;
          } else if (f.type === 'choice' && (f.options || []).length) {
            input = document.createElement('select');
            input.className = 'pdf-export-input';
            const blank = document.createElement('option');
            blank.value = '';
            blank.textContent = '— (none) —';
            input.appendChild(blank);
            for (const o of f.options) {
              const opt = document.createElement('option');
              opt.value = o; opt.textContent = o;
              if (o === f.value) opt.selected = true;
              input.appendChild(opt);
            }
          } else {
            input = document.createElement('input');
            input.type = 'text';
            input.value = f.value == null ? '' : String(f.value);
            input.className = 'pdf-export-input';
            input.style.cssText = 'width:100%;';
          }
          if (!isSignature && !isDate) {
            input.dataset.fieldName = f.name;
            input.dataset.fieldType = f.type;
          }
          row.appendChild(input);
          sec.appendChild(row);
        }
        body.appendChild(sec);
      }

      const downloadBtn = overlay.querySelector('#pdf-export-download');
      downloadBtn.disabled = false;
      downloadBtn.addEventListener('click', async () => {
        const values = {};
        const signatures = {};
        for (const el of overlay.querySelectorAll('[data-field-name]')) {
          const name = el.dataset.fieldName;
          const ftype = el.dataset.fieldType;
          if (ftype === 'signature') {
            if (el.dataset.signatureId) signatures[name] = el.dataset.signatureId;
          } else if (ftype === 'checkbox') {
            values[name] = el.checked;
          } else {
            values[name] = el.value;
          }
        }
        downloadBtn.disabled = true;
        overlay.querySelector('#pdf-export-status').textContent = 'Building PDF…';
        try {
          const r = await fetch(`${API_BASE}/api/document/${activeDocId}/export-pdf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values, signatures }),
          });
          if (!r.ok) {
            const t = await r.text();
            throw new Error(t || r.statusText);
          }
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          const cd = r.headers.get('Content-Disposition') || '';
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^"';]+)/i);
          const _slug = (s) => (s || 'form').replace(/\.pdf$/i, '').replace(/\s+/g, '_').replace(/[^A-Za-z0-9._-]/g, '').replace(/_+/g, '_').replace(/^_|_$/g, '') || 'form';
          a.download = (m && decodeURIComponent(m[1])) || (_slug(docs.get(activeDocId)?.title) + '_annotated.pdf');
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          close();
        } catch (e) {
          overlay.querySelector('#pdf-export-status').textContent = 'Error: ' + e.message;
          downloadBtn.disabled = false;
        }
      });
    } catch (e) {
      overlay.querySelector('#pdf-export-body').innerHTML =
        `<div style="color:#c00;">Failed to load preview: ${(e && e.message) || e}</div>`;
    }
  }

  // Tracks which form-backed docs the user has toggled into PDF view
  // (per-doc, in-memory). Survives switches between docs in the same session.
  const _pdfViewState = new Map();
  const _pdfPaneFieldsByDoc = new Map(); // docId -> [{name, type, inputEl, ...}]
  const _pdfPaneAnnotationsByDoc = new Map(); // docId -> [{id, page, x, y, w, h, el, wrap}]
  const _pdfUndoStackByDoc = new Map(); // docId -> markdown snapshots
  let _pdfPaneSaveTimer = null;

  // Match a freeform-annotation bullet line in the markdown source.
  // Coords are percentages of page width/height (0–100) so they scale with
  // however wide the PDF pane is rendered. `kind` and `lh` (line-height)
  // are optional for backward compat with earlier annotation formats.
  function _annotationRegexGlobal() {
    return /^[ \t]*-\s+(.*?)\s*<!--\s*annotation\s+id=([\w-]+)\s+page=(\d+)\s+x=([\d.]+)\s+y=([\d.]+)\s+w=([\d.]+)\s+h=([\d.]+)(?:\s+kind=(\w+))?(?:\s+lh=([\d.]+))?\s*-->[ \t]*$/gm;
  }

  // Bullet lines are single-line, so newlines in the value are escaped to
  // \n (backslash-n) for storage and unescaped on parse. Backslashes are
  // escaped first so the reverse mapping is unambiguous.
  function _escapeAnnotationValue(s) {
    return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/\n/g, '\\n');
  }
  function _unescapeAnnotationValue(s) {
    return String(s || '').replace(/\\(.)/g, (m, c) => c === 'n' ? '\n' : c === '\\' ? '\\' : m);
  }

  function _parseAnnotations(md) {
    const out = [];
    const re = _annotationRegexGlobal();
    let m;
    while ((m = re.exec(md || '')) !== null) {
      const rawVal = m[1] === '_(empty)_' ? '' : _unescapeAnnotationValue(m[1]);
      out.push({
        value: rawVal,
        id: m[2],
        page: parseInt(m[3], 10),
        x: parseFloat(m[4]),
        y: parseFloat(m[5]),
        w: parseFloat(m[6]),
        h: parseFloat(m[7]),
        kind: m[8] || 'text',
        lineHeight: m[9] ? parseFloat(m[9]) : 1.3,
      });
    }
    return out;
  }

  function _annotationLine(a) {
    const kind = a.kind || 'text';
    const lh = (a.lineHeight && Number.isFinite(a.lineHeight)) ? a.lineHeight : 1.3;
    const escaped = a.value === '' || a.value == null ? '_(empty)_' : _escapeAnnotationValue(a.value);
    return `- ${escaped} <!-- annotation id=${a.id} page=${a.page} x=${a.x.toFixed(2)} y=${a.y.toFixed(2)} w=${a.w.toFixed(2)} h=${a.h.toFixed(2)} kind=${kind} lh=${lh.toFixed(2)} -->`;
  }

  // Strip every annotation bullet + the "## Annotations" section, then
  // re-emit them at the end. Cleanest way to keep the section in sync with
  // the live set of refs without diffing line-by-line.
  function _writeAnnotations(md, annotations) {
    let out = (md || '').replace(_annotationRegexGlobal(), '');
    out = out.replace(/\n##\s+Annotations\s*\r?\n+/g, '\n');
    out = out.replace(/\n{3,}/g, '\n\n');
    if (!annotations.length) return out;
    if (!out.endsWith('\n')) out += '\n';
    out += '\n## Annotations\n\n';
    for (const a of annotations) out += _annotationLine(a) + '\n';
    return out;
  }

  function _newAnnotationId() {
    return 'ann-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
  }

  function _pdfMarkdownFromLive(docId = activeDocId) {
    const doc = docs.get(docId);
    if (!doc) return null;
    const annotations = _pdfPaneAnnotationsByDoc.get(docId) || [];
    return _writeAnnotations(doc.content || '', annotations.map(a => {
      let value = '';
      if (a.kind === 'check') {
        value = '✓';
      } else if (a.kind === 'signature') {
        const sid = a.el && a.el.dataset && a.el.dataset.signatureId;
        value = sid ? `signature:${sid}` : '';
      } else {
        value = (a.el && typeof a.el.value === 'string') ? a.el.value : '';
      }
      return {
        id: a.id, page: a.page, x: a.x, y: a.y, w: a.w, h: a.h,
        kind: a.kind || 'text',
        lineHeight: a.lineHeight || 1.3,
        value,
      };
    }));
  }

  function _pushPdfUndoSnapshot(docId = activeDocId) {
    const md = _pdfMarkdownFromLive(docId);
    if (md == null) return;
    const stack = _pdfUndoStackByDoc.get(docId) || [];
    if (stack[stack.length - 1] === md) return;
    stack.push(md);
    if (stack.length > 50) stack.shift();
    _pdfUndoStackByDoc.set(docId, stack);
  }

  async function _undoPdfPaneAction() {
    const docId = activeDocId;
    const stack = _pdfUndoStackByDoc.get(docId) || [];
    const prev = stack.pop();
    if (!prev) return false;
    _pdfUndoStackByDoc.set(docId, stack);
    if (_pdfPaneSaveTimer) {
      clearTimeout(_pdfPaneSaveTimer);
      _pdfPaneSaveTimer = null;
    }
    const doc = docs.get(docId);
    if (!doc) return false;
    doc.content = prev;
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) ta.value = prev;
    _setPdfSaveStatus('saving');
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: prev }),
      });
      if (!res.ok) throw new Error(res.statusText || String(res.status));
      _setPdfSaveStatus('saved');
      _renderPdfPane();
      return true;
    } catch (e) {
      _setPdfSaveStatus('error', e.message || 'Undo failed');
      return true;
    }
  }

  // Active drop mode for the PDF toolbar — toolbar buttons set this; the
  // next click on a page consumes it. null means clicks do nothing.
  let _pdfDropMode = null;
  // Per-doc last-used line spacing for text annotations. Once the user picks
  // 1.6 for one box, every text box dropped after that defaults to 1.6.
  const _pdfLastLineHeight = new Map(); // docId -> number
  function _setPdfDropMode(mode) {
    _pdfDropMode = mode;
    const pane = document.getElementById('doc-pdf-view');
    if (pane) pane.style.cursor = mode ? 'crosshair' : '';
    // Highlight the active toolbar button so users see which mode is on.
    for (const id of ['doc-pdf-add-text-btn', 'doc-pdf-add-check-btn', 'doc-pdf-add-sign-btn']) {
      const b = document.getElementById(id);
      if (!b) continue;
      const want = (mode === 'text' && id === 'doc-pdf-add-text-btn')
        || (mode === 'check' && id === 'doc-pdf-add-check-btn')
        || (mode === 'signature' && id === 'doc-pdf-add-sign-btn');
      b.style.outline = want ? '2px solid var(--accent-primary, var(--red))' : '';
    }
  }
  // Cache of signature data URLs by id, populated lazily as the PDF view
  // renders inline signatures and as the user picks new ones.
  const _sigCache = new Map();

  // Mirror of Python _encode_name in src/pdf_form_doc.py — keep in sync.
  // Percent-encode everything that's not A-Za-z0-9 _ . -
  function _encodeFieldName(name) {
    let out = '';
    for (const ch of name || '') {
      if (/[A-Za-z0-9_.\-]/.test(ch)) {
        out += ch;
      } else {
        const enc = new TextEncoder().encode(ch);
        for (const b of enc) out += '%' + b.toString(16).toUpperCase().padStart(2, '0');
      }
    }
    return out;
  }

  // Proximity-based handle visibility — show ×/drag/resize handles whenever
  // the cursor gets within ~30px of an annotation, not only when it's inside.
  // Attached once to the pane; reads the current doc's refs at fire time.
  let _pdfPaneProximityWired = false;
  function _wirePdfPaneProximity(pane) {
    if (_pdfPaneProximityWired || !pane) return;
    _pdfPaneProximityWired = true;
    let raf = 0;
    const buffer = 30;
    pane.addEventListener('mousemove', (ev) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const refs = _pdfPaneAnnotationsByDoc.get(activeDocId) || [];
        for (const ref of refs) {
          if (!ref || !ref.wrap || !ref._setHandlesVisible) continue;
          const r = ref.wrap.getBoundingClientRect();
          const dx = Math.max(r.left - ev.clientX, 0, ev.clientX - r.right);
          const dy = Math.max(r.top - ev.clientY, 0, ev.clientY - r.bottom);
          ref._setHandlesVisible(Math.hypot(dx, dy) <= buffer);
        }
      });
    });
    pane.addEventListener('mouseleave', () => {
      const refs = _pdfPaneAnnotationsByDoc.get(activeDocId) || [];
      for (const ref of refs) ref._setHandlesVisible && ref._setHandlesVisible(false);
    });
  }

  async function _pdfResponseErrorMessage(res) {
    const text = await res.text().catch(() => '');
    try {
      const data = JSON.parse(text);
      if (typeof data?.detail === 'string') return data.detail;
      if (data?.detail) return JSON.stringify(data.detail);
    } catch (_) {}
    return text || res.statusText || `HTTP ${res.status}`;
  }

  async function _renderPdfPane() {
    const pane = document.getElementById('doc-pdf-view');
    if (!pane || !activeDocId) return;
    _wirePdfPaneProximity(pane);
    const docId = activeDocId;
    // Keep the save pill across re-renders by detaching/re-attaching it
    const savedPill = document.getElementById('doc-pdf-save-pill');
    pane.innerHTML = '<div style="color:#bbb;font-size:13px;text-align:center;padding:40px;">Loading PDF…</div>';
    if (savedPill) pane.appendChild(savedPill);
    let data;
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}/render-pages`);
      if (!res.ok) throw new Error(await _pdfResponseErrorMessage(res));
      data = await res.json();
    } catch (e) {
      pane.innerHTML = `<div style="color:#fbb;padding:40px;text-align:center;">Failed to load PDF view: ${_escHtml(e.message || String(e))}</div>`;
      if (savedPill) pane.appendChild(savedPill);
      return;
    }
    if (docId !== activeDocId) return;

    pane.innerHTML = '';
    if (savedPill) pane.appendChild(savedPill);
    const fieldRefs = [];
    // Reset annotation refs for this doc before the page loop — we rebuild them
    // page by page from the live markdown.
    const annotationRefs = [];
    _pdfPaneAnnotationsByDoc.set(docId, annotationRefs);
    const liveMd = (docs.get(docId) && docs.get(docId).content) || '';
    const allAnnotations = _parseAnnotations(liveMd);
    // Recover the last-used line spacing from existing text annotations so the
    // pref survives page reload, not just the in-memory life of this session.
    if (!_pdfLastLineHeight.has(docId)) {
      for (let i = allAnnotations.length - 1; i >= 0; i--) {
        const a = allAnnotations[i];
        if (a.kind === 'text' && a.lineHeight) {
          _pdfLastLineHeight.set(docId, a.lineHeight);
          break;
        }
      }
    }
    for (const page of data.pages) {
      // Lock the wrap to the page's exact aspect ratio so percentage-positioned
      // inputs stay aligned no matter how wide the panel is rendered.
      const pageWrap = document.createElement('div');
      pageWrap.style.cssText = `position:relative;margin:0 auto 16px auto;width:${page.width}px;max-width:calc(100% - 24px);aspect-ratio:${page.width} / ${page.height};background:#fff;box-shadow:0 4px 16px rgba(0,0,0,0.4);container-type:size;`;
      const img = document.createElement('img');
      img.src = `${API_BASE}/api/document/${docId}/page/${page.page}.png`;
      img.style.cssText = 'display:block;width:100%;height:100%;user-select:none;-webkit-user-drag:none;pointer-events:none;';
      img.draggable = false;
      pageWrap.appendChild(img);

      // Scale-aware overlay so inputs track if the page wrap shrinks below
      // its natural width (we set width:page.width but max-width:100% caps it).
      // Each field is positioned via percentages of the page rect.
      for (const f of page.fields) {
        const [x0, y0, x1, y1] = f.rect_px;
        const wPct = ((x1 - x0) / page.width) * 100;
        const hPct = ((y1 - y0) / page.height) * 100;
        const lPct = (x0 / page.width) * 100;
        const tPct = (y0 / page.height) * 100;
        const isSig = f.type === 'signature' || /sign(?:ed|ature)/i.test((f.name || '') + ' ' + (f.label || ''));
        let el;
        const baseStyle = `position:absolute;left:${lPct}%;top:${tPct}%;width:${wPct}%;height:${hPct}%;box-sizing:border-box;font-family:inherit;`;
        if (isSig) {
          // Inline signature: click to pick / change. The selected signature
          // ID is mirrored into the markdown bullet as `signature:<id>` via
          // the existing debounced save flow, which the export route reads.
          el = document.createElement('div');
          el.style.cssText = baseStyle + 'cursor:pointer;display:flex;align-items:center;justify-content:center;overflow:hidden;';
          el.dataset.fieldName = f.name;
          el.dataset.fieldType = 'signature';

          // Parse pre-existing selection from value: `signature:<id>` shape
          const initialSigId = (typeof f.value === 'string' && f.value.startsWith('signature:'))
            ? f.value.slice('signature:'.length).trim() : '';
          const renderSigUI = async (sigId) => {
            el.innerHTML = '';
            if (sigId) {
              el.dataset.signatureId = sigId;
              const img = document.createElement('img');
              img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;';
              // Look up the signature data URL via the saved-list cache or fetch
              try {
                if (!_sigCache.has(sigId)) {
                  const r = await fetch(`${API_BASE}/api/signatures`);
                  const data = await r.json();
                  for (const s of data.signatures || []) _sigCache.set(s.id, s.data_url);
                }
                const dataUrl = _sigCache.get(sigId);
                if (dataUrl) img.src = dataUrl;
                else throw new Error('not found');
                el.appendChild(img);
                el.style.border = '1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent)';
                el.style.background = 'transparent';
              } catch {
                el.removeAttribute('data-signature-id');
                renderSigUI('');
              }
            } else {
              delete el.dataset.signatureId;
              el.style.border = '1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent)';
              el.style.background = 'color-mix(in srgb, var(--accent, var(--red)) 10%, transparent)';
              const span = document.createElement('span');
              span.style.cssText = 'color:var(--accent, var(--red));font-size:11px;';
              span.textContent = 'Sign here';
              el.appendChild(span);
            }
          };
          el.addEventListener('click', async (ev) => {
            ev.stopPropagation();
            const sig = await signatureModule.pick();
            if (sig) {
              _sigCache.set(sig.id, sig.dataUrl);
              await renderSigUI(sig.id);
              _schedulePdfPaneSave();
            }
          });
          renderSigUI(initialSigId);
        } else if (f.type === 'checkbox') {
          el = document.createElement('input');
          el.type = 'checkbox';
          el.checked = !!f.value;
          el.style.cssText = baseStyle + 'cursor:pointer;';
        } else if (f.type === 'choice' && (f.options || []).length) {
          el = document.createElement('select');
          const blank = document.createElement('option');
          blank.value = ''; blank.textContent = '—';
          el.appendChild(blank);
          for (const opt of f.options) {
            const o = document.createElement('option');
            o.value = opt; o.textContent = opt;
            if (opt === f.value) o.selected = true;
            el.appendChild(o);
          }
          el.style.cssText = baseStyle + 'border:1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent);background:rgba(255,255,255,0.85);font-size:11px;padding:0 2px;';
        } else {
          el = document.createElement('input');
          el.type = 'text';
          el.value = f.value == null ? '' : String(f.value);
          // Pick a font-size that roughly fits the field height. Smaller
          // multiplier than line-height to leave breathing room and match
          // what AcroForm renderers typically use.
          const fontPx = Math.max(8, Math.min(14, Math.round((y1 - y0) * 0.4)));
          el.style.cssText = baseStyle + `border:1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent);background:rgba(255,255,255,0.85);font-size:${fontPx}px;padding:0 2px;`;
        }
        if (!isSig) {
          el.dataset.fieldName = f.name;
          el.dataset.fieldType = f.type;
          el.addEventListener('input', _schedulePdfPaneSave);
          el.addEventListener('change', _schedulePdfPaneSave);
        }
        pageWrap.appendChild(el);
        // Signature fields are also persisted via the markdown bullet — the
        // click handler invokes _schedulePdfPaneSave directly after picking.
        fieldRefs.push({ name: f.name, type: isSig ? 'signature' : f.type, el });

        // Date-field shortcut: any text field whose name or label hints at
        // a date gets a small "Today" button anchored to its right edge.
        const isDate = f.type === 'text' && /\b(date|dated)\b/i.test(`${f.name} ${f.label}`);
        if (isDate) {
          const today = document.createElement('button');
          today.type = 'button';
          today.textContent = 'Today';
          today.title = "Set to today's date";
          today.style.cssText = `position:absolute;left:calc(${lPct}% + ${wPct}%);top:${tPct}%;height:${hPct}%;margin-left:4px;padding:0 6px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 55%, transparent);background:rgba(255,255,255,0.95);color:var(--accent, var(--red));border-radius:3px;cursor:pointer;font-size:10px;line-height:1;white-space:nowrap;`;
          today.addEventListener('click', () => {
            const d = new Date();
            const dd = String(d.getDate()).padStart(2, '0');
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const yyyy = d.getFullYear();
            el.value = `${dd}/${mm}/${yyyy}`;
            _schedulePdfPaneSave();
          });
          pageWrap.appendChild(today);
        }
      }
      // Freeform annotations for this page
      for (const ann of allAnnotations) {
        if (ann.page !== page.page) continue;
        const built = _buildAnnotation(pageWrap, ann);
        annotationRefs.push(built.ref);
      }
      // Click on empty page area drops a new annotation when a drop mode is
      // active (toolbar buttons set the mode). Without a mode, clicking does
      // nothing — keeps page interactions predictable so users don't get
      // surprise boxes from stray clicks.
      pageWrap.addEventListener('click', (ev) => {
        if (ev.target !== pageWrap && ev.target.tagName !== 'IMG') return;
        if (!_pdfDropMode) return;
        const rect = pageWrap.getBoundingClientRect();
        const xPct = ((ev.clientX - rect.left) / rect.width) * 100;
        const yPct = ((ev.clientY - rect.top) / rect.height) * 100;
        // Default sizes per kind. Center the box on the click so the value
        // shows up where the user pointed (text input width is wider than tall,
        // so centering vertically is what matters).
        const sizes = {
          text: { w: 8, h: 2.5 },
          check: { w: 2.5, h: 2.5 },
          signature: { w: 22, h: 6 },
        };
        const size = sizes[_pdfDropMode] || sizes.text;
        // Check stamps drop centered on the click (you point at the box you
        // want to tick). Text + signature anchor top-left at the click so the
        // first character lands exactly where the cursor was.
        const centered = _pdfDropMode === 'check';
        const x = Math.max(0, Math.min(100 - size.w, centered ? xPct - size.w / 2 : xPct));
        const y = Math.max(0, Math.min(100 - size.h, centered ? yPct - size.h / 2 : yPct));
        const ann = {
          id: _newAnnotationId(),
          page: page.page,
          x, y, w: size.w, h: size.h,
          value: _pdfDropMode === 'check' ? '[ ]' : '',
          kind: _pdfDropMode,
          // For text drops, inherit the doc's last-used line spacing so the
          // user's "1.6" choice sticks across every new box they place.
          lineHeight: _pdfDropMode === 'text' ? (_pdfLastLineHeight.get(docId) || 1.3) : undefined,
        };
        _pushPdfUndoSnapshot(docId);
        const built = _buildAnnotation(pageWrap, ann);
        annotationRefs.push(built.ref);
        if (_pdfDropMode === 'text') {
          built.ref.el.focus();
        } else if (_pdfDropMode === 'signature') {
          // Trigger the signature picker right away — users always want to
          // pick the signature when they place the box.
          built.ref.el.click();
        }
        _schedulePdfPaneSave();
        // Mode stays armed — keep placing more until the user clicks the
        // toolbar button again to turn it off.
      });

      pane.appendChild(pageWrap);
    }
    _pdfPaneFieldsByDoc.set(docId, fieldRefs);
  }

  // Render one annotation as a positioned wrapper with type-appropriate
  // content (text input / checkbox / signature picker) plus delete and drag
  // handles. Returns { ref } so the caller can track it for save.
  function _buildAnnotation(pageWrap, ann) {
    const kind = ann.kind || 'text';
    const wrap = document.createElement('div');
    wrap.className = 'pdf-annotation-wrap';
    wrap.style.cssText = `position:absolute;left:${ann.x}%;top:${ann.y}%;width:${ann.w}%;height:${ann.h}%;box-sizing:border-box;z-index:2;`;
    wrap.dataset.annId = ann.id;
    wrap.dataset.annKind = kind;

    let input;
    if (kind === 'check') {
      // Stamp-style checkmark drawn as an SVG so it scales with the box —
      // a glyph at fixed font-size always over- or under-fills.
      input = document.createElement('div');
      input.style.cssText = `width:100%;height:100%;display:flex;align-items:center;justify-content:center;user-select:none;pointer-events:none;`;
      input.innerHTML = `<svg viewBox="0 0 24 24" preserveAspectRatio="xMidYMid meet" style="width:100%;height:100%;display:block;"><path d="M4 12 L10 18 L20 6" fill="none" stroke="#111" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    } else if (kind === 'signature') {
      input = document.createElement('div');
      input.style.cssText = `width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;font-size:10px;color:var(--accent, var(--red));`;
      input.textContent = (ann.value && ann.value.startsWith('signature:')) ? '' : 'Sign here';
      input.dataset.signatureId = (ann.value && ann.value.startsWith('signature:')) ? ann.value.slice(10) : '';
    } else {
      // Multi-line text input. Browser resize disabled — we use the custom
      // bottom-right handle for resizing so position metadata stays in sync.
      // Font size uses cqh (container-query height) so the text scales with
      // the rendered page when the doc panel resizes — keeps annotations
      // visually anchored to the PDF instead of looking small/large after
      // a fullscreen toggle.
      input = document.createElement('textarea');
      input.value = ann.value || '';
      input.placeholder = 'Type…';
      input.rows = 1;
      input.spellcheck = false;
      const lh = ann.lineHeight || 1.3;
      input.style.cssText = `width:100%;height:100%;box-sizing:border-box;border:1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);font-family:inherit;font-size:1.5cqh;line-height:${lh};padding:1px 4px;color:#111;resize:none;overflow:auto;white-space:pre-wrap;`;
    }

    // Touch devices have no cursor, so the hover/proximity reveal never fires —
    // there, show the handles permanently and make them finger-sized so the
    // box edges are actually grabbable.
    const _isTouch = typeof matchMedia === 'function' && matchMedia('(hover: none)').matches;
    const HS = _isTouch ? 28 : 20;       // handle size (px)
    // Sit the handles just outside the box — inner edge meets the corner (no
    // gap, no overlap) so they don't cover the text you're typing but stay close.
    const OFF = -HS;
    const HIDE = _isTouch ? '' : 'none'; // initial display ('' = shown on touch)

    // × delete button
    const del = document.createElement('button');
    del.type = 'button';
    del.textContent = '✖';
    del.title = 'Delete annotation';
    del.style.cssText = `position:absolute;top:${OFF}px;right:${OFF}px;width:${HS}px;height:${HS}px;padding:0 0 0 1px;border:1px solid var(--accent, var(--red));background:#fff;color:var(--accent, var(--red));border-radius:50%;cursor:pointer;font-size:11px;line-height:1;display:${HIDE};font-weight:bold;touch-action:none;`;

    // ☰ drag handle — same size as the × button.
    const grip = document.createElement('div');
    grip.title = 'Drag to move';
    grip.textContent = '☰';
    grip.style.cssText = `position:absolute;top:${OFF}px;left:${OFF}px;width:${HS}px;height:${HS}px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:#fff;color:var(--accent, var(--red));border-radius:3px;cursor:move;font-size:11px;line-height:${HS - 2}px;text-align:center;display:${HIDE};touch-action:none;`;

    // ↘ resize handle — same size as the × button.
    const resize = document.createElement('div');
    resize.title = 'Drag to resize';
    resize.style.cssText = `position:absolute;bottom:${OFF}px;right:${OFF}px;width:${HS}px;height:${HS}px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:#fff;color:var(--accent, var(--red));border-radius:3px;cursor:nwse-resize;display:${HIDE};touch-action:none;`;
    resize.innerHTML = '<svg width="14" height="14" viewBox="0 0 10 10" style="display:block;margin:auto;height:100%;"><path d="M2 8 L8 2 M5 8 L8 5" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/></svg>';

    let menuBtn = null;
    if (kind === 'text') {
      menuBtn = document.createElement('button');
      menuBtn.type = 'button';
      menuBtn.textContent = '…';
      menuBtn.title = 'Text annotation options';
      menuBtn.style.cssText = `position:absolute;bottom:${OFF}px;left:${OFF}px;width:${HS}px;height:${HS}px;padding:0;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 65%, transparent);background:#fff;color:var(--accent, var(--red));border-radius:50%;cursor:pointer;font-size:15px;line-height:0.8;display:${HIDE};font-weight:bold;touch-action:none;`;
    }

    // Set handle visibility together; clicking/tapping the annotation itself
    // brings hidden controls back.
    const _setHandlesVisible = (show) => {
      const dismissed = wrap.dataset.controlsDismissed === '1';
      const v = (show && !dismissed) ? '' : 'none';
      del.style.display = v;
      grip.style.display = v;
      resize.style.display = v;
      if (menuBtn) menuBtn.style.display = v;
    };
    if (!_isTouch) {
      wrap.addEventListener('mouseenter', () => _setHandlesVisible(true));
      wrap.addEventListener('mouseleave', () => _setHandlesVisible(false));
    }
    wrap.addEventListener('pointerdown', (ev) => {
      if (ev.target === del || ev.target === grip || ev.target === resize || ev.target === menuBtn) return;
      wrap.dataset.controlsDismissed = '0';
      _setHandlesVisible(true);
    });

    const ref = { id: ann.id, page: ann.page, x: ann.x, y: ann.y, w: ann.w, h: ann.h, el: input, wrap, kind, _setHandlesVisible };

    if (kind === 'check') {
      // Stamp checkmark — value is fixed, nothing to listen for.
      ref.value = '✓';
    } else if (kind === 'signature') {
      const _renderSig = async (sigId) => {
        input.innerHTML = '';
        if (!sigId) {
          input.dataset.signatureId = '';
          input.style.background = 'color-mix(in srgb, var(--accent, var(--red)) 10%, transparent)';
          input.style.border = '1px dashed color-mix(in srgb, var(--accent, var(--red)) 65%, transparent)';
          const span = document.createElement('span');
          span.textContent = 'Sign here';
          input.appendChild(span);
          return;
        }
        input.dataset.signatureId = sigId;
        try {
          if (!_sigCache.has(sigId)) {
            const r = await fetch(`${API_BASE}/api/signatures`);
            const data = await r.json();
            for (const s of data.signatures || []) _sigCache.set(s.id, s.data_url);
          }
          const dataUrl = _sigCache.get(sigId);
          if (!dataUrl) throw new Error('not found');
          const img = document.createElement('img');
          img.src = dataUrl;
          img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;';
          input.appendChild(img);
          input.style.background = 'transparent';
          input.style.border = '1px solid color-mix(in srgb, var(--accent, var(--red)) 45%, transparent)';
        } catch {
          _renderSig('');
        }
      };
      input.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const sig = await signatureModule.pick();
        if (sig) {
          _pushPdfUndoSnapshot();
          _sigCache.set(sig.id, sig.dataUrl);
          await _renderSig(sig.id);
          ref.value = `signature:${sig.id}`;
          _schedulePdfPaneSave();
        }
      });
      // Render any pre-existing signature value
      _renderSig(input.dataset.signatureId);
    } else {
      // Grow the wrap to fit typed content. Width grows for the longest line,
      // height grows for total content height. Never shrinks — user-driven
      // resizes (the corner handle) are preserved.
      let _mirror = null;
      const _autoGrow = () => {
        const pageRect = pageWrap.getBoundingClientRect();
        if (!pageRect.height || !pageRect.width) return;

        // --- Width: measure the longest line via a hidden mirror div with
        // the same typography as the textarea ---
        if (!_mirror) {
          _mirror = document.createElement('div');
          _mirror.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font-family:inherit;padding:1px 4px;left:-9999px;top:-9999px;';
          document.body.appendChild(_mirror);
        }
        const cs = window.getComputedStyle(input);
        _mirror.style.fontSize = cs.fontSize;
        _mirror.style.fontWeight = cs.fontWeight;
        _mirror.style.fontFamily = cs.fontFamily;
        _mirror.style.letterSpacing = cs.letterSpacing;
        let widestPx = 0;
        const lines = (input.value || input.placeholder || '').split('\n');
        for (const line of lines) {
          _mirror.textContent = line || ' ';
          if (_mirror.offsetWidth > widestPx) widestPx = _mirror.offsetWidth;
        }
        const neededWPct = ((widestPx + 12) / pageRect.width) * 100;
        if (neededWPct > ref.w) {
          ref.w = Math.min(100 - ref.x, neededWPct);
          wrap.style.width = ref.w + '%';
        }

        // --- Height: same trick as before, briefly let textarea fit content ---
        const prev = input.style.height;
        input.style.height = 'auto';
        const neededHpx = input.scrollHeight + 4;
        input.style.height = prev || '100%';
        const neededHpct = (neededHpx / pageRect.height) * 100;
        if (neededHpct > ref.h) {
          ref.h = Math.min(100 - ref.y, neededHpct);
          wrap.style.height = ref.h + '%';
        }
      };
      input.addEventListener('input', () => {
        if (wrap.dataset.textUndoCaptured !== '1') {
          _pushPdfUndoSnapshot();
          wrap.dataset.textUndoCaptured = '1';
        }
        ref.value = input.value;
        _autoGrow();
        _schedulePdfPaneSave();
      });
      input.addEventListener('change', () => {
        ref.value = input.value;
        _autoGrow();
        _schedulePdfPaneSave();
      });
      input.addEventListener('focus', () => {
        _pushPdfUndoSnapshot();
        wrap.dataset.textUndoCaptured = '1';
      });
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') input.blur();
      });
      // Initial fit in case the saved value is taller than the saved height
      // (e.g. line-height was bumped up after the box was placed).
      requestAnimationFrame(_autoGrow);
      // Expose so the line-spacing slider can re-fit each annotation when
      // the doc-wide spacing is changed.
      ref._autoGrow = _autoGrow;
    }

    del.addEventListener('click', (ev) => {
      ev.stopPropagation();
      _pushPdfUndoSnapshot();
      _removeAnnotation(ref);
    });
    // Drag to reposition. Coordinates are stored as percentages of the page
    // wrap so they survive resizing.
    grip.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _pushPdfUndoSnapshot();
      try { grip.setPointerCapture(ev.pointerId); } catch (_) {}
      // Hide the × and resize handles while moving so they don't obscure the
      // box — easier to see exactly where it lands. Restored on release.
      del.style.display = 'none';
      resize.style.display = 'none';
      if (menuBtn) menuBtn.style.display = 'none';
      const start = { mx: ev.clientX, my: ev.clientY, x: ref.x, y: ref.y };
      const rect = pageWrap.getBoundingClientRect();
      const onMove = (e) => {
        const dxPct = ((e.clientX - start.mx) / rect.width) * 100;
        const dyPct = ((e.clientY - start.my) / rect.height) * 100;
        ref.x = Math.max(0, Math.min(100 - ref.w, start.x + dxPct));
        ref.y = Math.max(0, Math.min(100 - ref.h, start.y + dyPct));
        wrap.style.left = ref.x + '%';
        wrap.style.top = ref.y + '%';
      };
      const onUp = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        _setHandlesVisible(true);
        _schedulePdfPaneSave();
      };
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });

    // Drag bottom-right corner to resize. Width/height stored as percentages.
    resize.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _pushPdfUndoSnapshot();
      try { resize.setPointerCapture(ev.pointerId); } catch (_) {}
      // Hide the × and move handles while resizing — clean view of the box edge.
      del.style.display = 'none';
      grip.style.display = 'none';
      if (menuBtn) menuBtn.style.display = 'none';
      const start = { mx: ev.clientX, my: ev.clientY, w: ref.w, h: ref.h };
      const rect = pageWrap.getBoundingClientRect();
      const onMove = (e) => {
        const dwPct = ((e.clientX - start.mx) / rect.width) * 100;
        const dhPct = ((e.clientY - start.my) / rect.height) * 100;
        ref.w = Math.max(1, Math.min(100 - ref.x, start.w + dwPct));
        ref.h = Math.max(0.8, Math.min(100 - ref.y, start.h + dhPct));
        wrap.style.width = ref.w + '%';
        wrap.style.height = ref.h + '%';
      };
      const onUp = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        _setHandlesVisible(true);
        _schedulePdfPaneSave();
      };
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });

    // Text options menu — opened from the floating … button so the spacing
    // controls are not always visible while typing.
    if (kind === 'text') {
      const popover = document.createElement('div');
      popover.className = 'pdf-annotation-text-menu';
      popover.style.cssText = `position:absolute;bottom:${OFF + HS + 4}px;left:${OFF}px;display:none;background:#fff;border:1px solid var(--accent, var(--red));border-radius:4px;padding:6px 8px;box-shadow:0 2px 8px rgba(0,0,0,0.2);z-index:10;flex-direction:column;align-items:stretch;gap:6px;font-size:10px;color:#222;white-space:nowrap;`;
      popover.innerHTML = `
        <div style="display:flex;align-items:center;gap:6px;">
          <span>Line spacing</span>
          <input type="range" min="1" max="3" step="0.05" value="${ann.lineHeight || 1.3}" style="width:90px;accent-color:var(--accent, var(--red));" />
          <input type="number" class="lh-val" min="0.5" max="5" step="0.01" value="${(ann.lineHeight || 1.3).toFixed(2)}" style="width:54px;font-size:10px;padding:1px 7px 1px 3px;border:1px solid var(--accent, var(--red));border-radius:3px;text-align:right;accent-color:var(--accent, var(--red));" />
        </div>
        <button type="button" class="pdf-ann-today" style="height:22px;padding:0 7px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 55%, transparent);background:color-mix(in srgb, var(--accent, var(--red)) 10%, transparent);color:var(--accent, var(--red));border-radius:4px;cursor:pointer;font-size:10px;font-family:inherit;text-align:left;">Today</button>
      `;
      const slider = popover.querySelector('input[type="range"]');
      const valInput = popover.querySelector('.lh-val');
      const todayBtn = popover.querySelector('.pdf-ann-today');
      const _applyLh = (v, fromSlider) => {
        if (!Number.isFinite(v)) return;
        if (popover.dataset.lhUndoCaptured !== '1') {
          _pushPdfUndoSnapshot();
          popover.dataset.lhUndoCaptured = '1';
        }
        v = Math.max(0.5, Math.min(5, v));
        // Apply to every text annotation in the doc so spacing stays
        // consistent — exports were "all over the place" because each box
        // could have its own lh; treat it as a doc-level setting.
        const allRefs = _pdfPaneAnnotationsByDoc.get(activeDocId) || [];
        for (const r of allRefs) {
          if (r.kind !== 'text') continue;
          r.lineHeight = v;
          if (r.el && r.el.style) r.el.style.lineHeight = String(v);
          // Spacing change can push content past the box height — fire each
          // ref's auto-grow so the wrap expands to fit the new line height.
          if (typeof r._autoGrow === 'function') r._autoGrow();
        }
        ref.lineHeight = v;
        input.style.lineHeight = String(v);
        if (fromSlider) valInput.value = v.toFixed(2);
        else slider.value = String(Math.max(parseFloat(slider.min), Math.min(parseFloat(slider.max), v)));
        _pdfLastLineHeight.set(activeDocId, v);
        _schedulePdfPaneSave();
      };
      slider.addEventListener('input', () => _applyLh(parseFloat(slider.value), true));
      valInput.addEventListener('input', () => _applyLh(parseFloat(valInput.value), false));
      // Reject invalid typed values on blur — snap back to the live ref value.
      valInput.addEventListener('blur', () => {
        const v = parseFloat(valInput.value);
        if (!Number.isFinite(v)) valInput.value = (ref.lineHeight || 1.3).toFixed(2);
        popover.dataset.lhUndoCaptured = '0';
      });
      todayBtn.addEventListener('click', () => {
        _pushPdfUndoSnapshot();
        const d = new Date();
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const yyyy = d.getFullYear();
        const text = `${dd}/${mm}/${yyyy}`;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? start;
        input.value = input.value.slice(0, start) + text + input.value.slice(end);
        const next = start + text.length;
        try { input.setSelectionRange(next, next); } catch (_) {}
        ref.value = input.value;
        if (typeof ref._autoGrow === 'function') ref._autoGrow();
        _schedulePdfPaneSave();
        input.focus({ preventScroll: true });
      });
      // Stop popover clicks from bubbling to pageWrap (would create new ann)
      popover.addEventListener('mousedown', (e) => e.stopPropagation());
      popover.addEventListener('click', (e) => e.stopPropagation());
      menuBtn?.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        popover.style.display = popover.style.display === 'flex' ? 'none' : 'flex';
      });
      wrap.appendChild(popover);
      ref.lineHeight = ann.lineHeight || 1.3;
    }

    wrap.appendChild(input);
    wrap.appendChild(del);
    wrap.appendChild(grip);
    wrap.appendChild(resize);
    if (menuBtn) wrap.appendChild(menuBtn);
    pageWrap.appendChild(wrap);
    return { wrap, ref };
  }

  function _removeAnnotation(ref) {
    if (!ref || !ref.wrap) return;
    const docId = activeDocId;
    const refs = _pdfPaneAnnotationsByDoc.get(docId) || [];
    const idx = refs.indexOf(ref);
    if (idx >= 0) refs.splice(idx, 1);
    ref.wrap.remove();
    _schedulePdfPaneSave();
  }

  // Prompt user for an instruction and ask the backend's VL pipeline to
  // propose annotations for every blank/labeled spot on the PDF. Resulting
  // annotations are appended into the doc's markdown and the PDF pane is
  // re-rendered so the user can review / edit / drag / delete each one.
  async function _aiFillAnnotations() {
    const docId = activeDocId;
    if (!docId) return;
    const doc = docs.get(docId);
    if (!doc) return;

    const instruction = window.prompt(
      'What should the AI fill in?\n(e.g. "My name is Jane Doe, address 123 Main St, dob 1990-01-15")'
    );
    if (!instruction || !instruction.trim()) return;

    _setPdfSaveStatus('saving');
    const btn = document.getElementById('doc-pdf-ai-fill-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Thinking…'; }
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}/ai-fill-annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: instruction.trim() }),
      });
      if (!res.ok) {
        const t = await res.text().catch(() => res.statusText);
        throw new Error(t || res.statusText);
      }
      const data = await res.json();
      const proposed = (data && data.annotations) || [];
      if (!proposed.length) {
        _setPdfSaveStatus('idle');
        if (uiModule && uiModule.showToast) uiModule.showToast('AI found nothing to fill');
        return;
      }
      // Merge into markdown via the same _writeAnnotations path: parse current,
      // append proposed (each gets a fresh id), persist, then re-render.
      const existing = _parseAnnotations(doc.content || '');
      const combined = existing.slice();
      for (const a of proposed) {
        combined.push({
          id: _newAnnotationId(),
          page: parseInt(a.page, 10) || 1,
          x: Math.max(0, Math.min(100, parseFloat(a.x) || 0)),
          y: Math.max(0, Math.min(100, parseFloat(a.y) || 0)),
          w: Math.max(0.5, Math.min(100, parseFloat(a.w) || 22)),
          h: Math.max(0.3, Math.min(100, parseFloat(a.h) || 3.5)),
          value: String(a.value || ''),
        });
      }
      const newMd = _writeAnnotations(doc.content || '', combined);
      doc.content = newMd;
      const ta = document.getElementById('doc-editor-textarea');
      if (ta) ta.value = newMd;
      const r2 = await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: newMd }),
      });
      if (!r2.ok) {
        const t = await r2.text().catch(() => r2.statusText);
        throw new Error(t || r2.statusText);
      }
      _setPdfSaveStatus('saved');
      if (uiModule && uiModule.showToast) uiModule.showToast(`AI added ${proposed.length} annotations`);
      _renderPdfPane();
    } catch (e) {
      console.error('AI fill failed:', e);
      _setPdfSaveStatus('error', `AI fill failed: ${e.message || e}`);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'AI fill'; }
    }
  }

  function _schedulePdfPaneSave() {
    _setPdfSaveStatus('dirty');
    if (_pdfPaneSaveTimer) clearTimeout(_pdfPaneSaveTimer);
    _pdfPaneSaveTimer = setTimeout(() => _savePdfPaneToMarkdown(), 600);
  }

  function _setPdfSaveStatus(status, msg) {
    const pill = document.getElementById('doc-pdf-save-pill');
    if (!pill) return;
    const palette = {
      idle:   { txt: '',           bg: 'transparent',           fg: 'transparent' },
      dirty:  { txt: 'Editing…',   bg: 'var(--panel)',          fg: 'var(--fg)' },
      saving: { txt: 'Saving…',    bg: 'var(--panel)',          fg: 'var(--fg)' },
      saved:  { txt: 'Saved',      bg: 'rgba(34,197,94,0.85)',  fg: '#fff' },
      error:  { txt: msg || 'Save failed', bg: 'var(--red)',    fg: 'var(--bg)' },
    };
    const p = palette[status] || palette.idle;
    pill.textContent = p.txt;
    pill.style.background = p.bg;
    pill.style.color = p.fg;
    pill.style.display = p.txt ? '' : 'none';
    if (status === 'saved') {
      setTimeout(() => {
        if (pill.textContent === 'Saved') _setPdfSaveStatus('idle');
      }, 1200);
    }
  }

  async function _savePdfPaneToMarkdown(opts = {}) {
    _pdfPaneSaveTimer = null;
    const docId = activeDocId;
    const fields = _pdfPaneFieldsByDoc.get(docId) || [];
    const annotations = _pdfPaneAnnotationsByDoc.get(docId) || [];
    if (!docId || (!fields.length && !annotations.length)) return false;
    const doc = docs.get(docId);
    if (!doc) return false;

    let md = doc.content || '';
    let changed = 0;
    for (const ref of fields) {
      // Server-side render percent-encodes everything outside [A-Za-z0-9_.-].
      // Match that exactly so spaces / newlines / parens / commas / `?` in
      // raw AcroForm names don't break the regex.
      const encName = _encodeFieldName(ref.name);
      const re = new RegExp(
        `^(\\s*-\\s+)(.*?)(\\s*<!--\\s*field=${encName.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\$&')}\\s+type=\\w+\\s*-->\\s*)$`,
        'm'
      );
      const m = md.match(re);
      if (!m) continue;
      const body = m[2];
      let newBody = body;
      if (ref.type === 'checkbox') {
        const mark = ref.el.checked ? '[x]' : '[ ]';
        newBody = body.replace(/^\s*\[[ xX]\]/, mark);
      } else if (ref.type === 'choice') {
        const v = ref.el.value || '_(not selected)_';
        newBody = body.replace(/(\][\s]*:[ ]*).*$/, `$1${v}`);
      } else if (ref.type === 'signature') {
        const sid = ref.el.dataset.signatureId || '';
        const v = sid ? `signature:${sid}` : '_(unsigned)_';
        newBody = body.replace(/(:\*\*[ ]*).*$/, `$1${v}`);
      } else {
        const v = ref.el.value === '' ? '_(empty)_' : ref.el.value;
        newBody = body.replace(/(:\*\*[ ]*).*$/, `$1${v}`);
      }
      if (newBody !== body) {
        md = md.replace(re, `${m[1]}${newBody}${m[3]}`);
        changed++;
      }
    }
    // Rewrite the freeform-annotations section from the live ref set so
    // creates / edits / moves / deletes all persist in one shot.
    md = _writeAnnotations(md, annotations.map(a => {
      let value = '';
      if (a.kind === 'check') {
        value = '✓';
      } else if (a.kind === 'signature') {
        const sid = a.el && a.el.dataset && a.el.dataset.signatureId;
        value = sid ? `signature:${sid}` : '';
      } else {
        value = (a.el && typeof a.el.value === 'string') ? a.el.value : '';
      }
      return {
        id: a.id, page: a.page, x: a.x, y: a.y, w: a.w, h: a.h,
        kind: a.kind || 'text',
        lineHeight: a.lineHeight || 1.3,
        value,
      };
    }));
    if (md === doc.content) {
      _setPdfSaveStatus('idle');
      return true;
    }
    doc.content = md;
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) ta.value = md;
    _setPdfSaveStatus('saving');
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: md }),
        keepalive: !!opts.keepalive,
      });
      if (!res.ok) {
        const t = await res.text().catch(() => res.statusText);
        _setPdfSaveStatus('error', `Save failed: ${res.status}`);
        console.warn('PDF-pane save HTTP error:', res.status, t);
        return false;
      }
      _setPdfSaveStatus('saved');
      return true;
    } catch (e) {
      _setPdfSaveStatus('error', e.message || 'Save failed');
      console.warn('PDF-pane save failed:', e);
      return false;
    }
  }

  // Flush any pending debounced save before navigating away
  window.addEventListener('beforeunload', () => {
    if (_pdfPaneSaveTimer) {
      clearTimeout(_pdfPaneSaveTimer);
      _savePdfPaneToMarkdown({ keepalive: true });
    }
  });

  async function _refreshPdfPreviewIframe() {
    // Re-render the pane from the backend's current parsed values.
    // Flush any debounced user edit first so we don't clobber it.
    const pane = document.getElementById('doc-pdf-view');
    if (!pane || !activeDocId) return;
    if (pane.style.display === 'none') return;
    if (_pdfPaneSaveTimer) {
      clearTimeout(_pdfPaneSaveTimer);
      await _savePdfPaneToMarkdown();
    }
    _renderPdfPane();
  }

  async function _setPdfViewActive(active) {
    const pane = document.getElementById('doc-pdf-view');
    const wrap = document.getElementById('doc-editor-wrap');
    const btn = document.getElementById('doc-pdf-view-btn');
    if (!pane || !wrap) return;
    if (active) {
      _pdfViewState.set(activeDocId, true);
      wrap.style.display = 'none';
      pane.style.display = '';
      _renderPdfPane();
      btn?.classList.add('active');
    } else {
      // Flush any pending debounced edit before tearing down the field refs.
      if (_pdfPaneSaveTimer) {
        clearTimeout(_pdfPaneSaveTimer);
        await _savePdfPaneToMarkdown();
      }
      _pdfViewState.set(activeDocId, false);
      pane.style.display = 'none';
      // Preserve the save pill across renders
      const savedPill = document.getElementById('doc-pdf-save-pill');
      pane.innerHTML = '';
      if (savedPill) pane.appendChild(savedPill);
      _pdfPaneFieldsByDoc.delete(activeDocId);
      _pdfPaneAnnotationsByDoc.delete(activeDocId);
      wrap.style.display = '';
      btn?.classList.remove('active');
    }
  }

  // Hide the top header bar when nothing in it is visible. With Undo + the type
  // picker moved to the footer, a plain doc on mobile would otherwise show an
  // empty bar (the "second footer"). Reflow-free (reads inline display only) so
  // it's safe to call from _syncHeaderActions on every stream patch. On desktop
  // the bar always shows (it still hosts Fullscreen + the version badge); on
  // mobile it shows only when a contextual control is active.
  function _syncHeaderBarVisibility() {
    const hdr = document.getElementById('doc-editor-actions');
    if (!hdr) return;
    // Email docs hide the whole header (they use their own send footer) — never
    // resurrect it here.
    if (docs.get(activeDocId)?.language === 'email') { hdr.style.display = 'none'; return; }
    const vis = (id) => {
      const e = document.getElementById(id);
      if (!e || !e.parentElement) return false;
      // Only count items still LIVING in the header itself — the runtime
      // rearrangement (~line 3217) moves several buttons into the footer, and
      // we don't want a button parked elsewhere to keep this top row alive.
      if (!hdr.contains(e)) return false;
      return e.style.display !== 'none';
    };
    // Hide the whole header when nothing visible lives here anymore. Without
    // this every desktop view rendered an empty doc-editor-header above the
    // real action footer — a duplicate row.
    const visible = vis('doc-stream-indicator')
      || vis('doc-version-badge')
      || vis('doc-export-pdf-btn')
      || vis('doc-pdf-view-btn');
    hdr.style.display = visible ? '' : 'none';
  }

  function _syncHeaderActions() {
    const actionBtn = document.getElementById('doc-header-preview-btn');
    const exportBtn = document.getElementById('doc-export-pdf-btn');
    const pdfViewBtn = document.getElementById('doc-pdf-view-btn');
    const pdfPane = document.getElementById('doc-pdf-view');
    const langSelect = document.getElementById('doc-language-select');
    const live = document.getElementById('doc-editor-textarea')?.value
      || docs.get(activeDocId)?.content
      || '';
    const isForm = _isFormBackedDoc(live);
    // Footer main button: for a doc opened from an email attachment, morph the
    // Copy button into "Reply" (send the filled file back to the sender via the
    // signed-reply flow). Otherwise it's the normal Copy action. The click
    // handler branches on data-mode.
    const _copyBtn = document.getElementById('doc-footer-copy-btn');
    if (_copyBtn) {
      const _ad = docs.get(activeDocId);
      const _replyable = !!(_ad && _ad.sourceEmailUid && _ad.sourceEmailFolder);
      if (_replyable && _copyBtn.dataset.mode !== 'reply') {
        _copyBtn.dataset.mode = 'reply';
        _copyBtn.title = 'Reply to the sender with this filled file attached';
        _copyBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>Attach';
      } else if (!_replyable && _copyBtn.dataset.mode !== 'copy') {
        _copyBtn.dataset.mode = 'copy';
        _copyBtn.title = 'Copy document';
        _copyBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy';
      }
    }
    // Standalone Export PDF / PDF-toggle icon buttons are retired — for a
    // form-backed doc the language selector itself toggles between
    // "pdf" (rendered view) and "markdown" (source view).
    if (exportBtn) exportBtn.style.display = 'none';
    if (pdfViewBtn) pdfViewBtn.style.display = 'none';
    if (true) {
      const explicit = _pdfViewState.get(activeDocId);
      const active = isForm && explicit !== false;
      // Sync the language select's displayed value to the current view.
      if (isForm && langSelect) {
        const want = active ? 'pdf' : 'markdown';
        if (langSelect.value !== want) langSelect.value = want;
      }
      if (pdfPane) {
        if (active) {
          if (pdfPane.style.display === 'none') {
            const wrap = document.getElementById('doc-editor-wrap');
            if (wrap) wrap.style.display = 'none';
            pdfPane.style.display = '';
            _renderPdfPane();
          }
        } else if (pdfPane.style.display !== 'none') {
          pdfPane.style.display = 'none';
          pdfPane.innerHTML = '';
          const wrap = document.getElementById('doc-editor-wrap');
          if (wrap) wrap.style.display = '';
        }
      }
    }
    if (!actionBtn) return;

    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    const canPreview = ['markdown', 'csv'].includes(lang) || _isRenderLang(lang);
    const canRun = ['javascript', 'js', 'python', 'py', 'bash', 'sh', 'shell', 'zsh'].includes(lang);

    const _eyeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    const _penIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>';
    const _playIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    const _codeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';

    // Check active states
    const _mdPreview = document.getElementById('doc-md-preview');
    const _csvPreview = document.getElementById('doc-csv-preview');
    const _htmlPreview = document.getElementById('doc-html-preview');
    const _outputPanel = document.getElementById('doc-run-output');
    const _mdActive = _mdPreview && _mdPreview.style.display !== 'none';
    const _csvActive = _csvPreview && _csvPreview.style.display !== 'none';
    const _htmlActive = _htmlPreview && _htmlPreview.style.display !== 'none';
    const _outputActive = _outputPanel && _outputPanel.style.display !== 'none';

    let show = false;
    actionBtn.classList.remove('active');

    // The markdown Edit/Preview toggle is a two-icon switch; other modes use
    // the single dynamic preview button.
    const mdToggle = document.getElementById('doc-md-view-toggle');
    if (mdToggle) mdToggle.style.display = (lang === 'markdown') ? 'inline-flex' : 'none';
    const renderToggle = document.getElementById('doc-render-view-toggle');
    if (renderToggle) {
      renderToggle.style.display = _hasViewToggle(lang) ? 'inline-flex' : 'none';
      // Swap the "run" side's icon to match what the language actually does:
      //   CSV → 4-quadrant grid (table view)
      //   HTML / SVG / XML → eye (rendered preview)
      //   Python / JS / TS / bash → play triangle (run code)
      const runBtn = renderToggle.querySelector('[data-renderview="run"]');
      if (runBtn) {
        let icon, title;
        if (lang === 'csv') {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>';
          title = 'Table view';
        } else if (_isRenderLang(lang)) {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
          title = 'Preview';
        } else {
          icon = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
          title = 'Run';
        }
        if (runBtn.dataset.lastIcon !== lang) {
          runBtn.innerHTML = icon;
          runBtn.title = title;
          runBtn.dataset.lastIcon = lang;
        }
      }
      // Swap the "code" side's icon too — CSV's "code" really means "edit
      // the underlying spreadsheet text", so a pencil reads better than the
      // </> brackets used for actual code.
      const codeBtn = renderToggle.querySelector('[data-renderview="code"]');
      if (codeBtn) {
        const codeIco = (lang === 'csv')
          ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>'
          : '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
        const codeTitle = (lang === 'csv') ? 'Edit' : 'Edit code';
        if (codeBtn.dataset.lastIcon !== lang) {
          codeBtn.innerHTML = codeIco;
          codeBtn.title = codeTitle;
          codeBtn.dataset.lastIcon = lang;
        }
      }
      // Reflect which side is currently active so the toggle shows the same
      // visual feedback markdown's Edit/Preview switch does (background tint
      // + the "punch" pop animation from .md-view-toggle .md-view-opt.active).
      // For CSV the run side = table view; for HTML/SVG/XML = iframe preview;
      // for runnable langs = output panel open.
      let _viewActive = false;
      if (lang === 'csv') _viewActive = _csvActive;
      else if (_isRenderLang(lang)) _viewActive = _htmlActive;
      else _viewActive = _outputActive;
      const _codeBtn2 = renderToggle.querySelector('[data-renderview="code"]');
      const _runBtn2 = renderToggle.querySelector('[data-renderview="run"]');
      _codeBtn2?.classList.toggle('active', !_viewActive);
      _runBtn2?.classList.toggle('active', _viewActive);
    }

    if (lang === 'markdown') {
      show = false;
      if (mdToggle) {
        mdToggle.querySelector('[data-mdview="edit"]')?.classList.toggle('active', !_mdActive);
        mdToggle.querySelector('[data-mdview="preview"]')?.classList.toggle('active', _mdActive);
      }
    } else if (lang === 'csv') {
      show = true;
      actionBtn.innerHTML = _csvActive ? _penIco : '<span style="font-size:12px;font-weight:600;">⊞</span>';
      actionBtn.title = _csvActive ? 'Edit' : 'Table View';
      if (_csvActive) actionBtn.classList.add('active');
    } else if (_isRenderLang(lang)) {
      // SVG/HTML/XML use the segmented Code </> | Run ▶ light-switch toggle
      // (like markdown's edit/preview switch) instead of the single button.
      show = false;
      if (renderToggle) {
        renderToggle.querySelector('[data-renderview="code"]')?.classList.toggle('active', !_htmlActive);
        renderToggle.querySelector('[data-renderview="run"]')?.classList.toggle('active', _htmlActive);
      }
    } else if (canRun) {
      show = true;
      actionBtn.innerHTML = _outputActive ? _codeIco : _playIco;
      actionBtn.title = _outputActive ? 'Hide output' : 'Run';
      if (_outputActive) actionBtn.classList.add('active');
    }

    // The unified segmented Code/Run-or-View toggle (`#doc-render-view-toggle`)
    // covers CSV / Python / JS / bash / HTML / SVG / XML. When it's shown,
    // suppress the single morph button to avoid two redundant controls.
    if (_hasViewToggle(lang)) show = false;
    actionBtn.style.display = show ? '' : 'none';

    // Now that the contextual buttons' visibility is settled, collapse the bar
    // if it ended up empty (the common plain-doc-on-mobile case).
    _syncHeaderBarVisibility();
  }

  // ── Email document type helpers ──

  function _parseEmailHeader(content) {
    const empty = { to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', attachments: [], body: content || '' };
    if (!content) return empty;
    const parts = content.split(/\n---\n/);
    if (parts.length < 2) return empty;
    const header = parts[0];
    const body = parts.slice(1).join('\n---\n');
    const fields = { to: '', cc: '', bcc: '', subject: '', inReplyTo: '', references: '', sourceUid: '', sourceFolder: '', attachments: [], body: body };
    for (const line of header.split('\n')) {
      const m = line.match(/^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder|X-Attachments):\s*(.*)$/i);
      if (m) {
        let key = m[1].toLowerCase();
        if (key === 'in-reply-to') key = 'inReplyTo';
        else if (key === 'x-source-uid') key = 'sourceUid';
        else if (key === 'x-source-folder') key = 'sourceFolder';
        else if (key === 'x-attachments') {
          fields.attachments = m[2].trim().split('|').map(a => {
            const [index, filename, size] = a.split(':');
            return { index: parseInt(index), filename, size: parseInt(size) };
          });
          continue;
        }
        fields[key] = m[2].trim();
      }
    }
    return fields;
  }

  function _buildEmailContent(to, subject, inReplyTo, references, body, sourceUid, sourceFolder, cc, bcc) {
    let header = `To: ${to}`;
    if (cc) header += `\nCc: ${cc}`;
    if (bcc) header += `\nBcc: ${bcc}`;
    header += `\nSubject: ${subject}`;
    if (inReplyTo) header += `\nIn-Reply-To: ${inReplyTo}`;
    if (references) header += `\nReferences: ${references}`;
    if (sourceUid) header += `\nX-Source-UID: ${sourceUid}`;
    if (sourceFolder) header += `\nX-Source-Folder: ${sourceFolder}`;
    return header + '\n---\n' + body;
  }

  // ── WYSIWYG email body helpers ──
  function _emailBodyToHtml(text) {
    const t = (text || '').trim();
    if (!t) return '';
    // If it already contains a formatting/structural HTML tag, it's a saved
    // WYSIWYG body — use it verbatim. (Checking a leading '<' isn't enough: a
    // rich body often starts with plain text, e.g. "Hi <b>there</b>".)
    if (/<\/?(b|i|u|s|strong|em|del|strike|a|p|div|br|ul|ol|li|h[1-3]|blockquote|span|code|pre)\b[^>]*>/i.test(t)) return t;
    // Email body: keep author-typed `:shortcode:` text literal. Issue #345
    // (shortcode → emoji) is scoped to chat; do not rewrite colons in mail.
    try { return markdownModule.mdToHtml(text, { shortcodes: false }); }
    catch (_) {
      const d = document.createElement('div'); d.textContent = text;
      return d.innerHTML.replace(/\n/g, '<br>');
    }
  }
  // Mirror the rich body's plain text into the hidden textarea so the existing
  // send / draft / change-detection plumbing (which reads the textarea) stays
  // valid. The rich body's HTML is read separately on send (body_html).
  function _syncEmailRichbody(rich) {
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    ta.value = rich.innerText;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function _wireEmailRichbody(rich) {
    if (rich._wired) { _syncEmailRichbody(rich); return; }
    rich._wired = true;
    rich.addEventListener('input', () => _syncEmailRichbody(rich));
    // Highlight toolbar buttons (B / I / S, headings, lists) when the caret
    // sits inside formatted text. queryCommandState reflects the live
    // selection — we just translate that into .is-active classes the CSS
    // already understands.
    const syncActive = () => {
      if (!rich.isConnected || rich.style.display === 'none') return;
      // Only sync when focus is inside the rich body — otherwise selection
      // outside it (e.g. clicking the toolbar itself) gives misleading state.
      if (!rich.contains(document.activeElement) && document.activeElement !== rich) return;
      const tb = document.getElementById('doc-md-toolbar');
      if (!tb) return;
      const set = (sel, on) => { const b = tb.querySelector(sel); if (b) b.classList.toggle('is-active', !!on); };
      try {
        set('[data-md="bold"]',   document.queryCommandState('bold'));
        set('[data-md="italic"]', document.queryCommandState('italic'));
        set('[data-md="strike"]', document.queryCommandState('strikeThrough'));
      } catch (_) {}
      // Block-level: heading / list dropdown toggles read their active state
      // from the current block tag.
      const cur = _currentBlockTag(rich);
      const hBtn = tb.querySelector('[data-dd="heading"]');
      if (hBtn) hBtn.classList.toggle('is-active', cur === 'h1' || cur === 'h2' || cur === 'h3');
      try {
        const inList = document.queryCommandState('insertOrderedList') || document.queryCommandState('insertUnorderedList');
        const lBtn = tb.querySelector('[data-dd="list"]');
        if (lBtn) lBtn.classList.toggle('is-active', !!inList);
      } catch (_) {}
    };
    rich.addEventListener('keyup',    syncActive);
    rich.addEventListener('mouseup',  syncActive);
    rich.addEventListener('focus',    syncActive);
    rich.addEventListener('input',    syncActive);
    // selectionchange fires on the document; filter to selections inside rich.
    document.addEventListener('selectionchange', () => {
      const sel = window.getSelection();
      if (sel && sel.rangeCount && rich.contains(sel.anchorNode)) syncActive();
    });
    rich._syncActive = syncActive;
  }
  function _emailRichbodyActive() {
    const r = document.getElementById('doc-email-richbody');
    return r && r.style.display !== 'none' ? r : null;
  }

  function _captureEmailBodyFocusState() {
    const rich = _emailRichbodyActive();
    const ta = document.getElementById('doc-editor-textarea');
    const active = document.activeElement;
    if (rich && (active === rich || rich.contains(active))) {
      const sel = window.getSelection();
      const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
      return {
        type: 'rich',
        range: range && rich.contains(range.commonAncestorContainer) ? range.cloneRange() : null,
      };
    }
    if (ta && active === ta) {
      return {
        type: 'textarea',
        start: ta.selectionStart,
        end: ta.selectionEnd,
      };
    }
    return null;
  }

  function _restoreEmailBodyFocusState(state) {
    if (!state) return;
    requestAnimationFrame(() => {
      if (state.type === 'rich') {
        const rich = _emailRichbodyActive();
        if (!rich) return;
        rich.focus({ preventScroll: true });
        if (state.range) {
          const sel = window.getSelection();
          if (sel) {
            sel.removeAllRanges();
            sel.addRange(state.range);
          }
        }
      } else if (state.type === 'textarea') {
        const ta = document.getElementById('doc-editor-textarea');
        if (!ta) return;
        ta.focus({ preventScroll: true });
        if (Number.isFinite(state.start) && Number.isFinite(state.end)) {
          try { ta.setSelectionRange(state.start, state.end); } catch (_) {}
        }
      }
    });
  }

  function _stripEmailReplyQuoteText(text) {
    const original = String(text || '');
    if (!original) return { body: '', stripped: false };
    const lines = original.split('\n');
    const quoteIdx = lines.findIndex(line =>
      /^-{5,}\s*Previous message\s*-{5,}$/i.test(line.trim())
      || /^On .+ wrote:\s*$/i.test(line.trim())
    );
    if (quoteIdx <= 0) return { body: original.trim(), stripped: false };
    const body = lines.slice(0, quoteIdx).join('\n').trim();
    return { body, stripped: !!body };
  }

  function _emailReplyOwnText(text) {
    return _stripEmailReplyQuoteText(text).body;
  }

  function _setEmailBodyText(textarea, value) {
    if (!textarea) return;
    textarea.value = value || '';
    syncHighlighting();
    const rich = _emailRichbodyActive();
    if (rich) rich.innerHTML = _emailBodyToHtml(textarea.value);
  }

  async function _streamEmailBodyText(textarea, value) {
    if (!textarea) return;
    const finalText = String(value || '');
    const maxFrames = 90;
    const chunk = Math.max(8, Math.ceil(finalText.length / maxFrames));
    textarea.value = '';
    const rich = _emailRichbodyActive();
    if (rich) rich.innerHTML = '';
    for (let i = 0; i < finalText.length; i += chunk) {
      const next = finalText.slice(0, i + chunk);
      textarea.value = next;
      if (rich) rich.innerHTML = _emailBodyToHtml(next);
      await new Promise(resolve => requestAnimationFrame(resolve));
    }
    _setEmailBodyText(textarea, finalText);
  }

  function _focusEmailBodyEnd() {
    const target = _emailRichbodyActive() || document.getElementById('doc-editor-textarea');
    if (!target) return;
    target.focus();
    if (target.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(target);
      range.collapse(false);
      const sel = window.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } else if (typeof target.setSelectionRange === 'function') {
      const len = target.value.length;
      target.setSelectionRange(len, len);
    }
  }

  function _syncEmailHeaderSummary() {
    const to = document.getElementById('doc-email-to')?.value?.trim() || 'No recipient';
    const subject = document.getElementById('doc-email-subject')?.value?.trim() || 'No subject';
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const summary = document.getElementById('doc-email-collapse-summary');
    if (!summary) return;
    const extras = [];
    if (cc) extras.push('Cc');
    if (bcc) extras.push('Bcc');
    summary.textContent = `${to} · ${subject}${extras.length ? ` · ${extras.join('/')}` : ''}`;
    summary.title = summary.textContent;
  }

  function _setEmailHeaderCollapsed(collapsed, { manual = true } = {}) {
    const header = document.getElementById('doc-email-header');
    const btn = document.getElementById('doc-email-collapse-btn');
    if (!header) return;
    if (window.innerWidth > 768) collapsed = false;
    header.classList.toggle('doc-email-header-collapsed', !!collapsed);
    if (btn) {
      btn.setAttribute('aria-expanded', String(!collapsed));
      btn.title = collapsed ? 'Show email fields' : 'Hide email fields';
    }
    const doc = activeDocId && docs.get(activeDocId);
    if (doc && manual) doc._emailHeaderCollapsed = !!collapsed;
    if (manual && !collapsed) _emailHeaderManualExpandUntil = Date.now() + 1400;
    _syncEmailHeaderSummary();
  }

  function _shouldAutoCollapseEmailHeader() {
    return window.innerWidth <= 768;
  }

  function _maybeAutoCollapseEmailHeader() {
    const doc = activeDocId && docs.get(activeDocId);
    if (!doc || doc.language !== 'email') return;
    if (Date.now() < _emailHeaderManualExpandUntil) return;
    if (document.activeElement?.closest?.('#doc-email-fields')) return;
    if (_shouldAutoCollapseEmailHeader()) _setEmailHeaderCollapsed(true, { manual: false });
  }

  function _showEmailFields(doc) {
    const emailHeader = document.getElementById('doc-email-header');
    const emailActions = document.getElementById('doc-email-actions');
    // Show MD toolbar for email too (B, I, etc.)
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar) {
      mdToolbar.style.display = '';
      if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
    }
    // Hide toolbar items that have no clean WYSIWYG equivalent in email (Code).
    document.querySelectorAll('.md-toolbar-email-hide').forEach(el => { el.style.display = 'none'; });
    // Show email-only toolbar items (AI reply button).
    document.querySelectorAll('.md-toolbar-email-only').forEach(el => { el.style.display = 'inline-flex'; });
    if (emailHeader) emailHeader.style.display = '';
    if (emailActions) emailActions.style.display = '';
    // Emails have their own complete footer (Close / More / Send), so hide the
    // generic documents action bar AND the generic bottom footer. The TYPE
    // picker is the exception — relocate it into the email footer so the
    // type-switching affordance is in the same footer slot across all docs.
    const docActions = document.getElementById('doc-editor-actions');
    if (docActions) docActions.style.display = 'none';
    const docFooter = document.getElementById('doc-actions-footer');
    if (docFooter) docFooter.style.display = 'none';
    if (emailActions) {
      const _lang = document.getElementById('doc-language-select');
      const _sendSplit = emailActions.querySelector('.email-send-split');
      if (_lang && _sendSplit) emailActions.insertBefore(_lang, _sendSplit);
    }
    // Colored system-emoji font for email compose
    document.getElementById('doc-editor-textarea')?.classList.add('email-mode');
    document.getElementById('doc-editor-code')?.classList.add('email-mode');
    document.getElementById('doc-editor-highlight')?.classList.add('email-mode');
    const fields = _parseEmailHeader(doc.content || '');
    const toInput = document.getElementById('doc-email-to');
    const subjectInput = document.getElementById('doc-email-subject');
    const inReplyTo = document.getElementById('doc-email-in-reply-to');
    const refs = document.getElementById('doc-email-references');
    const textarea = document.getElementById('doc-editor-textarea');
    if (toInput) toInput.value = fields.to;
    if (subjectInput) subjectInput.value = fields.subject;
    _setEmailHeaderCollapsed(!!(doc && doc._emailHeaderCollapsed), { manual: false });
    if (subjectInput && !subjectInput._emailTabBodyBound) {
      subjectInput._emailTabBodyBound = true;
      subjectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Tab' && !e.shiftKey) {
          e.preventDefault();
          _focusEmailBodyEnd();
        }
      });
    }
    if (inReplyTo) inReplyTo.value = fields.inReplyTo;
    if (refs) refs.value = fields.references;
    const sourceUid = document.getElementById('doc-email-source-uid');
    const sourceFolder = document.getElementById('doc-email-source-folder');
    if (sourceUid) sourceUid.value = fields.sourceUid || '';
    if (sourceFolder) sourceFolder.value = fields.sourceFolder || '';
    // Show/hide unread button only if we have a source UID (came from inbox)
    const unreadBtn = document.getElementById('doc-email-unread-btn');
    if (unreadBtn) unreadBtn.style.display = fields.sourceUid ? '' : 'none';
    // Render attachment chips
    const attDiv = document.getElementById('doc-email-attachments');
    if (attDiv) {
      attDiv.innerHTML = '';
      if (fields.attachments && fields.attachments.length > 0 && fields.sourceUid) {
        attDiv.style.display = '';
        for (const att of fields.attachments) {
          const isPdf = (att.filename || '').toLowerCase().endsWith('.pdf');
          const sizeKb = att.size > 0 ? `${Math.round(att.size / 1024)} KB` : '';
          const chipHtml = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg><span>${_escHtml(att.filename)}</span><span class="att-size">${sizeKb}</span>`;
          // Helper: swap chip content for a whirlpool spinner while busy.
          const _withSpinner = async (chip, fn) => {
            if (chip.dataset.loading === '1') return;
            chip.dataset.loading = '1';
            const orig = chip.innerHTML;
            chip.innerHTML = '';
            const sp = spinnerModule.createWhirlpool(14);
            sp.style.marginRight = '6px';
            chip.appendChild(sp);
            const lbl = document.createElement('span');
            lbl.textContent = att.filename;
            chip.appendChild(lbl);
            try { await fn(); }
            finally { chip.dataset.loading = ''; chip.innerHTML = orig; }
          };
          if (isPdf) {
            // PDF: open in the in-app PDF viewer as a new doc tab
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'email-attachment-chip email-attachment-chip-pdf';
            // Full filename on hover — chip ellipsis-truncates long names.
            chip.title = att.filename;
            chip.innerHTML = chipHtml;
            chip.addEventListener('click', () => _withSpinner(chip, async () => {
              try {
                const folderQs = encodeURIComponent(fields.sourceFolder || 'INBOX');
                const res = await fetch(`${API_BASE}/api/email/attachment-as-doc/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`, { method: 'POST' });
                const data = await res.json();
                if (data.doc_id) {
                  await loadDocument(data.doc_id);
                } else if (uiModule) {
                  uiModule.showError(data.error || 'Failed to open PDF');
                  window.open(`${API_BASE}/api/email/attachment/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`, '_blank');
                }
              } catch (e) {
                console.error('Open PDF attachment failed:', e);
                if (uiModule) uiModule.showError('Failed to open PDF');
              }
            }));
            attDiv.appendChild(chip);
          } else {
            // Non-PDF: download via fetch+blob+anchor — browser-native download
            // with target=_blank was unreliable in some browsers (the click did
            // nothing). The blob path forces a real Save dialog every time.
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'email-attachment-chip';
            // Full filename on hover for the chip ellipsis-truncated label.
            chip.title = `Download ${att.filename}`;
            chip.innerHTML = chipHtml;
            chip.addEventListener('click', () => _withSpinner(chip, async () => {
              try {
                const folderQs = encodeURIComponent(fields.sourceFolder || 'INBOX');
                const res = await fetch(`${API_BASE}/api/email/attachment/${encodeURIComponent(fields.sourceUid)}/${att.index}?folder=${folderQs}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url; a.download = att.filename;
                document.body.appendChild(a);
                a.click();
                a.remove();
                setTimeout(() => URL.revokeObjectURL(url), 1000);
              } catch (e) {
                console.error('Download attachment failed:', e);
                if (uiModule) uiModule.showError('Download failed: ' + e.message);
              }
            }));
            attDiv.appendChild(chip);
          }
        }
      } else {
        attDiv.style.display = 'none';
      }
    }
    if (textarea) {
      textarea.value = fields.body;
      // Store original body for change detection on close
      if (doc) doc._originalBody = fields.body;
      syncHighlighting();
    }
    // WYSIWYG: swap the source editor for the rich body and render the markdown.
    // The textarea above stays as the plain-text mirror (kept in sync below) so
    // send / draft / change-detection still read it.
    const _rich = document.getElementById('doc-email-richbody');
    const _srcWrap = document.getElementById('doc-editor-wrap');
    if (_rich && _srcWrap) {
      _srcWrap.style.display = 'none';
      _rich.style.display = '';
      _rich.innerHTML = _emailBodyToHtml(fields.body);
      _wireEmailRichbody(_rich);
      setTimeout(() => {
        try {
          const _isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0;
          if (!_isTouch) _rich.focus();
          _rich.scrollTop = 0;
        } catch (_) {}
      }, 50);
    }
    // Render compose attachments (if any uploaded for this doc)
    _renderComposeAttachments();
    // Populate CC/BCC from parsed header, show rows if populated
    const ccRow = document.getElementById('doc-email-cc-row');
    const bccRow = document.getElementById('doc-email-bcc-row');
    const ccToggle = document.getElementById('doc-email-show-cc');
    const ccInput = document.getElementById('doc-email-cc');
    const bccInput = document.getElementById('doc-email-bcc');
    if (ccInput) ccInput.value = fields.cc || '';
    if (bccInput) bccInput.value = fields.bcc || '';
    const hasCcBcc = !!(fields.cc || fields.bcc);
    if (ccRow) ccRow.style.display = hasCcBcc ? '' : 'none';
    if (bccRow) bccRow.style.display = hasCcBcc ? '' : 'none';
    if (ccToggle) ccToggle.style.display = hasCcBcc ? 'none' : '';
    _syncEmailHeaderSummary();
  }

  async function _uploadComposeFiles(files) {
    const list = Array.from(files || []);
    if (list.length === 0) return;
    const doc = docs.get(activeDocId);
    if (!doc) return;
    if (doc.language !== 'email') return;
    if (!doc._composeAtts) doc._composeAtts = [];

    for (const file of list) {
      try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await fetch(`${API_BASE}/api/email/compose-upload`, {
          method: 'POST',
          body: fd,
        });
        const data = await res.json();
        if (data.success) {
          doc._composeAtts.push({
            token: data.token,
            filename: data.filename,
            size: data.size,
          });
        } else {
          if (uiModule) uiModule.showError(`Failed to upload ${file.name}: ${data.error || ''}`);
        }
      } catch (err) {
        if (uiModule) uiModule.showError(`Failed to upload ${file.name}`);
      }
    }
    _renderComposeAttachments();
  }

  async function _handleAttachUpload(e) {
    const files = e.target.files;
    e.target.value = ''; // reset for next upload
    await _uploadComposeFiles(files);
  }

  function _renderComposeAttachments() {
    const container = document.getElementById('doc-email-compose-atts');
    if (!container) return;
    const doc = docs.get(activeDocId);
    const atts = doc?._composeAtts || [];
    if (atts.length === 0) {
      container.style.display = 'none';
      container.innerHTML = '';
      return;
    }
    container.style.display = '';
    container.innerHTML = '';
    for (const att of atts) {
      const chip = document.createElement('span');
      chip.className = 'email-compose-chip';
      const sizeKb = att.size > 0 ? `${Math.round(att.size / 1024)} KB` : '';
      chip.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        <span class="compose-chip-name">${_escHtml(att.filename)}</span>
        <span class="att-size">${sizeKb}</span>
        <button class="compose-chip-remove" title="Remove">×</button>
      `;
      chip.querySelector('.compose-chip-remove').addEventListener('click', async (e) => {
        e.stopPropagation();
        try {
          await fetch(`${API_BASE}/api/email/compose-upload/${encodeURIComponent(att.token)}`, { method: 'DELETE' });
        } catch (_) {}
        const d = docs.get(activeDocId);
        if (d) d._composeAtts = d._composeAtts.filter(a => a.token !== att.token);
        _renderComposeAttachments();
      });
      container.appendChild(chip);
    }
  }

  // Split a To/Cc/Bcc text field into recipients + the in-progress fragment
  // the user is currently typing (after the last comma). Returns a tuple so
  // we can show suggestions for just the fragment without disturbing the
  // already-confirmed recipients.
  function _splitRecipientsAndFragment(rawValue) {
    const cut = (rawValue || '').lastIndexOf(',');
    if (cut < 0) return { confirmed: '', fragment: (rawValue || '').trimStart() };
    return {
      confirmed: rawValue.slice(0, cut + 1).trimStart(),
      fragment: rawValue.slice(cut + 1).trimStart(),
    };
  }

  // Replace the in-progress fragment in `input` with the chosen email,
  // append ", " so the user can type the next recipient immediately, then
  // hide the suggestion dropdown.
  function _commitRecipient(input, sugg, email) {
    if (!input) return;
    const { confirmed } = _splitRecipientsAndFragment(input.value);
    // Preserve a single trailing space between commas for readability.
    const head = confirmed ? confirmed.replace(/\s+$/, '') + ' ' : '';
    input.value = head + email + ', ';
    if (sugg) sugg.style.display = 'none';
    input.focus();
    // Caret to end so the next keystroke lands in the right place.
    const end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (_) {}
  }

  // Search contacts for an autocomplete dropdown. `input` is the To/Cc/Bcc
  // text field, `sugg` is its sibling .email-autocomplete div. Suggestions
  // are scoped to the LAST comma-separated fragment so already-entered
  // recipients aren't disturbed.
  async function _searchContacts(input, sugg) {
    if (!input || !sugg) return;
    const { fragment } = _splitRecipientsAndFragment(input.value);
    if (!fragment || fragment.length < 1) { sugg.style.display = 'none'; return; }
    try {
      const res = await fetch(`${API_BASE}/api/contacts/search?q=${encodeURIComponent(fragment)}`);
      const data = await res.json();
      if (!data.results || data.results.length === 0) {
        sugg.style.display = 'none';
        return;
      }
      // Already-entered emails in this field — skip in the dropdown so
      // users don't accidentally add the same person twice.
      const already = new Set(
        (input.value || '').split(',').map(s => {
          const m = s.match(/<([^>]+)>/);
          return (m ? m[1] : s).trim().toLowerCase();
        }).filter(Boolean)
      );
      sugg.innerHTML = '';
      let count = 0;
      for (const c of data.results) {
        for (const em of (c.emails || [])) {
          if (already.has(em.toLowerCase())) continue;
          const item = document.createElement('div');
          item.className = 'contact-suggestion';
          item.innerHTML = `<span class="contact-name">${_escHtml(c.name)}</span><span class="contact-email">${_escHtml(em)}</span>`;
          // mousedown fires before blur so the click doesn't get lost
          item.addEventListener('mousedown', (e) => { e.preventDefault(); _commitRecipient(input, sugg, em); });
          item.addEventListener('click', (e) => { e.preventDefault(); _commitRecipient(input, sugg, em); });
          sugg.appendChild(item);
          count += 1;
        }
      }
      if (count === 0) { sugg.style.display = 'none'; return; }
      // Auto-highlight first suggestion so Enter accepts it.
      const first = sugg.querySelector('.contact-suggestion');
      if (first) first.classList.add('active');
      sugg.style.display = '';
    } catch (e) {
      sugg.style.display = 'none';
    }
  }

  // Bind input/keydown/blur for a recipient field so it gets the same
  // autocomplete-and-commit behavior. Used by To/Cc/Bcc.
  function _wireRecipientAutocomplete(inputId, suggId) {
    const input = document.getElementById(inputId);
    const sugg = document.getElementById(suggId);
    if (!input || !sugg) return;
    let timer = null;
    input.addEventListener('input', () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => _searchContacts(input, sugg), 150);
    });
    input.addEventListener('blur', () => {
      setTimeout(() => { sugg.style.display = 'none'; }, 200);
    });
    input.addEventListener('keydown', (e) => {
      const open = sugg.style.display !== 'none';
      const items = open ? sugg.querySelectorAll('.contact-suggestion') : [];
      const active = open ? sugg.querySelector('.contact-suggestion.active') : null;
      let idx = active ? Array.from(items).indexOf(active) : -1;
      if (open && e.key === 'ArrowDown') {
        e.preventDefault();
        idx = Math.min(items.length - 1, idx + 1);
        items.forEach(it => it.classList.remove('active'));
        if (items[idx]) items[idx].classList.add('active');
      } else if (open && e.key === 'ArrowUp') {
        e.preventDefault();
        idx = Math.max(0, idx - 1);
        items.forEach(it => it.classList.remove('active'));
        if (items[idx]) items[idx].classList.add('active');
      } else if (e.key === 'Enter') {
        // If a suggestion is highlighted, commit it. Otherwise — if the
        // current fragment already looks like a complete email — commit
        // the raw text so users who type a brand-new address don't have
        // to add the comma themselves.
        if (active) {
          e.preventDefault();
          const em = active.querySelector('.contact-email')?.textContent?.trim();
          if (em) _commitRecipient(input, sugg, em);
        } else {
          const { fragment } = _splitRecipientsAndFragment(input.value);
          if (/^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/.test(fragment.trim())) {
            e.preventDefault();
            _commitRecipient(input, sugg, fragment.trim());
          }
        }
      } else if (e.key === 'Tab' && active) {
        e.preventDefault();
        const em = active.querySelector('.contact-email')?.textContent?.trim();
        if (em) _commitRecipient(input, sugg, em);
      } else if (e.key === 'Escape') {
        sugg.style.display = 'none';
      } else if (e.key === ',' || (e.key === ' ' && input.value.trim().endsWith(','))) {
        // Typing a comma directly also accepts a highlighted suggestion.
        if (active) {
          e.preventDefault();
          const em = active.querySelector('.contact-email')?.textContent?.trim();
          if (em) _commitRecipient(input, sugg, em);
        }
      }
    });
  }

  function _hideEmailFields() {
    const emailHeader = document.getElementById('doc-email-header');
    const emailActions = document.getElementById('doc-email-actions');
    if (emailHeader) emailHeader.style.display = 'none';
    if (emailActions) emailActions.style.display = 'none';
    // Restore toolbar items that were hidden for email (Code dropdown).
    document.querySelectorAll('.md-toolbar-email-hide').forEach(el => { el.style.display = ''; });
    // Re-hide email-only toolbar items (AI reply button).
    document.querySelectorAll('.md-toolbar-email-only').forEach(el => { el.style.display = 'none'; });
    // Restore the generic documents action bar + its bottom footer (Close /
    // Copy / Export) for non-email docs.
    const docActions = document.getElementById('doc-editor-actions');
    if (docActions) docActions.style.display = '';
    const docFooter = document.getElementById('doc-actions-footer');
    if (docFooter) docFooter.style.display = '';
    // Return the type picker to its non-email home (right before the
    // Copy/Export split) — _showEmailFields moved it into the email footer.
    if (docFooter) {
      const _lang = document.getElementById('doc-language-select');
      const _split = docFooter.querySelector('#doc-copy-export-split');
      if (_lang && _split) docFooter.insertBefore(_lang, _split);
    }
    // Restore the source editor and hide the WYSIWYG email body.
    const _rich = document.getElementById('doc-email-richbody');
    if (_rich) _rich.style.display = 'none';
    const _srcWrap = document.getElementById('doc-editor-wrap');
    if (_srcWrap) _srcWrap.style.display = '';
    // Drop the email-mode class so editors return to monospace monochrome
    document.getElementById('doc-editor-textarea')?.classList.remove('email-mode');
    document.getElementById('doc-editor-code')?.classList.remove('email-mode');
    document.getElementById('doc-editor-highlight')?.classList.remove('email-mode');
  }

  const _ATTACH_RE = /\b(attach(ed|ment|ments|ing)?|enclosed|enclosing|PFA|find attached|see attached|ci-joint|en pi[eè]ce jointe|ajout[eé]|joint|jointe|anbei|im Anhang|beigef[uü]gt|添付|fichier joint)\b/i;

  function _bodyMentionsAttachment(text) {
    if (!text) return false;
    // Only check the user's own text, not quoted replies
    const parts = text.split(/^>|^On .* wrote:/m);
    const own = parts[0] || '';
    return _ATTACH_RE.test(own);
  }

  function _confirmMissingAttachment() {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'modal';
      overlay.style.display = 'flex';
      overlay.innerHTML = `
        <div class="modal-content" style="width:360px;max-width:90vw;">
          <div class="modal-header"><h4>No attachments found</h4></div>
          <div class="modal-body" style="padding:16px;font-size:13px;opacity:0.8;">
            Your message mentions an attachment, but nothing is attached. Send anyway?
          </div>
          <div class="modal-footer" style="display:flex;gap:8px;justify-content:flex-end;">
            <button class="memory-toolbar-btn" id="att-warn-cancel">Go back</button>
            <button class="memory-toolbar-btn" id="att-warn-send" style="background:var(--accent-primary,var(--red));color:#fff;border-color:var(--accent-primary,var(--red));">Send anyway</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);
      const cleanup = (val) => { overlay.remove(); resolve(val); };
      overlay.querySelector('#att-warn-cancel').addEventListener('click', () => cleanup(false));
      overlay.querySelector('#att-warn-send').addEventListener('click', () => cleanup(true));
      overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(false); });
    });
  }

  async function _sendEmail() {
    const sendDocId = activeDocId;
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim();
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
    // WYSIWYG: the rich body's HTML becomes the email's HTML part (server
    // sanitizes it). `body` (plain text mirror) stays the text/plain fallback.
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const textarea = document.getElementById('doc-editor-textarea');
    const body = (_rich ? (_rich.innerText || _rich.textContent || '') : (textarea?.value || '')).trim();
    const bodyHtml = _rich ? _rich.innerHTML : null;
    const doc = docs.get(activeDocId);
    const attachments = (doc?._composeAtts || []).map(a => a.token);
    if (!to || !body) {
      if (uiModule) uiModule.showError('To and body are required');
      return;
    }
    if (inReplyTo && !_emailReplyOwnText(body)) {
      if (uiModule) uiModule.showError('Reply body is empty');
      return;
    }
    // Warn if body mentions attachments but none are actually attached
    if (attachments.length === 0 && _bodyMentionsAttachment(body)) {
      const proceed = await _confirmMissingAttachment();
      if (!proceed) return;
    }
    const btn = document.getElementById('doc-email-send-btn');
    const _sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    let sendSpinner = null;
    let origBtnHtml = '';
    let detachedEmailDoc = null;
    if (btn) {
      btn.disabled = true;
      origBtnHtml = btn.innerHTML;
      sendSpinner = spinnerModule.createWhirlpool(14);
      sendSpinner.element.style.cssText = 'display:inline-block;vertical-align:-2px;margin-right:6px;width:14px;height:14px;';
      btn.innerHTML = '';
      btn.appendChild(sendSpinner.element);
      btn.appendChild(document.createTextNode('Sending'));
    }
    try {
      let canceled = false;
      if (uiModule) {
        uiModule.showToast('Sending', {
          duration: 3200,
          leadingIcon: 'spinner',
          action: 'Cancel',
          onAction: () => { canceled = true; },
        });
      }
      await _sleep(3000);
      if (!canceled) detachedEmailDoc = _detachActiveEmailForBackground(sendDocId);
      await _sleep(200);
      if (canceled) {
        _restoreDetachedEmailDoc(detachedEmailDoc);
        detachedEmailDoc = null;
        if (uiModule) uiModule.showToast('Send canceled');
        return;
      }

      const activeAccountId = await _resolveComposeSendAccountId();
      const res = await fetch(`${API_BASE}/api/email/send`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to, cc: cc || null, bcc: bcc || null, subject, body, body_html: bodyHtml,
          in_reply_to: inReplyTo || null, references: references || null,
          attachments: attachments.length > 0 ? attachments : null,
          account_id: activeAccountId,
          wait_for_delivery: true,
        }),
      });
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = { success: false, error: `Send failed (${res.status})` };
      }
      if (!res.ok && data && !data.error) data.error = `Send failed (${res.status})`;
      if (data.success) {
        if (uiModule) {
          uiModule.showToast('Message sent', {
            duration: 7000,
            leadingIcon: 'check',
            action: 'View Message',
            onAction: () => {
              import('./emailLibrary.js').then(mod => {
                const open = mod.openEmailLibrary || (mod.default && mod.default.openEmailLibrary);
                if (open) open({
                  account_id: data.account_id || activeAccountId || null,
                  folder: data.sent_folder || 'Sent',
                  uid: data.sent_uid || null,
                });
              }).catch(() => {});
            },
          });
        }
        // Auto-save recipients to the configured contacts backend (CardDAV).
        // The compose fields accept plain emails and "Name <email>" chips.
        const _contactPieces = [to, cc, bcc].join(',').split(/[,;]/).map(s => s.trim()).filter(Boolean);
        const _seenContacts = new Set();
        for (const piece of _contactPieces) {
          const match = piece.match(/^(.*?)<([^>]+)>$/);
          const email = (match ? match[2] : piece).trim();
          const name = (match ? match[1] : '').replace(/^["']|["']$/g, '').trim();
          if (!email || !/@/.test(email)) continue;
          const key = email.toLowerCase();
          if (_seenContacts.has(key)) continue;
          _seenContacts.add(key);
          fetch(`${API_BASE}/api/contacts/add`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email }),
          }).catch(() => {});
        }
        // Mark the source email as answered if this was a reply
        if (sourceUid) {
          fetch(`${API_BASE}/api/email/mark-answered/${sourceUid}?folder=${encodeURIComponent(sourceFolder)}`, { method: 'POST' }).catch(() => {});
          // Tell the inbox to refresh so the answered state shows
          window.dispatchEvent(new CustomEvent('email-answered', { detail: { uid: sourceUid } }));
        }
        // Delete the compose document after successful send. It was usually
        // already detached from the visible tabs so sending can finish in the
        // background while the user continues in the next tab.
        if (sendDocId) {
          fetch(`${API_BASE}/api/document/${sendDocId}`, { method: 'DELETE' }).catch(() => {});
          const wasActiveSentDoc = activeDocId === sendDocId;
          docs.delete(sendDocId);
          if (wasActiveSentDoc) {
            activeDocId = null;
            const nextId = _visibleDocIdsForCurrentSession().find(id => docs.has(id));
            if (nextId) switchToDoc(nextId);
            else closePanel();
          } else {
            renderTabs();
          }
          _syncDocIndicator();
        }
      } else {
        _restoreDetachedEmailDoc(detachedEmailDoc);
        detachedEmailDoc = null;
        if (uiModule) uiModule.showError(data.error || 'Failed to send');
      }
    } catch (e) {
      _restoreDetachedEmailDoc(detachedEmailDoc);
      detachedEmailDoc = null;
      if (uiModule) uiModule.showError(e?.message ? `Failed to send email: ${e.message}` : 'Failed to send email');
    } finally {
      if (sendSpinner) sendSpinner.destroy();
      if (btn) {
        btn.disabled = false;
        if (origBtnHtml) btn.innerHTML = origBtnHtml;
      }
    }
  }

  async function _saveDraft() {
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const textarea = document.getElementById('doc-editor-textarea');
    const body = (_rich ? (_rich.innerText || _rich.textContent || '') : (textarea?.value || '')).trim();
    const bodyHtml = _rich ? _rich.innerHTML : null;
    const btn = document.getElementById('doc-email-draft-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const res = await fetch(`${API_BASE}/api/email/draft`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          to: to || '',
          cc: cc || null,
          bcc: bcc || null,
          subject: subject || '',
          body: body || '',
          body_html: bodyHtml,
          in_reply_to: inReplyTo || null,
          references: references || null,
          account_id: window.__odysseusActiveEmailAccount || null,
        }),
      });
      const data = await res.json();
      if (data.success) {
        if (uiModule) uiModule.showToast('Draft saved to mailbox');
      } else {
        if (uiModule) uiModule.showError(data.error || 'Failed to save draft');
      }
    } catch (e) {
      const timedOut = e && e.name === 'AbortError';
      if (uiModule) uiModule.showError(timedOut ? 'Saving draft timed out' : 'Failed to save draft');
    } finally {
      clearTimeout(timeout);
      if (btn) { btn.disabled = false; btn.textContent = 'Draft'; }
    }
  }

  function _discardEmail() {
    if (!activeDocId) return;
    // Just close — the Draft button handles saving explicitly
    _closeWithoutDeleting(true);
  }

  function _visibleDocIdsForCurrentSession() {
    const curSession = sessionModule?.getCurrentSessionId() || '';
    const ids = [];
    for (const [id, doc] of docs) {
      if (doc.sessionId && curSession && doc.sessionId !== curSession) continue;
      ids.push(id);
    }
    return ids;
  }

  function _detachActiveEmailForBackground(docId) {
    if (!docId || !docs.has(docId)) return null;
    saveCurrentToMap();
    const doc = docs.get(docId);
    const snapshot = { id: docId, doc: { ...doc } };
    const wasActive = activeDocId === docId;
    if (wasActive) saveDocument({ silent: true }).catch(() => {});

    const visibleBefore = _visibleDocIdsForCurrentSession();
    const idx = visibleBefore.indexOf(docId);
    docs.delete(docId);
    if (wasActive) activeDocId = null;

    if (wasActive) {
      const remaining = visibleBefore.filter(id => id !== docId && docs.has(id));
      const nextId = remaining[idx] || remaining[idx - 1] || remaining[0] || null;
      if (nextId) {
        switchToDoc(nextId);
      } else {
        closePanel();
      }
    }
    renderTabs();
    _syncDocIndicator();
    return snapshot;
  }

  function _restoreDetachedEmailDoc(snapshot) {
    if (!snapshot || !snapshot.id || !snapshot.doc) return;
    if (!docs.has(snapshot.id)) docs.set(snapshot.id, snapshot.doc);
    _ensureDocPaneMounted();
    switchToDoc(snapshot.id);
    _syncDocIndicator();
  }

  function _closeWithoutDeleting(deleteDoc = false) {
    if (!activeDocId) return;
    if (deleteDoc) {
      fetch(`${API_BASE}/api/document/${activeDocId}`, { method: 'DELETE' }).catch(() => {});
    }
    // Save the current state to the doc first so it persists in the library
    saveCurrentToMap();
    if (!deleteDoc) {
      saveDocument({ silent: true }).catch(() => {});
    }
    docs.delete(activeDocId);
    const remaining = Array.from(docs.keys());
    if (remaining.length > 0) {
      switchToDoc(remaining[0]);
    } else {
      closePanel();
    }
    renderTabs();
  }

  // Fast/Full + optional context popover for the doc-editor email Reply button.
  // Mirrors the email reader's AI reply choice popover so the UX is identical:
  // textarea for an optional steering note, then Fast (lightning) or Full
  // (concentric dot) buttons; both feed into _aiReply with the chosen mode.
  let _docAiReplyChoiceMenu = null;
  function _closeDocAiReplyChoice() {
    if (_docAiReplyChoiceMenu) {
      try { _docAiReplyChoiceMenu.remove(); } catch (_) {}
      _docAiReplyChoiceMenu = null;
    }
  }
  function _showDocAiReplyChoice(btn) {
    _closeDocAiReplyChoice();
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.className = 'doc-ai-reply-choice';
    const menuMaxW = Math.min(240, window.innerWidth - 16);
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - menuMaxW - 8));
    const estHeight = 150;
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const top = (spaceBelow >= estHeight || spaceBelow >= spaceAbove)
      ? Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - estHeight - 8))
      : Math.max(8, rect.top - estHeight - 6);
    menu.style.cssText = [
      'position:fixed',
      `left:${left}px`,
      `top:${top}px`,
      `max-width:${menuMaxW}px`,
      'box-sizing:border-box',
      'z-index:10060',
      'display:flex',
      'gap:6px',
      'padding:6px',
      'background:var(--bg,#111)',
      'border:1px solid var(--border,#333)',
      'border-radius:7px',
      'box-shadow:0 8px 24px rgba(0,0,0,.28)',
    ].join(';');
    menu.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;min-width:200px;">
        <textarea data-note-input rows="2" placeholder="Add context (optional)" style="width:100%;box-sizing:border-box;resize:vertical;min-height:42px;font-family:inherit;font-size:11px;padding:5px 6px;border-radius:5px;border:1px solid var(--border,#333);background:var(--bg-elev,#1a1a1a);color:var(--fg);"></textarea>
        <div style="display:flex;align-items:center;gap:4px;">
          <button class="memory-toolbar-btn" data-mode="ai-reply-fast" title="Shorter, faster draft" style="display:inline-flex;align-items:center;justify-content:center;gap:5px;flex:1;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="var(--accent, var(--red))" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
            Fast
          </button>
          <button class="memory-toolbar-btn" data-mode="ai-reply-full" title="Fuller reply with more context" style="display:inline-flex;align-items:center;justify-content:center;gap:5px;flex:1;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" style="color:var(--accent, var(--red));"><circle cx="12" cy="12" r="6"/></svg>
            Full
          </button>
        </div>
      </div>
    `;
    const noteInput = menu.querySelector('[data-note-input]');
    setTimeout(() => noteInput?.focus(), 0);
    menu.addEventListener('mousedown', (ev) => ev.stopPropagation());
    menu.addEventListener('click', async (ev) => {
      const choice = ev.target.closest('[data-mode]');
      if (!choice) return;
      ev.preventDefault();
      ev.stopPropagation();
      const mode = choice.getAttribute('data-mode') || 'ai-reply-fast';
      const noteHint = (noteInput?.value || '').trim();
      _closeDocAiReplyChoice();
      await _aiReply({ mode, noteHint });
    });
    document.body.appendChild(menu);
    _docAiReplyChoiceMenu = menu;
    const outsideClose = (ev) => {
      if (menu.contains(ev.target)) return;
      document.removeEventListener('click', outsideClose, true);
      _closeDocAiReplyChoice();
    };
    setTimeout(() => document.addEventListener('click', outsideClose, true), 0);
    // Esc to close.
    const escClose = (ev) => {
      if (ev.key === 'Escape') {
        ev.stopPropagation();
        document.removeEventListener('keydown', escClose, true);
        _closeDocAiReplyChoice();
      }
    };
    document.addEventListener('keydown', escClose, true);
  }

  async function _aiReply(opts = {}) {
    const { mode = 'auto', noteHint = '' } = (opts || {});
    const to = document.getElementById('doc-email-to')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim() || '';
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const currentBody = textarea.value || '';
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim() || '';
    const sourceUid = document.getElementById('doc-email-source-uid')?.value?.trim() || '';
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value?.trim() || 'INBOX';
    const cleanAiReplyText = (text) => {
      if (!text) return '';
      let t = String(text);
      const open = /<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/i;
      const close = /<<<\s*END\s*>>+/i;
      const m = open.exec(t);
      if (m) {
        const rest = t.slice(m.index + m[0].length);
        const c = close.exec(rest);
        t = c ? rest.slice(0, c.index) : rest;
      }
      return t
        .replace(/<<<\s*(?:REPLY|SUMMARY|OUTPUT)\s*>>+/gi, '')
        .replace(/<<<\s*END\s*>>+/gi, '')
        .trim();
    };
    const shouldUseFastAiReply = () => {
      const text = `${subject}\n${currentBody}`.toLowerCase();
      if (/\b(attach(?:ed|ment)?|pdf|document|contract|invoice|receipt|quote|estimate|proposal|question|questions|details|schedule|booking|reservation|meeting|calendar|availability|confirm|confirmation|review|sign|signature)\b/.test(text)) {
        return false;
      }
      return currentBody.length < 2500;
    };

    // Use the current chat model
    let currentModel = '';
    let currentSessionId = '';
    try {
      currentModel = sessionModule?.getCurrentModel() || '';
      currentSessionId = sessionModule?.getCurrentSessionId() || '';
    } catch (_) {}

    const btn = document.getElementById('doc-email-ai-reply-btn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:3px"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>Drafting...'; }

    try {
      // Empty-compose path: if there's no original body, send a placeholder
      // so the backend's "no body" guard doesn't fail. The user_hint carries
      // the user's compose intent; the model uses To/Subject + that hint.
      const bodyForApi = currentBody || (noteHint ? '(no prior email — compose a new message based on the To, Subject, and user instructions)' : currentBody);
      const fastFlag = mode === 'ai-reply-fast' ? true
                     : mode === 'ai-reply-full' ? false
                     : shouldUseFastAiReply();
      const res = await fetch(`${API_BASE}/api/email/ai-reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          to: to,
          subject: subject,
          original_body: bodyForApi,
          model: currentModel,
          session_id: currentSessionId,
          message_id: inReplyTo,
          uid: sourceUid,
          folder: sourceFolder,
          fast: fastFlag,
          user_hint: noteHint || '',
        }),
      });
      const data = await res.json();
      if (data.success && data.reply) {
        let cleanReply = cleanAiReplyText(data.reply);
        // Strip any "On <date>, <name> wrote:" attribution + everything
        // after it from the AI's output — the model sometimes re-quotes
        // the original thread, and we already have the real quote in
        // currentBody. Without this, AI's invented quote stacked on top
        // of the real one and looked like the history had been "edited".
        cleanReply = cleanReply.replace(/\n*On\b[\s\S]*?\bwrote:[\s\S]*$/m, '').trim();
        // Never overwrite the existing draft (user's typed text + the
        // quoted history below it). Always prepend the AI suggestion so
        // the user can read it, copy parts, or delete it — but their
        // own work and the original quote are untouched.
        const newBody = currentBody ? cleanReply + '\n\n' + currentBody : cleanReply;
        await _streamEmailBodyText(textarea, newBody);
        if (uiModule) uiModule.showToast(`AI draft inserted (${data.model_used || 'AI'})`);
      } else {
        if (uiModule) uiModule.showError(data.error || 'Failed to generate reply');
      }
    } catch (e) {
      if (uiModule) uiModule.showError('Failed to generate AI reply');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;margin-left:4px;">Reply</span>'; }
    }
  }

  async function _scheduleSend(anchorEl = null) {
    const to = document.getElementById('doc-email-to')?.value?.trim();
    const cc = document.getElementById('doc-email-cc')?.value?.trim() || '';
    const bcc = document.getElementById('doc-email-bcc')?.value?.trim() || '';
    const subject = document.getElementById('doc-email-subject')?.value?.trim();
    const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value?.trim();
    const references = document.getElementById('doc-email-references')?.value?.trim();
    const _rich = _emailRichbodyActive();
    if (_rich) _syncEmailRichbody(_rich);
    const body = (_rich
      ? (_rich.innerText || _rich.textContent || '')
      : (document.getElementById('doc-editor-textarea')?.value || '')
    ).trim();
    const doc = docs.get(activeDocId);
    const attachments = (doc?._composeAtts || []).map(a => a.token);

    if (!to || !body) {
      if (uiModule) uiModule.showError('To and body are required');
      return;
    }
    if (inReplyTo && !_emailReplyOwnText(body)) {
      if (uiModule) uiModule.showError('Reply body is empty');
      return;
    }
    if (attachments.length === 0 && _bodyMentionsAttachment(body)) {
      const proceed = await _confirmMissingAttachment();
      if (!proceed) return;
    }

    // Create a small modal with datetime input and quick presets
    const overlay = document.createElement('div');
    overlay.className = 'modal';
    overlay.style.display = 'flex';
    overlay.innerHTML = `
      <div class="modal-content schedule-send-modal" style="width:400px;max-width:92vw;">
        <div class="modal-header">
          <h4>Schedule Send</h4>
          <button class="close-btn" id="sched-close" title="Close"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div class="modal-body schedule-send-body">
          <label class="schedule-send-label">Quick presets</label>
          <div class="schedule-send-presets">
            <button class="memory-toolbar-btn" data-preset="1h">In 1 hour</button>
            <button class="memory-toolbar-btn" data-preset="3h">In 3 hours</button>
            <button class="memory-toolbar-btn" data-preset="tomorrow">Tomorrow 9am</button>
            <button class="memory-toolbar-btn" data-preset="monday">Monday 9am</button>
          </div>
          <label class="schedule-send-label" for="sched-datetime">Or pick a specific time</label>
          <input type="datetime-local" id="sched-datetime" class="schedule-send-datetime" />
        </div>
        <div class="modal-footer schedule-send-footer">
          <button class="memory-toolbar-btn" id="sched-cancel">Cancel</button>
          <button class="memory-toolbar-btn schedule-send-confirm" id="sched-confirm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>Schedule</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const modalContent = overlay.querySelector('.schedule-send-modal');
    const anchor = anchorEl || document.getElementById('doc-email-send-caret') || document.getElementById('doc-email-send-btn');
    if (modalContent && anchor) {
      const rect = anchor.getBoundingClientRect();
      const gap = 8;
      const width = Math.min(400, Math.max(280, window.innerWidth - 16));
      modalContent.style.width = `${width}px`;
      modalContent.style.position = 'fixed';
      modalContent.style.margin = '0';
      modalContent.style.transform = 'none';
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
      const belowTop = rect.bottom + gap;
      const estimatedHeight = Math.min(320, window.innerHeight - 16);
      const top = belowTop + estimatedHeight <= window.innerHeight - 8
        ? belowTop
        : Math.max(8, rect.top - estimatedHeight - gap);
      modalContent.style.left = `${left}px`;
      modalContent.style.top = `${top}px`;
    }

    const dtInput = overlay.querySelector('#sched-datetime');
    // Default to 1 hour from now
    const now = new Date(Date.now() + 60 * 60 * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    dtInput.value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;

    const escHandler = (e) => { if (e.key === 'Escape') cleanup(); };
    const cleanup = () => {
      overlay.remove();
      document.removeEventListener('keydown', escHandler);
    };
    overlay.querySelector('#sched-close').addEventListener('click', cleanup);
    overlay.querySelector('#sched-cancel').addEventListener('click', cleanup);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(); });
    document.addEventListener('keydown', escHandler);

    overlay.querySelectorAll('[data-preset]').forEach(btn => {
      btn.addEventListener('click', () => {
        const preset = btn.getAttribute('data-preset');
        const d = new Date();
        if (preset === '1h') d.setHours(d.getHours() + 1);
        else if (preset === '3h') d.setHours(d.getHours() + 3);
        else if (preset === 'tomorrow') { d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); }
        else if (preset === 'monday') {
          const daysUntilMon = (8 - d.getDay()) % 7 || 7;
          d.setDate(d.getDate() + daysUntilMon);
          d.setHours(9, 0, 0, 0);
        }
        dtInput.value = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
      });
    });

    overlay.querySelector('#sched-confirm').addEventListener('click', async () => {
      const localDt = dtInput.value;
      if (!localDt) { if (uiModule) uiModule.showError('Please pick a time'); return; }
      // Convert local datetime to UTC ISO
      const utcIso = new Date(localDt).toISOString();
      try {
        const activeAccountId = await _resolveComposeSendAccountId();
        const res = await fetch(`${API_BASE}/api/email/schedule`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            to, cc: cc || null, bcc: bcc || null, subject, body,
            in_reply_to: inReplyTo || null,
            references: references || null,
            attachments: attachments.length > 0 ? attachments : null,
            send_at: utcIso,
            account_id: activeAccountId,
          }),
        });
        const data = await res.json();
        if (data.success) {
          if (uiModule) uiModule.showToast(`Scheduled for ${new Date(localDt).toLocaleString()}`);
          cleanup();
          // Close the document
          _closeWithoutDeleting(true);
        } else {
          if (uiModule) uiModule.showError(data.error || 'Failed to schedule');
        }
      } catch (e) {
        if (uiModule) uiModule.showError('Failed to schedule');
      }
    });
  }

  async function _markUnreadAndClose() {
    const sourceUid = document.getElementById('doc-email-source-uid')?.value || '';
    const sourceFolder = document.getElementById('doc-email-source-folder')?.value || 'INBOX';
    if (sourceUid) {
      try {
        await fetch(`${API_BASE}/api/email/mark-unread/${sourceUid}?folder=${encodeURIComponent(sourceFolder)}`, { method: 'POST' });
      } catch (e) { console.error('Failed to mark unread:', e); }
    }
    _discardEmail();
  }

  function switchToDoc(docId) {
    if (!docs.has(docId)) return;
    _hideLoadingOverlay();
    if (_diffModeActive) exitDiffMode(true);

    // Save current doc state before switching
    saveCurrentToMap();

    // Auto-delete the doc we're leaving if it's completely empty
    const prevId = activeDocId;
    if (prevId && prevId !== docId && docs.has(prevId)) {
      const prev = docs.get(prevId);
      if (!(prev.content || '').trim() && !(prev.title || '').trim()) {
        fetch(`${API_BASE}/api/document/${prevId}`, { method: 'DELETE' }).catch(() => {});
        docs.delete(prevId);
        _syncDocIndicator();
      }
    }

    activeDocId = docId;
    clearSelection();
    const doc = docs.get(docId);

    // Populate editor
    const titleInput = document.getElementById('doc-title-input');
    const textarea = document.getElementById('doc-editor-textarea');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');

    if (titleInput) titleInput.value = doc.title || '';
    // For email docs, _showEmailFields will set textarea to body only (not raw header)
    if (textarea && doc.language !== 'email') textarea.value = doc.content || '';
    if (langSelect) langSelect.value = doc.language || 'markdown';
    if (badge) { const _v = doc.version || 1; badge.textContent = `v${_v}`; badge.style.display = _v > 1 ? '' : 'none'; }
    { const _v = doc.version || 1; const _dbtn = document.getElementById('doc-diff-toggle-btn'); if (_dbtn) _dbtn.style.display = _v > 1 ? '' : 'none'; }
    syncHighlighting();
    // Deferred re-sync: ensure minHeight is correct after browser layout
    requestAnimationFrame(() => {
      const ta2 = document.getElementById('doc-editor-textarea');
      const code2 = document.getElementById('doc-editor-code');
      const pre2 = document.getElementById('doc-editor-highlight');
      if (ta2 && code2 && pre2) {
        code2.style.minHeight = ta2.scrollHeight + 'px';
        pre2.scrollTop = ta2.scrollTop;
      }
    });

    // Auto-detect language for docs with no language set
    if (!doc.userSetLanguage && !doc.language) {
      setTimeout(attemptAutoDetect, 100);
    }

    // Show/hide markdown toolbar based on language. PDF-backed docs are
    // markdown under the hood, so the toolbar shows up for them too — and
    // gets the PDF-specific buttons (Text/Check/Sign/AI) revealed below.
    const isMd = (doc.language || 'markdown') === 'markdown';
    const isPdf = _isFormBackedDoc(doc.content || '');

    // For PDF-backed docs, re-run text extraction on the backend so the AI
    // can see the contents on the very next message. Idempotent + skipped
    // once per session per doc to avoid hammering the VL model on every
    // switch — track via a sentinel on the doc object.
    if (isPdf && !doc._ocrTriggered) {
      doc._ocrTriggered = true;
      (async () => {
        try {
          const r = await fetch(`${API_BASE}/api/document/${docId}/extract-pdf-text`, { method: 'POST', credentials: 'same-origin' });
          if (!r.ok) return;
          const j = await r.json().catch(() => ({}));
          if (j && j.extracted) {
            // Pull the fresh content into the local cache so subsequent AI
            // turns and the source view both reflect the extraction.
            const dr = await fetch(`${API_BASE}/api/document/${docId}`, { credentials: 'same-origin' });
            if (dr.ok) {
              const full = await dr.json();
              const cached = docs.get(docId);
              if (cached && full && full.current_content) {
                cached.content = full.current_content;
              }
            }
          }
        } catch (_) {}
      })();
    }
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar) {
      // Show for every doc type so users always have access to font-size /
      // diff toggle / language-specific controls. Items inside the toolbar
      // gate their own visibility on language (md edit/preview toggle, etc).
      mdToolbar.style.display = '';
      if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
    }
    // Toggle PDF-only toolbar group
    document.querySelectorAll('.md-toolbar-pdf-only').forEach(el => {
      el.style.display = isPdf ? '' : 'none';
    });
    // Font size does nothing for a PDF (annotations are placed, not styled) —
    // hide it on PDFs so the toolbar only shows what actually works.
    const _fsBtn = document.getElementById('doc-fontsize-btn');
    if (_fsBtn) _fsBtn.style.display = isPdf ? 'none' : '';
    // Exit CSV preview when switching docs, or auto-show for CSV
    const isCsv = doc.language === 'csv';
    const csvPreview = document.getElementById('doc-csv-preview');
    if (!isCsv) {
      if (csvPreview) csvPreview.style.display = 'none';
    } else {
      // Auto-show table view for CSV documents
      requestAnimationFrame(() => toggleCsvPreview());
    }

    // Exit HTML preview on switch
    exitHtmlPreview();

    // Show/hide email fields. Markdown preview uses the same editor wrapper
    // as email source mode, so clear it before showing the rich email body;
    // otherwise the source wrapper can reappear over the composer.
    const isEmail = doc.language === 'email';
    if (isEmail) {
      _setMarkdownPreviewActive(false, { remember: false });
      _showEmailFields(doc);
    } else {
      _hideEmailFields();
      const wantsMarkdownPreview = (doc.language || 'markdown') === 'markdown' && doc._markdownPreviewActive === true;
      _setMarkdownPreviewActive(wantsMarkdownPreview, { remember: false });
    }

    // Hide version panel on switch
    const vp = document.getElementById('doc-version-panel');
    if (vp) vp.classList.add('hidden');

    renderTabs();
    _syncHeaderActions();

    // Restore any persisted suggestions for this doc
    if (_activeSuggestions.length === 0) {
      _restoreSuggestionsFromStorage(docId);
    }

  }

  // Detach a doc from its chat session so it stops reappearing in that
  // chat: docs with content are unlinked (kept in the library), empty docs
  // are deleted. Used by both the tab × and the mobile chip-to-trash close.
  function _detachDocFromSession(docId, { toast = false } = {}) {
    const doc = docs.get(docId);
    const hasContent = doc && doc.content && doc.content.trim().length > 0;
    if (hasContent) {
      fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: '' }),
      }).then(() => {
        if (toast && uiModule) uiModule.showToast('Document unlinked from session');
      }).catch(() => {});
    } else {
      fetch(`${API_BASE}/api/document/${docId}`, { method: 'DELETE' }).catch(() => {});
    }
    docs.delete(docId);
    _syncDocIndicator();
  }

  async function closeTab(docId) {
    // Save current editor content to map so the check below uses fresh data
    saveCurrentToMap();
    _detachDocFromSession(docId, { toast: true });
    // Find next tab in the current session
    const curSession = sessionModule?.getCurrentSessionId() || '';
    let nextId = null;
    for (const [id, d] of docs) {
      if (!d.sessionId || !curSession || d.sessionId === curSession) {
        nextId = id;
        break;
      }
    }
    if (!nextId) {
      activeDocId = null;
      closePanel();
      return;
    }
    if (activeDocId === docId) {
      switchToDoc(nextId);
    } else {
      renderTabs();
    }
  }

  /** Auto-create a document when user types/pastes into empty editor */
  let _autoCreating = false;
  // True while createDocument's POST is in flight — suppresses the type-to-
  // auto-create path so clicking "New document" and immediately typing can't
  // spawn a SECOND untitled doc (the create round-trip hadn't set activeDocId
  // yet, so the input handler thought the editor was empty).
  let _creatingDoc = false;
  async function _autoCreateFromInput(content) {
    if (_autoCreating) return;
    _autoCreating = true;
    try {
      let sessionId = _lastSessionId
        || (sessionModule && sessionModule.getCurrentSessionId());
      if (!sessionId) {
        sessionId = await _autoCreateSession();
      }
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, title: '', content }),
      });
      const doc = await res.json();
      addDocToTabs(doc, sessionId);
      // Set the content into the map so switchToDoc preserves it
      const d = docs.get(doc.id);
      if (d) d.content = content;
      activeDocId = doc.id;
      // Update textarea (keep existing content the user typed)
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.placeholder = 'Document content...';
      }
      syncHighlighting();
      renderTabs();
      // Trigger auto-detect and auto-title
      setTimeout(attemptAutoDetect, 100);
      setTimeout(() => autoTitleFromContent(content), 300);
      // Auto-save
      clearTimeout(_autoSaveDebounce);
      _autoSaveDebounce = setTimeout(() => { saveDocument({ silent: true }); }, 2000);
    } catch (e) {
      console.error('Failed to auto-create document from input:', e);
    } finally {
      _autoCreating = false;
    }
  }

  /** Save current editor state back into the docs map */
  function saveCurrentToMap() {
    if (!activeDocId || !docs.has(activeDocId)) return;
    const doc = docs.get(activeDocId);
    const textarea = document.getElementById('doc-editor-textarea');
    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');
    if (titleInput) doc.title = titleInput.value;
    if (langSelect) doc.language = langSelect.value;
    // For email docs, reconstruct full content with header
    if (doc.language === 'email' && textarea) {
      const to = document.getElementById('doc-email-to')?.value || '';
      const cc = document.getElementById('doc-email-cc')?.value || '';
      const bcc = document.getElementById('doc-email-bcc')?.value || '';
      const subject = document.getElementById('doc-email-subject')?.value || '';
      const inReplyTo = document.getElementById('doc-email-in-reply-to')?.value || '';
      const references = document.getElementById('doc-email-references')?.value || '';
      const sourceUid = document.getElementById('doc-email-source-uid')?.value || '';
      const sourceFolder = document.getElementById('doc-email-source-folder')?.value || '';
      // Persist the WYSIWYG body as HTML so reopening the draft keeps its
      // formatting (the textarea mirror is plain text). _emailBodyToHtml detects
      // the leading '<' on reload and restores it verbatim.
      const _rich = document.getElementById('doc-email-richbody');
      const _emailBody = (_rich && _rich.style.display !== 'none') ? _rich.innerHTML : textarea.value;
      doc.content = _buildEmailContent(to, subject, inReplyTo, references, _emailBody, sourceUid, sourceFolder, cc, bcc);
    } else if (textarea) {
      // Don't clobber a PDF/form-backed doc's source when the textarea is empty
      // (it's hidden behind the rendered PDF view, so its value isn't the source
      // of truth). Overwriting here dropped the pdf_form_source marker, so after
      // minimize→restore the doc came back blank.
      if (!(textarea.value === '' && _isFormBackedDoc(doc.content))) {
        doc.content = textarea.value;
      }
    }
  }

  // ---- Panel open/close ----

  export function openPanel() {
    if (isOpen) return;
    // Clear any pane/divider still sliding out from a just-fired close so we
    // don't end up with two #doc-editor-pane nodes (and a stale close stripping
    // doc-view). Paired with the isOpen guard in _finishClose above.
    document.getElementById('doc-editor-pane')?.remove();
    document.getElementById('doc-divider')?.remove();
    // If the doc was minimized as a chip and the user opened the panel via
    // a different path (toolbar button, indicator), clear that chip — the
    // doc is becoming visible again.
    if (Modals.isRegistered('doc-panel') && Modals.isMinimized('doc-panel')) {
      _minimizedDocId = null;
      Modals.unregister('doc-panel');
    }
    const container = document.getElementById('chat-container');
    if (!container) return;

    isOpen = true;
    // Doc was opened last → it goes in front of the email windows (clears the
    // email-front flag; the doc/email z-index alternation lives in CSS).
    document.body.classList.remove('email-front');
    _ensureAgentMode();
    _markDocVisibleState(_lastSessionId, 'open');

    document.body.classList.add('doc-view');

    // Sync toggle button state
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) toggleBtn.classList.add('active');
    const docInd = document.getElementById('doc-indicator-btn');
    if (docInd) docInd.classList.add('active');

    // Create divider — grip in the middle (drag-to-resize), swapped for a
    // clickable collapse chevron on hover.
    const divider = document.createElement('div');
    divider.className = 'doc-divider';
    divider.id = 'doc-divider';
    // Single chevron that swaps direction based on cursor position:
    //   - cursor INSIDE the doc pane  →  › (collapse / close panel)
    //   - cursor OUTSIDE the doc pane →  ‹ (fullscreen — grow leftward)
    // The arrow rotates via CSS so the swap feels clean. The action follows
    // the glyph, so clicking always does what the arrow promises.
    // The secondary X button below it is only shown in fullscreen mode and
    // hides the pane outright (so fullscreen has an escape that isn't just
    // "exit fullscreen").
    divider.innerHTML = '<button type="button" class="doc-divider-collapse" title="Collapse panel" data-mode="collapse"><span>›</span></button>' +
      '<button type="button" class="doc-divider-hide" title="Hide panel" aria-label="Hide panel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';
    const _divHide = divider.querySelector('.doc-divider-hide');
    if (_divHide) {
      _divHide.addEventListener('mousedown', (e) => e.stopPropagation());
      _divHide.addEventListener('click', (e) => { e.stopPropagation(); closePanel('down'); });
    }

    // Create the editor pane
    const pane = document.createElement('div');
    pane.id = 'doc-editor-pane';
    pane.className = 'doc-editor-pane';
    // ── Mobile: make toolbar/footer buttons work on the FIRST tap with the
    // keyboard up ──
    // Normally a tap while the keyboard is open is eaten by the OS keyboard
    // dismissal and the button's click never fires ("nothing triggers"). Keep
    // the field focused through the press so the tap isn't consumed, then
    // re-dispatch the click on release so the action fires on the first tap.
    // The action handler itself decides whether to then drop the keyboard
    // (Undo/Export/Close do; Format/Copy keep it). Touch only — desktop is
    // untouched.
    {
      let _kbBtn = null;
      pane.addEventListener('pointerdown', (e) => {
        _kbBtn = null;
        if (e.pointerType !== 'touch') return;
        const btn = e.target.closest && e.target.closest('button');
        if (!btn) return;
        const ae = document.activeElement;
        if (!(ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA'))) return;
        e.preventDefault();   // keep focus; this cancels the native touch click
        _kbBtn = btn;
      }, true);
      pane.addEventListener('pointerup', (e) => {
        const btn = _kbBtn; _kbBtn = null;
        if (!btn) return;
        if (e.target.closest && e.target.closest('button') === btn) {
          btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        }
      }, true);
      pane.addEventListener('pointercancel', () => { _kbBtn = null; }, true);
    }
    pane.innerHTML = `
      <input type="hidden" id="doc-title-input" value="" />
      <div class="doc-mobile-grabber" id="doc-mobile-grabber" aria-hidden="true"></div>
      <div class="doc-editor-header" id="doc-editor-actions">
        <button id="doc-undo-btn" class="doc-action-icon-btn" title="Undo (Ctrl+Z)" style="gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg><span style="font-size:11px;">Undo</span></button>
        <button id="doc-header-preview-btn" class="doc-action-icon-btn" title="Run / Preview" style="display:none;opacity:0.85;gap:4px;"></button>
        <span id="doc-stream-indicator" class="doc-stream-indicator" style="display:none"><span class="doc-stream-dot"></span> editing</span>
        <span id="doc-version-badge" class="doc-version-badge" title="Version history" style="display:none">v1</span>
        <span style="flex:1"></span>
        <button id="doc-export-pdf-btn" class="doc-action-icon-btn" title="Export PDF" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 18 15 15"/></svg> <span style="font-size:11px;">Export PDF</span></button>
        <button id="doc-pdf-view-btn" class="doc-action-icon-btn" title="Toggle PDF view" style="display:none;opacity:0.7;gap:4px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> <span style="font-size:11px;">PDF</span></button>
        <select id="doc-language-select" class="doc-language-select">
          <option value="python">python</option>
          <option value="javascript">javascript</option>
          <option value="typescript">typescript</option>
          <option value="html">html</option>
          <option value="css">css</option>
          <option value="markdown">markdown</option>
          <option value="json">json</option>
          <option value="yaml">yaml</option>
          <option value="bash">bash</option>
          <option value="sql">sql</option>
          <option value="rust">rust</option>
          <option value="go">go</option>
          <option value="java">java</option>
          <option value="c">c</option>
          <option value="cpp">c++</option>
          <option value="csharp">c#</option>
          <option value="xml">xml</option>
          <option value="svg">svg</option>
          <option value="toml">toml</option>
          <option value="ini">ini</option>
          <option value="ruby">ruby</option>
          <option value="php">php</option>
          <option value="csv">csv</option>
          <option value="email">email</option>
          <option value="pdf">pdf</option>
        </select>
        <!-- Close + Copy/Export moved to the bottom action footer (#doc-actions-footer)
             so regular docs match the email footer layout. -->
      </div>
      <div class="doc-tab-bar" id="doc-tab-bar"></div>
      <div id="doc-email-header" class="doc-email-header" style="display:none">
        <button type="button" id="doc-email-collapse-btn" class="doc-email-collapse-btn" title="Hide email fields" aria-expanded="true">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg>
          <span id="doc-email-collapse-summary" class="doc-email-collapse-summary">No recipient · No subject</span>
        </button>
        <div id="doc-email-fields" class="doc-email-fields">
          <div class="email-field" style="position:relative">
            <span class="email-field-prefix">To</span>
            <input type="text" id="doc-email-to" placeholder="recipient@example.com" autocomplete="off" />
            <div id="doc-email-to-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" id="doc-email-show-cc" class="email-cc-toggle" title="Show Cc/Bcc">Cc</button>
          </div>
          <div class="email-field" id="doc-email-cc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Cc</span>
            <input type="text" id="doc-email-cc" placeholder="cc@example.com, example2" autocomplete="off" />
            <div id="doc-email-cc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" id="doc-email-bcc-row" style="display:none;position:relative">
            <span class="email-field-prefix">Bcc</span>
            <input type="text" id="doc-email-bcc" placeholder="bcc@example.com" autocomplete="off" />
            <div id="doc-email-bcc-suggestions" class="email-autocomplete" style="display:none"></div>
            <button type="button" class="email-cc-close" data-cc-close title="Hide Cc/Bcc" aria-label="Hide Cc/Bcc"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div class="email-field" style="position:relative"><span class="email-field-prefix">Subject</span><input type="text" id="doc-email-subject" placeholder="" /></div>
          <div id="doc-email-attachments" class="email-attachments" style="display:none"></div>
          <div id="doc-email-compose-atts" class="email-compose-atts" style="display:none"></div>
        </div>
        <input type="hidden" id="doc-email-in-reply-to" />
        <input type="hidden" id="doc-email-references" />
        <input type="hidden" id="doc-email-source-uid" />
        <input type="hidden" id="doc-email-source-folder" />
        <input type="file" id="doc-email-file-input" multiple style="display:none" />
      </div>
      <div class="doc-md-toolbar" id="doc-md-toolbar" style="display:none">
        <div class="md-toolbar-items" id="md-toolbar-items">
          <span class="md-view-toggle" id="doc-md-view-toggle" style="display:none" role="group" aria-label="Edit or preview">
            <button type="button" class="md-view-opt" data-mdview="edit" title="Edit source (Ctrl+Alt+M to toggle)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
            <button type="button" class="md-view-opt" data-mdview="preview" title="Preview (Ctrl+Alt+M to toggle)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>
          </span>
          <span class="md-view-toggle" id="doc-render-view-toggle" style="display:none" role="group" aria-label="Code or run">
            <button type="button" class="md-view-opt" data-renderview="code" title="Edit code"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button>
            <button type="button" class="md-view-opt" data-renderview="run" title="Run / Preview"><svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>
          </span>
          <button id="doc-email-ai-reply-btn" class="doc-action-icon-btn md-toolbar-email-only" type="button" title="Draft a reply with AI (Fast / Full + optional context)" style="display:none;align-items:center;gap:4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="color:var(--accent, var(--red));flex-shrink:0;position:relative;top:-1px;"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg><span style="font-size:11px;">Reply</span></button>
          <button id="doc-fontsize-btn" class="doc-action-icon-btn" title="Font size" style="position:relative;width:28px;height:26px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.7;"><path d="M4 7V4h16v3"/><path d="M12 4v16"/><path d="M8 20h8"/></svg><span class="doc-fontsize-levels"><i data-sz="s">S</i><i data-sz="m">M</i><i data-sz="l">L</i></span></button>
          <button id="doc-diff-toggle-btn" class="doc-action-icon-btn" title="Compare changes" style="opacity:0.7;display:none;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 12H2l5-5 5 5H9"/><path d="M19 12h3l-5 5-5-5h3"/></svg></button>
          <span class="md-toolbar-sep"></span>
          <button type="button" data-md="bold" title="Bold (Ctrl+B)"><b>B</b></button>
          <button type="button" data-md="italic" title="Italic (Ctrl+I)"><i>I</i></button>
          <button type="button" data-md="strike" title="Strikethrough"><s>S</s></button>
          <span class="md-toolbar-sep"></span>
          <button type="button" class="md-dd-toggle" data-dd="heading" title="Heading"><b>H</b><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" class="md-dd-toggle" data-dd="list" title="List"><span style="font-variant-numeric:tabular-nums;">1.</span><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <span class="md-toolbar-sep"></span>
          <button type="button" data-md="link" title="Link"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></button>
          <button type="button" id="md-toolbar-attach-btn" class="md-toolbar-attach-btn" title="Attach files"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>
          <button type="button" class="md-dd-toggle md-toolbar-email-hide" data-dd="code" title="Code">\`<svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>
          <button type="button" data-md="hr" title="Horizontal rule">—</button>
          <span class="md-toolbar-sep"></span>
          <span id="md-toolbar-emoji-slot"></span>
          <span class="md-toolbar-sep md-toolbar-pdf-only" style="display:none"></span>
          <button type="button" id="doc-pdf-add-text-btn" class="md-toolbar-pdf-only" title="Add text box (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/></svg></button>
          <button type="button" id="doc-pdf-add-check-btn" class="md-toolbar-pdf-only" title="Add checkmark (then click on PDF)" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></button>
          <button type="button" id="doc-pdf-add-sign-btn" class="md-toolbar-pdf-only" title="Add signature (then click on PDF)" style="display:none"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3l6 6-9 9-3-3z"/><path d="M9 15l-3 1 1-3"/><path d="M4 18l3-3"/><path d="M3 20l3-3"/><path d="M5 22l3-3"/></svg><span class="doc-pdf-sign-label">sign</span></button>
          <button type="button" id="doc-pdf-refresh-btn" class="md-toolbar-pdf-only" title="Reload PDF view" style="display:none"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>
        </div>
        <div class="md-toolbar-overflow-wrapper" id="md-toolbar-overflow-wrapper" style="display:none">
          <button class="md-toolbar-overflow-toggle" id="md-toolbar-overflow-toggle" title="More formatting"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button>
          <div class="md-toolbar-overflow-menu" id="md-toolbar-overflow-menu"></div>
        </div>
        <button type="button" class="md-scroll-arrow md-scroll-left" id="md-scroll-left" title="Scroll left" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>
        <button type="button" class="md-scroll-arrow md-scroll-right" id="md-scroll-right" title="Scroll right" style="display:none"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>
      </div>
      <div id="doc-find-bar" class="doc-find-bar" style="display:none">
        <input id="doc-find-input" class="doc-find-input" type="text" placeholder="Find..." />
        <span id="doc-find-count" class="doc-find-count"></span>
        <button id="doc-find-prev" class="doc-find-nav" title="Previous">&uarr;</button>
        <button id="doc-find-next" class="doc-find-nav" title="Next">&darr;</button>
        <button id="doc-find-close" class="doc-find-close" title="Close">&times;</button>
      </div>
      <div id="doc-editor-wrap" class="doc-editor-wrap">
        <div id="doc-line-numbers" class="doc-line-numbers">1</div>
        <pre id="doc-editor-highlight" class="doc-editor-highlight"><code id="doc-editor-code"></code></pre>
        <textarea id="doc-editor-textarea" class="doc-editor-textarea" placeholder="Document content..." spellcheck="false"></textarea>
      </div>
      <!-- WYSIWYG email body. In email mode this replaces the source editor:
           B/I/S act on the live text (execCommand), and on send its HTML becomes
           the email's HTML part. Its plain text is mirrored into the textarea so
           the existing send/draft/change-detection paths keep working. -->
      <div id="doc-email-richbody" class="doc-email-richbody" contenteditable="true" spellcheck="true" style="display:none" data-no-swipe-dismiss></div>
      <div id="doc-email-actions" class="doc-email-actions" style="display:none">
        <button id="doc-email-discard-btn" class="email-discard-btn" title="Close email" style="display:inline-flex;align-items:center;gap:5px;"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span>Close</span></button>
        <span style="flex:1"></span>
        <div class="email-send-split">
          <button id="doc-email-send-btn" class="email-send-btn email-send-main" title="Send email (Ctrl+Enter)"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>Send</button>
          <button id="doc-email-send-caret" class="email-send-btn email-send-caret" title="More send options" aria-haspopup="true" aria-expanded="false"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></button>
          <div id="doc-email-more-menu" class="email-more-menu" style="display:none">
            <div class="dropdown-item-compact" id="doc-email-draft-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></span>Save Draft</div>
            <div class="dropdown-item-compact" id="doc-email-schedule-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></span>Schedule Send...</div>
            <div class="dropdown-item-compact" id="doc-email-unread-btn"><span class="dropdown-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg></span>Mark Unread</div>
          </div>
        </div>
      </div>
      <div id="doc-md-preview" class="doc-md-preview" style="display:none"></div>
      <div id="doc-csv-preview" class="doc-csv-preview" style="display:none"></div>
      <iframe id="doc-html-preview" class="doc-html-preview" sandbox="allow-scripts allow-modals" style="display:none"></iframe>
      <div id="doc-pdf-view" style="display:none;width:100%;flex:1;min-height:0;overflow:auto;background:#525659;padding:20px 0;position:relative;">
        <div id="doc-pdf-save-pill" style="display:none;position:absolute;top:8px;right:14px;padding:4px 10px;border-radius:12px;font-size:11px;z-index:5;pointer-events:none;background:transparent;color:transparent;"></div>
      </div>
      <!-- Action footer sits AFTER all the content/preview panes so it stays
           pinned to the bottom no matter which pane (editor / md-preview /
           csv / html / pdf) is the one growing to fill. -->
      <div id="doc-actions-footer" class="doc-email-actions">
        <span class="email-send-split" id="doc-copy-export-split">
          <button type="button" id="doc-footer-copy-btn" class="email-send-btn email-send-main" title="Copy document"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>Copy</button>
          <button type="button" id="doc-footer-export-btn" class="email-send-btn email-send-caret" title="Export as…" aria-label="Export options"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 15 12 9 18 15"/></svg></button>
        </span>
      </div>
      <div id="doc-version-panel" class="doc-version-panel hidden">
        <div class="doc-version-header">
          <span>Version History</span>
          <button id="doc-version-close" class="doc-action-icon-btn" title="Close"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div id="doc-version-list" class="doc-version-list"></div>
      </div>
      <div id="doc-mobile-footer" class="doc-mobile-footer">
        <button id="doc-mobile-close" class="doc-mobile-footer-btn" type="button">Unlink</button>
        <span style="flex:1"></span>
        <button id="doc-mobile-copy" class="doc-mobile-footer-btn" type="button">Copy</button>
      </div>
    `;

    // Consolidate into a SINGLE action bar: move Undo + the type picker out of
    // the top header into the bottom footer (left side, next to Close) so a
    // regular doc shows one bar, not two. The rest of the header (run/preview,
    // fullscreen, version, PDF) stays put; the header hides itself when nothing
    // in it is visible — see _syncHeaderBarVisibility().
    // Note: `#doc-render-view-toggle` (code↔run for SVG/HTML) intentionally
    // stays in the top header so it matches `#doc-md-view-toggle` (markdown
    // edit↔preview) — both view toggles live in the same place.
    {
      const _footer = pane.querySelector('#doc-actions-footer');
      const _split = _footer && _footer.querySelector('#doc-copy-export-split');
      const _undo = pane.querySelector('#doc-undo-btn');
      const _lang = pane.querySelector('#doc-language-select');
      const _preview = pane.querySelector('#doc-header-preview-btn');  // single Run ▶ for python/bash/js/csv
      const _exportPdf = pane.querySelector('#doc-export-pdf-btn');
      const _pdfView = pane.querySelector('#doc-pdf-view-btn');
      if (_footer && _split) {
        // Footer order (left → right): Undo, Run/Preview, Lang, …, Copy/Export.
        // The X close was here too but is now redundant with the per-tab close
        // button in the title strip — removed.
        if (_undo) _footer.insertBefore(_undo, _footer.firstChild);
        const _anchor = _undo;
        if (_preview && _anchor) _anchor.after(_preview);
        if (_lang) _split.before(_lang);
        // Pull every remaining header-only control into the footer so we
        // only ever render ONE bottom action row. The standalone top header
        // was leaving a duplicate row above (with fullscreen + version badge
        // + stream indicator). Each item keeps its own display: toggling.
        const _streamInd = pane.querySelector('#doc-stream-indicator');
        const _versionBadge = pane.querySelector('#doc-version-badge');
        if (_split) {
          if (_pdfView)      _split.before(_pdfView);
          if (_exportPdf)    _split.before(_exportPdf);
          if (_versionBadge) _split.before(_versionBadge);
          if (_streamInd)    _split.before(_streamInd);
        }
      }
      // iOS keeps the soft keyboard up when you tap a <button> (it doesn't blur
      // the focused textarea), so it lingers after you've typed. Dismiss it on
      // any footer control tap.
      if (_footer) _footer.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('button, select')) return;
        const _ta = document.getElementById('doc-editor-textarea');
        if (_ta && document.activeElement === _ta) _ta.blur();
      });
    }

    // Insert after chat-container (appears on right by default)
    // If sidebar is on the right, insert before chat-container instead
    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');
    if (isRight) {
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

    // Slide-in animation from the correct side
    const fromLeft = pane.classList.contains('doc-left');
    pane.style.transform = fromLeft ? 'translateX(-40px)' : 'translateX(40px)';
    pane.style.opacity = '0';
    requestAnimationFrame(() => {
      pane.style.transition = 'transform 0.15s cubic-bezier(0.22,1,0.36,1), opacity 0.12s ease-out';
      pane.style.transform = 'translateX(0)';
      pane.style.opacity = '1';
      pane.addEventListener('transitionend', () => {
        pane.style.transition = '';
        pane.style.transform = '';
        pane.style.opacity = '';
      }, { once: true });
    });

    // Wire up divider drag to resize
    initDividerDrag(divider, pane, isRight);
    // Divider chevron — single button with three modes (the glyph is the
     // same `›` in markup; CSS rotates 180° for the left-pointing variant).
     //   • cursor INSIDE the doc pane  →  collapse  (›, slide back, closes panel)
     //   • cursor OUTSIDE the doc pane →  fullscreen (‹, slide outward, expands)
     //   • already fullscreen          →  unfullscreen (›, points back in)
     // The user can also drag the chevron vertically along the divider to
     // reposition it.
    const _divCollapse = divider.querySelector('.doc-divider-collapse');
    if (_divCollapse) {
      _divCollapse.addEventListener('mousedown', (e) => e.stopPropagation());
      let _dragging = false;
      _divCollapse.addEventListener('click', (e) => {
        e.stopPropagation();
        if (_dragging) { _dragging = false; return; }  // suppress click after drag
        const mode = _divCollapse.dataset.mode;
        if (mode === 'fullscreen' || mode === 'unfullscreen') toggleFullscreen();
        else closePanel('down');
      });
      const HYSTERESIS = 24;
      const _applyMode = (ev) => {
        // Fullscreen state takes precedence — once the pane is fullscreen the
        // chevron always offers the "exit fullscreen" affordance regardless
        // of cursor position.
        const isFull = pane.classList.contains('doc-fullscreen');
        if (isFull) {
          if (_divCollapse.dataset.mode !== 'unfullscreen') {
            _divCollapse.dataset.mode = 'unfullscreen';
            _divCollapse.title = 'Exit fullscreen';
          }
          return;
        }
        if (!ev) return;
        const rect = divider.getBoundingClientRect();
        const midX = (rect.left + rect.right) / 2;
        const cur = _divCollapse.dataset.mode;
        if (ev.clientX > midX + HYSTERESIS && cur !== 'collapse') {
          _divCollapse.dataset.mode = 'collapse';
          _divCollapse.title = 'Collapse panel';
        } else if (ev.clientX < midX - HYSTERESIS && cur !== 'fullscreen') {
          _divCollapse.dataset.mode = 'fullscreen';
          _divCollapse.title = 'Fullscreen';
        }
      };
      const _onMove = (ev) => _applyMode(ev);
      document.addEventListener('pointermove', _onMove, { passive: true });
      // Reflect the fullscreen state immediately on toggle (no cursor move).
      const _classObs = new MutationObserver(() => _applyMode());
      _classObs.observe(pane, { attributes: true, attributeFilter: ['class'] });

      // Drag-to-reposition: hold + drag vertically moves the chevron along
      // the divider. Stored as a percent so resizing the pane keeps it
      // proportional. Only kicks in after a small movement so a normal tap
      // still registers as a click.
      const DRAG_THRESHOLD = 4;
      let _startY = 0, _moved = false, _pid = null;
      _divCollapse.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 && ev.pointerType === 'mouse') return;
        _startY = ev.clientY;
        _moved = false;
        _pid = ev.pointerId;
        _divCollapse.setPointerCapture?.(_pid);
        ev.preventDefault();
      });
      _divCollapse.addEventListener('pointermove', (ev) => {
        if (_pid === null) return;
        const dy = ev.clientY - _startY;
        if (!_moved && Math.abs(dy) < DRAG_THRESHOLD) return;
        _moved = true;
        _dragging = true;
        const rect = divider.getBoundingClientRect();
        if (!rect.height) return;
        const pct = Math.max(6, Math.min(94, ((ev.clientY - rect.top) / rect.height) * 100));
        _divCollapse.style.top = pct + '%';
      });
      const _endDrag = () => {
        if (_pid !== null) {
          try { _divCollapse.releasePointerCapture?.(_pid); } catch {}
          _pid = null;
        }
      };
      _divCollapse.addEventListener('pointerup', _endDrag);
      _divCollapse.addEventListener('pointercancel', _endDrag);

      const _obs = new MutationObserver(() => {
        if (!document.body.contains(divider)) {
          document.removeEventListener('pointermove', _onMove);
          _classObs.disconnect();
          _obs.disconnect();
        }
      });
      _obs.observe(document.body, { childList: true, subtree: true });
    }

    // Mobile grab handle — swipe down to dismiss (like the other sheet windows).
    _wireSwipeDismiss(document.getElementById('doc-mobile-grabber'));
    document.getElementById('doc-mobile-grabber')?.addEventListener('click', () => closePanel('down'));

    // Wire up events
    document.getElementById('doc-close-btn')?.addEventListener('click', () => closePanel('down'));
    document.getElementById('doc-footer-close-btn')?.addEventListener('click', () => { if (activeDocId) closeTab(activeDocId); });
    document.getElementById('doc-import-btn')?.addEventListener('click', () => openLibrary());
    document.getElementById('doc-footer-copy-btn')?.addEventListener('click', (e) => {
      if (e.currentTarget.dataset.mode === 'reply') { if (activeDocId) _sendSignedReply(activeDocId); }
      else copyDocument();
    });
    document.getElementById('doc-footer-export-btn')?.addEventListener('click', (e) => showExportMenu(null, e.currentTarget.getBoundingClientRect()));
    // Mobile footer: Close the current doc + Copy its content (replaces the
    // per-tab × on small screens, mirroring the email reader's Close footer).
    document.getElementById('doc-mobile-close')?.addEventListener('click', () => { if (activeDocId) closeTab(activeDocId); });
    document.getElementById('doc-mobile-copy')?.addEventListener('click', () => copyDocument());
    // Save, copy, run, export, delete, preview toggles are now in per-tab context menu
    document.getElementById('doc-version-badge').addEventListener('click', toggleVersionHistory);
    document.getElementById('doc-version-close').addEventListener('click', _closeVersionPanel);
    // Reflect the current language as a small icon left of the type select.
    const _syncLangIcon = () => {
      const iconEl = document.getElementById('doc-language-icon');
      const v = document.getElementById('doc-language-select')?.value || '';
      if (iconEl) iconEl.innerHTML = v ? langIcon(v, 14, { style: 'opacity:0.75;' }) : '';
    };
    // Intercept programmatic `langSelect.value = …` so the icon updates without
    // having to instrument every set-site in this file.
    (function _interceptLangSelectValue() {
      const ls = document.getElementById('doc-language-select');
      if (!ls) return;
      const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
      if (!desc || !desc.set) return;
      Object.defineProperty(ls, 'value', {
        configurable: true,
        get() { return desc.get.call(this); },
        set(v) { desc.set.call(this, v); _syncLangIcon(); _syncLangPicker(); },
      });
      _syncLangIcon();  // initial paint
    })();

    // ── Custom language picker ────────────────────────────────────────────
    // Native <option> can't render SVG. So we build a custom dropdown that
    // shows each language's icon + label, while keeping the underlying
    // <select> in place as the source of truth (all existing code that
    // reads/writes langSelect.value keeps working). The native select is
    // visually-hidden but still focusable for accessibility / keyboard.
    let _syncLangPicker = () => {};
    (function _initLangPicker() {
      const ls = document.getElementById('doc-language-select');
      if (!ls || ls.dataset.pickerWired === '1') return;
      ls.dataset.pickerWired = '1';

      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.id = 'doc-langpicker-trigger';
      trigger.className = 'doc-langpicker-trigger';
      trigger.setAttribute('aria-haspopup', 'listbox');
      trigger.setAttribute('aria-expanded', 'false');

      const menu = document.createElement('div');
      menu.id = 'doc-langpicker-menu';
      menu.className = 'doc-langpicker-menu';
      menu.setAttribute('role', 'listbox');
      menu.style.display = 'none';

      // Build the menu rows from the <select>'s real <option>s — single
      // source of truth, future additions to the select auto-propagate.
      const _buildMenu = () => {
        menu.innerHTML = '';
        for (const opt of ls.options) {
          const row = document.createElement('button');
          row.type = 'button';
          row.className = 'doc-langpicker-item';
          row.dataset.value = opt.value;
          row.setAttribute('role', 'option');
          const ic = opt.value
            ? langIcon(opt.value, 14, { style: 'opacity:0.85;' })
            // Empty value = the "type" placeholder option — small dot so the
            // row still aligns with the others (and the picker shows _some_
            // mark when no type is set yet).
            : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;"><circle cx="12" cy="12" r="3"/></svg>';
          row.innerHTML = ic +
                          `<span class="doc-langpicker-label">${uiModule.esc(opt.textContent || opt.value)}</span>`;
          row.addEventListener('click', (e) => {
            e.stopPropagation();
            if (ls.value !== opt.value) {
              ls.value = opt.value;
              ls.dispatchEvent(new Event('change', { bubbles: true }));
            }
            _close();
          });
          menu.appendChild(row);
        }
      };
      _buildMenu();

      _syncLangPicker = () => {
        const v = ls.value || '';
        const sel = Array.from(ls.options).find(o => o.value === v) || ls.options[0];
        const ic = v
          ? langIcon(v, 14, { style: 'opacity:0.85;flex-shrink:0;' })
          // No language picked yet → small dot mark so the trigger isn't bare.
          : '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" style="opacity:0.5;flex-shrink:0;"><circle cx="12" cy="12" r="3"/></svg>';
        trigger.innerHTML = ic +
          `<span class="doc-langpicker-label">${uiModule.esc(sel?.textContent || 'type')}</span>` +
          '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:4px;opacity:0.6;"><polyline points="6 9 12 15 18 9"/></svg>';
        // Highlight the current row in the open menu.
        menu.querySelectorAll('.doc-langpicker-item').forEach(r => {
          r.classList.toggle('is-selected', r.dataset.value === v);
        });
      };

      const _close = () => {
        menu.style.display = 'none';
        trigger.setAttribute('aria-expanded', 'false');
        document.removeEventListener('click', _outsideClick, true);
        document.removeEventListener('keydown', _escKey, true);
      };
      const _outsideClick = (e) => {
        if (!menu.contains(e.target) && e.target !== trigger) _close();
      };
      const _escKey = (e) => {
        if (e.key !== 'Escape' || menu.style.display === 'none') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _close();
      };

      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = menu.style.display !== 'none';
        if (open) { _close(); return; }
        // Position the menu under the trigger (fixed so it escapes any
        // overflow-clipped ancestor like the footer).
        const r = trigger.getBoundingClientRect();
        menu.style.display = 'block';
        menu.style.position = 'fixed';
        menu.style.left = r.left + 'px';
        menu.style.top = (r.bottom + 4) + 'px';
        menu.style.minWidth = r.width + 'px';
        // If it would overflow the bottom of the viewport, flip above.
        requestAnimationFrame(() => {
          const mr = menu.getBoundingClientRect();
          if (mr.bottom > window.innerHeight - 8) {
            menu.style.top = Math.max(8, r.top - mr.height - 4) + 'px';
          }
        });
        trigger.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', _outsideClick, true);
        document.addEventListener('keydown', _escKey, true);
      });

      // Hide the native select but keep it in the layout for screen readers
      // / programmatic value sets / focus management. The icon span next to
      // it is removed since the trigger now carries the current icon.
      ls.classList.add('doc-langpicker-native-hidden');
      const iconSpan = document.getElementById('doc-language-icon');
      if (iconSpan) iconSpan.remove();
      ls.parentNode.insertBefore(trigger, ls);
      // Menu is body-mounted so position:fixed coords work cleanly.
      document.body.appendChild(menu);

      _syncLangPicker();
    })();
    document.getElementById('doc-language-select').addEventListener('change', () => {
      _syncLangIcon();
      _syncLangPicker();
      const val = document.getElementById('doc-language-select').value;
      // For form-backed docs, the select toggles between PDF view and the
      // markdown source instead of changing the underlying language.
      const live = document.getElementById('doc-editor-textarea')?.value
        || docs.get(activeDocId)?.content || '';
      if (_isFormBackedDoc(live) && (val === 'pdf' || val === 'markdown')) {
        _setPdfViewActive(val === 'pdf');
        return;
      }
      // Mark user explicitly chose a language — stop auto-detection
      if (activeDocId && docs.has(activeDocId)) {
        docs.get(activeDocId).userSetLanguage = (val !== '');
      }
      updateLanguage();
      syncHighlighting();
      // Show/hide markdown toolbar
      const lang = document.getElementById('doc-language-select').value;
      const mdToolbar = document.getElementById('doc-md-toolbar');
      if (mdToolbar) {
        // Toolbar stays visible for every type now; only the items inside
        // gate themselves on language.
        mdToolbar.style.display = '';
        if (mdToolbar._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
      }
      // If switching away from markdown, exit preview
      if (lang !== 'markdown') {
        _setMarkdownPreviewActive(false);
      }
      // If switching away from CSV, exit table preview
      if (lang !== 'csv') {
        const csvPreview = document.getElementById('doc-csv-preview');
        const wrap2 = document.getElementById('doc-editor-wrap');
        if (csvPreview) csvPreview.style.display = 'none';
        if (wrap2) wrap2.style.display = '';
      }
      // If switching away from html, exit HTML preview
      if (!_isRenderLang(lang)) exitHtmlPreview();
      // Show/hide email fields
      if (lang === 'email') {
        const doc = activeDocId && docs.get(activeDocId);
        if (doc) _showEmailFields(doc);
      } else {
        _hideEmailFields();
      }
      // Sync header action buttons for new language
      _syncHeaderActions();
    });

    // Email send/draft buttons
    // Inject emoji picker button into markdown toolbar
    const emojiSlot = document.getElementById('md-toolbar-emoji-slot');
    if (emojiSlot && !emojiSlot.querySelector('.emoji-picker-btn')) {
      // Resolve the live target on click: the WYSIWYG email contenteditable
      // when active, otherwise the plain markdown textarea.
      emojiSlot.appendChild(emojiPicker.createEmojiButton(
        () => _emailRichbodyActive() || document.getElementById('doc-editor-textarea')
      ));
    }

    document.getElementById('doc-email-send-btn')?.addEventListener('click', () => {
      // Pressing Send must never leave the "more options" menu showing.
      const _m = document.getElementById('doc-email-more-menu');
      if (_m) _m.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      _sendEmail();
    });

    // Ctrl+Enter / Cmd+Enter sends the email when an email doc is active
    // Bind once at module level via a guard to avoid duplicate listeners on re-open
    if (!window._emailCtrlEnterBound) {
      window._emailCtrlEnterBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          const doc = activeDocId && docs.get(activeDocId);
          if (doc && doc.language === 'email' && isOpen) {
            e.preventDefault();
            _sendEmail();
          }
        }
      });
    }
    // Ctrl+Alt+M (and Cmd+Opt+M on mac) flips Edit ↔ Preview on a markdown
    // doc. Bound once globally; gated on the doc panel being open and the
    // active doc being markdown so it doesn't fire while the user is typing
    // in a non-markdown context.
    if (!window._docMdToggleBound) {
      window._docMdToggleBound = true;
      document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.altKey && !e.shiftKey && (e.key === 'm' || e.key === 'M' || e.code === 'KeyM')) {
          if (!isOpen) return;
          const doc = activeDocId && docs.get(activeDocId);
          const lang = (doc?.language || 'markdown').toLowerCase();
          if (lang !== 'markdown') return;
          e.preventDefault();
          toggleMarkdownPreview();
          _syncHeaderActions();
        }
      });
    }
    document.getElementById('doc-email-draft-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _saveDraft();
    });
    document.getElementById('doc-email-discard-btn')?.addEventListener('click', _discardEmail);
    document.getElementById('doc-email-unread-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _markUnreadAndClose();
    });
    document.getElementById('doc-email-schedule-btn')?.addEventListener('click', (e) => {
      const anchor = document.getElementById('doc-email-send-caret') || e.currentTarget;
      document.getElementById('doc-email-more-menu').style.display = 'none';
      _scheduleSend(anchor);
    });
    document.getElementById('doc-email-ai-reply-btn')?.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _showDocAiReplyChoice(ev.currentTarget);
    });

    const collapseBtn = document.getElementById('doc-email-collapse-btn');
    if (collapseBtn && !collapseBtn._emailCollapseWired) {
      collapseBtn._emailCollapseWired = true;
      collapseBtn.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const focusState = _captureEmailBodyFocusState();
        const header = document.getElementById('doc-email-header');
        const nextCollapsed = !header?.classList.contains('doc-email-header-collapsed');
        _setEmailHeaderCollapsed(nextCollapsed);
        if (!nextCollapsed) _restoreEmailBodyFocusState(focusState);
      });
      collapseBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
    }
    ['doc-email-to', 'doc-email-cc', 'doc-email-bcc', 'doc-email-subject'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', _syncEmailHeaderSummary);
      document.getElementById(id)?.addEventListener('focus', () => _setEmailHeaderCollapsed(false, { manual: false }));
    });
    document.getElementById('doc-email-richbody')?.addEventListener('focus', _maybeAutoCollapseEmailHeader);
    if (window.visualViewport && !window._docEmailViewportCollapseBound) {
      window._docEmailViewportCollapseBound = true;
      window.visualViewport.addEventListener('resize', _maybeAutoCollapseEmailHeader);
    }

    // Split-button caret toggles the send-options menu (drops up).
    document.getElementById('doc-email-send-caret')?.addEventListener('click', (e) => {
      e.stopPropagation();
      const menu = document.getElementById('doc-email-more-menu');
      const caret = document.getElementById('doc-email-send-caret');
      if (!menu) return;
      const opening = menu.style.display === 'none';
      menu.style.display = opening ? '' : 'none';
      if (caret) caret.setAttribute('aria-expanded', String(opening));
    });
    document.addEventListener('click', (e) => {
      const menu = document.getElementById('doc-email-more-menu');
      // Keep the menu open ONLY while interacting with the caret itself or the
      // menu. Any other click — including the Send button (which sits in the
      // same .email-send-split) — closes it, so the popup is tied to the arrow.
      if (menu && !e.target.closest('#doc-email-send-caret, #doc-email-more-menu')) {
        menu.style.display = 'none';
        document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      const menu = document.getElementById('doc-email-more-menu');
      if (!menu || menu.style.display === 'none') return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation?.();
      menu.style.display = 'none';
      document.getElementById('doc-email-send-caret')?.setAttribute('aria-expanded', 'false');
    }, true);

    // Attachments
    document.getElementById('doc-email-attach-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-file-input')?.click();
    });
    document.getElementById('md-toolbar-attach-btn')?.addEventListener('click', () => {
      document.getElementById('doc-email-file-input')?.click();
    });
    document.getElementById('doc-email-file-input')?.addEventListener('change', _handleAttachUpload);

    // Cc/Bcc toggle
    document.getElementById('doc-email-show-cc')?.addEventListener('click', () => {
      _setEmailHeaderCollapsed(false, { manual: false });
      const ccRow = document.getElementById('doc-email-cc-row');
      const bccRow = document.getElementById('doc-email-bcc-row');
      if (ccRow) ccRow.style.display = '';
      if (bccRow) bccRow.style.display = '';
      document.getElementById('doc-email-show-cc').style.display = 'none';
      _syncEmailHeaderSummary();
    });

    // Cc/Bcc close — X buttons inside the Cc and Bcc fields hide both
    // rows + clear their inputs + restore the Cc opener on the To row.
    document.querySelectorAll('[data-cc-close]').forEach(closeBtn => {
      closeBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const ccRow = document.getElementById('doc-email-cc-row');
        const bccRow = document.getElementById('doc-email-bcc-row');
        const ccInput = document.getElementById('doc-email-cc');
        const bccInput = document.getElementById('doc-email-bcc');
        if (ccRow) ccRow.style.display = 'none';
        if (bccRow) bccRow.style.display = 'none';
        if (ccInput) ccInput.value = '';
        if (bccInput) bccInput.value = '';
        const ccToggle = document.getElementById('doc-email-show-cc');
        if (ccToggle) ccToggle.style.display = '';
        _syncEmailHeaderSummary();
      });
    });

    // Autocomplete for To / Cc / Bcc — typed fragment after the last
    // comma triggers contact search; Enter / Tab / click on a suggestion
    // appends "<email>, " so the user can keep typing more recipients.
    _wireRecipientAutocomplete('doc-email-to',  'doc-email-to-suggestions');
    _wireRecipientAutocomplete('doc-email-cc',  'doc-email-cc-suggestions');
    _wireRecipientAutocomplete('doc-email-bcc', 'doc-email-bcc-suggestions');

    // Header unified action button (preview or run depending on language)
    document.getElementById('doc-header-preview-btn').addEventListener('click', () => {
      const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
      if (lang === 'markdown') toggleMarkdownPreview();
      else if (lang === 'csv') toggleCsvPreview();
      else if (_isRenderLang(lang)) toggleHtmlPreview();
      else {
        // Runnable language — toggle output
        const outputPanel = document.getElementById('doc-run-output');
        if (outputPanel && outputPanel.style.display !== 'none') {
          outputPanel.style.display = 'none';
        } else {
          runDocument();
        }
      }
      _syncHeaderActions();
    });

    // Markdown Edit/Preview two-icon switch — click a side to go to that view.
    document.getElementById('doc-md-view-toggle')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.md-view-opt');
      if (!opt) return;
      const wantPreview = opt.dataset.mdview === 'preview';
      const mdPrev = document.getElementById('doc-md-preview');
      const isPreview = mdPrev && mdPrev.style.display !== 'none';
      if (wantPreview !== isPreview) toggleMarkdownPreview();
      _syncHeaderActions();
    });

    // Unified Code / Run-or-View two-icon switch — language-aware: CSV flips
    // between code and the table view, Python/JS/etc. between code and run
    // output, HTML/SVG/XML between code and the iframe preview.
    document.getElementById('doc-render-view-toggle')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.md-view-opt');
      if (!opt) return;
      const wantRun = opt.dataset.renderview === 'run';
      const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
      if (lang === 'csv') {
        const csv = document.getElementById('doc-csv-preview');
        const isOn = csv && csv.style.display !== 'none';
        if (wantRun !== isOn) toggleCsvPreview();
      } else if (_isRenderLang(lang)) {
        const htmlPrev = document.getElementById('doc-html-preview');
        const isOn = htmlPrev && htmlPrev.style.display !== 'none';
        if (wantRun !== isOn) toggleHtmlPreview();
      } else {
        // Runnable language (python / js / ts / bash …) — clicking Run is
        // a one-shot execute; clicking Code dismisses the output pane.
        if (wantRun) {
          document.getElementById('doc-header-preview-btn')?.click();
        } else {
          const out = document.getElementById('doc-run-output');
          if (out) out.style.display = 'none';
        }
      }
      _syncHeaderActions();
    });

    // Font size toggle (S → M → L)
    const fontBtn = document.getElementById('doc-fontsize-btn');
    const editorWrap = document.getElementById('doc-editor-wrap');
    const _fontSizes = ['s', 'm', 'l'];
    const _iconSizes = [12, 14, 16];
    let _fontIdx = parseInt(localStorage.getItem('odysseus-doc-fontsize') || '0', 10);
    if (!(_fontIdx >= 0 && _fontIdx < 3)) _fontIdx = 0;
    function _applyDocFont() {
      const richEmailBody = document.getElementById('doc-email-richbody');
      [editorWrap, richEmailBody].filter(Boolean).forEach(el => {
        el.classList.remove('doc-font-s', 'doc-font-m', 'doc-font-l');
        if (_fontSizes[_fontIdx] !== 's') el.classList.add('doc-font-' + _fontSizes[_fontIdx]);
      });
      if (fontBtn) {
        fontBtn.dataset.size = _fontSizes[_fontIdx];
        // Keep the original behaviour: the icon itself grows with the size.
        const svg = fontBtn.querySelector('svg');
        if (svg) { const sz = _iconSizes[_fontIdx]; svg.setAttribute('width', sz); svg.setAttribute('height', sz); }
        // Show only the active size letter (just S, or just M, or just L).
        fontBtn.querySelectorAll('.doc-fontsize-levels [data-sz]').forEach(el => {
          const active = el.dataset.sz === _fontSizes[_fontIdx];
          el.classList.toggle('active', active);
          el.style.display = active ? '' : 'none';
        });
      }
      localStorage.setItem('odysseus-doc-fontsize', _fontIdx);
    }
    _applyDocFont();
    // Click cycles through the sizes (S → M → L → S).
    if (fontBtn) fontBtn.addEventListener('click', () => {
      _fontIdx = (_fontIdx + 1) % 3;
      _applyDocFont();
      syncHighlighting();
    });

    // Undo button in header
    const docUndoBtn = document.getElementById('doc-undo-btn');
    if (docUndoBtn) docUndoBtn.addEventListener('click', async () => {
      const pdfPane = document.getElementById('doc-pdf-view');
      const pdfVisible = pdfPane && pdfPane.style.display !== 'none';
      if (pdfVisible && await _undoPdfPaneAction()) return;
      const ta = document.getElementById('doc-editor-textarea');
      if (ta) {
        ta.focus();   // execCommand('undo') needs the textarea focused
        document.execCommand('undo');
        _dismissDocKb();   // then force the keyboard back down on touch
      }
    });

    // Diff toggle button — compare current content against previous version
    const diffToggleBtn = document.getElementById('doc-diff-toggle-btn');
    if (diffToggleBtn) diffToggleBtn.addEventListener('click', async () => {
      if (_diffModeActive) {
        exitDiffMode(true);
        return;
      }
      if (!activeDocId) return;
      const ta = document.getElementById('doc-editor-textarea');
      if (!ta) return;
      const current = ta.value;

      // Fetch version history and compare against previous version
      try {
        const res = await fetch(`${API_BASE}/api/document/${activeDocId}/versions`);
        if (!res.ok) throw new Error('Failed');
        const versions = await res.json();
        if (versions.length < 2) {
          if (uiModule) uiModule.showToast('No previous version to compare');
          return;
        }
        // versions are sorted desc — [0] is latest, [1] is previous
        const prevContent = versions[1].content || '';
        if (prevContent === current) {
          if (uiModule) uiModule.showToast('No changes from previous version');
          return;
        }
        enterDiffMode(prevContent, current);
      } catch {
        if (uiModule) uiModule.showError('Failed to load version history');
      }
    });

    // Export PDF (form-backed markdown docs)
    document.getElementById('doc-export-pdf-btn')?.addEventListener('click', _downloadFilledPdf);

    // Toggle inline PDF view (form-backed markdown docs). Default for a
    // form-backed doc is "active" — the toggle reads back the visible state.
    document.getElementById('doc-pdf-view-btn')?.addEventListener('click', () => {
      const pane = document.getElementById('doc-pdf-view');
      const visible = pane && pane.style.display !== 'none';
      _setPdfViewActive(!visible);
    });

    // Toolbar buttons toggle: clicking the active mode clears it. Otherwise
    // the mode stays armed across multiple placements until the user turns
    // it off explicitly.
    document.getElementById('doc-pdf-add-text-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'text' ? null : 'text'));
    document.getElementById('doc-pdf-add-check-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'check' ? null : 'check'));
    document.getElementById('doc-pdf-add-sign-btn')?.addEventListener('click', () => _setPdfDropMode(_pdfDropMode === 'signature' ? null : 'signature'));
    document.getElementById('doc-pdf-refresh-btn')?.addEventListener('click', () => _renderPdfPane());

    // Markdown formatting toolbar
    initMdToolbar();

    // Wire highlighting sync
    const ta = document.getElementById('doc-editor-textarea');
    const pre = document.getElementById('doc-editor-highlight');
    if (ta && pre) {
      ta.addEventListener('input', () => {
        // Typing invalidates any pinned selection highlight
        if (_selections.length) clearSelection();
        // Auto-create a document if user types/pastes with no active doc.
        // Skip while a createDocument POST is in flight — otherwise typing
        // during the round-trip spawns a duplicate untitled doc.
        if (!activeDocId && !_creatingDoc && ta.value.trim()) {
          _autoCreateFromInput(ta.value);
          return;
        }
        // Sync text content immediately (prevents visual duplication from scroll desync)
        const codeEl = document.getElementById('doc-editor-code');
        if (codeEl && !codeEl.dataset.hasDiff) {
          codeEl.textContent = ta.value + '\n';
          codeEl.style.minHeight = ta.scrollHeight + 'px';
        }
        if (pre) {
          pre.scrollTop = ta.scrollTop;
          pre.scrollLeft = ta.scrollLeft;
        }
        updateLineNumbers(ta.value);
        // Debounce expensive operations (syntax highlighting, auto-detect, auto-save)
        clearTimeout(_hlDebounce);
        _hlDebounce = setTimeout(syncHighlighting, 80);
        clearTimeout(_autoDetectDebounce);
        _autoDetectDebounce = setTimeout(attemptAutoDetect, AUTO_DETECT_DELAY);
        clearTimeout(_autoTitleDebounce);
        _autoTitleDebounce = setTimeout(() => autoTitleFromContent(ta.value), 600);
        clearTimeout(_autoSaveDebounce);
        _autoSaveDebounce = setTimeout(() => { saveDocument({ silent: true }); }, 2000);
      });
      ta.addEventListener('scroll', () => {
        const code = document.getElementById('doc-editor-code');
        if (code) code.style.minHeight = ta.scrollHeight + 'px';
        pre.scrollTop = ta.scrollTop;
        pre.scrollLeft = ta.scrollLeft;
        syncGutterScroll();
        syncSelectionOverlay();
        // Re-position find rects so they track the textarea on scroll.
        if (_findMatches && _findMatches.length) {
          const _q = document.getElementById('doc-find-input')?.value || '';
          if (_q) renderFindRects(_findMatches.map(s => [s, s + _q.length]), _findIdx);
        }
      });
      // Tab key inserts a real tab; Escape clears selection
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          if (_diffModeActive) { exitDiffMode(true); return; }
          // First Esc clears any pinned selection without closing the
          // panel. Second Esc (no selection left) minimises the panel
          // to a dock chip. The previous all-in-one path made one Esc
          // press both clear and close, which was annoying because the
          // user lost their working doc by hitting Esc to dismiss a
          // mistaken highlight.
          if (_selections.length > 0) {
            clearSelection();
            e.preventDefault();
            e.stopPropagation();
            return;
          }
          // No pinned selection — Esc MINIMIZES the panel (tabs it
          // down to a dock chip) — same as the chevron button.
          e.preventDefault();
          e.stopPropagation();
          closePanel('down');
          return;
        }
        if (e.key === 'Tab') {
          e.preventDefault();
          document.execCommand('insertText', false, '\t');
        }
        // Markdown shortcuts (only when language is markdown)
        const lang = document.getElementById('doc-language-select')?.value;
        if (lang === 'markdown' && (e.ctrlKey || e.metaKey)) {
          if (e.key === 'b') { e.preventDefault(); applyMdFormat('bold'); }
          else if (e.key === 'i') { e.preventDefault(); applyMdFormat('italic'); }
          else if (e.key === 'k') { e.preventDefault(); applyMdFormat('link'); }
        }
      });

      // ── In-document find (Ctrl+F) ──
      let _findMatches = [];
      let _findIdx = -1;

      function _openFindBar() {
        const bar = document.getElementById('doc-find-bar');
        if (!bar) return;
        bar.style.display = 'flex';
        // The highlight overlay is normally display:none (single-layer
        // rendering — textarea owns the visible text). Find marks live
        // inside that overlay, so we have to re-show it while find is
        // active. The body class lets a CSS rule un-hide it without
        // touching every per-language stylesheet path.
        document.body.classList.add('doc-find-active');
        const inp = document.getElementById('doc-find-input');
        if (inp) { inp.focus(); inp.select(); }
      }
      function _closeFindBar() {
        const bar = document.getElementById('doc-find-bar');
        if (bar) bar.style.display = 'none';
        document.body.classList.remove('doc-find-active');
        _findMatches = [];
        _findIdx = -1;
        const cnt = document.getElementById('doc-find-count');
        if (cnt) cnt.textContent = '';
        const codeEl = document.getElementById('doc-editor-code');
        if (codeEl) {
          delete codeEl.dataset.findQuery;
          delete codeEl.dataset.findCurrent;
          applyFindMarks(codeEl);
        }
        renderFindRects([], -1);
        ta.focus();
      }
      function _doFind(dir, focusTextarea) {
        const inp = document.getElementById('doc-find-input');
        const cnt = document.getElementById('doc-find-count');
        if (!inp) return;
        const q = inp.value;
        const codeEl = document.getElementById('doc-editor-code');
        if (!q) {
          _findMatches = []; _findIdx = -1;
          if (cnt) cnt.textContent = '';
          if (codeEl) { delete codeEl.dataset.findQuery; delete codeEl.dataset.findCurrent; applyFindMarks(codeEl); }
          return;
        }
        const text = ta.value;
        const lq = q.toLowerCase();
        const lt = text.toLowerCase();
        _findMatches = [];
        let pos = 0;
        while (true) {
          const i = lt.indexOf(lq, pos);
          if (i < 0) break;
          _findMatches.push(i);
          pos = i + 1;
        }
        if (_findMatches.length === 0) {
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
        } else {
          _findIdx = 0;
        }
        if (cnt) cnt.textContent = `${_findIdx + 1} / ${_findMatches.length}`;
        const matchPos = _findMatches[_findIdx];
        // Highlight the match in the textarea without stealing focus from the input
        ta.setSelectionRange(matchPos, matchPos + q.length);
        const linesBefore = text.slice(0, matchPos).split('\n').length;
        const lineH = parseFloat(getComputedStyle(ta).lineHeight) || 18;
        ta.scrollTop = Math.max(0, (linesBefore - 3) * lineH);
        if (codeEl) {
          codeEl.dataset.findQuery = q;
          codeEl.dataset.findCurrent = String(_findIdx);
          applyFindMarks(codeEl);
        }
        // Dedicated overlay rects on top of the textarea — bulletproof
        // visibility across markdown / email / code modes.
        renderFindRects(_findMatches.map(s => [s, s + q.length]), _findIdx);
        if (focusTextarea) ta.focus();
      }

      document.getElementById('doc-find-close')?.addEventListener('click', _closeFindBar);
      document.getElementById('doc-find-next')?.addEventListener('click', () => _doFind('next', true));
      document.getElementById('doc-find-prev')?.addEventListener('click', () => _doFind('prev', true));
      document.getElementById('doc-find-input')?.addEventListener('input', () => _doFind('first', false));
      document.getElementById('doc-find-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { e.preventDefault(); _closeFindBar(); }
        else if (e.key === 'Enter') { e.preventDefault(); _doFind(e.shiftKey ? 'prev' : 'next', false); }
      });

      // Intercept Ctrl+F on the editor pane
      pane.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
          e.preventDefault();
          e.stopPropagation();
          _openFindBar();
        }
      });

      // Delete (or Backspace) over the doc PANEL itself (not while typing in
      // a field) deletes the active document. Matches the email-reader Delete
      // behavior so the keyboard shortcut is consistent across surfaces.
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Delete' && e.key !== 'Backspace') return;
        if (!isPanelOpen()) return;
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
        e.preventDefault();
        deleteActiveDocument();
      });

      // Drag-and-drop file attachment for email compose docs. The whole pane
      // is the drop target; visual highlight while a drag is hovering. Files
      // dropped get uploaded via the same compose-upload endpoint as the
      // file picker.
      let _dragDepth = 0;
      const _isEmailDrag = (e) => {
        const doc = docs.get(activeDocId);
        if (!doc || doc.language !== 'email') return false;
        const dt = e.dataTransfer;
        if (!dt) return false;
        // Files-only — don't trigger on text drags etc.
        return dt.types && Array.from(dt.types).includes('Files');
      };
      pane.addEventListener('dragenter', (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        _dragDepth++;
        pane.classList.add('email-dragover');
      });
      pane.addEventListener('dragover', (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
      });
      pane.addEventListener('dragleave', (e) => {
        if (!_isEmailDrag(e)) return;
        _dragDepth = Math.max(0, _dragDepth - 1);
        if (_dragDepth === 0) pane.classList.remove('email-dragover');
      });
      pane.addEventListener('drop', async (e) => {
        if (!_isEmailDrag(e)) return;
        e.preventDefault();
        _dragDepth = 0;
        pane.classList.remove('email-dragover');
        const files = e.dataTransfer.files;
        if (files && files.length) await _uploadComposeFiles(files);
      });

      // Track selection for AI-assisted editing
      ta.addEventListener('mouseup', () => {
        setTimeout(updateSelectionState, 50);
      });
      ta.addEventListener('keyup', (e) => {
        if (e.shiftKey) updateSelectionState();
      });
      // ESC clears any pinned selections — matches the badge's clear
      // button so users have a keyboard shortcut for the same action.
      ta.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && _selections.length > 0) {
          e.preventDefault();
          e.stopPropagation();
          clearSelection();
        }
      });
    }

    renderTabs();

    // If no docs loaded, show empty state with helpful placeholder
    if (docs.size === 0 || !activeDocId) {
      showEmptyState();
    }
  }

  /** Apply markdown formatting to the textarea selection */
  let _lastMdFormat = { action: null, t: 0 };
  // Styled two-field link dialog (display text + URL). Resolves {url, text}
  // or null on cancel. Reuses the styled-prompt CSS. Text is optional — left
  // empty it falls back to the selected text, then the URL itself.
  function _promptLink(defaultText = '') {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.id = 'doc-link-prompt-overlay';
      overlay.className = 'modal';
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box styled-prompt-box">' +
          '<div class="modal-header"><h4>Insert link</h4></div>' +
          '<div class="modal-body">' +
            '<input type="text" id="doc-link-text" class="styled-prompt-input" placeholder="Link text (optional)" maxlength="500" />' +
            '<input type="url" id="doc-link-url" class="styled-prompt-input" placeholder="https://example.com" maxlength="2048" style="margin-top:8px;" />' +
          '</div>' +
          '<div class="modal-footer">' +
            '<button id="doc-link-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>' +
            '<button id="doc-link-ok" class="confirm-btn confirm-btn-primary">Insert</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
      const textEl = overlay.querySelector('#doc-link-text');
      const urlEl = overlay.querySelector('#doc-link-url');
      textEl.value = defaultText || '';
      function done(result) {
        overlay.remove();
        document.removeEventListener('keydown', onKey, true);
        resolve(result);
      }
      function submit() {
        const url = (urlEl.value || '').trim();
        if (!url) { urlEl.focus(); return; }
        done({ url, text: (textEl.value || '').trim() });
      }
      function onKey(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(null); }
      }
      overlay.querySelector('#doc-link-ok').addEventListener('click', submit);
      overlay.querySelector('#doc-link-cancel').addEventListener('click', () => done(null));
      overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
      urlEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
      textEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); urlEl.focus(); } });
      document.addEventListener('keydown', onKey, true);
      // Focus the URL field when the text is prefilled; otherwise start at text.
      requestAnimationFrame(() => { (defaultText ? urlEl : textEl).focus(); });
    });
  }

  // Email WYSIWYG link insertion. We snapshot the Range first (the dialog steals
  // focus and would otherwise collapse it) and insert via direct DOM ops, since
  // execCommand is unreliable once focus has moved to the modal.
  async function _wysiwygInsertLink(rich) {
    const selObj = window.getSelection();
    let savedRange = null;
    if (selObj && selObj.rangeCount) {
      const r = selObj.getRangeAt(0);
      if (rich.contains(r.commonAncestorContainer)) savedRange = r.cloneRange();
    }
    const selText = savedRange ? savedRange.toString() : '';
    let res;
    try { res = await _promptLink(selText); } catch (_) { res = null; }
    if (!res) { rich.focus(); return; }
    let url = (res.url || '').trim();
    if (!url) { rich.focus(); return; }
    if (!/^[a-z][a-z0-9+.-]*:/i.test(url) && !url.startsWith('//')) url = 'https://' + url;
    const linkText = (res.text || '').trim() || selText || url;

    if (!savedRange) {
      savedRange = document.createRange();
      savedRange.selectNodeContents(rich);
      savedRange.collapse(false);
    }
    const a = document.createElement('a');
    a.href = url;
    if (selText && linkText === selText) {
      // Unchanged selection — wrap it to keep any inline formatting.
      a.appendChild(savedRange.extractContents());
    } else {
      savedRange.deleteContents();
      a.textContent = linkText;
    }
    savedRange.insertNode(a);
    // Place the caret right after the inserted link.
    const after = document.createRange();
    after.setStartAfter(a);
    after.collapse(true);
    rich.focus();
    const s = window.getSelection();
    s.removeAllRanges();
    s.addRange(after);
    _syncEmailRichbody(rich);
  }

  function applyMdFormat(action) {
    // Guard against a duplicate/"ghost" click firing the same toggle twice in
    // quick succession — that would wrap then immediately unwrap, so the
    // markers appear for a split second and vanish.
    const _now = Date.now();
    if (_lastMdFormat.action === action && _now - _lastMdFormat.t < 350) return;
    _lastMdFormat = { action, t: _now };
    // Email WYSIWYG: format the live rich text via execCommand instead of
    // inserting markdown markers into the (hidden) source textarea.
    const _rich = _emailRichbodyActive();
    if (_rich) {
      _rich.focus();
      // Link needs an async styled URL prompt — handle it separately so we can
      // save/restore the selection (opening the modal collapses it otherwise).
      if (action === 'link') { _wysiwygInsertLink(_rich); return; }
      const _cmd = { bold: 'bold', italic: 'italic', strike: 'strikeThrough',
                     ul: 'insertUnorderedList', ol: 'insertOrderedList', hr: 'insertHorizontalRule' };
      try {
        if (_cmd[action]) document.execCommand(_cmd[action]);
        else if (action === 'h1' || action === 'h2' || action === 'h3') {
          // Toggle: if the block is already this heading, revert to a normal
          // paragraph; otherwise apply (or switch to) the heading.
          const cur = _currentBlockTag(_rich);
          document.execCommand('formatBlock', false, (cur === action) ? 'div' : action);
        } else if (action === 'code') {
          const cur = _currentBlockTag(_rich);
          document.execCommand('formatBlock', false, (cur === 'pre') ? 'div' : 'pre');
        }
        // quote/check/codeblock have no clean execCommand — skipped in WYSIWYG v1.
      } catch (_) {}
      _syncEmailRichbody(_rich);
      if (_rich._syncActive) _rich._syncActive();
      return;
    }
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const val = ta.value;
    const sel = val.substring(start, end);
    const before = val.substring(0, start);
    const after = val.substring(end);

    // Inline wrap toggles: bold, italic, strike, code
    const wrapMarks = { bold: '**', italic: '*', strike: '~~', code: '`' };
    if (wrapMarks[action]) {
      const m = wrapMarks[action];
      _applyWrapToggle(ta, before, sel, after, start, end, m, action);
      return;
    }

    // Numbered list — special handling for incrementing numbers
    if (action === 'ol') {
      _applyOrderedList(ta, start, end);
      return;
    }

    // Headings get their own toggle so applying the same level removes it and
    // a different level switches cleanly (rather than stacking # markers).
    if (action === 'h1' || action === 'h2' || action === 'h3') {
      _applyHeadingToggle(ta, start, { h1: '# ', h2: '## ', h3: '### ' }[action]);
      return;
    }

    // Line prefix toggles: quote, lists, checkbox
    const prefixMap = { quote: '> ', ul: '- ', check: '- [ ] ' };
    if (prefixMap[action]) {
      _applyLinePrefixToggle(ta, start, end, prefixMap[action]);
      return;
    }

    // Non-toggle actions
    let insert = '';
    let sS = start, sE = start;
    switch (action) {
      case 'link':
        if (sel) {
          insert = `[${sel}](url)`;
          sS = start + 1; sE = start + 1 + sel.length;
        } else {
          insert = '[text](url)';
          sS = start + 1; sE = start + 5;
        }
        break;
      case 'codeblock': {
        // Toggle: find if current line/selection is inside a ``` block
        const linesBefore = val.substring(0, start).split('\n');
        const linesAfter = val.substring(end).split('\n');
        // Look backward for opening ```
        let openIdx = -1;
        for (let i = linesBefore.length - 1; i >= 0; i--) {
          if (/^```/.test(linesBefore[i].trimEnd())) { openIdx = i; break; }
        }
        // Look forward for closing ```
        let closeIdx = -1;
        for (let i = 0; i < linesAfter.length; i++) {
          if (/^```\s*$/.test(linesAfter[i].trimEnd())) { closeIdx = i; break; }
        }
        if (openIdx >= 0 && closeIdx >= 0) {
          // Unwrap: remove the opening and closing fence lines
          const openLineStart = linesBefore.slice(0, openIdx).join('\n').length + (openIdx > 0 ? 1 : 0);
          const openLineEnd = openLineStart + linesBefore[openIdx].length + 1; // +1 for \n
          const closeLineStart = end + linesAfter.slice(0, closeIdx).join('\n').length + (closeIdx > 0 ? 1 : 0);
          const closeLineEnd = closeLineStart + linesAfter[closeIdx].length + (closeIdx < linesAfter.length - 1 ? 1 : 0);
          // Remove closing first (so indices stay valid), then opening
          _replaceRange(ta, closeLineStart, closeLineEnd, '');
          _replaceRange(ta, openLineStart, openLineEnd, '');
          const inner = val.substring(openLineEnd, closeLineStart);
          ta.selectionStart = openLineStart;
          ta.selectionEnd = openLineStart + inner.length;
          return;
        }
        // Wrap in code block
        const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
        insert = nl + '```\n' + (sel || '') + '\n```\n';
        sS = start + nl.length + 4;
        sE = sS + (sel ? sel.length : 0);
        break;
      }
      case 'hr': {
        const nl = before.length > 0 && !before.endsWith('\n') ? '\n' : '';
        insert = `${nl}---\n`;
        sE = sS = start + insert.length;
        break;
      }
      default: return;
    }
    _replaceRange(ta, start, end, insert);
    ta.selectionStart = sS;
    ta.selectionEnd = sE;
  }

  /** Replace a range in the textarea using execCommand to preserve undo stack */
  function _replaceRange(ta, from, to, text) {
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

  /** Toggle inline wrap markers (**, *, ~~, `) */
  function _applyWrapToggle(ta, before, sel, after, start, end, mark, action) {
    const mLen = mark.length;

    // Case 1: selection is wrapped inside — e.g. selected "**bold**" → unwrap to "bold"
    if (sel.startsWith(mark) && sel.endsWith(mark) && sel.length > mLen * 2) {
      const inner = sel.slice(mLen, -mLen);
      _replaceRange(ta, start, end, inner);
      ta.selectionStart = start;
      ta.selectionEnd = start + inner.length;
      return;
    }

    // Case 2: markers are outside selection — e.g. **|bold|** → unwrap
    if (before.endsWith(mark) && after.startsWith(mark)) {
      _replaceRange(ta, start - mLen, end + mLen, sel);
      ta.selectionStart = start - mLen;
      ta.selectionEnd = end - mLen;
      return;
    }

    // Case 3: wrap — add markers. With no selection, insert empty markers and
    // drop the cursor between them (don't inject the action name as text).
    const inner = sel;
    const wrapped = mark + inner + mark;
    _replaceRange(ta, start, end, wrapped);
    ta.selectionStart = start + mLen;
    ta.selectionEnd = start + mLen + inner.length;
  }

  /** Toggle line prefix (headings, quotes, lists) */
  // The block-level tag (h1/h2/h3/pre/p/…) containing the current selection in
  // a contenteditable root — used to decide whether a heading toggle should
  // apply or revert.
  function _currentBlockTag(root) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return '';
    let node = sel.getRangeAt(0).startContainer;
    if (node.nodeType === 3) node = node.parentNode;
    while (node && node !== root) {
      const tag = node.tagName && node.tagName.toLowerCase();
      if (tag && /^(h1|h2|h3|h4|h5|h6|p|div|pre|blockquote|li)$/.test(tag)) return tag;
      node = node.parentNode;
    }
    return '';
  }

  // Heading toggle for the markdown textarea: strips any existing leading
  // `#{1,6} `, then removes it (toggle off) if it was the same level, or applies
  // the new level otherwise.
  function _applyHeadingToggle(ta, caret, prefix) {
    const val = ta.value;
    const lineStart = val.lastIndexOf('\n', caret - 1) + 1;
    const nlIdx = val.indexOf('\n', caret);
    const lineEnd = nlIdx === -1 ? val.length : nlIdx;
    const line = val.substring(lineStart, lineEnd);
    const m = line.match(/^(#{1,6}) /);
    let newLine;
    if (m && m[1].length === prefix.trim().length) {
      newLine = line.slice(m[0].length);            // same level → toggle off
    } else if (m) {
      newLine = prefix + line.slice(m[0].length);   // different level → switch
    } else {
      newLine = prefix + line;                       // none → add
    }
    _replaceRange(ta, lineStart, lineEnd, newLine);
    const delta = newLine.length - line.length;
    const pos = Math.max(lineStart, caret + delta);
    ta.selectionStart = ta.selectionEnd = pos;
    ta.focus();
  }

  function _applyLinePrefixToggle(ta, start, end, prefix) {
    const val = ta.value;
    const sel = val.substring(start, end);
    const lineStart = val.lastIndexOf('\n', start - 1) + 1;

    if (sel) {
      // Multi-line: toggle prefix on each line
      const lines = sel.split('\n');
      const nonEmpty = lines.filter(l => l.trim());
      const allPrefixed = nonEmpty.length > 0 && nonEmpty.every(l => l.startsWith(prefix));
      const result = allPrefixed
        ? lines.map(l => l.startsWith(prefix) ? l.slice(prefix.length) : l).join('\n')
        : lines.map(l => l.trim() ? prefix + l : l).join('\n');
      _replaceRange(ta, start, end, result);
      ta.selectionStart = start;
      ta.selectionEnd = start + result.length;
    } else {
      // No selection: toggle prefix on the current line
      const lineBefore = val.substring(lineStart, start);

      if (lineBefore.startsWith(prefix)) {
        // Remove prefix
        _replaceRange(ta, lineStart, lineStart + prefix.length, '');
      } else {
        // Add prefix at line start
        _replaceRange(ta, lineStart, lineStart, prefix);
      }
    }
  }

  /** Toggle ordered list with incrementing numbers */
  function _applyOrderedList(ta, start, end) {
    const val = ta.value;
    const sel = val.substring(start, end);
    const lineStart = val.lastIndexOf('\n', start - 1) + 1;

    if (sel) {
      const lines = sel.split('\n');
      const nonEmpty = lines.filter(l => l.trim());
      const allNumbered = nonEmpty.length > 0 && nonEmpty.every(l => /^\d+\.\s/.test(l));
      const result = allNumbered
        ? lines.map(l => l.replace(/^\d+\.\s/, '')).join('\n')
        : (() => { let n = 0; return lines.map(l => l.trim() ? `${++n}. ${l}` : l).join('\n'); })();
      _replaceRange(ta, start, end, result);
      ta.selectionStart = start;
      ta.selectionEnd = start + result.length;
    } else {
      const lineBefore = val.substring(lineStart, start);
      if (/^\d+\.\s/.test(lineBefore)) {
        const prefixLen = lineBefore.match(/^\d+\.\s/)[0].length;
        _replaceRange(ta, lineStart, lineStart + prefixLen, '');
      } else {
        // Find the previous numbered line to continue the sequence
        const prevText = val.substring(0, lineStart);
        const prevMatch = prevText.match(/(\d+)\.\s[^\n]*\n$/);
        const num = prevMatch ? parseInt(prevMatch[1]) + 1 : 1;
        _replaceRange(ta, lineStart, lineStart, `${num}. `);
      }
    }
  }

  /** Wire up the markdown formatting toolbar */
  // Grouped formatting dropdown (headings / code / lists). Menu is appended to
  // <body> so the draggable panel's transform can't clip its fixed position.
  let _mdDdOpenedAt = 0;
  function _showMdDropdown(toggleBtn) {
    const kind = toggleBtn.dataset.dd;
    const now = Date.now();
    const existing = document.getElementById('doc-md-dd-menu');
    // Mobile fires a duplicate/ghost click right after the real one. If it lands
    // on the same toggle it would re-toggle the menu shut the instant it opened.
    // Ignore a same-kind re-invocation within 400ms so the menu stays up.
    if (existing && existing.dataset.dd === kind && (now - _mdDdOpenedAt) < 400) return;
    const prevKind = existing && existing.dataset.dd;
    if (existing) existing.remove();
    if (existing && prevKind === kind) return; // same toggle clicked → just close
    _mdDdOpenedAt = now;

    const groups = {
      heading: [['h1', 'Heading 1', 'H1'], ['h2', 'Heading 2', 'H2'], ['h3', 'Heading 3', 'H3']],
      code: [['code', 'Inline code', '`'], ['codeblock', 'Code block', '```']],
      list: [['ul', 'Bullet list', '•'], ['ol', 'Numbered list', '1.']],
    };
    const items = groups[kind];
    if (!items) return;

    const rect = toggleBtn.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'doc-md-dd-menu';
    menu.dataset.dd = kind;
    menu.className = 'doc-overflow-menu open';
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = rect.left + 'px';
    menu.style.zIndex = '9999';
    items.forEach(([md, label, ico]) => {
      const it = document.createElement('button');
      it.className = 'doc-overflow-item';
      const icoSpan = document.createElement('span');
      icoSpan.className = 'md-dd-ico';
      icoSpan.textContent = ico;
      const lbl = document.createElement('span');
      lbl.textContent = label;
      it.append(icoSpan, lbl);
      // Don't let the menu item steal focus from the editor (preserve selection).
      it.addEventListener('mousedown', (ev) => ev.preventDefault());
      it.addEventListener('click', (ev) => { ev.stopPropagation(); menu.remove(); applyMdFormat(md); });
      menu.appendChild(it);
    });
    document.body.appendChild(menu);

    const close = (ev) => {
      if (ev && ev.type === 'keydown') {
        if (ev.key !== 'Escape') return;
        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation?.();
      }
      if (ev && ev.type === 'click') {
        // Ignore the ghost/duplicate click mobile fires right after opening.
        if (Date.now() - _mdDdOpenedAt < 400) return;
        if (menu.contains(ev.target) || toggleBtn.contains(ev.target)) return;
      }
      menu.remove();
      document.removeEventListener('click', close, true);
      document.removeEventListener('keydown', close, true);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close, true);
    };
    setTimeout(() => {
      document.addEventListener('click', close, true);
      document.addEventListener('keydown', close, true);
      window.addEventListener('scroll', close, true);
      window.addEventListener('resize', close, true);
    }, 0);
  }

  function initMdToolbar() {
    const toolbar = document.getElementById('doc-md-toolbar');
    if (!toolbar) return;

    const itemsWrap = document.getElementById('md-toolbar-items');
    const overflowWrapper = document.getElementById('md-toolbar-overflow-wrapper');
    const overflowToggle = document.getElementById('md-toolbar-overflow-toggle');
    const overflowMenu = document.getElementById('md-toolbar-overflow-menu');
    const undoBtn = document.getElementById('md-toolbar-undo');

    // Click handler for format buttons + the grouped dropdown toggles. The menu
    // is appended to <body> (not nested in the toolbar) so the draggable panel's
    // CSS transform doesn't reparent its fixed positioning or clip it.
    // Keep the editor's focus + selection when a format button / dropdown
    // toggle is pressed. Without this the button steals focus on press, which
    // collapses the textarea selection (so B/I/S apply to nothing) and, on
    // mobile, drops the keyboard — whose viewport resize then instantly closes
    // any dropdown that just opened. Preventing the default mousedown keeps the
    // textarea focused, so formatting hits the live selection and menus stay up.
    toolbar.addEventListener('mousedown', (e) => {
      if (e.target.closest('[data-md], .md-dd-toggle, .emoji-picker-btn')) e.preventDefault();
    });

    toolbar.addEventListener('click', (e) => {
      const dd = e.target.closest('.md-dd-toggle');
      if (dd) { e.preventDefault(); _showMdDropdown(dd); return; }
      const btn = e.target.closest('[data-md]');
      if (!btn) return;
      e.preventDefault();
      applyMdFormat(btn.dataset.md);
    });

    // Undo button
    if (undoBtn) {
      undoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        const ta = document.getElementById('doc-editor-textarea');
        if (ta) { ta.focus(); document.execCommand('undo'); }
      });
    }

    // Overflow collapse logic
    let _mdMenuOpen = false;
    // Horizontal-scroll affordance: the toolbar scrolls its icons; edge arrows
    // appear when there's more off either side and smoothly scroll to that edge.
    const scrollLeftBtn = document.getElementById('md-scroll-left');
    const scrollRightBtn = document.getElementById('md-scroll-right');
    function updateScrollArrows() {
      if (!itemsWrap || !scrollLeftBtn || !scrollRightBtn) return;
      const maxScroll = itemsWrap.scrollWidth - itemsWrap.clientWidth;
      const overflowing = maxScroll > 2;
      scrollLeftBtn.style.display = (overflowing && itemsWrap.scrollLeft > 1) ? 'flex' : 'none';
      scrollRightBtn.style.display = (overflowing && itemsWrap.scrollLeft < maxScroll - 1) ? 'flex' : 'none';
    }
    scrollLeftBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: 0, behavior: 'smooth' }));
    scrollRightBtn?.addEventListener('click', () => itemsWrap.scrollTo({ left: itemsWrap.scrollWidth, behavior: 'smooth' }));
    itemsWrap?.addEventListener('scroll', updateScrollArrows, { passive: true });
    if (window.ResizeObserver && itemsWrap) {
      new ResizeObserver(updateScrollArrows).observe(itemsWrap);
    }

    function syncMdOverflow() {
      if (overflowWrapper) overflowWrapper.style.display = 'none';
      updateScrollArrows();
    }

    function closeMdMenu() {
      _mdMenuOpen = false;
      if (overflowMenu) overflowMenu.classList.remove('open');
    }

    if (overflowToggle) {
      overflowToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        _mdMenuOpen = !_mdMenuOpen;
        if (_mdMenuOpen) {
          document.body.appendChild(overflowMenu);
          const rect = overflowToggle.getBoundingClientRect();
          overflowMenu.style.position = 'fixed';
          overflowMenu.style.top = (rect.bottom + 2) + 'px';
          overflowMenu.style.right = (window.innerWidth - rect.right) + 'px';
          overflowMenu.style.left = 'auto';
        } else {
          overflowWrapper.appendChild(overflowMenu);
        }
        overflowMenu.classList.toggle('open', _mdMenuOpen);
      });
    }
    document.addEventListener('click', () => {
      if (_mdMenuOpen) { closeMdMenu(); overflowWrapper.appendChild(overflowMenu); }
    });

    // Re-check overflow on resize
    let _mdResizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(_mdResizeTimer);
      _mdResizeTimer = setTimeout(syncMdOverflow, 100);
    });

    // Show toolbar if language is already markdown
    const lang = document.getElementById('doc-language-select')?.value;
    if (lang === 'markdown') toolbar.style.display = '';

    // Initial sync after layout
    requestAnimationFrame(syncMdOverflow);
    // Expose for external calls (e.g. after fullscreen toggle)
    toolbar._syncOverflow = syncMdOverflow;
  }

  /** Collapse action buttons into overflow "..." menu (3 most-used visible) */
  const _DOC_RECENTS_KEY = 'odysseus-doc-actions-recent';
  const _DOC_MAX_VISIBLE = 2;

  function _getDocRecent() {
    try { return JSON.parse(localStorage.getItem(_DOC_RECENTS_KEY) || '[]'); } catch { return []; }
  }
  function _trackDocAction(id) {
    let recent = _getDocRecent().filter(x => x !== id);
    recent.unshift(id);
    if (recent.length > 10) recent.length = 10;
    localStorage.setItem(_DOC_RECENTS_KEY, JSON.stringify(recent));
  }

  function initActionOverflow() {
    const actionsEl = document.getElementById('doc-editor-actions');
    const wrapper = document.getElementById('doc-overflow-wrapper');
    const toggle = document.getElementById('doc-overflow-toggle');
    const menu = document.getElementById('doc-overflow-menu');
    if (!actionsEl || !wrapper || !toggle || !menu) return;

    const allBtns = Array.from(actionsEl.querySelectorAll('.doc-collapsible-btn'));
    let _menuOpen = false;

    function syncOverflow() {
      allBtns.forEach(b => { b.classList.remove('doc-collapsed'); });
      menu.innerHTML = '';

      // Filter to currently visible buttons
      const available = allBtns.filter(b => b.style.display !== 'none');

      // Sort by recent usage, defaults: copy, export, save
      const recent = _getDocRecent();
      const defaults = ['doc-copy-btn', 'doc-export-btn', 'doc-save-btn'];
      const order = recent.length > 0 ? recent : defaults;

      // Auto-pin: md preview when language is markdown
      const lang = document.getElementById('doc-language-select')?.value;
      const pinned = [];
      if (lang === 'markdown') {
        const mdBtn = available.find(b => b.id === 'doc-md-btn');
        if (mdBtn) pinned.push(mdBtn);
      }

      const sorted = [...available].sort((a, b) => {
        const ai = order.indexOf(a.id), bi = order.indexOf(b.id);
        if (ai >= 0 && bi >= 0) return ai - bi;
        if (ai >= 0) return -1;
        if (bi >= 0) return 1;
        return 0;
      });

      // Pinned + top N (deduplicated) — pinned count against the max
      const visible = [...pinned];
      for (const btn of sorted) {
        if (visible.length >= _DOC_MAX_VISIBLE) break;
        if (!visible.includes(btn)) visible.push(btn);
      }
      // Ensure we never exceed MAX_VISIBLE
      while (visible.length > _DOC_MAX_VISIBLE) visible.pop();
      const overflow = sorted.filter(b => !visible.includes(b));

      // Show visible, hide overflow
      overflow.forEach(b => b.classList.add('doc-collapsed'));

      // Reorder DOM: visible buttons before wrapper
      for (const btn of visible) {
        actionsEl.insertBefore(btn, wrapper);
      }

      if (overflow.length > 0) {
        wrapper.style.display = '';
        overflow.forEach(btn => {
          const item = document.createElement('button');
          item.className = 'doc-overflow-item';
          item.innerHTML = btn.innerHTML + '<span>' + (btn.title || '') + '</span>';
          item.addEventListener('click', (e) => {
            _trackDocAction(btn.id);
            // Export button has its own submenu
            if (btn.id === 'doc-export-btn') {
              e.stopPropagation();
              const savedRect = item.getBoundingClientRect();
              closeMenu();
              setTimeout(() => showExportMenu(null, savedRect), 50);
              return;
            }
            closeMenu();
            btn.click();
            syncOverflow(); // re-sort with new recency
          });
          menu.appendChild(item);
        });
      } else {
        wrapper.style.display = 'none';
      }
    }

    function closeMenu() {
      _menuOpen = false;
      menu.classList.remove('open');
    }

    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      _menuOpen = !_menuOpen;
      if (_menuOpen) {
        // Move to body to escape overflow:hidden on doc-editor-pane
        document.body.appendChild(menu);
        const rect = toggle.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.top = (rect.bottom + 2) + 'px';
        menu.style.right = (window.innerWidth - rect.right) + 'px';
        menu.style.left = 'auto';
      } else {
        wrapper.appendChild(menu);
      }
      menu.classList.toggle('open', _menuOpen);
    });
    document.addEventListener('click', () => {
      if (_menuOpen) { closeMenu(); wrapper.appendChild(menu); }
    });

    // Also track when visible buttons are clicked directly
    allBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        _trackDocAction(btn.id);
        // Defer re-sort so the click handler fires first
        setTimeout(syncOverflow, 100);
      });
    });

    requestAnimationFrame(syncOverflow);
    _syncOverflow = syncOverflow;
  }

  /** Divider drag to resize the editor pane */
  function initDividerDrag(divider, pane, isRight) {
    let dragging = false;
    divider.addEventListener('mousedown', (e) => {
      dragging = true;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const width = isRight
        ? e.clientX
        : window.innerWidth - e.clientX;
      pane.style.width = Math.max(250, Math.min(width, window.innerWidth * 0.7)) + 'px';
      pane.style.flex = 'none';
    });
    document.addEventListener('mouseup', () => {
      if (dragging) {
        dragging = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        // Re-sync highlighting and line numbers after resize
        syncHighlighting();
        const ta = document.getElementById('doc-editor-textarea');
        if (ta) updateLineNumbers(ta.value);
      }
    });
  }

  /** Close the editor panel */
  // When the doc panel is "tab/chevron down" minimized, it lives as a chip
  // in the bottom dock instead of a red toolbar indicator. We remember which
  // doc was active so the chip can restore it.
  let _minimizedDocId = null;

  function _ensureDocChipRegistered() {
    if (Modals.isRegistered('doc-panel')) return;
    Modals.register('doc-panel', {
      // The ✕ / drag-to-trash on the minimized chip is a real close — detach
      // the doc from the chat session so it doesn't reappear in that chat.
      closeFn: () => {
        // Content was already saved to the map when the panel was minimized,
        // so just detach (don't re-read the now-removed editor).
        const id = _minimizedDocId;
        _minimizedDocId = null;
        if (id) _detachDocFromSession(id);
      },
      restoreFn: () => {
        const id = _minimizedDocId;
        _minimizedDocId = null;
        // openPanel builds the pane shell; switchToDoc re-renders the
        // saved doc content into it (including PDF render-pages, syntax
        // highlighting, etc.). Without switchToDoc, the pane is empty.
        openPanel();
        if (id && docs.has(id)) {
          try { switchToDoc(id); } catch (e) { console.error('Restore doc failed:', e); }
        }
      },
    });
  }

  export function closePanel(direction) {
    if (!isOpen) {
      if (direction !== 'down' && Modals.isRegistered('doc-panel')) {
        _minimizedDocId = null;
        _markDocVisibleState(_lastSessionId, 'closed');
        Modals.unregister('doc-panel');
      }
      return;
    }
    isOpen = false;
    // On touch, closing the doc should leave the keyboard DOWN. The tap blurs
    // the textarea (keyboard starts down), but a stray refocus during teardown
    // (the view behind regaining focus, etc.) was bouncing it back up. Blur any
    // focused field now and again after the close settles to keep it down.
    if (direction !== 'down' && (('ontouchstart' in window) || (navigator.maxTouchPoints || 0) > 0)) {
      const _dropKb = () => {
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) { try { ae.blur(); } catch (_) {} }
      };
      _dropKb();
      requestAnimationFrame(_dropKb);
      setTimeout(_dropKb, 80);
    }
    // Save current state
    saveCurrentToMap();

    // A "down" close means minimize, not close. Register the chip and flip
    // the dock state to minimized so a chip appears at the bottom. Any
    // other direction is a real close — make sure any leftover chip from a
    // prior minimize cycle is cleared too.
    if (direction === 'down') {
      _minimizedDocId = activeDocId;
      _markDocVisibleState(_lastSessionId, 'minimized');
      _ensureDocChipRegistered();
      Modals.minimize('doc-panel');
    } else if (Modals.isRegistered('doc-panel')) {
      _minimizedDocId = null;
      _markDocVisibleState(_lastSessionId, 'closed');
      Modals.unregister('doc-panel');
    } else {
      _markDocVisibleState(_lastSessionId, 'closed');
    }

    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');

    const _finishClose = () => {
      // If the panel was reopened during the slide-out animation (close →
      // reopen fast, e.g. close a draft then immediately compose a new one),
      // bail — otherwise this stale close strips doc-view after the new open
      // re-added it, and the fresh pane drops into the desktop split layout
      // (renders as a narrow "sidebar" on mobile).
      if (isOpen) { if (pane) pane.remove(); if (divider) divider.remove(); return; }
      document.body.classList.remove('doc-view');
      const container = document.getElementById('chat-container');
      if (container) container.style.display = '';
      if (pane) pane.remove();
      if (divider) divider.remove();
      activeDocId = null;
      const btn = document.getElementById('overflow-doc-btn');
      if (btn) btn.classList.remove('active');
      const docInd = document.getElementById('doc-indicator-btn');
      if (docInd) docInd.classList.remove('active');
    };

    if (pane) {
      // Determine slide direction
      let transform;
      if (direction === 'down') {
        // Full slide off-screen on mobile (sheet dismiss); small nudge on desktop.
        transform = window.innerWidth <= 768 ? 'translateY(100%)' : 'translateY(30px)';
      } else {
        const fromLeft = pane.classList.contains('doc-left');
        transform = fromLeft ? 'translateX(-40px)' : 'translateX(40px)';
      }
      pane.style.transition = 'transform 0.15s ease-in, opacity 0.1s ease-in';
      pane.style.transform = transform;
      pane.style.opacity = '0';
      if (divider) { divider.style.transition = 'opacity 0.1s ease-in'; divider.style.opacity = '0'; }
      pane.addEventListener('transitionend', _finishClose, { once: true });
      // Safety fallback
      setTimeout(_finishClose, 200);
    } else {
      _finishClose();
    }
  }

  /** Swap doc panel side (called when sidebar side changes) */
  export function swapSide() {
    if (!isOpen) return;
    const pane = document.getElementById('doc-editor-pane');
    const divider = document.getElementById('doc-divider');
    const container = document.getElementById('chat-container');
    if (!pane || !divider || !container) return;

    const sidebar = document.getElementById('sidebar');
    const isRight = sidebar && sidebar.classList.contains('right-side');

    if (isRight) {
      // Sidebar moved right → doc goes left (before chat)
      pane.classList.add('doc-left');
      container.parentNode.insertBefore(pane, container);
      container.parentNode.insertBefore(divider, container);
    } else {
      // Sidebar moved left → doc goes right (after chat)
      pane.classList.remove('doc-left');
      container.after(divider);
      divider.after(pane);
    }

    // Re-init divider drag for the new side
    initDividerDrag(divider, pane, isRight);
  }

  // ---- Document CRUD ----

  /** Create a new document for the current session */
  // Create a new blank document, reusing the current/last session or
  // auto-creating one. Same flow as the tab-bar "+" — the single entry point
  // the sidebar Library "+" should use too.
  export async function newDocument() {
    let sessionId = docs.get(activeDocId)?.sessionId
      || _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try { sessionId = await _autoCreateSession(); }
      catch (e) { console.error('Failed to auto-create session for document:', e); return; }
    }
    await createDocument(sessionId);
  }

  export async function createDocument(sessionId) {
    if (_creatingDoc) return;
    _creatingDoc = true;
    // If the panel was in empty-state, the user may type into the editor
    // during the create round-trip — preserve that text into the new doc
    // instead of letting switchToDoc blank it.
    const wasEmpty = !activeDocId;
    try {
      const res = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          title: '',
          content: '',
          language: 'markdown',
        }),
      });
      const doc = await res.json();
      addDocToTabs(doc, sessionId);
      if (!isOpen) openPanel();
      // Re-enable editor if it was in empty state
      let textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.disabled = false;
        textarea.placeholder = 'Document content...';
      }
      // Capture text typed during the round-trip (only when starting from the
      // empty editor — don't steal another doc's content).
      const typed = (wasEmpty && textarea && textarea.value.trim()) ? textarea.value : '';
      switchToDoc(doc.id);
      if (typed) {
        textarea = document.getElementById('doc-editor-textarea');
        if (textarea) textarea.value = typed;
        const d = docs.get(doc.id);
        if (d) d.content = typed;
        syncHighlighting();
        clearTimeout(_autoSaveDebounce);
        _autoSaveDebounce = setTimeout(() => { saveDocument({ silent: true }); }, 800);
      }
      textarea = document.getElementById('doc-editor-textarea');
      if (textarea) textarea.focus();
    } catch (e) {
      console.error('Failed to create document:', e);
      if (uiModule) uiModule.showError('Failed to create document');
    } finally {
      _creatingDoc = false;
    }
  }

  /** Load an existing document into a tab */
  /** Inject a freshly-created doc dict (from a POST response) directly into
   * the tabs without re-fetching it via GET. Fixes a race where GET
   * /api/document/{id} can 404 right after a successful POST — we already
   * have the full doc payload from the create response, no need to round-trip.
   */
  export function injectFreshDoc(doc) {
    if (!doc || !doc.id) return;
    const sessionId = doc.session_id || _lastSessionId || null;
    addDocToTabs(doc, sessionId);
    // Use _ensureDocPaneMounted (not `if (!isOpen) openPanel()`): when a draft
    // is composed from the email modal, `isOpen` can be stale-true while the
    // actual pane was torn down — a bare openPanel() early-returns and the doc
    // mounts into a wrong/half-built pane (rendered as a narrow sidebar on
    // mobile instead of its own full-screen window). This remounts it cleanly.
    _ensureDocPaneMounted();
    // Defer to next frame so the panel DOM exists before switchToDoc populates
    requestAnimationFrame(() => requestAnimationFrame(() => {
      switchToDoc(doc.id);
    }));
  }

  export async function replaceEmailReplyBody(docId, replyText) {
    const doc = docs.get(docId);
    if (!doc) return;
    const fields = _parseEmailHeader(doc.content || '');
    const lines = String(fields.body || '').split('\n');
    const quoteIdx = lines.findIndex(line =>
      /^-{5,}\s*Previous message\s*-{5,}$/i.test(line.trim())
      || /^On .+ wrote:\s*$/i.test(line.trim())
    );
    const quote = quoteIdx >= 0 ? lines.slice(quoteIdx).join('\n') : '';
    const ownText = _emailReplyOwnText(fields.body || '');
    if (ownText && !/^(\[AI reply draft will appear here\]|Drafting AI reply)/i.test(ownText)) {
      if (uiModule) uiModule.showToast('AI reply ready, but draft was edited');
      return;
    }
    const body = String(replyText || '').trim() + (quote ? `\n\n${quote}` : '');
    doc.content = _buildEmailContent(
      fields.to,
      fields.subject,
      fields.inReplyTo,
      fields.references,
      body,
      fields.sourceUid,
      fields.sourceFolder,
      fields.cc,
      fields.bcc,
    );
    if (activeDocId === docId) {
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) await _streamEmailBodyText(textarea, body);
    }
    clearTimeout(_autoSaveDebounce);
    _autoSaveDebounce = setTimeout(() => { saveDocument({ silent: true }); }, 800);
  }

  // Force the panel into a genuinely-open state. `isOpen` can be true while the
  // pane was torn down by another full-screen view (e.g. opening a doc from the
  // email modal): in that case openPanel() early-returns and nothing mounts, so
  // the doc silently never appears. Reset the stale flag and re-open for real.
  function _ensureDocPaneMounted() {
    if (!isOpen || !document.getElementById('doc-editor-pane')) {
      isOpen = false;
      openPanel();
    }
  }

  export async function loadDocument(docId) {
    // If already in tabs, just switch
    if (docs.has(docId)) {
      _ensureDocPaneMounted();
      switchToDoc(docId);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/document/${docId}`);
      if (!res.ok) throw new Error(res.status === 404 ? 'Not found' : `HTTP ${res.status}`);
      const doc = await res.json();
      addDocToTabs(doc, doc.session_id);
      _ensureDocPaneMounted();
      switchToDoc(doc.id);
    } catch (e) {
      console.error('Failed to load document:', e);
      if (uiModule) {
        const msg = e.message === 'Not found'
          ? 'Document not found — try opening it from the Library.'
          : 'Could not open document.';
        uiModule.showError(msg);
      }
    }
  }

  // Deep-link: #document-<id> opens that document on load / URL-bar nav.
  // Clicks on in-chat document anchors are handled separately (they call
  // preventDefault, so they don't change the hash); this covers refresh
  // and pasted/typed document URLs, which previously did nothing.
  function _maybeOpenDocFromHash() {
    const m = (window.location.hash || '').match(/^#document-(.+)$/);
    if (m) loadDocument(m[1]);
  }

  /** Open panel and ensure a document exists, creating a session if needed */
  export async function ensureDocPanel() {
    let sessionId = _lastSessionId
      || (sessionModule && sessionModule.getCurrentSessionId());
    if (!sessionId) {
      try {
        sessionId = await _autoCreateSession();
      } catch (e) {
        console.error('Failed to auto-create session for document:', e);
        openPanel();
        return;
      }
    }
    await loadSessionDocs(sessionId);
  }

  /** Create a session and sync it with the sessions module */
  async function _autoCreateSession() {
    // Materialize pending chat first if one exists
    if (sessionModule && sessionModule.hasPendingChat && sessionModule.hasPendingChat()) {
      await sessionModule.materializePendingSession();
      const id = sessionModule.getCurrentSessionId();
      if (id) { _lastSessionId = id; return id; }
    }
    // Preserve the current model when creating a doc session
    const curModel = sessionModule?.getCurrentModel ? sessionModule.getCurrentModel() : null;
    const sessions = sessionModule ? sessionModule.getSessions() : [];
    const match = curModel && sessions.find(s => s.model === curModel && s.endpoint_url);
    const fd = new FormData();
    fd.append('name', `Notes ${new Date().toLocaleTimeString()}`);
    fd.append('skip_validation', 'true');
    if (match) {
      fd.append('endpoint_url', match.endpoint_url);
      fd.append('model', match.model);
      if (match.endpoint_id) fd.append('endpoint_id', match.endpoint_id);
    }
    const res = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Session create failed');
    const payload = await res.json();
    const sessionId = payload.id;
    _lastSessionId = sessionId;
    // Tell sessions module so chat uses the same session
    if (sessionModule && sessionModule.setCurrentSessionId) {
      sessionModule.setCurrentSessionId(sessionId);
    }
    if (sessionModule && sessionModule.loadSessions) sessionModule.loadSessions();
    return sessionId;
  }

  /** Load all documents for a session into tabs */
  export async function loadSessionDocs(sessionId, opts = {}) {
    _lastSessionId = sessionId;
    const restoreMode = !!opts.restoreMode;
    const shouldRestoreOpen = localStorage.getItem(_docOpenKey(sessionId)) === '1';
    const shouldRestoreMinimized = localStorage.getItem(_docMinimizedKey(sessionId)) === '1';
    // Clear docs from other sessions so tabs are per-session,
    // but keep session-less docs (e.g. email compose) — they're independent
    for (const [id, doc] of [...docs]) {
      if (doc.sessionId && doc.sessionId !== sessionId) docs.delete(id);
    }
    activeDocId = null;

    // Show loading state while fetching
    if (isOpen) _showLoadingOverlay();

    try {
      const res = await fetch(`${API_BASE}/api/documents/${sessionId}`);
      const allDocs = await res.json();
      _hideLoadingOverlay();
      // Only load active docs
      const activeDocs = allDocs.filter(d => d.is_active);
      if (activeDocs.length === 0) {
        // No docs yet — show empty editor, doc will be created when user types
        if (!restoreMode || shouldRestoreOpen) {
          if (!isOpen) openPanel();
          showEmptyState();
          renderTabs();
        }
        return;
      }
      for (const doc of activeDocs) {
        if (!docs.has(doc.id)) {
          addDocToTabs(doc, sessionId);
        }
      }
      _syncDocIndicator();
      // Switch to the most recently active one (or first)
      const target = activeDocs[0];
      if (restoreMode && shouldRestoreMinimized && !shouldRestoreOpen) {
        activeDocId = null;
        _minimizedDocId = target.id;
        _markDocVisibleState(sessionId, 'minimized');
        _ensureDocChipRegistered();
        Modals.minimize('doc-panel');
        return;
      }
      // Removed: the old "if restoreMode && !shouldRestoreOpen → stay
      // closed" branch. Users expect that entering a chat with an
      // attached document opens the panel automatically, not just shows
      // an indicator. The minimised branch above still respects an
      // explicit user choice to dock the panel; everything else falls
      // through to the "open panel" path below.
      if (false) {
        activeDocId = null;
        _minimizedDocId = null;
        if (Modals.isRegistered('doc-panel')) Modals.unregister('doc-panel');
        return;
      }
      // Always open when there are docs — the minimised branch above
      // already returned for users who explicitly docked the panel.
      // The previous `if (!restoreMode || shouldRestoreOpen)` gate left
      // the panel closed on first entry to a chat with docs, which
      // hides the doc unless the user manually opens the panel.
      _markDocVisibleState(sessionId, 'open');
      if (!isOpen) openPanel();
      switchToDoc(target.id);
    } catch (e) {
      _hideLoadingOverlay();
      console.error('Failed to load session documents:', e);
      // Open empty panel on error too
      if (!isOpen) openPanel();
      showEmptyState();
    }
  }

  /** Add a document to the tabs map */
  function addDocToTabs(doc, sessionId) {
    const existing = docs.get(doc.id);
    docs.set(doc.id, {
      id: doc.id,
      title: doc.title || '',
      language: doc.language || '',
      content: doc.current_content || '',
      version: doc.version_count || 1,
      sessionId: sessionId || doc.session_id,
      userSetLanguage: !!doc.language,
      _composeAtts: existing?._composeAtts,
      // Provenance for the "Send signed reply" flow
      sourceEmailUid:       doc.source_email_uid || null,
      sourceEmailFolder:    doc.source_email_folder || null,
      sourceEmailAccountId: doc.source_email_account_id || null,
      sourceEmailMessageId: doc.source_email_message_id || null,
    });
  }

  /** Populate the editor with document data (used internally) */
  function populateEditor(doc) {
    const titleInput = document.getElementById('doc-title-input');
    const textarea = document.getElementById('doc-editor-textarea');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');

    if (titleInput) titleInput.value = doc.title || '';
    if (textarea) textarea.value = doc.current_content || doc.content || '';
    if (langSelect) langSelect.value = doc.language || 'markdown';
    if (badge) { const _v = doc.version_count || doc.version || 1; badge.textContent = `v${_v}`; badge.style.display = _v > 1 ? '' : 'none'; }
    { const _v = doc.version_count || doc.version || 1; const _dbtn = document.getElementById('doc-diff-toggle-btn'); if (_dbtn) _dbtn.style.display = _v > 1 ? '' : 'none'; }
    syncHighlighting();
  }

  /** Post-process hljs markdown output: colorize [brackets] and heading # markers */
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

  // Find-result rectangles drawn ON TOP of the textarea — bypasses
  // the syntax-highlight overlay entirely so visibility works in
  // markdown, email, and any other mode regardless of single-layer-
  // rendering quirks. Same mirror-measurement approach as pinned
  // selections so wrap matches the textarea exactly.
  //
  // `matches` is an array of [start, end] offsets; `currentIdx` is
  // the focused one (gets brighter accent). Pass empty matches to
  // clear all rects.
  function renderFindRects(matches, currentIdx) {
    const wrap = document.getElementById('doc-editor-wrap');
    if (!wrap) return;
    wrap.querySelectorAll('.doc-find-rect').forEach(el => el.remove());
    if (!matches || matches.length === 0) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const text = textarea.value;
    const style = getComputedStyle(textarea);
    const paddingTop = parseFloat(style.paddingTop) || 10;
    const paddingLeft = parseFloat(style.paddingLeft) || 48;
    const lineHeight = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);

    let mirror = document.getElementById('doc-find-rect-mirror');
    if (!mirror) {
      mirror = document.createElement('div');
      mirror.id = 'doc-find-rect-mirror';
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

    const scrollTop = textarea.scrollTop;
    for (let i = 0; i < matches.length; i++) {
      const [s, e] = matches[i];
      // Line-band style: highlight the FULL visual row containing the
      // match. Cheap, always-visible, doesn't need character-precise
      // mirror measurement that varies across email/markdown/code modes.
      mirror.textContent = text.substring(0, s);
      const startTop = mirror.scrollHeight - paddingTop;
      // Find the wrap-row's end by measuring with one extra char beyond
      // the match end and stepping back to the last whitespace boundary.
      mirror.textContent = text.substring(0, e);
      const endHeight = mirror.scrollHeight - paddingTop;
      mirror.textContent = '';

      const top = paddingTop + startTop - scrollTop;
      const height = Math.max(endHeight - startTop, lineHeight);
      const rect = document.createElement('div');
      rect.className = 'doc-find-rect' + (i === currentIdx ? ' current' : '');
      rect.style.cssText =
        `position:absolute;left:${paddingLeft}px;right:8px;` +
        `top:${top}px;height:${height}px;` +
        `pointer-events:none;z-index:6;border-radius:2px;`;
      wrap.appendChild(rect);
    }
  }

  /** Wrap find-matches in the syntax-highlighted overlay with <mark> spans.
   * Walks text nodes so existing hljs spans are preserved. Matches that cross
   * syntax tokens are skipped (rare for user searches). */
  function applyFindMarks(codeEl) {
    if (!codeEl) return;
    // Remove prior find marks (unwrap)
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

  /** Sync highlighted overlay with textarea content */
  function syncHighlighting() {
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

  /** Update the line number gutter */
  let _lineNumberResizeObserver = null;
  let _lineNumberObservedTextarea = null;
  let _lineNumberResizeRaf = null;

  function _lineNumberContentEl(gutter) {
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

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', _scheduleLineNumberRerender);
  }

  function updateLineNumbers(text, force = false) {
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

  /** Sync line number gutter scroll with textarea */
  function syncGutterScroll() {
    const textarea = document.getElementById('doc-editor-textarea');
    const gutter = document.getElementById('doc-line-numbers');
    if (textarea && gutter) {
      _lineNumberContentEl(gutter).style.transform = `translateY(${-textarea.scrollTop}px)`;
    }
  }

  /** Attempt language auto-detection using hljs.highlightAuto() */
  /** Quick heuristic check for markdown before falling back to hljs */
  function _looksLikeMarkdown(text) {
    const lines = text.slice(0, 2000).split('\n');
    let score = 0;
    for (const line of lines) {
      if (/^#{1,6}\s/.test(line)) score += 3;         // headings
      else if (/^\s*[-*+]\s/.test(line)) score += 1;  // list items
      else if (/^\s*\d+\.\s/.test(line)) score += 1;  // ordered list
      else if (/^\s*>/.test(line)) score += 1;         // blockquote
      else if (/\[.+\]\(.+\)/.test(line)) score += 2; // links
      else if (/^```/.test(line)) score += 2;          // fenced code
      else if (/\*\*.+\*\*/.test(line)) score += 1;   // bold
      else if (/^---\s*$/.test(line)) score += 1;      // horizontal rule
    }
    return score >= 3;
  }

  function attemptAutoDetect() {
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

  // ---- Selection-based AI editing ----

  // Tracked selection state — when set, the next chat message auto-includes this context
  let _selections = [];  // [{ text, startLine, endLine, start, end }, ...]

  // Pinned-selection overlays are positioned in pixel coords measured
  // against the textarea's current size. When the window shrinks (or
  // the sidebar collapses, or the panel resizes), the text wraps to
  // more rows but the overlay rectangles stay where they were —
  // visibly drifting off the real highlighted text. Re-render on any
  // size change so the overlays follow the new wrap. Debounced via
  // rAF to coalesce the rapid-fire ResizeObserver pulses during a
  // drag-resize.
  let _selResizeScheduled = false;
  function _scheduleSelRerender() {
    if (_selResizeScheduled || _selections.length === 0) return;
    _selResizeScheduled = true;
    requestAnimationFrame(() => {
      _selResizeScheduled = false;
      try { renderAllSelectionHighlights(); } catch (_) {}
    });
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', _scheduleSelRerender);
  }
  // Observe the textarea itself so internal layout changes (sidebar
  // collapse, panel snap, mobile keyboard show/hide) also trigger a
  // re-render. The observer attaches lazily on first selection so we
  // don't churn before the editor mounts.
  let _selResizeObserver = null;
  function _ensureSelResizeObserver() {
    if (_selResizeObserver || typeof ResizeObserver === 'undefined') return;
    const ta = document.getElementById('doc-editor-textarea');
    if (!ta) return;
    _selResizeObserver = new ResizeObserver(_scheduleSelRerender);
    _selResizeObserver.observe(ta);
  }

  // Detect whether the textarea is currently wrapping any line. If
  // every logical line fits on one visual row, the overlay positions
  // are exact and pinned selections are safe regardless of fullscreen
  // state. We compute rendered-row-count from scrollHeight/line-height
  // and compare against the number of \n-separated lines.
  function _textareaWraps(ta) {
    if (!ta) return false;
    const style = getComputedStyle(ta);
    const lh = parseFloat(style.lineHeight) || (parseFloat(style.fontSize) * 1.45);
    if (!lh) return false;
    const padTop = parseFloat(style.paddingTop) || 0;
    const padBottom = parseFloat(style.paddingBottom) || 0;
    const renderedRows = Math.round((ta.scrollHeight - padTop - padBottom) / lh);
    const logicalLines = (ta.value || '').split('\n').length;
    return renderedRows > logicalLines;
  }

  /** Update selection tracking, show badge + persistent highlight.
   *  Each new selection is added (pinned). Click without selecting to clear all. */
  function updateSelectionState() {
    // Pinned selections are safe whenever the overlay measurement can
    // be exact. That holds in two cases: (1) fullscreen — width is
    // stable, or (2) no line wrapping — every logical \n-line fits on
    // one visual row, so character-precise mirror measurement isn't
    // needed. Outside both cases, panel resizes / wrap shifts make
    // overlays drift, so we no-op.
    const _pane = document.querySelector('.doc-editor-pane');
    const _isFs = !!(_pane && _pane.classList.contains('doc-fullscreen'));
    const _ta0 = document.getElementById('doc-editor-textarea');
    if (!_isFs && _textareaWraps(_ta0)) {
      if (_selections.length) clearSelection();
      return;
    }
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

  /** Show a selection indicator badge with count + clear button */
  function showSelectionBadge() {
    let badge = document.getElementById('doc-selection-badge');
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
    const labels = _selections.map(s =>
      s.startLine === s.endLine ? `L${s.startLine}` : `L${s.startLine}-${s.endLine}`
    );
    const label = _selections.length === 1
      ? `${labels[0]} selected`
      : `${_selections.length} selections (${labels.join(', ')})`;
    badge.innerHTML = `${label}<button class="doc-selection-clear" title="Clear all selections">&times;</button>`;
    badge.style.display = '';
    badge.querySelector('.doc-selection-clear').addEventListener('click', (e) => {
      e.stopPropagation();
      clearSelection();
    });
  }

  /** Markdown / prose docs get character-precise highlights (like a
   *  normal browser selection but persistent). Code docs get line-based
   *  highlights — when working in code you usually operate on whole
   *  lines, and the character-based version reads as jittery against
   *  monospace alignment. */
  function _isCodeDoc() {
    const lang = (document.getElementById('doc-language-select')?.value || '').toLowerCase();
    if (!lang) return false;
    // Prose / preview types that should get character-precise highlights.
    const prose = new Set(['markdown', 'md', 'text', 'txt', 'email', 'html', 'csv']);
    return !prose.has(lang);
  }

  /** Measure the visual x,y position of a character index inside the
   *  mirror element by inserting a zero-width marker span there and
   *  reading its bounding rect. Returns {x, y} relative to the mirror's
   *  content-box origin. */
  function _measurePos(mirror, text, pos) {
    mirror.innerHTML = '';
    if (pos > 0) mirror.appendChild(document.createTextNode(text.substring(0, pos)));
    const marker = document.createElement('span');
    marker.textContent = '​';
    mirror.appendChild(marker);
    const r = marker.getBoundingClientRect();
    const m = mirror.getBoundingClientRect();
    return { x: r.left - m.left, y: r.top - m.top };
  }

  /** Render persistent highlight overlays for all selections */
  // Re-anchor pinned selections against the live textarea content. After
  // an undo or any other path that shrinks/shifts the text, captured
  // {start, end} positions can point at unrelated content (or past the
  // end of the buffer). We:
  //   1. Verify the captured text still sits at [start, end].
  //   2. If not, look for the captured text elsewhere in the doc and
  //      re-anchor. Prefers the nearest occurrence to the old position.
  //   3. If the captured text is gone entirely, drop the selection.
  //   4. Refresh derived fields (startLine/endLine) when re-anchored.
  // Cheap O(N) per selection; only runs when _selections is non-empty.
  function _validateSelections(text) {
    if (_selections.length === 0) return;
    const survivors = [];
    for (const s of _selections) {
      const captured = s.text || '';
      if (!captured) continue;
      // Fast path: still at the same offsets.
      if (text.substring(s.start, s.end) === captured) {
        survivors.push(s);
        continue;
      }
      // Re-anchor: find the captured text and pick the occurrence
      // nearest to the old start so multi-match docs don't snap to the
      // wrong one. indexOf scans are cheap for typical doc sizes.
      let best = -1, bestDist = Infinity;
      let from = 0;
      while (true) {
        const idx = text.indexOf(captured, from);
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
        startLine: text.substring(0, newStart).split('\n').length,
        endLine: text.substring(0, newEnd).split('\n').length,
      });
    }
    _selections = survivors;
  }

  function renderAllSelectionHighlights() {
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
    if (_selections.length === 0) return;
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

    for (const sel of _selections) {
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
        wrap.appendChild(overlay);
      } else {
        // Character-precise: measure the actual selection start/end via
        // a marker span. Render one rect for single-line selections, or
        // three rects (first partial, middle full, last partial) for
        // multi-line selections.
        const startPos = _measurePos(mirror, text, sel.start);
        const endPos = _measurePos(mirror, text, sel.end);
        mirror.innerHTML = '';

        const addRect = (top, left, width, height) => {
          const overlay = document.createElement('div');
          overlay.className = 'doc-selection-overlay';
          overlay.style.top = (paddingTop + top - scrollTop) + 'px';
          overlay.style.left = (paddingLeft + left) + 'px';
          if (width != null) overlay.style.width = width + 'px';
          else overlay.style.right = '0';
          overlay.style.height = height + 'px';
          wrap.appendChild(overlay);
        };

        if (Math.abs(endPos.y - startPos.y) < 1) {
          // Single visual line.
          addRect(startPos.y, startPos.x, endPos.x - startPos.x, lineHeight);
        } else {
          // First line: from selection start to right edge.
          addRect(startPos.y, startPos.x, null, lineHeight);
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

  /** Sync all selection highlight positions on scroll */
  function syncSelectionOverlay() {
    if (_selections.length === 0) return;
    renderAllSelectionHighlights();
  }

  /** Clear all selections, badge, and highlights */
  function clearSelection() {
    _selections = [];
    const badge = document.getElementById('doc-selection-badge');
    if (badge) badge.style.display = 'none';
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.querySelectorAll('.doc-selection-overlay').forEach(el => el.remove());
  }

  /**
   * Get all selection contexts for chat injection.
   * Called by chat module before sending a message.
   * Returns null if no selections, or array of { text, startLine, endLine }.
   */
  export function getSelectionContext() {
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

  // ── Inline Suggestion Comments (Google Docs style) ──

  let _activeSuggestions = []; // [{ id, find, replace, reason, highlightEl, bubbleEl }]

  /** Persist suggestions to localStorage for the active doc */
  function _saveSuggestionsToStorage() {
    if (!activeDocId) return;
    const data = _activeSuggestions.map(s => ({ id: s.id, find: s.find, replace: s.replace, reason: s.reason }));
    if (data.length) {
      localStorage.setItem('odysseus-suggestions-' + activeDocId, JSON.stringify(data));
    } else {
      localStorage.removeItem('odysseus-suggestions-' + activeDocId);
    }
  }

  /** Restore suggestions from localStorage for a doc */
  function _restoreSuggestionsFromStorage(docId) {
    try {
      const raw = localStorage.getItem('odysseus-suggestions-' + docId);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (!Array.isArray(data) || !data.length) return;
      _activeSuggestions = data.map(s => ({ id: s.id, find: s.find, replace: s.replace, reason: s.reason, cardEl: null }));
      _suggestionTotal = _activeSuggestions.length;
      _suggestionIndex = 0;
      _showCurrentSuggestion();
    } catch {}
  }

  /** Handle doc_suggestions SSE event — show one suggestion at a time.
   *
   *  If a previous batch is already pending approval, NEW suggestions are
   *  appended to the live queue rather than replacing it. The agent (or a
   *  follow-up batch) can keep adding edits while the user reviews; the count
   *  and "n of m" header update on the fly. */
  export function handleDocSuggestions(data) {
    if (_diffModeActive) exitDiffMode(true);
    if (!data.suggestions || !data.suggestions.length) return;

    if (!isOpen) openPanel();
    if (data.doc_id && data.doc_id !== activeDocId) switchToDoc(data.doc_id);

    const hadPending = _activeSuggestions.length > 0;
    const existingIds = new Set(_activeSuggestions.map(s => s.id));

    // Append new suggestions, skipping any IDs already in the queue so a
    // re-sent batch doesn't duplicate.
    let added = 0;
    for (const sugg of data.suggestions) {
      if (existingIds.has(sugg.id)) continue;
      _activeSuggestions.push({
        id: sugg.id,
        find: sugg.find,
        replace: sugg.replace,
        reason: sugg.reason,
        cardEl: null,
      });
      added++;
    }
    _suggestionTotal = (_suggestionTotal || 0) + added;

    _saveSuggestionsToStorage();

    // If nothing was pending before, kick off the visual flow. Otherwise the
    // currently-shown suggestion stays on screen and the queue size update is
    // reflected in the next card's header.
    if (!hadPending) {
      _suggestionIndex = 0;
      _showCurrentSuggestion();
    } else {
      // Refresh just the counter in the active card so the user sees the
      // queue grew while they were deliberating.
      const active = document.getElementById('doc-suggestion-active');
      if (active) {
        const counter = active.querySelector('.doc-suggestion-counter');
        if (counter) {
          const num = _suggestionTotal - _activeSuggestions.length + 1;
          counter.textContent = `${num} / ${_suggestionTotal}`;
        }
      }
    }
  }

  /** Render the current suggestion card (one at a time) + inline diff in document */
  function _showCurrentSuggestion() {
    const wrap = document.getElementById('doc-editor-wrap');
    const pane = document.querySelector('.doc-editor-pane');
    if (!wrap || !pane) return;

    // Remove previous card and inline diff
    const old = document.getElementById('doc-suggestion-active');
    if (old) { if (old._cleanup) old._cleanup(); old.remove(); }
    _clearSuggestionHighlight();
    _clearInlineDiff();

    if (_activeSuggestions.length === 0) {
      return;
    }

    const sugg = _activeSuggestions[0];
    const remaining = _activeSuggestions.length;
    const num = _suggestionTotal - remaining + 1;

    // Show inline diff in the document
    _showInlineDiff(sugg.find, sugg.replace);

    const textarea = document.getElementById('doc-editor-textarea');

    // Scroll to the change text
    if (textarea) {
      const text = textarea.value;
      const idx = text.indexOf(sugg.find);
      if (idx >= 0) {
        const lineNum = text.substring(0, idx).split('\n').length - 1;
        const lineH = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
        const target = Math.max(0, lineNum * lineH - (textarea.clientHeight / 3));
        textarea.scrollTop = target;
      }
    }

    // Position card next to the highlighted text
    function _positionCard(card) {
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
        <button class="doc-suggestion-close" title="Close all suggestions">&times;</button>
      </div>
      <div class="doc-suggestion-reason">${_esc(sugg.reason)}</div>
      <div class="doc-suggestion-actions">
        <button class="doc-suggestion-accept">Accept</button>
        <button class="doc-suggestion-dismiss">Skip</button>
        ${remaining > 1 ? '<button class="doc-suggestion-accept-all">Accept All</button>' : ''}
      </div>
    `;

    // Wire buttons
    card.querySelector('.doc-suggestion-close').addEventListener('click', clearAllSuggestions);
    card.querySelector('.doc-suggestion-prev').addEventListener('click', () => {
      const current = _activeSuggestions.shift();
      _activeSuggestions.push(current);
      const prev = _activeSuggestions.pop();
      _activeSuggestions.unshift(prev);
      _suggestionIndex = (_suggestionIndex - 1 + _suggestionTotal) % _suggestionTotal;
      _showCurrentSuggestion();
    });
    card.querySelector('.doc-suggestion-next').addEventListener('click', () => {
      const current = _activeSuggestions.shift();
      _activeSuggestions.push(current);
      _suggestionIndex = (_suggestionIndex + 1) % _suggestionTotal;
      _showCurrentSuggestion();
    });
    card.querySelector('.doc-suggestion-accept').addEventListener('click', () => {
      _applySuggestion(sugg);
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
        for (const s of _activeSuggestions) _applySuggestion(s);
        _activeSuggestions = [];
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
    // Store cleanup refs on the card
    card._cleanup = () => {
      if (textarea) textarea.removeEventListener('scroll', _reposition);
      window.removeEventListener('resize', _reposition);
    };
  }

  /** Show inline diff by modifying the code highlight element directly */
  function _showInlineDiff(findText, replaceText) {
    const codeEl = document.getElementById('doc-editor-code');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!codeEl || !textarea) return;

    const text = textarea.value;
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
    codeEl.dataset.hasDiff = '1';
  }

  /** Clear inline diff — restore normal highlighting */
  function _clearInlineDiff() {
    const codeEl = document.getElementById('doc-editor-code');
    if (codeEl && codeEl.dataset.hasDiff) {
      delete codeEl.dataset.hasDiff;
      syncHighlighting();
    }
  }

  // ---- Diff mode (line-level review) ----

  const DIFF_MODE_THRESHOLD = 3; // min changed lines to trigger diff mode

  /** Line-level LCS diff algorithm */
  function _computeLineDiff(oldText, newText) {
    const oldLines = oldText.split('\n');
    const newLines = newText.split('\n');
    const m = oldLines.length, n = newLines.length;

    // Build LCS table
    const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        dp[i][j] = oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }

    // Backtrack to produce diff entries
    const entries = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
        entries.push({ type: 'equal', line: oldLines[i - 1] });
        i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        entries.push({ type: 'insert', line: newLines[j - 1] });
        j--;
      } else {
        entries.push({ type: 'delete', line: oldLines[i - 1] });
        i--;
      }
    }
    entries.reverse();
    return entries;
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

  /** Enter diff mode — show line-level diff for review */
  function enterDiffMode(oldContent, newContent) {
    if (_diffModeActive) exitDiffMode(true);

    _diffModeActive = true;
    _diffOldContent = oldContent;
    _diffNewContent = newContent;

    const entries = _computeLineDiff(oldContent, newContent);
    _diffChunks = _buildDiffChunks(entries);
    _diffUnresolvedCount = _diffChunks.length;

    if (_diffChunks.length === 0) {
      _diffModeActive = false;
      if (uiModule) uiModule.showToast('No changes');
      return;
    }

    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea) textarea.readOnly = true;
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.classList.add('diff-mode');

    _renderDiffOverlay(entries);
    _renderDiffToolbar();
    _renderDiffGutter();

    // Update header button
    const diffBtn = document.getElementById('doc-diff-toggle-btn');
    if (diffBtn) diffBtn.classList.add('active');
  }

  /** Render the line-level diff into the code highlight element */
  function _renderDiffOverlay(entries) {
    const codeEl = document.getElementById('doc-editor-code');
    const gutter = document.getElementById('doc-line-numbers');
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
        // This is the start of a change block — assign all contiguous non-equal entries to the current chunk
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
    const textarea = document.getElementById('doc-editor-textarea');
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

    const acceptAll = document.createElement('button');
    acceptAll.className = 'diff-toolbar-btn diff-toolbar-btn-accept';
    acceptAll.textContent = 'Accept All';
    acceptAll.addEventListener('click', () => _resolveAllChunks(true));

    const rejectAll = document.createElement('button');
    rejectAll.className = 'diff-toolbar-btn diff-toolbar-btn-reject';
    rejectAll.textContent = 'Reject All';
    rejectAll.addEventListener('click', () => _resolveAllChunks(false));

    toolbar.appendChild(status);
    toolbar.appendChild(acceptAll);
    toolbar.appendChild(rejectAll);

    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.parentNode.insertBefore(toolbar, wrap);
  }

  /** Render per-chunk accept/reject buttons in a gutter overlay */
  function _renderDiffGutter() {
    let gutterEl = document.getElementById('doc-diff-gutter');
    if (gutterEl) gutterEl.remove();

    gutterEl = document.createElement('div');
    gutterEl.id = 'doc-diff-gutter';
    gutterEl.className = 'diff-gutter';

    const codeEl = document.getElementById('doc-editor-code');
    if (!codeEl) return;

    // Insert chunk action buttons directly next to the first changed line of each chunk
    // This way they scroll naturally with the content
    requestAnimationFrame(() => {
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
    });
  }

  /** Update the toolbar status text */
  function _updateDiffStatus(statusEl) {
    const el = statusEl || document.getElementById('diff-toolbar-status');
    if (!el) return;
    const resolved = _diffChunks.length - _diffUnresolvedCount;
    el.textContent = `${resolved} / ${_diffChunks.length} changes resolved`;
  }

  /** Resolve a single chunk */
  function _resolveChunk(chunkId, accept) {
    const chunk = _diffChunks.find(c => c.id === chunkId);
    if (!chunk || chunk.resolved) return;

    chunk.resolved = true;
    chunk.accepted = accept;
    _diffUnresolvedCount--;

    // Fade resolved lines in the overlay
    const codeEl = document.getElementById('doc-editor-code');
    if (codeEl) {
      codeEl.querySelectorAll(`[data-chunk-id="${chunkId}"]`).forEach(el => {
        el.classList.add('diff-chunk-resolved');
      });
    }

    // Remove the gutter buttons for this chunk
    const gutterActions = document.querySelector(`.diff-chunk-actions[data-chunk-id="${chunkId}"]`);
    if (gutterActions) gutterActions.remove();

    _updateDiffStatus();

    // Persist partial progress so refresh doesn't lose individually-resolved chunks
    _applyResolvedChunksToTextarea();
    saveDocument({ silent: true });

    if (_diffUnresolvedCount === 0) {
      setTimeout(() => exitDiffMode(false), 300);
    }
  }

  /** Compute current content from old + resolved chunk decisions; unresolved chunks
   *  default to the original (rejected) until the user decides. Updates textarea. */
  function _applyResolvedChunksToTextarea() {
    const textarea = document.getElementById('doc-editor-textarea');
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
        // Resolved+accepted → use new; resolved+rejected OR unresolved → keep old
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
  function exitDiffMode(discard) {
    if (!_diffModeActive) return;
    _diffModeActive = false;

    const textarea = document.getElementById('doc-editor-textarea');
    const codeEl = document.getElementById('doc-editor-code');
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap) wrap.classList.remove('diff-mode');

    if (discard) {
      // Reject all — restore original content
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

    const diffBtn = document.getElementById('doc-diff-toggle-btn');
    if (diffBtn) diffBtn.classList.remove('active');

    syncHighlighting();
    updateLineNumbers(textarea ? textarea.value : '');
    saveDocument({ silent: true });
  }

  /** Check if diff mode is active */
  function isDiffModeActive() { return _diffModeActive; }

  let _suggestionTotal = 0;
  let _suggestionIndex = 0;

  // Override handleDocSuggestions to track total
  const _origHandleDocSuggestions = handleDocSuggestions;
  // (total is set inside handleDocSuggestions before _showCurrentSuggestion)

  /** Apply a single suggestion edit without removing from queue */
  function _applySuggestion(sugg) {
    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea && sugg.find && textarea.value.includes(sugg.find)) {
      textarea.value = textarea.value.replace(sugg.find, sugg.replace);
      syncHighlighting();
      saveDocument({ silent: true });
    }
  }

  /** Animate transition to next suggestion */
  function _animateNext() {
    _saveSuggestionsToStorage();
    const old = document.getElementById('doc-suggestion-active');
    if (old) {
      if (old._cleanup) old._cleanup();
      old.style.transition = 'opacity 0.15s, transform 0.15s';
      old.style.opacity = '0';
      old.style.transform = 'translateY(-10px)';
      setTimeout(() => {
        old.remove();
        _showCurrentSuggestion();
      }, 150);
    } else {
      _showCurrentSuggestion();
    }
  }

  function _esc(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /** Accept a suggestion — apply the edit */
  function acceptSuggestion(id) {
    const sugg = _activeSuggestions.find(s => s.id === id);
    if (!sugg) return;

    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea && sugg.find && textarea.value.includes(sugg.find)) {
      textarea.value = textarea.value.replace(sugg.find, sugg.replace);
      syncHighlighting();
      saveDocument({ silent: true });
    }

    // Animate card out
    sugg.cardEl.style.transition = 'opacity 0.2s, transform 0.2s';
    sugg.cardEl.style.opacity = '0';
    sugg.cardEl.style.transform = 'translateX(10px)';
    setTimeout(() => sugg.cardEl.remove(), 200);

    _activeSuggestions = _activeSuggestions.filter(s => s.id !== id);
    _clearSuggestionHighlight();

    // Remove container if empty
    if (_activeSuggestions.length === 0) {
      const container = document.getElementById('doc-suggestions-container');
      if (container) container.style.display = 'none';
    }
  }

  /** Dismiss a suggestion — just remove the card */
  function dismissSuggestion(id) {
    const sugg = _activeSuggestions.find(s => s.id === id);
    if (!sugg) return;

    sugg.cardEl.style.transition = 'opacity 0.15s';
    sugg.cardEl.style.opacity = '0';
    setTimeout(() => sugg.cardEl.remove(), 150);

    _activeSuggestions = _activeSuggestions.filter(s => s.id !== id);
    _clearSuggestionHighlight();

    if (_activeSuggestions.length === 0) {
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
    _clearInlineDiff();
    const old = document.getElementById('doc-suggestion-active');
    if (old) { if (old._cleanup) old._cleanup(); old.remove(); }
    const container = document.getElementById('doc-suggestions-container');
    if (container) { container.innerHTML = ''; container.style.display = 'none'; }
    // Restore line numbers
    const ta = document.getElementById('doc-editor-textarea');
    if (ta) updateLineNumbers(ta.value);
  }

  /** Highlight the referenced text in the editor when hovering a suggestion */
  function _highlightSuggestionText(findText) {
    _clearSuggestionHighlight();
    const textarea = document.getElementById('doc-editor-textarea');
    const wrap = document.getElementById('doc-editor-wrap');
    if (!textarea || !wrap) return;

    const text = textarea.value;
    const idx = text.indexOf(findText);
    if (idx === -1) return;

    const style = getComputedStyle(textarea);
    const paddingTop = parseFloat(style.paddingTop) || 10;
    const paddingLeft = parseFloat(style.paddingLeft) || 48;
    const lineHeight = parseFloat(style.lineHeight) || 20;

    let mirror = document.getElementById('doc-selection-mirror');
    if (!mirror) return;

    const beforeStart = text.substring(0, idx);
    const lastNewline = beforeStart.lastIndexOf('\n');
    const startLineBegin = lastNewline + 1;
    mirror.textContent = text.substring(0, startLineBegin);
    const startTop = mirror.scrollHeight - paddingTop;

    const endIdx = idx + findText.length;
    const afterEnd = text.indexOf('\n', endIdx);
    const endLineEnd = afterEnd === -1 ? text.length : afterEnd;
    mirror.textContent = text.substring(0, endLineEnd);
    const endBottom = mirror.scrollHeight - paddingTop;
    mirror.textContent = '';

    const top = paddingTop + startTop - textarea.scrollTop;
    const height = Math.max(endBottom - startTop, lineHeight);

    const highlight = document.createElement('div');
    highlight.className = 'doc-suggestion-highlight';
    highlight.id = 'doc-suggestion-hover-hl';
    highlight.style.top = top + 'px';
    highlight.style.left = paddingLeft + 'px';
    highlight.style.right = '0';
    highlight.style.height = height + 'px';
    wrap.appendChild(highlight);

    // Don't auto-scroll here — caller handles scrolling
  }

  /** Remove hover highlight */
  function _clearSuggestionHighlight() {
    const hl = document.getElementById('doc-suggestion-hover-hl');
    if (hl) hl.remove();
  }

  /** Run the document's code using the in-browser code runner */
  function runDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || !textarea.value.trim()) return;

    const code = textarea.value;
    const langSelect = document.getElementById('doc-language-select');
    const lang = (langSelect ? langSelect.value : '').toLowerCase();

    // Get or create the output panel below the editor
    let outputPanel = document.getElementById('doc-run-output');
    if (!outputPanel) {
      outputPanel = document.createElement('div');
      outputPanel.id = 'doc-run-output';
      outputPanel.className = 'doc-run-output';
      const editorWrap = document.getElementById('doc-editor-wrap');
      if (editorWrap) editorWrap.after(outputPanel);
    }
    outputPanel.style.display = 'block';
    outputPanel.innerHTML = '';

    if (_isRenderLang(lang)) {
      // HTML / SVG / XML — render inline in the sandboxed preview iframe.
      outputPanel.style.display = 'none';
      toggleHtmlPreview();
      return;
    }

    if (!codeRunnerModule) {
      outputPanel.innerHTML = '<pre class="doc-run-error">Code runner not loaded</pre>';
      setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
      return;
    }

    if (lang === 'bash' || lang === 'sh' || lang === 'shell' || lang === 'zsh') {
      codeRunnerModule.runServer(code, outputPanel, 'bash');
      return;
    }

    if (lang === 'python' || lang === 'py') {
      codeRunnerModule.runServer(code, outputPanel, 'python');
      return;
    }

    if (lang === 'javascript' || lang === 'js') {
      codeRunnerModule.runJavaScript(code, outputPanel);
      return;
    }

    outputPanel.innerHTML = '<pre class="doc-run-error">Unsupported language. Supported: bash, python, javascript, html</pre>';
    setTimeout(() => { if (outputPanel) outputPanel.style.display = 'none'; }, 5000);
  }

  /** Copy document content to clipboard */
  async function copyDocument() {
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea || !textarea.value) return;
    if (uiModule && uiModule.copyToClipboard) {
      await uiModule.copyToClipboard(textarea.value);
    } else {
      try {
        await navigator.clipboard.writeText(textarea.value);
      } catch (e) { /* ignore */ }
    }
    if (uiModule) uiModule.showToast('Copied to clipboard');
  }

  /* ---- Per-tab context menu ---- */

  let _docTabMenu = null; // singleton dropdown element

  function _closeDocTabMenu() {
    if (_docTabMenu) { _docTabMenu.style.display = 'none'; }
  }

  function showDocTabMenu(btnEl, docId) {
    // Toggle off if already open for this doc
    if (_docTabMenu && _docTabMenu.style.display === 'block' && _docTabMenu._docId === docId) {
      _closeDocTabMenu();
      return;
    }

    // Capture button position before any DOM changes
    const _menuAnchorRect = btnEl.getBoundingClientRect();

    // Switch to this doc if not already active
    if (docId !== activeDocId) switchToDoc(docId);

    const doc = docs.get(docId);
    if (!doc) return;

    // Create singleton menu container once
    if (!_docTabMenu) {
      _docTabMenu = document.createElement('div');
      _docTabMenu.className = 'doc-tab-dropdown';
      _docTabMenu.style.cssText = 'position:fixed;z-index:1000;min-width:0;width:max-content;padding:4px;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.3);backdrop-filter:blur(12px);font-size:12px;display:none;';
      document.body.appendChild(_docTabMenu);
      // Close on outside click
      document.addEventListener('click', (e) => {
        if (_docTabMenu && !_docTabMenu.contains(e.target) && !e.target.closest('.doc-tab-menu-btn')) {
          _closeDocTabMenu();
        }
      });
      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || !_docTabMenu || _docTabMenu.style.display !== 'block') return;
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation?.();
        _closeDocTabMenu();
      }, true);
    }

    const lang = (doc.language || '').toLowerCase();
    const canRun = _isRenderLang(lang) || ['javascript', 'js', 'python', 'py', 'bash', 'sh', 'shell', 'zsh'].includes(lang);

    let previewIcon = '', previewLabel = '';
    const _mdPreview = document.getElementById('doc-md-preview');
    const _csvPreview = document.getElementById('doc-csv-preview');
    const _htmlPreview = document.getElementById('doc-html-preview');
    const _mdActive = _mdPreview && _mdPreview.style.display !== 'none';
    const _csvActive = _csvPreview && _csvPreview.style.display !== 'none';
    const _htmlActive = _htmlPreview && _htmlPreview.style.display !== 'none';
    if (lang === 'markdown') { previewIcon = 'MD'; previewLabel = _mdActive ? 'Edit' : 'Preview'; }
    else if (lang === 'csv') { previewIcon = '⊞'; previewLabel = _csvActive ? 'Edit' : 'Table View'; }
    else if (_isRenderLang(lang)) { previewIcon = '▶'; previewLabel = _htmlActive ? 'Edit' : 'Run / Preview'; }

    const _di = (svg) => `<span class="dropdown-icon">${svg}</span>`;
    const _saveIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>';
    const _copyIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const _runIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    const _previewIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    const _deleteIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';

    let items = '';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="save">${_di(_saveIco)}<span>Save</span></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="copy">${_di(_copyIco)}<span>Copy</span></div>`;
    if (canRun) {
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="run">${_di(_runIco)}<span>Run</span></div>`;
    }
    if (previewLabel) {
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="preview"><span class="dropdown-icon">${previewIcon}</span><span>${previewLabel}</span></div>`;
    }
    const _downloadIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="download">${_di(_downloadIco)}<span>Download</span></div>`;
    // "Send signed reply" — only if this doc was opened from an email attachment
    if (doc.sourceEmailUid && doc.sourceEmailFolder) {
      const _sendBackIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>';
      items += `<div class="dropdown-item-compact doc-tab-action" data-action="signed-reply">${_di(_sendBackIco)}<span>Send signed reply</span></div>`;
    }
    const _closeIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    items += `<div class="dropdown-item-compact doc-tab-action" data-action="close">${_di(_closeIco)}<span>Close</span></div>`;
    items += `<div class="dropdown-divider"></div>`;
    items += `<div class="dropdown-item-compact doc-tab-action doc-tab-action-delete" data-action="delete">${_di(_deleteIco)}<span>Delete</span></div>`;

    _docTabMenu.innerHTML = items;
    _docTabMenu.style.display = 'block';
    _docTabMenu._docId = docId;

    // Position: anchor to the tab bar bottom, aligned to button horizontally
    const rect = _menuAnchorRect;
    const tabBar = document.getElementById('doc-tab-bar');
    const barBottom = tabBar ? tabBar.getBoundingClientRect().bottom : rect.bottom;
    _docTabMenu.style.position = 'fixed';
    _docTabMenu.style.zIndex = '1000';
    _docTabMenu.style.left = rect.left + 'px';
    _docTabMenu.style.top = (barBottom + 2) + 'px';

    // Clamp to viewport edges
    requestAnimationFrame(() => {
      const menuRect = _docTabMenu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth - 8) {
        _docTabMenu.style.left = (window.innerWidth - menuRect.width - 8) + 'px';
      }
      if (menuRect.left < 8) {
        _docTabMenu.style.left = '8px';
      }
      if (menuRect.bottom > window.innerHeight - 8) {
        _docTabMenu.style.top = (barBottom - menuRect.height - 4) + 'px';
      }
    });

    // Wire action clicks
    _docTabMenu.querySelectorAll('.doc-tab-action').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        const action = item.dataset.action;
        _closeDocTabMenu();
        switch (action) {
          case 'save': saveDocument(); break;
          case 'copy': copyDocument(); break;
          case 'run': runDocument(); break;
          case 'preview':
            if (lang === 'markdown') toggleMarkdownPreview();
            else if (lang === 'csv') toggleCsvPreview();
            else if (_isRenderLang(lang)) toggleHtmlPreview();
            break;
          case 'download': {
            const btn = document.getElementById('doc-fontsize-btn') || document.getElementById('doc-language-select');
            showExportMenu(null, btn?.getBoundingClientRect());
            break;
          }
          case 'signed-reply': _sendSignedReply(docId); break;
          case 'close': closeTab(docId); break;
          case 'delete': deleteActiveDocument(); break;
        }
      });
    });
  }

  /**
   * "Send signed reply" — flatten the current PDF (form fields + signature
   * stamps + freeform annotations), drop it into the compose-uploads dir,
   * then either:
   *   1. add the attachment to an existing open email draft for the same
   *      source thread (so multiple signed docs accumulate into one reply), or
   *   2. create a fresh email-language draft document pre-filled with To /
   *      Subject / In-Reply-To / References and the first attachment.
   * Switches the doc panel to that draft so the user can review + send.
   */
  async function _sendSignedReply(docId) {
    const doc = docs.get(docId);
    if (!doc || !doc.sourceEmailUid) return;
    if (uiModule) uiModule.showToast('Preparing signed reply…');
    let result;
    try {
      const res = await fetch(`${API_BASE}/api/document/${encodeURIComponent(docId)}/prepare-signed-reply`, {
        method: 'POST',
        credentials: 'same-origin',
      });
      result = await res.json().catch(() => ({}));
      if (!res.ok || !result.ok) {
        const msg = (result && result.error) || `HTTP ${res.status}`;
        if (uiModule) uiModule.showError(`Couldn't prepare signed reply: ${msg}`);
        return;
      }
    } catch (e) {
      console.error('prepare-signed-reply failed:', e);
      if (uiModule) uiModule.showError("Couldn't prepare signed reply");
      return;
    }

    const att = result.attachment;
    const reply = result.reply || {};
    const mid = reply.source_message_id || doc.sourceEmailMessageId || '';

    // 1) Already have a draft tab open for this source thread? Append.
    for (const [, d] of docs) {
      if (d.language === 'email' && d._draftForMessageId === mid && mid) {
        d._composeAtts = (d._composeAtts || []).concat([att]);
        await loadDocument(d.id);
        _renderComposeAttachments();
        if (uiModule) uiModule.showToast(`Added "${att.filename}" to the reply draft`);
        return;
      }
    }

    // 2) Otherwise create a fresh email draft.
    const headerLines = [
      `To: ${reply.to || ''}`,
      `Subject: ${reply.subject || ''}`,
      reply.in_reply_to ? `In-Reply-To: ${reply.in_reply_to}` : null,
      reply.references ? `References: ${reply.references}` : null,
      reply.source_uid ? `X-Source-UID: ${reply.source_uid}` : null,
      reply.source_folder ? `X-Source-Folder: ${reply.source_folder}` : null,
    ].filter(Boolean);
    const content = headerLines.join('\n') + '\n---\n\nHi' + (reply.to_name ? ' ' + reply.to_name.split(/\s+/)[0] : '') + ',\n\nPlease find the signed copy attached.\n\nBest,\n';

    let draftId = null;
    try {
      // Use the source PDF's session if available; else fall back to current.
      let sessionId = doc.sessionId
        || _lastSessionId
        || (sessionModule && sessionModule.getCurrentSessionId());
      if (!sessionId) {
        try { sessionId = await _autoCreateSession(); } catch (_) {}
      }
      const cRes = await fetch(`${API_BASE}/api/document`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          session_id: sessionId,
          title: reply.subject || 'Signed reply',
          language: 'email',
          content,
        }),
      });
      const created = await cRes.json();
      draftId = created && (created.id || created.doc_id);
      if (!draftId) throw new Error('No draft id returned');
    } catch (e) {
      console.error('Failed to create draft doc:', e);
      if (uiModule) uiModule.showError("Couldn't create reply draft");
      return;
    }

    // Tag the draft (in-memory only) with the thread message-id so future
    // signed PDFs from the same email get appended to this same draft.
    addDocToTabs({
      id: draftId,
      title: reply.subject || 'Signed reply',
      language: 'email',
      current_content: content,
      version_count: 1,
    }, doc.sessionId);
    const draft = docs.get(draftId);
    if (draft) {
      draft._composeAtts = [att];
      draft._draftForMessageId = mid;
      if (reply.account_id) draft._draftAccountId = reply.account_id;
    }

    await loadDocument(draftId);
    _renderComposeAttachments();
    if (uiModule) uiModule.showToast(`Reply draft ready — "${att.filename}" attached`);
  }

  /** Save manual edits */
  export async function saveDocument({ silent = false } = {}) {
    if (!activeDocId) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;

    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: textarea.value }),
      });
      const doc = await res.json();
      const badge = document.getElementById('doc-version-badge');
      if (badge) { const _v = doc.version_count || 1; badge.textContent = `v${_v}`; badge.style.display = _v > 1 ? '' : 'none'; }
      // Update map
      if (docs.has(activeDocId)) {
        docs.get(activeDocId).version = doc.version_count || 1;
        docs.get(activeDocId).content = textarea.value;
      }
      _syncDocIndicator();
      if (!silent && uiModule) uiModule.showToast('Document saved');
    } catch (e) {
      console.error('Failed to save document:', e);
      if (!silent && uiModule) uiModule.showError('Failed to save document');
    }
  }

  /** Export/download the active document */
  let _docxReady = null;
  function ensureDocx() {
    if (_docxReady) return _docxReady;
    if (window.docx) return (_docxReady = Promise.resolve());
    _docxReady = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/lib/docx.umd.min.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error('Failed to load DOCX library'));
      document.head.appendChild(s);
    });
    return _docxReady;
  }

  let _html2pdfReady = null;
  function ensureHtml2Pdf() {
    if (_html2pdfReady) return _html2pdfReady;
    if (window.html2pdf) return (_html2pdfReady = Promise.resolve());
    _html2pdfReady = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/static/lib/html2pdf.bundle.min.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error('Failed to load PDF library'));
      document.head.appendChild(s);
    });
    return _html2pdfReady;
  }

  function _getExportBaseName() {
    const doc = docs.get(activeDocId);
    const title = (doc && doc.title) || 'document';
    const safeName = title.replace(/[^a-zA-Z0-9_\-. ]/g, '_').trim() || 'document';
    const ver = doc && doc.version ? `_v${doc.version}` : '';
    return safeName + ver;
  }

  function exportDocument() {
    if (!activeDocId) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const doc = docs.get(activeDocId);
    const title = (doc && doc.title) || 'document';
    const lang = document.getElementById('doc-language-select')?.value || '';
    const extMap = {
      javascript: '.js', python: '.py', html: '.html', css: '.css',
      markdown: '.md', json: '.json', yaml: '.yml', bash: '.sh',
      sql: '.sql', rust: '.rs', go: '.go', java: '.java', c: '.c', cpp: '.cpp', csharp: '.cs',
      typescript: '.ts', ruby: '.rb', php: '.php', text: '.txt',
      xml: '.xml', toml: '.toml', ini: '.ini', csv: '.csv',
    };
    const ext = extMap[lang] || '.txt';
    const safeName = title.replace(/[^a-zA-Z0-9_\-. ]/g, '_').trim() || 'document';
    const ver = doc && doc.version ? `_v${doc.version}` : '';
    const mime = lang === 'csv' ? 'text/csv' : lang === 'json' ? 'application/json' : 'text/plain';
    const blob = new Blob([textarea.value], { type: mime });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = safeName + ver + ext;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // "Import from device" — open a file picker, upload, and immediately open
  // the resulting doc in THIS panel (vs. dumping it in the library and
  // making the user click through). Mirrors the library's extension logic
  // for text/code; routes PDFs through the dedicated import-pdf endpoint
  // that handles AcroForm fields. Spreadsheets fall back to the library
  // flow which already knows how to split sheets.
  function _importFromDevice() {
    const EXT_TO_LANG = {
      '.py':'python','.js':'javascript','.ts':'typescript','.html':'html','.htm':'html',
      '.css':'css','.md':'markdown','.json':'json','.yml':'yaml','.yaml':'yaml',
      '.sh':'bash','.bash':'bash','.sql':'sql','.rs':'rust','.go':'go',
      '.java':'java','.c':'c','.cpp':'cpp','.h':'c','.hpp':'cpp',
      '.rb':'ruby','.php':'php','.xml':'xml','.toml':'toml','.ini':'ini',
      '.txt':'','.log':'','.csv':'csv','.tsv':'csv','.jsx':'javascript','.tsx':'typescript',
    };
    const fi = document.createElement('input');
    fi.type = 'file';
    fi.style.display = 'none';
    fi.addEventListener('change', async () => {
      const file = fi.files?.[0];
      if (!file) return;
      const name = file.name;
      const dotIdx = name.lastIndexOf('.');
      const ext = dotIdx >= 0 ? name.slice(dotIdx).toLowerCase() : '';
      const baseTitle = dotIdx > 0 ? name.slice(0, dotIdx) : name;
      const isSpreadsheet = ['.xlsx','.xls','.ods'].includes(ext);
      const isPdf = ext === '.pdf';
      // Spreadsheets need the library's per-sheet split — defer to it.
      if (isSpreadsheet) {
        openLibrary();
        requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById('doclib-import-file-btn')?.click()));
        return;
      }
      try {
        let docId = null;
        if (isPdf) {
          const fd = new FormData();
          fd.append('file', file);
          const sid = (sessionModule && sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId()) || _lastSessionId || '';
          if (sid) fd.append('session_id', sid);
          const r = await fetch(`${API_BASE}/api/documents/import-pdf`, { method: 'POST', body: fd, credentials: 'same-origin' });
          if (!r.ok) throw new Error('PDF import failed');
          const j = await r.json();
          docId = j.doc_id || j.id;
        } else {
          const content = await new Promise((res, rej) => {
            const reader = new FileReader();
            reader.onload = () => res(reader.result || '');
            reader.onerror = () => rej(reader.error);
            reader.readAsText(file);
          });
          const lang = EXT_TO_LANG[ext] !== undefined ? EXT_TO_LANG[ext] : null;
          const sid = (sessionModule && sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId()) || _lastSessionId || '';
          const body = { title: baseTitle, language: lang, content };
          if (sid) body.session_id = sid;
          const r = await fetch(`${API_BASE}/api/document`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body),
          });
          if (!r.ok) throw new Error('Import failed');
          const j = await r.json();
          docId = j.id || j.doc_id;
        }
        if (docId) {
          // Fetch the full doc so addDocToTabs has the proper content +
          // language fields (it's used downstream by switchToDoc).
          try {
            const dr = await fetch(`${API_BASE}/api/document/${docId}`, { credentials: 'same-origin' });
            const full = dr.ok ? await dr.json() : { id: docId, title: baseTitle };
            const sid = (sessionModule && sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId()) || _lastSessionId || '';
            addDocToTabs(full, full.session_id || sid);
            switchToDoc(full.id || docId);
          } catch (_) {
            // Fallback — at least try to switch (may fail silently if not loaded).
            addDocToTabs({ id: docId, title: baseTitle }, _lastSessionId || '');
            switchToDoc(docId);
          }
        }
      } catch (err) {
        if (uiModule && uiModule.showError) uiModule.showError('Import failed: ' + (err.message || err));
      } finally {
        fi.value = '';
        fi.remove();
      }
    });
    document.body.appendChild(fi);
    fi.click();
  }

  function showExportMenu(e, anchorRect) {
    if (e) e.stopPropagation();
    // Remove existing menu if any
    const existing = document.getElementById('doc-export-menu');
    if (existing) { existing.remove(); return; }

    // Position from provided rect, clicked element, or fallback to language select
    const rect = anchorRect
      || (e && e.target && e.target.closest('button')?.getBoundingClientRect())
      || document.getElementById('doc-language-select')?.getBoundingClientRect();
    if (!rect) return;

    const lang = document.getElementById('doc-language-select')?.value || '';
    const extMap = {
      javascript: '.js', python: '.py', html: '.html', css: '.css',
      markdown: '.md', json: '.json', yaml: '.yml', bash: '.sh',
      sql: '.sql', rust: '.rs', go: '.go', java: '.java', c: '.c', cpp: '.cpp', csharp: '.cs',
      typescript: '.ts', ruby: '.rb', php: '.php', text: '.txt',
      xml: '.xml', toml: '.toml', ini: '.ini', csv: '.csv',
    };
    const ext = extMap[lang] || '.txt';

    const menu = document.createElement('div');
    menu.id = 'doc-export-menu';
    menu.className = 'doc-overflow-menu open';
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 2) + 'px';
    menu.style.right = (window.innerWidth - rect.right) + 'px';
    menu.style.left = 'auto';
    menu.style.zIndex = '9999';

    const langLabel = lang ? lang.toUpperCase() : 'TXT';
    // Form-backed markdown doc → primary export is the filled PDF, not the
    // markdown source. Promote it to the top of the menu.
    const liveContent = document.getElementById('doc-editor-textarea')?.value
      || docs.get(activeDocId)?.content || '';
    const isForm = _isFormBackedDoc(liveContent);
    const options = [];
    // Import lives at the top of the same dropdown — it's a sibling action
    // ("bring something IN" vs "send something OUT"), and the footer was
    // getting too cramped for dedicated icons.
    options.push({ label: 'Import from library', fn: () => openLibrary() });
    options.push({ label: 'Import from device', fn: () => _importFromDevice(), _divider: true });
    if (isForm) options.push({ label: 'Filled PDF (.pdf)', fn: _downloadFilledPdf });
    options.push(
      { label: 'Export Markdown', fn: exportDocument },
      { label: 'Print as PDF', fn: exportAsPdf },
      { label: 'Export as Word', fn: exportAsDocx },
    );

    options.forEach(opt => {
      const item = document.createElement('button');
      item.className = 'doc-overflow-item';
      item.textContent = opt.label;
      item.addEventListener('click', (ev) => { ev.stopPropagation(); menu.remove(); opt.fn(); });
      menu.appendChild(item);
      if (opt._divider) {
        const sep = document.createElement('div');
        sep.className = 'doc-overflow-divider';
        sep.style.cssText = 'height:1px;margin:3px 6px;background:color-mix(in srgb,var(--border) 60%,transparent);';
        menu.appendChild(sep);
      }
    });

    document.body.appendChild(menu);
    // Flip above the anchor when there's no room below — the Export button now
    // lives in the bottom footer, so the menu would otherwise drop off-screen.
    const mh = menu.offsetHeight;
    if (rect.bottom + mh > window.innerHeight - 8) {
      menu.style.top = 'auto';
      menu.style.bottom = (window.innerHeight - rect.top + 2) + 'px';
    }
    const close = (ev) => {
      if (ev && ev.type === 'keydown') {
        if (ev.key !== 'Escape') return;
        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation?.();
      } else if (ev && menu.contains(ev.target)) {
        return;
      }
      menu.remove();
      document.removeEventListener('click', close);
      document.removeEventListener('keydown', close, true);
    };
    setTimeout(() => document.addEventListener('click', close), 100);
    document.addEventListener('keydown', close, true);
  }

  function exportAsHtml() {
    if (!activeDocId) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    const lang = document.getElementById('doc-language-select')?.value || '';
    const text = textarea.value || '';
    let body;
    if (lang === 'markdown' && markdownModule?.mdToHtml) {
      body = markdownModule.mdToHtml(text, { shortcodes: false }); // export: keep :shortcodes: literal
    } else {
      body = '<pre style="white-space:pre-wrap;font-size:12px;font-family:monospace;">' +
        text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
    }
    const title = docs.get(activeDocId)?.title || 'document';
    const html = `<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>${title.replace(/</g,'&lt;')}</title></head><body style="max-width:800px;margin:40px auto;font-family:sans-serif;line-height:1.6;padding:0 20px;">\n${body}\n</body></html>`;
    const blob = new Blob([html], { type: 'text/html' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = _getExportBaseName() + '.html';
    a.click();
    URL.revokeObjectURL(a.href);
    if (uiModule) uiModule.showToast('Exported as HTML');
  }

  async function exportAsPdf() {
    if (!activeDocId) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    try {
      await ensureHtml2Pdf();
    } catch (e) {
      if (uiModule) uiModule.showError('Failed to load PDF library');
      return;
    }
    const lang = document.getElementById('doc-language-select')?.value || '';
    const text = textarea.value || '';
    // Render content as HTML for PDF
    let html;
    if (lang === 'markdown' && markdownModule?.mdToHtml) {
      html = markdownModule.mdToHtml(text, { shortcodes: false }); // export: keep :shortcodes: literal
    } else {
      html = '<pre style="white-space:pre-wrap;font-size:11px;font-family:monospace;color:#000;background:#fff;">' +
        text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
    }
    const container = document.createElement('div');
    container.style.cssText = 'padding:20px;font-family:sans-serif;font-size:12px;color:#000;background:#fff;line-height:1.6;';
    container.innerHTML = html;
    const baseName = _getExportBaseName();
    window.html2pdf().set({
      margin: 10,
      filename: baseName + '.pdf',
      image: { type: 'jpeg', quality: 0.95 },
      html2canvas: { scale: 2 },
      jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
    }).from(container).save();
    if (uiModule) uiModule.showToast('Exporting PDF...');
  }

  async function exportAsDocx() {
    if (!activeDocId) return;
    const textarea = document.getElementById('doc-editor-textarea');
    if (!textarea) return;
    try {
      await ensureDocx();
    } catch (e) {
      if (uiModule) uiModule.showError('Failed to load DOCX library');
      return;
    }
    const text = textarea.value || '';
    const { Document, Packer, Paragraph, TextRun, HeadingLevel } = window.docx;
    // Parse text into paragraphs, handle markdown headings
    const paragraphs = text.split('\n').map(line => {
      const h1 = line.match(/^# (.+)/);
      const h2 = line.match(/^## (.+)/);
      const h3 = line.match(/^### (.+)/);
      if (h1) return new Paragraph({ text: h1[1], heading: HeadingLevel.HEADING_1 });
      if (h2) return new Paragraph({ text: h2[1], heading: HeadingLevel.HEADING_2 });
      if (h3) return new Paragraph({ text: h3[1], heading: HeadingLevel.HEADING_3 });
      // Handle bold/italic
      const runs = [];
      const parts = line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/);
      for (const part of parts) {
        if (part.startsWith('**') && part.endsWith('**')) {
          runs.push(new TextRun({ text: part.slice(2, -2), bold: true }));
        } else if (part.startsWith('*') && part.endsWith('*')) {
          runs.push(new TextRun({ text: part.slice(1, -1), italics: true }));
        } else {
          runs.push(new TextRun(part));
        }
      }
      return new Paragraph({ children: runs });
    });

    const doc = new Document({
      sections: [{ children: paragraphs }],
    });
    const blob = await Packer.toBlob(doc);
    const baseName = _getExportBaseName();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = baseName + '.docx';
    a.click();
    URL.revokeObjectURL(a.href);
    if (uiModule) uiModule.showToast('Exported as DOCX');
  }

  /** Delete the active document */
  async function deleteActiveDocument() {
    if (!activeDocId) return;
    const doc = docs.get(activeDocId);
    const name = doc ? doc.title : 'this document';
    const ok = uiModule && uiModule.styledConfirm
      ? await uiModule.styledConfirm(`Delete "${name}"?`, { confirmText: 'Delete', danger: true })
      : confirm(`Delete "${name}"?`);
    if (!ok) return;
    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      // Remove tab
      const tab = document.querySelector(`.doc-tab[data-doc-id="${activeDocId}"]`);
      if (tab) tab.remove();
      docs.delete(activeDocId);
      // Switch to another doc or close panel
      const remaining = Array.from(docs.keys());
      if (remaining.length > 0) {
        switchToDoc(remaining[0]);
      } else {
        activeDocId = null;
        closePanel();
      }
      if (uiModule) uiModule.showToast('Document deleted');
    } catch (e) {
      console.error('Failed to delete document:', e);
      if (uiModule) uiModule.showError('Failed to delete document');
    }
  }

  /** Toggle fullscreen on doc editor pane */
  function toggleFullscreen() {
    const pane = document.getElementById('doc-editor-pane');
    const container = document.getElementById('chat-container');
    if (!pane) return;
    // Note: the divider stays in the DOM during fullscreen so its chevron can
    // act as the exit-fullscreen affordance (the CSS rule
    // `body:has(.doc-editor-pane.doc-fullscreen) .doc-divider-collapse` slides
    // it into a forced-inside position). Hiding the divider here would hide
    // the chevron with it.

    // Hide the tab bar during the layout shift so any in-flight smooth
    // scroll / reflow doesn't visibly "fly" the active tab across the
    // pane as it expands. Restored after the layout settles.
    const tabBar = document.getElementById('doc-tab-bar');
    if (tabBar) {
      tabBar.style.visibility = 'hidden';
      clearTimeout(tabBar._fsHideTimer);
      tabBar._fsHideTimer = setTimeout(() => {
        tabBar.style.visibility = '';
      }, 240);
    }

    if (pane.classList.contains('doc-fullscreen')) {
      pane.classList.remove('doc-fullscreen');
      if (container) container.style.display = '';
    } else {
      pane.classList.add('doc-fullscreen');
      if (container) container.style.display = 'none';
    }
    // Re-check md toolbar overflow after layout change
    const mdToolbar = document.getElementById('doc-md-toolbar');
    if (mdToolbar?._syncOverflow) requestAnimationFrame(mdToolbar._syncOverflow);
  }

  /** Toggle markdown preview */
  function _setMarkdownPreviewActive(active, { remember = true } = {}) {
    const preview = document.getElementById('doc-md-preview');
    const wrap = document.getElementById('doc-editor-wrap');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!preview || !wrap || !textarea) return;

    if (active) {
      const md = textarea.value || '';
      if (markdownModule && markdownModule.mdToHtml) {
        preview.innerHTML = markdownModule.mdToHtml(md, { shortcodes: false }); // doc preview: keep :shortcodes: literal
      } else {
        preview.innerHTML = md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g, '<br>');
      }
      if (window.hljs) {
        preview.querySelectorAll('pre code').forEach(b => window.hljs.highlightElement(b));
      }
      if (markdownModule && markdownModule.renderMermaid) {
        markdownModule.renderMermaid(preview);
      }
      preview.style.display = '';
      wrap.style.display = 'none';
    } else {
      preview.style.display = 'none';
      preview.innerHTML = '';
      const isEmailDoc = docs.get(activeDocId)?.language === 'email';
      const richEmailBody = document.getElementById('doc-email-richbody');
      if (!(isEmailDoc && richEmailBody && richEmailBody.style.display !== 'none')) {
        wrap.style.display = '';
      }
    }
    if (remember && activeDocId && docs.has(activeDocId)) {
      docs.get(activeDocId)._markdownPreviewActive = !!active;
    }
    _syncHeaderActions();
  }

  function toggleMarkdownPreview() {
    const preview = document.getElementById('doc-md-preview');
    _setMarkdownPreviewActive(!(preview && preview.style.display !== 'none'));
  }

  /** Parse CSV text into a 2D array (handles quoted fields) */
  function parseCSV(text) {
    const rows = [];
    let row = [];
    let field = '';
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inQuotes) {
        if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
        else if (ch === '"') { inQuotes = false; }
        else { field += ch; }
      } else {
        if (ch === '"') { inQuotes = true; }
        else if (ch === ',') { row.push(field); field = ''; }
        else if (ch === '\n' || (ch === '\r' && text[i + 1] === '\n')) {
          if (ch === '\r') i++;
          row.push(field); field = '';
          if (row.some(c => c.trim())) rows.push(row);
          row = [];
        } else { field += ch; }
      }
    }
    row.push(field);
    if (row.some(c => c.trim())) rows.push(row);
    return rows;
  }

  /** Escape a CSV field (quote if it contains comma, quote, or newline) */
  function csvEscapeField(val) {
    if (val.includes(',') || val.includes('"') || val.includes('\n')) {
      return '"' + val.replace(/"/g, '""') + '"';
    }
    return val;
  }

  /** Rebuild CSV text from the live table DOM */
  function syncTableToTextarea(preview, textarea) {
    const table = preview.querySelector('.csv-table');
    if (!table) return;
    const lines = [];
    // Header
    const ths = table.querySelectorAll('thead th');
    if (ths.length) lines.push([...ths].map(th => csvEscapeField(th.textContent)).join(','));
    // Body
    table.querySelectorAll('tbody tr').forEach(tr => {
      const cells = [...tr.querySelectorAll('td')].map(td => csvEscapeField(td.textContent));
      lines.push(cells.join(','));
    });
    textarea.value = lines.join('\n') + '\n';
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  /** Toggle CSV table preview */
  function toggleCsvPreview() {
    const preview = document.getElementById('doc-csv-preview');
    const wrap = document.getElementById('doc-editor-wrap');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!preview || !wrap || !textarea) return;

    if (preview.style.display === 'none') {
      const rows = parseCSV(textarea.value || '');
      if (rows.length === 0) {
        // Re-route the "no data" message to the shared run-output block so
        // every doc type surfaces errors/empty-state in the same place
        // (instead of stamping it inside the table view itself).
        let outputPanel = document.getElementById('doc-run-output');
        if (!outputPanel) {
          outputPanel = document.createElement('div');
          outputPanel.id = 'doc-run-output';
          outputPanel.className = 'doc-run-output';
          const editorWrap = document.getElementById('doc-editor-wrap');
          if (editorWrap) editorWrap.after(outputPanel);
        }
        outputPanel.style.display = 'block';
        outputPanel.innerHTML = '<pre class="doc-run-error">No data — CSV is empty or unparseable.</pre>';
        return;
      } else {
        const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const colCount = Math.max(...rows.map(r => r.length));
        let html = '<div class="csv-table-wrap"><table class="csv-table"><thead><tr>';
        for (let j = 0; j < colCount; j++) {
          html += `<th contenteditable="true">${esc(rows[0][j] || '')}</th>`;
        }
        html += '</tr></thead><tbody>';
        for (let i = 1; i < rows.length; i++) {
          html += '<tr>';
          for (let j = 0; j < colCount; j++) {
            html += `<td contenteditable="true">${esc(rows[i][j] || '')}</td>`;
          }
          html += '</tr>';
        }
        html += '</tbody></table>';
        html += '</div>';
        preview.innerHTML = html;

        // Sync edits back to textarea
        const table = preview.querySelector('.csv-table');
        if (table) {
          table.addEventListener('input', () => syncTableToTextarea(preview, textarea));
          // Prevent Enter from creating <br> inside cells
          table.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              // Move to next row, same column
              const cell = e.target.closest('td,th');
              if (!cell) return;
              const colIdx = [...cell.parentElement.children].indexOf(cell);
              const nextRow = cell.parentElement.nextElementSibling;
              if (nextRow && nextRow.children[colIdx]) {
                nextRow.children[colIdx].focus();
              }
            } else if (e.key === 'Tab') {
              e.preventDefault();
              const cell = e.target.closest('td,th');
              if (!cell) return;
              const next = e.shiftKey ? cell.previousElementSibling : cell.nextElementSibling;
              if (next) next.focus();
            }
          });
        }

        // Add row button
        const addBtn = preview.querySelector('.csv-add-row-btn');
        if (addBtn && table) {
          addBtn.addEventListener('click', () => {
            const tbody = table.querySelector('tbody');
            const tr = document.createElement('tr');
            for (let j = 0; j < colCount; j++) {
              const td = document.createElement('td');
              td.contentEditable = 'true';
              tr.appendChild(td);
            }
            tbody.appendChild(tr);
            tr.children[0].focus();
            syncTableToTextarea(preview, textarea);
          });
        }
      }
      preview.style.display = '';
      wrap.style.display = 'none';
    } else {
      preview.style.display = 'none';
      wrap.style.display = '';
    }
    // Update the segmented Code/Run toggle's active class so the icon
    // highlights match the new state — without this, opening a CSV that
    // auto-shows the table view left the Edit (code) side wrongly marked
    // active and the user had to flip the toggle to resync.
    _syncHeaderActions();
  }

  /** Toggle inline HTML preview (iframe) */
  function toggleHtmlPreview() {
    const iframe = document.getElementById('doc-html-preview');
    const wrap = document.getElementById('doc-editor-wrap');
    const textarea = document.getElementById('doc-editor-textarea');
    if (!iframe || !wrap || !textarea) return;

    if (!_htmlPreviewActive) {
      // Show preview — hide markdown preview if active
      const mdPreview = document.getElementById('doc-md-preview');
      if (mdPreview) mdPreview.style.display = 'none';
      const code = textarea.value || '';
      iframe.srcdoc = code;
      iframe.style.display = '';
      wrap.style.display = 'none';
      _htmlPreviewActive = true;
      renderTabs();
    } else {
      exitHtmlPreview();
    }
  }

  /** Exit HTML preview back to code view */
  function exitHtmlPreview() {
    const iframe = document.getElementById('doc-html-preview');
    const wrap = document.getElementById('doc-editor-wrap');
    if (!_htmlPreviewActive) return;
    _htmlPreviewActive = false;
    if (iframe) { iframe.style.display = 'none'; iframe.srcdoc = ''; }
    if (wrap) wrap.style.display = '';
    renderTabs();
  }

  // ---- Streaming animation engine ----

  /**
   * Simple diff: find the first and last differing positions between two strings.
   * Returns { prefixLen, oldMid, newMid } where:
   *   oldText = prefix + oldMid + suffix
   *   newText = prefix + newMid + suffix
   */
  function simpleDiff(oldText, newText) {
    let i = 0;
    const minLen = Math.min(oldText.length, newText.length);
    while (i < minLen && oldText[i] === newText[i]) i++;
    const prefixLen = i;

    let oj = oldText.length;
    let nj = newText.length;
    while (oj > prefixLen && nj > prefixLen && oldText[oj - 1] === newText[nj - 1]) {
      oj--; nj--;
    }

    return {
      prefixLen,
      oldMid: oldText.slice(prefixLen, oj),
      newMid: newText.slice(prefixLen, nj),
    };
  }

  /**
   * Animate the transition from oldText to newText in the editor textarea.
   * First deletes the old differing section char-by-char, then types the new one.
   */
  /**
   * Compute a line-level diff between two texts.
   * Returns array of { type: 'same'|'del'|'add', text: string }
   */
  function lineDiff(oldText, newText) {
    const oldLines = oldText.split('\n');
    const newLines = newText.split('\n');

    // Simple LCS-based diff (Myers-like, but O(n*m) for clarity)
    const m = oldLines.length, n = newLines.length;
    // For very large diffs, skip detailed diff
    if (m * n > 500000) return null;

    const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
    for (let i = m - 1; i >= 0; i--) {
      for (let j = n - 1; j >= 0; j--) {
        if (oldLines[i] === newLines[j]) {
          dp[i][j] = dp[i + 1][j + 1] + 1;
        } else {
          dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
      }
    }

    const result = [];
    let i = 0, j = 0;
    while (i < m || j < n) {
      if (i < m && j < n && oldLines[i] === newLines[j]) {
        result.push({ type: 'same', text: oldLines[i] });
        i++; j++;
      } else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) {
        result.push({ type: 'add', text: newLines[j] });
        j++;
      } else {
        result.push({ type: 'del', text: oldLines[i] });
        i++;
      }
    }
    return result;
  }

  async function animateDocChange(oldText, newText) {
    if (_animationCancel) _animationCancel();

    const textarea = document.getElementById('doc-editor-textarea');
    const wrap = document.getElementById('doc-editor-wrap');
    if (!textarea) return false;
    if (oldText === newText) return true;

    const diff = lineDiff(oldText, newText);
    if (!diff) return false; // too large for diff

    // Count changes
    const delCount = diff.filter(d => d.type === 'del').length;
    const addCount = diff.filter(d => d.type === 'add').length;
    if (delCount + addCount === 0) return true;

    _animationInProgress = true;
    let cancelled = false;
    _animationCancel = () => { cancelled = true; };

    textarea.readOnly = true;
    if (wrap) wrap.classList.add('animating');

    try {
      // Build diff overlay HTML
      const overlay = document.createElement('div');
      overlay.className = 'doc-diff-overlay';

      // Stats bar
      const stats = document.createElement('div');
      stats.className = 'doc-diff-stats';
      stats.innerHTML = `<span class="diff-stat-del">\u2212${delCount}</span> <span class="diff-stat-add">+${addCount}</span>`;
      overlay.appendChild(stats);

      const content = document.createElement('div');
      content.className = 'doc-diff-content';

      // Render diff lines — show context around changes
      let inContext = false;
      let skipped = 0;
      diff.forEach((line, idx) => {
        if (line.type === 'same') {
          // Show 2 lines of context around changes
          const nearChange = diff.slice(Math.max(0, idx - 2), idx + 3).some(d => d.type !== 'same');
          if (nearChange) {
            if (skipped > 0) {
              const sep = document.createElement('div');
              sep.className = 'doc-diff-sep';
              sep.textContent = `\u22EF ${skipped} unchanged`;
              content.appendChild(sep);
              skipped = 0;
            }
            const row = document.createElement('div');
            row.className = 'doc-diff-line same';
            row.textContent = line.text || '\u00A0';
            content.appendChild(row);
          } else {
            skipped++;
          }
        } else {
          if (skipped > 0) {
            const sep = document.createElement('div');
            sep.className = 'doc-diff-sep';
            sep.textContent = `\u22EF ${skipped} unchanged`;
            content.appendChild(sep);
            skipped = 0;
          }
          const row = document.createElement('div');
          row.className = 'doc-diff-line ' + line.type;
          row.textContent = (line.type === 'del' ? '\u2212 ' : '+ ') + (line.text || '\u00A0');
          content.appendChild(row);
        }
      });

      overlay.appendChild(content);

      // Insert overlay over the textarea
      const editorArea = textarea.parentElement;
      if (editorArea) editorArea.appendChild(overlay);

      // Show diff for a moment, then fade to final content
      overlay.offsetHeight; // force reflow
      overlay.classList.add('visible');

      const DIFF_DISPLAY_MS = 2500;
      await new Promise(r => setTimeout(r, cancelled ? 0 : DIFF_DISPLAY_MS));

      if (!cancelled) {
        overlay.classList.remove('visible');
        overlay.classList.add('fading');
        textarea.value = newText;
        syncHighlighting();
        await new Promise(r => setTimeout(r, 400));
      }

      overlay.remove();

      if (!cancelled) {
        textarea.value = newText;
        syncHighlighting();
      }

      return !cancelled;
    } finally {
      textarea.readOnly = false;
      _animationInProgress = false;
      _animationCancel = null;
      if (wrap) wrap.classList.remove('animating');
    }
  }

  // --- Streaming helpers: open panel & feed content as AI generates ---
  let _streamDocId = null;

  /** Sync the markdown toolbar + header actions for a streaming doc, so the
   *  Edit/Preview toggle and formatting tools appear without a manual refresh. */
  function _syncStreamDocChrome(doc) {
    if (!doc) return;
    const lang = (doc.language || 'markdown').toLowerCase();
    const isMd = lang === 'markdown';
    const isPdf = _isFormBackedDoc(doc.content || '');
    // Show the toolbar for any doc type that has a view toggle of its own
    // (markdown edit↔preview, or code↔run for renderable code types). The
    // `data-mode` attribute lets CSS hide markdown-only buttons (bold,
    // italic, headings, etc.) when we're in a code-mode doc.
    const renderable = ['svg', 'html', 'css', 'csv', 'python', 'javascript', 'typescript',
                        'json', 'xml', 'bash', 'sh', 'yaml', 'toml', 'sql'];
    const isCodeRenderable = renderable.includes(lang);
    const mt = document.getElementById('doc-md-toolbar');
    if (mt) {
      const showToolbar = isMd || isPdf || isCodeRenderable;
      mt.style.display = showToolbar ? '' : 'none';
      mt.dataset.mode = isMd ? 'md' : (isPdf ? 'pdf' : (isCodeRenderable ? 'code' : ''));
      if (showToolbar && mt._syncOverflow) requestAnimationFrame(mt._syncOverflow);
    }
    _syncHeaderActions();
  }

  /** Open the document panel immediately for a doc being streamed in */
  export function streamDocOpen(title, language) {
    // Discard any pending AI-edit diff before this stream changes the active
    // document. When the AI streams a NEW document while an unapproved diff is
    // open on the current one, streamDocOpen reassigns activeDocId below; if the
    // stale diff isn't cleared first, a later exitDiffMode applies the old doc's
    // content to the new one and overwrites it (issue #2467). activeDocId still
    // points at the previously-active doc here, so exitDiffMode(true) restores
    // and saves THAT doc — same guard handleDocUpdate/switchToDoc use.
    if (_diffModeActive) exitDiffMode(true);
    // If already streaming a doc, reuse it (don't create a second temp doc)
    if (_streamDocId && docs.has(_streamDocId)) {
      const existing = docs.get(_streamDocId);
      if (title) existing.title = title;
      if (language) existing.language = language;
      // Update UI fields
      const titleInput = document.getElementById('doc-title-input');
      const langSelect = document.getElementById('doc-language-select');
      if (title && titleInput) titleInput.value = title;
      if (langSelect) langSelect.value = existing.language || 'markdown';
      if (language === 'email') {
        _showEmailFields(existing);
      }
      _syncStreamDocChrome(existing);
      renderTabs();
      return;
    }

    const sessionId = sessionModule?.getCurrentSessionId() || '';
    // Reuse existing doc with same title in this session, or create a temp one
    let docId = null;
    if (title) {
      for (const [existingId, existingDoc] of docs) {
        if (existingDoc.title === title && existingDoc.sessionId === sessionId) {
          docId = existingId;
          break;
        }
      }
    }
    if (!docId) {
      docId = '_streaming_' + Date.now();
      docs.set(docId, {
        id: docId,
        title: title || '',
        language: language || '',
        content: '',
        version: 1,
        sessionId,
      });
    }
    _streamDocId = docId;
    activeDocId = docId;
    _syncDocIndicator();

    if (!isOpen) openPanel();

    // Force doc button visible
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) {
      toggleBtn.style.display = '';
      toggleBtn.classList.remove('toolbar-collapsed');
      toggleBtn.classList.add('has-docs');
    }
    const docInd2 = document.getElementById('doc-indicator-btn');
    if (docInd2) docInd2.classList.add('visible');

    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');
    const badge = document.getElementById('doc-version-badge');
    if (titleInput) titleInput.value = title || '';
    if (langSelect) langSelect.value = language || 'markdown';
    if (badge) badge.textContent = 'v1';

    const textarea = document.getElementById('doc-editor-textarea');
    if (textarea) {
      textarea.disabled = false;
      textarea.placeholder = 'Document content...';
      textarea.value = '';
    }
    // Show streaming indicator
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = '';

    // Show email fields immediately when streaming an email doc so the user
    // doesn't have to refresh for the editor to flip into email mode.
    if (language === 'email') {
      const streamDoc = docs.get(_streamDocId);
      if (streamDoc) _showEmailFields(streamDoc);
    } else {
      _hideEmailFields();
    }

    syncHighlighting();
    _syncStreamDocChrome(docs.get(_streamDocId));
    renderTabs();
  }

  /** Simulate streaming effect for doc edits */
  let _editAnimFrame = null;
  function _animateDocEdit(textarea, newContent) {
    if (_editAnimFrame) cancelAnimationFrame(_editAnimFrame);
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = '';
    const codeEl = document.getElementById('doc-editor-code');
    let cursor = document.getElementById('doc-stream-cursor');
    if (!cursor) {
      cursor = document.createElement('span');
      cursor.id = 'doc-stream-cursor';
      cursor.className = 'doc-stream-cursor';
      cursor.textContent = '\u258F';
    }

    const oldContent = textarea.value;

    // Find common prefix and suffix to isolate the changed region
    let prefixLen = 0;
    while (prefixLen < oldContent.length && prefixLen < newContent.length &&
           oldContent[prefixLen] === newContent[prefixLen]) prefixLen++;
    let suffixLen = 0;
    while (suffixLen < (oldContent.length - prefixLen) &&
           suffixLen < (newContent.length - prefixLen) &&
           oldContent[oldContent.length - 1 - suffixLen] === newContent[newContent.length - 1 - suffixLen]) suffixLen++;

    const deletedText = oldContent.slice(prefixLen, oldContent.length - suffixLen);
    const insertedText = newContent.slice(prefixLen, newContent.length - suffixLen);
    const suffix = oldContent.slice(oldContent.length - suffixLen);

    // Phase 1: delete characters one by one, then Phase 2: insert
    const deleteChunk = Math.max(2, Math.ceil(deletedText.length / 30));
    const insertChunk = Math.max(2, Math.ceil(insertedText.length / 30));
    let deletePos = deletedText.length;
    let insertPos = 0;
    let phase = deletedText.length > 0 ? 'delete' : 'insert';

    // Scroll to the edit region
    const linesBefore = oldContent.slice(0, prefixLen).split('\n').length;
    const lineH = parseFloat(getComputedStyle(textarea).lineHeight) || 20;
    textarea.scrollTop = Math.max(0, (linesBefore - 3) * lineH);

    function tick() {
      if (phase === 'delete') {
        deletePos = Math.max(0, deletePos - deleteChunk);
        const current = oldContent.slice(0, prefixLen) + deletedText.slice(0, deletePos) + suffix;
        textarea.value = current;
        if (codeEl) codeEl.textContent = current + '\n';
        if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
        updateLineNumbers(current);
        if (deletePos > 0) {
          _editAnimFrame = requestAnimationFrame(tick);
        } else {
          phase = 'insert';
          _editAnimFrame = requestAnimationFrame(tick);
        }
      } else {
        insertPos = Math.min(insertPos + insertChunk, insertedText.length);
        const current = newContent.slice(0, prefixLen + insertPos) + suffix;
        textarea.value = current;
        if (codeEl) codeEl.textContent = current + '\n';
        if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
        updateLineNumbers(current);
        if (insertPos < insertedText.length) {
          _editAnimFrame = requestAnimationFrame(tick);
        } else {
          // Done — set final content
          textarea.value = newContent;
          _editAnimFrame = null;
          if (indicator) indicator.style.display = 'none';
          if (cursor) cursor.remove();
          syncHighlighting();
        }
      }
    }
    _editAnimFrame = requestAnimationFrame(tick);
  }

  /** Append streaming content to the currently-streaming doc */
  let _streamHlDebounce = null;
  export function streamDocDelta(content) {
    if (!_streamDocId) return;
    const doc = docs.get(_streamDocId);
    if (doc) doc.content = content;

    if (_streamDocId === activeDocId) {
      if ((doc?.language || '').toLowerCase() === 'email') {
        _showEmailFields(doc);
        return;
      }
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) {
        textarea.value = content;
        // Auto-scroll to bottom as content streams in
        textarea.scrollTop = textarea.scrollHeight;
      }
      // Update text and line numbers immediately, debounce expensive highlighting
      const codeEl = document.getElementById('doc-editor-code');
      if (codeEl) codeEl.textContent = content + '\n';
      updateLineNumbers(content);
      // Show blinking cursor at end of content
      let cursor = document.getElementById('doc-stream-cursor');
      if (!cursor) {
        cursor = document.createElement('span');
        cursor.id = 'doc-stream-cursor';
        cursor.className = 'doc-stream-cursor';
        cursor.textContent = '\u258F';
      }
      if (codeEl && codeEl.parentElement) codeEl.parentElement.appendChild(cursor);
      clearTimeout(_streamHlDebounce);
      _streamHlDebounce = setTimeout(syncHighlighting, 150);
    }
  }

  /** Finalize streaming — called when doc_update arrives with the real ID.
   *  Returns the old _streamDocId so handleDocUpdate can migrate temp→real. */
  export function streamDocFinalize() {
    const oldId = _streamDocId;
    _streamDocId = null;
    // Hide streaming indicator + cursor
    const indicator = document.getElementById('doc-stream-indicator');
    if (indicator) indicator.style.display = 'none';
    const cursor = document.getElementById('doc-stream-cursor');
    if (cursor) cursor.remove();
    // Final highlighting pass + auto-detect language
    clearTimeout(_streamHlDebounce);
    syncHighlighting();
    attemptAutoDetect();
    return oldId;
  }

  function _isMarkdownPreviewVisible() {
    const preview = document.getElementById('doc-md-preview');
    return !!(preview && preview.style.display !== 'none');
  }

  function _refreshMarkdownPreviewIfVisible(docId, content) {
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

  /** Handle SSE doc_update event from AI */
  export function handleDocUpdate(data) {
    const streamingId = streamDocFinalize();
    // Discard any pending AI-edit diff before this update changes the active
    // document. The diff state (_diffModeActive/_diffOldContent/...) is a
    // module-global singleton bound to whatever doc was active when the diff
    // opened; if we switch documents without clearing it, a later tab switch or
    // Accept/Reject-All flushes the stale diff's content into the now-active
    // doc and silently overwrites it (issue #2467). activeDocId still points at
    // the previously-active doc here, so exitDiffMode(true) restores and saves
    // THAT doc before we reassign activeDocId below — mirroring switchToDoc()
    // and enterDiffMode().
    if (_diffModeActive) exitDiffMode(true);
    let docId = data.doc_id;
    const newContent = data.content || '';

    // Migrate streaming temp doc to real ID
    if (streamingId && streamingId.startsWith('_streaming_') && docs.has(streamingId)) {
      const tempDoc = docs.get(streamingId);
      docs.delete(streamingId);
      tempDoc.id = docId;
      tempDoc.version = data.version || 1;
      if (data.title) tempDoc.title = data.title;
      if (data.language) tempDoc.language = data.language;
      tempDoc.content = newContent;
      docs.set(docId, tempDoc);
      // Fix activeDocId reference
      if (activeDocId === streamingId) activeDocId = docId;
    }

    // Deduplicate: if a new doc has same title as existing doc in this session, update it instead
    if (!docs.has(docId)) {
      const curSession = sessionModule?.getCurrentSessionId() || '';
      let reuseId = null;

      // First: match by title
      if (data.title) {
        for (const [existingId, existingDoc] of docs) {
          if (existingDoc.title === data.title && existingDoc.sessionId === curSession) {
            reuseId = existingId;
            break;
          }
        }
      }

      // Second: if no title match, reuse an empty untitled doc in this session
      if (!reuseId) {
        for (const [existingId, existingDoc] of docs) {
          if (existingDoc.sessionId === curSession &&
              (!existingDoc.title || existingDoc.title === 'Untitled') &&
              (!existingDoc.content || existingDoc.content.trim() === '')) {
            reuseId = existingId;
            break;
          }
        }
      }

      if (reuseId) docId = reuseId;
    }

    // Capture old content before updating the map
    const textarea = document.getElementById('doc-editor-textarea');
    const oldContent = (docId === activeDocId && textarea) ? textarea.value : '';
    const isExistingDoc = docs.has(docId);

    // Add or update in docs map
    if (isExistingDoc) {
      const doc = docs.get(docId);
      doc.content = newContent;
      doc.version = data.version || doc.version;
      if (data.title) doc.title = data.title;
      if (data.language) doc.language = data.language;
    } else {
      docs.set(docId, {
        id: docId,
        title: data.title || '',
        language: data.language || '',
        content: newContent,
        version: data.version || 1,
        sessionId: sessionModule?.getCurrentSessionId() || '',
      });
    }

    _syncDocIndicator();

    // Auto-title from content if still "Untitled" and AI didn't provide a title
    if (!data.title) autoTitleFromContent(newContent, docId);

    if (!isOpen) openPanel();

    // Force doc button visible (overrides appearance settings & toolbar collapse)
    const toggleBtn = document.getElementById('overflow-doc-btn');
    if (toggleBtn) {
      toggleBtn.style.display = '';
      toggleBtn.classList.remove('toolbar-collapsed');
      toggleBtn.classList.add('has-docs');
    }
    const docInd = document.getElementById('doc-indicator-btn');
    if (docInd) docInd.classList.add('visible');

    // Switch to this doc's tab
    activeDocId = docId;

    const badge = document.getElementById('doc-version-badge');
    const titleInput = document.getElementById('doc-title-input');
    const langSelect = document.getElementById('doc-language-select');

    // Re-enable editor if it was in empty state
    if (textarea) {
      textarea.disabled = false;
      textarea.placeholder = 'Document content...';
    }
    if (badge) badge.textContent = `v${data.version || 1}`;
    if (data.title && titleInput) titleInput.value = data.title;
    // Set language from data, or fall back to what the doc already has (e.g. from streaming)
    const docLang = data.language || (docs.has(docId) && docs.get(docId).language) || '';
    if (docLang && langSelect) langSelect.value = docLang;
    if (!docLang) attemptAutoDetect();
    const isEmailUpdate = (docLang || '').toLowerCase() === 'email';
    const markdownPreviewWasVisible = _isMarkdownPreviewVisible();

    // Animate content update for edits; apply directly for creates/streaming
    const isEdit = !isEmailUpdate && isExistingDoc && oldContent && oldContent !== newContent && !streamingId;
    if (isEdit && textarea) {
      // Count changed lines to decide between animation and diff mode
      const oldLines = oldContent.split('\n');
      const newLines = newContent.split('\n');
      let changedLines = 0;
      const maxLen = Math.max(oldLines.length, newLines.length);
      for (let li = 0; li < maxLen; li++) {
        if (oldLines[li] !== newLines[li]) changedLines++;
      }
      if (changedLines >= DIFF_MODE_THRESHOLD) {
        if (markdownPreviewWasVisible) _setMarkdownPreviewActive(false, { remember: false });
        enterDiffMode(oldContent, newContent);
      } else if (markdownPreviewWasVisible && _refreshMarkdownPreviewIfVisible(docId, newContent)) {
        // Preview is the visible surface, so refresh it instead of animating a hidden editor.
      } else {
        _animateDocEdit(textarea, newContent);
      }
    } else {
      if (isEmailUpdate) {
        const updatedDocForEmail = docs.get(docId);
        if (updatedDocForEmail) {
          _setMarkdownPreviewActive(false, { remember: false });
          _showEmailFields(updatedDocForEmail);
        }
      } else {
        if (textarea) textarea.value = newContent;
        syncHighlighting();
        _refreshMarkdownPreviewIfVisible(docId, newContent);
      }
    }

    // Flash the editor wrap to indicate content was updated
    const wrap = document.getElementById('doc-editor-wrap');
    if (wrap && !isEdit) {
      wrap.classList.remove('doc-updated-flash');
      void wrap.offsetWidth; // force reflow
      wrap.classList.add('doc-updated-flash');
      wrap.addEventListener('animationend', () => wrap.classList.remove('doc-updated-flash'), { once: true });
    }

    // Auto-detect language for docs with no language set
    const updatedDoc = docs.get(docId);
    if (isEmailUpdate && updatedDoc) {
      updatedDoc.language = 'email';
      if (langSelect) langSelect.value = 'email';
      _showEmailFields(updatedDoc);
    }
    if (updatedDoc && !updatedDoc.userSetLanguage && !updatedDoc.language) {
      setTimeout(attemptAutoDetect, 100);
    }

    // Show/hide format-specific buttons and auto-toggle previews
    const finalLang = docLang || (updatedDoc && updatedDoc.language) || '';
    const mdToolbar = document.getElementById('doc-md-toolbar');
    // Toolbar shown for every doc type — items inside self-gate on language.
    if (mdToolbar) mdToolbar.style.display = '';
    // Auto-show table view for CSV after streaming
    if (finalLang === 'csv') {
      requestAnimationFrame(() => {
        const csvPreview = document.getElementById('doc-csv-preview');
        if (csvPreview && csvPreview.style.display === 'none') toggleCsvPreview();
      });
    }

    renderTabs();

    // Refresh the header buttons (Run/Preview ▶, edit toggles) for the active
    // doc after ANY update — otherwise an AI-created html/svg/code doc wouldn't
    // show its ▶ Run button until the page was refreshed.
    if (docId === activeDocId) {
      _syncHeaderActions();
      // Form-backed (PDF) docs: re-fetch the rendered preview if it's showing.
      if (_isFormBackedDoc(newContent)) {
        const explicit = _pdfViewState.get(docId);
        if (explicit !== false) _refreshPdfPreviewIframe();
      }
    }
  }

  /** Toggle version history panel */
  let _versionClickOutside = null;
  let _versionSavedContent = null;  // stash current content for preview/revert
  async function toggleVersionHistory() {
    const panel = document.getElementById('doc-version-panel');
    if (!panel || !activeDocId) return;

    if (panel.classList.contains('hidden')) {
      // Stash current content so we can restore on close
      const ta = document.getElementById('doc-editor-textarea');
      _versionSavedContent = ta ? ta.value : null;

      // Position next to sidebar on desktop
      const sidebar = document.getElementById('sidebar');
      const isMobile = window.innerWidth <= 768;
      if (!isMobile && sidebar) {
        const sidebarRight = sidebar.classList.contains('right-side');
        const collapsed = document.body.classList.contains('sidebar-collapsed');
        if (sidebarRight || collapsed) {
          panel.style.left = '0';
          panel.style.right = 'auto';
        } else {
          panel.style.left = sidebar.offsetWidth + 'px';
          panel.style.right = 'auto';
        }
      } else if (isMobile) {
        // Clear any stale inline positioning from a prior desktop open so the
        // mobile bottom-sheet (CSS) isn't pushed off-screen.
        panel.style.left = '';
        panel.style.right = '';
        panel.style.top = '';
      }

      // Move panel to body so it's not clipped by doc pane overflow
      if (panel.parentElement !== document.body) {
        document.body.appendChild(panel);
      }

      panel.classList.remove('hidden');
      await loadVersionHistory();
      // Close on click outside
      setTimeout(() => {
        _versionClickOutside = (e) => {
          if (!panel.contains(e.target) && e.target.id !== 'doc-version-badge') {
            _closeVersionPanel();
          }
        };
        document.addEventListener('click', _versionClickOutside, true);
      }, 0);
    } else {
      _closeVersionPanel();
    }
  }

  function _closeVersionPanel() {
    const panel = document.getElementById('doc-version-panel');
    if (panel) panel.classList.add('hidden');
    // Restore to latest (stashed) content
    if (_versionSavedContent !== null) {
      const ta = document.getElementById('doc-editor-textarea');
      if (ta) ta.value = _versionSavedContent;
      syncHighlighting();
      _versionSavedContent = null;
    }
    if (_versionClickOutside) {
      document.removeEventListener('click', _versionClickOutside, true);
      _versionClickOutside = null;
    }
  }

  /** Build a short diff summary between two strings */
  function _buildDiffSummary(oldText, newText) {
    if (!oldText && !newText) return '';
    const oldLines = (oldText || '').split('\n');
    const newLines = (newText || '').split('\n');
    const added = [], removed = [];
    // Simple line diff — collect changed lines
    const maxCheck = Math.max(oldLines.length, newLines.length);
    for (let i = 0; i < maxCheck; i++) {
      const ol = oldLines[i], nl = newLines[i];
      if (ol === nl) continue;
      if (ol !== undefined && (nl === undefined || ol !== nl)) removed.push(ol.trim());
      if (nl !== undefined && (ol === undefined || ol !== nl)) added.push(nl.trim());
    }
    // Show up to 3 changes
    const parts = [];
    for (const line of removed.slice(0, 2)) {
      if (line) parts.push(`<span class="diff-del">${_escHtml(line.slice(0, 60))}</span>`);
    }
    for (const line of added.slice(0, 2)) {
      if (line) parts.push(`<span class="diff-add">${_escHtml(line.slice(0, 60))}</span>`);
    }
    const extra = (added.length + removed.length) - 4;
    if (extra > 0) parts.push(`<span>+${extra} more changes</span>`);
    return parts.join('<br>');
  }
  function _escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  /** Load version history list */
  async function loadVersionHistory() {
    if (!activeDocId) return;
    const list = document.getElementById('doc-version-list');
    if (!list) return;

    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}/versions`);
      const versions = await res.json();

      // Build diff summaries between consecutive versions
      const diffs = [];
      for (let i = 0; i < versions.length; i++) {
        if (i < versions.length - 1) {
          diffs.push(_buildDiffSummary(versions[i + 1].content, versions[i].content));
        } else {
          diffs.push('');
        }
      }

      list.innerHTML = versions.map((v, i) => `
        <div class="doc-version-item" data-version="${v.version_number}">
          <div class="doc-version-info">
            <span class="doc-version-num">v${v.version_number}</span>
            ${i === 0 ? '<span class="doc-version-latest">latest</span>' : `<span class="doc-version-source">${v.source}</span><span class="doc-version-time">${v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>`}
          </div>
          ${v.summary ? `<div class="doc-version-summary">${v.summary}</div>` : ''}
          ${diffs[i] ? `<div class="doc-version-diff">${diffs[i]}</div>` : ''}
          ${i > 0 ? `<button class="doc-version-restore" data-version="${v.version_number}">Restore</button>` : ''}
        </div>
      `).join('');

      // Wire restore buttons
      list.querySelectorAll('.doc-version-restore').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          restoreVersion(parseInt(btn.dataset.version));
        });
      });

      // Wire click to preview version + active state
      list.querySelectorAll('.doc-version-item').forEach(item => {
        item.addEventListener('click', (e) => {
          if (e.target.classList.contains('doc-version-restore')) return;
          // Toggle active state
          list.querySelectorAll('.doc-version-item.active').forEach(el => el.classList.remove('active'));
          item.classList.add('active');
          previewVersion(parseInt(item.dataset.version));
        });
      });
    } catch (e) {
      list.innerHTML = '<div style="padding:8px;opacity:0.5;">Failed to load versions</div>';
    }
  }

  /** Preview a specific version in the editor (without saving) */
  async function previewVersion(num) {
    if (!activeDocId) return;
    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}/version/${num}`);
      const ver = await res.json();
      const textarea = document.getElementById('doc-editor-textarea');
      if (textarea) textarea.value = ver.content || '';
      syncHighlighting();
    } catch (e) {
      console.error('Failed to preview version:', e);
    }
  }

  /** Restore an old version (creates new version) */
  async function restoreVersion(num) {
    if (!activeDocId) return;
    try {
      const res = await fetch(`${API_BASE}/api/document/${activeDocId}/restore/${num}`, {
        method: 'POST',
      });
      const doc = await res.json();
      populateEditor(doc);
      // Clear stash — restored content IS the new latest
      _versionSavedContent = null;
      // Update map
      if (docs.has(activeDocId)) {
        const d = docs.get(activeDocId);
        d.content = doc.current_content || '';
        d.version = doc.version_count || 1;
      }
      await loadVersionHistory();
      if (uiModule) uiModule.showToast(`Restored to v${num}`);
    } catch (e) {
      console.error('Failed to restore version:', e);
      if (uiModule) uiModule.showError('Failed to restore version');
    }
  }

  /** Update document title via PATCH */
  async function updateTitle(overrideDocId, overrideTitle) {
    const docId = overrideDocId || activeDocId;
    if (!docId) return;
    const title = overrideTitle || document.getElementById('doc-title-input')?.value;
    if (!title) return;
    try {
      await fetch(`${API_BASE}/api/document/${docId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (docs.has(docId)) {
        docs.get(docId).title = title;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update title:', e);
    }
  }

  /** Auto-detect title from content if still "Untitled" */
  function autoTitleFromContent(content, docId) {
    const id = docId || activeDocId;
    if (!id) return;
    const doc = docs.get(id);
    if (!doc || (doc.title && doc.title !== '' && doc.title !== 'Untitled')) return;

    const text = (content || '').trimStart();
    if (!text) return;

    let title = null;

    // Markdown header: # Title
    const mdMatch = text.match(/^#{1,3}\s+(.+)/m);
    if (mdMatch) {
      title = mdMatch[1].trim();
    }

    // HTML heading: <h1>Title</h1>
    if (!title) {
      const htmlMatch = text.match(/<h[1-3][^>]*>([^<]+)<\/h[1-3]>/i);
      if (htmlMatch) title = htmlMatch[1].trim();
    }

    // First non-empty line as fallback (only if short enough to be a title)
    if (!title) {
      const firstLine = text.split('\n').find(l => l.trim().length > 0);
      if (firstLine) {
        const cleaned = firstLine.trim();
        if (cleaned.length <= 60 && cleaned.length >= 2) {
          title = cleaned;
        }
      }
    }

    if (!title) return;

    // Clean up: strip trailing punctuation like : or ...
    title = title.replace(/[:#*`]+$/g, '').trim();
    if (title.length > 50) title = title.slice(0, 48) + '...';
    if (!title) return;

    updateTitle(id, title);
    const titleInput = document.getElementById('doc-title-input');
    if (titleInput && id === activeDocId) titleInput.value = title;
  }

  /** Update document language via PATCH */
  async function updateLanguage() {
    if (!activeDocId) return;
    const select = document.getElementById('doc-language-select');
    if (!select) return;
    try {
      await fetch(`${API_BASE}/api/document/${activeDocId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: select.value }),
      });
      if (docs.has(activeDocId)) {
        docs.get(activeDocId).language = select.value;
        renderTabs();
      }
    } catch (e) {
      console.error('Failed to update language:', e);
    }
  }

  /** Clear all tab state (e.g. on session switch) */
  export function clearAll() {
    docs.clear();
    activeDocId = null;
    _lastSessionId = '';
    if (isOpen) closePanel();
    _syncDocIndicator();
  }

  export function isPanelOpen() {
    return isOpen;
  }

  export function getCurrentDocId() {
    return activeDocId;
  }

  /** Find an open email tab by source UID + folder. Returns docId or null. */
  export function findEmailDocId(uid, folder) {
    if (uid == null) return null;
    const wantUid = String(uid);
    const wantFolder = (folder || '').trim();
    for (const [id, d] of docs) {
      if (d.language !== 'email') continue;
      const fields = _parseEmailHeader(d.content || '');
      if (fields.sourceUid && String(fields.sourceUid) === wantUid &&
          (!wantFolder || (fields.sourceFolder || '').trim() === wantFolder)) {
        return id;
      }
    }
    return null;
  }



const documentModule = {
  init,
  openPanel,
  closePanel,
  swapSide,
  createDocument,
  newDocument,
  loadDocument,
  injectFreshDoc,
  ensurePaneMounted: _ensureDocPaneMounted,
  loadSessionDocs,
  ensureDocPanel,
  saveDocument,
  handleDocUpdate,
  handleDocSuggestions,
  streamDocOpen,
  streamDocDelta,
  streamDocFinalize,
  isPanelOpen,
  enterDiffMode,
  exitDiffMode,
  isDiffModeActive,
  getCurrentDocId,
  findEmailDocId,
  getSelectionContext,
  clearSelection,
  clearAll,
  openLibrary,
  closeLibrary,
  isLibraryOpen,
};

export default documentModule;
window.documentModule = documentModule;

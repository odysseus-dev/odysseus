/** Panel mounting, keyboard handlers, minimization and note navigation. */
export function createPanel({
  wireToolbar,
  wireSelectionActions,
  _wireNotesWindow,
  _restoreNotesSidebarDock,
  _bringNotesToFront,
  disposeUI,
  lifecycle,
  Modals,
  NOTES_FIRST_OPEN_HINT_KEY,
  REMINDER_DISMISSED_AT_KEY,
  _clearViewedReminderGlows,
  _closeMobileFullscreenEdit,
  _commitOpenInPlaceEditor,
  _copyNote,
  _enterSelectMode,
  _exitSelectMode,
  _fetchNotes,
  _flushPendingHighlights,
  _isNotesMobileMode,
  _popAndRunUndo,
  _renderLoadingSkeleton,
  _renderNotes,
  _startReminderLoop,
  app,
  bindTourHintDismiss,
  dismissOrRemove,
  persistence,
  state,
  workflows
}) {
  function _forceCloseNotesPanel() {
    app.setOpen(false);
    app.setEditingId(null);
    try { _commitOpenInPlaceEditor(); } catch {}
    try { _closeMobileFullscreenEdit({ save: true }); } catch {}
    try { _clearViewedReminderGlows(); } catch {}
    if (_notesKeydownHandler) {
      document.removeEventListener('keydown', _notesKeydownHandler);
      _notesKeydownHandler = null;
    }
    if (_notesSelectEscHandler) {
      document.removeEventListener('keydown', _notesSelectEscHandler, true);
      _notesSelectEscHandler = null;
    }
    disposeOwnedPortals();
    disposeUI();
    document.body.classList.remove('notes-view', 'notes-mobile-mode', 'notes-drag-mode');
    document.getElementById('tool-notes-btn')?.classList.remove('active');
    try { Modals.unregister('notes-panel'); } catch {}
    try { document.getElementById('notes-pane')?.remove(); } catch {}
    try { document.getElementById('notes-pane-backdrop')?.remove(); } catch {}
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch {}
  }

  function _showNotesFirstOpenHint(pane) {
    if (!pane || typeof persistence === 'undefined') return;
    try {
      if (persistence.getItem(NOTES_FIRST_OPEN_HINT_KEY)) return;
      persistence.setItem(NOTES_FIRST_OPEN_HINT_KEY, '1');
    } catch {
      return;
    }

    dismissOrRemove(document.getElementById('notes-first-open-hint'));
    const hint = document.createElement('div');
    hint.id = 'notes-first-open-hint';
    hint.className = 'tour-hint';
    hint.innerHTML = `
    <div class="tour-hint-text"><b>Notes</b> is your basic todo list, and also where reminders are managed.</div>
    <button type="button" class="tour-hint-dismiss">OK</button>
  `;
    document.body.appendChild(hint);

    const place = () => {
      const r = pane.getBoundingClientRect();
      const hw = hint.offsetWidth || 260;
      hint.style.top = Math.max(12, r.top + 58) + 'px';
      hint.style.left = Math.min(window.innerWidth - hw - 12, Math.max(12, r.left + 18)) + 'px';
    };
    requestAnimationFrame(() => {
      place();
      hint.classList.add('tour-hint-in');
    });
    lifecycle.listen(hint, window, 'resize', place);
    bindTourHintDismiss(hint, {
      timeout: 6500,
      fade: 180,
      onDismiss: () => window.removeEventListener('resize', place),
    });
  }

  function _ensureNotesChipRegistered() {
    if (Modals.isRegistered('notes-panel')) return;
    Modals.register('notes-panel', {
      railBtnId: 'rail-notes',
      sidebarBtnId: 'tool-notes-btn',
      restoreFn: () => { openPanel(); },
      closeFn: () => { _forceCloseNotesPanel(); },
    });
  }

  function openPanel() {
    if (state.open) {
      _bringNotesToFront();
      return;
    }
    // Notes and the document editor occupy the same primary workspace. Preserve
    // an open document as a dock chip instead of allowing the two panes to stack
    // into a broken layout (or closing the user's document outright).
    try {
      if (window.documentModule?.isPanelOpen?.()) {
        window.documentModule.closePanel('down');
      }
    } catch (_) {}
    app.setOpen(true);
    app.setEditingId(null);
    app.setSearchQuery('');
    _clearViewedReminderGlows();
    app.setFiredDotDismissedAt(Date.now());
    try { persistence.setItem(REMINDER_DISMISSED_AT_KEY, String(state.firedDotDismissedAt)); } catch {}

    const container = document.getElementById('chat-container');
    if (!container) return;

    document.body.classList.add('notes-view');

    // On mobile the notes panel takes the whole screen : auto-close the
    // sidebar so the panel isn't cropped underneath it.
    if (window.innerWidth <= 768) {
      const sb = document.getElementById('sidebar');
      if (sb) sb.classList.add('hidden');
      document.body.classList.add('sidebar-collapsed');
    }
    // Mobile mode: tiles become read-only previews (no inline checkbox /
    // edit / archive / etc.), tap opens a fullscreen edit overlay,
    // long-press enters drag-to-reorder mode. See _bindCardEvents +
    // .notes-mobile-mode CSS rules.
    if (_isNotesMobileMode()) document.body.classList.add('notes-mobile-mode');

    // Toggle button state
    const btn = document.getElementById('tool-notes-btn');
    if (btn) btn.classList.add('active');

    // Create panel
    const pane = document.createElement('div');
    pane.id = 'notes-pane';
    pane.className = 'notes-pane';
    pane.innerHTML = `
    <div class="notes-mobile-grabber" id="notes-mobile-grabber" aria-hidden="true"></div>
    <div class="notes-pane-header">
      <h4 class="notes-pane-title"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2.5px;margin-right:6px"><path d="M5 3h10l4 4v14H5z"/><path d="M15 3v5h5"/><path d="M8 17.5 15.5 10l2.5 2.5L10.5 20H8z"/></svg>Notes</h4>
      <span style="flex:1"></span>
      <button id="notes-archive-toggle" class="doc-action-icon-btn notes-header-text-btn" title="View archive" style="opacity:0.8;gap:5px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg>
        <span class="notes-header-btn-label">Archive</span>
      </button>
      <button id="notes-view-toggle" class="doc-action-icon-btn notes-header-text-btn" title="Toggle view" style="opacity:0.8;gap:5px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
        <span class="notes-header-btn-label">Toggle</span>
      </button>
      <button id="notes-minimize-btn" class="modal-minimize-btn" title="Minimize" aria-label="Minimize notes" style="position:relative;left:2px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="18" x2="18" y2="18"/></svg></button>
    </div>
    <div class="notes-search-bar">
      <input type="text" id="notes-search" class="memory-search-input" placeholder="Search notes…" autocomplete="off" />
      <button id="notes-select-btn" class="notes-select-trigger" type="button">Select</button>
    </div>
    <div id="notes-bulk-bar" class="memory-bulk-bar hidden">
      <label class="memory-bulk-check-all"><input type="checkbox" id="notes-select-all" /> All</label>
      <span id="notes-selected-count">0 Selected</span>
      <span style="flex:1"></span>
      <button id="notes-send-agent" class="memory-toolbar-btn" disabled><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>Send to Agent</button>
      <button id="notes-bulk-archive" class="memory-toolbar-btn" disabled>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg>Archive
      </button>
      <button id="notes-bulk-delete" class="memory-toolbar-btn danger" disabled>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:4px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>Delete
      </button>
      <button id="notes-bulk-cancel" class="memory-toolbar-btn active" title="Cancel selection" aria-label="Cancel selection">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="notes-pane-body"></div>
  `;

    // On mobile open as a full-screen bottom sheet (slide up), not the desktop
    // side panel. Set inline so it wins over the base .notes-pane rule regardless
    // of cascade specifics (the CSS @media override wasn't reliably applying,
    // which left it as a side panel squeezing the chat).
    if (window.innerWidth <= 768) {
      pane.style.position = 'fixed';
      pane.style.inset = 'var(--sheet-top) 0 0';
      pane.style.width = '100%';
      pane.style.maxWidth = '100%';
      pane.style.zIndex = '170';
      pane.style.borderRadius = '14px 14px 0 0';
      pane.style.animation = 'sheet-enter 0.25s cubic-bezier(0.2, 0.8, 0.2, 1) both';
      pane.style.transformOrigin = 'bottom center';
    }

    // Mount on body so Notes can behave like the other draggable windows. On
    // desktop it is immediately docked to the right by _restoreNotesSidebarDock.
    const backdrop = document.createElement('div');
    backdrop.className = 'notes-pane-backdrop';
    backdrop.id = 'notes-pane-backdrop';
    backdrop.addEventListener('click', (ev) => {
      if (ev.target === backdrop) closePanel('down');
    });
    backdrop.appendChild(pane);
    document.body.appendChild(backdrop);
    _wireNotesWindow(pane);
    _restoreNotesSidebarDock(pane);
    _bringNotesToFront(pane);

    // Events
    // (Close chevron removed : swipe down on mobile, tool-rail toggle on desktop.)

    // Mobile: swipe the grab handle / header down to dismiss (minimise to chip).
    // Mirrors the document sheet gesture : finger-following, velocity-based
    // dismiss, rubber-band on up-drag, spring snap-back.
    _wireNotesSwipeDismiss(pane.querySelector('.notes-mobile-grabber'), pane);
    _wireNotesSwipeDismiss(pane.querySelector('.notes-pane-header'), pane);

    pane.addEventListener('pointerdown', () => _bringNotesToFront(pane), true);
    pane.addEventListener('focusin', () => _bringNotesToFront(pane), true);

    const minBtn = document.getElementById('notes-minimize-btn');
    if (minBtn) minBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      closePanel('down');
    });
    wireToolbar(pane);
    // Select mode
    document.getElementById('notes-select-btn').addEventListener('click', () => {
      if (state.selectMode) _exitSelectMode(); else _enterSelectMode();
    });
    // Esc cancels select mode. Notes uses a toggle "Select" button rather
    // than a *-bulk-cancel button, so the global Esc-cancel handler in
    // keyboard-shortcuts.js can't reach it : handle it here. Capture phase
    // + stopPropagation so Esc cancels select instead of closing the panel.
    if (_notesSelectEscHandler) {
      document.removeEventListener('keydown', _notesSelectEscHandler, true);
    }
    _notesSelectEscHandler = (e) => {
      if (e.key === 'Escape' && state.selectMode) {
        e.preventDefault();
        e.stopPropagation();
        _exitSelectMode();
      }
    };
    document.addEventListener('keydown', _notesSelectEscHandler, true);
    wireSelectionActions();
    // Escape: exit select mode first (if active), otherwise close the panel.
    // Skip when the user is editing a form field : those have their own
    // ESC-to-cancel handlers and we don't want to nuke the whole panel
    // mid-edit.
    // Idempotent: remove any previous handler from a prior openPanel so
    // re-opening doesn't stack multiple handlers.
    if (_notesKeydownHandler) {
      document.removeEventListener('keydown', _notesKeydownHandler);
      _notesKeydownHandler = null;
    }
    _notesKeydownHandler = (e) => {
      if (!state.open) return;
      const t = e.target;
      const inField = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
      // Ctrl/Cmd+Z anywhere in the panel : undo the last note action. Skip when
      // typing in a field so the browser's normal text-undo still works.
      if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z') && !e.shiftKey) {
        if (inField) return;
        if (workflows.undoCount() === 0) return;
        e.preventDefault();
        _popAndRunUndo();
        return;
      }
      // Ctrl/Cmd+C while hovering a note card → copy that note. Skip when the
      // user is editing or has an active text selection (let the browser handle
      // a real text copy in that case).
      if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C') && !e.shiftKey && !e.altKey) {
        if (inField) return;
        const sel = window.getSelection?.();
        if (sel && sel.toString && sel.toString().length > 0) return;
        const hovered = document.querySelector('.note-card:hover');
        if (!hovered) return;
        const id = hovered.dataset.noteId;
        if (!id) return;
        e.preventDefault();
        // Flash the ⋯ menu button (copy now lives in that menu).
        const btn = hovered.querySelector('.note-card-corner-menu');
        _copyNote(id, btn);
        return;
      }
      if (e.key !== 'Escape') return;
      if (inField) return;
      if (state.selectMode) { _exitSelectMode(); return; }
      if (state.showingArchived) {
        // Mirror the archive toggle button: flip back to active notes.
        document.getElementById('notes-archive-toggle')?.click();
        return;
      }
      _forceCloseNotesPanel();
    };
    document.addEventListener('keydown', _notesKeydownHandler);

    // Load : show skeleton immediately, then fetch
    _renderLoadingSkeleton();
    // Defer the highlight flush to the next frame so it runs *after* the cards
    // are committed to the DOM (and any FLIP animations have settled), giving
    // the querySelector lookups inside something to find.
    _fetchNotes().then(() => {
      if (!pane.isConnected || !state.open) { _startReminderLoop(); return; }
      _renderNotes();
      requestAnimationFrame(() => _flushPendingHighlights());
      _startReminderLoop();
      _showNotesFirstOpenHint(pane);
    });
  }

  function closePanel(direction) {
    if (!state.open) return;
    app.setOpen(false);
    app.setEditingId(null);
    _clearViewedReminderGlows();
    const _minimize = direction === 'down';
    if (_minimize) {
      _ensureNotesChipRegistered();
    } else if (Modals.isRegistered('notes-panel')) {
      Modals.unregister('notes-panel');
    }

    // Drop panel-owned keydown listeners. The reminder interval is page-owned.
    if (_notesKeydownHandler) {
      document.removeEventListener('keydown', _notesKeydownHandler);
      _notesKeydownHandler = null;
    }
    if (_notesSelectEscHandler) {
      document.removeEventListener('keydown', _notesSelectEscHandler, true);
      _notesSelectEscHandler = null;
    }

    disposeOwnedPortals();
    disposeUI();
    document.body.classList.remove('notes-view');
    document.body.classList.remove('notes-mobile-mode');
    document.body.classList.remove('notes-drag-mode');
    // Closing the panel should PRESERVE in-progress edits, not drop them.
    // Commit any open in-place editor, and close the mobile fullscreen
    // overlay with save=true so the note is persisted.
    try { _commitOpenInPlaceEditor(); } catch {}
    _closeMobileFullscreenEdit({ save: true });
    // /notes route may have collapsed the wide sidebar to a rail; restore.
    try { window._restoreSidebarIfRouteCollapsed?.(); } catch (_) {}

    const btn = document.getElementById('tool-notes-btn');
    if (btn) btn.classList.remove('active');

    const pane = document.getElementById('notes-pane');
    const backdrop = document.getElementById('notes-pane-backdrop');
    if (pane) {
      // Scale-out + fade. Match the enter animation duration so close feels
      // like the same gesture played backwards.
      pane.classList.add('notes-pane-leaving');
      const _cleanup = () => {
        try { pane.remove(); } catch {}
        try { backdrop?.remove(); } catch {}
      };
      pane.addEventListener('animationend', _cleanup, { once: true });
      // Belt-and-braces: if animation is skipped (reduced motion / detached
      // tab) the listener won't fire; remove after the expected duration.
      setTimeout(_cleanup, 220);
    } else if (backdrop) {
      backdrop.remove();
    }
    // Show the dock chip for a swipe-down minimize (tap it to reopen).
    if (_minimize) { try { Modals.minimize('notes-panel'); } catch {} }
  }

  function togglePanel() {
    if (state.open) closePanel();
    else openPanel();
  }

  function isPanelOpen() { return state.open; }

  async function openNote(noteId) {
    const wasOpen = !!(isPanelOpen && isPanelOpen());
    app.setShowingArchived(false);
    app.setActiveLabel(null);
    app.setActiveFilter(null);
    app.setSearchQuery('');
    if (!wasOpen) {
      openPanel();
    } else {
      _bringNotesToFront();
      const searchEl = document.getElementById('notes-search');
      if (searchEl) searchEl.value = '';
      const pane = document.getElementById('notes-pane');
      if (pane) pane.classList.remove('notes-pane-archive');
      const archiveBtn = document.getElementById('notes-archive-toggle');
      if (archiveBtn) archiveBtn.classList.remove('active');
      await _fetchNotes();
      _renderNotes();
    }
    // openPanel() kicks off _fetchNotes() asynchronously, so the cards for
    // newly-created notes may not be in the DOM yet. Poll until the card exists.
    if (!noteId) return;
    let tries = 0;
    const findAndFlash = () => {
      const card = document.querySelector(`.note-card[data-note-id="${noteId}"]`)
        || document.querySelector(`.note-card[data-note-id^="${noteId.slice(0, 8)}"]`);
      if (card) {
        _smoothScrollToNoteCard(card);
        card.classList.add('note-card-flash');
        setTimeout(() => card.classList.remove('note-card-flash'), 1600);
        return true;
      }
      return false;
    };
    const tryNext = () => {
      if (findAndFlash()) return;
      if (++tries < 20) setTimeout(tryNext, 200);
    };
    setTimeout(tryNext, 120);
  }

  let _notesKeydownHandler = null;

  let _notesSelectEscHandler = null;

  function _wireNotesSwipeDismiss(el, pane) {
    if (!el || !pane) return;
    const DISMISS_THRESHOLD = 50, VELOCITY_THRESHOLD = 0.3, RUBBER = 0.35;
    let startY = 0, startX = 0, lastY = 0, lastT = 0, velocity = 0;
    let dragging = false, cancelled = false;

    el.addEventListener('touchstart', (e) => {
      if (window.innerWidth > 768 || e.touches.length !== 1) return;
      if (e.target.closest('button, input, select, label, textarea')) return;
      const t = e.touches[0];
      startY = t.clientY; startX = t.clientX; lastY = startY; lastT = e.timeStamp;
      velocity = 0; dragging = false; cancelled = false;
    }, { passive: true });

    el.addEventListener('touchmove', (e) => {
      if (cancelled || window.innerWidth > 768) return;
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - startX);
      const dy = t.clientY - startY;
      if (!dragging) {
        if (dx > 40 && dx > Math.abs(dy) * 2) { cancelled = true; return; }
        if (Math.abs(dy) > 8) {
          dragging = true;
          pane.style.animation = 'none';
          pane.style.transition = 'none';
          pane.style.willChange = 'transform';
        } else return;
      }
      const dt = e.timeStamp - lastT;
      if (dt > 0) velocity = velocity * 0.6 + ((t.clientY - lastY) / dt) * 0.4;
      lastY = t.clientY; lastT = e.timeStamp;
      e.preventDefault();
      pane.style.transform = dy > 0 ? `translateY(${dy}px)` : `translateY(${dy * RUBBER}px)`;
    }, { passive: false });

    const endSwipe = () => {
      if (!dragging) return;
      dragging = false;
      pane.style.willChange = '';
      const dy = lastY - startY;
      if (dy > DISMISS_THRESHOLD || (dy > 20 && velocity > VELOCITY_THRESHOLD)) {
        // Slide fully off-screen, then minimise. Keep it translated down (don't
        // reset) so it doesn't flash back before closePanel removes it.
        pane.style.transition = 'transform 0.2s cubic-bezier(0.2, 0, 0.4, 1)';
        pane.style.transform = 'translateY(100%)';
        setTimeout(() => closePanel('down'), 200);
      } else {
        pane.style.transition = 'transform 0.25s cubic-bezier(0.2, 0.9, 0.3, 1.05)';
        pane.style.transform = '';
        setTimeout(() => { pane.style.transition = ''; }, 260);
      }
    };
    el.addEventListener('touchend', endSwipe, { passive: true });
    el.addEventListener('touchcancel', endSwipe, { passive: true });
  }

  function _smoothScrollToNoteCard(card) {
    if (!card) return;
    const scroller = card.closest('.notes-pane-body');
    if (!scroller) {
      try { card.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (_) {}
      return;
    }
    const scrollerRect = scroller.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const delta = (cardRect.top - scrollerRect.top) - (scroller.clientHeight - cardRect.height) / 2;
    scroller.scrollTo({ top: Math.max(0, scroller.scrollTop + delta), behavior: 'smooth' });
  }

  function disposeOwnedPortals() {
    document.querySelectorAll('.note-reminder-menu, .note-corner-menu-dropdown, #notes-first-open-hint').forEach(dismissOrRemove);
  }

  return { _forceCloseNotesPanel, openPanel, closePanel, togglePanel, isPanelOpen, openNote, _smoothScrollToNoteCard };
}

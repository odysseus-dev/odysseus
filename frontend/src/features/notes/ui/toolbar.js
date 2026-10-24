/** Panel search, archive, list/grid and Microsoft To Do sync controls. */
export function createToolbar({
  api,
  app,
  state,
  persistence,
  uiModule,
  _renderNotes,
  _fetchNotes,
  _applyMasonry
}) {
  // Microsoft To Do sync. The button stays hidden unless an account is
  // actually connected — a control that can only ever say "nothing connected"
  // is noise in a header this small.
  let _todoSyncRunning = false;

  async function _todoAccountConnected() {
    try {
      const res = await api.microsoftTodoConfig();
      if (!res.ok) return false;
      const data = await res.json();
      return (data.accounts || []).length > 0;
    } catch (_) {
      return false;
    }
  }

  function _flattenTodoSync(raw) {
    const out = { tasks: 0, deleted: 0, errors: [] };
    const add = (part) => {
      if (!part || typeof part !== 'object') return;
      out.tasks += Number(part.tasks || 0);
      out.deleted += Number(part.deleted || 0);
      if (Array.isArray(part.errors)) out.errors.push(...part.errors);
    };
    // `direction=both` nests its counts under push/pull.
    if (raw && (raw.push || raw.pull)) { add(raw.push); add(raw.pull); }
    else add(raw);
    return out;
  }

  async function _syncTodo(interactive = true) {
    if (_todoSyncRunning) return;
    _todoSyncRunning = true;
    const btn = document.getElementById('notes-todo-sync');
    btn?.classList.add('active');
    if (btn) btn.disabled = true;
    try {
      // `both` pushes local edits before pulling. A pull alone would strand a
      // task created here while offline, and "Sync" is exactly when the user
      // expects that retry to happen.
      const res = await api.syncTodo('both');
      const data = _flattenTodoSync(await res.json().catch(() => ({})));
      // The open-time pass is silent: it redraws only when something actually
      // moved, so opening Notes does not flash the board or pop a toast for a
      // sync the user did not ask for.
      if (interactive || data.tasks > 0 || data.deleted > 0) {
        await _fetchNotes();
        _renderNotes();
      }
      if (!interactive) return;
      if (data.errors.length) uiModule.showError(String(data.errors[0]).slice(0, 200));
      else uiModule.showToast?.(`Tasks synced (${data.tasks})`);
    } catch (e) {
      if (interactive) uiModule.showError('Task sync failed');
    } finally {
      _todoSyncRunning = false;
      btn?.classList.remove('active');
      if (btn) btn.disabled = false;
    }
  }

  function wireToolbar(pane) {
    // Search
    const searchEl = document.getElementById('notes-search');
    if (searchEl) {
      searchEl.addEventListener('input', () => {
        app.setSearchQuery(searchEl.value.trim().toLowerCase());
        _renderNotes();
      });
    }

    // Microsoft To Do sync
    const todoSyncBtn = document.getElementById('notes-todo-sync');
    if (todoSyncBtn) {
      todoSyncBtn.addEventListener('click', () => { _syncTodo(true); });
      _todoAccountConnected().then((connected) => {
        if (!connected) return;
        todoSyncBtn.style.display = '';
        // Same shape as the calendar: pull on open so a task ticked off on a
        // phone is already here, without waiting for the user to press Sync.
        _syncTodo(false);
      });
    }

    // View toggle
    const archiveBtn = document.getElementById('notes-archive-toggle');
    if (archiveBtn) {
      const ARCHIVE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg><span class="notes-header-btn-label">Archive</span>';
      const CLOSE_ICON   = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg><span class="notes-header-btn-label">Archive</span>';
      const syncArchiveBtn = () => {
        archiveBtn.classList.toggle('active', state.showingArchived);
        archiveBtn.title = state.showingArchived ? 'Exit archive' : 'View archive';
        archiveBtn.style.opacity = state.showingArchived ? '1' : '0.8';
        // Swap to an X while in archive view so it doubles as a close-back-
        // to-active-notes toggle.
        archiveBtn.innerHTML = state.showingArchived ? CLOSE_ICON : ARCHIVE_ICON;
        // Tint the whole pane so it's obvious you're not in the active list.
        pane.classList.toggle('notes-pane-archive', state.showingArchived);
      };
      syncArchiveBtn();
      archiveBtn.addEventListener('click', async () => {
        app.setShowingArchived(!state.showingArchived);
        app.clearSelection();
        syncArchiveBtn();
        // Brief fade so the body content swap doesn't snap : the bg-tint
        // change is already eased by CSS transitions on .notes-pane*.
        const _bodyEl = document.querySelector('#notes-pane .notes-pane-body');
        if (_bodyEl) {
          _bodyEl.style.transition = 'opacity 0.18s ease';
          _bodyEl.style.opacity = '0.25';
        }
        await _fetchNotes();
        _renderNotes();
        if (_bodyEl) {
          requestAnimationFrame(() => {
            _bodyEl.style.opacity = '';
            _bodyEl.addEventListener('transitionend', () => { _bodyEl.style.transition = ''; }, { once: true });
          });
        }
      });
    }
    const viewBtn = document.getElementById('notes-view-toggle');
    if (viewBtn) {
      pane.classList.toggle('notes-view-grid', state.viewMode === 'grid');
      // Label shows what you'll switch TO : "Grid" while in list, "List" while in grid.
      const _setViewLabel = () => {
        const lbl = viewBtn.querySelector('.notes-header-btn-label');
        if (lbl) lbl.textContent = state.viewMode === 'grid' ? 'List' : 'Grid';
      };
      _setViewLabel();
      requestAnimationFrame(() => _applyMasonry(document.querySelector('#notes-pane .notes-pane-body')));
      viewBtn.addEventListener('click', () => {
        app.setViewMode(state.viewMode === 'grid' ? 'list' : 'grid');
        try { persistence.setItem('odysseus-notes-view', state.viewMode); } catch {}
        pane.classList.toggle('notes-view-grid', state.viewMode === 'grid');
        _setViewLabel();
        requestAnimationFrame(() => _applyMasonry(document.querySelector('#notes-pane .notes-pane-body')));
      });
    }

  }
  return { wireToolbar };
}

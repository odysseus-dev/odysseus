/** Mobile fullscreen editing, link reader mode and return to the panel. */
export function createMobileEditor({
  _applyDraftToNote,
  _buildForm,
  _fetchNotes,
  _linkify,
  _renderNotes,
  _uid,
  _wireChecklistTouchReorder,
  _wireRow,
  app,
  state,
  uiModule
}) {
  function _isNotesMobileMode() {
    return ('ontouchstart' in window) && window.innerWidth <= 768;
  }

  let _mobileFsOverlay = null;

  let _mobileFsNoteId = null;

  function _openMobileFullscreenEdit(id, fromCard) {
    const note = state.notes.find(n => n.id === id);
    if (!note) return;
    // Tear down any previous overlay (defensive).
    _closeMobileFullscreenEdit({ save: false });
    _mobileFsNoteId = id;
    app.setEditingId(id);

    const overlay = document.createElement('div');
    overlay.className = 'note-fullscreen-overlay';
    overlay.innerHTML = `
    <div class="note-fullscreen-header">
      <button type="button" class="note-fullscreen-back" title="Back">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>
      </button>
      <div class="note-fullscreen-actions"></div>
    </div>
    <div class="note-fullscreen-body"></div>
  `;
    const body = overlay.querySelector('.note-fullscreen-body');
    // Reuse the same edit form the in-place flow builds. Save buttons,
    // checklist toggles, etc. all work as-is. Restore any unsaved draft.
    const { note: _n, restored } = _applyDraftToNote(note, id);
    const form = _buildForm(_n);
    body.appendChild(form);
    if (restored) uiModule.showToast('Restored unsaved changes');
    document.body.appendChild(overlay);
    _mobileFsOverlay = overlay;

    // Animate up from the tapped tile's position so the transition reads
    // as a zoom rather than a hard cut.
    if (fromCard) {
      const r = fromCard.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      overlay.style.transformOrigin =
        `${((r.left + r.width / 2) / vw) * 100}% ${((r.top + r.height / 2) / vh) * 100}%`;
    }
    overlay.classList.add('opening');
    requestAnimationFrame(() => overlay.classList.add('open'));

    // Wire the back button : saves whatever the form has and closes.
    // mousedown preventDefault so it doesn't blur the input on first tap (which
    // would eat the tap and require a second one).
    const _backBtn = overlay.querySelector('.note-fullscreen-back');
    _backBtn.addEventListener('mousedown', (e) => e.preventDefault());
    _backBtn.addEventListener('click', () => {
      _closeMobileFullscreenEdit({ save: true });
    });

    // The form's built-in Cancel only resets the in-place edit state; in
    // the overlay context it does nothing visible. Replace its handler so
    // Cancel actually dismisses the overlay without saving.
    const cancelBtn = form.querySelector('.note-form-cancel');
    if (cancelBtn) {
      const fresh = cancelBtn.cloneNode(true);
      cancelBtn.parentNode.replaceChild(fresh, cancelBtn);
      fresh.addEventListener('mousedown', (e) => e.preventDefault());
      fresh.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        _closeMobileFullscreenEdit({ save: false });
      });
    }
    // The built-in Save handler does the API call + refresh, but doesn't
    // dismiss our overlay. Augment it (do NOT replace : the original is
    // async and we'd lose the API call) to schedule a close once the
    // save+render has had time to complete.
    const saveBtn = form.querySelector('.note-form-save');
    if (saveBtn) {
      saveBtn.addEventListener('click', () => {
        setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 350);
      });
    }
    // Make the checklist row drag handle (⋮⋮) actually work on touch.
    // The form's default uses HTML5 native draggable which never fires
    // on iOS/Android. Wire touch-based reorder for any row inside the
    // overlay's checklist.
    _wireChecklistTouchReorder(form);

    // For each todo row whose text contains a URL, swap the bare <input>
    // for a linkified <span> so URLs are tappable. Tapping anywhere that
    // ISN'T a link flips back to the input for editing.
    form.querySelectorAll('.note-cl-row').forEach(_addRowReadMode);

    // Move only Delete from the form's footer actions row into the header.
    // Archive intentionally stays out of the header: the footer Update button
    // already morphs into the mobile Archive action for an untouched note.
    const headerActions = overlay.querySelector('.note-fullscreen-actions');
    const archiveBtn = form.querySelector('.note-form-archive-btn');
    const deleteBtn  = form.querySelector('.note-form-delete-btn');
    if (headerActions && deleteBtn) {
      deleteBtn.classList.remove('note-form-collapsible');
      headerActions.appendChild(deleteBtn);
    }
    // The built-in archive/delete handlers re-render the notes grid but
    // leave THIS overlay sitting in front of it : looks like nothing
    // happened. Add follow-up listeners that close the overlay so the
    // user sees the action take effect.
    if (archiveBtn) {
      archiveBtn.addEventListener('click', () => {
        setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 200);
      });
    }
    if (deleteBtn) {
      deleteBtn.addEventListener('click', () => {
        // Delete shows a styled confirm : give it room to resolve before
        // we try to dismiss the overlay.
        setTimeout(() => _closeMobileFullscreenEdit({ save: false }), 500);
      });
    }

    // Tuck the tags input into the bottom actions row (Cancel / Update),
    // pinned to the LEFT. Frees the meta row of an extra wrapping line
    // and groups all the "exit" controls together.
    const actionsGroup = form.querySelector('.note-form-actions-group');
    const tagsInput    = form.querySelector('.note-form-label');
    if (actionsGroup && tagsInput) {
      actionsGroup.insertBefore(tagsInput, actionsGroup.firstChild);
    }

    // For checklist-type notes, move the photo (attach image) button into
    // the same row as the + Add button (right side) : keeps the meta row
    // tidy and puts the camera within thumb-reach of the active edit.
    const addBtn   = form.querySelector('.note-cl-add');
    const photoBtn = form.querySelector('.note-form-photo-btn');
    if (addBtn && photoBtn) {
      const addRow = document.createElement('div');
      addRow.className = 'note-cl-add-row';
      addBtn.parentNode.insertBefore(addRow, addBtn);
      addRow.appendChild(addBtn);
      addRow.appendChild(photoBtn);
      // Tapping anywhere on the row (the empty gap, the dashed border,
      // the "+ Add" label) triggers add. The photo button keeps its own
      // click target so attach-image isn't ambushed.
      addRow.addEventListener('click', (e) => {
        if (e.target.closest('.note-form-photo-btn')) return;
        if (e.target === addBtn || addBtn.contains(e.target)) return;
        addBtn.click();
      });
      // The form's delegated "+ Add" handler does
      //   inputs.insertBefore(newRow, addBtn)
      // : but addBtn is no longer a direct child of `.note-checklist-inputs`
      // now that we wrapped it. Bind a fresh handler that does the same
      // thing but inserts before the WRAPPING row, and stop propagation
      // so the broken delegate never runs.
      addBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const inputs = form.querySelector('.note-checklist-inputs');
        if (!inputs) return;
        const newRow = document.createElement('div');
        newRow.className = 'note-cl-row';
        newRow.draggable = true;
        newRow.dataset.itemId = _uid();
        newRow.dataset.indent = '0';
        newRow.innerHTML = '<span class="note-cl-grip" title="Drag">⋮⋮</span><span class="note-cl-dot"></span><input type="text" class="note-cl-text" placeholder="Item..." /><button type="button" class="note-cl-rm">&times;</button>';
        inputs.insertBefore(newRow, addRow);
        _wireRow(newRow, inputs);
        // Touch reorder on the freshly-added row's grip.
        _wireChecklistTouchReorder(form);
        newRow.querySelector('.note-cl-text')?.focus();
      }, { capture: true });
    }

    // Read-mode overlay for plain notes: render the content as a div with
    // clickable hyperlinks, layered above the textarea. Tapping anywhere
    // in the overlay that ISN'T a link hides the overlay and focuses the
    // textarea so the user can start editing. Tapping a link opens it.
    const ta = form.querySelector('.note-form-content');
    if (ta && (note.content || '').trim()) {
      const reader = document.createElement('div');
      reader.className = 'note-form-content-reader';
      reader.innerHTML = _linkify(note.content || '');
      ta.style.display = 'none';
      ta.insertAdjacentElement('beforebegin', reader);
      reader.addEventListener('click', (e) => {
        if (e.target.closest('a')) return;  // let links open normally
        reader.remove();
        ta.style.display = '';
        // Let the browser place the cursor naturally : forcing
        // setSelectionRange right after focus() raced with the underlying
        // tap event and produced inconsistent cursor positions on mobile.
        ta.focus({ preventScroll: true });
      });
    }

    // Opening an EXISTING note → read mode, no keyboard pop. Only a
    // brand-new note (created via the + button) should auto-focus an
    // input field. The user can tap the content to switch to edit.
    // (New-note creation flows through _createNote, not this function.)
  }

  function _closeMobileFullscreenEdit(opts = {}) {
    if (!_mobileFsOverlay) return;
    const overlay = _mobileFsOverlay;
    overlay.querySelector('.note-form')?._flushDraft?.();
    _mobileFsOverlay = null;
    // If the form has a Save button, click it on close so edits aren't lost
    // when the user uses the back arrow instead of an explicit Save.
    if (opts.save) {
      const saveBtn = overlay.querySelector('.note-form-save, [data-action="save"]');
      if (saveBtn) try { saveBtn.click(); } catch {}
    }
    overlay.classList.remove('open');
    overlay.classList.add('closing');
    setTimeout(() => {
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      _mobileFsNoteId = null;
      app.setEditingId(null);
      // Refresh the grid so any save the user made is reflected.
      if (opts.save !== false) { _fetchNotes().then(_renderNotes).catch(() => {}); }
    }, 220);
  }

  function _addRowReadMode(row) {
    const txt = row.querySelector('.note-cl-text');
    if (!txt) return;
    const val = txt.value || '';
    if (!/(https?:\/\/|www\.)/i.test(val)) return;
    if (row.querySelector('.note-cl-text-reader')) return;  // already wired
    const span = document.createElement('span');
    span.className = 'note-cl-text-reader';
    span.innerHTML = _linkify(val);
    txt.style.display = 'none';
    txt.insertAdjacentElement('beforebegin', span);
    span.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;  // let the link open
      span.remove();
      txt.style.display = '';
      txt.focus({ preventScroll: true });
    });
  }

  return { _isNotesMobileMode, _openMobileFullscreenEdit, _closeMobileFullscreenEdit, _addRowReadMode };
}

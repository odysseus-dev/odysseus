/** Build and wire note, checklist, goal and drawing editor forms. */
export function createEditor({
  deleteFromEditor,
  submitNote,
  COLORS,
  _applyMasonry,
  _archiveNoteById,
  _buildChecklistHtml,
  _buildDrawHtml,
  _buildGoalHtml,
  _collectItems,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _esc,
  _isBgImage,
  _renderNotes,
  _safeImgSrc,
  _uid,
  _uploadCanvasAsPng,
  _wireCanvas,
  _wireChecklist,
  _wireDraftAutosave,
  _wireFormColors,
  _wireGoalForm,
  _wireReminderPicker,
  api,
  app,
  state,
  uiModule
}) {
  function _buildForm(note = null) {
    const isEdit = note && note.id;
    const type = note?.note_type === 'checklist' ? 'todo' : (note?.note_type || 'note');
    const color = note?.color || '';
    const items = note?.items || [{ id: _uid(), text: '', done: false }];

    const form = document.createElement('div');
    form.className = 'note-form';
    form.dataset.noteType = type;
    form.dataset.noteColor = color || '';
    if (color && !_isBgImage(color)) form.classList.add('note-color-' + color);
    if (_isBgImage(color)) form.setAttribute('style', _customColorStyle(color));
    let currentImageUrl = _safeImgSrc(note?.image_url || '');
    form.innerHTML = `
    <div class="note-form-header">
      <input type="text" class="note-form-title" placeholder="Title" value="${_esc(note?.title || '')}" />
      <button type="button" class="note-form-icon-btn note-form-remind-btn${note?.due_date ? ' has-date' : ''}" title="Remind me">
        <svg width="31" height="31" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
      </button>
      <input type="hidden" class="note-form-due" value="${note?.due_date || ''}" />
      <input type="hidden" class="note-form-repeat" value="${note?.repeat || 'none'}" />
    </div>
    ${currentImageUrl && type !== 'draw' ? `<div class="note-form-image-wrap"><img class="note-form-image" src="${_esc(currentImageUrl)}" draggable="false" /><button class="note-form-image-rm" title="Remove">&times;</button></div>` : ''}
    <div class="note-form-body">
      ${type === 'note'
          ? `<textarea class="note-form-content" placeholder="Take a note..." rows="4">${_esc(note?.content || '')}</textarea>`
          : type === 'draw'
          ? _buildDrawHtml()
          : type === 'goal'
          ? _buildGoalHtml(note, items)
          : _buildChecklistHtml(items)}
    </div>
    <div class="note-form-reminder-tags"></div>
    <div class="note-form-meta">
      <div class="note-form-type-seg${type === 'todo' ? ' is-todo' : type === 'draw' ? ' is-draw' : ''}" role="group">
        <button type="button" class="note-form-type-pill${type === 'note' ? ' active' : ''}" data-type="note">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg>
          <span>Note</span>
        </button>
        <button type="button" class="note-form-type-pill${type === 'todo' ? ' active' : ''}" data-type="todo">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
          <span>Todo</span>
        </button>
        <button type="button" class="note-form-type-pill${type === 'draw' ? ' active' : ''}" data-type="draw">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19l7-7 3 3-7 7-3-3z"/><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z"/><path d="M2 2l7.586 7.586"/><circle cx="11" cy="11" r="2"/></svg>
          <span>Draw</span>
        </button>
      </div>
      <button class="note-form-photo-btn" title="Attach photo">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
      </button>
      <input type="file" class="note-form-photo-input" accept="image/*" capture="environment" style="display:none" />
      <div class="note-color-picker">
        ${COLORS.map(c => `<span class="note-color-dot${_dotIsActive(c.value, color) ? ' active' : ''}" data-color="${c.value}" style="background:${_dotBg(c.value, color)}" title="${c.name || 'default'}"></span>`).join('')}
      </div>
      <input type="text" class="note-form-label" value="${_esc(note?.label || '')}" placeholder="#tag1 #tag2" title="Tag(s) — space-separated" />
      <div class="note-form-actions-group">
        ${isEdit ? `
        <button type="button" class="note-form-text-btn note-form-archive-btn note-form-collapsible" title="Archive">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 002 2h12a2 2 0 002-2V8"/><path d="M10 12h4"/></svg><span class="nft-label">Archive</span>
        </button>
        <button type="button" class="note-form-text-btn note-form-delete-btn note-form-collapsible danger" title="Delete">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg><span class="nft-label">Delete</span>
        </button>
        ` : ''}
        <span class="note-form-actions-spacer"></span>
        <button class="note-form-cancel note-form-text-btn note-form-collapsible" title="Close (keep changes)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg><span class="nft-label">Close</span>
        </button>
        <button class="note-form-save note-form-text-btn" title="${isEdit ? 'Update' : 'Save'}">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><span class="nft-label">${isEdit ? 'Update' : 'Save'}</span>
        </button>
      </div>
    </div>
  `;

    let currentType = type;
    let currentColor = color;
    // Stash original-form values so round-trips (Note→Todo→Note) restore the
    // user's hand-formatted text instead of a join of generated items. Same the
    // other way: if you started in todo, switch to note, switch back, items
    // come back unchanged.
    let _stashedNoteText = (type === 'note') ? (note?.content || '') : null;
    let _stashedTodoItems = (type === 'todo' && Array.isArray(note?.items)) ? note.items.slice() : null;
    // Goal mode kept its own pair of stashes (description + steps) so a
    // Todo→Goal→Todo round-trip wouldn't lose either side. The Goal pill in
    // the type picker was later removed, so the only entry point now is
    // *editing* an existing goal-typed note : but the switch handler still
    // accepts Goal→Todo/Note transitions (downgrading legacy goals), so
    // these stashes still earn their keep.
    let _stashedGoalDesc = (type === 'goal') ? (note?.content || '') : null;
    let _stashedGoalItems = (type === 'goal' && Array.isArray(note?.items)) ? note.items.slice() : null;

    // Drawing also stashes the saved image URL so it survives Note↔Draw flips.
    let _stashedDrawUrl = (type === 'draw') ? (_safeImgSrc(note?.image_url) || null) : null;
    const _refreshFormLayout = () => {
      const body = form.closest('.notes-pane-body');
      if (!body) return;
      _applyMasonry(body);
      requestAnimationFrame(() => {
        _applyMasonry(body);
        requestAnimationFrame(() => _applyMasonry(body));
      });
    };

    // Type segmented control : Note | Todo | Draw
    form.querySelectorAll('.note-form-type-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        const newType = pill.dataset.type;
        if (newType === currentType) return;
        const bodyEl = form.querySelector('.note-form-body');
        // Stash whatever the user has in the current mode before swapping it
        // out, so a subsequent flip back restores their work.
        if (currentType === 'note') {
          _stashedNoteText = form.querySelector('.note-form-content')?.value || '';
        } else if (currentType === 'todo') {
          _stashedTodoItems = _collectItems(form);
        } else if (currentType === 'goal') {
          _stashedGoalDesc = form.querySelector('.note-form-goal-desc')?.value || '';
          _stashedGoalItems = _collectItems(form);
        } else if (currentType === 'draw') {
          const c = form.querySelector('.note-form-canvas');
          if (c) { try { _stashedDrawUrl = c.toDataURL('image/png'); } catch {} }
        }
        // Render the new mode's body and re-wire its inputs.
        if (newType === 'todo') {
          let nextItems;
          if (_stashedTodoItems && _stashedTodoItems.length) {
            nextItems = _stashedTodoItems;
          } else if (_stashedGoalItems && _stashedGoalItems.length) {
            // Going Goal→Todo keeps the AI-generated steps as a plain checklist.
            nextItems = _stashedGoalItems;
          } else if (_stashedNoteText) {
            const lines = _stashedNoteText.split('\n').map(s => s.trim()).filter(Boolean);
            nextItems = lines.length ? lines.map(t => ({ id: _uid(), text: t, done: false })) : [{ id: _uid(), text: '', done: false }];
          } else {
            nextItems = [{ id: _uid(), text: '', done: false }];
          }
          bodyEl.innerHTML = _buildChecklistHtml(nextItems);
          _wireChecklist(bodyEl);
        } else if (newType === 'draw') {
          bodyEl.innerHTML = _buildDrawHtml();
          // If the user just attached a photo (via the photo button) and then
          // toggled to Draw, paint that photo onto the canvas so they can draw
          // on top of it. _stashedDrawUrl wins if they were drawing earlier in
          // the same edit session.
          _wireCanvas(bodyEl, _stashedDrawUrl || currentImageUrl || _safeImgSrc(note?.image_url) || null);
        } else {
          const text = (_stashedNoteText !== null && _stashedNoteText !== undefined && _stashedNoteText !== '')
            ? _stashedNoteText
            : (_stashedGoalDesc && _stashedGoalDesc)
            || (_stashedTodoItems || _stashedGoalItems || []).map(i => i.text).join('\n');
          bodyEl.innerHTML = `<textarea class="note-form-content" placeholder="Take a note..." rows="4">${_esc(text)}</textarea>`;
          _wireHashtag(bodyEl.querySelector('.note-form-content'));
        }
        const focusEl = newType === 'note'
          ? bodyEl.querySelector('.note-form-content')
          : newType === 'todo'
            ? bodyEl.querySelector('.note-cl-text')
            : null;
        if (focusEl) {
          requestAnimationFrame(() => {
            focusEl.focus({ preventScroll: true });
            try {
              const end = focusEl.value.length;
              focusEl.setSelectionRange(end, end);
            } catch {}
          });
        }
        currentType = newType;
        const seg = form.querySelector('.note-form-type-seg');
        seg?.classList.toggle('is-todo', newType === 'todo');
        seg?.classList.toggle('is-draw', newType === 'draw');
        form.querySelectorAll('.note-form-type-pill').forEach(p => p.classList.toggle('active', p.dataset.type === newType));
        // The standalone image preview (form-image-wrap) and the canvas would
        // otherwise both show the same image_url when editing a drawn note.
        // Hide it in draw mode, restore it when leaving draw mode.
        const imgWrap = form.querySelector('.note-form-image-wrap');
        if (imgWrap) imgWrap.style.display = (newType === 'draw') ? 'none' : '';
        // The background-color dots set the note card's bg : they make no sense
        // for a drawn note (the canvas image IS the card content), so hide them.
        const bgPicker = form.querySelector('.note-color-picker');
        if (bgPicker) bgPicker.style.display = (newType === 'draw') ? 'none' : '';
        if (form.closest('.notes-pane.notes-view-grid') && window.matchMedia('(max-width: 768px)').matches) {
          form.style.gridColumn = '1 / -1';
          form.style.gridRowEnd = newType === 'draw' ? 'span 152' : 'span 64';
        }
        _refreshFormLayout();
      });
    });

    // Slide a finger across the Note/Todo/Draw control to switch modes (mobile).
    // On touchmove we find the pill under the finger and click it : reusing the
    // pill click handler above, so the body re-renders + content stashing all
    // work. Only fires when crossing into a *different* pill.
    const _typeSeg = form.querySelector('.note-form-type-seg');
    if (_typeSeg) {
      let _sliding = false;
      const _activateAt = (x, y) => {
        const pill = document.elementFromPoint(x, y)?.closest?.('.note-form-type-pill');
        if (pill && !pill.classList.contains('active')) pill.click();
      };
      _typeSeg.addEventListener('touchstart', () => { _sliding = true; }, { passive: true });
      _typeSeg.addEventListener('touchmove', (e) => {
        if (_sliding && e.touches[0]) _activateAt(e.touches[0].clientX, e.touches[0].clientY);
      }, { passive: true });
      _typeSeg.addEventListener('touchend', () => { _sliding = false; });
      _typeSeg.addEventListener('touchcancel', () => { _sliding = false; });
    }

    _wireFormColors(form, value => { currentColor = value; });

    if (currentType === 'todo') _wireChecklist(form.querySelector('.note-form-body'));
    if (currentType === 'goal') _wireGoalForm(form, form.querySelector('.note-form-body'));
    if (currentType === 'draw') {
      _wireCanvas(form.querySelector('.note-form-body'), _safeImgSrc(note?.image_url) || null);
      // Same hides we apply on type-switch : keep them consistent on initial open.
      const _ip = form.querySelector('.note-form-image-wrap'); if (_ip) _ip.style.display = 'none';
      const _cp = form.querySelector('.note-color-picker'); if (_cp) _cp.style.display = 'none';
    }

    // Auto-grow the plain-note textarea so editing longer notes is
    // comfortable : it expands with the content (up to a cap) instead of
    // staying a cramped 4-row box. The user can still drag-resize too.
    const _contentTa = form.querySelector('.note-form-content');
    if (_contentTa) {
      const _grow = () => {
        _contentTa.style.height = 'auto';
        // Inline form: cap at ~50vh so a huge note doesn't push the action
        // buttons off-screen. Fullscreen mobile overlay: the body scrolls and
        // there are no inline buttons crowding, so allow nearly the full height
        // : capping at 50vh there clipped longer notes ("part disappears").
        const inFullscreen = !!_contentTa.closest('.note-fullscreen-overlay');
        const max = Math.round(window.innerHeight * (inFullscreen ? 0.9 : 0.5));
        _contentTa.style.height = Math.min(_contentTa.scrollHeight, max) + 'px';
      };
      _contentTa.addEventListener('input', _grow);
      // Grow on open so existing content is fully visible. Run again after the
      // fullscreen overlay's open animation settles : measuring mid-animation
      // (the overlay starts scaled/transitioning) can under-size the box.
      setTimeout(_grow, 0);
      setTimeout(_grow, 360);
    }

    _wireReminderPicker(form);

    // Photo upload
    const photoBtn = form.querySelector('.note-form-photo-btn');
    const photoInput = form.querySelector('.note-form-photo-input');
    if (photoBtn && photoInput) {
      photoBtn.addEventListener('click', () => photoInput.click());
      photoInput.addEventListener('change', async () => {
        const file = photoInput.files?.[0];
        if (!file) return;
        const fd = new FormData();
        fd.append('files', file);
        try {
          const res = await api.upload(fd);
          const data = await res.json();
          const fileId = data.files?.[0]?.id;
          if (!fileId) throw new Error('Upload failed');
          currentImageUrl = api.uploadUrl(fileId);
          // Only ever keep the latest attached photo : drop any existing wrap
          // before inserting a fresh one. Picking a second photo replaces the
          // first instead of stacking.
          form.querySelector('.note-form-image-wrap')?.remove();
          const wrap = document.createElement('div');
          wrap.className = 'note-form-image-wrap';
          wrap.innerHTML = `<img class="note-form-image" draggable="false" /><button class="note-form-image-rm" title="Remove">&times;</button>`;
          // Insert AFTER the whole header (a flex-row), not after the
          // title input itself : otherwise the image lands as a sibling
          // of the title inside the header and flex puts them side-by-side.
          form.querySelector('.note-form-header').after(wrap);
          wrap.querySelector('.note-form-image-rm').addEventListener('click', () => { wrap.remove(); currentImageUrl = ''; });
          wrap.querySelector('img').src = currentImageUrl;
        } catch (err) { uiModule.showError('Image upload failed'); }
        photoInput.value = '';
      });
    }
    // Existing image remove
    form.querySelector('.note-form-image-rm')?.addEventListener('click', () => {
      form.querySelector('.note-form-image-wrap')?.remove();
      currentImageUrl = '';
    });

    // Title Enter -> focus body (textarea or first checklist item)
    form.querySelector('.note-form-title').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        const ta = form.querySelector('.note-form-content');
        if (ta) { ta.focus(); return; }
        const firstItem = form.querySelector('.note-cl-text');
        if (firstItem) firstItem.focus();
      }
    });

    // Hashtag → label: typing "#foo " in title/content appends "foo" to the
    // space-separated tag list. Repeats are deduplicated, so #foo #foo only
    // keeps one. Tags already present in the label field are left alone.
    const labelInput = form.querySelector('.note-form-label');
    const _hashtagRe = /(^|\s)#([A-Za-z0-9][\w-]*)\s$/;
    function _wireHashtag(el) {
      if (!el || !labelInput) return;
      el.addEventListener('input', () => {
        const m = _hashtagRe.exec(el.value);
        if (!m) return;
        const tag = m[2];
        // Dedup against the stripped form : labelInput may already hold `#tag`
        // (after Enter normalised), so includes(tag) on the raw split would
        // miss the duplicate and append a bare `tag` next to `#tag`.
        const existing = labelInput.value.trim().split(/\s+/).filter(Boolean);
        const stripped = existing.map(t => t.replace(/^#+/, ''));
        if (!stripped.includes(tag)) {
          existing.push('#' + tag);
          labelInput.value = existing.join(' ');
          labelInput.classList.add('flash-once');
          setTimeout(() => labelInput.classList.remove('flash-once'), 600);
        }
        const cut = el.value.length - m[0].length + m[1].length;
        el.value = el.value.slice(0, cut);
      });
    }
    _wireHashtag(form.querySelector('.note-form-title'));
    _wireHashtag(form.querySelector('.note-form-content'));
    // Pressing Enter in the tag field commits the current word as its own tag
    // and parks the cursor after a trailing space, so the next word becomes a
    // separate tag rather than overwriting the previous one.
    labelInput?.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey) return;
      e.preventDefault();
      e.stopPropagation();
      // Strip any leading #s the user typed, dedupe, then re-prepend exactly
      // one #. So typing "foo" or "#foo" both end up as "#foo " in the input;
      // the save handler keeps stripping #s before storing so DB stays clean.
      const tags = [...new Set(labelInput.value.split(/\s+/).map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
      if (!tags.length) return;
      labelInput.value = tags.map(t => '#' + t).join(' ') + ' ';
      labelInput.setSelectionRange(labelInput.value.length, labelInput.value.length);
      labelInput.classList.add('flash-once');
      setTimeout(() => labelInput.classList.remove('flash-once'), 600);
    });

    // Shift+Enter (or Cmd/Ctrl+Enter) anywhere in the form -> save
    // Escape -> cancel edit
    form.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.shiftKey || e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        form.querySelector('.note-form-save')?.click();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        form.querySelector('.note-form-cancel')?.click();
      }
    });

    // Save. Prevent the button from stealing focus on press: on mobile, the
    // first tap would otherwise just blur the focused textarea/input (closing
    // the keyboard and shifting layout), so the tap never reached the button and
    // you had to tap "Done" twice. mousedown preventDefault keeps focus put while
    // still letting the click fire.
    const _saveBtnEl0 = form.querySelector('.note-form-save');
    _saveBtnEl0.addEventListener('mousedown', (e) => e.preventDefault());
    _saveBtnEl0.addEventListener('click', async () => {
      // Guard against spam-clicks: the drawing save AWAITS a canvas upload before
      // the optimistic re-render removes the form, so without this a slow upload
      // let repeated clicks create duplicate notes.
      const _saveBtn = form.querySelector('.note-form-save');
      if (_saveBtn._saving) return;
      // Mobile: when an existing note is opened and closed without edits, the
      // Update (✓) button morphs into Archive (set up below). Route the click
      // directly through the archive flow. Using a DOM .click() proxy here was
      // fragile on mobile because the real archive button can move from the
      // footer to the fullscreen header.
      if (_saveBtn.classList.contains('archive-mode')) {
        _archiveNoteById(note?.id);
        return;
      }
      _saveBtn._saving = true; _saveBtn.disabled = true; _saveBtn.style.opacity = '0.5';
      try {
      form._flushDraft?.();
      await form._stopDraftAutosave?.();
      form._flushDraft?.();
      const title = form.querySelector('.note-form-title').value.trim();
      // Normalize tag input: split on whitespace, strip leading #s, dedupe,
      // re-join with single spaces. Empty → null.
      const _rawLabel = form.querySelector('.note-form-label')?.value || '';
      const _tags = [...new Set(_rawLabel.split(/\s+/).map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
      if (form.querySelector('.note-form-due').value && !_tags.includes('reminder')) _tags.push('reminder');
      const labelVal = _tags.length ? _tags.join(' ') : null;
      const payload = {
        title,
        note_type: currentType,
        color: currentColor,
        label: labelVal,
        due_date: form.querySelector('.note-form-due').value || null,
        repeat: form.querySelector('.note-form-repeat')?.value || 'none',
        image_url: currentImageUrl || null,
      };
      if (currentType === 'note') {
        payload.content = form.querySelector('.note-form-content')?.value || '';
      } else if (currentType === 'draw') {
        // Upload the canvas PNG before saving so image_url points to a
        // persistent file. We block the save until upload completes : drawings
        // can't be re-rendered later without the URL.
        const canvas = form.querySelector('.note-form-canvas');
        const upload = await _uploadCanvasAsPng(canvas, note?.gallery_id || null);
        if (!upload?.url) { uiModule.showError('Failed to save drawing'); return; }
        payload.image_url = upload.url;
        if (upload.galleryId) payload.gallery_id = upload.galleryId;
      } else if (currentType === 'goal') {
        // Legacy: existing goal-type notes still edit through this branch.
        // No AI involvement : save as a normal note with description + items.
        payload.content = form.querySelector('.note-form-goal-desc')?.value || '';
        payload.items = _collectItems(form);
      } else {
        payload.items = _collectItems(form);
      }
      if (isEdit) payload.id = note.id;
      submitNote(payload, note, isEdit);
      } finally {
        // Re-enable on early returns / errors. On success the form is removed by
        // the optimistic re-render, so re-enabling the detached button is a no-op.
        _saveBtn._saving = false; _saveBtn.disabled = false; _saveBtn.style.opacity = '';
      }
    });

    // Mobile-only: when editing an existing note, the Update (✓) button starts in
    // archive-mode (visually + behaviorally) and flips to Update on the first
    // edit. Lets the user tap a note to skim, then tap ✓ to archive without ever
    // touching a separate Archive button.
    if (isEdit && window.innerWidth <= 768) {
      const _saveLabelEl = _saveBtnEl0.querySelector('.nft-label');
      const _isFinishedTodo = () => {
        if (currentType !== 'todo') return false;
        const rows = [...form.querySelectorAll('.note-cl-row')];
        const withText = rows.filter(row => (row.querySelector('.note-cl-text')?.value || '').trim());
        return withText.length > 0 && withText.every(row => row.classList.contains('done'));
      };
      const _enterArchive = () => {
        _saveBtnEl0.classList.add('archive-mode');
        const label = _isFinishedTodo() ? 'Finish' : 'Archive';
        if (_saveLabelEl) _saveLabelEl.textContent = label;
        _saveBtnEl0.title = label;
      };
      const _enterUpdate = () => {
        if (!_saveBtnEl0.classList.contains('archive-mode')) return;
        _saveBtnEl0.classList.remove('archive-mode');
        if (_saveLabelEl) _saveLabelEl.textContent = 'Update';
        _saveBtnEl0.title = 'Update';
      };
      _enterArchive();
      form.addEventListener('input', _enterUpdate, true);
      form.addEventListener('change', _enterUpdate, true);
      // Checklist dots use a custom control and dispatch their update before
      // this click bubbles. Re-evaluate afterward so a fully completed todo
      // offers Finish (which archives/removes it from the active list).
      form.addEventListener('click', (event) => {
        if (!event.target.closest('.note-cl-dot')) return;
        setTimeout(() => {
          if (_isFinishedTodo()) _enterArchive();
          else _enterUpdate();
        }, 0);
      }, true);
    }

    // Cancel
    form.querySelector('.note-form-cancel').addEventListener('click', () => { form._flushDraft?.(); app.setEditingId(null); _renderNotes(); });

    // Archive / Delete : edit-mode-only buttons, mirror the (now-hidden) card actions.
    form.querySelector('.note-form-archive-btn')?.addEventListener('click', () => {
      if (!isEdit) return;
      _archiveNoteById(note.id);
    });
    form.querySelector('.note-form-delete-btn')?.addEventListener('click', () => {
      if (!isEdit) return;
      return deleteFromEditor(note.id);
    });

    // Autosave a draft to persistence on every change so unsaved edits
    // survive connection loss / reload / accidental close.
    _wireDraftAutosave(form, isEdit ? note.id : '__new__');

    return form;
  }

  return { _buildForm };
}

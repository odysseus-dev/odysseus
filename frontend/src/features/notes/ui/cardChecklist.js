/** Card checklist editing and Today-step interactions. */
export function createCardChecklist({
  checklistOperations,
  _openNoteWindow,
  _renderNotes,
  app,
  spawnConfetti,
  state
}) {
  const EDIT_ICON = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  const REMOVE_ICON = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const revisionOf = id => state.notes.find(note => note.id === id)?.body_revision;

  // The card body is rendered markdown. Give each interactive task row the
  // card checklist classes and hover buttons, so the existing card styles,
  // mobile rules and handlers below apply to it unchanged. data-idx is the
  // renderer's task index, which is the index the toggle endpoint flips.
  function _decorateCardTasks(body) {
    body.querySelectorAll('.note-card[data-note-id] li.md-task[data-task-index]').forEach(li => {
      const noteId = li.closest('.note-card').dataset.noteId;
      const idx = li.dataset.taskIndex;
      li.classList.add('note-checkbox');
      li.classList.toggle('done', li.dataset.done === 'true');
      li.dataset.noteId = noteId;
      li.dataset.idx = idx;
      li.style.paddingLeft = `${Math.min(parseInt(li.dataset.indent, 10) || 0, 3) * 16}px`;
      li.querySelector('.md-task-box')?.classList.add('note-check-dot');
      li.querySelector('.md-task-text')?.classList.add('note-check-text');
      li.insertAdjacentHTML('beforeend',
        `<button class="note-checkbox-edit" data-note-id="${noteId}" data-idx="${idx}" title="Edit item">${EDIT_ICON}</button>` +
        `<button class="note-checkbox-rm" data-note-id="${noteId}" data-idx="${idx}" title="Delete item">${REMOVE_ICON}</button>`);
    });
    body.querySelectorAll('.note-card .md-note-image').forEach(img => {
      img.classList.add('note-card-image');
      img.draggable = false;
    });
  }

  function _wireCardChecklist(body) {
    _decorateCardTasks(body);
    // Remove a single checklist item (hover X)
    body.querySelectorAll('.note-checkbox-rm').forEach(btn => {
      const revision = revisionOf(btn.dataset.noteId);
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.selectMode) return;
        const noteId = btn.dataset.noteId;
        const idx = parseInt(btn.dataset.idx);
        checklistOperations.removeItem(noteId, idx, revision);
      });
    });

    function _startChecklistItemEdit(noteId, idx, span, revision) {
      if (span.isContentEditable) return;
      const note = state.notes.find(n => n.id === noteId);
      if (!note || !Array.isArray(note.items) || !note.items[idx]) return;
      if (note.body_revision !== revision) { _renderNotes(); return; }

      span.textContent = note.items[idx].text || '';
      span.contentEditable = "true";
      span.spellcheck = false;
      span.focus();

      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(span);
      selection.removeAllRanges();
      selection.addRange(range);

      const save = () => {
        if (!span.isContentEditable) return;
        span.contentEditable = "false";
        const newText = span.textContent.trim();
        if (note.body_revision === revision) checklistOperations.editItem(note, idx, newText);
        else _renderNotes();
      };

      const onKeydown = (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          save();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          span.contentEditable = "false";
          _renderNotes();
        }
      };

      span.addEventListener('blur', save, { once: true });
      span.addEventListener('keydown', onKeydown);
    }

    // Edit a single checklist item (hover Edit button)
    body.querySelectorAll('.note-checkbox-edit').forEach(btn => {
      const revision = revisionOf(btn.dataset.noteId);
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.selectMode) return;
        const noteId = btn.dataset.noteId;
        const idx = parseInt(btn.dataset.idx);
        const span = btn.parentElement.querySelector('.note-check-text');
        if (span) _startChecklistItemEdit(noteId, idx, span, revision);
      });
    });

    // Prevent clicks from toggling the row while actively editing inline
    body.querySelectorAll('.note-check-text').forEach(span => {
      span.addEventListener('click', (e) => {
        if (span.isContentEditable) {
          e.stopPropagation();
        }
      });
    });

    // Quick-add new checklist item (hover input at bottom of todo cards)
    body.querySelectorAll('.note-cl-quickadd-input').forEach(input => {
      input.addEventListener('click', (e) => e.stopPropagation());
      input.addEventListener('keydown', async (e) => {
        e.stopPropagation();
        if (e.key !== 'Enter') return;
        e.preventDefault();
        const text = input.value.trim();
        if (!text) return;
        const noteId = input.dataset.noteId;
        input.disabled = true;
        try {
          await checklistOperations.addItem(noteId, text, () => { input.value = ''; });
        } finally {
          input.disabled = false;
        }
        // Refocus the input on the same card
        setTimeout(() => {
          const next = document.querySelector(`.note-cl-quickadd-input[data-note-id="${noteId}"]`);
          if (next) {
            if (!next.value) next.value = input.value;
            next.focus();
          }
        }, 0);
      });
    });

    // Checkboxes are disabled in select mode.
    body.querySelectorAll('.note-checkbox').forEach(el => {
      const revision = revisionOf(el.dataset.noteId);
      el.addEventListener('click', (e) => {
        if (e.target.closest('a')) return;
        if (state.selectMode) return; // let card-level handler take over
        e.preventDefault();
        e.stopPropagation();
        const noteId = el.dataset.noteId;
        const idx = parseInt(el.dataset.idx);
        checklistOperations.toggleItem(noteId, idx, done => {
          el.classList.toggle('done', done);
          el.querySelector('.md-task-box')?.setAttribute('aria-checked', String(done));
        }, () => {
          const card = el.closest('.note-card');
          if (card) { const r = card.getBoundingClientRect(); spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60); }
        }, revision);
      });
    });
    // Keyboard: the rendered box is a focusable role=checkbox.
    body.querySelectorAll('.note-card .md-task-box').forEach(box => {
      box.addEventListener('keydown', (e) => {
        if (e.key !== ' ' && e.key !== 'Enter') return;
        e.preventDefault();
        box.closest('.note-checkbox')?.click();
      });
    });


  }

  function _wireTodayView(body) {
    body.querySelectorAll('.notes-today-row .note-check-dot').forEach(dot => {
      const revision = revisionOf(dot.dataset.noteId);
      dot.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = dot.dataset.noteId;
        const idx = parseInt(dot.dataset.idx);
        const row = dot.closest('.notes-today-row');
        await checklistOperations.toggleTodayStep(id, idx, () => row?.classList.add('done'), () => {
          const r = (row || dot).getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60);
        }, revision);
      });
    });
    body.querySelectorAll('.notes-today-title').forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.noteId;
        if (!id) return;
        _openNoteWindow(id);
      });
    });
  }

  return { _wireCardChecklist, _wireTodayView };
}

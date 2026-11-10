/** Card checklist editing and Today-step interactions. */
export function createCardChecklist({
  checklistOperations,
  _editNote,
  _renderNotes,
  app,
  spawnConfetti,
  state
}) {
  function _wireCardChecklist(body) {
    // Remove a single checklist item (hover X)
    body.querySelectorAll('.note-checkbox-rm').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.selectMode) return;
        const noteId = btn.dataset.noteId;
        const idx = parseInt(btn.dataset.idx);
        checklistOperations.removeItem(noteId, idx);
      });
    });

    function _startChecklistItemEdit(noteId, idx, span) {
      if (span.isContentEditable) return;
      const note = state.notes.find(n => n.id === noteId);
      if (!note || !Array.isArray(note.items) || !note.items[idx]) return;

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
        checklistOperations.editItem(note, idx, newText);
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
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.selectMode) return;
        const noteId = btn.dataset.noteId;
        const idx = parseInt(btn.dataset.idx);
        const span = btn.parentElement.querySelector('.note-check-text');
        if (span) _startChecklistItemEdit(noteId, idx, span);
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
        const persist = checklistOperations.addItem(noteId, text, () => { input.value = ''; });
        if (!persist) return;
        // Refocus the input on the same card
        setTimeout(() => {
          const next = document.querySelector(`.note-cl-quickadd-input[data-note-id="${noteId}"]`);
          if (next) next.focus();
        }, 0);
        persist();
      });
    });

    // Checkboxes (dot toggle, optimistic) : disabled in select mode
    body.querySelectorAll('.note-checkbox').forEach(el => {
      el.addEventListener('click', (e) => {
        if (state.selectMode) return; // let card-level handler take over
        e.preventDefault();
        e.stopPropagation();
        const noteId = el.dataset.noteId;
        const idx = parseInt(el.dataset.idx);
        checklistOperations.toggleItem(noteId, idx, done => el.classList.toggle('done', done), () => {
          const card = el.closest('.note-card');
          if (card) { const r = card.getBoundingClientRect(); spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60); }
        });
      });
    });


  }

  function _wireTodayView(body) {
    body.querySelectorAll('.notes-today-row .note-check-dot').forEach(dot => {
      dot.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = dot.dataset.noteId;
        const idx = parseInt(dot.dataset.idx);
        const row = dot.closest('.notes-today-row');
        await checklistOperations.toggleTodayStep(id, idx, () => row?.classList.add('done'), () => {
          const r = (row || dot).getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 60);
        });
      });
    });
    body.querySelectorAll('.notes-today-title').forEach(el => {
      el.addEventListener('click', () => {
        const id = el.dataset.noteId;
        if (!id) return;
        // Drop the Today filter first so the regular card list is rendered;
        // _editNote needs to find a .note-card in the DOM to replace with
        // the editor form.
        app.setActiveFilter(null);
        _renderNotes();
        _editNote(id);
      });
    });
  }

  return { _wireCardChecklist, _wireTodayView };
}

/** Card taps, pin/color controls, menus and archive animation. */
import { copyText } from '/static/js/shared/platform/clipboard.js';
export function createCardInteractions({
  cardOperations,
  COLORS,
  _archiveNoteById,
  _bindLongPressDrag,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _isBgImage,
  _isNotesMobileMode,
  _openNoteWindow,
  _openNoteCornerMenu,
  _pickCustomBgImage,
  _renderNotes,
  _setReminderCardGlow,
  _smoothScrollToNoteCard,
  _updateBulkBar,
  _wireCardChecklist,
  _wireCardReorder,
  app,
  spawnConfetti,
  state,
  uiModule
}) {
  /** Wire the cards under `scope`: the whole grid, or one card re-rendered in place. */
  function _bindCardEvents(body, scope = body) {
    // Skip completed leading rows so the first unfinished checklist item is
    // visible when a note preview has its own scroll area.
    const previews = Array.from(scope.querySelectorAll('.note-checklist-preview'));
    requestAnimationFrame(() => {
      previews.forEach(preview => {
        const firstPending = preview.querySelector('.note-checkbox:not(.done)');
        if (firstPending) preview.scrollTop = Math.max(0, firstPending.offsetTop - 4);
      });
    });
    const tapToEditOrSelect = (cardEl) => {
      const id = cardEl.dataset.noteId;
      // A long-press enters select mode and re-renders the card. Ignore the
      // synthetic click that mobile browsers emit when that same touch ends.
      if (state.suppressNextNoteTapId === id) {
        app.setSuppressNextNoteTapId(null);
        return;
      }
      if (state.selectMode) {
        const cb = cardEl.querySelector('.note-card-cb');
        if (cb) {
          cb.checked = !cb.checked;
          cb.dispatchEvent(new Event('change'));
        }
      } else {
        // The note window is the only editor, on desktop and on mobile.
        _openNoteWindow(id);
      }
    };
    // Mobile: long-press anywhere on a note card → enter Select mode and
    // select that card. Cancelled by movement (so it doesn't interfere with
    // vertical scrolling) or by lifting the finger before the timer fires.
    if (_isNotesMobileMode()) {
      scope.querySelectorAll('.note-card:not(.note-card-shared-in)').forEach(card => _bindLongPressDrag(card));
    }
    scope.querySelectorAll('.note-card.note-card-reminder-fired-sticky').forEach(card => {
      card.addEventListener('click', () => _setReminderCardGlow(card.dataset.noteId, false), true);
    });
    // Click title : edit, or toggle select in select mode
    scope.querySelectorAll('.note-card-title[data-action="edit"]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        _smoothScrollToNoteCard(el.closest('.note-card'));
        tapToEditOrSelect(el.closest('.note-card'));
      });
      // The title is the card's keyboard handle: Enter or Space opens the note.
      el.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        e.stopPropagation();
        tapToEditOrSelect(el.closest('.note-card'));
      });
    });
    // Click content : edit, or toggle select in select mode
    scope.querySelectorAll('.note-content-preview').forEach(el => {
      el.addEventListener('click', (e) => {
        // Links in rendered markdown open on their own; checkbox rows stop the event first.
        if (e.target.closest('a, summary')) { e.stopPropagation(); return; }
        e.stopPropagation(); tapToEditOrSelect(el.closest('.note-card'));
      });
    });
    // The card's image thumbnail opens the note, like the title and the text.
    scope.querySelectorAll('.note-card .note-card-image').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        tapToEditOrSelect(el.closest('.note-card'));
      });
    });
    // Click empty area of checklist preview (not on checkbox/X) : edit
    scope.querySelectorAll('.note-checklist-preview').forEach(el => {
      el.addEventListener('wheel', (e) => {
        const atTop = el.scrollTop <= 0;
        const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
        if ((e.deltaY < 0 && atTop) || (e.deltaY > 0 && atBottom)) {
          const pane = el.closest('.notes-pane-body');
          if (pane) {
            pane.scrollTop += e.deltaY;
            e.preventDefault();
          }
        }
      }, { passive: false });
      el.addEventListener('click', (e) => {
        if (e.target.closest('a, summary')) { e.stopPropagation(); return; }
        const inertTask = e.target.closest('.note-card-readonly') && e.target.closest('.note-checkbox');
        if (e.target.closest('.note-checkbox, .note-checkbox-rm, .note-cl-quickadd, input') && !inertTask) return;
        e.stopPropagation();
        tapToEditOrSelect(el.closest('.note-card'));
      });
    });
    // Clicking todo item text now toggles its checkbox : let the click bubble
    // up to the parent .note-checkbox row handler. To open the editor, the
    // user clicks the pencil corner.
    // (No-op block kept as a marker : removing the listener entirely means
    // clicks naturally bubble to the row toggle below.)
    // In select mode, clicking anywhere on the card toggles selection
    if (state.selectMode) {
      scope.querySelectorAll('.note-card').forEach(card => {
        card.addEventListener('click', (e) => {
          if (e.target.closest('.note-card-cb, a')) return; // links open without toggling selection
          e.stopPropagation();
          tapToEditOrSelect(card);
        });
      });
    }
    // Mobile, non-select: tapping anywhere on the card body (not on an
    // interactive child : buttons, pin, checkbox, color dot, reminder pill,
    // agent tag, links) opens the fullscreen editor. Previously only the
    // title / content preview triggered edit, so padding + empty gutters were
    // dead zones that felt broken on mobile.
    if (_isNotesMobileMode() && !state.selectMode) {
      const _INTERACTIVE = 'button, a, input, label, .note-card-color-dot, .note-checkbox, .note-checkbox-rm, .note-cl-quickadd, .note-agent-tag, .note-card-pin, .note-card-corner-trash, .note-card-corner-menu, .note-card-corner-unarchive, .note-card-edit-corner, .note-card-reminder, .note-card-cb';
      scope.querySelectorAll('.note-card').forEach(card => {
        card.addEventListener('click', (e) => {
          // A view-only task row is not a checkbox: tapping it opens the note.
          const inertTask = card.classList.contains('note-card-readonly') && e.target.closest('.note-checkbox');
          if (e.target.closest(_INTERACTIVE) && !inertTask) return;
          e.stopPropagation();
          tapToEditOrSelect(card);
        });
      });
    }
    // Multi-select checkbox (only in select mode)
    scope.querySelectorAll('.note-card-cb').forEach(cb => {
      cb.addEventListener('click', (e) => e.stopPropagation());
      cb.addEventListener('change', () => {
        const id = cb.dataset.noteId;
        if (cb.checked) app.selectId(id);
        else app.deselectId(id);
        cb.closest('.note-card').classList.toggle('note-card-selected', cb.checked);
        _updateBulkBar();
      });
    });
    // Pin toggle (optimistic)
    scope.querySelectorAll('.note-card-pin').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        cardOperations.togglePin(id);
      });
    });
    // Color picker
    const _applyCardColor = async (card, id, newColor) => {
      const isBg = _isBgImage(newColor);
      COLORS.forEach(c => { if (c.value && c.value !== 'custom') card.classList.remove('note-color-' + c.value); });
      if (newColor && !isBg && COLORS.some(c => c.value === newColor && c.value !== 'custom')) card.classList.add('note-color-' + newColor);
      if (isBg && _customColorStyle(newColor)) card.setAttribute('style', _customColorStyle(newColor));
      else card.removeAttribute('style');
      card.querySelectorAll('.note-card-color-dot').forEach(d => {
        d.classList.toggle('active', _dotIsActive(d.dataset.color, newColor));
        d.style.background = _dotBg(d.dataset.color, newColor);
      });
      await cardOperations.updateColor(id, newColor);
    };
    scope.querySelectorAll('.note-card-color-dot').forEach(dot => {
      dot.addEventListener('click', (e) => {
        e.stopPropagation();
        const card = dot.closest('.note-card');
        const id = card.dataset.noteId;
        if (dot.dataset.color === 'custom') {
          _pickCustomBgImage().then(url => { if (url) _applyCardColor(card, id, 'bg:' + url); });
          return;
        }
        _applyCardColor(card, id, dot.dataset.color);
      });
    });
    // Plain pencil corner → open the note window. The unarchive corner shares
    // the .note-card-edit-corner class for styling, so :not() keeps the edit
    // handler off it.
    scope.querySelectorAll('.note-card-edit-corner:not(.note-card-unarchive-corner)').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        if (id) _openNoteWindow(id);
      });
    });
    // Copy corner : bottom-right, just left of the Done check. Shared with
     // the Ctrl/Cmd+C shortcut wired further down so both code paths run the
     // same serializer + feedback flash.
    // ⋯ corner menu : Copy + Agent (solve-this-todo).
    scope.querySelectorAll('.note-card-corner-menu').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        _openNoteCornerMenu(btn);
      });
    });
    scope.querySelectorAll('.note-card-label-chip').forEach(chip => {
      chip.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const label = chip.dataset.noteLabelFilter;
        if (!label) return;
        if (state.activeLabel === label && state.activeFilter === null) {
          app.setActiveLabel(null);
        } else {
          app.setActiveFilter(null);
          app.setActiveLabel(label);
        }
        _renderNotes();
      });
    });
    // Done (✓) at bottom-right : only visible on hover for active notes.
    scope.querySelectorAll('.note-card-done').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        const card = btn.closest('.note-card');
        const idx = state.notes.findIndex(n => n.id === id);
        if (idx < 0) return;
        // Celebrate completion : same confetti shower the bulk-archive uses.
        if (card) {
          const r = card.getBoundingClientRect();
          spawnConfetti(r.left + r.width / 2, r.top + r.height / 2, 80);
        }
        const finish = cardOperations.stageArchive(id);
        if (!finish) return;
        if (card) {
          card.classList.add('note-card-sliding-out');
          let done = false;
          const once = () => { if (done) return; done = true; finish(); };
          card.addEventListener('transitionend', once, { once: true });
          setTimeout(once, 400);
        } else {
          finish();
        }
      });
    });
    // Unarchive corner : only visible in archive view.
    scope.querySelectorAll('.note-card-corner-unarchive').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        cardOperations.unarchive(id);
      });
    });
    // Trash corner : archive view only. Permanent delete, no confirmation.
    scope.querySelectorAll('.note-card-corner-trash').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        cardOperations.deleteOptimistically(id, true);
      });
    });

    scope.querySelectorAll('.note-card-archive').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        if (!id) return;
        const card = btn.closest('.note-card');
        let done = false;
        const finishRemove = () => {
          if (done) return;
          done = true;
          _archiveNoteById(id, { card, celebrate: true });
        };
        if (card) {
          card.classList.add('note-card-sliding-out');
          card.addEventListener('transitionend', finishRemove, { once: true });
          setTimeout(finishRemove, 400);
        } else {
          finishRemove();
        }
      });
    });
    // Unarchive (optimistic) : only present in archive view
    scope.querySelectorAll('.note-card-unarchive').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        cardOperations.unarchive(id);
      });
    });
    // Delete (optimistic)
    scope.querySelectorAll('.note-card-delete, .note-card-x').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = btn.dataset.noteId;
        cardOperations.deleteOptimistically(id);
      });
    });
    // Copy entire checklist (title + items, markdown-style)
    scope.querySelectorAll('.note-card-copy').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        const id = btn.dataset.noteId;
        const note = state.notes.find(n => n.id === id);
        if (!note) return;
        const lines = [];
        if (note.title) lines.push(note.title);
        if (note.content) lines.push(note.content);
        if (lines.length) lines.push('');
        for (const it of (note.items || [])) {
          if (!it || !(it.text || '').trim()) continue;
          lines.push(`- [${it.done ? 'x' : ' '}] ${(it.text || '').trim()}`);
        }
        const text = lines.join('\n').trim();
        // copyText falls back to a hidden textarea when the async API is
        // blocked or the page is served over plain HTTP.
        if (await copyText(text)) uiModule.showToast?.(`Copied ${(note.items || []).filter(i => (i?.text || '').trim()).length} items`);
        else uiModule.showError?.('Copy failed');
      });
    });

    _wireCardChecklist(scope);
    _wireCardReorder(body, scope);

  }

  return { _bindCardEvents };
}

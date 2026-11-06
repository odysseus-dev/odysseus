/** Reminder badges, sticky card highlights and entry scrolling. */
import { _isNoteFullyDone } from '../domain/noteRules.js';
import { _hasTimeComponent } from '../domain/recurrence.js';
export function createReminderDisplay({
  _countFiredReminders,
  _hasActiveReminder,
  _loadActiveHighlights,
  _loadGlowedReminders,
  _loadPendingHighlights,
  _saveActiveHighlights,
  _saveGlowedReminders,
  _savePendingHighlights,
  _smoothScrollToNoteCard,
  state
}) {
  function _clearViewedReminderGlows() {
    const active = _loadActiveHighlights();
    if (!active.size) return;
    _saveActiveHighlights(new Set());
    document.querySelectorAll('.note-card-reminder-fired-sticky').forEach(card => {
      card.classList.remove('note-card-reminder-fired-sticky');
    });
  }

  function _setReminderCardGlow(noteId, on = true) {
    if (!noteId) return;
    const active = _loadActiveHighlights();
    if (on) active.add(noteId);
    else active.delete(noteId);
    _saveActiveHighlights(active);
    document.querySelectorAll(`.note-card[data-note-id="${noteId}"]`).forEach(card => {
      card.classList.toggle('note-card-reminder-fired-sticky', on);
    });
  }

  function _queuePendingHighlight(noteId) {
    const set = _loadPendingHighlights();
    set.add(noteId);
    _savePendingHighlights(set);
  }

  function _flushPendingHighlights() {
    // Fresh firings (queued by the background loop while the panel was closed)
    // glow unconditionally : a notification just told the user something
    // happened, so we always point at the note even if it was glowed before.
    const queued = _loadPendingHighlights();
    const glowed = _loadGlowedReminders();
    const toGlow = new Set(queued);
    // For notes that are merely overdue at open time (no fresh firing event),
    // only glow the ones we haven't already shown : otherwise reopening the
    // panel keeps lighting up old reminders forever.
    for (const n of state.notes) {
      if (!_hasActiveReminder(n) || !_hasTimeComponent(n.due_date)) continue;
      if (queued.has(n.id) || !glowed.has(n.id)) toGlow.add(n.id);
    }
    // Always consume the queue.
    _savePendingHighlights(new Set());
    if (!toGlow.size) return;
    let firstCard = null;
    for (const id of toGlow) {
      const card = document.querySelector(`.note-card[data-note-id="${id}"]`);
      if (!card) continue;
      _setReminderCardGlow(id, true);
      if (!firstCard) firstCard = card;
      glowed.add(id);
    }
    _saveGlowedReminders(glowed);
    // Bring the first one into view so it can't get buried below the fold.
    if (firstCard) {
      requestAnimationFrame(() => {
        _smoothScrollToNoteCard(firstCard);
      });
    }
  }

  function _updateRailBadge() {
    const fired = _countFiredReminders();
    // Rail (mini sidebar) : only show the count when reminders have ACTUALLY
    // fired since the last dismissal (i.e. you haven't opened notes yet).
    // Showing every overdue note forever made the badge feel permanent.
    const railBtn = document.getElementById('rail-notes');
    if (railBtn) {
      let badge = railBtn.querySelector('.rail-notes-badge');
      if (fired > 0) {
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'rail-notes-badge';
          railBtn.appendChild(badge);
        }
        badge.textContent = fired > 99 ? '99+' : String(fired);
        badge.classList.add('fired');
      } else if (badge) {
        badge.remove();
      }
    }
    // Main sidebar button
    const sidebarBtn = document.getElementById('tool-notes-btn');
    if (sidebarBtn) {
      let dot = sidebarBtn.querySelector('.tool-notes-dot');
      if (fired > 0) {
        if (!dot) {
          dot = document.createElement('span');
          dot.className = 'tool-notes-dot';
          const newNoteBtn = sidebarBtn.querySelector('#notes-new-note-btn');
          sidebarBtn.insertBefore(dot, newNoteBtn || null);
        }
      } else if (dot) {
        dot.remove();
      }
    }
    // Individual note cards : pulse ones with fired reminders
    document.querySelectorAll('.note-card').forEach(card => {
      const id = card.dataset.noteId;
      const note = state.notes.find(n => n.id === id);
      if (!note || note.archived || _isNoteFullyDone(note)) {
        card.classList.remove('note-card-reminder-due');
        return;
      }
      if (note.due_date && _hasTimeComponent(note.due_date)) {
        const t = new Date(note.due_date).getTime();
        card.classList.toggle('note-card-reminder-due', !isNaN(t) && t <= Date.now());
      } else {
        card.classList.remove('note-card-reminder-due');
      }
    });
  }

  return { _clearViewedReminderGlows, _setReminderCardGlow, _queuePendingHighlight, _flushPendingHighlights, _updateRailBadge };
}

/** Page-owned reminder scheduler and notification delivery workflow. */
import { _hasTimeComponent, _isDueTodayOrOverdue, _advanceRecurring } from '../domain/recurrence.js';
import { reminderBody } from '../domain/noteRules.js';
export function createNotesReminders({
  app,
  api,
  persistence,
  notifications,
  clock,
  scheduler,
  effects,
  fetchNotes,
  isNoteFullyDone,
  dismissedKey
}) {
  const state = app.state;
  const { _loadFiredReminders, _saveFiredReminders } = persistence;
  const _patchNote = api.patchNote;
  const _fetchNotes = fetchNotes;
  const _isNoteFullyDone = isNoteFullyDone;
  const REMINDER_DISMISSED_AT_KEY = dismissedKey;
  const _updateRailBadge = effects.badge;
  let _reminderTimer = null;
  let startupTimer = null;
  let started = false;
  let disposed = false;
  function _checkReminders() {
    if (!state.notes.length) return;
    const now = clock.now();
    const fired = _loadFiredReminders();
    let changed = false;
    for (const note of state.notes) {
      if (!note.due_date || note.archived) continue;
      if (!_hasTimeComponent(note.due_date)) continue;
      if (fired.has(note.id)) continue;
      const due = new Date(note.due_date).getTime();
      if (isNaN(due)) continue;
      if (due <= now && due > now - 60000) {
        _fireReminder(note);
        // Recurring? advance the due_date instead of marking as fired
        if (note.repeat && note.repeat !== 'none') {
          const next = _advanceRecurring(note.due_date, note.repeat, clock);
          if (next) {
            app.mergeNote(note, { due_date: next });
            _patchNote(note.id, { due_date: next }).catch(() => {});
            // Don't add to fired : new due_date is in the future
            continue;
          }
        }
        fired.add(note.id);
        changed = true;
      } else if (due <= now - 60000) {
        // Past, never seen : silently advance recurring or mark fired
        if (note.repeat && note.repeat !== 'none') {
          const next = _advanceRecurring(note.due_date, note.repeat, clock);
          if (next) {
            app.mergeNote(note, { due_date: next });
            _patchNote(note.id, { due_date: next }).catch(() => {});
            continue;
          }
        }
        fired.add(note.id);
        changed = true;
      }
    }
    if (changed) _saveFiredReminders(fired);
    // Always refresh badge : fired state may have changed visually without note mutation
    _updateRailBadge();
  }

  function _fireReminder(note) {
    const title = note.title || 'Note reminder';
    // The note's prose and open tasks as plain text, by the same rules the
    // server's reminder builder uses, so the local notification and the
    // email/ntfy one say the same thing.
    const rawBody = reminderBody(note.content);

    // Ask the server to dispatch according to user settings. The server may
    // return an LLM-written synthesis line and/or send an email. We still show
    // a local browser notification so the user gets immediate feedback even if
    // the server path is disabled or slow.
    const showLocal = body => notifications.show(title, body, note.id);

    // Fire-and-forget server dispatch. If synthesis comes back quickly enough,
    // use it as the notification body; otherwise the local notification has
    // already shown with the raw body.
    let shown = false;
    const timer = scheduler.setTimeout(() => { if (!shown) { shown = true; showLocal(rawBody); } }, 1500);

    api.fireReminder({ note_id: note.id, title, body: rawBody })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        scheduler.clearTimeout(timer);
        if (shown) return;
        shown = true;
        const body = (data && data.synthesis) ? data.synthesis : rawBody;
        showLocal(body);
      })
      .catch(() => {
        scheduler.clearTimeout(timer);
        if (!shown) { shown = true; showLocal(rawBody); }
      });

    // Pulse the card if visible; otherwise queue it so the next time the user
    // opens the notes panel the card gets a brief glow.
    effects.highlight(note.id);
  }

  function _startReminderLoop() {
    if (disposed || _reminderTimer) return;
    _reminderTimer = scheduler.setInterval(_checkReminders, 30000);
    _checkReminders(); // run once immediately
  }

  function _countDueReminders() {
    return state.notes.filter(n => !n.archived && _isDueTodayOrOverdue(n.due_date, clock) && !_isNoteFullyDone(n)).length;
  }

  function _countFiredReminders() {
    // Reminders whose time has actually passed (not just date-today),
    // and which fired after the last user dismissal.
    const now = clock.now();
    return state.notes.filter(n => {
      if (n.archived || _isNoteFullyDone(n)) return false;
      if (!n.due_date || !_hasTimeComponent(n.due_date)) return false;
      const t = new Date(n.due_date).getTime();
      if (isNaN(t) || t > now) return false;
      return t > state.firedDotDismissedAt;
    }).length;
  }

  function dismissFiredReminderDot() {
    app.setFiredDotDismissedAt(clock.now());
    try { persistence.setItem(REMINDER_DISMISSED_AT_KEY, String(state.firedDotDismissedAt)); } catch {}
    _updateRailBadge();
  }

  async function refreshDueBadge(opts = {}) {
    // Usually lightweight, but callers that just created a note reminder can
    // force a refresh so the background reminder loop sees it immediately.
    if (opts.force || state.notes.length === 0) {
      try {
        const wasArchived = state.showingArchived;
        app.setShowingArchived(false);
        await _fetchNotes();
        app.setShowingArchived(wasArchived);
      } catch {}
    }
    _updateRailBadge();
  }

  // An open panel showing active notes owns the list and redraws it; swapping
  // it here without a redraw left cards that no longer matched it, so a note
  // deleted elsewhere kept its card and the 20 s re-read saw nothing new.
  const panelOwnsList = () => state.open && !state.showingArchived;

  async function _initReminders() {
    try {
      if (panelOwnsList()) { _startReminderLoop(); return; }
      const res = await api.listNotes(false);
      if (res.ok && !disposed) {
        const data = await res.json();
        if (!panelOwnsList()) app.replaceNotes(data.notes || data || []);
        _startReminderLoop();
      }
    } catch {}
  }

  function start() { if (started || disposed) return; started = true; startupTimer = scheduler.setTimeout(_initReminders, 3000); }
  function dispose() { disposed = true; scheduler.clearTimeout(startupTimer); scheduler.clearInterval(_reminderTimer); _reminderTimer = null; }
  return { start, dispose, _checkReminders, _fireReminder, _startReminderLoop, _countDueReminders, _countFiredReminders, dismissFiredReminderDot, refreshDueBadge, _initReminders };
}

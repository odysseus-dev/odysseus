/** Application-owned filtering, searching and note ordering. */
import { _noteTags, _visibleNoteTags, _nextGoalStep, _isCalendarReminderNote } from '../domain/noteRules.js';
import { _hasTimeComponent } from '../domain/recurrence.js';
export function createQueries({
  _hasActiveReminder,
  state
}) {
  function _sortedVisibleNotes() {
    const displayNotes = _userVisibleNotes();
    let filtered = state.activeLabel ? displayNotes.filter(n => _noteTags(n).includes(state.activeLabel)) : displayNotes;
    if (state.activeFilter === 'reminders') {
      filtered = filtered.filter(n => n.due_date && _hasTimeComponent(n.due_date));
    } else if (state.activeFilter === 'no-reminders') {
      filtered = filtered.filter(n => !(n.due_date && _hasTimeComponent(n.due_date)));
    } else if (state.activeFilter === 'default') {
      filtered = filtered.filter(n => _visibleNoteTags(n).length === 0);
    } else if (state.activeFilter === 'goals') {
      filtered = filtered.filter(n => n.note_type === 'goal' && !n.archived);
    } else if (state.activeFilter === 'today') {
      // Today view: only goals that still have an unchecked step.
      filtered = filtered.filter(n => n.note_type === 'goal' && !n.archived && _nextGoalStep(n));
    }
    if (state.searchQuery) {
      filtered = filtered.filter(n => {
        const q = state.searchQuery;
        if ((n.title || '').toLowerCase().includes(q)) return true;
        if ((n.content || '').toLowerCase().includes(q)) return true;
        if ((n.label || '').toLowerCase().includes(q)) return true;
        if (Array.isArray(n.items) && n.items.some(it => (it.text || '').toLowerCase().includes(q))) return true;
        return false;
      });
    }
    const sorted = [...filtered].sort((a, b) => {
      // In reminders view: sort by due date ascending (soonest first)
      if (state.activeFilter === 'reminders') {
        const da = new Date(a.due_date || 0).getTime();
        const db = new Date(b.due_date || 0).getTime();
        return da - db;
      }
      // Archived view: newest archived first (ignore manual sort_order).
      if (state.showingArchived) {
        return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
      }
      if (a.pinned && !b.pinned) return -1;
      if (!a.pinned && b.pinned) return 1;
      // Active reminders (due date in the past, not done/archived) rank
      // immediately under the pinned block.
      const aActive = _hasActiveReminder(a);
      const bActive = _hasActiveReminder(b);
      if (aActive && !bActive) return -1;
      if (!aActive && bActive) return 1;
      const so = (a.sort_order || 0) - (b.sort_order || 0);
      if (so !== 0) return so;
      return new Date(b.updated_at || 0) - new Date(a.updated_at || 0);
    });


  return sorted;
  }

  function _userVisibleNotes() {
    return state.notes.filter(n => !_isCalendarReminderNote(n));
  }

  return { _sortedVisibleNotes, _userVisibleNotes };
}

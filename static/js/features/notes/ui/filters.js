/** Label chips, reminder/goal filters and collapsed tag-strip rendering. */
import { _visibleNoteTags, _nextGoalStep } from '../domain/noteRules.js';
import { _hasTimeComponent } from '../domain/recurrence.js';
export function createFilters({
  NOTES_LABELS_COLLAPSED_KEY,
  _clearPastReminders,
  _esc,
  _isPastReminder,
  _renderNotes,
  _userVisibleNotes,
  app,
  persistence,
  state
}) {
  function _renderLabels(root = document) {
    const bar = root.querySelector?.('.notes-labels-bar') || document.querySelector('.notes-labels-bar');
    if (!bar) return;
    if (state.labelsCollapsed) {
      bar.innerHTML = '';
      bar.style.display = 'none';
      return;
    }
    const visibleNotes = _userVisibleNotes();
    const labels = new Set();
    for (const n of visibleNotes) for (const t of _visibleNoteTags(n)) labels.add(t);
    const sortedLabels = [...labels].sort();
    // Count active reminders (not archived, has datetime due_date)
    const reminderCount = visibleNotes.filter(n => !n.archived && n.due_date && _hasTimeComponent(n.due_date)).length;
    const pastReminderCount = visibleNotes.filter(n => !n.archived && _isPastReminder(n)).length;
    const defaultCount = visibleNotes.filter(n => !n.archived && _visibleNoteTags(n).length === 0).length;
    // Active goals = non-archived goal notes. Today view lists pending steps
    // from each, so we surface the count next to the chip.
    const goalCount = visibleNotes.filter(n => n.note_type === 'goal' && !n.archived).length;
    const todayCount = visibleNotes.filter(n => n.note_type === 'goal' && !n.archived && _nextGoalStep(n)).length;
    const hasUsefulFilters = sortedLabels.length > 0 || reminderCount > 0 || goalCount > 0;
    const hasActiveFilter = state.activeLabel !== null || state.activeFilter !== null;
    if (!hasUsefulFilters && !hasActiveFilter) {
      bar.innerHTML = '';
      bar.style.display = 'none';
      return;
    }
    bar.style.display = '';
    const allActive = state.activeLabel === null && state.activeFilter === null;
    let html = `<button class="notes-label-chip${allActive ? ' active' : ''}" data-action="all">All</button>`;
    html += `<button class="notes-label-chip${state.activeFilter === 'default' ? ' active' : ''}" data-action="default" title="Show notes without tags">Default <span class="notes-label-chip-count">${defaultCount}</span></button>`;
    if (todayCount > 0) {
      const isOn = state.activeFilter === 'today';
      const icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
      html += `<button class="notes-label-chip notes-label-chip-today${isOn ? ' active' : ''}" data-action="today" title="Next step from every goal">${icon}Today <span class="notes-label-chip-count">${todayCount}</span></button>`;
    }
    if (goalCount > 0) {
      const isOn = state.activeFilter === 'goals';
      const icon = '<svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:-1px;margin-right:2px"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>';
      html += `<button class="notes-label-chip notes-label-chip-goals${isOn ? ' active' : ''}" data-action="goals" title="Show only goals">${icon}Goals <span class="notes-label-chip-count">${goalCount}</span></button>`;
    }
    const isReminderOn = state.activeFilter === 'reminders';
    const isReminderOff = state.activeFilter === 'no-reminders';
    const reminderCls = `notes-label-chip notes-label-chip-reminders${isReminderOn ? ' active' : ''}${isReminderOff ? ' active negated' : ''}`;
    const reminderIcon = isReminderOff
      // bell-off icon
      ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><path d="M13.73 21a2 2 0 0 1-3.46 0"/><path d="M18.63 13A17.89 17.89 0 0 1 18 8"/><path d="M6.26 6.26A5.86 5.86 0 0 0 6 8c0 7-3 9-3 9h14"/><path d="M18 8a6 6 0 0 0-9.33-5"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
      : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:2px"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
    html += `<button class="${reminderCls}" data-action="reminders" title="${isReminderOn ? 'Showing only reminders — click to show all' : isReminderOff ? 'Hiding reminders — click to show only reminders' : 'Click to filter reminders'}">${reminderIcon}Reminders <span class="notes-label-chip-count">${reminderCount}</span></button>`;
    const showingReminders = state.activeFilter === 'reminders';
    if (showingReminders && pastReminderCount > 0) {
      html += `<button class="notes-label-chip notes-label-clear-past" data-action="clear-past-reminders" title="Delete reminders whose time has passed"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>Clear past <span class="notes-label-chip-count">${pastReminderCount}</span></button>`;
    }
    for (const lbl of sortedLabels) {
      html += `<button class="notes-label-chip${state.activeLabel === lbl ? ' active' : ''}" data-label="${_esc(lbl)}">#${_esc(lbl)}</button>`;
    }
    bar.innerHTML = html;
    bar.querySelectorAll('.notes-label-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        if (chip.dataset.action === 'all') {
          app.setActiveLabel(null);
          app.setActiveFilter(null);
        } else if (chip.dataset.action === 'today') {
          app.setActiveLabel(null);
          app.setActiveFilter((state.activeFilter === 'today') ? null : 'today');
        } else if (chip.dataset.action === 'goals') {
          app.setActiveLabel(null);
          app.setActiveFilter((state.activeFilter === 'goals') ? null : 'goals');
        } else if (chip.dataset.action === 'default') {
          app.setActiveLabel(null);
          app.setActiveFilter((state.activeFilter === 'default') ? null : 'default');
        } else if (chip.dataset.action === 'reminders') {
          app.setActiveLabel(null);
          // Cycle: null → reminders → null → no-reminders → null → reminders → ...
          if (state.activeFilter === null) {
            app.setActiveFilter(state.reminderChipNext);
            app.setReminderChipNext((state.reminderChipNext === 'reminders') ? 'no-reminders' : 'reminders');
          } else {
            app.setActiveFilter(null);
          }
        } else if (chip.dataset.action === 'clear-past-reminders') {
          _clearPastReminders();
          return;
        } else {
          app.setActiveFilter(null);
          app.setActiveLabel(chip.dataset.label || null);
        }
        _renderNotes();
      });
    });
  }

  function _renderLabelsInto(_body) {
    if (!_body) return;
    const pane = _body.closest('.notes-pane');
    const toggleHost = pane?.querySelector('.notes-search-bar') || _body;
    let toggle = toggleHost.querySelector(':scope > .notes-label-toggle');
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'notes-label-toggle cal-filter-toggle';
      toggle.addEventListener('click', () => {
        app.setLabelsCollapsed(!state.labelsCollapsed);
        try { persistence.setItem(NOTES_LABELS_COLLAPSED_KEY, state.labelsCollapsed ? '1' : '0'); } catch {}
        _renderNotes();
      });
      const selectButton = toggleHost.querySelector('#notes-select-btn');
      toggleHost.insertBefore(toggle, selectButton || null);
    }
    toggle.textContent = state.labelsCollapsed ? '+ Tags' : '− Tags';
    toggle.title = state.labelsCollapsed ? 'Show tags' : 'Hide tags';
    toggle.setAttribute('aria-expanded', state.labelsCollapsed ? 'false' : 'true');

    // The shared chip scroller wraps this row once it is mounted.
    let bar = _body.querySelector('.notes-labels-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'notes-labels-bar';
      _body.appendChild(bar);
    }
    _renderLabels(_body);
  }

  return { _renderLabels, _renderLabelsInto };
}

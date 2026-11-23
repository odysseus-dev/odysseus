/** Render note cards from application queries and read-only state. */
import { _nextGoalStep, _goalProgress, _hasItems, _standardVisualVariant, _visibleNoteTags } from '../domain/noteRules.js';
import { _hasTimeComponent, _formatRepeatLabel } from '../domain/recurrence.js';
/** The empty-list line while a search is active: says the search found
 *  nothing, rather than suggesting the account has no notes. */
export function emptySearchText(query) {
  return `No notes match “${query}”`;
}

export function createCardRendering({
  COLORS,
  COLOR_HEX,
  _animateReflow,
  _applyMasonry,
  _bindCardEvents,
  _captureCardPositions,
  _customColorStyle,
  _dotBg,
  _dotIsActive,
  _esc,
  _formatDueDate,
  _formatReminderTag,
  _hasActiveReminder,
  _isBgImage,
  _isDueOverdue,
  _isNotesMobileMode,
  _linkify,
  _loadActiveHighlights,
  _renderLabelsInto,
  _renderNoteMarkdown,
  _renderQuickAdd,
  _sortedVisibleNotes,
  _updateRailBadge,
  _wireTodayView,
  state,
  uiModule
}) {
  function _renderLoadingSkeleton() {
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body) return;
    body.innerHTML = '';
    _renderLabelsInto(body);
    _renderQuickAdd(body);
    const skel = document.createElement('div');
    skel.className = 'notes-skeleton';
    skel.innerHTML = `
    <div class="notes-skeleton-card"></div>
    <div class="notes-skeleton-card"></div>
    <div class="notes-skeleton-card short"></div>
    <div class="notes-skeleton-card"></div>
  `;
    body.appendChild(skel);
  }

  function _renderNotes() {
    _updateRailBadge();
    const body = document.querySelector('#notes-pane .notes-pane-body');
    if (!body) return;
    const prevPositions = _captureCardPositions();
    const activeReminderHighlights = _loadActiveHighlights();

    const sorted = _sortedVisibleNotes();

    let html = '';
    // Today view: render a compact card listing the next-unchecked step from
    // each active goal. Tapping a step toggles it done (same idx-based wiring
    // as regular checkboxes). Tapping the title opens the goal note for full
    // editing.
    if (state.activeFilter === 'today') {
      body.innerHTML = '';
      _renderLabelsInto(body);
      _renderQuickAdd(body);
      if (sorted.length === 0) {
        body.insertAdjacentHTML('beforeend', `<div class="notes-empty">All caught up — no pending goal steps right now.</div>`);
      } else {
        let todayHtml = `<div class="notes-today-wrap">
        <div class="notes-today-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span>Today &middot; one step per goal</span>
        </div>
        <div class="notes-today-list">`;
        for (const note of sorted) {
          const next = _nextGoalStep(note);
          if (!next) continue;
          const progress = _goalProgress(note).trim();
          todayHtml += `<div class="notes-today-row" data-note-id="${note.id}">
          <span class="note-check-dot" data-note-id="${note.id}" data-idx="${next.idx}" title="Mark step done"></span>
          <div class="notes-today-text">
            <div class="notes-today-title" data-action="edit" data-note-id="${note.id}">${_esc(note.title || '(untitled goal)')}</div>
            <div class="notes-today-step">${_linkify(next.item.text || '')}</div>
          </div>
          <span class="notes-today-progress">${_esc(progress)}</span>
        </div>`;
        }
        todayHtml += `</div></div>`;
        body.insertAdjacentHTML('beforeend', todayHtml);
      }
      _wireTodayView(body);
      return;
    }
    for (const note of sorted) {
      const borderColor = COLOR_HEX[note.color || ''] || 'var(--border)';
      const dueFmt = _formatDueDate(note.due_date);
      const overdue = _isDueOverdue(note.due_date);

      // Every body goes through the same markdown renderer: task lines become
      // checkboxes (wired in cardChecklist), uploads render inline, and CSS
      // clips the height. A pure task list keeps the scrolling checklist box.
      const source = note.content || '';
      const rendered = source.trim() ? _renderNoteMarkdown(source) : '';
      const contentHtml = !rendered
        ? ''
        : (note.note_type === 'checklist' || note.note_type === 'todo') && Array.isArray(note.items)
          ? `<div class="note-checklist-preview note-md-preview">${rendered}</div>`
          : `<div class="note-content-preview note-md-preview">${rendered}</div>`;

      const isBg = _isBgImage(note.color);
      // Only a palette name becomes a class, and only an upload a style: the
      // colour is the owner's, and this card may be a collaborator's.
      const cc = (note.color && !isBg && Object.hasOwn(COLOR_HEX, note.color)) ? ' note-color-' + note.color : '';
      const bgStyle = isBg ? _customColorStyle(note.color) : '';
      const cardStyle = bgStyle ? ` style="${_esc(bgStyle)}"` : '';
      const isStandard = !note.note_type || note.note_type === 'note';
      const visualClass = isStandard && !note.color ? ` note-standard note-standard-v${_standardVisualVariant(note)}` : '';
      const sel = state.selectedIds.has(note.id) ? ' note-card-selected' : '';
      const reminderTagHtml = note.due_date && _hasTimeComponent(note.due_date)
        ? `<div class="note-card-reminder${overdue ? ' overdue' : ''}">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
          <span>${_esc(_formatReminderTag(note.due_date))}${note.repeat && note.repeat !== 'none' ? ' · ' + _esc(_formatRepeatLabel(note.repeat, new Date(note.due_date))) : ''}</span>
        </div>`
        : '';
      const noteTags = _visibleNoteTags(note);
      const dueBadge = dueFmt && !_hasTimeComponent(note.due_date) ? `<span class="note-due-inline${overdue ? ' note-due-overdue' : ''}">${dueFmt}</span>` : '';
      const colorDots = COLORS.map(c => `<span class="note-card-color-dot${_dotIsActive(c.value, note.color) ? ' active' : ''}" data-color="${c.value}" style="background:${_dotBg(c.value, note.color)}" title="${c.name || 'default'}"></span>`).join('');
      const goalClass = note.note_type === 'goal' ? ' note-card-goal' : '';
      const reminderGlowClass = activeReminderHighlights.has(note.id) && _hasActiveReminder(note) ? ' note-card-reminder-fired-sticky' : '';
      const goalPill = note.note_type === 'goal'
        ? `<span class="note-goal-pill" title="AI-broken-down goal">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z"/></svg>
          Goal${_goalProgress(note)}
        </span>`
        : '';
      const drawingClass = note.note_type === 'draw' ? ' note-card-drawing' : '';
      html += `<div class="note-card${note.pinned ? ' note-card-pinned' : ''}${cc}${visualClass}${sel}${goalClass}${drawingClass}${reminderGlowClass}${state.selectMode ? ' note-card-selectmode' : ''}" draggable="${(state.selectMode || _isNotesMobileMode()) ? 'false' : 'true'}" data-note-id="${note.id}"${cardStyle}>
      ${state.selectMode ? `<input type="checkbox" class="memory-select-cb note-card-cb" data-note-id="${note.id}" ${state.selectedIds.has(note.id) ? 'checked' : ''} />` : ''}
      ${goalPill}
      <button class="note-card-pin${note.pinned ? ' active' : ''}" data-note-id="${note.id}" title="${note.pinned ? 'Unpin' : 'Pin'}">
        <svg width="16" height="16" viewBox="0 0 24 28" fill="${note.pinned ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"${note.pinned ? ' style="color:var(--accent,var(--red));"' : ''}><g transform="rotate(${note.pinned ? 0 : 45} 12 14)" style="transition:transform 0.2s ease;"><line x1="12" y1="17" x2="12" y2="27"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17z"/></g></svg>
      </button>
      ${state.showingArchived
          ? `<button class="note-card-corner-trash" data-note-id="${note.id}" title="Delete forever" aria-label="Delete forever">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
          </button>
          <button class="note-card-corner-unarchive" data-note-id="${note.id}" title="Unarchive" aria-label="Unarchive note">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14l-5-5 5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v0a5 5 0 0 1-5 5H9"/></svg>
          </button>`
          : `<button class="note-card-done" data-note-id="${note.id}" title="Mark done" aria-label="Mark done">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </button>
          ${_hasItems(note) ? `<button class="note-card-copy note-card-copy-corner" data-note-id="${note.id}" title="Copy all items" aria-label="Copy all items">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>` : ''}`}
      <div class="note-card-header">
        <div class="note-card-title${note.title ? '' : ' empty'}" data-action="edit" role="button" tabindex="0"${note.title ? '' : ' aria-label="Untitled note"'}>${_esc(note.title || '')}</div>
        ${dueBadge}
      </div>
      ${contentHtml}
      ${_hasItems(note) ? `<div class="note-cl-quickadd"><input type="text" class="note-cl-quickadd-input" placeholder="+ Add item" data-note-id="${note.id}" /></div>` : ''}
      ${reminderTagHtml}
      ${noteTags.length ? `<div class="note-card-label">${noteTags.map(t => `<button type="button" class="note-card-label-chip" data-note-label-filter="${_esc(t)}" title="Filter #${_esc(t)}">#${_esc(t)}</button>`).join(' ')}</div>` : ''}
      <div class="note-card-actions">
        <div class="note-card-colors">${colorDots}</div>
        <span style="flex:1"></span>
        ${state.showingArchived ? `
        <button class="note-card-action note-card-delete" data-note-id="${note.id}" title="Delete permanently">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
        </button>
        <button class="note-card-action note-card-unarchive" data-note-id="${note.id}" title="Unarchive">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><polyline points="3 4 3 10 9 10"/></svg>
        </button>` : `
        ${_hasItems(note) ? `
        <button class="note-card-action note-card-copy" data-note-id="${note.id}" title="Copy all items">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>` : ''}
        <button class="note-card-action note-card-archive" data-note-id="${note.id}" title="Save (archive)">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
        </button>
        <button class="note-card-action note-card-delete" data-note-id="${note.id}" title="Delete">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <button class="note-card-action note-card-corner-menu" data-note-id="${note.id}" title="More" aria-label="More actions">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>
        </button>`}
      </div>
    </div>`;
    }

    // Quick-add always sits at the top.
    body.innerHTML = '';
    _renderLabelsInto(body);
    _renderQuickAdd(body);
    if (sorted.length === 0) {
      const query = String(state.searchQuery || '').trim();
      body.insertAdjacentHTML('beforeend', query
        ? '<div class="notes-empty-msg">' + _esc(emptySearchText(query)) + '</div>'
        : '<div class="notes-empty-msg">No notes yet <span style="vertical-align:-3px;margin-left:4px;">' + uiModule.emptyStateIcon('smiley') + '</span></div>');
    } else {
      body.insertAdjacentHTML('beforeend', html);
    }

    _bindCardEvents(body);
    _animateReflow(prevPositions);
    _applyMasonry(body);
  }

  return { _renderLoadingSkeleton, _renderNotes };
}

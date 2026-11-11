/** Pure Notes completion, tags, reminder and serialization rules. */
import { _hasTimeComponent } from './recurrence.js';
export function _isCalendarReminderNote(n) {
  return n && n.source === 'calendar' && n.label === 'calendar';
}

export function _hasActiveReminder(n, clock) {
  if (!n || n.archived || _isNoteFullyDone(n)) return false;
  if (!n.due_date) return false;
  const t = new Date(n.due_date).getTime();
  return !isNaN(t) && t <= clock.now();
}

export function _isNoteFullyDone(note) {
  if (_hasItems(note) && Array.isArray(note.items) && note.items.length > 0) {
    return note.items.every(it => it.done);
  }
  return false;
}

export function _hasItems(note) {
  return note && (note.note_type === 'todo' || note.note_type === 'goal' || note.note_type === 'checklist');
}

export function _goalProgress(note) {
  if (!Array.isArray(note?.items) || note.items.length === 0) return '';
  const done = note.items.filter(it => it.done).length;
  return ` ${done}/${note.items.length}`;
}

export function _nextGoalStep(note) {
  if (!Array.isArray(note?.items)) return null;
  for (let i = 0; i < note.items.length; i++) {
    if (!note.items[i].done) return { idx: i, item: note.items[i] };
  }
  return null;
}

export function _noteTags(n) {
  const tags = [];
  if (n?.label) tags.push(...n.label.trim().split(/\s+/).filter(Boolean));
  if (n?.due_date && _hasTimeComponent(n.due_date)) tags.push('reminder');
  return [...new Set(tags.map(t => t.replace(/^#+/, '').trim()).filter(Boolean))];
}

export function _visibleNoteTags(n) {
  return _noteTags(n).filter(t => t !== 'reminder');
}

export function _isPastReminder(n, clock) {
  if (!n?.due_date || !_hasTimeComponent(n.due_date)) return false;
  const due = new Date(n.due_date).getTime();
  return !isNaN(due) && due <= clock.now();
}

export function _serializeNoteForCopy(note) {
  const lines = [];
  if (note.title) lines.push(note.title);
  if (note.content) lines.push(note.content);
  if (Array.isArray(note.items) && note.items.length) {
    if (lines.length) lines.push('');
    for (const it of note.items) {
      if (!it || !(it.text || '').trim()) continue;
      lines.push(`- [${it.done ? 'x' : ' '}] ${(it.text || '').trim()}`);
    }
  }
  return lines.join('\n').trim();
}

export function _noteToAgentPrompt(note) {
  const parts = [];
  if ((note.title || '').trim()) parts.push(note.title.trim());
  if ((note.content || '').trim()) parts.push(note.content.trim());
  if (Array.isArray(note.items)) {
    note.items.filter(it => !it.done && (it.text || '').trim())
      .forEach(it => parts.push('- ' + it.text.trim()));
  }
  const body = parts.join('\n');
  return body ? `Help me get this done:\n\n${body}\n\nThe source note is read-only. Do not edit, replace, or update it.` : '';
}

export function _standardVisualVariant(note) {
  const source = String(note?.id || note?.title || note?.created_at || 'standard-note');
  let hash = 0;
  for (let i = 0; i < source.length; i++) hash = ((hash << 5) - hash + source.charCodeAt(i)) | 0;
  return Math.abs(hash) % 5;
}

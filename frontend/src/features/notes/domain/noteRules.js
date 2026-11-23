/** Pure Notes completion, tags, reminder and serialization rules. */
import { _hasTimeComponent } from './recurrence.js';
import { parseTaskLines, stripTaskLines } from './markdownTasks.js';
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

/** The title, then the note's markdown as stored. Its task lines are already in `content`; `items` is derived from them. */
export function _serializeNoteForCopy(note) {
  const lines = [];
  if (note.title) lines.push(note.title);
  if (note.content) lines.push(note.content);
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

/** The sort order that puts an edited or new note at the top of the unpinned notes. */
export function topSortOrder(notes, exceptId = null) {
  const lowest = (notes || [])
    .filter(n => !n.pinned && n.id !== exceptId)
    .reduce((min, n) => Math.min(min, n.sort_order || 0), 0);
  return lowest - 1;
}

// Reminder text, the same rules as the server's `reminder_body` (checked
// against tests/fixtures/notes_reminder_vectors.json): prose without task
// lines or markup a notification cannot show, then the open tasks. Fenced
// code is content and stays as written.
const REMINDER_TEXT_LIMIT = 400;
const REMINDER_TASK_LIMIT = 8;
const REMINDER_IMAGE_RE = /!\[[^\]\n]*\]\([^)\n]*\)/g;
const REMINDER_LINK_RE = /(?<!!)\[([^\]\n]*)\]\([^)\n]*\)/g;
const REMINDER_LINE_MARK_RE = /^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?)/;
const REMINDER_FENCE_RE = /^[ \t]{0,3}(?:`{3,}|~{3,})/;
const firstChars = (text, n) => Array.from(text).slice(0, n).join('');

/** `text` as plain words for a reminder: no image markdown, links as their text. */
export function reminderText(text) {
  let inFence = false;
  const lines = String(text || '').split('\n').map(line => {
    if (REMINDER_FENCE_RE.test(line)) inFence = !inFence;
    else if (!inFence) line = line.replace(REMINDER_IMAGE_RE, '').replace(REMINDER_LINK_RE, (_m, label) => label).replace(REMINDER_LINE_MARK_RE, '');
    return line.trimEnd();
  });
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** What a reminder for this note content says. */
export function reminderBody(content) {
  const tasks = parseTaskLines(content);
  if (!tasks.length) return firstChars(reminderText(content), REMINDER_TEXT_LIMIT).trim();
  const prose = firstChars(reminderText(stripTaskLines(content)), REMINDER_TEXT_LIMIT).trim();
  const pending = tasks.filter(task => !task.done).map(task => reminderText(task.text)).filter(Boolean);
  if (!pending.length) return prose || `${tasks.length} item${tasks.length === 1 ? '' : 's'}`;
  const shown = pending.slice(0, REMINDER_TASK_LIMIT).map(text => `- ${text}`).join('\n');
  const extra = pending.length > REMINDER_TASK_LIMIT ? `\n...and ${pending.length - REMINDER_TASK_LIMIT} more` : '';
  const summary = `Pending (${pending.length}):\n${shown}${extra}`;
  return prose ? `${prose}\n\n${summary}` : summary;
}

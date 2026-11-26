/** Pure Notes completion, tags, reminder and serialization rules. */
import { _hasTimeComponent } from './recurrence.js';
import { parseTaskLines, stripTaskLines } from './markdownTasks.js';
/** The server's reason when it refused a write because the note's title or text is too long (413), else null. */
export function sizeRefusal(error) {
  if (error?.status !== 413) return null;
  const message = error.body?.detail?.message;
  return typeof message === 'string' && message ? message : 'This note is too long to save.';
}

export function _isCalendarReminderNote(n) {
  return n && n.source === 'calendar' && n.label === 'calendar';
}

/** A past reminder on one of my notes. A note shared with me carries its owner's reminder, which is not mine. */
export function _hasActiveReminder(n, clock) {
  if (!n || n.archived || !isOwnNote(n) || _isNoteFullyDone(n)) return false;
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

// Agent prompts. A run started from a note may change that note, and only
// it: the server scopes its manage_notes to the note the chat was created
// from, so every prompt says so. The prompt is the user's own turn and
// carries only the user's request: the note itself (its title, content and
// the todo a run is about) is attached by id and the server adds it as
// untrusted reference context, since a collaborator may have written it.
// The notice names no tool: a tool name in the user turn narrows the run's
// tools to that one family, so an Ask such as "add this to my calendar" would
// lose the calendar. The server offers manage_notes to every turn of a chat
// started from a note instead.
export const EDIT_SCOPE_NOTICE = 'You may edit the attached note (and only it); other notes are read-only.';
// A run that only reads (a list, a view, a search of the notes) is done when
// its answer shows what it read: "the list returned 13 notes" is not a list.
// Worded to leave routing alone: "show" or "search" here would add the UI or
// web family to every note run, and "list" made the stalled-notes fallback
// replace the model's own view or update with a list of every note.
export const SHOW_RESULTS_NOTICE = 'If you read anything for this, give the results in your answer, not just that you did it.';
const ATTACHED_NOTE = 'The note is attached as the selected note.';
const FOCUS_ITEM = 'The item is the focus todo of the attached note.';
const NOTICES = `${SHOW_RESULTS_NOTICE}\n\n${EDIT_SCOPE_NOTICE}`;

// The chat shows a note run's first message without the sentences above:
// they are for the model, and the note and its todo are shown as cards.
// The model still receives them. Set to false to show the message as sent.
export const HIDE_NOTE_RUN_NOTICES = true;
// Every wording a stored message may carry, older ones included.
const NOTE_RUN_NOTICES = [
  ATTACHED_NOTE,
  FOCUS_ITEM,
  SHOW_RESULTS_NOTICE,
  'If you read or list anything for this, put the results in your answer, not just that you did it.',
  EDIT_SCOPE_NOTICE,
];

/** A note run's message as the chat shows it: the request, without the notices. Unchanged when nothing would be left. */
export function noteRunDisplayText(text) {
  const source = String(text || '');
  let shown = source.replace(/\r\n/g, '\n');
  for (const notice of NOTE_RUN_NOTICES) shown = shown.split(notice).join('');
  shown = shown.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return shown || source;
}
const hasBody = note => !!((note?.title || '').trim() || (note?.content || '').trim());

/** The whole note. Empty when the note has nothing to work on. */
export function _noteToAgentPrompt(note) {
  return hasBody(note) ? `Help me get this note done. ${ATTACHED_NOTE}\n\n${NOTICES}` : '';
}

/** One checklist item. The item itself travels as the run's focus todo, next to the note. */
export function taskAgentPrompt(note, text) {
  return String(text || '').trim() ? `Help me with the focus todo of the attached note. ${ATTACHED_NOTE}\n\n${NOTICES}` : '';
}

// `itemRequest` is the same preset asked about one checklist item.
export const NOTE_ASK_PRESETS = [
  { label: 'Rewrite more clearly', request: 'Rewrite this note more clearly.', itemRequest: 'Rewrite this item more clearly.' },
  { label: 'Summarize', request: 'Summarize this note.', itemRequest: 'Summarize this item.' },
  { label: 'Fix spelling and grammar', request: 'Fix the spelling and grammar in this note.', itemRequest: 'Fix the spelling and grammar in this item.' },
  { label: 'Search the web about this', request: 'Search the web about the subject of this note and tell me what you find.', itemRequest: 'Search the web about this item and tell me what you find.' },
];

/** A free-text or preset request about the whole note. */
export function askNotePrompt(note, request) {
  const ask = String(request || '').trim();
  if (!ask) return '';
  return `${ask}\n\n${ATTACHED_NOTE}\n\n${NOTICES}`;
}

/** A free-text or preset request about one checklist item. The item travels as the run's focus todo. */
export function askTaskPrompt(note, request) {
  const ask = String(request || '').trim();
  if (!ask) return '';
  return `${ask}\n\n${FOCUS_ITEM} ${ATTACHED_NOTE}\n\n${NOTICES}`;
}

/** Agent runs are the owner's: a collaborator never starts one or sees its session. */
export function canRunNoteAgent(note) {
  return !!note?.id && isOwnNote(note);
}

/** Per-item agent buttons: my saved note, outside select mode. Done rows hide theirs in CSS, since a tick flips in place. */
export function showsTaskAgent(note, { selectMode = false } = {}) {
  return canRunNoteAgent(note) && !selectMode;
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
    .filter(n => !n.pinned && n.id !== exceptId && n.is_owner !== false)
    .reduce((min, n) => Math.min(min, n.sort_order || 0), 0);
  return lowest - 1;
}

// Sharing. Payloads from a server without sharing carry none of these fields,
// so a missing field means "mine, editable".
/** True unless the note was shared with me by someone else. */
export function isOwnNote(note) {
  return note?.is_owner !== false;
}

/** Content, title and tasks are editable: mine, or shared with me as edit. */
export function canEditNote(note) {
  return note?.can_edit !== false;
}

/** Shared in either direction: its images load through the note. */
export function isSharedNote(note) {
  return note?.is_owner === false || !!note?.is_shared;
}

/** "Shared · 2" on my note, the owner's name on one shared with me, else ''. */
export function shareBadge(note) {
  if (!isOwnNote(note)) return note?.shared_by || 'Shared';
  const count = Array.isArray(note?.shared_with) ? note.shared_with.length : 0;
  return count ? `Shared · ${count}` : '';
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

/** Notes in a chat's context: which ones, in which order, and what each card says. */
import { _hasItems } from './noteRules.js';
import { linesOutsideFences, parseTaskLines, stripTaskLines } from './markdownTasks.js';

/** Lines of a note's text, or open tasks of a checklist, a card shows. */
const EXCERPT_LINES = 3;
const IMAGE_RE = /!\[[^\]\n]*\]\([^)\n]*\)/g;

/**
 * The notes a chat's next turn carries, as the server orders them: the note
 * the chat was started from first (it is in context on every turn), then the
 * notes selected for it. `removable` is false for the chat's own note, which
 * the chat keeps.
 */
export function contextNoteIds(scopeId, selectedIds) {
  const out = [];
  const seen = new Set();
  const add = (id, removable) => {
    const key = String(id || '');
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push({ id: key, removable });
  };
  add(scopeId, false);
  for (const id of selectedIds || []) add(id, true);
  return out;
}

/** A checklist card lists its open tasks rather than its text. */
function isChecklist(note) {
  const content = note?.content || '';
  return _hasItems(note) || (parseTaskLines(content).length > 0 && !stripTaskLines(content));
}

/**
 * What a card shows under the title, as note markdown. A checklist: its first
 * open tasks, or "All done". Any other note: its first lines of text, without
 * fenced code or images (the card is too small for either).
 */
export function noteExcerpt(note, lines = EXCERPT_LINES) {
  const content = String(note?.content || '');
  if (isChecklist(note)) {
    const tasks = parseTaskLines(content);
    const open = tasks.filter(task => !task.done && task.text);
    if (!open.length) return tasks.length ? 'All done' : '';
    return open.slice(0, lines).map(task => `- [ ] ${task.text}`).join('\n');
  }
  const kept = [];
  for (const [, raw] of linesOutsideFences(content)) {
    const line = raw.replace(IMAGE_RE, '').trimEnd();
    if (!line.trim()) continue;
    kept.push(line);
    if (kept.length >= lines) break;
  }
  return kept.join('\n');
}

/** What a sent message records of its context notes: id and title, as the server stores them. */
export function contextNoteRecord(notes) {
  return (notes || []).filter(n => n && n.id).map(n => ({ id: String(n.id), title: String(n.title || '') }));
}

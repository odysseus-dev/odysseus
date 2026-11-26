/** The words and the comparisons behind the note history: when ("2 min ago", grouped by day), who ("Agent in Agent: Trip", "You"), what a change did (the version before it against the note it left), and what undoing it would change against the note as it is now. Pure, so it is tested without a page. */

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
// Beyond this many lines on each side the diff stops looking for moved
// lines and shows the differing middle as removed then added.
const DIFF_MAX_CELLS = 250000;

// Server timestamps are naive UTC.
export const asDate = iso => new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`);

const startOfDay = date => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const daysBetween = (date, now) => Math.round((startOfDay(now) - startOfDay(date)) / DAY);
const clock = (date, locale) => date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });

/** A time as a person says it: "18:04", "yesterday 18:04", "Tue 18:04", "3 Nov 18:04". */
export function shortTime(iso, now, locale = []) {
  if (!iso) return '';
  const date = asDate(iso);
  if (Number.isNaN(date.getTime())) return '';
  const days = daysBetween(date, now);
  if (days === 0) return clock(date, locale);
  if (days === 1) return `yesterday ${clock(date, locale)}`;
  if (days > 1 && days < 7) return `${date.toLocaleDateString(locale, { weekday: 'short' })} ${clock(date, locale)}`;
  const sameYear = date.getFullYear() === now.getFullYear();
  const day = date.toLocaleDateString(locale, sameYear ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' });
  return `${day} ${clock(date, locale)}`;
}

/** "just now", "2 min ago", "1 h ago" within the hour or so; beyond that, `shortTime`. */
export function relativeTime(iso, now, locale = []) {
  if (!iso) return '';
  const date = asDate(iso);
  const ago = now - date;
  if (Number.isNaN(ago)) return '';
  if (ago >= 0 && ago < MINUTE) return 'just now';
  if (ago >= 0 && ago < HOUR) return `${Math.floor(ago / MINUTE)} min ago`;
  if (ago >= 0 && ago < 2 * HOUR && daysBetween(date, now) === 0) return '1 h ago';
  return shortTime(iso, now, locale);
}

/** The heading a row sits under: "Today", "Yesterday", or the date. */
export function dayLabel(iso, now, locale = []) {
  const date = asDate(iso);
  if (Number.isNaN(date.getTime())) return '';
  const days = daysBetween(date, now);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(locale, sameYear ? { weekday: 'long', day: 'numeric', month: 'long' } : { day: 'numeric', month: 'long', year: 'numeric' });
}

/** Consecutive rows under one heading each: [{ label, rows }]. Rows stay in their order (newest first). */
export function groupByDay(rows, now, locale = []) {
  const groups = [];
  for (const row of rows || []) {
    const label = dayLabel(row.created_at, now, locale);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(row);
    else groups.push({ label, rows: [row] });
  }
  return groups;
}

/**
 * Who made the change after a version, as the row names them. `chat`: the
 * chat's name for an agent edit (null when it has none or was deleted),
 * `chatLink`: whether that chat can still be opened.
 */
export function changeAuthor(version, me) {
  if (version?.source === 'agent') {
    const chatLink = !!(version.session_id && version.chat_exists);
    return { who: 'Agent', chat: chatLink ? (version.chat_title || 'a chat') : (version.session_id ? 'a deleted chat' : null), chatLink };
  }
  if (version?.source === 'restore') return { who: 'Restore', chat: null, chatLink: false };
  const author = version?.author || '';
  return { who: !author || author === me ? 'You' : author, chat: null, chatLink: false };
}

/** The row as one sentence: "Agent in Agent: Trip ticked “book the cabin”", "You renamed the note", "Restore: edited the text". */
export function changeSentence(version, me) {
  const { who, chat } = changeAuthor(version, me);
  const summary = version?.summary || 'changed the note';
  if (who === 'Restore') return `Restored a version: ${summary}`;
  return chat ? `${who} in ${chat} ${summary}` : `${who} ${summary}`;
}

const splitLines = text => {
  const value = String(text ?? '');
  return value ? value.replace(/\r\n?/g, '\n').split('\n') : [];
};

/**
 * Line diff turning `from` into `to`: [{ op: 'same' | 'del' | 'add', text }].
 * `del` lines are in `from` only, `add` lines in `to` only.
 */
export function lineDiff(from, to) {
  const a = splitLines(from);
  const b = splitLines(to);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const head = a.slice(0, start).map(text => ({ op: 'same', text }));
  const tail = a.slice(endA).map(text => ({ op: 'same', text }));
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  if (midA.length * midB.length > DIFF_MAX_CELLS) {
    return [...head, ...midA.map(text => ({ op: 'del', text })), ...midB.map(text => ({ op: 'add', text })), ...tail];
  }
  // Longest common subsequence over the differing middle.
  const n = midA.length;
  const m = midB.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = midA[i] === midB[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const middle = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (midA[i] === midB[j]) { middle.push({ op: 'same', text: midA[i] }); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) middle.push({ op: 'del', text: midA[i++] });
    else middle.push({ op: 'add', text: midB[j++] });
  }
  while (i < n) middle.push({ op: 'del', text: midA[i++] });
  while (j < m) middle.push({ op: 'add', text: midB[j++] });
  return [...head, ...middle, ...tail];
}

const FIELD_NAMES = [
  ['title', 'Title'],
  ['color', 'Colour'],
  ['label', 'Tag'],
  ['due_date', 'Reminder'],
  ['pinned', 'Pinned'],
];

const shown = (field, value) => {
  if (field === 'pinned') return value ? 'yes' : 'no';
  return value == null || value === '' ? 'none' : String(value);
};

/**
 * What turns `from` into `to` (two notes, or versions in full): the fields
 * that differ ({ name, from, to }) and the content's line diff, `del` for
 * lines only in `from`, `add` for lines only in `to`. `same` is true when
 * nothing shown differs.
 */
export function noteDiff(from, to) {
  const fields = [];
  for (const [field, name] of FIELD_NAMES) {
    const a = field === 'pinned' ? !!from?.[field] : (from?.[field] ?? '');
    const b = field === 'pinned' ? !!to?.[field] : (to?.[field] ?? '');
    if (String(a).trim() !== String(b).trim()) fields.push({ name, from: shown(field, from?.[field]), to: shown(field, to?.[field]) });
  }
  const lines = lineDiff(from?.content || '', to?.content || '');
  const same = !fields.length && lines.every(line => line.op === 'same');
  return { fields, lines, same };
}

/**
 * What the change after `version` did: from the version (the note just
 * before it) to `version.after` (the note as it left it, from the server).
 * Added lines are the change's own, removed lines are what it took out.
 */
export const changeDiff = version => noteDiff(version, version?.after);

/** What restoring `version` would do to `now` (the note as it is): from now to the version. */
export const restoreDiff = (now, version) => noteDiff(now, version);

/** The diff trimmed for reading: changed lines with `context` unchanged lines around them, the rest folded to { op: 'gap', count }. */
export function foldDiff(lines, context = 2) {
  const keep = lines.map(line => line.op !== 'same');
  const near = keep.map((_, index) => {
    for (let k = Math.max(0, index - context); k <= Math.min(lines.length - 1, index + context); k++) if (keep[k]) return true;
    return false;
  });
  const out = [];
  let gap = 0;
  lines.forEach((line, index) => {
    if (near[index]) {
      if (gap) { out.push({ op: 'gap', count: gap }); gap = 0; }
      out.push(line);
    } else gap++;
  });
  if (gap) out.push({ op: 'gap', count: gap });
  return out;
}

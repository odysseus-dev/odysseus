// Pure (DOM-free) keyboard-intent classifier for the sidebar session list.
//
// Kept standalone and side-effect-free so it can be unit-tested under node
// without a DOM (see tests/test_session_list_keys_js.py, same node-driven
// pattern as escMenuStack.js / keybind tests). The handler in sessions.js maps
// the returned intent to the actual effects (focus / select / delete).
//
// Bug this guards against: double-clicking a session to rename renders an
// <input> INSIDE the focused .list-item, so a bare keydown still bubbles up to
// the list handler. Backspace there must edit the rename text — NOT fire the
// delete-session shortcut. Any editable target therefore classifies as 'ignore'
// regardless of which key was pressed.

const _EDITABLE_TAGS = { INPUT: true, TEXTAREA: true };

// True when the keystroke is being typed into an editable field (the inline
// rename input, or any input/textarea/contenteditable).
export function isEditableTarget(t) {
  return !!(t && (t.isContentEditable || _EDITABLE_TAGS[t.tagName] === true));
}

// Classify a session-list keydown into an intent. One of:
//   'ignore'  — editing text, or not on a session row: handler does nothing
//   'nav-down' / 'nav-up' — arrow navigation between rows
//   'delete'  — Delete/Backspace on a focused row
//   'open'    — Enter on a focused row
//   null      — a key we don't handle (let it pass through untouched)
export function classifySessionListKey(e) {
  if (isEditableTarget(e.target)) return 'ignore';

  const onRow = !!(e.target && typeof e.target.closest === 'function'
    && e.target.closest('.list-item[data-session-id]'));
  if (!onRow) return 'ignore';

  switch (e.key) {
    case 'ArrowDown': return 'nav-down';
    case 'ArrowUp': return 'nav-up';
    case 'Delete':
    case 'Backspace': return 'delete';
    case 'Enter': return 'open';
    default: return null;
  }
}

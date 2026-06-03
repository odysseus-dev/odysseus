// static/js/escMenuStack.js
//
// Dismissal registry for transient, ad-hoc overlays — dropdown menus and
// context popups that are built on the fly and appended to <body>, living
// OUTSIDE the .modal system. The global Escape arbiter in ui.js can find
// modals but not these, so each menu registers a dismiss callback here while
// it is open and unregisters when it closes.
//
// The stack is LIFO: dismissTopMenu() closes the most-recently-opened menu
// first, so a dropdown opened on top of a modal closes before the modal does.
// Deliberately DOM-free so it can be unit-tested under plain node (see
// tests/test_esc_menu_stack_js.py).

const _stack = [];

/**
 * Register a menu's dismiss callback. Returns an unregister function that the
 * menu MUST call from its own teardown (outside-click close, item click, etc.)
 * so the stack never holds a stale entry. Calling the returned function more
 * than once, or after the menu was already dismissed via Escape, is safe.
 */
export function registerMenuDismiss(dismissFn) {
  if (typeof dismissFn !== 'function') return () => {};
  const entry = { dismissFn };
  _stack.push(entry);
  return () => {
    const i = _stack.indexOf(entry);
    if (i !== -1) _stack.splice(i, 1);
  };
}

/**
 * Dismiss the most-recently-registered menu, if any. Returns true when a menu
 * was dismissed (so the caller can swallow the Escape key), false when nothing
 * was open. The entry is popped BEFORE its callback runs, so even if a
 * dismissFn forgets to unregister or throws, a single Escape closes exactly
 * one menu and the stack never gets stuck.
 */
export function dismissTopMenu() {
  const entry = _stack.pop();
  if (!entry) return false;
  try { entry.dismissFn(); } catch {}
  return true;
}

/** Test/debug helper: number of currently-registered menus. */
export function _openMenuCount() {
  return _stack.length;
}

/**
 * Tear a transient menu down through its registered dismiss callback if it has
 * one (releasing its Escape-stack entry and any listeners), else fall back to a
 * plain node removal. Use this anywhere menus are cleared in bulk — scroll /
 * swipe / modal-dismiss cleanup, or a "close the previous one" reopen sweep —
 * instead of a raw `el.remove()`, which would strand the stack entry.
 */
export function dismissOrRemove(el) {
  if (!el) return;
  if (typeof el._dismiss === 'function') el._dismiss();
  else el.remove();
}

// ── DOM convenience wrapper ──────────────────────────────────────────────
// The registry above is intentionally DOM-free (and unit-tested as such).
// bindMenuDismiss is the thin DOM layer most callers actually want: it wires
// the ubiquitous "overlay appended to <body>, closes on an outside click"
// idiom to BOTH the outside-click listener AND the Escape stack in one call,
// so a menu only has to describe how to tear itself down once.
//
//   const close = bindMenuDismiss(popup, () => popup.remove());
//   // outside-click and Escape now both call close(); call it yourself from
//   // item handlers too.
//
// `onClose` runs exactly once (idempotent) and owns the actual teardown
// (removing/hiding the node, clearing anchor state, …). `isOutside(ev)`
// defaults to "the click landed outside `el`"; override it when extra anchors
// should count as inside the menu. The returned idempotent close() is also
// stashed on `el._dismiss`, so bulk removers (see dismissOrRemove) can tear the
// menu down through its real teardown rather than orphaning its stack entry.
export function bindMenuDismiss(el, onClose, isOutside) {
  let done = false;
  let unreg = () => {};
  const onDocClick = (ev) => {
    const outside = typeof isOutside === 'function' ? isOutside(ev) : !el.contains(ev.target);
    if (outside) close();
  };
  function close() {
    if (done) return;
    done = true;
    unreg(); unreg = () => {};
    document.removeEventListener('click', onDocClick, true);
    try { if (typeof onClose === 'function') onClose(); } catch {}
  }
  // Defer attaching the outside-click listener so the opening click doesn't
  // immediately close the menu. Skip the attach if close() already ran in the
  // same tick (e.g. an instant Escape) so we never leave a dangling listener.
  setTimeout(() => { if (!done) document.addEventListener('click', onDocClick, true); }, 0);
  unreg = registerMenuDismiss(close);
  el._dismiss = close;
  return close;
}

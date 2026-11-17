// static/js/escMenuStack.js
//
// Dismissal registry for transient UI layers — dropdown menus, context
// popups, and expanded cards that live outside the .modal system. The global
// Escape arbiter in ui.js dismisses the most recently active layer first.
//
// The stack is LIFO: dismissTopEscapeLayer() closes the most-recently-opened
// active layer first, so a dropdown or expanded card closes before its modal.
// Deliberately DOM-free so it can be unit-tested under plain node (see
// tests/test_esc_menu_stack_js.py).

const _stack = [];

/** Register any Escape-dismissable layer. */
export function registerEscapeLayer(dismissFn, isActive) {
  if (typeof dismissFn !== 'function') return () => {};
  const entry = { dismissFn, isActive };
  _stack.push(entry);
  return () => {
    const i = _stack.indexOf(entry);
    if (i !== -1) _stack.splice(i, 1);
  };
}

/**
 * Backwards-compatible menu name. Menus and cards share the same stack.
 */
export function registerMenuDismiss(dismissFn) {
  return registerEscapeLayer(dismissFn);
}

/** Dismiss the most-recently-registered active layer, if any. */
export function dismissTopEscapeLayer() {
  while (_stack.length) {
    const entry = _stack.pop();
    let active = true;
    try { active = typeof entry.isActive !== 'function' || entry.isActive() !== false; } catch { active = false; }
    if (!active) continue;
    try { entry.dismissFn(); } catch {}
    return true;
  }
  return false;
}

/** Backwards-compatible arbiter name used by existing callers. */
export function dismissTopMenu() {
  return dismissTopEscapeLayer();
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

// Expanded library cards are Escape layers too. The active predicate means a
// card that was removed or collapsed without its teardown being reached is
// skipped instead of consuming a future Escape press.
export function bindExpandedCardDismiss(card, onDismiss, expandedClass = 'doclib-card-expanded') {
  if (!card || typeof onDismiss !== 'function') return () => {};
  unbindExpandedCardDismiss(card);
  const unreg = registerEscapeLayer(onDismiss, () => (
    card.isConnected !== false && card.classList?.contains(expandedClass)
  ));
  card._escapeLayerUnregister = unreg;
  return unreg;
}

export function unbindExpandedCardDismiss(card) {
  if (typeof card?._escapeLayerUnregister === 'function') {
    card._escapeLayerUnregister();
    delete card._escapeLayerUnregister;
  }
}

// static/js/composerClear.js
/**
 * Empty the chat composer (value + autosized height) and notify listeners.
 *
 * The `el` element-lookup is passed in rather than referenced as a free
 * variable. chat.js aliases `const el = uiModule.el` only inside its send
 * functions (block scope), so a module-scope helper that called a bare
 * `el('message')` threw `ReferenceError: el is not defined` on the
 * no-model/no-session bail path — defeating the #1475 fix and leaving the send
 * button stuck. Taking the lookup as a parameter removes that hidden dependency
 * and lets the logic be unit-tested under node.
 */
export function clearComposer(el) {
  const mi = el('message');
  if (!mi) return;
  mi.value = '';
  mi.style.height = '';
  mi.dispatchEvent(new Event('input'));
}

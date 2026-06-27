// Regression test for #4856 — "Spacebar activates hovered buttons / closes
// tabs while typing". The hover-to-toggle Space shortcut must never override a
// keyboard focus that belongs to a text field or another interactive control,
// regardless of where the mouse pointer happens to be.
//
// Mirrors the node:vm harness used by markdown_codefence_placeholder_regression.mjs:
// load static/js/ui.js into a sandbox, strip module wiring, and exercise the
// internal `_spaceIsBlocked` guard directly with minimal fake DOM nodes.
//
// Run: node tests/space_hover_guard_regression.mjs

import vm from 'node:vm';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const uiPath = join(__dirname, '..', 'static', 'js', 'ui.js');

let src = readFileSync(uiPath, 'utf8');
src = src.replace(/^import .*;$/gm, '');           // drop ES imports
src = src.replace(/^export default .*;$/gm, '');   // drop the aggregate default export
src = src.replace(/^export\s+/gm, '');             // keep remaining declarations module-local

const noop = () => {};
// A universal stub that is callable, constructable, and resolves every property
// access back to itself — so any unknown top-level expression in ui.js
// (`new MutationObserver(...).observe(...)`, imported helpers, etc.) is inert.
const anything = new Proxy(function () {}, {
  get: () => anything,
  apply: () => anything,
  construct: () => anything,
});
// ui.js makes top-level calls into imported helpers (menus, modals, init wiring)
// that are irrelevant to the pure guard under test. Rather than stub each one,
// back the sandbox with a Proxy that claims every name exists and resolves any
// unknown identifier to the universal stub — so the module loads side-effect-free.
const base = {
  document: {
    addEventListener: noop, removeEventListener: noop,
    querySelector: () => null, querySelectorAll: () => [],
    getElementById: () => null, getElementsByClassName: () => [], getElementsByTagName: () => [],
    createElement: () => ({ style: {}, classList: { add: noop, remove: noop }, addEventListener: noop, appendChild: noop }),
    body: null, documentElement: null,
  },
  window: { addEventListener: noop, removeEventListener: noop, matchMedia: () => ({ matches: false, addEventListener: noop }) },
  navigator: { userAgent: '' },
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
};
const sandbox = new Proxy(base, {
  has: () => true,
  get: (t, k) => (k in t ? t[k] : anything),
  set: (t, k, v) => { t[k] = v; return true; },
});
base.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const spaceIsBlocked = base._spaceIsBlocked;
assert.equal(typeof spaceIsBlocked, 'function', 'expected _spaceIsBlocked to load from ui.js');

// Fake element: `closest(sel)` answers the two queries the guard makes.
// _isTextEditingTarget uses a selector WITHOUT 'button'; SPACE_BLOCKED_SELECTOR starts with 'button'.
function makeEl({ textEditing = false, spaceBlocked = false } = {}) {
  const el = {
    nodeType: 1,
    closest(sel) {
      if (sel.includes('button')) return spaceBlocked ? el : null;
      return textEditing ? el : null;
    },
  };
  return el;
}
const surfaceContaining = (contains) => ({ contains: () => contains });
const ev = (target) => ({ target });

let failures = 0;
function check(name, got, want) {
  if (got === want) { console.log(`  ok   ${name}`); return; }
  failures++;
  console.error(`  FAIL ${name}: got ${got}, want ${want}`);
}

// 1) The reported data-loss path: typing in one window while the pointer hovers
//    a different surface. Pre-fix this returned false (Space hijacked, tab closed).
check('typing + hovering a different surface -> blocked',
  spaceIsBlocked(ev(makeEl({ textEditing: true })), surfaceContaining(false)), true);

// 2) Typing with nothing hovered -> Space types (unchanged).
check('typing + no hovered surface -> blocked',
  spaceIsBlocked(ev(makeEl({ textEditing: true })), null), true);

// 3) Typing inside the very surface under the pointer -> Space types (unchanged).
check('typing inside the hovered surface -> blocked',
  spaceIsBlocked(ev(makeEl({ textEditing: true })), surfaceContaining(true)), true);

// 4) A focused interactive control (Tab-focused button) while hovering elsewhere:
//    Space activates the focused control, not the hovered surface. Pre-fix: false.
check('focused control + hovering a different surface -> blocked',
  spaceIsBlocked(ev(makeEl({ spaceBlocked: true })), surfaceContaining(false)), true);

// 5) Feature preserved: no text/interactive focus -> Space acts on the hovered surface.
check('plain target + hovered surface -> not blocked (shortcut still works)',
  spaceIsBlocked(ev(makeEl({})), surfaceContaining(false)), false);

if (failures) { console.error(`\n${failures} assertion(s) failed`); process.exit(1); }
console.log('\nall space-hover guard assertions passed');
process.exit(0); // ui.js schedules background timers on load; exit explicitly so the runner returns

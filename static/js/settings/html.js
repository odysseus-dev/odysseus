// HTML escaping for the Settings modules that build markup from strings.
// Delegates to the shared ui.js implementation so Settings has one escaper.
// Kept apart from dom.js: this pulls ui.js in, and dom.js must stay loadable
// without it.
import uiModule from '../ui.js?v=20260916largetoolscroll1';

export function esc(s) {
  return uiModule.esc(s);
}

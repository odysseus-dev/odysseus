// Shared DOM helpers for the Settings modules.
// Keep this module intentionally small so panel modules do not grow their own
// element-lookup conventions as Settings is split out of settings.js.

export function byId(id) {
  return document.getElementById(id);
}

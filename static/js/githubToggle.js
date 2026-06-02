/* GitHub integration — chat-input toggle.
 *
 * Single-click toggle (same pattern as web-toggle-btn / bash-toggle-btn).
 * Hidden until the user has a PAT configured in Settings; shows automatically
 * once the integration is set up.
 *
 * Write actions are configured in Settings (a separate `write_enabled` flag),
 * not per-conversation here. This button is a single "do GitHub now" switch:
 * when ON, chat.js sends `allow_github=true`, plus `allow_github_write=true`
 * only when the server-side integration has write_enabled set. So Settings is
 * the one place to grant write privilege; the chat toggle just activates the
 * integration for the current chat.
 */

const TOGGLE_KEY = 'odysseus-gh-toggle';  // persisted toggle state across reloads

let _writeEnabled = false;  // mirrors the server-side write_enabled flag
let _configured = false;    // mirrors `configured` from /api/github/integration

function $(id) { return document.getElementById(id); }

async function _fetchIntegration() {
  try {
    const r = await fetch('/api/github/integration', { credentials: 'same-origin' });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function _loadToggle() {
  // Default ON once GitHub is configured (same philosophy as web/bash tool
  // prefs). Explicit off persists via localStorage.
  try {
    const v = localStorage.getItem(TOGGLE_KEY);
    return v === null ? true : v === 'true';
  } catch { return true; }
}
function _saveToggle(val) {
  try { localStorage.setItem(TOGGLE_KEY, String(!!val)); } catch {}
}

/** Sync hidden checkboxes + button state. */
function _setEnabled(on) {
  const chk = $('gh-toggle');
  const writeChk = $('gh-toggle-write');
  const btn = $('gh-toggle-btn');
  if (chk) chk.checked = !!on;
  // The write flag rides with the read toggle — the server's write_enabled
  // gates it, and when GitHub is OFF for the chat there's nothing to write
  // anyway, so we set the write form-field only when both apply.
  if (writeChk) writeChk.checked = !!on && _writeEnabled;
  if (btn) btn.classList.toggle('active', !!on);
  _saveToggle(on);
}

function _setButtonVisibility(visible) {
  const btn = $('gh-toggle-btn');
  if (btn) btn.style.display = visible ? '' : 'none';
}

async function _refresh() {
  const info = await _fetchIntegration();
  _configured = !!(info && info.configured);
  // Treat a paused integration (enabled=false) the same as not-configured for
  // chat purposes — the button hides, the toggle clears. The PAT stays stored;
  // re-enabling in Settings re-shows the button on the next refresh.
  const _active = _configured && info && info.enabled !== false;
  _writeEnabled = !!(info && info.write_enabled);
  _setButtonVisibility(_active);
  if (!_active) _setEnabled(false);
  // Re-mirror state into the hidden checkboxes so chat.js picks up the current
  // write_enabled on the next submit without waiting for a click.
  _setEnabled($('gh-toggle')?.checked || false);
}

function _wireUp() {
  const btn = $('gh-toggle-btn');
  if (!btn) return;  // markup not present (e.g. compare mode strips the toolbar)

  // Restore previous session's toggle state.
  _setEnabled(_loadToggle());

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const chk = $('gh-toggle');
    _setEnabled(!(chk && chk.checked));
  });

  // Initial fetch — drives button visibility + write_enabled state.
  _refresh();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _wireUp);
} else {
  _wireUp();
}

// Settings page calls this after PAT save/delete or a write_enabled toggle so
// the chat-input button reflects the new state without a page reload.
window.githubToggle = {
  refresh: _refresh,
};

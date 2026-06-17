// static/js/shellWorkspace.js
// Lets shell-mode (`/sh`) commands run inside a selected Docker workspace
// container instead of the host. A small <select> appears next to the shell
// toggle button while shell mode is active; the chosen workspace id is then
// used to route `/sh` commands to POST /api/docker-workspaces/{id}/exec.

const ShellWorkspace = (() => {
  const KEY = 'odysseus-shell-ws-id';
  const WS_API = '/api/docker-workspaces';

  // ── Selection state (persisted) ────────────────────────────────────────────
  function getSelected() {
    try { return localStorage.getItem(KEY) || ''; } catch (_) { return ''; }
  }
  function setSelected(id) {
    try {
      if (id) localStorage.setItem(KEY, id);
      else localStorage.removeItem(KEY);
    } catch (_) {}
  }

  // ── API helpers ─────────────────────────────────────────────────────────────
  async function listWorkspaces() {
    try {
      const res = await fetch(WS_API, { credentials: 'same-origin' });
      if (!res.ok) return [];
      const data = await res.json();
      return data.workspaces || [];
    } catch (_) {
      return [];
    }
  }

  /** Run a command in a workspace. Returns the raw JSON (either the exec
   *  result {exit_code, output, ...} or {status:'pending_approval', token, ...}). */
  async function exec(wsId, command, guardToken) {
    const body = guardToken ? { command, guard_token: guardToken } : { command };
    const res = await fetch(`${WS_API}/${wsId}/exec`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Exec failed (${res.status})`);
    return data;
  }

  /** Approve/deny a pending guard token for a MEDIUM-risk command. */
  async function guardDecide(token, decision) {
    const res = await fetch(`/api/guard/decide/${token}`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Approval failed');
    }
    return res.json().catch(() => ({}));
  }

  // ── Selector UI ─────────────────────────────────────────────────────────────
  async function populate(sel) {
    const current = getSelected();
    const workspaces = await listWorkspaces();
    sel.innerHTML = '';

    const hostOpt = document.createElement('option');
    hostOpt.value = '';
    hostOpt.textContent = '🖥 Host';
    sel.appendChild(hostOpt);

    if (!workspaces.length) {
      const none = document.createElement('option');
      none.value = '';
      none.disabled = true;
      none.textContent = 'No workspaces — create one in AI Workspaces';
      sel.appendChild(none);
    }

    let stillExists = false;
    workspaces.forEach(ws => {
      const opt = document.createElement('option');
      opt.value = ws.id;
      const status = ws.status ? ` (${ws.status})` : '';
      opt.textContent = `📦 ${ws.name}${status}`;
      if (ws.id === current) { opt.selected = true; stillExists = true; }
      sel.appendChild(opt);
    });

    // If the previously-selected workspace was destroyed, fall back to Host.
    if (current && !stillExists) {
      setSelected('');
      sel.value = '';
    } else {
      sel.value = current;
    }
  }

  function _visible() {
    const btn = document.getElementById('bash-toggle-btn');
    if (!btn) return false;
    const shown = btn.style.display !== 'none';
    return shown && btn.classList.contains('active');
  }

  function syncVisibility(sel) {
    const on = _visible();
    sel.style.display = on ? '' : 'none';
    if (on) populate(sel);
  }

  // Wire the selector to shell-mode state. The selector shows only while the
  // shell toggle is active and visible, and refreshes its workspace list each
  // time it appears (so newly-created workspaces show up without a reload).
  function mount() {
    const sel = document.getElementById('shell-ws-select');
    const btn = document.getElementById('bash-toggle-btn');
    if (!sel || !btn) return;

    sel.addEventListener('change', () => setSelected(sel.value));

    const obs = new MutationObserver(() => syncVisibility(sel));
    obs.observe(btn, { attributes: true, attributeFilter: ['class', 'style'] });

    syncVisibility(sel);
  }

  return { KEY, getSelected, setSelected, listWorkspaces, exec, guardDecide, populate, mount, syncVisibility };
})();

export default ShellWorkspace;

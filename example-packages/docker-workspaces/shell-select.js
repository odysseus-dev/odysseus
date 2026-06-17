/**
 * Docker Workspaces — Shell target selector
 *
 * Injects a workspace picker next to the shell-mode toggle. While shell mode
 * is active the user can choose a Docker workspace; `/sh` commands then run
 * inside that container instead of the host. Exposes window.OdysseusShellWS so
 * the core shell command (slashCommands.js) can route to the selection. When
 * this package is not installed the global is absent and `/sh` stays on the host.
 *
 * Loaded as a chatInputWidget hook; uses window.OdysseusPkg as a fallback mount.
 */
(function () {
  'use strict';
  const KEY = 'odysseus-shell-ws-id';
  const WS_API = '/api/docker-workspaces';

  function getSelected() {
    try { return localStorage.getItem(KEY) || ''; } catch (_) { return ''; }
  }
  function setSelected(id) {
    try {
      if (id) localStorage.setItem(KEY, id);
      else localStorage.removeItem(KEY);
    } catch (_) {}
  }

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

  // Run a command in a workspace. Returns the raw JSON (exec result or a
  // {status:'pending_approval', token, command, reason} guard response).
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

  // Expose the router so the core /sh handler can use the selection.
  window.OdysseusShellWS = { getSelected, setSelected, exec, guardDecide };

  // ── Selector element ─────────────────────────────────────────────────────
  const sel = document.createElement('select');
  sel.id = 'shell-ws-select';
  sel.className = 'shell-ws-select';
  sel.title = 'Run shell commands in…';
  sel.setAttribute('aria-label', 'Shell workspace');
  sel.style.display = 'none';
  sel.addEventListener('change', () => setSelected(sel.value));

  async function populate() {
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

    if (current && !stillExists) { setSelected(''); sel.value = ''; }
    else { sel.value = current; }
  }

  function shellModeActive() {
    const btn = document.getElementById('bash-toggle-btn');
    if (!btn) return false;
    return btn.style.display !== 'none' && btn.classList.contains('active');
  }

  function syncVisibility() {
    const on = shellModeActive();
    sel.style.display = on ? '' : 'none';
    if (on) populate();
  }

  // ── Mount: place the selector right after the shell toggle ────────────────
  function mount() {
    const btn = document.getElementById('bash-toggle-btn');
    if (btn && btn.parentNode) {
      btn.insertAdjacentElement('afterend', sel);
      const obs = new MutationObserver(syncVisibility);
      obs.observe(btn, { attributes: true, attributeFilter: ['class', 'style'] });
      syncVisibility();
    } else if (window.OdysseusPkg) {
      // Fallback: inject into the chat-input slot if the toggle isn't found.
      window.OdysseusPkg.addWidget('chatInput', sel);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
})();

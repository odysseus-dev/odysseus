/* extensionsPanel.js — admin UI for /api/oxs/* (Extensions).

   Mirrors the MCP servers panel pattern in static/js/admin.js: a flat list of
   rows with per-row enable/disable/reload/uninstall buttons, an expandable
   detail panel showing capabilities + stats + webhooks (with raw token) +
   recent audit events.

   The panel itself lives inline in the Settings modal under the "Extensions"
   admin sub-tab. Module is wired by initExtensionsPanel() — called once on
   app start — which:
     - binds the rescan button inside the panel
     - listens for clicks on the [data-settings-tab="extensions"] nav item
       and refreshes the list each time the tab is opened

   All fetches use credentials:'same-origin' — the oxs_api router gates
   access via session admin (core.middleware.require_admin), the same auth
   other admin pages use.
*/

import uiModule from './ui.js?v=20260916largetoolscroll1';

const API = '/api/oxs';

function _emitChanged() {
  document.dispatchEvent(new CustomEvent('oxs-extensions-changed'));
}

async function _jsonOrThrow(res, op) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.error || `${res.status} ${res.statusText}`;
    throw new Error(`${op} failed: ${msg}`);
  }
  return data;
}

async function _post(path, op) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  return _jsonOrThrow(res, op);
}

function _setMsg(text, kind = '') {
  const m = document.getElementById('ext-panel-msg');
  if (!m) return;
  m.textContent = text || '';
  if (text && kind === 'ok') {
    setTimeout(() => { if (m.textContent === text) { m.textContent = ''; } }, 3000);
  }
}

function _setInstallMsg(text, kind = '') {
  const m = document.getElementById('ext-install-msg');
  if (!m) return;
  m.textContent = text || '';
  m.style.color = kind === 'err' ? '#a04040' : (kind === 'ok' ? '#3a8a4f' : '');
}

function _badge(text, color) {
  const bg = color ? `${color}33` : 'var(--border)';
  const fg = color || 'var(--fg)';
  return `<span class="admin-badge" style="background:${bg};color:${fg};">${uiModule.esc(text)}</span>`;
}

function _renderRow(ext) {
  const enabledBadge = ext.is_enabled
    ? _badge('enabled', '#3a8a4f')
    : _badge('disabled', '#a04040');
  const errorBadge = ext.install_error
    ? _badge(`install error: ${ext.install_error}`, '#a04040')
    : '';
  const privBadge = ext.privileged.length
    ? _badge(`privileged: ${ext.privileged.length}`, '#a07a3a')
    : '';
  const sourceBadge = ext.source ? _badge(ext.source) : '';
  const caps = ext.capabilities.length
    ? ext.capabilities.map(c => `<span style="font-size:10px;opacity:0.55;padding:1px 6px;border:1px solid var(--border);border-radius:4px;">${uiModule.esc(c)}</span>`).join(' ')
    : '<span style="font-size:10px;opacity:0.4;">(no declared capabilities)</span>';
  const violations = ext.violations && ext.violations.length
    ? `<div style="font-size:11px;color:#a04040;margin-top:4px;">Violations: ${ext.violations.length}</div>`
    : '';
  const stats = ext.stats || {};
  const statLine = [
    `${stats.tools || 0} tools`,
    `${stats.mcp_tools || 0} mcp`,
    `${stats.routes || 0} routes`,
    `${stats.webhooks || 0} webhooks`,
    `${stats.event_subs || 0} events`,
    `${stats.task_actions || 0} tasks`,
    `${stats.statics || 0} statics`,
  ].join(' · ');

  const sidebarChecked = ext.sidebar_visible ? 'checked' : '';
  return `
    <div class="admin-user-row" data-ext-id="${uiModule.esc(ext.id)}">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:4px 0;cursor:pointer;" data-ext-header="${uiModule.esc(ext.id)}">
        <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:6px;">
          <span class="admin-user-name">${uiModule.esc(ext.name)}</span>
          <span style="font-size:10px;opacity:0.45;font-family:monospace;">${uiModule.esc(ext.id)}</span>
          <span style="font-size:10px;opacity:0.4;">v${uiModule.esc(ext.version || '?')}</span>
          ${enabledBadge}${errorBadge}${sourceBadge}${privBadge}
        </div>
        <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;">
          <button class="admin-btn-sm" data-ext-action="toggle" data-ext-id="${uiModule.esc(ext.id)}" data-ext-enable="${ext.is_enabled ? '0' : '1'}">
            ${ext.is_enabled ? 'Disable' : 'Enable'}
          </button>
          <button class="admin-btn-sm" data-ext-action="reload" data-ext-id="${uiModule.esc(ext.id)}">Reload</button>
          <button class="admin-btn-delete" data-ext-action="uninstall" data-ext-id="${uiModule.esc(ext.id)}">Uninstall</button>
        </div>
      </div>
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:4px;font-size:10px;opacity:0.65;">
        <label style="display:flex;gap:4px;align-items:center;cursor:pointer;">
          <input type="checkbox" data-ext-action="sidebar" data-ext-id="${uiModule.esc(ext.id)}" ${sidebarChecked} />
          Show in sidebar
        </label>
        <span>${uiModule.esc(statLine)}</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;font-size:10px;opacity:0.6;">${caps}</div>
      ${violations}
      <div class="admin-priv-panel hidden" data-ext-detail="${uiModule.esc(ext.id)}" style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);">
        <div style="font-size:11px;opacity:0.5;">Loading…</div>
      </div>
    </div>
  `;
}

function _renderDetail(ext, webhooks, audit) {
  const whRows = (webhooks && webhooks.webhooks && webhooks.webhooks.length)
    ? webhooks.webhooks.map(w => `
        <div style="padding:6px;border:1px solid var(--border);border-radius:6px;margin-top:4px;">
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            <span style="font-size:11px;font-family:monospace;">/api/ext/${uiModule.esc(ext.id)}/webhook/<span style="color:var(--red);">${uiModule.esc(w.raw_token || '')}</span></span>
          </div>
          <div style="font-size:10px;opacity:0.5;margin-top:2px;">path: <code>${uiModule.esc(w.path_suffix || '')}</code> · events: ${(w.event_filter || []).map(uiModule.esc).join(', ') || '(none)'} · registered ${uiModule.esc(w.registered_at || '')}</div>
        </div>
      `).join('')
    : '<div style="font-size:11px;opacity:0.5;">No webhooks registered.</div>';

  const auditRows = (audit && audit.events && audit.events.length)
    ? audit.events.slice(0, 20).map(ev => `
        <div style="font-size:10px;font-family:monospace;padding:2px 0;border-bottom:1px dotted var(--border);">
          <span style="opacity:0.5;">${uiModule.esc((ev.timestamp || ev.ts || '').slice(0, 19))}</span>
          <span style="color:var(--accent);">${uiModule.esc(ev.event || ev.action || '')}</span>
          <span style="opacity:0.7;">${uiModule.esc(ev.actor || '')}</span>
        </div>
      `).join('')
    : '<div style="font-size:11px;opacity:0.5;">No audit entries.</div>';

  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
      <div>
        <div class="admin-priv-section">Webhooks</div>
        ${whRows}
      </div>
      <div>
        <div class="admin-priv-section">Recent events</div>
        ${auditRows}
      </div>
    </div>
  `;
}

async function _loadDetail(extId, panel) {
  panel.innerHTML = '<div style="font-size:11px;opacity:0.5;">Loading…</div>';
  try {
    const [ext, wh, au] = await Promise.all([
      _jsonOrThrow(await fetch(`${API}/extensions/${encodeURIComponent(extId)}`, { credentials: 'same-origin' }), 'detail'),
      _jsonOrThrow(await fetch(`${API}/extensions/${encodeURIComponent(extId)}/webhooks`, { credentials: 'same-origin' }), 'webhooks'),
      _jsonOrThrow(await fetch(`${API}/extensions/${encodeURIComponent(extId)}/audit?limit=20`, { credentials: 'same-origin' }), 'audit'),
    ]);
    panel.innerHTML = _renderDetail(ext, wh, au);
  } catch (e) {
    panel.innerHTML = `<div style="font-size:11px;color:#a04040;">${uiModule.esc(String(e.message || e))}</div>`;
  }
}

async function loadExtensions() {
  const list = document.getElementById('ext-panel-list');
  if (!list) return;
  list.innerHTML = '<div style="font-size:11px;opacity:0.5;">Loading…</div>';
  try {
    const data = await _jsonOrThrow(await fetch(`${API}/extensions`, { credentials: 'same-origin' }), 'list');
    if (!data.extensions.length) {
      list.innerHTML = '<div style="font-size:11px;opacity:0.5;padding:12px;text-align:center;">No extensions installed. Drop a folder into <code>data/extensions/</code> and click Rescan.</div>';
      return;
    }
    list.innerHTML = data.extensions.map(_renderRow).join('');
    list.querySelectorAll('[data-ext-header]').forEach(hdr => {
      hdr.addEventListener('click', (ev) => {
        if (ev.target.closest('button')) return;
        const id = hdr.dataset.extHeader;
        const panel = list.querySelector(`[data-ext-detail="${CSS.escape(id)}"]`);
        if (!panel) return;
        const wasHidden = panel.classList.contains('hidden');
        panel.classList.toggle('hidden');
        if (wasHidden && !panel.dataset.loaded) {
          panel.dataset.loaded = '1';
          _loadDetail(id, panel);
        }
      });
    });
    list.querySelectorAll('[data-ext-action]').forEach(btn => {
      if (btn.tagName === 'INPUT') return; // checkbox handled below
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = btn.dataset.extId;
        const action = btn.dataset.extAction;
        try {
          if (action === 'toggle') {
            const enable = btn.dataset.extEnable === '1';
            await _post(`/extensions/${encodeURIComponent(id)}/${enable ? 'enable' : 'disable'}`, enable ? 'enable' : 'disable');
            _setMsg(`${enable ? 'Enabled' : 'Disabled'} ${id}`, 'ok');
          } else if (action === 'reload') {
            await _post(`/extensions/${encodeURIComponent(id)}/reload`, 'reload');
            _setMsg(`Reloaded ${id}`, 'ok');
          } else if (action === 'uninstall') {
            if (!await uiModule.styledConfirm(`Uninstall extension "${id}"? This removes it from the registry.`, { confirmText: 'Uninstall', danger: true })) return;
            await _post(`/extensions/${encodeURIComponent(id)}/uninstall`, 'uninstall');
            _setMsg(`Uninstalled ${id}`, 'ok');
          }
          loadExtensions();
          _emitChanged();
        } catch (e) {
          _setMsg(String(e.message || e));
        }
      });
    });
    list.querySelectorAll('input[data-ext-action="sidebar"]').forEach(cb => {
      cb.addEventListener('change', async (ev) => {
        ev.stopPropagation();
        const id = cb.dataset.extId;
        try {
          const res = await fetch(`${API}/extensions/${encodeURIComponent(id)}/sidebar-visible`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ visible: cb.checked }),
          });
          const data = await _jsonOrThrow(res, 'sidebar-visible');
          _setMsg(`${data.sidebar_visible ? 'Surfaced' : 'Hidden'} ${id} in sidebar`, 'ok');
        } catch (e) {
          _setMsg(String(e.message || e));
          cb.checked = !cb.checked; // revert on failure
        }
      });
      cb.addEventListener('click', (ev) => ev.stopPropagation());
    });
  } catch (e) {
    list.innerHTML = `<div style="font-size:11px;color:#a04040;padding:12px;">${uiModule.esc(String(e.message || e))}</div>`;
  }
}

async function rescan() {
  _setMsg('Rescanning…');
  try {
    const data = await _post('/rescan', 'rescan');
    _setMsg(`Rescan complete: ${data.count} found`, 'ok');
    loadExtensions();
          _emitChanged();
  } catch (e) {
    _setMsg(String(e.message || e));
  }
}

async function installFromGit() {
  const input = document.getElementById('ext-install-git-url');
  const btn = document.getElementById('ext-install-git-btn');
  if (!input || !btn) return;
  const url = (input.value || '').trim();
  if (!url) { _setInstallMsg('Paste a git URL first.', 'err'); return; }
  btn.disabled = true;
  _setInstallMsg('Cloning…');
  try {
    const res = await fetch(`${API}/install/git`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await _jsonOrThrow(res, 'install git');
    _setInstallMsg(`Installed ${data.id} v${data.version || '?'}${data.is_enabled ? ' (enabled)' : ''}.`, 'ok');
    input.value = '';
    loadExtensions();
          _emitChanged();
  } catch (e) {
    _setInstallMsg(String(e.message || e), 'err');
  } finally {
    btn.disabled = false;
  }
}

async function installFromZip(file) {
  const btn = document.getElementById('ext-install-zip-btn');
  if (!file) { _setInstallMsg('Pick a .zip file first.', 'err'); return; }
  if (btn) btn.disabled = true;
  _setInstallMsg(`Uploading ${file.name} (${(file.size / 1024).toFixed(1)} KB)…`);
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const res = await fetch(`${API}/install/zip`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    const data = await _jsonOrThrow(res, 'install zip');
    _setInstallMsg(`Installed ${data.id} v${data.version || '?'}${data.is_enabled ? ' (enabled)' : ''}.`, 'ok');
    const fileInput = document.getElementById('ext-install-zip-file');
    if (fileInput) fileInput.value = '';
    loadExtensions();
          _emitChanged();
  } catch (e) {
    _setInstallMsg(String(e.message || e), 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
}

let _wired = false;
export function initExtensionsPanel() {
  if (_wired) return;
  _wired = true;

  const rescanBtn = document.getElementById('ext-panel-rescan-btn');
  if (rescanBtn) rescanBtn.addEventListener('click', rescan);

  const gitBtn = document.getElementById('ext-install-git-btn');
  if (gitBtn) gitBtn.addEventListener('click', installFromGit);
  const gitInput = document.getElementById('ext-install-git-url');
  if (gitInput) {
    gitInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); installFromGit(); }
    });
  }
  const zipBtn = document.getElementById('ext-install-zip-btn');
  if (zipBtn) zipBtn.addEventListener('click', () => {
    const f = document.getElementById('ext-install-zip-file')?.files?.[0];
    installFromZip(f);
  });

  document.querySelectorAll('[data-settings-tab="extensions"]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (!window._isAdmin) return;
      loadExtensions();
          _emitChanged();
    });
  });
}

export default { loadExtensions, rescan, initExtensionsPanel };
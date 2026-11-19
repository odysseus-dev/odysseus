// Legacy integrations list/form binder, still exposed as
// settingsModule.initIntegrations() for admin.js. Extracted verbatim.

import { byId as el } from '../dom.js';
import { notifyIntegrationsChanged } from './events.js';

export async function initIntegrations() {
  const listEl = el('integrations-list');
  const formCard = el('integration-form-card');
  const addBtn = el('intg-add-btn');
  if (!listEl || !formCard) return;

  const presetSel = el('intg-preset');
  const nameIn = el('intg-name');
  const urlIn = el('intg-url');
  const authTypeSel = el('intg-auth-type');
  const authHeaderRow = el('intg-auth-header-row');
  const authHeaderIn = el('intg-auth-header');
  const keyIn = el('intg-key');
  const descIn = el('intg-description');
  const saveBtn = el('intg-save-btn');
  const cancelBtn = el('intg-cancel-btn');
  const testBtn = el('intg-test-btn');
  const statusEl = el('intg-status');
  const formTitle = el('integration-form-title');

  let editingId = null;
  let presets = {};

  // Presets where the secret is embedded in the URL — no separate key or
  // auth header is used, so hiding those fields avoids confusion.
  const URL_AUTH_PRESETS = ['discord_webhook'];

  // Toggle auth header + key row visibility based on auth type and preset.
  function syncAuthRow() {
    const v = authTypeSel.value;
    authHeaderRow.style.display = (v === 'header' || v === 'query') ? 'flex' : 'none';
    if (v === 'query') authHeaderIn.placeholder = 'api_key';
    else authHeaderIn.placeholder = 'X-Auth-Token';
    const keyRow = keyIn?.closest('.settings-row');
    if (keyRow) keyRow.style.display = URL_AUTH_PRESETS.includes(presetSel?.value) ? 'none' : '';
  }
  authTypeSel.addEventListener('change', syncAuthRow);

  // Load presets
  try {
    const res = await fetch('/api/auth/integrations/presets', { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      presets = data.presets || {};
      for (const [key, preset] of Object.entries(presets)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = preset.name || key;
        presetSel.appendChild(opt);
      }
    }
  } catch (e) {}

  // Preset auto-fill
  presetSel.addEventListener('change', () => {
    const p = presets[presetSel.value];
    if (!p) return;
    nameIn.value = p.name || '';
    authTypeSel.value = p.auth_type || 'none';
    authHeaderIn.value = p.auth_header || '';
    descIn.value = p.description || '';
    syncAuthRow();
  });

  // Render list
  async function renderList() {
    try {
      const res = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
      if (!res.ok) { listEl.innerHTML = '<div style="padding:12px;opacity:0.5;font-size:12px;">Admin access required</div>'; return; }
      const data = await res.json();
      const items = data.integrations || [];
      if (!items.length) {
        listEl.innerHTML = '<div style="padding:12px;opacity:0.5;font-size:12px;text-align:center;">No integrations configured</div>';
        return;
      }
      listEl.innerHTML = items.map(i => `
        <div class="admin-card" style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
          <div style="flex:1;min-width:0;">
            <div style="font-size:13px;font-weight:600;">${_esc(i.name || i.id)}</div>
            <div style="font-size:11px;opacity:0.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(i.base_url || '')}</div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0;">
            <button class="admin-btn-sm intg-edit-btn" data-id="${i.id}" style="font-size:11px;">Edit</button>
            <button class="admin-btn-sm intg-del-btn" data-id="${i.id}" style="font-size:11px;opacity:0.6;">Del</button>
          </div>
        </div>
      `).join('');
      listEl.querySelectorAll('.intg-edit-btn').forEach(b => b.addEventListener('click', () => startEdit(b.dataset.id)));
      listEl.querySelectorAll('.intg-del-btn').forEach(b => b.addEventListener('click', () => doDelete(b.dataset.id)));
    } catch (e) { listEl.innerHTML = '<div style="padding:12px;color:var(--red);font-size:12px;">Failed to load</div>'; }
  }

  function _esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  // Start editing
  async function startEdit(id) {
    editingId = id;
    formTitle.textContent = 'Edit Integration';
    // Fetch full data (with unmasked key from a dedicated edit fetch — we'll just load what we have)
    try {
      const res = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
      const data = await res.json();
      const item = (data.integrations || []).find(i => i.id === id);
      if (!item) return;
      presetSel.value = item.preset || '';
      nameIn.value = item.name || '';
      urlIn.value = item.base_url || '';
      authTypeSel.value = item.auth_type || 'none';
      authHeaderIn.value = item.auth_header || '';
      keyIn.value = ''; // masked — user re-enters if changing
      keyIn.placeholder = item.api_key ? 'Leave blank to keep current' : 'API key or token';
      descIn.value = item.description || '';
      syncAuthRow();
      formCard.style.display = '';
    } catch (e) {}
  }

  // Show add form
  addBtn.addEventListener('click', () => {
    editingId = null;
    formTitle.textContent = 'Add Integration';
    presetSel.value = '';
    nameIn.value = '';
    urlIn.value = '';
    authTypeSel.value = 'header';
    authHeaderIn.value = '';
    keyIn.value = '';
    keyIn.placeholder = 'API key or token';
    descIn.value = '';
    statusEl.textContent = '';
    syncAuthRow();
    formCard.style.display = '';
  });

  cancelBtn.addEventListener('click', () => {
    formCard.style.display = 'none';
    statusEl.textContent = '';
  });

  // Save
  saveBtn.addEventListener('click', async () => {
    const payload = {
      name: nameIn.value.trim(),
      base_url: urlIn.value.trim().replace(/\/+$/, ''),
      auth_type: authTypeSel.value,
      auth_header: authHeaderIn.value.trim(),
      description: descIn.value.trim(),
    };
    if (presetSel.value) payload.preset = presetSel.value;
    if (keyIn.value.trim()) payload.api_key = keyIn.value.trim();
    if (!payload.name) { statusEl.textContent = 'Name required'; statusEl.style.color = 'var(--red)'; return; }
    if (!payload.base_url) { statusEl.textContent = 'URL required'; statusEl.style.color = 'var(--red)'; return; }

    try {
      const url = editingId ? `/api/auth/integrations/${editingId}` : '/api/auth/integrations';
      const method = editingId ? 'PUT' : 'POST';
      const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), credentials: 'same-origin' });
      if (res.ok) {
        statusEl.textContent = 'Saved';
        statusEl.style.color = 'var(--green, #98c379)';
        formCard.style.display = 'none';
        await renderList();
        notifyIntegrationsChanged();
      } else {
        const err = await res.json().catch(() => ({}));
        statusEl.textContent = err.detail || 'Save failed';
        statusEl.style.color = 'var(--red)';
      }
    } catch (e) {
      statusEl.textContent = 'Error saving';
      statusEl.style.color = 'var(--red)';
    }
  });

  // Test
  testBtn.addEventListener('click', async () => {
    if (!editingId) { statusEl.textContent = 'Save first, then test'; statusEl.style.color = 'var(--fg)'; return; }
    statusEl.textContent = 'Testing...';
    statusEl.style.color = 'var(--fg)';
    try {
      const res = await fetch(`/api/auth/integrations/${editingId}/test`, { method: 'POST', credentials: 'same-origin' });
      const data = await res.json();
      statusEl.textContent = data.message || (data.ok ? 'OK' : 'Failed');
      statusEl.style.color = data.ok ? 'var(--green, #98c379)' : 'var(--red)';
    } catch (e) {
      statusEl.textContent = 'Connection failed';
      statusEl.style.color = 'var(--red)';
    }
  });

  // Delete
  async function doDelete(id) {
    if (!await window.styledConfirm('Delete this integration?', { confirmText: 'Delete', danger: true })) return;
    try {
      await fetch(`/api/auth/integrations/${id}`, { method: 'DELETE', credentials: 'same-origin' });
      if (editingId === id) { formCard.style.display = 'none'; editingId = null; }
      await renderList();
      notifyIntegrationsChanged();
    } catch (e) {}
  }

  syncAuthRow();
  renderList();
}

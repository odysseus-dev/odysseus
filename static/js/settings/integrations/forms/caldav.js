// CalDAV calendar integration form. Extracted from the unified integrations host.

import { byId as el } from '../../dom.js';
import { notifyIntegrationsChanged } from '../events.js';

// ── CalDAV form (supports add + edit per account) ──
export async function showCalDavForm({ formEl, renderList, closeForm }, editId) {
  const isNew = !editId || editId === 'new';
  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>${isNew ? 'Add CalDAV Calendar' : 'Edit CalDAV Calendar'}</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label" for="uf-caldav-label">Label</label><input id="uf-caldav-label" class="settings-input" placeholder="e.g. Work, Personal"></div>
          <div class="settings-row"><label class="settings-label" for="uf-caldav-url">Server URL</label><input id="uf-caldav-url" class="settings-input" placeholder="https://www.google.com/calendar/dav/you@gmail.com/user/" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-caldav-user">Username</label><input id="uf-caldav-user" class="settings-input" placeholder="you@example.com" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-caldav-pass">Password</label><input id="uf-caldav-pass" class="settings-input" type="password" placeholder="${isNew ? '' : 'Leave blank to keep existing'}"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-caldav-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-caldav-test" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Test</button>
            <button class="admin-btn-add" id="uf-caldav-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save</button>
            <button class="admin-btn-add" id="uf-caldav-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
          </div>
        </div>
      </div>`;

  if (!isNew) {
    try {
      const r = await fetch('/api/calendar/config/accounts', { credentials: 'same-origin' });
      const d = await r.json();
      const acc = (d.accounts || []).find(a => a.id === editId);
      if (acc) {
        el('uf-caldav-label').value = acc.label || '';
        el('uf-caldav-url').value = acc.url || '';
        el('uf-caldav-user').value = acc.username || '';
      }
    } catch (_) {}
  }

  el('uf-caldav-cancel').addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });

  const _runCalDavTest = async () => {
    const body = {
      url: el('uf-caldav-url').value.trim(),
      username: el('uf-caldav-user').value.trim(),
      password: el('uf-caldav-pass').value,
    };
    if (!isNew && !body.password) body.account_id = editId;
    try {
      const r = await fetch('/api/calendar/test', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      return await r.json();
    } catch (e) {
      return { ok: false, error: 'Network error: ' + e.message };
    }
  };

  const _setCalDavMsg = (text, ok) => {
    const msg = el('uf-caldav-msg');
    msg.textContent = text;
    msg.style.color = ok ? 'var(--green, #50fa7b)' : 'var(--red)';
  };

  el('uf-caldav-save').addEventListener('click', async () => {
    _setCalDavMsg('Testing…', true);
    el('uf-caldav-msg').style.color = '';
    const d = await _runCalDavTest();
    if (!d.ok) {
      _setCalDavMsg(d.error || 'Connection failed — not saved', false);
      return;
    }
    try {
      const payload = {
        label: el('uf-caldav-label').value.trim(),
        url: el('uf-caldav-url').value.trim(),
        username: el('uf-caldav-user').value.trim(),
        password: el('uf-caldav-pass').value,
      };
      let resp;
      if (isNew) {
        resp = await fetch('/api/calendar/config/accounts', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        resp = await fetch(`/api/calendar/config/accounts/${editId}`, {
          method: 'PUT', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        _setCalDavMsg(err.detail || 'Save failed', false);
        return;
      }
      _setCalDavMsg('Saved', true);
      formEl.style.display = 'none';
      await renderList();
      notifyIntegrationsChanged();
    } catch (_) {
      _setCalDavMsg('Save failed', false);
    }
  });

  el('uf-caldav-test').addEventListener('click', async () => {
    _setCalDavMsg('Testing…', true);
    el('uf-caldav-msg').style.color = '';
    const d = await _runCalDavTest();
    _setCalDavMsg(d.ok ? 'Connected' : (d.error || 'Failed'), d.ok);
  });
}

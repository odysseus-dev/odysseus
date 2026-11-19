// Vault integration form. Extracted from the unified integrations host.

import { byId as el } from '../../dom.js';

// ── Vaultwarden form ──
export async function showVaultForm({ formEl, renderList, closeForm }) {
  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px">Vaultwarden (Password Vault)</h2>
        <div id="uf-vault-status" style="font-size:11px;opacity:0.7;margin-bottom:8px">Loading...</div>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label" for="uf-vault-url">Server URL</label><input id="uf-vault-url" class="settings-input" placeholder="https://vault.example.com" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-vault-email">Email</label><input id="uf-vault-email" class="settings-input" placeholder="you@example.com" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-vault-pass">Master Password</label><input id="uf-vault-pass" class="settings-input" type="password" placeholder="Only required for Login / Unlock"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap;">
            <span id="uf-vault-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-vault-save" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save Config</button>
            <button class="admin-btn-add" id="uf-vault-login" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Login</button>
            <button class="admin-btn-add" id="uf-vault-unlock" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Unlock</button>
            <button class="admin-btn-add" id="uf-vault-lock" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Lock</button>
            <button class="admin-btn-add" id="uf-vault-logout" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Logout</button>
            <button class="admin-btn-add" id="uf-vault-cancel" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
          </div>
          <div style="font-size:10px;opacity:0.5;margin-top:6px;line-height:1.4">
            <strong>Login</strong> registers this device with your Vaultwarden account (once per account).<br>
            <strong>Unlock</strong> decrypts the vault — required after restart or Lock. Session is saved so the assistant can read passwords.
          </div>
        </div>
      </div>`;

  const msg = (text, color) => {
    const m = el('uf-vault-msg');
    m.textContent = text || '';
    m.style.color = color || '';
  };

  async function refreshStatus() {
    try {
      const r = await fetch('/api/vault/config', { credentials: 'same-origin' });
      const d = await r.json();
      el('uf-vault-url').value = d.server_url || '';
      el('uf-vault-email').value = d.email || '';
      const installed = d.bw_installed;
      const parts = [];
      parts.push(installed ? 'bw CLI: installed' : 'bw CLI: NOT installed (install nodejs-bitwarden-cli)');
      parts.push(d.unlocked ? 'Status: UNLOCKED' : 'Status: locked');
      if (d.unlocked_at) parts.push(`Last unlock: ${d.unlocked_at.replace('T',' ').slice(0,19)}`);
      const statusEl = el('uf-vault-status');
      statusEl.textContent = parts.join(' — ');
      statusEl.style.color = !installed ? 'var(--red)' : d.unlocked ? 'var(--green,#50fa7b)' : '';
    } catch (_) {
      el('uf-vault-status').textContent = 'Failed to load vault status';
    }
  }
  await refreshStatus();

  el('uf-vault-cancel').addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });

  el('uf-vault-save').addEventListener('click', async () => {
    msg('Saving...');
    try {
      const r = await fetch('/api/vault/config', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_url: el('uf-vault-url').value, email: el('uf-vault-email').value }),
      });
      const d = await r.json();
      if (d.ok) { msg('Saved', 'var(--green,#50fa7b)'); await refreshStatus(); await renderList(); }
      else msg(d.error || 'Failed', 'var(--red)');
    } catch (e) { msg('Error: ' + e.message, 'var(--red)'); }
  });

  el('uf-vault-login').addEventListener('click', async () => {
    const email = el('uf-vault-email').value.trim();
    const pass = el('uf-vault-pass').value;
    if (!email || !pass) { msg('Email + master password required', 'var(--red)'); return; }
    msg('Logging in...');
    try {
      const r = await fetch('/api/vault/login', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, master_password: pass }),
      });
      const d = await r.json();
      if (d.ok) {
        msg(d.already ? 'Already logged in — use Unlock' : 'Logged in', 'var(--green,#50fa7b)');
        el('uf-vault-pass').value = '';
        await refreshStatus(); await renderList();
      } else msg(d.error || 'Login failed', 'var(--red)');
    } catch (e) { msg('Error: ' + e.message, 'var(--red)'); }
  });

  el('uf-vault-unlock').addEventListener('click', async () => {
    const pass = el('uf-vault-pass').value;
    if (!pass) { msg('Master password required', 'var(--red)'); return; }
    msg('Unlocking...');
    try {
      const r = await fetch('/api/vault/unlock', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ master_password: pass }),
      });
      const d = await r.json();
      if (d.ok) {
        msg('Vault unlocked', 'var(--green,#50fa7b)');
        el('uf-vault-pass').value = '';
        await refreshStatus(); await renderList();
      } else msg(d.error || 'Unlock failed', 'var(--red)');
    } catch (e) { msg('Error: ' + e.message, 'var(--red)'); }
  });

  el('uf-vault-lock').addEventListener('click', async () => {
    msg('Locking...');
    try {
      await fetch('/api/vault/lock', { method: 'POST', credentials: 'same-origin' });
      msg('Locked', 'var(--green,#50fa7b)');
      await refreshStatus(); await renderList();
    } catch (e) { msg('Error: ' + e.message, 'var(--red)'); }
  });

  el('uf-vault-logout').addEventListener('click', async () => {
    if (!await window.styledConfirm('Log out of Bitwarden CLI? You\'ll need to re-enter your master password to log back in.', { confirmText: 'Log out' })) return;
    msg('Logging out...');
    try {
      await fetch('/api/vault/logout', { method: 'POST', credentials: 'same-origin' });
      msg('Logged out', 'var(--green,#50fa7b)');
      await refreshStatus(); await renderList();
    } catch (e) { msg('Error: ' + e.message, 'var(--red)'); }
  });
}

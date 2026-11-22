// Account panel: profile, password, two-factor and the auth policy they
// validate against. Extracted verbatim from settings.js; the policy state is
// private to this module.

import { byId as el } from './dom.js';
import { initSelfApiTokens } from './apiTokens.js';
import { esc } from './html.js';
import { SESSION_ENDED_EVENT } from '../authStatus.js';
import uiModule from '../ui.js?v=20260916largetoolscroll1';

let _authPolicy = { password_min_length: 8 };

function safeRasterDataUrl(raw) {
  const value = String(raw || '').trim();
  return /^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(value) ? value : '';
}

export function initAccount() {
  // Populate user info
  fetch('/api/auth/status', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => {
      const nameEl = el('settings-account-username');
      const roleEl = el('settings-account-role');
      const avatarEl = el('settings-account-avatar');
      if (nameEl) nameEl.textContent = d.username || 'Unknown';
      if (roleEl) roleEl.textContent = d.is_admin ? 'Admin' : 'User';
      if (avatarEl) {
        const initial = (d.username || '?')[0].toUpperCase();
        avatarEl.textContent = initial;
      }
      const tokenCard = el('settings-api-tokens-card');
      if (tokenCard && d.authenticated) {
        tokenCard.style.display = '';
        initSelfApiTokens(!!d.is_admin);
      }
    }).catch(() => {});

  // Update password placeholder and policy from server
  fetch('/api/auth/policy', { credentials: 'same-origin' })
    .then(r => r.ok ? r.json() : null)
    .then(policy => {
      if (!policy) return;
      _authPolicy = policy;
      const pwNew = el('settings-pw-new');
      if (pwNew) pwNew.placeholder = `New password (min ${policy.password_min_length})`;
    }).catch(() => {});

  // Change password
  const saveBtn = el('settings-pw-save');
  const msgEl = el('settings-pw-msg');
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const cur = el('settings-pw-current').value;
      const nw = el('settings-pw-new').value;
      const conf = el('settings-pw-confirm').value;
      msgEl.style.color = '';
      if (!cur || !nw) { msgEl.textContent = 'Fill in all fields'; msgEl.style.color = 'var(--red)'; return; }
      if (nw.length < _authPolicy.password_min_length) { msgEl.textContent = `Min ${_authPolicy.password_min_length} characters`; msgEl.style.color = 'var(--red)'; return; }
      if (nw !== conf) { msgEl.textContent = 'Passwords don\'t match'; msgEl.style.color = 'var(--red)'; return; }
      saveBtn.disabled = true;
      try {
        const res = await fetch('/api/auth/change-password', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: cur, new_password: nw })
        });
        if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Failed'); }
        msgEl.style.color = 'var(--green)';
        msgEl.textContent = 'Password updated';
        el('settings-pw-current').value = '';
        el('settings-pw-new').value = '';
        el('settings-pw-confirm').value = '';
      } catch (e) {
        msgEl.style.color = 'var(--red)';
        msgEl.textContent = e.message;
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  // ── Two-Factor Authentication ──
  const tfaContent = el('settings-2fa-content');
  if (tfaContent) {
    async function render2FA() {
      try {
        const res = await fetch('/api/auth/2fa/status', { credentials: 'same-origin' });
        const data = await res.json();
        if (data.enabled) {
          // 2FA is ON — show disable option
          tfaContent.innerHTML = `
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
              <span style="color:var(--color-save-green, #4caf50);font-size:12px;font-weight:600;">&#x2713; Enabled</span>
              <span style="font-size:11px;opacity:0.5;">Authenticator app required on login</span>
            </div>
            <input id="tfa-disable-pw" type="password" placeholder="Enter password to disable" autocomplete="current-password" style="padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);font-family:inherit;font-size:12px;width:100%;box-sizing:border-box;margin-bottom:6px;">
            <div class="settings-row" style="justify-content:flex-end;">
              <span id="tfa-msg" style="font-size:11px;margin-right:auto;"></span>
              <button class="admin-btn-add" id="tfa-disable-btn" style="opacity:0.7;">Disable 2FA</button>
            </div>`;
          el('tfa-disable-btn').addEventListener('click', async () => {
            const pw = el('tfa-disable-pw').value;
            const msg = el('tfa-msg');
            if (!pw) { msg.textContent = 'Enter your password'; msg.style.color = 'var(--red)'; return; }
            try {
              const r = await fetch('/api/auth/2fa/disable', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw })
              });
              if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
              render2FA();
            } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
          });
        } else {
          // 2FA is OFF — show setup button
          tfaContent.innerHTML = `
            <div style="font-size:12px;opacity:0.6;margin-bottom:8px;">Add an extra layer of security with an authenticator app (Aegis, Google Authenticator, etc.)</div>
            <div class="settings-row" style="justify-content:flex-end;">
              <span id="tfa-msg" style="font-size:11px;margin-right:auto;"></span>
              <button class="admin-btn-add" id="tfa-setup-btn">Set Up 2FA</button>
            </div>`;
          el('tfa-setup-btn').addEventListener('click', async () => {
            const msg = el('tfa-msg');
            try {
              const r = await fetch('/api/auth/2fa/setup', { method: 'POST', credentials: 'same-origin' });
              if (!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
              const setup = await r.json();
              const qrCode = safeRasterDataUrl(setup.qr_code);
              // Show QR code + manual secret + verify input
              tfaContent.innerHTML = `
                <div style="text-align:center;margin-bottom:12px;">
                  ${qrCode ? `<img src="${esc(qrCode)}" alt="QR Code" style="border-radius:8px;max-width:200px;">` : ''}
                </div>
                <div style="font-size:11px;opacity:0.5;text-align:center;margin-bottom:8px;">
                  Scan with your authenticator app, or enter manually:
                </div>
                <div style="font-family:monospace;font-size:12px;text-align:center;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;margin-bottom:12px;word-break:break-all;user-select:all;cursor:text;">${esc(setup.secret)}</div>
                <input id="tfa-verify-code" type="text" placeholder="Enter 6-digit code to verify" autocomplete="one-time-code" inputmode="numeric" maxlength="8" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);font-family:inherit;font-size:13px;box-sizing:border-box;text-align:center;letter-spacing:3px;margin-bottom:6px;">
                <div class="settings-row" style="justify-content:flex-end;">
                  <span id="tfa-msg" style="font-size:11px;margin-right:auto;"></span>
                  <button class="admin-btn-add" id="tfa-cancel-btn" style="opacity:0.5;">Cancel</button>
                  <button class="admin-btn-add" id="tfa-verify-btn">Verify & Enable</button>
                </div>`;
              el('tfa-verify-code').focus();
              el('tfa-cancel-btn').addEventListener('click', () => render2FA());
              el('tfa-verify-btn').addEventListener('click', async () => {
                const code = el('tfa-verify-code').value.trim();
                const vmsg = el('tfa-msg');
                if (!code) { vmsg.textContent = 'Enter the code'; vmsg.style.color = 'var(--red)'; return; }
                try {
                  const vr = await fetch('/api/auth/2fa/confirm', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ code })
                  });
                  if (!vr.ok) { const d = await vr.json(); throw new Error(d.detail || 'Invalid code'); }
                  const result = await vr.json();
                  // Show backup codes
                  const codes = result.backup_codes || [];
                  tfaContent.innerHTML = `
                    <div style="color:var(--color-save-green, #4caf50);font-size:13px;font-weight:600;margin-bottom:8px;">&#x2713; 2FA Enabled!</div>
                    <div style="font-size:12px;opacity:0.7;margin-bottom:8px;">Save these backup codes somewhere safe. Each can be used once if you lose your authenticator:</div>
                    <div style="font-family:monospace;font-size:12px;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;columns:2;column-gap:16px;margin-bottom:8px;">${codes.map(c => '<div style="margin-bottom:2px;">' + c + '</div>').join('')}</div>
                    <button class="admin-btn-add" id="tfa-done-btn">Done</button>`;
                  el('tfa-done-btn').addEventListener('click', () => render2FA());
                } catch (e) { vmsg.textContent = e.message; vmsg.style.color = 'var(--red)'; }
              });
            } catch (e) { msg.textContent = e.message; msg.style.color = 'var(--red)'; }
          });
        }
      } catch (_) {
        tfaContent.innerHTML = '<div style="font-size:11px;opacity:0.4;">Could not load 2FA status</div>';
      }
    }
    render2FA();
  }

  // Logout
  const logoutBtn = el('settings-logout-btn');
  if (logoutBtn) {
    logoutBtn.addEventListener('mouseenter', () => { logoutBtn.style.opacity = '1'; logoutBtn.style.borderColor = 'var(--red)'; logoutBtn.style.color = 'var(--red)'; });
    logoutBtn.addEventListener('mouseleave', () => { logoutBtn.style.opacity = ''; logoutBtn.style.borderColor = ''; logoutBtn.style.color = ''; });
    logoutBtn.addEventListener('click', async () => {
      if (logoutBtn.disabled) return;
      logoutBtn.disabled = true;
      let loggedOut = false;
      try {
        const res = await fetch('/api/auth/logout', { method: 'POST' });
        // An already-expired session (401) is logged out as far as this tab is concerned.
        loggedOut = res.ok || res.status === 401;
      } catch (_) {}
      if (!loggedOut) {
        logoutBtn.disabled = false;
        if (uiModule && uiModule.showToast) uiModule.showToast('Could not log out. You are still signed in.');
        return;
      }
      // Keep recovery active until logout is acknowledged. Then stop draft
      // writers before clearing storage and navigating away.
      window.dispatchEvent(new CustomEvent(SESSION_ENDED_EVENT));
      // SECURITY: wipe all client-side state on logout so the next user that
      // signs in on this browser doesn't inherit the previous account's
      // session id, last-used model, draft chat input, or any cached lists.
      // Keep "odysseus-last-user" so the login form remembers the username
      // (if "Remember me" was on). Without this the chat composer pre-loaded
      // the previous user's last model into a fresh session, which read as
      // cross-account leakage.
      try {
        const _keepKeys = new Set(['odysseus-last-user', SESSION_ENDED_EVENT]);
        const _toRemove = [];
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && !_keepKeys.has(k)) _toRemove.push(k);
        }
        _toRemove.forEach(k => localStorage.removeItem(k));
      } catch (_) {}
      try { sessionStorage.clear(); } catch (_) {}
      // Other tabs share the account cookie but have their own sessionStorage.
      // Leave a marker for live tabs and tabs that load the app later.
      try { localStorage.setItem(SESSION_ENDED_EVENT, String(Date.now())); } catch (_) {}
      window.location.replace('/login');
    });
  }
}

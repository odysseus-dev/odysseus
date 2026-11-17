// Email account integration form (IMAP/SMTP and Google OAuth). Extracted from
// the unified integrations host; setNotice reports the saved account back to it.

import { esc } from '../../html.js';
import { byId as el } from '../../dom.js';
import uiModule from '../../../ui.js?v=20260916largetoolscroll1';
import { bindMenuDismiss } from '../../../escMenuStack.js';
import { notifyIntegrationsChanged } from '../events.js';

// ── Email form (multi-account) ──
// When editId is a real account id, edit that row. When editId is falsy or 'new',
// create a fresh account. Posts to /api/email/accounts, never to the legacy
// /api/email/config which would overwrite the default.
export async function showEmailForm({ formEl, renderList, setNotice, closeForm }, editId) {
  const isEdit = editId && editId !== 'new' && editId !== '__email__';
  let existing = null;
  if (isEdit) {
    try {
      const r = await fetch('/api/email/accounts', { credentials: 'same-origin' });
      const d = await r.json();
      existing = (d.accounts || []).find(a => a.id === editId) || null;
    } catch (_) {}
  }
  const placeholderPass = (isEdit && existing) ? '(leave blank to keep current)' : '';
  // Small `?` indicator next to each label (native title tooltip).
  const _hint = (tip) =>
    `<span class="uf-hint" title="${esc(tip)}" aria-label="${esc(tip)}" tabindex="0" `
    + `style="display:inline-block;width:13px;height:13px;border-radius:50%;`
    + `border:1px solid currentColor;font-size:9px;line-height:11px;text-align:center;`
    + `opacity:0.45;margin-left:5px;cursor:help;vertical-align:1px;font-weight:600;">?</span>`;
  // Provider presets — picking one auto-fills IMAP + SMTP host/port.
  // Dovecot is IMAP-only here; the host is intentionally blank because
  // it may be remote (DNS, LAN, Tailscale), not localhost.
  const PROVIDERS = {
    gmail:    { label: 'Gmail',                   emailEx: 'you@gmail.com',     imap: { host: 'imap.gmail.com',           port: 993, starttls: false }, smtp: { host: 'smtp.gmail.com',     port: 465 } },
    google_workspace: { label: 'Google Workspace / .edu', emailEx: 'you@yourschool.edu', imap: { host: 'imap.gmail.com', port: 993, starttls: false }, smtp: { host: 'smtp.gmail.com', port: 587 }, oauth: 'google' },
    migadu:   { label: 'Migadu',                  emailEx: 'you@yourdomain.com', imap: { host: 'imap.migadu.com',          port: 993, starttls: false }, smtp: { host: 'smtp.migadu.com',    port: 465 } },
    icloud:   { label: 'iCloud',                  emailEx: 'you@icloud.com',    imap: { host: 'imap.mail.me.com',         port: 993, starttls: false }, smtp: { host: 'smtp.mail.me.com',   port: 587 } },
    outlook:  { label: 'Outlook / Office 365',    emailEx: 'you@outlook.com',   imap: { host: 'outlook.office365.com',    port: 993, starttls: false }, smtp: { host: 'smtp.office365.com', port: 587 } },
    fastmail: { label: 'Fastmail',                emailEx: 'you@fastmail.com',  imap: { host: 'imap.fastmail.com',        port: 993, starttls: false }, smtp: { host: 'smtp.fastmail.com',  port: 465 } },
    yahoo:    { label: 'Yahoo',                   emailEx: 'you@yahoo.com',     imap: { host: 'imap.mail.yahoo.com',      port: 993, starttls: false }, smtp: { host: 'smtp.mail.yahoo.com', port: 465 } },
    dovecot:  { label: 'Dovecot IMAP (no SMTP)',  emailEx: 'you@example.com',   imap: { host: '',                         port: 31143, starttls: false }, smtp: { host: '',                   port: 465 } },
  };
  const _providerOptions = Object.entries(PROVIDERS)
    .map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join('');
  // Provider logos — small SVGs the custom dropdown renders next to each
  // option. Letter-in-brand-color circle for known providers; outline
  // envelope for "Custom…". Inline SVG (no external assets, no emoji).
  const _letterLogo = (letter, bg) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" style="flex-shrink:0"><circle cx="12" cy="12" r="11" fill="${bg}"/><text x="12" y="16.5" font-size="13" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif">${letter}</text></svg>`;
  const _customLogo = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex-shrink:0;opacity:0.7"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>';
  const PROV_LOGO = {
    '':       _customLogo,
    gmail:    _letterLogo('G', '#ea4335'),
    google_workspace: _letterLogo('G', '#ea4335'),
    migadu:   _letterLogo('M', '#3aa39d'),
    icloud:   _letterLogo('i', '#3693f3'),
    outlook:  _letterLogo('O', '#0078d4'),
    fastmail: _letterLogo('F', '#4a5fbb'),
    yahoo:    _letterLogo('Y', '#6001d2'),
    dovecot:  _letterLogo('D', '#6b7280'),
  };
  const _provOptionRows = [['', 'Custom…'], ...Object.entries(PROVIDERS).map(([k, v]) => [k, v.label])]
    .map(([k, label]) => `<button type="button" class="ufp-option" data-value="${esc(k)}" style="display:flex;align-items:center;gap:8px;width:100%;padding:8px 10px;background:transparent;border:0;color:var(--fg);font:inherit;cursor:pointer;text-align:left;">${PROV_LOGO[k] || _customLogo}<span>${esc(label)}</span></button>`).join('');
  const _smtpSecurity = (acct) => acct?.smtp_security || ((parseInt(acct?.smtp_port || 465) === 587) ? 'starttls' : 'ssl');
  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px">${isEdit ? 'Edit' : 'Add'} Email Account</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label" for="uf-email-provider-trigger">Provider${_hint('Pick a known provider to auto-fill the IMAP and SMTP host/port. Choose Custom to type your own.')}</label>
            <div class="ufp-wrap" style="position:relative;flex:1;min-width:0;">
              <select id="uf-email-provider" tabindex="-1" aria-hidden="true" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;"><option value="">Custom…</option>${_providerOptions}</select>
              <button type="button" id="uf-email-provider-trigger" class="settings-select" style="display:flex;align-items:center;gap:8px;cursor:pointer;text-align:left;width:100%;padding-right:24px;position:relative;">
                <span class="ufp-icon" style="display:inline-flex;align-items:center;">${PROV_LOGO['']}</span>
                <span class="ufp-label" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Custom…</span>
                <span aria-hidden="true" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);opacity:0.5;font-size:10px;pointer-events:none;">▾</span>
              </button>
              <div id="uf-email-provider-menu" style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;z-index:1000;background:var(--panel);border:1px solid var(--border);border-radius:6px;max-height:280px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,0.25);">${_provOptionRows}</div>
            </div>
          </div>
          <div id="uf-email-provider-note" style="display:none;font-size:11px;line-height:1.5;padding:8px 10px;margin:2px 0 4px;border:1px solid color-mix(in srgb, var(--fg) 15%, transparent);border-left:3px solid var(--accent, var(--red));border-radius:4px;background:color-mix(in srgb, var(--fg) 4%, transparent);"></div>
          <div class="settings-row"><label class="settings-label" for="uf-email-name">Name${_hint('Optional label for this account (e.g. “Work” or “Personal”). Leave blank to use the email address.')}</label><input id="uf-email-name" class="settings-input" placeholder="(optional — leave blank to use email)"></div>
          <div class="settings-row"><label class="settings-label" for="uf-email-from">Email${_hint('Your email address. Used as the From: header on outgoing mail and as the display label when Name is blank.')}</label><input id="uf-email-from" class="settings-input" placeholder="you@example.com" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-display-name">Display Name${_hint('Your name as it appears in the From: field of emails you send, e.g. Jane Smith. Auto-filled from Google during OAuth.')}</label><input id="uf-display-name" class="settings-input" placeholder="Your Name"></div>
          <div id="uf-oauth-section" style="display:none;margin:8px 0;padding:10px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb,var(--accent,#50fa7b) 6%,transparent)">
            <div style="font-size:11px;font-weight:600;margin-bottom:6px">Google OAuth2 — required for Workspace / .edu accounts</div>
            <div id="uf-oauth-status" style="font-size:11px;opacity:0.7;margin-bottom:6px">${existing && existing.oauth_provider === 'google' ? '✓ Connected via Google OAuth' : 'Not connected — click below to authorize'}</div>
            <button type="button" id="uf-oauth-btn" class="admin-btn-add" style="font-size:11px">${existing && existing.oauth_provider === 'google' ? 'Reconnect with Google' : 'Connect with Google'}</button>
          </div>
          <div style="font-size:11px;font-weight:600;opacity:0.6;margin:4px 0 2px;display:flex;align-items:center;gap:5px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;" aria-hidden="true"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>IMAP (Receiving)</div>
          <div class="settings-row"><label class="settings-label" for="uf-imap-host">Host${_hint('Your IMAP server, e.g. imap.gmail.com, imap.migadu.com, a LAN host, or a Tailscale IP for Dovecot.')}</label><input id="uf-imap-host" class="settings-input" placeholder="imap.example.com" required aria-required="true"></div>
          <div class="settings-row"><label class="settings-label" for="uf-imap-port">Port${_hint('993 for IMAPS (most providers), 143 for plain or STARTTLS. Local servers often use a custom port like 31143.')}</label><input id="uf-imap-port" class="settings-input" type="number" placeholder="993" style="max-width:100px"></div>
          <div class="settings-row"><label class="settings-label" for="uf-imap-user">Username${_hint('Yes — your full email address goes here too (e.g. you@gmail.com). Same as the Email field above for almost every provider.')}</label><input id="uf-imap-user" class="settings-input" placeholder="you@example.com"></div>
          <div class="uf-password-section"><div class="settings-row"><label class="settings-label" for="uf-imap-pass">Password${_hint('For Gmail, iCloud, and Yahoo: paste your App Password (NOT your normal account password). For Migadu and Fastmail, your mailbox password usually works. Outlook / Office 365 generally requires OAuth and will not work with this password form.')}</label><input id="uf-imap-pass" class="settings-input" type="password" placeholder="${placeholderPass}"></div></div>
          <div class="settings-row"><label class="settings-label" for="uf-imap-starttls">STARTTLS${_hint('Turn ON for port 143/587 to upgrade plain to TLS. Turn OFF for port 993 (IMAPS — already encrypted) or a local server with no TLS configured.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-imap-starttls" checked><span class="admin-slider"></span></label></div>
          <div style="font-size:11px;font-weight:600;opacity:0.6;margin:8px 0 2px;display:flex;align-items:center;gap:5px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>SMTP (Sending) <span style="font-weight:normal;opacity:0.7">— optional, leave blank for read-only</span></div>
          <div class="settings-row"><label class="settings-label" for="uf-smtp-host">Host${_hint('Your outgoing-mail server, e.g. smtp.gmail.com. Leave blank to make this account read-only.')}</label><input id="uf-smtp-host" class="settings-input" placeholder="smtp.example.com"></div>
          <div class="settings-row"><label class="settings-label" for="uf-smtp-port">Port${_hint('465 for SSL/SMTPS, 587 for STARTTLS. 25 is usually blocked by ISPs.')}</label><input id="uf-smtp-port" class="settings-input" type="number" placeholder="465" style="max-width:100px"></div>
          <div class="settings-row"><label class="settings-label" for="uf-smtp-security">Security${_hint('SSL for port 465, STARTTLS for port 587, or None for local SMTP bridges such as Proton Mail Bridge.')}</label><select id="uf-smtp-security" class="settings-select"><option value="ssl">SSL</option><option value="starttls">STARTTLS</option><option value="none">None</option></select></div>
          <div class="settings-row"><label class="settings-label" for="uf-smtp-same">Same as IMAP${_hint('Use the IMAP username and password for SMTP too (right for almost every provider). Turn off to enter separate SMTP credentials.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-smtp-same" checked><span class="admin-slider"></span></label></div>
          <div class="settings-row uf-smtp-creds"><label class="settings-label" for="uf-smtp-user">Username${_hint('Usually the same as your IMAP username (your email address).')}</label><input id="uf-smtp-user" class="settings-input"></div>
          <div class="settings-row uf-smtp-creds"><label class="settings-label" for="uf-smtp-pass">Password${_hint('Your SMTP password — often the same as your IMAP password. Outlook / Office 365 generally requires OAuth and will not work with this password form.')}</label><input id="uf-smtp-pass" class="settings-input" type="password" placeholder="${placeholderPass}"></div>
          <div class="settings-row" style="margin-top:4px"><label class="settings-label" for="uf-email-default">Default${_hint('Use this account whenever no specific account is chosen.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-email-default"><span class="admin-slider"></span></label><span style="font-size:10px;opacity:0.5;margin-left:6px">Used when nothing else is selected</span></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-email-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-email-test" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <span class="uf-email-test-ico" style="display:inline-flex;width:11px;height:11px;align-items:center;justify-content:center;">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 4 12 14.01 9 11.01"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
              </span>
              Test
            </button>
            <button class="admin-btn-add" id="uf-email-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">
              <span class="uf-email-save-ico" style="display:inline-flex;width:11px;height:11px;align-items:center;justify-content:center;">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
              </span>
              <span class="uf-email-save-label">${isEdit ? 'Save' : 'Create'}</span>
            </button>
            <button class="admin-btn-add" id="uf-email-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Cancel
            </button>
          </div>
        </div>
      </div>`;

  // Provider-specific helper notes — surfaces for providers that
  // require an app-specific password (Gmail killed basic IMAP auth
  // in 2022; iCloud + Yahoo follow the same model). The Generate
  // button opens the right page in a new tab and copies the URL for
  // mobile / cross-device flows.
  const PROVIDER_NOTES = {
    gmail: {
      title: 'Gmail needs an App Password',
      body: 'Your regular Google password won\'t work for IMAP. Generate a 16-character App Password (requires 2-Step Verification enabled) and paste it as the Password.',
      url: 'https://myaccount.google.com/apppasswords',
    },
    icloud: {
      title: 'iCloud needs an App-Specific Password',
      body: 'Sign in to your Apple ID, go to Sign-In and Security → App-Specific Passwords, and generate one (requires 2FA on your Apple ID).',
      url: 'https://account.apple.com/account/manage',
    },
    yahoo: {
      title: 'Yahoo needs an App Password',
      body: 'Generate an App Password from Yahoo Account Security (requires 2-Step Verification enabled) and paste it as the Password.',
      url: 'https://login.yahoo.com/account/security/app-passwords',
    },
    outlook: {
      title: 'Outlook / Office 365 needs OAuth',
      body: 'Microsoft disables normal password login for IMAP/SMTP in most Outlook and Microsoft 365 accounts. Odysseus does not support Microsoft OAuth/Graph mail yet, so this preset is only a placeholder for future support.',
      url: 'https://learn.microsoft.com/exchange/clients-and-mobile-in-exchange-online/disable-basic-authentication-in-exchange-online',
      linkLabel: 'Read Microsoft note',
    },
  };
  const noteEl = el('uf-email-provider-note');
  const _copyProviderUrl = async (text) => {
    const value = String(text || '');
    if (!value) return false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {
        // Fall through to the textarea path below.
      }
    }
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', 'readonly');
    ta.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;z-index:-1;';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    ta.remove();
    return ok;
  };
  if (noteEl && !noteEl._ufProviderCopyWired) {
    noteEl._ufProviderCopyWired = true;
    noteEl.addEventListener('click', async (e) => {
      const copyBtn = e.target.closest?.('.uf-prov-copy');
      if (!copyBtn || !noteEl.contains(copyBtn)) return;
      e.preventDefault();
      e.stopPropagation();
      const url = copyBtn.dataset.url || '';
      const orig = copyBtn.innerHTML;
      const ok = await _copyProviderUrl(url);
      if (!ok) {
        uiModule.showError?.('Copy failed');
        return;
      }
      uiModule.showToast?.('Copied');
      copyBtn.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied';
      setTimeout(() => {
        if (copyBtn.isConnected) copyBtn.innerHTML = orig;
      }, 1500);
    });
  }
  const _renderProviderNote = (key) => {
    const n = PROVIDER_NOTES[key];
    if (!n) { noteEl.style.display = 'none'; noteEl.innerHTML = ''; return; }
    noteEl.style.display = '';
    noteEl.innerHTML = `
        <div style="font-weight:600;margin-bottom:3px;">${esc(n.title)}</div>
        <div style="opacity:0.8;margin-bottom:6px;">${esc(n.body)}</div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
          <a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer" class="admin-btn-sm" style="background:var(--red);border-color:var(--red);color:#fff;text-decoration:none;display:inline-flex;align-items:center;gap:5px;font-weight:600;">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            ${esc(n.linkLabel || 'Generate App Password')}
          </a>
          <button type="button" class="admin-btn-sm uf-prov-copy" data-url="${esc(n.url)}" style="opacity:0.7;display:inline-flex;align-items:center;gap:5px;">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            Copy link
          </button>
        </div>`;
  };

  // Show/hide the OAuth section and password fields based on provider selection.
  function _syncOauthUI(providerKey) {
    const p = PROVIDERS[providerKey];
    const isOauth = !!(p && p.oauth);
    el('uf-oauth-section').style.display = isOauth ? '' : 'none';
    formEl.querySelectorAll('.uf-password-section').forEach(r => {
      r.style.display = isOauth ? 'none' : '';
    });
  }

  // Custom dropdown wire-up — the native <select> stays in the DOM as the
  // data source and accessibility target, but the visible UI is a button +
  // popup so each provider row can render with its SVG logo. Selecting an
  // option updates select.value and dispatches a `change` event so the
  // existing autofill handler below runs unchanged.
  (() => {
    const trigger = el('uf-email-provider-trigger');
    const menu = el('uf-email-provider-menu');
    const sel = el('uf-email-provider');
    if (!trigger || !menu || !sel) return;
    const labelEl = trigger.querySelector('.ufp-label');
    const iconEl = trigger.querySelector('.ufp-icon');
    const _setFromKey = (k) => {
      const row = menu.querySelector(`.ufp-option[data-value="${k}"]`);
      const lbl = row?.querySelector('span')?.textContent || 'Custom…';
      if (labelEl) labelEl.textContent = lbl;
      if (iconEl) iconEl.innerHTML = PROV_LOGO[k] || _customLogo;
    };
    // Menu is reused (hidden, not recreated). _closeMenu hides it and tears
    // down its outside-click listener + Escape-stack entry; bindMenuDismiss is
    // re-registered fresh on each open (see _openMenu).
    let _closeMenu = () => { menu.style.display = 'none'; };
    const _openMenu = () => {
      menu.style.display = 'block';
      // Drop-up when there's not enough room below the trigger.
      const tRect = trigger.getBoundingClientRect();
      const mRect = menu.getBoundingClientRect();
      const below = window.innerHeight - tRect.bottom;
      const above = tRect.top;
      if (mRect.height > below && above > below) {
        menu.style.top = 'auto'; menu.style.bottom = 'calc(100% + 2px)';
      } else {
        menu.style.top = 'calc(100% + 2px)'; menu.style.bottom = 'auto';
      }
      _closeMenu = bindMenuDismiss(menu, () => { menu.style.display = 'none'; }, (ev) => !menu.contains(ev.target) && ev.target !== trigger);
    };
    trigger.addEventListener('click', (e) => { e.stopPropagation(); menu.style.display === 'block' ? _closeMenu() : _openMenu(); });
    menu.querySelectorAll('.ufp-option').forEach(btn => {
      btn.addEventListener('mouseenter', () => { btn.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
      btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = btn.dataset.value || '';
        sel.value = k;
        _setFromKey(k);
        _closeMenu();
        sel.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    _setFromKey(sel.value || '');
  })();

  // Provider preset → autofill IMAP + SMTP host/port + STARTTLS, set the
  // helper note, and update the Email/Username placeholders to a
  // provider-specific example so users see the right format at a glance.
  el('uf-email-provider').addEventListener('change', (e) => {
    const key = e.target.value;
    _renderProviderNote(key);
    _syncOauthUI(key);
    const p = PROVIDERS[key];
    if (!p) return;
    el('uf-imap-host').value = p.imap.host;
    el('uf-imap-port').value = p.imap.port;
    el('uf-imap-starttls').checked = !!p.imap.starttls;
    el('uf-smtp-host').value = p.smtp.host;
    el('uf-smtp-port').value = p.smtp.port;
    el('uf-smtp-security').value = p.smtp.security || ((parseInt(p.smtp.port || 465) === 587) ? 'starttls' : 'ssl');
    if (p.emailEx) {
      el('uf-email-from').placeholder = p.emailEx;
      el('uf-imap-user').placeholder = p.emailEx;
      el('uf-smtp-user').placeholder = p.emailEx;
    }
  });

  // Init OAuth UI for accounts already connected via OAuth.
  if (existing && existing.oauth_provider === 'google') _syncOauthUI('google_workspace');

  // "Connect with Google" — save the account first, then redirect to OAuth.
  el('uf-oauth-btn').addEventListener('click', async () => {
    const body = _collectBody();
    if (!body.name) body.name = body.from_address;
    if (!body.name) { el('uf-email-msg').textContent = 'Enter a Name or Email first'; el('uf-email-msg').style.color = 'var(--red)'; return; }
    const url = isEdit ? `/api/email/accounts/${editId}` : '/api/email/accounts';
    const method = isEdit ? 'PUT' : 'POST';
    const r = await fetch(url, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (!(d.ok || d.id)) { el('uf-email-msg').textContent = d.error || 'Save failed'; el('uf-email-msg').style.color = 'var(--red)'; return; }
    const accId = isEdit ? editId : d.id;
    window.location.href = `/api/email/oauth/google/authorize?account_id=${encodeURIComponent(accId)}`;
  });

  // "Same as IMAP" toggle — hide the SMTP creds rows when on.
  const _syncSmtpSame = () => {
    const same = el('uf-smtp-same').checked;
    formEl.querySelectorAll('.uf-smtp-creds').forEach(r => {
      r.style.display = same ? 'none' : '';
    });
  };
  el('uf-smtp-same').addEventListener('change', _syncSmtpSame);
  _syncSmtpSame();
  if (existing) {
    el('uf-email-name').value = existing.name || '';
    el('uf-email-from').value = existing.from_address || '';
    el('uf-display-name').value = existing.display_name || '';
    el('uf-imap-host').value = existing.imap_host || '';
    el('uf-imap-port').value = existing.imap_port || 993;
    el('uf-imap-user').value = existing.imap_user || '';
    el('uf-imap-starttls').checked = existing.imap_starttls !== false;
    el('uf-smtp-host').value = existing.smtp_host || '';
    el('uf-smtp-port').value = existing.smtp_port || 465;
    el('uf-smtp-security').value = _smtpSecurity(existing);
    el('uf-smtp-user').value = existing.smtp_user || '';
    el('uf-email-default').checked = !!existing.is_default;
    // If the saved SMTP user matches the IMAP user, keep the "Same as
    // IMAP" toggle ON (and stay hidden). Otherwise turn it off so the
    // separate SMTP credentials are visible for editing.
    const sameCreds = !!(existing.imap_user && existing.smtp_user && existing.imap_user === existing.smtp_user);
    el('uf-smtp-same').checked = sameCreds || !existing.smtp_user;
    _syncSmtpSame();
  } else {
    el('uf-imap-port').value = 993;
    el('uf-smtp-port').value = 465;
    el('uf-smtp-security').value = 'ssl';
  }
  el('uf-email-cancel').addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });

  // Reset the Test button to neutral when the user edits any field
  // after a test — stale green/red would imply the new values were
  // tested too.
  const _resetTestBtn = () => {
    const btn = el('uf-email-test');
    if (!btn) return;
    btn.style.background = '';
    btn.style.borderColor = '';
    btn.style.color = '';
    btn.style.boxShadow = '';
    btn.style.animation = '';
    const ico = btn.querySelector('.uf-email-test-ico');
    if (ico && btn.dataset.origIco) ico.innerHTML = btn.dataset.origIco;
  };
  formEl.querySelectorAll('input, select').forEach(inp => {
    if (inp.id === 'uf-email-msg') return;
    inp.addEventListener('input', _resetTestBtn);
    inp.addEventListener('change', _resetTestBtn);
  });

  // Collect the current form values + apply the "Same as IMAP" mirror —
  // shared by both Save and Test so they agree on what's being sent.
  const _collectBody = () => {
    const body = {
      name: el('uf-email-name').value.trim(),
      from_address: el('uf-email-from').value.trim(),
      display_name: el('uf-display-name').value.trim(),
      imap_host: el('uf-imap-host').value.trim(),
      imap_port: parseInt(el('uf-imap-port').value) || 993,
      imap_user: el('uf-imap-user').value.trim(),
      imap_starttls: el('uf-imap-starttls').checked,
      smtp_host: el('uf-smtp-host').value.trim(),
      smtp_port: parseInt(el('uf-smtp-port').value) || 465,
      smtp_security: el('uf-smtp-security').value,
      smtp_user: el('uf-smtp-user').value.trim(),
      is_default: el('uf-email-default').checked,
    };
    if (el('uf-imap-pass').value) body.imap_password = el('uf-imap-pass').value;
    if (el('uf-smtp-pass').value) body.smtp_password = el('uf-smtp-pass').value;
    if (el('uf-smtp-same').checked) {
      body.smtp_user = body.imap_user;
      if (body.imap_password) body.smtp_password = body.imap_password;
    }
    return body;
  };

  // Spinner SVG kept inline so we can swap it back to the original
  // checkmark on completion. ~13px to match the button icon size.
  const _spinner = '<span style="display:inline-block;width:11px;height:11px;border-radius:50%;border:1.5px solid currentColor;border-top-color:transparent;animation:whirlpool-spin 0.7s linear infinite"></span>';
  const _checkIcon = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

  el('uf-email-test').addEventListener('click', async () => {
    const body = _collectBody();
    // Edit-mode + blank password = use the saved row's stored creds
    // via the account_id shortcut. Other overrides in the body still
    // win (server merges).
    if (isEdit && !body.imap_password) body.account_id = editId;
    const msg = el('uf-email-msg');
    const btn = el('uf-email-test');
    const ico = btn.querySelector('.uf-email-test-ico');
    btn.dataset.origIco = btn.dataset.origIco || ico.innerHTML;
    btn.disabled = true;
    // Clear any prior green/red while testing.
    btn.style.background = '';
    btn.style.borderColor = '';
    btn.style.color = '';
    btn.style.boxShadow = '';
    btn.style.animation = '';
    // Use the canonical whirlpool spinner so this matches Probe / Test
    // elsewhere; fall back to the inline CSS ring if the module fails.
    try {
      const sp = window.spinnerModule || (await import('../../../spinner.js')).default;
      const wp = sp.createWhirlpool(11);
      wp.element.style.cssText = 'display:inline-flex;width:11px;height:11px;position:relative;top:-2px;';
      ico.innerHTML = '';
      ico.appendChild(wp.element);
    } catch (_) { ico.innerHTML = _spinner; }
    msg.textContent = '';
    msg.style.color = '';
    try {
      const r = await fetch('/api/email/accounts/test', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (d.ok) {
        // Button becomes the indicator — green checkmark with the
        // cookbook-style halo + breathing animation. No status text;
        // the glow is the signal.
        btn.style.background = 'var(--green, #50fa7b)';
        btn.style.borderColor = 'var(--green, #50fa7b)';
        btn.style.color = '#0b0';
        btn.style.boxShadow =
          '0 0 0 2px color-mix(in srgb, var(--green, #50fa7b) 25%, transparent),'
        + ' 0 0 10px 2px color-mix(in srgb, var(--green, #50fa7b) 55%, transparent)';
        btn.style.animation = 'cookbook-srv-glow-ok 2.4s ease-in-out infinite';
        ico.innerHTML = _checkIcon;
      } else {
        // Failure — red glow, original icon, error detail in status
        // text so we can say WHICH half failed (IMAP vs SMTP).
        btn.style.background = 'var(--red)';
        btn.style.borderColor = 'var(--red)';
        btn.style.color = '#fff';
        btn.style.boxShadow =
          '0 0 0 2px color-mix(in srgb, var(--red) 25%, transparent),'
        + ' 0 0 10px 2px color-mix(in srgb, var(--red) 55%, transparent)';
        ico.innerHTML = btn.dataset.origIco;
        const imap = d.imap?.ok ? 'IMAP ok' : `IMAP: ${d.imap?.error || 'fail'}`;
        const smtp = d.smtp ? (d.smtp.ok ? ' · SMTP ok' : ` · SMTP: ${d.smtp.error || 'fail'}`) : '';
        msg.textContent = imap + smtp;
        msg.style.color = 'var(--red)';
      }
    } catch (e) {
      btn.style.background = 'var(--red)';
      btn.style.borderColor = 'var(--red)';
      btn.style.color = '#fff';
      ico.innerHTML = btn.dataset.origIco;
      msg.textContent = 'Test error: ' + e.message;
      msg.style.color = 'var(--red)';
    } finally {
      btn.disabled = false;
    }
  });

  el('uf-email-save').addEventListener('click', async () => {
    const body = _collectBody();
    // Name is optional — fall back to Email so the list still has a label.
    if (!body.name) body.name = body.from_address;
    if (!body.name) { el('uf-email-msg').textContent = 'Need at least a Name or Email'; el('uf-email-msg').style.color = 'var(--red)'; return; }
    const saveBtn = el('uf-email-save');
    saveBtn.disabled = true;
    const saveIcoEl = saveBtn.querySelector('.uf-email-save-ico');
    const saveLblEl = saveBtn.querySelector('.uf-email-save-label');
    const prevIco = saveIcoEl.innerHTML;
    const prevLbl = saveLblEl.textContent;
    saveIcoEl.innerHTML = _spinner;
    saveLblEl.textContent = 'Saving…';
    try {
      const url = isEdit ? `/api/email/accounts/${editId}` : '/api/email/accounts';
      const method = isEdit ? 'PUT' : 'POST';
      const r = await fetch(url, {
        method, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!(d.ok || d.id)) {
        el('uf-email-msg').textContent = d.error || 'Failed';
        el('uf-email-msg').style.color = 'var(--red)';
        return;
      }
      el('uf-email-msg').textContent = 'Saved';
      el('uf-email-msg').style.color = 'var(--green,#50fa7b)';
      setNotice('Email account saved. For more settings, go to Settings > Email.');
      formEl.style.display = 'none';
      await renderList();
      notifyIntegrationsChanged();
    } catch (e) {
      el('uf-email-msg').textContent = 'Error: ' + e.message;
      el('uf-email-msg').style.color = 'var(--red)';
    } finally {
      saveBtn.disabled = false;
      saveIcoEl.innerHTML = prevIco;
      saveLblEl.textContent = prevLbl;
    }
  });
}

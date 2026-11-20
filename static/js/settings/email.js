// Email panel: composition preferences and the connected-accounts list.
// Extracted from settings.js; opening another Settings panel is injected.

import { byId as el } from './dom.js';

export async function initEmailAccountsSettings({ openSettings }) {
  const root = el('settings-modal');
  if (!root || !root.querySelector('[data-settings-panel="email"]')) return;

  const emailPreferences = el('settings-email-preferences');
  if (emailPreferences && emailPreferences.dataset.emailSettingsBound !== '1') {
    emailPreferences.dataset.emailSettingsBound = '1';
    try {
      const mod = await import('../emailLibrary.js?v=20260915trashmove2');
      if (typeof mod.mountEmailSettings === 'function') {
        await mod.mountEmailSettings(emailPreferences);
      }
    } catch (e) {
      console.warn('Failed to mount Email settings', e);
    }
  }
  const manageBtn = el('set-email-open-integrations');
  if (manageBtn && manageBtn.dataset.bound !== '1') {
    manageBtn.dataset.bound = '1';
    manageBtn.addEventListener('click', () => openSettings('integrations'));
  }
  const tasksBtn = el('set-email-open-tasks');
  if (tasksBtn && tasksBtn.dataset.bound !== '1') {
    tasksBtn.dataset.bound = '1';
    tasksBtn.addEventListener('click', async () => {
      try {
        const mod = await import('../tasks.js?v=20260914taskmodel1');
        const openTasks = mod.openTasks || (mod.default && mod.default.openTasks);
        if (typeof openTasks === 'function') openTasks(null, { filter: 'Email' });
        else document.getElementById('tool-tasks-btn')?.click();
      } catch (_) {
        document.getElementById('tool-tasks-btn')?.click();
      }
    });
  }
  const listEl = el('set-email-accounts-list');
  const msgEl = el('set-email-accounts-msg');
  const formEl = el('set-email-accounts-form');
  const addBtn = el('set-email-accounts-add-btn');
  if (!listEl || !addBtn || !formEl) return;

  const esc = s => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  async function fetchAccounts() {
    const r = await fetch('/api/email/accounts', { credentials: 'same-origin' });
    const d = await r.json();
    return d.accounts || [];
  }

  function renderRow(a) {
    const imap = a.imap_host ? `${a.imap_host}:${a.imap_port}` : '<no IMAP>';
    const badge = a.is_default
      ? '<span style="font-size:9px;text-transform:uppercase;letter-spacing:0.5px;padding:1px 6px;border-radius:3px;background:color-mix(in srgb, var(--accent,#50fa7b) 15%, transparent);color:var(--accent,#50fa7b)">Default</span>'
      : (a.enabled ? '' : '<span style="font-size:9px;text-transform:uppercase;letter-spacing:0.5px;padding:1px 6px;border-radius:3px;opacity:0.4">Disabled</span>');
    return `<div class="email-account-row" data-acc-id="${esc(a.id)}" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:6px">
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px">${esc(a.name)} ${badge}</div>
        <div style="font-size:11px;opacity:0.6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.imap_user || a.from_address || '')} — ${esc(imap)}</div>
      </div>
      ${a.is_default ? '' : `<button class="admin-btn-sm email-acc-default-btn" style="font-size:10px">Make Default</button>`}
      <button class="admin-btn-sm email-acc-edit-btn" style="font-size:10px">Edit</button>
      <button class="admin-btn-sm email-acc-del-btn" style="font-size:10px;opacity:0.6">Delete</button>
    </div>`;
  }

  async function renderList() {
    const accs = await fetchAccounts();
    if (!accs.length) {
      listEl.innerHTML = '<div style="padding:12px;opacity:0.5;font-size:12px;text-align:center">No email accounts configured</div>';
      return;
    }
    listEl.innerHTML = accs.map(renderRow).join('');
    listEl.querySelectorAll('.email-account-row').forEach(row => {
      const id = row.dataset.accId;
      row.querySelector('.email-acc-default-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        await fetch(`/api/email/accounts/${id}/set-default`, { method: 'POST', credentials: 'same-origin' });
        renderList();
      });
      row.querySelector('.email-acc-edit-btn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        showForm(accs.find(a => a.id === id));
      });
      row.querySelector('.email-acc-del-btn')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!await window.styledConfirm(`Delete account "${accs.find(a => a.id === id)?.name}"?`, { confirmText: 'Delete', danger: true })) return;
        await fetch(`/api/email/accounts/${id}`, { method: 'DELETE', credentials: 'same-origin' });
        renderList();
      });
    });
  }

  function showForm(existing) {
    const a = existing || {};
    const isEdit = !!existing;
    formEl.style.display = '';
    // Small `?` indicator next to each label. Hover/focus to read the
    // hint via the native `title` tooltip. tabindex makes it
    // keyboard-focusable too.
    const _hint = (tip) =>
      `<span class="eaf-hint" title="${esc(tip)}" aria-label="${esc(tip)}" tabindex="0" `
      + `style="display:inline-block;width:13px;height:13px;border-radius:50%;`
      + `border:1px solid currentColor;font-size:9px;line-height:11px;text-align:center;`
      + `opacity:0.45;margin-left:5px;cursor:help;vertical-align:1px;font-weight:600;">?</span>`;
    // Provider presets — picking one fills host/port/STARTTLS for both
    // IMAP and SMTP. Dovecot is IMAP-only here; the host is intentionally
    // blank because it may live on another machine (DNS, LAN, Tailscale).
    const PROVIDERS = {
      gmail:             { label: 'Gmail',                       imap: { host: 'imap.gmail.com',        port: 993, starttls: false }, smtp: { host: 'smtp.gmail.com',        port: 465 } },
      google_workspace:  { label: 'Google Workspace / .edu',   imap: { host: 'imap.gmail.com',        port: 993, starttls: false }, smtp: { host: 'smtp.gmail.com',        port: 587 }, oauth: 'google' },
      migadu:            { label: 'Migadu',                     imap: { host: 'imap.migadu.com',       port: 993, starttls: false }, smtp: { host: 'smtp.migadu.com',       port: 465 } },
      icloud:            { label: 'iCloud',                     imap: { host: 'imap.mail.me.com',      port: 993, starttls: false }, smtp: { host: 'smtp.mail.me.com',      port: 587 } },
      outlook:           { label: 'Outlook / Office 365',       imap: { host: 'outlook.office365.com', port: 993, starttls: false }, smtp: { host: 'smtp.office365.com',    port: 587 } },
      fastmail:          { label: 'Fastmail',                   imap: { host: 'imap.fastmail.com',     port: 993, starttls: false }, smtp: { host: 'smtp.fastmail.com',     port: 465 } },
      yahoo:             { label: 'Yahoo',                      imap: { host: 'imap.mail.yahoo.com',   port: 993, starttls: false }, smtp: { host: 'smtp.mail.yahoo.com',   port: 465 } },
      dovecot:           { label: 'Dovecot IMAP (no SMTP)',     imap: { host: '',                      port: 31143, starttls: false }, smtp: { host: '',                     port: 465 } },
    };
    const _providerOptions = Object.entries(PROVIDERS)
      .map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`)
      .join('');
    const _smtpSecurity = (acct) => acct?.smtp_security || ((parseInt(acct?.smtp_port || 465) === 587) ? 'starttls' : 'ssl');
    formEl.innerHTML = `
      <h3 style="font-size:12px;margin:0 0 8px">${isEdit ? 'Edit Account' : 'New Account'}</h3>
      <div class="settings-col">
        <div class="settings-row"><label class="settings-label">Provider${_hint('Pick a known provider to auto-fill the IMAP and SMTP host/port. Choose Custom to type your own.')}</label><select id="eaf-provider" class="settings-select"><option value="">Custom…</option>${_providerOptions}</select></div>
        <div id="eaf-provider-note" style="display:none;font-size:11px;line-height:1.5;padding:8px 10px;margin:2px 0 4px;border:1px solid color-mix(in srgb, var(--fg) 15%, transparent);border-left:3px solid var(--accent, var(--red));border-radius:4px;background:color-mix(in srgb, var(--fg) 4%, transparent);"></div>
        <div class="settings-row"><label class="settings-label">Name${_hint('Optional label for this account (e.g. “Work” or “Personal”). Leave blank to use the email address.')}</label><input id="eaf-name" class="settings-input" placeholder="(optional — leave blank to use email)" value="${esc(a.name || '')}"></div>
        <div class="settings-row"><label class="settings-label">Email${_hint('Your email address. Used as the From: header on outgoing mail and as the display label when Name is blank.')}</label><input id="eaf-from" class="settings-input" placeholder="you@example.com" value="${esc(a.from_address || '')}"></div>
        <div class="settings-row"><label class="settings-label">Display Name${_hint('Your name as it appears in the From: field of emails you send, e.g. Jane Smith. Auto-filled from Google during OAuth.')}</label><input id="eaf-display-name" class="settings-input" placeholder="Your Name" value="${esc(a.display_name || '')}"></div>
        <div id="eaf-oauth-section" style="display:none;margin:8px 0;padding:10px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb,var(--accent,#50fa7b) 6%,transparent)">
          <div style="font-size:11px;font-weight:600;margin-bottom:6px">Google OAuth2 — required for Workspace / .edu accounts</div>
          <div id="eaf-oauth-status" style="font-size:11px;opacity:0.7;margin-bottom:6px">${a.oauth_provider === 'google' ? '✓ Connected via Google OAuth' : 'Not connected — click below to authorize'}</div>
          <button type="button" id="eaf-oauth-btn" class="admin-btn-add" style="font-size:11px">${a.oauth_provider === 'google' ? 'Reconnect with Google' : 'Connect with Google'}</button>
        </div>
        <div style="font-size:11px;font-weight:600;opacity:0.6;margin:6px 0 2px">IMAP (Receiving)</div>
        <div class="settings-row"><label class="settings-label">Host${_hint('Your IMAP server, e.g. imap.gmail.com, imap.migadu.com, a LAN host, or a Tailscale IP for Dovecot.')}</label><input id="eaf-imap-host" class="settings-input" value="${esc(a.imap_host || '')}"></div>
        <div class="settings-row"><label class="settings-label">Port${_hint('993 for IMAPS (most providers), 143 for plain or STARTTLS. Local servers often use a custom port like 31143.')}</label><input id="eaf-imap-port" class="settings-input" type="number" value="${esc(a.imap_port || 993)}" style="max-width:100px"></div>
        <div class="settings-row"><label class="settings-label">Username${_hint('Usually your full email address.')}</label><input id="eaf-imap-user" class="settings-input" value="${esc(a.imap_user || '')}"></div>
        <div class="eaf-password-section"><div class="settings-row"><label class="settings-label">Password${_hint('Your IMAP login password. Use an app-specific password if your provider requires 2FA. Outlook / Office 365 generally requires OAuth and will not work with a normal password here.')}</label><input id="eaf-imap-pass" class="settings-input" type="password" placeholder="${isEdit && a.has_imap_password ? '(unchanged)' : ''}"></div></div>
        <div class="settings-row"><label class="settings-label">STARTTLS${_hint('Turn ON for port 143/587 to upgrade plain to TLS. Turn OFF for port 993 (IMAPS — already encrypted) or a local server with no TLS configured.')}</label><label class="admin-switch"><input type="checkbox" id="eaf-imap-starttls" ${a.imap_starttls !== false ? 'checked' : ''}><span class="admin-slider"></span></label></div>
        <div style="font-size:11px;font-weight:600;opacity:0.6;margin:8px 0 2px">SMTP (Sending) <span style="font-weight:normal;opacity:0.7">— optional, leave blank for read-only</span></div>
        <div class="settings-row"><label class="settings-label">Host${_hint('Your outgoing-mail server, e.g. smtp.gmail.com, smtp.migadu.com. Leave blank to make this account read-only.')}</label><input id="eaf-smtp-host" class="settings-input" value="${esc(a.smtp_host || '')}"></div>
        <div class="settings-row"><label class="settings-label">Port${_hint('465 for SSL/SMTPS, 587 for STARTTLS. 25 is usually blocked by ISPs.')}</label><input id="eaf-smtp-port" class="settings-input" type="number" value="${esc(a.smtp_port || 465)}" style="max-width:100px"></div>
        <div class="settings-row"><label class="settings-label">Security${_hint('SSL for port 465, STARTTLS for port 587, or None for local SMTP bridges such as Proton Mail Bridge.')}</label><select id="eaf-smtp-security" class="settings-select"><option value="ssl">SSL</option><option value="starttls">STARTTLS</option><option value="none">None</option></select></div>
        <div class="settings-row"><label class="settings-label">Same as IMAP${_hint('Use the IMAP username and password for SMTP too (this is right for almost every provider). Turn off to enter separate SMTP credentials.')}</label><label class="admin-switch"><input type="checkbox" id="eaf-smtp-same" ${(!isEdit || (a.smtp_user && a.imap_user && a.smtp_user === a.imap_user)) ? 'checked' : ''}><span class="admin-slider"></span></label></div>
        <div class="settings-row eaf-smtp-creds"><label class="settings-label">Username${_hint('Usually the same as your IMAP username (your email address).')}</label><input id="eaf-smtp-user" class="settings-input" value="${esc(a.smtp_user || '')}"></div>
        <div class="settings-row eaf-smtp-creds"><label class="settings-label">Password${_hint('Your SMTP password — often the same as your IMAP password. Outlook / Office 365 generally requires OAuth and will not work with a normal password here.')}</label><input id="eaf-smtp-pass" class="settings-input" type="password" placeholder="${isEdit && a.has_smtp_password ? '(unchanged)' : ''}"></div>
        <div class="settings-row" style="margin-top:10px;align-items:center;">
          <button class="admin-btn-add" id="eaf-save" style="background:var(--red);border-color:var(--red);color:#fff;display:inline-flex;align-items:center;gap:5px;font-weight:600;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
            ${isEdit ? 'Save' : 'Create'}
          </button>
          <span id="eaf-msg" style="font-size:11px;flex:1;margin-left:8px;"></span>
          <button class="admin-btn-add" id="eaf-cancel" style="opacity:0.7;display:inline-flex;align-items:center;gap:5px;position:relative;top:1px;margin-left:auto;">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            Cancel
          </button>
        </div>
      </div>
    `;

    // Show/hide OAuth section and password fields based on provider selection.
    function _syncOauthUI(providerKey) {
      const p = PROVIDERS[providerKey];
      const isOauth = !!(p && p.oauth);
      el('eaf-oauth-section').style.display = isOauth ? '' : 'none';
      formEl.querySelectorAll('.eaf-password-section').forEach(r => {
        r.style.display = isOauth ? 'none' : '';
      });
    }

    const eafProviderNotes = {
      outlook: {
        title: 'Outlook / Office 365 needs OAuth',
        body: 'Microsoft disables normal password login for IMAP/SMTP in most Outlook and Microsoft 365 accounts. Odysseus does not support Microsoft OAuth/Graph mail yet, so this preset is only a placeholder for future support.',
      },
    };
    const eafNoteEl = el('eaf-provider-note');
    const _renderEafProviderNote = (key) => {
      const n = eafProviderNotes[key];
      if (!eafNoteEl || !n) {
        if (eafNoteEl) {
          eafNoteEl.style.display = 'none';
          eafNoteEl.innerHTML = '';
        }
        return;
      }
      eafNoteEl.style.display = '';
      eafNoteEl.innerHTML = `<div style="font-weight:600;margin-bottom:3px;">${esc(n.title)}</div><div style="opacity:0.8;">${esc(n.body)}</div>`;
    };

    // Provider preset → autofill host/port/STARTTLS for both halves.
    el('eaf-provider').addEventListener('change', (e) => {
      _renderEafProviderNote(e.target.value);
      const p = PROVIDERS[e.target.value];
      if (!p) { _syncOauthUI(''); return; }
      el('eaf-imap-host').value = p.imap.host;
      el('eaf-imap-port').value = p.imap.port;
      el('eaf-imap-starttls').checked = !!p.imap.starttls;
      el('eaf-smtp-host').value = p.smtp.host;
      el('eaf-smtp-port').value = p.smtp.port;
      el('eaf-smtp-security').value = p.smtp.security || ((parseInt(p.smtp.port || 465) === 587) ? 'starttls' : 'ssl');
      _syncOauthUI(e.target.value);
    });

    // Init OAuth UI for accounts already connected via OAuth.
    if (a.oauth_provider === 'google') _syncOauthUI('google_workspace');

    // "Connect with Google" button — save the account first, then redirect to OAuth.
    el('eaf-oauth-btn').addEventListener('click', async () => {
      // Must save the account first to get an account_id to pass to the OAuth flow.
      const body = {
        name: el('eaf-name').value.trim() || el('eaf-from').value.trim(),
        from_address: el('eaf-from').value.trim(),
        display_name: el('eaf-display-name').value.trim(),
        imap_host: el('eaf-imap-host').value.trim(),
        imap_port: parseInt(el('eaf-imap-port').value) || 993,
        imap_user: el('eaf-imap-user').value.trim(),
        imap_starttls: el('eaf-imap-starttls').checked,
        smtp_host: el('eaf-smtp-host').value.trim(),
        smtp_port: parseInt(el('eaf-smtp-port').value) || 587,
        smtp_security: el('eaf-smtp-security').value,
        smtp_user: el('eaf-imap-user').value.trim(),
      };
      if (!body.name) { el('eaf-msg').textContent = 'Enter a Name or Email first'; el('eaf-msg').style.color = 'var(--red)'; return; }
      const url = isEdit ? `/api/email/accounts/${a.id}` : '/api/email/accounts';
      const method = isEdit ? 'PUT' : 'POST';
      const r = await fetch(url, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!d.ok) { el('eaf-msg').textContent = d.error || 'Save failed'; el('eaf-msg').style.color = 'var(--red)'; return; }
      const accId = isEdit ? a.id : d.id;
      window.location.href = `/api/email/oauth/google/authorize?account_id=${encodeURIComponent(accId)}`;
    });
    el('eaf-smtp-security').value = _smtpSecurity(a);

    // "Same as IMAP" toggle — hide the SMTP creds rows when on. The save
    // handler copies the IMAP user/password into SMTP at submit time.
    const _syncSmtpSame = () => {
      const same = el('eaf-smtp-same').checked;
      formEl.querySelectorAll('.eaf-smtp-creds').forEach(r => {
        r.style.display = same ? 'none' : '';
      });
    };
    el('eaf-smtp-same').addEventListener('change', _syncSmtpSame);
    _syncSmtpSame();

    el('eaf-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });
    el('eaf-save').addEventListener('click', async () => {
      const body = {
        name: el('eaf-name').value.trim(),
        from_address: el('eaf-from').value.trim(),
        display_name: el('eaf-display-name').value.trim(),
        imap_host: el('eaf-imap-host').value.trim(),
        imap_port: parseInt(el('eaf-imap-port').value) || 993,
        imap_user: el('eaf-imap-user').value.trim(),
        imap_starttls: el('eaf-imap-starttls').checked,
        smtp_host: el('eaf-smtp-host').value.trim(),
        smtp_port: parseInt(el('eaf-smtp-port').value) || 465,
        smtp_security: el('eaf-smtp-security').value,
        smtp_user: el('eaf-smtp-user').value.trim(),
      };
      if (el('eaf-imap-pass').value) body.imap_password = el('eaf-imap-pass').value;
      if (el('eaf-smtp-pass').value) body.smtp_password = el('eaf-smtp-pass').value;
      // "Same as IMAP" toggle — copy IMAP username/password into SMTP at
      // save time, so the hidden SMTP-creds rows don't matter. We only
      // mirror the password if the user actually typed an IMAP one
      // (otherwise SMTP keeps whatever it already had on the server).
      if (el('eaf-smtp-same').checked) {
        body.smtp_user = body.imap_user;
        if (body.imap_password) body.smtp_password = body.imap_password;
      }
      // Name is optional — fall back to the From address so the list view
      // still has a label to render. Only refuse if both are blank.
      if (!body.name) body.name = body.from_address;
      if (!body.name) { el('eaf-msg').textContent = 'Need at least a Name or Email'; el('eaf-msg').style.color = 'var(--red)'; return; }

      try {
        const url = isEdit ? `/api/email/accounts/${a.id}` : '/api/email/accounts';
        const method = isEdit ? 'PUT' : 'POST';
        const r = await fetch(url, {
          method, credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.ok || d.id) {
          el('eaf-msg').textContent = 'Saved';
          el('eaf-msg').style.color = 'var(--green,#50fa7b)';
          setTimeout(() => { formEl.style.display = 'none'; renderList(); }, 400);
        } else {
          el('eaf-msg').textContent = d.error || 'Save failed';
          el('eaf-msg').style.color = 'var(--red)';
        }
      } catch (e) {
        el('eaf-msg').textContent = 'Error: ' + e.message;
        el('eaf-msg').style.color = 'var(--red)';
      }
    });
  }

  addBtn.addEventListener('click', () => showForm(null));
  await renderList();
}

export async function initEmailSettings({ restricted = () => false } = {}) {
  const root = el('settings-modal');
  if (!root || !root.querySelector('[data-settings-panel="email"]')) return;

  const styleKey = () => {
    const account = String(window.__odysseusActiveEmailAccount || '').trim();
    return account ? `odysseus-email-writing-style:${account}` : 'odysseus-email-writing-style';
  };
  const styleEl = el('set-email-style');
  const emailAccountSuffix = () => {
    const account = String(window.__odysseusActiveEmailAccount || '').trim();
    return account ? `?account_id=${encodeURIComponent(account)}` : '';
  };

  // The account/CardDAV config endpoints can be slow when remote mail servers
  // are cold. Populate the Writing Style box independently so saved prose does
  // not appear seconds after the panel opens.
  try {
    const cachedStyle = localStorage.getItem(styleKey());
    if (styleEl && cachedStyle !== null && !styleEl.value) styleEl.value = cachedStyle;
  } catch (_) {}

  const loadWritingStyle = async () => {
    try {
      const res = await fetch(`/api/email/style${emailAccountSuffix()}`);
      const data = await res.json();
      const style = data.style || '';
      if (styleEl) styleEl.value = style;
      try { localStorage.setItem(styleKey(), style); } catch (_) {}
    } catch (_) {}
  };
  loadWritingStyle();

  // Load current email config
  try {
    const res = await fetch('/api/email/config');
    const cfg = await res.json();
    if (el('set-email-imap-host')) el('set-email-imap-host').value = cfg.imap_host || '';
    if (el('set-email-imap-port')) el('set-email-imap-port').value = cfg.imap_port || '';
    if (el('set-email-imap-user')) el('set-email-imap-user').value = cfg.imap_user || '';
    if (el('set-email-imap-pass')) el('set-email-imap-pass').value = ''; // never prefill
    if (el('set-email-smtp-host')) el('set-email-smtp-host').value = cfg.smtp_host || '';
    if (el('set-email-smtp-port')) el('set-email-smtp-port').value = cfg.smtp_port || '';
    if (el('set-email-smtp-user')) el('set-email-smtp-user').value = cfg.smtp_user || '';
    if (el('set-email-smtp-pass')) el('set-email-smtp-pass').value = '';
    if (el('set-email-from')) el('set-email-from').value = cfg.from_address || '';
  } catch (_) {}

  // Load contacts config
  try {
    // The contacts sync account is admin-managed.
    const isRestricted = typeof restricted === 'function' ? await restricted() : false;
    let cfg = {};
    if (!isRestricted) {
      const res = await fetch('/api/contacts/config');
      if (res.ok) {
        cfg = await res.json();
      } else if (res.status !== 401 && res.status !== 403) {
        console.warn('[settings] unexpected response from /api/contacts/config: HTTP ' + res.status);
      }
    }
    if (el('set-carddav-url')) el('set-carddav-url').value = cfg.url || '';
    if (el('set-carddav-user')) el('set-carddav-user').value = cfg.username || '';
    if (el('set-carddav-pass')) el('set-carddav-pass').value = '';
  } catch (_) {}

  // Save email config
  el('set-email-save')?.addEventListener('click', async () => {
    const msg = el('set-email-msg');
    if (msg) msg.textContent = 'Saving...';
    const data = {
      imap_host: el('set-email-imap-host').value,
      imap_port: parseInt(el('set-email-imap-port').value) || 0,
      imap_user: el('set-email-imap-user').value,
      smtp_host: el('set-email-smtp-host').value,
      smtp_port: parseInt(el('set-email-smtp-port').value) || 0,
      smtp_user: el('set-email-smtp-user').value,
      email_from: el('set-email-from').value,
    };
    const imapPass = el('set-email-imap-pass').value;
    const smtpPass = el('set-email-smtp-pass').value;
    if (imapPass) data.imap_password = imapPass;
    if (smtpPass) data.smtp_password = smtpPass;
    try {
      const res = await fetch('/api/email/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await res.json();
      if (msg) msg.textContent = result.success ? '✓ Saved' : (result.error || 'Failed');
      setTimeout(() => { if (msg) msg.textContent = ''; }, 3000);
    } catch (e) {
      if (msg) msg.textContent = 'Failed';
    }
  });

  // Save CardDAV config
  el('set-carddav-save')?.addEventListener('click', async () => {
    const msg = el('set-carddav-msg');
    if (msg) msg.textContent = 'Saving...';
    const data = {
      carddav_url: el('set-carddav-url').value,
      carddav_username: el('set-carddav-user').value,
    };
    const pass = el('set-carddav-pass').value;
    if (pass) data.carddav_password = pass;
    try {
      const res = await fetch('/api/contacts/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await res.json();
      if (msg) msg.textContent = result.success ? '✓ Saved' : (result.error || 'Failed');
      setTimeout(() => { if (msg) msg.textContent = ''; }, 3000);
    } catch (e) {
      if (msg) msg.textContent = 'Failed';
    }
  });

  // Extract writing style
  el('set-email-style-extract')?.addEventListener('click', async () => {
    const btn = el('set-email-style-extract');
    const msg = el('set-email-style-msg');
    btn.disabled = true;
    // Render whirlpool + label inside the status area (same pattern as
    // the "Find" / network-discover button in Add Models).
    let wp = null;
    if (msg) {
      msg.className = '';
      msg.innerHTML = '';
      try {
        const sp = window.spinnerModule || (await import('../spinner.js')).default;
        wp = sp.createWhirlpool(16);
        wp.element.style.cssText = 'display:inline-block;vertical-align:middle;margin:0 8px 0 0;';
        const wrap = document.createElement('span');
        wrap.style.cssText = 'display:inline-flex;align-items:center;';
        wrap.appendChild(wp.element);
        const txt = document.createElement('span');
        txt.textContent = 'Analyzing your sent emails…';
        txt.style.cssText = 'font-size:12px;opacity:0.7;';
        wrap.appendChild(txt);
        msg.appendChild(wrap);
      } catch (_) {
        msg.textContent = 'Analyzing your sent emails…';
      }
    }
    try {
      const res = await fetch(`/api/email/extract-style${emailAccountSuffix()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sample_count: 15 }),
      });
      const data = await res.json();
      if (data.success && data.style) {
        if (styleEl) styleEl.value = data.style;
        try { localStorage.setItem(styleKey(), data.style); } catch (_) {}
        if (msg) msg.textContent = '✓ Style extracted';
      } else {
        if (msg) msg.textContent = data.error || 'Failed';
      }
    } catch (e) {
      if (msg) msg.textContent = 'Failed to extract';
    } finally {
      if (wp && wp.destroy) { try { wp.destroy(); } catch (_) {} }
      btn.disabled = false;
      setTimeout(() => { if (msg) msg.textContent = ''; }, 5000);
    }
  });

  // Save writing style manually
  el('set-email-style-save')?.addEventListener('click', async () => {
    const msg = el('set-email-style-msg');
    if (msg) msg.textContent = 'Saving...';
    try {
      const style = styleEl ? styleEl.value : '';
      const res = await fetch(`/api/email/style${emailAccountSuffix()}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ style }),
      });
      const result = await res.json();
      if (result.success) {
        try { localStorage.setItem(styleKey(), style); } catch (_) {}
      }
      if (msg) msg.textContent = result.success ? '✓ Saved' : 'Failed';
      setTimeout(() => { if (msg) msg.textContent = ''; }, 3000);
    } catch (e) {
      if (msg) msg.textContent = 'Failed';
    }
  });
}

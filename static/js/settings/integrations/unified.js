// Unified Integrations panel: the combined list of API, CalDAV, CardDAV, email,
// vault, MCP and agent integrations and their create/edit forms.

import { byId as el } from '../dom.js';
import { notifyIntegrationsChanged } from './events.js';
import { esc } from '../html.js';
import { bindMenuDismiss } from '../../escMenuStack.js';
import { showApiForm } from './forms/api.js';
import { showCalDavForm } from './forms/caldav.js';
import { showCardDavForm } from './forms/carddav.js';
import { showEmailForm } from './forms/email.js';
import { showVaultForm } from './forms/vault.js';
import { showMcpForm } from './forms/mcp.js';
import { showAgentForm } from './forms/agent.js';

const INTG_TYPES = {
  api:     { label: 'API',     icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' },
  caldav:  { label: 'CalDAV',  icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>' },
  contacts: { label: 'Contacts', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' },
  carddav: { label: 'CardDAV', icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' },
  email:   { label: 'Email',   icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>' },
  mcp:     { label: 'MCP',     icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>' },
  codex:   { label: 'Codex',   icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 10.696.453a6.023 6.023 0 0 0-5.75 4.172 6.061 6.061 0 0 0-3.946 2.945 6.024 6.024 0 0 0 .742 7.099 5.98 5.98 0 0 0 .516 4.911 6.046 6.046 0 0 0 6.51 2.9A5.996 5.996 0 0 0 13.26 23.547a6.023 6.023 0 0 0 5.75-4.172 6.061 6.061 0 0 0 3.946-2.945 6.024 6.024 0 0 0-.674-6.609zM13.26 21.047a4.508 4.508 0 0 1-2.886-1.041l.143-.082 4.793-2.769a.777.777 0 0 0 .391-.676V10.34l2.026 1.17a.072.072 0 0 1 .039.061v5.596a4.532 4.532 0 0 1-4.506 4.48zM3.968 17.64a4.473 4.473 0 0 1-.537-3.018l.143.086 4.793 2.769a.79.79 0 0 0 .782 0l5.852-3.379v2.34a.072.072 0 0 1-.029.062l-4.845 2.796a4.532 4.532 0 0 1-6.159-1.656zM2.804 7.922a4.49 4.49 0 0 1 2.348-1.973V11.6a.778.778 0 0 0 .391.676l5.852 3.378-2.026 1.17a.072.072 0 0 1-.068 0L4.456 14.03a4.532 4.532 0 0 1-1.652-6.108zm16.423 3.823L13.375 8.367l2.026-1.17a.072.072 0 0 1 .068 0l4.845 2.796a4.525 4.525 0 0 1-.7 8.08V12.42a.778.778 0 0 0-.387-.676zm2.015-3.025l-.143-.086-4.793-2.769a.79.79 0 0 0-.782 0L9.672 9.243V6.903a.072.072 0 0 1 .029-.062l4.845-2.796a4.525 4.525 0 0 1 6.696 4.675zM8.598 12.66L6.57 11.49a.072.072 0 0 1-.039-.061V5.833a4.525 4.525 0 0 1 7.413-3.48l-.143.082-4.793 2.769a.777.777 0 0 0-.391.676l-.019 6.78zm1.1-2.379l2.607-1.505 2.607 1.505v3.01l-2.607 1.505-2.607-1.505z"/></svg>' },
  claude:  { label: 'Claude',  icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z"/></svg>' },
  vault:   { label: 'Vault',   icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' },
};

let _unifiedInited = false;
let _refreshForAccess = null;

/**
 * Tell the list that Settings access has just been resolved. Only a list that
 * was built while access was unknown (a failed /api/auth/status lookup) is
 * rebuilt, once, so recovery shows the caller's integrations without a reload.
 * Any other case is a no-op, so a normal open never fetches twice.
 */
export function refreshIntegrationsForAccess() {
  return _refreshForAccess ? _refreshForAccess() : Promise.resolve();
}

export async function initUnifiedIntegrations({ openSettings, restricted = () => false, accessKnown = () => true }) {
  if (_unifiedInited) return;
  _unifiedInited = true;

  const listEl = el('unified-integrations-list');
  const formEl = el('unified-intg-form');
  const addBtn = el('unified-intg-add-btn');
  if (!listEl) return;
  let integrationNotice = '';
  const setNotice = (text) => { integrationNotice = text; };

  // Hide the "+ Add Integration" button whenever the per-type create form
  // is open so it doesn't compete visually with the in-progress form.
  // Many call sites toggle formEl.style.display directly; observe instead
  // of patching every one of them.
  if (formEl && addBtn && addBtn.parentElement && !formEl._addBtnObserved) {
    formEl._addBtnObserved = true;
    const addBtnWrap = addBtn.parentElement;
    const _syncAddBtnWrap = () => {
      const formOpen = formEl.style.display && formEl.style.display !== 'none';
      addBtnWrap.style.display = formOpen ? 'none' : '';
    };
    new MutationObserver(_syncAddBtnWrap).observe(formEl, { attributes: true, attributeFilter: ['style'] });
    _syncAddBtnWrap();
  }

  function _openEmailSettings() {
    openSettings('email');
  }

  // Admin-managed routes: a caller who may not manage settings gets the empty
  // answer without asking (the server would refuse the request anyway).
  // Set when an admin-managed list was skipped only because the caller's
  // access was still unknown, so a later answer can fill it in.
  let adminListsSkippedWhileUnknown = false;
  const adminJson = async (url, empty) => {
    const isRestricted = typeof restricted === 'function' ? await restricted() : false;
    if (isRestricted) {
      if (!accessKnown()) adminListsSkippedWhileUnknown = true;
      return empty;
    }
    try {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) {
        if (r.status !== 401 && r.status !== 403) {
          console.warn('[settings] unexpected response from ' + url + ': HTTP ' + r.status);
        }
        return empty;
      }
      return await r.json();
    } catch (e) {
      console.warn('[settings] network failure fetching ' + url, e);
      return empty;
    }
  };

  async function fetchAll() {
    const [apiRes, calRes, cardRes, contactsRes, emailAccountsRes, mcpRes, vaultRes, tokenRes, calendarsRes] = await Promise.all([
      adminJson('/api/auth/integrations', { integrations: [] }),
      fetch('/api/calendar/config/accounts', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { accounts: [] }).catch(() => ({ accounts: [] })),
      adminJson('/api/contacts/config', {}),
      adminJson('/api/contacts/list', { contacts: [], count: 0 }),
      fetch('/api/email/accounts', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { accounts: [] }).catch(() => ({ accounts: [] })),
      adminJson('/api/mcp/servers', []),
      adminJson('/api/vault/config', {}),
      adminJson('/api/tokens', []),
      fetch('/api/calendar/calendars', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { calendars: [] }).catch(() => ({ calendars: [] })),
    ]);
    const items = [];
    // API integrations
    for (const intg of (apiRes.integrations || [])) {
      items.push({ type: 'api', id: intg.id, name: intg.name || 'Unnamed', detail: intg.base_url || '', enabled: intg.enabled !== false, data: intg });
    }
    // CalDAV — one card per account
    for (const acc of (calRes.accounts || [])) {
      items.push({ type: 'caldav', id: acc.id, name: acc.label || 'Calendar (CalDAV)', detail: acc.url, enabled: true, data: acc });
    }
    // Contacts import first, then the optional CardDAV sync account.
    const contactCount = Number(contactsRes.count || (contactsRes.contacts || []).length || 0);
    if (contactCount > 0) {
      items.push({
        type: 'contacts',
        id: '__contacts__',
        name: 'Contacts Import',
        detail: `${contactCount} contact${contactCount === 1 ? '' : 's'}`,
        enabled: true,
        data: contactsRes,
      });
    }
    if (cardRes.url) {
      items.push({
        type: 'carddav',
        id: '__carddav__',
        name: 'Contacts (CardDAV)',
        detail: cardRes.url,
        enabled: true,
        data: cardRes,
      });
    }
    // Email — one entry per EmailAccount row
    for (const acc of (emailAccountsRes.accounts || [])) {
      const label = acc.name + (acc.is_default ? ' (default)' : '');
      const detail = [acc.from_address || acc.imap_user, acc.imap_host].filter(Boolean).join(' — ');
      items.push({ type: 'email', id: acc.id, name: label, detail, enabled: acc.enabled !== false, data: acc });
    }
    // MCP servers
    const mcpList = Array.isArray(mcpRes) ? mcpRes : (mcpRes.servers || []);
    for (const srv of mcpList) {
      const statusText = srv.needs_oauth ? 'needs auth' : srv.status === 'connected' ? `${srv.enabled_tool_count}/${srv.tool_count} tools` : srv.status === 'error' ? 'error' : 'disconnected';
      items.push({ type: 'mcp', id: srv.id || srv.name, name: srv.name || 'MCP Server', detail: statusText, enabled: srv.is_enabled !== false, data: srv });
    }
    for (const tok of (Array.isArray(tokenRes) ? tokenRes : [])) {
      const scopes = tok.scopes || [];
      const lowerName = (tok.name || '').toLowerCase();
      let agentType = null;
      if (lowerName.startsWith('claude agent')) agentType = 'claude';
      else if (lowerName.startsWith('codex agent')) agentType = 'codex';
      else if (scopes.some(s => String(s || '').startsWith('todos:') || String(s || '').startsWith('email:') || String(s || '').startsWith('documents:'))) {
        // Legacy / un-prefixed scoped tokens fall back to Codex for backwards compat.
        agentType = 'codex';
      }
      if (!agentType) continue;
      const detail = `${tok.token_prefix || 'token'}... - ${scopes.join(', ') || 'chat'}`;
      items.push({ type: agentType, id: tok.id, name: tok.name || (agentType === 'claude' ? 'Claude Agent' : 'Codex Agent'), detail, enabled: true, data: tok });
    }
    // Vaultwarden removed as an integration option.
    return items;
  }

  function renderCard(item) {
    const t = INTG_TYPES[item.type] || INTG_TYPES.api;
    // Static enabled/disabled indicator — same dot every integration
    // type gets. (The clickable glow-on-test variant for email was
    // removed earlier; this matches the API/CalDAV/MCP pattern.)
    const statusDot = item.enabled
      ? '<span style="width:8px;height:8px;border-radius:50%;background:var(--color-success,#50fa7b);flex-shrink:0;--notif-glow:var(--color-success,#50fa7b);animation:cookbook-notif-pulse 2s ease-in-out infinite;" title="Active"></span>'
      : '<span style="width:8px;height:8px;border-radius:50%;background:var(--fg);opacity:0.3;flex-shrink:0" title="Disabled"></span>';
    return `<div class="intg-card" data-intg-id="${item.id}" data-intg-type="${item.type}" style="display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:color-mix(in srgb, var(--fg) 3%, transparent);margin-bottom:6px;cursor:pointer;transition:all 0.15s;" title="Click to edit">
      <span style="color:var(--accent, var(--red));flex-shrink:0">${t.icon}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px">${item.name} <span style="font-size:9px;text-transform:uppercase;letter-spacing:0.5px;padding:1px 5px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 50%, transparent);border-radius:3px;color:var(--accent, var(--red));background:color-mix(in srgb, var(--accent, var(--red)) 12%, transparent);">${t.label}</span></div>
        <div style="font-size:11px;opacity:0.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${item.detail || ''}</div>
      </div>
      ${statusDot}
      <button class="admin-btn-sm intg-del-btn" data-intg-id="${item.id}" data-intg-type="${item.type}" data-intg-name="${(item.name || '').replace(/"/g, '&quot;')}" title="Remove" style="background:none;border:none;padding:4px;cursor:pointer;color:var(--red);opacity:0.55;display:inline-flex;align-items:center;justify-content:center;">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
      </button>
    </div>`;
  }

  // Only the newest render may touch the list: an older, slower response
  // (an overlapping refresh) must not replace what a newer one showed.
  let renderSeq = 0;
  let latestRender = Promise.resolve();
  function renderList() {
    const seq = ++renderSeq;
    latestRender = renderListNow(seq);
    return latestRender;
  }

  async function renderListNow(seq) {
    const items = await fetchAll();
    if (seq !== renderSeq) return latestRender;
    const noticeHtml = integrationNotice ? `
      <div class="intg-followup-note" style="display:flex;align-items:center;gap:8px;padding:8px 10px;margin-bottom:8px;border:1px solid color-mix(in srgb, var(--accent, var(--red)) 35%, transparent);border-left:3px solid var(--accent, var(--red));border-radius:5px;background:color-mix(in srgb, var(--accent, var(--red)) 8%, transparent);font-size:11px;">
        <span style="flex:1;line-height:1.35">${integrationNotice}</span>
        <button type="button" class="admin-btn-sm intg-open-email-settings" style="white-space:nowrap;">Email settings</button>
      </div>` : '';
    if (items.length === 0) {
      listEl.innerHTML = noticeHtml + '<div style="padding:12px;opacity:0.5;font-size:12px;text-align:center">No integrations configured</div>';
    } else {
      listEl.innerHTML = noticeHtml + items.map(renderCard).join('');
    }
    listEl.querySelector('.intg-open-email-settings')?.addEventListener('click', (e) => {
      e.stopPropagation();
      _openEmailSettings();
    });
    // Wire edit clicks
    listEl.querySelectorAll('.intg-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('.intg-del-btn')) return;
        const type = card.dataset.intgType;
        const id = card.dataset.intgId;
        // Toggle a class instead of mutating inline borderColor — the
        // inline border shorthand made the reset unreliable, leaving
        // stale accent borders on previously-clicked cards.
        listEl.querySelectorAll('.intg-card.intg-card-active').forEach(c => c.classList.remove('intg-card-active'));
        card.classList.add('intg-card-active');
        showForm(type, id);
      });
    });
    // Wire delete
    listEl.querySelectorAll('.intg-del-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const intgName = btn.dataset.intgName || 'this integration';
        if (!await window.styledConfirm(`Remove "${intgName}"?`, { confirmText: 'Remove', danger: true })) return;
        const type = btn.dataset.intgType;
        const id = btn.dataset.intgId;
        try {
          if (type === 'api') await fetch(`/api/auth/integrations/${id}`, { method: 'DELETE', credentials: 'same-origin' });
          else if (type === 'caldav') await fetch(`/api/calendar/config/accounts/${id}`, { method: 'DELETE', credentials: 'same-origin' });
          else if (type === 'contacts') {
            await fetch('/api/contacts/clear', { method: 'DELETE', credentials: 'same-origin' });
          }
          else if (type === 'carddav') {
            await fetch('/api/contacts/config', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ carddav_url: '', carddav_username: '', carddav_password: '' }) });
          }
          else if (type === 'email') await fetch(`/api/email/accounts/${id}`, { method: 'DELETE', credentials: 'same-origin' });
          else if (type === 'mcp') await fetch(`/api/mcp/servers/${id}`, { method: 'DELETE', credentials: 'same-origin' });
          else if (type === 'codex' || type === 'claude') await fetch(`/api/tokens/${id}`, { method: 'DELETE', credentials: 'same-origin' });
          else if (type === 'vault') await fetch('/api/vault/logout', { method: 'POST', credentials: 'same-origin' });
        } catch (_) {}
        formEl.style.display = 'none';
        closeForm();
        await renderList();
        notifyIntegrationsChanged();
      });
    });
  }

  function closeForm() {
    formEl.style.display = 'none';
    formEl.innerHTML = '';
    listEl.querySelectorAll('.intg-card.intg-card-active').forEach(c => c.classList.remove('intg-card-active'));
    if (addBtn && addBtn.parentElement && addBtn.parentElement.style.display !== 'none') {
      addBtn.focus();
    }
  }

  async function showForm(type, editId) {
    formEl.style.display = '';
    const ctx = { formEl, renderList, setNotice, closeForm };
    if (type === 'api') await showApiForm(ctx, editId);
    else if (type === 'caldav') await showCalDavForm(ctx, editId);
    else if (type === 'contacts' || type === 'carddav') await showCardDavForm(ctx);
    else if (type === 'email') await showEmailForm(ctx, editId);
    else if (type === 'mcp') await showMcpForm(ctx, editId);
    else if (type === 'codex') await showAgentForm(ctx, 'codex', editId);
    else if (type === 'claude') await showAgentForm(ctx, 'claude', editId);
    else if (type === 'vault') await showVaultForm(ctx);

    const first = formEl.querySelector('input:not([type="hidden"]):not([tabindex="-1"]), select:not([tabindex="-1"]), button:not([tabindex="-1"])');
    if (first && typeof first.focus === 'function') {
      first.focus();
    }
  }

  // ── Add button now drops a type-picker menu directly anchored to itself ──
  if (addBtn) {
    const _typeOptions = [
      ['api', 'API Service'],
      ['caldav', 'CalDAV Calendar'],
      ['claude', 'Claude Agent'],
      ['codex', 'Codex Agent'],
      ['carddav', 'Contacts (CardDAV)'],
      ['contacts', 'Contacts Import'],
      ['email', 'Email (IMAP/SMTP)'],
      ['mcp', 'MCP Tool Server'],
    ];
    const _iconFor = (k) => (INTG_TYPES[k]?.icon || '').replace(/width="14"/, 'width="16"').replace(/height="14"/, 'height="16"');
    const _rowsHtml = _typeOptions.map(([k, label]) => `<button type="button" role="menuitem" class="uf-type-option" data-value="${k}" style="display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;background:transparent;border:0;color:var(--fg);font:inherit;cursor:pointer;text-align:left;"><span style="display:inline-flex;color:var(--accent, var(--red));flex-shrink:0;">${_iconFor(k)}</span><span>${esc(label)}</span></button>`).join('');

    // Anchor wrapper so the absolutely-positioned menu lands directly under
    // the add button. The button is the wrapper's only sibling.
    addBtn.setAttribute('aria-haspopup', 'menu');
    addBtn.setAttribute('aria-expanded', 'false');
    if (!addBtn.parentElement.classList.contains('uf-add-anchor')) {
      addBtn.parentElement.style.position = 'relative';
      addBtn.parentElement.classList.add('uf-add-anchor');
    }
    // Menu is created per open and removed on close. _closeMenu routes through
    // the bindMenuDismiss close() bound when the menu opens, so the outside-click
    // listener + Escape-stack entry are torn down alongside the node removal.
    let _menuEl = null;
    let _closeMenu = () => {};
    addBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (_menuEl) { _closeMenu(); return; }
      const menu = document.createElement('div');
      menu.className = 'uf-add-menu';
      menu.setAttribute('role', 'menu');
      menu.setAttribute('aria-label', 'Integration types');
      menu.innerHTML = _rowsHtml;
      menu.style.cssText = 'position:absolute;right:0;z-index:1000;background:var(--panel);border:1px solid var(--border);border-radius:6px;max-height:340px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,0.25);min-width:220px;max-width:calc(100vw - 32px);';
      addBtn.parentElement.appendChild(menu);
      addBtn.setAttribute('aria-expanded', 'true');
      _menuEl = menu;
      // Drop-up when there isn't enough room below the button (modal near
      // the viewport bottom, mobile keyboard up, etc.).
      const tRect = addBtn.getBoundingClientRect();
      const mRect = menu.getBoundingClientRect();
      const below = window.innerHeight - tRect.bottom;
      const above = tRect.top;
      if (mRect.height > below && above > below) {
        menu.style.top = 'auto'; menu.style.bottom = 'calc(100% + 2px)';
      } else {
        menu.style.top = 'calc(100% + 2px)'; menu.style.bottom = 'auto';
      }
      const optionBtns = Array.from(menu.querySelectorAll('.uf-type-option'));
      if (optionBtns[0]) optionBtns[0].focus();
      menu.addEventListener('keydown', (ke) => {
        const idx = optionBtns.indexOf(document.activeElement);
        if (ke.key === 'ArrowDown') {
          ke.preventDefault();
          const next = (idx + 1) % optionBtns.length;
          optionBtns[next]?.focus();
        } else if (ke.key === 'ArrowUp') {
          ke.preventDefault();
          const prev = (idx - 1 + optionBtns.length) % optionBtns.length;
          optionBtns[prev]?.focus();
        }
      });
      optionBtns.forEach(btn => {
        btn.addEventListener('mouseenter', () => { btn.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
        btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
        btn.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const k = btn.dataset.value;
          _closeMenu();
          formEl.style.display = '';
          showForm(k, 'new');
        });
      });
      _closeMenu = bindMenuDismiss(menu, () => {
        addBtn.setAttribute('aria-expanded', 'false');
        menu.remove();
        _menuEl = null;
      }, (ev) => !menu.contains(ev.target) && ev.target !== addBtn);
    });
  }

  _refreshForAccess = async () => {
    if (!adminListsSkippedWhileUnknown) return;
    adminListsSkippedWhileUnknown = false;
    // A caller who may not manage settings was shown exactly what they would
    // see now, so there is nothing to fetch for them.
    if (typeof restricted === 'function' && await restricted()) return;
    const active = listEl.querySelector('.intg-card.intg-card-active');
    const activeKey = active ? { type: active.dataset.intgType, id: active.dataset.intgId } : null;
    await renderList();
    if (!activeKey) return;
    const card = Array.from(listEl.querySelectorAll('.intg-card')).find(c => (
      c.dataset.intgType === activeKey.type && c.dataset.intgId === activeKey.id));
    if (card) card.classList.add('intg-card-active');
  };

  await renderList();
}

// Unified Integrations panel: the combined list of API, CalDAV, CardDAV, email,
// vault, MCP and agent integrations and their create/edit forms.

import { byId as el } from '../dom.js';
import { notifyIntegrationsChanged } from './events.js';
import { esc } from '../html.js';
import { bindMenuDismiss } from '../../escMenuStack.js';
import uiModule from '../../ui.js?v=20260916largetoolscroll1';

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

// Config shared by the Codex Agent and Claude Agent forms. Both use the same
// scope-gated /api/codex/* backend; this just parameterizes the UI label,
// default token name, and the per-agent install commands.
const AGENT_CONFIGS = {
  codex: {
    label: 'Codex Agent',
    word: 'Codex',
    namePrefix: 'codex agent',
    defaultName: 'Codex Agent',
    pluginPath: '/api/codex/plugin.zip',
    setupDescription: 'Downloads a plugin bundle and registers it.',
    buildSetup: (origin, token) => `export ODYSSEUS_URL=${origin}
export ODYSSEUS_API_TOKEN='${token}'
mkdir -p ~/plugins
curl -fsSL -H "Authorization: Bearer $ODYSSEUS_API_TOKEN" "$ODYSSEUS_URL/api/codex/plugin.zip" -o /tmp/odysseus-codex-plugin.zip
python3 -m zipfile -e /tmp/odysseus-codex-plugin.zip ~/plugins
python3 - <<'PY'
import json
from pathlib import Path

p = Path.home() / ".agents" / "plugins" / "marketplace.json"
p.parent.mkdir(parents=True, exist_ok=True)
if p.exists():
    data = json.loads(p.read_text())
else:
    data = {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}

data.setdefault("name", "personal")
data.setdefault("interface", {}).setdefault("displayName", "Personal")
plugins = data.setdefault("plugins", [])
entry = {
    "name": "odysseus",
    "source": {"source": "local", "path": "./plugins/odysseus"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}
data["plugins"] = [item for item in plugins if item.get("name") != "odysseus"] + [entry]
p.write_text(json.dumps(data, indent=2) + "\\n")
PY
codex plugin add odysseus@personal
python3 ~/plugins/odysseus/scripts/odysseus_api.py capabilities`,
  },
  claude: {
    label: 'Claude Agent',
    word: 'Claude',
    namePrefix: 'claude agent',
    defaultName: 'Claude Agent',
    pluginPath: '/api/claude/plugin.zip',
    setupDescription: 'Downloads a plugin bundle and registers it.',
    buildSetup: (origin, token) => `export ODYSSEUS_URL=${origin}
export ODYSSEUS_API_TOKEN='${token}'
mkdir -p ~/.claude
curl -fsSL -H "Authorization: Bearer $ODYSSEUS_API_TOKEN" "$ODYSSEUS_URL/api/claude/plugin.zip" -o /tmp/odysseus-claude-skill.zip
python3 -m zipfile -e /tmp/odysseus-claude-skill.zip ~/.claude/
python3 ~/.claude/skills/odysseus/scripts/odysseus_api.py capabilities`,
  },
};

let _unifiedInited = false;

export async function initUnifiedIntegrations({ openSettings }) {
  if (_unifiedInited) return;
  _unifiedInited = true;

  const listEl = el('unified-integrations-list');
  const formEl = el('unified-intg-form');
  const addBtn = el('unified-intg-add-btn');
  if (!listEl) return;
  let integrationNotice = '';

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

  async function fetchAll() {
    const [apiRes, calRes, cardRes, contactsRes, emailAccountsRes, mcpRes, vaultRes, tokenRes, calendarsRes] = await Promise.all([
      fetch('/api/auth/integrations', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { integrations: [] }).catch(() => ({ integrations: [] })),
      fetch('/api/calendar/config/accounts', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { accounts: [] }).catch(() => ({ accounts: [] })),
      fetch('/api/contacts/config', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : {}).catch(() => ({})),
      fetch('/api/contacts/list', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { contacts: [], count: 0 }).catch(() => ({ contacts: [], count: 0 })),
      fetch('/api/email/accounts', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : { accounts: [] }).catch(() => ({ accounts: [] })),
      fetch('/api/mcp/servers', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch('/api/vault/config', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : {}).catch(() => ({})),
      fetch('/api/tokens', { credentials: 'same-origin' }).then(r => r.ok ? r.json() : []).catch(() => []),
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

  async function renderList() {
    const items = await fetchAll();
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
        await renderList();
        notifyIntegrationsChanged();
      });
    });
  }

  function showForm(type, editId) {
    formEl.style.display = '';
    if (type === 'api') showApiForm(editId);
    else if (type === 'caldav') showCalDavForm(editId);
    else if (type === 'contacts' || type === 'carddav') showCardDavForm();
    else if (type === 'email') showEmailForm(editId);
    else if (type === 'mcp') showMcpForm(editId);
    else if (type === 'codex') showAgentForm('codex', editId);
    else if (type === 'claude') showAgentForm('claude', editId);
    else if (type === 'vault') showVaultForm();
  }

  // ── API form ──
  async function showApiForm(editId) {
    let presets = {};
    try {
      const r = await fetch('/api/auth/integrations/presets', { credentials: 'same-origin' });
      if (r.ok) { const d = await r.json(); presets = d.presets || {}; }
    } catch (_) {}
    const presetEntries = Object.entries(presets);
    // Same `?` hint helper as the email form. Native title tooltip,
    // tabbable for keyboard users. Inline-styled so it doesn't need
    // a CSS dependency.
    const _apiHint = (tip) =>
      `<span class="uf-hint" title="${esc(tip.replace(/<[^>]+>/g, ''))}" aria-label="${esc(tip.replace(/<[^>]+>/g, ''))}" tabindex="0" `
      + `style="display:inline-block;width:13px;height:13px;border-radius:50%;`
      + `border:1px solid currentColor;font-size:9px;line-height:11px;text-align:center;`
      + `opacity:0.45;margin-left:5px;cursor:help;vertical-align:1px;font-weight:600;">?</span>`;
    // Real <select> instead of <datalist>: datalists are silently
    // suppressed in Firefox when autocomplete="off" is on the input,
    // and they're patchy on mobile browsers. A native select renders
    // the same everywhere and makes the available options visible
    // without needing the user to type.
    const sortedPresets = presetEntries.sort((a, b) => (a[1].name || a[0]).localeCompare(b[1].name || b[0]));
    const selectOpts = sortedPresets
      .map(([k, p]) => `<option value="${k}">${esc(p.name || k)}</option>`)
      .join('');
    // Letter-in-brand-color logo for each API preset; outline plug icon for
    // "Custom (no preset)". Matches the email-provider dropdown pattern.
    const _apiLetter = (letter, bg) => `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" style="flex-shrink:0"><circle cx="12" cy="12" r="11" fill="${bg}"/><text x="12" y="16.5" font-size="13" font-weight="700" text-anchor="middle" fill="#fff" font-family="system-ui,sans-serif">${letter}</text></svg>`;
    const _apiCustomIco = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex-shrink:0;opacity:0.7"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
    const API_PRESET_LOGO = {
      miniflux:        _apiLetter('M', '#214c87'),
      gitea:           _apiLetter('G', '#609926'),
      linkding:        _apiLetter('L', '#1f2937'),
      home_assistant:  _apiLetter('H', '#41bdf5'),
      ntfy:            _apiLetter('n', '#317f43'),
      vaultwarden:     _apiLetter('V', '#175ddc'),
      freshrss:        _apiLetter('R', '#ef6c00'),
    };
    const _apiIconFor = (k) => {
      if (!k) return _apiCustomIco;
      if (API_PRESET_LOGO[k]) return API_PRESET_LOGO[k];
      const first = (presets[k]?.name || k).trim().charAt(0).toUpperCase() || '?';
      return _apiLetter(first, '#6b7280');
    };
    const _apiRows = [['', 'Custom (no preset)'], ...sortedPresets.map(([k, p]) => [k, p.name || k])]
      .map(([k, label]) => `<button type="button" class="ufapi-option" data-value="${esc(k)}" style="display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;background:transparent;border:0;color:var(--fg);font:inherit;cursor:pointer;text-align:left;">${_apiIconFor(k)}<span>${esc(label)}</span></button>`).join('');
    formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>API Integration</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label">Preset</label>
            <div style="position:relative;flex:1;min-width:0;">
              <select id="uf-api-preset" tabindex="-1" aria-hidden="true" style="position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;"><option value="">Custom (no preset)</option>${selectOpts}</select>
              <button type="button" id="uf-api-preset-trigger" class="settings-select" style="display:flex;align-items:center;gap:10px;cursor:pointer;text-align:left;width:100%;padding-right:24px;position:relative;">
                <span class="ufapi-icon" style="display:inline-flex;align-items:center;">${_apiCustomIco}</span>
                <span class="ufapi-label" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Custom (no preset)</span>
                <span aria-hidden="true" style="position:absolute;right:8px;top:50%;transform:translateY(-50%);opacity:0.5;font-size:10px;pointer-events:none;">▾</span>
              </button>
              <div id="uf-api-preset-menu" style="display:none;position:absolute;top:calc(100% + 2px);left:0;right:0;z-index:1000;background:var(--panel);border:1px solid var(--border);border-radius:6px;max-height:340px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,0.25);">${_apiRows}</div>
            </div>
          </div>
          <div class="settings-row"><label class="settings-label">Name</label><input id="uf-api-name" class="settings-input" placeholder="My Service"></div>
          <div class="settings-row"><label class="settings-label">Base URL</label><input id="uf-api-url" class="settings-input" placeholder="http://localhost:8080"></div>
          <div id="uf-api-ntfy-hint" style="display:none;font-size:11px;line-height:1.35;opacity:0.68;margin:-2px 0 2px 106px;"></div>
          <div class="settings-row"><label class="settings-label">Auth${_apiHint('How this service expects the credential to be sent. <b>Bearer</b> = sends "Authorization: Bearer YOUR_KEY" (most modern APIs, ntfy, OpenAI-style). <b>Header</b> = sends YOUR_KEY verbatim under a header name you choose (Miniflux uses X-Auth-Token). <b>Basic</b> = HTTP basic auth (user:pass). <b>None</b> = the API is open / no auth.')}</label><select id="uf-api-auth" class="settings-input"><option value="bearer">Bearer (most common)</option><option value="header">Header</option><option value="basic">Basic</option><option value="none">None</option></select></div>
          <div class="settings-row" id="uf-api-header-row"><label class="settings-label">Header${_apiHint('The HTTP header name the key goes under (Miniflux: X-Auth-Token; most others: Authorization). Only used when Auth = Header.')}</label><input id="uf-api-header" class="settings-input" placeholder="X-Auth-Token"></div>
          <div class="settings-row"><label class="settings-label">API Key${_apiHint('The secret token the service issued you (generated in its admin panel / settings). Used to prove your identity on each request. Required for any Auth mode except None.')}</label><input id="uf-api-key" class="settings-input" type="password" placeholder="Token/key"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-api-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-api-test" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Test</button>
            <button class="admin-btn-add" id="uf-api-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save</button>
            <button class="admin-btn-add" id="uf-api-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
          </div>
        </div>
      </div>`;
    // Custom preset dropdown wire-up (hidden select stays as data source).
    (() => {
      const trig = el('uf-api-preset-trigger');
      const menu = el('uf-api-preset-menu');
      const sel = el('uf-api-preset');
      if (!trig || !menu || !sel) return;
      const lbl = trig.querySelector('.ufapi-label');
      const ico = trig.querySelector('.ufapi-icon');
      const _setFromKey = (k) => {
        const row = menu.querySelector(`.ufapi-option[data-value="${k}"]`);
        const text = row?.querySelector('span')?.textContent || 'Custom (no preset)';
        if (lbl) lbl.textContent = text;
        if (ico) ico.innerHTML = _apiIconFor(k);
      };
      // Menu is reused (hidden, not recreated). close() hides it and tears down
      // its outside-click listener + Escape-stack entry; bindMenuDismiss is
      // re-registered fresh on each open (see _open).
      let _close = () => { menu.style.display = 'none'; };
      const _open = () => {
        menu.style.display = 'block';
        const tRect = trig.getBoundingClientRect();
        const mRect = menu.getBoundingClientRect();
        const below = window.innerHeight - tRect.bottom;
        const above = tRect.top;
        if (mRect.height > below && above > below) { menu.style.top = 'auto'; menu.style.bottom = 'calc(100% + 2px)'; }
        else { menu.style.top = 'calc(100% + 2px)'; menu.style.bottom = 'auto'; }
        _close = bindMenuDismiss(menu, () => { menu.style.display = 'none'; }, (ev) => !menu.contains(ev.target) && ev.target !== trig);
      };
      trig.addEventListener('click', (e) => { e.stopPropagation(); menu.style.display === 'block' ? _close() : _open(); });
      menu.querySelectorAll('.ufapi-option').forEach(btn => {
        btn.addEventListener('mouseenter', () => { btn.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
        btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const k = btn.dataset.value || '';
          sel.value = k;
          _setFromKey(k);
          _close();
          sel.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });
      _setFromKey(sel.value || '');
    })();

    const preset = el('uf-api-preset'), name = el('uf-api-name'), url = el('uf-api-url'), auth = el('uf-api-auth'), header = el('uf-api-header'), key = el('uf-api-key'), ntfyHint = el('uf-api-ntfy-hint');
    let _editId = editId && editId !== 'new' ? editId : null;
    // Load existing
    if (_editId) {
      try {
        const r = await fetch('/api/auth/integrations', { credentials: 'same-origin' });
        const d = await r.json();
        const item = (d.integrations || []).find(i => i.id === _editId);
        if (item) { name.value = item.name || ''; url.value = item.base_url || ''; auth.value = item.auth_type || 'none'; header.value = item.auth_header || ''; }
      } catch (_) {}
    }
    // Native <select>: the option `value` is the preset key directly, so
    // no typed-name → key lookup is needed (datalist-era leftover).
    const _applyPreset = () => {
      const p = presets[preset.value];
      const isNtfy = preset.value === 'ntfy' || (p && (p.name || '').toLowerCase() === 'ntfy');
      const isUrlAuth = preset.value === 'discord_webhook'; // secret embedded in URL — no key/auth fields needed
      if (ntfyHint) {
        ntfyHint.style.display = isNtfy ? 'block' : 'none';
        if (isNtfy) {
          ntfyHint.innerHTML = 'Enter the ntfy server URL Odysseus can reach. Examples: <code>http://127.0.0.1:8091</code>, <code>http://100.x.y.z:8091</code>, or <code>https://ntfy.example.com</code>.';
        }
      }
      if (url) {
        url.placeholder = isNtfy ? 'http://127.0.0.1:8091' : isUrlAuth ? 'https://discord.com/api/webhooks/...' : 'http://localhost:8080';
      }
      // For presets that embed the secret in the URL, hide auth/key/header rows
      // so users aren't confused into thinking they need to fill them in.
      const keyRow = key?.closest('.settings-row');
      const authRow = auth?.closest('.settings-row');
      const headerRow = el('uf-api-header-row');
      if (keyRow) keyRow.style.display = isUrlAuth ? 'none' : '';
      if (authRow) authRow.style.display = isUrlAuth ? 'none' : '';
      if (headerRow) headerRow.style.display = isUrlAuth ? 'none' : '';
      if (!p) return;
      name.value = p.name || '';
      auth.value = p.auth_type || 'none';
      header.value = p.auth_header || '';
    };
    preset.addEventListener('change', _applyPreset);
    _applyPreset();
    el('uf-api-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });
    el('uf-api-save').addEventListener('click', async () => {
      const presetKey = preset.value || undefined;
      const nameValue = name.value.trim();
      const urlValue = url.value.trim();
      if (!nameValue) { el('uf-api-msg').textContent = 'Name required'; el('uf-api-msg').style.color = 'var(--red)'; return; }
      if (!urlValue) { el('uf-api-msg').textContent = 'Base URL required'; el('uf-api-msg').style.color = 'var(--red)'; return; }
      const body = { name: nameValue, base_url: urlValue, auth_type: auth.value, auth_header: header.value, preset: presetKey };
      if (key.value) body.api_key = key.value;
      try {
        const u = _editId ? `/api/auth/integrations/${_editId}` : '/api/auth/integrations';
        const m = _editId ? 'PUT' : 'POST';
        const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (!r.ok) throw new Error();
        const saved = await r.json().catch(() => null);
        // If this was a create, capture the new ID so Test works
        // immediately without needing a form reopen. The POST response
        // shape is {ok, integration: {id, ...}} — saved.id at the top
        // level would silently miss, leaving Test perpetually stuck on
        // "Save first" until the form was reopened.
        if (!_editId && saved) _editId = saved.integration?.id || saved.id;
        el('uf-api-msg').textContent = 'Saved'; el('uf-api-msg').style.color = 'var(--green,#50fa7b)';
        await renderList();
        notifyIntegrationsChanged();
      } catch (_) { el('uf-api-msg').textContent = 'Failed'; el('uf-api-msg').style.color = 'var(--red)'; }
    });
    el('uf-api-test').addEventListener('click', async () => {
      if (!_editId) { el('uf-api-msg').textContent = 'Save first'; return; }
      try {
        const r = await fetch(`/api/auth/integrations/${_editId}/test`, { method: 'POST', credentials: 'same-origin' });
        const d = await r.json();
        // Backend returns {ok: bool, message: str}
        if (d.ok) {
          el('uf-api-msg').textContent = d.message || 'Connected';
          el('uf-api-msg').style.color = 'var(--green,#50fa7b)';
        } else {
          el('uf-api-msg').textContent = (d.message || d.error || d.detail || `HTTP ${r.status}`).slice(0, 360);
          el('uf-api-msg').style.color = 'var(--red)';
        }
      } catch (e) { el('uf-api-msg').textContent = 'Error: ' + e.message; el('uf-api-msg').style.color = 'var(--red)'; }
    });
  }

  // ── CalDAV form (supports add + edit per account) ──
  async function showCalDavForm(editId) {
    const isNew = !editId || editId === 'new';
    formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>${isNew ? 'Add CalDAV Calendar' : 'Edit CalDAV Calendar'}</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label">Label</label><input id="uf-caldav-label" class="settings-input" placeholder="e.g. Work, Personal"></div>
          <div class="settings-row"><label class="settings-label">Server URL</label><input id="uf-caldav-url" class="settings-input" placeholder="https://www.google.com/calendar/dav/you@gmail.com/user/"></div>
          <div class="settings-row"><label class="settings-label">Username</label><input id="uf-caldav-user" class="settings-input" placeholder="you@example.com"></div>
          <div class="settings-row"><label class="settings-label">Password</label><input id="uf-caldav-pass" class="settings-input" type="password" placeholder="${isNew ? '' : 'Leave blank to keep existing'}"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-caldav-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
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

    el('uf-caldav-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });

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

  // ── CardDAV form + contacts manager ──
  async function showCardDavForm() {
    formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>Contacts (CardDAV)</h2>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label">URL</label><input id="uf-carddav-url" class="settings-input" placeholder="http://localhost:5232/user/contacts/"></div>
          <div class="settings-row"><label class="settings-label">Username</label><input id="uf-carddav-user" class="settings-input"></div>
          <div class="settings-row"><label class="settings-label">Password</label><input id="uf-carddav-pass" class="settings-input" type="password"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-carddav-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
            <button class="admin-btn-add" id="uf-carddav-save" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
              Save
            </button>
            <button class="admin-btn-add" id="uf-carddav-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Cancel
            </button>
          </div>
        </div>
      </div>
      <div class="admin-card contacts-manager" style="margin-top:8px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
          <h2 style="font-size:13px;margin:0;display:flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>Contacts Import <span id="cm-count" style="opacity:0.5;font-weight:normal;font-size:11px;"></span></h2>
          <button class="admin-btn-sm" id="cm-import-btn" style="margin-left:auto;">Import</button>
          <button class="admin-btn-sm" id="cm-export-vcf-btn">Export .vcf</button>
          <button class="admin-btn-sm" id="cm-export-csv-btn">Export .csv</button>
          <button class="admin-btn-sm" id="cm-add-toggle">+ Add</button>
          <input type="file" id="cm-import-file" accept=".vcf,.csv,text/vcard,text/csv" multiple style="display:none">
        </div>
        <div id="cm-add-row" class="contacts-add-row" style="display:none;flex-direction:column;gap:4px;">
          <input id="cm-add-name" class="settings-input" placeholder="Name">
          <input id="cm-add-email" class="settings-input" placeholder="email@example.com">
          <input id="cm-add-phone" class="settings-input" placeholder="Phone (optional)">
          <input id="cm-add-address" class="settings-input" placeholder="Address (optional)">
          <div style="display:flex;gap:6px;justify-content:flex-end;"><button class="admin-btn-sm" id="cm-add-save">Save</button></div>
        </div>
        <input type="text" id="cm-search" class="settings-input" placeholder="Search contacts (name, email, phone, address)" style="margin-top:6px;">
        <div id="cm-list" class="contacts-list"><div style="opacity:0.4;font-size:11px;padding:8px 2px;">Loading…</div></div>
      </div>`;
    try {
      const r = await fetch('/api/contacts/config', { credentials: 'same-origin' }); const d = await r.json();
      el('uf-carddav-url').value = d.url || ''; el('uf-carddav-user').value = d.username || '';
      // Server masks the password as '***' when one is saved (or '' when
      // none). Surface that state via the input's placeholder so users
      // can tell their password is already on file without us echoing it.
      const passInput = el('uf-carddav-pass');
      if (passInput && d.password) passInput.placeholder = '(unchanged)';
    } catch (_) {}
    el('uf-carddav-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });
    el('uf-carddav-save').addEventListener('click', async () => {
      const body = { carddav_url: el('uf-carddav-url').value, carddav_username: el('uf-carddav-user').value };
      if (el('uf-carddav-pass').value) body.carddav_password = el('uf-carddav-pass').value;
      try {
        await fetch('/api/contacts/config', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        el('uf-carddav-msg').textContent = 'Saved';
        el('uf-carddav-msg').style.color = 'var(--green, #50fa7b)';
        // Refresh both the sub-panel (contacts manager) AND the
        // outer integrations list so the CardDAV row appears
        // immediately instead of waiting for a page reload.
        await _renderContactsManager();
        await renderList();
        notifyIntegrationsChanged();
      } catch (_) {
        el('uf-carddav-msg').textContent = 'Failed';
        el('uf-carddav-msg').style.color = 'var(--red)';
      }
    });
    // Add-row toggle + save
    el('cm-add-toggle')?.addEventListener('click', () => {
      const row = el('cm-add-row');
      const open = row.style.display !== 'none';
      row.style.display = open ? 'none' : 'flex';
      if (!open) el('cm-add-name')?.focus();
    });
    el('cm-add-save')?.addEventListener('click', async () => {
      const name = el('cm-add-name').value.trim();
      const email = el('cm-add-email').value.trim();
      const phone = el('cm-add-phone')?.value.trim() || '';
      const address = el('cm-add-address')?.value.trim() || '';
      // Need at least a name or email; address-only entries without a
      // name aren't useful as a contact.
      if (!name && !email) { (name ? el('cm-add-email') : el('cm-add-name')).focus(); return; }
      try {
        await fetch('/api/contacts/add', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, email, phone, address }) });
      } catch (_) {}
      el('cm-add-name').value = '';
      el('cm-add-email').value = '';
      if (el('cm-add-phone')) el('cm-add-phone').value = '';
      if (el('cm-add-address')) el('cm-add-address').value = '';
      el('cm-add-row').style.display = 'none';
      await _renderContactsManager();
    });
    const _downloadContacts = async (format) => {
      const btn = el(format === 'csv' ? 'cm-export-csv-btn' : 'cm-export-vcf-btn');
      const orig = btn ? btn.textContent : '';
      if (btn) { btn.textContent = 'Exporting...'; btn.disabled = true; }
      try {
        const res = await fetch(`/api/contacts/export?format=${encodeURIComponent(format)}`, { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Export failed');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = format === 'csv' ? 'odysseus-contacts.csv' : 'odysseus-contacts.vcf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (_) {
        uiModule.showError ? uiModule.showError('Export failed') : alert('Export failed');
      } finally {
        if (btn) { btn.textContent = orig; btn.disabled = false; }
      }
    };
    el('cm-export-vcf-btn')?.addEventListener('click', () => _downloadContacts('vcf'));
    el('cm-export-csv-btn')?.addEventListener('click', () => _downloadContacts('csv'));

    // Import .vcf/.csv — read each selected file as text, concatenate by type,
    // then POST. Imported CardDAV contacts immediately feed email autocomplete
    // because compose searches /api/contacts/search.
    el('cm-import-btn')?.addEventListener('click', () => el('cm-import-file')?.click());
    el('cm-import-file')?.addEventListener('change', async (e) => {
      const files = Array.from(e.target.files || []);
      if (!files.length) return;
      const btn = el('cm-import-btn');
      const orig = btn ? btn.textContent : '';
      if (btn) { btn.textContent = 'Importing…'; btn.disabled = true; }
      try {
        const texts = await Promise.all(files.map(f => f.text()));
        const vcfParts = [];
        const csvParts = [];
        texts.forEach((text, idx) => {
          const name = (files[idx]?.name || '').toLowerCase();
          if (name.endsWith('.csv') || !String(text || '').toUpperCase().includes('BEGIN:VCARD')) csvParts.push(text);
          else vcfParts.push(text);
        });
        let imported = 0, total = 0, failed = 0;
        const _postImport = async (body) => {
          const r = await fetch('/api/contacts/import', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          const d = await r.json();
          if (d.error) throw new Error(d.error);
          imported += Number(d.imported || 0);
          total += Number(d.total || 0);
          failed += Number(d.failed || 0);
        };
        if (vcfParts.length) await _postImport({ vcf: vcfParts.join('\n') });
        if (csvParts.length) await _postImport({ csv: csvParts.join('\n') });
        if (!vcfParts.length && !csvParts.length) throw new Error('No contact data found');
        const msg = `Imported ${imported}/${total}` + (failed ? ` (${failed} failed)` : '');
        uiModule.showToast ? uiModule.showToast(msg) : null;
      } catch (err) {
        uiModule.showError ? uiModule.showError(err?.message || 'Import failed') : alert(err?.message || 'Import failed');
      } finally {
        if (btn) { btn.textContent = orig; btn.disabled = false; }
        e.target.value = '';
        await _renderContactsManager();
      }
    });
    await _renderContactsManager();
  }

  // Render the contacts list inside the manager card with inline edit +
  // delete. Each row: name + emails; pencil flips to editable inputs.
  async function _renderContactsManager() {
    const list = el('cm-list');
    if (!list) return;
    let contacts = [];
    try {
      const r = await fetch('/api/contacts/list', { credentials: 'same-origin' });
      const d = await r.json();
      contacts = d.contacts || [];
    } catch (_) {
      list.innerHTML = '<div style="opacity:0.5;font-size:11px;padding:8px 2px;">Failed to load contacts (check CardDAV config above).</div>';
      return;
    }
    const cnt = el('cm-count');
    if (cnt) cnt.textContent = contacts.length ? `(${contacts.length})` : '';
    if (!contacts.length) {
      list.innerHTML = '<div style="opacity:0.4;font-size:11px;padding:8px 2px;">No contacts yet.</div>';
      return;
    }
    // Sort by name for a stable list.
    contacts.sort((a, b) => (a.name || '').localeCompare(b.name || ''));

    // Live filter — search across name/emails/phones/address.
    const searchInput = el('cm-search');
    const q = (searchInput?.value || '').trim().toLowerCase();
    const filtered = !q ? contacts : contacts.filter(c => {
      const hay = [
        c.name || '',
        (c.emails || []).join(' '),
        (c.phones || []).join(' '),
        c.address || '',
      ].join(' ').toLowerCase();
      return hay.includes(q);
    });
    if (cnt) cnt.textContent = contacts.length ? `(${filtered.length}/${contacts.length})` : '';

    if (!filtered.length) {
      list.innerHTML = `<div style="opacity:0.4;font-size:11px;padding:8px 2px;">${q ? 'No matches.' : 'No contacts yet.'}</div>`;
    } else {
      list.innerHTML = filtered.map(c => {
        const emails = (c.emails || []).join(', ');
        const phones = (c.phones || []).join(', ');
        const address = c.address || '';
        const sub = [emails, phones, address].filter(Boolean).join(' · ');
        return `<div class="contact-row" data-uid="${esc(c.uid)}">
          <div class="contact-row-view" style="display:flex;align-items:center;gap:8px;">
            <div style="flex:1;min-width:0;">
              <div class="contact-name" style="font-size:12px;font-weight:600;">${esc(c.name || '(no name)')}</div>
              <div class="contact-sub" style="font-size:10px;opacity:0.55;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(sub)}</div>
            </div>
            <button class="admin-btn-sm contact-edit" title="Edit" style="display:inline-flex;align-items:center;gap:4px;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 35%, var(--border));">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              Edit
            </button>
            <button class="admin-btn-sm contact-del" title="Delete" style="opacity:0.85;display:inline-flex;align-items:center;gap:4px;">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              Delete
            </button>
          </div>
          <div class="contact-row-edit" style="display:none;flex-direction:column;gap:4px;">
            <input class="settings-input contact-edit-name" value="${esc(c.name || '')}" placeholder="Name">
            <input class="settings-input contact-edit-emails" value="${esc(emails)}" placeholder="email1, email2">
            <input class="settings-input contact-edit-phones" value="${esc(phones)}" placeholder="phone1, phone2">
            <input class="settings-input contact-edit-address" value="${esc(address)}" placeholder="Address">
            <div style="display:flex;gap:6px;"><button class="admin-btn-sm contact-save">Save</button><button class="admin-btn-sm contact-cancel" style="opacity:0.7;">Cancel</button></div>
          </div>
        </div>`;
      }).join('');
    }

    // Wire the search input — debounced so we don't refetch on every key.
    if (searchInput && !searchInput._wired) {
      searchInput._wired = true;
      let _t;
      searchInput.addEventListener('input', () => {
        clearTimeout(_t);
        _t = setTimeout(() => _renderContactsManager(), 80);
      });
    }
    // Stash latest contacts so the search input doesn't have to refetch.
    list._lastContacts = contacts;
    // Wire each row's edit / delete / save / cancel.
    list.querySelectorAll('.contact-row').forEach(row => {
      const uid = row.dataset.uid;
      const view = row.querySelector('.contact-row-view');
      const editForm = row.querySelector('.contact-row-edit');
      row.querySelector('.contact-edit')?.addEventListener('click', () => {
        view.style.display = 'none';
        editForm.style.display = 'flex';
      });
      row.querySelector('.contact-cancel')?.addEventListener('click', () => {
        editForm.style.display = 'none';
        view.style.display = 'flex';
      });
      row.querySelector('.contact-save')?.addEventListener('click', async () => {
        const body = {
          name: row.querySelector('.contact-edit-name').value.trim(),
          emails: row.querySelector('.contact-edit-emails').value.split(',').map(s => s.trim()).filter(Boolean),
          phones: row.querySelector('.contact-edit-phones').value.split(',').map(s => s.trim()).filter(Boolean),
          address: row.querySelector('.contact-edit-address')?.value.trim() || '',
        };
        try {
          await fetch('/api/contacts/' + encodeURIComponent(uid), { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        } catch (_) {}
        await _renderContactsManager();
      });
      row.querySelector('.contact-del')?.addEventListener('click', async () => {
        const ok = uiModule.styledConfirm
          ? await uiModule.styledConfirm('Delete this contact?', { confirmText: 'Delete', danger: true })
          : window.confirm('Delete this contact?');
        if (!ok) return;
        try {
          await fetch('/api/contacts/' + encodeURIComponent(uid), { method: 'DELETE', credentials: 'same-origin' });
        } catch (_) {}
        await _renderContactsManager();
      });
    });
  }

  // ── Email form (multi-account) ──
  // When editId is a real account id, edit that row. When editId is falsy or 'new',
  // create a fresh account. Posts to /api/email/accounts, never to the legacy
  // /api/email/config which would overwrite the default.
  async function showEmailForm(editId) {
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
          <div class="settings-row"><label class="settings-label">Provider${_hint('Pick a known provider to auto-fill the IMAP and SMTP host/port. Choose Custom to type your own.')}</label>
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
          <div class="settings-row"><label class="settings-label">Name${_hint('Optional label for this account (e.g. “Work” or “Personal”). Leave blank to use the email address.')}</label><input id="uf-email-name" class="settings-input" placeholder="(optional — leave blank to use email)"></div>
          <div class="settings-row"><label class="settings-label">Email${_hint('Your email address. Used as the From: header on outgoing mail and as the display label when Name is blank.')}</label><input id="uf-email-from" class="settings-input" placeholder="you@example.com"></div>
          <div class="settings-row"><label class="settings-label">Display Name${_hint('Your name as it appears in the From: field of emails you send, e.g. Jane Smith. Auto-filled from Google during OAuth.')}</label><input id="uf-display-name" class="settings-input" placeholder="Your Name"></div>
          <div id="uf-oauth-section" style="display:none;margin:8px 0;padding:10px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb,var(--accent,#50fa7b) 6%,transparent)">
            <div style="font-size:11px;font-weight:600;margin-bottom:6px">Google OAuth2 — required for Workspace / .edu accounts</div>
            <div id="uf-oauth-status" style="font-size:11px;opacity:0.7;margin-bottom:6px">${existing && existing.oauth_provider === 'google' ? '✓ Connected via Google OAuth' : 'Not connected — click below to authorize'}</div>
            <button type="button" id="uf-oauth-btn" class="admin-btn-add" style="font-size:11px">${existing && existing.oauth_provider === 'google' ? 'Reconnect with Google' : 'Connect with Google'}</button>
          </div>
          <div style="font-size:11px;font-weight:600;opacity:0.6;margin:4px 0 2px;display:flex;align-items:center;gap:5px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;" aria-hidden="true"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>IMAP (Receiving)</div>
          <div class="settings-row"><label class="settings-label">Host${_hint('Your IMAP server, e.g. imap.gmail.com, imap.migadu.com, a LAN host, or a Tailscale IP for Dovecot.')}</label><input id="uf-imap-host" class="settings-input" placeholder="imap.example.com"></div>
          <div class="settings-row"><label class="settings-label">Port${_hint('993 for IMAPS (most providers), 143 for plain or STARTTLS. Local servers often use a custom port like 31143.')}</label><input id="uf-imap-port" class="settings-input" type="number" placeholder="993" style="max-width:100px"></div>
          <div class="settings-row"><label class="settings-label">Username${_hint('Yes — your full email address goes here too (e.g. you@gmail.com). Same as the Email field above for almost every provider.')}</label><input id="uf-imap-user" class="settings-input" placeholder="you@example.com"></div>
          <div class="uf-password-section"><div class="settings-row"><label class="settings-label">Password${_hint('For Gmail, iCloud, and Yahoo: paste your App Password (NOT your normal account password). For Migadu and Fastmail, your mailbox password usually works. Outlook / Office 365 generally requires OAuth and will not work with this password form.')}</label><input id="uf-imap-pass" class="settings-input" type="password" placeholder="${placeholderPass}"></div></div>
          <div class="settings-row"><label class="settings-label">STARTTLS${_hint('Turn ON for port 143/587 to upgrade plain to TLS. Turn OFF for port 993 (IMAPS — already encrypted) or a local server with no TLS configured.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-imap-starttls" checked><span class="admin-slider"></span></label></div>
          <div style="font-size:11px;font-weight:600;opacity:0.6;margin:8px 0 2px;display:flex;align-items:center;gap:5px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--accent, var(--red));flex-shrink:0;" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>SMTP (Sending) <span style="font-weight:normal;opacity:0.7">— optional, leave blank for read-only</span></div>
          <div class="settings-row"><label class="settings-label">Host${_hint('Your outgoing-mail server, e.g. smtp.gmail.com. Leave blank to make this account read-only.')}</label><input id="uf-smtp-host" class="settings-input" placeholder="smtp.example.com"></div>
          <div class="settings-row"><label class="settings-label">Port${_hint('465 for SSL/SMTPS, 587 for STARTTLS. 25 is usually blocked by ISPs.')}</label><input id="uf-smtp-port" class="settings-input" type="number" placeholder="465" style="max-width:100px"></div>
          <div class="settings-row"><label class="settings-label">Security${_hint('SSL for port 465, STARTTLS for port 587, or None for local SMTP bridges such as Proton Mail Bridge.')}</label><select id="uf-smtp-security" class="settings-select"><option value="ssl">SSL</option><option value="starttls">STARTTLS</option><option value="none">None</option></select></div>
          <div class="settings-row"><label class="settings-label">Same as IMAP${_hint('Use the IMAP username and password for SMTP too (right for almost every provider). Turn off to enter separate SMTP credentials.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-smtp-same" checked><span class="admin-slider"></span></label></div>
          <div class="settings-row uf-smtp-creds"><label class="settings-label">Username${_hint('Usually the same as your IMAP username (your email address).')}</label><input id="uf-smtp-user" class="settings-input"></div>
          <div class="settings-row uf-smtp-creds"><label class="settings-label">Password${_hint('Your SMTP password — often the same as your IMAP password. Outlook / Office 365 generally requires OAuth and will not work with this password form.')}</label><input id="uf-smtp-pass" class="settings-input" type="password" placeholder="${placeholderPass}"></div>
          <div class="settings-row" style="margin-top:4px"><label class="settings-label">Default${_hint('Use this account whenever no specific account is chosen.')}</label><label class="admin-switch" style="margin-left:0"><input type="checkbox" id="uf-email-default"><span class="admin-slider"></span></label><span style="font-size:10px;opacity:0.5;margin-left:6px">Used when nothing else is selected</span></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
            <span id="uf-email-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
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
    el('uf-email-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });

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
        const sp = window.spinnerModule || (await import('../../spinner.js')).default;
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
        integrationNotice = 'Email account saved. For more settings, go to Settings > Email.';
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

  // ── Vaultwarden form ──
  async function showVaultForm() {
    formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <h2 style="font-size:13px">Vaultwarden (Password Vault)</h2>
        <div id="uf-vault-status" style="font-size:11px;opacity:0.7;margin-bottom:8px">Loading...</div>
        <div class="settings-col">
          <div class="settings-row"><label class="settings-label">Server URL</label><input id="uf-vault-url" class="settings-input" placeholder="https://vault.example.com"></div>
          <div class="settings-row"><label class="settings-label">Email</label><input id="uf-vault-email" class="settings-input" placeholder="you@example.com"></div>
          <div class="settings-row"><label class="settings-label">Master Password</label><input id="uf-vault-pass" class="settings-input" type="password" placeholder="Only required for Login / Unlock"></div>
          <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap;">
            <span id="uf-vault-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
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

    el('uf-vault-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });

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

  // ── MCP form — full management view ──
  async function showMcpForm(editId) {
    // Toggle an in-flight loading state on a button (disabled + dimmed + label).
    function _setBtnLoading(btn, loading, label) {
      if (!btn) return;
      btn.disabled = loading;
      btn.style.opacity = loading ? '0.6' : '';
      btn.style.cursor = loading ? 'progress' : '';
      if (label != null) btn.textContent = label;
    }
    function _showMcpPasteback(id) {
      const msg = el('uf-mcp-msg'); if (!msg) return;
      if (el('uf-mcp-pasteback')) return;  // already shown
      msg.innerHTML =
        'Authorize in the opened tab. If the redirect fails (remote access), paste the resulting URL here: ' +
        '<input id="uf-mcp-pasteback" class="settings-input" placeholder="http://localhost:7000/api/mcp/oauth/callback?code=..." style="margin-top:4px">' +
        '<button class="admin-btn-sm" id="uf-mcp-paste-go" style="margin-top:4px">Submit</button>';
      const pasteGo = el('uf-mcp-paste-go');
      if (pasteGo) pasteGo.addEventListener('click', async () => {
        const cb = el('uf-mcp-pasteback').value.trim();
        if (!cb) return;
        const pf = new FormData(); pf.append('callback_url', cb);
        _setBtnLoading(pasteGo, true, 'Submitting…');
        try {
          await fetch(`/api/mcp/oauth/exchange/${id}`, { method: 'POST', credentials: 'same-origin', body: pf });
        } finally {
          _setBtnLoading(pasteGo, false, 'Submit');
        }
      });
    }

    // Drives the OAuth flow: waits for the auth_url (discovery+DCR may lag),
    // opens it once, then resolves on connected/error.
    async function _handleMcpAuth(id, initialAuthUrl, tries = 90) {
      let opened = false;
      const openAuth = (u) => { if (!opened && u) { opened = true; window.open(u, '_blank', 'noopener'); _showMcpPasteback(id); } };
      openAuth(initialAuthUrl);
      const msg = el('uf-mcp-msg');
      let fails = 0;
      for (let i = 0; i < tries; i++) {
        await new Promise(res => setTimeout(res, 2000));
        try {
          const r = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const list = await r.json();
          fails = 0;
          const s = Array.isArray(list) ? list.find(x => x.id === id) : null;
          if (!s) continue;
          if (s.auth_url) openAuth(s.auth_url);
          if (s.status === 'connected') {
            if (msg) msg.textContent = `Connected (${s.tool_count || 0} tools)`;
            await renderList(); return;
          }
          if (s.status === 'error') {
            if (msg) msg.textContent = `Failed: ${s.error || 'unknown'}`; return;
          }
        } catch (e) {
          // Tolerate a single blip, but surface persistent failures instead of
          // silently polling until timeout.
          if (++fails >= 5 && msg) msg.textContent = `Status check failing (${e.message || 'network error'}) — still retrying…`;
        }
      }
      if (msg) msg.textContent = 'Authorization timed out. Reconnect from the server list to retry.';
    }
    if (editId && editId !== 'new') {
      // Show management view for existing server
      formEl.innerHTML = '<div class="admin-card" style="margin-top:8px"><span style="opacity:0.5;font-size:11px">Loading...</span></div>';
      try {
        const res = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
        const servers = await res.json();
        const srv = servers.find(s => (s.id || s.name) === editId);
        if (!srv) { formEl.innerHTML = '<div class="admin-card" style="margin-top:8px">Server not found</div>'; return; }
        const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
        const statusColor = srv.needs_oauth ? '#e5a33a' : srv.status === 'connected' ? 'var(--green,#50fa7b)' : srv.status === 'error' ? 'var(--red)' : 'var(--fg)';
        const toolInfo = srv.status === 'connected' ? `${srv.enabled_tool_count}/${srv.tool_count} tools` : '';
        const statusText = srv.needs_oauth ? 'Needs authorization' : srv.status === 'connected' ? `Connected (${toolInfo})` : srv.status === 'error' ? `Error: ${esc(srv.error || 'unknown')}` : 'Disconnected';
        formEl.innerHTML = `
          <div class="admin-card" style="margin-top:8px">
            <h2 style="font-size:13px">${esc(srv.name)}</h2>
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${statusColor}"></span>
              <span style="font-size:11px;opacity:0.7">${statusText}</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px;justify-content:flex-end;">
              <span id="uf-mcp-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
              ${srv.needs_oauth ? `<a href="/api/mcp/oauth/authorize/${srv.id}" target="_blank" class="admin-btn-add" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));text-decoration:none;font-weight:600;">Authorize</a>` : ''}
              <button class="admin-btn-add" id="uf-mcp-reconnect" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Reconnect</button>
              <button class="admin-btn-add" id="uf-mcp-toggle" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">${srv.is_enabled ? 'Disable' : 'Enable'}</button>
              <button class="admin-btn-add" id="uf-mcp-cancel" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Close</button>
            </div>
            <div id="uf-mcp-tools-panel"></div>
          </div>`;
        // Reconnect
        el('uf-mcp-reconnect').addEventListener('click', async () => {
          const msg = el('uf-mcp-msg'); msg.textContent = 'Reconnecting...';
          try {
            const r = await fetch(`/api/mcp/servers/${srv.id}/reconnect`, { method: 'POST', credentials: 'same-origin' });
            const d = await r.json();
            msg.textContent = d.connected ? `Connected (${d.tool_count} tools)` : `Failed: ${d.error || 'unknown'}`;
            await renderList();
            showMcpForm(editId); // refresh this view
          } catch (e) { msg.textContent = 'Failed'; }
        });
        // Toggle enable/disable
        el('uf-mcp-toggle').addEventListener('click', async () => {
          const fd = new FormData(); fd.append('is_enabled', String(!srv.is_enabled));
          await fetch(`/api/mcp/servers/${srv.id}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
          await renderList();
          showMcpForm(editId);
        });
        el('uf-mcp-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });
        // Load tools list
        if (srv.status === 'connected' && srv.tool_count > 0) {
          const panel = el('uf-mcp-tools-panel');
          try {
            const tr = await fetch(`/api/mcp/servers/${srv.id}/tools`, { credentials: 'same-origin' });
            const tools = await tr.json();
            if (tools.length) {
              const disabled = new Set(tools.filter(t => t.is_disabled).map(t => t.name));
              panel.innerHTML = `<div class="mcp-tools-header"><span>Tools</span><span style="display:flex;gap:8px;align-items:center"><span class="mcp-tools-count">${tools.length - disabled.size}/${tools.length} enabled</span><a href="#" id="uf-mcp-all">All</a> <a href="#" id="uf-mcp-none">None</a></span></div><div class="mcp-tools-list">${tools.map(t => `<label title="${esc(t.description)}"><input type="checkbox" data-mcp-tool-name="${esc(t.name)}" ${!t.is_disabled ? 'checked' : ''}><span><strong>${esc(t.name)}</strong> <span style="opacity:0.5">— ${esc((t.description||'').slice(0,80))}</span></span></label>`).join('')}</div>`;
              const saveFn = async () => {
                const dis = [];
                panel.querySelectorAll('input[type=checkbox]').forEach(cb => { if (!cb.checked) dis.push(cb.dataset.mcpToolName); });
                await fetch(`/api/mcp/servers/${srv.id}/tools`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ disabled: dis }) });
                const cnt = panel.querySelector('.mcp-tools-count');
                if (cnt) cnt.textContent = `${tools.length - dis.length}/${tools.length} enabled`;
              };
              panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.addEventListener('change', saveFn));
              el('uf-mcp-all')?.addEventListener('click', (e) => { e.preventDefault(); panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true); saveFn(); });
              el('uf-mcp-none')?.addEventListener('click', (e) => { e.preventDefault(); panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false); saveFn(); });
            }
          } catch (_) { panel.innerHTML = '<span style="opacity:0.5;font-size:11px">Failed to load tools</span>'; }
        }
      } catch (_) { formEl.innerHTML = '<div class="admin-card" style="margin-top:8px">Failed to load server</div>'; }
    } else {
      // Add new MCP server form
      formEl.innerHTML = `
        <div class="admin-card" style="margin-top:8px">
          <h2 style="font-size:13px">Add MCP Server</h2>
          <div class="settings-col">
            <div class="settings-row"><label class="settings-label">Name</label><input id="uf-mcp-name" class="settings-input" placeholder="Server name"></div>
            <div class="settings-row"><label class="settings-label">Transport</label><select id="uf-mcp-transport" class="settings-input"><option value="stdio">stdio</option><option value="sse">SSE</option><option value="http">Streamable HTTP</option></select></div>
            <div id="uf-mcp-stdio-fields" style="display:flex;flex-direction:column;gap:6px;">
              <div class="settings-row"><label class="settings-label">Command</label><input id="uf-mcp-cmd" class="settings-input" placeholder="npx"></div>
              <div class="settings-row"><label class="settings-label">Args</label><input id="uf-mcp-args" class="settings-input" placeholder='["-y", "@modelcontextprotocol/server-filesystem"]'></div>
              <div class="settings-row"><label class="settings-label">Env</label><input id="uf-mcp-env" class="settings-input" placeholder='{"KEY": "value"}'></div>
            </div>
            <div id="uf-mcp-sse-fields" style="display:none;flex-direction:column;gap:6px;">
              <div class="settings-row"><label class="settings-label">URL</label><input id="uf-mcp-url" class="settings-input" placeholder="http://localhost:3001/sse"></div>
            </div>
            <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
              <span id="uf-mcp-msg" style="font-size:11px;flex:1;margin-right:8px"></span>
              <button class="admin-btn-add" id="uf-mcp-save" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save</button>
              <button class="admin-btn-add" id="uf-mcp-cancel" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
            </div>
          </div>
        </div>`;
      el('uf-mcp-transport').addEventListener('change', () => {
        const v = el('uf-mcp-transport').value;
        const isUrl = (v === 'sse' || v === 'http');
        el('uf-mcp-stdio-fields').style.display = isUrl ? 'none' : 'flex';
        el('uf-mcp-sse-fields').style.display = isUrl ? 'flex' : 'none';
        const urlInput = el('uf-mcp-url');
        if (urlInput) urlInput.placeholder = (v === 'http') ? 'https://mcp.example.com/mcp' : 'http://localhost:3001/sse';
      });
      el('uf-mcp-cancel').addEventListener('click', () => { formEl.style.display = 'none'; });
      el('uf-mcp-save').addEventListener('click', async () => {
        const transport = el('uf-mcp-transport').value;
        // routes/mcp_routes.py uses FastAPI Form(...) — send multipart, not JSON.
        const fd = new FormData();
        fd.append('name', el('uf-mcp-name').value);
        fd.append('transport', transport);
        if (transport === 'stdio') {
          fd.append('command', el('uf-mcp-cmd').value);
          // Unlike env below, an unparseable args value is not silently
          // defaulted: it would spawn the subprocess with an empty argv.
          let args;
          try { args = JSON.stringify(JSON.parse(el('uf-mcp-args').value || '[]')); }
          catch (_) { el('uf-mcp-msg').textContent = 'Args must be valid JSON, e.g. ["-y", "pkg"]'; return; }
          let env  = '{}'; try { env  = JSON.stringify(JSON.parse(el('uf-mcp-env').value  || '{}')); } catch (_) {}
          fd.append('args', args);
          fd.append('env', env);
        } else {
          fd.append('url', el('uf-mcp-url').value);
        }
        const saveBtn = el('uf-mcp-save'), cancelBtn = el('uf-mcp-cancel');
        const _origLabel = saveBtn.textContent;
        _setBtnLoading(saveBtn, true, 'Saving…'); if (cancelBtn) cancelBtn.disabled = true;
        try {
          const r = await fetch('/api/mcp/servers', { method: 'POST', credentials: 'same-origin', body: fd });
          const data = await r.json().catch(() => ({}));
          if (r.ok && data.needs_auth) {
            el('uf-mcp-msg').textContent = 'Preparing authorization…';
            _handleMcpAuth(data.id, data.auth_url);
          } else if (r.ok && (data.connected || data.status === 'connected')) {
            el('uf-mcp-msg').textContent = `Connected (${data.tool_count || 0} tools)`;
            formEl.style.display = 'none'; await renderList();
          } else if (r.ok) {
            el('uf-mcp-msg').textContent = 'Saved'; formEl.style.display = 'none'; await renderList();
          } else {
            // Surface the server's reason. The Args validation above rejects
            // unparseable JSON, but `"x"` and `{}` parse and are refused by
            // routes/mcp/mcp_routes.py with a message naming the expected
            // shape; a bare status code sends the user looking in the wrong
            // place. Matches what admin.js shows for the same endpoint.
            el('uf-mcp-msg').textContent = data.detail || `Failed (${r.status})`;
          }
        } catch (_) { el('uf-mcp-msg').textContent = 'Failed'; }
        finally { _setBtnLoading(saveBtn, false, _origLabel); if (cancelBtn) cancelBtn.disabled = false; }
      });
    }
  }

  async function showAgentForm(kind, editId) {
    const cfg = AGENT_CONFIGS[kind] || AGENT_CONFIGS.codex;
    let tokens = [];
    try {
      const tokRes = await fetch('/api/tokens', { credentials: 'same-origin' });
      if (tokRes.ok) tokens = await tokRes.json();
    } catch (_) {}

    const toolScopes = [
      { key: 'todos:read', label: 'Todos', detail: 'Read notes and checklists' },
      { key: 'todos:write', label: 'Todos write', detail: 'Create, update, delete, and toggle todo items' },
      { key: 'documents:read', label: 'Documents', detail: 'Read documents when a document API is enabled' },
      { key: 'documents:write', label: 'Documents write', detail: 'Create and update draft documents' },
      { key: 'email:read', label: 'Email', detail: 'Read email when an email API is enabled' },
      { key: 'email:draft', label: 'Email drafts', detail: 'Create email reply drafts without sending' },
      { key: 'email:send', label: 'Email send', detail: 'Send email directly' },
      { key: 'calendar:read', label: 'Calendar', detail: 'Read calendar events when enabled' },
      { key: 'calendar:write', label: 'Calendar write', detail: 'Create and update calendar events' },
      { key: 'memory:read', label: 'Memory', detail: 'Read memory when enabled' },
      { key: 'memory:write', label: 'Memory write', detail: 'Write memory when enabled' },
      { key: 'cookbook:read', label: 'Cookbook', detail: 'List cookbook tasks + tail their tmux output (debug a model serve from outside the UI)' },
      { key: 'cookbook:launch', label: 'Cookbook launch', detail: 'Launch and stop cookbook serve tasks. Powerful: runs SSH commands on your configured servers, bounded by the same allowlist the UI uses (vllm/python3/sglang/llama-server/...)' },
    ];
    // Strict name-prefix match keeps Codex and Claude tokens in their own forms.
    const agentTokens = (Array.isArray(tokens) ? tokens : []).filter(tok =>
      (tok.name || '').toLowerCase().startsWith(cfg.namePrefix)
    );
    const current = agentTokens.find(t => String(t.id) === String(editId));
    const _scopeIcons = {
      todos: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>',
      documents: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
      email: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><polyline points="2 6 12 13 22 6"/></svg>',
      calendar: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
      memory: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2a2.5 2.5 0 0 0-2.5 2.5 2.5 2.5 0 0 0-2.5 2.5A2.5 2.5 0 0 0 2 9.5v3A2.5 2.5 0 0 0 4.5 15a2.5 2.5 0 0 0 2.5 2.5A2.5 2.5 0 0 0 9.5 20H10V2z"/><path d="M14.5 2a2.5 2.5 0 0 1 2.5 2.5 2.5 2.5 0 0 1 2.5 2.5A2.5 2.5 0 0 1 22 9.5v3A2.5 2.5 0 0 1 19.5 15a2.5 2.5 0 0 1-2.5 2.5A2.5 2.5 0 0 1 14.5 20H14V2z"/></svg>',
      cookbook: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    };
    const _scopeNiceLabel = (label) => label.replace(/\s+(write|drafts?|send)$/i, '');
    const _scopeAction = (key) => (key.split(':')[1] || '').toLowerCase();
    const _pillStyle = (action) => {
      if (action === 'read') return 'background:rgba(150,150,150,0.18);color:var(--fg-muted,#888);';
      return 'background:color-mix(in srgb, var(--accent, var(--red)) 18%, transparent);color:var(--accent, var(--red));';
    };
    const scopeToggles = (t) => {
      const scopes = new Set(t.scopes || []);
      return toolScopes.map(scope => {
        const tool = scope.key.split(':')[0];
        const action = _scopeAction(scope.key);
        const icon = _scopeIcons[tool] || '';
        const niceLabel = _scopeNiceLabel(scope.label);
        return `
        <label class="settings-row" style="align-items:center;gap:8px;display:flex;min-height:30px;padding:2px 0;">
          <span style="opacity:0.7;display:inline-flex;align-items:center;justify-content:center;width:16px;flex-shrink:0;">${icon}</span>
          <span class="settings-label" style="width:75px;flex-shrink:0;padding:0;">${esc(niceLabel)}</span>
          <span style="font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;padding:1px 7px;border-radius:999px;flex-shrink:0;min-width:44px;text-align:center;margin-left:-3px;box-sizing:border-box;${_pillStyle(action)}">${esc(action)}</span>
          <span style="font-size:11px;line-height:1.35;opacity:0.62;flex:1;min-width:0;">${esc(scope.detail)}</span>
          <label class="admin-switch" style="margin-left:auto;flex-shrink:0;"><input type="checkbox" class="uf-codex-scope" data-token-id="${esc(t.id)}" data-scope="${esc(scope.key)}" ${scopes.has(scope.key) ? 'checked' : ''}><span class="admin-slider"></span></label>
        </label>`;
      }).join('');
    };
    const origin = window.location.origin || '';
    const setupForToken = (token) => cfg.buildSetup(origin, token);

    // Inline editor for the existing token the user clicked into (current).
    // Shows the rename input, the prefix/last-used, and scope toggles that
    // PATCH /api/tokens/{id} on change. The integration row's trash button
    // handles revoke, so no Revoke button in here.
    const editExistingHtml = current ? `
      <div style="border:1px solid var(--border);border-radius:6px;padding:9px 10px;margin-bottom:8px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
          <input type="text" id="uf-codex-existing-rename" data-token-id="${esc(current.id)}" value="${esc(current.name || cfg.defaultName)}" style="font-size:12px;font-weight:600;padding:3px 6px;flex:1;background:transparent;border:1px solid transparent;border-radius:4px;" title="Click to rename">
          <span style="font-size:10px;opacity:0.55;">${esc(current.token_prefix || 'token')}...${current.last_used_at ? ` · Last used ${new Date(current.last_used_at).toLocaleDateString()}` : ' · Never used'}</span>
        </div>
        <div style="font-size:11px;font-weight:600;opacity:0.62;margin-bottom:4px;">Permissions</div>
        ${scopeToggles(current)}
        <div id="uf-codex-existing-msg" style="font-size:11px;min-height:14px;margin-top:4px;"></div>
      </div>` : '';

    formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <div class="settings-col">
          ${editExistingHtml}
          <div id="uf-codex-prompt" style="display:${current ? 'none' : 'block'};padding:6px 0;">
            <div style="font-size:11px;opacity:0.7;margin-bottom:6px;">Name this ${esc(cfg.word)} agent so you can tell it apart from other ones (e.g. "${esc(cfg.defaultName)} — laptop").</div>
            <input type="text" id="uf-codex-name-input" class="settings-select" placeholder="${esc(cfg.defaultName)}" style="width:100%;font-size:12px;padding:6px 8px;">
          </div>
          <div id="uf-codex-pending" style="display:none;align-items:center;gap:8px;padding:6px 0;font-size:11px;opacity:0.7;"></div>
          <div id="uf-codex-reveal" style="display:none;width:100%;box-sizing:border-box;">
            <div style="font-weight:600;font-size:12px;margin-bottom:6px;">Token</div>

            <div style="font-size:11px;opacity:0.62;margin-bottom:4px;">Copy this token, it won't be shown again.</div>
            <div style="position:relative;">
              <code id="uf-codex-token" style="display:block;word-break:break-all;font-size:11px;padding:6px 30px 6px 8px;background:rgba(0,0,0,0.08);border-radius:4px;"></code>
              <button type="button" class="admin-btn-sm" id="uf-codex-copy-token" title="Copy token" aria-label="Copy token" style="position:absolute;right:4px;top:50%;transform:translateY(-50%);padding:3px 5px;background:none;border:none;color:inherit;opacity:0.7;cursor:pointer;display:inline-flex;align-items:center;">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </button>
            </div>

            <div style="margin-top:14px;font-weight:600;font-size:11px;margin-bottom:4px;">Quickstart &mdash; simply paste directly in your terminal.</div>
            <div style="font-size:11px;opacity:0.62;margin-bottom:6px;">${cfg.setupDescription}</div>
            <pre style="margin:0;white-space:pre;overflow-x:auto;max-height:220px;overflow-y:auto;font-size:10px;line-height:1.45;padding:8px 10px;background:rgba(0,0,0,0.08);border-radius:4px;width:100%;box-sizing:border-box;"><code id="uf-codex-setup-code"></code></pre>

            <div style="margin-top:14px;display:flex;align-items:center;gap:8px;">
              <span style="font-weight:600;font-size:11px;">Configure access</span>
              <span style="flex:1"></span>
              <button type="button" class="admin-btn-sm" id="uf-codex-copy-setup" title="Copy setup" aria-label="Copy setup" style="font-size:11px;font-weight:normal;display:inline-flex;align-items:center;gap:5px;opacity:0.85;">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                <span>Copy</span>
              </button>
              <button type="button" class="admin-btn-sm" id="uf-codex-toggle-config" aria-expanded="false" style="font-size:11px;font-weight:normal;display:inline-flex;align-items:center;gap:5px;opacity:0.85;">
                <svg id="uf-codex-toggle-config-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="transition:transform 0.15s"><polyline points="6 9 12 15 18 9"/></svg>
                <span>Configure</span>
              </button>
            </div>
            <div id="uf-codex-config-body" style="display:none;">
              <div style="font-size:11px;opacity:0.62;margin:4px 0 6px;">Toggle which Odysseus tools this agent can use. New agents start with chat only.</div>
              <div id="uf-codex-inline-scopes"></div>
            </div>
          </div>
          <div class="settings-row" style="margin-top:10px;align-items:center;gap:6px;">
            <button class="admin-btn-add" id="uf-codex-cancel" style="display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Cancel
            </button>
            <span id="uf-codex-msg" style="font-size:11px;flex:1;text-align:center;"></span>
            <button class="admin-btn-add" id="uf-codex-revoke" style="display:none;align-items:center;gap:5px;background:color-mix(in srgb, var(--color-error) 10%, transparent);color:var(--color-error);border:1px solid var(--color-error);font-weight:600;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
              Revoke
            </button>
            <button class="admin-btn-add" id="uf-codex-create-btn" style="display:${current ? 'none' : 'inline-flex'};align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2l-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/><path d="M15.5 7.5l3 3"/></svg>
              Create token
            </button>
            <button class="admin-btn-add" id="uf-codex-save" style="display:none;align-items:center;gap:5px;background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>
              Save
            </button>
          </div>
        </div>
      </div>`;

    // Editing an existing token: surface Revoke alongside Cancel, and stash
    // the id so the Revoke handler knows what to DELETE.
    if (current) {
      formEl.dataset.createdTokenId = String(current.id);
      const revokeBtn = el('uf-codex-revoke');
      if (revokeBtn) revokeBtn.style.display = 'inline-flex';
      // Inline rename + per-scope PATCH on change.
      const renameInput = el('uf-codex-existing-rename');
      if (renameInput) {
        const original = renameInput.value;
        const commit = async () => {
          const name = (renameInput.value || '').trim();
          if (!name || name === original) return;
          try {
            const r = await fetch(`/api/tokens/${renameInput.dataset.tokenId}`, {
              method: 'PATCH', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name }),
            });
            if (!r.ok) throw new Error('Save failed');
            notifyIntegrationsChanged();
          } catch (_) { renameInput.value = original; }
        };
        renameInput.addEventListener('blur', commit);
        renameInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); renameInput.blur(); } });
      }
      formEl.querySelectorAll('.uf-codex-scope').forEach(cb => {
        cb.addEventListener('change', async () => {
          const msg = el('uf-codex-existing-msg');
          const scopes = ['chat'].concat(
            Array.from(formEl.querySelectorAll('.uf-codex-scope:checked')).map(input => input.dataset.scope)
          );
          try {
            const r = await fetch(`/api/tokens/${cb.dataset.tokenId}`, {
              method: 'PATCH', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ scopes }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.detail || 'Failed');
            if (msg) { msg.textContent = 'Saved'; msg.style.color = 'var(--green, #50fa7b)'; setTimeout(() => { msg.textContent = ''; }, 1200); }
            notifyIntegrationsChanged();
          } catch (err) {
            cb.checked = !cb.checked;
            if (msg) { msg.textContent = (err && err.message) || 'Failed'; msg.style.color = 'var(--red)'; }
          }
        });
      });
    }

    el('uf-codex-cancel')?.addEventListener('click', () => { formEl.style.display = 'none'; });

    // Configure access — collapsed by default so the reveal panel doesn't
    // dump 13 toggles at once. Click reveals + rotates the caret.
    el('uf-codex-toggle-config')?.addEventListener('click', () => {
      const body = el('uf-codex-config-body');
      const btn = el('uf-codex-toggle-config');
      const caret = el('uf-codex-toggle-config-caret');
      if (!body || !btn) return;
      const open = body.style.display === 'none';
      body.style.display = open ? '' : 'none';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (caret) caret.style.transform = open ? 'rotate(180deg)' : '';
    });

    el('uf-codex-save')?.addEventListener('click', async () => {
      const msg = el('uf-codex-msg');
      const tokenId = formEl.dataset.createdTokenId;
      if (!tokenId) { formEl.style.display = 'none'; return; }
      const scopes = ['chat'].concat(
        Array.from(formEl.querySelectorAll('#uf-codex-inline-scopes .uf-codex-scope:checked'))
          .map(input => input.dataset.scope)
      );
      try {
        const r = await fetch(`/api/tokens/${tokenId}`, {
          method: 'PATCH', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scopes }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'Failed');
        if (msg) { msg.textContent = 'Saved'; msg.style.color = 'var(--green, #50fa7b)'; }
        await renderList();
        setTimeout(() => { formEl.style.display = 'none'; }, 350);
      } catch (err) {
        if (msg) { msg.textContent = err?.message || 'Save failed'; msg.style.color = 'var(--red)'; }
      }
    });

    // Revoke = delete this agent token entirely. Confirmation prompt keeps
    // it from being a one-click footgun. Closes the form on success.
    el('uf-codex-revoke')?.addEventListener('click', async () => {
      const tokenId = formEl.dataset.createdTokenId;
      if (!tokenId) return;
      const ok = window.styledConfirm
        ? await window.styledConfirm(`Revoke this ${cfg.word} agent token? Integrations using it will lose access.`, { confirmText: 'Revoke', danger: true })
        : confirm(`Revoke this ${cfg.word} agent token? Integrations using it will lose access.`);
      if (!ok) return;
      const msg = el('uf-codex-msg');
      try {
        const r = await fetch(`/api/tokens/${tokenId}`, { method: 'DELETE', credentials: 'same-origin' });
        if (!r.ok) throw new Error('Revoke failed');
        if (msg) { msg.textContent = 'Revoked'; msg.style.color = 'var(--color-error)'; }
        await renderList();
        setTimeout(() => { formEl.style.display = 'none'; }, 350);
      } catch (err) {
        if (msg) { msg.textContent = err?.message || 'Revoke failed'; msg.style.color = 'var(--red)'; }
      }
    });

    const _autoCreateCodex = async () => {
      const msg = el('uf-codex-msg');
      const prompt = el('uf-codex-prompt');
      const pending = el('uf-codex-pending');
      const createBtn = el('uf-codex-create-btn');
      if (prompt) prompt.style.display = 'none';
      if (createBtn) createBtn.style.display = 'none';
      // Whirlpool spinner while the POST is in flight.
      let _wp = null;
      if (pending) {
        pending.innerHTML = '';
        pending.style.display = 'flex';
        try {
          const sp = window.spinnerModule || (await import('../../spinner.js')).default;
          _wp = sp.createWhirlpool(14);
          _wp.element.style.cssText = 'display:inline-flex;width:14px;height:14px;margin:0 4px 0 0;';
          pending.appendChild(_wp.element);
          pending.appendChild(document.createTextNode('Creating token…'));
        } catch (_) {
          pending.textContent = 'Creating token…';
        }
      }
      const existingNames = new Set(agentTokens.map(t => (t.name || '').trim()));
      const nameInput = el('uf-codex-name-input');
      // User-typed name wins. Empty / whitespace falls back to the default,
      // auto-suffixed with " 2", " 3"… so two tokens never collide.
      let name = (nameInput && nameInput.value || '').trim() || cfg.defaultName;
      if (existingNames.has(name)) {
        let n = 2;
        const base = name;
        while (existingNames.has(name)) { name = `${base} ${n++}`; }
      }
      // Minimum scope on creation so the token isn't effectively saved
      // with everything granted before the user has clicked Save. The
      // UI toggles below are pre-checked as a preview of what *will*
      // be granted; nothing else is persisted server-side until Save.
      const fd = new FormData();
      fd.append('name', name);
      fd.append('scopes', 'chat');
      try {
        const r = await fetch('/api/tokens', { method: 'POST', credentials: 'same-origin', body: fd });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || 'Failed');
        if (_wp) { try { _wp.destroy(); } catch (_) {} }
        if (pending) pending.style.display = 'none';
        el('uf-codex-token').textContent = d.token || '';
        el('uf-codex-reveal').style.display = '';
        const setupBtn = el('uf-codex-copy-setup');
        if (setupBtn) setupBtn.dataset.token = d.token || '';
        const setupCode = el('uf-codex-setup-code');
        if (setupCode) setupCode.textContent = setupForToken(d.token || '');
        // Populate inline scope toggles for the just-created token with
        // ALL scopes pre-checked as a UI preview — the underlying token
        // still only has 'chat' until the user clicks Save below.
        const uiToken = { id: d.id, scopes: ['chat'].concat(toolScopes.map(s => s.key)) };
        const inlineEl = el('uf-codex-inline-scopes');
        if (inlineEl) {
          inlineEl.innerHTML = `
            <div class="uf-codex-token" data-token-id="${esc(uiToken.id)}">
              ${scopeToggles(uiToken)}
              <div class="uf-codex-scope-msg" data-token-id="${esc(uiToken.id)}" style="font-size:11px;min-height:14px;"></div>
            </div>`;
          // No auto-PATCH: scope toggles only persist on Save click below.
        }
        // Now that the token exists, surface the Save button.
        const saveBtn = el('uf-codex-save');
        if (saveBtn) saveBtn.style.display = 'inline-flex';
        // Remember the created token id so Save can PATCH its scopes.
        formEl.dataset.createdTokenId = String(uiToken.id);
        if (msg) {
          msg.textContent = `Created "${name}".`;
          msg.style.color = 'var(--green, #50fa7b)';
        }
        await renderList();
      } catch (err) {
        if (_wp) { try { _wp.destroy(); } catch (_) {} }
        if (pending) pending.style.display = 'none';
        if (msg) {
          msg.textContent = err?.message || 'Failed';
          msg.style.color = 'var(--red)';
        }
      }
    };
    // Bind the explicit Create button; no auto-creation.
    el('uf-codex-create-btn')?.addEventListener('click', () => { _autoCreateCodex(); });
    const _copyCodexToken = async (text) => {
      const value = String(text || '');
      if (!value) return false;
      if (navigator.clipboard && window.isSecureContext) {
        try {
          await navigator.clipboard.writeText(value);
          return true;
        } catch (_) {}
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
    const _selectTextFallback = (text, containerId) => {
      const code = document.createElement('pre');
      code.textContent = text;
      code.style.cssText = 'white-space:pre-wrap;word-break:break-word;font-size:10px;margin:6px 0 0;';
      el(containerId)?.appendChild(code);
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    };
    const COPY_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const CHECK_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    el('uf-codex-copy-setup')?.addEventListener('click', async () => {
      const token = el('uf-codex-copy-setup')?.dataset.token || '';
      const btn = el('uf-codex-copy-setup');
      if (!token) return;
      const setup = setupForToken(token);
      const ok = await _copyCodexToken(setup);
      if (!btn) return;
      if (ok) {
        btn.innerHTML = CHECK_ICON;
        btn.style.color = 'var(--accent, var(--red))';
        btn.style.opacity = '1';
      } else {
        _selectTextFallback(setup, 'uf-codex-reveal');
      }
      setTimeout(() => {
        const latest = el('uf-codex-copy-setup');
        if (latest) { latest.innerHTML = COPY_ICON; latest.style.color = ''; latest.style.opacity = '0.7'; }
      }, 1600);
    });
    el('uf-codex-copy-token')?.addEventListener('click', async () => {
      const token = el('uf-codex-token')?.textContent || '';
      const ok = await _copyCodexToken(token);
      const btn = el('uf-codex-copy-token');
      if (!btn) return;
      if (ok) {
        btn.innerHTML = CHECK_ICON;
        btn.style.color = 'var(--accent, var(--red))';
        btn.style.opacity = '1';
      } else {
        _selectTextFallback(token, 'uf-codex-reveal');
      }
      setTimeout(() => {
        const latest = el('uf-codex-copy-token');
        if (latest) { latest.innerHTML = COPY_ICON; latest.style.color = ''; latest.style.opacity = '0.7'; }
      }, 1600);
    });
    function _wireScopeChange(scope) {
      scope.querySelectorAll('.uf-codex-scope').forEach(cb => {
        if (cb.dataset.wired === '1') return;
        cb.dataset.wired = '1';
        cb.addEventListener('change', async () => {
          const tokenId = cb.dataset.tokenId;
          const panel = formEl.querySelector(`.uf-codex-token[data-token-id="${CSS.escape(tokenId)}"]`);
          const msg = formEl.querySelector(`.uf-codex-scope-msg[data-token-id="${CSS.escape(tokenId)}"]`);
          const scopes = Array.from(panel.querySelectorAll('.uf-codex-scope:checked')).map(input => input.dataset.scope);
          try {
            const r = await fetch(`/api/tokens/${tokenId}`, {
              method: 'PATCH',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ scopes }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.detail || 'Failed');
            if (msg) { msg.textContent = 'Saved'; msg.style.color = 'var(--green, #50fa7b)'; }
            await renderList();
          } catch (err) {
            cb.checked = !cb.checked;
            if (msg) { msg.textContent = err?.message || 'Failed'; msg.style.color = 'var(--red)'; }
          }
        });
      });
    }
    // Note: don't call _wireScopeChange(formEl) here. The existing-token
    // editor (current) already wires its own change handler that PATCHes
    // immediately. The inline scopes for a *just-created* token should
    // remain unwired so they only persist on Save click below.
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
    const _rowsHtml = _typeOptions.map(([k, label]) => `<button type="button" class="uf-type-option" data-value="${k}" style="display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;background:transparent;border:0;color:var(--fg);font:inherit;cursor:pointer;text-align:left;"><span style="display:inline-flex;color:var(--accent, var(--red));flex-shrink:0;">${_iconFor(k)}</span><span>${esc(label)}</span></button>`).join('');

    // Anchor wrapper so the absolutely-positioned menu lands directly under
    // the add button. The button is the wrapper's only sibling.
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
      menu.innerHTML = _rowsHtml;
      menu.style.cssText = 'position:absolute;right:0;z-index:1000;background:var(--panel);border:1px solid var(--border);border-radius:6px;max-height:340px;overflow-y:auto;box-shadow:0 6px 18px rgba(0,0,0,0.25);min-width:220px;';
      addBtn.parentElement.appendChild(menu);
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
      menu.querySelectorAll('.uf-type-option').forEach(btn => {
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
      _closeMenu = bindMenuDismiss(menu, () => { menu.remove(); _menuEl = null; }, (ev) => !menu.contains(ev.target) && ev.target !== addBtn);
    });
  }

  await renderList();
}

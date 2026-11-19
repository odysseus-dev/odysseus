// Coding-agent (Codex / Claude) integration form and the connection recipes it
// shows. Extracted from the unified integrations host.

import { esc } from '../../html.js';
import { byId as el } from '../../dom.js';
import { notifyIntegrationsChanged } from '../events.js';

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

export async function showAgentForm({ formEl, renderList, closeForm }, kind, editId) {
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
          <input type="text" id="uf-codex-existing-rename" data-token-id="${esc(current.id)}" value="${esc(current.name || cfg.defaultName)}" style="font-size:12px;font-weight:600;padding:3px 6px;flex:1;background:transparent;border:1px solid transparent;border-radius:4px;" title="Click to rename" aria-label="Rename agent token">
          <span style="font-size:10px;opacity:0.55;">${esc(current.token_prefix || 'token')}...${current.last_used_at ? ` · Last used ${new Date(current.last_used_at).toLocaleDateString()}` : ' · Never used'}</span>
        </div>
        <div style="font-size:11px;font-weight:600;opacity:0.62;margin-bottom:4px;">Permissions</div>
        ${scopeToggles(current)}
        <div id="uf-codex-existing-msg" role="status" aria-live="polite" style="font-size:11px;min-height:14px;margin-top:4px;"></div>
      </div>` : '';

  formEl.innerHTML = `
      <div class="admin-card" style="margin-top:8px">
        <div class="settings-col">
          ${editExistingHtml}
          <div id="uf-codex-prompt" style="display:${current ? 'none' : 'block'};padding:6px 0;">
            <div style="font-size:11px;opacity:0.7;margin-bottom:6px;">Name this ${esc(cfg.word)} agent so you can tell it apart from other ones (e.g. "${esc(cfg.defaultName)} — laptop").</div>
            <input type="text" id="uf-codex-name-input" class="settings-select" placeholder="${esc(cfg.defaultName)}" aria-label="Agent name" style="width:100%;font-size:12px;padding:6px 8px;">
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
            <span id="uf-codex-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;text-align:center;"></span>
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

  el('uf-codex-cancel')?.addEventListener('click', () => {
    if (typeof closeForm === 'function') closeForm();
    else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
  });

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
        const sp = window.spinnerModule || (await import('../../../spinner.js')).default;
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

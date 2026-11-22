// Personal API tokens (Account panel): scope picker, creation and revocation.
// Extracted verbatim from settings.js.

import { byId as el } from './dom.js';
import { esc } from './html.js';

/* ═══════════════════════════════════════════
   INIT & REFRESH
   ═══════════════════════════════════════════ */
// ── Personal API Tokens (Account tab) ──
const SELF_TOKEN_SCOPES = [
  { key: 'chat', label: 'Chat', detail: 'Chat and companion access' },
  { key: 'todos:read', label: 'Todos', detail: 'Read notes and checklists' },
  { key: 'todos:write', label: 'Todos write', detail: 'Create, update, delete, and toggle todo items' },
  { key: 'documents:read', label: 'Documents', detail: 'Read documents' },
  { key: 'documents:write', label: 'Documents write', detail: 'Create and update draft documents' },
  { key: 'email:read', label: 'Email', detail: 'Read email' },
  { key: 'email:draft', label: 'Email drafts', detail: 'Create email reply drafts' },
  { key: 'email:send', label: 'Email send', detail: 'Send email directly' },
  { key: 'calendar:read', label: 'Calendar', detail: 'Read calendar events' },
  { key: 'calendar:write', label: 'Calendar write', detail: 'Create and update calendar events' },
  { key: 'memory:read', label: 'Memory', detail: 'Read memory' },
  { key: 'memory:write', label: 'Memory write', detail: 'Write memory' },
  { key: 'cookbook:read', label: 'Cookbook', detail: 'Read model inventory and presets', adminOnly: true },
  { key: 'cookbook:launch', label: 'Cookbook launch', detail: 'Start and stop model servers', adminOnly: true },
];

export function initSelfApiTokens(isAdmin) {
  const listEl = el('settings-api-tokens-list');
  const createPanel = el('settings-api-tokens-create');
  const newBtn = el('settings-api-token-new-btn');
  const createBtn = el('settings-api-token-create-btn');
  const nameInput = el('settings-api-token-name');
  const scopesEl = el('settings-api-token-scopes');
  const msgEl = el('settings-api-token-create-msg');
  const revealEl = el('settings-api-token-reveal');
  const tokenValueEl = el('settings-api-token-value');
  const copyBtn = el('settings-api-token-copy-btn');

  if (!listEl || !newBtn) return;

  // Build scope checkboxes once
  scopesEl.innerHTML = SELF_TOKEN_SCOPES.filter(s => !s.adminOnly || isAdmin).map(s => `
    <label style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:12px;cursor:pointer;">
      <input type="checkbox" class="self-token-scope" value="${esc(s.key)}" title="${esc(s.detail)}">
      <span>${esc(s.label)}</span>
    </label>
  `).join('');

  async function loadTokens() {
    try {
      const res = await fetch('/api/tokens/self', { credentials: 'same-origin' });
      const tokens = await res.json();
      if (!tokens.length) {
        listEl.innerHTML = '<div style="font-size:11px;opacity:0.4;padding:4px 0;">No tokens yet</div>';
        return;
      }
      listEl.innerHTML = tokens.map(t => {
        const scopes = (t.scopes || []).join(', ') || 'chat';
        return `<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;margin-bottom:4px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb, var(--fg) 3%, transparent);">
          <div style="flex:1;min-width:0;">
            <div style="font-size:12px;font-weight:600;">${esc(t.name)}</div>
            <div style="font-size:10px;opacity:0.5;display:flex;gap:8px;flex-wrap:wrap;">
              <span>${esc(t.token_prefix)}...</span>
              <span>${esc(scopes)}</span>
              ${t.last_used_at ? `<span>Last used ${new Date(t.last_used_at).toLocaleDateString()}</span>` : '<span>Never used</span>'}
            </div>
          </div>
          <button class="admin-btn-delete self-token-revoke" data-token-id="${esc(t.id)}" title="Revoke token" style="flex-shrink:0;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          </button>
        </div>`;
      }).join('');

      // Wire revoke buttons
      listEl.querySelectorAll('.self-token-revoke').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const tokenId = btn.dataset.tokenId;
          if (!await window.styledConfirm('Revoke this token? Any scripts or apps using it will stop working.', { confirmText: 'Revoke', danger: true })) return;
          try {
            const r = await fetch(`/api/tokens/self/${tokenId}`, { method: 'DELETE', credentials: 'same-origin' });
            if (r.ok) loadTokens();
          } catch (_) {}
        });
      });
    } catch (_) {
      listEl.innerHTML = '<div style="font-size:11px;opacity:0.4;">Could not load tokens</div>';
    }
  }

  loadTokens();

  // New token button
  newBtn.addEventListener('click', () => {
    createPanel.style.display = '';
    newBtn.style.display = 'none';
    nameInput.value = '';
    nameInput.focus();
    revealEl.style.display = 'none';
    msgEl.textContent = '';
    scopesEl.querySelectorAll('.self-token-scope').forEach(cb => { cb.checked = false; });
  });

  // Create token
  createBtn.addEventListener('click', async () => {
    const name = nameInput.value.trim();
    if (!name) { msgEl.textContent = 'Token name is required'; msgEl.style.color = 'var(--red)'; return; }
    const checked = Array.from(scopesEl.querySelectorAll('.self-token-scope:checked')).map(cb => cb.value);
    const fd = new FormData();
    fd.append('name', name);
    if (checked.length) fd.append('scopes', checked.join(','));
    msgEl.textContent = '';
    msgEl.style.color = '';
    createBtn.disabled = true;
    try {
      const res = await fetch('/api/tokens/self', { method: 'POST', body: fd, credentials: 'same-origin' });
      const data = await res.json();
      if (res.ok) {
        tokenValueEl.textContent = data.token;
        revealEl.style.display = '';
        nameInput.value = '';
        scopesEl.querySelectorAll('.self-token-scope').forEach(cb => { cb.checked = false; });
        loadTokens();
      } else {
        msgEl.textContent = data.detail || 'Failed';
        msgEl.style.color = 'var(--red)';
      }
    } catch (_) {
      msgEl.textContent = 'Request failed';
      msgEl.style.color = 'var(--red)';
    } finally {
      createBtn.disabled = false;
    }
  });

  // Copy button
  if (copyBtn) {
    const COPY_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const CHECK_ICON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    copyBtn.addEventListener('click', () => {
      const val = tokenValueEl.textContent;
      navigator.clipboard.writeText(val).then(() => {
        copyBtn.innerHTML = CHECK_ICON;
        copyBtn.style.color = 'var(--accent, var(--red))';
        copyBtn.style.opacity = '1';
        setTimeout(() => {
          copyBtn.innerHTML = COPY_ICON;
          copyBtn.style.color = '';
          copyBtn.style.opacity = '';
        }, 1600);
      });
    });
  }

  // Allow Enter key in name input to create
  nameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      createBtn.click();
    }
  });
}

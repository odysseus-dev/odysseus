// static/js/admin.js — Admin panel module (ES6)
// Admin-only: users, endpoints, MCP, RAG, embeddings, tokens, webhooks, features

import uiModule from './ui.js';
import settingsModule from './settings.js';
import { providerLogo } from './providers.js';
import { sortModelObjects } from './modelSort.js';
import { PROVIDER_DEVICE_FLOWS, formatDeviceFlowError, runProviderDeviceFlow } from './providerDeviceFlow.js';
import { t } from './i18n.js';

let initialized = false;
let modalEl = null;
// When the user adds an endpoint, store its id so the next render of
// the endpoints list can flash a glow on that row. Cleared once the
// animation fires.
let _recentlyAddedEpId = null;

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

/* ═══════════════════════════════════════════
   USERS TAB
   ═══════════════════════════════════════════ */
// Resolved lazily (function, not a frozen object): admin.js is imported at
// page load, before initI18n() has fetched the locale JSON, so evaluating
// t() at module top-level would freeze the English fallback even for pt/es
// users. Calling it at render time picks up the active locale.
const PRIV_LABELS = () => ({
  can_use_agent: t('ui.js.admin_priv_agent', null, 'Agent mode'),
  can_use_browser: t('ui.js.admin_priv_browser', null, 'Browser automation'),
  can_use_bash: t('ui.js.admin_priv_bash', null, 'Shell / Python / Files'),
  can_use_documents: t('ui.js.admin_priv_documents', null, 'Document editor'),
  can_use_research: t('ui.js.admin_priv_research', null, 'Deep research'),
  can_generate_images: t('ui.js.admin_priv_images', null, 'Image generation'),
  can_manage_memory: t('ui.js.admin_priv_memory', null, 'Memory & skills'),
});

async function loadUsers() {
  const list = el('adm-userList');
  try {
    const res = await fetch('/api/auth/users', { credentials: 'same-origin' });
    if (res.status === 401 || res.status === 403) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_access_denied', null, 'Access denied') + '</div>'; return; }
    const data = await res.json();
    if (!data.users || data.users.length === 0) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_users', null, 'No users found') + '</div>'; return; }
    list.innerHTML = '';
    data.users.forEach(u => {
      const row = document.createElement('div');
      row.className = 'admin-user-row';

      // Header: name + badges + delete
      const header = document.createElement('div');
      header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:4px 0;';
      const initial = u.username.charAt(0).toUpperCase();
      header.innerHTML = `
        <div class="admin-user-info">
          <div style="width:28px;height:28px;border-radius:50%;background:color-mix(in srgb, var(--accent) 20%, var(--panel));display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;flex-shrink:0;color:var(--accent);">${esc(initial)}</div>
          <div>
            <span class="admin-user-name">${esc(u.username)}</span>
            ${u.is_admin ? '<span class="admin-badge" style="margin-left:6px;">' + t('ui.js.admin_badge_admin', null, 'ADMIN') + '</span>' : '<span style="font-size:10px;opacity:0.4;display:block;">' + t('ui.js.admin_click_manage_privileges', null, 'Click to manage privileges') + '</span>'}
          </div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="admin-btn-sm" data-adm-rename-user="${esc(u.username)}" style="font-size:11px;">${t('ui.js.admin_rename', null, 'Rename')}</button>
          ${u.is_admin ? '' : `<button class="admin-btn-delete" data-adm-del-user="${esc(u.username)}" style="font-size:11px;">${t('ui.js.admin_remove', null, 'Remove')}</button>`}
          ${u.is_admin ? '' : '<svg class="admin-user-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3;transition:transform 0.2s,opacity 0.2s;"><polyline points="6 9 12 15 18 9"/></svg>'}
        </div>
      `;
      row.appendChild(header);

      // Privileges panel (hidden by default, not for admins)
      if (!u.is_admin) {
        const privPanel = document.createElement('div');
        privPanel.className = 'admin-priv-panel hidden';
        privPanel.style.cssText = 'padding:8px 0 4px;border-top:1px solid var(--border);margin-top:8px;';

        // Boolean toggles
        let html = '<div style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;opacity:0.35;font-weight:600;margin-bottom:4px;">' + t('ui.js.admin_features_header', null, 'Features') + '</div>';
        for (const [key, label] of Object.entries(PRIV_LABELS())) {
          const checked = u.privileges && u.privileges[key] ? 'checked' : '';
          html += `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;">
            <span style="font-size:12px;">${label}</span>
            <label class="admin-switch" style="transform:scale(0.85);"><input type="checkbox" data-priv="${key}" data-user="${esc(u.username)}" ${checked}><span class="admin-slider"></span></label>
          </div>`;
        }
        // Rate limit
        html += '<div style="font-size:10px;text-transform:uppercase;letter-spacing:0.5px;opacity:0.35;font-weight:600;margin:10px 0 4px;">' + t('ui.js.admin_limits_header', null, 'Limits') + '</div>';
        const maxMsg = (u.privileges && u.privileges.max_messages_per_day) || 0;
        html += `<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;">
          <div>
            <span style="font-size:12px;">${t('ui.js.admin_daily_limit', null, 'Daily message limit')}</span>
            <div style="font-size:10px;opacity:0.4;">${t('ui.js.admin_zero_no_limit', null, '0 = no limit')}</div>
          </div>
          <input type="number" min="0" value="${maxMsg}" data-priv="max_messages_per_day" data-user="${esc(u.username)}" style="width:70px;padding:4px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);font-size:12px;text-align:center;">
        </div>`;
        // Allowed models — checkbox list
        const allowedModels = Array.isArray(u.privileges && u.privileges.allowed_models)
          ? u.privileges.allowed_models
          : [];
        const allowedSet = new Set(allowedModels);
        const modelsRestricted = !!(u.privileges && u.privileges.allowed_models_restricted);
        const blockAllModels = !!(u.privileges && u.privileges.block_all_models);
        html += `<div style="padding:4px 0;">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <span style="font-size:12px;">${t('ui.js.admin_allowed_models', null, 'Allowed models')}</span>
            <div style="display:flex;gap:8px;">
              <a href="#" class="priv-models-all" data-user="${esc(u.username)}" style="font-size:10px;opacity:0.5;">${t('ui.js.admin_all', null, 'All')}</a>
              <a href="#" class="priv-models-none" data-user="${esc(u.username)}" style="font-size:10px;opacity:0.5;">${t('ui.js.admin_none', null, 'None')}</a>
            </div>
          </div>
          <div style="font-size:10px;opacity:0.4;margin-bottom:4px;">${blockAllModels ? t('ui.js.admin_no_models_allowed', null, 'No models allowed') : (!modelsRestricted ? t('ui.js.admin_all_models_allowed', null, 'All models allowed (no restrictions)') : (allowedSet.size === 0 ? t('ui.js.admin_no_models_allowed', null, 'No models allowed') : t('ui.js.admin_n_models_allowed', { count: allowedSet.size }, '{count} model(s) allowed')))}</div>
          <div class="priv-models-list" data-user="${esc(u.username)}">
            <span style="opacity:0.4;font-size:11px;">${t('ui.js.admin_loading_models', null, 'Loading models...')}</span>
          </div>
        </div>`;
        privPanel.innerHTML = html;
        row.appendChild(privPanel);

        // Toggle panel visibility + rotate chevron + load models
        let _modelsLoaded = false;
        header.addEventListener('click', (e) => {
          if (e.target.closest('.admin-btn-delete, [data-adm-rename-user]')) return;
          privPanel.classList.toggle('hidden');
          const chevron = header.querySelector('.admin-user-chevron');
          if (chevron) {
            const isOpen = !privPanel.classList.contains('hidden');
            chevron.style.transform = isOpen ? 'rotate(180deg)' : '';
            chevron.style.opacity = isOpen ? '0.7' : '0.3';
          }
          // Load models list on first expand
          if (!_modelsLoaded && !privPanel.classList.contains('hidden')) {
            _modelsLoaded = true;
            _loadModelsForUser(u.username, allowedSet, modelsRestricted, blockAllModels, privPanel);
          }
        });

        // Wire privilege changes (boolean + number inputs, not model checkboxes)
        privPanel.querySelectorAll('[data-priv]').forEach(input => {
          const handler = async () => {
            const username = input.dataset.user;
            const key = input.dataset.priv;
            let value;
            if (input.type === 'checkbox') value = input.checked;
            else if (input.type === 'number') value = parseInt(input.value) || 0;
            else value = input.value;
            try {
              await fetch(`/api/auth/users/${encodeURIComponent(username)}/privileges`, {
                method: 'PUT', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [key]: value }),
              });
            } catch (e) { uiModule.showError(t('ui.js.admin_priv_update_failed', null, 'Failed to update privilege')); }
          };
          if (input.type === 'checkbox') input.addEventListener('change', handler);
          else input.addEventListener('change', handler);
        });
      }

      // Rename button
      const renameBtn = row.querySelector('[data-adm-rename-user]');
      if (renameBtn) {
        renameBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const oldUsername = renameBtn.dataset.admRenameUser;
          const next = await uiModule.styledPrompt(t('ui.js.admin_rename_user_title', { name: oldUsername }, 'Rename "{name}"'), {
            defaultValue: oldUsername,
            placeholder: t('ui.js.admin_new_username', null, 'New username'),
            confirmText: t('ui.js.admin_rename', null, 'Rename'),
          });
          const username = (next || '').trim();
          if (!username || username === oldUsername) return;
          try {
            const res = await fetch(`/api/auth/users/${encodeURIComponent(oldUsername)}/rename`, {
              method: 'PUT',
              credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ username }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
              uiModule.showError(data.detail || t('ui.js.admin_rename_failed', null, 'Failed to rename user'));
              return;
            }
            if (data.renamed_self) {
              window.location.reload();
              return;
            }
            loadUsers();
          } catch (err) {
            uiModule.showError(t('ui.js.admin_rename_failed', null, 'Failed to rename user'));
          }
        });
      }

      // Delete button
      const delBtn = row.querySelector('[data-adm-del-user]');
      if (delBtn) {
        delBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const username = delBtn.dataset.admDelUser;
          if (!await uiModule.styledConfirm(t('ui.js.admin_remove_user_confirm', { name: username }, 'Remove user "{name}"?'), { confirmText: t('ui.js.admin_remove', null, 'Remove'), danger: true })) return;
          const res = await fetch('/api/auth/users', { method: 'DELETE', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username }) });
          if (res.ok) loadUsers();
          else uiModule.showError(t('ui.js.admin_delete_user_failed', null, 'Failed to delete user'));
        });
      }

      list.appendChild(row);
    });
  } catch (e) { list.innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_users_failed', null, 'Failed to load users') + '</div>'; }
}

async function _loadModelsForUser(username, allowedSet, modelsRestricted, blockAllModels, privPanel) {
  const listEl = privPanel.querySelector(`.priv-models-list[data-user="${username}"]`);
  if (!listEl) return;
  try {
    // Use /api/model-endpoints rather than /api/models — the latter is
    // backed by `cached_models`, so endpoints that haven't been probed yet
    // (e.g. a freshly-added cloud API like DeepSeek) simply don't show up
    // until some other endpoint happens to trigger a cache refresh. The
    // endpoints listing always reflects every configured endpoint.
    const res = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    const data = await res.json();
    const allModels = [];
    (Array.isArray(data) ? data : []).forEach(ep => {
      if (!ep.online) return;
      (ep.models || []).forEach(mid => {
        allModels.push({ mid, epName: ep.name || '', display: mid.split('/').pop() });
      });
    });
    if (!allModels.length) {
      listEl.innerHTML = '<span style="opacity:0.4;font-size:11px;">' + t('ui.js.admin_no_models_available', null, 'No models available') + '</span>';
      return;
    }
    let restricted = modelsRestricted;
    let blockAll = blockAllModels;
    listEl.innerHTML = sortModelObjects(allModels).map(m => {
      const checked = !blockAll && (!restricted || allowedSet.has(m.mid)) ? 'checked' : '';
      return `<label>
        <input type="checkbox" class="priv-model-cb" data-mid="${esc(m.mid)}" ${checked}>
        <span>${esc(m.display)}</span>
        <span style="opacity:0.3;font-size:10px;margin-left:auto;">${esc(m.epName)}</span>
      </label>`;
    }).join('');

    // Save on change
    function _saveModels() {
      const checked = [];
      listEl.querySelectorAll('.priv-model-cb').forEach(cb => {
        if (cb.checked) checked.push(cb.dataset.mid);
      });
      // Three distinct states the backend must be able to tell apart:
      //  - all checked   -> no restriction (allowed_models: [], block_all_models: false)
      //  - none checked  -> block everything (allowed_models: [], block_all_models: true)
      //  - some checked  -> allowlist (allowed_models: checked, block_all_models: false)
      let value, hintText;
      if (checked.length === allModels.length) {
        restricted = false;
        blockAll = false;
        value = [];
        hintText = t('ui.js.admin_all_models_allowed', null, 'All models allowed (no restrictions)');
      } else if (checked.length === 0) {
        restricted = true;
        blockAll = true;
        value = [];
        hintText = t('ui.js.admin_no_models_allowed', null, 'No models allowed');
      } else {
        restricted = true;
        blockAll = false;
        value = checked;
        hintText = t('ui.js.admin_n_models_allowed', { count: value.length }, '{count} model(s) allowed');
      }
      const hint = privPanel.querySelector('.priv-models-list[data-user]')?.previousElementSibling?.querySelector('div[style*="opacity"]');
      if (hint) hint.textContent = hintText;
      fetch(`/api/auth/users/${encodeURIComponent(username)}/privileges`, {
        method: 'PUT', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allowed_models: value, allowed_models_restricted: restricted, block_all_models: blockAll }),
      }).catch(() => {});
    }
    listEl.querySelectorAll('.priv-model-cb').forEach(cb => cb.addEventListener('change', _saveModels));

    // All / None buttons
    privPanel.querySelector(`.priv-models-all[data-user="${username}"]`)?.addEventListener('click', (e) => {
      e.preventDefault();
      listEl.querySelectorAll('.priv-model-cb').forEach(cb => cb.checked = true);
      _saveModels();
    });
    privPanel.querySelector(`.priv-models-none[data-user="${username}"]`)?.addEventListener('click', (e) => {
      e.preventDefault();
      listEl.querySelectorAll('.priv-model-cb').forEach(cb => cb.checked = false);
      _saveModels();
    });
  } catch (e) {
    listEl.innerHTML = '<span style="opacity:0.4;font-size:11px;">' + t('ui.js.admin_load_models_failed', null, 'Failed to load models') + '</span>';
  }
}

function initSignupToggle() {
  const toggle = el('adm-signupToggle');
  fetch('/api/auth/status', { credentials: 'same-origin' })
    .then(r => r.json())
    .then(d => { toggle.checked = !!d.signup_enabled; })
    .catch(e => console.warn('Auth status fetch failed:', e));
  toggle.addEventListener('change', async () => {
    try {
      const res = await fetch('/api/auth/signup-toggle', { method: 'POST', credentials: 'same-origin' });
      const data = await res.json();
      toggle.checked = data.signup_enabled;
    } catch (e) { toggle.checked = !toggle.checked; }
  });
}

function initAddUser() {
  el('adm-addBtn').addEventListener('click', async () => {
    const msg = el('adm-addMsg');
    msg.textContent = ''; msg.className = '';
    const username = el('adm-newUsername').value.trim();
    const password = el('adm-newPassword').value;
    const is_admin = el('adm-newIsAdmin').checked;
    if (!username) { msg.textContent = t('ui.js.admin_username_required', null, 'Username required'); msg.className = 'admin-error'; return; }
    if (password.length < 8) { msg.textContent = t('ui.js.admin_password_min', null, 'Password must be at least 8 characters'); msg.className = 'admin-error'; return; }
    el('adm-addBtn').disabled = true;
    try {
      const res = await fetch('/api/auth/users', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password, is_admin }) });
      const data = await res.json();
      if (res.ok) { msg.textContent = t('ui.js.admin_user_created', null, 'User created'); msg.className = 'admin-success'; el('adm-newUsername').value = ''; el('adm-newPassword').value = ''; el('adm-newIsAdmin').checked = false; loadUsers(); }
      else { msg.textContent = data.detail || t('ui.js.admin_failed', null, 'Failed'); msg.className = 'admin-error'; }
    } catch (e) { msg.textContent = t('ui.js.admin_request_failed', null, 'Request failed'); msg.className = 'admin-error'; }
    el('adm-addBtn').disabled = false;
  });
}

/* ═══════════════════════════════════════════
   SERVICES TAB — Endpoints
   ═══════════════════════════════════════════ */
function _isLocalEndpoint(url) {
  if (!url) return false;
  try {
    const u = new URL(url);
    const h = u.hostname.toLowerCase();
    if (h === 'localhost' || h === '127.0.0.1' || h === '0.0.0.0') return true;
    if (h.endsWith('.local')) return true;
    if (/^10\./.test(h)) return true;
    if (/^192\.168\./.test(h)) return true;
    if (/^172\.(1[6-9]|2[0-9]|3[01])\./.test(h)) return true;
    // Tailscale CGNAT range (100.64.0.0/10 → 100.64.x–100.127.x). Servers
    // found via "Scan for Servers" come back as tailnet IPs, which are still
    // your own machines, so group them under Local rather than API.
    if (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(h)) return true;
    // Single-label hostnames are LAN by convention.
    if (!h.includes('.')) return true;
    return false;
  } catch { return false; }
}

async function _refreshAfterEndpointChange(deletedEndpointId) {
  try {
    const sm = window.sessionModule;
    const pending = sm && sm.getPendingChat ? sm.getPendingChat() : null;
    if (deletedEndpointId && pending && String(pending.endpointId || '') === String(deletedEndpointId)) {
      if (sm.setPendingChat) sm.setPendingChat(null);
    }
  } catch (_) {}
  try {
    if (window.modelsModule && window.modelsModule.refreshModels) {
      await window.modelsModule.refreshModels(true);
    }
  } catch (_) {}
  try {
    window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', {
      detail: { deletedEndpointId: deletedEndpointId || null }
    }));
  } catch (_) {}
  try {
    if (window.sessionModule && window.sessionModule.updateModelPicker) {
      window.sessionModule.updateModelPicker();
    }
  } catch (_) {}
}

async function _selectAddedModelInChat(endpoint) {
  const modelId = endpoint && Array.isArray(endpoint.models) ? endpoint.models[0] : '';
  if (!modelId) return;
  try {
    if (window.modelsModule && window.modelsModule.refreshModels) {
      await window.modelsModule.refreshModels(true);
    }
  } catch (_) {}
  try {
    document.dispatchEvent(new CustomEvent('odysseus:auto-select-model', {
      detail: {
        endpointId: endpoint.id || '',
        endpointName: endpoint.name || '',
        modelId,
        url: endpoint.base_url || '',
      }
    }));
  } catch (_) {}
}

async function loadEndpoints() {
  const listLocal = el('adm-epList-local');
  const listApi = el('adm-epList-api');
  // Fallback to the legacy single list if the split containers don't exist
  // (older HTML or third-party embedding).
  const listLegacy = el('adm-epList');
  // Refresh model picker so new endpoints show up in chat
  if (window.modelsModule && window.modelsModule.refreshModels) {
    window.modelsModule.refreshModels();
    setTimeout(() => {
      if (window.sessionModule && window.sessionModule.updateModelPicker) {
        window.sessionModule.updateModelPicker();
      }
    }, 1500);
  }
  if (settingsModule && typeof settingsModule.refreshAiModelEndpoints === 'function') {
    settingsModule.refreshAiModelEndpoints();
  }
  try {
    const res = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    // Treat a non-OK response (e.g. 401/403 for non-admins, or backend
    // returning an error envelope) the same as "no endpoints yet": show the
    // empty state, not "Failed to load". The user just installed the app —
    // there's literally nothing to load, so the error read as broken UI.
    let data = [];
    if (res.ok) {
      try { data = await res.json(); } catch { data = []; }
    }
    if (!Array.isArray(data) || data.length === 0) {
      const empty = '<div class="admin-empty">' + t('ui.js.admin_none_empty', null, 'None') + '</div>';
      if (listLocal) listLocal.innerHTML = empty;
      if (listApi) listApi.innerHTML = empty;
      if (listLegacy) listLegacy.innerHTML = empty;
      return;
    }
    const rowHtml = data.map(ep => {
      const visibleCount = ep.models.length;
      const totalCount = visibleCount + (ep.hidden_count || 0);
      // `ep.models` is the *visible* set — when every model is hidden it's
      // empty, but we still need to render the expand panel so the user can
      // un-hide them. Gate on the total instead.
      const hasModels = ep.online && totalCount > 0;
      const statusBadge = ep.status === 'empty'
        ? '<span class="admin-badge">' + t('ui.js.admin_no_models_badge', null, 'no models') + '</span>'
        : ep.online
          ? `<span class="admin-badge">${t('ui.js.admin_models_enabled_badge', { enabled: visibleCount, total: totalCount }, '{enabled}/{total} models enabled')}</span>`
          : '<span class="admin-badge admin-badge-off">' + t('ui.js.admin_offline_badge', null, 'offline') + '</span>';
      const justAddedClass = (_recentlyAddedEpId && String(ep.id) === _recentlyAddedEpId) ? ' adm-ep-just-added' : '';
      const category = ep.category || (_isLocalEndpoint(ep.base_url) ? 'local' : 'api');
      const kindLabel = ep.endpoint_kind && ep.endpoint_kind !== 'auto' ? ep.endpoint_kind.toUpperCase() : '';
      const keyLabel = ep.has_key
        ? (ep.api_key_fingerprint ? ' (' + t('ui.js.admin_key_fingerprint', { fingerprint: esc(ep.api_key_fingerprint) }, 'key {fingerprint}') + ')' : ' (' + t('ui.js.admin_key_set', null, 'key set') + ')')
        : '';
      return `
        <div class="admin-user-row${ep.is_enabled ? '' : ' admin-ep-disabled'}${justAddedClass}" data-adm-ep-id="${ep.id}">
          <div style="display:flex;align-items:center;justify-content:space-between;${hasModels ? 'cursor:pointer;' : ''}padding:4px 0;" data-adm-ep-header="${ep.id}">
            <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;">
              <span class="admin-user-name">${esc(ep.name)}</span>
              ${ep.model_type === 'image' ? '<span class="admin-badge" style="background:color-mix(in srgb, var(--accent) 20%, transparent);color:var(--accent);">' + t('ui.js.admin_image_badge', null, 'Image') + '</span>' : ''}
              ${kindLabel ? `<span class="admin-badge">${esc(kindLabel)}</span>` : ''}
              ${statusBadge}
              ${ep.is_enabled ? '' : '<span class="admin-badge admin-badge-off">' + t('ui.js.admin_disabled_badge', null, 'disabled') + '</span>'}
              ${hasModels ? '<span style="font-size:10px;opacity:0.4;">' + t('ui.js.admin_click_manage_models', null, 'Click to manage models') + '</span>' : ''}
            </div>
            <div style="display:flex;gap:4px;align-items:center;">
              <button class="admin-btn-sm" data-adm-toggle-ep="${ep.id}">${ep.is_enabled ? t('ui.js.admin_disable', null, 'Disable') : t('ui.js.admin_enable', null, 'Enable')}</button>
              <button class="admin-btn-delete" data-adm-del-ep="${ep.id}" data-adm-ep-online="${ep.online ? '1' : '0'}">${t('ui.js.admin_delete', null, 'Delete')}</button>
              ${hasModels ? '<svg class="admin-user-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3;transition:transform 0.2s,opacity 0.2s;"><polyline points="6 9 12 15 18 9"/></svg>' : ''}
            </div>
          </div>
          <div class="admin-ep-detail">${esc(ep.base_url)}${category === 'local' ? `<button type="button" class="admin-ep-copy-btn" data-adm-copy-url="${esc(ep.base_url)}" title="${t('ui.js.admin_copy_url', null, 'Copy URL')}" aria-label="${t('ui.js.admin_copy_url', null, 'Copy URL')}" style="background:none;border:none;padding:0 2px;margin-left:6px;cursor:pointer;color:inherit;opacity:0.45;vertical-align:-2px;line-height:1;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : ''}${keyLabel}</div>
          ${hasModels ? `<div class="mcp-tools-panel hidden" data-adm-ep-models-panel="${ep.id}"></div>` : ''}
        </div>`;
    });
    // Partition rows into Local vs API for the split sections.
    // Subsections without any rows are hidden entirely (heading + all)
    // so empty groups don't take up vertical real estate.
    const _renderInto = (container, indices) => {
      if (!container) return;
      const section = container.closest('.adm-ep-section');
      if (!indices.length) {
        if (section) section.style.display = 'none';
        container.innerHTML = '';
        return;
      }
      if (section) section.style.display = '';
      container.innerHTML = indices.map(i => rowHtml[i]).join('');
    };
    const localIdx = [], apiIdx = [];
    data.forEach((ep, i) => ((ep.category || (_isLocalEndpoint(ep.base_url) ? 'local' : 'api')) === 'local' ? localIdx : apiIdx).push(i));
    // Sort each section: enabled endpoints first, disabled at the bottom.
    // Preserve original order within each group via stable sort.
    const _sortByEnabled = (a, b) => Number(!!data[b].is_enabled) - Number(!!data[a].is_enabled);
    localIdx.sort(_sortByEnabled);
    apiIdx.sort(_sortByEnabled);
    _renderInto(listLocal, localIdx);
    _renderInto(listApi, apiIdx);
    if (listLegacy) listLegacy.innerHTML = rowHtml.join('');
    // Iterate matching nodes across both containers.
    const queryAll = (sel) => {
      const out = [];
      [listLocal, listApi, listLegacy].forEach(c => {
        if (c) c.querySelectorAll(sel).forEach(n => out.push(n));
      });
      return out;
    };
    queryAll('[data-adm-toggle-ep]').forEach(btn => {
      btn.addEventListener('click', async (e) => { e.stopPropagation(); await fetch(`/api/model-endpoints/${btn.dataset.admToggleEp}`, { method: 'PATCH' }); loadEndpoints(); });
    });
    queryAll('[data-adm-copy-url]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const url = btn.dataset.admCopyUrl || '';
        if (!url) return;
        uiModule.copyToClipboard(url).then(() => {
          // Brief icon swap to a checkmark so the user gets feedback that
          // the copy actually happened. Reverts after ~1.4s.
          const prev = btn.innerHTML;
          btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
          btn.style.opacity = '1';
          setTimeout(() => { btn.innerHTML = prev; btn.style.opacity = ''; }, 1400);
        }).catch(() => {});
      });
    });
    queryAll('[data-adm-del-ep]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        var epId = btn.dataset.admDelEp;
        var isOffline = btn.dataset.admEpOnline === '0';
        // Offline endpoints are already broken — skip the confirm dialog
        // entirely and delete immediately. The optimistic UI removal makes
        // the action feel instant.
        if (!isOffline) {
          var deps = [];
          try {
            var depRes = await fetch('/api/model-endpoints/' + epId + '/dependents', { credentials: 'same-origin' });
            var depData = await depRes.json();
            deps = depData.dependents || [];
          } catch (e) { /* proceed without warning */ }
          var msg = t('ui.js.admin_delete_endpoint_confirm', null, 'Delete this endpoint?');
          if (deps.length) {
            msg += '\n\n' + t('ui.js.admin_endpoint_dependents', null, 'The following settings use this endpoint and will be reset:') + '\n— ' + deps.join('\n— ');
          }
          if (!await uiModule.styledConfirm(msg, { confirmText: t('ui.js.admin_delete', null, 'Delete'), danger: true })) return;
        }
        // Optimistic: remove from UI immediately
        const row = btn.closest('[data-adm-ep-id]');
        if (row) row.remove();
        fetch('/api/model-endpoints/' + epId, { method: 'DELETE' })
          .then(() => _refreshAfterEndpointChange(epId))
          .then(() => loadEndpoints())
          .catch(() => loadEndpoints());
      });
    });
    // Clear the just-added marker now that the row has been rendered
    // with the animation class — keeps the glow from re-firing on every
    // subsequent loadEndpoints() call (e.g. when toggling a model).
    if (_recentlyAddedEpId) _recentlyAddedEpId = null;
    // Models expand/collapse (click anywhere on card)
    queryAll('[data-adm-ep-id]').forEach(row => {
      const header = row.querySelector('[data-adm-ep-header]');
      if (!header) return;
      let _modelsLoaded = false;
      row.style.cursor = 'pointer';
      row.addEventListener('click', async (e) => {
        // Don't let interactions inside the expanded panel re-fire the
        // expand/collapse handler — the search box was getting closed
        // because clicking it bubbled up to here.
        if (e.target.closest('.admin-btn-sm, .admin-btn-delete, .mcp-tools-list, .mcp-tools-header, .mcp-tools-search, input, label')) return;
        const epId = header.dataset.admEpHeader;
        const panel = row.querySelector(`[data-adm-ep-models-panel="${epId}"]`);
        if (!panel) return;
        panel.classList.toggle('hidden');
        const chevron = row.querySelector('.admin-user-chevron');
        const isOpen = !panel.classList.contains('hidden');
        if (chevron) {
          chevron.style.transform = isOpen ? 'rotate(180deg)' : '';
          chevron.style.opacity = isOpen ? '0.7' : '0.3';
        }
        if (!_modelsLoaded && isOpen) {
          _modelsLoaded = true;
          // Our shared whirlpool spinner (consistent with the rest of the app).
          panel.innerHTML = '';
          let _modelsSpin = null;
          const _ld = document.createElement('span');
          _ld.style.cssText = 'opacity:0.55;font-size:11px;display:inline-flex;align-items:center;gap:8px;';
          _ld.appendChild(document.createTextNode(t('ui.js.admin_loading_models_ellipsis', null, 'Loading models…')));
          try {
            const _sp = (await import('./spinner.js')).default;
            _modelsSpin = _sp.createWhirlpool(14);
            _modelsSpin.element.style.cssText = 'width:14px;height:14px;margin:0;display:inline-block;';
            _ld.appendChild(_modelsSpin.element);
          } catch (_) {}
          panel.appendChild(_ld);
          const _stopSpin = () => { try { _modelsSpin && _modelsSpin.stop(); } catch (_) {} };
          const _loadingHtml = (label) => `<span style="opacity:0.55;font-size:11px;display:inline-flex;align-items:center;gap:8px;">${esc(label)}</span>`;
          const renderModels = (models, warning = '') => {
            const sortedModels = sortModelObjects(models);
            const warningHtml = warning ? `<div class="admin-error" style="font-size:11px;margin:6px 0;">${esc(warning)}</div>` : '';
            const attachRefresh = () => {
              panel.querySelector(`[data-ep-refresh-models="${epId}"]`)?.addEventListener('click', async (e) => {
                e.preventDefault();
                panel.innerHTML = _loadingHtml(t('ui.js.admin_refreshing_models', null, 'Refreshing models...'));
                try {
                  const res = await fetch(`/api/model-endpoints/${epId}/models?refresh=true&refresh_timeout=60`, { credentials: 'same-origin' });
                  const refreshWarning = res.headers.get('X-Model-Refresh-Warning') || '';
                  if (!res.ok) throw new Error(`HTTP ${res.status}`);
                  const refreshedModels = await res.json();
                  renderModels(refreshedModels, refreshWarning);
                  if (refreshWarning && uiModule?.showToast) uiModule.showToast(refreshWarning, 6000);
                } catch (_) {
                  renderModels(sortedModels, t('ui.js.admin_model_refresh_failed', null, 'Model refresh failed; kept cached models.'));
                }
              });
            };
            if (!sortedModels.length) {
              panel.innerHTML = `<div class="mcp-tools-header">
                <span>${t('ui.js.admin_models_header', null, 'Models')}</span>
                <span style="display:flex;gap:8px;align-items:center;">
                  <span class="mcp-tools-count">${t('ui.js.admin_n_enabled', { enabled: 0, total: 0 }, '{enabled}/{total} enabled')}</span>
                  <a href="#" data-ep-refresh-models="${epId}">${t('ui.js.admin_refresh', null, 'Refresh')}</a>
                </span>
              </div>${warningHtml}<span style="opacity:0.5;font-size:11px;">${t('ui.js.admin_no_models', null, 'No models')}</span>`;
              attachRefresh();
              return;
            }
            const hiddenSet = new Set(sortedModels.filter(m => m.is_hidden).map(m => m.id));
            const showSearch = sortedModels.length >= 8;
            panel.innerHTML = `<div class="mcp-tools-header">
              <span>${t('ui.js.admin_models_header', null, 'Models')}</span>
              <span style="display:flex;gap:8px;align-items:center;">
                <span class="mcp-tools-count">${t('ui.js.admin_n_enabled', { enabled: sortedModels.length - hiddenSet.size, total: sortedModels.length }, '{enabled}/{total} enabled')}</span>
                <a href="#" data-ep-refresh-models="${epId}">${t('ui.js.admin_refresh', null, 'Refresh')}</a>
                <a href="#" data-ep-select-all="${epId}">${t('ui.js.admin_all', null, 'All')}</a>
                <a href="#" data-ep-select-none="${epId}">${t('ui.js.admin_none', null, 'None')}</a>
              </span>
            </div>${warningHtml}${showSearch ? `<input type="search" class="mcp-tools-search" placeholder="${t('ui.js.admin_search_n_models', { count: sortedModels.length }, 'Search {count} models...')}" data-ep-search="${epId}">` : ''}<div class="mcp-tools-list">` + sortedModels.map(m =>
              `<label title="${esc(m.id)}" data-ep-model-row data-search="${esc((m.display + ' ' + m.id).toLowerCase())}" class="adm-model-row">
                <input type="checkbox" class="adm-cb-hidden" data-ep-model-id="${esc(m.id)}" ${!m.is_hidden ? 'checked' : ''}>
                <span class="adm-check-dot" aria-hidden="true"></span>
                <span>${esc(m.display)}</span>
              </label>`
            ).join('') + '</div>';
            const filterRows = (q) => {
              const needle = q.trim().toLowerCase();
              panel.querySelectorAll('[data-ep-model-row]').forEach(row => {
                row.style.display = (!needle || row.dataset.search.includes(needle)) ? '' : 'none';
              });
            };
            attachRefresh();
            panel.querySelector(`[data-ep-search="${epId}"]`)?.addEventListener('input', (e) => filterRows(e.target.value));
            panel.querySelector(`[data-ep-select-all="${epId}"]`)?.addEventListener('click', (e) => {
              e.preventDefault();
              panel.querySelectorAll('[data-ep-model-row]').forEach(row => {
                if (row.style.display !== 'none') row.querySelector('input[type=checkbox]').checked = true;
              });
              _saveEpModelState(epId, panel);
            });
            panel.querySelector(`[data-ep-select-none="${epId}"]`)?.addEventListener('click', (e) => {
              e.preventDefault();
              panel.querySelectorAll('[data-ep-model-row]').forEach(row => {
                if (row.style.display !== 'none') row.querySelector('input[type=checkbox]').checked = false;
              });
              _saveEpModelState(epId, panel);
            });
            panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
              cb.addEventListener('change', () => _saveEpModelState(epId, panel));
            });
          };
          try {
            const res = await fetch(`/api/model-endpoints/${epId}/models`, { credentials: 'same-origin' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const models = await res.json();
            _stopSpin();
            renderModels(models);
          } catch (e) { _stopSpin(); panel.innerHTML = '<span class="admin-error" style="font-size:11px;">' + t('ui.js.admin_load_models_failed', null, 'Failed to load models') + '</span>'; }
        }
      });
    });
  } catch (e) {
    const err = '<div class="admin-error">' + t('ui.js.admin_load_failed', null, 'Failed to load') + '</div>';
    [listLocal, listApi, listLegacy].forEach(c => { if (c) c.innerHTML = err; });
  }
}

async function _saveEpModelState(epId, panel) {
  const hidden = [];
  panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
    if (!cb.checked) hidden.push(cb.dataset.epModelId);
  });
  const total = panel.querySelectorAll('input[type=checkbox]').length;
  try {
    await fetch(`/api/model-endpoints/${epId}/models`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ hidden }),
    });
    const countLabel = panel.querySelector('.mcp-tools-count');
    if (countLabel) countLabel.textContent = t('ui.js.admin_n_enabled', { enabled: total - hidden.length, total }, '{enabled}/{total} enabled');
    const row = panel.closest('[data-adm-ep-id]');
    if (row) {
      const badge = row.querySelector('.admin-badge');
      if (badge && !badge.classList.contains('admin-badge-off')) badge.textContent = t('ui.js.admin_models_enabled_badge', { enabled: total - hidden.length, total }, '{enabled}/{total} models enabled');
    }
    if (settingsModule && typeof settingsModule.refreshAiModelEndpoints === 'function') {
      settingsModule.refreshAiModelEndpoints();
    }
  } catch (e) { /* silent */ }
}

function initEndpointForm() {
  const provider = el('adm-epProvider');
  const urlInput = el('adm-epUrl');
  const kindSel = el('adm-epKind');

  // Custom provider picker — mirrors the (now hidden) <select id="adm-epProvider">
  // so the rest of this function (which reads provider.value and dispatches
  // change events) keeps working unchanged.
  const picker = el('adm-provider-picker');
  const pickerBtn = el('adm-provider-btn');
  const pickerMenu = el('adm-provider-menu');
  const pickerCurrent = picker ? picker.querySelector('.adm-provider-current') : null;
  const DEVICE_AUTH_PROVIDER_VALUES = new Set(Object.keys(PROVIDER_DEVICE_FLOWS));
  let deviceAuthPolling = false;
  function _selectedProviderOption() {
    return provider && provider.selectedOptions ? provider.selectedOptions[0] : null;
  }
  function _selectedDeviceAuthProvider() {
    const opt = _selectedProviderOption();
    const flow = opt && opt.dataset ? opt.dataset.authFlow : '';
    if (flow && DEVICE_AUTH_PROVIDER_VALUES.has(flow)) return flow;
    return DEVICE_AUTH_PROVIDER_VALUES.has(provider.value) ? provider.value : '';
  }
  function _isDeviceAuthSelected() {
    return !!_selectedDeviceAuthProvider();
  }
  function _setApiFormForProvider() {
    const deviceAuthProvider = _selectedDeviceAuthProvider();
    const deviceAuthConfig = PROVIDER_DEVICE_FLOWS[deviceAuthProvider] || null;
    const apiKey = el('adm-epApiKey');
    const testBtn = el('adm-epApiTestBtn');
    const addBtn = el('adm-epAddBtn');
    const status = el('adm-deviceAuthStatus');
    const msg = _endpointMsg('api');
    if (deviceAuthConfig) {
      urlInput.value = '';
      urlInput.placeholder = deviceAuthProvider === 'copilot'
        ? t('ui.js.admin_copilot_signin', null, 'GitHub Copilot uses GitHub account sign-in')
        : t('ui.js.admin_chatgpt_signin', null, 'ChatGPT Subscription uses OpenAI account sign-in');
      urlInput.readOnly = true;
      if (apiKey) {
        apiKey.value = '';
        apiKey.placeholder = t('ui.js.admin_no_api_key_needed', null, 'No API key needed');
        apiKey.disabled = true;
      }
      if (testBtn) {
        testBtn.disabled = true;
        testBtn.style.opacity = '0.45';
        testBtn.style.cursor = 'not-allowed';
      }
      if (addBtn) {
        addBtn.disabled = false;
        addBtn.textContent = t('ui.js.admin_add', null, 'Add');
        addBtn.style.width = '55px';
        addBtn.style.display = '';
      }
      if (kindSel) kindSel.value = 'api';
      if (msg) {
        msg.textContent = '';
        msg.className = '';
      }
    } else {
      urlInput.placeholder = t('ui.js.admin_base_url_placeholder', null, 'Base URL or pick provider');
      urlInput.readOnly = false;
      if (apiKey) {
        apiKey.placeholder = t('ui.js.admin_api_key_placeholder', null, 'API key');
        apiKey.disabled = false;
      }
      if (testBtn) {
        testBtn.disabled = false;
        testBtn.style.opacity = '';
        testBtn.style.cursor = '';
      }
      if (addBtn) {
        addBtn.disabled = false;
        addBtn.textContent = t('ui.js.admin_add', null, 'Add');
        addBtn.style.width = '55px';
        addBtn.style.display = '';
      }
      if (msg) {
        msg.textContent = '';
        msg.className = '';
      }
      if (!deviceAuthPolling && status) status.textContent = '';
    }
  }
  function _renderPickerMenu() {
    if (!pickerMenu) return;
    pickerMenu.innerHTML = Array.from(provider.options).map(o => {
      const logo = o.dataset.logo ? (providerLogo(o.dataset.logo) || '') : '';
      const active = o.value === provider.value ? ' active' : '';
      return `<div class="adm-provider-item${active}" role="option" data-value="${o.value.replace(/"/g, '&quot;')}">
        <span class="adm-provider-logo">${logo}</span>
        <span>${o.textContent}</span>
      </div>`;
    }).join('');
  }
  function _syncPickerCurrent() {
    if (!pickerCurrent) return;
    const opt = provider.selectedOptions[0] || provider.options[0];
    const logo = opt.dataset.logo ? (providerLogo(opt.dataset.logo) || '') : '';
    pickerCurrent.querySelector('.adm-provider-logo').innerHTML = logo;
    pickerCurrent.querySelector('.adm-provider-name').textContent = opt.textContent;
  }
  if (picker && pickerBtn && pickerMenu && pickerCurrent) {
    _renderPickerMenu();
    _syncPickerCurrent();
    if (provider.value && !urlInput.value) urlInput.value = provider.value;
    pickerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      pickerMenu.classList.toggle('hidden');
    });
    pickerMenu.addEventListener('click', (e) => {
      const item = e.target.closest('.adm-provider-item');
      if (!item) return;
      provider.value = item.dataset.value;
      provider.dispatchEvent(new Event('change', { bubbles: true }));
      pickerMenu.classList.add('hidden');
      _renderPickerMenu();
      _syncPickerCurrent();
    });
    document.addEventListener('click', (e) => {
      if (!picker.contains(e.target)) pickerMenu.classList.add('hidden');
    });
  }

  provider.addEventListener('change', () => {
    if (_isDeviceAuthSelected()) {
      _setApiFormForProvider();
      _renderPickerMenu();
      _syncPickerCurrent();
      return;
    }
    if (provider.value) urlInput.value = provider.value;
    else urlInput.value = '';
    if (kindSel) kindSel.value = provider.value ? 'api' : 'proxy';
    _setApiFormForProvider();
  });
  urlInput.addEventListener('input', () => {
    if (provider.value && urlInput.value.trim() !== provider.value) {
      provider.value = '';
      if (kindSel) kindSel.value = 'api';
      _renderPickerMenu();
      _syncPickerCurrent();
    }
  });
  if (kindSel) kindSel.value = kindSel.value || 'api';
  function _apiEndpointKind() {
    return (kindSel && kindSel.value) ? kindSel.value : 'api';
  }
  function _normalizeBaseUrl(raw) {
    let u = raw.trim();
    // Fix common protocol typos
    u = u.replace(/^https?:\/(?!\/)/, m => m + '/');  // https:/ → https://
    u = u.replace(/^htp:/, 'http:').replace(/^htps:/, 'https:');
    u = u.replace(/^http:\/\/\//, 'http://');  // http:/// → http://
    u = u.replace(/^https:\/\/\//, 'https://');
    // Add http:// if no protocol
    if (!/^https?:\/\//.test(u)) u = 'http://' + u;
    // Strip trailing slashes
    u = u.replace(/\/+$/, '');
    // Strip trailing paths that shouldn't be in a base URL
    u = u.replace(/\/v1\/(models|chat\/completions|completions|messages)\/?$/i, '/v1');
    u = u.replace(/\/(models|chat\/completions|completions|v1\/messages)\/?$/i, '');
    u = u.replace(/\/api\/(chat|tags|generate)\/?$/i, '/api');
    // Fix double /v1/v1
    u = u.replace(/\/v1\/v1$/, '/v1');
    // Strip query params and fragments
    u = u.split('?')[0].split('#')[0];
    try {
      const parsed = new URL(u);
      if (parsed.hostname.endsWith('ollama.com')) {
        u = 'https://ollama.com/api';
      }
    } catch(e) {}
    // Ensure /v1 suffix for bare host:port URLs (not cloud providers)
    if (!u.includes('api.') && !u.includes('openrouter') && !u.includes('opencode.ai') && !u.includes('ollama.com') && !u.endsWith('/v1')) {
      try {
        const parsed = new URL(u);
        if (!parsed.pathname || parsed.pathname === '/') {
          u += '/v1';
        }
      } catch(e) {}
    }
    return u;
  }

  async function _defaultOllamaUrl() {
    try {
      const res = await fetch('/api/runtime', { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        if (data && data.ollama_base_url) return data.ollama_base_url;
      }
    } catch (_) {}
    return 'http://127.0.0.1:11434/v1';
  }

  function _renderEndpointTestResult(msg, res, d) {
    if (res.ok && d.status === 'empty') {
      msg.textContent = t('ui.js.admin_online_no_models', null, 'Online — no models found');
      msg.className = 'admin-success';
      return;
    }
    if (res.ok && d.online) {
      const models = d.models || [];
      const preview = models.slice(0, 3).map(m => esc(String(m).split('/').pop())).join(', ');
      msg.innerHTML = t('ui.js.admin_online_found_models', { count: models.length }, 'Online — found {count} model(s)') + `${preview ? `: ${preview}${models.length > 3 ? ', …' : ''}` : ''}`;
      msg.className = 'admin-success';
      return;
    }
    msg.textContent = (d && d.detail) || (d && d.ping_error ? t('ui.js.admin_offline_reason', { reason: d.ping_error }, 'Offline — {reason}') : t('ui.js.admin_offline', null, 'Offline'));
    msg.className = 'admin-error';
  }

  function _endpointMsg(kind) {
    return el(kind === 'local' ? 'adm-epLocalMsg' : 'adm-epApiMsg') || el('adm-epMsg');
  }

  let apiTestController = null;
  const apiTestBtn = el('adm-epApiTestBtn');
  const apiCancelTestBtn = el('adm-epApiCancelTestBtn');
  if (apiTestBtn) {
    apiTestBtn.addEventListener('click', async () => {
      if (_isDeviceAuthSelected()) {
        const msg = _endpointMsg('api');
        msg.textContent = '';
        msg.className = '';
        return;
      }
      const msg = _endpointMsg('api');
      msg.textContent = ''; msg.className = '';
      const rawUrl = (urlInput.value || provider.value).trim();
      const apiKey = el('adm-epApiKey').value.trim();
      if (!rawUrl) { msg.textContent = t('ui.js.admin_select_provider_or_url', null, 'Select a provider or enter a base URL'); msg.className = 'admin-error'; return; }
      if (provider.value && !apiKey) { msg.textContent = t('ui.js.admin_api_key_required', null, 'API key is required for cloud providers'); msg.className = 'admin-error'; return; }
      const url = provider.value && rawUrl === provider.value ? rawUrl : _normalizeBaseUrl(rawUrl);
      apiTestController = new AbortController();
      apiTestBtn.disabled = true;
      apiTestBtn.textContent = t('ui.js.admin_testing', null, 'Testing...');
      if (apiCancelTestBtn) apiCancelTestBtn.classList.remove('hidden');
      try {
        const fd = new FormData();
        fd.append('base_url', url);
        fd.append('endpoint_kind', _apiEndpointKind());
        fd.append('model_refresh_timeout', '30');
        if (apiKey) fd.append('api_key', apiKey);
        const res = await fetch('/api/model-endpoints/test', {
          method: 'POST',
          body: fd,
          credentials: 'same-origin',
          signal: apiTestController.signal,
        });
        const d = await res.json();
        _renderEndpointTestResult(msg, res, d);
      } catch (e) {
        if (e && e.name === 'AbortError') {
          msg.textContent = t('ui.js.admin_test_canceled', null, 'Test canceled');
          msg.className = '';
        } else {
          msg.textContent = t('ui.js.admin_test_failed', { error: e && e.message ? e.message : t('ui.js.admin_request_failed_lc', null, 'request failed') }, 'Test failed: {error}');
          msg.className = 'admin-error';
        }
      }
      apiTestController = null;
      apiTestBtn.disabled = false;
      apiTestBtn.textContent = t('ui.js.admin_test', null, 'Test');
      if (apiCancelTestBtn) apiCancelTestBtn.classList.add('hidden');
    });
  }
  if (apiCancelTestBtn) {
    apiCancelTestBtn.addEventListener('click', () => {
      if (apiTestController) apiTestController.abort();
    });
  }

  el('adm-epAddBtn').addEventListener('click', async () => {
    const deviceAuthProvider = _selectedDeviceAuthProvider();
    if (deviceAuthProvider) {
      await _startProviderDeviceAuth(deviceAuthProvider, el('adm-epAddBtn'));
      return;
    }
    const msg = _endpointMsg('api');
    msg.textContent = ''; msg.className = '';
    const rawUrl = (urlInput.value || provider.value).trim();
    const apiKey = el('adm-epApiKey').value.trim();
    if (!rawUrl) { msg.textContent = t('ui.js.admin_select_provider_or_url', null, 'Select a provider or enter a base URL'); msg.className = 'admin-error'; return; }
    if (provider.value && !apiKey) { msg.textContent = t('ui.js.admin_api_key_required', null, 'API key is required for cloud providers'); msg.className = 'admin-error'; return; }
    // Normalize URL (fix typos, add /v1, strip wrong paths)
    const url = provider.value && rawUrl === provider.value ? rawUrl : _normalizeBaseUrl(rawUrl);
    const btn = el('adm-epAddBtn');
    btn.disabled = true; btn.textContent = t('ui.js.admin_adding', null, 'Adding...');
    try {
      const fd = new FormData();
      fd.append('base_url', url);
      const endpointKind = _apiEndpointKind();
      fd.append('endpoint_kind', endpointKind);
      fd.append('model_refresh_mode', endpointKind === 'proxy' ? 'manual' : 'auto');
      fd.append('model_refresh_timeout', '30');
      if (apiKey) fd.append('api_key', apiKey);
      if (provider.value && provider.selectedOptions && provider.selectedOptions[0]) {
        fd.append('name', provider.selectedOptions[0].textContent.trim());
      }
      const epType = el('adm-epType');
      if (epType) fd.append('model_type', epType.value);
      if (provider.value && /openrouter\.ai|ollama\.com/i.test(provider.value)) fd.append('require_models', 'true');
      else fd.append('skip_probe', 'false');
      const res = await fetch('/api/model-endpoints', { method: 'POST', body: fd, credentials: 'same-origin' });
      const d = await res.json();
      if (res.ok) {
        const count = d.models ? d.models.length : 0;
        urlInput.value = ''; urlInput.style.display = '';
        el('adm-epApiKey').value = ''; provider.value = '';
        if (kindSel) kindSel.value = 'proxy';
        if (epType) epType.value = 'llm';
        if (d.id) _recentlyAddedEpId = String(d.id);
        await loadEndpoints();
        await _selectAddedModelInChat(d);
        if (!d.online) {
          msg.textContent = t('ui.js.admin_added_offline', null, 'Added (endpoint offline — will retry on next load)');
          msg.className = 'admin-error';
        } else if (d.status === 'empty') {
          msg.textContent = t('ui.js.admin_added_empty', null, 'Added — endpoint reachable, no models found');
          msg.className = 'admin-success';
        } else {
          msg.textContent = t('ui.js.admin_added_found', { count }, 'Added — found {count} model(s)');
          msg.className = 'admin-success';
        }
      } else { msg.textContent = d.detail || t('ui.js.admin_failed', null, 'Failed'); msg.className = 'admin-error'; }
    } catch (e) { msg.textContent = t('ui.js.admin_request_failed', null, 'Request failed'); msg.className = 'admin-error'; }
    btn.disabled = false; btn.textContent = t('ui.js.admin_add', null, 'Add');
  });

  async function _startProviderDeviceAuth(providerKey, triggerEl = null) {
    if (deviceAuthPolling) return;
    const config = PROVIDER_DEVICE_FLOWS[providerKey];
    if (!config) return;
    const status = el('adm-deviceAuthStatus') || _endpointMsg('api');
    if (!status) return;
    const triggerText = triggerEl ? triggerEl.textContent : '';
    // Render an error with an inline "Try again" (the top button is hidden for
    // device-auth providers, so retry lives here). Built with DOM methods, not
    // innerHTML. Call reset() first so the deviceAuthPolling guard is cleared.
    const showAuthError = (text) => {
      status.className = 'admin-error';
      status.textContent = text + ' ';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'admin-btn-sm';
      retry.textContent = t('ui.js.admin_try_again', null, 'Try again');
      retry.addEventListener('click', () => { _startProviderDeviceAuth(providerKey, triggerEl); });
      status.appendChild(retry);
    };
    const reset = () => {
      if (triggerEl) {
        triggerEl.disabled = false;
        triggerEl.textContent = triggerText || t('ui.js.admin_add', null, 'Add');
      }
      deviceAuthPolling = false;
      _setApiFormForProvider();
    };
    status.textContent = '';
    status.className = 'adm-ep-inline-msg';
    if (triggerEl) {
      triggerEl.disabled = true;
      triggerEl.textContent = t('ui.js.admin_starting', null, 'Starting...');
    }
    deviceAuthPolling = true;
    _setApiFormForProvider();
    status.textContent = t('ui.js.admin_starting_signin', { provider: config.label }, 'Starting {provider} sign-in...');

    try {
      const result = await runProviderDeviceFlow(providerKey, {
        openWindow: () => {},
        onStart: ({ start, authUrl }) => {
          if (triggerEl) triggerEl.textContent = t('ui.js.admin_waiting', null, 'Waiting...');
          status.className = '';
          const authLabel = providerKey === 'copilot' ? t('ui.js.admin_authorize_github', null, 'Authorize on GitHub') : t('ui.js.admin_authorize_openai', null, 'Authorize with OpenAI');
          const waitLabel = providerKey === 'copilot' ? t('ui.js.admin_waiting_github', null, 'Waiting for GitHub authorization...') : t('ui.js.admin_waiting_chatgpt', null, 'Waiting for ChatGPT authorization...');
          status.innerHTML =
            '<div class="adm-copilot-panel">' +
              '<div class="adm-copilot-wait"><span class="admin-spinner"></span>' +
                '<span>' + esc(waitLabel) + '</span></div>' +
              '<div class="adm-copilot-coderow">' +
                '<span class="adm-copilot-code-label">' + t('ui.js.admin_code_label', null, 'Code') + '</span>' +
                '<code class="adm-copilot-code">' + esc(start.user_code) + '</code>' +
                '<button type="button" class="admin-btn-sm adm-device-auth-copy">' + t('ui.js.admin_copy', null, 'Copy') + '</button>' +
              '</div>' +
              '<a class="admin-btn-add adm-copilot-auth" href="' + encodeURI(authUrl || '') + '" target="_blank" rel="noopener">' + esc(authLabel) + ' ↗</a>' +
            '</div>';
          const copyBtn = status.querySelector('.adm-device-auth-copy');
          if (copyBtn) copyBtn.addEventListener('click', async () => {
            const code = start.user_code || '';
            let ok = false;
            try {
              if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(code);
                ok = true;
              }
            } catch (e) {}
            if (!ok) {
              // navigator.clipboard is unavailable in non-secure contexts (HTTP
              // self-host over a LAN IP), so fall back to execCommand('copy').
              const ta = document.createElement('textarea');
              ta.value = code;
              ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;';
              document.body.appendChild(ta);
              ta.focus();
              ta.select();
              try { ta.setSelectionRange(0, code.length); } catch (e) {}
              try { ok = document.execCommand('copy'); } catch (e) {}
              ta.remove();
            }
            copyBtn.textContent = ok ? t('ui.js.admin_copied', null, 'Copied') : t('ui.js.admin_failed', null, 'Failed');
            setTimeout(() => { copyBtn.textContent = t('ui.js.admin_copy', null, 'Copy'); }, 1500);
          });
        },
      });
      if (result.status === 'authorized') {
        const endpoint = result.endpoint || {};
        const n = ((endpoint && endpoint.models) || []).length;
        status.className = 'admin-success';
        status.textContent = t('ui.js.admin_connected_models', { count: n, provider: config.label }, 'Connected - {count} {provider} model(s) available.');
        if (endpoint && endpoint.id) _recentlyAddedEpId = String(endpoint.id);
        await loadEndpoints();
        await _selectAddedModelInChat(endpoint || {});
        reset();
        return;
      }
      if (result.status === 'failed') {
        reset();
        showAuthError(t('ui.js.admin_auth_failed', { error: result.error || 'denied' }, 'Authorization failed ({error}).'));
        return;
      }
      if (result.status === 'expired') {
        reset();
        showAuthError(t('ui.js.admin_auth_expired', null, 'Authorization expired.'));
        return;
      }
    } catch (e) {
      reset();
      showAuthError(formatDeviceFlowError(e));
    }
  }

  // API Key reveal toggle. The key inputs are hidden by default so the Add
  // form reads as a single action row; the Key button toggles the input row
  // and flips aria-expanded for screen readers / CSS pseudo-classes.
  const _wireKeyToggle = (btnId, rowId) => {
    const btn = el(btnId);
    const row = el(rowId);
    if (!btn || !row) return;
    btn.addEventListener('click', () => {
      const showing = row.style.display !== 'none';
      row.style.display = showing ? 'none' : '';
      btn.setAttribute('aria-expanded', showing ? 'false' : 'true');
      btn.style.opacity = showing ? '0.75' : '1';
      if (!showing) {
        const inp = row.querySelector('input');
        if (inp) inp.focus();
      }
    });
  };
  _wireKeyToggle('adm-epLocalKeyBtn', 'adm-epLocalApiKey-row');
  _wireKeyToggle('adm-epApiKeyBtn', 'adm-epApiKey-row');

  // ── Added Models toolbar: Probe + Clear offline ────────────────────
  // Both buttons act over the currently-rendered endpoint list. The
  // online/offline marker is stamped on each row's [data-adm-ep-online]
  // attribute by loadEndpoints(), so both buttons just iterate the DOM
  // without re-fetching anything they don't already have.
  const _refreshOfflineCount = () => {
    const lbl = el('adm-epOfflineCount');
    if (!lbl) return;
    const n = document.querySelectorAll('[data-adm-ep-id] [data-adm-ep-online="0"]').length;
    lbl.textContent = n > 0 ? `(${n})` : '';
    // Keep the button enabled even when there are no offline rows — a
    // click on the empty case fires a toast instead of feeling dead.
    const btn = el('adm-epClearOfflineBtn');
    if (btn) btn.style.opacity = n === 0 ? '0.55' : '0.85';
  };
  // Wire after every loadEndpoints() run by patching the render hook —
  // simplest path: MutationObserver on the two list containers.
  const _obsRoots = ['adm-epList-local', 'adm-epList-api']
    .map(id => el(id)).filter(Boolean);
  if (_obsRoots.length) {
    const mo = new MutationObserver(_refreshOfflineCount);
    _obsRoots.forEach(r => mo.observe(r, { childList: true, subtree: true }));
    _refreshOfflineCount();
  }

  const probeAllBtn = el('adm-epProbeAllBtn');
  if (probeAllBtn) {
    probeAllBtn.addEventListener('click', async () => {
      probeAllBtn.disabled = true;
      const origHTML = probeAllBtn.innerHTML;
      probeAllBtn.innerHTML = '<span style="opacity:0.7;">' + t('ui.js.admin_probing', null, 'Probing…') + '</span>';
      try {
        // Hit the bulk local probe (same one the model picker uses).
        await fetch('/api/model-endpoints/probe-local', { credentials: 'same-origin' }).catch(() => {});
        // Then per-endpoint /probe for the rest so API/cloud endpoints
        // refresh too. Parallel — capped to 6 at a time so we don't
        // hammer the backend on a big list.
        const ids = Array.from(document.querySelectorAll('[data-adm-ep-id]')).map(r => r.getAttribute('data-adm-ep-id')).filter(Boolean);
        const lane = async (id) => {
          try { await fetch(`/api/model-endpoints/${id}/probe`, { credentials: 'same-origin' }); } catch (_) {}
        };
        const queue = [...ids];
        const workers = Array.from({length: Math.min(6, queue.length)}, () => (async () => {
          while (queue.length) {
            const id = queue.shift();
            if (id) await lane(id);
          }
        })());
        await Promise.all(workers);
        await loadEndpoints();
        if (uiModule && uiModule.showToast) uiModule.showToast(t('ui.js.admin_status_refreshed', null, 'Endpoint status refreshed'), 1800);
      } finally {
        probeAllBtn.innerHTML = origHTML;
        probeAllBtn.disabled = false;
      }
    });
  }

  const clearOfflineBtn = el('adm-epClearOfflineBtn');
  if (clearOfflineBtn) {
    clearOfflineBtn.addEventListener('click', async () => {
      const offlineBtns = Array.from(document.querySelectorAll('[data-adm-del-ep][data-adm-ep-online="0"]'));
      const ids = offlineBtns.map(b => b.getAttribute('data-adm-del-ep')).filter(Boolean);
      if (!ids.length) {
        if (uiModule && uiModule.showToast) {
          uiModule.showToast(t('ui.js.admin_no_offline_endpoints', null, 'No offline endpoints — nothing to clear'), 1800);
        }
        return;
      }
      const confirmMsg = ids.length === 1
        ? t('ui.js.admin_remove_offline_one', null, 'Remove 1 offline endpoint?')
        : t('ui.js.admin_remove_offline_many', { count: ids.length }, 'Remove {count} offline endpoints?');
      if (uiModule && uiModule.styledConfirm) {
        const ok = await uiModule.styledConfirm(confirmMsg, { confirmText: t('ui.js.admin_remove', null, 'Remove'), danger: true });
        if (!ok) return;
      } else if (!confirm(confirmMsg)) {
        return;
      }
      clearOfflineBtn.disabled = true;
      // Optimistic UI: pull rows immediately, then fire the DELETEs.
      offlineBtns.forEach(b => {
        const row = b.closest('[data-adm-ep-id]');
        if (row) row.remove();
      });
      await Promise.all(ids.map(id =>
        fetch('/api/model-endpoints/' + id, { method: 'DELETE', credentials: 'same-origin' }).catch(() => {})
      ));
      try { await loadEndpoints(); } catch (_) {}
      _refreshOfflineCount();
      if (uiModule && uiModule.showToast) uiModule.showToast(ids.length === 1 ? t('ui.js.admin_removed_offline_one', null, 'Removed 1 offline endpoint') : t('ui.js.admin_removed_offline_many', { count: ids.length }, 'Removed {count} offline endpoints'), 1800);
    });
  }

  // Clear-on-focus for the API key inputs. The fields are type=password so the
  // value is masked; users can't see what's there to edit it in place, so the
  // expected gesture is "click in, type new key". Wiping on focus removes the
  // select-all-and-delete dance.
  const _wireClearOnFocus = (id) => {
    const inp = el(id);
    if (!inp) return;
    inp.addEventListener('focus', () => {
      if (inp.value) inp.value = '';
    });
  };
  _wireClearOnFocus('adm-epLocalApiKey');
  _wireClearOnFocus('adm-epApiKey');

  // Drop the Ollama provider logo into the Ollama Quickstart button. Reuses
  // the same SVG the provider picker uses, so brand parity stays free.
  try {
    const _ollamaLogoSlot = document.querySelector('#adm-epOllamaBtn .adm-ollama-logo');
    if (_ollamaLogoSlot) {
      const svg = providerLogo('ollama') || '';
      if (svg) _ollamaLogoSlot.innerHTML = svg;
    }
  } catch (_) {}

  // Local "Add" button — sibling form for self-hosted base URLs.
  const localAddBtn = el('adm-epLocalAddBtn');
  const localTestBtn = el('adm-epLocalTestBtn');
  if (localTestBtn) {
    localTestBtn.addEventListener('click', async () => {
      const msg = _endpointMsg('local');
      msg.textContent = ''; msg.className = '';
      const raw = (el('adm-epLocalUrl').value || '').trim();
      if (!raw) { msg.textContent = t('ui.js.admin_enter_url_test', null, 'Enter a base URL to test'); msg.className = 'admin-error'; return; }
      const url = _normalizeBaseUrl(raw);
      const keyEl = el('adm-epLocalApiKey');
      const apiKey = keyEl ? keyEl.value.trim() : '';
      localTestBtn.disabled = true;
      localTestBtn.textContent = t('ui.js.admin_testing', null, 'Testing...');
      try {
        const fd = new FormData();
        fd.append('base_url', url);
        if (apiKey) fd.append('api_key', apiKey);
        const res = await fetch('/api/model-endpoints/test', { method: 'POST', body: fd, credentials: 'same-origin' });
        const d = await res.json();
        _renderEndpointTestResult(msg, res, d);
      } catch (e) {
        msg.textContent = t('ui.js.admin_test_failed', { error: e && e.message ? e.message : t('ui.js.admin_request_failed_lc', null, 'request failed') }, 'Test failed: {error}');
        msg.className = 'admin-error';
      }
      localTestBtn.disabled = false;
      localTestBtn.textContent = t('ui.js.admin_test', null, 'Test');
    });
  }
  if (localAddBtn) {
    localAddBtn.addEventListener('click', async () => {
      const msg = _endpointMsg('local');
      msg.textContent = ''; msg.className = '';
      const raw = (el('adm-epLocalUrl').value || '').trim();
      if (!raw) { msg.textContent = t('ui.js.admin_enter_url_example', null, 'Enter a base URL (e.g. http://localhost:8002/v1)'); msg.className = 'admin-error'; return; }
      const url = _normalizeBaseUrl(raw);
      const keyEl = el('adm-epLocalApiKey');
      const apiKey = keyEl ? keyEl.value.trim() : '';
      localAddBtn.disabled = true; localAddBtn.textContent = t('ui.js.admin_adding', null, 'Adding...');
      try {
        const fd = new FormData();
        fd.append('base_url', url);
        if (apiKey) fd.append('api_key', apiKey);
        fd.append('endpoint_kind', 'local');
        fd.append('model_refresh_mode', 'auto');
        const lt = el('adm-epLocalType');
        if (lt) fd.append('model_type', lt.value);
        fd.append('skip_probe', 'false');
        const res = await fetch('/api/model-endpoints', { method: 'POST', body: fd, credentials: 'same-origin' });
        const d = await res.json();
        if (res.ok) {
          el('adm-epLocalUrl').value = '';
          if (keyEl) keyEl.value = '';
          if (lt) lt.value = 'llm';
          if (d.id) _recentlyAddedEpId = String(d.id);
          await loadEndpoints();
          await _selectAddedModelInChat(d);
          const count = (d.models || []).length;
          msg.textContent = d.status === 'empty'
            ? t('ui.js.admin_added_ollama_empty', null, 'Added — Ollama is running, no models pulled yet')
            : d.online
            ? t('ui.js.admin_added_found', { count }, 'Added — found {count} model(s)')
            : t('ui.js.admin_added_offline_short', null, 'Added (offline — will retry on next load)');
          msg.className = d.online ? 'admin-success' : 'admin-error';
        } else { msg.textContent = d.detail || t('ui.js.admin_failed', null, 'Failed'); msg.className = 'admin-error'; }
      } catch (e) { msg.textContent = t('ui.js.admin_request_failed', null, 'Request failed'); msg.className = 'admin-error'; }
      localAddBtn.disabled = false; localAddBtn.textContent = t('ui.js.admin_add', null, 'Add');
    });
  }

  const ollamaBtn = el('adm-epOllamaBtn');
  if (ollamaBtn) {
    ollamaBtn.addEventListener('click', async () => {
      const input = el('adm-epLocalUrl');
      if (input) {
        input.value = await _defaultOllamaUrl();
        input.focus();
      }
      const msg = _endpointMsg('local');
      if (msg) {
        msg.innerHTML = '<span style="font-size:11px;opacity:0.55;">' + t('ui.js.admin_ollama_ready', null, 'Ollama ready to test.') + '</span>';
        msg.className = '';
      }
    });
  }

  // Discover local models button
  const discoverBtn = el('adm-epDiscoverBtn');
  if (discoverBtn) {
    discoverBtn.addEventListener('click', async () => {
      const msg = _endpointMsg('local');
      discoverBtn.disabled = true;
      // Keep the button's icon as-is while scanning; the whirlpool +
      // status text below is enough feedback. (Two spinning indicators
      // at once looks busy.)
      msg.className = '';
      msg.innerHTML = '';
      try {
        const sp = window.spinnerModule || (await import('./spinner.js')).default;
        const wp = sp.createWhirlpool(20);
        wp.element.style.cssText = 'display:inline-block;vertical-align:middle;margin:0 8px 0 0;';
        const wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;align-items:center;padding:8px 0;';
        wrap.appendChild(wp.element);
        const txt = document.createElement('span');
        txt.textContent = t('ui.js.admin_scanning_ports', null, 'Scanning ports 8000-8020 and 11434 for model servers...');
        txt.style.cssText = 'font-size:12px;opacity:0.7;';
        wrap.appendChild(txt);
        msg.appendChild(wrap);
        discoverBtn._wp = wp;
      } catch(e) { msg.textContent = t('ui.js.admin_scanning', null, 'Scanning...'); }
      try {
        const res = await fetch('/api/discover');
        const data = await res.json();
        const items = data.items || [];
        if (!items.length) {
          msg.textContent = t('ui.js.admin_no_servers_found', null, 'No model servers found. Make sure vLLM, llama.cpp, SGLang, or Ollama is running. Docker users may need Ollama bound to a trusted reachable interface.');
          msg.className = 'admin-error';
        } else {
          // Auto-add each discovered endpoint. Server dedupes on base_url
          // and returns `existing: true` for already-registered ones.
          let added = 0;
          let skipped = 0;
          for (const item of items) {
            const base = item.url.replace('/chat/completions', '').replace(/\/$/, '');
            const fd = new FormData();
            fd.append('base_url', base);
            fd.append('endpoint_kind', 'local');
            fd.append('model_refresh_mode', 'auto');
            fd.append('skip_probe', 'false');
            const r = await fetch('/api/model-endpoints', { method: 'POST', body: fd });
            if (r.ok) {
              try {
                const dd = await r.json();
                if (dd && dd.existing) { skipped++; }
                else { added++; if (dd && dd.id) _recentlyAddedEpId = String(dd.id); }
              } catch (_) { added++; }
            }
          }
          const totalModels = items.reduce((n, i) => n + (i.models ? i.models.length : 0), 0);
          const parts = [t('ui.js.admin_found_servers', { servers: items.length, models: totalModels }, 'Found {servers} server(s) with {models} model(s)')];
          if (added) parts.push(t('ui.js.admin_added_new', { count: added }, 'added {count} new'));
          if (skipped) parts.push(t('ui.js.admin_already_added', { count: skipped }, '{count} already added'));
          msg.innerHTML = parts.join(' — ');
          msg.className = 'admin-success';
          loadEndpoints();
        }
      } catch (e) {
        msg.textContent = t('ui.js.admin_scan_failed', { error: e.message }, 'Scan failed: {error}');
        msg.className = 'admin-error';
      }
      if (discoverBtn._wp) { discoverBtn._wp.destroy(); discoverBtn._wp = null; }
      discoverBtn.disabled = false;
      discoverBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-1px;margin-right:4px;"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>' + t('ui.js.admin_scan_for_servers', null, 'Scan for Servers');
    });
  }

  // Collapsible Add-Models subsections (API / Local). Both start collapsed
  // so the card is compact; the last-used state is remembered per section
  // in localStorage so a frequent API-adder doesn't re-expand every time.
  document.querySelectorAll('#adm-add-api, #adm-add-local').forEach((sec) => {
    const head = sec.querySelector('.adm-section-toggle');
    if (!head) return;
    const key = 'odysseus.addModels.' + sec.id + '.open';
    let open = false;
    try { open = localStorage.getItem(key) === '1'; } catch {}
    const apply = () => {
      sec.classList.toggle('collapsed', !open);
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    apply();
    const toggle = () => {
      open = !open;
      try { localStorage.setItem(key, open ? '1' : '0'); } catch {}
      apply();
    };
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
  document.querySelectorAll('.adm-quickstart-section').forEach((sec) => {
    const head = sec.querySelector('.adm-quickstart-toggle');
    if (!head) return;
    const key = 'odysseus.addModels.' + sec.id + '.open';
    let open = false;
    try { open = localStorage.getItem(key) === '1'; } catch {}
    const apply = () => {
      sec.classList.toggle('collapsed', !open);
      head.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    apply();
    const toggle = () => {
      open = !open;
      try { localStorage.setItem(key, open ? '1' : '0'); } catch {}
      apply();
    };
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });
}

/* ═══════════════════════════════════════════
   TOOLS TAB — MCP
   ═══════════════════════════════════════════ */

const _GOOGLE_OAUTH_HELP = `To get Google OAuth credentials:
1. Go to console.cloud.google.com
2. Click the project dropdown (top left) > New Project > name it > Create
3. APIs & Services > Library > enable the API you need (Gmail, Calendar, Drive, etc.)
4. APIs & Services > OAuth consent screen > configure (External, app name + email)
5. Under Audience, click Add Users > add your Google email as a test user
6. APIs & Services > Credentials > + Create Credentials > OAuth Client ID > Desktop App
7. Copy the Client ID and Client Secret into the fields above
8. After adding the server, click Authorize to sign in with Google
9. If accessing remotely: sign in, then copy the URL from the error page and paste it back`;

const MCP_PRESETS = [
  { name: "Gmail",           command: "npx", args: ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],      env: { GOOGLE_CLIENT_ID: "", GOOGLE_CLIENT_SECRET: "" },
    oauthFile: { dir: "gmail", filename: "gcp-oauth.keys.json" },
    oauth: {
      provider: "google",
      keys_file: "gmail/gcp-oauth.keys.json",
      token_file: "gmail/credentials.json",
      scopes: ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.settings.basic"],
    },
    help: `Setup:
1. Go to console.cloud.google.com > create or select a project
2. APIs & Services > Library > search "Gmail API" > Enable
3. APIs & Services > OAuth consent screen > set up (External is fine)
4. Under Audience, add your Gmail address as a test user
5. APIs & Services > Credentials > + Create Credentials > OAuth Client ID
6. Application type: Desktop App > Create
7. Copy the Client ID and Client Secret into the fields above
8. Click Add Server, then click the Authorize button
9. Sign in with Google, copy the URL from the error page, paste it back` },
  { name: "Email (IMAP/SMTP)", command: "npx", args: ["-y", "@codefuturist/email-mcp", "stdio"],        env: { MCP_EMAIL_ADDRESS: "", MCP_EMAIL_PASSWORD: "", MCP_EMAIL_IMAP_HOST: "", MCP_EMAIL_SMTP_HOST: "" },
    providerDropdown: {
      label: "Provider",
      targets: { MCP_EMAIL_IMAP_HOST: "imap", MCP_EMAIL_SMTP_HOST: "smtp" },
      options: [
        { name: "Migadu",        imap: "imap.migadu.com",     smtp: "smtp.migadu.com" },
        { name: "Fastmail",      imap: "imap.fastmail.com",   smtp: "smtp.fastmail.com" },
        { name: "Proton Bridge", imap: "127.0.0.1",           smtp: "127.0.0.1" },
        { name: "Outlook/Hotmail", imap: "outlook.office365.com", smtp: "smtp.office365.com" },
        { name: "Yahoo",         imap: "imap.mail.yahoo.com", smtp: "smtp.mail.yahoo.com" },
        { name: "iCloud",        imap: "imap.mail.me.com",    smtp: "smtp.mail.me.com" },
        { name: "Zoho",          imap: "imap.zoho.com",       smtp: "smtp.zoho.com" },
        { name: "Custom",        imap: "",                    smtp: "" },
      ],
    },
    help: "Works with any IMAP/SMTP email provider.\n1. Pick your provider from the dropdown (or choose Custom)\n2. Enter your email address and password (or app password)\n3. Click Add Server" },
  { name: "CalDAV (Radicale/Nextcloud)", command: "npx", args: ["-y", "caldav-mcp"],                     env: { CALDAV_BASE_URL: "http://localhost:5232", CALDAV_USERNAME: "", CALDAV_PASSWORD: "" },
    help: "Works with any CalDAV server (Radicale, Nextcloud, etc.).\n1. Enter your CalDAV server URL (e.g. http://localhost:5232)\n2. Enter your username and password\n3. Click Add Server" },
  { name: "Google Calendar", command: "npx", args: ["-y", "@cocal/google-calendar-mcp"],                 env: { GOOGLE_OAUTH_CREDENTIALS: "" },
    help: `Setup:
1. Go to console.cloud.google.com > create/select a project
2. APIs & Services > Library > enable Google Calendar API
3. APIs & Services > Credentials > + Create Credentials > OAuth Client ID
4. Application type: Desktop App > Create
5. Click "Download JSON" on the credential you just created
6. Set Google Oauth Credentials to the full path of the downloaded JSON file` },
  { name: "Google Drive",    command: "npx", args: ["-y", "@modelcontextprotocol/server-gdrive"],        env: {},
    help: "Google Drive uses browser-based OAuth on first run. No env vars needed — just click Add and authorize when prompted." },
  { name: "GitHub",          command: "npx", args: ["-y", "@modelcontextprotocol/server-github"],        env: { GITHUB_PERSONAL_ACCESS_TOKEN: "" },
    help: "1. Go to github.com > Settings > Developer Settings > Personal Access Tokens > Fine-grained tokens\n2. Generate a new token with the repo permissions you need\n3. Paste it as Github Personal Access Token" },
  { name: "Slack",           command: "npx", args: ["-y", "@modelcontextprotocol/server-slack"],         env: { SLACK_BOT_TOKEN: "", SLACK_TEAM_ID: "" },
    help: "1. Go to api.slack.com/apps > Create New App > From Scratch\n2. Add Bot Token Scopes (channels:read, chat:write, etc.)\n3. Install to workspace, copy the Bot User OAuth Token (xoxb-...)\n4. Team ID is in your workspace URL or Slack admin settings" },
  { name: "Notion",          command: "npx", args: ["-y", "@notionhq/notion-mcp-server"],               env: { OPENAPI_MCP_HEADERS: "" },
    help: "1. Go to notion.so/my-integrations\n2. Create a new integration\n3. Copy the Internal Integration Secret\n4. Share the Notion pages/databases you want accessible with the integration\n5. For Openapi Mcp Headers enter:\n   {\"Authorization\": \"Bearer YOUR_SECRET\", \"Notion-Version\": \"2022-06-28\"}" },
  { name: "Linear",          command: "npx", args: ["-y", "mcp-linear"],                                env: { LINEAR_API_KEY: "" },
    help: "1. Go to linear.app > Settings > API\n2. Create a Personal API Key\n3. Paste it as Linear Api Key" },
  { name: "Brave Search",    command: "npx", args: ["-y", "@modelcontextprotocol/server-brave-search"], env: { BRAVE_API_KEY: "" },
    help: "1. Go to brave.com/search/api\n2. Sign up for a free plan (2000 queries/month)\n3. Copy your API key" },
  { name: "Browser (Playwright)", command: "npx", args: ["-y", "@playwright/mcp@latest", "--headless"],  env: {},
    help: "Browser automation via Playwright. The AI can navigate pages, click, fill forms, and read content.\nRuns headless by default. Remove --headless from Args to see the browser window.\nFirst run installs Chromium automatically." },
  { name: "Filesystem",      command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "/home"], env: {},
    help: "Edit the Args field to change which directory the server has access to." },
  { name: "Memory",          command: "npx", args: ["-y", "@modelcontextprotocol/server-memory"],        env: {} },
  { name: "Postgres",        command: "npx", args: ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/db"], env: {},
    help: "Replace the connection string in the Args field with your actual Postgres connection URL." },
  { name: "Todoist",         command: "npx", args: ["-y", "todoist-mcp-server"],                         env: { TODOIST_API_TOKEN: "" },
    help: "1. Go to todoist.com > Settings > Integrations > Developer\n2. Copy your API token" },
];
// ── Built-in tools management ──
// Lazy (function) so labels resolve at render time against the active locale —
// admin.js is imported before initI18n() loads the locale JSON, so a frozen
// top-level object would lock in English. See PRIV_LABELS note above.
const TOOL_META = () => ({
  bash:              { name: t('ui.js.admin_tool_bash', null, 'Shell'),            desc: t('ui.js.admin_tool_bash_desc', null, 'Execute bash commands'),           cat: 'Code',       ctx: '~200' },
  python:            { name: t('ui.js.admin_tool_python', null, 'Python'),         desc: t('ui.js.admin_tool_python_desc', null, 'Run Python scripts'),            cat: 'Code',       ctx: '~200' },
  read_file:         { name: t('ui.js.admin_tool_read_file', null, 'Read File'),   desc: t('ui.js.admin_tool_read_file_desc', null, 'Read files from disk'),       cat: 'Code',       ctx: '~150' },
  write_file:        { name: t('ui.js.admin_tool_write_file', null, 'Write File'), desc: t('ui.js.admin_tool_write_file_desc', null, 'Write/create files'),        cat: 'Code',       ctx: '~150' },
  web_search:        { name: t('ui.js.admin_tool_web_search', null, 'Web Search'), desc: t('ui.js.admin_tool_web_search_desc', null, 'Search the web via SearXNG'), cat: 'Search',    ctx: '~300' },
  search_chats:      { name: t('ui.js.admin_tool_search_chats', null, 'Search Chats'), desc: t('ui.js.admin_tool_search_chats_desc', null, 'Search conversation history'), cat: 'Search', ctx: '~150' },
  create_document:   { name: t('ui.js.admin_tool_create_document', null, 'Create Document'), desc: t('ui.js.admin_tool_create_document_desc', null, 'Create new documents'), cat: 'Documents', ctx: '~200' },
  update_document:   { name: t('ui.js.admin_tool_update_document', null, 'Update Document'), desc: t('ui.js.admin_tool_update_document_desc', null, 'Modify existing documents'), cat: 'Documents', ctx: '~200' },
  edit_document:     { name: t('ui.js.admin_tool_edit_document', null, 'Edit Document'), desc: t('ui.js.admin_tool_edit_document_desc', null, 'Find & replace in documents'), cat: 'Documents', ctx: '~200' },
  suggest_document:  { name: t('ui.js.admin_tool_suggest_document', null, 'Suggest Changes'), desc: t('ui.js.admin_tool_suggest_document_desc', null, 'Propose document edits'), cat: 'Documents', ctx: '~200' },
  manage_documents:  { name: t('ui.js.admin_tool_manage_documents', null, 'Manage Documents'), desc: t('ui.js.admin_tool_manage_documents_desc', null, 'List, delete, organize docs'), cat: 'Documents', ctx: '~150' },
  generate_image:    { name: t('ui.js.admin_tool_generate_image', null, 'Generate Image'), desc: t('ui.js.admin_tool_generate_image_desc', null, 'Create images via AI'), cat: 'Media', ctx: '~150' },
  manage_memory:     { name: t('ui.js.admin_tool_manage_memory', null, 'Memory'), desc: t('ui.js.admin_tool_manage_memory_desc', null, 'Save and recall memories'), cat: 'Knowledge', ctx: '~200' },
  manage_skills:     { name: t('ui.js.admin_tool_manage_skills', null, 'Skills'), desc: t('ui.js.admin_tool_manage_skills_desc', null, 'Learn and use procedures'), cat: 'Knowledge', ctx: '~200' },
  manage_rag:        { name: t('ui.js.admin_tool_manage_rag', null, 'RAG / Docs'), desc: t('ui.js.admin_tool_manage_rag_desc', null, 'Query indexed documents'), cat: 'Knowledge', ctx: '~150' },
  chat_with_model:   { name: t('ui.js.admin_tool_chat_with_model', null, 'Chat with Model'), desc: t('ui.js.admin_tool_chat_with_model_desc', null, 'Talk to another AI model'), cat: 'Multi-Agent', ctx: '~200' },
  second_opinion:    { name: t('ui.js.admin_tool_second_opinion', null, 'Second Opinion'), desc: t('ui.js.admin_tool_second_opinion_desc', null, "Get another model's take"), cat: 'Multi-Agent', ctx: '~150' },
  pipeline:          { name: t('ui.js.admin_tool_pipeline', null, 'Pipeline'), desc: t('ui.js.admin_tool_pipeline_desc', null, 'Multi-step AI workflows'), cat: 'Multi-Agent', ctx: '~200' },
  ask_teacher:       { name: t('ui.js.admin_tool_ask_teacher', null, 'Ask Teacher'), desc: t('ui.js.admin_tool_ask_teacher_desc', null, 'Query a more capable model'), cat: 'Multi-Agent', ctx: '~150' },
  send_to_session:   { name: t('ui.js.admin_tool_send_to_session', null, 'Send to Session'), desc: t('ui.js.admin_tool_send_to_session_desc', null, 'Send message to another chat'), cat: 'Sessions', ctx: '~100' },
  create_session:    { name: t('ui.js.admin_tool_create_session', null, 'Create Session'), desc: t('ui.js.admin_tool_create_session_desc', null, 'Start a new chat session'), cat: 'Sessions', ctx: '~100' },
  list_sessions:     { name: t('ui.js.admin_tool_list_sessions', null, 'List Sessions'), desc: t('ui.js.admin_tool_list_sessions_desc', null, 'Browse existing sessions'), cat: 'Sessions', ctx: '~100' },
  manage_session:    { name: t('ui.js.admin_tool_manage_session', null, 'Manage Session'), desc: t('ui.js.admin_tool_manage_session_desc', null, 'Rename, archive, configure'), cat: 'Sessions', ctx: '~100' },
  list_models:       { name: t('ui.js.admin_tool_list_models', null, 'List Models'), desc: t('ui.js.admin_tool_list_models_desc', null, 'Show available models'), cat: 'System', ctx: '~100' },
  ui_control:        { name: t('ui.js.admin_tool_ui_control', null, 'UI Control'), desc: t('ui.js.admin_tool_ui_control_desc', null, 'Change theme, layout, settings'), cat: 'System', ctx: '~150' },
  manage_tasks:      { name: t('ui.js.admin_tool_manage_tasks', null, 'Tasks'), desc: t('ui.js.admin_tool_manage_tasks_desc', null, 'Schedule automated tasks'), cat: 'System', ctx: '~150' },
  api_call:          { name: t('ui.js.admin_tool_api_call', null, 'API Call'), desc: t('ui.js.admin_tool_api_call_desc', null, 'Make HTTP requests'), cat: 'System', ctx: '~200' },
  manage_endpoints:  { name: t('ui.js.admin_tool_manage_endpoints', null, 'Endpoints'), desc: t('ui.js.admin_tool_manage_endpoints_desc', null, 'Add/remove model endpoints'), cat: 'System', ctx: '~100' },
  manage_mcp:        { name: t('ui.js.admin_tool_manage_mcp', null, 'MCP Servers'), desc: t('ui.js.admin_tool_manage_mcp_desc', null, 'Manage MCP connections'), cat: 'System', ctx: '~100' },
  manage_webhooks:   { name: t('ui.js.admin_tool_manage_webhooks', null, 'Webhooks'), desc: t('ui.js.admin_tool_manage_webhooks_desc', null, 'Configure webhook events'), cat: 'System', ctx: '~100' },
  manage_tokens:     { name: t('ui.js.admin_tool_manage_tokens', null, 'API Tokens'), desc: t('ui.js.admin_tool_manage_tokens_desc', null, 'Manage API access tokens'), cat: 'System', ctx: '~100' },
  manage_settings:   { name: t('ui.js.admin_tool_manage_settings', null, 'Settings'), desc: t('ui.js.admin_tool_manage_settings_desc', null, 'Change app settings'), cat: 'System', ctx: '~100' },
});
// Translated labels for tool categories (keys stay English for grouping).
// Lazy for the same reason as TOOL_META.
const TOOL_CAT_LABELS = () => ({
  'Code': t('ui.js.admin_cat_code', null, 'Code'),
  'Search': t('ui.js.admin_cat_search', null, 'Search'),
  'Documents': t('ui.js.admin_cat_documents', null, 'Documents'),
  'Media': t('ui.js.admin_cat_media', null, 'Media'),
  'Knowledge': t('ui.js.admin_cat_knowledge', null, 'Knowledge'),
  'Multi-Agent': t('ui.js.admin_cat_multi_agent', null, 'Multi-Agent'),
  'Sessions': t('ui.js.admin_cat_sessions', null, 'Sessions'),
  'System': t('ui.js.admin_cat_system', null, 'System'),
  'Other': t('ui.js.admin_cat_other', null, 'Other'),
});

async function loadBuiltinTools() {
  const list = el('adm-builtin-tools-list');
  if (!list) return;
  try {
    const res = await fetch('/api/tools', { credentials: 'same-origin' });
    const data = await res.json();
    const tools = data.tools || [];
    if (!tools.length) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_tools_found', null, 'No tools found') + '</div>'; return; }

    // Resolve the localized maps once (lazy — see TOOL_META definition).
    const toolMeta = TOOL_META();
    const catLabels = TOOL_CAT_LABELS();

    // Group by category
    const groups = {};
    for (const tool of tools) {
      const meta = toolMeta[tool.id] || { name: tool.id, desc: '', cat: 'Other', ctx: '?' };
      const cat = meta.cat;
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push({ ...tool, ...meta });
    }

    // Category order
    const catOrder = ['Code', 'Search', 'Documents', 'Media', 'Knowledge', 'Multi-Agent', 'Sessions', 'System', 'Other'];
    let html = '';
    for (const cat of catOrder) {
      const items = groups[cat];
      if (!items) continue;
      const enabledCount = items.filter(i => i.enabled).length;
      const totalCount = items.length;
      const catId = 'tool-cat-' + cat.replace(/[^a-zA-Z]/g, '');
      const allEnabled = enabledCount === totalCount;
      html += `<div class="admin-tool-category">
        <div class="admin-tool-cat-header" data-tool-cat="${catId}" style="cursor:pointer;display:flex;align-items:center;justify-content:space-between;">
          <span>${esc(catLabels[cat] || cat)}</span>
          <span style="display:flex;align-items:center;gap:6px;" class="admin-tool-cat-right">
            <span class="admin-tool-cat-count" style="font-size:10px;opacity:0.5;">${enabledCount}/${totalCount}</span>
            <label class="admin-switch" style="flex-shrink:0;">
              <input type="checkbox" data-tool-cat-toggle="${catId}" ${allEnabled ? 'checked' : ''}>
              <span class="admin-slider"></span>
            </label>
            <svg class="admin-tool-cat-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3;transition:transform 0.2s,opacity 0.2s;"><polyline points="6 9 12 15 18 9"/></svg>
          </span>
        </div>
        <div class="admin-tool-cat-body hidden" id="${catId}">`;
      const _ctxTitle = t('ui.js.admin_ctx_title', null, 'Approximate context tokens used');
      for (const tool of items) {
        html += `
        <div class="admin-tool-row">
          <div class="admin-tool-info">
            <span class="admin-tool-name">${esc(tool.name)}</span>
            <span class="admin-tool-desc">${esc(tool.desc)}</span>
          </div>
          <span class="admin-tool-ctx" title="${esc(_ctxTitle)}">${esc(tool.ctx)}</span>
          <label class="admin-switch" style="flex-shrink:0;">
            <input type="checkbox" data-tool-id="${esc(tool.id)}" ${tool.enabled ? 'checked' : ''}>
            <span class="admin-slider"></span>
          </label>
        </div>`;
      }
      html += '</div></div>';
    }
    list.innerHTML = html;

    // Prevent toggle clicks from expanding/collapsing
    list.querySelectorAll('.admin-tool-cat-right').forEach(span => {
      span.addEventListener('click', e => e.stopPropagation());
    });

    // Wire category expand/collapse
    list.querySelectorAll('[data-tool-cat]').forEach(header => {
      header.addEventListener('click', () => {
        const body = el(header.dataset.toolCat);
        if (!body) return;
        body.classList.toggle('hidden');
        const chevron = header.querySelector('.admin-tool-cat-chevron');
        const isOpen = !body.classList.contains('hidden');
        if (chevron) {
          chevron.style.transform = isOpen ? 'rotate(180deg)' : '';
          chevron.style.opacity = isOpen ? '0.7' : '0.3';
        }
      });
    });

    // Helper: save disabled tools + update counters
    async function _saveToolState() {
      const allChecks = list.querySelectorAll('input[data-tool-id]');
      const disabled = [];
      allChecks.forEach(c => { if (!c.checked) disabled.push(c.dataset.toolId); });
      await fetch('/api/tools', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disabled }),
        credentials: 'same-origin',
      });
    }
    function _updateCatCounter(catEl) {
      if (!catEl) return;
      const catChecks = catEl.querySelectorAll('input[data-tool-id]');
      const catEnabled = Array.from(catChecks).filter(c => c.checked).length;
      const counter = catEl.querySelector('.admin-tool-cat-count');
      if (counter) counter.textContent = catEnabled + '/' + catChecks.length;
      const catToggle = catEl.querySelector('input[data-tool-cat-toggle]');
      if (catToggle) catToggle.checked = (catEnabled === catChecks.length);
    }

    // Wire individual tool toggles
    list.querySelectorAll('input[data-tool-id]').forEach(chk => {
      chk.addEventListener('change', async () => {
        await _saveToolState();
        _updateCatCounter(chk.closest('.admin-tool-category'));
      });
    });

    // Wire category-level toggle (enable/disable all in category)
    list.querySelectorAll('input[data-tool-cat-toggle]').forEach(chk => {
      chk.addEventListener('change', async () => {
        const catEl = chk.closest('.admin-tool-category');
        if (!catEl) return;
        const checked = chk.checked;
        catEl.querySelectorAll('input[data-tool-id]').forEach(c => { c.checked = checked; });
        await _saveToolState();
        _updateCatCounter(catEl);
      });
    });
  } catch (e) {
    console.error('Failed to load tools:', e);
    list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_load_tools_failed', null, 'Failed to load tools') + '</div>';
  }
}

async function loadMcpServers() {
  const list = el('adm-mcpList');
  if (!list) return;  // MCP section not visible / not yet rendered
  try {
    const res = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
    const servers = await res.json();
    if (!servers.length) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_mcp_servers', null, 'No MCP servers configured') + '</div>'; return; }
    list.innerHTML = servers.map(s => {
      const statusColor = s.needs_oauth ? '#e5a33a' : s.status === 'connected' ? 'var(--fg)' : s.status === 'error' ? 'var(--red)' : 'color-mix(in srgb, var(--fg) 50%, transparent)';
      const toolInfo = s.status === 'connected' ? t('ui.js.admin_tools_enabled', { enabled: s.enabled_tool_count, total: s.tool_count }, '{enabled}/{total} tools enabled') : '';
      const statusText = s.needs_oauth ? t('ui.js.admin_needs_authorization', null, 'Needs authorization') : s.status === 'connected' ? t('ui.js.admin_connected_info', { info: toolInfo }, 'Connected ({info})') : s.status === 'error' ? t('ui.js.admin_error_info', { error: s.error || 'unknown' }, 'Error: {error}') : t('ui.js.admin_disconnected', null, 'Disconnected');
      const hasTools = s.status === 'connected' && s.tool_count > 0;
      return `<div class="admin-user-row" data-adm-mcp-id="${s.id}">
        <div style="display:flex;align-items:center;justify-content:space-between;${hasTools ? 'cursor:pointer;' : ''}padding:4px 0;" data-adm-mcp-header="${s.id}">
          <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;">
            <span class="admin-user-name">${esc(s.name)}</span>
            <span class="admin-badge" style="background:${statusColor}33;color:${statusColor}">${statusText}</span>
            ${hasTools ? `<span style="font-size:10px;opacity:0.4;">${t('ui.js.admin_click_manage_tools', null, 'Click to manage tools')}</span>` : ''}
          </div>
          <div style="display:flex;gap:4px;align-items:center;">
            ${s.needs_oauth ? `<a href="/api/mcp/oauth/authorize/${s.id}" target="_blank" class="admin-btn-sm" style="background:var(--red);color:#fff;text-decoration:none;padding:3px 10px;border-radius:4px;font-size:11px;font-weight:600;">${t('ui.js.admin_authorize', null, 'Authorize')}</a>` : ''}
            <button class="admin-btn-sm" data-adm-mcp-reconnect="${s.id}">${t('ui.js.admin_reconnect', null, 'Reconnect')}</button>
            <button class="admin-btn-delete" style="border-color:${s.is_enabled ? 'color-mix(in srgb, var(--red) 30%, transparent)' : 'color-mix(in srgb, var(--fg) 30%, transparent)'};color:${s.is_enabled ? 'var(--red)' : 'var(--fg)'};" data-adm-mcp-toggle="${s.id}" data-adm-mcp-enable="${!s.is_enabled}">${s.is_enabled ? t('ui.js.admin_disable', null, 'Disable') : t('ui.js.admin_enable', null, 'Enable')}</button>
            <button class="admin-btn-delete" data-adm-mcp-delete="${s.id}">${t('ui.js.admin_delete', null, 'Delete')}</button>
            ${hasTools ? '<svg class="admin-user-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.3;transition:transform 0.2s,opacity 0.2s;"><polyline points="6 9 12 15 18 9"/></svg>' : ''}
          </div>
        </div>
        ${hasTools ? `<div class="mcp-tools-panel hidden" data-adm-mcp-tools-panel="${s.id}"></div>` : ''}
      </div>`;
    }).join('');
    list.querySelectorAll('[data-adm-mcp-reconnect]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const msg = el('adm-mcpMsg'); msg.textContent = t('ui.js.admin_reconnecting', null, 'Reconnecting...'); msg.className = '';
        try {
          const res = await fetch(`/api/mcp/servers/${btn.dataset.admMcpReconnect}/reconnect`, { method: 'POST', credentials: 'same-origin' });
          const data = await res.json();
          msg.textContent = data.connected ? t('ui.js.admin_reconnected', { count: data.tool_count }, 'Reconnected ({count} tools)') : t('ui.js.admin_failed_error', { error: data.error || 'unknown' }, 'Failed: {error}');
          msg.className = data.connected ? 'admin-success' : 'admin-error';
          loadMcpServers();
        } catch (e) { msg.textContent = t('ui.js.admin_failed_error', { error: e.message }, 'Failed: {error}'); msg.className = 'admin-error'; }
      });
    });
    list.querySelectorAll('[data-adm-mcp-toggle]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const fd = new FormData(); fd.append('is_enabled', btn.dataset.admMcpEnable);
        await fetch(`/api/mcp/servers/${btn.dataset.admMcpToggle}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
        loadMcpServers();
      });
    });
    list.querySelectorAll('[data-adm-mcp-delete]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!await uiModule.styledConfirm(t('ui.js.admin_delete_mcp_confirm', null, 'Delete this MCP server?'), { confirmText: t('ui.js.admin_delete', null, 'Delete'), danger: true })) return;
        await fetch(`/api/mcp/servers/${btn.dataset.admMcpDelete}`, { method: 'DELETE', credentials: 'same-origin' });
        loadMcpServers();
      });
    });
    // Tools expand/collapse (click anywhere on card)
    list.querySelectorAll('[data-adm-mcp-id]').forEach(row => {
      const header = row.querySelector('[data-adm-mcp-header]');
      if (!header) return;
      let _toolsLoaded = false;
      row.style.cursor = 'pointer';
      row.addEventListener('click', async (e) => {
        if (e.target.closest('.admin-btn-sm, .admin-btn-delete, a, .mcp-tools-list, .mcp-tools-header')) return;
        const sid = header.dataset.admMcpHeader;
        const panel = row.querySelector(`[data-adm-mcp-tools-panel="${sid}"]`);
        if (!panel) return;
        panel.classList.toggle('hidden');
        const chevron = row.querySelector('.admin-user-chevron');
        const isOpen = !panel.classList.contains('hidden');
        if (chevron) {
          chevron.style.transform = isOpen ? 'rotate(180deg)' : '';
          chevron.style.opacity = isOpen ? '0.7' : '0.3';
        }
        if (!_toolsLoaded && isOpen) {
          _toolsLoaded = true;
          panel.innerHTML = '<span style="opacity:0.5;font-size:11px;">' + t('ui.js.admin_loading_tools', null, 'Loading tools...') + '</span>';
          try {
            const res = await fetch(`/api/mcp/servers/${sid}/tools`, { credentials: 'same-origin' });
            const tools = await res.json();
            if (!tools.length) { panel.innerHTML = '<span style="opacity:0.5;font-size:11px;">' + t('ui.js.admin_no_tools', null, 'No tools') + '</span>'; return; }
            const disabled = new Set(tools.filter(t => t.is_disabled).map(t => t.name));
            panel.innerHTML = `<div class="mcp-tools-header">
              <span>${t('ui.js.admin_tools_header', null, 'Tools')}</span>
              <span style="display:flex;gap:8px;align-items:center;">
                <span class="mcp-tools-count">${t('ui.js.admin_n_enabled', { enabled: tools.length - disabled.size, total: tools.length }, '{enabled}/{total} enabled')}</span>
                <a href="#" data-mcp-select-all="${sid}">${t('ui.js.admin_all', null, 'All')}</a>
                <a href="#" data-mcp-select-none="${sid}">${t('ui.js.admin_none', null, 'None')}</a>
              </span>
            </div><div class="mcp-tools-list">` + tools.map(t =>
              `<label title="${esc(t.description)}">
                <input type="checkbox" data-mcp-tool-name="${esc(t.name)}" ${!t.is_disabled ? 'checked' : ''}>
                <span><strong>${esc(t.name)}</strong> <span style="opacity:0.5;">— ${esc((t.description || '').slice(0, 80))}</span></span>
              </label>`
            ).join('') + '</div>';
            panel.querySelector(`[data-mcp-select-all="${sid}"]`)?.addEventListener('click', (e) => {
              e.preventDefault();
              panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true);
              _saveMcpToolState(sid, panel);
            });
            panel.querySelector(`[data-mcp-select-none="${sid}"]`)?.addEventListener('click', (e) => {
              e.preventDefault();
              panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false);
              _saveMcpToolState(sid, panel);
            });
            panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
              cb.addEventListener('change', () => _saveMcpToolState(sid, panel));
            });
          } catch (e) { panel.innerHTML = '<span class="admin-error" style="font-size:11px;">' + t('ui.js.admin_load_tools_failed', null, 'Failed to load tools') + '</span>'; }
        }
      });
    });
  } catch (e) { if (list) list.innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_mcp_failed', null, 'Failed to load MCP servers') + '</div>'; }
}

async function _saveMcpToolState(serverId, panel) {
  const disabled = [];
  panel.querySelectorAll('input[type=checkbox]').forEach(cb => {
    if (!cb.checked) disabled.push(cb.dataset.mcpToolName);
  });
  const total = panel.querySelectorAll('input[type=checkbox]').length;
  try {
    await fetch(`/api/mcp/servers/${serverId}/tools`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ disabled }),
    });
    // Update the count label in the panel
    const countLabel = panel.querySelector('.mcp-tools-count');
    if (countLabel) countLabel.textContent = t('ui.js.admin_n_enabled', { enabled: total - disabled.length, total }, '{enabled}/{total} enabled');
    // Update badge in the server row
    const row = panel.closest('[data-adm-mcp-id]');
    if (row) {
      const badge = row.querySelector('.admin-badge');
      if (badge) badge.textContent = t('ui.js.admin_connected_info', { info: t('ui.js.admin_tools_enabled', { enabled: total - disabled.length, total }, '{enabled}/{total} tools enabled') }, 'Connected ({info})');
    }
  } catch (e) { /* silent */ }
}

function initMcpForm() {
  const cmdEl = el('adm-mcpCommand');
  if (!cmdEl) return;  // MCP form not present in this build — nothing to wire
  const transportSel = el('adm-mcpTransport');
  const sseRow = el('adm-mcpSseRow');
  const envRow = el('adm-mcpEnvRow');
  const envFieldsWrap = el('adm-mcpEnvFields');
  const helpBox = el('adm-mcpHelp');
  const cmdRow = cmdEl.parentElement;
  let _activeHelp = null;
  let _envKeys = []; // track which env keys have dedicated fields
  let _activeOauthFile = null; // preset oauthFile config (for Google servers)
  let _activeOauth = null;     // preset OAuth flow config (provider, scopes, etc.)

  function _clearEnvFields() {
    envFieldsWrap.innerHTML = '';
    _envKeys = [];
    envRow.style.display = 'none';
    el('adm-mcpEnv').value = '';
    _activeOauth = null;
  }

  function _buildEnvFields(envObj, help, preset) {
    _clearEnvFields();
    const keys = Object.keys(envObj);
    if (!keys.length) return;
    _envKeys = keys;

    // Provider dropdown (e.g. for Email IMAP/SMTP)
    if (preset?.providerDropdown) {
      const pd = preset.providerDropdown;
      const row = document.createElement('div');
      row.className = 'admin-model-form-row';
      row.style.cssText = 'gap:6px;align-items:center;';
      const label = document.createElement('span');
      label.style.cssText = 'font-size:11px;opacity:0.55;min-width:0;white-space:nowrap;';
      label.textContent = pd.label || t('ui.js.admin_provider_label', null, 'Provider');
      const select = document.createElement('select');
      select.style.cssText = 'flex:1;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-secondary);color:var(--text-primary);font-size:12px;';
      pd.options.forEach((opt, i) => {
        const o = document.createElement('option');
        o.value = i;
        o.textContent = opt.name;
        select.appendChild(o);
      });
      select.addEventListener('change', () => {
        const opt = pd.options[parseInt(select.value)];
        for (const [envKey, field] of Object.entries(pd.targets)) {
          const inp = envFieldsWrap.querySelector(`.mcp-env-input[data-env-key="${envKey}"]`);
          if (inp) inp.value = opt[field] || '';
        }
      });
      row.appendChild(label);
      row.appendChild(select);
      envFieldsWrap.appendChild(row);
      // Auto-fill with first provider after inputs are created
      setTimeout(() => {
        const first = pd.options[0];
        for (const [envKey, field] of Object.entries(pd.targets)) {
          const inp = envFieldsWrap.querySelector(`.mcp-env-input[data-env-key="${envKey}"]`);
          if (inp && !inp.value) inp.value = first[field] || '';
        }
      }, 0);
    }

    for (const key of keys) {
      const row = document.createElement('div');
      row.className = 'admin-model-form-row';
      row.style.cssText = 'gap:6px;align-items:center;';
      const label = document.createElement('span');
      label.style.cssText = 'font-size:11px;opacity:0.55;min-width:0;white-space:nowrap;';
      label.textContent = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const input = document.createElement('input');
      input.type = key.toLowerCase().includes('secret') || key.toLowerCase().includes('token') || key.toLowerCase().includes('key') || key.toLowerCase().includes('password') ? 'password' : 'text';
      input.placeholder = key;
      input.dataset.envKey = key;
      input.className = 'mcp-env-input';
      input.style.cssText = 'flex:1;';
      if (envObj[key]) input.value = envObj[key];
      row.appendChild(label);
      row.appendChild(input);
      envFieldsWrap.appendChild(row);
    }
    // Help toggle link
    if (help) {
      _activeHelp = help;
      const helpLink = document.createElement('a');
      helpLink.textContent = t('ui.js.admin_how_get_these', null, 'How do I get these?');
      helpLink.href = '#';
      helpLink.style.cssText = 'font-size:10.5px;opacity:0.5;margin-top:2px;display:inline-block;';
      helpLink.addEventListener('click', (e) => {
        e.preventDefault();
        helpBox.style.display = helpBox.style.display === 'none' ? '' : 'none';
      });
      envFieldsWrap.appendChild(helpLink);
      helpBox.textContent = help;
      helpBox.style.display = 'none';
    } else {
      _activeHelp = null;
      helpBox.style.display = 'none';
    }
  }

  // Collect env from either dedicated fields or raw JSON fallback
  function _collectEnv() {
    if (_envKeys.length) {
      const obj = {};
      envFieldsWrap.querySelectorAll('.mcp-env-input').forEach(inp => {
        if (inp.value.trim()) obj[inp.dataset.envKey] = inp.value.trim();
      });
      return JSON.stringify(obj);
    }
    return el('adm-mcpEnv').value.trim() || '{}';
  }

  transportSel.addEventListener('change', () => {
    const isSse = transportSel.value === 'sse';
    sseRow.style.display = isSse ? '' : 'none';
    cmdRow.style.display = isSse ? 'none' : '';
    if (isSse) { _clearEnvFields(); helpBox.style.display = 'none'; }
  });

  // Preset catalog
  const presetSel = el('adm-mcpPreset');
  if (presetSel) {
    MCP_PRESETS.forEach((p, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = p.name + (Object.keys(p.env).length ? '  ' + t('ui.js.admin_requires_keys', null, '(requires keys)') : '');
      presetSel.appendChild(opt);
    });
    presetSel.addEventListener('change', () => {
      if (presetSel.value === '') return;
      const p = MCP_PRESETS[parseInt(presetSel.value)];
      el('adm-mcpName').value = p.name.toLowerCase().replace(/\s+/g, '-');
      transportSel.value = 'stdio';
      el('adm-mcpCommand').value = p.command;
      el('adm-mcpArgs').value = JSON.stringify(p.args);
      sseRow.style.display = 'none';
      cmdRow.style.display = '';
      _buildEnvFields(p.env, p.help || null, p);
      _activeOauthFile = p.oauthFile || null;
      _activeOauth = p.oauth || null;
      presetSel.value = '';
      // Focus first env field if keys are needed
      const firstInput = envFieldsWrap.querySelector('.mcp-env-input');
      if (firstInput) firstInput.focus();
      else el('adm-mcpAddBtn').focus();
    });
  }

  el('adm-mcpAddBtn').addEventListener('click', async () => {
    const name = el('adm-mcpName').value.trim();
    const transport = transportSel.value;
    const command = el('adm-mcpCommand').value.trim();
    const args = el('adm-mcpArgs').value.trim() || '[]';
    const env = _collectEnv();
    const url = el('adm-mcpUrl').value.trim();
    const msg = el('adm-mcpMsg');
    if (!name) { msg.textContent = t('ui.js.admin_name_required', null, 'Name is required'); msg.className = 'admin-error'; return; }
    if (transport === 'stdio' && !command) { msg.textContent = t('ui.js.admin_command_required', null, 'Command is required for stdio'); msg.className = 'admin-error'; return; }
    if (transport === 'sse' && !url) { msg.textContent = t('ui.js.admin_url_required_sse', null, 'URL is required for SSE'); msg.className = 'admin-error'; return; }
    try { JSON.parse(env); } catch { msg.textContent = t('ui.js.admin_env_json', null, 'Env must be valid JSON'); msg.className = 'admin-error'; return; }
    const fd = new FormData();
    fd.append('name', name); fd.append('transport', transport); fd.append('command', command); fd.append('args', args); fd.append('env', env); fd.append('url', url);
    // If preset has oauthFile config, send credentials for file generation
    if (_activeOauthFile) {
      const envObj = JSON.parse(env);
      fd.append('oauth_file', JSON.stringify({
        dir: _activeOauthFile.dir,
        filename: _activeOauthFile.filename,
        client_id: envObj.GOOGLE_CLIENT_ID || '',
        client_secret: envObj.GOOGLE_CLIENT_SECRET || '',
      }));
    }
    // If preset has OAuth flow config, send it so the server can handle authorization
    if (_activeOauth) {
      fd.append('oauth_config', JSON.stringify(_activeOauth));
    }
    msg.textContent = t('ui.js.admin_adding', null, 'Adding...'); msg.className = '';
    try {
      const res = await fetch('/api/mcp/servers', { method: 'POST', body: fd, credentials: 'same-origin' });
      const data = await res.json();
      if (data.needs_oauth) {
        msg.innerHTML = t('ui.js.admin_added_authorize_google', { name: esc(name), link: `<a href="/api/mcp/oauth/authorize/${data.id}" target="_blank" style="color:var(--red);font-weight:600;">${t('ui.js.admin_authorize_with_google', null, 'Authorize with Google')}</a>` }, 'Added {name} — {link} to connect');
        msg.className = 'admin-success';
      } else if (data.connected) {
        msg.textContent = t('ui.js.admin_added_tools_discovered', { name, count: data.tool_count }, 'Added {name} ({count} tools discovered)'); msg.className = 'admin-success';
      } else { msg.textContent = t('ui.js.admin_added_connection_failed', { error: data.error || 'unknown' }, 'Added but connection failed: {error}'); msg.className = 'admin-error'; }
      el('adm-mcpName').value = ''; el('adm-mcpCommand').value = ''; el('adm-mcpArgs').value = ''; el('adm-mcpUrl').value = '';
      _clearEnvFields(); helpBox.style.display = 'none'; _activeHelp = null; _activeOauthFile = null; _activeOauth = null;
      loadMcpServers();
    } catch (e) { msg.textContent = t('ui.js.admin_failed_error', { error: e.message }, 'Failed: {error}'); msg.className = 'admin-error'; }
  });
}

/* ── Embedding model ──
   No settings UI: the embedding model (RAG, semantic memory, tool selection)
   is fixed infrastructure that ships with the app, and swapping it would
   invalidate every existing vector. Configure via the FASTEMBED_MODEL /
   EMBEDDING_URL env vars if you really need to override it. */

/* ── RAG ── */
async function loadRag() {
  try {
    const res = await fetch('/api/personal');
    const data = await res.json();
    const dirList = el('adm-ragDirList');
    const dirs = data.directories || [];
    if (dirs.length === 0) { dirList.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_dirs_indexed', null, 'No directories indexed') + '</div>'; }
    else {
      dirList.innerHTML = dirs.map(d => `<div class="admin-rag-item"><span class="admin-rag-item-name" title="${esc(d)}">${esc(d)}</span><button class="admin-btn-delete" data-adm-rag-dir="${esc(d)}">${t('ui.js.admin_remove', null, 'Remove')}</button></div>`).join('');
      dirList.querySelectorAll('[data-adm-rag-dir]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!await uiModule.styledConfirm(t('ui.js.admin_remove_dir_confirm', { dir: btn.dataset.admRagDir }, 'Remove directory "{dir}" from RAG?'), { confirmText: t('ui.js.admin_remove', null, 'Remove'), danger: true })) return;
          btn.disabled = true; btn.textContent = '...';
          try {
            const res = await fetch('/api/personal/remove_directory?directory=' + encodeURIComponent(btn.dataset.admRagDir), { method: 'DELETE' });
            if (res.ok) { ragMsg(t('ui.js.admin_dir_removed', null, 'Directory removed')); loadRag(); }
            else { const e = await res.json(); ragMsg(e.detail || t('ui.js.admin_failed', null, 'Failed'), true); }
          } catch (e) { ragMsg(t('ui.js.admin_error_info', { error: e.message }, 'Error: {error}'), true); }
        });
      });
    }
    const fileList = el('adm-ragFileList');
    const files = data.files || [];
    if (files.length === 0) { fileList.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_files_indexed', null, 'No files indexed') + '</div>'; }
    else {
      fileList.innerHTML = files.map(f => {
        const size = f.size ? (f.size > 1024 ? (f.size / 1024).toFixed(1) + ' KB' : f.size + ' B') : '';
        return `<div class="admin-rag-item"><span class="admin-rag-item-name" title="${esc(f.path || f.name)}">${esc(f.name)}</span><span class="admin-rag-item-meta">${size}</span><button class="admin-btn-delete" data-adm-rag-file="${esc(f.path || f.name)}">${t('ui.js.admin_delete', null, 'Delete')}</button></div>`;
      }).join('');
      fileList.querySelectorAll('[data-adm-rag-file]').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!await uiModule.styledConfirm(t('ui.js.admin_delete_file_confirm', { file: btn.dataset.admRagFile }, 'Delete "{file}" from RAG?'), { confirmText: t('ui.js.admin_delete', null, 'Delete'), danger: true })) return;
          btn.disabled = true; btn.textContent = '...';
          try {
            const res = await fetch('/api/personal/file?filepath=' + encodeURIComponent(btn.dataset.admRagFile), { method: 'DELETE' });
            if (res.ok) { ragMsg(t('ui.js.admin_file_removed', null, 'File removed')); loadRag(); }
            else { const e = await res.json(); ragMsg(e.detail || t('ui.js.admin_failed', null, 'Failed'), true); }
          } catch (e) { ragMsg(t('ui.js.admin_error_info', { error: e.message }, 'Error: {error}'), true); }
        });
      });
    }
  } catch (e) {
    el('adm-ragDirList').innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_failed', null, 'Failed to load') + '</div>';
    el('adm-ragFileList').innerHTML = '';
  }
}

let _ragMsgTimer = null;
function ragMsg(text, isError, persist) {
  const s = el('adm-ragStatus');
  s.textContent = text; s.style.color = isError ? 'var(--red)' : 'var(--fg)';
  if (_ragMsgTimer) { clearTimeout(_ragMsgTimer); _ragMsgTimer = null; }
  if (text && !persist) _ragMsgTimer = setTimeout(() => { s.textContent = ''; }, 5000);
}

async function ragUpload(files) {
  if (!files || files.length === 0) return;
  ragMsg(t('ui.js.admin_uploading_files', { count: files.length }, 'Uploading {count} file(s)...'), false, true);
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  try {
    const res = await fetch('/api/personal/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.success) { ragMsg(t('ui.js.admin_uploaded_indexed', { files: data.uploaded.length, chunks: data.indexed_count }, 'Uploaded {files} file(s), {chunks} chunks indexed')); loadRag(); }
    else ragMsg(data.detail || t('ui.js.admin_upload_failed', null, 'Upload failed'), true);
  } catch (e) { ragMsg(t('ui.js.admin_upload_error', { error: e.message }, 'Upload error: {error}'), true); }
}

function initRag() {
  const dropZone = el('adm-ragDropZone');
  const fileInput = el('adm-ragFileInput');
  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => ragUpload(fileInput.files));
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => { e.preventDefault(); dropZone.classList.remove('dragover'); ragUpload(e.dataTransfer.files); });
  el('adm-ragAddDirBtn').addEventListener('click', async () => {
    const dir = el('adm-ragDirInput').value.trim();
    if (!dir) return;
    const btn = el('adm-ragAddDirBtn');
    btn.disabled = true; btn.textContent = t('ui.js.admin_indexing', null, 'Indexing...');
    try {
      const res = await fetch('/api/personal/add_directory', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ directory: dir }) });
      const data = await res.json();
      if (data.success) { ragMsg(t('ui.js.admin_indexed_chunks', { count: data.indexed_count }, 'Indexed {count} chunks from directory')); el('adm-ragDirInput').value = ''; loadRag(); }
      else ragMsg(data.detail || data.message || t('ui.js.admin_failed', null, 'Failed'), true);
    } catch (e) { ragMsg(t('ui.js.admin_error_info', { error: e.message }, 'Error: {error}'), true); }
    btn.disabled = false; btn.textContent = t('ui.js.admin_add_directory', null, 'Add Directory');
  });
  el('adm-ragReloadBtn').addEventListener('click', async () => {
    const btn = el('adm-ragReloadBtn');
    btn.disabled = true; btn.textContent = t('ui.js.admin_reloading', null, 'Reloading...');
    try {
      const res = await fetch('/api/personal/reload', { method: 'POST' });
      const data = await res.json();
      ragMsg(t('ui.js.admin_index_reloaded', { count: data.count }, 'Index reloaded: {count} documents'));
      loadRag();
    } catch (e) { ragMsg(t('ui.js.admin_reload_failed', { error: e.message }, 'Reload failed: {error}'), true); }
    btn.disabled = false; btn.textContent = t('ui.js.admin_reload_index', null, 'Reload Index');
  });
}

/* ═══════════════════════════════════════════
   SYSTEM TAB — Tokens
   ═══════════════════════════════════════════ */
async function loadTokens() {
  const list = el('adm-tokenList');
  try {
    const res = await fetch('/api/tokens', { credentials: 'same-origin' });
    const tokens = await res.json();
    if (!tokens.length) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_tokens', null, 'No API tokens') + '</div>'; return; }
    const _scopesTitle = t('ui.js.admin_scopes_title', null, 'Allowed API scopes');
    const _neverUsed = t('ui.js.admin_never_used', null, 'Never used');
    const _revoke = t('ui.js.admin_revoke', null, 'Revoke');
    const _ownerLbl = (owner) => t('ui.js.admin_owner', { owner }, 'Owner: {owner}');
    const _lastUsedLbl = (when) => t('ui.js.admin_last_used', { when }, 'Last used: {when}');
    list.innerHTML = tokens.map(tk => `
      <div class="admin-user-row">
        <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;">
          <span class="admin-user-name">${esc(tk.name)}</span>
          <span class="admin-badge">${esc(tk.token_prefix)}...</span>
          <span class="admin-badge" title="${esc(_scopesTitle)}">${esc((tk.scopes || ['chat']).join(', '))}</span>
          ${tk.owner ? `<span style="font-size:0.75rem;opacity:0.5;">${esc(_ownerLbl(tk.owner))}</span>` : ''}
          ${tk.last_used_at ? `<span style="font-size:0.75rem;opacity:0.5;">${esc(_lastUsedLbl(new Date(tk.last_used_at).toLocaleDateString()))}</span>` : `<span style="font-size:0.75rem;opacity:0.4;">${esc(_neverUsed)}</span>`}
        </div>
        <button class="admin-btn-delete" data-adm-del-token="${tk.id}">${esc(_revoke)}</button>
      </div>`).join('');
    list.querySelectorAll('[data-adm-del-token]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!await uiModule.styledConfirm(t('ui.js.admin_revoke_token_confirm', null, 'Revoke this API token? External integrations using it will stop working.'), { confirmText: t('ui.js.admin_revoke', null, 'Revoke'), danger: true })) return;
        await fetch(`/api/tokens/${btn.dataset.admDelToken}`, { method: 'DELETE', credentials: 'same-origin' });
        loadTokens();
      });
    });
  } catch (e) { list.innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_tokens_failed', null, 'Failed to load tokens') + '</div>'; }
}

function initTokenForm() {
  const addBtn = el('adm-tokenAddBtn');
  if (!addBtn || addBtn.dataset.bound) return;
  addBtn.dataset.bound = '1';
  addBtn.addEventListener('click', async () => {
    const msg = el('adm-tokenMsg');
    const reveal = el('adm-tokenReveal');
    msg.textContent = ''; msg.className = ''; reveal.style.display = 'none';
    const name = el('adm-tokenName').value.trim();
    if (!name) { msg.textContent = t('ui.js.admin_token_name_required', null, 'Token name is required'); msg.className = 'admin-error'; return; }
    const fd = new FormData(); fd.append('name', name);
    const scopes = (el('adm-tokenScopes')?.value || '').trim();
    if (scopes) fd.append('scopes', scopes);
    try {
      const res = await fetch('/api/tokens', { method: 'POST', body: fd, credentials: 'same-origin' });
      const data = await res.json();
      if (res.ok) {
        el('adm-tokenValue').textContent = data.token;
        reveal.style.display = '';
        el('adm-tokenName').value = '';
        if (el('adm-tokenScopes')) el('adm-tokenScopes').value = '';
        loadTokens();
      }
      else { msg.textContent = data.detail || t('ui.js.admin_failed', null, 'Failed'); msg.className = 'admin-error'; }
    } catch (e) { msg.textContent = t('ui.js.admin_request_failed', null, 'Request failed'); msg.className = 'admin-error'; }
  });
  el('adm-tokenCopyBtn').addEventListener('click', () => {
    const val = el('adm-tokenValue').textContent;
    navigator.clipboard.writeText(val).then(() => {
      el('adm-tokenCopyBtn').textContent = t('ui.js.admin_copied_excl', null, 'Copied!');
      setTimeout(() => { el('adm-tokenCopyBtn').textContent = t('ui.js.admin_copy', null, 'Copy'); }, 2000);
    });
  });
}

/* ── Webhooks ── */
async function loadWebhooks() {
  const list = el('adm-whList');
  try {
    const res = await fetch('/api/webhooks', { credentials: 'same-origin' });
    const hooks = await res.json();
    if (!hooks.length) { list.innerHTML = '<div class="admin-empty">' + t('ui.js.admin_no_webhooks', null, 'No webhooks configured') + '</div>'; return; }
    list.innerHTML = hooks.map(w => {
      const events = (w.events || []).map(e => `<span class="admin-badge">${esc(e)}</span>`).join(' ');
      const statusBadge = w.last_status_code
        ? `<span class="admin-badge" style="background:${w.last_status_code < 400 ? 'color-mix(in srgb, var(--fg) 20%, transparent)' : 'color-mix(in srgb, var(--red) 20%, transparent)'};color:${w.last_status_code < 400 ? 'var(--fg)' : 'var(--red)'};">${w.last_status_code}</span>`
        : '';
      const lastTriggered = w.last_triggered_at ? new Date(w.last_triggered_at).toLocaleString() : t('ui.js.admin_never', null, 'Never');
      const errorText = w.last_error ? `<div style="font-size:0.75rem;color:var(--red);margin-top:0.2rem;">${t('ui.js.admin_error_info', { error: esc(w.last_error.substring(0, 80)) }, 'Error: {error}')}</div>` : '';
      return `
        <div class="admin-ep-item" style="flex-wrap:wrap;">
          <div class="admin-ep-info" style="flex:1;min-width:200px;">
            <div class="admin-ep-name">${esc(w.name)} ${w.is_active ? '' : '<span class="admin-badge admin-badge-off">' + t('ui.js.admin_disabled_badge', null, 'disabled') + '</span>'} ${w.has_secret ? '<span class="admin-badge">' + t('ui.js.admin_signed_badge', null, 'signed') + '</span>' : ''}</div>
            <div class="admin-ep-detail">${esc(w.url)}</div>
            <div style="margin-top:0.3rem;">${events}</div>
            <div class="admin-ep-detail">${t('ui.js.admin_last_label', { when: lastTriggered }, 'Last: {when}')} ${statusBadge}</div>
            ${errorText}
          </div>
          <div class="admin-ep-actions">
            <button class="admin-btn-sm" data-adm-wh-test="${w.id}">${t('ui.js.admin_test', null, 'Test')}</button>
            <button class="admin-btn-sm" data-adm-wh-toggle="${w.id}">${w.is_active ? t('ui.js.admin_disable', null, 'Disable') : t('ui.js.admin_enable', null, 'Enable')}</button>
            <button class="admin-btn-delete" data-adm-wh-delete="${w.id}">${t('ui.js.admin_delete', null, 'Delete')}</button>
          </div>
        </div>`;
    }).join('');
    list.querySelectorAll('[data-adm-wh-test]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const msg = el('adm-whMsg'); msg.textContent = t('ui.js.admin_sending_test', null, 'Sending test...'); msg.className = '';
        try {
          const res = await fetch(`/api/webhooks/${btn.dataset.admWhTest}/test`, { method: 'POST', credentials: 'same-origin' });
          msg.textContent = res.ok ? t('ui.js.admin_test_sent', null, 'Test sent!') : t('ui.js.admin_test_failed_short', null, 'Test failed'); msg.className = res.ok ? 'admin-success' : 'admin-error';
          setTimeout(() => loadWebhooks(), 1000);
        } catch (e) { msg.textContent = t('ui.js.admin_failed_error', { error: e.message }, 'Failed: {error}'); msg.className = 'admin-error'; }
      });
    });
    list.querySelectorAll('[data-adm-wh-toggle]').forEach(btn => {
      btn.addEventListener('click', async () => { await fetch(`/api/webhooks/${btn.dataset.admWhToggle}`, { method: 'PATCH', credentials: 'same-origin' }); loadWebhooks(); });
    });
    list.querySelectorAll('[data-adm-wh-delete]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!await uiModule.styledConfirm(t('ui.js.admin_delete_webhook_confirm', null, 'Delete this webhook?'), { confirmText: t('ui.js.admin_delete', null, 'Delete'), danger: true })) return;
        await fetch(`/api/webhooks/${btn.dataset.admWhDelete}`, { method: 'DELETE', credentials: 'same-origin' }); loadWebhooks();
      });
    });
  } catch (e) { list.innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_webhooks_failed', null, 'Failed to load webhooks') + '</div>'; }
}

function initWebhookForm() {
  el('adm-whAddBtn').addEventListener('click', async () => {
    const msg = el('adm-whMsg');
    msg.textContent = ''; msg.className = '';
    const name = el('adm-whName').value.trim();
    const url = el('adm-whUrl').value.trim();
    const secret = el('adm-whSecret').value.trim();
    const events = Array.from(modalEl.querySelectorAll('.adm-wh-event:checked')).map(e => e.value).join(',');
    if (!name) { msg.textContent = t('ui.js.admin_name_required', null, 'Name is required'); msg.className = 'admin-error'; return; }
    if (!url) { msg.textContent = t('ui.js.admin_url_required', null, 'URL is required'); msg.className = 'admin-error'; return; }
    if (!events) { msg.textContent = t('ui.js.admin_select_event', null, 'Select at least one event'); msg.className = 'admin-error'; return; }
    const fd = new FormData();
    fd.append('name', name); fd.append('url', url); fd.append('secret', secret); fd.append('events', events);
    try {
      const res = await fetch('/api/webhooks', { method: 'POST', body: fd, credentials: 'same-origin' });
      if (res.ok) { msg.textContent = t('ui.js.admin_webhook_added', null, 'Webhook added'); msg.className = 'admin-success'; el('adm-whName').value = ''; el('adm-whUrl').value = ''; el('adm-whSecret').value = ''; loadWebhooks(); }
      else { const d = await res.json(); msg.textContent = d.detail || t('ui.js.admin_failed', null, 'Failed'); msg.className = 'admin-error'; }
    } catch (e) { msg.textContent = t('ui.js.admin_failed_error', { error: e.message }, 'Failed: {error}'); msg.className = 'admin-error'; }
  });
}

/* ── Features ── */
// Lazy — see PRIV_LABELS note (resolves at render time, not module load).
const featureLabels = () => ({
  web_search: t('ui.js.admin_feat_web_search', null, 'Web Search'), deep_research: t('ui.js.admin_feat_deep_research', null, 'Deep Research'),
  memory: t('ui.js.admin_feat_memory', null, 'Memory'), document_editor: t('ui.js.admin_feat_document_editor', null, 'Document Editor'), rag: t('ui.js.admin_feat_rag', null, 'RAG Knowledge Base'), sensitive_filter: t('ui.js.admin_feat_sensitive_filter', null, 'Sensitive Info Filter'),
  gallery: t('ui.js.admin_feat_gallery', null, 'Gallery')
});

async function loadFeatures() {
  const container = el('adm-featureToggles');
  try {
    const res = await fetch('/api/auth/features', { credentials: 'same-origin' });
    const features = await res.json();
    container.innerHTML = Object.entries(featureLabels()).map(([key, label]) => `
      <div class="admin-toggle-row" style="padding:0.4rem 0;border-bottom:1px solid var(--border);">
        <div class="admin-toggle-label">${label}</div>
        <label class="admin-switch"><input type="checkbox" data-adm-feature="${key}" ${features[key] ? 'checked' : ''}><span class="admin-slider"></span></label>
      </div>`).join('');
    container.querySelectorAll('input[data-adm-feature]').forEach(toggle => {
      toggle.addEventListener('change', async () => {
        const body = {}; body[toggle.dataset.admFeature] = toggle.checked;
        await fetch('/api/auth/features', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      });
    });
  } catch (e) { container.innerHTML = '<div class="admin-error">' + t('ui.js.admin_load_features_failed', null, 'Failed to load features') + '</div>'; }
}

/* ── CalDAV Config ── */
function initCalDAV() {
  const urlIn = el('caldav-url');
  const userIn = el('caldav-user');
  const passIn = el('caldav-pass');
  const saveBtn = el('caldav-save-btn');
  const testBtn = el('caldav-test-btn');
  const status = el('caldav-status');
  if (!urlIn || !saveBtn) return;

  // Load current config
  fetch(`${API_BASE}/api/calendar/config`, { credentials: 'same-origin' })
    .then(r => r.json()).then(d => {
      urlIn.value = d.caldav_url || '';
      userIn.value = d.caldav_username || '';
      passIn.value = d.caldav_password || '';
    }).catch(() => {});

  saveBtn.addEventListener('click', async () => {
    status.textContent = t('ui.js.admin_saving', null, 'Saving...');
    try {
      const res = await fetch(`${API_BASE}/api/calendar/config`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caldav_url: urlIn.value, caldav_username: userIn.value, caldav_password: passIn.value }),
      });
      const d = await res.json();
      status.textContent = d.ok ? t('ui.js.admin_saved', null, 'Saved') : t('ui.js.admin_error', null, 'Error');
      status.style.color = d.ok ? 'var(--green)' : 'var(--red)';
    } catch (e) { status.textContent = t('ui.js.admin_error', null, 'Error'); status.style.color = 'var(--red)'; }
    setTimeout(() => { status.textContent = ''; status.style.color = ''; }, 3000);
  });

  testBtn.addEventListener('click', async () => {
    status.textContent = t('ui.js.admin_testing', null, 'Testing...');
    try {
      // Save first
      await fetch(`${API_BASE}/api/calendar/config`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caldav_url: urlIn.value, caldav_username: userIn.value, caldav_password: passIn.value }),
      });
      const res = await fetch(`${API_BASE}/api/calendar/test`, { method: 'POST', credentials: 'same-origin' });
      const d = await res.json();
      status.textContent = d.ok ? t('ui.js.admin_connected_calendars', { count: d.calendars }, 'Connected ({count} calendars)') : t('ui.js.admin_failed_error', { error: d.error }, 'Failed: {error}');
      status.style.color = d.ok ? 'var(--green)' : 'var(--red)';
    } catch (e) { status.textContent = t('ui.js.admin_error', null, 'Error'); status.style.color = 'var(--red)'; }
    setTimeout(() => { status.textContent = ''; status.style.color = ''; }, 5000);
  });
}

/* ── Data Backup (export/import) ── */
function initBackup() {
  el('adm-exportDataBtn').addEventListener('click', async () => {
    const btn = el('adm-exportDataBtn');
    const msg = el('adm-backupMsg');
    btn.disabled = true; btn.textContent = t('ui.js.admin_exporting', null, 'Exporting...'); msg.textContent = '';
    try {
      const res = await fetch('/api/export', { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const disposition = res.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename=(.+)/);
      const filename = match ? match[1] : 'odysseus_backup.json';
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
      msg.textContent = t('ui.js.admin_export_downloaded', null, 'Export downloaded.'); msg.className = 'admin-success';
    } catch (e) { msg.textContent = t('ui.js.admin_export_failed', { error: e.message }, 'Export failed: {error}'); msg.className = 'admin-error'; }
    btn.disabled = false; btn.textContent = t('ui.js.admin_export_data', null, 'Export Data');
  });

  const fileInput = el('adm-importFile');
  el('adm-importDataBtn').addEventListener('click', () => { fileInput.value = ''; fileInput.click(); });
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files[0];
    if (!file) return;
    const msg = el('adm-backupMsg');
    const btn = el('adm-importDataBtn');
    btn.disabled = true; btn.textContent = t('ui.js.admin_importing', null, 'Importing...'); msg.textContent = '';
    try {
      const text = (await file.text()).replace(/^\uFEFF/, '').trim();
      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        throw new Error(t('ui.js.admin_invalid_backup', { error: e.message }, 'Invalid backup file: {error}'));
      }
      const res = await fetch('/api/import', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await res.json().catch(() => null);
      if (!result) {
        throw new Error(t('ui.js.admin_import_server_error', { status: res.status }, 'Import failed: server returned {status}'));
      }
      if (res.ok && result.ok) {
        msg.textContent = result.message || t('ui.js.admin_import_successful', null, 'Import successful.'); msg.className = 'admin-success';
      } else {
        msg.textContent = result.message || result.detail || t('ui.js.admin_import_failed_short', null, 'Import failed'); msg.className = 'admin-error';
      }
    } catch (e) { msg.textContent = t('ui.js.admin_import_failed', { error: e.message }, 'Import failed: {error}'); msg.className = 'admin-error'; }
    btn.disabled = false; btn.textContent = t('ui.js.admin_import_data', null, 'Import Data');
  });
}

/* ── Danger Zone ── */
function initDangerZone() {
  // Per-category Danger Zone wipes. Each button declares its target
  // via data-wipe-kind; one delegated handler handles double-confirm,
  // POSTs to /api/admin/wipe/{kind}, and writes the result.
  const _LABELS = {
    chats: t('ui.js.admin_wipe_chats', null, 'chats'), memory: t('ui.js.admin_wipe_memory', null, 'memory entries'), skills: t('ui.js.admin_wipe_skills', null, 'skills'),
    notes: t('ui.js.admin_wipe_notes', null, 'notes'), tasks: t('ui.js.admin_wipe_tasks', null, 'tasks'), documents: t('ui.js.admin_wipe_documents', null, 'documents'),
    gallery: t('ui.js.admin_wipe_gallery', null, 'gallery images'), calendar: t('ui.js.admin_wipe_calendar', null, 'calendar items'),
  };
  const _wipeMsg = el('adm-wipeMsg');
  modalEl.querySelectorAll('[data-wipe-kind]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const kind = btn.dataset.wipeKind;
      const label = _LABELS[kind] || kind;
      if (!await uiModule.styledConfirm(t('ui.js.admin_wipe_confirm', { label }, 'Wipe ALL {label}? This cannot be undone.'), { confirmText: t('ui.js.admin_wipe', null, 'Wipe'), danger: true })) return;
      if (!await uiModule.styledConfirm(t('ui.js.admin_wipe_confirm_2', { label }, 'Really wipe every one of your {label}?'), { confirmText: t('ui.js.admin_wipe_everything', null, 'Yes, wipe everything'), danger: true })) return;
      btn.disabled = true; const prev = btn.textContent; btn.textContent = t('ui.js.admin_wiping', null, 'Wiping…');
      if (_wipeMsg) { _wipeMsg.textContent = ''; _wipeMsg.className = ''; }
      try {
        const res = await fetch(`/api/admin/wipe/${kind}`, { method: 'DELETE', credentials: 'same-origin' });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (_wipeMsg) { _wipeMsg.textContent = t('ui.js.admin_wiped', { count: data.count ?? 0, label }, 'Wiped {count} {label}.'); _wipeMsg.className = 'admin-success'; }
        } else {
          if (_wipeMsg) { _wipeMsg.textContent = data.detail || t('ui.js.admin_failed', null, 'Failed'); _wipeMsg.className = 'admin-error'; }
        }
      } catch (e) {
        if (_wipeMsg) { _wipeMsg.textContent = t('ui.js.admin_request_failed_error', { error: e.message }, 'Request failed: {error}'); _wipeMsg.className = 'admin-error'; }
      }
      btn.disabled = false; btn.textContent = prev;
    });
  });
}

/* ═══════════════════════════════════════════
   INIT & REFRESH
   ═══════════════════════════════════════════ */
function initAll() {
  modalEl = el('settings-modal');
  const inits = [initSignupToggle, initAddUser, initEndpointForm, initMcpForm, initCalDAV, initBackup, initDangerZone, initTokenForm, () => settingsModule.initIntegrations()];
  for (const fn of inits) {
    try { fn(); } catch (e) { console.error('Admin init error in', fn.name || 'anonymous', e); }
  }
  initialized = true;
  refreshAll();
}

function refreshAll() {
  loadUsers();
  loadEndpoints();
  loadBuiltinTools();
  loadMcpServers();
  loadTokens();
}

/* ═══════════════════════════════════════════
   PUBLIC API
   ═══════════════════════════════════════════ */
export function _initData() {
  if (!initialized) initAll();
  else refreshAll();
}

export function open(tab) {
  _initData();
  settingsModule.open(tab || 'services');
}

export function close() {
  settingsModule.close();
}

const adminModule = { open, close, _initData, get _initialized() { return initialized; } };
export default adminModule;

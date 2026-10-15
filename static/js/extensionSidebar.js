/* extensionSidebar.js — user-facing sidebar for OXS extensions.

   On init: GET /api/oxs/sidebar (session auth, not admin). The endpoint
   returns extensions the operator has flagged with sidebar_visible=true.
   For each, render a `.list-item` inside #ext-sidebar-list that opens a
   modal on click. The modal is draggable, resizable, and snaps to edges
   like every other tool window — wired via makeWindowDraggable + the
   auto-binding tileManager.

   Modal contents depend on what the extension declared:
     - panels.length > 0      → iframe(s) in a tab bar
     - settings_tabs.length > 0 → info block + buttons that swap in an iframe
     - neither                → generic info block

   Source URLs are validated to be same-origin before loading into iframes.

   Refetch is invoked after the admin toggles sidebar_visible from the
   Settings panel — extensionsPanelModule emits a CustomEvent
   'oxs-extensions-changed' on the document whenever the list mutates, and
   this module listens for it. Initial fetch also runs once auth is known.
*/

import uiModule from './ui.js?v=20260916largetoolscroll1';
import { makeWindowDraggable } from './windowDrag.js';

const API = '/api/oxs';

function _esc(s) { return uiModule.esc(s); }

async function _fetchList() {
  const res = await fetch(`${API}/sidebar`, { credentials: 'same-origin' });
  if (res.status === 401) return { extensions: [] };
  if (!res.ok) throw new Error(`sidebar list failed: ${res.status}`);
  return res.json();
}

async function _fetchDetail(id) {
  const res = await fetch(`${API}/extensions/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`detail failed: ${res.status}`);
  return res.json();
}

async function _post(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `${res.status} ${res.statusText}`);
  return data;
}

function _safeRelativeUrl(url) {
  if (!url || typeof url !== 'string') return null;
  if (url.startsWith('/') && !url.startsWith('//')) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin === window.location.origin) return parsed.pathname + parsed.search + parsed.hash;
  } catch {}
  return null;
}

function _itemTpl(ext) {
  return `<div class="list-item ext-sidebar-item" data-ext-sidebar-id="${_esc(ext.id)}">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
      style="flex-shrink:0;opacity:0.5;">
      <path d="M12 2L2 7l10 5 10-5-10-5z"/>
      <path d="M2 17l10 5 10-5"/>
      <path d="M2 12l10 5 10-5"/>
    </svg>
    <span class="grow">${_esc(ext.name)}</span>
    <span style="font-size:9px;opacity:0.4;font-family:monospace;">v${_esc(ext.version || '?')}</span>
  </div>`;
}

async function refresh() {
  const section = document.getElementById('ext-sidebar-section');
  const list = document.getElementById('ext-sidebar-list');
  if (!section || !list) return;
  try {
    const data = await _fetchList();
    if (!data.extensions || !data.extensions.length) {
      section.style.display = 'none';
      list.innerHTML = '';
      return;
    }
    section.style.display = '';
    list.innerHTML = data.extensions.map(_itemTpl).join('');
    list.querySelectorAll('.ext-sidebar-item').forEach(el => {
      el.addEventListener('click', () => openModal(el.dataset.extSidebarId));
    });
  } catch (e) {
    section.style.display = 'none';
  }
}

function _setTitle(d, showToggle = true) {
  const titleEl = document.getElementById('ext-info-title-text');
  if (titleEl) titleEl.textContent = d.name || d.id;
  const modal = document.getElementById('ext-info-modal');
  const content = modal?.querySelector('.modal-content');
  if (content) content.setAttribute('aria-label', d.name || d.id);
  const toggle = document.getElementById('ext-info-toggle');
  if (toggle) {
    toggle.textContent = d.is_enabled ? 'Disable' : 'Enable';
    toggle.dataset.extId = d.id;
    toggle.dataset.extEnabled = d.is_enabled ? '1' : '0';
    toggle.style.display = showToggle ? '' : 'none';
  }
}

async function _onToggleClicked() {
  const toggle = document.getElementById('ext-info-toggle');
  if (!toggle) return;
  const id = toggle.dataset.extId;
  const enable = toggle.dataset.extEnabled !== '1';
  toggle.disabled = true;
  try {
    await _post(`/extensions/${encodeURIComponent(id)}/${enable ? 'enable' : 'disable'}`);
    refresh();
    openModal(id);
  } catch (e) {
    toggle.disabled = false;
    toggle.textContent = `Error: ${e.message}`;
  }
}

function openModal(id) {
  const modal = document.getElementById('ext-info-modal');
  const body = document.getElementById('ext-info-body');
  if (!modal || !body) return;
  body.innerHTML = '<div style="padding:1rem;font-size:11px;opacity:0.5;">Loading…</div>';
  modal.classList.remove('hidden');
  modal.dataset.extId = id;
  _fetchDetail(id).then(d => {
    const panels = (d.panels || []).filter(p => _safeRelativeUrl(p.source_url));
    const settingsTabs = (d.settings_tabs || []).filter(t => _safeRelativeUrl(t.source_url));

    // Toggle only makes sense when the iframe is NOT shown — otherwise
    // the user is clearly already interacting with the extension.
    const showToggle = panels.length === 0;
    _setTitle(d, showToggle);

    if (panels.length > 0) {
      _renderPanelTabs(d, panels, settingsTabs);
      return;
    }
    if (settingsTabs.length > 0) {
      _renderInfoWithSettings(d, settingsTabs);
      return;
    }
    _renderGenericInfo(d);
  }).catch(e => {
    body.innerHTML = `<div style="padding:1rem;font-size:11px;color:#a04040;">${_esc(String(e.message || e))}</div>`;
  });
}

function _renderPanelTabs(d, panels, settingsTabs) {
  const body = document.getElementById('ext-info-body');
  // Single panel: iframe fills the body, no tab bar (a lone tab would
  // just repeat the modal header's title). Multiple panels get a tab bar
  // plus an Info tab if settings_tabs are also present.
  const needTabs = panels.length > 1 || settingsTabs.length > 0;
  const firstUrl = _safeRelativeUrl(panels[0].source_url);
  if (needTabs) {
    const tabs = panels.map((p, i) => `<button class="admin-btn-sm ext-panel-tab${i === 0 ? ' active' : ''}" data-panel-id="${_esc(p.id)}" data-panel-url="${_esc(_safeRelativeUrl(p.source_url) || '')}" style="font-size:11px;">${_esc(p.title)}</button>`).join('');
    const infoBtn = settingsTabs.length ? `<button class="admin-btn-sm ext-panel-tab" data-panel-id="__info__" style="font-size:11px;">Info</button>` : '';
    body.innerHTML = `
      <div style="display:flex;gap:4px;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid var(--border);background:var(--bg);">
        ${tabs}${infoBtn}
      </div>
      <iframe id="ext-panel-iframe" src="${_esc(firstUrl)}" style="width:100%;height:calc(100% - 38px);border:0;background:var(--bg);display:block;" referrerpolicy="same-origin" onload="_extPanelIframeOnload(this)"></iframe>
    `;
  } else {
    body.innerHTML = `<iframe id="ext-panel-iframe" src="${_esc(firstUrl)}" style="width:100%;height:100%;border:0;background:var(--bg);display:block;" referrerpolicy="same-origin" onload="_extPanelIframeOnload(this)"></iframe>`;
  }
  _wirePanelTabs(panels, settingsTabs, d);
}

// Strip the iframe's own <h1> (the extension's page usually opens with
// its own banner that duplicates our modal title) and any default top
// padding. Same-origin injection only — bails silently on cross-origin.
window._extPanelIframeOnload = function(iframe) {
  try {
    const doc = iframe.contentDocument;
    if (!doc) return;
    let style = doc.getElementById('__ext_panel_cleanup');
    if (!style) {
      style = doc.createElement('style');
      style.id = '__ext_panel_cleanup';
      doc.head.appendChild(style);
    }
    style.textContent = `
      body { padding-top: 0.25rem !important; }
      body > h1:first-child { display: none !important; }
    `;
  } catch (e) {
    // Cross-origin or detached — nothing to do.
  }
};

function _wirePanelTabs(panels, settingsTabs, d) {
  const body = document.getElementById('ext-info-body');
  body.querySelectorAll('.ext-panel-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const url = btn.dataset.panelUrl;
      const iframe = document.getElementById('ext-panel-iframe');
      const info = document.getElementById('ext-panel-info');
      if (btn.dataset.panelId === '__info__') {
        // Replace iframe with info block
        if (iframe) iframe.remove();
        if (!info) {
          const div = document.createElement('div');
          div.id = 'ext-panel-info';
          div.style.cssText = 'padding:1rem;overflow-y:auto;height:calc(100% - 38px);box-sizing:border-box;';
          div.innerHTML = _renderInfoBlock(d, settingsTabs);
          body.appendChild(div);
        }
      } else if (url) {
        // Restore iframe if it was replaced by info
        if (info) info.remove();
        if (!iframe) {
          const newIframe = document.createElement('iframe');
          newIframe.id = 'ext-panel-iframe';
          newIframe.style.cssText = 'width:100%;height:calc(100% - 38px);border:0;background:var(--bg);display:block;';
          newIframe.referrerPolicy = 'same-origin';
          newIframe.src = url;
          newIframe.addEventListener('load', () => window._extPanelIframeOnload(newIframe));
          body.appendChild(newIframe);
        } else {
          iframe.src = url;
        }
      }
      // Active tab highlight (opacity + bold)
      body.querySelectorAll('.ext-panel-tab').forEach(b => {
        const isActive = b === btn;
        b.style.opacity = isActive ? '1' : '0.55';
        b.style.fontWeight = isActive ? '600' : '400';
      });
    });
  });
}

function _renderInfoWithSettings(d, settingsTabs) {
  const body = document.getElementById('ext-info-body');
  const buttons = settingsTabs.map(t => `<button class="admin-btn-sm ext-settings-btn" data-settings-url="${_esc(_safeRelativeUrl(t.source_url))}" style="font-size:11px;">${_esc(t.title)}</button>`).join('');
  body.innerHTML = `
    <div style="padding:1rem;overflow-y:auto;height:100%;box-sizing:border-box;">
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;">${buttons}</div>
      <div id="ext-settings-host"></div>
      ${_renderInfoBlock(d, settingsTabs)}
    </div>
  `;
  body.querySelectorAll('.ext-settings-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const url = btn.dataset.settingsUrl;
      const host = document.getElementById('ext-settings-host');
      if (!url || !host) return;
      host.innerHTML = `<iframe src="${_esc(url)}" style="width:100%;height:55vh;border:1px solid var(--border);border-radius:6px;background:var(--bg);margin-top:4px;" referrerpolicy="same-origin"></iframe>`;
    });
  });
}

function _renderGenericInfo(d) {
  const body = document.getElementById('ext-info-body');
  body.innerHTML = `<div style="padding:1rem;overflow-y:auto;height:100%;box-sizing:border-box;">${_renderInfoBlock(d, [])}</div>`;
}

function _renderInfoBlock(d, settingsTabs) {
  const caps = (d.capabilities || []).map(c => `<span style="font-size:10px;opacity:0.55;padding:1px 6px;border:1px solid var(--border);border-radius:4px;">${_esc(c)}</span>`).join(' ') || '<span style="font-size:10px;opacity:0.4;">(no declared capabilities)</span>';
  const stats = d.stats || {};
  const statLine = [
    `${stats.tools || 0} tools`, `${stats.mcp_tools || 0} mcp`,
    `${stats.routes || 0} routes`, `${stats.webhooks || 0} webhooks`,
    `${stats.event_subs || 0} events`, `${stats.task_actions || 0} tasks`,
    `${stats.statics || 0} statics`,
  ].join(' · ');
  const settingsNote = settingsTabs.length
    ? `<div style="margin-top:10px;font-size:11px;opacity:0.65;">This extension exposes ${settingsTabs.length} settings tab${settingsTabs.length > 1 ? 's' : ''}. Use the button${settingsTabs.length > 1 ? 's' : ''} above to open ${settingsTabs.length > 1 ? 'them' : 'it'}.</div>`
    : `<div style="margin-top:12px;font-size:11px;opacity:0.6;">For full controls (webhooks, audit, reload) open Settings → Extensions.</div>`;
  return `
    <div style="font-size:10px;opacity:0.5;">${_esc(statLine)}</div>
    <div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:4px;font-size:10px;opacity:0.65;">${caps}</div>
    ${d.install_error ? `<div style="margin-top:8px;font-size:11px;color:#a04040;">Install error: ${_esc(d.install_error)}</div>` : ''}
    ${settingsNote}
  `;
}

function closeModal() {
  const modal = document.getElementById('ext-info-modal');
  if (modal) modal.classList.add('hidden');
}

let _wired = false;
export function initExtensionSidebar() {
  if (_wired) return;
  _wired = true;
  const closeBtn = document.getElementById('close-ext-info-modal');
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  const toggleBtn = document.getElementById('ext-info-toggle');
  if (toggleBtn) toggleBtn.addEventListener('click', _onToggleClicked);
  const modal = document.getElementById('ext-info-modal');
  if (modal) {
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    // Make the modal draggable + resizable. Edge-dock is handled
    // automatically by tileManager (it listens for pointerdown on any
    // .modal-header).
    const content = modal.querySelector('.modal-content');
    const header = modal.querySelector('.modal-header');
    if (content && header) {
      makeWindowDraggable(modal, {
        content,
        header,
        skipSelector: 'button, input, select, label, a',
      });
    }
  }

  // Refresh whenever an extension changes.
  document.addEventListener('oxs-extensions-changed', refresh);

  // Initial fetch: wait for auth status so we know whether to bother.
  let tries = 0;
  const t = setInterval(() => {
    tries++;
    if (typeof window._isAdmin !== 'undefined' || tries > 30) {
      clearInterval(t);
      refresh();
    }
  }, 100);
}

export default { refresh, initExtensionSidebar, openModal, closeModal };
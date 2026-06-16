/**
 * Plugin frontend registry — thin core contract.
 *
 * Provides a minimal host API that plugins call to register nav items,
 * sidebar items, panels, settings tabs, and slash commands.  The
 * registry itself renders nothing; it just collects declarations and
 * exposes them to the rest of the app.
 */

import { open as openSettingsModal } from './settings.js';
import { showToast, showError } from './ui.js';

const _pluginNavItems = [];
const _pluginSidebarItems = [];
const _pluginPanels = [];
const _pluginSettingsTabs = [];
const _pluginStyles = [];
const _pluginScripts = new Map();
const _slashCommands = new Map();

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ── Host API exposed to plugins ── */
window.__odysseusPluginHost = {
  registerNavItem({ id, label, icon, onClick }) {
    if (!id || !label) {
      console.warn('[PluginRegistry] registerNavItem requires id and label');
      return;
    }
    _pluginNavItems.push({ id, label, icon, onClick, _rendered: false });
    _renderPluginNavItems();
  },

  registerSidebarItem({ id, label, icon, onClick }) {
    if (!id || !label) {
      console.warn('[PluginRegistry] registerSidebarItem requires id and label');
      return;
    }
    _pluginSidebarItems.push({ id, label, icon, onClick, _rendered: false });
    _renderPluginSidebarItems();
  },

  registerPanel({ id, label, icon, render }) {
    if (!id || !label || typeof render !== 'function') {
      console.warn('[PluginRegistry] registerPanel requires id, label, and render function');
      return;
    }
    _pluginPanels.push({ id, label, icon, render, _rendered: false });
  },

  registerSettingsTab({ id, label, render }) {
    if (!id || !label || typeof render !== 'function') {
      console.warn('[PluginRegistry] registerSettingsTab requires id, label, and render function');
      return;
    }
    _pluginSettingsTabs.push({ id, label, render, _rendered: false });
    _renderPluginSettingsTabs();
  },

  openSettings(tab) {
    openSettingsModal(tab);
  },

  showToast(msg, opts) {
    showToast(msg, opts);
  },

  showError(msg) {
    showError(msg);
  },

  async styledConfirm(msg, opts) {
    if (typeof window !== 'undefined' && window.styledConfirm) {
      return window.styledConfirm(msg, opts);
    }
    return confirm(msg);
  },

  registerSlashCommand(name, handler) {
    if (typeof name !== 'string' || !name.startsWith('/')) {
      console.warn('[PluginRegistry] Slash commands must be strings starting with /');
      return;
    }
    if (typeof handler !== 'function') {
      console.warn('[PluginRegistry] Slash handler must be a function');
      return;
    }
    _slashCommands.set(name, handler);
  },

  loadScript(url, pluginName) {
    return new Promise((resolve, reject) => {
      if (_pluginScripts.has(url)) {
        resolve();
        return;
      }
      const script = document.createElement('script');
      script.src = url;
      script.async = true;
      script.onload = () => { _pluginScripts.set(url, true); resolve(); };
      script.onerror = () => reject(new Error(`Failed to load ${url}`));
      document.head.appendChild(script);
    });
  },

  loadStyle(url, pluginName) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = url;
    link.dataset.pluginStyle = pluginName || 'true';
    document.head.appendChild(link);
  },
};

/* ── Render helpers ── */
function _renderPluginNavItems() {
  const container = document.getElementById('plugin-nav-items');
  if (!container) return;
  for (const item of _pluginNavItems) {
    if (item._rendered) continue;
    const btn = document.createElement('button');
    btn.className = 'icon-rail-btn';
    btn.title = item.label;
    btn.dataset.pluginNavId = item.id;
    btn.innerHTML = item.icon
      ? `<span style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;">${item.icon}</span>`
      : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/></svg>`;
    if (typeof item.onClick === 'function') {
      btn.addEventListener('click', item.onClick);
    }
    container.appendChild(btn);
    item._rendered = true;
  }
}

function _renderPluginSidebarItems() {
  const container = document.getElementById('plugin-sidebar-items');
  if (!container) return;
  for (const item of _pluginSidebarItems) {
    if (item._rendered) continue;
    const div = document.createElement('div');
    div.className = 'list-item';
    div.dataset.pluginSidebarId = item.id;
    div.innerHTML = item.icon
      ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;">${item.icon}</svg>`
      : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;"><rect x="2" y="3" width="20" height="7" rx="2"/><rect x="2" y="14" width="20" height="7" rx="2"/></svg>`;
    const span = document.createElement('span');
    span.className = 'grow';
    span.textContent = item.label;
    div.appendChild(span);
    if (typeof item.onClick === 'function') {
      div.addEventListener('click', item.onClick);
    }
    container.appendChild(div);
    item._rendered = true;
  }
}

function _renderPluginSettingsTabs() {
  const container = document.getElementById('plugin-settings-tabs');
  if (!container) return;
  for (const tab of _pluginSettingsTabs) {
    if (tab._rendered) continue;
    const btn = document.createElement('button');
    btn.className = 'settings-nav-item';
    btn.dataset.settingsTab = `plugin-${tab.id}`;
    btn.textContent = tab.label;
    container.appendChild(btn);
    // Also create the panel
    const panel = document.createElement('div');
    panel.className = 'settings-panel hidden';
    panel.dataset.settingsPanel = `plugin-${tab.id}`;
    panel.innerHTML = tab.render();
    const settingsPanels = document.querySelector('.settings-panels');
    if (settingsPanels) settingsPanels.appendChild(panel);
    tab._rendered = true;
  }
}

/* ── Bootstrap: load enabled plugin frontend code ── */
async function initPluginRegistry() {
  try {
    const r = await fetch('/api/plugins');
    const data = await r.json();
    const plugins = data.installed || [];
    for (const p of plugins) {
      if (!p._enabled) continue;
      // Load frontend script if declared
      const fe = p.frontend;
      if (fe) {
        const src = `/api/plugins/static/${encodeURIComponent(p.name)}/${fe}`;
        try {
          await window.__odysseusPluginHost.loadScript(src, p.name);
        } catch (e) {
          console.warn(`[PluginRegistry] Failed to load frontend for ${p.name}:`, e);
        }
      }
      // Load stylesheets
      if (Array.isArray(p.styles)) {
        for (const sheet of p.styles) {
          const url = `/api/plugins/static/${encodeURIComponent(p.name)}/${sheet}`;
          window.__odysseusPluginHost.loadStyle(url, p.name);
        }
      }
    }
    _renderPluginNavItems();
    _renderPluginSidebarItems();
    _renderPluginSettingsTabs();
  } catch (e) {
    console.warn('[PluginRegistry] init failed:', e);
  }
}

/* ── Slash command dispatch ── */
export async function tryPluginSlashCommand(text) {
  const trimmed = text.trim();
  const spaceIdx = trimmed.indexOf(' ');
  const name = spaceIdx > 0 ? trimmed.slice(0, spaceIdx) : trimmed;
  const args = spaceIdx > 0 ? trimmed.slice(spaceIdx + 1).trim() : '';
  const handler = _slashCommands.get(name);
  if (!handler) return false;
  try {
    await handler(args);
  } catch (e) {
    console.warn(`[PluginRegistry] Slash command ${name} failed:`, e);
    showError(`Plugin command ${name} failed`);
  }
  return true;
}

/* ── Public exports ── */
export function getPluginPanels() {
  return _pluginPanels;
}

export function openPluginPanel(id) {
  const panel = _pluginPanels.find(p => p.id === id);
  if (!panel) return;
  // Lazy render on first open
  if (!panel._rendered && typeof panel.render === 'function') {
    const container = document.getElementById('plugin-panels-container');
    if (container) {
      const div = document.createElement('div');
      div.dataset.pluginPanelId = id;
      div.innerHTML = panel.render();
      container.appendChild(div);
      panel._rendered = true;
    }
  }
}

export { initPluginRegistry };

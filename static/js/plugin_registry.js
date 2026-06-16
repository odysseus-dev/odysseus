/**
 * Plugin frontend registry — thin core contract.
 *
 * Provides a minimal host API that plugins call to register nav items,
 * panels, and settings tabs.  The registry itself renders nothing; it
 * just collects declarations and exposes them to the rest of the app.
 */

const _pluginNavItems = [];
const _pluginPanels = [];
const _pluginSettingsTabs = [];
const _pluginStyles = [];
const _pluginScripts = new Map();

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
    _renderPluginSettingsTabs();
  } catch (e) {
    console.warn('[PluginRegistry] init failed:', e);
  }
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

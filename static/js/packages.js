/**
 * Package Manager UI
 * Handles install, list, toggle, and remove of platform extension packages.
 */

const PackageManager = (() => {
  const API = '/api/packages';

  // ── State ──────────────────────────────────────────────────────────────────
  let _packages = [];
  let _available = [];
  let _panel = null;

  // ── Risk badge helper ──────────────────────────────────────────────────────
  function _riskBadge(level) {
    const map = {
      LOW:    { cls: 'pkg-risk-low',    label: 'Safe' },
      MEDIUM: { cls: 'pkg-risk-medium', label: 'Review' },
      HIGH:   { cls: 'pkg-risk-high',   label: 'Blocked' },
    };
    const r = map[level] || map.LOW;
    return `<span class="pkg-risk-badge ${r.cls}">${r.label}</span>`;
  }

  function _statusBadge(status) {
    const cls = status === 'installed' ? 'pkg-status-on' : 'pkg-status-off';
    return `<span class="pkg-status-badge ${cls}">${status}</span>`;
  }

  // ── API helpers ────────────────────────────────────────────────────────────
  async function _api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  }

  async function _upload(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`${API}/install`, { method: 'POST', body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Upload failed');
    }
    return res.json();
  }

  // ── Load packages from API ─────────────────────────────────────────────────
  async function load() {
    try {
      const data = await _api('GET', '');
      _packages = data.packages || [];
      return _packages;
    } catch (e) {
      console.error('[PackageManager] load failed:', e);
      return [];
    }
  }

  async function loadAvailable() {
    try {
      const data = await _api('GET', '/available');
      _available = data.available || [];
      return _available;
    } catch (e) {
      _available = [];
      return [];
    }
  }

  // ── Render package card ────────────────────────────────────────────────────
  function _renderCard(pkg) {
    const perms = (pkg.permissions || []).map(p =>
      `<span class="pkg-perm-tag">${p}</span>`
    ).join('');

    return `
      <div class="pkg-card" data-id="${pkg.id}">
        <div class="pkg-card-header">
          <div class="pkg-card-title">
            <strong>${_esc(pkg.name)}</strong>
            <span class="pkg-version">v${_esc(pkg.version)}</span>
          </div>
          <div class="pkg-card-badges">
            ${_riskBadge(pkg.risk_level)}
            ${_statusBadge(pkg.status)}
          </div>
        </div>
        ${pkg.description ? `<p class="pkg-description">${_esc(pkg.description)}</p>` : ''}
        ${pkg.author ? `<div class="pkg-author">by ${_esc(pkg.author)}</div>` : ''}
        ${perms ? `<div class="pkg-perms">${perms}</div>` : ''}
        <div class="pkg-card-actions">
          <button class="pkg-btn pkg-btn-toggle" data-id="${pkg.id}" data-enabled="${pkg.status === 'installed'}">
            ${pkg.status === 'installed' ? 'Disable' : 'Enable'}
          </button>
          <button class="pkg-btn pkg-btn-remove" data-id="${pkg.id}">Remove</button>
        </div>
      </div>
    `;
  }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Render full panel ──────────────────────────────────────────────────────
  function _renderPanel(container) {
    // No .pkg-panel wrapper here: the admin-cards sit directly in the settings
    // panel so the layout matches every other settings tab (full width, left
    // aligned). The old .pkg-panel had max-width:900px + margin:0 auto, which
    // centered the content and left a growing gap as the window widened.
    container.innerHTML = `
      <div class="pkg-settings-stack">
        <div class="admin-card">
          <h2>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;opacity:0.7">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
              <polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>
            </svg>Install Package
          </h2>
          <div class="admin-toggle-sub" style="margin-bottom:10px">Install extensions to add new AI capabilities, hardware drivers, and UI components.</div>
          <div class="pkg-install-zone" id="pkg-drop-zone">
            <div class="pkg-drop-inner">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              <p>Drop a <strong>.zip</strong> package here or</p>
              <label class="pkg-btn pkg-btn-primary pkg-upload-label">
                Browse Files
                <input type="file" id="pkg-file-input" accept=".zip" style="display:none">
              </label>
            </div>
            <div class="pkg-install-progress" id="pkg-install-progress" style="display:none">
              <div class="pkg-progress-bar">
                <div class="pkg-progress-fill" id="pkg-progress-fill"></div>
              </div>
              <span id="pkg-progress-msg">Installing...</span>
            </div>
          </div>
        </div>

        <div class="admin-card" id="pkg-available-card">
          <div class="pkg-list-header">
            <h2 style="margin:0;border:none;padding:0">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;opacity:0.7"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>Available Packages
            </h2>
            <span class="pkg-count" id="pkg-available-count"></span>
          </div>
          <div class="admin-toggle-sub" style="margin-bottom:10px">Bundled extensions you can install with one click.</div>
          <div class="pkg-list" id="pkg-available-list"></div>
        </div>

        <div class="admin-card">
          <div class="pkg-list-header">
            <h2 style="margin:0;border:none;padding:0">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px;opacity:0.7"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>Installed Packages
            </h2>
            <span class="pkg-count" id="pkg-count">0 packages</span>
          </div>
          <div class="pkg-list" id="pkg-list">
            <div class="pkg-empty">No packages installed yet.</div>
          </div>
        </div>
      </div>
    `;

    _bindEvents(container);
    _refreshList(container);
    _refreshAvailable(container);
  }

  // ── Render available (bundled) packages ────────────────────────────────────
  function _renderAvailableCard(pkg) {
    const perms = (pkg.permissions || []).map(p =>
      `<span class="pkg-perm-tag">${_esc(p)}</span>`
    ).join('');
    return `
      <div class="pkg-card" data-avail-id="${_esc(pkg.id)}">
        <div class="pkg-card-header">
          <div class="pkg-card-title">
            <strong>${_esc(pkg.name)}</strong>
            ${pkg.version ? `<span class="pkg-version">v${_esc(pkg.version)}</span>` : ''}
          </div>
        </div>
        ${pkg.description ? `<p class="pkg-description">${_esc(pkg.description)}</p>` : ''}
        ${perms ? `<div class="pkg-perms">${perms}</div>` : ''}
        <div class="pkg-card-actions">
          <button class="pkg-btn pkg-btn-primary pkg-btn-install-bundled" data-id="${_esc(pkg.id)}">Install</button>
        </div>
      </div>
    `;
  }

  function _refreshAvailable(container) {
    const listEl = container.querySelector('#pkg-available-list');
    const countEl = container.querySelector('#pkg-available-count');
    const card = container.querySelector('#pkg-available-card');
    if (!listEl) return;

    const notInstalled = _available.filter(p => !p.installed);

    // Hide the whole card when nothing is bundled at all.
    if (card) card.style.display = _available.length ? '' : 'none';

    if (!notInstalled.length) {
      listEl.innerHTML = '<div class="pkg-empty">All bundled packages are installed.</div>';
      if (countEl) countEl.textContent = '';
      return;
    }

    if (countEl) countEl.textContent = `${notInstalled.length} available`;
    listEl.innerHTML = notInstalled.map(_renderAvailableCard).join('');

    listEl.querySelectorAll('.pkg-btn-install-bundled').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        btn.disabled = true;
        btn.textContent = 'Installing…';
        try {
          await _api('POST', '/install-bundled', { id });
          await Promise.all([load(), loadAvailable()]);
          _refreshList(container);
          _refreshAvailable(container);
        } catch (e) {
          btn.disabled = false;
          btn.textContent = 'Install';
          alert(`Install failed: ${e.message}`);
        }
      });
    });
  }

  function _refreshList(container) {
    const listEl = container.querySelector('#pkg-list');
    const countEl = container.querySelector('#pkg-count');
    if (!listEl) return;

    if (_packages.length === 0) {
      listEl.innerHTML = '<div class="pkg-empty">No packages installed yet.</div>';
      if (countEl) countEl.textContent = '0 packages';
      return;
    }

    if (countEl) countEl.textContent = `${_packages.length} package${_packages.length !== 1 ? 's' : ''}`;
    listEl.innerHTML = _packages.map(_renderCard).join('');

    // Wire action buttons
    listEl.querySelectorAll('.pkg-btn-toggle').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const enable = btn.dataset.enabled === 'true' ? false : true;
        try {
          await _api('PATCH', `/${id}/toggle`, { enable });
          await load();
          _refreshList(container);
        } catch (e) {
          alert(`Toggle failed: ${e.message}`);
        }
      });
    });

    listEl.querySelectorAll('.pkg-btn-remove').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const pkg = _packages.find(p => p.id === id);
        if (!confirm(`Remove package "${pkg?.name || id}"? This cannot be undone.`)) return;
        try {
          await _api('DELETE', `/${id}`);
          await Promise.all([load(), loadAvailable()]);
          _refreshList(container);
          _refreshAvailable(container);
        } catch (e) {
          alert(`Remove failed: ${e.message}`);
        }
      });
    });
  }

  function _bindEvents(container) {
    const dropZone = container.querySelector('#pkg-drop-zone');
    const fileInput = container.querySelector('#pkg-file-input');
    const progress = container.querySelector('#pkg-install-progress');
    const progressFill = container.querySelector('#pkg-progress-fill');
    const progressMsg = container.querySelector('#pkg-progress-msg');

    async function installFile(file) {
      if (!file || !file.name.endsWith('.zip')) {
        alert('Please select a .zip package file.');
        return;
      }
      progress.style.display = 'block';
      progressFill.style.width = '30%';
      progressMsg.textContent = 'Uploading...';
      try {
        progressFill.style.width = '60%';
        progressMsg.textContent = 'Running security scan...';
        const result = await _upload(file);
        progressFill.style.width = '100%';

        const riskMsg = result.risk_level === 'MEDIUM'
          ? `\n\nNote: This package requires elevated permissions (${result.risk_level} risk).`
          : '';
        const warnings = result.warnings?.length ? `\nWarnings: ${result.warnings.join(', ')}` : '';
        progressMsg.textContent = `Installed: ${result.package_id}`;
        setTimeout(() => { progress.style.display = 'none'; progressFill.style.width = '0%'; }, 2000);

        if (result.warnings?.length) {
          setTimeout(() => alert(`Package installed.${riskMsg}${warnings}`), 100);
        }
        await Promise.all([load(), loadAvailable()]);
        _refreshList(container);
        _refreshAvailable(container);
      } catch (e) {
        progress.style.display = 'none';
        alert(`Installation failed: ${e.message}`);
      }
    }

    if (fileInput) {
      fileInput.addEventListener('change', e => {
        if (e.target.files?.[0]) installFile(e.target.files[0]);
      });
    }

    if (dropZone) {
      dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('pkg-drag-over'); });
      dropZone.addEventListener('dragleave', () => dropZone.classList.remove('pkg-drag-over'));
      dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('pkg-drag-over');
        const file = e.dataTransfer?.files?.[0];
        if (file) installFile(file);
      });
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  async function init(container) {
    _panel = container;
    await Promise.all([load(), loadAvailable()]);
    _renderPanel(container);
  }

  async function loadFrontendHooks() {
    try {
      const hooks = await _api('GET', '/hooks');
      _injectWidgets(hooks.sidebarWidget   || [], 'package-widgets-sidebar');
      _injectWidgets(hooks.chatInputWidget || [], 'pkg-slot-chat-input');
      _injectWidgets(hooks.toolbarWidget   || [], 'package-widgets-toolbar');
      _injectWidgets(hooks.chatPanel       || [], 'pkg-slot-chat-panel');
      return hooks;
    } catch (e) {
      return {};
    }
  }

  function _injectWidgets(widgets, containerId) {
    const target = document.getElementById(containerId);
    if (!target || !widgets.length) return;
    target.innerHTML = '';
    widgets.forEach(w => {
      const s = document.createElement('script');
      s.type = 'module';
      s.src = w.url;
      s.dataset.pkgId = w.pkg_id;
      target.appendChild(s);
    });
  }

  return { init, load, loadFrontendHooks };
})();

export default PackageManager;

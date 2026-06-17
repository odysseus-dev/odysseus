/**
 * Package Manager UI — settings panel edition.
 * Styled to match native settings panels (admin-card / admin-user-row pattern).
 */

const PackageManager = (() => {
  const API = '/api/packages';

  let _packages = [];
  let _available = [];
  let _panel = null;

  // ── Escape helper ──────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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

  // ── Load ───────────────────────────────────────────────────────────────────
  async function load() {
    try {
      const data = await _api('GET', '');
      _packages = data.packages || [];
    } catch (e) {
      console.error('[PackageManager] load failed:', e);
      _packages = [];
    }
  }

  async function loadAvailable() {
    try {
      const data = await _api('GET', '/available');
      _available = data.available || [];
    } catch (e) {
      _available = [];
    }
  }

  // ── Risk badge ─────────────────────────────────────────────────────────────
  function _riskBadge(level) {
    if (!level || level === 'LOW') return '';
    const color = level === 'HIGH' ? 'var(--color-error, #e06c75)' : 'var(--accent, var(--red))';
    return `<span class="admin-badge" style="background:color-mix(in srgb,${color} 20%,transparent);color:${color}">${_esc(level)}</span>`;
  }

  // ── Render an installed package row ────────────────────────────────────────
  function _pkgRow(pkg) {
    const enabled = pkg.status === 'installed';
    const riskBadge = _riskBadge(pkg.risk_level);
    const statusBadge = enabled
      ? `<span class="admin-badge" style="background:color-mix(in srgb,var(--red) 18%,transparent);color:var(--red)">enabled</span>`
      : `<span class="admin-badge admin-badge-off">disabled</span>`;
    const perms = (pkg.permissions || []).map(p =>
      `<span class="admin-badge" style="font-size:9px;opacity:0.75">${_esc(p)}</span>`
    ).join('');

    return `
      <div class="admin-user-row" data-pkg-id="${_esc(pkg.id)}">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;">
          <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;align-items:center;min-width:0;">
            <span class="admin-user-name">${_esc(pkg.name)}</span>
            ${pkg.version ? `<span class="admin-badge" style="font-size:9px;opacity:0.6">v${_esc(pkg.version)}</span>` : ''}
            ${statusBadge}
            ${riskBadge}
            ${perms}
            ${pkg.author ? `<span style="font-size:10px;opacity:0.4">by ${_esc(pkg.author)}</span>` : ''}
            ${pkg.description ? `<span style="font-size:11px;opacity:0.55;flex-basis:100%;margin-top:1px">${_esc(pkg.description)}</span>` : ''}
          </div>
          <div style="display:flex;gap:4px;align-items:center;flex-shrink:0;margin-left:8px;">
            <button class="admin-btn-sm pkg-toggle-btn" data-id="${_esc(pkg.id)}" data-enabled="${enabled}">
              ${enabled ? 'Disable' : 'Enable'}
            </button>
            <button class="admin-btn-delete pkg-remove-btn" data-id="${_esc(pkg.id)}" style="font-size:10px;padding:2px 7px">Remove</button>
          </div>
        </div>
      </div>
    `;
  }

  // ── Render an available (bundled) package row ──────────────────────────────
  function _availRow(pkg) {
    const perms = (pkg.permissions || []).map(p =>
      `<span class="admin-badge" style="font-size:9px;opacity:0.75">${_esc(p)}</span>`
    ).join('');

    return `
      <div class="admin-user-row" data-avail-id="${_esc(pkg.id)}">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;">
          <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;align-items:center;min-width:0;">
            <span class="admin-user-name">${_esc(pkg.name)}</span>
            ${pkg.version ? `<span class="admin-badge" style="font-size:9px;opacity:0.6">v${_esc(pkg.version)}</span>` : ''}
            ${perms}
            ${pkg.author ? `<span style="font-size:10px;opacity:0.4">by ${_esc(pkg.author)}</span>` : ''}
            ${pkg.description ? `<span style="font-size:11px;opacity:0.55;flex-basis:100%;margin-top:1px">${_esc(pkg.description)}</span>` : ''}
          </div>
          <div style="flex-shrink:0;margin-left:8px;">
            <button class="admin-btn-add pkg-install-bundled-btn" data-id="${_esc(pkg.id)}" style="font-size:11px;padding:3px 10px;">Install</button>
          </div>
        </div>
      </div>
    `;
  }

  // ── Render full panel ──────────────────────────────────────────────────────
  function _renderPanel(container) {
    const notInstalled = _available.filter(p => !p.installed);
    const pkgCount = _packages.length;

    container.innerHTML = `
      <!-- Install card -->
      <div class="admin-card" id="pkg-install-card">
        <h2>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
          </svg>Install Package
        </h2>
        <div class="admin-toggle-sub" style="margin-bottom:10px">Upload a <strong>.zip</strong> package to add new capabilities. You can also drag-and-drop a file onto this card.</div>
        <div class="settings-row">
          <label class="admin-btn-add" style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;font-size:12px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            Browse .zip
            <input type="file" id="pkg-file-input" accept=".zip" style="display:none">
          </label>
          <span id="pkg-install-msg" style="font-size:11px;margin-left:8px;color:color-mix(in srgb,var(--fg) 50%,transparent)"></span>
        </div>
      </div>

      <!-- Available (bundled) packages card -->
      ${_available.length ? `
      <div class="admin-card" id="pkg-available-card">
        <h2>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>
          </svg>Available Packages
          ${notInstalled.length ? `<span class="admin-badge" style="margin-left:6px;font-size:10px">${notInstalled.length} available</span>` : ''}
        </h2>
        <div class="admin-toggle-sub" style="margin-bottom:10px">Bundled extensions — install with one click.</div>
        <div id="pkg-available-list">
          ${notInstalled.length
            ? notInstalled.map(_availRow).join('')
            : '<div class="admin-empty">All bundled packages are installed.</div>'
          }
        </div>
      </div>
      ` : ''}

      <!-- Installed packages card -->
      <div class="admin-card">
        <h2>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6">
            <polyline points="20 6 9 17 4 12"/>
          </svg>Installed Packages
          ${pkgCount ? `<span class="admin-badge" style="margin-left:6px;font-size:10px">${pkgCount} installed</span>` : ''}
        </h2>
        <div id="pkg-list">
          ${pkgCount
            ? _packages.map(_pkgRow).join('')
            : '<div class="admin-empty">No packages installed yet.</div>'
          }
        </div>
      </div>
    `;

    _bindEvents(container);
  }

  // ── Refresh lists in place (after install/remove) ──────────────────────────
  function _refreshPanel(container) {
    // Re-render fully — simpler than patching individual sections
    _renderPanel(container);
  }

  // ── Event binding ──────────────────────────────────────────────────────────
  function _bindEvents(container) {
    const fileInput = container.querySelector('#pkg-file-input');
    const installCard = container.querySelector('#pkg-install-card');
    const installMsg = container.querySelector('#pkg-install-msg');

    async function _installFile(file) {
      if (!file || !file.name.endsWith('.zip')) {
        alert('Please select a .zip package file.');
        return;
      }
      installMsg.textContent = 'Uploading…';
      if (fileInput) fileInput.disabled = true;
      try {
        const result = await _upload(file);
        installMsg.textContent = `Installed: ${result.package_id}`;
        const warnings = result.warnings?.length ? result.warnings.join(', ') : '';
        if (warnings) setTimeout(() => alert(`Package installed.\nWarnings: ${warnings}`), 100);
        setTimeout(() => { installMsg.textContent = ''; }, 3000);
        await Promise.all([load(), loadAvailable()]);
        _refreshPanel(container);
      } catch (e) {
        installMsg.textContent = `Error: ${e.message}`;
        if (fileInput) fileInput.disabled = false;
      }
    }

    if (fileInput) {
      fileInput.addEventListener('change', e => {
        if (e.target.files?.[0]) _installFile(e.target.files[0]);
      });
    }

    // Drag-and-drop on the install card
    if (installCard) {
      installCard.addEventListener('dragover', e => { e.preventDefault(); installCard.style.outline = '2px dashed var(--red)'; });
      installCard.addEventListener('dragleave', () => { installCard.style.outline = ''; });
      installCard.addEventListener('drop', e => {
        e.preventDefault();
        installCard.style.outline = '';
        const file = e.dataTransfer?.files?.[0];
        if (file) _installFile(file);
      });
    }

    // Install bundled buttons
    container.querySelectorAll('.pkg-install-bundled-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        btn.disabled = true;
        btn.textContent = 'Installing…';
        try {
          await _api('POST', '/install-bundled', { id });
          await Promise.all([load(), loadAvailable()]);
          _refreshPanel(container);
        } catch (e) {
          btn.disabled = false;
          btn.textContent = 'Install';
          alert(`Install failed: ${e.message}`);
        }
      });
    });

    // Toggle buttons
    container.querySelectorAll('.pkg-toggle-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const enable = btn.dataset.enabled !== 'true';
        try {
          await _api('PATCH', `/${id}/toggle`, { enable });
          await load();
          _refreshPanel(container);
        } catch (e) {
          alert(`Toggle failed: ${e.message}`);
        }
      });
    });

    // Remove buttons
    container.querySelectorAll('.pkg-remove-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        const pkg = _packages.find(p => p.id === id);
        if (!confirm(`Remove package "${pkg?.name || id}"? This cannot be undone.`)) return;
        try {
          await _api('DELETE', `/${id}`);
          await Promise.all([load(), loadAvailable()]);
          _refreshPanel(container);
        } catch (e) {
          alert(`Remove failed: ${e.message}`);
        }
      });
    });
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

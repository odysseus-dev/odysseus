/**
 * Hardware Monitor & Control Panel UI
 * Shows real-time CPU, RAM, disk, temperatures.
 * Admin users can scan and connect to Wi-Fi networks.
 */

const HardwarePanel = (() => {
  const API = '/api/hardware';
  let _refreshTimer = null;
  let _metrics = {};
  let _container = null;

  // ── API helpers ────────────────────────────────────────────────────────────
  async function _api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || 'Request failed');
    }
    return res.json();
  }

  // ── Gauges ─────────────────────────────────────────────────────────────────
  function _gauge(value, max, label, unit = '%') {
    const pct = Math.min(100, Math.round((value / max) * 100));
    const cls = pct > 85 ? 'hw-gauge-critical' : pct > 65 ? 'hw-gauge-warn' : 'hw-gauge-ok';
    return `
      <div class="hw-gauge-wrap">
        <div class="hw-gauge ${cls}">
          <div class="hw-gauge-fill" style="width:${pct}%"></div>
        </div>
        <div class="hw-gauge-label">
          <span>${label}</span>
          <span class="hw-gauge-value">${typeof value === 'number' ? value.toFixed(1) : value}${unit}</span>
        </div>
      </div>
    `;
  }

  function _tempBadge(val) {
    if (val > 85) return `<span class="hw-temp hw-temp-hot">${val.toFixed(1)}°C</span>`;
    if (val > 65) return `<span class="hw-temp hw-temp-warm">${val.toFixed(1)}°C</span>`;
    return `<span class="hw-temp hw-temp-ok">${val.toFixed(1)}°C</span>`;
  }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _fmtUptime(sec) {
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }

  // ── Render metrics section ─────────────────────────────────────────────────
  function _renderMetrics(m) {
    if (m.error) {
      return `<div class="hw-error">Could not load metrics: ${_esc(m.error)}</div>`;
    }

    const temps = Object.entries(m.temperatures || {});
    const tempHtml = temps.length
      ? temps.map(([k, v]) => `<span class="hw-temp-item"><span class="hw-temp-name">${_esc(k.replace(/_/g,' '))}</span>${_tempBadge(v)}</span>`).join('')
      : '<span class="hw-muted">No sensor data</span>';

    const net = m.network_io || {};

    return `
      <div class="hw-section">
        <div class="hw-section-title">Performance</div>
        <div class="hw-gauges">
          ${_gauge(m.cpu_percent, 100, 'CPU', '%')}
          ${_gauge(m.ram_percent, 100, 'RAM', '%')}
          ${_gauge(m.disk_percent, 100, 'Disk', '%')}
          ${m.swap_percent !== undefined ? _gauge(m.swap_percent, 100, 'Swap', '%') : ''}
        </div>
      </div>
      <div class="hw-section hw-section-row">
        <div class="hw-stat-card">
          <div class="hw-stat-label">RAM</div>
          <div class="hw-stat-val">${m.ram_used_gb} / ${m.ram_total_gb} GB</div>
        </div>
        <div class="hw-stat-card">
          <div class="hw-stat-label">Disk</div>
          <div class="hw-stat-val">${m.disk_used_gb} / ${m.disk_total_gb} GB</div>
        </div>
        <div class="hw-stat-card">
          <div class="hw-stat-label">CPU Cores</div>
          <div class="hw-stat-val">${m.cpu_count_physical || m.cpu_count || '—'} physical</div>
        </div>
        ${m.uptime_seconds ? `
        <div class="hw-stat-card">
          <div class="hw-stat-label">Uptime</div>
          <div class="hw-stat-val">${_fmtUptime(m.uptime_seconds)}</div>
        </div>` : ''}
      </div>
      <div class="hw-section">
        <div class="hw-section-title">Temperatures</div>
        <div class="hw-temps">${tempHtml}</div>
      </div>
      ${Object.keys(net).length ? `
      <div class="hw-section">
        <div class="hw-section-title">Network I/O</div>
        <div class="hw-section-row">
          <div class="hw-stat-card"><div class="hw-stat-label">Sent</div><div class="hw-stat-val">${net.bytes_sent_mb} MB</div></div>
          <div class="hw-stat-card"><div class="hw-stat-label">Received</div><div class="hw-stat-val">${net.bytes_recv_mb} MB</div></div>
        </div>
      </div>` : ''}
    `;
  }

  // ── Wi-Fi panel ────────────────────────────────────────────────────────────
  function _renderWifiPanel() {
    return `
      <div class="hw-section hw-wifi-section">
        <div class="hw-section-header">
          <div class="hw-section-title">Wi-Fi</div>
          <button class="hw-btn" id="hw-wifi-scan-btn">Scan Networks</button>
        </div>
        <div id="hw-wifi-status-bar" class="hw-wifi-status-bar">
          <span id="hw-wifi-status-text" class="hw-muted">Checking...</span>
        </div>
        <div id="hw-wifi-networks" class="hw-wifi-list"></div>
        <div id="hw-wifi-connect-form" class="hw-wifi-connect-form" style="display:none">
          <h4 id="hw-wifi-connect-ssid" class="hw-wifi-connect-title"></h4>
          <input type="password" id="hw-wifi-password" class="hw-input" placeholder="Password (leave empty for open networks)">
          <div class="hw-form-actions">
            <button class="hw-btn hw-btn-primary" id="hw-wifi-connect-confirm">Connect</button>
            <button class="hw-btn" id="hw-wifi-connect-cancel">Cancel</button>
          </div>
          <div class="hw-connect-status" id="hw-connect-status" style="display:none"></div>
        </div>
      </div>
    `;
  }

  function _bindWifiEvents(container) {
    const scanBtn = container.querySelector('#hw-wifi-scan-btn');
    const networksEl = container.querySelector('#hw-wifi-networks');
    const connectForm = container.querySelector('#hw-wifi-connect-form');
    const connectTitle = container.querySelector('#hw-wifi-connect-ssid');
    const pwInput = container.querySelector('#hw-wifi-password');
    const connectBtn = container.querySelector('#hw-wifi-connect-confirm');
    const cancelBtn = container.querySelector('#hw-wifi-connect-cancel');
    const statusBar = container.querySelector('#hw-wifi-status-text');
    const connectStatus = container.querySelector('#hw-connect-status');
    let _selectedSsid = null;

    // Load current status
    _api('GET', '/wifi/status').then(s => {
      if (s.connected && s.ssid) {
        statusBar.textContent = `Connected to: ${s.ssid}`;
        statusBar.className = 'hw-wifi-connected';
      } else {
        statusBar.textContent = 'Not connected';
        statusBar.className = 'hw-muted';
      }
    }).catch(() => { statusBar.textContent = 'Status unavailable'; });

    if (!scanBtn) return;
    scanBtn.addEventListener('click', async () => {
      scanBtn.disabled = true;
      scanBtn.textContent = 'Scanning...';
      networksEl.innerHTML = '<div class="hw-scanning">Scanning for networks...</div>';
      connectForm.style.display = 'none';

      try {
        const data = await _api('GET', '/wifi/scan');
        const networks = data.networks || [];
        if (!networks.length || networks[0]?.error) {
          networksEl.innerHTML = `<div class="hw-muted">${networks[0]?.error || 'No networks found'}</div>`;
        } else {
          networksEl.innerHTML = networks.map(n => `
            <div class="hw-wifi-network" data-ssid="${_esc(n.ssid)}">
              <div class="hw-wifi-network-info">
                <span class="hw-wifi-ssid">${_esc(n.ssid)}</span>
                <span class="hw-wifi-security hw-muted">${_esc(n.security)}</span>
              </div>
              <div class="hw-wifi-signal">
                ${_signalBars(n.signal)}
                <span class="hw-muted">${n.signal}%</span>
              </div>
            </div>
          `).join('');

          networksEl.querySelectorAll('.hw-wifi-network').forEach(el => {
            el.addEventListener('click', () => {
              networksEl.querySelectorAll('.hw-wifi-network').forEach(e => e.classList.remove('selected'));
              el.classList.add('selected');
              _selectedSsid = el.dataset.ssid;
              connectTitle.textContent = `Connect to "${_selectedSsid}"`;
              pwInput.value = '';
              connectStatus.style.display = 'none';
              connectForm.style.display = 'block';
            });
          });
        }
      } catch (e) {
        networksEl.innerHTML = `<div class="hw-error">${e.message}</div>`;
      } finally {
        scanBtn.disabled = false;
        scanBtn.textContent = 'Scan Networks';
      }
    });

    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        connectForm.style.display = 'none';
        _selectedSsid = null;
      });
    }

    if (connectBtn) {
      connectBtn.addEventListener('click', async () => {
        if (!_selectedSsid) return;
        const pw = pwInput.value;
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';
        connectStatus.style.display = 'none';

        try {
          const result = await _api('POST', '/wifi/connect', { ssid: _selectedSsid, password: pw });
          connectStatus.textContent = result.message || 'Connected!';
          connectStatus.className = 'hw-connect-status hw-connect-ok';
          connectStatus.style.display = 'block';
          statusBar.textContent = `Connected to: ${_selectedSsid}`;
          statusBar.className = 'hw-wifi-connected';
        } catch (e) {
          connectStatus.textContent = e.message;
          connectStatus.className = 'hw-connect-status hw-connect-err';
          connectStatus.style.display = 'block';
        } finally {
          connectBtn.disabled = false;
          connectBtn.textContent = 'Connect';
        }
      });
    }
  }

  function _signalBars(signal) {
    const bars = Math.ceil((signal / 100) * 4);
    return Array.from({ length: 4 }, (_, i) =>
      `<div class="hw-signal-bar ${i < bars ? 'hw-signal-bar-on' : ''}" style="height:${(i + 1) * 4}px"></div>`
    ).join('');
  }

  // ── Render full panel ──────────────────────────────────────────────────────
  async function _renderPanel(container, isAdmin) {
    container.innerHTML = `
      <div class="hw-panel">
        <div class="hw-panel-header">
          <h2 class="hw-panel-title">Hardware & System</h2>
          <button class="hw-btn hw-btn-sm" id="hw-refresh-btn">↻ Refresh</button>
        </div>
        <div id="hw-metrics-section"><div class="hw-loading">Loading metrics...</div></div>
        ${isAdmin ? _renderWifiPanel() : ''}
        <div class="hw-section">
          <div class="hw-section-title">Network Interfaces</div>
          <div id="hw-interfaces"><div class="hw-muted">Loading...</div></div>
        </div>
      </div>
    `;

    container.querySelector('#hw-refresh-btn').addEventListener('click', () => _refreshMetrics(container));

    if (isAdmin) {
      _bindWifiEvents(container);
    }

    await _refreshMetrics(container);
    _refreshInterfaces(container);
  }

  async function _refreshMetrics(container) {
    const metricsEl = container.querySelector('#hw-metrics-section');
    if (!metricsEl) return;
    try {
      _metrics = await _api('GET', '/metrics');
      metricsEl.innerHTML = _renderMetrics(_metrics);
    } catch (e) {
      metricsEl.innerHTML = `<div class="hw-error">Failed to load metrics: ${e.message}</div>`;
    }
  }

  async function _refreshInterfaces(container) {
    const ifacesEl = container.querySelector('#hw-interfaces');
    if (!ifacesEl) return;
    try {
      const data = await _api('GET', '/interfaces');
      const ifaces = data.interfaces || [];
      if (!ifaces.length) {
        ifacesEl.innerHTML = '<div class="hw-muted">No interfaces found.</div>';
        return;
      }
      ifacesEl.innerHTML = ifaces.map(i => `
        <div class="hw-iface ${i.is_up ? 'hw-iface-up' : 'hw-iface-down'}">
          <span class="hw-iface-name">${_esc(i.name)}</span>
          <span class="hw-iface-status">${i.is_up ? '▲ Up' : '▼ Down'}</span>
          ${i.speed_mbps ? `<span class="hw-muted">${i.speed_mbps} Mbps</span>` : ''}
          <div class="hw-iface-addrs">
            ${(i.addresses || []).map(a => `<span class="hw-addr">${_esc(a.address)}</span>`).join('')}
          </div>
        </div>
      `).join('');
    } catch (e) {
      ifacesEl.innerHTML = `<div class="hw-error">${e.message}</div>`;
    }
  }

  // ── Auto-refresh ───────────────────────────────────────────────────────────
  function _startAutoRefresh(container, intervalMs = 5000) {
    _stopAutoRefresh();
    _refreshTimer = setInterval(() => _refreshMetrics(container), intervalMs);
  }

  function _stopAutoRefresh() {
    if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  async function init(container, { isAdmin = false, autoRefresh = true } = {}) {
    _container = container;
    await _renderPanel(container, isAdmin);
    if (autoRefresh) _startAutoRefresh(container, 8000);
  }

  function destroy() {
    _stopAutoRefresh();
  }

  return { init, destroy };
})();

export default HardwarePanel;

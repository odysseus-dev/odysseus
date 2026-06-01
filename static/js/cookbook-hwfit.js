// ============================================
// COOKBOOK HWFIT SUB-MODULE
// "What Fits?" hardware model fitting UI
// ============================================

import {
  _envState,
  _persistEnvState,
  esc,
  modelLogo,
  _detectBackend,
  _runModelDownload,
  _runPanelCmd,
  _buildDownloadCmd,
  _addTask,
  _renderRunningTab,
  _detectToolParser,
  _lastCacheHost,
  _setLastCacheHost,
  _serverByVal,
  _shellQuote,
  _MODELDIR_CHECK_ON,
  _MODELDIR_CHECK_OFF,
  _serverEntryHtml,
  _copyText,
  // Import cookbook.js WITHOUT a ?v= query — the same plain specifier every other
  // importer uses. A query mismatch loads cookbook.js twice as two separate modules
  // (two _envState objects), which silently sent downloads to the wrong server.
} from './cookbook.js';
import uiModule from './ui.js';
import spinnerModule from './spinner.js';

// ── What Fits? (hardware model fitting) ──

export let _hwfitCache = null;
export let _hwfitDebounce = null;
export let _cachedModelIds = null; // repo IDs already downloaded
// Bumped on every _hwfitFetch; a slow scan (remote SSH probe can take ~10s)
// checks this before rendering so a stale response can't clobber a newer one
// after the user has switched servers.
let _hwfitFetchToken = 0;
let _dismissedHwChips = new Set();
// Permanently removed (X-clicked) chips. Separate from _dismissedHwChips
// so the ranker treats "off" and "removed" the same (both ignore the
// hardware) but the UI keeps "off" chips visible to toggle back on,
// while "removed" ones don't render at all until next rescan.
let _removedHwChips = new Set();

export let _gpuToggleTotal = 0; // real GPU count from first scan, never overridden

// Reset GPU-toggle state so the next scan re-renders the RAM/GPU buttons for a
// (possibly different) server, WITHOUT clearing the markup now — clearing it made
// the buttons flicker out and back in. The old buttons stay visible until the
// fresh scan returns and swaps them in place. Lives here (not cookbook.js) because
// _gpuToggleTotal is a module-local binding that can't be reassigned by importers.
export function _resetGpuToggleState(clearDismissed = true) {
  if (clearDismissed) {
    _dismissedHwChips = new Set();
    _removedHwChips = new Set();
  }
  const tc = document.getElementById('hwfit-gpu-toggles');
  if (tc) {
    tc._originalSystem = null;
    tc._activeCount = undefined;
    tc._activeGroup = undefined;
    tc._groups = null;
    tc._builtGroup = undefined;
    delete tc.dataset.rendered;
  }
  _gpuToggleTotal = 0;
}

// Trim vendor noise so a pool label reads "RTX 4090 D" not "NVIDIA GeForce RTX 4090 D".
function _shortGpuName(name) {
  return String(name || 'GPU')
    .replace(/^NVIDIA\s+GeForce\s+/i, '')
    .replace(/^NVIDIA\s+/i, '')
    .replace(/^AMD\s+(Radeon\s+)?/i, '')
    .trim() || 'GPU';
}

// Powers of two up to the pool size, plus the exact pool size — these are the
// only safe vLLM --tensor-parallel-size values (TP must divide the GPU count and
// the model's attention heads). Never offer a count we can't actually serve.
function _validTpCounts(poolSize) {
  const out = [1, 2, 4, 8, 16].filter(n => n <= poolSize);
  if (poolSize > 0 && !out.includes(poolSize)) out.push(poolSize);
  return out;
}

export function _renderGpuToggles(system) {
  const container = document.getElementById('hwfit-gpu-toggles');
  if (!container) return;
  const groups = Array.isArray(system.gpu_groups) ? system.gpu_groups : [];
  // Box-wide GPU total, stable across fetches. The route shrinks system.gpu_count
  // to the *active pool* once we pin one, so derive the total from the (immutable)
  // group list or the raw detection, never from the possibly-overridden count.
  const total = system.detected_gpu_count
    || (groups.length ? groups.reduce((s, g) => s + (g.count || 0), 0) : (system.gpu_count || 0));
  if (total <= 0 && !system.has_gpu) {
    container.innerHTML = '';
    container._groups = null;
    _gpuToggleTotal = 0;
    return;
  }
  if (!_gpuToggleTotal) _gpuToggleTotal = total;

  container._groups = groups;
  if (container._activeGroup === undefined) container._activeGroup = 0;  // auto = largest pool
  const heterogeneous = groups.length > 1;

  // Rebuild only when the hardware shape changes OR the chosen pool changes (the
  // count buttons are pool-specific). Otherwise a re-scan would flicker them.
  const sig = `${total}|${groups.map(g => g.count + ':' + g.vram_each).join(',')}`;
  if (container.dataset.rendered === sig && container._builtGroup === container._activeGroup) return;
  container.dataset.rendered = sig;
  container._builtGroup = container._activeGroup;

  const grp = groups[container._activeGroup] || groups[0]
    || { count: total, vram_each: 0, name: system.gpu_name || 'GPU' };
  const poolSize = grp.count || total;

  let html = '';
  if (heterogeneous) {
    html += `<select class="hwfit-gpu-group" id="hwfit-gpu-group" title="Which GPU pool to serve from — vLLM can only tensor-parallel across identical GPUs">`;
    groups.forEach((g, i) => {
      const lbl = `${g.count}× ${_shortGpuName(g.name)} (${Math.round(g.vram_total)} GB)`;
      html += `<option value="${i}"${i === container._activeGroup ? ' selected' : ''}>${esc(lbl)}</option>`;
    });
    html += '</select>';
  }
  const validCounts = _validTpCounts(poolSize);
  const maxGpu = validCounts.length ? validCounts[validCounts.length - 1] : 0;
  html += '<button class="hwfit-gpu-btn" data-count="0" title="CPU / RAM only">RAM</button>';
  const hasExplicitCount = typeof container._activeCount === 'number';
  for (const n of validCounts) {
    const text = n === 1 ? 'GPU' : n + ' GPU';
    const isActive = hasExplicitCount ? (n === container._activeCount) : (container._activeCount === undefined && n === maxGpu);
    html += `<button class="hwfit-gpu-btn${isActive ? ' active' : ''}" data-count="${n}" title="${n} GPU${n > 1 ? 's' : ''}">${text}</button>`;
  }
  container.innerHTML = html;

  // Pool dropdown: switch pools, reset the count to the new pool's max, rebuild.
  const sel = container.querySelector('#hwfit-gpu-group');
  if (sel) {
    sel.addEventListener('change', () => {
      container._activeGroup = parseInt(sel.value) || 0;
      container._activeCount = undefined;   // default to the new pool's max
      delete container.dataset.rendered;    // force a count-button rebuild
      _renderGpuToggles(system);
      _hwfitCache = null;
      _hwfitFetch();
    });
  }

  if (!container._gpuBound) {
    container._gpuBound = true;
    container.addEventListener('click', (e) => {
      const btn = e.target.closest('.hwfit-gpu-btn');
      if (!btn) return;
      const count = parseInt(btn.dataset.count);
      const wasActive = btn.classList.contains('active') && container._activeCount === count;
      container.querySelectorAll('.hwfit-gpu-btn').forEach(b => b.classList.remove('active'));
      if (wasActive) {
        container._activeCount = null;
      } else {
        btn.classList.add('active');
        container._activeCount = count;
        // Auto-set quant based on hardware selection
        const quantSel = document.getElementById('hwfit-quant');
        if (quantSel) {
          if (count <= 1) {
            quantSel.value = 'Q4_K_M'; // RAM or 1 GPU -> Q4 sweet spot
          } else {
            quantSel.value = 'AWQ-4bit'; // Multi-GPU -> AWQ for vLLM
          }
        }
      }
      _hwfitCache = null;
      _hwfitFetch();
    });
  }
}

// --- Scan persistence (survives page reloads) ----------------------------
// The backend caches hardware detection per host (~30 min) but that's lost on a
// service restart, and a reload still shows a spinner while it re-fetches. Cache
// the last successful /models result per param-signature in localStorage so a
// reload paints instantly, then we refresh in the background and swap.
const _SCAN_CACHE_KEY = 'hwfit_scan_cache_v1';
const _MANUAL_HW_KEY = 'hwfit_manual_hardware_v1';
const _SCAN_CACHE_MAX = 12;            // keep the newest N signatures
const _SCAN_CACHE_TTL = 6 * 3600 * 1000; // 6 h — hardware rarely changes

function _manualHwState() {
  try {
    const s = JSON.parse(localStorage.getItem(_MANUAL_HW_KEY) || '{}');
    if (s && (s.mode === 'gpu' || s.mode === 'ram')) return s;
  } catch {}
  return null;
}

function _saveManualHwState(s) {
  try {
    if (!s || !s.mode) localStorage.removeItem(_MANUAL_HW_KEY);
    else localStorage.setItem(_MANUAL_HW_KEY, JSON.stringify(s));
  } catch {}
}

function _manualHwParams() {
  const s = _manualHwState();
  if (!s) return {};
  return {
    manual_mode: s.mode,
    manual_gpu_count: s.mode === 'gpu' ? String(s.gpuCount || 1) : '',
    manual_vram_gb: s.mode === 'gpu' ? String(s.vramGb || 8) : '',
    manual_ram_gb: s.ramGb ? String(s.ramGb) : '',
    manual_backend: s.mode === 'gpu' ? (s.backend || 'cuda') : '',
  };
}

function _manualNumber(value, fallback) {
  const raw = String(value || '').replace(',', '.');
  const match = raw.match(/-?\d+(?:\.\d+)?/);
  if (!match) return fallback;
  const n = Number(match[0]);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function _manualOptionalNumber(value) {
  const raw = String(value || '').replace(',', '.');
  const match = raw.match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const n = Number(match[0]);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function _manualHwLabel(s) {
  if (!s) return '';
  // Manual mode is a "what if" SIMULATOR — values REPLACE detected
  // hardware (matches server-side _apply_manual_hardware). Label
  // phrased as plain "X GB" instead of additive "+X GB" so the user
  // sees the simulated TOTAL, not an addition.
  const ram = s.ramGb ? ` · ${s.ramGb} GB RAM` : '';
  if (s.mode === 'ram') return `Manual: ${s.ramGb || 0} GB RAM only`;
  const gpus = `${s.gpuCount || 1} GPU${Number(s.gpuCount || 1) === 1 ? '' : 's'}`;
  return `Manual: ${gpus} · ${s.vramGb || 8} GB VRAM each${ram}`;
}

function _manualDisplaySystem(sys, manual) {
  const base = { ...(sys || {}) };
  if (!manual) return base;
  base.manual_hardware = true;
  // REPLACE detected RAM with the manual total. Previously this added
  // on top of detected, which (a) contradicted the new server-side
  // "replace" behavior and (b) made the chip's displayed total not
  // match what was actually being ranked against.
  if (manual.ramGb) {
    base.available_ram_gb = Number(manual.ramGb);
    base.total_ram_gb = Number(manual.ramGb);
  }
  if (manual.mode === 'ram') {
    // RAM-only simulation — wipe GPU side so the chip display matches
    // what the server is ranking against (CPU/RAM paths only).
    base.has_gpu = false;
    base.gpu_name = null;
    base.gpu_vram_gb = 0;
    base.gpu_count = 0;
    return base;
  }
  if (manual.mode !== 'ram') {
    const count = Number(manual.gpuCount || 1);
    const vram = Number(manual.vramGb || 8);
    const backend = (manual.backend || 'cuda').toUpperCase();
    base.gpu_name = `Simulated ${backend} GPU` + (count > 1 ? ` × ${count}` : '');
    base.gpu_vram_gb = Math.round(vram * count * 10) / 10;
    base.gpu_count = count;
    base.backend = manual.backend || 'cuda';
  }
  return base;
}

// Signature of everything that affects the result list, so we never paint a
// cached list under mismatched filters.
function _scanSig() {
  const sortEl = document.getElementById('hwfit-sort');
  const tc = document.getElementById('hwfit-gpu-toggles');
  return JSON.stringify({
    h: _envState.remoteHost || '',
    u: document.getElementById('hwfit-usecase')?.value || '',
    s: document.getElementById('hwfit-search')?.value?.trim() || '',
    o: sortEl?.value || 'score',
    r: sortEl?.dataset.reverse === '1' ? 1 : 0,
    q: document.getElementById('hwfit-quant')?.value || '',
    g: (tc && typeof tc._activeCount === 'number') ? String(tc._activeCount) : '',
    gg: (tc && tc._activeGroup) ? String(tc._activeGroup) : '',
    m: _manualHwParams(),
    d: Array.from(_dismissedHwChips).sort(),
  });
}

function _readScanCache(sig) {
  try {
    const all = JSON.parse(localStorage.getItem(_SCAN_CACHE_KEY) || '{}');
    const e = all[sig];
    if (e && (Date.now() - e.ts) < _SCAN_CACHE_TTL) return e.data;
  } catch {}
  return null;
}

function _writeScanCache(sig, data) {
  try {
    const all = JSON.parse(localStorage.getItem(_SCAN_CACHE_KEY) || '{}');
    all[sig] = { ts: Date.now(), data: { system: data.system, models: data.models } };
    const keys = Object.keys(all);
    if (keys.length > _SCAN_CACHE_MAX) {
      keys.sort((a, b) => (all[a].ts || 0) - (all[b].ts || 0));
      for (const k of keys.slice(0, keys.length - _SCAN_CACHE_MAX)) delete all[k];
    }
    localStorage.setItem(_SCAN_CACHE_KEY, JSON.stringify(all));
  } catch {}
}

// Render a clear scan-failure card into the model list: which server failed, the
// underlying reason (small), and a Retry button that forces a fresh probe. Used
// for both the backend-reported error (SSH/probe failure) and network failures,
// instead of dumping a raw one-line message.
function _hwfitShowError(list, host, detail) {
  if (!list) return;
  const where = host ? esc(host) : 'this machine';
  const div = document.createElement('div');
  div.className = 'hwfit-loading';
  div.style.cssText = 'flex-direction:column;gap:8px;text-align:center;';
  div.innerHTML =
    `<div style="color:var(--red);font-weight:600;">Couldn't scan ${where}</div>`
    + (detail ? `<div style="opacity:0.6;font-size:11px;max-width:340px;line-height:1.4;">${esc(detail)}</div>` : '')
    + `<button type="button" class="hwfit-gpu-btn" id="hwfit-retry" style="margin-top:2px;height:26px;">↻ Retry</button>`;
  list.innerHTML = '';
  list.appendChild(div);
  const rb = div.querySelector('#hwfit-retry');
  if (rb) rb.addEventListener('click', () => { _resetGpuToggleState(); _hwfitFetch(true); });
}

export async function _hwfitFetch(fresh = false) {
  const _tk = ++_hwfitFetchToken;
  const useCase = document.getElementById('hwfit-usecase')?.value || '';
  const search = document.getElementById('hwfit-search')?.value?.trim() || '';
  const remoteHost = _envState.remoteHost || '';
  const list = document.getElementById('hwfit-list');
  const hw = document.getElementById('hwfit-hw');
  if (!list) return;
  const hasManualOrDismissed = !!_manualHwState() || _dismissedHwChips.size > 0;
  if (hasManualOrDismissed) fresh = true;
  // Instant paint from the persisted cache (skipped on a forced Rescan), so a
  // reload shows the last result with no spinner. We still fetch fresh below and
  // swap it in. If there's no cache hit, fall back to the spinner.
  const _sig = _scanSig();
  const _cached = fresh ? null : _readScanCache(_sig);
  const wp = spinnerModule.createWhirlpool(18);
  if (_cached) {
    _hwfitCache = _cached;
    _hwfitRenderHw(hw, _cached.system);
    _hwfitRenderList(list, _cached.models);
  } else {
    // Show spinner while scanning — stack the spinner above a text label
    // (the .hwfit-loading class is a centered flex ROW, so force column here).
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'hwfit-loading';
    loadingDiv.style.flexDirection = 'column';
    loadingDiv.style.gap = '6px';
    loadingDiv.appendChild(wp.element);
    // Text label like the other cookbook tabs: "Loading…", then if the scan runs
    // long (remote SSH hardware probe), switch to "Scanning hardware…".
    const loadingLbl = document.createElement('div');
    loadingLbl.textContent = 'Loading…';
    loadingLbl.style.cssText = 'text-align:center;opacity:0.5;font-size:11px;';
    loadingDiv.appendChild(loadingLbl);
    setTimeout(() => { if (loadingLbl.isConnected) loadingLbl.textContent = 'Scanning hardware…'; }, 2000);
    list.innerHTML = '';
    list.appendChild(loadingDiv);
    _hwfitCache = null;   // no instant paint — clear until the fetch returns
  }
  // Only fetch cached model IDs when server changes, not on every search/sort
  if (!_cachedModelIds || _lastCacheHost() !== remoteHost) {
    _setLastCacheHost(remoteHost);
    const _cacheSrv = _envState.servers.find(s => s.host === remoteHost);
    const _cachePort = _cacheSrv?.port || '';
    const _cacheParams = new URLSearchParams({ host: remoteHost }); if (_cachePort) _cacheParams.set('ssh_port', _cachePort); if (_cacheSrv?.platform) _cacheParams.set('platform', _cacheSrv.platform);
    fetch(`/api/model/cached?${_cacheParams}`, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => {
        _cachedModelIds = new Set((d.models || []).map(m => m.repo_id));
        // Re-mark rows if already rendered
        list.querySelectorAll('.hwfit-row[data-model]').forEach(row => {
          const name = row.dataset.model;
          if (_cachedModelIds.has(name) || [..._cachedModelIds].some(id => id.endsWith('/' + name?.split('/').pop()))) {
            const nameEl = row.querySelector('.hwfit-name');
            if (nameEl && !nameEl.querySelector('.hwfit-dl-dot')) {
              nameEl.insertAdjacentHTML('beforeend', '<span class="hwfit-dl-dot" title="Downloaded">\u25CF</span>');
            }
          }
        });
      }).catch(() => {});
  }
  try {
    const sortBy = document.getElementById('hwfit-sort')?.value || 'score';
    const quantPref = document.getElementById('hwfit-quant')?.value || '';
    // Get active GPU count from toggles
    const toggleContainer = document.getElementById('hwfit-gpu-toggles');
    let gpuCountOverride = '';
    if (!hasManualOrDismissed && toggleContainer && typeof toggleContainer._activeCount === 'number') {
      gpuCountOverride = String(toggleContainer._activeCount);
    }
    // Which homogeneous GPU pool to rank against (heterogeneous boxes only).
    let gpuGroupOverride = '';
    if (!hasManualOrDismissed && toggleContainer && toggleContainer._activeGroup) {
      gpuGroupOverride = String(toggleContainer._activeGroup);
    }
    const params = new URLSearchParams({ limit: '80', sort: sortBy });
    if (fresh) params.set('fresh', '1');   // bypass the hardware-scan cache
    if (search) params.set('search', search);
    if (remoteHost) {
      params.set('host', remoteHost);
      const _srv = _envState.servers.find(s => s.host === remoteHost);
      const _hp = _srv?.port || '';
      if (_hp) params.set('ssh_port', _hp);
      if (_srv?.platform) params.set('platform', _srv.platform);
    }
    if (gpuCountOverride !== '') params.set('gpu_count', gpuCountOverride);
    if (gpuGroupOverride !== '') params.set('gpu_group', gpuGroupOverride);
    if (_dismissedHwChips.has('gpu') || _dismissedHwChips.has('vram')) params.set('ignore_detected_gpu', 'true');
    if (_dismissedHwChips.has('ram')) params.set('ignore_detected_ram', 'true');
    const manualParams = _manualHwParams();
    Object.entries(manualParams).forEach(([k, v]) => {
      if (v !== '') params.set(k, v);
    });
    if (hasManualOrDismissed) params.set('_hw_override_ts', String(Date.now()));
    // Image models use a separate registry/endpoint
    const isImageMode = useCase === 'image_gen';
    if (!isImageMode) {
      if (useCase) params.set('use_case', useCase);
      if (quantPref) params.set('quant', quantPref);
    }
    const endpoint = isImageMode ? `/api/hwfit/image-models?${params}` : `/api/hwfit/models?${params}`;
    const res = await fetch(endpoint);
    // A newer scan started while this one was in flight (user switched servers
    // mid-probe) — drop this stale response so it can't clobber the new one.
    if (_tk !== _hwfitFetchToken) { try { wp.destroy(); } catch {} return; }
    if (!res.ok) throw new Error(res.statusText);
    let data = await res.json();
    if (_tk !== _hwfitFetchToken) { try { wp.destroy(); } catch {} return; }
    if (!isImageMode && quantPref && !data.error && Array.isArray(data.models) && data.models.length === 0) {
      const fallbackParams = new URLSearchParams(params);
      fallbackParams.delete('quant');
      const fallbackRes = await fetch(`/api/hwfit/models?${fallbackParams}`);
      if (_tk !== _hwfitFetchToken) { try { wp.destroy(); } catch {} return; }
      if (fallbackRes.ok) {
        const fallbackData = await fallbackRes.json();
        if (!fallbackData.error && Array.isArray(fallbackData.models) && fallbackData.models.length > 0) {
          data = fallbackData;
          const quantSel = document.getElementById('hwfit-quant');
          if (quantSel) quantSel.value = '';
        }
      }
    }
    // Normalize image model fields to match LLM renderer expectations
    if (isImageMode && data.models) {
      data.models = data.models.map(m => ({
        ...m,
        name: m.id || m.name,
        fit_level: m.fit || 'no_fit',
        parameter_count: m.params_b ? m.params_b + 'B' : '?',
        required_gb: m.vram_needed || 0,
        speed_tps: 0,
        context: 0,
        run_mode: m.capabilities?.[0] || 'image',
        is_image_gen: true,
        quant: m.quant || m.default_quant || 'BF16',
        quant_repo: m.quant_repo || null,
      }));
    }
    wp.destroy();
    if (data.error) {
      // Keep the instantly-painted cache if we had one — don't replace good data
      // with an error on a transient probe failure (stale-while-revalidate).
      if (!_cached) { _hwfitShowError(list, remoteHost, data.error); if (hw) hw.innerHTML = ''; }
      return;
    }
    _hwfitCache = data;
    _hwfitRenderHw(hw, data.system);
    // Sort client-side by the active column so the highest↔lowest toggle is
    // deterministic (the previous array .reverse() didn't reliably flip).
    // 1st click on a column = highest first; clicking it again = lowest first.
    if (!isImageMode) {
      const sortSel = document.getElementById('hwfit-sort');
      const sortKey = sortSel?.value || 'score';
      const asc = sortSel?.dataset.reverse === '1';   // reversed → ascending (lowest first)
      const field = { score: 'score', vram: 'required_gb', speed: 'speed_tps', params: 'params_b', context: 'context' }[sortKey] || 'score';
      data.models.sort((a, b) => {
        const av = Number(a[field]) || 0, bv = Number(b[field]) || 0;
        return asc ? av - bv : bv - av;
      });
    }
    _hwfitRenderList(list, data.models);
    // Persist this result so the next page load can paint it instantly.
    _writeScanCache(_sig, data);
    // Render GPU toggles — only on first scan (no override active)
    if (toggleContainer && !toggleContainer._originalSystem) {
      // Only trust the system info if no GPU override was applied
      if (toggleContainer._activeCount === undefined) {
        toggleContainer._originalSystem = { ...data.system };
        _renderGpuToggles(toggleContainer._originalSystem);
      }
    }
  } catch (e) {
    wp.destroy();
    // Same stale-while-revalidate rule: only surface the error if we have nothing
    // already on screen from the cache.
    if (!_cached) _hwfitShowError(list, remoteHost, e.message);
  }
}

export function _hwfitRenderHw(el, sys) {
  if (!el || !sys) return;
  // Cache system info globally so other modules can read VRAM without refetching
  try { window._hwfitSystemCache = sys; } catch {}
  // Show the hardware row when we have data
  const hwRow = document.getElementById('hwfit-hw-row');
  if (hwRow) hwRow.style.display = 'flex';
  const gpuCount = sys.gpu_count || 0;
  // gpu_error = nvidia-smi present but failing (e.g. driver/library version
  // mismatch). Surface it instead of the misleading "No GPU" — plain text
  // label, full error in the tooltip.
  // Chip rendering: split into a clickable body (toggle off / on) and a
  // separate × button (fully remove from view + treat as dismissed for
  // ranking). The body's "off" state is just visually dimmed — the
  // chip stays visible so you can flip it back on without re-scanning.
  const chip = (key, label, title = 'Click to toggle off (X to hide)') => {
    if (_removedHwChips.has(key)) return '';
    const dim = _dismissedHwChips.has(key) ? ' hwfit-hw-chip-off' : '';
    return (
      `<span class="hwfit-hw-chip hwfit-hw-chip-row${dim}" data-hw-chip="${esc(key)}">`
      + `<button type="button" class="hwfit-hw-chip-toggle" data-hw-chip="${esc(key)}" title="${esc(title)}">${label}</button>`
      + `<button type="button" class="hwfit-hw-chip-x" data-hw-chip="${esc(key)}" title="Remove this chip" aria-label="Remove">×</button>`
      + `</span>`
    );
  };
  let gpuChip;
  if (sys.gpu_name) {
    const label = gpuCount > 1 ? `${gpuCount}x ${esc(sys.gpu_name)}` : esc(sys.gpu_name);
    gpuChip = chip('gpu', label);
  } else if (sys.gpu_error) {
    gpuChip = _removedHwChips.has('gpu')
      ? ''
      : (() => {
          const dim = _dismissedHwChips.has('gpu') ? ' hwfit-hw-chip-off' : '';
          return (
            `<span class="hwfit-hw-chip hwfit-hw-chip-row hwfit-hw-chip-error${dim}" data-hw-chip="gpu">`
            + `<button type="button" class="hwfit-hw-chip-toggle" data-hw-chip="gpu" title="${esc(sys.gpu_error)}">GPU driver error</button>`
            + `<button type="button" class="hwfit-hw-chip-x" data-hw-chip="gpu" title="Remove this chip" aria-label="Remove">×</button>`
            + `</span>`
          );
        })();
  } else {
    gpuChip = chip('gpu', 'No GPU');
  }
  const vram = sys.gpu_vram_gb ? `${sys.gpu_vram_gb.toFixed(1)} GB VRAM` : '';
  const ram = `${sys.available_ram_gb?.toFixed(1) || '?'} / ${sys.total_ram_gb?.toFixed(1) || '?'} GB RAM`;
  const cores = `${sys.cpu_cores || '?'} cores`;
  const manual = _manualHwState();
  const manualChip = (sys.manual_hardware || manual)
    ? `<span class="hwfit-hw-chip hwfit-hw-chip-row hwfit-hw-chip-manual" data-hw-chip="manual">`
      + `<button type="button" class="hwfit-hw-chip-toggle" data-hw-chip="manual" title="Using manual hardware">${esc(_manualHwLabel(manual) || 'Manual hardware')}</button>`
      + `<button type="button" class="hwfit-hw-chip-x" data-hw-chip="manual" title="Clear manual hardware" aria-label="Clear">×</button>`
      + `</span>`
    : '';
  el.innerHTML = gpuChip
    + (vram ? chip('vram', vram) : '')
    + chip('ram', ram)
    + chip('cores', cores)
    + chip('backend', esc(sys.backend || ''))
    + manualChip;
  // Body click → toggle "off" (dimmed, still visible). Membership of
  // _dismissedHwChips is what the ranker reads, so both add+remove
  // here also flips the model list. The manual chip is excluded —
  // dimming "manual" has no ranking effect (the key isn't checked),
  // so click-to-toggle there would feel broken. Use × to clear it.
  el.querySelectorAll('.hwfit-hw-chip-toggle').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const key = btn.dataset.hwChip;
      if (!key || key === 'manual') return;
      const row = btn.closest('.hwfit-hw-chip-row');
      if (_dismissedHwChips.has(key)) {
        _dismissedHwChips.delete(key);
        row?.classList.remove('hwfit-hw-chip-off');
      } else {
        _dismissedHwChips.add(key);
        row?.classList.add('hwfit-hw-chip-off');
      }
      _resetGpuToggleState(false);
      _hwfitCache = null;
      _hwfitFetch(true);
    });
  });
  // × button → fully remove the chip from view AND treat it as
  // dismissed for ranking purposes (until next rescan).
  el.querySelectorAll('.hwfit-hw-chip-x').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const key = btn.dataset.hwChip;
      if (!key) return;
      // The manual-hardware chip needs special teardown: clear the
      // saved manual state so the chip doesn't re-render on the next
      // fetch from localStorage. Routes through clearManual() which
      // also collapses the edit panel.
      if (key === 'manual') {
        _saveManualHwState(null);
        btn.closest('.hwfit-hw-chip-row')?.remove();
        document.getElementById('hwfit-manual-panel')?.classList.add('hidden');
        _resetGpuToggleState();
        _hwfitCache = null;
        _hwfitFetch(true);
        return;
      }
      _removedHwChips.add(key);
      _dismissedHwChips.add(key);
      btn.closest('.hwfit-hw-chip-row')?.remove();
      _resetGpuToggleState(false);
      _hwfitCache = null;
      _hwfitFetch(true);
    });
  });
  _wireManualHardwareControls(el);
}

function _wireManualHardwareControls(el) {
  const btn = document.getElementById('hwfit-hw-manual-btn');
  const panel = document.getElementById('hwfit-manual-panel');
  if (!btn || !panel) return;
  const clearManual = () => {
    _saveManualHwState(null);
    el.querySelector('.hwfit-hw-chip-manual')?.remove();
    panel.classList.add('hidden');
    _resetGpuToggleState();
    _hwfitCache = null;
    _hwfitFetch(true);
  };
  const manual = _manualHwState();
  btn.textContent = 'EDIT';
  if (manual) {
    panel.querySelector('.hwfit-manual-mode').value = manual.mode || 'gpu';
    panel.querySelector('.hwfit-manual-backend').value = manual.backend || 'cuda';
  }
  const syncMode = () => {
    const isRam = panel.querySelector('.hwfit-manual-mode')?.value === 'ram';
    panel.querySelector('.hwfit-manual-gpus')?.closest('label')?.style.setProperty('display', isRam ? 'none' : '');
    panel.querySelector('.hwfit-manual-vram')?.closest('label')?.style.setProperty('display', isRam ? 'none' : '');
    const backend = panel.querySelector('.hwfit-manual-backend');
    if (backend) backend.style.display = isRam ? 'none' : '';
  };
  if (!btn._hwfitManualBound) {
    btn._hwfitManualBound = true;
    btn.addEventListener('click', () => {
      panel.classList.toggle('hidden');
      syncMode();
    });
  }
  el.querySelector('.hwfit-hw-chip-toggle[data-hw-chip="manual"]')?.addEventListener('click', () => {
    panel.classList.remove('hidden');
    syncMode();
  });
  if (!panel._hwfitManualBound) {
    panel._hwfitManualBound = true;
    panel.querySelector('.hwfit-manual-mode')?.addEventListener('change', syncMode);
    panel.querySelector('.hwfit-hw-manual-save')?.addEventListener('click', () => {
      const mode = panel.querySelector('.hwfit-manual-mode')?.value || 'gpu';
      const gpuCount = _manualNumber(panel.querySelector('.hwfit-manual-gpus')?.value, 1);
      const vramGb = _manualNumber(panel.querySelector('.hwfit-manual-vram')?.value, 8);
      const ramGb = _manualOptionalNumber(panel.querySelector('.hwfit-manual-ram')?.value);
      const backend = panel.querySelector('.hwfit-manual-backend')?.value || 'cuda';
      const manual = { mode, gpuCount, vramGb, ramGb, backend };
      _saveManualHwState(manual);
      _resetGpuToggleState();
      _hwfitCache = null;
      panel.classList.add('hidden');
      _hwfitRenderHw(el, _manualDisplaySystem(window._hwfitSystemCache, manual));
      _hwfitFetch(true);
    });
    panel.querySelector('.hwfit-hw-manual-clear')?.addEventListener('click', clearManual);
  }
  syncMode();
}

export const _fitColors = { perfect: 'var(--green, #50fa7b)', good: 'var(--yellow, #f1fa8c)', marginal: 'var(--orange, #ffb86c)', too_tight: 'var(--red, #ff5555)' };

export const _hwfitColumns = [
  { key: 'score', label: 'Fit',    cls: 'hwfit-fit' },
  { key: null,    label: 'Model',  cls: 'hwfit-name' },
  { key: 'params',label: 'Param', cls: 'hwfit-c-params' },
  { key: null,    label: 'Quant',  cls: 'hwfit-c-quant' },
  { key: 'vram',  label: 'VRAM',   cls: 'hwfit-c-vram' },
  { key: 'context',label: 'Ctx',   cls: 'hwfit-c-ctx' },
  { key: 'speed', label: 'Speed',  cls: 'hwfit-c-speed' },
  { key: 'score', label: 'Score',  cls: 'hwfit-c-score' },
  { key: null,    label: 'Mode',   cls: 'hwfit-c-mode' },
];

export function _hwfitRenderList(el, models) {
  if (!el) return;
  models = models || [];
  if (!models.length) {
    // Disambiguate WHY the list is empty so capable servers don't read as "too weak":
    // active filters vs. a likely under-reported probe vs. genuinely low hardware.
    const sys = _hwfitCache?.system;
    const hasHw = sys && ((sys.gpu_vram_gb || 0) > 0 || (sys.total_ram_gb || 0) > 8);
    const hasFilters = !!(document.getElementById('hwfit-search')?.value?.trim()
      || document.getElementById('hwfit-usecase')?.value
      || document.getElementById('hwfit-quant')?.value);
    let msg;
    if (hasFilters) msg = 'No models match these filters — try clearing the search, use-case, or quant.';
    else if (hasHw) msg = 'No models fit — the hardware probe may have under-reported. Try Rescan.';
    else msg = 'No models fit your hardware';
    el.innerHTML = `<div class="hwfit-loading">${msg}</div>`;
    return;
  }
  const sortSel = document.getElementById('hwfit-sort');
  const currentSort = sortSel?.value || 'score';
  const isReversed = sortSel?.dataset.reverse === '1';
  let html = '<div class="hwfit-row hwfit-header">';
  for (const col of _hwfitColumns) {
    const sortable = col.key ? ' hwfit-sortable' : '';
    const active = col.key === currentSort ? ' hwfit-sort-active' : '';
    let arrow = '';
    if (col.key === currentSort) {
      // \u25BC = highest first (default), \u25B2 = reversed (lowest first) \u2014 uniform
      // across all columns now.
      arrow = isReversed ? ' \u25B2' : ' \u25BC';
    }
    const dataAttr = col.key ? ` data-sort="${col.key}"` : '';
    html += `<span class="hwfit-col ${col.cls}${sortable}${active}"${dataAttr}>${col.label}${arrow}</span>`;
  }
  html += '</div>';
  for (const m of models) {
    const fitColor = _fitColors[m.fit_level] || 'var(--fg-muted)';
    const score = m.score?.toFixed?.(1) ?? m.score ?? '0';
    let tpsRaw = m.speed_tps ?? 0;
    if (tpsRaw > 9999) tpsRaw = 9999;
    const tps = tpsRaw > 0 ? (tpsRaw >= 100 ? Math.round(tpsRaw) : tpsRaw.toFixed(1)) : '?';
    const pcount = m.parameter_count || '?';
    const ctx = m.context ? (m.context >= 1024 ? (m.context / 1024).toFixed(0) + 'k' : m.context) : '?';
    const fitLabel = (m.fit_level || '').replace('_', ' ');
    const modeLabel = (m.run_mode || '').replace('_', '+');
    const vramLabel = m.required_gb ? m.required_gb.toFixed(1) + 'G' : '?';
    const moeBadge = m.is_moe ? '<span class="hwfit-badge hwfit-moe">MoE</span>' : '';
    const imgBadge = m.is_image_gen ? '<span class="hwfit-badge" style="background:color-mix(in srgb, var(--red) 20%, transparent);color:var(--red);font-size:8px;padding:1px 4px;border-radius:3px;margin-left:4px;">IMG</span>' : '';
    const dlDot = (_cachedModelIds && (_cachedModelIds.has(m.name) || [..._cachedModelIds].some(id => id === m.name?.split('/').pop()))) ? '<span class="hwfit-dl-dot" title="Downloaded">\u25CF</span>' : '';
    html += `<div class="hwfit-row" data-model="${esc(m.name)}">`;
    html += `<span class="hwfit-col hwfit-fit" style="color:${fitColor}">${esc(fitLabel)}</span>`;
    html += `<span class="hwfit-col hwfit-name">${modelLogo(m.name)}${esc(m.name?.split('/').pop() || m.name)}${moeBadge}${imgBadge}${dlDot}</span>`;
    html += `<span class="hwfit-col hwfit-c-params">${esc(pcount)}</span>`;
    html += `<span class="hwfit-col hwfit-c-quant">${esc(m.quant || '?')}</span>`;
    html += `<span class="hwfit-col hwfit-c-vram">${vramLabel}</span>`;
    html += `<span class="hwfit-col hwfit-c-ctx">${m.is_image_gen ? '\u2014' : ctx}</span>`;
    html += `<span class="hwfit-col hwfit-c-speed">${m.is_image_gen ? '\u2014' : tps + ' t/s'}</span>`;
    html += `<span class="hwfit-col hwfit-c-score">${score}</span>`;
    html += `<span class="hwfit-col hwfit-c-mode">${m.is_image_gen ? 'image' : esc(modeLabel)}</span>`;
    html += `</div>`;
  }
  el.innerHTML = html;
  // Click row → expand inline action panel
  el.querySelectorAll('.hwfit-row:not(.hwfit-header)').forEach(row => {
    row.addEventListener('click', () => {
      const name = row.dataset.model;
      if (!name) return;
      // Find model data from cache
      const modelData = (_hwfitCache?.models || []).find(m => m.name === name);
      if (!modelData) return;
      _expandModelRow(row, modelData);
    });
  });
  // Clickable header columns → sort (click again to toggle direction)
  el.querySelectorAll('.hwfit-header .hwfit-sortable').forEach(col => {
    col.addEventListener('click', () => {
      const sortKey = col.dataset.sort;
      if (!sortKey) return;
      const sel = document.getElementById('hwfit-sort');
      if (!sel) return;
      // Toggle direction if clicking the same column
      if (sel.value === sortKey) {
        sel.dataset.reverse = sel.dataset.reverse === '1' ? '0' : '1';
      } else {
        sel.value = sortKey;
        sel.dataset.reverse = '0';
      }
      _hwfitFetch();
    });
  });
}

// Read the server currently selected in the scan dropdown and make it the
// active host. Called right before a download/run so the action targets the
// server the user sees selected — defends against the global remoteHost being
// changed elsewhere (e.g. background serve-task handling) between selecting and
// clicking, which was sending downloads to the wrong host.
// Resolve the server the user currently has selected in the scan dropdown and
// return its host string (''/local for local). Also mirrors it into _envState
// for the command preview. The RETURN VALUE is the source of truth passed to
// the download — never trust _envState.remoteHost downstream (multiple copies).
function _syncHostFromScanDropdown() {
  const ss = document.getElementById('hwfit-server-select');
  if (!ss || ss.value == null) return _envState.remoteHost || '';
  let host = '';
  if (ss.value === 'local') {
    _envState.remoteHost = '';
  } else {
    const s = _serverByVal(ss.value);
    if (s) {
      host = s.host;
      _envState.remoteHost = s.host;
      _envState.env = s.env;
      _envState.envPath = s.envPath;
      _envState.platform = s.platform || '';
    }
  }
  try { _persistEnvState(); } catch {}
  return host;
}

export function _expandModelRow(row, modelData) {
  const list = row.closest('.hwfit-list');
  if (!list) return;

  const existingPanel = list.querySelector('.hwfit-action-panel');
  const wasActive = row.classList.contains('hwfit-row-active');

  // Remove existing panel and active state
  if (existingPanel) existingPanel.remove();
  list.querySelectorAll('.hwfit-row-active').forEach(r => r.classList.remove('hwfit-row-active'));

  // Toggle: if clicking same row, just close
  if (wasActive) return;

  row.classList.add('hwfit-row-active');
  const { backend, label } = _detectBackend(modelData);
  const isVllm = backend === 'vllm';
  const isLlamaCpp = backend === 'llamacpp';
  const ctx = modelData.context || 8192;

  const dlRepo = modelData.quant_repo || modelData.name;
  const hfUrl = `https://huggingface.co/${dlRepo}`;
  let html = `<div class="hwfit-action-panel" data-model-name="${esc(modelData.name)}">`;
  html += `<div class="hwfit-panel-header">`;
  html += `<span class="hwfit-panel-model">${esc(modelData.name)}${modelData.quant_repo ? ` <span style="opacity:0.5;font-size:10px;">(${esc(modelData.quant)})</span>` : ''}</span>`;
  html += `<span class="hwfit-panel-badge">${esc(label)}</span>`;
  html += `<a href="${esc(hfUrl)}" target="_blank" rel="noopener" class="hwfit-panel-hf-link" title="View on HuggingFace">HF \u2197</a>`;
  html += `</div>`;
  html += `<div class="hwfit-panel-actions">`;
  html += `<button class="cookbook-btn hwfit-dl-btn">Download</button>`;
  if (!modelData.is_image_gen) {
    html += `<button class="cookbook-btn cookbook-run-btn hwfit-quickrun-btn" title="Download + launch with smart defaults">Run</button>`;
    html += `<button class="cookbook-btn hwfit-serve-expand-btn" title="Configure & serve">Configure</button>`;
  }
  html += `</div>`;
  if (modelData.is_image_gen) {
    html += `<div style="font-size:10px;opacity:0.5;margin-top:4px;">${esc((modelData.capabilities || []).join(' \u00B7 ') || '')}${modelData.description ? ' \u2014 ' + esc(modelData.description) : ''}</div>`;
  }
  html += `</div>`;

  row.insertAdjacentHTML('afterend', html);
  const panel = row.nextElementSibling;

  // Wire download button
  const dlBtn = panel.querySelector('.hwfit-dl-btn');
  if (dlBtn) {
    dlBtn.addEventListener('click', () => {
      const host = _syncHostFromScanDropdown();   // host the user picked, passed explicitly
      if (backend === 'ollama') {
        _runPanelCmd(panel, _buildDownloadCmd(modelData, backend), { timeout: 0 });
      } else {
        _runModelDownload(panel, modelData, backend, host);
      }
    });
  }

  // Wire quick-run button — download + launch with smart defaults
  const quickRunBtn = panel.querySelector('.hwfit-quickrun-btn');
  if (quickRunBtn) {
    quickRunBtn.addEventListener('click', async () => {
      const _qrHost = _syncHostFromScanDropdown();

      // Don't serve a model that isn't downloaded yet. vLLM/SGLang would
      // background-pull at launch, so the serve task shows up as "running" in
      // the Running tab while nothing is actually served (and llama.cpp just
      // errors "No GGUF found"). The Configure button and the Serve tab already
      // gate on the cached-model list — mirror that here. When the model isn't
      // present, honor the button's "Download" half by kicking off the download
      // instead, then the user can Run again to serve once it finishes.
      const _short = modelData.name.split('/').pop();
      const _downloaded = _cachedModelIds && (
        _cachedModelIds.has(modelData.name)
        || [..._cachedModelIds].some(id => id === modelData.name || id.endsWith('/' + _short))
      );
      if (_cachedModelIds && !_downloaded) {
        uiModule.showToast('Model not downloaded yet — starting download. Run again to serve once it finishes.');
        if (backend === 'ollama') {
          _runPanelCmd(panel, _buildDownloadCmd(modelData, backend), { timeout: 0 });
        } else {
          _runModelDownload(panel, modelData, backend, _qrHost);
        }
        return;
      }

      quickRunBtn.disabled = true;
      quickRunBtn.textContent = 'Starting...';

      // Smart defaults based on hardware and model
      const system = _hwfitCache?.system || {};
      // Prefer the active homogeneous pool (the route sets active_group when a GPU
      // pool is selected). Its per-GPU VRAM + device indices are what we serve on —
      // vLLM can only tensor-parallel across identical GPUs, so we pin to one pool.
      const grp = system.active_group || null;
      const poolCount = (grp && grp.use_count) || system.gpu_count || 1;
      const gpuMem = (grp && grp.vram_each) || (system.gpu_vram_gb / (system.gpu_count || 1)) || 20;
      const modelVram = modelData.required_gb || 10;

      // TP must be a power of two within the pool (plus the exact pool size) —
      // pick the smallest that fits the model in VRAM, else the whole pool.
      const _tpOpts = [1, 2, 4, 8, 16].filter(n => n <= poolCount);
      if (poolCount > 0 && !_tpOpts.includes(poolCount)) _tpOpts.push(poolCount);
      let tp = _tpOpts[_tpOpts.length - 1] || 1;
      for (const n of _tpOpts) { if (n * gpuMem >= modelVram) { tp = n; break; } }

      // Pin to exactly this pool's first `tp` GPUs so vLLM can't reach across into
      // a mismatched pool. Respect a manual GPU pin (_envState.gpus) if the user set one.
      let cudaDevices = '';
      if (grp && Array.isArray(grp.indices)) cudaDevices = grp.indices.slice(0, tp).join(',');
      // Context: scale based on available VRAM headroom
      const headroom = (tp * gpuMem) - modelVram;
      let maxCtx = modelData.context_length || 8192;
      if (headroom < 4) maxCtx = Math.min(maxCtx, 4096);
      else if (headroom < 8) maxCtx = Math.min(maxCtx, 8192);
      else if (headroom < 16) maxCtx = Math.min(maxCtx, 16384);
      // GPU mem utilization
      const gpuUtil = modelVram / (tp * gpuMem) > 0.8 ? '0.95' : '0.90';
      // Tool parser
      const parser = _detectToolParser(modelData.name);

      const host = _envState.remoteHost || '';
      const hostIp = host.includes('@') ? host.split('@').pop() : host;
      const port = '8000';
      const detected = _detectBackend(modelData);
      const runBackend = detected.backend || 'vllm';

      // Build serve command
      let cmd = '';
      if (runBackend === 'sglang') {
        cmd = `python3 -m sglang.launch_server --model-path ${modelData.name} --host 0.0.0.0 --port ${port}`;
        if (tp > 1) cmd += ` --tp ${tp}`;
        cmd += ` --context-length ${maxCtx}`;
        cmd += ` --mem-fraction-static ${gpuUtil}`;
        cmd += ' --trust-remote-code';
      } else if (runBackend === 'llamacpp') {
        const dir = `"$HOME/.cache/huggingface/hub/models--${modelData.name.replace(/\//g, '--')}/snapshots"`;
        const ggufPath = `$({ find ${dir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${dir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`;
        cmd = `MODEL_FILE=${ggufPath} && { [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } || { echo "ERROR: No GGUF found on this host. Download a GGUF quant or switch backend."; exit 1; } && llama-server --model "$MODEL_FILE" --host 0.0.0.0 --port 8080 -ngl 99 -c ${maxCtx} || python3 -m llama_cpp.server --model "$MODEL_FILE" --host 0.0.0.0 --port 8080 --n_gpu_layers 99 --n_ctx ${maxCtx}`;
      } else {
        cmd = `vllm serve ${modelData.name} --host 0.0.0.0 --port ${port}`;
        cmd += ` --tensor-parallel-size ${tp}`;
        cmd += ` --max-model-len ${maxCtx}`;
        cmd += ` --gpu-memory-utilization ${gpuUtil}`;
        cmd += ' --dtype auto';
        cmd += ' --enforce-eager';
        cmd += ' --trust-remote-code';
        cmd += ` --enable-auto-tool-choice --tool-call-parser ${parser}`;
      }

      // Build env prefix
      let envPrefix = '';
      if (_envState.env === 'venv' && _envState.envPath) {
        const p = _envState.envPath;
        envPrefix = 'source ' + _shellQuote(p.endsWith('/bin/activate') ? p : p + '/bin/activate');
      } else if (_envState.env === 'conda' && _envState.envPath) {
        envPrefix = 'eval "$(conda shell.bash hook)" && conda activate ' + _shellQuote(_envState.envPath);
      }

      // Launch via serve API. Field names must match the backend ServeRequest
      // schema (repo_id + cmd) — sending `command`/`model` failed Pydantic
      // validation (422), which is why Run silently did nothing.
      const _srv = (_envState.servers || []).find(s => s.host === host);
      const payload = {
        repo_id: modelData.name,
        cmd: cmd,
        remote_host: host || undefined,
        ssh_port: (_srv && _srv.port) || undefined,
        env_prefix: envPrefix || undefined,
        hf_token: _envState.hfToken || undefined,
        gpus: _envState.gpus || cudaDevices || undefined,
        platform: _envState.platform || undefined,
      };

      try {
        const res = await fetch('/api/model/serve', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.ok) {
          const shortName = modelData.name.split('/').pop();
          _addTask(data.session_id, shortName, 'serve', { _cmd: cmd, model: modelData.name, backend: runBackend, remote_host: host });
          _renderRunningTab();
          uiModule.showToast(`Launching ${shortName}...`);
          // Switch to Running tab
          const runTab = document.querySelector('.cookbook-tab[data-backend="Running"]');
          if (runTab) runTab.click();
        } else {
          uiModule.showError('Launch failed: ' + (data.error || ''));
        }
      } catch (e) {
        uiModule.showError('Launch failed: ' + e.message);
      }
      quickRunBtn.disabled = false;
      quickRunBtn.textContent = 'Run';
    });
  }

  // Wire configure button — open the model's Serve panel.
  const configBtn = panel.querySelector('.hwfit-serve-expand-btn');
  if (configBtn) {
    configBtn.addEventListener('click', async () => {
      const repo = modelData.name;
      const short = repo?.split('/').pop();
      // Use the same "downloaded" source as the dl-dot (_cachedModelIds), NOT a
      // DOM lookup for .hwfit-cached-item — those only exist on the Serve tab, so
      // from the What-Fits tab the old check always failed and falsely said
      // "download first" even for models that ARE downloaded.
      const downloaded = _cachedModelIds && (
        _cachedModelIds.has(repo)
        || [..._cachedModelIds].some(id => id === repo || id.endsWith('/' + short))
      );
      if (_cachedModelIds && !downloaded) {
        uiModule.showToast('Download the model first, then configure from Serve tab');
        return;
      }
      // Downloaded (or cache state unknown) — open the Serve panel, which switches
      // to the Serve tab, fetches the cached list, and expands this model's card.
      try {
        const { openServePanelForRepo } = await import('./cookbookServe.js');
        await openServePanelForRepo(repo);
      } catch (e) {
        uiModule.showToast('Could not open Serve: ' + (e && e.message ? e.message : e));
      }
    });
  }

}

export function _hwfitInit() {
  const uc = document.getElementById('hwfit-usecase');
  const sort = document.getElementById('hwfit-sort');
  const qpref = document.getElementById('hwfit-quant');
  const search = document.getElementById('hwfit-search');
  const remote = document.getElementById('hwfit-host');
  if (uc) uc.addEventListener('change', () => _hwfitFetch());
  if (sort) sort.addEventListener('change', () => _hwfitFetch());
  if (qpref) qpref.addEventListener('change', () => _hwfitFetch());
  // Rescan — force a fresh hardware probe (bypasses the per-host cache).
  const rescan = document.getElementById('hwfit-rescan');
  if (rescan && !rescan.dataset.bound) {
    rescan.dataset.bound = '1';
    rescan.addEventListener('click', async () => {
      if (rescan.dataset.scanning) return;   // ignore re-clicks mid-scan
      rescan.dataset.scanning = '1';
      const orig = rescan.innerHTML;
      rescan.disabled = true;
      rescan.style.opacity = '0.85';
      // Swap the ↻ glyph for a live whirlpool so the click feels responsive
      // during the (often slow) SSH hardware probe.
      const wp = spinnerModule.createWhirlpool(12);
      wp.element.style.marginRight = '4px';
      wp.element.style.position = 'relative';
      wp.element.style.top = '-2px';   // sit a touch higher, aligned with the label
      rescan.innerHTML = '';
      rescan.appendChild(wp.element);
      rescan.appendChild(document.createTextNode('RESCAN'));
      // Reset toggle state (no flicker — buttons stay until the fresh scan swaps them).
      _resetGpuToggleState();
      try {
        await _hwfitFetch(true);
      } finally {
        try { wp.destroy(); } catch {}
        rescan.innerHTML = orig;
        rescan.disabled = false;
        rescan.style.opacity = '';
        delete rescan.dataset.scanning;
      }
    });
  }
  if (search) search.addEventListener('input', () => {
    clearTimeout(_hwfitDebounce);
    _hwfitDebounce = setTimeout(() => _hwfitFetch(), 400);
  });
  // HF Token
  const hfToken = document.getElementById('hwfit-hftoken');
  if (hfToken) {
    hfToken.addEventListener('change', () => { _envState.hfToken = hfToken.value.trim(); _persistEnvState(); });
    hfToken.addEventListener('input', () => { _envState.hfToken = hfToken.value.trim(); });
  }

  // Rebuild all server select dropdowns with current servers
  function _rebuildServerSelect() {
    const selectors = [
      document.getElementById('hwfit-server-select'),
      document.getElementById('hwfit-dl-server'),
    ];
    for (const sel of selectors) {
      if (!sel) continue;
      const currentVal = sel.value;
      let html = `<option value="local">Local</option>`;
      _envState.servers.forEach((s, i) => {
        if (!s.host) return;
        const label = s.name || s.host || `Server ${i + 1}`;
        html += `<option value="${i}">${uiModule.esc(label)}</option>`;
      });
      sel.innerHTML = html;
      sel.value = currentVal;
    }
  }

  // Servers — sync changes, add, remove
  function _syncServers() {
    const entries = document.querySelectorAll('.cookbook-server-entry');
    _envState.servers = [];
    entries.forEach(entry => {
      const row = entry.querySelector('.cookbook-server-row');
      if (!row) return;
      const nameEl = row.querySelector('.cookbook-srv-name');
      const hostEl = row.querySelector('.cookbook-srv-host');
      const name = nameEl?.value.trim() || '';
      const host = (hostEl?.disabled || hostEl?.readOnly) ? '' : (hostEl?.value.trim() || '');
      const port = row.querySelector('.cookbook-srv-port')?.value.trim() || '';
      const env = row.querySelector('.cookbook-srv-env')?.value || 'none';
      const envPath = row.querySelector('.cookbook-srv-path')?.value.trim() || '';
      // Collect model directories from tags. Read the authoritative data-dir
      // attribute, not textContent \u2014 the tag now also holds a download-target
      // icon, and textContent would fold the icon/\u2716 glyph into the path.
      const dirTags = entry.querySelectorAll('.cookbook-modeldir-tag');
      const modelDirs = [];
      dirTags.forEach(tag => {
        const d = (tag.dataset.dir || '').replaceAll('\u2715', '').replaceAll('\u2716', '').trim();
        if (d) modelDirs.push(d);
      });
      if (!modelDirs.length) modelDirs.push('~/.cache/huggingface/hub');
      // Which dir (if any) is flagged as the download target. '' = HF cache.
      const dlEl = entry.querySelector('.cookbook-modeldir-dl.active');
      const downloadDir = dlEl ? (dlEl.dataset.dlDir || '') : '';
      const platform = entry.dataset.platform || '';
      _envState.servers.push({ name, host: host || '', port, env, envPath, modelDirs, modelDir: modelDirs.filter(d => d !== '~/.cache/huggingface/hub')[0] || modelDirs[0], downloadDir, platform });
    });
    // Do NOT auto-change the selected host here. _syncServers can run while the
    // servers DOM is mid-render — host fields that are disabled/readonly read as
    // empty (see above), which made the rebuilt list temporarily miss the
    // selected server. The old code then "fell back" to the first remote server
    // and persisted it, silently flipping the active host even though the
    // dropdown still showed odysseus. The user's selection must only change via
    // an explicit dropdown pick. Here we just refresh env/path if we can match
    // the current host; otherwise leave remoteHost untouched.
    const sel = _envState.servers.find(s => s.host === _envState.remoteHost);
    if (sel) { _envState.env = sel.env; _envState.envPath = sel.envPath; }
    _persistEnvState();
  }

  async function _testServerConnection(entry) {
    const host = entry.querySelector('.cookbook-srv-host')?.value?.trim();
    const port = entry.querySelector('.cookbook-srv-port')?.value?.trim() || '';
    const dot = entry.querySelector('.cookbook-srv-status');
    const msg = entry.querySelector('.cookbook-srv-test-msg');
    const setMsg = (text, color = '') => {
      if (!msg) return;
      msg.textContent = text || '';
      msg.title = text || '';
      msg.style.color = color || '';
      msg.style.opacity = text ? '0.75' : '0.55';
    };
    if (!dot) return;
    if (!host) {
      dot.className = 'cookbook-srv-status';
      dot.title = 'Enter user@host to test';
      setMsg('');
      return;
    }
    dot.className = 'cookbook-srv-status testing';
    dot.title = 'Testing SSH…';
    setMsg('Testing SSH...');
    const pf = port && port !== '22' ? `-p ${port} ` : '';
    const cmd = `ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new ${pf}${host} "echo ok"`;
    const t0 = Date.now();
    try {
      const res = await fetch('/api/shell/exec', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: cmd, timeout: 8 }),
      });
      const data = await res.json();
      const ms = Date.now() - t0;
      const out = (data.stdout || '').trim();
      if (data.exit_code === 0 && out.startsWith('ok')) {
        dot.className = 'cookbook-srv-status ok';
        dot.title = `Reachable · ${ms} ms · use Dependencies to check tmux/HF setup`;
        setMsg(`Connected · ${ms} ms`, 'var(--green,#50fa7b)');
      } else {
        dot.className = 'cookbook-srv-status fail';
        const err = (data.stderr || data.stdout || `exit ${data.exit_code}`).toString().trim().slice(0, 240);
        dot.title = `SSH failed: ${err}`;
        setMsg(`Failed · ${err}`, 'var(--red,#e06c75)');
      }
    } catch (e) {
      dot.className = 'cookbook-srv-status fail';
      dot.title = `Test failed: ${e.message || e}`;
      setMsg(`Failed · ${e.message || e}`, 'var(--red,#e06c75)');
    }
  }

  function _singleQuote(value) {
    return `'${String(value || '').replace(/'/g, `'\"'\"'`)}'`;
  }

  function _serverKeyCommand(host, port, publicKey) {
    const pf = port && port !== '22' ? `-p ${port} ` : '';
    const remote = [
      `KEY=${_singleQuote(publicKey)}`,
      'mkdir -p ~/.ssh',
      'chmod 700 ~/.ssh',
      'touch ~/.ssh/authorized_keys',
      '(grep -qxF "$KEY" ~/.ssh/authorized_keys || printf "%s\\n" "$KEY" >> ~/.ssh/authorized_keys)',
      'chmod 600 ~/.ssh/authorized_keys',
    ].join(' && ');
    return `ssh -o StrictHostKeyChecking=accept-new ${pf}${host} ${_singleQuote(remote)}`;
  }

  async function _fetchCookbookSshKey(generate = false) {
    const res = await fetch('/api/cookbook/ssh-key', {
      method: generate ? 'POST' : 'GET',
      credentials: 'same-origin',
    });
    const data = await res.json();
    if (generate && !data.ok) throw new Error(data.error || 'Failed to generate SSH key');
    return (data.public_key || '').trim();
  }

  async function _populateServerKeyPanel(entry, generate = false) {
    const panel = entry.querySelector('.cookbook-server-key-panel');
    const cmdBox = entry.querySelector('.cookbook-server-key-command');
    const copyBtn = entry.querySelector('.cookbook-server-key-copy');
    const genBtn = entry.querySelector('.cookbook-server-key-gen');
    if (!panel || !cmdBox) return;
    const host = entry.querySelector('.cookbook-srv-host')?.value?.trim() || '';
    const port = entry.querySelector('.cookbook-srv-port')?.value?.trim() || '';
    if (!host || !host.includes('@')) {
      cmdBox.value = 'Enter the server as user@host first.';
      if (copyBtn) copyBtn.disabled = true;
      return;
    }
    if (!/^[A-Za-z0-9._~-]+@[A-Za-z0-9._:-]+$/.test(host) || (port && !/^\d{1,5}$/.test(port))) {
      cmdBox.value = 'Use a plain SSH target like user@host and an optional numeric port.';
      if (copyBtn) copyBtn.disabled = true;
      return;
    }
    if (genBtn) {
      genBtn.disabled = true;
      genBtn.textContent = generate ? 'Generating...' : 'Loading...';
    }
    try {
      let publicKey = await _fetchCookbookSshKey(generate);
      if (!publicKey && !generate) publicKey = await _fetchCookbookSshKey(true);
      cmdBox.value = _serverKeyCommand(host, port, publicKey);
      if (copyBtn) copyBtn.disabled = false;
      if (genBtn) genBtn.textContent = 'Key ready';
    } catch (e) {
      cmdBox.value = e.message || String(e);
      if (copyBtn) copyBtn.disabled = true;
      if (genBtn) genBtn.textContent = 'Generate key';
    } finally {
      if (genBtn) genBtn.disabled = false;
    }
  }

  function _wireServerEntry(entry) {
    // Idempotency guard: _hwfitInit() can run more than once per panel open,
    // and re-wiring would stack duplicate listeners on every control (e.g. the
    // model-dir "+" button would add two tags per click, change handlers fire
    // twice). Bind each entry exactly once.
    if (entry.dataset.wired) return;
    entry.dataset.wired = '1';
    // Inject the status dot once if missing — into the card header next to the
    // server name (was previously the first child of the input row).
    const row = entry.querySelector('.cookbook-server-row');
    const titleEl = entry.querySelector('.cookbook-server-title');
    if (!entry.querySelector('.cookbook-srv-status')) {
      const dot = document.createElement('span');
      dot.className = 'cookbook-srv-status';
      dot.title = 'Click to test SSH';
      dot.addEventListener('click', (e) => { e.stopPropagation(); _testServerConnection(entry); });
      if (titleEl) titleEl.insertBefore(dot, titleEl.firstChild);
      else if (row) row.insertBefore(dot, row.firstChild);
      // The local server (readonly host) is always reachable — show it green
      // without an SSH test.
      const _hostEl = entry.querySelector('.cookbook-srv-host');
      if (_hostEl && (_hostEl.readOnly || _hostEl.disabled)) {
        dot.className = 'cookbook-srv-status ok';
        dot.title = 'Local (this machine)';
      }
    }
    const checkBtn = entry.querySelector('.cookbook-server-check-btn');
    if (checkBtn && !checkBtn.dataset.bound) {
      checkBtn.dataset.bound = '1';
      checkBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _testServerConnection(entry);
      });
    }
    // Default-server toggle: exclusive checkmark in the entry title. The chosen
    // server is what Cookbook lands on (all dropdowns) on the next open.
    const _defBtn = entry.querySelector('.cookbook-srv-default');
    if (_defBtn && !_defBtn.dataset.bound) {
      _defBtn.dataset.bound = '1';
      _defBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = _defBtn.dataset.srvKey || '';
        // Toggle off if it's already the default; otherwise make it the default.
        _envState.defaultServer = (_envState.defaultServer === key) ? '' : key;
        _persistEnvState();
        document.querySelectorAll('.cookbook-srv-default').forEach(b => {
          const on = !!_envState.defaultServer && b.dataset.srvKey === _envState.defaultServer;
          b.classList.toggle('active', on);
          // Keep the "default" label after the icon (don't overwrite it).
          b.innerHTML = (on ? _MODELDIR_CHECK_ON : _MODELDIR_CHECK_OFF) + '<span class="cookbook-srv-default-label">default</span>';
          b.title = on ? 'Default server — Cookbook opens here' : 'Make this the default server';
        });
        // Apply immediately so the dropdowns reflect it without reopening
        // (inline — _applyServerSelection lives in cookbook.js and isn't imported here).
        const _dk = _envState.defaultServer;
        if (_dk) {
          if (_dk === 'local') { _envState.remoteHost = ''; _envState.env = 'none'; _envState.envPath = ''; _envState.platform = ''; }
          else { const _s = (_envState.servers || []).find(x => x.host === _dk); if (_s) { _envState.remoteHost = _s.host; _envState.env = _s.env || 'none'; _envState.envPath = _s.envPath || ''; _envState.platform = _s.platform || ''; } }
          _persistEnvState();
          document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
            if (sel && sel.tagName === 'SELECT') sel.value = _envState.remoteHost || 'local';
          });
        }
        uiModule.showToast(_envState.defaultServer
          ? 'Default server: ' + (_envState.defaultServer === 'local' ? 'Local' : _envState.defaultServer)
          : 'Default server cleared');
      });
    }
    const keyBtn = entry.querySelector('.cookbook-server-key-btn');
    if (keyBtn && !keyBtn.dataset.bound) {
      keyBtn.dataset.bound = '1';
      keyBtn.addEventListener('click', async () => {
        const panel = entry.querySelector('.cookbook-server-key-panel');
        if (!panel) return;
        const willOpen = panel.classList.contains('hidden');
        panel.classList.toggle('hidden', !willOpen);
        panel.style.display = willOpen ? 'flex' : '';
        if (willOpen) await _populateServerKeyPanel(entry, false);
      });
    }
    const keyGenBtn = entry.querySelector('.cookbook-server-key-gen');
    if (keyGenBtn && !keyGenBtn.dataset.bound) {
      keyGenBtn.dataset.bound = '1';
      keyGenBtn.addEventListener('click', () => _populateServerKeyPanel(entry, true));
    }
    const keyCopyBtn = entry.querySelector('.cookbook-server-key-copy');
    if (keyCopyBtn && !keyCopyBtn.dataset.bound) {
      keyCopyBtn.dataset.bound = '1';
      keyCopyBtn.addEventListener('click', async () => {
        const cmd = entry.querySelector('.cookbook-server-key-command')?.value?.trim() || '';
        if (!cmd || cmd.startsWith('Enter ')) return;
        await _copyText(cmd);
        uiModule.showToast('SSH setup command copied');
      });
    }
    entry.querySelectorAll('input, select').forEach(el => {
      el.addEventListener('change', () => {
        const selectedBefore = _envState.remoteHost || '';
        const entryHost = entry.querySelector('.cookbook-srv-host')?.value?.trim() || '';
        _syncServers();
        _rebuildServerSelect();
        if (selectedBefore && selectedBefore === entryHost) {
          _hwfitCache = null;
          _hwfitFetch();
        }
        if (!entry.querySelector('.cookbook-server-key-panel')?.classList.contains('hidden')) {
          _populateServerKeyPanel(entry, false);
        }
      });
    });
    // Auto-test when host or port blur
    entry.querySelectorAll('.cookbook-srv-host, .cookbook-srv-port').forEach(el => {
      el.addEventListener('blur', () => _testServerConnection(entry));
    });
    // Initial test for pre-filled rows (existing servers on tab load)
    if (entry.querySelector('.cookbook-srv-host')?.value?.trim() && !entry.dataset.tested) {
      entry.dataset.tested = '1';
      _testServerConnection(entry);
    }
    // Cancel button on a brand-new server entry: discard it (no confirm — it's
    // unsaved) and re-sync so the dropped blank server doesn't linger.
    const cancelBtn = entry.querySelector('.cookbook-server-cancel-btn');
    if (cancelBtn && !cancelBtn.dataset.bound) {
      cancelBtn.dataset.bound = '1';
      cancelBtn.addEventListener('click', () => {
        entry.remove();
        _syncServers();
        _rebuildServerSelect();
        _hwfitCache = null;
        _hwfitFetch();
      });
    }
    // Save button on a brand-new server entry: persist + confirm with a check.
    const saveBtn = entry.querySelector('.cookbook-server-save-btn');
    if (saveBtn && !saveBtn.dataset.bound) {
      saveBtn.dataset.bound = '1';
      saveBtn.addEventListener('click', () => {
        _syncServers();
        _rebuildServerSelect();
        saveBtn.classList.add('saved');
        saveBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#50fa7b" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><polyline points="20 6 9 17 4 12"/></svg>Saved';
      });
    }
    const rmBtn = entry.querySelector('.cookbook-server-rm');
    if (rmBtn) rmBtn.addEventListener('click', async () => {
      const name = entry.querySelector('.cookbook-srv-name')?.value?.trim()
                || entry.querySelector('.cookbook-srv-host')?.value?.trim()
                || 'this server';
      let ok = true;
      if (uiModule && uiModule.styledConfirm) {
        ok = await uiModule.styledConfirm(`Remove "${name}"?`, { confirmText: 'Remove', danger: true });
      } else {
        ok = confirm(`Remove "${name}"?`);
      }
      if (!ok) return;
      entry.remove();
      _syncServers();
      _rebuildServerSelect();
      _hwfitCache = null;
      _hwfitFetch();
    });
    // Setup is owned by cookbook.js's delegated handler (Settings behavior:
    // select server + open the Dependencies tab). Don't bind the inline-install
    // handler here too, or one click would do two conflicting things.
    const setupBtn = null;
    if (setupBtn) {
      setupBtn.addEventListener('click', async () => {
        const host = entry.querySelector('.cookbook-srv-host')?.value?.trim();
        const port = entry.querySelector('.cookbook-srv-port')?.value?.trim() || '';
        if (!host) return;
        setupBtn.disabled = true;
        const origText = setupBtn.textContent;
        setupBtn.textContent = 'Installing...';
        try {
          const res = await fetch('/api/cookbook/setup', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host, ssh_port: port || undefined }),
          });
          const data = await res.json();
          if (data.ok) {
            setupBtn.textContent = '\u2713 Done';
            setupBtn.style.color = '#50fa7b';
            uiModule.showToast(`Setup complete (${data.platform})`);
            // Store detected platform on the server entry
            if (data.platform) {
              entry.dataset.platform = data.platform;
              _syncServers();
              // Show platform badge
              const existingBadge = entry.querySelector('.cookbook-platform-badge');
              if (existingBadge) existingBadge.remove();
              const badge = document.createElement('span');
              badge.className = 'cookbook-platform-badge';
              badge.style.cssText = 'font-size:8px;padding:1px 5px;border-radius:3px;border:1px solid ' + (data.platform === 'windows' ? 'var(--cyan,#56b6c2)' : 'var(--green,#98c379)') + ';color:' + (data.platform === 'windows' ? 'var(--cyan,#56b6c2)' : 'var(--green,#98c379)') + ';opacity:0.7;white-space:nowrap;flex-shrink:0;';
              badge.textContent = data.platform;
              setupBtn.parentNode.insertBefore(badge, setupBtn);
            }
            // Auto-set Termux model dir
            if (data.platform === 'termux') {
              const container = entry.querySelector('.cookbook-modeldirs');
              if (container) {
                const existing = [...container.querySelectorAll('.cookbook-modeldir-tag')].map(t => t.textContent.replace('\u2716', '').replace('\u2715', '').trim());
                const termuxDir = '/data/data/com.termux/files/home/models';
                if (!existing.includes(termuxDir)) {
                  const tag = document.createElement('span');
                  tag.className = 'cookbook-modeldir-tag';
                  tag.dataset.dirIdx = existing.length;
                  tag.innerHTML = `${uiModule.esc(termuxDir)} <span class="cookbook-modeldir-rm" title="Remove">\u2715</span>`;
                  tag.querySelector('.cookbook-modeldir-rm').addEventListener('click', () => { tag.remove(); _syncServers(); });
                  const addBtn = container.querySelector('.cookbook-modeldir-add');
                  if (addBtn) container.insertBefore(tag, addBtn);
                  else container.appendChild(tag);
                  _syncServers();
                }
              }
            }
          } else {
            setupBtn.textContent = 'Failed';
            setupBtn.style.color = 'var(--red)';
            uiModule.showError(data.error || data.output || 'Setup failed');
          }
        } catch (e) {
          setupBtn.textContent = 'Error';
          setupBtn.style.color = 'var(--red)';
          uiModule.showError(e.message);
        }
        setTimeout(() => { setupBtn.disabled = false; setupBtn.textContent = origText; setupBtn.style.color = ''; }, 3000);
      });
    }
    // Model dir add/remove
    const addDirBtn = entry.querySelector('.cookbook-modeldir-add');
    if (addDirBtn) addDirBtn.addEventListener('click', () => {
      const raw = prompt('Model directory path:', '/data/models');
      if (!raw) return;
      const dir = raw.replaceAll('\u2715', '').replaceAll('\u2716', '').trim();
      if (!dir) return;
      // Don't add duplicates
      const existing = [...entry.querySelectorAll('.cookbook-modeldir-tag')].some(t => (t.dataset.dir || t.textContent.trim()) === dir);
      if (existing) return;
      const container = entry.querySelector('.cookbook-modeldirs');
      const tag = document.createElement('span');
      tag.className = 'cookbook-modeldir-tag';
      tag.dataset.dirIdx = container.querySelectorAll('.cookbook-modeldir-tag').length;
      tag.dataset.dir = dir;
      tag.innerHTML = `<span class="cookbook-modeldir-dl" title="Send downloads here" data-dl-dir="${uiModule.esc(dir)}">${_MODELDIR_CHECK_OFF}</span> ${uiModule.esc(dir)} <span class="cookbook-modeldir-rm" title="Remove">\u2716</span>`;
      tag.querySelector('.cookbook-modeldir-rm').addEventListener('click', () => { tag.remove(); _syncServers(); });
      _wireModelDirTarget(entry, tag.querySelector('.cookbook-modeldir-dl'));
      container.insertBefore(tag, addDirBtn);
      _syncServers();
    });
    entry.querySelectorAll('.cookbook-modeldir-rm').forEach(rm => {
      rm.addEventListener('click', () => { rm.closest('.cookbook-modeldir-tag').remove(); _syncServers(); });
    });
    // Download-target toggles: clicking one makes that dir the sole target for
    // this server (or the default HF cache if it's the default dir).
    entry.querySelectorAll('.cookbook-modeldir-dl').forEach(dl => _wireModelDirTarget(entry, dl));
  }

  // Mark a model-dir tag as this server's download target (exclusive), then
  // persist. Clicking ANYWHERE on the tag (not just the check) selects it \u2014
  // except the remove \u2716, which has its own handler.
  function _wireModelDirTarget(entry, dlEl) {
    if (!dlEl) return;
    const tag = dlEl.closest('.cookbook-modeldir-tag');
    if (!tag || tag.dataset.dlBound) return;
    tag.dataset.dlBound = '1';
    tag.style.cursor = 'pointer';
    tag.addEventListener('click', (e) => {
      if (e.target.closest('.cookbook-modeldir-rm')) return;   // remove handled elsewhere
      e.stopPropagation();
      entry.querySelectorAll('.cookbook-modeldir-dl').forEach(d => {
        d.classList.remove('active');
        d.innerHTML = _MODELDIR_CHECK_OFF;          // uncheck the others
        d.closest('.cookbook-modeldir-tag')?.classList.remove('cookbook-modeldir-target');
        d.title = 'Send downloads here';
      });
      dlEl.classList.add('active');
      dlEl.innerHTML = _MODELDIR_CHECK_ON;           // check the chosen one
      tag.classList.add('cookbook-modeldir-target');
      dlEl.title = 'Downloads go here';
      _syncServers();
      uiModule.showToast((dlEl.dataset.dlDir ? 'Downloads \u2192 ' + dlEl.dataset.dlDir : 'Downloads \u2192 default HF cache'));
    });
  }

  document.querySelectorAll('.cookbook-server-entry').forEach(_wireServerEntry);

  const addBtn = document.getElementById('cookbook-server-add');
  if (addBtn && !addBtn.dataset.bound) {
    addBtn.dataset.bound = '1';
    addBtn.addEventListener('click', () => {
      const list = document.getElementById('cookbook-servers-list');
      if (!list) return;
      const idx = list.children.length;
      // Build the new entry with the SAME template as existing servers (Model
      // Directory header, default checkmark, platform icon) \u2014 isNew swaps the
      // delete button for a Save button. forceRemote keeps it editable.
      const blank = { host: '', name: '', port: '', env: 'none', envPath: '', platform: '', modelDirs: ['~/.cache/huggingface/hub'] };
      const wrap = document.createElement('div');
      wrap.innerHTML = _serverEntryHtml(blank, idx, _envState.defaultServer || '', true, true);
      const entry = wrap.firstElementChild;
      list.appendChild(entry);
      _wireServerEntry(entry);
      _syncServers();
      // Also refresh the server select dropdown
      _rebuildServerSelect();
      entry.querySelector('.cookbook-srv-host')?.focus();
    });
  }

  // Server selector dropdown
  const serverSelect = document.getElementById('hwfit-server-select');
  if (serverSelect && !serverSelect.dataset.bound) {
    serverSelect.dataset.bound = '1';
    serverSelect.addEventListener('change', () => {
      const val = serverSelect.value;
      if (val === 'local') {
        _envState.remoteHost = '';
        _envState.env = 'none';
        _envState.envPath = '';
      } else {
        const s = _serverByVal(val);
        if (s) {
          _envState.remoteHost = s.host;
          _envState.env = s.env;
          _envState.envPath = s.envPath;
        }
      }
      _persistEnvState();
      // Keep the other server dropdowns (Download / Cache / Deps) in sync. The
      // download-input button reads #hwfit-dl-server *directly*, so without this
      // it kept its old value and downloads went to the wrong host even
      // though the scan here correctly switched to the selected server.
      // Option values are host strings now ('local' for the local box).
      document.querySelectorAll('#hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
        if (!sel || sel.tagName !== 'SELECT') return;
        sel.value = _envState.remoteHost || 'local';
      });
      _hwfitCache = null;
      // Reset GPU-toggle state (no flicker) so the new server's hardware re-renders.
      _resetGpuToggleState();
      _hwfitFetch();
    });
  }

}

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
  _serverKey,
  _currentServerValue,
  _shellQuote,
  _MODELDIR_CHECK_ON,
  _MODELDIR_CHECK_OFF,
  _serverEntryHtml,
  _serverDefaultHtml,
  _applyServerSelectColor,
  _syncServerSelectColors,
  _copyText,
  // Import cookbook.js WITHOUT a ?v= query — the same plain specifier every other
  // importer uses. A query mismatch loads cookbook.js twice as two separate modules
  // (two _envState objects), which silently sent downloads to the wrong server.
} from './cookbook.js';
import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import { _loadTasks, _tmuxGracefulKill, _nextAvailablePort, _taskPort } from './cookbookRunning.js';
import { openCookbookDependencies } from './cookbook-diagnosis.js';

// Map a serve-backend code (vllm / sglang / llamacpp / mlx) → the package name
// the Dependencies API reports. Used to look up "is this backend installed
// on the target server" before firing a launch.
const _BACKEND_PKG = { vllm: 'vllm', sglang: 'sglang', llamacpp: 'llama_cpp', mlx: 'mlx_lm' };

function _normalizeCookbookModelDir(dir) {
  const d = String(dir || '').replaceAll('\u2715', '').replaceAll('\u2716', '').trim();
  return /^(home|mnt|media|data|opt|srv|var)\//.test(d) ? `/${d}` : d;
}

function _wireServerColorPicker(entry) {
  const wrap = entry.querySelector('.cookbook-srv-color-wrap');
  const select = entry.querySelector('.cookbook-srv-color');
  const btn = entry.querySelector('.cookbook-srv-color-btn');
  const menu = entry.querySelector('.cookbook-srv-color-menu');
  if (!wrap || !select || !btn || !menu || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  const close = () => {
    menu.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  };
  const open = () => {
    document.querySelectorAll('.cookbook-srv-color-menu').forEach(m => {
      if (m !== menu) m.classList.add('hidden');
    });
    menu.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
  };
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden')) open();
    else close();
  });
  menu.querySelectorAll('.cookbook-srv-color-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const color = item.dataset.color || '';
      select.value = color;
      const label = item.querySelector('span:last-child')?.textContent || 'Auto';
      const labelEl = btn.querySelector('.cookbook-srv-color-label');
      if (labelEl) labelEl.textContent = label;
      const swatch = item.style.getPropertyValue('--swatch-color') || color;
      if (/^#[0-9a-fA-F]{6}$/.test(swatch.trim())) {
        entry.style.setProperty('--cookbook-server-color', swatch.trim());
        wrap.style.setProperty('--cookbook-server-color', swatch.trim());
      }
      menu.querySelectorAll('.cookbook-srv-color-item').forEach(b => b.classList.toggle('active', b === item));
      close();
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });
  document.addEventListener('click', close);
}

// Pre-launch: ask the deps API whether the chosen backend is present on
// the target server. Returns true if it's good to go, false if we should
// block and route the user into Dependencies.
async function _ensureBackendInstalled(runBackend, host, port, envPath, modelName) {
  const pkgName = _BACKEND_PKG[runBackend];
  if (!pkgName) return true; // unknown backend — don't block
  try {
    const params = new URLSearchParams();
    if (host) {
      params.set('host', host);
      if (port) params.set('ssh_port', String(port));
      if (envPath) params.set('venv', envPath);
    }
    const r = await fetch('/api/cookbook/packages' + (params.toString() ? '?' + params : ''));
    const d = await r.json();
    const pkg = (d.packages || []).find(p => p.name === pkgName);
    if (pkg && pkg.installed) return true;
  } catch (_) {
    // If we can't tell, don't block — the server's own serve route will
    // surface a clearer error anyway.
    return true;
  }
  const targetLabel = host || 'this server';
  uiModule.showToast(
    `${pkgName} not installed on ${targetLabel}. Opening Dependencies — pick your model and click Run.`,
    6000
  );
  openCookbookDependencies(pkgName, { expandRecipe: pkgName, model: modelName });
  return false;
}

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

function _firstGgufSource(model) {
  const sources = Array.isArray(model?.gguf_sources) ? model.gguf_sources : [];
  return sources.find(src => src && src.repo) || null;
}

function _looksLikeGgufRepo(model) {
  const haystack = `${model?.quant_repo || ''} ${model?.repo_id || ''} ${model?.path || ''} ${model?.name || ''}`.toLowerCase();
  return !!model?.is_gguf || haystack.includes('gguf') || haystack.includes('.gguf');
}

function _downloadSourceRepo(model, backend) {
  if (backend === 'llamacpp') {
    const ggufSource = _firstGgufSource(model);
    if (ggufSource) return { repo: ggufSource.repo, kind: 'GGUF' };
    if (_looksLikeGgufRepo(model)) {
      const repo = model?.quant_repo || model?.repo_id || model?.name;
      if (repo) return { repo, kind: 'GGUF' };
    }
  }
  return { repo: model?.quant_repo || model?.name || '', kind: '' };
}

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
  // Update on every scan that returns a positive total — previously this
   // only set on the first scan, so switching servers (e.g. local 1-GPU
   // first, then a 4-GPU remote) left the Run-panel GPU buttons stuck on
   // the original count. Zero/missing totals still don't clobber a known
   // good value (avoids flicker during an in-flight re-probe).
  if (total > 0) _gpuToggleTotal = total;

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
  // Commit the data layer to maxGpu on initial render so it matches the
  // visual highlight. Before this, _activeCount stayed undefined → no
  // gpu_count param sent → backend's fallback could rank against RAM on
  // mixed-resource boxes ("tightest" sorted by RAM instead of GPU).
  //
  // On boxes where total RAM > total VRAM, default to RAM (count=0) instead
  // — RAM is the dominant pool so it's the better starting filter.
  if (container._activeCount === undefined) {
    const ramGb = Number(system.total_ram_gb) || 0;
    const vramGb = Number(system.gpu_vram_gb) || 0;
    if (ramGb > vramGb) {
      container._activeCount = 0;
    } else if (validCounts.length) {
      container._activeCount = maxGpu;
    }
  }
  html += '<button class="hwfit-gpu-btn" data-count="0" title="CPU / RAM only">RAM</button>';
  const hasExplicitCount = typeof container._activeCount === 'number';
  for (const n of validCounts) {
    const text = n === 1 ? 'GPU' : n + ' GPU';
    const isActive = hasExplicitCount && n === container._activeCount;
    html += `<button class="hwfit-gpu-btn${isActive ? ' active' : ''}" data-count="${n}" title="${n} GPU${n > 1 ? 's' : ''}">${text}</button>`;
  }
  // Also mark the RAM button active when the user explicitly chose RAM (0)
  // — the loop above only handles GPU buttons.
  if (container._activeCount === 0) {
    const ramBtn = container.querySelector('.hwfit-gpu-btn[data-count="0"]');
    // (we just set innerHTML so we re-mark below after assignment)
  }
  container.innerHTML = html;
  if (container._activeCount === 0) {
    const ramBtn = container.querySelector('.hwfit-gpu-btn[data-count="0"]');
    if (ramBtn) ramBtn.classList.add('active');
  }

  // Pool dropdown: switch pools, reset the count to the new pool's max, rebuild.
  const sel = container.querySelector('#hwfit-gpu-group');
  if (sel) {
    sel.addEventListener('change', () => {
      container._activeGroup = parseInt(sel.value) || 0;
      container._activeCount = undefined;   // default to the new pool's max
      delete container.dataset.rendered;    // force a count-button rebuild
      _renderGpuToggles(system);
      _hwfitFetch(false, { keepPrevious: true, forceRevalidate: true });
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
        // Auto-suggest a quant based on hardware selection — but ONLY when the
        // user has already picked a specific quant. When they're on "All"
        // (value === ""), leave them on All: toggling a GPU shouldn't silently
        // yank them out of the All view they wanted to see.
        const quantSel = document.getElementById('hwfit-quant');
        if (quantSel && quantSel.value !== '') {
          if (count <= 1) {
            quantSel.value = 'Q4_K_M'; // RAM or 1 GPU -> Q4 sweet spot
          } else if (String(system?.backend || '').toLowerCase() === 'rocm') {
            quantSel.value = 'Q4_K_M'; // ROCm default stays GGUF/local-safe; AWQ is explicit only
          } else {
            quantSel.value = 'AWQ-4bit'; // Multi-GPU -> AWQ for vLLM
          }
        }
      }
      _hwfitFetch(false, { keepPrevious: true, forceRevalidate: true });
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
const _CTX_KEY = 'hwfit_target_context_v1';
const _CTX_PRESETS = [8192, 16384, 32768, 50000, 131072, 0]; // 0 = model max
const _SCAN_CACHE_MAX = 12;            // keep the newest N signatures
const _SCAN_CACHE_TTL = 6 * 3600 * 1000; // 6 h — hardware rarely changes

// Ctx slider helpers (ported from origin/main). The slider picks an INDEX into
// _CTX_PRESETS; _ctxValue() resolves it to a token count (0 = "Max"). The label
// next to the slider re-renders to "8k" / "16k" / … / "Max".
function _ctxLabel(value) {
  const n = Number(value) || 0;
  if (!n) return 'Max';
  return n >= 1000 ? Math.round(n / 1000) + 'k' : String(n);
}

function _ctxValue() {
  const slider = document.getElementById('hwfit-context');
  const idx = Math.max(0, Math.min(_CTX_PRESETS.length - 1, Number(slider?.value ?? 3) || 0));
  return _CTX_PRESETS[idx] || 0;
}

function _syncCtxControl() {
  const slider = document.getElementById('hwfit-context');
  const label = document.getElementById('hwfit-context-label');
  if (!slider) return;
  const saved = localStorage.getItem(_CTX_KEY);
  const savedIdx = saved == null ? 3 : _CTX_PRESETS.indexOf(Number(saved));
  slider.value = String(savedIdx >= 0 ? savedIdx : 3);
  if (label) label.textContent = _ctxLabel(_ctxValue());
}

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
  const tc = document.getElementById('hwfit-gpu-toggles');
  return JSON.stringify({
    h: _envState.remoteHost || '',
    hk: _currentServerValue(),
    u: document.getElementById('hwfit-usecase')?.value || '',
    s: document.getElementById('hwfit-search')?.value?.trim() || '',
    q: document.getElementById('hwfit-quant')?.value || '',
    c: _ctxValue(),
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

function _readNearestScanCache(sig) {
  try {
    const wanted = JSON.parse(sig || '{}');
    const all = JSON.parse(localStorage.getItem(_SCAN_CACHE_KEY) || '{}');
    let best = null;
    for (const [key, entry] of Object.entries(all)) {
      if (!entry || !entry.data || (Date.now() - (entry.ts || 0)) >= _SCAN_CACHE_TTL) continue;
      let parsed = null;
      try { parsed = JSON.parse(key); } catch { continue; }
      if (!parsed) continue;
      if ((parsed.h || '') !== (wanted.h || '')) continue;
      if ((parsed.hk || '') !== (wanted.hk || '')) continue;
      if (JSON.stringify(parsed.m || {}) !== JSON.stringify(wanted.m || {})) continue;
      if (JSON.stringify(parsed.d || []) !== JSON.stringify(wanted.d || [])) continue;
      if (!best || (entry.ts || 0) > (best.ts || 0)) best = entry;
    }
    return best?.data || null;
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

// Client-side "Engine" filter (llama.cpp / vLLM / SGLang / Ollama / Diffusers). Empty =
// show all. Uses the same _detectBackend() the serve commands use, so what you
// filter to is exactly what would be launched. Pure view filter — no refetch
// needed. Ollama rows are merged into the main list (see _ensureOllamaLib +
// _ollamaToHwfitRows below) so the filter handles all engines uniformly.
function _applyEngineFilter(models) {
  const want = document.getElementById('hwfit-engine')?.value || '';
  if (!want || !Array.isArray(models)) return models || [];
  return models.filter(m => {
    try { return _detectBackend(m).backend === want; } catch { return true; }
  });
}

// Ollama library cache (per-page). Filled lazily on first _hwfitFetch; the raw
// list is the same shape returned by /api/cookbook/ollama/library, then turned
// into per-tag hwfit rows so they slot into the main list grid alongside HF
// scan results.
let _ollamaLibCache = null;
async function _ensureOllamaLib() {
  if (_ollamaLibCache) return _ollamaLibCache;
  try {
    const res = await fetch('/api/cookbook/ollama/library');
    const data = await res.json();
    _ollamaLibCache = Array.isArray(data?.models) ? data.models : [];
  } catch { _ollamaLibCache = []; }
  return _ollamaLibCache;
}

// Convert an Ollama library entry's sizes into per-tag hwfit rows. Shape
// matches what _hwfitRenderList expects (fit_level, parameter_count,
// required_gb, score, …) so the rows render identically to HF results.
function _olParseSize(s) {
  // "14b" → 14, "1.5b" → 1.5, "8x7b" → 56 (rough), "135m" → 0.135, "latest" → null
  if (!s) return null;
  const low = s.toLowerCase();
  let m = low.match(/^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)b$/);
  if (m) return parseFloat(m[1]) * parseFloat(m[2]);
  m = low.match(/^(\d+(?:\.\d+)?)b$/);
  if (m) return parseFloat(m[1]);
  m = low.match(/^(\d+(?:\.\d+)?)m$/);
  if (m) return parseFloat(m[1]) / 1000;
  return null;
}
function _ollamaToHwfitRows(libModels, vramAvail, ramAvail) {
  const out = [];
  if (!Array.isArray(libModels)) return out;
  const _ramFitLevel = (need, budget) => {
    if (!need || !budget || need > budget) return 'too_tight';
    const ratio = need / budget;
    if (ratio <= 0.50) return 'perfect';
    if (ratio <= 0.78) return 'good';
    return 'marginal';
  };
  for (const m of libModels) {
    const sizes = (Array.isArray(m.sizes) && m.sizes.length) ? m.sizes : ['latest'];
    for (const sz of sizes) {
      const params = _olParseSize(sz);
      // Ollama default GGUF is ~Q4_K_M. Rough VRAM estimate: 0.6 GB / B.
      const vramGb = params ? params * 0.6 : 0;
      let fitLevel = 'no_fit';
      if (vramGb && vramAvail) {
        if (vramGb <= vramAvail * 0.6) fitLevel = 'perfect';
        else if (vramGb <= vramAvail) fitLevel = 'good';
        else if (ramAvail && vramGb <= ramAvail) fitLevel = _ramFitLevel(vramGb, ramAvail);
        else fitLevel = 'too_tight';
      } else if (vramGb && ramAvail && vramGb <= ramAvail) {
        fitLevel = _ramFitLevel(vramGb, ramAvail);
      }
      const tag = `${m.name}:${sz}`;
      const paramsLabel = params
        ? (params >= 1 ? params.toFixed(params >= 10 ? 0 : 1) + 'B' : (params * 1000).toFixed(0) + 'M')
        : '?';
      // A modest score so Ollama rows still sort sensibly in the default
      // score view — bigger models get a slightly higher base, but they
      // always come in below well-scored HF results. Sort by Fit or VRAM
      // to surface them more aggressively.
      const score = params ? Math.min(30 + params * 0.3, 60) : 25;
      out.push({
        name: tag,
        repo_id: tag,
        quant: 'Q4_K_M',
        parameter_count: paramsLabel,
        params_b: params || 0,
        required_gb: vramGb,
        fit_level: fitLevel,
        score,
        speed_tps: 0,
        context: 0,
        is_gguf: true,
        backend: 'ollama',
        _isOllama: true,
        _olName: m.name,
        _olSize: sz,
        _description: m.description || '',
      });
    }
  }
  return out;
}

export async function _hwfitFetch(fresh = false, opts = {}) {
  const _tk = ++_hwfitFetchToken;
  const allowNetwork = fresh || opts.allowNetwork !== false;
  const keepPrevious = !!opts.keepPrevious;
  const forceRevalidate = !!opts.forceRevalidate;
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
  let _cached = fresh ? null : _readScanCache(_sig);
  if (!_cached && !fresh && (!allowNetwork || keepPrevious)) {
    _cached = _readNearestScanCache(_sig);
  }
  const wp = spinnerModule.createWhirlpool(18);
  const _paintedFromCache = !!_cached;
  if (_cached) {
    // Tag the restored cache with its host too (scan-sig keys cache per
    // host, so a hit here is always for the current remoteHost).
    _hwfitCache = { ..._cached, _scannedHost: remoteHost || '' };
    _hwfitRenderHw(hw, _cached.system);
    if (!remoteHost && _cached.system && _cached.system.platform) {
      _envState.platform = _cached.system.platform;
    }
    _hwfitRenderList(list, _applyEngineFilter(_cached.models));
  } else {
    const canKeepPrevious = keepPrevious && _hwfitCache && Array.isArray(_hwfitCache.models);
    if (canKeepPrevious) {
      try { wp.destroy(); } catch {}
    } else if (!allowNetwork) {
      _hwfitCache = null;
      _hwfitRenderHw(hw, null);
      const loadingDiv = document.createElement('div');
      loadingDiv.className = 'hwfit-loading';
      loadingDiv.style.cssText = 'flex-direction:column;gap:6px;text-align:center;';
      loadingDiv.appendChild(wp.element);
      const loadingTitle = document.createElement('div');
      loadingTitle.textContent = 'No cached scan yet';
      loadingTitle.style.cssText = 'font-size:12px;opacity:0.7;';
      const loadingLbl = document.createElement('div');
      loadingLbl.textContent = 'Loading model list…';
      loadingLbl.style.cssText = 'font-size:11px;opacity:0.55;max-width:420px;line-height:1.4;';
      loadingDiv.appendChild(loadingTitle);
      loadingDiv.appendChild(loadingLbl);
      list.innerHTML = '';
      list.appendChild(loadingDiv);
      setTimeout(() => {
        if (_tk === _hwfitFetchToken) {
          _resetGpuToggleState();
          _hwfitFetch(true, { autoFromEmpty: true });
        }
      }, 60);
      return;
    }
    if (!canKeepPrevious) {
      // Show spinner while scanning — stack the spinner above a text label
      // (the .hwfit-loading class is a centered flex ROW, so force column here).
      const loadingDiv = document.createElement('div');
      loadingDiv.className = 'hwfit-loading';
      loadingDiv.style.flexDirection = 'column';
      loadingDiv.style.gap = '6px';
      loadingDiv.appendChild(wp.element);
      // Text label like the other cookbook tabs. Only fresh rescans are hardware
      // probes; normal refreshes are just model ranking/loading from cached hw.
      const loadingLbl = document.createElement('div');
      loadingLbl.textContent = fresh ? 'Scanning hardware…' : 'Loading models…';
      loadingLbl.style.cssText = 'text-align:center;opacity:0.5;font-size:11px;';
      loadingDiv.appendChild(loadingLbl);
      setTimeout(() => {
        if (loadingLbl.isConnected) loadingLbl.textContent = fresh ? 'Scanning hardware…' : 'Loading model list…';
      }, 2000);
      list.innerHTML = '';
      list.appendChild(loadingDiv);
      _hwfitCache = null;   // no instant paint — clear until the fetch returns
    }
  }
  if (!allowNetwork) {
    try { wp.destroy(); } catch {}
    return;
  }
  // Only fetch cached model IDs when server changes, not on every search/sort
  const remoteKey = _currentServerValue();
  if (!_cachedModelIds || _lastCacheHost() !== remoteKey) {
    const _cacheSrv = _serverByVal(_envState.remoteServerKey || remoteHost);
    const _cachePort = _cacheSrv?.port || '';
    const _cacheParams = new URLSearchParams();
    if (remoteHost) {
      _cacheParams.set('host', remoteHost);
      if (_cachePort) _cacheParams.set('ssh_port', _cachePort);
      if (_cacheSrv?.platform) _cacheParams.set('platform', _cacheSrv.platform);
    }
    fetch(`/api/model/cached?${_cacheParams}`, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(d => {
        if (d && d.error) throw new Error(d.error);
        // Exclude stalled (download-shell) entries — a 12 KB README-only
        // folder shouldn't count as "downloaded" in the Scan/Download list.
        _cachedModelIds = new Set((d.models || []).filter(m => m.status !== 'stalled').map(m => m.repo_id));
        _setLastCacheHost(remoteKey);
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
      }).catch((err) => {
        console.warn('Cached model marker scan failed:', err);
        _setLastCacheHost('');
      });
  }
  try {
    const sortBy = document.getElementById('hwfit-sort')?.value || 'newest';
    const quantPref = document.getElementById('hwfit-quant')?.value || '';
    const targetCtx = _ctxValue();
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
    // Sorting is a table operation, not a different backend query. Fetch a
    // broad candidate set once, then sort it client-side so VRAM/Params/etc.
    // do not appear to "filter out" rows by returning a different top-80 slice.
    const params = new URLSearchParams({ limit: '2500', sort: 'score' });
    if (fresh) params.set('fresh', '1');   // bypass the hardware-scan cache
    if (search) params.set('search', search);
    if (remoteHost) {
      params.set('host', remoteHost);
      const _srv = _serverByVal(_envState.remoteServerKey || remoteHost);
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
    if ((fresh || (_paintedFromCache && !search)) && !isImageMode) {
      params.set('refresh_catalog', '1'); // update HF-backed dynamic catalogs in the background
    }
    if (!isImageMode) {
      if (useCase) params.set('use_case', useCase);
      if (quantPref) params.set('quant', quantPref);
      if (targetCtx) params.set('ctx', String(targetCtx));
      // Fit-only filter — set by the dot in the Fit column header.
      const _fitOnly = (() => { try { return localStorage.getItem('hwfit_fit_only_v1') === '1'; } catch { return false; } })();
      if (_fitOnly) params.set('fit_only', '1');
    }
    const endpoint = isImageMode ? `/api/hwfit/image-models?${params}` : `/api/hwfit/models?${params}`;
    const res = await fetch(endpoint);
    // A newer scan started while this one was in flight (user switched servers
    // mid-probe) — drop this stale response so it can't clobber the new one.
    if (_tk !== _hwfitFetchToken) { try { wp.destroy(); } catch {} return; }
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      let msg = '';
      try {
        const payload = JSON.parse(body);
        msg = payload && (payload.detail || payload.error || payload.message);
      } catch {
        msg = body;
      }
      msg = typeof msg === 'string' ? msg.trim() : '';
      throw new Error(`HTTP ${res.status} ${res.statusText}${msg ? `: ${msg}` : ''}`);
    }
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
    // Merge Ollama library rows into the main list so they appear with the
    // same Fit/Param/Quant/VRAM/Mode columns as HF results and respond to the
    // Engine filter. Skipped in image-gen mode (Ollama doesn't serve diffusers).
    if (!isImageMode) {
      const _vramAvail = data.system?.gpu_vram_gb || 0;
      const _ramAvail = data.system?.total_ram_gb || 0;
      const _lib = await _ensureOllamaLib();
      const _olRows = _ollamaToHwfitRows(_lib, _vramAvail, _ramAvail);
      // Search filter on Ollama rows: HF API already filters by search; do the
      // same client-side over Ollama name + description so the search box
      // works consistently across both sources.
      const _s = (search || '').trim().toLowerCase();
      const _olFiltered = _s
        ? _olRows.filter(r => r.name.toLowerCase().includes(_s) || (r._description || '').toLowerCase().includes(_s))
        : _olRows;
      data.models = (data.models || []).concat(_olFiltered);
    }
    // Tag the cache with the host this scan was for, so downstream
    // code (_gpuEnvVarName, backend-aware command builders) can avoid
    // trusting a stale scan when the user switches the server picker
    // to a different target without re-running hwfit.
    _hwfitCache = { ...data, _scannedHost: remoteHost || '' };
    _hwfitRenderHw(hw, data.system);
    // Propagate local platform from hardware probe so _isWindows(task) works
    // for local tasks (menu items, shell commands, etc.).
    if (!remoteHost && data.system && data.system.platform) {
      _envState.platform = data.system.platform;
    }
    // Sort client-side by the active column so the highest↔lowest toggle is
    // deterministic (the previous array .reverse() didn't reliably flip).
    // 1st click on a column = highest first; clicking it again = lowest first.
    if (!isImageMode) {
      const sortSel = document.getElementById('hwfit-sort');
      const sortKey = sortSel?.value || 'newest';
      const asc = sortSel?.dataset.reverse === '1';   // reversed → ascending (lowest first)
      if (sortKey === 'fit') {
        // fit_level is categorical (perfect→good→marginal→too_tight), not numeric,
        // so rank it explicitly instead of falling through to the score column.
        // Tie-break by score so rows within one fit tier stay meaningfully ordered.
        const fitRank = { perfect: 4, good: 3, marginal: 2, too_tight: 1, no_fit: 0 };
        data.models.sort((a, b) => {
          const ar = fitRank[a.fit_level] ?? -1, br = fitRank[b.fit_level] ?? -1;
          if (ar !== br) return asc ? ar - br : br - ar;
          const as = Number(a.score) || 0, bs = Number(b.score) || 0;
          return asc ? as - bs : bs - as;
        });
      } else if (sortKey === 'newest') {
        // release_date is an ISO-ish "YYYY-MM-DD" string — lexical sort is
        // chronological. Default direction: newest first (reverse=undefined).
        data.models.sort((a, b) => {
          const ad = String(a.release_date || ''), bd = String(b.release_date || '');
          if (ad === bd) return 0;
          // Empty dates land last regardless of direction so the column never
          // floats undated rows above real releases.
          if (!ad) return 1;
          if (!bd) return -1;
          return asc ? (ad < bd ? -1 : 1) : (ad < bd ? 1 : -1);
        });
      } else {
        const field = { score: 'score', vram: 'required_gb', speed: 'speed_tps', params: 'params_b', context: 'context' }[sortKey] || 'score';
        data.models.sort((a, b) => {
          const av = Number(a[field]) || 0, bv = Number(b[field]) || 0;
          return asc ? av - bv : bv - av;
        });
      }
    }
    _hwfitRenderList(list, _applyEngineFilter(data.models));
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

// Renders a non-blocking hardware visibility warning when Cookbook is using
// container-visible hardware that may not match the user's actual host machine.
function _renderHwVisibilityWarning(sys) {
  const row = document.getElementById('hwfit-hw-row');
  if (!row) return;

  let box = document.getElementById('hwfit-hw-visibility-warning');

  // Manual hardware is an explicit user override, so avoid showing stale
  // container-detection warnings once the user has chosen a simulated profile.
  const warning = sys?.manual_hardware ? null : sys?.hardware_visibility_warning;

  if (!warning) {
    if (box) box.remove();
    return;
  }

  if (!box) {
    box = document.createElement('div');
    box.id = 'hwfit-hw-visibility-warning';
    box.className = 'hwfit-loading hwfit-hw-visibility-warning';
    row.insertAdjacentElement('afterend', box);
  }

  box.innerHTML = `
    <div class="hwfit-hw-visibility-warning-title">${esc(warning.title || 'Hardware visibility note')}</div>
    <div class="hwfit-hw-visibility-warning-body">${esc(warning.message || '')}</div>
    <div class="hwfit-hw-visibility-warning-actions">
      <button type="button" class="hwfit-gpu-btn" data-hw-action="manual">Edit manual hardware</button>
      <button type="button" class="hwfit-gpu-btn" data-hw-action="rescan">Rescan</button>
      <button type="button" class="hwfit-gpu-btn" data-hw-action="copy">Copy diagnostics</button>
    </div>
  `;

  box.querySelector('[data-hw-action="manual"]')?.addEventListener('click', () => {
    const panel = document.getElementById('hwfit-manual-panel');
    if (panel) panel.classList.remove('hidden');
    const manualBtn = document.getElementById('hwfit-hw-manual-btn');
    if (manualBtn) manualBtn.textContent = 'CANCEL';
    document.getElementById('hwfit-hw-manual-btn')?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'center',
    });
  });

  box.querySelector('[data-hw-action="rescan"]')?.addEventListener('click', () => {
    _resetGpuToggleState();
    _hwfitCache = null;
    _hwfitFetch(true);
  });

  box.querySelector('[data-hw-action="copy"]')?.addEventListener('click', () => {
    // Keep diagnostics copy/paste friendly for GitHub issues and Docker support.
    const text = [
      'Odysseus Cookbook hardware diagnostics',
      `probe_scope=${sys?.probe_scope || ''}`,
      `containerized=${sys?.containerized === true}`,
      `backend=${sys?.backend || ''}`,
      `has_gpu=${sys?.has_gpu === true}`,
      `gpu_name=${sys?.gpu_name || ''}`,
      `gpu_count=${sys?.gpu_count || 0}`,
      `gpu_vram_gb=${sys?.gpu_vram_gb || ''}`,
      `ram=${sys?.available_ram_gb || '?'} / ${sys?.total_ram_gb || '?'} GB`,
      `cpu_cores=${sys?.cpu_cores || ''}`,
      `cpu_name=${sys?.cpu_name || ''}`,
      '',
      'Useful checks:',
      'docker compose exec odysseus nvidia-smi -L',
      'docker compose exec odysseus cat /proc/meminfo | head',
      'docker compose exec odysseus python -c "from services.hwfit.hardware import detect_system; import json; print(json.dumps(detect_system(fresh=True), indent=2))"',
    ].join('\n');

    _copyText(text);
  });
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
    // Mixed-GPU boxes (#711): `${gpuCount}x ${gpu_name}` uses gpus[0].name for
    // every card, so a 4090+3060 reads as "2x RTX 4090". Use gpu_groups (the
    // backend already groups identical cards) to render each pool separately
    // and put the per-card index+VRAM into the tooltip so it's actually
    // useful for picking CUDA_VISIBLE_DEVICES.
    const groups = Array.isArray(sys.gpu_groups) ? sys.gpu_groups : [];
    // Shorten vendor prefixes so a mixed-GPU label fits in the chip row
    // without overflowing. Single-GPU label still shows the full name
    // (that's what users are used to seeing). Tooltip carries the full
    // unmodified names regardless, so no information is lost.
    const _shortGpuName = (n) => String(n || '')
      .replace(/^NVIDIA\s+GeForce\s+/i, '')
      .replace(/^NVIDIA\s+/i, '')
      .replace(/^AMD\s+Radeon\s+/i, '')
      .replace(/^AMD\s+/i, '')
      .replace(/^Intel\s+/i, '');
    let label;
    if (groups.length > 1) {
      // Heterogeneous: "1× RTX 4090 + 1× RTX 3060"
      label = groups.map(g => `${g.count}× ${esc(_shortGpuName(g.name))}`).join(' + ');
    } else if (gpuCount > 1) {
      label = `${gpuCount}× ${esc(sys.gpu_name)}`;
    } else {
      label = esc(sys.gpu_name);
    }
    const gpus = Array.isArray(sys.gpus) ? sys.gpus : [];
    const tip = gpus.length
      ? gpus.map(g => `GPU ${g.index}: ${g.name} · ${(+g.vram_gb).toFixed(1)} GB`).join('\n')
      : 'Click to toggle off (X to hide)';
    gpuChip = chip('gpu', label, tip);
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
  _renderHwVisibilityWarning(sys);
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
        const manualBtn = document.getElementById('hwfit-hw-manual-btn');
        if (manualBtn) manualBtn.textContent = 'EDIT';
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
  const syncManualButton = () => {
    btn.textContent = panel.classList.contains('hidden') ? 'EDIT' : 'CANCEL';
  };
  const clearManual = () => {
    _saveManualHwState(null);
    el.querySelector('.hwfit-hw-chip-manual')?.remove();
    panel.classList.add('hidden');
    syncManualButton();
    _resetGpuToggleState();
    _hwfitCache = null;
    _hwfitFetch(true);
  };
  const manual = _manualHwState();
  syncManualButton();
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
      syncManualButton();
      syncMode();
    });
  }
  el.querySelector('.hwfit-hw-chip-toggle[data-hw-chip="manual"]')?.addEventListener('click', () => {
    panel.classList.remove('hidden');
    syncManualButton();
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
      syncManualButton();
      _hwfitRenderHw(el, _manualDisplaySystem(window._hwfitSystemCache, manual));
      _hwfitFetch(true);
    });
    panel.querySelector('.hwfit-hw-manual-clear')?.addEventListener('click', clearManual);
  }
  syncMode();
  syncManualButton();
}

export const _fitColors = { perfect: 'var(--green, #50fa7b)', good: 'var(--yellow, #f1fa8c)', marginal: 'var(--orange, #ffb86c)', too_tight: 'var(--red, #ff5555)' };

function _requiresAcceleratorBackend(model) {
  const q = String(model?.quant || model?.quantization || '').toUpperCase();
  const text = `${model?.name || ''} ${model?.repo_id || ''} ${model?.path || ''}`.toLowerCase();
  return /^AWQ|^GPTQ|^NVFP4/.test(q) || q === 'FP8' || /\b(awq|gptq|fp8|nvfp4)\b/i.test(text);
}

function _modeLabel(model) {
  if (model?.is_image_gen) return 'image';
  if (_requiresAcceleratorBackend(model)) return 'vLLM/SGLang';
  const detected = _detectBackend(model);
  if (detected?.label) return detected.label;
  return String(model?.run_mode || '').replace('_', '+');
}

export const _hwfitColumns = [
  { key: 'fit', label: 'Fit',    cls: 'hwfit-fit' },
  { key: 'newest', label: 'Model (latest)',  cls: 'hwfit-name' },
  { key: 'vram',  label: 'VRAM',   cls: 'hwfit-c-vram' },
  { key: 'params',label: 'Param', cls: 'hwfit-c-params' },
  { key: null,    label: 'Quant',  cls: 'hwfit-c-quant' },
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
      || document.getElementById('hwfit-quant')?.value
      || document.getElementById('hwfit-engine')?.value);
    let msg;
    if (hasFilters) msg = 'No models match these filters — try clearing the search, use-case, quant, or engine.';
    else if (hasHw) msg = 'No models fit — the hardware probe may have under-reported. Try Rescan.';
    else msg = 'No models fit your hardware';
    el.innerHTML = `<div class="hwfit-loading">${msg}</div>`;
    return;
  }
  const sortSel = document.getElementById('hwfit-sort');
  const currentSort = sortSel?.value || 'newest';
  const isReversed = sortSel?.dataset.reverse === '1';
  // Active budget for the Fit column label \u2014 make it obvious whether the
  // ranking is against GPU or RAM so "tightest" can't be ambiguous on a
  // mixed-resource box.
  const tc = document.getElementById('hwfit-gpu-toggles');
  const _budget = (tc && typeof tc._activeCount === 'number')
    ? (tc._activeCount === 0 ? 'RAM' : (tc._activeCount === 1 ? 'GPU' : tc._activeCount + ' GPU'))
    : null;
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
    // Fit column gets a small dot to its left that toggles "show only models
    // that fit" — replaces the old Fits On/Off button next to the toolbar.
    let label = col.label;
    if (col.cls === 'hwfit-fit') {
      const _fitOnly = (() => { try { return localStorage.getItem('hwfit_fit_only_v1') === '1'; } catch { return false; } })();
      label = `<span class="hwfit-fit-dot${_fitOnly ? ' active' : ''}" title="${_fitOnly ? 'Showing only models that fit. Click to also show too-tight rows.' : 'Click to show only models that fit your hardware.'}" data-fit-dot>●</span>${col.label}`;
      // (Budget tag removed — the GPU/RAM/N-GPU suffix next to "Fit" was noise;
      // the toggle row already shows which budget is active.)
    }
    // The Model column's "(newest)" / "(oldest)" suffix flips with the sort
    // direction so the user can see at a glance which way they're sorted.
    if (col.key === 'newest' && col.key === currentSort) {
      label = isReversed ? 'Model (oldest)' : 'Model (latest)';
    } else if (col.key === 'newest') {
      label = 'Model (latest)';
    }
    html += `<span class="hwfit-col ${col.cls}${sortable}${active}"${dataAttr}>${label}${arrow}</span>`;
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
    const modeLabel = _modeLabel(m);
    const vramLabel = m.required_gb ? m.required_gb.toFixed(1) + 'G' : '?';
    const moeBadge = m.is_moe ? '<span class="hwfit-badge hwfit-moe">MoE</span>' : '';
    const imgBadge = m.is_image_gen ? '<span class="hwfit-badge" style="background:color-mix(in srgb, var(--red) 20%, transparent);color:var(--red);font-size:8px;padding:1px 4px;border-radius:3px;margin-left:4px;">IMG</span>' : '';
    const dlDot = (_cachedModelIds && (_cachedModelIds.has(m.name) || [..._cachedModelIds].some(id => id === m.name?.split('/').pop()))) ? '<span class="hwfit-dl-dot" title="Downloaded">\u25CF</span>' : '';
    html += `<div class="hwfit-row" data-model="${esc(m.name)}">`;
    html += `<span class="hwfit-col hwfit-fit" style="color:${fitColor}">${esc(fitLabel)}</span>`;
    // Append quant to the title when it's not already in the repo name. The
    // suffix strips quant-parts the name already contains — e.g. for
    // QuantTrio/MiniMax-M2-AWQ + quant=AWQ-4bit we just show "(4bit)", not
    // "(AWQ-4bit)". DeepSeek-V4-Flash + FP4-MoE-Mixed keeps the full tag
    // (none of those parts are in the repo id).
    const _short = m.name?.split('/').pop() || m.name || '';
    const _quantTag = (m.quant || '').trim();
    const _lowerShort = _short.toLowerCase();
    let _quantSuffix = '';
    if (_quantTag) {
      const _parts = _quantTag.split(/[-_]/).filter(Boolean);
      const _remaining = _parts.filter(p => !_lowerShort.includes(p.toLowerCase()));
      if (_remaining.length && _remaining.length < _parts.length + 1) {  // at least one part is new
        let _display = _remaining.join('-');
        if (_display.length > 9) _display = _display.slice(0, 9) + '…';
        _quantSuffix = ` <span class="hwfit-name-quant" title="${esc(_quantTag)} — full storage format">(${esc(_display)})</span>`;
      }
    }
    html += `<span class="hwfit-col hwfit-name">${modelLogo(m.name)}${esc(_short)}${_quantSuffix}${moeBadge}${imgBadge}${dlDot}</span>`;
    html += `<span class="hwfit-col hwfit-c-vram" title="Estimated loaded footprint for this quant/backend/context, including model weights and KV/runtime allowance.">${vramLabel}</span>`;
    html += `<span class="hwfit-col hwfit-c-params" title="Original total model parameters, not quantized storage size.">${esc(pcount)}</span>`;
    // Truncate the Quant cell to 9 chars + ellipsis so long tags like
    // "FP4-MoE-Mixed" don't push neighboring columns. Full tag stays in title.
    const _qRaw = m.quant || '?';
    const _qShort = _qRaw.length > 9 ? _qRaw.slice(0, 9) + '…' : _qRaw;
    html += `<span class="hwfit-col hwfit-c-quant" title="${esc(_qRaw)}">${esc(_qShort)}</span>`;
    html += `<span class="hwfit-col hwfit-c-ctx">${m.is_image_gen ? '\u2014' : ctx}</span>`;
    html += `<span class="hwfit-col hwfit-c-speed">${m.is_image_gen ? '\u2014' : tps + ' t/s'}</span>`;
    html += `<span class="hwfit-col hwfit-c-score">${score}</span>`;
    html += `<span class="hwfit-col hwfit-c-mode" title="${_requiresAcceleratorBackend(m) ? 'Requires vLLM or SGLang with a visible CUDA/ROCm accelerator. llama.cpp and Ollama need GGUF files.' : ''}">${esc(modeLabel)}</span>`;
    html += `</div>`;
  }
  el.innerHTML = html;
  // Click row → expand inline action panel. Exception: Ollama rows skip the
  // expand panel (no HF metadata to power it) and just fill the Download
  // input with the `<name>:<size>` tag — one click → ready to pull.
  el.querySelectorAll('.hwfit-row:not(.hwfit-header)').forEach(row => {
    row.addEventListener('click', () => {
      const name = row.dataset.model;
      if (!name) return;
      const modelData = (_hwfitCache?.models || []).find(m => m.name === name);
      if (!modelData) return;
      if (modelData._isOllama) {
        // Force-open the Download card if it's been collapsed — otherwise
        // filling the (hidden) input silently swallows the click.
        const dlBody = document.getElementById('cookbook-download-card-body');
        const dlArrow = document.getElementById('cookbook-download-card-arrow');
        if (dlBody && dlBody.style.display === 'none') {
          dlBody.style.display = 'block';
          if (dlArrow) dlArrow.style.transform = 'rotate(90deg)';
        }
        const dlInput = document.getElementById('cookbook-dl-repo');
        if (dlInput) {
          dlInput.value = modelData.name;
          dlInput.focus();
          // Briefly highlight so the user sees what got filled even when the
          // download card sits far above the (long) hwfit list.
          dlInput.classList.add('cookbook-dl-flash');
          setTimeout(() => dlInput.classList.remove('cookbook-dl-flash'), 800);
          dlInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        return;
      }
      _expandModelRow(row, modelData);
    });
  });
  // Clickable header columns → sort (click again to toggle direction)
  el.querySelectorAll('.hwfit-header .hwfit-sortable').forEach(col => {
    col.addEventListener('click', (e) => {
      // The little dot inside the Fit header is its own toggle (fit-only
      // filter), don't let it fall through to a sort click.
      if (e.target.closest('[data-fit-dot]')) {
        const on = !e.target.classList.contains('active');
        try { localStorage.setItem('hwfit_fit_only_v1', on ? '1' : '0'); } catch {}
        // Un-toggling the fit filter should still keep the list usable: show
        // nearest/smallest VRAM first, not a wall of impossible 7000G rows.
        if (!on) {
          const sortSel = document.getElementById('hwfit-sort');
          if (sortSel) {
            sortSel.value = 'vram';
            sortSel.dataset.reverse = '1';   // ascending (smallest VRAM first)
          }
        }
        _hwfitCache = null;
        _hwfitFetch();
        return;
      }
      const sortKey = col.dataset.sort;
      if (!sortKey) return;
      const sel = document.getElementById('hwfit-sort');
      if (!sel) return;
      // Toggle direction if clicking the same column
      if (sel.value === sortKey) {
        sel.dataset.reverse = sel.dataset.reverse === '1' ? '0' : '1';
      } else {
        sel.value = sortKey;
        // VRAM is most useful as "what fits / closest fit first"; descending
        // buries qwen/gemma-sized rows below absurd impossible footprints.
        sel.dataset.reverse = sortKey === 'vram' ? '1' : '0';
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
    _envState.remoteServerKey = '';
  } else {
    const s = _serverByVal(ss.value);
    if (s) {
      host = s.host;
      _envState.remoteHost = s.host;
      _envState.remoteServerKey = _serverKey(s);
      _envState.env = s.env;
      _envState.envPath = s.envPath;
      _envState.platform = s.platform || '';
    }
  }
  try { _persistEnvState(); } catch {}
  return host;
}

// Minimum backend version a given model needs. Returns a semver string like
// "0.10.0" or null when the model has no known floor. Hardcoded for now —
// when the vLLM-recipes integration lands we can pull this from the upstream
// recipe page instead. Keep this conservative: a null return means "any
// installed version passes", so we don't false-positive launches.
function _minBackendVersion(modelName, backend) {
  const n = (modelName || '').toLowerCase();
  if (backend === 'vllm') {
    // MiniMax M2 / M2.5 / M2.7 — minimax_m2 parser shipped in 0.10.0
    if (n.includes('minimax') && n.match(/\bm2(?:\.\d)?\b/)) return '0.10.0';
    // MiniMax M3 — newer parser registered in 0.11.x
    if (n.includes('minimax') && n.includes('m3')) return '0.11.0';
    // DeepSeek V3 / V3.1 / R1 — MoE expert-parallel paths matured in 0.7.0+
    if (n.includes('deepseek') && (n.includes('v3') || n.includes('r1'))) return '0.7.0';
    // Qwen3 reasoning models — qwen3 reasoning parser added in 0.7.0
    if (n.includes('qwen3') && !n.includes('coder') && !n.includes('instruct')) return '0.7.0';
    // GLM-4.5 / GLM-4.6 — glm45 reasoning parser added in 0.8.0
    if (n.includes('glm-4.5') || n.includes('glm-4.6') || n.includes('glm-5')) return '0.8.0';
    // gpt-oss reasoning models — gpt_oss parser
    if (n.includes('gpt-oss')) return '0.10.0';
    // Llama-4 multimodal — landed in 0.7.0
    if (n.includes('llama-4') || n.includes('llama4')) return '0.7.0';
  }
  return null;
}

// Tiny semver compare: returns <0 / 0 / >0 like strcmp. Tolerates "0.10",
// "0.10.0", "0.10.0+cu124" — pre-release / build suffixes are stripped.
function _cmpSemver(a, b) {
  const _parse = (s) => String(s || '').split(/[.+-]/).filter(p => /^\d+$/.test(p)).map(Number);
  const A = _parse(a), B = _parse(b);
  for (let i = 0; i < Math.max(A.length, B.length); i++) {
    const av = A[i] || 0, bv = B[i] || 0;
    if (av !== bv) return av - bv;
  }
  return 0;
}

// Map the detected GPU + the model's quant to SGLang's URL-hash params so
// the cookbook page lands on the right preset. SGLang supports:
//   hw      = b200 | b300 | gb200 | gb300 | mi300x | mi325x | mi350x | mi355x | h200
//   quant   = mxfp8 | bf16
//   variant = default        strategy = balanced       nodes = single
// We only set what we can confidently infer; anything missing degrades to
// SGLang's own default (which is `h200` + bf16 single-node balanced).
function _sglangHashFor(modelData) {
  const sys = (typeof _hwfitCache !== 'undefined' ? _hwfitCache?.system : null) || {};
  const gpuName = String(sys.gpu_name || '').toLowerCase();
  let hw = '';
  if (/\bgb300/.test(gpuName)) hw = 'gb300';
  else if (/\bgb200/.test(gpuName)) hw = 'gb200';
  else if (/\bb300/.test(gpuName)) hw = 'b300';
  else if (/\bb200/.test(gpuName)) hw = 'b200';
  else if (/\bh200/.test(gpuName)) hw = 'h200';
  else if (/mi355/.test(gpuName)) hw = 'mi355x';
  else if (/mi350/.test(gpuName)) hw = 'mi350x';
  else if (/mi325/.test(gpuName)) hw = 'mi325x';
  else if (/mi300/.test(gpuName)) hw = 'mi300x';
  const qRaw = String(modelData?.quant || '').toLowerCase();
  // mxfp8 covers fp8 / mxfp8 / nvfp4; bf16 covers everything else cheap.
  const quant = /fp8|mxfp|nvfp/.test(qRaw) ? 'mxfp8' : 'bf16';
  const parts = ['variant=default', `quant=${quant}`, 'strategy=balanced', 'nodes=single'];
  if (hw) parts.unshift(`hw=${hw}`);
  return '#' + parts.join('&');
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

  const dlSource = _downloadSourceRepo(modelData, backend);
  const hfUrl = `https://huggingface.co/${dlSource.repo}`;
  let html = `<div class="hwfit-action-panel" data-model-name="${esc(modelData.name)}">`;
  html += `<div class="hwfit-panel-header">`;
  html += `<span class="hwfit-panel-model">${esc(modelData.name)}${dlSource.kind ? ` <span style="opacity:0.5;font-size:10px;">(${esc(dlSource.kind)} ${esc(modelData.quant || '')})</span>` : (modelData.quant_repo ? ` <span style="opacity:0.5;font-size:10px;">(${esc(modelData.quant)})</span>` : '')}</span>`;
  html += `<span class="hwfit-panel-badge">${esc(label)}</span>`;
  html += `<a href="${esc(hfUrl)}" target="_blank" rel="noopener" class="hwfit-panel-hf-link" title="View download source on HuggingFace">HF \u2197</a>`;
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
  } else if (_requiresAcceleratorBackend(modelData)) {
    // Only show the "needs CUDA/ROCm" note when the host doesn't already have
    // one. With a visible CUDA/ROCm accelerator the note is noise — the user
    // can already serve the model and reading the warning on every row makes
    // the panel feel like everything's broken.
    const _sys = _hwfitCache?.system || {};
    const _backend = (_sys.backend || '').toLowerCase();
    const _hasGpuAccel = !!_sys.has_gpu && (_backend === 'cuda' || _backend === 'rocm');
    if (!_hasGpuAccel) {
      html += `<div class="hwfit-panel-note">This is a safetensors GPU-serving format. Use vLLM/SGLang with a visible CUDA/ROCm accelerator, or pick a GGUF download for llama.cpp/Ollama.</div>`;
    }
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
      // Detect backend and port now — the pre-launch guard below needs them.
      const _qrBackendDetect = _detectBackend(modelData);
      const _qrRunBackend = _qrBackendDetect.backend || 'vllm';
      const _qrPort = _nextAvailablePort();

      // ─── Pre-launch: stop colliding serves on the same port ───────
      // Different ports coexist fine (e.g. vLLM on 8000 + Qwen VL on
      // 8001). Only block when the new model's port genuinely collides
      // with a running serve. (Issue #4507)
      try {
        const _qrHostStr = _envState.remoteHost || '';
        const _allServes = _loadTasks().filter(t =>
          t && t.type === 'serve'
          && (t.remoteHost || '') === _qrHostStr
          && (t.status === 'running' || t.status === 'ready' || t._serveReady)
        );
        const _clashing = _allServes.filter(t => _taskPort(t) === _qrPort);
        if (_clashing.length) {
          const _names = _clashing.map(t => t.payload?.repo_id || t.repo || t.name || '?').filter(Boolean);
          const _ok = await window.styledConfirm?.(
            `${_clashing.length} model${_clashing.length === 1 ? '' : 's'} on port ${_qrPort} (${_names.join(', ')}). Stop it and launch this one?`,
            { confirmText: 'Stop & launch', cancelText: 'Cancel' }
          );
          if (!_ok) return;
          quickRunBtn.disabled = true;
          quickRunBtn.textContent = 'Stopping…';
          for (const t of _clashing) {
            try {
              const _taskEl = document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`);
              const _stopBtn = _taskEl?.querySelector('.cookbook-task-action-stop');
              if (_stopBtn) {
                _stopBtn.click();
              } else {
                await fetch('/api/shell/exec', {
                  method: 'POST',
                  credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ command: _tmuxGracefulKill(t) }),
                });
              }
            } catch (_killErr) { /* best-effort */ }
          }
          await new Promise(r => setTimeout(r, 2500));
        }
      } catch (_e) { /* best-effort */ }

      // -- Launch ───────────────────────────────────────────────────

      // ─── Pre-launch driver check ─────────────────────────────────────
      // vLLM/SGLang need a working CUDA/ROCm driver. nvidia-smi failures
      // surface as system.gpu_error from our hardware probe; "no GPU
      // detected" is the other common case. Bail with a clear message
      // before kicking off the long install/launch chain — otherwise the
      // user watches `pip install vllm` finish, then sees a cryptic CUDA
      // error 10 minutes later. (llama.cpp / Ollama have CPU fallbacks
      // so they skip this gate.)
      if (_qrRunBackend === 'vllm' || _qrRunBackend === 'sglang') {
        const _sys = _hwfitCache?.system || {};
        if (_sys.gpu_error) {
          uiModule.showError(`Can't launch: GPU driver error — ${_sys.gpu_error}. Reinstall or repair the NVIDIA driver, then re-scan.`);
          return;
        }
        if (!_sys.has_gpu || !(_sys.gpu_count > 0)) {
          uiModule.showError(`Can't launch: no GPU detected by nvidia-smi. ${_qrRunBackend === 'vllm' ? 'vLLM' : 'SGLang'} needs a working CUDA or ROCm device.`);
          return;
        }
      }

      // ─── Pre-launch install + version check ─────────────────────────
      // Catches:
      //   a) "command not found" (binary not in PATH)
      //   b) "version too old" (model needs e.g. vllm >= 0.10.0 for the
      //      reasoning/tool parser registered for it).
      // Both cases would otherwise fail 10s-3min into the launch with a
      // cryptic shell error. Best-effort: a venv activated only by the
      // launch wrapper can false-negative the PATH check, in which case
      // the launch proceeds and the existing diagnosis layer handles it.
      if (_qrRunBackend === 'vllm' || _qrRunBackend === 'sglang') {
        try {
          const _qrHostStr = _envState.remoteHost || '';
          const _coreCheck = _qrRunBackend === 'vllm'
            ? "command -v vllm >/dev/null 2>&1 && vllm --version 2>&1 | grep -oE '[0-9]+\\.[0-9]+(\\.[0-9]+)?' | head -1 || echo MISSING"
            : "python3 -c 'import sglang, sys; sys.stdout.write(sglang.__version__)' 2>/dev/null || echo MISSING";
          const _wrappedCheck = _qrHostStr
            ? `ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new ${_qrHostStr} "bash -lc ${JSON.stringify(_coreCheck)}"`
            : `bash -lc ${JSON.stringify(_coreCheck)}`;
          const _chkRes = await fetch('/api/shell/exec', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: _wrappedCheck, timeout: 10 }),
          });
          if (_chkRes.ok) {
            const _chk = await _chkRes.json();
            const _stdout = String(_chk.stdout || '').trim();
            const _stderr = String(_chk.stderr || '').trim();
            const _out = `${_stdout}\n${_stderr}`;
            if (_out.includes('MISSING')) {
              const _pkg = _qrRunBackend === 'vllm' ? 'vLLM' : 'SGLang';
              const _hint = _qrRunBackend === 'vllm'
                ? 'uv pip install -U vllm --torch-backend auto'
                : "pip install -U 'sglang[all]'";
              uiModule.showError(`Can't launch: ${_pkg} isn't installed${_qrHostStr ? ' on ' + _qrHostStr : ''}. Install it first:\n${_hint}`);
              return;
            }
            // Version-floor check. _minBackendVersion returns null when this
            // model has no known requirement — in which case any installed
            // version passes.
            const _minVer = _minBackendVersion(modelData.name, _qrRunBackend);
            const _verMatch = _stdout.match(/(\d+\.\d+(?:\.\d+)?)/);
            const _curVer = _verMatch ? _verMatch[1] : '';
            if (_minVer && _curVer && _cmpSemver(_curVer, _minVer) < 0) {
              const _pkg = _qrRunBackend === 'vllm' ? 'vLLM' : 'SGLang';
              const _hint = _qrRunBackend === 'vllm'
                ? 'uv pip install -U vllm --torch-backend auto'
                : "pip install -U 'sglang[all]'";
              uiModule.showError(`Can't launch: ${modelData.name} needs ${_pkg} ≥ ${_minVer}, but ${_curVer} is installed${_qrHostStr ? ' on ' + _qrHostStr : ''}. Upgrade:\n${_hint}`);
              return;
            }
          }
        } catch (_e) {
          // Network/exec failed — fall through and let the launch try.
        }
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
      const port = _qrPort;
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
      } else if (runBackend === 'mlx') {
        const bindHost = host ? '0.0.0.0' : '127.0.0.1';
        cmd = `python3 -m mlx_lm.server --model ${_shellQuote(modelData.name)} --host ${bindHost} --port ${port}`;
      } else if (runBackend === 'llamacpp') {
        const dir = `"$HOME/.cache/huggingface/hub/models--${modelData.name.replace(/\//g, '--')}/snapshots"`;
        const ggufPath = `$({ find ${dir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${dir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`;
        cmd = `llama-server --model "${ggufPath}" --host 0.0.0.0 --port ${port} -ngl 99 -c ${maxCtx} --flash-attn auto`;
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
      const _srv = _serverByVal(_envState.remoteServerKey || host);

      // Pre-flight: if the backend isn't installed on the target server,
      // route the user into Dependencies → recipe panel for that backend
      // instead of launching into an obvious "command not found" failure.
      const _ok = await _ensureBackendInstalled(
        runBackend,
        host,
        (_srv && _srv.port) || undefined,
        _envState.envPath || '',
        modelData.name,
      );
      if (!_ok) {
        quickRunBtn.disabled = false;
        quickRunBtn.textContent = 'Run';
        return;
      }

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

const _HWFIT_ENGINE_GLYPHS = {
  '': '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="4" y1="6" x2="20" y2="6"></line><line x1="4" y1="12" x2="20" y2="12"></line><line x1="4" y1="18" x2="20" y2="18"></line><circle cx="8" cy="6" r="2" fill="currentColor" stroke="none"></circle><circle cx="16" cy="12" r="2" fill="currentColor" stroke="none"></circle><circle cx="10" cy="18" r="2" fill="currentColor" stroke="none"></circle></svg>',
  vllm: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4l7 16 7-16"></path><path d="M14 4l4 9 3-9"></path></svg>',
  sglang: '<span aria-hidden="true" style="display:block;width:14px;height:14px;background:currentColor;-webkit-mask:url(/static/icons/sglang-mark.png) center/contain no-repeat;mask:url(/static/icons/sglang-mark.png) center/contain no-repeat;"></span>',
  mlx: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 18V6l4 7 4-7v12"></path><path d="M16 6v12"></path><path d="M20 6v12"></path></svg>',
  llamacpp: '<svg width="14" height="14" viewBox="0 0 600 600" fill="none" aria-hidden="true"><path d="M600 392L504.249 558L504.137 557.929C487.252 584.069 458.193 600 426.864 600H120L240 392H600Z" fill="currentColor"></path><path d="M240 392H0L199.602 46.0254C216.032 17.5463 246.411 0 279.29 0H466.154L240 392Z" fill="currentColor"></path></svg>',
  ollama: '<span aria-hidden="true" style="display:block;width:14px;height:14px;background:currentColor;-webkit-mask:url(/static/icons/ollama-mark-crop.png) center/contain no-repeat;mask:url(/static/icons/ollama-mark-crop.png) center/contain no-repeat;"></span>',
  diffusers: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"></path></svg>',
};

function _hwfitEngineGlyph(value) {
  return _HWFIT_ENGINE_GLYPHS[value] || _HWFIT_ENGINE_GLYPHS[''];
}

function _bindHwfitEnginePicker(engine) {
  const wrap = engine?.closest('.hwfit-engine-wrap');
  const btn = wrap?.querySelector('[data-hwfit-engine-btn]');
  const menu = wrap?.querySelector('[data-hwfit-engine-menu]');
  const icon = wrap?.querySelector('[data-hwfit-engine-icon]');
  const label = wrap?.querySelector('[data-hwfit-engine-label]');
  if (!engine || !wrap || !btn || !menu || wrap.dataset.enginePickerBound) return;
  wrap.dataset.enginePickerBound = '1';

  const setOpen = (open) => {
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  const currentLabel = () => {
    const opt = Array.from(engine.options).find((o) => o.value === engine.value);
    return opt?.textContent || 'Engine';
  };
  const syncButton = () => {
    if (label) label.textContent = currentLabel();
    if (icon) icon.innerHTML = _hwfitEngineGlyph(engine.value);
    menu.querySelectorAll('[data-hwfit-engine-value]').forEach((item) => {
      const active = item.dataset.hwfitEngineValue === engine.value;
      item.classList.toggle('active', active);
      item.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  };
  const renderMenu = () => {
    menu.innerHTML = Array.from(engine.options).map((opt) => (
      `<button type="button" role="option" class="hwfit-engine-item" data-hwfit-engine-value="${opt.value}">`
      + `<span class="hwfit-engine-item-icon" aria-hidden="true">${_hwfitEngineGlyph(opt.value)}</span>`
      + `<span class="hwfit-engine-item-label">${opt.textContent}</span>`
      + '</button>'
    )).join('');
    menu.querySelectorAll('[data-hwfit-engine-value]').forEach((item) => {
      item.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const next = item.dataset.hwfitEngineValue || '';
        if (engine.value !== next) {
          engine.value = next;
          engine.dispatchEvent(new Event('change', { bubbles: true }));
        }
        syncButton();
        setOpen(false);
      });
    });
    syncButton();
  };

  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    setOpen(menu.hidden);
  });
  engine.addEventListener('change', syncButton);
  document.addEventListener('click', (ev) => {
    if (!wrap.contains(ev.target)) setOpen(false);
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') setOpen(false);
  });
  renderMenu();
}

export function _hwfitInit() {
  const uc = document.getElementById('hwfit-usecase');
  const sort = document.getElementById('hwfit-sort');
  const qpref = document.getElementById('hwfit-quant');
  const ctx = document.getElementById('hwfit-context');
  const ctxLabel = document.getElementById('hwfit-context-label');
  const search = document.getElementById('hwfit-search');
  const remote = document.getElementById('hwfit-host');
  _syncCtxControl();
  if (uc) uc.addEventListener('change', () => _hwfitFetch());
  if (sort) sort.addEventListener('change', () => _hwfitFetch());
  if (qpref) qpref.addEventListener('change', () => _hwfitFetch());
  // Engine filter is a pure client-side view filter over the already-fetched
  // list (HF + Ollama merged), so just re-render from cache.
  const engine = document.getElementById('hwfit-engine');
  if (engine) _bindHwfitEnginePicker(engine);
  if (engine) engine.addEventListener('change', () => {
    const list = document.getElementById('hwfit-list');
    if (list && _hwfitCache && Array.isArray(_hwfitCache.models)) {
      _hwfitRenderList(list, _applyEngineFilter(_hwfitCache.models));
    } else {
      _hwfitFetch();
    }
  });
  if (ctx && !ctx.dataset.bound) {
    ctx.dataset.bound = '1';
    ctx.addEventListener('input', () => {
      if (ctxLabel) ctxLabel.textContent = _ctxLabel(_ctxValue());
    });
    ctx.addEventListener('change', () => {
      const targetCtx = _ctxValue();
      try { localStorage.setItem(_CTX_KEY, String(targetCtx)); } catch {}
      // Ctx drag affects sort mode: a specific ctx target (anything < Max)
      // implies "what runs at this context length" — sort by VRAM ascending
      // so the cheapest-fitting models surface first. Dragging back to Max
      // releases the constraint → go back to the default score ranking.
      const sortSel = document.getElementById('hwfit-sort');
      if (sortSel) {
        if (targetCtx) {
          sortSel.value = 'vram';
          sortSel.dataset.reverse = '1';   // ascending = smallest VRAM first
        } else {
          sortSel.value = 'score';
          sortSel.dataset.reverse = '';
        }
      }
      _hwfitCache = null;
      _hwfitFetch();
    });
  }
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
  // HF token save is owned by cookbook.js (_wireTabEvents) — do not wire a
  // second change/input handler here. The old duplicate ran after cookbook.js
  // cleared the input on save and overwrote _envState.hfToken with "", so the
  // debounced state sync never persisted the token to cookbook_state.json.

  // Rebuild all server select dropdowns with current servers
  function _rebuildServerSelect() {
    const selectors = [
      document.getElementById('hwfit-server-select'),
      document.getElementById('hwfit-dl-server'),
    ];
    for (const sel of selectors) {
      if (!sel) continue;
      const currentVal = sel.value || _currentServerValue();
      const localSrv = _envState.servers.find(s => !s.host || String(s.host).toLowerCase() === 'local') || {};
      const localColor = /^#[0-9a-fA-F]{6}$/.test(String(localSrv.color || '').trim()) ? String(localSrv.color).trim() : '';
      const localLabel = localSrv.name || 'Local';
      let html = `<option value="local"${localColor ? ` style="color:${uiModule.esc(localColor)};"` : ''}>${uiModule.esc(localColor ? `● ${localLabel}` : localLabel)}</option>`;
      _envState.servers.forEach((s, i) => {
        if (!s.host) return;
        const label = s.name || s.host || `Server ${i + 1}`;
        const color = /^#[0-9a-fA-F]{6}$/.test(String(s.color || '').trim()) ? String(s.color).trim() : '';
        html += `<option value="${uiModule.esc(_serverKey(s))}"${color ? ` style="color:${uiModule.esc(color)};"` : ''}>${uiModule.esc(color ? `● ${label}` : label)}</option>`;
      });
      sel.innerHTML = html;
      sel.value = currentVal;
      if (sel.selectedIndex < 0) sel.value = _currentServerValue();
      if (sel.selectedIndex < 0) sel.value = 'local';
      _applyServerSelectColor(sel);
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
      const colorRaw = row.querySelector('.cookbook-srv-color')?.value?.trim() || '';
      const color = /^#[0-9a-fA-F]{6}$/.test(colorRaw) ? colorRaw : '';
      // Collect model directories from tags. Read the authoritative data-dir
      // attribute, not textContent \u2014 the tag now also holds a download-target
      // icon, and textContent would fold the icon/\u2716 glyph into the path.
      const dirTags = entry.querySelectorAll('.cookbook-modeldir-tag');
      const modelDirs = [];
      dirTags.forEach(tag => {
        const d = _normalizeCookbookModelDir(tag.dataset.dir || '');
        if (d) modelDirs.push(d);
      });
      if (!modelDirs.length) modelDirs.push('~/.cache/huggingface/hub');
      // Which dir (if any) is flagged as the download target. '' = HF cache.
      const dlEl = entry.querySelector('.cookbook-modeldir-dl.active');
      const downloadDir = dlEl ? (dlEl.dataset.dlDir || '') : '';
      const platform = entry.dataset.platform || '';
      _envState.servers.push({ name, host: host || '', port, env, envPath, color, modelDirs, modelDir: modelDirs.filter(d => d !== '~/.cache/huggingface/hub')[0] || modelDirs[0], downloadDir, platform });
    });
    // Do NOT auto-change the selected host here. _syncServers can run while the
    // servers DOM is mid-render — host fields that are disabled/readonly read as
    // empty (see above), which made the rebuilt list temporarily miss the
    // selected server. The old code then "fell back" to the first remote server
    // and persisted it, silently flipping the active host even though the
    // dropdown still showed odysseus. The user's selection must only change via
    // an explicit dropdown pick. Here we just refresh env/path if we can match
    // the current host; otherwise leave remoteHost untouched.
    const sel = _serverByVal(_envState.remoteServerKey || _envState.remoteHost);
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
    const t0 = Date.now();
    try {
      const res = await fetch('/api/cookbook/test-ssh', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host, ssh_port: port || undefined }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      }
      const ms = Date.now() - t0;
      const out = (data.stdout || '').trim();
      if (data.exit_code === 0 && out.startsWith('ok')) {
        dot.className = 'cookbook-srv-status ok';
        dot.title = `Reachable · ${ms} ms · use Dependencies to check tmux/HF setup`;
        setMsg(`Connected · ${ms} ms`, 'var(--green,#50fa7b)');
      } else {
        dot.className = 'cookbook-srv-status fail';
        const err = (data.stderr || data.stdout || (data.exit_code == null ? 'no exit code' : `exit ${data.exit_code}`)).toString().trim().slice(0, 240);
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
          b.innerHTML = _serverDefaultHtml(on);
          b.title = on ? 'Default server — Cookbook opens here' : 'Make this the default server';
        });
        // Apply immediately so the dropdowns reflect it without reopening
        // (inline — _applyServerSelection lives in cookbook.js and isn't imported here).
        const _dk = _envState.defaultServer;
        if (_dk) {
          if (_dk === 'local') { _envState.remoteHost = ''; _envState.remoteServerKey = ''; _envState.env = 'none'; _envState.envPath = ''; _envState.platform = ''; }
          else { const _s = _serverByVal(_dk); if (_s) { _envState.remoteHost = _s.host; _envState.remoteServerKey = _serverKey(_s); _envState.env = _s.env || 'none'; _envState.envPath = _s.envPath || ''; _envState.platform = _s.platform || ''; } }
          _persistEnvState();
          document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
            if (sel && sel.tagName === 'SELECT') sel.value = _currentServerValue();
          });
        }
        const defaultSrv = _serverByVal(_envState.defaultServer);
        uiModule.showToast(_envState.defaultServer
          ? 'Default server: ' + (_envState.defaultServer === 'local' ? 'Local' : (defaultSrv?.name || defaultSrv?.host || 'selected server'))
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
    _wireServerColorPicker(entry);
    entry.querySelectorAll('input, select').forEach(el => {
      el.addEventListener('change', () => {
        const selectedBefore = _envState.remoteHost || '';
        const entryHost = entry.querySelector('.cookbook-srv-host')?.value?.trim() || '';
        const color = entry.querySelector('.cookbook-srv-color')?.value?.trim() || '';
        const hasColor = /^#[0-9a-fA-F]{6}$/.test(color);
        const colorWrap = entry.querySelector('.cookbook-srv-color-wrap');
        if (hasColor) {
          entry.style.setProperty('--cookbook-server-color', color);
          colorWrap?.style.setProperty('--cookbook-server-color', color);
        } else {
          const autoColor = (colorWrap?.style.getPropertyValue('--cookbook-server-color') || entry.style.getPropertyValue('--cookbook-server-color') || '').trim();
          if (/^#[0-9a-fA-F]{6}$/.test(autoColor)) {
            entry.style.setProperty('--cookbook-server-color', autoColor);
            colorWrap?.style.setProperty('--cookbook-server-color', autoColor);
          } else {
            entry.style.removeProperty('--cookbook-server-color');
            colorWrap?.style.removeProperty('--cookbook-server-color');
          }
        }
        colorWrap?.classList.toggle('has-color', true);
        _syncServers();
        _rebuildServerSelect();
        if (selectedBefore && selectedBefore === entryHost) {
          _hwfitCache = null;
          _hwfitFetch();
        }
        if (!entry.querySelector('.cookbook-server-key-panel')?.classList.contains('hidden')) {
          _populateServerKeyPanel(entry, false);
        }
        const saveBtn = entry.querySelector('.cookbook-server-save-btn.saved');
        if (saveBtn) {
          saveBtn.classList.remove('saved');
          saveBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save';
        }
      });
    });
    // Manual connectivity test after editing host or port. Existing saved
    // servers are not auto-tested on panel open; unreachable hosts can stall the
    // Cookbook UI and make opening the panel feel blocked.
    entry.querySelectorAll('.cookbook-srv-host, .cookbook-srv-port').forEach(el => {
      el.addEventListener('blur', () => _testServerConnection(entry));
    });
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
    // Save button: persist + confirm with a check.
    const saveBtn = entry.querySelector('.cookbook-server-save-btn');
    if (saveBtn && !saveBtn.dataset.bound) {
      saveBtn.dataset.bound = '1';
      saveBtn.addEventListener('click', () => {
        _syncServers();
        _rebuildServerSelect();
        // Broadcast for anything outside the settings tab that depends on
        // the server list (Serve dialog host picker, Running tasks, etc.).
        // Without this the user had to hard-refresh to see the new entry
        // in those other places.
        try {
          document.dispatchEvent(new CustomEvent('cookbook:servers-changed', {
            detail: { servers: _envState.servers.slice() },
          }));
        } catch (_) {}
        saveBtn.classList.add('saved');
        saveBtn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#50fa7b" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><polyline points="20 6 9 17 4 12"/></svg>Saved';
        uiModule.showToast('Server saved');
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
      try {
        document.dispatchEvent(new CustomEvent('cookbook:servers-changed', {
          detail: { servers: _envState.servers.slice() },
        }));
      } catch (_) {}
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
      const blank = { host: '', name: '', port: '', env: 'none', envPath: '', color: '', platform: '', modelDirs: ['~/.cache/huggingface/hub'] };
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
        _envState.remoteServerKey = '';
        _envState.env = 'none';
        _envState.envPath = '';
      } else {
        const s = _serverByVal(val);
        if (s) {
          _envState.remoteHost = s.host;
          _envState.remoteServerKey = _serverKey(s);
          _envState.env = s.env;
          _envState.envPath = s.envPath;
        }
      }
      _persistEnvState();
      _applyServerSelectColor(serverSelect);
      // Keep the other server dropdowns (Download / Cache / Deps) in sync. The
      // download-input button reads #hwfit-dl-server *directly*, so without this
      // it kept its old value and downloads went to the wrong host even
      // though the scan here correctly switched to the selected server.
      document.querySelectorAll('#hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
        if (!sel || sel.tagName !== 'SELECT') return;
        sel.value = _currentServerValue();
        _applyServerSelectColor(sel);
      });
      _hwfitCache = null;
      // Reset GPU-toggle state (no flicker) so the new server's hardware re-renders.
      _resetGpuToggleState();
      _hwfitFetch();
    });
  }
  _syncServerSelectColors();

}

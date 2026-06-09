// ============================================
// COOKBOOK SERVE SUB-MODULE
// Serve tab: cached model list, serve panel building,
// command building, preset slots, launch logic
// ============================================

import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import { providerLogo } from './providers.js';
import { modelColor } from './chatRenderer.js';
import { bindMenuDismiss, dismissOrRemove } from './escMenuStack.js';

// Shared state/functions injected by init()
let _envState;
let _sshCmd;
let _getPort;
let _sshPrefix;
let _getPlatform;
let _isWindows;
let _isMetal;
let _buildEnvPrefix;
let _buildServeCmd;
let _shellQuote;
let _psQuote;
let _detectBackend;
let _detectToolParser;
let _detectModelOptimizations;
let _loadPresets;
let _savePresets;
let _copyText;
let _persistEnvState;
let _getGpuToggleTotal;
let modelLogo;
let esc;
let _launchServeTask;
let _retryDownload;
let _nextAvailablePort;

// Storage keys
const SERVE_STATE_KEY = 'cookbook-serve-state';

let _cachedAllModels = [];

function _repoLooksAwqLike(model, repo) {
  const q = String(model?.quant || '').toUpperCase();
  const n = `${repo || ''} ${model?.repo_id || ''} ${model?.name || ''} ${model?.path || ''}`.toLowerCase();
  return /^AWQ|^GPTQ/.test(q) || q === 'FP8' || /\b(awq|gptq|fp8)\b/i.test(n);
}

function _repoLooksGgufLike(model, repo) {
  const q = String(model?.quant || '').toUpperCase();
  const n = `${repo || ''} ${model?.repo_id || ''} ${model?.name || ''} ${model?.path || ''}`.toLowerCase();
  return !!model?.is_gguf || /^Q[2-8]/.test(q) || /^IQ/.test(q) || q === 'GGUF' || n.includes('gguf');
}

function _serveBackendWarning(model, repo, backend, fields = {}) {
  const awqLike = _repoLooksAwqLike(model, repo);
  const ggufLike = _repoLooksGgufLike(model, repo);
  if (awqLike && (backend === 'llamacpp' || backend === 'ollama')) {
    return {
      title: 'AWQ needs vLLM or SGLang',
      body: 'This model looks like AWQ/GPTQ/FP8 safetensors. llama.cpp and Ollama need GGUF files, so this backend cannot serve it. Choose vLLM/SGLang on a CUDA/ROCm GPU server, or download a GGUF version for llama.cpp/Ollama.',
    };
  }
  if (awqLike && _isMetal() && (backend === 'vllm' || backend === 'sglang')) {
    return {
      title: 'AWQ is not a unified-memory path',
      body: 'This model looks like AWQ/GPTQ/FP8 safetensors. AWQ is for vLLM/SGLang on CUDA/ROCm-style GPU servers, not local unified-memory llama.cpp/Ollama serving. For unified memory, download a GGUF model and use llama.cpp/Ollama.',
    };
  }
  if (awqLike && fields.unified_mem) {
    return {
      title: 'AWQ is not a unified-memory path',
      body: 'This model looks like AWQ/GPTQ/FP8 safetensors, but unified-memory local serving expects GGUF. Use vLLM/SGLang on a compatible GPU server, or download a GGUF version for llama.cpp/Ollama.',
    };
  }
  if (ggufLike && (backend === 'vllm' || backend === 'sglang')) {
    return {
      title: 'GGUF needs llama.cpp or Ollama',
      body: 'This model looks like GGUF. vLLM/SGLang expect HuggingFace safetensors-style repos. Choose llama.cpp/Ollama for GGUF, or download a safetensors model for vLLM/SGLang.',
    };
  }
  return null;
}

function _hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj || {}, key);
}

function _allGpuIds(count) {
  const n = Number(count || 0);
  if (!Number.isFinite(n) || n <= 0) return '';
  return Array.from({ length: Math.floor(n) }, (_, i) => String(i)).join(',');
}

function _selectedServeTarget(panel) {
  const select = document.getElementById('hwfit-server-select') || document.getElementById('hwfit-dl-server');
  const servers = Array.isArray(_envState.servers) ? _envState.servers : [];
  let host = _envState.remoteHost || '';
  let server = host ? (_serverByVal?.(_envState.remoteServerKey || host) || servers.find(s => s.host === host)) : null;
  if (select && select.value != null) {
    if (select.value === 'local') {
      host = '';
      server = servers.find(s => !s.host || s.host === 'local') || null;
    } else {
      const idx = /^\d+$/.test(String(select.value)) ? parseInt(select.value, 10) : -1;
      server = _serverByVal?.(select.value) || (idx >= 0 ? servers[idx] : null) || null;
      host = server?.host || '';
    }
  }
  const venv = panel?.querySelector('[data-field="venv"]')?.value?.trim() || server?.envPath || _envState.envPath || '';
  const label = host
    ? (server?.name ? `${server.name} (${host})` : host)
    : (server?.name || 'local server');
  return {
    host,
    port: host ? (_getPort(host) || server?.port || '') : '',
    venv,
    label,
  };
}

async function _fetchServeRuntimePackage(panel, backend) {
  const packageByBackend = {
    vllm: 'vllm',
    sglang: 'sglang',
    llamacpp: 'llama_cpp',
    diffusers: 'diffusers',
  };
  const packageName = packageByBackend[backend];
  if (!packageName) return null;
  const target = _selectedServeTarget(panel);
  const params = new URLSearchParams();
  if (target.host) {
    params.set('host', target.host);
    if (target.port) params.set('ssh_port', target.port);
    if (target.venv) params.set('venv', target.venv);
  }
  const res = await fetch('/api/cookbook/packages' + (params.toString() ? '?' + params.toString() : ''), { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  const pkg = (data.packages || []).find(p => p.name === packageName);
  return { pkg, target };
}

function _runtimeNoteText(backend, pkg, target) {
  const labels = { vllm: 'vLLM', sglang: 'SGLang', llamacpp: 'llama.cpp', diffusers: 'Diffusers' };
  const label = labels[backend] || backend;
  if (!pkg) return `${label} readiness unavailable for ${target.label}.`;
  const note = pkg.status_note || pkg.update_note || '';
  if (pkg.installed) {
    return note ? `${label} ready on ${target.label}: ${note}` : `${label} ready on ${target.label}.`;
  }
  return note ? `${label} missing on ${target.label}: ${note}` : `${label} missing on ${target.label}.`;
}

// ── Filter/sort cached model list ──

function _filterCachedList() {
  const list = document.getElementById('hwfit-cached-list');
  const tagContainer = document.getElementById('serve-tags');
  if (!list) return;
  const activeTag = tagContainer?.querySelector('.memory-cat-chip.active')?.dataset.serveTag || '';
  const searchVal = (document.getElementById('serve-search')?.value || '').toLowerCase().trim();
  const isFamily = activeTag.startsWith('fam:');
  const familyVal = isFamily ? activeTag.slice(4) : '';

  list.querySelectorAll('.memory-item[data-repo]').forEach(item => {
    const repo = (item.dataset.repo || '').toLowerCase();
    const tag = item.dataset.tag || '';
    const family = item.dataset.family || '';
    const tagMatch = !activeTag || (isFamily ? family === familyVal : tag === activeTag);
    const searchMatch = !searchVal || repo.includes(searchVal);
    item.style.display = (tagMatch && searchMatch) ? '' : 'none';
  });
}

// Is there a live download task for this repo in the Running tab? The cache
// reports any incomplete download dir as "downloading", but if nothing is
// actively pulling it, it's really a stalled/partial download — so we label it
// accordingly. Reads the running-tab tasks straight from localStorage (same
// key the running module writes) to avoid a cross-module import cycle.
function _isActivelyDownloading(repoId) {
  try {
    const tasks = JSON.parse(localStorage.getItem('cookbook-tasks')) || [];
    const short = (repoId || '').split('/').pop();
    return tasks.some(t => t.type === 'download' && t.status === 'running'
      && (t.payload?.repo_id === repoId || t.name === repoId || t.name === short
          || (t.payload?.repo_id || '').split('/').pop() === short));
  } catch { return false; }
}

// Same idea for serve: is there a live serve task for this repo? Used to
// surface a "running" pill on the Serve tab card.
function _isActivelyServing(repoId) {
  try {
    const tasks = JSON.parse(localStorage.getItem('cookbook-tasks')) || [];
    const short = (repoId || '').split('/').pop();
    return tasks.some(t => t.type === 'serve' && t.status === 'running'
      && (t.payload?.repo_id === repoId || t.name === repoId || t.name === short
          || (t.payload?.repo_id || '').split('/').pop() === short));
  } catch { return false; }
}

function _formatGgufSize(bytes) {
  const n = Number(bytes || 0);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n >= 1024 ** 3) return `${(n / (1024 ** 3)).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${Math.round(n / (1024 ** 2))} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

function _ggufFilesForModel(model) {
  return Array.isArray(model?.gguf_files)
    ? model.gguf_files.filter(f => f && typeof f.rel_path === 'string' && f.rel_path)
    : [];
}

function _runnableGgufFiles(model) {
  const files = _ggufFilesForModel(model);
  const primary = files.filter(f => (f.role || 'model') === 'model');
  return primary.length ? primary : files;
}

function _ggufFileLabel(file) {
  const base = (file.name || file.rel_path || '').split('/').pop();
  const size = _formatGgufSize(file.size_bytes);
  const quant = file.quant ? `${file.quant} ` : '';
  const parts = Number(file.parts || 0);
  const split = parts > 1 ? `, ${parts} parts` : '';
  const role = file.role && file.role !== 'model' ? ` ${file.role}` : '';
  return `${quant}${base}${size || split ? ` (${[size, split.replace(/^, /, '')].filter(Boolean).join(', ')})` : ''}${role}`;
}

function _shellPathExpr(path) {
  const s = String(path || '');
  if (s === '~') return '${HOME}';
  if (s.startsWith('~/')) return '${HOME}' + _shellQuote(s.slice(1));
  return _shellQuote(s);
}

function _selectedGgufExpr(model, repo, relPath) {
  const rel = String(relPath || '').replace(/^\/+/, '');
  if (!rel) return '';
  if (model.is_local_dir && model.path) {
    const base = String(model.path || '').replace(/\/+$/, '');
    return `$(printf %s ${_shellPathExpr(`${base}/${repo}/${rel}`)})`;
  }
  if (model.path) {
    const base = String(model.path || '').replace(/\/+$/, '');
    return `$(printf %s ${_shellPathExpr(`${base}/models--${repo.replace(/\//g, '--')}/snapshots/${rel}`)})`;
  }
  const cacheRepo = repo.replace(/\//g, '--');
  return `$(printf %s \${HOME}${_shellQuote(`/.cache/huggingface/hub/models--${cacheRepo}/snapshots/${rel}`)})`;
}

function _ggufSearchDirExpr(model, repo) {
  if (model.is_local_dir && model.path) return _shellQuote(`${String(model.path || '').replace(/\/+$/, '')}/${repo}`);
  if (model.path) return _shellQuote(`${String(model.path || '').replace(/\/+$/, '')}/models--${repo.replace(/\//g, '--')}/snapshots`);
  return `"$HOME/.cache/huggingface/hub/models--${repo.replace(/\//g, '--')}/snapshots"`;
}

function _rerenderCachedModels() {
  const list = document.getElementById('hwfit-cached-list');
  const tagContainer = document.getElementById('serve-tags');
  if (!list || !_cachedAllModels.length) return;

  const allModels = _cachedAllModels;
  const _h = (text) => `<span class="hwfit-hint" title="${text}">?</span>`;

  const activeTag = tagContainer?.querySelector('.memory-cat-chip.active')?.dataset.serveTag || '';
  const searchVal = (document.getElementById('serve-search')?.value || '').toLowerCase().trim();

  const sortVal = document.getElementById('serve-sort')?.value || 'name';
  const _parseSize = (s) => { const m = (s || '').match(/([\d.]+)\s*(GB|MB|KB)/i); if (!m) return 0; const n = parseFloat(m[1]); if (m[2] === 'GB') return n * 1024; if (m[2] === 'MB') return n; return n / 1024; };
  if (sortVal === 'name') allModels.sort((a, b) => (a.repo_id || '').localeCompare(b.repo_id || ''));
  else if (sortVal === 'size-desc') allModels.sort((a, b) => _parseSize(b.size) - _parseSize(a.size));
  else if (sortVal === 'size-asc') allModels.sort((a, b) => _parseSize(a.size) - _parseSize(b.size));
  else if (sortVal === 'recent') allModels.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));

  let html = '';
  let visibleCount = 0;
  for (const m of allModels) {
    if (activeTag && m._tag !== activeTag) continue;
    if (searchVal && !(m.repo_id || '').toLowerCase().includes(searchVal)) continue;
    visibleCount++;
    const shortName = m.repo_id.split('/').pop() || m.repo_id;
    const hfLink = m.repo_id.includes('/') ? `https://huggingface.co/${m.repo_id}` : '';
    const metaParts = [];
    if (m.repo_id.includes('/')) metaParts.push(m.repo_id.split('/')[0]);
    metaParts.push(m.size);
    if (m.path) {
      metaParts.push(`<span style="opacity:0.7;">${esc(m.path)}</span>`);
    }
    const ggufCount = _runnableGgufFiles(m).length;
    if (ggufCount > 1) metaParts.push(`${ggufCount} GGUFs`);
    // "downloading" status now renders as a title-row pill instead of
    // a meta-row text label, matching the "running" pill style and
    // living on the same line as the model name.
    const _isDownloading = m.status === 'downloading';
    const _isDlActive = _isDownloading ? _isActivelyDownloading(m.repo_id) : false;
    const isSelectMode = document.getElementById('hwfit-cache-select')?.classList.contains('active');
    html += `<div class="doclib-card memory-item" data-repo="${esc(m.repo_id)}" data-tag="${m._tag || ''}" data-family="${m._family || ''}" style="cursor:pointer;">`;
    html += `<span class="serve-select-cb memory-select-dot" style="display:${isSelectMode ? 'inline-block' : 'none'};cursor:pointer;"></span>`;
    html += `<div style="flex:1;min-width:0;">`;
    const _mc = modelColor(m.repo_id) || '';
    const _runningPill = _isActivelyServing(m.repo_id)
      ? ` <span class="cookbook-serve-running-pill is-clickable" title="This model is currently being served — click to open in Running" data-repo="${esc(m.repo_id)}" role="button" tabindex="0">running</span>`
      : '';
    const _downloadingPill = _isDownloading
      ? ` <span class="cookbook-serve-downloading-pill${_isDlActive ? '' : ' is-stalled'}" title="${_isDlActive ? 'Download in progress' : 'Download stalled — retry to resume'}">${_isDlActive ? 'downloading' : 'stalled'}</span>`
      : '';
    html += `<div class="memory-item-title"${_mc ? ` style="color:${_mc}"` : ''}>${modelLogo(m.repo_id)}${esc(shortName)}${hfLink ? ` <a href="${esc(hfLink)}" target="_blank" rel="noopener" class="cookbook-hf-link">HF ↗</a>` : ''}${_runningPill}${_downloadingPill}</div>`;
    html += `<div class="memory-item-meta" style="font-size:10px;opacity:0.4;margin-top:2px;">${metaParts.join(' \u00b7 ')}</div>`;
    html += `</div>`;
    const _bk = _detectBackend(m).backend;
    const _bkIco = _bk === 'llamacpp' ? '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M7 3C5.5 5 5 8 5 11v7c0 1.5 1 3 3 3h1v-4h6v4h1c2 0 3-1.5 3-3v-7c0-3-.5-6-2-8l-1 3c-.5-2-1.5-4-3-5-.5 2-1 3-1.5 3S11 3.5 10.5 2L7 3z" fill="currentColor"/><circle cx="9" cy="11" r="1.5" fill="var(--bg,#1a1a2e)"/><circle cx="15" cy="11" r="1.5" fill="var(--bg,#1a1a2e)"/></svg>'
      : _bk === 'diffusers' ? '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10 10-4.5 10-10S17.5 2 12 2zm0 3c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zM6 9c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm0 6c1.1 0 2 .9 2 2s-.9 2-2 2-2-.9-2-2 .9-2 2-2zm6 4c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm4-8c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z" fill="currentColor"/></svg>'
      : '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M4 4l8 16 8-16h-4l-4 8-4-8z" fill="currentColor"/></svg>';
    html += `<span class="cookbook-card-backend" data-detected="${_bk}">${_bkIco}</span>`;
    html += `<div class="memory-item-actions"><button type="button" class="memory-item-btn hwfit-cached-menu-btn" title="Actions" aria-label="Model actions"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button></div>`;
    html += `</div>`;
  }
  if (!visibleCount) html += '<div class="hwfit-loading">No matching models</div>';
  list.innerHTML = html;

  // Wire tag chips
  if (tagContainer) {
    tagContainer.querySelectorAll('.memory-cat-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        tagContainer.querySelectorAll('.memory-cat-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        _filterCachedList();
      });
    });
  }

  // Long-press anywhere on a cached model card → click its ⋮ menu, so
  // mobile users don't have to hit the small 3-dot target precisely.
  list.querySelectorAll('.memory-item').forEach(item => {
    const menuBtn = item.querySelector('.hwfit-cached-menu-btn');
    if (!menuBtn || item.dataset.lpWired === '1') return;
    item.dataset.lpWired = '1';
    let _t = null;
    let _y = 0;
    const _cancel = () => { if (_t) { clearTimeout(_t); _t = null; } };
    item.addEventListener('touchstart', (e) => {
      if (e.target.closest('button, a, input, textarea, .hwfit-cached-dropdown')) return;
      _y = e.touches?.[0]?.clientY ?? 0;
      _t = setTimeout(() => { _t = null; try { menuBtn.click(); } catch {} }, 500);
    }, { passive: true });
    item.addEventListener('touchmove', (e) => {
      const y = e.touches?.[0]?.clientY ?? 0;
      if (Math.abs(y - _y) > 8) _cancel();
    }, { passive: true });
    item.addEventListener('touchend', _cancel, { passive: true });
    item.addEventListener('touchcancel', _cancel, { passive: true });
  });

  // Wire menu on each cached model
  list.querySelectorAll('.hwfit-cached-menu-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Toggle: if a dropdown for THIS button is already open, close it
      // (through its own dismiss so the Escape-stack entry goes with it).
      const existing = document.querySelector('.hwfit-cached-dropdown');
      if (existing && existing._anchor === btn) {
        if (typeof existing._dismiss === 'function') existing._dismiss();
        else { existing.remove(); btn.classList.remove('cookbook-menu-active'); }
        return;
      }
      // Otherwise close any other open menu (and clear its anchor's active
      // state) before opening fresh.
      document.querySelectorAll('.hwfit-cached-dropdown').forEach(d => {
        if (d._anchor) d._anchor.classList.remove('cookbook-menu-active');
        if (typeof d._dismiss === 'function') d._dismiss(); else d.remove();
      });
      const item = btn.closest('.memory-item');
      const repo = item?.dataset.repo;
      if (!repo) return;
      const m = allModels.find(x => x.repo_id === repo);

      const dropdown = document.createElement('div');
      dropdown.className = 'hwfit-cached-dropdown';
      dropdown._anchor = btn;
      btn.classList.add('cookbook-menu-active');
      // Shared close — used by every item, the mobile Cancel, outside-click,
      // and the Escape arbiter (reassigned to the registry-aware close below).
      let closeDropdown = () => { dropdown.remove(); btn.classList.remove('cookbook-menu-active'); };
      const _di = (svg) => `<span class="dropdown-icon">${svg}</span>`;
      const _serveIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
      const _retryIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
      const _deleteIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
      const _selectIco = '<span style="font-size:16px;line-height:1;position:relative;top:-2px;">●</span>';
      const _schedIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
      const items = [];
      if (m && m.status === 'ready') items.push({ label: 'Serve', icon: _serveIco, action: 'serve' });
      if (m && m.status === 'downloading') items.push({ label: 'Retry', icon: _retryIco, action: 'retry' });
      if (m && m.status === 'ready') items.push({ label: 'Schedule…', icon: _schedIco, action: 'schedule' });
      items.push({ label: 'Select', icon: _selectIco, action: 'select' });
      items.push({ label: 'Delete', icon: _deleteIco, action: 'delete', danger: true });
      for (const opt of items) {
        const div = document.createElement('div');
        div.className = 'dropdown-item-compact' + (opt.danger ? ' dropdown-item-danger' : '');
        div.innerHTML = _di(opt.icon) + '<span>' + opt.label + '</span>';
        div.addEventListener('click', () => {
          closeDropdown();
          if (opt.action === 'serve') item.click();
          else if (opt.action === 'delete') _deleteCachedModel(repo, item, false, m);
          else if (opt.action === 'retry') _retryCachedModel(repo, m);
          else if (opt.action === 'schedule') {
            // Same entry point as the ^ button next to Launch — let
            // cookbookSchedule.js handle it. Expand the panel first
            // so the form has somewhere to mount.
            if (!item.querySelector('.hwfit-serve-panel')) item.click();
            setTimeout(() => {
              const arrow = item.querySelector('.hwfit-serve-schedule-arrow');
              if (arrow) arrow.click();
            }, 120);
          }
          else if (opt.action === 'select') {
            const selectBtn = document.getElementById('hwfit-cache-select');
            const bulkBar = document.getElementById('serve-bulk-bar');
            if (selectBtn) {
              selectBtn.classList.add('active');
              selectBtn.textContent = 'Cancel';
            }
            if (bulkBar) bulkBar.classList.remove('hidden');
            document.querySelectorAll('.serve-select-cb').forEach(dot => {
              dot.style.display = 'inline-block';
            });
            const dot = item.querySelector('.serve-select-cb');
            if (dot) dot.classList.add('selected');
            const count = document.querySelectorAll('.serve-select-cb.selected').length;
            const countEl = document.getElementById('serve-bulk-count');
            if (countEl) countEl.textContent = count + ' selected';
            const all = document.getElementById('serve-select-all');
            const dots = document.querySelectorAll('.serve-select-cb');
            if (all) all.checked = dots.length > 0 && count === dots.length;
          }
        });
        dropdown.appendChild(div);
      }
      // Mobile-only Cancel — gives an explicit close on touch devices where
      // outside-tap-to-close is fiddly. Hidden on desktop via CSS.
      const _cancelIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
      const cancelDiv = document.createElement('div');
      cancelDiv.className = 'dropdown-item-compact dropdown-cancel-mobile';
      cancelDiv.innerHTML = _di(_cancelIco) + '<span>Cancel</span>';
      cancelDiv.addEventListener('click', () => { closeDropdown(); });
      dropdown.appendChild(cancelDiv);
      const rect = btn.getBoundingClientRect();
      dropdown.style.cssText = `position:fixed;z-index:10001;visibility:hidden;top:0;right:${window.innerWidth-rect.right}px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,0.3);font-size:12px;`;
      document.body.appendChild(dropdown);
      // Clamp into the VISIBLE area (visualViewport, not innerHeight — they differ
      // on mobile under the dynamic toolbar). Flip above the button if there's no
      // room below, else clamp to the visible bottom edge, so it never runs
      // off-screen / grows the page.
      {
        const vv = window.visualViewport;
        const viewTop = vv ? vv.offsetTop : 0;
        const viewBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
        const dh = dropdown.offsetHeight;
        const mm = 8;
        let top = rect.bottom + 2;
        if (top + dh > viewBottom - mm) {
          const above = rect.top - 2 - dh;
          top = above >= viewTop + mm ? above : Math.max(viewTop + mm, viewBottom - dh - mm);
        }
        dropdown.style.top = top + 'px';
        dropdown.style.visibility = '';
      }
      closeDropdown = bindMenuDismiss(dropdown, () => { dropdown.remove(); btn.classList.remove('cookbook-menu-active'); }, (ev) => !dropdown.contains(ev.target) && ev.target !== btn);
    });
  });

  // Wire click on card to expand serve panel
  list.querySelectorAll('.memory-item[data-repo]').forEach(item => {
    item.addEventListener('click', (e) => {
      if (e.target.closest('a, .hwfit-cached-menu-btn, .memory-item-btn, .hwfit-serve-panel')) return;
      if (document.getElementById('hwfit-cache-select')?.classList.contains('active')) return;
      const repo = item.dataset.repo;
      if (!repo) return;
      const m = allModels.find(x => x.repo_id === repo);
      if (!m || m.status !== 'ready') return;

      // Toggle — close if already open
      if (item.classList.contains('doclib-card-expanded')) {
        const existingPanel = item.querySelector('.hwfit-serve-panel');
        existingPanel?._cleanupRuntimeReadiness?.();
        existingPanel?.remove();
        item.classList.remove('doclib-card-expanded');
        item.style.flexDirection = '';
        item.style.alignItems = '';
        list.style.minHeight = '';
        list.style.maxHeight = '';
        return;
      }

      // Collapse any other expanded
      list.querySelectorAll('.doclib-card-expanded').forEach(c => {
        const openPanel = c.querySelector('.hwfit-serve-panel');
        openPanel?._cleanupRuntimeReadiness?.();
        openPanel?.remove();
        c.classList.remove('doclib-card-expanded');
        c.style.flexDirection = '';
        c.style.alignItems = '';
      });

      const shortName = repo.split('/').pop();
      const _es = _envState;
      // The venv set per-server in Settings (server.envPath). Used as the venv
      // field default when the global active env path isn't carrying it, so a
      // configured server venv shows up without re-typing it.
      const _selSrv = _serverByVal?.(_es.remoteServerKey || _es.remoteHost || '') || {};
      const _srvVenv = _selSrv.envPath || '';
      // Serve state schema: { _byRepo: { <repo>: {...} }, _lastUsed: {...} }.
      // Loading priority: this-repo's saved settings → last-used (from any
      // model) as sensible first-run defaults → fall through to code defaults.
      // Legacy flat state (pre-schema) is also accepted as a last-resort fallback.
      let _allSs = {};
      try { _allSs = JSON.parse(localStorage.getItem(SERVE_STATE_KEY)) || {}; } catch {}
      const _byRepo = (_allSs && typeof _allSs === 'object' && _allSs._byRepo) || {};
      const _lastUsed = (_allSs && typeof _allSs === 'object' && _allSs._lastUsed) || null;
      const _isLegacyFlat = _allSs && typeof _allSs === 'object' && !_allSs._byRepo && !_allSs._lastUsed;
      const ss = (_byRepo[repo] && typeof _byRepo[repo] === 'object')
        ? _byRepo[repo]
        : (_lastUsed || (_isLegacyFlat ? _allSs : {}));
      const detectedBackend = _detectBackend(m).backend;
      const _allowedBackends = new Set(_isWindows()
        ? ['llamacpp']
        : (_isMetal() ? ['llamacpp', 'ollama'] : ['vllm', 'sglang', 'llamacpp', 'ollama', 'diffusers']));
      const defaultBackend = (ss._forceBackend && ss.backend && _allowedBackends.has(ss.backend))
        ? ss.backend
        : detectedBackend;
      const savedMatchesBackend = !!ss._forceBackend || (ss.backend || 'vllm') === detectedBackend;
      const sv = (k, def) => (ss[k] !== undefined && savedMatchesBackend) ? ss[k] : def;
      const defaultTp = defaultBackend === 'llamacpp' ? '1' : sv('tp', '1');
      const detectedGpuIds = _allGpuIds(_getGpuToggleTotal?.());
      const defaultGpus = defaultBackend === 'llamacpp'
        ? '0'
        : (savedMatchesBackend && _hasOwn(ss, 'gpus') && String(ss.gpus || '').trim()
          ? ss.gpus
          : (_es.gpus || detectedGpuIds));
      const tpOpts = [1,2,4,8].map(n => `<option${defaultTp==String(n)?' selected':''}>${n}</option>`).join('');
      const dtypeOpts = ['auto','float16','bfloat16'].map(d => `<option value="${d}"${sv('dtype','auto')===d?' selected':''}>${d}</option>`).join('');
      const vllmKvCacheOpts = ['auto','fp8'].map(d => `<option value="${d}"${sv('vllm_kv_cache_dtype','auto')===d?' selected':''}>${d}</option>`).join('');
      const _l = (name, tip) => `<span>${name}<span class="hwfit-hint" title="${tip}">?</span></span>`;
      const _ggufChoices = _runnableGgufFiles(m);
      const _savedGguf = String(sv('gguf_file', '') || '');
      const _defaultGguf = _ggufChoices.some(f => f.rel_path === _savedGguf)
        ? _savedGguf
        : (_ggufChoices[0]?.rel_path || '');
      const _ggufOptions = _ggufChoices.map(f =>
        `<option value="${esc(f.rel_path)}"${f.rel_path === _defaultGguf ? ' selected' : ''}>${esc(_ggufFileLabel(f))}</option>`
      ).join('');
      // Build save slots
      const _allPresets = _loadPresets();
      const _repoShort = repo.split('/').pop();
      const _modelPresets = _presetsForModel(_allPresets, repo);
      // Saved configs live in a single dropdown (used to be a row of squeezed
      // chips). The toggle shows the count; the menu lists each config (click to
      // load, × to delete) plus a "Save current config" row — see _showSavedConfigMenu.
      // Split button: "Save" saves the current config directly; the arrow opens
      // the dropdown of saved configs (load / delete). Arrow shows the count.
      // The arrow button shows just the saved-config count next to a "▾".
      // Spell out what the number means in the tooltip so users don't have
      // to click it to find out the badge isn't a notification dot.
      const _arrowLabel = _modelPresets.length > 0 ? `${_modelPresets.length} ▾` : '▾';
      const _arrowTitle = _modelPresets.length > 0
        ? `${_modelPresets.length} saved launch config${_modelPresets.length === 1 ? '' : 's'} for ${_repoShort} — click ▾ to load or delete`
        : `No saved launch configs for ${_repoShort} yet — click Save to add one`;
      let _slotsHtml = `<div class="cookbook-serve-slots cookbook-saved-split">`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-save" title="Save current config"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save</button>`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-arrow" title="${esc(_arrowTitle)}">${_arrowLabel}</button>`
        + `</div>`;

      let panelHtml = `<div class="hwfit-serve-panel">`;
      // Warn when serving a model whose download hasn't fully completed —
      // the user CAN still hit Launch (vLLM/llama-server will start, then
      // crash trying to read missing shards), but they should know.
      if (m && (m.status === 'downloading' || m.status === 'stalled' || m.has_incomplete)) {
        const _warnText = m.status === 'stalled'
          ? `This model looks like a stale download shell (${esc(m.size || '0 KB')}). The weights aren't on disk — the serve will fail to load. Re-download first, or pick another model.`
          : `This model's download isn't complete yet (${esc(m.size || 'partial')}). The serve will start but is likely to crash on a missing shard. Wait for the download to finish, or relaunch after it's done.`;
        panelHtml += `<div class="hwfit-serve-warn" style="margin:0 0 8px;padding:6px 10px;border-radius:5px;font-size:11px;background:color-mix(in srgb, var(--color-warning, #f0ad4e) 14%, transparent);border:1px solid color-mix(in srgb, var(--color-warning, #f0ad4e) 40%, transparent);color:var(--color-warning, #f0ad4e);display:flex;gap:6px;align-items:flex-start;line-height:1.4;"><span aria-hidden="true">⚠</span><span>${_warnText}</span></div>`;
      }
      // Row 1: Backend + Server + Env
      panelHtml += `<div class="hwfit-serve-row">`;
      const _backendChoices = _isWindows()
        ? [['llamacpp','llama.cpp']]
        : _isMetal()
        // Diffusers (diffusion_server.py) is CUDA-only — omit it on Metal.
        ? [['llamacpp','llama.cpp'],['ollama','Ollama']]
        : [['vllm','vLLM'],['sglang','SGLang'],['llamacpp','llama.cpp'],['ollama','Ollama'],['diffusers','Diffusers']];
      const backendOpts = _backendChoices.map(([v,l]) => `<option value="${v}"${defaultBackend===v?' selected':''}>${l}</option>`).join('');
      panelHtml += `<label>${_l('Backend','Inference engine: vLLM, SGLang, llama.cpp, Ollama, or Diffusers')}<select class="hwfit-sf" data-field="backend">${backendOpts}</select></label>`;
      panelHtml += `<input type="hidden" class="hwfit-sf" data-field="host" value="${esc(_es.remoteHost || '')}" />`;
      panelHtml += `<label>${_l('venv','Path to Python venv or conda env activate script')}<input type="text" class="hwfit-sf hwfit-sf-wide" data-field="venv" value="${esc(sv('venv', _es.envPath || _srvVenv || ''))}" placeholder="~/venv" /></label>`;
      const defaultPort = defaultBackend === 'ollama' ? '11434' : _nextAvailablePort();
      panelHtml += `<label>${_l('Port','HTTP port for the API server')}<input type="text" class="hwfit-sf" data-field="port" value="${esc(sv('port', defaultPort))}" /></label>`;
      const _activeGpus = (defaultGpus || '').split(',').map(s => s.trim()).filter(Boolean);
      const detectedGpuCount = Number(_getGpuToggleTotal?.() || 0);
      const _gpuMax = Math.max(detectedGpuCount || 8, ...(_activeGpus.map(Number).filter(n => !isNaN(n)).map(n => n + 1)));
      let _gpuBtnsHtml = '';
      for (let i = 0; i < _gpuMax; i++) {
        const on = _activeGpus.includes(String(i));
        _gpuBtnsHtml += `<button type="button" class="cookbook-gpu-btn${on ? ' active' : ''}" data-gpu="${i}">${i}</button>`;
      }
      panelHtml += `<label>${_l('GPUs','Toggle which GPUs to use')}<div class="cookbook-gpu-group">${_gpuBtnsHtml}</div><input type="hidden" class="hwfit-sf" data-field="gpus" value="${esc(defaultGpus)}" /></label>`;
      // Save / saved-configs split button — moved into Row 1 (next to GPUs)
      // so it shares the same baseline as the rest of the top controls.
      panelHtml += _slotsHtml;
      panelHtml += `</div>`;
      panelHtml += `<div class="hwfit-serve-runtime-note" style="display:none;font-size:11px;line-height:1.35;color:var(--fg-muted);margin-top:-4px;"></div>`;
      if (_ggufChoices.length > 1) {
        // Show the GGUF File dropdown for BOTH llama.cpp and Ollama — Ollama
        // also needs to know which exact .gguf to import via the new
        // `docker exec ollama-test ollama-import` auto-fill (otherwise the
        // helper falls back to "first sorted gguf", which may not match what
        // the user picked).
        panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp hwfit-backend-ollama">`;
        panelHtml += `<label class="hwfit-backend-llamacpp hwfit-backend-ollama">${_l('GGUF File','Choose the exact GGUF artifact to serve from this cached model folder.')}<select class="hwfit-sf hwfit-sf-wide" data-field="gguf_file">${_ggufOptions}</select></label>`;
        panelHtml += `</div>`;
      } else if (_defaultGguf) {
        panelHtml += `<input type="hidden" class="hwfit-sf" data-field="gguf_file" value="${esc(_defaultGguf)}" />`;
      }
      // Row 2: Core settings — the handful you actually touch every launch.
      // TP / Context / GPU / GPU Mem / Max Seqs / Dtype. Everything else
      // (Swap, KV Cache, Attention backend, Env vars, llama.cpp batch/ubatch)
      // moved to the Advanced fold below to keep this row scannable.
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-vllm hwfit-backend-sglang hwfit-backend-llamacpp hwfit-backend-ollama">`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('TP','Tensor Parallelism — split model across N GPUs')}<select class="hwfit-sf" data-field="tp">${tpOpts}</select></label>`;
      // ctx resets to the model's max on every panel open (the real ctx slider
      // lives in the Scan/Download toolbar — see cookbook.js .hwfit-ctx-control).
      panelHtml += `<label>${_l('Context','Max tokens per request — resets to the model max on every open. Lower = less VRAM')}<input type="text" class="hwfit-sf" data-field="ctx" value="${esc(m.context_length || m.context || '20000')}" /></label>`;
      panelHtml += `<label>${_l('GPU','Which GPU to use. Leave empty for default')}<input type="text" class="hwfit-sf" data-field="gpu_id" value="${esc(sv('gpu_id', ''))}" placeholder="auto" style="width:50px;" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('GPU Mem','Fraction of GPU memory (0.0–1.0). Lower if OOM')}<input type="text" class="hwfit-sf" data-field="gpu_mem" value="${esc(sv('gpu_mem', '0.90'))}" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('Max Seqs','Maximum concurrent requests. Lower = less memory. Default 4 — prosumer GPUs often OOM on vLLM default 256 during CUDA graph capture.')}<input type="text" class="hwfit-sf" data-field="max_seqs" value="${esc(sv('max_seqs', '4'))}" placeholder="4" /></label>`;
      panelHtml += `<label>${_l('Dtype','Data type for weights. auto picks best for GPU')}<select class="hwfit-sf" data-field="dtype">${dtypeOpts}</select></label>`;
      panelHtml += `</div>`;
      // ── Advanced (collapsed by default) ──
      // Everything below the fold is tuning users only touch occasionally:
      // vLLM kernel/env knobs, llama.cpp fit/cache/split controls, the
      // GGUF batch sizes, the speculative-decoding row, and the live VRAM
      // monitor. Wrapped in a native <details> so toggle state survives
      // re-renders cheaply and a closed fold doesn't trigger any layout
      // work for the dozens of nested inputs.
      panelHtml += `<details class="hwfit-serve-advanced">`;
      panelHtml += `<summary class="hwfit-serve-advanced-summary">Advanced</summary>`;
      // Advanced vLLM/SGLang row (KV Cache, Attention, Swap, Env)
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-vllm hwfit-backend-sglang">`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('KV Cache','vLLM --kv-cache-dtype. auto uses the model/runtime default; fp8 reduces KV memory for long context.')}<select class="hwfit-sf" data-field="vllm_kv_cache_dtype" style="height:32px;">${vllmKvCacheOpts}</select></label>`;
      // Attention backend selector — pin the kernel impl. Default `auto` lets
      // vLLM pick FlashInfer (which JITs on first use and breaks on older
      // system nvcc) → FlashAttention → xformers. Forcing FLASH_ATTN skips
      // the JIT entirely, fixing the `nvcc fatal: Unsupported gpu
      // architecture 'compute_89'` failure mode on Ada / Hopper hosts.
      const vllmAttnBackendOpts = ['auto', 'FLASH_ATTN', 'XFORMERS', 'FLASHINFER', 'TORCH_SDPA']
        .map(b => `<option value="${b === 'auto' ? '' : b}"${(sv('vllm_attn_backend','') === (b === 'auto' ? '' : b)) ? ' selected' : ''}>${b}</option>`).join('');
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Attention','vLLM VLLM_ATTENTION_BACKEND. auto = vLLM picks (often FLASHINFER, which JITs and can fail on old nvcc). FLASH_ATTN skips the JIT entirely.')}<select class="hwfit-sf" data-field="vllm_attn_backend" style="height:32px;">${vllmAttnBackendOpts}</select></label>`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Swap','CPU swap space in GB. Leave empty to omit (removed in newer vLLM)')}<input type="text" class="hwfit-sf" data-field="swap" value="${esc(sv('swap', ''))}" placeholder="off" /></label>`;
      // Free-text env-vars field. Anything pasted here is prepended to the
      // launch command verbatim. Use for CUDACXX, PATH overrides, NCCL_*
      // tuning, or any other KEY=VALUE pair that doesn't have a dedicated
      // field. After the venv activate runs, $VIRTUAL_ENV / $PATH / etc. are
      // already exported so they expand correctly here.
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang" style="flex:1 1 100%;">${_l('Env','Extra KEY=VALUE env-var pairs prepended to the launch (space-separated). Example: CUDACXX=$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_nvcc/bin/nvcc — points flashinfer at the venv-bundled nvcc when the system one is too old for your GPU.')}<input type="text" class="hwfit-sf" data-field="extra_env" value="${esc(sv('extra_env',''))}" placeholder="CUDACXX=/path/to/nvcc NCCL_P2P_DISABLE=1" style="width:100%;" /></label>`;
      panelHtml += `</div>`;
      // Advanced llama.cpp row (Batch / UBatch — moved out of Core for the
      // same "rarely touched" reason as the vLLM extras above).
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp">`;
      panelHtml += `<label class="hwfit-backend-llamacpp">${_l('Batch','llama.cpp prompt batch size. Leave blank for llama.cpp default.')}<input type="text" class="hwfit-sf" data-field="llama_batch_size" value="${esc(sv('llama_batch_size', ''))}" placeholder="2048" /></label>`;
      panelHtml += `<label class="hwfit-backend-llamacpp">${_l('UBatch','llama.cpp physical micro-batch size. Leave blank for llama.cpp default.')}<input type="text" class="hwfit-sf" data-field="llama_ubatch_size" value="${esc(sv('llama_ubatch_size', ''))}" placeholder="512" /></label>`;
      panelHtml += `</div>`;
      // Row 2b: Diffusers settings
      const diffDtypeOpts = ['bfloat16','float16','float32'].map(d => `<option value="${d}"${sv('diff_dtype','bfloat16')===d?' selected':''}>${d}</option>`).join('');
      const deviceMapOpts = ['balanced','auto','sequential'].map(d => `<option value="${d}"${sv('diff_device_map','balanced')===d?' selected':''}>${d}</option>`).join('');
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-diffusers">`;
      panelHtml += `<label>Dtype${_h('Precision. bfloat16 recommended for Flux, float16 for SD')} <select class="hwfit-sf" data-field="diff_dtype">${diffDtypeOpts}</select></label>`;
      panelHtml += `<label>Device Map${_h('How to place model on GPUs. balanced = split evenly')} <select class="hwfit-sf" data-field="diff_device_map">${deviceMapOpts}</select></label>`;
      panelHtml += `<label>Steps${_h('Default inference steps. More = better quality, slower')} <input type="text" class="hwfit-sf" data-field="diff_steps" value="${esc(sv('diff_steps', ''))}" placeholder="auto" /></label>`;
      panelHtml += `<label>Width${_h('Default output width')} <input type="text" class="hwfit-sf" data-field="diff_width" value="${esc(sv('diff_width', ''))}" placeholder="1024" /></label>`;
      panelHtml += `<label>Height${_h('Default output height')} <input type="text" class="hwfit-sf" data-field="diff_height" value="${esc(sv('diff_height', ''))}" placeholder="1024" /></label>`;
      panelHtml += `</div>`;
      // Row 3: Checkboxes (vLLM)
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-vllm hwfit-backend-sglang">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="enforce_eager"${sv('enforce_eager',false)?' checked':''} /> Enforce Eager${_h('Disable CUDA graphs. Slower but uses less memory')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="trust_remote"${sv('trust_remote',false)?' checked':''} /> Trust Remote Code${_h('Allow model to run custom code from HuggingFace')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="prefix_cache"${sv('prefix_cache',false)?' checked':''} /> Prefix Caching${_h('Cache shared prompt prefixes across requests')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm"><input type="checkbox" class="hwfit-sf" data-field="auto_tool"${sv('auto_tool',false)?' checked':''} /> Auto Tool Choice${_h('Enable function/tool calling for agent mode')}</label>`;
      panelHtml += `</div>`;
      // Row 2c: llama.cpp fit/perf flags (set by Auto profiles, editable by hand)
      const _kvOpts = ['', 'q4_0', 'q8_0', 'f16'].map(k => `<option value="${k}"${sv('cache_type','')===k?' selected':''}>${k||'default'}</option>`).join('');
      const llamaFitOpts = ['', 'off', 'on'].map(d => `<option value="${d}"${sv('llama_fit','')===d?' selected':''}>${d||'default'}</option>`).join('');
      const llamaSplitModeOpts = ['', 'layer', 'tensor', 'row', 'none'].map(d => `<option value="${d}"${sv('llama_split_mode','')===d?' selected':''}>${d||'default'}</option>`).join('');
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp">`;
      panelHtml += `<label>${_l('CPU MoE','n-cpu-moe: number of MoE expert layers to run on CPU when the model is bigger than VRAM. 0 = all on GPU. Set automatically by the Auto profiles below.')}<input type="text" class="hwfit-sf" data-field="n_cpu_moe" value="${esc(sv('n_cpu_moe',''))}" placeholder="0" style="width:54px;position:relative;top:-8px;" /></label>`;
      panelHtml += `<label>${_l('KV Cache','cache-type-k/v: quantize the KV cache. q4_0 = smallest (more context), q8_0 = sharp long-context, f16 = full. Blank = llama.cpp default.')}<select class="hwfit-sf" data-field="cache_type">${_kvOpts}</select></label>`;
      panelHtml += `<label class="hwfit-sf-cb" style="align-self:end;"><input type="checkbox" class="hwfit-sf" data-field="flash_attn"${sv('flash_attn',false)?' checked':''} /> Flash Attn${_h('--flash-attn on: faster attention + needed for quantized KV cache.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb" style="align-self:end;"><input type="checkbox" class="hwfit-sf" data-field="vision"${sv('vision',false)?' checked':''} /> Vision${_h('Serve with the vision encoder so the model can read images. Auto-finds an mmproj-*.gguf next to the model (download one into the model folder). Adds ~1 GB VRAM + a small per-image cost.')}</label>`;
      panelHtml += `<label>${_l('Fit','llama.cpp --fit. Leave default unless you need explicit off/on behavior for a preset.')}<select class="hwfit-sf" data-field="llama_fit">${llamaFitOpts}</select></label>`;
      panelHtml += `</div>`;
      // Row 2d: native llama-server placement/runtime controls. These are
      // explicit overrides for known-good advanced presets; blank keeps
      // llama.cpp/profile defaults.
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp">`;
      panelHtml += `<label>${_l('Split Mode','llama.cpp GPU placement. layer is the usual default; tensor splits weights and KV across GPUs.')}<select class="hwfit-sf" data-field="llama_split_mode" style="position:relative;top:-8px;">${llamaSplitModeOpts}</select></label>`;
      panelHtml += `<label>${_l('Tensor Split','GPU proportions for llama.cpp, e.g. 50,50 across two visible GPUs. Leave blank for auto.')}<input type="text" class="hwfit-sf" data-field="llama_tensor_split" value="${esc(sv('llama_tensor_split', ''))}" placeholder="50,50" /></label>`;
      panelHtml += `<label>${_l('Main GPU','llama.cpp --main-gpu index inside the visible GPU set. Mostly useful for split mode none/row.')}<input type="text" class="hwfit-sf" data-field="llama_main_gpu" value="${esc(sv('llama_main_gpu', ''))}" placeholder="auto" /></label>`;
      panelHtml += `<label>${_l('Parallel','llama.cpp parallel slots. Leave blank for llama.cpp default; 1 matches single-lane presets.')}<input type="text" class="hwfit-sf" data-field="llama_parallel" value="${esc(sv('llama_parallel', ''))}" placeholder="1" /></label>`;
      panelHtml += `</div>`;
      // Auto-profile chips row removed — visual fit with the rest of the
      // serve panel was off, and the manual ctx/n_cpu_moe/cache controls
      // above are already sufficient. The hwfit profile API
      // (/api/hwfit/profiles) is still available for any caller that
      // wants it.
      // Live VRAM / RAM-spillover monitor for the serve target's GPU. Polls
      // /api/cookbook/gpus while the panel is open so you can SEE whether the
      // config fits VRAM (fast) or spills to system RAM (slow). Populated after mount.
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp hwfit-vram-monitor" style="align-items:center;gap:8px;font-size:11px;">`;
      panelHtml += `<span style="opacity:0.7;">GPU memory:</span>`;
      panelHtml += `<span class="hwfit-vram-readout" style="opacity:0.5;">checking…</span>`;
      panelHtml += `</div>`;
      // Row 3a: Checkboxes (llama.cpp-only)
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-llamacpp">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="unified_mem"${sv('unified_mem',false)?' checked':''} /> Unified Memory${_h('For AMD APUs / Strix Halo: exports GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 so llama.cpp can address the full BIOS VRAM carveout instead of the default ~28 GB cap. No-op on discrete GPUs.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="llama_no_mmap"${sv('llama_no_mmap',false)?' checked':''} /> No mmap${_h('Adds --no-mmap for native llama-server. Useful for some high-context/local-storage setups, but not a universal default.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="llama_no_warmup"${sv('llama_no_warmup',false)?' checked':''} /> Skip warmup${_h('Adds --no-warmup. Can reduce startup memory spikes for tight launches, but llama.cpp defaults to warming up.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-spec-group"><input type="checkbox" class="hwfit-sf" data-field="llama_speculative_mtp"${sv('llama_speculative_mtp',false)?' checked':''} /> MTP Spec${_h('llama.cpp native MTP speculative decoding: --spec-type draft-mtp. Requires a GGUF with MTP heads and a recent llama-server build.')} <span class="hwfit-numstep"><button type="button" class="hwfit-numstep-btn" data-step="-1" tabindex="-1" aria-label="Decrease">‹</button><input type="number" class="hwfit-sf hwfit-spec-tokens" data-field="llama_spec_tokens" value="${esc(sv('llama_spec_tokens', '3'))}" min="1" max="10" title="--spec-draft-n-max" /><button type="button" class="hwfit-numstep-btn" data-step="1" tabindex="-1" aria-label="Increase">›</button></span></label>`;
      panelHtml += `</div>`;
      // Row 3b: Checkboxes (diffusers)
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-diffusers">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_offload"${sv('diff_offload',false)?' checked':''} /> CPU Offload${_h('Offload parts of model to CPU RAM to save VRAM. Slower but fits larger models')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_attention_slicing"${sv('diff_attention_slicing',false)?' checked':''} /> Attention Slicing${_h('Slice attention computation to reduce peak VRAM. Slower')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_vae_slicing"${sv('diff_vae_slicing',false)?' checked':''} /> VAE Slicing${_h('Process VAE in slices. Reduces VRAM for high-res images')}</label>`;
      panelHtml += `</div><div class="hwfit-serve-row hwfit-backend-diffusers">`;
      panelHtml += `<label>Harmonize GPU${_h('Separate GPU for img2img/harmonize. Leave empty to use same GPU')}<input type="text" class="hwfit-sf" data-field="diff_harmonize_gpu" value="${esc(sv('diff_harmonize_gpu', ''))}" placeholder="auto" style="width:50px;" /></label>`;
      panelHtml += `</div>`;
      // Row 4: Extra args
      panelHtml += `<div class="hwfit-serve-extra">`;
      panelHtml += `<label>Extra args<input type="text" class="hwfit-sf" data-field="extra" value="${esc(sv('extra', ''))}" placeholder="--flag value" /></label>`;
      panelHtml += `</div>`;
      // Model-specific optimizations. The checks row always renders for the
      // vLLM backend so the Speculative (MTP) control is ALWAYS reachable —
      // even for models the auto-detector doesn't recognize. Expert-parallel,
      // reasoning-parser and MoE-env still only appear when auto-detected.
      const _opts2 = _detectModelOptimizations(repo);
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-vllm">`;
      if (_opts2.flags.includes('--enable-expert-parallel')) panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="expert_parallel" /> Expert Parallel</label>`;
      if (_opts2.flags.some(f => f.includes('--reasoning-parser'))) { const rp = _opts2.flags.find(f => f.includes('--reasoning-parser')).split(' ')[1]; panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="reasoning_parser" data-parser="${rp}" /> Reasoning Parser <span class="hwfit-parser-tag">${rp}</span></label>`; }
      {
        // Speculative decoding (vLLM --speculative-config). Default OFF; the
        // method/token defaults come from auto-detection when available,
        // else fall back to MTP/3. Toggling the checkbox is what actually
        // adds the flag at launch (see cookbook.js command builder).
        const _specDef = _opts2.spec || { method: 'mtp', tokens: 3 };
        const _specMethod = sv('spec_method', _specDef.method);
        const _specTokens = sv('spec_tokens', String(_specDef.tokens));
        const _specMethods = ['mtp', 'qwen3_next_mtp', 'eagle', 'medusa', 'ngram'];
        if (!_specMethods.includes(_specMethod)) _specMethods.unshift(_specMethod);
        const _specOpts = _specMethods.map(m =>
          `<option value="${m}"${m === _specMethod ? ' selected' : ''}>${m}</option>`).join('');
        panelHtml += `<label class="hwfit-sf-cb hwfit-spec-group"><input type="checkbox" class="hwfit-sf" data-field="speculative" /> Speculative <select class="hwfit-sf hwfit-spec-method" data-field="spec_method" title="vLLM --speculative-config method">${_specOpts}</select><span class="hwfit-numstep"><button type="button" class="hwfit-numstep-btn" data-step="-1" tabindex="-1" aria-label="Decrease">‹</button><input type="number" class="hwfit-sf hwfit-spec-tokens" data-field="spec_tokens" value="${esc(_specTokens)}" min="1" max="10" title="num_speculative_tokens" /><button type="button" class="hwfit-numstep-btn" data-step="1" tabindex="-1" aria-label="Increase">›</button></span><span class="hwfit-help-chip hwfit-help-chip-inline" title="MTP / speculative decoding is supported on a few model families only — turn it on when the model card explicitly recommends it. On supported models it can boost inference throughput up to ~3×; on unsupported models it will either be ignored or fail to launch." style="margin-left:6px;">?</span></label>`;
      }
      if (_opts2.envVars.length) panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="moe_env" /> MoE Env Vars</label>`;
      panelHtml += `</div>`;
      // ── End Advanced fold ──
      panelHtml += `</details>`;
      // Command preview + actions. Wrap the textarea so a floating Copy
      // button can sit at its top-right corner — same pattern as the chat
      // run-output panel.
      panelHtml += `<div class="hwfit-serve-cmd-wrap">`;
      panelHtml += `<textarea class="hwfit-serve-cmd" spellcheck="false" rows="2"></textarea>`;
      panelHtml += `<button type="button" class="cookbook-btn hwfit-serve-copy hwfit-serve-copy-inline" title="Copy launch command" aria-label="Copy"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
      panelHtml += `</div>`;
      panelHtml += `<div class="hwfit-serve-actions">`;
      // Split button: main "Clear Server" + caret that opens Probe / Cancel.
      // The .cookbook-gpu-probe button stays in the DOM but hidden so the
      // existing event-listener wiring further down keeps working — the
      // popup just programmatically clicks it.
      panelHtml += `<span class="cookbook-gpu-split">`;
      panelHtml += `<button class="cookbook-btn cookbook-gpu-clear cookbook-gpu-split-main" title="Clear server GPU memory by stopping processes that hold VRAM (SIGTERM first)">Clear Server</button>`;
      panelHtml += `<button class="cookbook-btn cookbook-gpu-split-arrow" type="button" aria-haspopup="menu" aria-label="More GPU actions" title="More GPU actions"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 15 12 9 18 15"/></svg></button>`;
      panelHtml += `</span>`;
      panelHtml += `<button class="cookbook-btn cookbook-gpu-probe" style="display:none;" title="Probe GPU memory and running GPU processes">Probe GPUs</button>`;
      // Copy moved inside the command textarea (top-right). Spacer then
      // pushes Cancel + Launch to the right.
      panelHtml += `<span class="hwfit-serve-actions-spacer"></span>`;
      panelHtml += `<button class="cookbook-btn hwfit-serve-cancel" type="button" title="Close this configuration panel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;flex-shrink:0;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Cancel</button>`;
      // Launch + a small ^ that opens an inline schedule form. The form
      // creates a ScheduledTask (action=cookbook_serve), so the schedule
      // ends up in the existing Tasks UI for edit/delete/pause.
      panelHtml += `<span class="hwfit-serve-launch-group">`;
      panelHtml += `<button class="cookbook-btn hwfit-serve-launch"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px;flex-shrink:0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>Launch</button>`;
      // Chevron points DOWN because the schedule form opens beneath the
      // panel — the arrow signals the direction of motion, not menu state.
      panelHtml += `<button class="cookbook-btn hwfit-serve-schedule-arrow" type="button" aria-haspopup="true" aria-label="Schedule this serve on a recurring window" title="Schedule this serve as a recurring task"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>`;
      panelHtml += `</span>`;
      panelHtml += `</div>`;
      panelHtml += `</div>`;

      item.classList.add('doclib-card-expanded');
      item.style.flexDirection = 'column';
      item.style.alignItems = 'stretch';
      item.insertAdjacentHTML('beforeend', panelHtml);
      const panel = item.querySelector('.hwfit-serve-panel');
      // Scroll the serve panel into view within its nearest scrollable ancestor
      requestAnimationFrame(() => panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));

      // Build command preview
      function updateCmd() {
        const f = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') f[el.dataset.field] = el.checked;
          else f[el.dataset.field] = el.value;
        });
        const backend = f.backend || 'vllm';
        const serveModel = m.is_local_dir && m.path ? `${m.path}/${repo}` : repo;
        if (backend === 'llamacpp') {
          const ggufChoices = _runnableGgufFiles(m);
          const selectedGguf = ggufChoices.find(file => file.rel_path === f.gguf_file);
          // For multi-part GGUFs, llama.cpp requires the first split
          // (-00001-of-NNNNN.gguf). Prefer it (sorted, so UD-IQ4_XS/001 comes
          // before Q4_K_M/001 etc); fall back to any single GGUF sorted.
          const dir = _ggufSearchDirExpr(m, repo);
          // GGUF needs the actual .gguf FILE, not the folder. For a custom-dir
          // model the file lives under "<path>/<repo>" — search there just like we
          // search the HF snapshots dir, so serving a GGUF from a custom dir works
          // instead of handing llama.cpp a directory (which fails).
          const _ldir = m.path ? _shellQuote(`${m.path}/${repo}`) : '""';
          f._gguf_path = selectedGguf
            ? _selectedGgufExpr(m, repo, selectedGguf.rel_path)
            : m.is_local_dir && m.path
            ? `$({ find ${_ldir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${_ldir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`
            : `$({ find ${dir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${dir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`;
          // Vision: auto-find the mmproj (CLIP/projector) file in the same dir.
          // Resolved at runtime so the toggle just works if an mmproj-*.gguf is
          // present (downloaded alongside the model). Empty if none → cmd omits it.
          const _vsearchdir = (m.is_local_dir && m.path) ? _ldir : dir;
          f._mmproj_path = `$(find ${_vsearchdir} -iname 'mmproj*.gguf' 2>/dev/null | sort | head -1)`;
        }
        if (f.reasoning_parser) {
          const _rpEl2 = panel.querySelector('[data-field="reasoning_parser"]');
          f._reasoning_parser_value = _rpEl2?.dataset?.parser || 'qwen3';
        }
        let cmd = _buildServeCmd(f, serveModel, backend);
        if (f.extra && f.extra.trim()) cmd += ' ' + f.extra.trim();
        const _ce2 = panel.querySelector('.hwfit-serve-cmd'); _ce2.value = cmd; _ce2.style.height = 'auto'; _ce2.style.height = _ce2.scrollHeight + 'px';
        panel._cmd = cmd;
        panel._host = f.host || '';
        return cmd;
      }
      updateCmd();

      // Context clamp. Two ceilings:
      //  - ABSOLUTE_CTX_MAX: a hard sanity cap (no LLM trains past ~1M tokens),
      //    so an obvious typo like 16000000 can never reach llama.cpp even when
      //    we don't know the model's real limit (not in catalog / profiles
      //    fetch failed). This is what stops the radv ErrorDeviceLost crash.
      //  - panel._modelCtxMax: the model's actual trained limit (set by the
      //    profiles fetch below) — a tighter, model-specific cap when known.
      const ABSOLUTE_CTX_MAX = 1048576;   // 1M tokens — above any real n_ctx_train
      const _ctxEl0 = panel.querySelector('[data-field="ctx"]');
      function _clampCtx(announce) {
        if (!_ctxEl0) return;
        const cap = panel._modelCtxMax > 0 ? panel._modelCtxMax : ABSOLUTE_CTX_MAX;
        const v = parseInt(_ctxEl0.value, 10);
        if (Number.isFinite(v) && v > cap) {
          _ctxEl0.value = String(cap);
          _ctxEl0.title = `Capped to ${panel._modelCtxMax > 0 ? "this model's trained limit" : "the maximum sane context"} (${cap}).`;
          if (announce) uiModule.showToast(`Context capped to ${cap}`);
          updateCmd();
        }
      }
      if (_ctxEl0) {
        _ctxEl0.addEventListener('change', () => _clampCtx(false));
        _ctxEl0.addEventListener('blur', () => _clampCtx(false));
        _clampCtx(false);   // fix any stale/preset value already present
      }

      // Tighten the ctx slider's upper bound to the model's trained limit.
      // Asking llama.cpp for ctx > n_ctx_train overflows and, with a quantized
      // KV cache, can crash the GPU (radv ErrorDeviceLost). The auto-profile
      // chip row that used to also live here was removed — visual fit with
      // the rest of the serve panel was off — but this clamp is essential.
      (async () => {
        try {
          const host = (_es.remoteHost || '').trim();
          const params = new URLSearchParams({ model: repo });
          if (host) {
            params.set('host', host);
            const _sp = (_es.servers || []).find(s => s.host === host)?.port;
            if (_sp) params.set('ssh_port', _sp);
          }
          const res = await fetch(`/api/hwfit/profiles?${params}`);
          const data = await res.json();
          const ctxMax = Number(data && data.model_ctx_max) || 0;
          if (ctxMax > 0) {
            panel._modelCtxMax = ctxMax;
            _clampCtx(false);
          }
        } catch { /* clamp falls back to the static default */ }
      })();

      // Live GPU-memory monitor: poll /api/cookbook/gpus and show VRAM usage +
      // RAM-spillover, with a plain-language health/speed hint. Lets you tell at
      // a glance whether the chosen config fits VRAM (fast) or is paging into
      // system RAM over PCIe (slow). AMD sysfs reports gtt_used_mb for spillover.
      async function _refreshVramMonitor() {
        const el = panel.querySelector('.hwfit-vram-readout');
        if (!el || !document.body.contains(el)) return false;  // panel closed → stop
        try {
          const host = (_es.remoteHost || '').trim();
          const params = new URLSearchParams();
          if (host) {
            params.set('host', host);
            const _sp = (_es.servers || []).find(s => s.host === host)?.port;
            if (_sp) params.set('ssh_port', _sp);
          }
          const res = await fetch('/api/cookbook/gpus' + (params.toString() ? '?' + params : ''));
          const data = await res.json();
          const gpus = Array.isArray(data) ? data : (data.gpus || []);
          if (!gpus.length) { el.textContent = 'no GPU detected'; el.style.color = ''; return true; }
          const g = gpus[0];
          const usedG = (g.used_mb / 1024), totG = (g.total_mb / 1024);
          const pct = totG ? Math.round((usedG / totG) * 100) : 0;
          const freeG = Math.max(0, totG - usedG);
          const spillG = (g.gtt_used_mb || 0) / 1024;
          // Color: green < 85%, amber 85-97%, red > 97% or spilling.
          const spilling = spillG > 0.5 && !g.unified_memory;   // unified APUs always use GTT; not a spill
          let color = 'var(--green, #50fa7b)';
          if (pct >= 97 || spilling) color = 'var(--red, #ff5555)';
          else if (pct >= 85) color = 'var(--orange, #ffb86c)';
          let txt = `${usedG.toFixed(1)} / ${totG.toFixed(1)} GB (${pct}%) · ${freeG.toFixed(1)} GB free`;
          if (spilling) {
            txt += ` · ⚠ ${spillG.toFixed(1)} GB spilled to RAM — slow (raise CPU MoE or lower context)`;
          } else if (pct >= 90) {
            txt += ` · tight — risk of OOM/spill on long context or images`;
          } else {
            txt += ` · healthy`;
          }
          el.textContent = txt;
          el.style.color = color;
          return true;
        } catch {
          el.textContent = 'unavailable';
          el.style.color = '';
          return true;
        }
      }
      _refreshVramMonitor();
      // Poll every 4s while the panel is open; stop when it's removed from the DOM.
      const _vramTimer = setInterval(async () => {
        const ok = await _refreshVramMonitor();
        if (ok === false) clearInterval(_vramTimer);
      }, 4000);

      // Show/hide backend-specific sections
      function updateBackendVisibility() {
        const b = panel.querySelector('[data-field="backend"]')?.value || 'vllm';
        panel.querySelectorAll('[class*="hwfit-backend-"]').forEach(el => {
          const show = el.classList.contains(`hwfit-backend-${b}`);
          el.style.display = show ? '' : 'none';
        });
      }
      updateBackendVisibility();

      async function updateRuntimeReadinessNote() {
        const note = panel.querySelector('.hwfit-serve-runtime-note');
        if (!note) return;
        const backend = panel.querySelector('[data-field="backend"]')?.value || 'vllm';
        if (!['vllm', 'sglang', 'llamacpp', 'diffusers'].includes(backend)) {
          note.style.display = 'none';
          note.textContent = '';
          return;
        }
        const seq = (panel._runtimeReadinessSeq || 0) + 1;
        panel._runtimeReadinessSeq = seq;
        note.style.display = '';
        note.textContent = 'Checking runtime on selected server...';
        try {
          const { pkg, target } = await _fetchServeRuntimePackage(panel, backend);
          if (panel._runtimeReadinessSeq !== seq) return;
          note.textContent = _runtimeNoteText(backend, pkg, target);
          note.style.color = pkg?.installed ? 'var(--fg-muted)' : 'var(--red)';
        } catch (err) {
          if (panel._runtimeReadinessSeq !== seq) return;
          note.textContent = `Runtime readiness unavailable: ${err?.message || err}`;
          note.style.color = 'var(--fg-muted)';
        }
      }
      updateRuntimeReadinessNote();
      const runtimeServerSelect = document.getElementById('hwfit-server-select') || document.getElementById('hwfit-dl-server');
      if (runtimeServerSelect) {
        const refreshRuntimeOnServerChange = () => updateRuntimeReadinessNote();
        runtimeServerSelect.addEventListener('change', refreshRuntimeOnServerChange);
        panel._cleanupRuntimeReadiness = () => runtimeServerSelect.removeEventListener('change', refreshRuntimeOnServerChange);
      }

      // Wire save slots
      function _loadSlotIntoPanel(slotIdx) {
        const presets = _loadPresets();
        const modelSlots = _presetsForModel(presets, repo);
        const p = modelSlots[slotIdx];
        if (!p) return;
        const cmd = p.cmd || '';
        // Hoisted so the GPU/venv restore below can use it in BOTH branches —
        // it used to be scoped to the else branch, throwing a ReferenceError when
        // a preset had saved fields (which aborted GPU + env restoration).
        const _ex = (re) => { const m = cmd.match(re); return m ? m[1] : ''; };
        // Prefer saved field values; fall back to regex parsing of command string
        if (p.fields) {
          panel.querySelectorAll('.hwfit-sf').forEach(el => {
            const f = el.dataset.field;
            if (f && p.fields[f] !== undefined) {
              if (el.type === 'checkbox') el.checked = !!p.fields[f];
              else el.value = p.fields[f];
            }
          });
        } else {
          const fields = {
            backend: cmd.includes('llama_cpp') || cmd.includes('llama-server') ? 'llamacpp' : cmd.includes('diffusion_server') ? 'diffusers' : cmd.includes('sglang') ? 'sglang' : cmd.includes('ollama') ? 'ollama' : 'vllm',
            port: _ex(/--port\s+(\d+)/) || '8000',
            tp: _ex(/--tensor-parallel-size\s+(\d+)/) || '1',
            ctx: _ex(/--max-model-len\s+(\d+)/) || _ex(/--n_ctx\s+(\d+)/) || _ex(/-c\s+(\d+)/) || '8192',
            gpu_mem: _ex(/--gpu-memory-utilization\s+([\d.]+)/) || '0.90',
            swap: _ex(/--swap-space\s+(\d+)/) || '',
            dtype: _ex(/--dtype\s+(\w+)/) || 'auto',
            vllm_kv_cache_dtype: _ex(/--kv-cache-dtype\s+([\w.-]+)/) || 'auto',
            max_seqs: _ex(/--max-num-seqs\s+(\d+)/) || '',
            cache_type: _ex(/(?:--cache-type-k|-ctk)\s+(\S+)/) || '',
            llama_fit: _ex(/(?:--fit|-fit)\s+(on|off)/) || '',
            llama_split_mode: _ex(/(?:--split-mode|-sm)\s+(none|layer|row|tensor)/) || '',
            llama_tensor_split: _ex(/(?:--tensor-split|-ts)\s+([0-9.,]+)/) || '',
            llama_main_gpu: _ex(/(?:--main-gpu|-mg)\s+(\d+)/) || '',
            llama_parallel: _ex(/(?:--parallel|-np)\s+(\d+)/) || '',
            llama_batch_size: _ex(/(?:--batch-size|-b)\s+(\d+)/) || '',
            llama_ubatch_size: _ex(/(?:--ubatch-size|-ub)\s+(\d+)/) || '',
            llama_spec_tokens: _ex(/--spec-draft-n-max\s+(\d+)/) || '3',
            venv: p.envPath || '',
          };
          const checks = {
            enforce_eager: cmd.includes('--enforce-eager'),
            trust_remote: cmd.includes('--trust-remote-code'),
            prefix_cache: cmd.includes('--enable-prefix-caching'),
            auto_tool: cmd.includes('--enable-auto-tool-choice'),
            flash_attn: /--flash-attn\s+on\b/.test(cmd),
            unified_mem: /GGML_CUDA_ENABLE_UNIFIED_MEMORY=1/.test(cmd),
            llama_no_mmap: /--no-mmap\b/.test(cmd),
            llama_no_warmup: /--no-warmup\b/.test(cmd),
            llama_speculative_mtp: /--spec-type\s+\S*draft-mtp/.test(cmd),
            speculative: cmd.includes('--speculative-config'),
          };
          const _specMatch = cmd.match(/--speculative-config\s+'?\{[^}]*"method"\s*:\s*"([^"]+)"[^}]*"num_speculative_tokens"\s*:\s*(\d+)/);
          if (_specMatch) {
            fields.spec_method = _specMatch[1];
            fields.spec_tokens = _specMatch[2];
          }
          panel.querySelectorAll('.hwfit-sf').forEach(el => {
            const f = el.dataset.field;
            if (f && fields[f] !== undefined) { el.value = fields[f]; }
            if (f && checks[f] !== undefined && el.type === 'checkbox') { el.checked = checks[f]; }
          });
        }
        // Restore the venv path from the saved config — OVERRIDE whatever's in the
        // box (don't just fill when empty), so loading a config reliably brings its
        // venv with it. (task-saved / older presets keep it as p.envPath.) Only
        // skip when the preset has no venv at all, so we don't blank a typed one.
        const _vf = panel.querySelector('[data-field="venv"]');
        const _savedVenv = (p.fields && p.fields.venv) || p.envPath || '';
        if (_vf && _savedVenv) _vf.value = _savedVenv;
        // Restore the activated GPUs: saved field → command's CUDA_VISIBLE_DEVICES
        // → the preset's top-level gpus. Reflect them on both the hidden field
        // and the GPU buttons so the rebuilt command pins the same devices.
        const gpuVal = (p.fields && p.fields.gpus) || _ex(/CUDA_VISIBLE_DEVICES=(\S+)/) || p.gpus || '';
        const activeGpus = String(gpuVal).split(',').filter(Boolean);
        panel.querySelectorAll('.cookbook-gpu-btn').forEach(btn => {
          btn.classList.toggle('active', activeGpus.includes(btn.dataset.gpu));
        });
        const _gf = panel.querySelector('[data-field="gpus"]');
        if (_gf) _gf.value = activeGpus.join(',');
        updateBackendVisibility();
        updateRuntimeReadinessNote();
        updateCmd();
        panel.querySelectorAll('.cookbook-slot-btn').forEach(b => b.classList.remove('active'));
        panel.querySelector(`.cookbook-slot-btn[data-slot="${slotIdx}"]`)?.classList.add('active');
      }

      // Keep the arrow button's count + tooltip in sync with stored presets.
      function _updateSavedToggleLabel() {
        const n = _presetsForModel(_loadPresets(), repo).length;
        const t = panel.querySelector('.cookbook-saved-arrow');
        if (!t) return;
        t.textContent = n > 0 ? `${n} ▾` : '▾';
        t.title = n > 0
          ? `${n} saved launch config${n === 1 ? '' : 's'} for ${_repoShort} — click ▾ to load or delete`
          : `No saved launch configs for ${_repoShort} yet — click Save to add one`;
      }

      // Save the current panel fields as a new named preset (shared by the menu's
      // "Save current config" row). Returns true if a config was actually saved.
      async function _saveCurrentConfig() {
        const presets = _loadPresets();
        const modelSlots = _presetsForModel(presets, repo);
        // Compute the current launch command first so we can detect a no-op save.
        updateCmd();
        const cmd = panel._cmd;
        // Already saved? If an existing preset for this model has the identical
        // launch command, don't make a duplicate — tell the user via a popup.
        const _norm = s => String(s || '').replace(/\s+/g, ' ').trim();
        const _existing = modelSlots.find(p => _norm(p.cmd) === _norm(cmd));
        if (_existing) {
          await window.styledConfirm(`This config is already saved as "${_existing.label || 'Unnamed'}".`, { confirmText: 'OK', cancelText: 'Close' });
          return false;
        }
        if (modelSlots.length >= 5) { uiModule.showToast('Max 5 saves per model'); return false; }
        const label = await uiModule.styledPrompt('Name this config so you can recall it later.', {
          title: 'Save Config', placeholder: 'e.g. LoRA, 8-bit, fast', confirmText: 'Save',
        });
        if (!label) return false;
        const host = panel._host || '';
        const fields = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') fields[el.dataset.field] = el.checked;
          else fields[el.dataset.field] = el.value;
        });
        presets.push({ name: shortName, model: repo, cmd, remoteHost: host, port: fields.port || '8000', label, fields });
        _savePresets(presets);
        uiModule.showToast(`Saved "${label}"`);
        _updateSavedToggleLabel();
        return true;
      }

      // Saved-configs dropdown. Rebuilt each open (and after delete) so it always
      // reflects the stored presets. Standard Odysseus .dropdown look, positioned
      // fixed at the toggle and right-aligned to it.
      function _showSavedConfigMenu(anchor) {
        document.querySelectorAll('.cookbook-saved-menu').forEach(d => { if (typeof d._dismiss === 'function') d._dismiss(); else d.remove(); });
        const modelSlots = _presetsForModel(_loadPresets(), repo);
        const dropdown = document.createElement('div');
        dropdown.className = 'dropdown cookbook-saved-menu';
        let closeMenu = () => { dropdown.remove(); anchor.classList.remove('cookbook-menu-active'); };
        const rect = anchor.getBoundingClientRect();
        const minW = 190;
        // Cap width/height to the viewport and start hidden — we clamp the final
        // position after mount (below) using the menu's real measured size, so it
        // can't run off-screen on a narrow mobile viewport.
        dropdown.style.cssText = `position:fixed;display:block;visibility:hidden;z-index:10001;top:0;left:0;right:auto;min-width:${minW}px;max-width:calc(100vw - 16px);max-height:calc(100vh - 24px);overflow-y:auto;box-sizing:border-box;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:6px;font-size:11px;`;

        if (!modelSlots.length) {
          const empty = document.createElement('div');
          empty.style.cssText = 'padding:6px 8px;opacity:0.5;position:relative;top:1px;';
          empty.textContent = 'No saved configs yet';
          dropdown.appendChild(empty);
        }
        modelSlots.forEach((p, idx) => {
          const it = document.createElement('div');
          it.className = 'dropdown-item-compact';
          it.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;';
          const lbl = document.createElement('span');
          lbl.textContent = p.label || `Config ${idx + 1}`;
          lbl.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
          const del = document.createElement('button');
          del.type = 'button';
          del.innerHTML = '×';
          del.title = 'Delete';
          del.style.cssText = 'background:none;border:none;color:var(--fg-muted);cursor:pointer;font-size:15px;line-height:1;padding:0 2px;flex-shrink:0;';
          del.addEventListener('mouseenter', () => { del.style.color = '#f44'; });
          del.addEventListener('mouseleave', () => { del.style.color = 'var(--fg-muted)'; });
          it.appendChild(lbl);
          if (p.confirmedWorking) {
            const badge = document.createElement('span');
            badge.className = 'cookbook-saved-confirmed';
            badge.title = 'Confirmed working — this config launched and registered an endpoint';
            badge.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#50fa7b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
            it.appendChild(badge);
          }
          it.appendChild(del);
          it.addEventListener('click', (e) => {
            if (e.target === del) return;
            e.stopPropagation();
            // Close the menu FIRST so it always dismisses, even if loading throws.
            closeMenu();
            _loadSlotIntoPanel(idx);
            // Confirm the click landed — loading is silent otherwise, so it was
            // unclear the settings actually changed.
            uiModule.showToast(`Loaded "${p.label || `Config ${idx + 1}`}"`);
            // Briefly flash the command box so the user sees the panel update.
            const _cmdBox = panel.querySelector('.hwfit-serve-cmd');
            if (_cmdBox) {
              _cmdBox.classList.add('cookbook-cmd-flash');
              setTimeout(() => _cmdBox.classList.remove('cookbook-cmd-flash'), 600);
            }
          });
          del.addEventListener('click', async (e) => {
            e.stopPropagation();
            const label = p.label || `Config ${idx + 1}`;
            if (!await window.styledConfirm(`Delete saved config "${label}"?`, { confirmText: 'Delete', danger: true })) return;
            const cur = _loadPresets();
            const toRemove = _presetsForModel(cur, repo)[idx];
            if (toRemove) {
              const gi = cur.indexOf(toRemove);
              if (gi >= 0) cur.splice(gi, 1);
              _savePresets(cur);
            }
            uiModule.showToast(`Deleted "${label}"`);
            _updateSavedToggleLabel();
            _showSavedConfigMenu(anchor);   // rebuild in place
          });
          dropdown.appendChild(it);
        });

        document.body.appendChild(dropdown);
        // Clamp into the viewport using the menu's real size (both axes); flip
        // above the toggle if there isn't room below. Right-align to the anchor.
        const w = dropdown.offsetWidth, h = dropdown.offsetHeight;
        let left = Math.min(rect.right - w, window.innerWidth - w - 8);
        left = Math.max(8, left);
        let top = rect.bottom + 6;
        if (top + h > window.innerHeight - 8) top = Math.max(8, rect.top - 6 - h);
        dropdown.style.left = `${left}px`;
        dropdown.style.top = `${top}px`;
        dropdown.style.visibility = '';
        closeMenu = bindMenuDismiss(dropdown, () => { dropdown.remove(); anchor.classList.remove('cookbook-menu-active'); }, (ev) => !dropdown.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target));
      }

      // "Save" segment — save the current config directly.
      const savedSaveBtn = panel.querySelector('.cookbook-saved-save');
      if (savedSaveBtn) {
        savedSaveBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          document.querySelectorAll('.cookbook-saved-menu').forEach(dismissOrRemove);
          await _saveCurrentConfig();
        });
      }
      // Arrow segment — open/close the saved-configs dropdown.
      const savedArrowBtn = panel.querySelector('.cookbook-saved-arrow');
      if (savedArrowBtn) {
        savedArrowBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const openSaved = document.querySelector('.cookbook-saved-menu');
          if (openSaved) {
            if (typeof openSaved._dismiss === 'function') openSaved._dismiss();
            else { openSaved.remove(); savedArrowBtn.classList.remove('cookbook-menu-active'); }
            return;
          }
          savedArrowBtn.classList.add('cookbook-menu-active');
          _showSavedConfigMenu(savedArrowBtn);
        });
      }

      // Wire GPU toggle buttons
      panel.querySelectorAll('.cookbook-gpu-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          btn.classList.toggle('active');
          const activeBtns = [...panel.querySelectorAll('.cookbook-gpu-btn.active')];
          const active = activeBtns.map(b => b.dataset.gpu).join(',');
          panel.querySelector('[data-field="gpus"]').value = active;
          // Guard: vLLM/SGLang tensor-parallel only works across IDENTICAL GPUs.
          // If the probe knows the per-GPU models and the selection mixes types,
          // warn — serving across a mixed set will fail or run badly.
          const byIdx = panel._gpuProbe && panel._gpuProbe.byIdx;
          if (byIdx && activeBtns.length > 1) {
            const names = new Set(activeBtns
              .map(b => byIdx.get(parseInt(b.dataset.gpu)))
              .filter(Boolean)
              .map(g => g.name));
            if (names.size > 1 && !panel._mixedGpuWarned) {
              panel._mixedGpuWarned = true;   // once per panel, don't nag
              uiModule.showToast('Mixed GPU types selected — tensor-parallel needs identical GPUs. Pick one pool (e.g. all the same card).', 7000);
            } else if (names.size <= 1) {
              panel._mixedGpuWarned = false;  // reset once they're back to one pool
            }
          }
          updateCmd();
        });
      });

      // Wire "Probe GPUs" / "Clear Server" — annotate GPU buttons with free VRAM and per-GPU PIDs
      const _probeBtn = panel.querySelector('.cookbook-gpu-probe');
      const _clearBtn = panel.querySelector('.cookbook-gpu-clear');
      const _splitArrow = panel.querySelector('.cookbook-gpu-split-arrow');
      // Split-button arrow opens a small popup with the secondary action
      // (Probe GPUs) + a Cancel item. The popup re-uses the same probe
      // logic by programmatically clicking the hidden .cookbook-gpu-probe.
      if (_splitArrow) {
        _splitArrow.addEventListener('click', (ev) => {
          ev.stopPropagation();
          document.querySelectorAll('.cookbook-gpu-split-menu').forEach(m => { if (typeof m._dismiss === 'function') m._dismiss(); else m.remove(); });
          const menu = document.createElement('div');
          menu.className = 'cookbook-task-dropdown cookbook-gpu-split-menu';
          let closeMenu = () => menu.remove();
          const mk = (label, cls, onClick) => {
            const it = document.createElement('div');
            it.className = 'dropdown-item-compact' + (cls ? ' ' + cls : '');
            it.style.cssText = 'display:flex;align-items:center;gap:8px;';
            it.textContent = label;
            it.addEventListener('click', (e) => {
              e.stopPropagation();
              closeMenu();
              if (onClick) onClick();
            });
            return it;
          };
          menu.appendChild(mk('Probe GPUs', '', () => _probeBtn?.click()));
          menu.appendChild(mk('Cancel', 'dropdown-cancel-mobile', () => {}));
          const r = _splitArrow.getBoundingClientRect();
          menu.style.position = 'fixed';
          menu.style.right = (window.innerWidth - r.right) + 'px';
          document.body.appendChild(menu);
          // Default open BELOW, but if there's no room (esp. on mobile where
          // the arrow sits near the bottom of the modal) flip ABOVE so the
          // popup isn't off-screen.
          {
            const vv = window.visualViewport;
            const viewTop = vv ? vv.offsetTop : 0;
            const viewBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
            const mh = menu.offsetHeight;
            const m = 8;
            let top = r.bottom + 4;
            if (top + mh > viewBottom - m) {
              const above = r.top - 4 - mh;
              top = above >= viewTop + m ? above : Math.max(viewTop + m, viewBottom - mh - m);
            }
            menu.style.top = top + 'px';
          }
          // Close on outside click or Escape (via the registry); also dismiss
          // on scroll since the popup is fixed-positioned to the arrow.
          const _scrollClose = () => closeMenu();
          closeMenu = bindMenuDismiss(menu, () => { menu.remove(); window.removeEventListener('scroll', _scrollClose, true); }, (e) => !menu.contains(e.target) && e.target !== _splitArrow);
          window.addEventListener('scroll', _scrollClose, true);
        });
      }
      const _withSpinner = async (btn, fn) => {
        const origHtml = btn.innerHTML;
        btn.disabled = true;
        const wp = spinnerModule.createWhirlpool(14);
        wp.element.style.cssText = 'display:inline-block;vertical-align:middle;position:relative;top:-1px;margin:0 4px 0 0;width:14px;height:14px;';
        btn.innerHTML = '';
        btn.appendChild(wp.element);
        const lbl = document.createElement('span');
        lbl.textContent = origHtml.replace(/<[^>]*>/g, '').trim() || '…';
        lbl.style.cssText = 'vertical-align:middle;';
        btn.appendChild(lbl);
        try { return await fn(); }
        finally {
          wp.destroy();
          btn.innerHTML = origHtml;
          btn.disabled = false;
        }
      };
      if (_probeBtn) {
        // Per-panel state so a previously opened popup can be closed/reused
        panel._gpuProbe = panel._gpuProbe || { popup: null, byIdx: null };

        const _closeProbePopup = () => {
          if (panel._gpuProbe.popup) {
            panel._gpuProbe.popup.remove();
            panel._gpuProbe.popup = null;
          }
        };

        const _doKill = async (pid, sig, hostVal) => {
          const res = await fetch('/api/cookbook/kill-pid', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pid, signal: sig, host: hostVal || null }),
          });
          let data;
          try { data = await res.json(); } catch (_) { data = {}; }
          if (!res.ok || !data.ok) {
            const err = data.error || data.detail || res.statusText || 'unknown';
            uiModule.showToast(`Kill PID ${pid} failed: ${err}`, 6000);
            return false;
          }
          uiModule.showToast(`Sent SIG${sig} to PID ${pid}`, 3000);
          return true;
        };

        const _openProbePopup = (anchorBtn, gpu, hostVal) => {
          _closeProbePopup();
          const popup = document.createElement('div');
          popup.className = 'cookbook-gpu-popup';
          const procs = gpu.processes || [];
          const procHtml = procs.length === 0
            ? '<div class="cookbook-gpu-popup-empty">No GPU processes reported. VRAM may be held by a zombie or another tenant.</div>'
            : procs.map(p =>
                `<div class="cookbook-gpu-proc" data-pid="${p.pid}">
                   <span class="cookbook-gpu-proc-info">
                     <span class="cookbook-gpu-proc-pid">${p.pid}</span>
                     <span class="cookbook-gpu-proc-name" title="${esc(p.name)}">${esc(p.name)}</span>
                     <span class="cookbook-gpu-proc-mem">${(p.used_mb/1024).toFixed(1)}G</span>
                   </span>
                   <span class="cookbook-gpu-proc-actions">
                     <button type="button" class="cookbook-gpu-kill" data-sig="TERM" title="Graceful (SIGTERM)">Kill</button>
                     <button type="button" class="cookbook-gpu-kill" data-sig="KILL" title="Force (SIGKILL)">!</button>
                   </span>
                 </div>`
              ).join('');
          popup.innerHTML = `
            <div class="cookbook-gpu-popup-head">
              GPU ${gpu.index} · ${esc(gpu.name)}
              <span class="cookbook-gpu-popup-stats">${(gpu.free_mb/1024).toFixed(1)} / ${(gpu.total_mb/1024).toFixed(1)} GB free · util ${gpu.util_pct}%</span>
              <button type="button" class="cookbook-gpu-popup-close" title="Close">×</button>
            </div>
            <div class="cookbook-gpu-popup-body">${procHtml}</div>`;
          document.body.appendChild(popup);
          panel._gpuProbe.popup = popup;

          // Position below the button using viewport coords (popup is
          // position:fixed). Measure the popup AFTER it's in the DOM so
          // we get the real rendered size, then clamp both axes so the
          // popup stays fully visible — GPU buttons near the right edge
          // of the modal previously anchored the popup mostly off-screen.
          const r = anchorBtn.getBoundingClientRect();
          const vw = window.innerWidth  || document.documentElement.clientWidth;
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const pw = popup.offsetWidth  || 320;
          const ph = popup.offsetHeight || 200;
          let left = r.left;
          let top  = r.bottom + 4;
          // Push left so the popup doesn't overflow the right edge.
          if (left + pw > vw - 8) left = Math.max(8, vw - pw - 8);
          // If there isn't room below, render above the button instead.
          if (top + ph > vh - 8) top = Math.max(8, r.top - ph - 4);
          popup.style.left = `${left}px`;
          popup.style.top  = `${top}px`;

          popup.querySelector('.cookbook-gpu-popup-close')?.addEventListener('click', _closeProbePopup);
          popup.querySelectorAll('.cookbook-gpu-kill').forEach(btn => {
            btn.addEventListener('click', async (ev) => {
              ev.stopPropagation();
              const row = btn.closest('.cookbook-gpu-proc');
              const pid = parseInt(row.dataset.pid);
              const sig = btn.dataset.sig;
              if (sig === 'KILL' && !await window.styledConfirm(`SIGKILL PID ${pid}? This force-terminates without cleanup.`, { confirmText: 'SIGKILL', danger: true })) return;
              btn.disabled = true;
              btn.textContent = '…';
              const ok = await _doKill(pid, sig, hostVal);
              if (ok) {
                row.style.opacity = '0.4';
                row.style.textDecoration = 'line-through';
                // Re-probe after a short delay so freed VRAM updates
                setTimeout(() => _probeBtn.click(), 1200);
              } else {
                btn.disabled = false;
                btn.textContent = sig === 'KILL' ? '!' : 'Kill';
              }
            });
          });

          // Click outside closes the popup
          setTimeout(() => {
            const outside = (ev) => {
              if (!popup.contains(ev.target) && ev.target !== anchorBtn) {
                _closeProbePopup();
                document.removeEventListener('mousedown', outside, true);
              }
            };
            document.addEventListener('mousedown', outside, true);
          }, 0);
        };

        const _runProbe = async (silent = false) => {
          _closeProbePopup();
          const hostEl = panel.querySelector('[data-field="host"]');
          const remoteHost = (hostEl && hostEl.value || '').trim();
          const params = new URLSearchParams();
          if (remoteHost) params.set('host', remoteHost);
          const url = '/api/cookbook/gpus' + (params.toString() ? '?' + params.toString() : '');
          const res = await fetch(url, { credentials: 'same-origin' });
          let data;
          try { data = await res.json(); } catch (_) { data = {}; }
          if (!res.ok) {
            const err = data.detail || data.error || res.statusText || `HTTP ${res.status}`;
            const hint = res.status === 404 ? ' — server may need a restart to pick up new endpoint' : '';
            if (!silent) uiModule.showToast('GPU probe failed: ' + err + hint, 8000);
            return null;
          }
          if (!data.ok) {
            if (!silent) uiModule.showToast('GPU probe failed: ' + (data.error || 'unknown'), 6000);
            return null;
          }
          panel._gpuProbe.byIdx = new Map(data.gpus.map(g => [g.index, g]));
          panel._gpuProbe.host = remoteHost;
          // If the probe found more GPUs than the panel originally
          // rendered (e.g. host switched from a 1-iGPU local box to an
          // 8-GPU remote), append buttons for the missing indexes so the
          // user can actually toggle them. Reuse the parent <div> from
          // the first existing button as the insertion target.
          try {
            const _existing = Array.from(panel.querySelectorAll('.cookbook-gpu-btn'));
            const _grp = _existing[0] && _existing[0].parentElement;
            if (_grp) {
              const _have = new Set(_existing.map(b => parseInt(b.dataset.gpu, 10)));
              const _activeStr = (panel.querySelector('[data-field="gpus"]')?.value || '').split(',').map(s => s.trim());
              data.gpus.forEach(g => {
                if (_have.has(g.index)) return;
                const _b = document.createElement('button');
                _b.type = 'button';
                _b.className = 'cookbook-gpu-btn' + (_activeStr.includes(String(g.index)) ? ' active' : '');
                _b.dataset.gpu = String(g.index);
                _b.textContent = String(g.index);
                _grp.appendChild(_b);
                // Re-wire the click handler the same way the panel did
                // on first render. Toggles active + rewrites the hidden
                // gpus input from the live set of active buttons.
                _b.addEventListener('click', () => {
                  _b.classList.toggle('active');
                  const activeBtns = [...panel.querySelectorAll('.cookbook-gpu-btn.active')];
                  const ids = activeBtns.map(x => x.dataset.gpu).sort((a, b) => +a - +b).join(',');
                  const hidden = panel.querySelector('[data-field="gpus"]');
                  if (hidden) { hidden.value = ids; hidden.dispatchEvent(new Event('change', { bubbles: true })); }
                });
              });
            }
          } catch (_) {}
          panel.querySelectorAll('.cookbook-gpu-btn').forEach(b => {
            const idx = parseInt(b.dataset.gpu);
            const g = panel._gpuProbe.byIdx.get(idx);
            b.classList.remove('gpu-free', 'gpu-busy', 'gpu-missing');
            if (!g) {
              // GPU doesn't exist on this server — hide it rather than show a
              // dead button. The panel renders up to 8 before the count is known
              // (e.g. a single-GPU box would otherwise show 0–7).
              b.style.display = 'none';
              b.classList.remove('active');
              return;
            }
            b.style.display = '';
            const freeGb = (g.free_mb / 1024).toFixed(1);
            const totalGb = (g.total_mb / 1024).toFixed(1);
            const procCount = (g.processes && g.processes.length) || 0;
            const procLine = procCount
              ? `\n${procCount} process(es) — click to view/kill`
              : '';
            const backendLine = g.backend || data.backend ? `\nprobe: ${g.source || data.source || g.backend || data.backend}` : '';
            b.title = `GPU ${idx} ${g.name}\n${freeGb} / ${totalGb} GB free · util ${g.util_pct}%${procLine}${backendLine}`;
            // Treat any GPU with attached compute processes OR <85% free as busy.
            const isBusy = procCount > 0 || g.busy;
            b.classList.add(isBusy ? 'gpu-busy' : 'gpu-free');
          });
          if (!silent) {
            if (data.gpus.length === 0) {
              uiModule.showToast('No GPU memory probe data available', 4000);
            } else {
              const summary = data.gpus.map(g => {
                const procs = (g.processes && g.processes.length) || 0;
                return `GPU${g.index}: ${(g.free_mb/1024).toFixed(1)}G free` + (procs ? ` (${procs}p)` : '');
              }).join(' · ');
              uiModule.showToast(summary + ' · dbl-click a GPU button to view/kill processes', 7000);
            }
          }
          return data;
        };

        _probeBtn.addEventListener('click', async () => {
          try { await _withSpinner(_probeBtn, () => _runProbe(false)); }
          catch (e) { uiModule.showToast('GPU probe error: ' + e.message, 6000); }
        });

        // Auto-probe (silent) on open so the GPU buttons reflect the real count
        // — a single-GPU server should show just GPU 0, not the placeholder 0–7.
        // Falls back to the full 0–7 set if the server is unreachable.
        _runProbe(true).catch(() => {});

        if (_clearBtn) {
          _clearBtn.addEventListener('click', async () => {
            try {
              await _withSpinner(_clearBtn, async () => {
                // Always probe first so we have fresh PID list
                const data = await _runProbe();
                if (!data) return;
                const pids = [];
                for (const g of data.gpus) {
                  for (const p of (g.processes || [])) pids.push({ pid: p.pid, name: p.name });
                }
                if (pids.length === 0) {
                  uiModule.showToast('No GPU processes to clear', 3000);
                  return;
                }
                const summary = pids.map(p => `${p.pid} (${p.name})`).join(', ');
                if (!await window.styledConfirm(`Clear server GPU memory by sending SIGTERM to ${pids.length} process(es)?\n\n${summary}\n\nIf any survive, the next prompt can force-kill them with SIGKILL.`, { confirmText: 'SIGTERM', danger: true })) return;
                // First pass: SIGTERM
                const hostVal = panel._gpuProbe.host;
                const results = await Promise.all(pids.map(p =>
                  fetch('/api/cookbook/kill-pid', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pid: p.pid, signal: 'TERM', host: hostVal || null }),
                  }).then(r => r.json()).catch(e => ({ ok: false, error: e.message }))
                ));
                const okCount = results.filter(r => r.ok).length;
                uiModule.showToast(`SIGTERM → ${okCount}/${pids.length} processes`, 5000);
                // Wait, then re-probe; if survivors, offer SIGKILL
                await new Promise(r => setTimeout(r, 1500));
                const after = await _runProbe();
                if (!after) return;
                const survivors = [];
                for (const g of after.gpus) {
                  for (const p of (g.processes || [])) {
                    if (pids.some(orig => orig.pid === p.pid)) survivors.push(p);
                  }
                }
                if (survivors.length === 0) {
                  uiModule.showToast(`Cleared ${pids.length} GPU process(es)`, 4000);
                  return;
                }
                if (!await window.styledConfirm(`${survivors.length} process(es) survived SIGTERM:\n\n${survivors.map(p => p.pid + ' (' + p.name + ')').join(', ')}\n\nForce-kill with SIGKILL?`, { confirmText: 'SIGKILL', danger: true })) return;
                const killResults = await Promise.all(survivors.map(p =>
                  fetch('/api/cookbook/kill-pid', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pid: p.pid, signal: 'KILL', host: hostVal || null }),
                  }).then(r => r.json()).catch(e => ({ ok: false, error: e.message }))
                ));
                const killOk = killResults.filter(r => r.ok).length;
                uiModule.showToast(`SIGKILL → ${killOk}/${survivors.length} processes`, 5000);
                await new Promise(r => setTimeout(r, 800));
                await _runProbe();
              });
            } catch (e) {
              uiModule.showToast('Clear Server error: ' + e.message, 6000);
            }
          });
        }

        // After probe, clicking a GPU button opens kill popup (Shift-click also toggles select)
        panel.querySelectorAll('.cookbook-gpu-btn').forEach(btn => {
          btn.addEventListener('contextmenu', (ev) => {
            if (!panel._gpuProbe.byIdx) return;
            const g = panel._gpuProbe.byIdx.get(parseInt(btn.dataset.gpu));
            if (!g) return;
            ev.preventDefault();
            _openProbePopup(btn, g, panel._gpuProbe.host);
          });
          btn.addEventListener('dblclick', (ev) => {
            if (!panel._gpuProbe.byIdx) return;
            const g = panel._gpuProbe.byIdx.get(parseInt(btn.dataset.gpu));
            if (!g) return;
            ev.preventDefault();
            _openProbePopup(btn, g, panel._gpuProbe.host);
          });
        });
      }

      // Update preview on input change
      panel.querySelectorAll('.hwfit-sf').forEach(el => {
        el.addEventListener('input', updateCmd);
        el.addEventListener('change', (e) => {
          if (e.target.dataset.field === 'backend') {
            const extraEl = panel.querySelector('[data-field="extra"]');
            if (extraEl) extraEl.value = '';
            updateBackendVisibility();
            updateRuntimeReadinessNote();
          }
          if (e.target.dataset.field === 'venv') {
            updateRuntimeReadinessNote();
          }
          updateCmd();
        });
      });
      // Themed +/- buttons next to spec_tokens — step the adjacent number input.
      panel.querySelectorAll('.hwfit-numstep-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const input = btn.parentElement?.querySelector('input[type="number"]');
          if (!input) return;
          const step = parseInt(btn.dataset.step, 10) || 0;
          const min = input.min !== '' ? Number(input.min) : -Infinity;
          const max = input.max !== '' ? Number(input.max) : Infinity;
          const next = Math.min(max, Math.max(min, (Number(input.value) || 0) + step));
          input.value = String(next);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
      });

      // Track manual edits
      let _cmdManuallyEdited = false;
      const _cmdTextarea = panel.querySelector('.hwfit-serve-cmd');
      if (_cmdTextarea) _cmdTextarea.addEventListener('input', () => { _cmdManuallyEdited = true; });

      // Cancel button — collapses the serve config panel (same effect as
      // tapping the row to toggle it shut). Mobile users wanted an explicit
      // "back out" affordance next to Launch.
      panel.querySelector('.hwfit-serve-cancel')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        panel._cleanupRuntimeReadiness?.();
        panel.remove();
        item.classList.remove('doclib-card-expanded');
        item.style.flexDirection = '';
        item.style.alignItems = '';
        if (list) { list.style.minHeight = ''; list.style.maxHeight = ''; }
      });

      // Launch button
      panel.querySelector('.hwfit-serve-launch').addEventListener('click', async (ev) => {
        const _launchBtn = ev.currentTarget;
        // Immediate visual feedback. The GPU probe + backend-warning prompt
        // below can take ~1-2s before the task UI shows up, leaving the
        // button looking dead. Drop in the same whirlpool spinner the rest of
        // the cookbook uses (Probe GPUs, dependency installs, etc.) right
        // away; restored on any early-return / failure path below.
        const _origBtnHtml = _launchBtn.innerHTML;
        const _origBtnDisabled = _launchBtn.disabled;
        let _launchingWp = null;
        const _restoreLaunchBtn = () => {
          try { _launchingWp?.destroy?.(); } catch {}
          _launchingWp = null;
          _launchBtn.innerHTML = _origBtnHtml;
          _launchBtn.disabled = _origBtnDisabled;
        };
        _launchBtn.disabled = true;
        _launchBtn.innerHTML = '';
        const _launchingWrap = document.createElement('span');
        _launchingWrap.className = 'hwfit-serve-launching';
        _launchingWrap.style.cssText = 'display:inline-flex;align-items:center;gap:6px;';
        _launchingWp = spinnerModule.createWhirlpool(18);
        if (_launchingWp?.element) {
          _launchingWp.element.style.margin = '0';
          _launchingWp.element.style.transform = 'translateY(-2px)';
          _launchingWrap.appendChild(_launchingWp.element);
        }
        const _launchingLabel = document.createElement('span');
        _launchingLabel.textContent = 'Launching…';
        _launchingWrap.appendChild(_launchingLabel);
        _launchBtn.appendChild(_launchingWrap);
        // Final safety net: never launch with ctx beyond the model's trained
        // limit (or the absolute sanity ceiling when the limit is unknown). A
        // stale preset or typo (e.g. 16000000) overflows and, with a quantized
        // KV cache, can crash the GPU. Skip only if the user hand-edited the raw
        // command (then we respect their literal text).
        if (!_cmdManuallyEdited) _clampCtx(true);
        if (!_cmdManuallyEdited) updateCmd();
        // Pasted commands often carry hidden newlines / CRs / tabs from copies
        // out of model cards or wrapped help text. The backend cmd allowlist
        // rejects \n / \r outright (`Invalid characters in cmd`), so collapse
        // all whitespace to single spaces before launch — same effect as the
        // user manually re-flowing the textarea, no behavior change.
        const _rawLaunchCmd = _cmdTextarea ? _cmdTextarea.value : panel._cmd;
        const launchCmd = String(_rawLaunchCmd || '').replace(/\s+/g, ' ').trim();
        if (_cmdTextarea && _cmdTextarea.value !== launchCmd) _cmdTextarea.value = launchCmd;
        const serveState = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') serveState[el.dataset.field] = el.checked;
          else serveState[el.dataset.field] = el.value;
        });
        serveState.backend = serveState.backend || (_detectBackend(m).backend) || 'vllm';
        const backendWarning = _serveBackendWarning(m, repo, serveState.backend, serveState);
        if (backendWarning) {
          _restoreLaunchBtn();
          await window.styledConfirm(backendWarning.body, {
            title: backendWarning.title,
            confirmText: 'Edit settings',
            cancelText: 'Close',
          });
          return;
        }
        // Pre-launch GPU probe — common failure pattern: vLLM/SGLang launched
        // on a host where no GPU is visible (driver missing, $CUDA_VISIBLE_DEVICES
        // unset, container without --gpus). Catch it BEFORE the user spends
        // minutes watching the task fail.
        const _needsGpu = ['vllm', 'sglang'].includes(serveState.backend)
          || (serveState.backend === 'diffusers');
        if (_needsGpu) {
          try {
            const _probeHost = (_envState.remoteHost || '').trim();
            const _probeParams = new URLSearchParams();
            if (_probeHost) {
              _probeParams.set('host', _probeHost);
              const _sp = (_serverByVal?.(_envState.remoteServerKey || _probeHost) || {}).port;
              if (_sp) _probeParams.set('ssh_port', _sp);
            }
            const _probeRes = await fetch('/api/cookbook/gpus' + (_probeParams.toString() ? '?' + _probeParams : ''), { credentials: 'same-origin' });
            const _probeData = await _probeRes.json();
            const _probeGpus = Array.isArray(_probeData) ? _probeData : (_probeData.gpus || []);
            if (!_probeGpus.length) {
              const _proceed = await window.styledConfirm(
                `No GPU detected on ${_probeHost ? _probeHost : 'this host'}. ${serveState.backend.toUpperCase()} needs a visible CUDA/ROCm accelerator to start — launching now will most likely crash early.\n\nLaunch anyway?`,
                { title: 'No GPU detected', confirmText: 'Launch anyway', cancelText: 'Cancel', danger: true },
              );
              if (!_proceed) { _restoreLaunchBtn(); return; }
            }
          } catch {
            // Network / probe failure — don't block. Better to let the launch
            // proceed than to silently refuse because the probe endpoint
            // hiccuped (the user can read the real error in the task output).
          }
        }

        // Pre-launch PORT probe — second most common failure pattern is
        // collision with an already-running server (vllm crashing with
        // "Address already in use" because Ollama owns 11434, or a
        // previous vllm on the same port wasn't killed). The post-mortem
        // "Suggested action: Kill existing vLLM" came AFTER the failed
        // launch — user wants to know BEFORE clicking Launch. Parse the
        // port out of the cmd, ssh-check who owns it on the target host,
        // and offer to abort or proceed.
        try {
          const _portMatch = launchCmd.match(/(?:^|\s)(?:--port|-p|--host\s+\S+\s+--port)\s+(\d{2,5})\b/)
            || launchCmd.match(/(?:^|\s)--port=(\d{2,5})\b/)
            || launchCmd.match(/OLLAMA_HOST=[^:\s]+:(\d{2,5})\b/);
          const _port = _portMatch ? _portMatch[1] : '';
          if (_port) {
            const _portHost = (_envState.remoteHost || '').trim();
            const _checkInner = `ss -tlnp 2>/dev/null | awk '$4 ~ /:${_port}$/ {print; exit}' || netstat -tlnp 2>/dev/null | awk '$4 ~ /:${_port}$/ {print; exit}'`;
            const _cmd = _portHost
              ? `ss h ${_portHost} <<<"" 2>/dev/null; ssh -o ConnectTimeout=4 -o StrictHostKeyChecking=no ${_portHost} ${JSON.stringify(_checkInner)}`
              : _checkInner;
            const _res = await fetch('/api/shell/exec', {
              method: 'POST', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command: _cmd }),
            });
            const _data = await _res.json().catch(() => ({}));
            const _stdout = (_data.stdout || '').trim();
            if (_stdout) {
              // Try to surface the process name from `users:(("name",pid=...,...))`.
              const _procMatch = _stdout.match(/users:\(\("([^"]+)",pid=(\d+)/);
              const _procDesc = _procMatch
                ? `${_procMatch[1]} (PID ${_procMatch[2]})`
                : 'another process';
              const _hostLabel = _portHost ? _portHost : 'this host';
              const _proceed = await window.styledConfirm(
                `Port ${_port} on ${_hostLabel} is already in use by ${_procDesc}. Launching ${serveState.backend.toUpperCase()} now will fail with "Address already in use".\n\nStop the existing process first, OR change the --port in the command above, OR launch anyway and watch it crash.`,
                {
                  title: `Port ${_port} taken`,
                  confirmText: 'Launch anyway',
                  cancelText: 'Cancel',
                  danger: true,
                },
              );
              if (!_proceed) { _restoreLaunchBtn(); return; }
            }
          }
        } catch {
          // Probe failure — don't block. If the port check can't run we'd
          // rather let the launch try than silently refuse.
        }
        // Save in the { _byRepo, _lastUsed } schema — no legacy flat keys at
        // the root so per-model state doesn't leak between models.
        // Stamp `_forceBackend: true` so the next open of this model defaults
        // to the launched configuration end-to-end, even when the detector
        // would have picked a different backend. Without this flag, the
        // `savedMatchesBackend` gate inside sv() throws away every saved
        // value when the detected backend doesn't match — the user opens
        // Serve again and the panel looks like a fresh form despite a
        // known-good prior launch.
        try {
          let cur = {};
          try { cur = JSON.parse(localStorage.getItem(SERVE_STATE_KEY)) || {}; } catch {}
          const byRepo = (cur && cur._byRepo && typeof cur._byRepo === 'object') ? cur._byRepo : {};
          const _saved = { ...serveState, _forceBackend: true };
          byRepo[repo] = _saved;
          localStorage.setItem(SERVE_STATE_KEY, JSON.stringify({ _byRepo: byRepo, _lastUsed: _saved }));
        } catch {}
        const origEnv = _envState.env;
        const origEnvPath = _envState.envPath;
        const venvVal = panel.querySelector('[data-field="venv"]')?.value?.trim();
        const gpusVal = panel.querySelector('[data-field="gpus"]')?.value?.trim();
        const origGpus = _envState.gpus;
        // Resolve the target host from the visible Server dropdown — the reliable
        // source. Relying on _envState.remoteHost silently sent serves to Local
        // when that value was stale/empty. Pass it explicitly to the launcher.
        let serveHost = _envState.remoteHost || '';
        let _srvEnv = '', _srvEnvPath = '';
        const _ssEl = document.getElementById('hwfit-server-select') || document.getElementById('hwfit-dl-server');
        if (_ssEl && _ssEl.value != null) {
          if (_ssEl.value === 'local') serveHost = '';
          else {
            const _srv = _serverByVal?.(_ssEl.value) || _envState.servers[parseInt(_ssEl.value)];
            if (_srv) {
              serveHost = _srv.host;
              _srvEnv = _srv.env || '';
              _srvEnvPath = _srv.envPath || '';
            }
          }
        }
        // The venv field wins; otherwise fall back to the env configured for the
        // selected server in Settings, so the activation isn't silently dropped
        // when the field is left blank (the per-server venv wasn't being applied).
        if (venvVal) { _envState.env = 'venv'; _envState.envPath = venvVal; }
        else if (_srvEnvPath) { _envState.env = (_srvEnv === 'conda' ? 'conda' : 'venv'); _envState.envPath = _srvEnvPath; }
        if (gpusVal) _envState.gpus = gpusVal;
        try {
          await _withSpinner(_launchBtn, async () => {
            // Pass the exact form values so the running task can be re-opened
            // in the Serve panel pre-filled with these settings (Edit button).
            await _launchServeTask(shortName, repo, launchCmd, serveState, serveHost);
          });
        } finally {
          _envState.env = origEnv;
          _envState.envPath = origEnvPath;
          _envState.gpus = origGpus;
        }
      });

      // Copy button — now icon-only, so flash a green checkmark on success
      // instead of swapping to text (which would also break the width).
      panel.querySelector('.hwfit-serve-copy').addEventListener('click', (e) => {
        // Without stopPropagation the click bubbles up to the
        // .doclib-card click handler that toggles the expand state →
        // copying collapses the whole serve panel mid-flight.
        e.preventDefault();
        e.stopPropagation();
        const cmd = panel.querySelector('.hwfit-serve-cmd').value;
        _copyText(cmd).then(() => {
          const btn = panel.querySelector('.hwfit-serve-copy');
          const origHtml = btn.innerHTML;
          btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
          btn.classList.add('copied');
          setTimeout(() => { btn.innerHTML = origHtml; btn.classList.remove('copied'); }, 1500);
        });
      });
    });
  });
}

// ── Delete / retry cached model ──

// Resolve the host the cached list was scanned from, mirroring
// _fetchCachedModels — so a delete targets the SAME machine the model
// actually lives on, not just the globally-selected serve host.
function _resolveCacheHost() {
  let host = _envState.remoteHost || '';
  const cacheSrv = document.getElementById('hwfit-cache-server');

  function _serverByCacheValue(val) {
    if (val === 'local') return null;
    const found = _serverByVal?.(val)
      || (/^\d+$/.test(String(val)) ? _envState.servers[parseInt(val)] : null)
      || _envState.servers.find(x => x.name === val)
      || null;
    return found || null;
  }

  if (cacheSrv) {
    const val = cacheSrv.value;
    if (val === 'local') {
      host = '';
    } else {
      const s = _serverByCacheValue(val);
      if (s) host = s.host;
    }
  }
  return host;
}

async function _deleteCachedModel(repo, itemEl, skipConfirm = false, model = null) {
  if (!skipConfirm && !(await uiModule.styledConfirm(`Delete ${repo} from cache?`, { confirmText: 'Delete', danger: true }))) return;
  const m = model || _cachedAllModels.find(x => x.repo_id === repo);
  // Delete the EXACT on-disk path the scan reported. Models in a custom
  // model dir live at <path>/<repo>; HF-cache models at
  // <path>/models--<org>--<name>. The old code always rm'd the hardcoded
  // ~/.cache/huggingface/hub path, so models in a custom dir were never
  // removed and reappeared on the next scan. m.path is already absolute
  // (os.path.expanduser ran on the host); only the bare fallback uses ~.
  let target;
  if (m && m.is_local_dir && m.path) {
    target = `${m.path}/${repo}`;
  } else if (m && m.path) {
    target = `${m.path}/models--${repo.replace(/\//g, '--')}`;
  } else {
    target = `~/.cache/huggingface/hub/models--${repo.replace(/\//g, '--')}`;
  }
  const host = _resolveCacheHost();
  let cmd;
  if (_isWindows()) {
    const winTarget = target.startsWith('~')
      ? target.replace(/^~/, '$env:USERPROFILE').replace(/\//g, '\\')
      : target.replace(/\//g, '\\');
    cmd = `Remove-Item -Recurse -Force "${winTarget}" -ErrorAction SilentlyContinue`;
    if (host) {
      const pf = _sshPrefix(_getPort(host));
      cmd = `ssh ${pf}${host} "powershell -Command \\"${cmd}\\""`;
    }
  } else {
    // $HOME expands inside double quotes; ~ would not, so normalize the
    // fallback. Quoting also handles spaces in custom model-dir paths.
    const unixTarget = target.startsWith('~') ? target.replace(/^~/, '$HOME') : target;
    cmd = `rm -rf "${unixTarget}"`;
    if (host) cmd = _sshCmd(host, cmd, _getPort(host));
  }
  // Deleting a large model (tens/hundreds of GB) can take a while, especially
  // over SSH — show a whirlpool spinner on the row so it doesn't look frozen.
  let _wp = null, _prevPos = '';
  if (itemEl) {
    _wp = spinnerModule.createWhirlpool(18);
    const ov = document.createElement('div');
    ov.className = 'cookbook-delete-overlay';
    // Just the whirlpool, centered — no "Deleting…" text.
    ov.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb, var(--panel, var(--bg)) 82%, transparent);z-index:5;border-radius:inherit;';
    ov.appendChild(_wp.element);
    _prevPos = itemEl.style.position;
    if (getComputedStyle(itemEl).position === 'static') itemEl.style.position = 'relative';
    itemEl.style.pointerEvents = 'none';
    itemEl.appendChild(ov);
  }
  try {
    const res = await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }),
    });
    if (!res.ok) { uiModule.showError(`Delete failed (${res.status})`); return; }
    if (itemEl) {
      itemEl.querySelector('.cookbook-delete-overlay')?.remove();
      itemEl.style.transition = 'opacity 0.24s ease, transform 0.24s ease, max-height 0.28s ease, padding 0.28s ease, margin 0.28s ease';
      itemEl.style.maxHeight = `${Math.max(itemEl.getBoundingClientRect().height, itemEl.scrollHeight)}px`;
      itemEl.style.overflow = 'hidden';
      itemEl.style.opacity = '0';
      itemEl.style.transform = 'translateX(-10px) scale(0.985)';
      itemEl.style.paddingTop = '0';
      itemEl.style.paddingBottom = '0';
      itemEl.style.marginTop = '0';
      itemEl.style.marginBottom = '0';
      requestAnimationFrame(() => { itemEl.style.maxHeight = '0'; });
      await new Promise(resolve => setTimeout(resolve, 300));
      if (itemEl.parentElement) itemEl.remove();
    }
    // Drop from the in-memory list so a re-render/filter doesn't resurrect it.
    _cachedAllModels = _cachedAllModels.filter(x => x.repo_id !== repo);
  } catch (e) {
    uiModule.showError('Delete failed: ' + (e && e.message ? e.message : e));
  } finally {
    // Tear down the spinner. On success the row is already gone; on error the
    // row survives, so restore it (remove overlay, re-enable interaction).
    if (_wp) { try { _wp.destroy(); } catch {} }
    if (itemEl && itemEl.isConnected) {
      itemEl.querySelector('.cookbook-delete-overlay')?.remove();
      itemEl.style.pointerEvents = '';
      itemEl.style.position = _prevPos;
    }
  }
}

function _retryCachedModel(repo, m) {
  const payload = { repo_id: repo };
  if (_envState.hfToken) payload.hf_token = _envState.hfToken;
  if (_envState.remoteHost) { payload.remote_host = _envState.remoteHost; const _sp2 = _getPort(_envState.remoteHost); if (_sp2) payload.ssh_port = _sp2; }
  if (_envState.platform) payload.platform = _envState.platform;
  if (_isWindows()) {
    if (_envState.env === 'venv' && _envState.envPath) {
      payload.env_prefix = '& ' + _psQuote(_envState.envPath.endsWith('\\Scripts\\Activate.ps1') ? _envState.envPath : _envState.envPath + '\\Scripts\\Activate.ps1');
    } else if (_envState.env === 'conda' && _envState.envPath) {
      payload.env_prefix = 'conda activate ' + _psQuote(_envState.envPath);
    }
  } else {
    if (_envState.env === 'venv' && _envState.envPath) {
      const p = _envState.envPath;
      payload.env_prefix = 'source ' + _shellQuote(p.endsWith('/bin/activate') ? p : p + '/bin/activate');
    } else if (_envState.env === 'conda' && _envState.envPath) {
      payload.env_prefix = 'eval "$(conda shell.bash hook)" && conda activate ' + _shellQuote(_envState.envPath);
    }
  }
  _retryDownload((m?.name || repo).split('/').pop(), payload);
}

// ── Open the Serve panel for a specific repo, pre-filled ──
//
// Used by the running-task "Edit / relaunch" button. Writes the supplied
// field values into the per-repo serve state so the panel's existing
// restore logic fills the form exactly, switches to the Serve tab, then
// finds the model's cached card and expands it.
export async function openServePanelForRepo(repo, fields) {
  if (!repo) return false;
  // Seed the per-repo serve state with the exact launch fields so the
  // panel restores them when it builds.
  if (fields && typeof fields === 'object') {
    try {
      let cur = {};
      try { cur = JSON.parse(localStorage.getItem(SERVE_STATE_KEY)) || {}; } catch {}
      const byRepo = (cur && cur._byRepo && typeof cur._byRepo === 'object') ? cur._byRepo : {};
      // Mirror the launch-time save: stamp _forceBackend so the panel's
      // sv() helper treats these seeded fields as authoritative, not as
      // overridable defaults.
      const _seeded = { ...fields, _forceBackend: true };
      byRepo[repo] = _seeded;
      localStorage.setItem(SERVE_STATE_KEY, JSON.stringify({ _byRepo: byRepo, _lastUsed: _seeded }));
    } catch {}
  }
  // Switch to the Serve tab (its click handler triggers _fetchCachedModels).
  const serveTab = document.querySelector('.cookbook-tab[data-backend="Serve"]');
  if (serveTab && !serveTab.classList.contains('active')) {
    serveTab.click();
  } else {
    // Already on the Serve tab — refresh the list so the card is present.
    try { await _fetchCachedModels(); } catch {}
  }
  // Poll for the model's card to render, then expand it. Cached-model
  // fetch is async and we don't get a direct completion hook from the
  // tab click, so retry for a few seconds.
  // A model downloaded to a CUSTOM dir is scanned by its folder name (the short
  // name), while the download task carries the full HF repo id — so match by the
  // exact repo OR by the short (last-segment) name, else the card is never found.
  const _short = repo.split('/').pop();
  const _esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v;
  for (let i = 0; i < 50; i++) {
    let card = document.querySelector(`.memory-item[data-repo="${_esc(repo)}"]`);
    if (!card && _short && _short !== repo) {
      card = document.querySelector(`.memory-item[data-repo="${_esc(_short)}"]`)
        || [...document.querySelectorAll('.memory-item[data-repo]')]
             .find(el => (el.dataset.repo || '').split('/').pop() === _short);
    }
    if (card) {
      // If we were given fields to restore, force a fresh render of the
      // serve panel so it reads the just-written _byRepo[repo] values
      // from localStorage. Without this, an already-expanded card kept
      // its stale form and the "Edit serve" → previous settings round-
      // trip looked broken from the user's side.
      if (fields && card.classList.contains('doclib-card-expanded')) {
        card.click();
        await new Promise(r => setTimeout(r, 40));
        card.click();
      } else if (!card.classList.contains('doclib-card-expanded')) {
        card.click();
      }
      try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch {}
      return true;
    }
    await new Promise(r => setTimeout(r, 100));
  }
  uiModule.showToast('Model not found in cache — switch to the Serve tab manually');
  return false;
}

// ── Fetch cached models from server ──

export async function _fetchCachedModels() {
  const list = document.getElementById('hwfit-cached-list');
  if (!list) return;

  list.innerHTML = '';
  const _dlWp = spinnerModule.createWhirlpool(18);
  const _dlWrap = document.createElement('div');
  _dlWrap.className = 'hwfit-loading';
  _dlWrap.style.cssText = 'flex-direction:column;gap:6px;';
  _dlWrap.appendChild(_dlWp.element);
  const _dlLabel = document.createElement('div');
  _dlLabel.textContent = 'Scanning cached models…';
  _dlLabel.style.cssText = 'opacity:0.5;font-size:11px;';
  _dlWrap.appendChild(_dlLabel);
  list.appendChild(_dlWrap);

  try {
    let host = _envState.remoteHost || '';
    let selectedServer = null;
    const _serverByCacheValue = (val) => {
      if (val === 'local') return null;
      return _serverByVal?.(val)
        || (/^\d+$/.test(String(val)) ? _envState.servers[parseInt(val)] : null)
        || _envState.servers.find(x => x.name === val)
        || null;
    };

    const cacheSrv = document.getElementById('hwfit-cache-server');
    if (cacheSrv) {
      const val = cacheSrv.value;
      if (val === 'local') {
        host = '';
        selectedServer = _envState.servers.find(s => !s.host || s.host === 'local') || _envState.servers[0];
      } else {
        const s = _serverByCacheValue(val);
        if (s) { host = s.host; selectedServer = s; }
      }
    } else {
      selectedServer = _envState.servers.find(s => s.host === host) || _envState.servers[0];
    }
    // Read extra model dirs from the SELECTED server's modelDirs (canonical source)
    const modelDirs = [];
    if (selectedServer && Array.isArray(selectedServer.modelDirs)) {
      for (const d of selectedServer.modelDirs) {
        if (d && d !== '~/.cache/huggingface/hub') modelDirs.push(d);
      }
    }
    // Sync the header dir pills to THIS server (the one whose models we're listing).
    // They were rendered once from _es.remoteHost, which can differ from the
    // cache-server dropdown — so the title showed only ~/.cache even while listing
    // models from a custom model directory. Keep them in lock-step with the actual scan host.
    const _dirsEl = document.querySelector('.cookbook-serve-dirs');
    if (_dirsEl && selectedServer) {
      const _allDirs = (Array.isArray(selectedServer.modelDirs) && selectedServer.modelDirs.length
        ? selectedServer.modelDirs
        : [selectedServer.modelDir || '~/.cache/huggingface/hub'])
        .map(d => (d || '').replaceAll('✕', '').replaceAll('✖', '').trim()).filter(Boolean);
      _dirsEl.innerHTML = _allDirs.map(d => `<span class="cookbook-serve-dir-pill">${esc(d)}</span>`).join('')
        + '<span class="cookbook-serve-dir-edit" title="Edit in Settings">edit</span>';
      _dirsEl.querySelector('.cookbook-serve-dir-edit')?.addEventListener('click', () => {
        document.querySelector('#cookbook-modal .cookbook-tab[data-backend="Settings"]')?.click();
      });
    }
    const qp = new URLSearchParams();
    if (host) { qp.set('host', host); const _sp4 = _getPort(host); if (_sp4) qp.set('ssh_port', _sp4); const _plat = _getPlatform(host); if (_plat) qp.set('platform', _plat); }
    if (modelDirs.length) qp.set('model_dir', modelDirs.join(','));
    const params = qp.toString() ? `?${qp}` : '';
    const res = await fetch(`/api/model/cached${params}`);
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
    const data = await res.json();
    _dlWp.destroy();

    // CHANGELOG: 'ready' already excludes partial downloads; 
    // show every complete model regardless of size/backend.
    const ready = data.models.filter(m => m.status === 'ready');

    const downloading = data.models.filter(m => m.status === 'downloading');
    const allModels = [...ready, ...downloading];
    _cachedAllModels = allModels;

    if (!allModels.length) {
      if (!host) {
        list.innerHTML = '<div class="hwfit-loading" style="flex-direction:column;gap:6px;text-align:center;"><div>No cached models found</div><div style="font-size:11px;opacity:0.55;max-width:420px;line-height:1.4;">Docker Local uses Odysseus’s cache in <code>data/huggingface</code>. Download a model here, or copy an existing host HuggingFace cache into that folder once.</div></div>';
      } else {
        list.innerHTML = '<div class="hwfit-loading">No cached models found</div>';
      }
      document.getElementById('serve-tags').innerHTML = '';
      return;
    }

    // Auto-detect type + family tags
    const _tagMap = {};
    const _familyMap = {};
    const _families = [
      [/qwen/i, 'qwen'], [/llama/i, 'llama'], [/mistral|mixtral/i, 'mistral'],
      [/deepseek/i, 'deepseek'], [/gemma/i, 'gemma'], [/phi/i, 'phi'],
      [/minimax/i, 'minimax'], [/glm/i, 'glm'], [/flux/i, 'flux'],
      [/stable.?diffusion|sdxl/i, 'sd'], [/z-image/i, 'z-image'],
      [/whisper/i, 'whisper'], [/command|cohere/i, 'cohere'],
      [/yi-/i, 'yi'], [/intern/i, 'intern'], [/falcon/i, 'falcon'],
    ];
    for (const m of allModels) {
      const n = (m.repo_id || '').toLowerCase();
      let tag = 'other';
      if (m.backend === 'ollama' || m.is_ollama) tag = 'llm';
      else if (m.is_diffusion || /flux|sdxl|stable-diffusion|z-image|qwen-image|diffusion|dreamshar/i.test(n)) tag = 'image';
      else if (/whisper|stt|asr/i.test(n)) tag = 'stt';
      else if (/tts|cosyvoice|parler/i.test(n)) tag = 'tts';
      else if (/embed|bge|minilm|e5-/i.test(n)) tag = 'embedding';
      else if (/lora|adapter/i.test(n)) tag = 'lora';
      else tag = 'llm';
      m._tag = tag;
      _tagMap[tag] = (_tagMap[tag] || 0) + 1;
      m._family = '';
      for (const [re, fam] of _families) {
        if (re.test(n)) { m._family = fam; _familyMap[fam] = (_familyMap[fam] || 0) + 1; break; }
      }
      if ((m.backend === 'ollama' || m.is_ollama) && !m._family) {
        m._family = 'ollama';
        _familyMap.ollama = (_familyMap.ollama || 0) + 1;
      }
    }

    // Render tag chips
    const tagContainer = document.getElementById('serve-tags');
    if (tagContainer) {
      const tagOrder = ['llm', 'image', 'lora', 'embedding', 'tts', 'stt', 'other'];
      let tagHtml = `<button class="memory-cat-chip active" data-serve-tag="">All (${allModels.length})</button>`;
      for (const t of tagOrder) {
        if (!_tagMap[t]) continue;
        tagHtml += `<button class="memory-cat-chip" data-serve-tag="${t}">${t} (${_tagMap[t]})</button>`;
      }
      const sortedFamilies = Object.entries(_familyMap).sort((a, b) => b[1] - a[1]);
      if (sortedFamilies.length) {
        for (const [fam, count] of sortedFamilies) {
          const logo = providerLogo(fam);
          const logoHtml = logo ? `<span style="width:12px;height:12px;display:inline-flex;align-items:center;vertical-align:-2px;margin-right:2px;opacity:0.6;">${logo}</span>` : '';
          tagHtml += `<button class="memory-cat-chip" data-serve-tag="fam:${fam}">${logoHtml}${fam} (${count})</button>`;
        }
      }
      tagContainer.innerHTML = tagHtml;
    }

    _rerenderCachedModels();
  } catch (e) {
    _dlWp.destroy();
    list.innerHTML = `<div class="hwfit-loading">Failed: ${esc(e.message)}</div>`;
  }
}

/** Filter presets matching a model repo */
function _presetsForModel(presets, repo) {
  const short = repo.split('/').pop();
  return presets.filter(p => {
    const pm = p.model || ''; const pn = p.name || '';
    return pm === repo || pn === repo || pm.split('/').pop() === short || pn === short;
  });
}

// ── Init ──

export function initServe(shared) {
  _envState = shared._envState;
  _sshCmd = shared._sshCmd;
  _getPort = shared._getPort;
  _sshPrefix = shared._sshPrefix;
  _getPlatform = shared._getPlatform;
  _isWindows = shared._isWindows;
  _isMetal = shared._isMetal;
  _buildEnvPrefix = shared._buildEnvPrefix;
  _buildServeCmd = shared._buildServeCmd;
  _shellQuote = shared._shellQuote;
  _psQuote = shared._psQuote;
  _detectBackend = shared._detectBackend;
  _detectToolParser = shared._detectToolParser;
  _detectModelOptimizations = shared._detectModelOptimizations;
  _loadPresets = shared._loadPresets;
  _savePresets = shared._savePresets;
  _copyText = shared._copyText;
  _persistEnvState = shared._persistEnvState;
  _getGpuToggleTotal = shared._getGpuToggleTotal;
  modelLogo = shared.modelLogo;
  esc = shared.esc;
  _launchServeTask = shared._launchServeTask;
  _retryDownload = shared._retryDownload;
  _nextAvailablePort = shared._nextAvailablePort;
}

export { _cachedAllModels, _filterCachedList, _rerenderCachedModels, _deleteCachedModel };

// Click the "running" pill on a serve-card → switch to Cookbook → Running
// tab and scroll the matching task into view, with a brief flash so the
// user can find it among a long list. Tracks the click via event
// delegation so it survives every _rerenderCachedModels() pass.
function _openRunningTabForRepo(repo) {
  const body = document.querySelector('#cookbook-modal .cookbook-body');
  if (!body) return;
  const runTab = body.querySelector('.cookbook-tab[data-backend="Running"]');
  if (runTab) runTab.click();
  // The Running tab needs a tick to mount/render before we can find
  // task cards inside it.
  setTimeout(() => {
    const candidates = Array.from(body.querySelectorAll('.cookbook-task'));
    const match = candidates.find(c => {
      // task cards expose modelId or name via dataset / inner title
      const dsRepo = c.dataset?.modelId || c.dataset?.repoId || '';
      if (dsRepo === repo) return true;
      const title = c.querySelector('.cookbook-task-title, .memory-item-title')?.textContent?.trim() || '';
      return title === repo || title === (repo.split('/').pop() || '');
    });
    if (match) {
      try { match.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (_) {}
      match.classList.add('cookbook-task-flash');
      setTimeout(() => match.classList.remove('cookbook-task-flash'), 1600);
    }
  }, 180);
}
document.addEventListener('click', (e) => {
  const pill = e.target.closest && e.target.closest('.cookbook-serve-running-pill.is-clickable');
  if (!pill) return;
  e.preventDefault();
  e.stopPropagation();
  const repo = pill.dataset.repo || '';
  if (repo) _openRunningTabForRepo(repo);
});

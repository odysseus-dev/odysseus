// ============================================
// COOKBOOK SERVE SUB-MODULE
// Serve tab: cached model list, serve panel building,
// command building, preset slots, launch logic
// ============================================

import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import { providerLogo } from './providers.js';
import { modelColor } from './chatRenderer.js';

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

function _hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj || {}, key);
}

function _allGpuIds(count) {
  const n = Number(count || 0);
  if (!Number.isFinite(n) || n <= 0) return '';
  return Array.from({ length: Math.floor(n) }, (_, i) => String(i)).join(',');
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
    if (m.status === 'downloading') {
      const _active = _isActivelyDownloading(m.repo_id);
      metaParts.push(`<span class="cookbook-dl-status" style="color:var(--accent,var(--red));">${_active ? 'downloading' : 'download stalled'}</span>`);
    }
    const isSelectMode = document.getElementById('hwfit-cache-select')?.classList.contains('active');
    html += `<div class="doclib-card memory-item" data-repo="${esc(m.repo_id)}" data-tag="${m._tag || ''}" data-family="${m._family || ''}" style="cursor:pointer;">`;
    html += `<span class="serve-select-cb memory-select-dot" style="display:${isSelectMode ? 'inline-block' : 'none'};cursor:pointer;"></span>`;
    html += `<div style="flex:1;min-width:0;">`;
    const _mc = modelColor(m.repo_id) || '';
    const _runningPill = _isActivelyServing(m.repo_id)
      ? ' <span class="cookbook-serve-running-pill" title="This model is currently being served">running</span>'
      : '';
    html += `<div class="memory-item-title"${_mc ? ` style="color:${_mc}"` : ''}>${modelLogo(m.repo_id)}${esc(shortName)}${hfLink ? ` <a href="${esc(hfLink)}" target="_blank" rel="noopener" class="cookbook-hf-link">HF ↗</a>` : ''}${_runningPill}</div>`;
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
      // Toggle: if a dropdown for THIS button is already open, close it.
      const existing = document.querySelector('.hwfit-cached-dropdown');
      if (existing && existing._anchor === btn) {
        existing.remove();
        btn.classList.remove('cookbook-menu-active');
        return;
      }
      // Otherwise close any other open menu (and clear its anchor's active
      // state) before opening fresh.
      document.querySelectorAll('.hwfit-cached-dropdown').forEach(d => {
        if (d._anchor) d._anchor.classList.remove('cookbook-menu-active');
        d.remove();
      });
      const item = btn.closest('.memory-item');
      const repo = item?.dataset.repo;
      if (!repo) return;
      const m = allModels.find(x => x.repo_id === repo);

      const dropdown = document.createElement('div');
      dropdown.className = 'hwfit-cached-dropdown';
      dropdown._anchor = btn;
      btn.classList.add('cookbook-menu-active');
      const _di = (svg) => `<span class="dropdown-icon">${svg}</span>`;
      const _serveIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
      const _retryIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
      const _deleteIco = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>';
      const _selectIco = '<span style="font-size:16px;line-height:1;position:relative;top:-2px;">●</span>';
      const items = [];
      if (m && m.status === 'ready') items.push({ label: 'Serve', icon: _serveIco, action: 'serve' });
      if (m && m.status === 'downloading') items.push({ label: 'Retry', icon: _retryIco, action: 'retry' });
      items.push({ label: 'Select', icon: _selectIco, action: 'select' });
      items.push({ label: 'Delete', icon: _deleteIco, action: 'delete', danger: true });
      for (const opt of items) {
        const div = document.createElement('div');
        div.className = 'dropdown-item-compact' + (opt.danger ? ' dropdown-item-danger' : '');
        div.innerHTML = _di(opt.icon) + '<span>' + opt.label + '</span>';
        div.addEventListener('click', () => {
          dropdown.remove();
          btn.classList.remove('cookbook-menu-active');
          if (opt.action === 'serve') item.click();
          else if (opt.action === 'delete') _deleteCachedModel(repo, item, false, m);
          else if (opt.action === 'retry') _retryCachedModel(repo, m);
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
      cancelDiv.addEventListener('click', () => {
        dropdown.remove();
        btn.classList.remove('cookbook-menu-active');
      });
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
      const close = (ev) => { if (!dropdown.contains(ev.target) && ev.target !== btn) { dropdown.remove(); btn.classList.remove('cookbook-menu-active'); document.removeEventListener('click', close, true); } };
      setTimeout(() => document.addEventListener('click', close, true), 0);
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
        item.querySelector('.hwfit-serve-panel')?.remove();
        item.classList.remove('doclib-card-expanded');
        item.style.flexDirection = '';
        item.style.alignItems = '';
        list.style.minHeight = '';
        list.style.maxHeight = '';
        return;
      }

      // Collapse any other expanded
      list.querySelectorAll('.doclib-card-expanded').forEach(c => {
        c.querySelector('.hwfit-serve-panel')?.remove();
        c.classList.remove('doclib-card-expanded');
        c.style.flexDirection = '';
        c.style.alignItems = '';
      });

      // Capture grid height
      const _tb = list.closest('.admin-card')?.querySelector('.memory-toolbar');
      const _tbH = _tb ? _tb.offsetHeight : 0;
      list.style.minHeight = (list.offsetHeight + _tbH) + 'px';
      list.style.maxHeight = (list.offsetHeight + _tbH) + 'px';

      const shortName = repo.split('/').pop();
      const _es = _envState;
      // The venv set per-server in Settings (server.envPath). Used as the venv
      // field default when the global active env path isn't carrying it, so a
      // configured server venv shows up without re-typing it.
      const _selSrv = (_es.servers || []).find(s => s.host === (_es.remoteHost || '')) || {};
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
      const defaultBackend = detectedBackend;
      const savedMatchesBackend = (ss.backend || 'vllm') === detectedBackend;
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
      const _l = (name, tip) => `<span>${name}<span class="hwfit-hint" title="${tip}">?</span></span>`;
      // Build save slots
      const _allPresets = _loadPresets();
      const _repoShort = repo.split('/').pop();
      const _modelPresets = _presetsForModel(_allPresets, repo);
      // Saved configs live in a single dropdown (used to be a row of squeezed
      // chips). The toggle shows the count; the menu lists each config (click to
      // load, × to delete) plus a "Save current config" row — see _showSavedConfigMenu.
      // Split button: "Save" saves the current config directly; the arrow opens
      // the dropdown of saved configs (load / delete). Arrow shows the count.
      const _arrowLabel = _modelPresets.length > 0 ? `${_modelPresets.length} ▾` : '▾';
      let _slotsHtml = `<div class="cookbook-serve-slots cookbook-saved-split">`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-save" title="Save current config"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save</button>`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-arrow" title="Saved launch configs">${_arrowLabel}</button>`
        + `</div>`;

      let panelHtml = `<div class="hwfit-serve-panel">${_slotsHtml}`;
      // Row 1: Backend + Server + Env
      panelHtml += `<div class="hwfit-serve-row">`;
      const _backendChoices = _isWindows()
        ? [['llamacpp','llama.cpp']]
        : _isMetal()
        // Diffusers (diffusion_server.py) is CUDA-only — omit it on Metal.
        ? [['llamacpp','llama.cpp'],['ollama','Ollama']]
        : [['vllm','vLLM'],['sglang','SGLang'],['llamacpp','llama.cpp'],['diffusers','Diffusers']];
      const backendOpts = _backendChoices.map(([v,l]) => `<option value="${v}"${defaultBackend===v?' selected':''}>${l}</option>`).join('');
      panelHtml += `<label>${_l('Backend','Inference engine: vLLM, SGLang, llama.cpp, or Diffusers')}<select class="hwfit-sf" data-field="backend">${backendOpts}</select></label>`;
      panelHtml += `<input type="hidden" class="hwfit-sf" data-field="host" value="${esc(_es.remoteHost || '')}" />`;
      panelHtml += `<label>${_l('venv','Path to Python venv or conda env activate script')}<input type="text" class="hwfit-sf hwfit-sf-wide" data-field="venv" value="${esc(sv('venv', _es.envPath || _srvVenv || ''))}" placeholder="~/venv" /></label>`;
      panelHtml += `<label>${_l('Port','HTTP port for the API server')}<input type="text" class="hwfit-sf" data-field="port" value="${esc(sv('port', _nextAvailablePort()))}" /></label>`;
      const _activeGpus = (defaultGpus || '').split(',').map(s => s.trim()).filter(Boolean);
      const detectedGpuCount = Number(_getGpuToggleTotal?.() || 0);
      const _gpuMax = Math.max(detectedGpuCount || 8, ...(_activeGpus.map(Number).filter(n => !isNaN(n)).map(n => n + 1)));
      let _gpuBtnsHtml = '';
      for (let i = 0; i < _gpuMax; i++) {
        const on = _activeGpus.includes(String(i));
        _gpuBtnsHtml += `<button type="button" class="cookbook-gpu-btn${on ? ' active' : ''}" data-gpu="${i}">${i}</button>`;
      }
      panelHtml += `<label>${_l('GPUs','Toggle which GPUs to use')}<div class="cookbook-gpu-group">${_gpuBtnsHtml}</div><input type="hidden" class="hwfit-sf" data-field="gpus" value="${esc(defaultGpus)}" /></label>`;
      panelHtml += `</div>`;
      // Row 2: Core settings
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-vllm hwfit-backend-sglang hwfit-backend-llamacpp">`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('TP','Tensor Parallelism — split model across N GPUs')}<select class="hwfit-sf" data-field="tp">${tpOpts}</select></label>`;
      panelHtml += `<label>${_l('Context','Max tokens per request. Lower = less VRAM')}<input type="text" class="hwfit-sf" data-field="ctx" value="${esc(sv('ctx', '8192'))}" /></label>`;
      panelHtml += `<label>${_l('GPU','Which GPU to use. Leave empty for default')}<input type="text" class="hwfit-sf" data-field="gpu_id" value="${esc(sv('gpu_id', ''))}" placeholder="auto" style="width:50px;" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('GPU Mem','Fraction of GPU memory (0.0–1.0). Lower if OOM')}<input type="text" class="hwfit-sf" data-field="gpu_mem" value="${esc(sv('gpu_mem', '0.90'))}" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Swap','CPU swap space in GB. Leave empty to omit (removed in newer vLLM)')}<input type="text" class="hwfit-sf" data-field="swap" value="${esc(sv('swap', ''))}" placeholder="off" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('Max Seqs','Maximum concurrent requests. Lower = less memory. Default 8 — prosumer GPUs often OOM on vLLM default 256 during CUDA graph capture.')}<input type="text" class="hwfit-sf" data-field="max_seqs" value="${esc(sv('max_seqs', '8'))}" placeholder="8" /></label>`;
      panelHtml += `<label>${_l('Dtype','Data type for weights. auto picks best for GPU')}<select class="hwfit-sf" data-field="dtype">${dtypeOpts}</select></label>`;
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
      // Row 3a: Checkboxes (llama.cpp-only)
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-llamacpp">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="unified_mem"${sv('unified_mem',false)?' checked':''} /> Unified Memory${_h('For AMD APUs / Strix Halo: exports GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 so llama.cpp can address the full BIOS VRAM carveout instead of the default ~28 GB cap. No-op on discrete GPUs.')}</label>`;
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
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-vllm" style="margin-top:2px;">`;
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
        panelHtml += `<label class="hwfit-sf-cb hwfit-spec-group"><input type="checkbox" class="hwfit-sf" data-field="speculative" /> Speculative <select class="hwfit-sf hwfit-spec-method" data-field="spec_method" title="vLLM --speculative-config method">${_specOpts}</select><span class="hwfit-numstep"><button type="button" class="hwfit-numstep-btn" data-step="-1" tabindex="-1" aria-label="Decrease">‹</button><input type="number" class="hwfit-sf hwfit-spec-tokens" data-field="spec_tokens" value="${esc(_specTokens)}" min="1" max="10" title="num_speculative_tokens" /><button type="button" class="hwfit-numstep-btn" data-step="1" tabindex="-1" aria-label="Increase">›</button></span></label>`;
      }
      if (_opts2.envVars.length) panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="moe_env" /> MoE Env Vars</label>`;
      panelHtml += `</div>`;
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
      panelHtml += `<button class="cookbook-btn hwfit-serve-cancel" type="button" title="Close this configuration panel">Cancel</button>`;
      panelHtml += `<button class="cookbook-btn hwfit-serve-launch">Launch</button>`;
      panelHtml += `</div>`;
      panelHtml += `</div>`;

      item.classList.add('doclib-card-expanded');
      item.style.flexDirection = 'column';
      item.style.alignItems = 'stretch';
      if (list) list.scrollTop = 0;
      item.insertAdjacentHTML('beforeend', panelHtml);
      const panel = item.querySelector('.hwfit-serve-panel');

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
          // For multi-part GGUFs, llama.cpp requires the first split
          // (-00001-of-NNNNN.gguf). Prefer it (sorted, so UD-IQ4_XS/001 comes
          // before Q4_K_M/001 etc); fall back to any single GGUF sorted.
          // Use $HOME (not ~) so tilde survives variable interpolation inside $(...).
          const dir = `"$HOME/.cache/huggingface/hub/models--${repo.replace(/\//g, '--')}/snapshots"`;
          // GGUF needs the actual .gguf FILE, not the folder. For a custom-dir
          // model the file lives under "<path>/<repo>" — search there just like we
          // search the HF snapshots dir, so serving a GGUF from a custom dir works
          // instead of handing llama.cpp a directory (which fails).
          const _ldir = `"${m.path}/${repo}"`;
          f._gguf_path = m.is_local_dir && m.path
            ? `$({ find ${_ldir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${_ldir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`
            : `$({ find ${dir} -name '*-00001-of-*.gguf' 2>/dev/null | sort; find ${dir} -name '*.gguf' 2>/dev/null | sort; } | head -1)`;
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

      // Show/hide backend-specific sections
      function updateBackendVisibility() {
        const b = panel.querySelector('[data-field="backend"]')?.value || 'vllm';
        panel.querySelectorAll('[class*="hwfit-backend-"]').forEach(el => {
          const show = el.classList.contains(`hwfit-backend-${b}`);
          el.style.display = show ? '' : 'none';
        });
      }
      updateBackendVisibility();

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
            max_seqs: _ex(/--max-num-seqs\s+(\d+)/) || '',
            venv: p.envPath || '',
          };
          const checks = {
            enforce_eager: cmd.includes('--enforce-eager'),
            trust_remote: cmd.includes('--trust-remote-code'),
            prefix_cache: cmd.includes('--enable-prefix-caching'),
            auto_tool: cmd.includes('--enable-auto-tool-choice'),
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
        updateCmd();
        panel.querySelectorAll('.cookbook-slot-btn').forEach(b => b.classList.remove('active'));
        panel.querySelector(`.cookbook-slot-btn[data-slot="${slotIdx}"]`)?.classList.add('active');
      }

      // Keep the arrow button's count in sync with the stored presets.
      function _updateSavedToggleLabel() {
        const n = _presetsForModel(_loadPresets(), repo).length;
        const t = panel.querySelector('.cookbook-saved-arrow');
        if (t) t.textContent = n > 0 ? `${n} ▾` : '▾';
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
        document.querySelectorAll('.cookbook-saved-menu').forEach(d => d.remove());
        const modelSlots = _presetsForModel(_loadPresets(), repo);
        const dropdown = document.createElement('div');
        dropdown.className = 'dropdown cookbook-saved-menu';
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
            dropdown.remove();
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
        const close = (ev) => {
          if (!dropdown.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) {
            dropdown.remove();
            anchor.classList.remove('cookbook-menu-active');
            document.removeEventListener('click', close, true);
          }
        };
        setTimeout(() => document.addEventListener('click', close, true), 10);
      }

      // "Save" segment — save the current config directly.
      const savedSaveBtn = panel.querySelector('.cookbook-saved-save');
      if (savedSaveBtn) {
        savedSaveBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          document.querySelectorAll('.cookbook-saved-menu').forEach(d => d.remove());
          await _saveCurrentConfig();
        });
      }
      // Arrow segment — open/close the saved-configs dropdown.
      const savedArrowBtn = panel.querySelector('.cookbook-saved-arrow');
      if (savedArrowBtn) {
        savedArrowBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          if (document.querySelector('.cookbook-saved-menu')) {
            document.querySelectorAll('.cookbook-saved-menu').forEach(d => d.remove());
            savedArrowBtn.classList.remove('cookbook-menu-active');
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
          document.querySelectorAll('.cookbook-gpu-split-menu').forEach(m => m.remove());
          const menu = document.createElement('div');
          menu.className = 'cookbook-task-dropdown cookbook-gpu-split-menu';
          const mk = (label, cls, onClick) => {
            const it = document.createElement('div');
            it.className = 'dropdown-item-compact' + (cls ? ' ' + cls : '');
            it.style.cssText = 'display:flex;align-items:center;gap:8px;';
            it.textContent = label;
            it.addEventListener('click', (e) => {
              e.stopPropagation();
              menu.remove();
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
          const close = (e) => {
            if (!menu.contains(e.target) && e.target !== _splitArrow) {
              menu.remove();
              document.removeEventListener('click', close);
              window.removeEventListener('scroll', _scrollClose, true);
            }
          };
          const _scrollClose = () => { menu.remove(); document.removeEventListener('click', close); window.removeEventListener('scroll', _scrollClose, true); };
          setTimeout(() => {
            document.addEventListener('click', close);
            window.addEventListener('scroll', _scrollClose, true);
          }, 0);
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

          const r = anchorBtn.getBoundingClientRect();
          popup.style.left = `${Math.max(8, r.left)}px`;
          popup.style.top  = `${r.bottom + 4 + window.scrollY}px`;

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
        panel.remove();
        item.classList.remove('doclib-card-expanded');
        item.style.flexDirection = '';
        item.style.alignItems = '';
        if (list) { list.style.minHeight = ''; list.style.maxHeight = ''; }
      });

      // Launch button
      panel.querySelector('.hwfit-serve-launch').addEventListener('click', async (ev) => {
        const _launchBtn = ev.currentTarget;
        if (!_cmdManuallyEdited) updateCmd();
        const launchCmd = _cmdTextarea ? _cmdTextarea.value.trim() : panel._cmd;
        const serveState = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') serveState[el.dataset.field] = el.checked;
          else serveState[el.dataset.field] = el.value;
        });
        serveState.backend = (_detectBackend(m).backend) || serveState.backend || 'vllm';
        // Save in the { _byRepo, _lastUsed } schema — no legacy flat keys at
        // the root so per-model state doesn't leak between models.
        try {
          let cur = {};
          try { cur = JSON.parse(localStorage.getItem(SERVE_STATE_KEY)) || {}; } catch {}
          const byRepo = (cur && cur._byRepo && typeof cur._byRepo === 'object') ? cur._byRepo : {};
          byRepo[repo] = serveState;
          localStorage.setItem(SERVE_STATE_KEY, JSON.stringify({ _byRepo: byRepo, _lastUsed: serveState }));
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
            // Values are host strings now; resolve by host (numeric fallback).
            const _srv = _envState.servers.find(s => s.host === _ssEl.value) || _envState.servers[parseInt(_ssEl.value)];
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
      panel.querySelector('.hwfit-serve-copy').addEventListener('click', () => {
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
  if (cacheSrv) {
    const val = cacheSrv.value;
    if (val === 'local') host = '';
    else { const s = _envState.servers.find(x => x.host === val) || _envState.servers[parseInt(val)]; if (s) host = s.host; }
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
      byRepo[repo] = fields;
      localStorage.setItem(SERVE_STATE_KEY, JSON.stringify({ _byRepo: byRepo, _lastUsed: fields }));
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
      if (!card.classList.contains('doclib-card-expanded')) card.click();
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
    const cacheSrv = document.getElementById('hwfit-cache-server');
    if (cacheSrv) {
      const val = cacheSrv.value;
      if (val === 'local') {
        host = '';
        selectedServer = _envState.servers.find(s => !s.host || s.host === 'local') || _envState.servers[0];
      } else {
        const s = _envState.servers.find(x => x.host === val) || _envState.servers[parseInt(val)];
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
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    _dlWp.destroy();

    const ready = data.models.filter(m => m.status === 'ready' && !m.size.includes('MB'));
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
      if (m.is_diffusion || /flux|sdxl|stable-diffusion|z-image|qwen-image|diffusion|dreamshar/i.test(n)) tag = 'image';
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

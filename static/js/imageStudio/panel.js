/**
 * Image Studio — manual txt2img / img2img without chat LLM.
 * Model selection warms SD (stops LLM); generation keeps SD loaded between runs.
 */
import * as Modals from '../modalManager.js';
import { makeWindowDraggable } from '../windowDrag.js';

let _open = false;
let _apiBase = '';
let _config = null;
let _pollTimer = null;
let _warmedProfile = null;
let _generating = false;
let _genStartMs = 0;
let _genEstMs = 45000;
let _initImageB64 = null;
let _initImagePreview = null;
let _lastOutputUrl = null;
let _pendingRecipe = null;
// IP-Adapter / PhotoMaker removed.

const LS_DRAFT_KEY = 'titan-image-studio-draft';
const SIZE_CUSTOM = 'custom';

const SDXL_SIZE_PRESETS = [
  { label: '1024×1024 square', value: '1024x1024' },
  { label: '832×1216 portrait', value: '832x1216' },
  { label: '1216×832 landscape', value: '1216x832' },
  { label: '768×768', value: '768x768' },
  { label: '512×512', value: '512x512' },
];

const KREA_SIZE_PRESETS = [
  { label: '960×1440 portrait (default)', value: '960x1440' },
  { label: '1440×960 landscape', value: '1440x960' },
  { label: '960×960 square', value: '960x960' },
  { label: '1280×720 widescreen (16:9)', value: '1280x720' },
  { label: '1024×1024 square', value: '1024x1024' },
  { label: '832×1216 portrait', value: '832x1216' },
];

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function injectStyles() {
  if (document.getElementById('image-studio-css')) return;
  const st = document.createElement('style');
  st.id = 'image-studio-css';
  st.textContent = `
    #image-studio-modal .image-studio-modal-content {
      width: min(1080px, 96vw);
      max-height: 92vh;
      display: flex;
      flex-direction: column;
    }
    #image-studio-modal .modal-body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
    .is-layout { display: grid; grid-template-columns: 280px 1fr; gap: 16px; flex: 1; min-height: 0; overflow: hidden; }
    @media (max-width: 820px) { .is-layout { grid-template-columns: 1fr; overflow-y: auto; } }
    .is-sidebar { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; padding-right: 4px; }
    .is-main { display: flex; flex-direction: column; gap: 10px; min-height: 0; overflow-y: auto; }
    .is-field label { display: block; font-size: 11px; opacity: 0.7; margin-bottom: 3px; }
    .is-field input, .is-field select, .is-field textarea {
      width: 100%; font-size: 13px; padding: 6px 8px; border-radius: 6px;
      border: 1px solid var(--border, #444); background: var(--surface-1, #222); color: inherit;
    }
    .is-field textarea { min-height: 88px; resize: vertical; font-family: inherit; line-height: 1.4; }
    .is-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .is-status { font-size: 12px; opacity: 0.85; min-height: 18px; padding: 4px 0; }
    .is-status.warn { color: #f59e0b; }
    .is-status.err { color: #ef4444; }
    .is-status.ok { color: #22c55e; }
    .is-preview { flex: 1; min-height: 200px; display: flex; align-items: center; justify-content: center;
      background: var(--surface-2, rgba(0,0,0,.2)); border-radius: 8px; border: 1px solid var(--border, #444); overflow: hidden; }
    .is-preview img { max-width: 100%; max-height: 55vh; object-fit: contain; }
    .is-preview-empty { opacity: 0.45; font-size: 13px; text-align: center; padding: 24px; }
    .is-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .is-actions button {
      font-size: 13px; padding: 8px 16px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--border, #555); background: var(--surface-1, #333); color: inherit;
    }
    .is-actions button.primary { background: var(--accent, #3b82f6); border-color: transparent; color: #fff; }
    .is-actions button:disabled { opacity: 0.45; cursor: not-allowed; }
    .is-krea-hint, .is-size-hint { font-size: 11px; opacity: 0.65; margin-top: 4px; line-height: 1.35; }
    .is-mode-tabs { display: flex; gap: 6px; margin-bottom: 4px; }
    .is-mode-tabs button {
      flex: 1; font-size: 12px; padding: 6px 10px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--border, #555); background: var(--surface-1, #333); color: inherit;
    }
    .is-mode-tabs button.active { background: var(--accent, #3b82f6); border-color: transparent; color: #fff; }
    .is-size-custom { display: none; margin-top: 8px; padding: 8px; border-radius: 8px;
      border: 1px solid var(--border, #444); background: var(--surface-2, rgba(0,0,0,.15)); }
    .is-size-custom.open { display: block; }
    .is-size-custom label { font-size: 11px; opacity: 0.75; }
    .is-size-custom input[type="range"] { width: 100%; margin: 2px 0 6px; }
    .is-size-custom .is-dim-row { display: grid; grid-template-columns: 1fr 72px; gap: 8px; align-items: center; margin-bottom: 6px; }
    .is-size-custom .is-dim-row input[type="number"] { width: 100%; }
    .is-init-wrap { display: none; margin-bottom: 8px; }
    .is-init-wrap.open { display: block; }
    .is-init-drop {
      border: 2px dashed var(--border, #555); border-radius: 8px; padding: 12px; text-align: center;
      cursor: pointer; font-size: 12px; opacity: 0.85; min-height: 72px; display: flex; align-items: center;
      justify-content: center; flex-direction: column; gap: 6px;
    }
    .is-init-drop.dragover { border-color: var(--accent, #3b82f6); opacity: 1; }
    .is-init-drop img { max-width: 100%; max-height: 120px; object-fit: contain; border-radius: 4px; }
    .is-init-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
    .is-init-actions button { font-size: 11px; padding: 4px 10px; border-radius: 6px; cursor: pointer;
      border: 1px solid var(--border, #555); background: var(--surface-1, #333); color: inherit; }
    .is-strength-row { margin-top: 8px; }
    .is-strength-row input[type="range"] { width: 100%; }
    .is-seed-lock-row {
      display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px;
      font-size: 11px; opacity: 0.85;
    }
    .is-seed-lock-row .toggle-switch { flex-shrink: 0; }
    .is-control-net-row {
      display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px;
      font-size: 11px; opacity: 0.85;
    }
    .is-control-net-row.disabled { opacity: 0.45; pointer-events: none; }
    .is-control-net-row .toggle-switch { flex-shrink: 0; }
    /* (removed) IP-Adapter UI */
  `;
  document.head.appendChild(st);
}

function _panel() {
  return document.getElementById('image-studio-pane');
}

function _sizePresetsForStyle(style) {
  return style === 'krea' ? KREA_SIZE_PRESETS : SDXL_SIZE_PRESETS;
}

function _dimStep(style) {
  return style === 'krea' ? 16 : 64;
}

function _snapDim(val, style) {
  const step = _dimStep(style);
  const n = Math.round(Number(val) / step) * step;
  return Math.min(2048, Math.max(step, n || step));
}

function _parseSizeStr(s) {
  const m = String(s || '').toLowerCase().match(/^(\d+)x(\d+)$/);
  return m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : null;
}

function _isImg2imgMode() {
  return _panel()?.querySelector('#is-mode-img2img')?.classList.contains('active') ?? false;
}

function _isIpMode() {
  return false;
}

function _setMode(mode) {
  const pane = _panel();
  if (!pane) return;
  const txt = pane.querySelector('#is-mode-txt2img');
  const img = pane.querySelector('#is-mode-img2img');
  const initWrap = pane.querySelector('#is-init-wrap');
  const isI2i = mode === 'img2img';
  txt?.classList.toggle('active', !isI2i);
  img?.classList.toggle('active', isI2i);
  initWrap?.classList.toggle('open', isI2i);
  _syncControlNetUi();
  _saveDraft();
}

function _toggleCustomSize(show) {
  const pane = _panel();
  if (!pane) return;
  pane.querySelector('#is-size-custom')?.classList.toggle('open', show);
}

function _syncCustomSizeFromInputs() {
  const pane = _panel();
  if (!pane) return;
  const style = _selectedStyle();
  const wIn = pane.querySelector('#is-width');
  const hIn = pane.querySelector('#is-height');
  const wSl = pane.querySelector('#is-width-range');
  const hSl = pane.querySelector('#is-height-range');
  if (!wIn || !hIn) return;
  const w = _snapDim(wIn.value, style);
  const h = _snapDim(hIn.value, style);
  wIn.value = String(w);
  hIn.value = String(h);
  if (wSl) wSl.value = String(w);
  if (hSl) hSl.value = String(h);
  const snapEl = pane.querySelector('#is-size-snapped');
  if (snapEl) {
    snapEl.textContent = style === 'krea'
      ? `Effective: ${w}×${h} (×16)`
      : `Effective: ${w}×${h} (SDXL may bucket further)`;
  }
}

function _getSelectedSize() {
  const pane = _panel();
  if (!pane) return '1024x1024';
  const sel = pane.querySelector('#is-size');
  if (sel?.value === SIZE_CUSTOM) {
    _syncCustomSizeFromInputs();
    const w = pane.querySelector('#is-width')?.value;
    const h = pane.querySelector('#is-height')?.value;
    if (w && h) return `${w}x${h}`;
  }
  return sel?.value || '1024x1024';
}

function _setInitImage(dataUrl, b64) {
  _initImagePreview = dataUrl || null;
  _initImageB64 = b64 || null;
  const pane = _panel();
  const drop = pane?.querySelector('#is-init-drop');
  if (!drop) return;
  if (dataUrl) {
    drop.innerHTML = `<img src="${esc(dataUrl)}" alt="Init">`;
  } else {
    drop.innerHTML = '<span>Drop image, paste, or click to upload</span>';
  }
}

async function _loadInitFromFile(file) {
  if (!file || !file.type.startsWith('image/')) {
    _setStatus('Choose an image file', 'warn');
    return;
  }
  const dataUrl = await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
  const b64 = String(dataUrl).includes(',') ? String(dataUrl).split(',')[1] : String(dataUrl);
  _setInitImage(dataUrl, b64);
  _setStatus('Init image loaded', 'ok');
}

async function _useLastOutputAsInit() {
  if (!_lastOutputUrl) {
    _setStatus('Generate an image first, or upload one', 'warn');
    return;
  }
  try {
    const r = await fetch(_lastOutputUrl, { credentials: 'same-origin' });
    const blob = await r.blob();
    await _loadInitFromFile(new File([blob], 'output.png', { type: blob.type || 'image/png' }));
  } catch (e) {
    _setStatus(String(e.message || e), 'err');
  }
}

function _renderIpRefs() {
  return;
}

function _ipRefsPayload() {
  return [];
}

async function _addIpRefFromFile(file) {
  _setStatus('Identity references are no longer supported here.', 'warn');
}

async function _openIpGalleryPicker() {
  _setStatus('Identity references are no longer supported here.', 'warn');
}

function _loadDraft() {
  try {
    return JSON.parse(localStorage.getItem(LS_DRAFT_KEY) || '{}');
  } catch (_) {
    return {};
  }
}

function _saveDraft() {
  const pane = _panel();
  if (!pane) return;
  const style = _selectedStyle();
  const prompt = pane.querySelector('#is-prompt')?.value ?? '';
  const negative = pane.querySelector('#is-negative')?.value ?? '';
  const draft = _loadDraft();
  draft.prompt = prompt;
  draft.negative = negative;
  draft.byStyle = draft.byStyle || {};
  draft.byStyle[style] = { prompt, negative };
  draft.mode = _isImg2imgMode() ? 'img2img' : 'txt2img';
  draft.strength = pane.querySelector('#is-strength')?.value;
  draft.customW = pane.querySelector('#is-width')?.value;
  draft.customH = pane.querySelector('#is-height')?.value;
  draft.sizePreset = pane.querySelector('#is-size')?.value;
  draft.seedLock = _isSeedLocked();
  draft.controlNet = _isControlNetEnabled();
  try {
    localStorage.setItem(LS_DRAFT_KEY, JSON.stringify(draft));
  } catch (_) {}
}

function _restoreDraft() {
  const pane = _panel();
  if (!pane) return;
  const style = _selectedStyle();
  const draft = _loadDraft();
  const per = draft.byStyle?.[style];
  const promptEl = pane.querySelector('#is-prompt');
  const negEl = pane.querySelector('#is-negative');
  if (promptEl) promptEl.value = per?.prompt ?? draft.prompt ?? '';
  if (negEl && style !== 'krea') negEl.value = per?.negative ?? draft.negative ?? '';
  if (draft.mode === 'img2img') _setMode('img2img');
  else _setMode('txt2img');
  const strEl = pane.querySelector('#is-strength');
  if (strEl && draft.strength != null) {
    strEl.value = draft.strength;
    const strLab = pane.querySelector('#is-strength-val');
    if (strLab) strLab.textContent = Number(draft.strength).toFixed(2);
  }
  const sizeSel = pane.querySelector('#is-size');
  if (sizeSel && draft.sizePreset) {
    if ([...sizeSel.options].some((o) => o.value === draft.sizePreset)) {
      sizeSel.value = draft.sizePreset;
      _toggleCustomSize(draft.sizePreset === SIZE_CUSTOM);
    }
  }
  if (draft.customW) pane.querySelector('#is-width').value = draft.customW;
  if (draft.customH) pane.querySelector('#is-height').value = draft.customH;
  const seedLock = pane.querySelector('#is-seed-lock');
  if (seedLock) seedLock.checked = !!draft.seedLock;
  _syncSeedLockLabel();
  const cnEl = pane.querySelector('#is-control-net');
  if (cnEl && draft.controlNet != null) cnEl.checked = !!draft.controlNet;
  _syncControlNetUi();
  _syncCustomSizeFromInputs();
}

function _isControlNetEnabled() {
  return !!_panel()?.querySelector('#is-control-net')?.checked;
}

function _syncControlNetLabel() {
  const el = _panel()?.querySelector('#is-control-net');
  const lab = _panel()?.querySelector('#is-control-net-label');
  if (el && lab) lab.textContent = el.checked ? 'on' : 'off';
}

function _syncControlNetUi() {
  const pane = _panel();
  if (!pane) return;
  const row = pane.querySelector('#is-control-net-row');
  const style = _selectedStyle();
  const disabled = _isImg2imgMode();
  if (row) row.style.display = style === 'krea' ? 'none' : '';
  row?.classList.toggle('disabled', disabled);
  const hint = pane.querySelector('#is-control-net-hint');
  if (hint) {
    hint.textContent = _isImg2imgMode() ? 'txt2img only.' : 'Two-pass: layout lock via canny.';
  }
  _syncControlNetLabel();
}

function _isSeedLocked() {
  return !!_panel()?.querySelector('#is-seed-lock')?.checked;
}

function _syncSeedLockLabel() {
  const el = _panel()?.querySelector('#is-seed-lock');
  const lab = _panel()?.querySelector('#is-seed-lock-label');
  if (el && lab) lab.textContent = el.checked ? 'on' : 'off';
}

function _profileIdForStyle(style) {
  const prof = (_config?.profiles || []).find((p) => p.style === style || p.id === style);
  return prof?.id || style;
}

async function _applyRecipe(recipe) {
  const pane = _panel();
  if (!pane || !recipe) return;
  const style = recipe.style || 'realistic';
  const profileId = recipe.profile_id || _profileIdForStyle(style);

  const sel = pane.querySelector('#is-model');
  if (sel && [...sel.options].some((o) => o.value === profileId)) {
    sel.value = profileId;
  } else if (sel) {
    const byStyle = [...sel.options].find((o) => o.dataset?.style === style);
    if (byStyle) sel.value = byStyle.value;
  }

  _applyProfileDefaults(sel?.value || profileId);

  const set = (id, val) => {
    const el = pane.querySelector(id);
    if (el && val != null && val !== '') el.value = val;
  };
  if (recipe.prompt != null) set('#is-prompt', recipe.prompt);
  if (recipe.negative_prompt != null && style !== 'krea') set('#is-negative', recipe.negative_prompt);
  if (recipe.steps != null) set('#is-steps', recipe.steps);
  if (recipe.cfg_scale != null) set('#is-cfg', recipe.cfg_scale);
  if (recipe.sampler) set('#is-sampler', recipe.sampler);
  if (recipe.scheduler) set('#is-scheduler', recipe.scheduler);
  if (recipe.seed != null && recipe.seed !== '') set('#is-seed', recipe.seed);

  const seedLock = pane.querySelector('#is-seed-lock');
  if (seedLock) {
    seedLock.checked = recipe.seedLock === true || (recipe.seed != null && recipe.seed !== '');
  }
  _syncSeedLockLabel();

  if (recipe.size) {
    const sizeSel = pane.querySelector('#is-size');
    const presets = _sizePresetsForStyle(style);
    if (sizeSel) {
      if (presets.some((p) => p.value === recipe.size)) {
        sizeSel.value = recipe.size;
        _toggleCustomSize(false);
      } else {
        sizeSel.value = SIZE_CUSTOM;
        _toggleCustomSize(true);
        const parsed = _parseSizeStr(recipe.size);
        if (parsed) {
          set('#is-width', parsed[0]);
          set('#is-height', parsed[1]);
          _syncCustomSizeFromInputs();
        }
      }
    }
  }

  _setMode(recipe.mode === 'img2img' ? 'img2img' : 'txt2img');

  if (recipe.imageUrl) {
    _lastOutputUrl = recipe.imageUrl;
    const preview = pane.querySelector('#is-preview');
    if (preview) preview.innerHTML = `<img src="${esc(recipe.imageUrl)}" alt="Reference">`;
  }

  _saveDraft();

  const pid = sel?.value;
  if (pid && pid !== _warmedProfile) {
    await _warmModel(pid);
  }
}

function _refreshSizePresets(style) {
  const pane = _panel();
  if (!pane) return;
  const sel = pane.querySelector('#is-size');
  if (!sel) return;
  const cur = sel.value;
  const presets = _sizePresetsForStyle(style);
  sel.innerHTML = presets.map((p) => `<option value="${esc(p.value)}">${esc(p.label)}</option>`).join('')
    + `<option value="${SIZE_CUSTOM}">Custom…</option>`;
  if (cur === SIZE_CUSTOM || presets.some((p) => p.value === cur)) sel.value = cur;
  else if (presets.length) sel.selectedIndex = 0;
  _toggleCustomSize(sel.value === SIZE_CUSTOM);

  const step = _dimStep(style);
  const wSl = pane.querySelector('#is-width-range');
  const hSl = pane.querySelector('#is-height-range');
  if (wSl) { wSl.min = step; wSl.max = 2048; wSl.step = step; }
  if (hSl) { hSl.min = step; hSl.max = 2048; hSl.step = step; }

  const sizeHint = pane.querySelector('#is-size-hint');
  if (sizeHint) {
    sizeHint.textContent = style === 'krea'
      ? 'KREA: custom sizes snap to multiples of 16. Not SDXL buckets.'
      : 'SDXL: presets use aspect buckets; custom values snap server-side.';
  }
  _syncCustomSizeFromInputs();
}

function _setStatus(msg, cls = '') {
  const el = _panel()?.querySelector('#is-status');
  if (el) {
    el.textContent = msg || '';
    el.className = 'is-status' + (cls ? ` ${cls}` : '');
  }
}

function _progressLabel(st) {
  const total = Number(st.progress_total) || 0;
  const step = Number(st.progress_step) || 0;
  if (total && step) return `Generating… ${Math.round((step / total) * 100)}%`;
  if (_genStartMs && _generating) {
    const elapsed = Date.now() - _genStartMs;
    const pct = Math.min(95, Math.round((elapsed / _genEstMs) * 100));
    return `Generating… ~${pct}%`;
  }
  return 'Generating…';
}

async function _fetchJson(path, opts = {}) {
  const r = await fetch(`${_apiBase}${path}`, { credentials: 'same-origin', ...opts });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail || j.error || JSON.stringify(j);
    } catch (_) {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return r.json();
}

function _applyProfileDefaults(profileId) {
  const prof = (_config?.profiles || []).find((p) => p.id === profileId);
  if (!prof) return;
  const d = prof.defaults || {};
  const pane = _panel();
  if (!pane) return;
  const style = prof.style || 'realistic';
  _refreshSizePresets(style);
  const set = (sel, val) => {
    const el = pane.querySelector(sel);
    if (el && val != null && val !== '') el.value = val;
  };
  set('#is-size', d.size || (style === 'krea' ? '960x1440' : '1024x1024'));
  const parsed = _parseSizeStr(d.size || (style === 'krea' ? '960x1440' : '1024x1024'));
  if (parsed) {
    pane.querySelector('#is-width').value = parsed[0];
    pane.querySelector('#is-height').value = parsed[1];
  }
  _toggleCustomSize(false);
  set('#is-steps', d.steps ?? (style === 'krea' ? 12 : 28));
  set('#is-cfg', d.cfg_scale ?? (style === 'krea' ? 1 : 7));
  set('#is-sampler', d.sampler_name || (style === 'krea' ? 'er_sde' : 'dpm++2m'));
  set('#is-scheduler', d.scheduler || (style === 'krea' ? 'simple' : 'karras'));
  const negWrap = pane.querySelector('#is-negative-wrap');
  if (negWrap) negWrap.style.display = style === 'krea' ? 'none' : '';
  const hint = pane.querySelector('#is-krea-hint');
  if (hint) hint.style.display = style === 'krea' ? 'block' : 'none';
  _restoreDraft();
}

async function _loadConfig() {
  _config = await _fetchJson('/api/titan/hub/image-studio/config');
  const sel = _panel()?.querySelector('#is-model');
  if (!sel) return;
  sel.innerHTML = (_config.profiles || [])
    .map(
      (p) =>
        `<option value="${esc(p.id)}" data-style="${esc(p.style)}">${esc(p.display_name)} — ${esc(p.model_label)}</option>`,
    )
    .join('');
  const cur = _config.sd_profile;
  if (cur && [...sel.options].some((o) => o.value === cur)) {
    sel.value = cur;
    _warmedProfile = cur;
  } else if (sel.options.length) {
    sel.selectedIndex = 0;
  }
  _applyProfileDefaults(sel.value);
  const cnEl = _panel()?.querySelector('#is-control-net');
  if (cnEl) {
    const draft = _loadDraft();
    if (draft.controlNet == null) {
      cnEl.checked = !!_config?.control_net_default;
    }
    _syncControlNetUi();
  }
  _syncStatusFromConfig();
}

function _syncStatusFromConfig() {
  if (!_config) return;
  if (_config.sd_active && _config.sd_profile) {
    _setStatus(`Model ready: ${_config.sd_profile} (LLM paused)`, 'ok');
    _warmedProfile = _config.sd_profile;
  } else if (_config.llm_active) {
    _setStatus('Select a model to load SD (chat LLM will pause)', 'warn');
  } else {
    _setStatus('Select a model to begin', '');
  }
}

async function _warmModel(profileId) {
  if (!profileId) return;
  _setStatus('Loading model — stopping LLM, starting SD…', '');
  const genBtn = _panel()?.querySelector('#is-generate');
  if (genBtn) genBtn.disabled = true;
  try {
    await _fetchJson('/api/titan/hub/image-studio/warm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId }),
    });
    _warmedProfile = profileId;
    _config = { ..._config, sd_active: true, sd_profile: profileId, llm_active: false };
    _setStatus(`Model ready: ${profileId}`, 'ok');
  } catch (e) {
    _setStatus(String(e.message || e), 'err');
  } finally {
    if (genBtn) genBtn.disabled = false;
  }
}

function _selectedStyle() {
  const sel = _panel()?.querySelector('#is-model');
  if (!sel) return 'realistic';
  const opt = sel.options[sel.selectedIndex];
  return opt?.dataset?.style || sel.value || 'realistic';
}

function _stopPoll() {
  if (_pollTimer) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  _genStartMs = 0;
}

function _startPoll(onDone) {
  _stopPoll();
  const tick = async () => {
    try {
      let st;
      if (window.titanSchedulerStatus) {
        const raw = await window.titanSchedulerStatus.fetchStatus();
        st = window.titanSchedulerStatus.imagePhasePayload(raw);
      } else {
        st = await _fetchJson('/api/titan/hub/image-status');
      }
      const phase = st?.phase || 'idle';
      if (phase === 'swapping') _setStatus('Preparing model…', '');
      else if (phase === 'generating') _setStatus(_progressLabel(st), '');
      else if (phase === 'restoring_llm') _setStatus('Restoring chat model…', '');
      else if (phase === 'error') {
        _setStatus(st.last_error || 'Generation failed', 'err');
        _generating = false;
        _stopPoll();
        onDone?.(false);
      } else if (phase === 'idle' && _generating) {
        _generating = false;
        _stopPoll();
        onDone?.(true);
      }
    } catch (_) {}
  };
  tick();
  _pollTimer = setInterval(tick, 900);
}

async function _generate() {
  const pane = _panel();
  if (!pane || _generating) return;
  const prompt = pane.querySelector('#is-prompt')?.value?.trim();
  if (!prompt) {
    _setStatus('Enter a prompt', 'warn');
    return;
  }
  _saveDraft();
  const profileId = pane.querySelector('#is-model')?.value;
  if (profileId !== _warmedProfile) {
    await _warmModel(profileId);
    if (_warmedProfile !== profileId) return;
  }
  const style = _selectedStyle();
  const size = _getSelectedSize();
  const body = {
    id: crypto.randomUUID(),
    op: 'generate',
    prompt,
    negative_prompt: pane.querySelector('#is-negative')?.value?.trim() || '',
    style,
    quality: 'high',
    size,
    n: 1,
    studio_mode: true,
    shutdown_after: false,
  };
  if (_isImg2imgMode()) {
    if (!_initImageB64) {
      _setStatus('Add an init image for img2img', 'warn');
      return;
    }
    body.image = _initImageB64;
    body.strength = parseFloat(pane.querySelector('#is-strength')?.value) || 0.55;
  }
  // (removed) IP-Adapter / PhotoMaker identity path
  const seedRaw = pane.querySelector('#is-seed')?.value?.trim();
  if (_isSeedLocked() && seedRaw) body.seed = parseInt(seedRaw, 10);
  const steps = pane.querySelector('#is-steps')?.value;
  const cfg = pane.querySelector('#is-cfg')?.value;
  const sampler = pane.querySelector('#is-sampler')?.value?.trim();
  const scheduler = pane.querySelector('#is-scheduler')?.value?.trim();
  if (steps) body.steps = parseInt(steps, 10);
  if (cfg) body.cfg_scale = parseFloat(cfg);
  if (sampler) body.sampler = sampler;
  if (scheduler) body.scheduler = scheduler;

  if (!_isImg2imgMode() && _selectedStyle() !== 'krea' && _isControlNetEnabled()) {
    body.control_net = true;
  }

  const genBtn = pane.querySelector('#is-generate');
  if (genBtn) genBtn.disabled = true;
  _generating = true;
  _genEstMs = (style === 'krea' ? 90000 : 45000);
  _genStartMs = Date.now();
  _setStatus('Starting generation…', '');

  try {
    _startPoll((ok) => {
      if (!ok && _panel()) {
        const btn = _panel()?.querySelector('#is-generate');
        if (btn) btn.disabled = false;
      }
    });
    const result = await _fetchJson('/api/titan/hub/image-execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    _stopPoll();
    _generating = false;
    if (genBtn) genBtn.disabled = false;
    const url = result.image_url || (result.image_urls && result.image_urls[0]);
    const preview = pane.querySelector('#is-preview');
    if (preview && url) {
      preview.innerHTML = `<img src="${esc(url)}" alt="Generated">`;
      _lastOutputUrl = url;
    }
    if (result.seed != null) {
      const seedEl = pane.querySelector('#is-seed');
      if (seedEl) seedEl.value = String(result.seed);
    }
    _setStatus(`Done${result.seed != null ? ` · seed ${result.seed}` : ''}`, 'ok');
  } catch (e) {
    _stopPoll();
    _generating = false;
    if (genBtn) genBtn.disabled = false;
    _setStatus(String(e.message || e), 'err');
  }
}

async function _releaseModel() {
  _stopPoll();
  _generating = false;
  try {
    await _fetchJson('/api/titan/hub/image-studio/release', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
  } catch (_) {}
  _warmedProfile = null;
}

function _buildBodyHTML() {
  return `
    <div class="is-status" id="is-status"></div>
    <div class="is-layout">
      <div class="is-sidebar">
        <div class="is-mode-tabs">
          <button type="button" id="is-mode-txt2img" class="active">txt2img</button>
          <button type="button" id="is-mode-img2img">img2img</button>
        </div>
        <div class="is-field">
          <label for="is-model">Model</label>
          <select id="is-model"></select>
        </div>
        <div class="is-field">
          <label for="is-size">Size</label>
          <select id="is-size"></select>
          <div class="is-size-custom" id="is-size-custom">
            <div class="is-dim-row">
              <div><label for="is-width-range">Width</label><input id="is-width-range" type="range" min="512" max="2048" step="64" value="1024"></div>
              <input id="is-width" type="number" min="64" max="2048" step="64" value="1024">
            </div>
            <div class="is-dim-row">
              <div><label for="is-height-range">Height</label><input id="is-height-range" type="range" min="512" max="2048" step="64" value="1024"></div>
              <input id="is-height" type="number" min="64" max="2048" step="64" value="1024">
            </div>
            <p class="is-size-hint" id="is-size-snapped"></p>
          </div>
          <p class="is-size-hint" id="is-size-hint"></p>
        </div>
        <div class="is-row2">
          <div class="is-field"><label for="is-steps">Steps</label><input id="is-steps" type="number" min="1" max="80" value="28"></div>
          <div class="is-field"><label for="is-cfg">CFG scale</label><input id="is-cfg" type="number" min="0" max="30" step="0.1" value="7"></div>
        </div>
        <div class="is-row2">
          <div class="is-field"><label for="is-sampler">Sampler</label><input id="is-sampler" value="dpm++2m"></div>
          <div class="is-field"><label for="is-scheduler">Scheduler</label><input id="is-scheduler" value="karras"></div>
        </div>
        <div class="is-field">
          <label for="is-seed">Seed (empty = random)</label>
          <input id="is-seed" type="number" placeholder="random">
          <div class="is-seed-lock-row">
            <label for="is-seed-lock">Use seed <span id="is-seed-lock-label">off</span></label>
            <label class="toggle-switch" title="On: reuse seed every generate. Off: random seed each run, field updates after.">
              <input id="is-seed-lock" type="checkbox">
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
        <div class="is-control-net-row" id="is-control-net-row">
          <div>
            <label for="is-control-net">ControlNet <span id="is-control-net-label">off</span></label>
            <p class="is-size-hint" id="is-control-net-hint" style="margin:2px 0 0">Two-pass: layout lock via canny.</p>
          </div>
          <label class="toggle-switch" title="Pass 1 txt2img, pass 2 ControlNet canny from pass 1.">
            <input id="is-control-net" type="checkbox">
            <span class="toggle-slider"></span>
          </label>
        </div>
      </div>
      <div class="is-main">
        <div class="is-init-wrap" id="is-init-wrap">
          <div class="is-field">
            <label>Init image</label>
            <div class="is-init-drop" id="is-init-drop"><span>Drop image, paste, or click to upload</span></div>
            <input type="file" id="is-init-file" accept="image/*" hidden>
            <div class="is-init-actions">
              <button type="button" id="is-init-pick">Upload…</button>
              <button type="button" id="is-init-use-output">Use last output</button>
              <button type="button" id="is-init-clear">Clear</button>
            </div>
            <div class="is-strength-row">
              <label for="is-strength">Denoising strength <span id="is-strength-val">0.55</span></label>
              <input id="is-strength" type="range" min="0.05" max="1" step="0.05" value="0.55">
            </div>
          </div>
        </div>
        <div class="is-field">
          <label for="is-prompt">Prompt</label>
          <textarea id="is-prompt" placeholder="Describe the image…"></textarea>
        </div>
        <div class="is-field" id="is-negative-wrap">
          <label for="is-negative">Negative prompt</label>
          <textarea id="is-negative" placeholder="Optional — anatomy, blur, watermark…"></textarea>
        </div>
        <p class="is-krea-hint" id="is-krea-hint" style="display:none;">KREA: natural-language prose; start vague and regenerate. Write exclusions in the prompt (not negative). Supports film, illustration, VHS grain, 16:9 cinematic, etc.</p>
        <div class="is-actions">
          <button type="button" class="primary" id="is-generate">Generate</button>
          <button type="button" id="is-open-gallery">Open Gallery</button>
        </div>
        <div class="is-preview" id="is-preview">
          <div class="is-preview-empty">Generated image appears here</div>
        </div>
      </div>
    </div>`;
}

function _wireEvents() {
  const pane = _panel();
  if (!pane) return;
  pane.querySelector('#is-mode-txt2img')?.addEventListener('click', () => _setMode('txt2img'));
  pane.querySelector('#is-mode-img2img')?.addEventListener('click', () => _setMode('img2img'));
  pane.querySelector('#is-model')?.addEventListener('change', async (e) => {
    const pid = e.target.value;
    _applyProfileDefaults(pid);
    _syncControlNetUi();
    await _warmModel(pid);
  });
  pane.querySelector('#is-size')?.addEventListener('change', (e) => {
    _toggleCustomSize(e.target.value === SIZE_CUSTOM);
    if (e.target.value !== SIZE_CUSTOM) {
      const parsed = _parseSizeStr(e.target.value);
      if (parsed) {
        pane.querySelector('#is-width').value = parsed[0];
        pane.querySelector('#is-height').value = parsed[1];
        _syncCustomSizeFromInputs();
      }
    }
    _saveDraft();
  });
  for (const id of ['#is-width', '#is-height', '#is-width-range', '#is-height-range']) {
    pane.querySelector(id)?.addEventListener('input', (e) => {
      const isRange = e.target.type === 'range';
      if (isRange) {
        const pair = e.target.id.includes('width') ? '#is-width' : '#is-height';
        pane.querySelector(pair).value = e.target.value;
      } else {
        const pair = e.target.id.includes('width') ? '#is-width-range' : '#is-height-range';
        const sl = pane.querySelector(pair);
        if (sl) sl.value = e.target.value;
      }
      _syncCustomSizeFromInputs();
      _saveDraft();
    });
  }
  pane.querySelector('#is-strength')?.addEventListener('input', (e) => {
    const lab = pane.querySelector('#is-strength-val');
    if (lab) lab.textContent = Number(e.target.value).toFixed(2);
    _saveDraft();
  });
  const drop = pane.querySelector('#is-init-drop');
  const fileIn = pane.querySelector('#is-init-file');
  drop?.addEventListener('click', () => fileIn?.click());
  fileIn?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    if (f) _loadInitFromFile(f);
    e.target.value = '';
  });
  drop?.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
  drop?.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop?.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    const f = e.dataTransfer?.files?.[0];
    if (f) _loadInitFromFile(f);
  });
  pane.querySelector('#is-init-pick')?.addEventListener('click', () => fileIn?.click());
  pane.querySelector('#is-init-use-output')?.addEventListener('click', () => _useLastOutputAsInit());
  pane.querySelector('#is-init-clear')?.addEventListener('click', () => _setInitImage(null, null));
  pane.addEventListener('paste', (e) => {
    if (!_isImg2imgMode()) return;
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (item) {
      e.preventDefault();
      const file = item.getAsFile();
      _loadInitFromFile(file);
    }
  });
  pane.querySelector('#is-generate')?.addEventListener('click', () => _generate());
  pane.querySelector('#is-open-gallery')?.addEventListener('click', () => {
    document.getElementById('tool-gallery-btn')?.click();
  });
  pane.querySelector('#is-prompt')?.addEventListener('input', () => _saveDraft());
  pane.querySelector('#is-negative')?.addEventListener('input', () => _saveDraft());
  pane.querySelector('#is-seed-lock')?.addEventListener('change', () => {
    _syncSeedLockLabel();
    _saveDraft();
  });
  pane.querySelector('#is-control-net')?.addEventListener('change', () => {
    _syncControlNetLabel();
    _saveDraft();
  });
}

function _doClose() {
  if (!_open) return;
  _open = false;
  _saveDraft();
  _stopPoll();
  document.getElementById('tool-image-studio-btn')?.classList.remove('active');
  document.getElementById('rail-image-studio')?.classList.remove('active');
  const overlay = document.getElementById('image-studio-modal');
  if (overlay) overlay.remove();
  Modals.unregister('image-studio-modal');
  _releaseModel().catch(() => {});
}

export function init(apiBase) {
  _apiBase = apiBase || window.API_BASE || '';
  injectStyles();
}

export function isOpen() {
  return _open;
}

export function toggle() {
  if (_open) closePanel();
  else openPanel();
}

export async function openWithRecipe(recipe) {
  if (Modals.isRegistered('image-studio-modal') && Modals.isMinimized('image-studio-modal')) {
    Modals.restore('image-studio-modal');
  }
  if (!_open) {
    _pendingRecipe = recipe || null;
    await openPanel();
    return;
  }
  if (recipe) await _applyRecipe(recipe);
}

export async function openPanel() {
  if (Modals.isRegistered('image-studio-modal') && Modals.isMinimized('image-studio-modal')) {
    Modals.restore('image-studio-modal');
    return;
  }
  if (_open) return;
  _open = true;
  injectStyles();

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'image-studio-modal';
  modal.innerHTML = `
    <div class="modal-content image-studio-modal-content">
      <div class="modal-header">
        <h4>Image Studio</h4>
        <button type="button" class="modal-close" id="image-studio-close" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body" id="image-studio-pane">${_buildBodyHTML()}</div>
    </div>`;
  document.body.appendChild(modal);

  document.getElementById('tool-image-studio-btn')?.classList.add('active');
  document.getElementById('rail-image-studio')?.classList.add('active');

  Modals.register('image-studio-modal', {
    railBtnId: 'rail-image-studio',
    sidebarBtnId: 'tool-image-studio-btn',
    closeFn: () => _doClose(),
    restoreFn: () => {},
  });

  makeWindowDraggable(modal, {
    content: modal.querySelector('.modal-content'),
    header: modal.querySelector('.modal-header'),
  });

  document.getElementById('image-studio-close')?.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (Modals.isRegistered('image-studio-modal')) Modals.close('image-studio-modal');
    else _doClose();
  });

  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      if (Modals.isRegistered('image-studio-modal')) Modals.close('image-studio-modal');
      else _doClose();
    }
  });

  _wireEvents();
  try {
    await _loadConfig();
    const sel = modal.querySelector('#is-model');
    if (sel?.value && !_config?.sd_active && !_pendingRecipe) {
      await _warmModel(sel.value);
    }
    if (_pendingRecipe) {
      const recipe = _pendingRecipe;
      _pendingRecipe = null;
      await _applyRecipe(recipe);
    }
  } catch (e) {
    _setStatus(String(e.message || e), 'err');
  }
}

export function closePanel() {
  if (Modals.isRegistered('image-studio-modal')) {
    Modals.close('image-studio-modal');
    return;
  }
  _doClose();
}

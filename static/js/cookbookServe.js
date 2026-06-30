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
import { openCookbookDependencies } from './cookbook-diagnosis.js';
import { _hwfitCache } from './cookbook-hwfit.js';
import { topPortalZ } from './toolWindowZOrder.js';

// Shared state/functions injected by init()
let _envState;
let _sshCmd;
let _getPort;
let _sshPrefix;
let _serverByVal;
let _serverKey;
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
const SERVE_FAVORITES_KEY = 'cookbook-serve-favorite-models';

let _cachedAllModels = [];

function _loadServeFavorites() {
  try {
    const raw = JSON.parse(localStorage.getItem(SERVE_FAVORITES_KEY) || '[]');
    return new Set(Array.isArray(raw) ? raw.filter(Boolean).map(String) : []);
  } catch {
    return new Set();
  }
}

function _saveServeFavorites(favorites) {
  try {
    localStorage.setItem(SERVE_FAVORITES_KEY, JSON.stringify(Array.from(favorites || [])));
  } catch {}
}

function _redactStoredCommand(value) {
  return String(value || '')
    .replace(/hf_[A-Za-z0-9]{20,}/g, '[redacted-token]')
    .replace(/((?:api[_-]?key|token|authorization|password|passwd|secret)\s*[=:]\s*)(["']?)[^\s"']+/gi, '$1$2[redacted]');
}

function _redactServeStateForStorage(value) {
  if (!value || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(_redactServeStateForStorage);
  const safe = { ...value };
  for (const key of Object.keys(safe)) {
    if (/token|password|passwd|secret|api[_-]?key/i.test(key)) {
      delete safe[key];
    } else if (typeof safe[key] === 'string' && /cmd|command|args|env/i.test(key)) {
      safe[key] = _redactStoredCommand(safe[key]);
    } else if (safe[key] && typeof safe[key] === 'object') {
      safe[key] = _redactServeStateForStorage(safe[key]);
    }
  }
  return safe;
}

function _isServeFavorite(repo) {
  return _loadServeFavorites().has(String(repo || ''));
}

function _toggleServeFavorite(repo) {
  const key = String(repo || '');
  if (!key) return false;
  const favorites = _loadServeFavorites();
  const next = !favorites.has(key);
  if (next) favorites.add(key);
  else favorites.delete(key);
  _saveServeFavorites(favorites);
  return next;
}

function _repoLooksAwqLike(model, repo) {
  const q = String(model?.quant || '').toUpperCase();
  const n = `${repo || ''} ${model?.repo_id || ''} ${model?.name || ''} ${model?.path || ''}`.toLowerCase();
  return /^AWQ|^GPTQ/.test(q) || q === 'FP8' || /\b(awq|gptq|fp8)\b/i.test(n);
}

function _repoLooksGgufLike(model, repo) {
  const q = String(model?.quant || '').toUpperCase();
  const n = `${repo || ''} ${model?.repo_id || ''} ${model?.name || ''} ${model?.path || ''}`.toLowerCase();
  const hasGgufFile = Array.isArray(model?.gguf_files)
    && model.gguf_files.some(f => f && typeof f.rel_path === 'string' && /\.gguf$/i.test(f.rel_path));
  return !!model?.is_gguf || hasGgufFile || /^Q[2-8]/.test(q) || /^IQ/.test(q) || q === 'GGUF' || n.includes('gguf');
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

function _shellSplitForPreview(cmd) {
  const s = String(cmd || '');
  const out = [];
  let cur = '';
  let quote = '';
  let escNext = false;
  for (const ch of s) {
    if (escNext) {
      cur += ch;
      escNext = false;
      continue;
    }
    if (ch === '\\') {
      cur += ch;
      escNext = true;
      continue;
    }
    if (quote) {
      cur += ch;
      if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      cur += ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (cur) {
        out.push(cur);
        cur = '';
      }
      continue;
    }
    cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

function _formatServeCmdPreview(cmd) {
  const raw = String(cmd || '');
  if (raw.startsWith('MODEL_FILE=$({')) {
    const marker = /&&\s+([A-Za-z_][A-Za-z0-9_]*=\S+\s+)*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)?(?:llama-server|python3?\s+-m\s+llama_cpp\.server)\b/;
    const match = raw.match(marker);
    if (match && match.index > 0) {
      const prelude = raw.slice(0, match.index).replace(/\s+/g, ' ').trim();
      const rest = raw.slice(match.index).replace(/^\s*&&\s*/, '');
      return `${prelude}\n&&\n${_formatServeCmdPreview(rest)}`;
    }
  }
  const tokens = _shellSplitForPreview(cmd);
  if (tokens.length <= 4) return String(cmd || '');
  const lines = [];
  let i = 0;
  while (i < tokens.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) {
    lines.push(tokens[i]);
    i++;
  }
  if (tokens[i]) {
    const head = [tokens[i++]];
    if (tokens[i] && !tokens[i].startsWith('--') && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) head.push(tokens[i++]);
    if (tokens[i] && !tokens[i].startsWith('--') && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) head.push(tokens[i++]);
    lines.push(head.join(' '));
  }
  while (i < tokens.length) {
    const t = tokens[i++];
    if (t.startsWith('--')) {
      const vals = [];
      while (i < tokens.length && !tokens[i].startsWith('--') && !/^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) {
        vals.push(tokens[i++]);
      }
      lines.push([t, ...vals].join(' '));
    } else {
      lines.push(t);
    }
  }
  return lines.join('\n');
}

function _normalizeServeCmdForLaunch(cmd) {
  return String(cmd || '')
    .replace(/MODEL_FILE=\$\(\{\s+/g, 'MODEL_FILE=$({ ')
    .replace(/\s+\}\s+\|\s+head\s+-1\)/g, ' } | head -1)')
    .replace(/\s*;\s*/g, '; ')
    .replace(/\s*\|\|\s*/g, ' __ODY_OR__ ')
    .replace(/\s*\|\s*/g, ' | ')
    .replace(/\s+__ODY_OR__\s+/g, ' || ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _modelSizeGb(model, explicitGb = 0) {
  const explicit = Number(explicitGb || 0);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const bytes = Number(model?.size_bytes || 0);
  if (Number.isFinite(bytes) && bytes > 0) return bytes / (1024 ** 3);
  const gb = Number(
    model?.size_gb
    || model?.required_gb
    || model?.vram_needed
    || model?.min_vram_gb
    || model?.recommended_ram_gb
    || model?.min_ram_gb
    || 0
  );
  if (Number.isFinite(gb) && gb > 0) return gb;
  if (_isMiniMaxM3Model(model)) return 240;
  return 0;
}

function _parseParamsB(text) {
  const s = String(text || '');
  const m = s.match(/(\d+(?:\.\d+)?)\s*([bBmMtT])\b/);
  if (!m) return 0;
  const n = parseFloat(m[1]);
  if (!Number.isFinite(n) || n <= 0) return 0;
  const unit = m[2].toLowerCase();
  if (unit === 't') return n * 1000;
  if (unit === 'b') return n;
  if (unit === 'm') return n / 1000;
  return 0;
}

function _knownModelContextMax(model) {
  if (_isMiniMaxM3Model(model)) return 1048576;
  return 0;
}

function _modelIdentityText(model) {
  return [
    model?.repo_id,
    model?.quant_repo,
    model?.name,
    model?.id,
    model?.path,
    model?.model_path,
    model?.served_model_name,
    model?.quant,
    model?.format,
  ].filter(Boolean).join(' ').toLowerCase();
}

function _isMiniMaxM3Model(model) {
  const name = _modelIdentityText(model);
  return (
    (/minimax/.test(name) && /\bm3\b/.test(name))
    || /minimax-m3/.test(name)
    || /models--cyankiwi--minimax-m3-awq-int4/.test(name)
    || /cyankiwi\/minimax-m3-awq-int4/.test(name)
  );
}

function _isMiniMaxM2Model(model) {
  const name = _modelIdentityText(model);
  return /minimax/.test(name) && /\bm2(?:\.\d+)?\b/.test(name);
}

function _modelContextMaxForServe(model, explicitMax) {
  const explicit = Number(explicitMax || 0);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const known = _knownModelContextMax(model);
  if (known > 0) return known;
  for (const key of ['context_length', 'max_position_embeddings', 'n_ctx_train', 'model_max_length', 'max_seq_len']) {
    const value = Number(model?.[key] || 0);
    if (Number.isFinite(value) && value > 0) return value;
  }
  const catalogCtx = Number(model?.context || 0);
  if (Number.isFinite(catalogCtx) && catalogCtx > 0) return catalogCtx;
  return 131072;
}

function _estimateVllmContextFit(model, fields, modelCtxMax, modelWeightsGb = 0, fitSystem = null) {
  const sys = fitSystem || _hwfitCache?.system || {};
  const isMiniMaxM3 = _isMiniMaxM3Model(model);
  const gpuIds = String(fields.gpus || '').split(',').map(s => parseInt(s.trim(), 10)).filter(Number.isFinite);
  const tp = Math.max(1, parseInt(fields.tp, 10) || gpuIds.length || 1);
  const selectedCount = Math.max(1, gpuIds.length || tp);
  const groups = Array.isArray(sys.gpu_groups) ? sys.gpu_groups : [];
  const activeGroup = sys.active_group || groups[0] || null;
  const perGpuGb = Number(activeGroup?.vram_each)
    || (Number(sys.gpu_vram_gb) / Math.max(1, Number(sys.gpu_count) || selectedCount))
    || 0;
  if (!perGpuGb) {
    return { needsHardwareScan: true, reason: 'scan hardware first to estimate context from VRAM' };
  }

  const gpuUtil = Math.min(0.99, Math.max(0.1, parseFloat(fields.gpu_mem) || 0.90));
  const budgetGb = perGpuGb * selectedCount * gpuUtil;
  const modelGb = _modelSizeGb(model, modelWeightsGb);
  if (!modelGb) return { needsModelSize: true, reason: 'model weight size unknown; scan model files or enter context manually' };
  const modelMax = Math.max(1024, _modelContextMaxForServe(model, modelCtxMax));

  if (isMiniMaxM3) {
    const perGpuBudgetGb = perGpuGb * gpuUtil;
    const modelShardGb = modelGb / Math.max(1, tp);
    const fixedOverheadGb = Math.max(1.5, perGpuBudgetGb * 0.035);
    const freeForKv = perGpuBudgetGb - modelShardGb - fixedOverheadGb;
    const kvGbPerToken = (29.25 / 1048576) * (String(fields.vllm_kv_cache_dtype || '').toLowerCase() === 'fp8' ? 1 : 1.8);
    if (freeForKv <= 0) {
      return {
        ctx: 1024,
        budgetGb,
        modelGb,
        kvGbPerToken,
        reason: `model shard ${modelShardGb.toFixed(1)}G exceeds per-GPU usable ${perGpuBudgetGb.toFixed(1)}G before KV`,
      };
    }
    const raw = Math.floor((freeForKv / kvGbPerToken) * 0.99);
    const rounded = Math.max(1024, Math.floor(raw / 128) * 128);
    const ctx = Math.min(modelMax, rounded);
    return {
      ctx,
      budgetGb,
      modelGb,
      kvGbPerToken,
      reason: `~${ctx.toLocaleString()} tokens fits per-GPU KV (${freeForKv.toFixed(1)}G free)`,
    };
  }

  const name = `${model?.repo_id || ''} ${model?.name || ''} ${model?.quant || ''}`;
  const lower = name.toLowerCase();
  const isMoE = /\bmoe\b|a\d+b|minimax|deepseek|mixtral|kimi-k2|glm-4\.5/.test(lower);
  const totalParams = _parseParamsB(name) || Math.max(1, modelGb / 0.58);
  const activeFromName = (() => {
    const m = lower.match(/\ba(\d+(?:\.\d+)?)b\b/);
    return m ? parseFloat(m[1]) : 0;
  })();
  const activeParams = activeFromName || (isMoE ? Math.min(totalParams, 32) : totalParams);
  const effectiveActiveParams = (/minimax/.test(lower) && /\bm3\b/.test(lower)) ? 23 : activeParams;
  const kvDtype = String(fields.vllm_kv_cache_dtype || '').toLowerCase();
  const kvFactor = kvDtype === 'fp8' ? 0.55 : 1;
  const kvGbPerTokenTotal = Math.max(0.00002, 0.000008 * effectiveActiveParams * kvFactor);
  const kvGbPerToken = kvGbPerTokenTotal / Math.max(1, tp);
  const perGpuBudgetGb = perGpuGb * gpuUtil;
  const modelShardGb = modelGb / Math.max(1, tp);
  const fixedOverheadGb = Math.max(1.5, perGpuBudgetGb * 0.035);
  const freeForKv = perGpuBudgetGb - modelShardGb - fixedOverheadGb;
  if (freeForKv <= 0) {
    return {
      ctx: 1024,
      budgetGb,
      modelGb,
      kvGbPerToken,
      reason: `model shard ${modelShardGb.toFixed(1)}G exceeds per-GPU usable ${perGpuBudgetGb.toFixed(1)}G before KV`,
    };
  }
  const raw = Math.floor(freeForKv / kvGbPerToken);
  const rounded = Math.max(1024, Math.floor(raw / 1024) * 1024);
  const ctx = Math.min(modelMax, rounded);
  return {
    ctx,
    budgetGb,
    modelGb,
    kvGbPerToken,
    reason: `~${ctx.toLocaleString()} tokens fits per-GPU KV (${freeForKv.toFixed(1)}G free)`,
  };
}

function _estimateLlamaContextFit(model, fields, modelCtxMax, modelWeightsGb = 0, fitSystem = null, profileData = null) {
  const profiles = Array.isArray(profileData?.profiles) ? profileData.profiles : [];
  const preferred = profiles.find(p => String(p?.key || '').toLowerCase() === 'balanced')
    || profiles.find(p => Number(p?.ctx) > 0)
    || null;
  const modelMax = Math.max(1024, _modelContextMaxForServe(model, modelCtxMax));
  if (preferred && Number(preferred.ctx) > 0) {
    const ctx = Math.min(modelMax, Number(preferred.ctx));
    return {
      ctx,
      reason: `profile ${preferred.label || preferred.key || 'fit'} fits scanned hardware`,
    };
  }

  const sys = fitSystem || _hwfitCache?.system || {};
  const modelGb = _modelSizeGb(model, modelWeightsGb);
  const backend = String(fields.backend || '').toLowerCase();
  const llamaMode = String(fields.llama_mode || '').toLowerCase();
  const isCpuMode = backend === 'llamacpp' && llamaMode === 'cpu';
  const isUnifiedMode = backend === 'llamacpp' && (llamaMode === 'unified' || fields.unified_mem);
  if (!modelGb) {
    return {
      ctx: Math.min(modelMax, 32768),
      needsModelSize: true,
      reason: 'model weight size unknown; using model limit fallback',
    };
  }

  if (isCpuMode) {
    return {
      ctx: Math.min(modelMax, 131072),
      modelGb,
      reason: 'CPU mode uses system RAM; capped to trained limit',
    };
  }

  const gpuIds = String(fields.gpus || '').split(',').map(s => parseInt(s.trim(), 10)).filter(Number.isFinite);
  const selectedCount = Math.max(1, gpuIds.length || parseInt(fields.tp, 10) || 1);
  const groups = Array.isArray(sys.gpu_groups) ? sys.gpu_groups : [];
  const activeGroup = sys.active_group || groups[0] || null;
  const totalVramGb = Number(activeGroup?.vram_each)
    ? Number(activeGroup.vram_each) * selectedCount
    : (Number(sys.gpu_vram_gb) || 0);
  if (!totalVramGb) {
    return {
      ctx: Math.min(modelMax, 32768),
      modelGb,
      needsHardwareScan: true,
      reason: 'scan hardware first; using model limit fallback',
    };
  }

  const totalRamGb = Number(sys.total_ram_gb) || 0;
  const availableRamGb = Number(sys.available_ram_gb) || 0;
  const unifiedPoolGb = isUnifiedMode
    ? Math.max(
        totalVramGb,
        availableRamGb,
        totalRamGb > 0 ? totalRamGb * 0.85 : 0
      )
    : totalVramGb;
  const usableGb = isUnifiedMode
    ? Math.max(1, unifiedPoolGb - Math.max(2.0, unifiedPoolGb * 0.08))
    : Math.max(1, totalVramGb - Math.max(1.0, selectedCount * 0.6));
  const freeForKv = usableGb - modelGb;
  const kv = String(fields.cache_type || '').toLowerCase();
  const kvFactor = kv === 'q4_0' ? 0.55 : (kv === 'q8_0' ? 1 : (kv === 'f16' ? 1.9 : 1));
  const kvGbPerToken = Math.max(0.00008, (modelGb / 7.5) * 0.0007 * kvFactor);
  if (freeForKv <= 0) {
    return {
      ctx: Math.min(modelMax, 8192),
      modelGb,
      kvGbPerToken,
      reason: `model ${modelGb.toFixed(1)}G exceeds usable ${isUnifiedMode ? 'unified memory' : 'VRAM'} ${usableGb.toFixed(1)}G before KV`,
    };
  }
  const raw = Math.floor(freeForKv / kvGbPerToken);
  const rounded = Math.max(1024, Math.floor(raw / 1024) * 1024);
  const ctx = Math.min(modelMax, rounded);
  return {
    ctx,
    modelGb,
    kvGbPerToken,
    reason: `~${ctx.toLocaleString()} tokens fits llama.cpp KV (${freeForKv.toFixed(1)}G free ${isUnifiedMode ? 'unified' : 'VRAM'})`,
  };
}

function _selectedServeTarget(panel) {
  const select = panel?.querySelector?.('#hwfit-server-select')
    || document.getElementById('hwfit-server-select')
    || document.getElementById('hwfit-dl-server');
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
    serverKey: server ? (_serverKey?.(server) || '') : (select?.value || ''),
    serverName: server?.name || '',
    env: server?.env || '',
    port: host ? (server?.port || _getPort(host) || '') : '',
    venv,
    platform: host ? (server?.platform || '') : (_envState.hostPlatform || ''),
    label,
  };
}

function _remoteWindowsDiffusersUnsupported(target) {
  return !!(target?.host && target?.platform === 'windows');
}

function _backendChoicesForTarget(target) {
  if (target?.platform === 'windows') {
    if (_remoteWindowsDiffusersUnsupported(target)) return [['llamacpp','llama.cpp']];
    return [['llamacpp','llama.cpp'],['diffusers','Diffusers']];
  }
  return _isMetal()
    ? [['llamacpp','llama.cpp'],['ollama','Ollama']]
    : [['vllm','vLLM'],['sglang','SGLang'],['llamacpp','llama.cpp'],['ollama','Ollama'],['diffusers','Diffusers']];
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
  if (pkg.installed === null || pkg.probe_error) {
    return note ? `${label} readiness unavailable for ${target.label}: ${note}` : `${label} readiness unavailable for ${target.label}.`;
  }
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

function _selectedGgufSizeGb(model, relPath) {
  const file = _runnableGgufFiles(model).find(f => f.rel_path === relPath);
  const bytes = Number(file?.size_bytes || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return 0;
  return bytes / (1024 ** 3);
}

function _projectorGgufFiles(model) {
  return _ggufFilesForModel(model)
    .filter(f => (f.role || '') === 'projector' || /(^|\/)mmproj[^/]*\.gguf$/i.test(f.rel_path || f.name || ''))
    .sort((a, b) => String(a.rel_path || a.name || '').localeCompare(String(b.rel_path || b.name || '')));
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

function _ggufTaskDisplayPart(model, relPath) {
  const rel = String(relPath || '');
  if (!rel) return '';
  const file = _ggufFilesForModel(model).find(f => f.rel_path === rel);
  if (file?.quant) return String(file.quant).toUpperCase().replace(/^UD-/, '');
  const parts = rel.split('/').filter(Boolean);
  const base = parts[parts.length - 1] || '';
  const parent = parts.length > 1 ? parts[parts.length - 2] : '';
  const text = `${parent} ${base}`;
  const quant = text.match(/\b(?:UD-)?(?:IQ[1-8]_[A-Z0-9]+|Q[2-8]_K_[MLS]|Q[2-8]_[0-9A-Z]+|Q[2-8])\b/i);
  if (quant) return quant[0].toUpperCase().replace(/^UD-/, '');
  return base.replace(/\.gguf$/i, '').replace(/-\d{5}-of-\d{5}$/i, '');
}

function _serveTaskDisplayName(shortName, model, fields) {
  const name = String(shortName || '').trim();
  const backend = String(fields?.backend || '').toLowerCase();
  if (backend !== 'llamacpp' && backend !== 'ollama') return name;
  const part = _ggufTaskDisplayPart(model, fields?.gguf_file);
  return part && !name.includes(` · ${part}`) ? `${name} · ${part}` : name;
}

function _safeGgufRelPath(relPath) {
  const rel = String(relPath || '').replace(/\\/g, '/').replace(/^\/+/, '');
  if (!rel || rel.startsWith('../') || rel.includes('/../') || rel === '..') return '';
  if (rel.includes('\0')) return '';
  return rel;
}

function _ggufDeleteChoice(repo, files) {
  return new Promise(resolve => {
    let overlay = document.getElementById('cookbook-gguf-delete-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'cookbook-gguf-delete-overlay';
      overlay.className = 'modal hidden';
      overlay.innerHTML =
        '<div class="modal-content styled-confirm-box cookbook-gguf-delete-box" role="dialog" aria-modal="true" aria-labelledby="cookbook-gguf-delete-title">' +
          '<div class="modal-header"><h4 id="cookbook-gguf-delete-title">Delete GGUF files</h4></div>' +
          '<div class="modal-body">' +
            '<p id="cookbook-gguf-delete-msg"></p>' +
            '<div id="cookbook-gguf-delete-list" class="cookbook-gguf-delete-list"></div>' +
          '</div>' +
          '<div class="modal-footer cookbook-gguf-delete-actions">' +
            '<button type="button" id="cookbook-gguf-delete-cancel" class="confirm-btn confirm-btn-secondary">Cancel</button>' +
            '<button type="button" id="cookbook-gguf-delete-repo" class="confirm-btn confirm-btn-secondary">Whole repo</button>' +
            '<button type="button" id="cookbook-gguf-delete-selected" class="confirm-btn confirm-btn-danger">Delete selected</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }

    const safeFiles = files
      .map(f => ({ ...f, rel_path: _safeGgufRelPath(f.rel_path) }))
      .filter(f => f.rel_path);
    const msg = overlay.querySelector('#cookbook-gguf-delete-msg');
    const list = overlay.querySelector('#cookbook-gguf-delete-list');
    const cancelBtn = overlay.querySelector('#cookbook-gguf-delete-cancel');
    const repoBtn = overlay.querySelector('#cookbook-gguf-delete-repo');
    const selectedBtn = overlay.querySelector('#cookbook-gguf-delete-selected');
    const prevFocus = document.activeElement;

    msg.textContent = `${repo} has multiple GGUF files. Pick what to delete.`;
    list.innerHTML = safeFiles.map((file, idx) => {
      const label = esc ? esc(_ggufFileLabel(file)) : _ggufFileLabel(file);
      const rel = esc ? esc(file.rel_path) : file.rel_path;
      return `<label class="cookbook-gguf-delete-row">
        <input class="cookbook-gguf-delete-cb" type="checkbox" value="${idx}">
        <span class="cookbook-gguf-delete-main">${label}</span>
        <span class="cookbook-gguf-delete-path">${rel}</span>
      </label>`;
    }).join('');

    function cleanup(result) {
      overlay.classList.add('hidden');
      overlay.style.display = 'none';
      cancelBtn.removeEventListener('click', onCancel);
      repoBtn.removeEventListener('click', onRepo);
      selectedBtn.removeEventListener('click', onSelected);
      overlay.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKey);
      try { prevFocus && prevFocus.focus && prevFocus.focus(); } catch {}
      resolve(result);
    }
    function onCancel() { cleanup(null); }
    function onRepo() { cleanup({ mode: 'repo' }); }
    function onSelected() {
      const selected = [...list.querySelectorAll('input[type="checkbox"]:checked')]
        .map(input => safeFiles[Number(input.value)])
        .filter(Boolean);
      if (!selected.length) {
        uiModule.showToast?.('Select at least one GGUF file.');
        return;
      }
      cleanup({ mode: 'files', files: selected });
    }
    function onBackdrop(e) { if (e.target === overlay) cleanup(null); }
    function onKey(e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        cleanup(null);
      }
    }

    cancelBtn.addEventListener('click', onCancel);
    repoBtn.addEventListener('click', onRepo);
    selectedBtn.addEventListener('click', onSelected);
    overlay.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKey);
    overlay.classList.remove('hidden');
    overlay.style.display = '';
    selectedBtn.focus();
  });
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
  const favorites = _loadServeFavorites();
  allModels.sort((a, b) => {
    const af = favorites.has(String(a.repo_id || '')) ? 1 : 0;
    const bf = favorites.has(String(b.repo_id || '')) ? 1 : 0;
    return bf - af;
  });

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
    const _isFavorite = favorites.has(String(m.repo_id || ''));
    const isSelectMode = document.getElementById('hwfit-cache-select')?.classList.contains('active');
    html += `<div class="doclib-card memory-item${_isFavorite ? ' memory-pinned cookbook-serve-favorite-model' : ''}" data-repo="${esc(m.repo_id)}" data-tag="${m._tag || ''}" data-family="${m._family || ''}" style="cursor:pointer;">`;
    html += `<span class="serve-select-cb memory-select-dot" style="display:${isSelectMode ? 'inline-block' : 'none'};cursor:pointer;"></span>`;
    html += `<div style="flex:1;min-width:0;">`;
    const _mc = modelColor(m.repo_id) || '';
    const _runningPill = _isActivelyServing(m.repo_id)
      ? ` <span class="cookbook-serve-running-pill is-clickable" title="This model is currently being served — click to open in Running" data-repo="${esc(m.repo_id)}" role="button" tabindex="0">running</span>`
      : '';
    const _downloadingPill = _isDownloading
      ? ` <span class="cookbook-serve-downloading-pill${_isDlActive ? '' : ' is-stalled'}" title="${_isDlActive ? 'Download in progress' : 'Download stalled — retry to resume'}">${_isDlActive ? 'downloading' : 'stalled'}</span>`
      : '';
    const _favoritePill = _isFavorite ? ' <span class="memory-cat-badge memory-cat-pinned cookbook-serve-fav-badge">pinned</span>' : '';
    html += `<div class="memory-item-title cookbook-serve-title"${_mc ? ` style="color:${_mc}"` : ''}><span class="cookbook-serve-title-name">${modelLogo(m.repo_id)}${esc(shortName)}</span>${_favoritePill}${hfLink ? ` <a href="${esc(hfLink)}" target="_blank" rel="noopener" class="cookbook-hf-link">HF ↗</a>` : ''}${_runningPill}${_downloadingPill}</div>`;
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
      const _favNow = _isServeFavorite(repo);
      const _favIco = _favNow
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
      const items = [];
      items.push({ label: _favNow ? 'Unfavorite' : 'Favorite', icon: _favIco, action: 'favorite' });
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
          else if (opt.action === 'favorite') {
            const favored = _toggleServeFavorite(repo);
            uiModule.showToast(favored ? 'Favorited — pinned to top' : 'Unfavorited');
            _rerenderCachedModels();
          }
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
      dropdown.style.cssText = `position:fixed;z-index:${topPortalZ()};visibility:hidden;top:0;right:${window.innerWidth-rect.right}px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,0.3);font-size:12px;`;
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
        item.style.maxHeight = '';
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
        c.style.maxHeight = '';
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
      const _modelSs = (_byRepo[repo] && typeof _byRepo[repo] === 'object') ? _byRepo[repo] : null;
      const _repoForcedBackend = !!(_modelSs && _modelSs._forceBackend);
      const _isMiniMaxM3 = _isMiniMaxM3Model({ ...m, repo_id: repo });
      const _isMiniMaxM2 = _isMiniMaxM2Model({ ...m, repo_id: repo });
      const _isMiniMaxMSeries = _isMiniMaxM3 || _isMiniMaxM2;
      const _toolParserDefault = _detectToolParser(repo);
      const _isStepFunStep = _toolParserDefault === 'step3p5';
      const _nativeToolDefault = _isMiniMaxMSeries || _isStepFunStep;
      const _reasoningDefault = _isMiniMaxMSeries || _isStepFunStep;
      const _expertParallelDefault = _isMiniMaxMSeries || _isStepFunStep;
      const svm = (k, def) => (_modelSs && _hasOwn(_modelSs, k)) ? _modelSs[k] : def;
      const _serveTarget = _selectedServeTarget();
      const _backendChoices = _backendChoicesForTarget(_serveTarget);
      const _allowedBackends = new Set(_backendChoices.map(([v]) => v));
      const detectedBackend = _detectBackend(m).backend;
      let defaultBackend = (_repoForcedBackend && ss.backend && _allowedBackends.has(ss.backend))
        ? ss.backend
        : detectedBackend;
      if (!_allowedBackends.has(defaultBackend)) defaultBackend = _backendChoices[0]?.[0] || detectedBackend;
      const savedMatchesBackend = _repoForcedBackend || (ss.backend || 'vllm') === detectedBackend;
      const sv = (k, def) => (ss[k] !== undefined && savedMatchesBackend) ? ss[k] : def;
      const defaultTp = defaultBackend === 'llamacpp' ? '1' : sv('tp', _isMiniMaxMSeries ? '8' : '1');
      const detectedGpuIds = _allGpuIds(_getGpuToggleTotal?.());
      const defaultGpus = defaultBackend === 'llamacpp'
        ? '0'
        : (savedMatchesBackend && _hasOwn(ss, 'gpus') && String(ss.gpus || '').trim()
          ? ss.gpus
          : (_es.gpus || detectedGpuIds));
      const tpOpts = [1,2,4,8].map(n => `<option${defaultTp==String(n)?' selected':''}>${n}</option>`).join('');
      const dtypeOpts = ['auto','float16','bfloat16'].map(d => `<option value="${d}"${sv('dtype','auto')===d?' selected':''}>${d}</option>`).join('');
      // KV cache default — most models are fine on auto, but a few
      // (e.g. DeepSeek-V3/V4/R1 MoE) need fp8 explicitly or the launch
      // OOMs. _detectModelOptimizations seeds opts.kvCacheDtype for
      // those families; honour it unless the user has a saved override.
      const _kvOptsCheck = _detectModelOptimizations(repo);
      const _kvAutoDefault = (_kvOptsCheck && _kvOptsCheck.kvCacheDtype) || (_isMiniMaxMSeries ? 'fp8' : 'auto');
      const _kvSelected = sv('vllm_kv_cache_dtype', _kvAutoDefault);
      const vllmKvCacheOpts = ['auto','fp8'].map(d => `<option value="${d}"${_kvSelected===d?' selected':''}>${d}</option>`).join('');
      const _l = (name, tip) => `<span>${name}<span class="hwfit-hint" title="${tip}">?</span></span>`;
      const _ggufChoices = _runnableGgufFiles(m);
      const _savedGguf = String(sv('gguf_file', '') || '');
      const _preferredGgufInclude = String(sv('_preferredGgufInclude', '') || '').replace(/\*/g, '').toLowerCase();
      const _preferredGguf = _preferredGgufInclude
        ? (_ggufChoices.find(f => String(f.rel_path || '').toLowerCase().includes(_preferredGgufInclude))
          || _ggufChoices.find(f => String(f.name || '').toLowerCase().includes(_preferredGgufInclude)))
        : null;
      const _defaultGguf = _ggufChoices.some(f => f.rel_path === _savedGguf)
        ? _savedGguf
        : (_preferredGguf?.rel_path || '')
          ? _preferredGguf.rel_path
        : (_ggufChoices[0]?.rel_path || '');
      const _ggufOptions = _ggufChoices.map(f =>
        `<option value="${esc(f.rel_path)}"${f.rel_path === _defaultGguf ? ' selected' : ''}>${esc(_ggufFileLabel(f))}</option>`
      ).join('');
      const _minimaxM3Snapshot = '/home/pewds/.cache/huggingface/hub/models--cyankiwi--MiniMax-M3-AWQ-INT4/snapshots/4082acbbec1236d21828d55b6bb0fe02ade4ab5b';
      const _defaultServeModel = _isMiniMaxM3 ? _minimaxM3Snapshot : (m.is_local_dir && m.path ? `${m.path}/${repo}` : repo);
      const _savedModelPath = String(svm('model_path', _defaultServeModel) || '').trim();
      const _modelPathValue = _isMiniMaxM3 && (!_savedModelPath || _savedModelPath === repo) ? _minimaxM3Snapshot : _savedModelPath;
      const _defaultServedModelName = _isMiniMaxM3 ? repo : '';
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
      let _slotsHtml = `<div class="cookbook-serve-slots cookbook-saved-split" title="Saved launch configurations for this model — click ▾ to load or delete">`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-save" title="Save current preset"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Preset</button>`
        + `<button type="button" class="cookbook-slot-btn cookbook-saved-arrow" title="${esc(_arrowTitle)}">${_arrowLabel}</button>`
        + `</div>`;

      let panelHtml = `<div class="hwfit-serve-panel">`;
      const _replaceTaskId = String(sv('_replaceTaskId', '') || '');
      if (_replaceTaskId) {
        panelHtml += `<input type="hidden" class="hwfit-sf" data-field="_replaceTaskId" value="${esc(_replaceTaskId)}" />`;
      }
      // Runtime-readiness note pinned at the top of the serve area so the
      // user sees "vLLM ready on …" before scrolling into the configure
      // form. Hidden until the readiness probe returns. The × button
      // dismisses it for this panel only (re-shows on re-expand).
      panelHtml += `<div class="hwfit-serve-runtime-note" style="display:none;font-size:11px;line-height:1.35;color:var(--fg-muted);margin:0 0 8px;padding:6px 28px 6px 10px;border-radius:5px;background:color-mix(in srgb, var(--fg) 4%, transparent);border:1px solid color-mix(in srgb, var(--border) 60%, transparent);position:relative;"><span class="hwfit-serve-runtime-text"></span><button type="button" class="hwfit-serve-runtime-close" title="Dismiss" aria-label="Dismiss" style="position:absolute;top:-8px;right:5px;background:none;border:0;color:inherit;cursor:pointer;padding:2px 4px;line-height:1;font-size:13px;opacity:0.6;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>`;
      // Warn when serving a model whose download hasn't fully completed —
      // the user CAN still hit Launch (vLLM/llama-server will start, then
      // crash trying to read missing shards), but they should know.
      if (m && (m.status === 'downloading' || m.status === 'stalled' || m.has_incomplete)) {
        const _warnText = m.status === 'stalled'
          ? `This model looks like a stale download shell (${esc(m.size || '0 KB')}). The weights aren't on disk — the serve will fail to load. Re-download first, or pick another model.`
          : `This model's download isn't complete yet (${esc(m.size || 'partial')}). The serve will start but is likely to crash on a missing shard. Wait for the download to finish, or relaunch after it's done.`;
        panelHtml += `<div class="hwfit-serve-warn" style="margin:0 0 8px;padding:6px 10px;border-radius:5px;font-size:11px;background:color-mix(in srgb, var(--color-warning, #f0ad4e) 14%, transparent);border:1px solid color-mix(in srgb, var(--color-warning, #f0ad4e) 40%, transparent);color:var(--color-warning, #f0ad4e);display:flex;gap:6px;align-items:flex-start;line-height:1.4;"><span aria-hidden="true">⚠</span><span>${_warnText}</span></div>`;
      }
      panelHtml += `<div class="hwfit-serve-preset-row">${_slotsHtml}</div>`;
      panelHtml += `<div class="hwfit-serve-vision-warn" style="display:none;margin:0 0 8px;padding:6px 10px;border-radius:5px;font-size:11px;background:color-mix(in srgb, var(--color-warning, #f0ad4e) 14%, transparent);border:1px solid color-mix(in srgb, var(--color-warning, #f0ad4e) 40%, transparent);color:var(--color-warning, #f0ad4e);gap:6px;align-items:flex-start;line-height:1.4;"><span aria-hidden="true">⚠</span><span>Vision is enabled, but no mmproj GGUF projector was found in the cached model scan. Download an mmproj-*.gguf for this model, then refresh the cached model list before launching.</span></div>`;
      // Row 1: Engine + Server + Env
      panelHtml += `<div class="hwfit-serve-row">`;
      const backendOpts = _backendChoices.map(([v,l]) => `<option value="${v}"${defaultBackend===v?' selected':''}>${l}</option>`).join('');
      // Custom Backend picker — native <select> can't host SVG inside
      // options, so we render a button + menu that show the backend logo
      // beside its name. The hidden <select.hwfit-sf data-field="backend">
      // stays as the source-of-truth so every existing change handler
      // (updateBackendVisibility, runtime readiness, command builder)
      // still fires via dispatchEvent('change') on selection.
      panelHtml += `<label>${_l('Engine','Inference engine: vLLM, SGLang, llama.cpp, Ollama, or Diffusers')}<div class="hwfit-backend-picker" data-backend-picker style="position:relative;width:100%;"><select class="hwfit-sf hwfit-backend-source" data-field="backend" style="display:none;">${backendOpts}</select><button type="button" class="hwfit-backend-btn" data-backend-btn aria-haspopup="listbox" aria-expanded="false" style="display:flex;align-items:center;gap:6px;width:100%;height:28px;padding:0 8px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;font:inherit;font-size:11px;cursor:pointer;text-align:left;position:relative;top:-3px;"><span class="hwfit-backend-btn-icon" data-backend-icon-slot aria-hidden="true" style="display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;color:var(--accent, var(--red));flex-shrink:0;"></span><span class="hwfit-backend-btn-label" data-backend-label style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"></span><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="opacity:0.6;flex-shrink:0;"><polyline points="6 9 12 15 18 9"/></svg></button><div class="hwfit-backend-menu" data-backend-menu role="listbox" hidden style="position:absolute;top:calc(100% + 4px);left:0;right:0;z-index:100;background:var(--panel, var(--bg));border:1px solid var(--border);border-radius:6px;box-shadow:0 6px 20px rgba(0,0,0,0.22);padding:4px;"></div></div></label>`;
      panelHtml += `<input type="hidden" class="hwfit-sf" data-field="host" value="${esc(_es.remoteHost || '')}" />`;
      // Inference mode pill (llama.cpp only) — lives directly to the
      // RIGHT of Backend in Row 1 so the engine and the GPU/CPU choice
      // are read together. .hwfit-backend-llamacpp visibility class
      // hides it when the user switches to vLLM/SGLang/Ollama.
      {
        // Default CPU — works on every host without GPU/wheel matching
        // hassle. User picks GPU explicitly if they have the right setup
        // (avoids "click Launch → silent CPU fallback because the wheel
        // is CPU-only" surprises that ate hours of debugging).
        // Layout: CPU on left, GPU on right → mode-right triggers when
        // GPU is selected so the sliding pill animates rightward.
        // Default to GPU mode when hwfit detected a GPU backend on the
        // current target — CPU as a global default sent the user down a
        // 35GB-model-on-CPU rabbit hole (-ngl 0, no flash-attn, no GPU
        // offload). Falls back to CPU only when hwfit detected no GPU
        // (cpu_x86 / generic / unscanned) or the cache is stale.
        const _hwBackend = String(_hwfitCache?.system?.backend || '').toLowerCase();
        const _hwScanMatch = String(_hwfitCache?._scannedHost || '') === String(_envState.remoteHost || '');
        const _llamaModeDefault = (_hwScanMatch && ['cuda', 'rocm', 'vulkan', 'metal', 'mps', 'apple'].includes(_hwBackend)) ? 'gpu' : 'cpu';
        const _savedUnified = !!sv('unified_mem', false);
        const _llamaModeRaw = sv('llama_mode', _llamaModeDefault);
        const _llamaMode = _savedUnified && _llamaModeRaw !== 'cpu' ? 'unified' : _llamaModeRaw;
        panelHtml += `<label class="hwfit-backend-llamacpp">${_l('Inference','CPU = -ngl 0. GPU = -ngl 99. Unified = GPU offload plus GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 for unified-memory CUDA systems.')}<div class="mode-toggle mode-toggle-three${_llamaMode === 'gpu' ? ' mode-mid' : (_llamaMode === 'unified' ? ' mode-third' : '')}" data-llama-mode-toggle style="display:flex;width:100%;height:30px;position:relative;top:2px;"><button type="button" class="mode-toggle-btn${_llamaMode === 'cpu' ? ' active' : ''}" data-llama-mode="cpu" aria-pressed="${_llamaMode === 'cpu'}" style="flex:1;"><span style="position:relative;top:-7px;">CPU</span></button><button type="button" class="mode-toggle-btn${_llamaMode === 'gpu' ? ' active' : ''}" data-llama-mode="gpu" aria-pressed="${_llamaMode === 'gpu'}" style="flex:1;"><span style="position:relative;top:-7px;">GPU</span></button><button type="button" class="mode-toggle-btn${_llamaMode === 'unified' ? ' active' : ''}" data-llama-mode="unified" aria-pressed="${_llamaMode === 'unified'}" style="flex:1;"><span style="position:relative;top:-7px;">Unified</span></button></div><input type="hidden" class="hwfit-sf" data-field="llama_mode" value="${esc(_llamaMode)}" /><input type="hidden" class="hwfit-sf" data-field="unified_mem" value="${_llamaMode === 'unified' ? '1' : ''}" /></label>`;
      }
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
      // GPUs button strip moved to Row 2 (next to GPU Mem) below. 4px
      // margin on the left, 8px on the right — extra 4px right-side gap
      // separates the GPU chiclets from the GPU Mem field that follows
      // (asked-for breathing room; 4px on either side felt cramped on
      // the GPU-Mem boundary).
      const _gpusLabelHtml = `<label class="hwfit-gpus-label cookbook-llama-gpu-only" style="margin:0 8px 0 4px;">${_l('GPUs','Toggle which GPUs to use')}<div class="cookbook-gpu-group">${_gpuBtnsHtml}</div><input type="hidden" class="hwfit-sf" data-field="gpus" value="${esc(defaultGpus)}" /></label>`;
      panelHtml += _gpusLabelHtml;
      panelHtml += `</div>`;
      // (hwfit-serve-runtime-note moved to the top of the panel — see above.)
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
      panelHtml += `<div class="hwfit-serve-row hwfit-serve-row-core hwfit-backend-vllm hwfit-backend-sglang hwfit-backend-llamacpp hwfit-backend-ollama">`;
      // Order: Dtype → TP → Context → Max Seqs → GPUs → GPU Mem.
      // Dtype moved down from Row 1 to make space for the Inference pill
      // (llama.cpp GPU/CPU toggle, llamacpp-only). GPUs lives next to
      // GPU Mem so "which devices + how much" sit adjacent. Max Seqs
      // follows Context per the "request-shape" cluster.
      panelHtml += `<label>${_l('Dtype','Data type for weights. auto picks best for GPU')}<select class="hwfit-sf" data-field="dtype">${dtypeOpts}</select></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('TP','Tensor Parallelism — split model across N GPUs')}<select class="hwfit-sf" data-field="tp">${tpOpts}</select></label>`;
      // ctx resets to the model's max on every panel open (the real ctx slider
      // lives in the Scan/Download toolbar — see cookbook.js .hwfit-ctx-control).
      const _knownCtxDefault = _knownModelContextMax({ ...m, repo_id: repo });
      const _ctxDefault = _knownCtxDefault ? String(_knownCtxDefault) : (m.context_length || m.context || '20000');
      const _ctxSavedValue = sv('ctx', _ctxDefault);
      const _ctxValue = _isMiniMaxMSeries && ['20000', '32768'].includes(String(_ctxSavedValue)) ? _ctxDefault : _ctxSavedValue;
      panelHtml += `<label class="hwfit-context-label">${_l('Context','Max tokens per request. Calculate suggests a value from model limit + selected GPU VRAM; edit manually to override.')}<span class="hwfit-context-control"><input type="text" class="hwfit-sf" data-field="ctx" value="${esc(_ctxValue)}" /><button type="button" class="hwfit-context-calc-btn cookbook-btn" title="Calculate and use suggested context from scanned hardware">Auto</button></span><span class="hwfit-auto-ctx-note"></span></label>`;
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('Max Seqs','Maximum concurrent requests. Lower = less memory. Default 4 — prosumer GPUs often OOM on vLLM default 256 during CUDA graph capture.')}<input type="text" class="hwfit-sf" data-field="max_seqs" value="${esc(sv('max_seqs', '4'))}" placeholder="4" /></label>`;
      // GPU "auto" field removed — the GPU button strip below already
      // writes data-field="gpus" (the canonical comma-separated device
      // list) and the command builders now read from that single source.
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang">${_l('GPU Mem','Fraction of GPU memory (0.0–1.0). Lower if OOM')}<input type="text" class="hwfit-sf" data-field="gpu_mem" value="${esc(sv('gpu_mem', _isMiniMaxMSeries ? '0.95' : '0.90'))}" /></label>`;
      panelHtml += `</div>`;
      // ── Advanced (collapsed by default) ──
      // Everything below the fold is tuning users only touch occasionally:
      // vLLM kernel/env knobs, llama.cpp fit/cache/split controls, the
      // GGUF batch sizes, the speculative-decoding row, and the live VRAM
      // monitor. Wrapped in a native <details> so toggle state survives
      // re-renders cheaply and a closed fold doesn't trigger any layout
      // work for the dozens of nested inputs.
      panelHtml += `<details class="hwfit-serve-advanced"${_isMiniMaxM3 ? ' open' : ''}>`;
      panelHtml += `<summary class="hwfit-serve-advanced-summary">Advanced</summary>`;
      // Advanced vLLM/SGLang row (KV Cache, Attention, Swap, Env)
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-vllm hwfit-backend-sglang">`;
      panelHtml += `<label class="hwfit-backend-vllm" style="grid-column:1 / -1;">${_l('Served Name','vLLM --served-model-name. Keeps the OpenAI model id stable when serving from a local snapshot path.')}<input type="text" class="hwfit-sf" data-field="served_model_name" value="${esc(svm('served_model_name', _defaultServedModelName))}" placeholder="${esc(repo)}" style="width:100%;" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm" style="grid-column:1 / -1;">${_l('Model Path','Argument passed after `vllm serve`. MiniMax M3 auto-fills the cached snapshot path because the nightly runtime needs the local repo files.')}<input type="text" class="hwfit-sf" data-field="model_path" value="${esc(_modelPathValue)}" placeholder="${esc(repo)}" style="width:100%;" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('KV Cache','vLLM --kv-cache-dtype. auto uses the model/runtime default; fp8 reduces KV memory for long context.')}<select class="hwfit-sf" data-field="vllm_kv_cache_dtype" style="height:32px;">${vllmKvCacheOpts}</select></label>`;
      // Attention backend selector — pin the kernel impl. Default `auto` lets
      // vLLM pick FlashInfer (which JITs on first use and breaks on older
      // system nvcc) → FlashAttention → xformers. Forcing FLASH_ATTN skips
      // the JIT entirely, fixing the `nvcc fatal: Unsupported gpu
      // architecture 'compute_89'` failure mode on Ada / Hopper hosts.
      const _attnDefault = _isMiniMaxMSeries ? 'TRITON_ATTN' : '';
      const _attnSelected = _isMiniMaxMSeries
        ? (String(svm('vllm_attn_backend', '') || '').trim() || _attnDefault)
        : svm('vllm_attn_backend', _attnDefault);
      const vllmAttnBackendOpts = ['auto', 'TRITON_ATTN', 'FLASH_ATTN', 'XFORMERS', 'FLASHINFER', 'TORCH_SDPA']
        .map(b => `<option value="${b === 'auto' ? '' : b}"${(_attnSelected === (b === 'auto' ? '' : b)) ? ' selected' : ''}>${b}</option>`).join('');
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Attention','vLLM VLLM_ATTENTION_BACKEND. auto = vLLM picks (often FLASHINFER, which JITs and can fail on old nvcc). FLASH_ATTN skips the JIT entirely.')}<select class="hwfit-sf" data-field="vllm_attn_backend" style="height:32px;">${vllmAttnBackendOpts}</select></label>`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Block Size','vLLM --block-size. Controls KV-cache block granularity. Leave blank for runtime default; some sparse-attention or custom runtimes need a specific value.')}<input type="text" class="hwfit-sf" data-field="vllm_block_size" value="${esc(svm('vllm_block_size', _isMiniMaxM3 ? '128' : ''))}" placeholder="auto" /></label>`;
      panelHtml += `<label class="hwfit-backend-vllm">${_l('Swap','vLLM CPU swap space in GB. Blank/off omits the flag; enter a positive number only for older vLLM runtimes that support --swap-space.')}<input type="text" class="hwfit-sf" data-field="swap" value="${esc(sv('swap', ''))}" placeholder="off" /></label>`;
      {
        const _envPresetDefault = _isMiniMaxM3 ? 'minimax_m3_cuda' : '';
        const _envPresetVal = svm('vllm_env_preset', _envPresetDefault);
        const _envPresetOpts = [
          ['', 'None'],
          ['minimax_m3_cuda', 'CUDA native sampler'],
        ].map(([v, label]) => `<option value="${v}"${_envPresetVal === v ? ' selected' : ''}>${label}</option>`).join('');
        panelHtml += `<label class="hwfit-backend-vllm" style="grid-column:1 / 2;">${_l('Env Preset','Adds known-good environment variables without typing them. CUDA native sampler adds VLLM_TARGET_DEVICE=cuda and disables FlashInfer sampler JIT; useful when system nvcc cannot compile the sampler for the GPU architecture.')}<select class="hwfit-sf" data-field="vllm_env_preset" style="height:32px;">${_envPresetOpts}</select></label>`;
      }
      // Free-text env-vars field. Anything pasted here is prepended to the
      // launch command verbatim. Use for CUDACXX, PATH overrides, NCCL_*
      // tuning, or any other KEY=VALUE pair that doesn't have a dedicated
      // field. After the venv activate runs, $VIRTUAL_ENV / $PATH / etc. are
      // already exported so they expand correctly here.
      // grid-column: 1 / -1 makes Env span every column of the Advanced
      // row's CSS grid (the old flex:1 1 100% did nothing in a grid
      // container — left an empty trailing column gap on wide modals).
      panelHtml += `<label class="hwfit-backend-vllm hwfit-backend-sglang" style="grid-column:2 / -1;">${_l('Env','Extra KEY=VALUE env-var pairs prepended to the launch (space-separated). The Env Preset above covers the usual MiniMax M3 values; use this for additional overrides.')}<input type="text" class="hwfit-sf" data-field="extra_env" value="${esc(svm('extra_env', sv('extra_env','')))}" placeholder="NCCL_P2P_DISABLE=1" style="width:100%;" /></label>`;
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
      // Order: Trust Remote → Auto Tool → Reasoning Parser (when the
      // model has one) → Enforce Eager → Prefix Caching. Reasoning
      // Parser was previously in a separate row below; the user wanted
      // it inline with the other vLLM toggles between Auto Tool and
      // Enforce Eager so the "what the model needs" decisions sit
      // together at the top.
      const _opts2_row3 = _detectModelOptimizations(repo);
      const _rp_flag = _opts2_row3.flags.find(f => f.includes('--reasoning-parser'));
      const _rp_name = _rp_flag ? _rp_flag.split(' ')[1] : '';
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-vllm hwfit-backend-sglang">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="trust_remote"${sv('trust_remote',_isMiniMaxMSeries)?' checked':''} /> Trust Remote Code${_h('Allow model to run custom code from HuggingFace')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm hwfit-backend-sglang"><input type="checkbox" class="hwfit-sf" data-field="auto_tool"${sv('auto_tool',_nativeToolDefault)?' checked':''} /> Auto Tool Choice${_h('Enable function/tool calling for agent mode')}</label>`;
      // Always-render the Reasoning Parser, Expert Parallel, and MoE Env
      // checkboxes — the model-family detection above is a hint, not a
      // hard gate. User asked to keep these visible regardless so that
      // a borderline-undetected MoE/reasoning model can still toggle
      // them without dropping back to the raw command box.
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm hwfit-backend-sglang"><input type="checkbox" class="hwfit-sf" data-field="reasoning_parser" data-parser="${_rp_name || ''}"${sv('reasoning_parser',_reasoningDefault)?' checked':''} /> Reasoning Parser${_rp_name ? ` <span class="hwfit-parser-tag">${_rp_name}</span>` : ''}${_h('Splits <think> tokens into a separate channel. The tag (when shown) is the auto-detected parser; edit the command if you need a different one.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="enforce_eager"${sv('enforce_eager',false)?' checked':''} /> Enforce Eager${_h('Disable CUDA graphs. Slower but uses less memory')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="prefix_cache"${sv('prefix_cache',false)?' checked':''} /> Prefix Caching${_h('Cache shared prompt prefixes across requests')}</label>`;
      // Inline the previously-second vLLM checks row so Expert Parallel /
      // Speculative / MoE Env sit next to Prefix Caching with no gap. All
      // three are vLLM-only — class-gated so they hide on SGLang. Always
      // render so the user can flip them on for any MoE model.
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm hwfit-backend-sglang"><input type="checkbox" class="hwfit-sf" data-field="expert_parallel"${sv('expert_parallel',_expertParallelDefault)?' checked':''} /> Expert Parallel${_h('MoE: shard expert layers across GPUs. Helps for MiniMax M-series, StepFun Step-3, Qwen3 A3B/A10B/A22B MoE, DeepSeek V3+/R1. Ignored / wasteful on dense models.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm"><input type="checkbox" class="hwfit-sf" data-field="language_model_only"${sv('language_model_only',_isMiniMaxM3)?' checked':''} /> Language Model Only${_h('vLLM --language-model-only. Needed by MiniMax M3 text serving when the repo also contains VL components.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm"><input type="checkbox" class="hwfit-sf" data-field="disable_custom_all_reduce"${sv('disable_custom_all_reduce',_isMiniMaxM3)?' checked':''} /> Disable Custom All Reduce${_h('vLLM --disable-custom-all-reduce. Useful for some 8-GPU/nightly configurations.')}</label>`;
      {
        const _specDef = _opts2_row3.spec || { method: 'mtp', tokens: 3 };
        const _specMethod = sv('spec_method', _specDef.method);
        const _specTokens = sv('spec_tokens', String(_specDef.tokens));
        const _specMethods = ['mtp', 'qwen3_next_mtp', 'eagle', 'medusa', 'ngram'];
        if (!_specMethods.includes(_specMethod)) _specMethods.unshift(_specMethod);
        const _specOpts = _specMethods.map(m =>
          `<option value="${m}"${m === _specMethod ? ' selected' : ''}>${m}</option>`).join('');
        panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm hwfit-spec-group"><input type="checkbox" class="hwfit-sf" data-field="speculative" /> Speculative <select class="hwfit-sf hwfit-spec-method" data-field="spec_method" title="vLLM --speculative-config method">${_specOpts}</select><input type="number" class="hwfit-sf hwfit-spec-tokens hwfit-spec-tokens-bare" data-field="spec_tokens" value="${esc(_specTokens)}" min="1" max="10" title="num_speculative_tokens" style="width:44px;" /><span class="hwfit-help-chip hwfit-help-chip-inline" title="MTP / speculative decoding is supported on a few model families only — turn it on when the model card explicitly recommends it. On supported models it can boost inference throughput up to ~3×; on unsupported models it will either be ignored or fail to launch." style="margin-left:6px;">?</span></label>`;
      }
      // Always-render MoE Env Vars — the env vars dict is empty for
      // most dense models (toggle is a no-op then), but for MoE families
      // the user can still flip it on without re-fitting model detection.
      panelHtml += `<label class="hwfit-sf-cb hwfit-backend-vllm"><input type="checkbox" class="hwfit-sf" data-field="moe_env" /> MoE Env Vars${_h('Adds MoE-specific env vars to the launch command: VLLM_USE_DEEP_GEMM=0, VLLM_USE_FLASHINFER_MOE_FP16=1, OMP_NUM_THREADS=4. Helpful on MoE models like Qwen3 A3B/A10B, MiniMax, DeepSeek V3+; ignored on dense models.')}</label>`;
      panelHtml += `</div>`;
      // ── llama.cpp Advanced — grouped by purpose ──
      // Three clean field rows + one checkbox row, all selects/inputs the
      // same 28px height (no per-field `top:-Npx` nudges). Groups follow
      // user mental model: (1) where it runs on GPU, (2) how memory is
      // shaped, (3) how requests are batched, (4) on/off toggles.
      const _kvOpts = ['', 'q4_0', 'q8_0', 'f16'].map(k => `<option value="${k}"${sv('cache_type','')===k?' selected':''}>${k||'default'}</option>`).join('');
      const llamaFitOpts = ['', 'off', 'on'].map(d => `<option value="${d}"${sv('llama_fit','')===d?' selected':''}>${d||'default'}</option>`).join('');
      const llamaSplitModeOpts = ['', 'layer', 'tensor', 'row', 'none'].map(d => `<option value="${d}"${sv('llama_split_mode','')===d?' selected':''}>${d||'default'}</option>`).join('');

      // Group 1 — GPU placement (GPU-only, hides in CPU mode)
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp cookbook-llama-gpu-only">`;
      panelHtml += `<label>${_l('Split Mode','llama.cpp GPU placement. layer = default; tensor splits weights and KV across GPUs.')}<select class="hwfit-sf" data-field="llama_split_mode">${llamaSplitModeOpts}</select></label>`;
      panelHtml += `<label>${_l('Tensor Split','GPU proportions, e.g. 50,50 across two GPUs. Blank = auto.')}<input type="text" class="hwfit-sf" data-field="llama_tensor_split" value="${esc(sv('llama_tensor_split', ''))}" placeholder="auto" /></label>`;
      panelHtml += `<label>${_l('Main GPU','--main-gpu index inside the visible GPU set. Useful for split mode none/row.')}<input type="text" class="hwfit-sf" data-field="llama_main_gpu" value="${esc(sv('llama_main_gpu', ''))}" placeholder="auto" /></label>`;
      panelHtml += `</div>`;

      // Group 2 — Memory tuning (KV cache + MoE-on-CPU + Fit policy)
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp">`;
      panelHtml += `<label>${_l('KV Cache','cache-type-k/v: quantize the KV cache. q4_0 = smallest (more context), q8_0 = long-context, f16 = full.')}<select class="hwfit-sf" data-field="cache_type">${_kvOpts}</select></label>`;
      panelHtml += `<label class="cookbook-llama-gpu-only">${_l('CPU MoE','n-cpu-moe: number of MoE expert layers to run on CPU when the model is bigger than VRAM. 0 = all on GPU.')}<input type="text" class="hwfit-sf" data-field="n_cpu_moe" value="${esc(sv('n_cpu_moe',''))}" placeholder="0" /></label>`;
      panelHtml += `<label>${_l('Fit','llama.cpp --fit. Leave default unless you need explicit off/on behavior for a preset.')}<select class="hwfit-sf" data-field="llama_fit">${llamaFitOpts}</select></label>`;
      panelHtml += `</div>`;

      // Group 3 — Request batching (Batch / UBatch / Parallel)
      panelHtml += `<div class="hwfit-serve-row hwfit-backend-llamacpp">`;
      panelHtml += `<label>${_l('Batch','llama.cpp prompt batch size. Blank = default.')}<input type="text" class="hwfit-sf" data-field="llama_batch_size" value="${esc(sv('llama_batch_size', ''))}" placeholder="2048" /></label>`;
      panelHtml += `<label>${_l('UBatch','llama.cpp physical micro-batch size. Blank = default.')}<input type="text" class="hwfit-sf" data-field="llama_ubatch_size" value="${esc(sv('llama_ubatch_size', ''))}" placeholder="512" /></label>`;
      panelHtml += `<label>${_l('Parallel','llama.cpp parallel slots. Blank = default; 1 matches single-lane presets.')}<input type="text" class="hwfit-sf" data-field="llama_parallel" value="${esc(sv('llama_parallel', ''))}" placeholder="1" /></label>`;
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
      // Group 4 — llama.cpp toggles. Single row of checkboxes, GPU-only
      // ones (Flash Attn, Allow CPU overflow) hide
      // automatically in CPU mode. Order: perf-critical → safety → I/O →
      // niche. MTP Spec sits last because it owns its own numstep widget
      // and is the widest item.
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-llamacpp">`;
      panelHtml += `<label class="hwfit-sf-cb cookbook-llama-gpu-only"><input type="checkbox" class="hwfit-sf" data-field="flash_attn"${sv('flash_attn',false)?' checked':''} /> Flash Attn${_h('--flash-attn on: faster attention + needed for quantized KV cache. Auto by default.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb cookbook-llama-gpu-only"><input type="checkbox" class="hwfit-sf" data-field="llama_cpu_overflow"${sv('llama_cpu_overflow',false)?' checked':''} /> Allow CPU overflow${_h('OFF (default): cookbook blocks launches that would overflow GPU VRAM. ON: layers/KV cache that do not fit get pushed to CPU (slow).')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb cookbook-llama-gpu-only"><input type="checkbox" class="hwfit-sf" data-field="vision"${sv('vision',false)?' checked':''} /> Vision${_h('Serve with the vision encoder so the model can read images. Auto-finds an mmproj-*.gguf next to the model. Adds ~1 GB VRAM.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="llama_no_mmap"${sv('llama_no_mmap',false)?' checked':''} /> No mmap${_h('Adds --no-mmap. Useful for some high-context/local-storage setups.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="llama_no_warmup"${sv('llama_no_warmup',false)?' checked':''} /> Skip warmup${_h('Adds --no-warmup. Reduces startup memory spikes; llama.cpp defaults to warming up.')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb hwfit-spec-group"><input type="checkbox" class="hwfit-sf" data-field="llama_speculative_mtp"${sv('llama_speculative_mtp',false)?' checked':''} /> MTP Spec${_h('llama.cpp native MTP speculative decoding: --spec-type draft-mtp. Requires a GGUF with MTP heads.')} <span class="hwfit-numstep"><button type="button" class="hwfit-numstep-btn" data-step="-1" tabindex="-1" aria-label="Decrease">‹</button><input type="number" class="hwfit-sf hwfit-spec-tokens" data-field="llama_spec_tokens" value="${esc(sv('llama_spec_tokens', '3'))}" min="1" max="10" title="--spec-draft-n-max" /><button type="button" class="hwfit-numstep-btn" data-step="1" tabindex="-1" aria-label="Increase">›</button></span></label>`;
      panelHtml += `</div>`;
      // Row 3b: Checkboxes (diffusers)
      panelHtml += `<div class="hwfit-serve-checks hwfit-backend-diffusers">`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_offload"${sv('diff_offload',false)?' checked':''} /> CPU Offload${_h('Offload parts of model to CPU RAM to save VRAM. Slower but fits larger models')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_attention_slicing"${sv('diff_attention_slicing',false)?' checked':''} /> Attention Slicing${_h('Slice attention computation to reduce peak VRAM. Slower')}</label>`;
      panelHtml += `<label class="hwfit-sf-cb"><input type="checkbox" class="hwfit-sf" data-field="diff_vae_slicing"${sv('diff_vae_slicing',false)?' checked':''} /> VAE Slicing${_h('Process VAE in slices. Reduces VRAM for high-res images')}</label>`;
      panelHtml += `</div><div class="hwfit-serve-row hwfit-backend-diffusers">`;
      panelHtml += `<label>Harmonize GPU${_h('Separate GPU for img2img/harmonize. Leave empty to use same GPU')}<input type="text" class="hwfit-sf" data-field="diff_harmonize_gpu" value="${esc(sv('diff_harmonize_gpu', ''))}" placeholder="auto" style="width:50px;" /></label>`;
      panelHtml += `</div>`;
      // Model-specific optimizations. The checks row always renders for the
      // vLLM backend so the Speculative (MTP) control is ALWAYS reachable —
      // even for models the auto-detector doesn't recognize. Expert-parallel,
      // reasoning-parser and MoE-env still only appear when auto-detected.
      // Expert Parallel / Speculative / MoE Env moved into Row 3 above so
      // the vLLM-only toggles sit next to Prefix Caching with no gap.
      // Extra args sits below the vLLM checks (Reasoning Parser + Spec)
      // so it reads as "after the advanced toggles, any other flags".
      panelHtml += `<div class="hwfit-serve-extra">`;
      panelHtml += `<label>Extra args<input type="text" class="hwfit-sf" data-field="extra" value="${esc(sv('extra', ''))}" placeholder="--flag value" /></label>`;
      panelHtml += `</div>`;
      // ── End Advanced fold ──
      panelHtml += `</details>`;
      // Command preview + actions. Wrap the textarea so a floating Copy
      // button can sit at its top-right corner — same pattern as the chat
      // run-output panel.
      panelHtml += `<details class="hwfit-serve-cmd-details">`;
      panelHtml += `<summary class="hwfit-serve-cmd-summary">Launch command</summary>`;
      panelHtml += `<div class="hwfit-serve-cmd-wrap">`;
      panelHtml += `<textarea class="hwfit-serve-cmd" spellcheck="false" rows="2"></textarea>`;
      panelHtml += `<button type="button" class="cookbook-btn hwfit-serve-copy hwfit-serve-copy-inline" title="Copy launch command" aria-label="Copy"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
      panelHtml += `</div>`;
      panelHtml += `</details>`;
      panelHtml += `<div class="hwfit-serve-actions">`;
      panelHtml += `<button class="cookbook-btn hwfit-serve-cancel" type="button" title="Close this configuration panel"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;flex-shrink:0;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Cancel</button>`;
      // Copy moved inside the command textarea (top-right). Spacer then
      // pushes Clear Server + Launch to the right.
      panelHtml += `<span class="hwfit-serve-actions-spacer"></span>`;
      panelHtml += `<button class="cookbook-btn cookbook-gpu-clear" style="display:none;" title="Clear server GPU memory by stopping processes that hold VRAM (SIGTERM first)">Clear Server</button>`;
      panelHtml += `<button class="cookbook-btn cookbook-gpu-probe" style="display:none;" title="Probe GPU memory and running GPU processes">Probe GPUs</button>`;
      // Launch + a small ^ that opens an inline schedule form. The form
      // creates a ScheduledTask (action=cookbook_serve), so the schedule
      // ends up in the existing Tasks UI for edit/delete/pause.
      panelHtml += `<span class="hwfit-serve-launch-group">`;
      panelHtml += `<button class="cookbook-btn hwfit-serve-launch"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:4px;flex-shrink:0;"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>Launch</button>`;
      // Chevron points DOWN because the schedule form opens beneath the
      // panel — the arrow signals the direction of motion, not menu state.
      panelHtml += `<button class="cookbook-btn hwfit-serve-schedule-arrow" type="button" aria-haspopup="menu" aria-label="More launch actions" title="More launch actions"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg></button>`;
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
      // Firefox-mobile fallback: the CSS that grows the cached-list and
      // expanded card uses :has(.doclib-card-expanded), which Firefox
      // mobile doesn't support — so the panel stays collapsed and the
      // form is unusable. Pin explicit px heights here. On Chromium/
      // WebKit the !important CSS still wins, so this is a no-op there.
      // (See project_skills_expand_firefox memory note.)
      requestAnimationFrame(() => {
        try {
          const _itemH = Math.max(item.scrollHeight, item.getBoundingClientRect().height);
          if (_itemH > 0) item.style.maxHeight = _itemH + 'px';
          const _listH = Math.max(list.scrollHeight, list.getBoundingClientRect().height);
          if (_listH > 0) list.style.maxHeight = _listH + 'px';
          list.style.minHeight = _listH + 'px';
        } catch {}
      });

      // Build command preview
      function updateCmd() {
        const f = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') f[el.dataset.field] = el.checked;
          else f[el.dataset.field] = el.value;
        });
        const buildTarget = _selectedServeTarget(panel);
        f.host = buildTarget.host || '';
        f.platform = buildTarget.platform || '';
        const hostField = panel.querySelector('[data-field="host"]');
        if (hostField) hostField.value = f.host;
        const backend = f.backend || 'vllm';
        const serveModel = (f.model_path || '').trim() || (m.is_local_dir && m.path ? `${m.path}/${repo}` : repo);
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
          // Vision: use the scanned projector (CLIP/mmproj) file when present.
          // Keeping this as a printf path avoids generating a command substitution
          // that the backend serve-command validator must reject as unsafe.
          const selectedProjector = _projectorGgufFiles(m)[0];
          f._mmproj_path = selectedProjector ? _selectedGgufExpr(m, repo, selectedProjector.rel_path) : '';
        }
        if (f.reasoning_parser) {
          const _rpEl2 = panel.querySelector('[data-field="reasoning_parser"]');
          f._reasoning_parser_value = _rpEl2?.dataset?.parser || '';
        }
        if (f.vllm_env_preset === 'minimax_m3_cuda') {
          const existingEnv = String(f.extra_env || '').trim();
          const envParts = existingEnv ? existingEnv.split(/\s+/) : [];
          const hasEnv = (key) => envParts.some(p => p.startsWith(`${key}=`));
          if (!hasEnv('VLLM_TARGET_DEVICE')) envParts.unshift('VLLM_TARGET_DEVICE=cuda');
          if (!hasEnv('VLLM_USE_FLASHINFER_SAMPLER')) envParts.push('VLLM_USE_FLASHINFER_SAMPLER=0');
          f.extra_env = envParts.join(' ');
        }
        let cmd = _buildServeCmd(f, serveModel, backend);
        if (f.extra && f.extra.trim()) cmd += ' ' + f.extra.trim();
        const missingVisionProjector = backend === 'llamacpp' && !!f.vision && !f._mmproj_path;
        panel._visionMissingProjector = missingVisionProjector;
        const _visionWarn = panel.querySelector('.hwfit-serve-vision-warn');
        if (_visionWarn) _visionWarn.style.display = missingVisionProjector ? 'flex' : 'none';
        const _ce2 = panel.querySelector('.hwfit-serve-cmd'); _ce2.value = _formatServeCmdPreview(cmd); _ce2.style.height = 'auto'; _ce2.style.height = _ce2.scrollHeight + 'px';
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
      panel._modelCtxMax = panel._modelCtxMax || _knownModelContextMax(m) || 0;
      panel._modelWeightsGb = panel._modelWeightsGb || 0;
      panel._fitSystem = panel._fitSystem || null;
      const _ctxEl0 = panel.querySelector('[data-field="ctx"]');
      const _ctxAutoNote = panel.querySelector('.hwfit-auto-ctx-note');
      const _ctxCalcBtn = panel.querySelector('.hwfit-context-calc-btn');
      if (_ctxEl0) _ctxEl0.dataset.autoCtx = '0';
      panel._contextProfileData = panel._contextProfileData || null;
      function _collectServeFields() {
        const f = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') f[el.dataset.field] = el.checked;
          else f[el.dataset.field] = el.value;
        });
        return f;
      }
      function _updateRecommendedCtx(apply = true) {
        if (!_ctxEl0) return;
        const f = _collectServeFields();
        const backend = f.backend || 'vllm';
        let fit = null;
        if (backend === 'vllm' || backend === 'sglang') {
          fit = _estimateVllmContextFit(m, f, panel._modelCtxMax, panel._modelWeightsGb, panel._fitSystem);
        } else if (backend === 'llamacpp' || backend === 'ollama') {
          const ggufGb = _selectedGgufSizeGb(m, f.gguf_file);
          fit = _estimateLlamaContextFit(m, f, panel._modelCtxMax, ggufGb || panel._modelWeightsGb, panel._fitSystem, panel._contextProfileData);
        } else {
          if (_ctxAutoNote) _ctxAutoNote.textContent = '';
          return;
        }
        if (!fit) {
          if (_ctxAutoNote) _ctxAutoNote.textContent = '';
          return;
        }
        if ((fit.needsHardwareScan || fit.needsModelSize) && !fit.ctx) {
          if (_ctxAutoNote) {
            _ctxAutoNote.textContent = fit.reason;
            _ctxAutoNote.title = fit.reason;
          }
          return;
        }
        if (_ctxAutoNote) {
          _ctxAutoNote.textContent = `Auto ${fit.ctx.toLocaleString()} · ${fit.reason}`;
          const _llamaMemoryLabel = String(f.llama_mode || '').toLowerCase() === 'unified' || f.unified_mem
            ? 'unified system memory'
            : 'selected GPU memory';
          _ctxAutoNote.title = backend === 'llamacpp' || backend === 'ollama'
            ? `Estimated from scanned GGUF/model size, trained context limit, and ${_llamaMemoryLabel} for llama.cpp KV cache.`
            : `Estimated from model size, selected GPU VRAM, GPU utilization, TP, and KV dtype.`;
        }
        if (apply && _ctxEl0.dataset.autoCtx === '1') {
          const next = String(fit.ctx);
          if (_ctxEl0.value !== next) {
            _ctxEl0.value = next;
            updateCmd();
          }
        }
      }
      async function _loadContextProfile() {
        const target = _selectedServeTarget(panel);
        const host = (target.host || '').trim();
        const params = new URLSearchParams({ model: repo });
        const profileModelPath = panel.querySelector('[data-field="model_path"]')?.value?.trim();
        if (profileModelPath && profileModelPath !== repo) params.set('model_path', profileModelPath);
        if (host) {
          params.set('host', host);
          const _sp = (_serverByVal?.(target.serverKey || host) || (_es.servers || []).find(s => s.host === host) || {}).port;
          if (_sp) params.set('ssh_port', _sp);
        }
        const res = await fetch(`/api/hwfit/profiles?${params}`);
        const data = await res.json();
        const ctxMax = Number(data && data.model_ctx_max) || 0;
        const weightsGb = Number(data && data.model_weights_gb) || 0;
        if (data && data.system && typeof data.system === 'object' && !data.system.error) {
          panel._fitSystem = data.system;
        }
        panel._contextProfileData = data || null;
        if (weightsGb > 0) panel._modelWeightsGb = weightsGb;
        if (ctxMax > 0) {
          panel._modelCtxMax = Math.max(ctxMax, _knownModelContextMax(m) || 0);
          _clampCtx(false);
        }
        if (_ctxAutoNote && data?.model_probe_error && (!ctxMax || !weightsGb)) {
          _ctxAutoNote.textContent = data.model_probe_error;
          _ctxAutoNote.title = data.model_probe_error;
        }
        return { ctxMax, weightsGb, data };
      }
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
        _ctxEl0.addEventListener('input', () => { _ctxEl0.dataset.autoCtx = '0'; });
        _ctxEl0.addEventListener('change', () => { _ctxEl0.dataset.autoCtx = '0'; _clampCtx(false); _updateRecommendedCtx(false); });
        _ctxEl0.addEventListener('blur', () => _clampCtx(false));
        if (_ctxCalcBtn) {
          let _ctxAutoTouchHandled = false;
          const _runContextAuto = async () => {
            if (_ctxCalcBtn.disabled) return;
            const oldHtml = _ctxCalcBtn.innerHTML;
            let calcWp = null;
            _ctxCalcBtn.disabled = true;
            _ctxCalcBtn.textContent = '';
            try {
              calcWp = spinnerModule.createWhirlpool(12);
              calcWp.element.classList.add('hwfit-context-calc-spinner');
              _ctxCalcBtn.appendChild(calcWp.element);
            } catch (_) {
              _ctxCalcBtn.textContent = '...';
            }
            try {
              await _loadContextProfile();
            } catch (err) {
              if (_ctxAutoNote) {
                _ctxAutoNote.textContent = 'context scan failed';
                _ctxAutoNote.title = err?.message || 'context scan failed';
              }
            } finally {
              if (calcWp) calcWp.destroy();
              _ctxCalcBtn.disabled = false;
              _ctxCalcBtn.innerHTML = oldHtml;
            }
            _ctxEl0.dataset.autoCtx = '1';
            _updateRecommendedCtx(true);
            _ctxEl0.dataset.autoCtx = '0';
            _clampCtx(false);
          };
          _ctxCalcBtn.addEventListener('pointerup', (ev) => {
            if (ev.pointerType !== 'touch') return;
            ev.preventDefault();
            ev.stopPropagation();
            _ctxAutoTouchHandled = true;
            _runContextAuto();
            setTimeout(() => { _ctxAutoTouchHandled = false; }, 350);
          });
          _ctxCalcBtn.addEventListener('click', async (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            if (_ctxAutoTouchHandled) return;
            await _runContextAuto();
          });
        }
        _clampCtx(false);   // fix any stale/preset value already present
        _updateRecommendedCtx(false);
      }

      // Tighten the ctx slider's upper bound to the model's trained limit.
      // Asking llama.cpp for ctx > n_ctx_train overflows and, with a quantized
      // KV cache, can crash the GPU (radv ErrorDeviceLost). The auto-profile
      // chip row that used to also live here was removed — visual fit with
      // the rest of the serve panel was off — but this clamp is essential.
      (async () => {
        try {
          const { ctxMax, weightsGb } = await _loadContextProfile();
          if (ctxMax > 0 || weightsGb > 0) _updateRecommendedCtx(false);
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

      // Backend icons — accent color, rendered via currentColor. vLLM gets
      // a stylized double-V mark, the others fall back to a recognizable
      // glyph for the engine family. Shown beside each option in the
      // custom picker so the dropdown lists "[V] vLLM", "[⚡] SGLang", etc.
      const _BACKEND_GLYPHS = {
        vllm:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4l7 16 7-16"/><path d="M14 4l4 9 3-9"/></svg>',
        sglang: '<span aria-hidden="true" style="display:block;width:14px;height:14px;background:currentColor;-webkit-mask:url(/static/icons/sglang-mark.png) center/contain no-repeat;mask:url(/static/icons/sglang-mark.png) center/contain no-repeat;"></span>',
        llamacpp: '<svg width="14" height="14" viewBox="0 0 600 600" fill="none" aria-hidden="true"><path d="M600 392L504.249 558L504.137 557.929C487.252 584.069 458.193 600 426.864 600H120L240 392H600Z" fill="currentColor"/><path d="M240 392H0L199.602 46.0254C216.032 17.5463 246.411 0 279.29 0H466.154L240 392Z" fill="currentColor"/></svg>',
        ollama: '<img src="/static/icons/ollama-mark-crop.png" alt="" aria-hidden="true" width="14" height="14" style="display:block;width:14px;height:14px;object-fit:contain;" />',
        diffusers: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/></svg>',
      };

      // ── Custom Backend picker wiring ────────────────────────────────
      // Reads the option list from the hidden <select.hwfit-backend-source>
      // so the canonical (value, label) pairs come from one place.
      const _backendPicker = panel.querySelector('[data-backend-picker]');
      const _backendSource = panel.querySelector('.hwfit-backend-source');
      const _backendBtn = panel.querySelector('[data-backend-btn]');
      const _backendMenu = panel.querySelector('[data-backend-menu]');
      const _backendBtnLabel = panel.querySelector('[data-backend-label]');
      const _backendBtnIconSlot = _backendBtn?.querySelector('[data-backend-icon-slot]');

      function _setBackendBtnState(v) {
        if (!_backendBtn) return;
        const opt = _backendSource?.querySelector(`option[value="${CSS.escape(v)}"]`);
        const label = opt ? opt.textContent : v;
        if (_backendBtnLabel) _backendBtnLabel.textContent = label;
        if (_backendBtnIconSlot) _backendBtnIconSlot.innerHTML = _BACKEND_GLYPHS[v] || _BACKEND_GLYPHS.vllm;
      }

      function _renderBackendMenu() {
        if (!_backendMenu || !_backendSource) return;
        const items = Array.from(_backendSource.options).map(o => ({ value: o.value, label: o.textContent }));
        _backendMenu.innerHTML = items.map(it => `
          <button type="button" role="option" class="hwfit-backend-item" data-value="${it.value}" style="all:unset;display:flex;align-items:center;gap:8px;width:100%;padding:6px 9px;border-radius:5px;font-size:12px;cursor:pointer;color:var(--fg);box-sizing:border-box;">
            <span class="hwfit-backend-item-icon" style="display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;color:var(--accent, var(--red));flex-shrink:0;">${_BACKEND_GLYPHS[it.value] || _BACKEND_GLYPHS.vllm}</span>
            <span class="hwfit-backend-item-label" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${it.label}</span>
          </button>
        `).join('');
        // Hover styling (no global CSS rule — keep it self-contained).
        _backendMenu.querySelectorAll('.hwfit-backend-item').forEach(btn => {
          btn.addEventListener('mouseenter', () => { btn.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; });
          btn.addEventListener('mouseleave', () => { btn.style.background = ''; });
          btn.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            const v = btn.dataset.value;
            if (_backendSource && _backendSource.value !== v) {
              _backendSource.value = v;
              _backendSource.dispatchEvent(new Event('change', { bubbles: true }));
            }
            _setBackendBtnState(v);
            _closeBackendMenu();
          });
        });
      }

      function _openBackendMenu() {
        if (!_backendMenu || !_backendBtn) return;
        _backendMenu.hidden = false;
        _backendBtn.setAttribute('aria-expanded', 'true');
      }
      function _closeBackendMenu() {
        if (!_backendMenu || !_backendBtn) return;
        _backendMenu.hidden = true;
        _backendBtn.setAttribute('aria-expanded', 'false');
      }
      if (_backendBtn) {
        _backendBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          if (_backendMenu.hidden) _openBackendMenu();
          else _closeBackendMenu();
        });
        document.addEventListener('click', (ev) => {
          if (!_backendMenu.hidden && !_backendPicker?.contains(ev.target)) _closeBackendMenu();
        });
        document.addEventListener('keydown', (ev) => {
          if (ev.key === 'Escape' && !_backendMenu.hidden) {
            ev.stopPropagation();
            _closeBackendMenu();
          }
        }, { capture: true });
      }
      _renderBackendMenu();
      _setBackendBtnState(_backendSource?.value || defaultBackend);

      function updateBackendVisibility() {
        const b = panel.querySelector('[data-field="backend"]')?.value || 'vllm';
        panel.querySelectorAll('[class*="hwfit-backend-"]').forEach(el => {
          // Skip the entire backend-picker subtree — the picker's own
          // classes (`hwfit-backend-picker`, `-btn`, `-menu`, `-item`,
          // `-btn-icon`, `-btn-label`, `-item-icon`, `-item-label`) all
          // match the wildcard and would get hidden as if they were
          // "backend-specific form sections", which left the dropdown
          // looking empty / collapsed.
          if (el.closest('.hwfit-backend-picker')) return;
          const show = el.classList.contains(`hwfit-backend-${b}`);
          el.style.display = show ? '' : 'none';
        });
        _setBackendBtnState(b);
      }
      updateBackendVisibility();

      async function updateRuntimeReadinessNote() {
        const note = panel.querySelector('.hwfit-serve-runtime-note');
        if (!note) return;
        // Mirror the message into a small chip next to the model title at
        // the top of the card, so the readiness state is visible without
        // having to look down into the panel body.
        // Clean up any title chip from previous versions — the readiness
        // text now lives inside the panel at the top, not in the card title.
        const card = panel.closest('.doclib-card, .memory-item');
        const titleEl = card ? card.querySelector('.memory-item-title') : null;
        const titleChip = titleEl ? titleEl.querySelector('.hwfit-serve-runtime-chip') : null;
        if (titleChip) titleChip.remove();
        const backend = panel.querySelector('[data-field="backend"]')?.value || 'vllm';
        const noteText = note.querySelector('.hwfit-serve-runtime-text');
        const _writeNote = (s) => { if (noteText) noteText.textContent = s; else note.textContent = s; };
        if (!['vllm', 'sglang', 'llamacpp', 'diffusers'].includes(backend)) {
          note.style.display = 'none';
          _writeNote('');
          return;
        }
        // Wire dismiss once per note element.
        const _closeBtn = note.querySelector('.hwfit-serve-runtime-close');
        if (_closeBtn && !_closeBtn._wired) {
          _closeBtn._wired = true;
          _closeBtn.addEventListener('click', (ev) => {
            ev.preventDefault();
            ev.stopPropagation();
            note.style.display = 'none';
            panel._runtimeNoteDismissed = true;
          });
        }
        // If the user dismissed it earlier on this panel, don't re-show.
        if (panel._runtimeNoteDismissed) return;
        const seq = (panel._runtimeReadinessSeq || 0) + 1;
        panel._runtimeReadinessSeq = seq;
        note.style.display = '';
        _writeNote('Checking runtime on selected server…');
        note.style.borderColor = '';
        note.style.color = 'var(--fg-muted)';
        try {
          const { pkg, target } = await _fetchServeRuntimePackage(panel, backend);
          if (panel._runtimeReadinessSeq !== seq) return;
          _writeNote(_runtimeNoteText(backend, pkg, target));
          if (pkg?.installed === null || pkg?.probe_error) {
            note.style.color = 'var(--fg-muted)';
            note.style.borderColor = 'color-mix(in srgb, var(--fg) 16%, transparent)';
            note.style.background = 'color-mix(in srgb, var(--fg) 4%, transparent)';
          } else if (!pkg?.installed) {
            note.style.color = 'var(--red)';
            note.style.borderColor = 'color-mix(in srgb, var(--red) 40%, transparent)';
            note.style.background = 'color-mix(in srgb, var(--red) 8%, transparent)';
            // Append an accent-color link straight to the Dependencies
            // recipe panel for this backend so the user has one click
            // to the fix instead of hunting for the right row.
            if (noteText) {
              const pkgName = pkg?.name || ({ vllm: 'vllm', sglang: 'sglang', llamacpp: 'llama_cpp', diffusers: 'diffusers' }[backend]);
              const repo = (panel.closest('.doclib-card, .memory-item')?.dataset?.repo) || '';
              const link = document.createElement('a');
              link.href = '#';
              link.textContent = ' Install in Dependencies →';
              link.style.cssText = 'color:var(--accent, var(--red));text-decoration:underline;font-weight:600;margin-left:4px;';
              link.addEventListener('click', (ev) => {
                ev.preventDefault();
                if (pkgName) openCookbookDependencies(pkgName, { expandRecipe: pkgName, model: repo });
              });
              noteText.appendChild(link);
            }
          } else {
            // Healthy / ready → green so the user reads "good to go" at a
            // glance instead of scanning fg-muted for a state.
            note.style.color = 'var(--green, #4caf50)';
            note.style.borderColor = 'color-mix(in srgb, var(--green, #4caf50) 40%, transparent)';
            note.style.background = 'color-mix(in srgb, var(--green, #4caf50) 8%, transparent)';
          }
        } catch (err) {
          if (panel._runtimeReadinessSeq !== seq) return;
          _writeNote(`Runtime readiness unavailable: ${err?.message || err}`);
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
        {
          const modeHidden = panel.querySelector('[data-field="llama_mode"]');
          const unifiedHidden = panel.querySelector('[data-field="unified_mem"]');
          const loadedUnified = ['1', 'true', 'yes', 'on'].includes(String(unifiedHidden?.value || '').toLowerCase());
          const loadedMode = loadedUnified && modeHidden?.value !== 'cpu'
            ? 'unified'
            : (modeHidden?.value || 'gpu');
          if (modeHidden) modeHidden.value = loadedMode;
          if (unifiedHidden) unifiedHidden.value = loadedMode === 'unified' ? '1' : '';
          const modeGroup = panel.querySelector('[data-llama-mode-toggle]');
          if (modeGroup) {
            modeGroup.querySelectorAll('.mode-toggle-btn').forEach(btn => {
              const isActive = btn.dataset.llamaMode === loadedMode;
              btn.classList.toggle('active', isActive);
              btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
            modeGroup.classList.toggle('mode-right', false);
            modeGroup.classList.toggle('mode-mid', loadedMode === 'gpu');
            modeGroup.classList.toggle('mode-third', loadedMode === 'unified');
          }
          panel.classList.toggle('cookbook-llama-cpu-mode', loadedMode === 'cpu');
          panel.querySelectorAll('.cookbook-llama-gpu-only').forEach(el => {
            el.style.display = loadedMode === 'cpu' ? 'none' : '';
          });
        }
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
        presets.push(_redactServeStateForStorage({ name: shortName, model: repo, cmd, remoteHost: host, port: fields.port || '8000', label, fields }));
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
        const modelSlots = _presetsForModel(_loadPresets(), repo)
          .map((preset, slotIdx) => ({ preset, slotIdx }))
          .sort((a, b) => {
            const favDelta = (b.preset.favorite ? 1 : 0) - (a.preset.favorite ? 1 : 0);
            return favDelta || (a.slotIdx - b.slotIdx);
          });
        const dropdown = document.createElement('div');
        dropdown.className = 'dropdown cookbook-saved-menu';
        let closeMenu = () => { dropdown.remove(); anchor.classList.remove('cookbook-menu-active'); };
        const rect = anchor.getBoundingClientRect();
        const minW = 190;
        // Cap width/height to the viewport and start hidden — we clamp the final
        // position after mount (below) using the menu's real measured size, so it
        // can't run off-screen on a narrow mobile viewport.
        dropdown.style.cssText = `position:fixed;display:block;visibility:hidden;z-index:${topPortalZ()};top:0;left:0;right:auto;min-width:${minW}px;max-width:calc(100vw - 16px);max-height:calc(100vh - 24px);overflow-y:auto;box-sizing:border-box;background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:6px;font-size:11px;`;

        if (!modelSlots.length) {
          const empty = document.createElement('div');
          empty.style.cssText = 'padding:6px 8px;opacity:0.5;position:relative;top:1px;';
          empty.textContent = 'No saved configs yet';
          dropdown.appendChild(empty);
        }
        modelSlots.forEach(({ preset: p, slotIdx }, idx) => {
          const it = document.createElement('div');
          it.className = 'dropdown-item-compact' + (p.favorite ? ' cookbook-saved-favorite' : '');
          it.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;';
          const lbl = document.createElement('span');
          lbl.textContent = p.label || `Config ${idx + 1}`;
          lbl.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
          const fav = document.createElement('button');
          fav.type = 'button';
          fav.className = 'cookbook-saved-fav-btn' + (p.favorite ? ' active' : '');
          fav.title = p.favorite ? 'Unfavorite' : 'Favorite';
          fav.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>';
          const del = document.createElement('button');
          del.type = 'button';
          del.innerHTML = '×';
          del.title = 'Delete';
          del.style.cssText = 'background:none;border:none;color:var(--fg-muted);cursor:pointer;font-size:15px;line-height:1;padding:0 2px;flex-shrink:0;';
          del.addEventListener('mouseenter', () => { del.style.color = '#f44'; });
          del.addEventListener('mouseleave', () => { del.style.color = 'var(--fg-muted)'; });
          it.appendChild(lbl);
          if (p.favorite) {
            const badge = document.createElement('span');
            badge.className = 'memory-cat-badge memory-cat-pinned cookbook-saved-fav-badge';
            badge.textContent = 'pinned';
            it.appendChild(badge);
          }
          if (p.confirmedWorking) {
            const badge = document.createElement('span');
            badge.className = 'cookbook-saved-confirmed';
            badge.title = 'Confirmed working — this config launched and registered an endpoint';
            badge.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#50fa7b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
            it.appendChild(badge);
          }
          it.appendChild(fav);
          it.appendChild(del);
          it.addEventListener('click', (e) => {
            if (e.target === del || e.target === fav || fav.contains(e.target)) return;
            e.stopPropagation();
            // Close the menu FIRST so it always dismisses, even if loading throws.
            closeMenu();
            _loadSlotIntoPanel(slotIdx);
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
          fav.addEventListener('click', (e) => {
            e.stopPropagation();
            const cur = _loadPresets();
            const target = _presetsForModel(cur, repo)[slotIdx];
            if (target) {
              target.favorite = !target.favorite;
              _savePresets(cur.map(_redactServeStateForStorage));
              uiModule.showToast(target.favorite ? 'Favorited — pinned to top' : 'Unfavorited');
              _showSavedConfigMenu(anchor);
            }
          });
          del.addEventListener('click', async (e) => {
            e.stopPropagation();
            const label = p.label || `Config ${idx + 1}`;
            if (!await window.styledConfirm(`Delete saved config "${label}"?`, { confirmText: 'Delete', danger: true })) return;
            const cur = _loadPresets();
            const toRemove = _presetsForModel(cur, repo)[slotIdx];
            if (toRemove) {
              const gi = cur.indexOf(toRemove);
              if (gi >= 0) cur.splice(gi, 1);
              _savePresets(cur.map(_redactServeStateForStorage));
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
          try { _updateRecommendedCtx(false); } catch {}
        });
      });

      // Wire "Probe GPUs" / "Clear Server" — annotate GPU buttons with free VRAM and per-GPU PIDs
      const _probeBtn = panel.querySelector('.cookbook-gpu-probe');
      const _clearBtn = panel.querySelector('.cookbook-gpu-clear');
      const _splitArrow = panel.querySelector('.cookbook-gpu-split-arrow');
      const _launchMoreBtn = panel.querySelector('.hwfit-serve-schedule-arrow');
      if (_launchMoreBtn) {
        _launchMoreBtn.addEventListener('click', (ev) => {
          if (ev.__openScheduleDirect) return;
          ev.preventDefault();
          ev.stopPropagation();
          document.querySelectorAll('.cookbook-launch-actions-menu').forEach(m => { if (typeof m._dismiss === 'function') m._dismiss(); else m.remove(); });
          const menu = document.createElement('div');
          menu.className = 'cookbook-task-dropdown cookbook-launch-actions-menu';
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
          menu.appendChild(mk('Copy launch command', '', () => {
            updateCmd();
            const cmdBox = panel.querySelector('.hwfit-serve-cmd');
            const cmd = (_cmdManuallyEdited && cmdBox)
              ? cmdBox.value
              : _formatServeCmdPreview(panel._cmd || cmdBox?.value || '');
            _copyText(cmd).then(() => uiModule.showToast('Launch command copied'));
          }));
          menu.appendChild(mk('Schedule', '', () => {
            const direct = new MouseEvent('click', { bubbles: true, cancelable: true });
            direct.__openScheduleDirect = true;
            _launchMoreBtn.dispatchEvent(direct);
          }));
          menu.appendChild(mk('Clear Server', 'cookbook-dropdown-danger', () => _clearBtn?.click()));
          menu.appendChild(mk('Cancel', 'dropdown-cancel-mobile', () => {}));
          const r = _launchMoreBtn.getBoundingClientRect();
          menu.style.position = 'fixed';
          menu.style.right = (window.innerWidth - r.right) + 'px';
          document.body.appendChild(menu);
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
          const _scrollClose = () => closeMenu();
          closeMenu = bindMenuDismiss(menu, () => { menu.remove(); window.removeEventListener('scroll', _scrollClose, true); }, (e) => !menu.contains(e.target) && e.target !== _launchMoreBtn);
          window.addEventListener('scroll', _scrollClose, true);
        });
      }
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
          if (['backend', 'tp', 'gpu_mem', 'vllm_kv_cache_dtype', 'gpus'].includes(e.target.dataset.field)) {
            try { _updateRecommendedCtx(false); } catch {}
          }
        });
      });
      // llama.cpp CPU/GPU/Unified mode-toggle wiring. Clicking a mode
      // flips the .active classes + marker class (so the sliding
      // pill matches Agent/Chat), updates the hidden data-field input,
      // and fires a change event so the existing field-change handler
      // rebuilds the serve cmd (sets -ngl 99 vs -ngl 0 and unified env).
      panel.querySelectorAll('[data-llama-mode-toggle]').forEach(group => {
        group.querySelectorAll('.mode-toggle-btn').forEach(btn => {
          btn.addEventListener('click', (e) => {
            e.preventDefault(); e.stopPropagation();
            const want = btn.dataset.llamaMode;
            if (!want) return;
            group.querySelectorAll('.mode-toggle-btn').forEach(b => {
              const isActive = b.dataset.llamaMode === want;
              b.classList.toggle('active', isActive);
              b.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            });
            group.classList.toggle('mode-right', false);
            group.classList.toggle('mode-mid', want === 'gpu');
            group.classList.toggle('mode-third', want === 'unified');
            const hidden = group.parentElement.querySelector('[data-field="llama_mode"]');
            if (hidden) {
              hidden.value = want;
              hidden.dispatchEvent(new Event('change', { bubbles: true }));
            }
            const unified = group.parentElement.querySelector('[data-field="unified_mem"]');
            if (unified) {
              unified.value = want === 'unified' ? '1' : '';
              unified.dispatchEvent(new Event('change', { bubbles: true }));
            }
            // Hide every GPU-only control (chiclets, Tensor Split,
            // Split Mode, Main GPU, Flash Attn, etc.)
            // in CPU mode — `-ngl 0` ignores them and showing them
            // implies they matter.
            panel.classList.toggle('cookbook-llama-cpu-mode', want === 'cpu');
            panel.querySelectorAll('.cookbook-llama-gpu-only').forEach(el => {
              el.style.display = (want === 'cpu') ? 'none' : '';
            });
          });
        });
      });
      // Apply the CPU-mode visibility on first render too, so a saved
      // preset that loaded with llama_mode=cpu hides GPU controls
      // immediately instead of flashing them then disappearing.
      {
        const _saved = panel.querySelector('[data-field="llama_mode"]')?.value || 'gpu';
        const _group = panel.querySelector('[data-llama-mode-toggle]');
        if (_group) {
          _group.classList.toggle('mode-right', false);
          _group.classList.toggle('mode-mid', _saved === 'gpu');
          _group.classList.toggle('mode-third', _saved === 'unified');
        }
        const _unified = panel.querySelector('[data-field="unified_mem"]');
        if (_unified) _unified.value = _saved === 'unified' ? '1' : '';
        if (_saved === 'cpu') {
          panel.classList.add('cookbook-llama-cpu-mode');
          panel.querySelectorAll('.cookbook-llama-gpu-only').forEach(el => { el.style.display = 'none'; });
        }
      }
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
      const _collapsePanel = () => {
        panel._cleanupRuntimeReadiness?.();
        panel.remove();
        item.classList.remove('doclib-card-expanded');
        item.style.flexDirection = '';
        item.style.alignItems = '';
        if (list) { list.style.minHeight = ''; list.style.maxHeight = ''; }
      };
      panel.querySelector('.hwfit-serve-cancel')?.addEventListener('click', (ev) => {
        ev.stopPropagation();
        _collapsePanel();
      });
      // Esc anywhere on the page closes the open serve panel. Skips when
      // the user is typing in a field — they want Esc to deselect / blur
      // those, not collapse the form they're configuring.
      const _onEscClose = (ev) => {
        if (ev.key !== 'Escape') return;
        if (!panel.isConnected) {
          document.removeEventListener('keydown', _onEscClose, true);
          return;
        }
        const t = ev.target;
        const inField = t && (
          t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
        );
        if (inField) return;
        // Skip when one of the dropdown/menu popovers is open — the
        // popovers handle their own Esc and use stopPropagation, so any
        // Esc that bubbles here means nothing else claimed it.
        ev.stopPropagation();
        _collapsePanel();
      };
      document.addEventListener('keydown', _onEscClose, true);

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
        const _rawLaunchCmd = (_cmdManuallyEdited && _cmdTextarea) ? _cmdTextarea.value : panel._cmd;
        const launchCmd = _normalizeServeCmdForLaunch(_rawLaunchCmd);
        const serveState = {};
        panel.querySelectorAll('.hwfit-sf').forEach(el => {
          if (el.type === 'checkbox') serveState[el.dataset.field] = el.checked;
          else serveState[el.dataset.field] = el.value;
        });
        serveState.backend = serveState.backend || (_detectBackend(m).backend) || 'vllm';
        const launchTarget = _selectedServeTarget(panel);
        if (serveState.backend === 'llamacpp' && serveState.vision && !/(?:^|\s)(?:--mmproj|--clip_model_path)\b/.test(launchCmd)) {
          _restoreLaunchBtn();
          uiModule.showToast('Vision is checked, but no mmproj projector is in the launch command. Refresh cached models after downloading mmproj, or add --mmproj manually.', 8000);
          return;
        }
        if (serveState.backend === 'diffusers' && _remoteWindowsDiffusersUnsupported(launchTarget)) {
          _restoreLaunchBtn();
          uiModule.showToast('Diffusers serving is not supported on remote Windows servers yet. Use local Windows or a Linux server.', 9000);
          return;
        }
        // Pre-launch: check our own task list for a serve already running
        // on this host. Offer to stop+launch as the default action — the
        // SSH-based port probe below is more thorough but it can miss
        // when SSH glitches or `ss` isn't installed. This catches the
        // common case instantly without waiting for a network round-trip.
        try {
          const _runningMod = await import('./cookbookRunning.js');
          const _hostStr = launchTarget.host || '';
          const _serverKeyStr = launchTarget.serverKey || (_hostStr || 'local');
          const _active = (_runningMod._loadTasks ? _runningMod._loadTasks() : []).filter(t =>
            t && t.type === 'serve'
            && ((t.remoteHost || '') === _hostStr || (t.remoteServerKey || '') === _serverKeyStr)
            && (t.status === 'running' || t.status === 'ready' || t._serveReady)
          );
          // Only block when the new model's port genuinely collides with
          // a running serve. Different ports coexist fine (issue #4507).
          if (_active.length) {
            const _newPort = (launchCmd.match(/--port[=\s]+(\d+)/) || [])[1] || '';
            const _clashing = _newPort
              ? _active.filter(t => _runningMod._taskPort(t) === _newPort)
              : _active;
            if (_clashing.length) {
              const _names = _clashing.map(t => t.payload?.repo_id || t.repo || t.name || '?').filter(Boolean);
              const _portNote = _newPort ? ` on port ${_newPort}` : '';
              const _ok = await window.styledConfirm(
                `${_clashing.length} model${_clashing.length === 1 ? '' : 's'} already serving on ${_hostStr || 'local'} (${_names.join(', ')})${_portNote}. Stop it and launch this one?`,
                { title: _newPort ? `Port ${_newPort} in use` : 'Server already running', confirmText: 'Stop & launch', cancelText: 'Cancel' },
              );
              if (!_ok) { _restoreLaunchBtn(); return; }
              // Kill each clashing serve; prefer the rendered Stop button so
              // endpoint cleanup + Ollama unload run normally. Fall back to
              // a raw tmux kill when the Active tab isn't in the DOM.
              for (const t of _clashing) {
                try {
                  const _el = document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`);
                  const _btn = _el?.querySelector('.cookbook-task-action-stop');
                  if (_btn) {
                    _btn.click();
                  } else if (_runningMod._tmuxGracefulKill) {
                    await fetch('/api/shell/exec', {
                      method: 'POST', credentials: 'same-origin',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ command: _runningMod._tmuxGracefulKill(t) }),
                    });
                  }
                } catch (_killErr) { /* best-effort */ }
              }
              await new Promise(r => setTimeout(r, 2500));
            }
          }
        } catch (_e) { /* best-effort */ }

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
        // llama.cpp VRAM-fit preflight. Catches the silent-CPU-fallback
        // trap: when the model + KV cache exceed the selected GPUs' free
        // VRAM, llama-cpp-python doesn't error — it pushes layers/KV to
        // CPU and inference crawls at sub-1 tok/s. Off by default; can
        // be bypassed per-launch via the dialog's "Allow CPU overflow"
        // action, OR persistently by ticking the same-named checkbox.
        if (serveState.backend === 'llamacpp'
            && String(serveState.llama_mode || 'gpu') !== 'cpu'
            && !serveState.llama_cpu_overflow) {
          try {
            const _ctx = Math.max(1, parseInt(serveState.ctx, 10) || 8192);
            // Model size on disk — close enough for GPU footprint of a GGUF.
            const _modelBytes = Number(m?.size_bytes || 0) || Math.round((Number(m?.size_gb || 0)) * 1024 * 1024 * 1024);
            const _modelGb = _modelBytes / (1024 ** 3);
            // KV cache heuristic. ~0.7MB / token / 7.5GB-of-model at fp16
            // KV, scaled linearly by model size. Imperfect but covers
            // the common 7B–70B range within ~20% — good enough to catch
            // overflow before it silently happens.
            const _kvGbPerToken = _modelGb > 0 ? (_modelGb / 7.5) * 0.0007 : 0.0007;
            const _kvGb = _ctx * _kvGbPerToken;
            const _needGb = _modelGb + _kvGb;
            const _selStr = (serveState.gpus || '').trim();
            const _selIdx = _selStr ? _selStr.split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isFinite(n)) : [0];
            // Fetch FRESH GPU data per-launch — the hwfit cache may be
            // stale or for a different host (e.g. user switched server
            // picker without scanning), which used to silently skip the
            // preflight and let the launch silently fall to CPU.
            let _hwGpus = [];
            try {
              const _gh = (launchTarget.host || '').trim();
              const _gp = new URLSearchParams();
              if (_gh) {
                _gp.set('host', _gh);
                const _sp = (_serverByVal?.(launchTarget.serverKey || _gh) || {}).port;
                if (_sp) _gp.set('ssh_port', _sp);
              }
              const _gr = await fetch('/api/cookbook/gpus' + (_gp.toString() ? '?' + _gp : ''), { credentials: 'same-origin' });
              if (_gr.ok) {
                const _gd = await _gr.json();
                _hwGpus = Array.isArray(_gd) ? _gd : (_gd.gpus || []);
              }
            } catch {}
            const _freeFor = (idx) => {
              const g = _hwGpus[idx];
              const mb = g?.free_mb;
              return Number.isFinite(mb) ? mb / 1024 : 0;
            };
            const _selFreeGb = _selIdx.reduce((s, i) => s + _freeFor(i), 0);
            // Skip the gate when we don't have any free-VRAM data (probe
            // failed) — better to let the launch try than silently refuse
            // on a missing data point.
            if (_selFreeGb > 0 && _needGb > _selFreeGb && _modelGb > 0) {
              // Suggest the smallest set of additional GPUs whose free
              // VRAM closes the gap. Greedy by largest-free-first.
              const _candidates = _hwGpus
                .map((g, i) => ({ i, free: _freeFor(i) }))
                .filter(x => !_selIdx.includes(x.i) && x.free > 0)
                .sort((a, b) => b.free - a.free);
              const _addGpus = [];
              let _runFree = _selFreeGb;
              for (const c of _candidates) {
                _addGpus.push(c.i); _runFree += c.free;
                if (_runFree >= _needGb) break;
              }
              const _canAddGpu = _runFree >= _needGb && _addGpus.length > 0;
              // Recommend ctx that just-fits on current selection.
              const _recCtxRaw = Math.floor((_selFreeGb - _modelGb) / _kvGbPerToken);
              const _recCtx = Math.max(1024, Math.floor(_recCtxRaw / 1024) * 1024);
              // Custom modal — styledConfirm only takes 2 buttons; this
              // surface needs up to 4 actions (Reduce / Add GPUs / Allow / Cancel).
              const _action = await new Promise(resolve => {
                const ov = document.createElement('div');
                ov.className = 'modal';
                ov.style.cssText = 'display:flex;align-items:center;justify-content:center;z-index:10050;position:fixed;inset:0;background:rgba(0,0,0,0.4);';
                const _btnRow = [];
                if (_recCtx > 1024 && _recCtx < _ctx) {
                  _btnRow.push(`<button data-vram-action="reduce" class="confirm-btn confirm-btn-primary" style="width:100%;">Reduce ctx to ${_recCtx.toLocaleString()}</button>`);
                }
                if (_canAddGpu) {
                  _btnRow.push(`<button data-vram-action="add_gpus" class="confirm-btn confirm-btn-primary" style="width:100%;">Add GPU${_addGpus.length > 1 ? 's' : ''} ${_addGpus.join(', ')}</button>`);
                }
                _btnRow.push(`<button data-vram-action="allow_cpu" class="confirm-btn confirm-btn-secondary" style="width:100%;">Allow CPU overflow (slow)</button>`);
                _btnRow.push(`<button data-vram-action="cancel" class="confirm-btn confirm-btn-secondary" style="width:100%;">Cancel</button>`);
                ov.innerHTML = '<div class="modal-content" style="max-width:480px;">'
                  + '<div class="modal-header"><h4>Will not fit on selected GPU' + (_selIdx.length > 1 ? 's' : '') + '</h4></div>'
                  + '<div class="modal-body" style="font-size:12px;line-height:1.5;">'
                  +   '<p>Model + KV cache would overflow VRAM on the selected GPU' + (_selIdx.length > 1 ? 's' : '') + '. llama-cpp-python will silently spill to CPU → very slow inference.</p>'
                  +   '<ul style="opacity:0.75;padding-left:18px;">'
                  +     '<li>Model: ~' + _modelGb.toFixed(1) + ' GB</li>'
                  +     '<li>KV cache (ctx ' + _ctx.toLocaleString() + '): ~' + _kvGb.toFixed(1) + ' GB</li>'
                  +     '<li>Total needed: ~' + _needGb.toFixed(1) + ' GB</li>'
                  +     '<li>Free on GPU ' + _selIdx.join(', ') + ': ~' + _selFreeGb.toFixed(1) + ' GB</li>'
                  +   '</ul>'
                  + '</div>'
                  + '<div class="modal-footer" style="flex-direction:column;gap:6px;align-items:stretch;">' + _btnRow.join('') + '</div>'
                  + '</div>';
                document.body.appendChild(ov);
                ov.addEventListener('click', (e) => {
                  const b = e.target.closest('[data-vram-action]');
                  if (b) { ov.remove(); resolve(b.dataset.vramAction); }
                  else if (e.target === ov) { ov.remove(); resolve('cancel'); }
                });
              });
              if (_action === 'cancel' || !_action) { _restoreLaunchBtn(); return; }
              if (_action === 'reduce') {
                const _ctxEl = panel.querySelector('[data-field="ctx"]');
                if (_ctxEl) {
                  _ctxEl.value = String(_recCtx);
                  serveState.ctx = String(_recCtx);
                  _ctxEl.dispatchEvent(new Event('change', { bubbles: true }));
                }
              } else if (_action === 'add_gpus') {
                for (const i of _addGpus) {
                  const _b = panel.querySelector(`.cookbook-gpu-btn[data-gpu="${i}"]`);
                  if (_b && !_b.classList.contains('active')) _b.click();
                }
                const _gpusEl = panel.querySelector('[data-field="gpus"]');
                if (_gpusEl) serveState.gpus = _gpusEl.value;
              } else if (_action === 'allow_cpu') {
                const _ov = panel.querySelector('[data-field="llama_cpu_overflow"]');
                if (_ov) {
                  _ov.checked = true;
                  _ov.dispatchEvent(new Event('change', { bubbles: true }));
                }
                serveState.llama_cpu_overflow = true;
              }
              // After mutation, rebuild the serve cmd preview so the
              // launched cmd matches what the user just chose.
              try { updateCmd(); } catch {}
            }
          } catch (_e) {
            // Preflight is best-effort — never block on its own failure.
          }
        }
        // Pre-launch GPU probe — common failure pattern: vLLM/SGLang launched
        // on a host where no GPU is visible (driver missing, $CUDA_VISIBLE_DEVICES
        // unset, container without --gpus). Catch it BEFORE the user spends
        // minutes watching the task fail.
        const _needsGpu = ['vllm', 'sglang'].includes(serveState.backend)
          || (serveState.backend === 'diffusers');
        if (_needsGpu) {
          try {
            const _probeHost = (launchTarget.host || '').trim();
            const _probeParams = new URLSearchParams();
            if (_probeHost) {
              _probeParams.set('host', _probeHost);
              if (launchTarget.port) _probeParams.set('ssh_port', launchTarget.port);
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
            const _portHost = (launchTarget.host || '').trim();
            const _checkInner = `ss -tlnp 2>/dev/null | awk '$4 ~ /:${_port}$/ {print; exit}' || netstat -tlnp 2>/dev/null | awk '$4 ~ /:${_port}$/ {print; exit}'`;
            const _cmd = _portHost
              ? `ssh -o ConnectTimeout=4 -o StrictHostKeyChecking=no ${_sshPrefix(launchTarget.port)}${_portHost} ${JSON.stringify(_checkInner)}`
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
          delete _saved._replaceTaskId;
          byRepo[repo] = _saved;
          localStorage.setItem(SERVE_STATE_KEY, JSON.stringify(_redactServeStateForStorage({ _byRepo: byRepo, _lastUsed: _saved })));
        } catch {}
        const origEnv = _envState.env;
        const origEnvPath = _envState.envPath;
        const venvVal = panel.querySelector('[data-field="venv"]')?.value?.trim();
        const gpusVal = panel.querySelector('[data-field="gpus"]')?.value?.trim();
        const origGpus = _envState.gpus;
        // Resolve the target host from the visible Server dropdown — the reliable
        // source. Relying on _envState.remoteHost silently sent serves to Local
        // when that value was stale/empty. Pass it explicitly to the launcher.
        const serveHost = launchTarget.host || '';
        const serveServerKey = launchTarget.serverKey || '';
        const serveServerName = launchTarget.serverName || '';
        const _srvEnv = launchTarget.env || '';
        const _srvEnvPath = launchTarget.venv || '';
        // The venv field wins; otherwise fall back to the env configured for the
        // selected server in Settings, so the activation isn't silently dropped
        // when the field is left blank (the per-server venv wasn't being applied).
        if (venvVal) { _envState.env = 'venv'; _envState.envPath = venvVal; }
        else if (_srvEnvPath) { _envState.env = (_srvEnv === 'conda' ? 'conda' : 'venv'); _envState.envPath = _srvEnvPath; }
        if (gpusVal) _envState.gpus = gpusVal;
        // Preflight: launching a GPU engine (llama.cpp / vLLM / SGLang)
        // against the local-in-container target on a host whose hwfit
        // scan reports no GPU backend. That falls through to a CPU build
        // / CPU inference path and is usually NOT what the user wants —
        // they typically have a host-side GPU (AMD/Vulkan, NVIDIA on a
        // different box) that the container can't see. Surface this so
        // the user can pick the host as a remote target instead, or
        // confirm they really meant CPU.
        try {
          const _isLocalInContainer = !serveHost; // empty serveHost == cookbook container's local
          const _wantsGpu = ['llamacpp', 'vllm', 'sglang', 'diffusers'].includes(serveState.backend);
          const _detectedBackend = String(_hwfitCache?.system?.backend || '').toLowerCase();
          const _gpuBackends = ['cuda', 'rocm', 'vulkan', 'metal', 'mps', 'apple'];
          if (_isLocalInContainer && _wantsGpu && _detectedBackend && !_gpuBackends.includes(_detectedBackend)) {
            const _proceed = await window.styledConfirm(
              `The local (in-container) target has no GPU backend detected (hwfit reports: "${_detectedBackend || 'none'}"). ${serveState.backend.toUpperCase()} will run on CPU only and may be unusably slow.\n\nIf this machine has a GPU on the host, add the host as a server in Settings and target that instead. Otherwise launch anyway for CPU inference.`,
              {
                title: 'No GPU on local target',
                confirmText: 'Launch anyway (CPU)',
                cancelText: 'Cancel',
                danger: true,
              },
            );
            if (!_proceed) {
              if (typeof _restoreLaunchBtn === 'function') _restoreLaunchBtn();
              _envState.env = origEnv;
              _envState.envPath = origEnvPath;
              _envState.gpus = origGpus;
              return;
            }
          }
        } catch { /* preflight is best-effort */ }
        try {
          await _withSpinner(_launchBtn, async () => {
            // Pass the exact form values so the running task can be re-opened
            // in the Serve panel pre-filled with these settings (Edit button).
            const taskDisplayName = _serveTaskDisplayName(shortName, m, serveState);
            await _launchServeTask(taskDisplayName, repo, launchCmd, serveState, serveHost, { serverKey: serveServerKey, serverName: serveServerName });
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
        const cmd = _cmdManuallyEdited ? panel.querySelector('.hwfit-serve-cmd').value : _formatServeCmdPreview(panel._cmd);
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
  let deleteChoice = { mode: 'repo' };
  const ggufFiles = _ggufFilesForModel(m);
  if (!skipConfirm) {
    if (ggufFiles.length > 1) {
      deleteChoice = await _ggufDeleteChoice(repo, ggufFiles);
      if (!deleteChoice) return;
    } else if (!(await uiModule.styledConfirm(`Delete ${repo} from cache?`, { confirmText: 'Delete', danger: true }))) {
      return;
    }
  }
  const host = _resolveCacheHost();
  let cmd;
  if (_isWindows()) {
    const _psSingleQuote = (value) => `'${String(value || '').replace(/'/g, "''")}'`;
    const winTarget = target.startsWith('~')
      ? target.replace(/^~/, '$env:USERPROFILE').replace(/\//g, '\\')
      : target.replace(/\//g, '\\');
    if (deleteChoice.mode === 'files') {
      const targets = deleteChoice.files
        .map(f => _safeGgufRelPath(f.rel_path))
        .filter(Boolean)
        .map(rel => `${winTarget}\\${rel.replace(/\//g, '\\')}`);
      if (!targets.length) return;
      cmd = targets.map(p => `Remove-Item -Force ${_psSingleQuote(p)} -ErrorAction SilentlyContinue`).join('; ');
    } else {
      cmd = `Remove-Item -Recurse -Force ${_psSingleQuote(winTarget)} -ErrorAction SilentlyContinue`;
    }
    if (host) {
      const pf = _sshPrefix(_getPort(host));
      cmd = `ssh ${pf}${host} "powershell -Command \\"${cmd}\\""`;
    }
  } else {
    // $HOME expands inside double quotes; ~ would not, so normalize the
    // fallback. Quoting also handles spaces in custom model-dir paths.
    const unixTarget = target.startsWith('~') ? target.replace(/^~/, '$HOME') : target;
    if (deleteChoice.mode === 'files') {
      const targets = deleteChoice.files
        .map(f => _safeGgufRelPath(f.rel_path))
        .filter(Boolean)
        .map(rel => `${target.replace(/\/+$/, '')}/${rel}`);
      if (!targets.length) return;
      cmd = `rm -f ${targets.map(p => _shellPathExpr(p)).join(' ')} && find ${_shellPathExpr(target)} -type d -empty -delete`;
    } else {
      cmd = `rm -rf "${unixTarget}"`;
    }
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
    if (deleteChoice.mode === 'files') {
      if (m && Array.isArray(m.gguf_files)) {
        const removed = new Set(deleteChoice.files.map(f => _safeGgufRelPath(f.rel_path)));
        m.gguf_files = m.gguf_files.filter(f => !removed.has(_safeGgufRelPath(f.rel_path)));
      }
      await _fetchCachedModels(false);
    } else if (itemEl) {
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
      // Drop from the in-memory list so a re-render/filter doesn't resurrect it.
      _cachedAllModels = _cachedAllModels.filter(x => x.repo_id !== repo);
    }
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
  const _target = _selectedServeTarget(document.getElementById('cookbook-modal') || document);
  if (_target.host) {
    payload.remote_host = _target.host;
    if (_target.port) payload.ssh_port = _target.port;
  }
  if (_target.platform) payload.platform = _target.platform;
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
      localStorage.setItem(SERVE_STATE_KEY, JSON.stringify(_redactServeStateForStorage({ _byRepo: byRepo, _lastUsed: _seeded })));
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
  _serverByVal = shared._serverByVal;
  _serverKey = shared._serverKey;
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

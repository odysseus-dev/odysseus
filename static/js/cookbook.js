// ============================================
// COOKBOOK MODULE (v2 — simplified)
// What Fits? + Saved presets, inline action panels
// ============================================

import uiModule from './ui.js';
import spinnerModule from './spinner.js';
import { providerLogo } from './providers.js';
import { makeWindowDraggable } from './windowDrag.js';
import { _diagnose, _showDiagnosis, _clearDiagnosis, _runQuickCmd, ERROR_PATTERNS } from './cookbook-diagnosis.js';
import { _hwfitCache, _hwfitDebounce, _hwfitFetch, _hwfitInit, _hwfitRenderList, _hwfitRenderHw, _renderGpuToggles, _expandModelRow, _fitColors, _hwfitColumns, _cachedModelIds, _gpuToggleTotal, _resetGpuToggleState } from './cookbook-hwfit.js';

// Sub-modules
import {
  initRunning,
  _loadTasks, _saveTasks, _addTask, _removeTask,
  _tmuxCmd, _renderRunningTab, _clearCookbookNotif,
  _launchServeTask, _serveAutoFix, _serveAutoRetry, _serveAutoRetryReplace, _serveAutoRetryRemove,
  _startBackgroundMonitor, _syncFromServer,
  _retryDownload, _nextAvailablePort, _processQueue,
} from './cookbookRunning.js';

import {
  initDownload,
  _setPanelField, _setPanelCheckbox,
  _wirePanelEvents, _runPanelCmd, _runModelDownload, _buildDownloadCmd,
} from './cookbookDownload.js';

import {
  initServe,
  _fetchCachedModels, _cachedAllModels, _filterCachedList, _rerenderCachedModels, _deleteCachedModel,
} from './cookbookServe.js';

const STORAGE_KEY = 'cookbook-presets';
const LAST_STATE_KEY = 'cookbook-last-state';
const SERVE_STATE_KEY = 'cookbook-serve-state';

// Global, once: tag chip rows (.doclib-lang-chips) scroll horizontally on mobile.
// Stop their touch events (capture phase, before any ancestor sees them) so a
// sideways tag scroll never triggers a swipe-to-change-tab / swipe-dismiss
// gesture in ANY modal (cookbook, document library, etc.). We don't preventDefault,
// so the browser's native horizontal scroll of the chips still works.
if (typeof window !== 'undefined' && !window._tagScrollGuardWired) {
  window._tagScrollGuardWired = true;
  ['touchstart', 'touchmove'].forEach(evt => {
    document.addEventListener(evt, (e) => {
      const t = e.target;
      if (t && t.closest && t.closest('.doclib-lang-chips')) e.stopPropagation();
    }, true);
  });
}

// Radio-style check marking which model directory is a server's download target.
// OFF = hollow circle (pickable); ON = checked circle (accent-tinted via CSS).
export const _MODELDIR_CHECK_OFF = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/></svg>';
export const _MODELDIR_CHECK_ON = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="8 12 11 15 16 9"/></svg>';

// Monochrome platform glyphs (currentColor) for a server's OS tag: a penguin for
// Linux, the four-pane logo for Windows, an Android robot for Termux/Android.
function _platformIcon(platform) {
  const k = (platform || '').toLowerCase();
  if (k === 'windows') {
    return '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M3 4.6l8-1.2v8.1H3V4.6zm9-1.3L21 2v9.5h-9V3.3zM3 12.5h8v8.1l-8-1.2v-6.9zm9 0h9V22l-9-1.3v-8.2z"/></svg>';
  }
  if (k === 'termux' || k === 'android') {
    return '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M7 9h10v6.6a1 1 0 0 1-1 1h-.7v2.6a1.15 1.15 0 1 1-2.3 0V16.6h-1.5v2.6a1.15 1.15 0 1 1-2.3 0V16.6H8a1 1 0 0 1-1-1V9zM4.3 9.1a1.15 1.15 0 0 1 2.3 0v4.6a1.15 1.15 0 1 1-2.3 0V9.1zm13.1 0a1.15 1.15 0 0 1 2.3 0v4.6a1.15 1.15 0 1 1-2.3 0V9.1zM8 8a4 4 0 0 1 8 0H8zm1.7-2.6-.8-1.2a.28.28 0 0 1 .47-.3l.83 1.25a4.8 4.8 0 0 1 3.66 0l.83-1.25a.28.28 0 0 1 .47.3L14.3 5.4M9.8 6.6a.62.62 0 1 0 0-1.24.62.62 0 0 0 0 1.24zm4.4 0a.62.62 0 1 0 0-1.24.62.62 0 0 0 0 1.24z"/></svg>';
  }
  if (k === 'linux' || k === 'termux-linux') {
    return '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M12 2a4 4 0 0 0-4 4v4.7c0 .9-.4 1.7-1 2.4-1.2 1.4-2 3-2 4.5C5 20.4 8.1 22 12 22s7-1.6 7-4.4c0-1.5-.8-3.1-2-4.5-.6-.7-1-1.5-1-2.4V6a4 4 0 0 0-4-4zm-1.7 4.8a1 1 0 1 1 0 2 1 1 0 0 1 0-2zm3.4 0a1 1 0 1 1 0 2 1 1 0 0 1 0-2zM12 9.4c.75 0 1.4.45 1.7 1.1h-3.4c.3-.65.95-1.1 1.7-1.1z"/></svg>';
  }
  return '';
}

export let _envState = { env: 'none', envPath: '', hfToken: '', hfTokenConfigured: false, hfTokenMasked: '', gpus: '', remoteHost: '', servers: [], modelPaths: [], platform: '', defaultServer: '' };
let _lastCacheHostVal = null;
let _cookbookOpeningSpinners = [];
export function _lastCacheHost() { return _lastCacheHostVal; }
export function _setLastCacheHost(v) { _lastCacheHostVal = v; }

function _setCookbookOpening(on) {
  // Sidebar (tool-cookbook-btn) deliberately excluded — the inline
  // whirlpool on the sidebar row read as "the click didn't register"
  // rather than "loading", which made users (rightly) think clicks
  // were being eaten. Keep only the icon-rail spinner since the
  // rail is narrow enough that an obvious loading state still helps.
  const targets = [
    document.getElementById('rail-cookbook'),
  ].filter(Boolean);
  if (!on) {
    _cookbookOpeningSpinners.forEach(({ spinner, wrap, target }) => {
      try { spinner?.stop?.(); } catch {}
      try { wrap?.remove?.(); } catch {}
      target?.classList?.remove('cookbook-opening');
    });
    _cookbookOpeningSpinners = [];
    return;
  }
  if (_cookbookOpeningSpinners.length) return;
  targets.forEach(target => {
    const spinner = spinnerModule.create('', 'clean', 'whirlpool');
    spinner._wpSize = target.id === 'rail-cookbook' ? 12 : 13;
    const wrap = document.createElement('span');
    wrap.className = 'cookbook-open-loading';
    wrap.appendChild(spinner.createElement());
    target.appendChild(wrap);
    target.classList.add('cookbook-opening');
    spinner.start();
    _cookbookOpeningSpinners.push({ spinner, wrap, target });
  });
}

/** Build server <option> HTML from _envState.servers. excludeLocal skips local-only entries. */
// True for the local server entry (empty / "local" / "localhost" host).
function _isLocalEntry(s) { return !s || !s.host || s.host === 'local' || s.host.toLowerCase() === 'localhost'; }

// Resolve a dropdown option value to a server entry. Option values are the
// stable HOST string ('local' for the local box) — NOT array indices — because
// `_envState.servers` gets deduped/reordered, which made index-based selection
// silently resolve to the wrong (or local) server. Accepts a numeric index too
// for backwards-compat with any stale value.
function _serverByVal(val) {
  if (val == null || val === 'local' || val === '') return null;
  let s = _envState.servers.find(x => x.host === val);
  if (!s && /^\d+$/.test(String(val))) s = _envState.servers[parseInt(val)];
  return s || null;
}

function _buildServerOpts(excludeLocal = false) {
  // The local server is ALWAYS represented by the synthetic value="local" option
  // (showing its custom name from the "server name" feature). We must therefore
  // skip that same entry in the loop below — otherwise it appeared twice.
  const _localIdx = _envState.servers.findIndex(_isLocalEntry);
  const _localSrv = _localIdx >= 0 ? _envState.servers[_localIdx] : null;
  const _localLabel = (_localSrv && _localSrv.name) ? _localSrv.name : 'Local';
  let html = `<option value="local"${!_envState.remoteHost ? ' selected' : ''}>${esc(_localLabel)}</option>`;
  for (let i = 0; i < _envState.servers.length; i++) {
    const s = _envState.servers[i];
    if (i === _localIdx) continue;                 // already the synthetic "local" option
    if (excludeLocal && _isLocalEntry(s)) continue;
    const label = s.name || s.host || `Server ${i + 1}`;
    const selected = _envState.remoteHost === s.host ? ' selected' : '';
    html += `<option value="${esc(s.host)}"${selected}>${esc(label)}</option>`;
  }
  return html;
}

/** Wrap a command in SSH for a remote host, with proper single-quote escaping. */
export function _sshCmd(host, cmd, port) {
  const portFlag = port && port !== '22' ? `-p ${port} ` : '';
  return `ssh ${portFlag}${host} '${cmd.replace(/'/g, "'\\''")}'`;
}

/** Get SSH port for a given host (or task object) */
function _getPort(hostOrTask) {
  if (!hostOrTask) return '';
  if (typeof hostOrTask === 'object') return hostOrTask.sshPort || _getPort(hostOrTask.remoteHost);
  const srv = _envState.servers.find(s => s.host === hostOrTask);
  return srv?.port || '';
}

/** Get platform for a given host (or task object). Returns 'windows', 'termux', 'linux', or '' */
export function _getPlatform(hostOrTask) {
  if (!hostOrTask) return _envState.platform || '';
  if (typeof hostOrTask === 'object') return hostOrTask.platform || _getPlatform(hostOrTask.remoteHost);
  const srv = _envState.servers.find(s => s.host === hostOrTask);
  return srv?.platform || '';
}

/** Check if the current active server is Windows */
export function _isWindows(hostOrTask) {
  return _getPlatform(hostOrTask) === 'windows';
}

/** Check if the detected (local) hardware is Apple Silicon / Metal. Keys off the
 *  hardware probe's backend rather than a platform string, since a local Mac
 *  reports no platform but does report backend: "metal". */
export function _isMetal() {
  return ['metal', 'mps', 'apple'].includes(String(_hwfitCache?.system?.backend || '').toLowerCase());
}

/** Detect model-specific vLLM optimizations */
function _detectModelOptimizations(modelName) {
  const n = (modelName || '').toLowerCase();
  const opts = { envVars: [], flags: [], tips: [] };

  // Qwen3.5 MoE models
  if (n.includes('qwen3.5') || n.includes('qwen3-') && (n.includes('a10b') || n.includes('a22b') || n.includes('a3b'))) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1', 'VLLM_USE_FLASHINFER_SAMPLER=0', 'OMP_NUM_THREADS=4');
    opts.flags.push('--enable-expert-parallel', '--reasoning-parser qwen3');
    opts.tips.push('MoE optimizations: expert parallel + flashinfer MoE kernels');
  }
  // Qwen3 MoE (non-3.5)
  else if (n.includes('qwen3') && (n.includes('a10b') || n.includes('a22b') || n.includes('a3b'))) {
    opts.envVars.push('VLLM_USE_DEEP_GEMM=0', 'VLLM_USE_FLASHINFER_MOE_FP16=1');
    opts.flags.push('--enable-expert-parallel', '--reasoning-parser qwen3');
    opts.tips.push('MoE optimizations: expert parallel');
  }
  // DeepSeek MoE
  else if (n.includes('deepseek') && (n.includes('v3') || n.includes('r1'))) {
    opts.flags.push('--enable-expert-parallel');
    opts.tips.push('MoE expert parallel for DeepSeek');
  }
  // Speculative decoding — pick the right MTP method per model family.
  // opts.spec.{method,tokens} seed the UI dropdown/input; the actual flag is
  // assembled by the command builder so the user can edit before launching.
  let specDefault = null;
  if (n.includes('qwen3-next') || (n.includes('qwen3.5') && (n.includes('a10b') || n.includes('a22b')))) {
    specDefault = { method: 'qwen3_next_mtp', tokens: 2 };
  } else if (
    (n.includes('deepseek') && (n.includes('v3') || n.includes('v3.1') || n.includes('r1'))) ||
    n.includes('kimi-k2') || n.includes('kimi_k2') ||
    n.includes('glm-4.5') || n.includes('glm4.5') ||
    n.includes('minimax-m1') || n.includes('minimax_m1')
  ) {
    specDefault = { method: 'mtp', tokens: 3 };
  }
  if (specDefault) {
    opts.spec = specDefault;
    opts.flags.push(`--speculative-config '{"method":"${specDefault.method}","num_speculative_tokens":${specDefault.tokens}}'`);
    opts.tips.push(`Speculative decoding (${specDefault.method}, ${specDefault.tokens} tokens): ~1.5-2x faster generation`);
  }

  return opts;
}

/** Detect the right vLLM tool-call-parser based on model name.
 *  Qwen tool-call formats split by generation:
 *   - Qwen3-Coder           → qwen3_coder  (XML <tool_call> with named params)
 *   - Qwen3 (non-coder)     → qwen3_xml    (reasoning/instruct, XML wrapper)
 *   - Qwen2.5 / Qwen2 / 1.5 → hermes       (Qwen2.5 was trained on Hermes format)
 *  Catching "qwen" first and labelling everything qwen3_xml breaks tool
 *  calls on the Qwen2.5 line (the model emits hermes-style which the
 *  qwen3_xml parser doesn't recognise, so the call leaks through as text).
 */
export function _detectToolParser(modelName) {
  const n = (modelName || '').toLowerCase();
  if (n.includes('qwen3') && n.includes('coder')) return 'qwen3_coder';
  if (n.includes('qwen3')) return 'qwen3_xml';
  if (n.includes('qwen')) return 'hermes';   // Qwen2.5 / Qwen2 / Qwen1.5
  if (n.includes('llama-4') || n.includes('llama4')) return 'llama4_json';
  if (n.includes('llama') || n.includes('nemotron')) return 'llama3_json';
  if (n.includes('mistral') || n.includes('mixtral')) return 'mistral';
  if (n.includes('deepseek-v3')) return 'deepseek_v3';
  if (n.includes('deepseek')) return 'deepseek_v3';
  if (n.includes('minimax') && n.includes('m2')) return 'minimax_m2';
  if (n.includes('minimax')) return 'minimax';
  if (n.includes('gemma')) return 'pythonic';
  if (n.includes('glm-4')) return 'glm45';
  if (n.includes('internlm')) return 'internlm';
  if (n.includes('granite')) return 'granite';
  return 'hermes'; // default fallback
}

// ── Backend detection ──

export function _detectBackend(model) {
  if (model?.backend === 'ollama' || model?.is_ollama) {
    return { backend: 'ollama', label: 'Ollama' };
  }
  const q = (model.quant || '').toUpperCase();
  const sysBackend = String(_hwfitCache?.system?.backend || '').toLowerCase();
  const isRocm = sysBackend === 'rocm';

  // Image gen models → diffusers
  if (model.is_image_gen || model.is_diffusion || model._tag === 'image') {
    return { backend: 'diffusers', label: 'Diffusers' };
  }

  // Windows → default to llama.cpp (no vLLM support on Windows)
  if (_isWindows()) {
    return { backend: 'llamacpp', label: 'llama.cpp' };
  }

  // Apple Silicon (Metal) → llama.cpp (GGUF). vLLM/SGLang are CUDA/ROCm-only and
  // don't run on macOS; AWQ/GPTQ/FP8 (vLLM-only) models are already filtered out
  // of metal Cookbook results, so llama.cpp is always the right engine here.
  if (['metal', 'mps', 'apple'].includes(sysBackend)) {
    return { backend: 'llamacpp', label: 'llama.cpp' };
  }

  // AWQ / GPTQ / FP8 → vLLM
  if (/^AWQ|^GPTQ/.test(q) || q === 'FP8') {
    return { backend: 'vllm', label: 'vLLM' };
  }

  // GGUF → llama.cpp. Match the quant tag OR a gguf hint in the repo/path/name:
  // a raw .gguf file often has no quant field, which made it fall through to the
  // vLLM default below.
  const _nm = `${model.repo_id || ''} ${model.path || ''} ${model.name || ''}`.toLowerCase();
  if (model.is_gguf || /^Q[2-8]/.test(q) || /^IQ/.test(q) || q === 'GGUF' || _nm.includes('gguf')) {
    return { backend: 'llamacpp', label: 'llama.cpp' };
  }

  // ROCm/AMD machines should not blindly default HF safetensors models to
  // vLLM. SGLang is the safer OpenAI-compatible default for plain HF text
  // repos there; llama.cpp still wins above whenever the model is GGUF.
  if (isRocm) {
    return { backend: 'sglang', label: 'SGLang' };
  }

  // Unquantized / BF16 / F16 → vLLM
  return { backend: 'vllm', label: 'vLLM' };
}

// ── Command builders ──

export function _shellQuote(value) {
  return "'" + String(value ?? '').replace(/'/g, "'\\''") + "'";
}

export function _psQuote(value) {
  return "'" + String(value ?? '').replace(/'/g, "''") + "'";
}

export function _buildEnvPrefix() {
  if (_isWindows()) return _buildEnvPrefixWindows();
  let parts = [];
  if (_envState.env === 'venv' && _envState.envPath) {
    const p = _envState.envPath;
    const activate = p.endsWith('/bin/activate') ? p : p + '/bin/activate';
    parts.push('source ' + _shellQuote(activate));
  } else if (_envState.env === 'conda' && _envState.envPath) {
    parts.push('eval "$(conda shell.bash hook)" && conda activate ' + _shellQuote(_envState.envPath));
  }
  let envVars = [];
  if (_envState.hfToken) envVars.push('export HF_TOKEN=' + _shellQuote(_envState.hfToken));
  if (_envState.gpus) envVars.push('export CUDA_VISIBLE_DEVICES=' + _shellQuote(_envState.gpus));
  if (envVars.length) parts.push(envVars.join(' && '));
  if (parts.length === 0) return '';
  return parts.join(' && ') + ' &&';
}

function _buildEnvPrefixWindows() {
  let parts = [];
  if (_envState.env === 'venv' && _envState.envPath) {
    const p = _envState.envPath;
    const activate = p.endsWith('\\Scripts\\Activate.ps1') ? p : p + '\\Scripts\\Activate.ps1';
    parts.push('& ' + _psQuote(activate));
  } else if (_envState.env === 'conda' && _envState.envPath) {
    parts.push('conda activate ' + _psQuote(_envState.envPath));
  }
  if (_envState.hfToken) parts.push('$env:HF_TOKEN=' + _psQuote(_envState.hfToken));
  if (_envState.gpus) parts.push('$env:CUDA_VISIBLE_DEVICES=' + _psQuote(_envState.gpus));
  if (parts.length === 0) return '';
  return parts.join('; ') + ';';
}

export function _buildServeCmd(f, modelName, backend) {
  let cmd = '';
  if (backend === 'vllm') {
    const gpuId = f.gpu_id?.trim() || '';
    if (gpuId) cmd += `CUDA_VISIBLE_DEVICES=${gpuId} `;
    if (f.moe_env) {
      const _opts = _detectModelOptimizations(modelName);
      if (_opts.envVars.length) cmd += _opts.envVars.join(' ') + ' ';
    }
    cmd += `vllm serve ${modelName} --host 0.0.0.0 --port ${f.port || '8000'}`;
    cmd += ` --tensor-parallel-size ${f.tp || '1'}`;
    cmd += ` --max-model-len ${f.ctx || '8192'}`;
    cmd += ` --gpu-memory-utilization ${f.gpu_mem || '0.90'}`;
    if (f.swap && f.swap !== '0') cmd += ` --swap-space ${f.swap}`;
    cmd += ` --dtype ${f.dtype || 'auto'}`;
    if (f.max_seqs && f.max_seqs.toString().trim()) cmd += ` --max-num-seqs ${f.max_seqs.toString().trim()}`;
    if (f.enforce_eager) cmd += ' --enforce-eager';
    if (f.trust_remote) cmd += ' --trust-remote-code';
    if (f.prefix_cache) cmd += ' --enable-prefix-caching';
    if (f.auto_tool) cmd += ` --enable-auto-tool-choice --tool-call-parser ${_detectToolParser(modelName)}`;
    if (f.expert_parallel) cmd += ' --enable-expert-parallel';
    if (f.reasoning_parser) {
      const rp = typeof f.reasoning_parser === 'string' && f.reasoning_parser !== 'true'
        ? f.reasoning_parser : (f._reasoning_parser_value || 'qwen3');
      cmd += ` --reasoning-parser ${rp}`;
    }
    if (f.speculative) {
      const _specMethod = (f.spec_method || 'mtp').trim() || 'mtp';
      const _specToksRaw = parseInt(f.spec_tokens, 10);
      const _specToks = (Number.isFinite(_specToksRaw) && _specToksRaw > 0) ? _specToksRaw : 3;
      cmd += ` --speculative-config '{"method":"${_specMethod}","num_speculative_tokens":${_specToks}}'`;
    }
  } else if (backend === 'sglang') {
    const gpuId = f.gpu_id?.trim() || '';
    if (gpuId) cmd += `CUDA_VISIBLE_DEVICES=${gpuId} `;
    cmd += `python3 -m sglang.launch_server --model-path ${modelName} --host 0.0.0.0 --port ${f.port || '30000'}`;
    if (f.tp && f.tp !== '1') cmd += ` --tp ${f.tp}`;
    if (f.ctx) cmd += ` --context-length ${f.ctx}`;
    if (f.gpu_mem && f.gpu_mem !== '0.90') cmd += ` --mem-fraction-static ${f.gpu_mem}`;
    if (f.dtype && f.dtype !== 'auto') cmd += ` --dtype ${f.dtype}`;
    if (f.max_seqs && f.max_seqs.toString().trim()) cmd += ` --max-running-requests ${f.max_seqs.toString().trim()}`;
    if (f.trust_remote) cmd += ' --trust-remote-code';
    if (!f.prefix_cache) cmd += ' --disable-radix-cache';
    if (f.enforce_eager) cmd += ' --disable-cuda-graph';
  } else if (backend === 'llamacpp') {
    const ggufPath = f._gguf_path || 'model.gguf';
    const gpuId = f.gpu_id?.trim() || '';
    const py = _isWindows() ? 'python' : 'python3';
    const lcPrefix = (() => {
      let p = '';
      if (f.unified_mem && !_isWindows()) p += `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 `;
      if (gpuId && !_isWindows()) p += `CUDA_VISIBLE_DEVICES=${gpuId} `;
      return p;
    })();
    if (f.unified_mem && _isWindows()) cmd += `$env:GGML_CUDA_ENABLE_UNIFIED_MEMORY="1"; `;
    if (gpuId && _isWindows()) cmd += `$env:CUDA_VISIBLE_DEVICES="${gpuId}"; `;
    if (!_isWindows()) {
      // Resolve GGUF path once, fail loudly if nothing matched (prevents
      // `--model ""` which causes confusing downstream errors).
      cmd += `MODEL_FILE=${ggufPath} && { [ -n "$MODEL_FILE" ] && [ -f "$MODEL_FILE" ]; } || { echo "ERROR: No GGUF found on this host. Either download the model here, or switch to the server where it's cached."; exit 1; } && `;
    }
    const modelArg = _isWindows() ? `"${ggufPath}"` : `"$MODEL_FILE"`;
    // Prefer the native llama-server binary on Linux — its minja templating
    // renders modern GGUF chat templates that the Python bindings' Jinja2
    // rejects (do_tojson ensure_ascii). Fall back to llama_cpp.server.
    // Don't suppress stderr — surface real errors (missing file, lib, OOM).
    const _lcpServer = `${lcPrefix}${py} -m llama_cpp.server --model ${modelArg} --host 0.0.0.0 --port ${f.port || '8080'} --n_gpu_layers ${f.ngl || '99'} --n_ctx ${f.ctx || '8192'}`;
    if (_isWindows()) {
      cmd += _lcpServer;
    } else {
      cmd += `${lcPrefix}llama-server --model ${modelArg} --host 0.0.0.0 --port ${f.port || '8080'} -ngl ${f.ngl || '99'} -c ${f.ctx || '8192'}`;
      cmd += ` || ${_lcpServer}`;
    }
  } else if (backend === 'ollama') {
    const ollamaPort = f.port || '11434';
    const hostEnv = ollamaPort !== '11434' ? `OLLAMA_HOST=0.0.0.0:${ollamaPort} ` : '';
    cmd = `${hostEnv}ollama serve`;
  } else if (backend === 'diffusers') {
    const gpuStr = f.gpus?.trim();
    if (gpuStr) cmd += `CUDA_VISIBLE_DEVICES=${gpuStr} `;
    cmd += `python3 scripts/diffusion_server.py --model ${modelName} --port ${f.port || '8100'}`;
    if (f.diff_dtype && f.diff_dtype !== 'bfloat16') cmd += ` --dtype ${f.diff_dtype}`;
    if (f.diff_device_map && f.diff_device_map !== 'balanced') cmd += ` --device-map ${f.diff_device_map}`;
    if (f.diff_steps) cmd += ` --steps ${f.diff_steps}`;
    if (f.diff_width) cmd += ` --width ${f.diff_width}`;
    if (f.diff_height) cmd += ` --height ${f.diff_height}`;
    if (f.diff_offload) cmd += ' --cpu-offload';
    if (f.diff_attention_slicing) cmd += ' --attention-slicing';
    if (f.diff_vae_slicing) cmd += ' --vae-slicing';
    if (f.diff_harmonize_gpu) cmd += ` --harmonize-gpu ${f.diff_harmonize_gpu}`;
  }
  return cmd;
}

/** Get inline logo HTML for a model name/repo_id */
export function modelLogo(name) {
  const logo = providerLogo(name);
  const svg = logo || '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="4"/></svg>';
  return `<span style="width:12px;height:12px;display:inline-flex;align-items:center;vertical-align:-2px;margin-right:3px;opacity:${logo ? '0.5' : '0.2'};">${svg}</span>`;
}

// Use shared esc() from ui module
export const esc = uiModule.esc;

// ── Clipboard ──

export function _copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).catch(() => _fallbackCopy(text));
  }
  return _fallbackCopy(text);
}

function _fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (_) {}
  document.body.removeChild(ta);
  return Promise.resolve();
}

// ── Presets (server-synced; localStorage is offline cache) ──
// Presets sync to/from cookbook_state.json via _syncToServer / _syncFromServer.
// _loadPresets reads the cache (which gets refreshed at app boot and on modal open).

export function _loadPresets() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; }
  catch { return []; }
}

export function _savePresets(presets) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(presets));
  // Trigger sync to server (via running module's _syncToServer debounce)
  _saveTasks(_loadTasks());
}

function _envStateForStorage() {
  const { hfToken, ...safeState } = _envState;
  return safeState;
}

function _readStoredEnvState() {
  const stored = JSON.parse(localStorage.getItem(LAST_STATE_KEY) || '{}');
  delete stored.hfToken;
  return stored;
}

export function _persistEnvState() {
  try { localStorage.setItem(LAST_STATE_KEY, JSON.stringify(_envStateForStorage())); }
  catch (_) {}
  _saveTasks(_loadTasks());
}

// ── Dependencies ──

// Category colors removed — using theme CSS classes instead

async function _fetchDependencies() {
  const list = document.getElementById('cookbook-deps-list');
  if (!list) return;
  // Use the shared whirlpool spinner so the user sees the request is in
  // flight (the package list takes a few seconds to enumerate on slow links).
  list.innerHTML = '';
  let _spin = null;
  try {
    const sp = (await import('./spinner.js')).default;
    _spin = sp.createWhirlpool(28);
    _spin.element.style.cssText = 'margin:24px auto 0;display:block;';
    list.appendChild(_spin.element);
    const label = document.createElement('div');
    label.className = 'hwfit-loading';
    label.textContent = 'Loading packages…';
    label.style.cssText = 'text-align:center;opacity:0.5;font-size:11px;margin-top:6px;';
    list.appendChild(label);
  } catch {
    list.innerHTML = '<div class="hwfit-loading">Loading packages...</div>';
  }
  try {
    // Resolve the target server from the deps dropdown so remote-target
    // packages are checked on THAT server's venv (not just the local host).
    let _depHost = '', _depPort = '', _depVenv = '';
    const _dsel = document.getElementById('hwfit-deps-server');
    const _depSrv = _dsel && _dsel.value !== 'local' ? _serverByVal(_dsel.value) : null;
    if (_depSrv) {
      _depHost = _depSrv.host || ''; _depPort = _depSrv.port || ''; _depVenv = _depSrv.envPath || '';
    } else if (_envState.remoteHost) {
      _depHost = _envState.remoteHost; _depPort = _getPort(_envState.remoteHost) || ''; _depVenv = _envState.envPath || '';
    }
    const _pkgParams = new URLSearchParams();
    if (_depHost) {
      _pkgParams.set('host', _depHost);
      if (_depPort) _pkgParams.set('ssh_port', _depPort);
      if (_depVenv) _pkgParams.set('venv', _depVenv);
    }
    const resp = await fetch('/api/cookbook/packages' + (_pkgParams.toString() ? '?' + _pkgParams.toString() : ''));
    const data = await resp.json();
    const pkgs = data.packages || [];
    if (!pkgs.length) { list.innerHTML = '<div class="hwfit-loading">No packages found</div>'; return; }
    const _winUnsupported = new Set(['diffusers', 'hf_transfer', 'vllm', 'rembg', 'gfpgan']);

    const _statusTag = (pkg, isLocal, isSystemDep, winBlocked) => {
      if (winBlocked) return `<span class="cookbook-dep-tag cookbook-dep-na">N/A</span>`;
      if (pkg.installed && isSystemDep) return `<span class="cookbook-dep-tag cookbook-dep-installed" title="Found on selected server">Installed</span>`;
      if (pkg.installed) return `<button class="cookbook-dep-tag cookbook-dep-installed cookbook-dep-installed-btn" title="Installed — click for actions"><span class="cookbook-dep-installed-label">Installed</span><span class="cookbook-dep-caret">&#9662;</span></button>`;
      if (isSystemDep) {
        const depTip = esc(pkg.install_hint || 'Install this OS package on the selected server.');
        const depLabel = pkg.applicable === false ? 'N/A ?' : 'Missing';
        return `<span class="cookbook-dep-tag cookbook-dep-na" title="${depTip}">${depLabel}</span>`;
      }
      return `<button class="cookbook-dep-tag cookbook-dep-install" data-dep-pip="${esc(pkg.pip)}" data-dep-target="${isLocal ? 'local' : 'remote'}">Install</button>`;
    };

    const _depRow = (pkg) => {
      const isLocal = pkg.target === 'local';
      const isSystemDep = pkg.kind === 'system';
      const winBlocked = !isLocal && _isWindows() && _winUnsupported.has(pkg.name);
      const note = pkg.status_note ? `<div class="memory-item-meta" style="font-size:10px;opacity:0.65;margin-top:3px;">${esc(pkg.status_note)}</div>` : '';
      return `<div class="cookbook-dep-row${winBlocked ? ' cookbook-dep-blocked' : ''}" data-pkg-name="${esc(pkg.name)}" data-dep-pip="${esc(pkg.pip || '')}" data-dep-target="${isLocal ? 'local' : 'remote'}" data-dep-kind="${esc(pkg.kind || 'python')}">`
        + `<div class="cookbook-dep-info">`
        + `<div class="memory-item-title">${esc(pkg.name)}</div>`
        + `<div class="memory-item-meta" style="font-size:10px;opacity:0.5;margin-top:2px;">${esc(pkg.desc)}</div>`
        + note
        + `</div>`
        + `<span class="cookbook-dep-tag cookbook-dep-cat">${esc(pkg.category)}</span>`
        + _statusTag(pkg, isLocal, isSystemDep, winBlocked)
        + `</div>`;
    };

    const _section = (title, note, items) =>
      items.length
        ? `<div class="cookbook-dep-section"><span class="cookbook-dep-section-title">${title}</span><span class="cookbook-dep-section-note">${note}</span></div>` + items.map(_depRow).join('')
        : '';

    const _viewingRemote = !!(_dsel && _dsel.value && _dsel.value !== 'local');
    const _appDeps = pkgs.filter(p => p.target === 'local');
    const _serverDeps = pkgs.filter(p => p.target !== 'local');

    list.innerHTML = [
      _viewingRemote ? '' : _section('Odysseus app', 'Run inside the Odysseus app itself.', _appDeps),
      _section('Server', 'Run on the server chosen above (Local, or a remote box over SSH).', _serverDeps),
    ].join('');

    // Shared install/update routine — used by the Install button and the
    // "Update" item in an installed package's ⋮ menu. `upgrade` adds pip -U;
    // `statusEl`, when given, shows "Installing…/Updating…" and is disabled.
    async function _installDep(pipName, pkgName, isLocalOnly, upgrade, statusEl) {
      if (isLocalOnly) {
        _envState.remoteHost = '';
        _envState.env = 'none';
        _envState.envPath = '';
      } else {
        const depsServerSel = document.getElementById('hwfit-deps-server');
        if (depsServerSel) _applyServerSelection(depsServerSel.value);
      }
      const targetHost = isLocalOnly ? 'this server' : (_envState.remoteHost || 'local');
      // Always go through `python -m pip` so the leading token is `python`
      // — matches the /api/model/serve allow-list (bare `pip` is blocked).
      // Inside a venv/conda env, `--user` is invalid (pip refuses), so we
      // only add `--user --break-system-packages` when there's no env —
      // for PEP-668-locked system pythons (Arch, newer Debian).
      const _inEnv = _envState.env === 'venv' || _envState.env === 'conda';
      const _pipFlags = (!_isWindows() && !_inEnv) ? ' --user --break-system-packages' : '';
      const _py = _isWindows() ? 'python' : 'python3';
      const cmd = `${_py} -m pip install${upgrade ? ' -U' : ''}${_pipFlags} "${pipName}"`;
      let envPrefix = '';
      if (_isWindows()) {
        if (_envState.env === 'venv' && _envState.envPath) {
          envPrefix = '& ' + _psQuote(_envState.envPath.endsWith('\\Scripts\\Activate.ps1') ? _envState.envPath : _envState.envPath + '\\Scripts\\Activate.ps1');
        } else if (_envState.env === 'conda' && _envState.envPath) {
          envPrefix = 'conda activate ' + _psQuote(_envState.envPath);
        }
      } else {
        if (_envState.env === 'venv' && _envState.envPath) {
          const p = _envState.envPath;
          envPrefix = 'source ' + _shellQuote(p.endsWith('/bin/activate') ? p : p + '/bin/activate');
        } else if (_envState.env === 'conda' && _envState.envPath) {
          envPrefix = 'eval "$(conda shell.bash hook)" && conda activate ' + _shellQuote(_envState.envPath);
        }
      }
      try {
        const reqBody = {
          repo_id: pipName,
          cmd: cmd,
          remote_host: _envState.remoteHost || undefined,
          ssh_port: _getPort(_envState.remoteHost) || undefined,
          env_prefix: envPrefix || undefined,
          platform: _envState.platform || undefined,
        };
        const res = await fetch('/api/model/serve', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(reqBody),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          // FastAPI HTTPException returns {detail: …}; the route's own
          // path returns {ok:false, error:…}. Surface whichever we get.
          const reason = data.detail || data.error || `HTTP ${res.status}`;
          uiModule.showToast('Install failed: ' + String(reason).slice(0, 200));
          return;
        }
        // _dep flags this as a pip dependency/driver install (not a servable
        // model) so the running-task card doesn't offer a "Serve →" button.
        const payload = { repo_id: pipName, _cmd: cmd, remote_host: _envState.remoteHost || '', _dep: true };
        _addTask(data.session_id, 'pip ' + pkgName, 'download', payload);
        if (statusEl) { statusEl.textContent = upgrade ? 'Updating...' : 'Installing...'; statusEl.disabled = true; }
        uiModule.showToast(`${upgrade ? 'Updating' : 'Installing'} ${pkgName} on ${targetHost}...`);
      } catch (err) {
        uiModule.showToast('Install failed: ' + err.message);
      }
    }

    // Wire install buttons (not-installed packages)
    list.querySelectorAll('.cookbook-dep-install').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const pipName = btn.dataset.depPip;
        const pkgName = btn.closest('.cookbook-dep-row')?.querySelector('.memory-item-title')?.textContent || pipName;
        await _installDep(pipName, pkgName, btn.dataset.depTarget === 'local', !!btn.dataset.upgrade, btn);
      });
    });

    // Wire the ⋮ menu on installed packages — currently just "Update".
    function _showDepMenu(anchor) {
      document.querySelectorAll('.cookbook-dep-menu').forEach(d => d.remove());
      const row = anchor.closest('.cookbook-dep-row');
      if (!row) return;
      const pipName = row.dataset.depPip;
      const pkgName = row.querySelector('.memory-item-title')?.textContent || pipName;
      const isLocalOnly = row.dataset.depTarget === 'local';
      const dropdown = document.createElement('div');
      dropdown.className = 'dropdown cookbook-dep-menu';
      const rect = anchor.getBoundingClientRect();
      const minW = 150;
      let left = Math.min(rect.right - minW, window.innerWidth - minW - 8);
      left = Math.max(8, left);
      dropdown.style.cssText = `position:fixed;display:block;z-index:10001;top:${rect.bottom + 6}px;left:${left}px;right:auto;min-width:${minW}px;max-width:calc(100vw - 16px);background:var(--panel,var(--bg));border:1px solid var(--border);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,0.3);padding:6px;font-size:11px;`;
      const upIco = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>';
      const it = document.createElement('div');
      it.className = 'dropdown-item-compact';
      it.innerHTML = `<span class="dropdown-icon">${upIco}</span><span>Update</span>`;
      it.title = `Update ${pkgName} to the latest version (pip install -U)`;
      it.addEventListener('click', async (e) => {
        e.stopPropagation();
        dropdown.remove();
        await _installDep(pipName, pkgName, isLocalOnly, true, null);
      });
      dropdown.appendChild(it);
      document.body.appendChild(dropdown);
      const close = (ev) => {
        if (!dropdown.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) {
          dropdown.remove();
          document.removeEventListener('click', close, true);
        }
      };
      setTimeout(() => document.addEventListener('click', close, true), 10);
    }
    list.querySelectorAll('.cookbook-dep-installed-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (document.querySelector('.cookbook-dep-menu')) {
          document.querySelectorAll('.cookbook-dep-menu').forEach(d => d.remove());
          return;
        }
        _showDepMenu(btn);
      });
    });
  } catch (err) {
    list.innerHTML = `<div class="hwfit-loading">Error loading packages: ${esc(err.message)}</div>`;
  }
}

// ── Tab wiring ──

function _applyServerSelection(val) {
  if (val === 'local') {
    _envState.remoteHost = '';
    _envState.env = 'none';
    _envState.envPath = '';
    _envState.platform = '';
  } else {
    const s = _serverByVal(val);
    if (s) {
      _envState.remoteHost = s.host;
      _envState.env = s.env || 'none';
      _envState.envPath = s.envPath || '';
      _envState.platform = s.platform || '';
    }
  }
  // Persist + keep every server dropdown in sync, so the choice sticks across
  // re-renders and the scan/download all target the SAME host (this was the
  // bug: the Download/Cache/Deps dropdowns set the host but never saved it, so
  // it silently reverted and downloads/scans hit the wrong server).
  _persistEnvState();
  const _want = _envState.remoteHost || 'local';
  document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
    if (!sel || sel.tagName !== 'SELECT') return;
    // Option values are host strings now ('local' for the local box).
    sel.value = _want;
    // If the host isn't among this select's current options (stale options after
    // the server list changed), the browser leaves the box BLANK/grey even though
    // the value is "set". Rebuild the options so the chosen host has an entry, then
    // re-apply; fall back to 'local' only if it's genuinely gone.
    if (sel.selectedIndex < 0) {
      sel.innerHTML = _buildServerOpts(sel.id === 'hwfit-dl-server');
      sel.value = _want;
      if (sel.selectedIndex < 0) sel.value = 'local';
    }
  });
}

function _wireTabEvents(body) {
  // Tab switching
  body.querySelectorAll('.cookbook-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      body.querySelectorAll('.cookbook-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const backend = tab.dataset.backend;
      body.querySelectorAll('.cookbook-group').forEach(g => {
        g.classList.toggle('hidden', g.dataset.backendGroup !== backend);
      });
      if (backend === 'Search') {
        _hwfitInit();
        _hwfitFetch();
      }
      if (backend === 'Serve') {
        _fetchCachedModels();
      }
      if (backend === 'Dependencies') {
        _fetchDependencies();
      }
    });
  });

  // Mobile: swipe left/right anywhere in the body to move to the next/previous
  // tab. Guarded so it ignores vertical scrolls, tiny moves, and form fields.
  if (!body._swipeWired) {
    body._swipeWired = true;
    let _sx = null, _sy = null;
    body.addEventListener('touchstart', (e) => {
      // Ignore swipes that start in a horizontally-scrollable tag row — those
      // should scroll the chips, not flip the tab.
      if (window.innerWidth > 768 || e.touches.length !== 1
          || e.target.closest('input, textarea, select, .doclib-lang-chips')) { _sx = null; return; }
      _sx = e.touches[0].clientX; _sy = e.touches[0].clientY;
    }, { passive: true });
    body.addEventListener('touchend', (e) => {
      if (_sx === null) return;
      const dx = e.changedTouches[0].clientX - _sx;
      const dy = e.changedTouches[0].clientY - _sy;
      _sx = null;
      // Require a clear horizontal swipe (>60px and mostly horizontal).
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      const tabs = [...body.querySelectorAll('.cookbook-tab')];
      const idx = tabs.findIndex(t => t.classList.contains('active'));
      if (idx < 0) return;
      const next = dx < 0 ? idx + 1 : idx - 1;   // swipe left → next tab
      if (next >= 0 && next < tabs.length) tabs[next].click();
    }, { passive: true });
  }

  // Sync server form DOM → _envState.servers
  function _syncServers() {
    const entries = document.querySelectorAll('.cookbook-server-entry');
    const servers = [];
    entries.forEach(entry => {
      const name = entry.querySelector('.cookbook-srv-name')?.value?.trim() || '';
      const host = entry.querySelector('.cookbook-srv-host')?.value?.trim() || '';
      const port = entry.querySelector('.cookbook-srv-port')?.value?.trim() || '';
      const env = entry.querySelector('.cookbook-srv-env')?.value || 'none';
      const envPath = entry.querySelector('.cookbook-srv-path')?.value?.trim() || '';
      const platform = entry.dataset.platform || '';
      const dirs = [];
      entry.querySelectorAll('.cookbook-modeldir-tag').forEach(tag => {
        // Read from data attribute (authoritative) — never parse displayed text
        const d = (tag.dataset.dir || '').replaceAll('✕', '').replaceAll('✖', '').trim();
        if (d) dirs.push(d);
      });
      // Directory flagged as the download target ('' = default HF cache).
      const dlEl = entry.querySelector('.cookbook-modeldir-dl.active');
      const downloadDir = dlEl ? (dlEl.dataset.dlDir || '') : '';
      servers.push({ name, host, port, env, envPath, modelDirs: dirs, downloadDir, platform });
    });
    _envState.servers = servers;
    // Auto-default: when the user has configured EXACTLY ONE remote server
    // and hasn't picked one yet, select it. Without this, the dropdown
    // stays on "Local" so the eventual serve/scan/launch resolves to no
    // remote host and the backend rejects the call with 403 (Forbidden),
    // which read to the user as a permission bug.
    if (!_envState.remoteHost) {
      const remotes = servers.filter(s => !_isLocalEntry(s));
      if (remotes.length === 1) {
        _envState.remoteHost = remotes[0].host;
        _envState.env = remotes[0].env || 'none';
        _envState.envPath = remotes[0].envPath || '';
      }
    }
    const activeSrv = servers.find(s => s.host === _envState.remoteHost);
    _envState.platform = activeSrv?.platform || '';
    localStorage.setItem('cookbook-last-state', JSON.stringify(_envStateForStorage()));
    _saveTasks(_loadTasks());
    // Reflect the auto-default selection into every server dropdown so the
    // UI matches the resolved host. Done in a microtask so the dropdowns
    // exist by the time we set their .value.
    Promise.resolve().then(() => {
      const _want = _envState.remoteHost || 'local';
      document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
        if (sel && sel.tagName === 'SELECT') sel.value = _want;
      });
    });
  }

  // Wire server form inputs
  document.querySelectorAll('.cookbook-srv-name, .cookbook-srv-host, .cookbook-srv-port, .cookbook-srv-path').forEach(el => {
    el.addEventListener('change', _syncServers);
  });
  document.querySelectorAll('.cookbook-srv-env').forEach(el => {
    el.addEventListener('change', _syncServers);
  });

  // Server selector — the server is global, so switching it here re-scans the
  // main Scan/Download list (#hwfit-list) for the new server's hardware too.
  // (The trending sublist reloads via its own handler in the HF-latest wiring.)
  const dlServer = document.getElementById('hwfit-dl-server');
  if (dlServer) {
    dlServer.addEventListener('change', () => {
      _applyServerSelection(dlServer.value);
      // Reset toggle state (no flicker) so the new server's hardware re-renders.
      _resetGpuToggleState();
      _hwfitFetch();
    });
  }

  // Add server link — switch to Settings tab
  const addServerLink = document.querySelector('.cookbook-dl-add-server');
  if (addServerLink) {
    addServerLink.addEventListener('click', () => {
      const settingsTab = body.querySelector('.cookbook-tab[data-backend="Settings"]');
      if (settingsTab) settingsTab.click();
    });
  }

  // Cache server selector
  const cacheServer = document.getElementById('hwfit-cache-server');
  const cacheDirEl = document.getElementById('hwfit-cache-dir');
  if (cacheServer) {
    cacheServer.addEventListener('change', () => {
      _applyServerSelection(cacheServer.value);
      const val = cacheServer.value;
      let srv;
      if (val === 'local') {
        srv = _envState.servers.find(_isLocalEntry) || _envState.servers[0] || {};
      } else {
        srv = _serverByVal(val) || {};
      }
      if (cacheDirEl) cacheDirEl.value = srv.modelDir || '~/.cache/huggingface/hub';
      const dirsEl = document.querySelector('.cookbook-serve-dirs');
      if (dirsEl) {
        const dirs = (Array.isArray(srv.modelDirs) ? srv.modelDirs : [srv.modelDir || '~/.cache/huggingface/hub']).map(d => d.replaceAll('✕', '').replaceAll('✖', '').trim()).filter(Boolean);
        dirsEl.innerHTML = dirs.map(d => `<span class="cookbook-serve-dir-pill">${esc(d)}</span>`).join('') +
          '<span class="cookbook-serve-dir-edit" title="Edit in Settings">edit</span>';
        dirsEl.querySelector('.cookbook-serve-dir-edit')?.addEventListener('click', () => {
          const settingsTab = body.querySelector('.cookbook-tab[data-backend="Settings"]');
          if (settingsTab) settingsTab.click();
        });
      }
      _fetchCachedModels();
    });
  }

  const scanBtn = document.getElementById('hwfit-cache-scan');
  if (scanBtn) {
    scanBtn.addEventListener('click', () => _fetchCachedModels());
  }

  const editDirsLink = document.querySelector('.cookbook-serve-dir-edit');
  if (editDirsLink) {
    editDirsLink.addEventListener('click', () => {
      const settingsTab = body.querySelector('.cookbook-tab[data-backend="Settings"]');
      if (settingsTab) settingsTab.click();
    });
  }

  const depsServer = document.getElementById('hwfit-deps-server');
  if (depsServer) {
    depsServer.addEventListener('change', () => {
      _applyServerSelection(depsServer.value);
      // Re-fetch the package list for the newly selected server — the installed
      // status is per-server, so the list must refresh on a server switch.
      _fetchDependencies();
    });
  }

  // Serve sort
  const serveSort = document.getElementById('serve-sort');
  if (serveSort) {
    serveSort.addEventListener('change', () => {
      if (_cachedAllModels.length) _rerenderCachedModels();
    });
  }

  // Serve search
  const serveSearch = document.getElementById('serve-search');
  if (serveSearch) {
    let _srvDebounce = null;
    serveSearch.addEventListener('input', () => {
      clearTimeout(_srvDebounce);
      _srvDebounce = setTimeout(() => _filterCachedList(), 200);
    });
  }

  // Select mode — bulk actions
  const selectBtn = document.getElementById('hwfit-cache-select');
  const bulkBar = document.getElementById('serve-bulk-bar');
  if (selectBtn && bulkBar) {
    selectBtn.addEventListener('click', () => {
      const active = selectBtn.classList.toggle('active');
      selectBtn.textContent = active ? 'Cancel' : 'Select';
      bulkBar.classList.toggle('hidden', !active);
      document.querySelectorAll('.serve-select-cb').forEach(dot => {
        dot.style.display = active ? '' : 'none';
        dot.classList.remove('selected');
      });
      _updateBulkCount();
    });

    document.getElementById('hwfit-cached-list')?.addEventListener('click', (e) => {
      if (!selectBtn.classList.contains('active')) return;
      const item = e.target.closest('.memory-item[data-repo]');
      if (!item) return;
      if (e.target.closest('a, .hwfit-cached-menu-btn, .memory-item-btn, .hwfit-serve-panel')) return;
      const dot = item.querySelector('.serve-select-cb');
      if (dot) {
        dot.classList.toggle('selected');
        _updateBulkCount();
      }
    });

    function _updateBulkCount() {
      const count = document.querySelectorAll('.serve-select-cb.selected').length;
      const countEl = document.getElementById('serve-bulk-count');
      if (countEl) countEl.textContent = count + ' selected';
    }

    document.getElementById('serve-bulk-cancel')?.addEventListener('click', () => {
      selectBtn.classList.remove('active');
      bulkBar.classList.add('hidden');
      document.querySelectorAll('.serve-select-cb').forEach(dot => { dot.style.display = 'none'; dot.classList.remove('selected'); });
    });

    document.getElementById('serve-bulk-delete')?.addEventListener('click', async () => {
      const checked = document.querySelectorAll('.serve-select-cb.selected');
      if (!checked.length) return;
      const repos = [];
      checked.forEach(dot => {
        const item = dot.closest('.memory-item[data-repo]');
        if (item?.dataset.repo) repos.push(item.dataset.repo);
      });
      if (!(await uiModule.styledConfirm(`Delete ${repos.length} model(s)? This removes cached files.`, { confirmText: 'Delete', danger: true }))) return;
      for (const repo of repos) {
        const item = document.querySelector(`.memory-item[data-repo="${repo}"]`);
        if (item) await _deleteCachedModel(repo, item, true);
      }
      selectBtn.classList.remove('active');
      bulkBar.classList.add('hidden');
      document.querySelectorAll('.serve-select-cb').forEach(dot => { dot.style.display = 'none'; dot.classList.remove('selected'); });
    });
  }

  // Download input
  const dlBtn = document.getElementById('cookbook-dl-btn');
  const dlInput = document.getElementById('cookbook-dl-repo');
  if (dlBtn && dlInput) {
    function _stripHfUrl(input) {
      let repo = input.trim();
      // Strip Ollama-style "hf.co/" prefix if present (e.g. hf.co/unsloth/...:tag)
      repo = repo.replace(/^hf\.co\//, '');
      const hfMatch = repo.match(/^https?:\/\/huggingface\.co\/([^/]+\/[^/?#]+(?::[^/?#\s]+)?)/);
      if (hfMatch) repo = hfMatch[1];
      return repo;
    }
    // Split `org/repo:tag` (Ollama/llama.cpp style) into repo + include-glob.
    // The `:tag` picks a specific GGUF quantization file from the repo.
    function _splitRepoTag(raw) {
      const m = raw.match(/^([^\s/:]+\/[^\s/:]+):([^\s/]+)$/);
      if (!m) return { repo: raw, include: null };
      return { repo: m[1], include: `*${m[2]}*` };
    }
    const triggerDownload = () => {
      const rawRepo = _stripHfUrl(dlInput.value);
      if (!rawRepo) return;
      const { repo, include: autoInclude } = _splitRepoTag(rawRepo);
      // HuggingFace repo IDs must be `org/model`. A bare model name would 404
      // at snapshot_download time with a raw traceback, so reject it up front.
      if (!/^[^\s/]+\/[^\s/]+$/.test(repo)) {
        uiModule.showToast('Enter a full HuggingFace repo ID like "org/model-name" (or paste the full HF URL).');
        dlInput.focus();
        return;
      }
      // Resolve the host straight from THIS window's server dropdown, by index
      // into the (consistent) servers list. We deliberately don't use
      // _envState.remoteHost — there can be multiple copies of the cookbook
      // state in memory and they disagree on the active host, which is what sent
      // downloads to the wrong server. The dropdown the user sees is the truth.
      const dlSrv = document.getElementById('hwfit-dl-server');
      const srvVal = dlSrv ? dlSrv.value : 'local';
      let host = '';
      if (srvVal !== 'local') {
        host = _serverByVal(srvVal)?.host || '';
      }
      const _hsrv = _envState.servers.find(sv => sv.host === host) || {};
      let env = host ? (_hsrv.env || 'none') : _envState.env;
      let envPath = host ? (_hsrv.envPath || '') : _envState.envPath;
      const payload = { repo_id: repo };
      if (autoInclude) payload.include = autoInclude;
      if (_envState.hfToken) payload.hf_token = _envState.hfToken;
      if (host) { payload.remote_host = host; const _sp3 = _getPort(host); if (_sp3) payload.ssh_port = _sp3; }
      const srvPlatform = _getPlatform(host);
      if (srvPlatform) payload.platform = srvPlatform;
      if (srvPlatform === 'windows') {
        if (env === 'venv' && envPath) {
          payload.env_prefix = '& ' + _psQuote(envPath.endsWith('\\Scripts\\Activate.ps1') ? envPath : envPath + '\\Scripts\\Activate.ps1');
        } else if (env === 'conda' && envPath) {
          payload.env_prefix = 'conda activate ' + _psQuote(envPath);
        }
      } else {
        if (env === 'venv' && envPath) {
          const p = envPath;
          payload.env_prefix = 'source ' + _shellQuote(p.endsWith('/bin/activate') ? p : p + '/bin/activate');
        } else if (env === 'conda' && envPath) {
          payload.env_prefix = 'eval "$(conda shell.bash hook)" && conda activate ' + _shellQuote(envPath);
        }
      }
      const shortName = repo.split('/').pop();
      _retryDownload(shortName, payload);
      dlInput.value = '';
    };
    dlBtn.addEventListener('click', triggerDownload);
    dlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') triggerDownload();
    });
  }

  // Latest HF models that fit — collapsible card list
  const hfToggle = document.getElementById('cookbook-hf-latest-toggle');
  const hfArrow = document.getElementById('cookbook-hf-latest-arrow');
  const hfList = document.getElementById('cookbook-hf-latest-list');
  const hfRefresh = document.getElementById('cookbook-hf-latest-refresh');
  if (hfToggle && hfList) {
    let _loaded = false;
    // Per-server VRAM cache so we don't re-probe on every expand
    const _vramCache = {};
    async function _getSelectedServerVram() {
      // Prefer the "What Fits" dropdown (the main control that shows hardware);
      // fall back to the download dropdown. This is the server the list ranks for.
      const dlSrv = document.getElementById('hwfit-server-select') || document.getElementById('hwfit-dl-server');
      const val = dlSrv?.value || 'local';
      let host = '';
      let sshPort = '';
      let platform = '';
      if (val !== 'local') {
        const s = _serverByVal(val);
        if (s) {
          host = s.host || '';
          sshPort = s.port || '';
          platform = s.platform || '';
        }
      }
      const cacheKey = host || 'local';
      if (_vramCache[cacheKey] !== undefined) return _vramCache[cacheKey];
      // Fetch system info for this server from hwfit
      try {
        const qp = new URLSearchParams();
        if (host) qp.set('host', host);
        if (sshPort) qp.set('ssh_port', sshPort);
        if (platform) qp.set('platform', platform);
        const r = await fetch(`/api/hwfit/system?${qp}`);
        if (r.ok) {
          const sys = await r.json();
          const v = sys?.gpu_vram_gb || 0;
          _vramCache[cacheKey] = v;
          return v;
        }
      } catch {}
      _vramCache[cacheKey] = 0;
      return 0;
    }
    async function _loadLatest() {
      // Match the Dependencies loader: whirlpool spinner + text label so the
      // user gets immediate feedback while the scan runs.
      hfList.innerHTML = '';
      try {
        const sp = (await import('./spinner.js')).default;
        const _spin = sp.createWhirlpool(28);
        _spin.element.style.cssText = 'margin:24px auto 0;display:block;';
        hfList.appendChild(_spin.element);
        const lbl = document.createElement('div');
        lbl.className = 'hwfit-loading';
        lbl.textContent = 'Scanning models…';
        lbl.style.cssText = 'text-align:center;opacity:0.5;font-size:11px;margin-top:6px;';
        hfList.appendChild(lbl);
      } catch {
        hfList.innerHTML = '<div class="hwfit-loading">Scanning models…</div>';
      }
      const vram = await _getSelectedServerVram();
      try {
        let lastErr = '';
        const _fetchLatest = async (v) => {
          const res = await fetch(`/api/cookbook/hf-latest?vram_gb=${v}&limit=10`);
          const data = await res.json();
          if (data.error) lastErr = data.error;   // HF API timeout/rate-limit etc.
          return data.models || [];
        };
        let models = await _fetchLatest(vram);
        // If the VRAM filter wiped everything out (often a flaky/zero hardware
        // probe for a remote server — a huge-VRAM box should fit MORE, not
        // fewer), fall back to the unfiltered trending list so something shows.
        if (!models.length && vram > 0) {
          models = await _fetchLatest(0);
        }
        if (!models.length) {
          // Distinguish "the HF API failed" from "nothing matched" so an outage
          // doesn't masquerade as no-fitting-models.
          const msg = lastErr
            ? `Couldn't load trending models (${esc(lastErr)})`
            : 'No trending models found';
          hfList.innerHTML = `<div class="hwfit-loading">${msg}</div>`;
          return;
        }
        let html = '';
        for (const m of models) {
          const shortName = m.repo_id.split('/').pop() || m.repo_id;
          const org = m.repo_id.includes('/') ? m.repo_id.split('/')[0] : '';
          const meta = [];
          if (org) meta.push(esc(org));
          if (m.needed_vram_gb) meta.push(`~${m.needed_vram_gb}GB`);
          if (m.downloads) meta.push(`${m.downloads.toLocaleString()} downloads`);
          const date = m.createdAt ? new Date(m.createdAt).toISOString().slice(0, 10) : '';
          if (date) meta.push(date);
          html += `<div class="doclib-card memory-item cookbook-hf-latest-card" data-repo="${esc(m.repo_id)}" style="cursor:pointer;">`;
          html += `<div style="flex:1;min-width:0;">`;
          html += `<div class="memory-item-title">${esc(shortName)} <a href="https://huggingface.co/${esc(m.repo_id)}" target="_blank" rel="noopener" class="cookbook-hf-link">HF \u2197</a></div>`;
          html += `<div class="memory-item-meta" style="font-size:10px;opacity:0.5;margin-top:2px;">${meta.join(' \u00b7 ')}</div>`;
          html += `</div>`;
          html += `</div>`;
        }
        hfList.innerHTML = html;
        // Wire card clicks → fill download input
        hfList.querySelectorAll('.cookbook-hf-latest-card').forEach(card => {
          card.addEventListener('click', (e) => {
            if (e.target.closest('a')) return;
            if (dlInput) {
              dlInput.value = card.dataset.repo;
              dlInput.focus();
            }
          });
        });
      } catch (e) {
        hfList.innerHTML = '<div class="hwfit-loading">Failed to load</div>';
      }
    }
    hfToggle.addEventListener('click', () => {
      const isOpen = hfList.style.display !== 'none';
      hfList.style.display = isOpen ? 'none' : 'flex';
      if (hfArrow) hfArrow.style.transform = isOpen ? 'rotate(0deg)' : 'rotate(90deg)';
      if (!isOpen && !_loaded) {
        _loaded = true;
        _loadLatest();
      }
    });
    if (hfRefresh) hfRefresh.addEventListener('click', (e) => {
      e.stopPropagation();
      _loaded = true;
      _loadLatest();
      // If list is hidden, open it
      if (hfList.style.display === 'none') {
        hfList.style.display = 'flex';
        if (hfArrow) hfArrow.style.transform = 'rotate(90deg)';
      }
    });
    // Re-fetch when a server dropdown changes — different server = different
    // hardware/VRAM. Mark the list stale so it reloads for the new server even
    // if it's currently collapsed (otherwise reopening showed the old server's
    // models); reload immediately when it's open.
    const _onServerChange = () => {
      _loaded = false;
      if (hfList.style.display !== 'none') { _loaded = true; _loadLatest(); }
    };
    document.getElementById('hwfit-dl-server')?.addEventListener('change', _onServerChange);
    document.getElementById('hwfit-server-select')?.addEventListener('change', _onServerChange);
  }

  // Server add button, row removal, model-dir add/remove, and per-row wiring
  // are ALL owned by cookbook-hwfit.js's _hwfitInit / _wireServerEntry.
  // A duplicate add handler used to live here and fired alongside the hwfit
  // one, appending two rows per click — removed.


  // HF token — save on change
  const hfInput = document.getElementById('hwfit-hftoken');
  if (hfInput) {
    hfInput.addEventListener('change', () => {
      _envState.hfToken = hfInput.value.trim();
      _persistEnvState();
    });
  }
}

// ── Main render ──

// Build one server entry's HTML — shared by the Settings render loop AND the
// "+ Add server" handler, so a freshly-added server has the IDENTICAL layout
// (Model Directory header, default-server checkmark, trash delete, platform icon).
// forceRemote renders an editable remote entry even before a host is typed
// (a new server's host is empty, which would otherwise read as "Local").
export function _serverEntryHtml(s, i, defaultServer, forceRemote, isNew) {
  const isLocal = (forceRemote || isNew) ? false : (!s.host || s.host === 'local');
  const envOpts = ['none', 'venv'].map(e => `<option value="${e}"${s.env === e ? ' selected' : ''}>${e === 'none' ? 'None' : e}</option>`).join('');
  let html = '';
  html += `<div class="cookbook-server-entry" data-idx="${i}" data-platform="${esc(s.platform || '')}">`;
  const _srvTitle = s.name || (isLocal ? 'Local' : (s.host || `Server ${i + 1}`));
  const _srvKey = isLocal ? 'local' : (s.host || '');
  const _isDefaultSrv = (defaultServer || '') === _srvKey;
  const _pIco = _platformIcon(s.platform);
  const _keyBtn = `<button class="cookbook-server-key-btn" title="Set up SSH key for this server" style="height:22px;box-sizing:border-box;display:inline-flex;align-items:center;position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M12 11l8-8"/><path d="M17 6l3 3"/></svg>Key</button>`;
  const _checkBtn = `<button class="cookbook-server-check-btn" title="Check SSH connection" style="height:22px;box-sizing:border-box;display:inline-flex;align-items:center;position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><polyline points="20 6 9 17 4 12"/></svg>Check</button>`;
  html += `<span class="cookbook-server-title" style="display:flex;align-items:center;gap:6px;width:100%;font-size:13px;font-weight:600;margin-bottom:4px;">`;
  html += `${esc(_srvTitle)}`;
  html += _pIco ? `<span class="cookbook-srv-platform" title="${esc(s.platform || '')}" style="display:inline-flex;align-items:center;opacity:0.55;">${_pIco}</span>` : '';
  html += `<span class="cookbook-srv-test-msg" style="font-size:10px;font-weight:400;opacity:0.55;max-width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;position:relative;top:2px;"></span>`;
  if (isNew) {
    // New server: Cancel (discard) sits top-right; the default toggle only makes
    // sense once the server is saved.
    html += `<span style="margin-left:auto;display:inline-flex;gap:4px;align-items:center;">${_checkBtn}${_keyBtn}<button class="cookbook-server-cancel-btn" title="Discard this new server" style="height:22px;box-sizing:border-box;display:inline-flex;align-items:center;position:relative;top:-2px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>Cancel</button></span>`;
  } else {
    html += `<span style="margin-left:auto;display:inline-flex;gap:4px;align-items:center;">${!isLocal ? _checkBtn + _keyBtn : ''}<span class="cookbook-srv-default${_isDefaultSrv ? ' active' : ''}" title="${_isDefaultSrv ? 'Default server — Cookbook opens here' : 'Make this the default server'}" data-srv-key="${esc(_srvKey)}">${_isDefaultSrv ? _MODELDIR_CHECK_ON : _MODELDIR_CHECK_OFF}<span class="cookbook-srv-default-label">default</span></span></span>`;
  }
  html += `</span>`;
  html += `<div class="cookbook-server-row">`;
  html += `<input type="text" class="hwfit-sf cookbook-srv-name" value="${esc(s.name || (isLocal ? 'Local' : ''))}" placeholder="Name (optional)" style="width:92px;flex-shrink:0;" />`;
  html += `<input type="text" class="hwfit-sf cookbook-srv-host" value="${isLocal ? '' : esc(s.host || '')}" placeholder="e.g. user@ip" style="width:214.5px;flex-shrink:0;box-sizing:border-box;" ${isLocal ? 'readonly' : ''} />`;
  html += `<input type="text" class="hwfit-sf cookbook-srv-port" value="${esc(s.port || '')}" placeholder="Port" title="SSH port (default 22)" style="width:48px;flex-shrink:0;" ${isLocal ? 'readonly' : ''} />`;
  html += `<select class="hwfit-sf cookbook-srv-env">${envOpts}</select>`;
  html += `<input type="text" class="hwfit-sf cookbook-srv-path" value="${esc(s.envPath || '')}" placeholder="${s.platform === 'windows' ? 'venv path' : '~/venv'}" />`;
  html += `<span class="cookbook-dep-tag cookbook-dep-target" style="font-size:8px;flex-shrink:0;min-width:46px;text-align:center;visibility:hidden;">placeholder</span>`;
  html += `<span class="cookbook-srv-actions" style="display:inline-flex;gap:4px;align-items:center;width:78px;flex-shrink:0;justify-content:flex-end;"></span>`;
  html += `</div>`;
  const modelDirs = Array.isArray(s.modelDirs) && s.modelDirs.length ? s.modelDirs : ['~/.cache/huggingface/hub'];
  const activeDlDir = s.downloadDir || '';
  html += `<div class="cookbook-modeldirs" style="margin:2px 0 0 0;display:flex;flex-wrap:wrap;gap:4px;align-items:center;">`;
  html += `<span style="width:100%;font-size:13px;font-weight:600;margin-bottom:3px;">Model Directory <span style="font-weight:400;opacity:0.5;font-size:11px;">— check the one downloads should go to</span></span>`;
  for (let j = 0; j < modelDirs.length; j++) {
    const isDefault = modelDirs[j] === '~/.cache/huggingface/hub';
    const dirVal = isDefault ? '' : modelDirs[j];
    const isTarget = activeDlDir === dirVal;
    const dlBtn = `<span class="cookbook-modeldir-dl${isTarget ? ' active' : ''}" title="${isTarget ? 'Downloads go here' : 'Send downloads here'}" data-dl-dir="${esc(dirVal)}">${isTarget ? _MODELDIR_CHECK_ON : _MODELDIR_CHECK_OFF}</span>`;
    const rmBtn = isDefault ? '' : ' <span class="cookbook-modeldir-rm" title="Remove">✖</span>';
    html += `<span class="cookbook-modeldir-tag${isDefault ? ' cookbook-modeldir-default' : ''}${isTarget ? ' cookbook-modeldir-target' : ''}" data-dir-idx="${j}" data-dir="${esc(modelDirs[j])}">${dlBtn} ${esc(modelDirs[j])}${rmBtn}</span>`;
  }
  html += `<button class="cookbook-modeldir-add" title="Add model directory">+ Add</button>`;
  const _btnStyle = 'margin-left:auto;position:relative;top:-2px;height:22px;box-sizing:border-box;display:inline-flex;align-items:center;';
  if (isNew) {
    // A brand-new server: Save (confirm) sits where Delete would be; Cancel is
    // top-right in the title. Save confirms with a checkmark (auto-saves on edit too).
    html += `<button class="cookbook-server-save-btn" title="Save this server" style="${_btnStyle}"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>Save</button>`;
  } else if (!isLocal) {
    html += `<button class="cookbook-server-rm cookbook-server-rm-btn" title="Delete this server" style="${_btnStyle}"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;flex-shrink:0;"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>Delete</button>`;
  }
  html += `</div>`;
  if (!isLocal) {
    html += `<div class="cookbook-server-key-panel hidden" style="margin-top:6px;flex-direction:column;gap:5px;">`;
    html += `<div style="display:flex;gap:4px;align-items:center;">`;
    html += `<button type="button" class="memory-toolbar-btn cookbook-server-key-gen" style="height:23px;">Generate key</button>`;
    html += `<button type="button" class="memory-toolbar-btn cookbook-server-key-copy" style="height:23px;" disabled>Copy command</button>`;
    html += `<span style="font-size:10px;opacity:0.55;line-height:1.25;">Docker: run this command in your terminal once.</span>`;
    html += `</div>`;
    html += `<textarea class="memory-search-input cookbook-server-key-command" readonly rows="3" style="min-height:58px;resize:vertical;font-family:var(--mono,monospace);font-size:10px;line-height:1.35;">Enter user@host, then generate the key.</textarea>`;
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function _renderRecipes() {
  const body = document.querySelector('#cookbook-modal .cookbook-body');
  if (!body) return;

  const presets = _loadPresets();
  const hasSaved = presets.length > 0;

  let html = '';

  // Tabs
  html += '<div class="cookbook-tabs">';
  html += '<button class="cookbook-tab active" data-backend="Search"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="7 14 12 19 17 14"/><line x1="12" y1="19" x2="12" y2="5"/><line x1="5" y1="21" x2="19" y2="21"/></svg>Download</button>';
  html += '<button class="cookbook-tab" data-backend="Serve"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-1px;margin-right:3px;"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><circle cx="6" cy="6" r="1"/><circle cx="6" cy="18" r="1"/></svg>Serve</button>';
  html += '<button class="cookbook-tab" data-backend="Dependencies"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-1px;margin-right:3px;"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>Dependencies</button>';
  html += '<button class="cookbook-tab" data-backend="Settings"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="vertical-align:-1px;margin-right:3px;"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>Settings</button>';
  html += '</div>';

  // Search group
  html += '<div class="cookbook-group" data-backend-group="Search" style="flex:0 0 auto;">';
  html += '<div class="admin-card" style="display:flex;flex-direction:column;overflow:hidden;">';
  html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">Download</h2>';
  html += '</div>';
  html += '<p class="memory-desc doclib-desc" style="margin-top:6px;">Download from <a href="https://huggingface.co/models" target="_blank" rel="noopener" style="color:var(--accent,var(--red));text-decoration:none;"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:1px;"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>HuggingFace</a> by pasting model link, or download directly in the Scan section below.</p>';
  html += '<div class="hwfit-container" id="hwfit-container">';

  // Section 1: Settings
  const _es = _envState;
  if (!_es.servers) _es.servers = [];
  let _localSeen = false;
  _es.servers = _es.servers.filter(s => {
    const isLocal = !s.host || s.host.toLowerCase() === 'local';
    if (isLocal) {
      s.host = '';
      if (_localSeen) return false;
      _localSeen = true;
    }
    return true;
  });
  if (!_localSeen) {
    _es.servers.unshift({ host: '', env: _es.env || 'none', envPath: _es.envPath || '', modelDir: '~/.cache/huggingface/hub' });
  }
  if (_es.remoteHost && !_es.servers.some(s => s.host === _es.remoteHost)) {
    _es.servers.push({ host: _es.remoteHost, env: _es.env || 'none', envPath: _es.envPath || '', modelDir: '~/.cache/huggingface/hub' });
    _persistEnvState();
  }
  // NOTE: deliberately do NOT auto-pick the first remote server when no host is
  // selected. That fallback turned any momentarily-empty remoteHost (a clobber,
  // a render before the user's pick registered) into the first saved server,
  // silently sending downloads to the wrong server. An empty selection means Local; the user
  // chooses a remote server explicitly via the dropdown.

  // Download input
  html += `<div style="margin-top:7px;margin-bottom:2px;display:flex;gap:4px;align-items:center;">`;
  if (_es.servers.length > 1) {
    html += `<select class="cookbook-field-input hwfit-dl-server" id="hwfit-dl-server" style="height:28px;position:relative;top:0px;">`;
    html += _buildServerOpts(true);
    html += `</select>`;
  } else {
    html += `<input type="hidden" id="hwfit-dl-server" value="local" />`;
  }
  html += `<button class="memory-toolbar-btn cookbook-dl-add-server" title="Add server in Settings" style="height:28px;">add server</button>`;
  html += `</div>`;
  html += `<div class="cookbook-dl-input" style="margin-top:0;">`;
  html += `<input type="text" class="cookbook-dl-repo" id="cookbook-dl-repo" placeholder="org/model-name, HF URL, or org/model:QUANT_TAG" />`;
  html += `<button class="cookbook-btn cookbook-dl-btn" id="cookbook-dl-btn">Download</button>`;
  html += `</div>`;
  // Latest HF models that fit — collapsible card list
  html += `<div style="margin-top:2px;position:relative;top:-8px;">`;
  html += `<div style="display:flex;gap:4px;align-items:center;">`;
  html += `<button type="button" class="memory-toolbar-btn" id="cookbook-hf-latest-toggle" style="flex:1;text-align:left;height:26px;display:flex;align-items:center;gap:6px;border-radius:4px;">`;
  html += `<span id="cookbook-hf-latest-arrow" style="display:inline-block;transition:transform 0.15s;pointer-events:none;">\u25B8</span>`;
  html += `<span style="pointer-events:none;">Trending models that fit your hardware</span>`;
  html += `</button>`;
  html += `<button type="button" class="memory-toolbar-btn" id="cookbook-hf-latest-refresh" title="Refresh" style="height:26px;width:26px;padding:0;border-radius:4px;">\u21BB</button>`;
  html += `</div>`;
  html += `<div id="cookbook-hf-latest-list" style="display:none;margin-top:4px;max-height:320px;overflow-y:auto;flex-direction:column;gap:4px;"></div>`;
  html += `</div>`;

  // Search section
  html += '</div></div></div>';
  html += '<div class="cookbook-group" data-backend-group="Search">';
  html += '<div class="admin-card" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">';
  html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">Scan / Download</h2>';
  html += '</div>';
  html += '<p class="memory-desc doclib-desc" style="margin-top:6px;">Scans your hardware for what models you can run. Hardware is cached; hit the scan button to re-probe after changing GPUs.</p>';
  html += '<div class="hwfit-toolbar" style="margin-top:9px;">';
  html += '<select class="cookbook-field-input hwfit-usecase" id="hwfit-usecase" style="height:28px;">';
  html += '<option value="">Type</option><option value="general">General</option><option value="coding">Coding</option>';
  html += '<option value="reasoning">Reasoning</option><option value="chat">Chat</option>';
  // Image tab removed — text→image gen is gone from this build (only inpaint
   // remains, which uses its own settings panel). Vision (multimodal) stays.
  html += '<option value="multimodal">Vision</option></select>';
  html += '<input type="text" class="cookbook-field-input hwfit-search" id="hwfit-search" placeholder="Search models..." style="flex:1;" />';
  // Quant (Q4/Q8/…) lives next to the search now.
  html += '<select class="cookbook-field-input hwfit-quant" id="hwfit-quant" style="height:28px;">';
  html += '<option value="Q4_K_M">Q4</option><option value="Q8_0">Q8</option>';
  html += '<option value="Q6_K">Q6</option><option value="Q5_K_M">Q5</option>';
  html += '<option value="Q3_K_M">Q3</option><option value="Q2_K">Q2</option>';
  html += '<option value="AWQ-4bit">AWQ</option><option value="FP8">FP8</option>';
  html += '<option value="">Native</option></select>';
  html += '</div>';
  html += '<div class="hwfit-toolbar" style="margin-top:7px;">';
  html += '<select class="cookbook-field-input hwfit-server-select" id="hwfit-server-select" style="height:28px;min-width:88px;position:relative;top:0px;">';
  html += _buildServerOpts(false);
  html += '</select>';
  html += '<div class="hwfit-gpu-toggles" id="hwfit-gpu-toggles"></div>';
  // Scan/refresh button (icon-only) where the quant dropdown used to sit.
  html += '<button type="button" class="hwfit-gpu-btn" id="hwfit-rescan" title="Re-scan hardware" style="flex-shrink:0;position:relative;top:-3px;left:-1px;">↻ RESCAN</button>';
  html += '<button type="button" class="hwfit-gpu-btn hwfit-hw-manual-btn" id="hwfit-hw-manual-btn" title="Set hardware manually" style="flex-shrink:0;position:relative;top:-3px;left:-1px;">EDIT</button>';
  html += '<select class="cookbook-field-input hwfit-sort" id="hwfit-sort" style="display:none">';
  html += '<option value="fit">Fit</option><option value="score">Score</option><option value="vram">VRAM</option>';
  html += '<option value="speed">Speed</option><option value="params">Params</option>';
  html += '<option value="context">Context</option></select>';
  html += '</div>';
  html += '<div class="hwfit-manual-panel hidden" id="hwfit-manual-panel">';
  html += '<span class="hwfit-manual-note" style="font-size:10px;opacity:0.6;width:100%;margin-bottom:2px;">Simulator — these values REPLACE detected hardware.</span>';
  html += '<select class="hwfit-manual-mode"><option value="gpu">GPU</option><option value="ram">RAM</option></select>';
  html += '<label>GPUs<input class="hwfit-manual-gpus" type="text" inputmode="numeric" placeholder="1"></label>';
  html += '<label>VRAM per GPU<input class="hwfit-manual-vram" type="text" inputmode="decimal" placeholder="8 GB"></label>';
  html += '<label>Total RAM<input class="hwfit-manual-ram" type="text" inputmode="decimal" placeholder="32 GB"></label>';
  html += '<select class="hwfit-manual-backend"><option value="cuda">CUDA</option><option value="rocm">ROCm</option></select>';
  html += '<button type="button" class="hwfit-hw-manual-save">✓ Apply</button>';
  html += '<button type="button" class="hwfit-hw-manual-clear">× Clear</button>';
  html += '</div>';
  html += '<div id="hwfit-hw-row" style="display:none;align-items:center;gap:4px;margin-top:3px;padding-top:2px;"><span style="font-size:10px;padding:2px 8px;border-radius:10px;background:color-mix(in srgb, var(--fg) 8%, transparent);color:var(--fg);opacity:0.7;white-space:nowrap;flex-shrink:0;position:relative;top:-1px;">Detected hardware</span><div class="hwfit-hw" id="hwfit-hw" style="flex:1;"></div></div>';
  html += '<div class="hwfit-list" id="hwfit-list"></div>';

  html += '</div></div>';

  // Serve group
  html += '<div class="cookbook-group hidden" data-backend-group="Serve">';
  html += '<div class="admin-card" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">';
  html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">Serve <span id="serve-stats" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal"></span></h2>';
  html += '</div>';
  const _selSrv = _es.servers.find(s => s.host === _es.remoteHost) || _es.servers[0] || {};
  const _srvDirs = (Array.isArray(_selSrv.modelDirs) ? _selSrv.modelDirs : [_selSrv.modelDir || '~/.cache/huggingface/hub']).map(d => d.replaceAll('✕', '').replaceAll('✖', '').trim()).filter(Boolean);
  html += '<div class="cookbook-serve-dirs" style="margin-top:6px;">';
  html += _srvDirs.map(d => `<span class="cookbook-serve-dir-pill">${esc(d)}</span>`).join('');
  html += '<span class="cookbook-serve-dir-edit" title="Edit in Settings">edit</span>';
  html += '</div>';
  html += '<div style="display:flex;gap:4px;align-items:center;margin-top:4px;">';
  html += '<select class="memory-sort-select" id="hwfit-cache-server" style="height:24px;">' + _buildServerOpts(true) + '</select>';
  html += '<select class="memory-sort-select" id="serve-sort" style="height:24px;">';
  html += '<option value="name">Name</option><option value="size-desc">Size \u2193</option><option value="size-asc">Size \u2191</option><option value="recent">Recent</option>';
  html += '</select>';
  html += '</div>';
  html += '<div class="memory-toolbar" style="margin-top:8px;">';
  html += '<div class="memory-category-filters">';
  html += '<input type="text" class="memory-search-input" id="serve-search" placeholder="Search cached models\u2026" style="flex:1;min-width:120px;" />';
  html += '<button class="memory-toolbar-btn" id="hwfit-cache-select">Select</button>';
  html += '</div>';
  html += '<div class="doclib-lang-chips" id="serve-tags"></div>';
  html += '</div>';

  html += '<div class="memory-bulk-bar hidden" id="serve-bulk-bar">';
  html += '<label class="memory-bulk-check-all"><input type="checkbox" id="serve-select-all"> All</label>';
  html += '<span id="serve-bulk-count" style="font-size:10px;opacity:0.5;">0 selected</span>';
  html += '<button class="memory-toolbar-btn danger" id="serve-bulk-delete" style="position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:3px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>Delete</button>';
  html += '<button class="memory-toolbar-btn" id="serve-bulk-cancel" title="Cancel (Esc)" style="margin-left:4px;padding:3px 6px;position:relative;top:-3px;"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';
  html += '</div>';

  html += '<div class="doclib-grid hwfit-cached-list" id="hwfit-cached-list"></div>';
  html += '</div></div>';

  // Dependencies tab
  html += '<div class="cookbook-group hidden" data-backend-group="Dependencies">';
  html += '<div class="admin-card" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">';
  html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">Dependencies</h2>';
  html += '<span style="font-size:10px;opacity:0.5;margin-left:auto;">Server</span>';
  html += '<select class="cookbook-field-input" id="hwfit-deps-server" style="height:28px;min-width:70px;">';
  html += _buildServerOpts(false);
  html += '</select>';
  html += '</div>';
  html += '<p class="memory-desc doclib-desc">Optional packages that extend Odysseus capabilities.</p>';
  html += '<div class="doclib-grid" id="cookbook-deps-list"></div>';
  html += '</div></div>';

  // Settings tab
  // Settings tab — split into two separate `.admin-card` blocks so the
  // HF Token and Server config look like distinct panels (matches the
  // Download tab's block-per-section layout).
  html += '<div class="cookbook-group hidden cookbook-settings-stack" data-backend-group="Settings">';

  // ── HuggingFace Token block ─────────────────────────────────────────
  html += '<div class="admin-card" style="flex:0 0 auto;display:flex;flex-direction:column;">';
  html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">HuggingFace Token</h2>';
  html += '</div>';
  html += '<p class="memory-desc doclib-desc">Personal access token for downloading gated and private models.</p>';
  html += '<div class="memory-toolbar">';
  html += `<div style="display:flex;gap:4px;align-items:center;">`;
  // Bold green check shown when a token is stored (a placeholder can't style a
  // single glyph, so it's its own element next to the input).
  if (_es.hfTokenConfigured) {
    html += `<span class="hwfit-hf-check" title="Token stored" style="font-weight:800;color:var(--green,#50fa7b);font-size:15px;line-height:1;flex-shrink:0;position:relative;top:2px;">✓</span>`;
  }
  const hfPlaceholder = _es.hfTokenConfigured
    ? `Stored (${esc(_es.hfTokenMasked || 'configured')}) - enter a new token to replace`
    : 'hf_...';
  html += `<input type="password" class="memory-search-input" id="hwfit-hftoken" value="${esc(_es.hfToken || '')}" placeholder="${hfPlaceholder}" style="flex:1;" />`;
  html += `</div>`;
  html += '</div>';
  html += '</div>';

  // ── Servers block ───────────────────────────────────────────────────
  html += '<div class="admin-card" style="flex:0 0 auto;display:flex;flex-direction:column;">';
  html += '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;margin-top:-8px;">';
  html += '<h2 style="margin:0;padding:0;line-height:1;">Servers</h2>';
  // Reuse the calendar +New pill: spinning plus, label fades in idea uses
   // the same `.cal-add-btn-text` rules, so styling stays consistent.
  html += '<button class="cal-add-btn cal-add-btn-text" id="cookbook-server-add" title="Add server" style="margin-left:auto;"><span class="cal-add-plus">+</span><span class="cal-add-label">Add</span></button>';
  html += '</div>';
  html += '<p class="memory-desc doclib-desc">Configure SSH servers, install Odysseus keys, choose model directories, and set the default server. Local is this machine.</p>';
  html += '<div class="memory-toolbar cookbook-servers-toolbar" style="margin-top:4px;">';
  html += `<div id="cookbook-servers-list">`;
  for (let i = 0; i < _es.servers.length; i++) {
    html += _serverEntryHtml(_es.servers[i], i, _es.defaultServer || '', false);
  }
  html += `</div>`;
  html += '</div>';

  html += '</div></div>';

  body.innerHTML = html;
  _wireTabEvents(body);

  // Auto-init What Fits
  _hwfitInit();
  _hwfitFetch();
}

// ── Public API ──

import * as Modals from './modalManager.js';

let _rendered = false;

let _closeGen = 0;

// ESC while a Serve card is expanded should collapse just that card, not
// close the whole Cookbook modal. Capture-phase so we run before the
// modal manager's global ESC-to-close handler and can stop it.
if (typeof window !== 'undefined' && !window._cookbookServeEscBound) {
  window._cookbookServeEscBound = true;
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('cookbook-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    // Layer 1: a model row in the scan/download list is highlighted —
    // deselect it before doing anything else.
    const activeRow = modal.querySelector('.hwfit-row-active');
    if (activeRow) {
      e.stopImmediatePropagation();
      e.preventDefault();
      activeRow.classList.remove('hwfit-row-active');
      return;
    }
    const expanded = modal.querySelector('.memory-item.doclib-card-expanded');
    if (!expanded) return;  // nothing expanded — let the modal close normally
    e.stopImmediatePropagation();
    e.preventDefault();
    // Collapse the card (mirror the toggle-close path in cookbookServe.js).
    expanded.querySelector('.hwfit-serve-panel')?.remove();
    expanded.classList.remove('doclib-card-expanded');
    expanded.style.flexDirection = '';
    expanded.style.alignItems = '';
    const list = expanded.closest('.hwfit-cached-list') || document.getElementById('hwfit-cached-list');
    if (list) { list.style.minHeight = ''; list.style.maxHeight = ''; }
  }, true);  // capture
}

export async function open(opts) {
  const modal = document.getElementById('cookbook-modal');
  if (!modal) return;
  // Run any post-open intent (switch tab, prefill search, etc) after the
  // current render pass so the target elements exist.
  const _applyIntent = () => {
    if (!opts) return;
    if (opts.tab) {
      const t = modal.querySelector(`.cookbook-tab[data-backend="${opts.tab}"]`);
      if (t && !t.classList.contains('active')) t.click();
    }
    if (opts.usecase) {
      const u = document.getElementById('hwfit-usecase');
      if (u && u.value !== opts.usecase) { u.value = opts.usecase; u.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    if (opts.serveSearch) {
      const s = document.getElementById('serve-search');
      if (s) { s.value = opts.serveSearch; s.dispatchEvent(new Event('input', { bubbles: true })); }
    }
  };
  // If minimized, restore in place — preserve all state
  if (Modals.isMinimized('cookbook-modal')) {
    Modals.restore('cookbook-modal');
    _renderRunningTab();
    setTimeout(_applyIntent, 0);
    return;
  }
  // If already visible, no-op (but still honour the intent)
  if (!modal.classList.contains('hidden')) {
    setTimeout(_applyIntent, 0);
    return;
  }
  _setCookbookOpening(true);
  try {
  // Invalidate any pending close() animation handlers so they won't re-hide us
  _closeGen++;
  // Clear any leftover inline styles from a previous swipe-dismiss or close animation
  const _content = modal.querySelector('.modal-content');
  if (_content) {
    _content.classList.remove('modal-closing', 'sheet-ready', 'cookbook-modal-entering');
    _content.style.transform = '';
    _content.style.transition = '';
    _content.style.animation = '';
    _content.style.opacity = '';
  }
  modal.style.display = '';
  Modals.register('cookbook-modal', {
    railBtnId: 'rail-cookbook',
    sidebarBtnId: 'tool-cookbook-btn',
    closeFn: () => _doClose(),
    restoreFn: () => { _renderRunningTab(); },
  });
  _wireCookbookDrag(modal);
  await _syncFromServer();
  // `_syncFromServer` lives in cookbookRunning.js and populates *its* _envState
  // (a different object reference than this module's), then mirrors the merged
  // state to localStorage. So ALWAYS hydrate our _envState from that mirror —
  // on a successful sync it holds the freshly-fetched servers; on failure it
  // holds the last-known state. Gating this on `!synced` left the render's
  // _envState empty whenever sync succeeded → "servers don't show".
  try { Object.assign(_envState, _readStoredEnvState()); } catch {}
  // Honour a user-set default server: always land on it when Cookbook opens, so
  // every dropdown (scan/download/serve/cache/deps) starts on the same machine.
  if (_envState.defaultServer) {
    const _dk = _envState.defaultServer;
    if (_dk === 'local') {
      _envState.remoteHost = ''; _envState.env = 'none'; _envState.envPath = ''; _envState.platform = '';
    } else {
      const _ds = (_envState.servers || []).find(s => s.host === _dk);
      if (_ds) { _envState.remoteHost = _ds.host; _envState.env = _ds.env || 'none'; _envState.envPath = _ds.envPath || ''; _envState.platform = _ds.platform || ''; }
    }
  }
  // Re-render on every open AFTER sync so the freshly-fetched state (servers,
  // HF token, presets) is always reflected. Gating this to once-per-page used
  // to freeze a stale/empty servers list whenever the first sync raced or
  // returned before hydration — and since close/reopen doesn't reset the page,
  // only a full reload recovered it. Re-rendering is cheap and the in-progress
  // Running tab is rendered separately just below.
  _renderRecipes();
  _rendered = true;
  _clearCookbookNotif();
  _renderRunningTab();
  if (_content) {
    // Put the panel in its entering state before it becomes visible. On
    // mobile, showing first and adding the class a frame later can paint the
    // sheet at its final position, which makes the slide-up look like a snap.
    _content.classList.add('cookbook-modal-entering');
  }
  modal.classList.remove('hidden');
  if (_content) {
    void _content.offsetWidth;
    _content.addEventListener('animationend', () => {
      _content.classList.remove('cookbook-modal-entering');
    }, { once: true });
  }
  setTimeout(_applyIntent, 0);
  } finally {
    _setCookbookOpening(false);
  }
}

// Make the Cookbook modal draggable (it had no drag wiring at all). We do
// NOT supply a fsClass fullscreen here — that would cover the whole viewport
// incl. the sidebar. Instead tileManager.js handles maximize/tiling (its
// safe-rect sits the window NEXT TO the sidebar), same as tasks/gallery/etc.
let _cookbookDragWired = false;
function _wireCookbookDrag(modal) {
  if (_cookbookDragWired || !modal) return;
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (!content || !header) return;
  _cookbookDragWired = true;
  makeWindowDraggable(modal, {
    content, header,
    skipSelector: '.close-btn, .modal-close',
    // Keep only the "close to the edge" dock gesture for Cookbook. The
    // tileManager side snap is suppressed for this modal so there isn't a
    // second, tighter edge state fighting the working one.
    enableDock: true,
  });
}

function _doClose() {
  const modal = document.getElementById('cookbook-modal');
  if (!modal) return;
  const content = modal.querySelector('.modal-content');
  const myGen = ++_closeGen;
  if (content && !content.classList.contains('modal-closing')) {
    content.classList.add('modal-closing');
    content.addEventListener('animationend', () => {
      if (myGen !== _closeGen) return;
      modal.classList.add('hidden');
      content.classList.remove('modal-closing');
    }, { once: true });
    setTimeout(() => {
      if (myGen !== _closeGen) return;
      if (!modal.classList.contains('hidden')) { modal.classList.add('hidden'); content.classList.remove('modal-closing'); }
    }, 250);
  } else {
    modal.classList.add('hidden');
  }
}

export function close() {
  // Full close — fires registered closeFn, removes badge, unregisters
  if (Modals.isRegistered('cookbook-modal')) {
    Modals.close('cookbook-modal');
  } else {
    _doClose();
  }
}

export function isVisible() {
  const modal = document.getElementById('cookbook-modal');
  if (!modal) return false;
  if (Modals.isMinimized('cookbook-modal')) return false;
  return !modal.classList.contains('hidden');
}

// Close button
document.addEventListener('DOMContentLoaded', () => {
  const closeBtn = document.getElementById('close-cookbook-modal');
  if (closeBtn) closeBtn.addEventListener('click', close);

  const modal = document.getElementById('cookbook-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (uiModule.isTouchInsideModal()) return;
      if (e.target === modal) close();
    });
  }
});

// ── Initialize sub-modules ──

// Shared SSH-port resolver — sub-modules use this via the shared bundle
// instead of redefining it. Kept here as the single source of truth.
function _sshPrefix(port) {
  return port && port !== '22' ? `-p ${port} ` : '';
}

const shared = {
  _envState,
  _sshCmd,
  _getPort,
  _sshPrefix,
  _getPlatform,
  _isWindows,
  _isMetal,
  _buildEnvPrefix,
  _buildServeCmd,
  _shellQuote,
  _psQuote,
  _detectBackend,
  _detectToolParser,
  _detectModelOptimizations,
  _loadPresets,
  _savePresets,
  _copyText,
  _persistEnvState,
  _refreshDependencies: _fetchDependencies,
  _getGpuToggleTotal: () => _gpuToggleTotal,
  modelLogo,
  esc,
};

// Init running module (adds task management, auto-fix, launch, background monitor)
initRunning({
  ...shared,
});

// Init download module (adds SSE, panel rendering, download commands)
initDownload({
  ...shared,
  _addTask,
  _renderRunningTab,
  _loadTasks,
  _saveTasks,
});

// Init serve module (adds cached models, serve panels, launch)
initServe({
  ...shared,
  _launchServeTask,
  _retryDownload,
  _nextAvailablePort,
});

// ── Re-exports for cookbook-diagnosis.js and cookbook-hwfit.js ──
// These modules import from cookbook.js, so we re-export what they need

export {
  _loadTasks, _saveTasks, _addTask, _removeTask,
  _tmuxCmd, _renderRunningTab,
  _launchServeTask, _serveAutoFix, _serveAutoRetry, _serveAutoRetryReplace, _serveAutoRetryRemove,
  _startBackgroundMonitor,
  _setPanelField, _setPanelCheckbox,
  _wirePanelEvents, _runPanelCmd, _runModelDownload, _buildDownloadCmd,
  _serverByVal, _isLocalEntry,
};

const cookbookModule = { open, close, isVisible, startBackgroundMonitor: _startBackgroundMonitor };

export default cookbookModule;

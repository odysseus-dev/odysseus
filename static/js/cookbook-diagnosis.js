// ============================================
// COOKBOOK DIAGNOSIS SUB-MODULE
// Error pattern matching and diagnosis UI
// ============================================

import {
  _envState,
  _loadTasks,
  _removeTask,
  _launchServeTask,
  _buildEnvPrefix,
  _sshCmd,
  _setPanelField,
  _setPanelCheckbox,
  _copyText,
  _persistEnvState,
  _tmuxCmd,
  _serveAutoRetry,
  _serveAutoRetryReplace,
  _serveAutoRetryRemove,
  _serveAutoFix,
  // Plain specifier (no ?v=) — must match every other cookbook.js importer so the
  // browser loads it once. See cookbook-hwfit.js.
} from './cookbook.js';
import uiModule from './ui.js';

// Tiny HTML-escape — keeps the file standalone instead of leaning on a
// shared helper that may not be exported from this module's import surface.
function _diagEsc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Pick an icon for a diagnosis-action button based on the label. The icon
// renders on the LEFT of the button text. Keeps the strokes consistent
// across the set so they read as one family.
function _diagFixIcon(label) {
  const l = String(label || '').toLowerCase();
  const _svg = (path) => `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" class="cookbook-diag-btn-ico" aria-hidden="true">${path}</svg>`;
  if (l.startsWith('retry') || l.includes('relaunch') || l.includes('restart')) {
    // Circular-arrow refresh
    return _svg('<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>');
  }
  if (l.startsWith('copy')) {
    return _svg('<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>');
  }
  if (l.startsWith('edit')) {
    return _svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z"/>');
  }
  if (l.startsWith('open') || l.includes('dependencies')) {
    return _svg('<path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>');
  }
  if (l.startsWith('install') || l.includes('upgrade')) {
    return _svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>');
  }
  if (l.startsWith('kill') || l.startsWith('stop')) {
    return _svg('<rect x="6" y="6" width="12" height="12" rx="1"/>');
  }
  if (l.startsWith('switch') || l.includes('use ')) {
    return _svg('<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>');
  }
  // Default: lightbulb (generic "suggestion")
  return _svg('<path d="M9 21h6"/><path d="M12 17v4"/><path d="M12 3a6 6 0 0 0-4 10.5c1 1 1.5 2 1.5 3.5h5c0-1.5.5-2.5 1.5-3.5A6 6 0 0 0 12 3Z"/>');
}
import spinnerModule from './spinner.js';

// ── Error diagnosis ──

function _openCookbookDependencies(pkgName = '') {
  const cookbook = window.cookbookModule;
  if (cookbook && typeof cookbook.open === 'function') {
    cookbook.open({ tab: 'Dependencies' });
  } else {
    document.getElementById('tool-cookbook-btn')?.click();
  }

  const wanted = String(pkgName || '').toLowerCase();
  const tryHighlight = (attempt = 0) => {
    const modal = document.getElementById('cookbook-modal');
    const tab = modal?.querySelector('.cookbook-tab[data-backend="Dependencies"]');
    if (tab && !tab.classList.contains('active')) tab.click();

    const rows = [...document.querySelectorAll('#cookbook-deps-list [data-pkg-name]')];
    if (!rows.length) {
      if (attempt < 45) setTimeout(() => tryHighlight(attempt + 1), 100);
      return;
    }
    if (!wanted) return;
    const row = rows.find(r => {
      const name = (r.dataset.pkgName || '').toLowerCase();
      const pip = (r.dataset.depPip || '').toLowerCase();
      return name === wanted || pip.includes(wanted) || wanted.includes(name);
    });
    if (row) {
      row.scrollIntoView({ block: 'center' });
      row.classList.add('cookbook-pkg-flash');
      setTimeout(() => row.classList.remove('cookbook-pkg-flash'), 1800);
    }
  };
  tryHighlight();
}

function _openServeEditFromDiagnosis(panel, fields = null) {
  const task = panel?.closest?.('.cookbook-task');
  if (!task) return;
  task.dispatchEvent(new CustomEvent('cookbook:edit-serve', { bubbles: true, detail: { fields } }));
}

function _openCpuServeEdit(panel) {
  _openServeEditFromDiagnosis(panel, {
    backend: 'llamacpp',
    gpus: '',
    tp: '1',
    gpu_mem: '0.80',
    _forceBackend: true,
  });
}

// Infer the gated base repo that single-file checkpoints need configs from
function _inferBaseRepo(text) {
  if (!text) return null;
  const t = text.toLowerCase();
  if (t.includes('sd3.5') || t.includes('stable-diffusion-3.5')) return 'stabilityai/stable-diffusion-3.5-large';
  if (t.includes('sd3') || t.includes('stable-diffusion-3')) return 'stabilityai/stable-diffusion-3-medium-diffusers';
  if (t.includes('flux')) return 'black-forest-labs/FLUX.1-schnell';
  if (t.includes('sdxl') || t.includes('stable-diffusion-xl')) return 'stabilityai/stable-diffusion-xl-base-1.0';
  return null;
}

export const ERROR_PATTERNS = [
  {
    pattern: /No available memory for the cache blocks|Available KV cache memory:.*-/i,
    message: 'No GPU memory left for KV cache after loading model.',
    fixes: [
      { label: 'Retry with GPU mem 0.95', action: (panel) => _serveAutoRetryReplace(panel, '--gpu-memory-utilization', '0.95') },
      { label: 'Retry with context 2048', action: (panel) => _serveAutoRetryReplace(panel, '--max-model-len', '2048') },
      { label: 'Retry with more GPUs (TP=8)', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '8') },
    ],
  },
  {
    pattern: /warming up sampler|max_num_seqs.*gpu_memory_utilization/i,
    message: 'OOM during warmup. Lower GPU memory or max sequences.',
    fixes: [
      { label: 'Retry with GPU mem 0.80', action: (panel) => _serveAutoRetryReplace(panel, '--gpu-memory-utilization', '0.80') },
      { label: 'Retry with --max-num-seqs 64', action: (panel) => _serveAutoRetry(panel, '--max-num-seqs 64') },
      { label: 'Retry with --max-num-seqs 32', action: (panel) => _serveAutoRetry(panel, '--max-num-seqs 32') },
    ],
  },
  {
    pattern: /CUDA out of memory|torch\.cuda\.OutOfMemoryError|CUDA error: out of memory/i,
    message: 'GPU ran out of memory. Try more GPUs (higher TP) or lower context.',
    fixes: [
      { label: 'Retry with TP=2', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '2') },
      { label: 'Retry with TP=4', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '4') },
      { label: 'Retry with GPU mem 0.80', action: (panel) => _serveAutoRetryReplace(panel, '--gpu-memory-utilization', '0.80') },
      { label: 'Retry with context 4096', action: (panel) => _serveAutoRetryReplace(panel, '--max-model-len', '4096') },
      { label: 'Retry with --enforce-eager', action: (panel) => _serveAutoRetry(panel, '--enforce-eager') },
    ],
  },
  {
    pattern: /not divisible by weight quantization|quantization block/i,
    message: 'FP8 MoE quantization is incompatible with this tensor-parallel split.',
    suggestion: 'Suggested action: retry with a lower tensor-parallel size, such as TP=4 or TP=2. If it still fails, use a non-FP8/GGUF version of the model.',
    fixes: [
      { label: 'Retry with TP=4', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '4') },
      { label: 'Retry with TP=2', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '2') },
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    pattern: /There is no module or parameter named ['"]lm_head\.input_scale['"]|lm_head\.input_scale|weight_scale_2/i,
    message: 'vLLM cannot load this ModelOpt LM-head quantized checkpoint with the current runtime.',
    suggestion: 'Suggested action: upgrade vLLM through the environment that provides this CLI (package manager, venv, Docker image, or source checkout), or choose a compatible checkpoint.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('vllm') },
      {
        label: 'Copy upgrade hint',
        action: () => _copyText('Upgrade the vLLM environment that provides the selected vllm CLI, or use a compatible checkpoint. Do not assume Odysseus owns PATH/system/source/Docker installs.'),
      },
    ],
  },
  {
    pattern: /not divisib|must be divisible|attention heads.*divisible/i,
    message: 'Tensor parallel size incompatible with model dimensions.',
    fixes: [
      { label: 'Retry with TP=1', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '1') },
      { label: 'Retry with TP=2', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '2') },
      { label: 'Retry with TP=4', action: (panel) => _serveAutoRetryReplace(panel, '--tensor-parallel-size', '4') },
    ],
  },
  {
    pattern: /Too large swap space|swap space.*total CPU memory/i,
    message: 'Swap space too large for available CPU memory.',
    fixes: [
      { label: 'Retry without swap', action: (panel) => _serveAutoRetryRemove(panel, '--swap-space') },
      { label: 'Retry with swap 1', action: (panel) => _serveAutoRetryReplace(panel, '--swap-space', '1') },
    ],
  },
  {
    pattern: /swap space|not enough.*memory.*cpu|Cannot allocate memory/i,
    message: 'Not enough CPU RAM or swap space.',
    fixes: [
      { label: 'Retry without swap', action: (panel) => _serveAutoRetryRemove(panel, '--swap-space') },
      { label: 'Lower max context to 4096', action: (panel) => _setPanelField(panel, 'ctx', '4096') },
    ],
  },
  {
    pattern: /unrecognized arguments:\s*--swap-space/i,
    message: '--swap-space was removed in newer vLLM versions. Remove it from the command.',
    fixes: [
      { label: 'Retry without swap', action: (panel) => _serveAutoRetryRemove(panel, '--swap-space') },
    ],
  },
  {
    pattern: /Address already in use|bind.*address.*in use/i,
    message: 'Port is already in use. Another server may be running.',
    fixes: [
      { label: 'Kill existing vLLM', action: (panel) => _runQuickCmd(panel, 'pkill -f vllm') },
      { label: 'Use port 8001', action: (panel) => _setPanelField(panel, 'port', '8001') },
    ],
  },
  {
    pattern: /No CUDA GPUs are available|no GPU.*found|CUDA_VISIBLE_DEVICES.*invalid/i,
    message: 'No GPUs visible. Check your GPU selection or driver.',
    fixes: [
      { label: 'Clear GPU selection (use all)', action: (panel) => {
        _setPanelField(panel, 'gpus', '');
        _envState.gpus = '';
        _persistEnvState();
      }},
    ],
  },
  {
    pattern: /403 Forbidden|401 Unauthorized|Access to model.*is restricted|gated repo|not in the authorized list|awaiting a review/i,
    message: 'Gated model. Your HF token IS being sent — but its account must be granted access first: open the model page, accept the license, and wait for approval (Meta models can take a while).',
    // Extract repo name from error text to build HF link
    _repoPattern: /Access to model\s+(\S+)\s+is restricted|gated repo.*?huggingface\.co\/([^\s/]+\/[^\s/]+)/i,
    fixes: [
      { label: 'Request access on HF', action: (panel, _text) => {
        const m = _text && (_text.match(/Access to model\s+(\S+)\s+is restricted/i) || _text.match(/huggingface\.co\/([^\s/]+\/[^\s/]+)/i));
        const repo = m && (m[1] || m[2]);
        if (repo) window.open('https://huggingface.co/' + repo, '_blank');
        else window.open('https://huggingface.co/settings/gated-repos', '_blank');
      }},
      { label: 'Check HF Token', action: (panel) => {
        const el = panel.querySelector('[data-field="hf_token"]');
        if (el) { el.focus(); el.style.borderColor = 'var(--red)'; }
      }},
    ],
  },
  {
    pattern: /Weights for this component appear to be missing|load the component before passing/i,
    message: 'Single-file checkpoint needs a base model for missing components (text encoder, VAE). The base model may be gated — accept the license and set your HF token.',
    fixes: [
      { label: 'Request access to base model', action: (panel, _text) => {
        // Extract gated repo from error, or infer from model name
        const gated = _text && _text.match(/Access to model\s+(\S+)\s+is restricted/i);
        const base = _text && _text.match(/config=([^\s,)]+)/i);
        const model = _text && _text.match(/load model from\s+(\S+)/i);
        const repo = (gated && gated[1]) || (base && base[1]) || _inferBaseRepo(_text);
        if (repo) window.open('https://huggingface.co/' + repo, '_blank');
        else if (model && model[1]) window.open('https://huggingface.co/' + model[1].replace(/[.]$/, ''), '_blank');
      }},
      { label: 'Check HF Token', action: (panel) => {
        const el = panel.querySelector('[data-field="hf_token"]');
        if (el) { el.focus(); el.style.borderColor = 'var(--red)'; }
      }},
    ],
  },
  {
    pattern: /Entry Not Found.*model_index\.json|Could not load model.*Check diffusers/i,
    message: 'Single-file model — needs base config from a gated repo. Accept the license and set your HF token.',
    fixes: [
      { label: 'Request access to base model', action: (panel, _text) => {
        const gated = _text && _text.match(/Access to model\s+(\S+)\s+is restricted/i);
        const repo = (gated && gated[1]) || _inferBaseRepo(_text);
        if (repo) window.open('https://huggingface.co/' + repo, '_blank');
        else window.open('https://huggingface.co/settings/gated-repos', '_blank');
      }},
      { label: 'Check HF Token', action: (panel) => {
        const el = panel.querySelector('[data-field="hf_token"]');
        if (el) { el.focus(); el.style.borderColor = 'var(--red)'; }
      }},
    ],
  },
  {
    pattern: /does not appear to have a file named|not a valid model|No such file or directory.*model/i,
    message: 'Model path or ID not found.',
    fixes: [
      { label: 'Check model name', action: (panel) => {
        const header = panel.querySelector('.hwfit-panel-model');
        if (header) header.style.color = 'var(--red)';
      }},
    ],
  },
  {
    pattern: /NCCL error|ncclSystemError|ncclInternalError/i,
    message: 'Multi-GPU communication (NCCL) failed.',
    fixes: [
      { label: 'Set TP to 1 (single GPU)', action: (panel) => _setPanelField(panel, 'tp', '1') },
      { label: 'Enable enforce eager', action: (panel) => _setPanelCheckbox(panel, 'enforce_eager', true) },
    ],
  },
  {
    pattern: /KV cache.*too (small|large)|max_model_len.*exceeds|maximum.*context/i,
    message: 'Context length too large for available GPU memory.',
    fixes: [
      { label: 'Lower to 8192', action: (panel) => _setPanelField(panel, 'ctx', '8192') },
      { label: 'Lower to 4096', action: (panel) => _setPanelField(panel, 'ctx', '4096') },
      { label: 'Lower to 2048', action: (panel) => _setPanelField(panel, 'ctx', '2048') },
    ],
  },
  {
    pattern: /vllm.*command not found|No module named vllm/i,
    message: 'vLLM is not installed or not in PATH.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('vllm') },
      { label: 'Check environment is set', action: (panel) => {
        const el = panel.querySelector('[data-field="env_type"]');
        if (el) { el.focus(); el.style.borderColor = 'var(--red)'; }
      }},
    ],
  },
  {
    pattern: /sglang.*command not found|No module named sglang|SGLang is not installed/i,
    message: 'SGLang is not installed or not in PATH.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('sglang') },
      { label: 'Copy install command', action: () => _copyText('python3 -m pip install "sglang[all]"') },
    ],
  },
  {
    pattern: /No accelerator \(CUDA, XPU, HPU, NPU, MUSA, MPS\) is available|Triton is not supported on current platform/i,
    message: 'SGLang needs a visible GPU/accelerator on this server.',
    suggestion: 'Suggested action: switch this serve config to llama.cpp for CPU/local serving, or choose a GPU server.',
    fixes: [
      { label: 'Switch to llama.cpp', action: (panel) => _openCpuServeEdit(panel) },
      { label: 'Choose GPU server', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    pattern: /flashinfer.*version.*does not match|flashinfer-cubin version/i,
    message: 'FlashInfer version mismatch.',
    fixes: [
      { label: 'Auto-fix: bypass version check', action: (panel) => _serveAutoFix(panel, 'FLASHINFER_DISABLE_VERSION_CHECK=1'), autofix: true },
      { label: 'Fix properly: pip install matching version', action: () => {} },
    ],
  },
  {
    pattern: /torch\.cuda\.is_available\(\).*False|No CUDA runtime/i,
    message: 'vLLM needs a visible CUDA/ROCm GPU.',
    suggestion: 'Suggested action: switch this serve config to llama.cpp for CPU/local serving, or choose a GPU server.',
    fixes: [
      { label: 'Switch to llama.cpp', action: (panel) => _openCpuServeEdit(panel) },
      { label: 'Choose GPU server', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    pattern: /Engine core initialization failed/i,
    message: 'vLLM engine failed to start. Check the error above.',
    fixes: [
      { label: 'Retry with --enforce-eager', action: (panel) => _serveAutoRetry(panel, '--enforce-eager'), autofix: true },
      { label: 'Retry with context 4096', action: (panel) => _serveAutoRetry(panel, '--max-model-len 4096'), autofix: true },
      { label: 'Lower context to 4096', action: (panel) => _setPanelField(panel, 'ctx', '4096') },
      { label: 'Lower GPU mem to 0.80', action: (panel) => _setPanelField(panel, 'gpu_mem', '0.80') },
    ],
  },
  {
    pattern: /weight_loader.*unexpected keyword|Unexpected key.*state_dict/i,
    message: 'Model format incompatible with this vLLM version.',
    fixes: [
      { label: 'Try trust remote code', action: (panel) => _setPanelCheckbox(panel, 'trust_remote', true) },
    ],
  },
  {
    pattern: /enable-auto-tool-choice requires --tool-call-parser/i,
    message: 'Auto tool choice needs a tool call parser.',
    fixes: [
      { label: 'Retry with --tool-call-parser hermes', action: (panel) => _serveAutoRetry(panel, '--tool-call-parser hermes'), autofix: true },
    ],
  },
  {
    pattern: /Please pass.*trust.remote.code=True|contains custom code which must be executed to correctly load/i,
    message: 'Model requires custom code. Enable --trust-remote-code.',
    fixes: [
      { label: 'Retry with --trust-remote-code', action: (panel) => _serveAutoRetry(panel, '--trust-remote-code'), autofix: true },
    ],
  },
  {
    pattern: /does not recognize this architecture|model type.*but Transformers does not/i,
    message: 'Model architecture too new for installed vLLM/transformers.',
    fixes: [
      { label: 'Try --trust-remote-code', action: (panel) => _serveAutoRetry(panel, '--trust-remote-code'), autofix: true },
      { label: 'Update vLLM on server', action: () => {
        // Use the venv's python3 by absolute path when configured (SSH non-
        // interactive sessions often pick user-site Python over the venv).
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('update-vllm', 'pip-update', `${_vp} -m pip install -U vllm transformers`);
      }},
    ],
  },
  {
    pattern: /Either a revision or a version must be specified|transformers\.integrations\.hub_kernels|kernels\/layer/i,
    message: 'Transformers/kernels package mismatch.',
    fixes: [
      { label: 'Repair kernel package', action: () => {
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('repair-kernels', 'pip-update', `${_vp} -m pip install --user --break-system-packages kernels<0.15`);
      }},
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('sglang') },
    ],
  },
  {
    pattern: /ollama.*command not found/i,
    message: 'Ollama is not installed on this server. Run: curl -fsSL https://ollama.com/install.sh | sh',
    fixes: [
      { label: 'Copy install command', action: () => _copyText('curl -fsSL https://ollama.com/install.sh | sh') },
    ],
  },
  {
    pattern: /llama-server.*command not found|llama\.cpp.*not found|No module named.*llama_cpp|No module named 'starlette_context'/i,
    message: 'llama-cpp-python server is not installed. Run: pip install "llama-cpp-python[server]"',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('llama_cpp') },
      { label: 'Copy install command', action: () => _copyText('pip install "llama-cpp-python[server]"') },
    ],
  },
  {
    pattern: /Windows Error 0xc000001d|Illegal instruction|0xc000001d/i,
    message: 'AVX2 Instruction Set Mismatch: the precompiled llama-cpp-python wheel requires CPU features (AVX2/FMA) that your processor or virtual machine lacks.',
    suggestion: 'Suggested action: switch this serve config to Ollama (highly recommended, has dynamic CPU fallbacks), or choose a remote Linux GPU server.',
    fixes: [
      { label: 'Switch to Ollama', action: (panel) => _openServeEditFromDiagnosis(panel, { backend: 'ollama' }) },
      { label: 'Choose remote server', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    pattern: /CUDA Toolkit not found|Unable to find cudart library|missing:\s*CUDA_CUDART/i,
    message: 'llama.cpp found nvcc, but the CUDA runtime library is missing.',
    suggestion: 'Suggested action: relaunch with the updated runner so llama.cpp builds CPU-only, or install a complete CUDA toolkit/runtime on this server for GPU llama.cpp.',
    fixes: [
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('llama_cpp') },
    ],
  },
  {
    pattern: /No module named ['"]?torch|No module named ['"]?diffusers|diffusers.*command not found/i,
    message: 'Diffusion serving needs PyTorch and diffusers. Install diffusers from Cookbook → Dependencies.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('diffusers') },
      { label: 'Copy install command', action: () => _copyText('python3 -m pip install "diffusers[torch]"') },
    ],
  },
  {
    pattern: /Triton kernels.*Failed to import|cannot import name '\w+' from 'triton_kernels/i,
    message: 'Triton kernels version mismatch. Non-fatal warning — model will still run, just without optimized MoE kernels.',
    fixes: [
      { label: 'Update triton on server', action: () => {
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('update-triton', 'pip-update', `${_vp} -m pip install -U triton triton-kernels`);
      }},
    ],
  },
  {
    pattern: /No space left on device|Disk quota exceeded|ENOSPC/i,
    message: 'Disk full on the server. Free up space before retrying.',
    fixes: [
      { label: 'Check HF cache size', action: (panel) => _runQuickCmd(panel, 'du -sh ~/.cache/huggingface 2>/dev/null') },
    ],
  },
  {
    pattern: /Connection refused|Could not connect|Connection reset by peer/i,
    message: 'Network connection failed. Server may be unreachable or HuggingFace is down.',
    fixes: [
      { label: 'Test HF connectivity', action: (panel) => _runQuickCmd(panel, 'curl -sI https://huggingface.co 2>&1 | head -3') },
    ],
  },
  {
    pattern: /attention_sink|sliding.window.*not supported|sliding_window.*incompatible/i,
    message: 'Model uses attention features unsupported in this vLLM version.',
    fixes: [
      { label: 'Update vLLM on server', action: () => {
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('update-vllm', 'pip-update', `${_vp} -m pip install -U vllm`);
      }},
    ],
  },
  {
    // FlashInfer JIT-compiles attention kernels for the host GPU on first
    // use. If the system /usr/bin/nvcc is older than CUDA 11.8 it can't
    // target sm_89/sm_90 (Ada/Hopper), and the engine workers die before
    // they can report a useful traceback. Two quick paths out: pick a
    // non-flashinfer attention backend, or set CUDACXX to a newer nvcc
    // (vLLM installs nvidia-cuda-nvcc into the venv — point at that).
    pattern: /nvcc fatal\s+:\s+Unsupported gpu architecture 'compute_\d+'/i,
    message: 'FlashInfer is JIT-compiling sampling kernels with an nvcc too old for this GPU (no sm_89 / sm_90 support — pre-CUDA 11.8). Changing the attention backend does not help — flashinfer JITs the SAMPLER too. The clean fix is to set VLLM_USE_FLASHINFER_SAMPLER=0 so vLLM uses its native sampler instead.',
    suggestion: 'Suggested action: relaunch with VLLM_USE_FLASHINFER_SAMPLER=0 prepended. (Confirmed on the QuantTrio/Qwen3.5 model card as the canonical workaround.)',
    fixes: [
      { label: 'Retry with VLLM_USE_FLASHINFER_SAMPLER=0', action: (panel) => _serveAutoRetryReplace(panel, '', 'VLLM_USE_FLASHINFER_SAMPLER=0 ', { prepend: true }) },
      { label: 'Uninstall flashinfer-python', action: () => {
        // Hard fallback: vLLM 0.22 reaches into flashinfer for sampling kernels
        // even with VLLM_USE_FLASHINFER_SAMPLER=0 in some configs. Removing
        // the package forces it onto the native sampler.
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('uninstall-flashinfer', 'pip-update', `${_vp} -m pip uninstall flashinfer-python -y`);
      }},
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    // vLLM <-> torch ABI mismatch: vLLM imports torch.library helpers
    // (`infer_schema`, `register_fake`, etc.) that only exist on newer torch
    // versions. When the installed torch is older, the import fails before
    // any server code runs. Fix is to reinstall vllm (which pulls a matching
    // torch) or upgrade torch directly.
    pattern: /ImportError: cannot import name '[^']+' from 'torch(\.\w+)+'/i,
    message: 'vLLM was built against a newer torch than what is installed. Reinstall vLLM so pip pulls a compatible torch (or upgrade torch directly).',
    fixes: [
      { label: 'Reinstall vLLM (pulls matching torch)', action: () => {
        // Absolute path to the venv's python3 — bare `python3` lands in the
        // wrong site-packages over SSH when ~/.local/bin precedes the venv.
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('reinstall-vllm', 'pip-reinstall', `${_vp} -m pip install --force-reinstall vllm`);
      }},
      { label: 'Upgrade torch only', action: () => {
        const _vp = (_envState.env === 'venv' && _envState.envPath)
          ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3` : 'python3';
        _launchServeTask('upgrade-torch', 'pip-update', `${_vp} -m pip install -U torch`);
      }},
    ],
  },
  {
    // Tail-only + healthy-server suppression. tmux capture-pane returns the
    // entire scrollback every poll, so a one-shot startup traceback would
    // otherwise stick on the panel forever even while the server happily
    // serves /v1/models. Only fire if the traceback is in recent output AND
    // the server isn't currently logging healthy traffic.
    match: (text) => {
      const TAIL = text.slice(-4096);
      if (!/Traceback \(most recent call last\)/i.test(TAIL)) return false;
      // Healthy markers in the tail mean whatever blew up has been recovered
      // from — the server is up and answering requests.
      if (/Application startup complete|"GET \/v1\/[^"]+ HTTP\/[\d.]+" 2\d\d|Uvicorn running on/i.test(TAIL)) return false;
      return true;
    },
    message: 'Python traceback detected — may be a handled error, check logs.',
    fixes: [
      { label: 'Kill vLLM processes', action: (panel) => _runQuickCmd(panel, 'pkill -f vllm') },
    ],
  },
];

export function _diagnose(text) {
  for (const entry of ERROR_PATTERNS) {
    const hit = entry.match ? entry.match(text) : entry.pattern.test(text);
    if (hit) return entry;
  }
  return null;
}

function _diagnosisCopyBundle(task, diagnosis, sourceText, suggestionText) {
  const lines = ['## Odysseus Cookbook troubleshooting'];
  if (task) {
    lines.push(
      '',
      '### Task',
      `- ID: ${task.sessionId || task.id || 'unknown'}`,
      `- Type: ${task.type || 'unknown'}`,
      `- Status: ${task.status || 'unknown'}`,
      `- Model: ${task.payload?.repo_id || task.name || 'unknown'}`,
      `- Host: ${task.remoteHost || 'local'}${task.sshPort ? `:${task.sshPort}` : ''}`,
    );
  }
  lines.push('', '### Diagnosis', diagnosis?.message || '(none)');
  if (suggestionText) lines.push('', '### Suggested action', suggestionText.replace(/^Suggested action:\s*/i, ''));
  const cmd = task?.payload?._cmd || '';
  if (cmd) lines.push('', '### Launch command', '```bash', cmd, '```');
  if (sourceText) lines.push('', '### Captured output', '```text', String(sourceText).trim(), '```');
  return lines.join('\n');
}

export function _showDiagnosis(panel, diagnosis, sourceText) {
  const wasCollapsed = panel._lastDiagMsg === diagnosis.message && panel._diagCollapsed;
  if (panel._diagDismissed === diagnosis.message) return;
  panel._lastDiagMsg = diagnosis.message;
  panel._diagCollapsed = !!wasCollapsed;

  let diag = panel.querySelector('.cookbook-diagnosis');
  if (!diag) {
    diag = document.createElement('div');
    diag.className = 'cookbook-diagnosis';
    const output = panel.querySelector('.cookbook-output-pre');
    if (output) output.after(diag);
    else panel.appendChild(diag);
  }
  diag.classList.remove('hidden');
  diag.innerHTML = '';
  const taskEl = panel?.closest?.('.cookbook-task');
  const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
  const fixes = [...(diagnosis.fixes || [])];
  if (task?.type === 'serve' && task.payload?._cmd && !fixes.some(f => f.label === 'Edit serve')) {
    fixes.push({ label: 'Edit serve', action: (p) => _openServeEditFromDiagnosis(p) });
  }
  const suggestionText = diagnosis.suggestion || (fixes.length
    ? `Suggested action: ${fixes[0].label}.`
    : 'Suggested action: copy the error and adjust the serve settings.');

  panel._diagCollapsed = false;

  // Top-right toolbar: Copy bundle + × dismiss. Restored after user feedback
  // — without them there's no way to quietly close a stale diagnosis or grab
  // the full error+context for a forum/discord paste.
  const toolbar = document.createElement('div');
  toolbar.className = 'cookbook-diag-toolbar';
  toolbar.style.cssText = 'display:flex;justify-content:flex-end;align-items:center;gap:4px;margin-bottom:-2px;';

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'cookbook-diag-copy';
  copyBtn.title = 'Copy diagnosis details';
  copyBtn.setAttribute('aria-label', 'Copy diagnosis');
  copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  copyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const bundle = _diagnosisCopyBundle(task, diagnosis, sourceText, suggestionText);
    try {
      await navigator.clipboard.writeText(bundle);
      copyBtn.classList.add('copied');
      setTimeout(() => { if (copyBtn.isConnected) copyBtn.classList.remove('copied'); }, 1200);
    } catch (_) {}
  });

  const dismissBtn = document.createElement('button');
  dismissBtn.type = 'button';
  dismissBtn.className = 'cookbook-diag-dismiss';
  dismissBtn.title = 'Dismiss diagnosis';
  dismissBtn.setAttribute('aria-label', 'Dismiss');
  dismissBtn.textContent = '×';
  dismissBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    panel._diagDismissed = diagnosis.message;
    _clearDiagnosis(panel);
  });

  toolbar.appendChild(copyBtn);
  toolbar.appendChild(dismissBtn);
  diag.appendChild(toolbar);

  const body = document.createElement('div');
  body.className = 'cookbook-diag-body';
  const msg = document.createElement('div');
  msg.className = 'cookbook-diag-message';
  msg.textContent = diagnosis.message;
  body.appendChild(msg);
  const suggestion = document.createElement('div');
  suggestion.className = 'cookbook-diag-suggestion';
  suggestion.textContent = suggestionText;
  body.appendChild(suggestion);
  diag.appendChild(body);

  const runFix = async (fix, button, busyLabel = fix.label, onStart = null, onDone = null) => {
    if (!fix || !button || button.dataset.busy) return;
    button.dataset.busy = '1';
    const _orig = button.textContent;
    const wp = spinnerModule.createWhirlpool(12);
    wp.element.style.cssText = 'display:inline-block;vertical-align:middle;width:12px;height:12px;margin-right:5px;';
    button.textContent = '';
    button.appendChild(wp.element);
    const _lbl = document.createElement('span');
    _lbl.textContent = busyLabel;
    _lbl.style.verticalAlign = 'middle';
    button.appendChild(_lbl);
    try {
      if (typeof onStart === 'function') onStart();
      await fix.action(panel, sourceText);
    } catch (err) {
      console.error('[cookbook] diagnosis fix failed', err);
    } finally {
      if (button.isConnected) {
        try { wp.destroy(); } catch {}
        button.textContent = _orig;
        delete button.dataset.busy;
      }
      if (typeof onDone === 'function') onDone();
    }
  };

  if (fixes.length) {
    // Always render fixes as inline buttons. The old "Actions ▾" dropdown
    // (for >3 fixes) was broken — the menu wouldn't open in some panels and
    // hid useful actions behind a non-working affordance. Inline buttons wrap
    // naturally in `.cookbook-diag-fixes` (flex-wrap) so a long list reflows
    // onto multiple rows instead of getting collapsed.
    const row = document.createElement('div');
    row.className = 'cookbook-diag-fixes';
    for (const fix of fixes) {
      const btn = document.createElement('button');
      btn.className = 'cookbook-btn cookbook-diag-btn';
      btn.type = 'button';
      btn.innerHTML = _diagFixIcon(fix.label) + '<span class="cookbook-diag-btn-label">' + _diagEsc(fix.label) + '</span>';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        runFix(fix, btn);
      });
      row.appendChild(btn);
    }
    body.appendChild(row);
  }
}

export function _clearDiagnosis(panel) {
  panel._lastDiagMsg = null;
  const diag = panel.querySelector('.cookbook-diagnosis');
  if (diag) { diag.innerHTML = ''; diag.classList.add('hidden'); }
}

// ── Quick command ──

export async function _runQuickCmd(panel, cmd) {
  let fullCmd = cmd;
  if (_envState.remoteHost) {
    fullCmd = _sshCmd(_envState.remoteHost, cmd);
  }
  const diag = panel.querySelector('.cookbook-diagnosis');
  if (diag) { diag.classList.remove('hidden'); diag.textContent = `Running: ${fullCmd}...`; }

  try {
    const res = await fetch('/api/shell/stream', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: fullCmd }),
    });
    if (diag) diag.textContent = res.ok ? `Done: ${cmd}` : `Failed (HTTP ${res.status})`;
  } catch (e) {
    if (diag) diag.textContent = `Error: ${e.message}`;
  }
}

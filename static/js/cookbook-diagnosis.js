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

// Re-exported so callers (Launch-tab pre-flight) can deep-link into the
// Dependencies tab + auto-expand a specific backend's recipe panel and
// pre-select the model they were trying to launch.
export function openCookbookDependencies(pkgName = '', opts = {}) {
  _openCookbookDependencies(pkgName, opts);
}
function _openCookbookDependencies(pkgName = '', opts = {}) {
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
      // Pre-flight deep link: auto-expand the recipe panel + pre-select
      // the model the user was trying to launch. The dropdown values are
      // now full model ids (sourced from _cachedModelIds), so we match by
      // exact value first, then fall back to a substring match.
      if (opts.expandRecipe) {
        const caret = row.querySelector('[data-dep-recipe-toggle]');
        if (caret && caret.getAttribute('aria-expanded') !== 'true') caret.click();
        if (opts.model) {
          const sel = document.querySelector(`[data-dep-recipe-pick="${CSS.escape(opts.expandRecipe)}"]`);
          if (sel) {
            const wanted = String(opts.model);
            let matched = false;
            for (let i = 0; i < sel.options.length; i++) {
              if (sel.options[i].value === wanted) {
                sel.value = wanted; matched = true; break;
              }
            }
            if (!matched) {
              for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value && wanted.includes(sel.options[i].value)) {
                  sel.value = sel.options[i].value; matched = true; break;
                }
              }
            }
            if (matched) sel.dispatchEvent(new Event('change'));
          }
        }
      }
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

function _taskForDiagnosisPanel(panel) {
  const taskEl = panel?.closest?.('.cookbook-task');
  const taskId = taskEl?.dataset?.taskId || '';
  if (!taskId) return null;
  return (_loadTasks() || []).find(t => t.sessionId === taskId) || null;
}

function _pythonFromServeCmd(cmd) {
  const s = String(cmd || '');
  const abs = s.match(/(?:^|\s)(\/[^\s]+\/bin\/python3?)(?=\s+-m\s+(?:sglang\.launch_server|mlx_lm\.server))/);
  if (abs) return abs[1];
  const rel = s.match(/(?:^|\s)(python3?)(?=\s+-m\s+(?:sglang\.launch_server|mlx_lm\.server))/);
  return rel ? rel[1] : '';
}

function _pythonForDiagnosisPanel(panel) {
  const task = _taskForDiagnosisPanel(panel);
  const fromCmd = _pythonFromServeCmd(task?.payload?._cmd || '');
  if (fromCmd) return fromCmd;
  return (_envState.env === 'venv' && _envState.envPath)
    ? `${_envState.envPath.replace(/\/+$/, '')}/bin/python3`
    : 'python3';
}

function _sglangKernelRepairCommand(panel) {
  return `${_pythonForDiagnosisPanel(panel)} -m pip install -U --force-reinstall --no-cache-dir sglang-kernel`;
}

function _mlxLmInstallCommand(panel) {
  return `${_pythonForDiagnosisPanel(panel)} -m pip install -U mlx-lm`;
}

async function _repairSglangKernel(panel) {
  const task = _taskForDiagnosisPanel(panel);
  uiModule.showToast('Repairing sglang-kernel on the selected server...');
  await _launchServeTask(
    'repair-sglang-kernel',
    'pip-update',
    _sglangKernelRepairCommand(panel),
    null,
    task?.remoteHost || undefined,
    task ? {
      serverKey: task.remoteServerKey || task.remoteHost || '',
      serverName: task.remoteServerName || task.remoteHost || '',
    } : null,
  );
}

async function _installMlxLm(panel) {
  const task = _taskForDiagnosisPanel(panel);
  uiModule.showToast('Installing MLX LM on the selected server...');
  await _launchServeTask(
    'install-mlx-lm',
    'pip-update',
    _mlxLmInstallCommand(panel),
    null,
    task?.remoteHost || undefined,
    _diagnosisTargetMeta(task),
  );
}

function _diagnosisTargetMeta(task) {
  return task ? {
    serverKey: task.remoteServerKey || task.remoteHost || '',
    serverName: task.remoteServerName || task.remoteHost || '',
  } : null;
}

function _gpuCleanupCommand() {
  return `set -u
echo "[odysseus] Clearing GPU compute processes..."
if command -v nvidia-smi >/dev/null 2>&1; then
  pids="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>/dev/null | tr -d " " | grep -E "^[0-9]+$" | sort -u)"
  if [ -z "$pids" ]; then
    echo "[odysseus] No NVIDIA compute processes found."
    exit 0
  fi
  echo "[odysseus] GPU PIDs: $pids"
  ps -fp $pids 2>/dev/null || true
  echo "[odysseus] Sending TERM..."
  kill -TERM $pids || true
  sleep 3
  alive=""
  for pid in $pids; do
    if kill -0 "$pid" 2>/dev/null; then alive="$alive $pid"; fi
  done
  if [ -n "$alive" ]; then
    echo "[odysseus] Force killing remaining GPU PIDs:$alive"
    kill -KILL $alive || true
  fi
  sleep 1
  remaining="$(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null | sed "/^$/d" || true)"
  if [ -n "$remaining" ]; then
    echo "[odysseus] GPU processes still remain:"
    echo "$remaining"
    exit 2
  fi
  echo "[odysseus] GPU cleanup complete. No NVIDIA compute processes remain."
else
  echo "[odysseus] nvidia-smi not found; falling back to common model-server process cleanup."
  pkill -TERM -f "sglang.launch_server|vllm|llama-server|text-generation-launcher|aphrodite" || true
  sleep 3
  pkill -KILL -f "sglang.launch_server|vllm|llama-server|text-generation-launcher|aphrodite" || true
  echo "[odysseus] Fallback cleanup complete."
fi`;
}

async function _clearGpuProcesses(panel) {
  uiModule.showToast('Clearing GPU compute processes on the selected server...');
  await _runQuickCmd(panel, _gpuCleanupCommand());
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
    pattern: /tmux is required|tmux.*not found|tmux:\s*command not found|command not found:\s*tmux|No such file or directory:\s*['"]?tmux/i,
    message: 'tmux is missing on this server.',
    suggestion: 'Suggested action: open Dependencies and install tmux on the selected server.',
    fixes: [
      { label: 'Open tmux dependency', action: () => _openCookbookDependencies('tmux') },
      { label: 'Copy apt install', action: () => _copyText('sudo apt install -y tmux') },
      { label: 'Copy pacman install', action: () => _copyText('sudo pacman -S --needed tmux') },
    ],
  },
  {
    pattern: /Port \d+ is already serving|port is occupied by a different model|choose another port before launching/i,
    message: 'Serve port is already occupied by another model.',
    suggestion: 'Suggested action: stop the old server or choose a different port before relaunching.',
    fixes: [
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Copy check command', action: () => _copyText('curl http://127.0.0.1:PORT/v1/models') },
    ],
  },
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
    pattern: /Loaded weights leave no GPU memory for the KV cache under --mem-fraction-static|Raise --mem-fraction-static above/i,
    message: 'SGLang static memory fraction is too low for the loaded weights.',
    suggestion: 'Suggested action: retry with --mem-fraction-static 0.80 so weights fit and KV cache can still allocate.',
    fixes: [
      { label: 'Retry mem 0.80', action: (panel) => _serveAutoRetryReplace(panel, '--mem-fraction-static', '0.80') },
      { label: 'Retry mem 0.82', action: (panel) => _serveAutoRetryReplace(panel, '--mem-fraction-static', '0.82') },
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
    ],
  },
  {
    pattern: /get_paged_mqa_logits_metadata|deepseek_v4_backend\.py|paged_mqa_metadata\.cuh:113.*CUDA error:\s*invalid argument/i,
    message: 'SGLang DeepSeek-V4 attention metadata kernel failed on this GPU/runtime.',
    suggestion: 'Suggested action: stop retrying graph/memory tweaks for this exact FP8 command. SGLang’s RTX PRO 6000 recipe uses the original deepseek-ai/DeepSeek-V4-Flash checkpoint with --moe-runner-backend marlin, not the converted sgl-project FP8 checkpoint. Try that recipe/checkpoint, official SGLang container/nightly, or supported Hopper/Blackwell hardware.',
    fixes: [
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Copy error', action: (panel) => {
        const task = panel.closest('.cookbook-task');
        const text = task?.querySelector('.cookbook-task-output')?.textContent || task?.textContent || '';
        _copyText(text.trim());
      } },
    ],
  },
  {
    pattern: /Capture cuda graph failed|cuda graph failed|paged_mqa_metadata|cuda-graph-backend-decode|cuda-graph-max-bs-decode|CUDA error:\s*invalid argument/i,
    message: 'SGLang failed while capturing decode CUDA graphs.',
    suggestion: 'Suggested action: disable SGLang decode CUDA graph for this launch. DeepSeek-V4 is reaching graph capture, but this kernel is failing on the target hardware.',
    fixes: [
      { label: 'Disable decode graph', action: (panel) => _serveAutoRetryReplace(panel, '--cuda-graph-backend-decode', 'disabled') },
      { label: 'Retry mem 0.80', action: (panel) => _serveAutoRetryReplace(panel, '--mem-fraction-static', '0.80') },
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
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
    pattern: /memory capacity is unbalanced|Some GPUs may be occupied by other processes|pre_model_load_memory=.*local_gpu_memory/i,
    message: 'SGLang refused to start because free GPU memory is uneven across the selected tensor-parallel GPUs.',
    suggestion: 'Suggested action: run Clear GPUs, then relaunch. If it still fails, choose only equally free GPUs or lower TP/context.',
    fixes: [
      { label: 'Clear GPUs', action: (panel) => _clearGpuProcesses(panel) },
      { label: 'Copy clear command', action: () => _copyText(_gpuCleanupCommand()) },
      { label: 'Edit serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Set TP to 1', action: (panel) => _setPanelField(panel, 'tp', '1') },
      { label: 'Lower context', action: (panel) => _setPanelField(panel, 'ctx', '32768') },
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
    pattern: /sgl_kernel[\s\S]*(Python\.h|libnuma\.so\.1|common_ops|libnvrtc\.so)|(?:Python\.h|libnuma\.so\.1|common_ops|libnvrtc\.so)[\s\S]*sgl_kernel|Could not load any common_ops library|Please ensure sgl_kernel is properly installed/i,
    message: 'SGLang native kernel/runtime is missing or mismatched on this server.',
    suggestion: 'Suggested action: relaunch with Odysseus’ venv CUDA library path fix. If the venv does not contain the matching NVIDIA runtime libs, run Repair sglang-kernel.',
    fixes: [
      { label: 'Edit / relaunch serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Repair sglang-kernel', action: (panel) => _repairSglangKernel(panel) },
      { label: 'Copy repair command', action: (panel) => _copyText(_sglangKernelRepairCommand(panel)) },
      { label: 'Copy OS package command', action: () => _copyText('sudo apt-get install -y libnuma-dev python3.12-dev build-essential') },
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('sglang') },
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
    pattern: /No module named ['"]?mlx_lm|mlx_lm.*command not found|MLX is not installed|MLX LM is not installed/i,
    message: 'MLX LM is not installed on this server.',
    suggestion: 'Suggested action: install mlx-lm in the selected Python environment. MLX serving is intended for Apple Silicon Macs.',
    fixes: [
      { label: 'Install MLX LM', action: (panel) => _installMlxLm(panel) },
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('mlx_lm') },
      { label: 'Copy install command', action: () => _copyText('python3 -m pip install -U mlx-lm') },
    ],
  },
  {
    pattern: /Unable to quantize model of type <class ['"]mlx_lm\.models\.switch_layers\.QuantizedSwitchLinear['"]>|QuantizedSwitchLinear/i,
    message: 'MLX-LM tried to quantize an already-quantized DeepSeek switch layer.',
    suggestion: 'Suggested action: relaunch from the cached local snapshot path. Odysseus now rewrites MLX repo-id launches to the newest local Hugging Face snapshot when it exists on the selected Mac.',
    fixes: [
      { label: 'Edit / relaunch serve', action: (panel) => _openServeEditFromDiagnosis(panel) },
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('mlx_lm') },
      { label: 'Copy error', action: (panel) => {
        const task = panel.closest('.cookbook-task');
        const text = task?.querySelector('.cookbook-task-output')?.textContent || task?.textContent || '';
        _copyText(text.trim());
      } },
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
        _launchServeTask('repair-kernels', 'pip-update', `${_vp} -m pip install --user --break-system-packages "kernels<0.15"`);
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
  // System build deps must be checked BEFORE the llama-server catch-all:
  // a `cmake: command not found` failure ALSO produces `llama-server:
  // command not found` later in the script (the build aborts then the
  // run line fails) — pattern order is first-match-wins, so without
  // these specific entries the user gets the misleading "install
  // llama-cpp-python[server]" suggestion when the actual blocker is a
  // missing OS-package toolchain that pip can't ship.
  {
    pattern: /cmake: command not found|cmake.*not found.*Could not/i,
    message: 'cmake is required to compile llama.cpp from source, but it is not installed on this server.',
    suggestion: 'Suggested action: install cmake via the OS package manager — apt: cmake build-essential / pacman: cmake base-devel / dnf: cmake gcc-c++ make / brew: cmake. Cookbook can do this automatically on the next launch if your user has passwordless sudo for apt/pacman/dnf.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('llama_cpp') },
      { label: 'Copy apt install', action: () => _copyText('sudo apt install -y cmake build-essential git') },
      { label: 'Copy pacman install', action: () => _copyText('sudo pacman -Sy --needed cmake base-devel git') },
      { label: 'Copy dnf install', action: () => _copyText('sudo dnf install -y cmake gcc gcc-c++ make git') },
    ],
  },
  {
    pattern: /^(make|g\+\+|gcc): command not found|Could not find C\+\+ compiler/i,
    message: 'A C/C++ compiler (build-essential / base-devel) is required to compile llama.cpp.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('llama_cpp') },
      { label: 'Copy apt install', action: () => _copyText('sudo apt install -y build-essential') },
    ],
  },
  {
    pattern: /^git: command not found/i,
    message: 'git is required to clone the llama.cpp source tree.',
    fixes: [
      { label: 'Open Dependencies', action: () => _openCookbookDependencies('llama_cpp') },
      { label: 'Copy apt install', action: () => _copyText('sudo apt install -y git') },
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
    // Dependency-install (pip) build failure — a required package failed to
    // build its wheel (common when an old sdist's setup.py breaks on a newer
    // Python, e.g. basicsr on 3.13). This is an install problem, NOT a serve
    // problem, so it must never suggest killing vLLM.
    match: (text) => {
      const TAIL = text.slice(-6000);
      // A serve script can run a fallback build and then start serving fine —
      // don't flag a stale build error once the server is up.
      if (/Application startup complete|"(?:GET|POST)\s+\/v1\/[^"]+ HTTP\/[\d.]+"\s*2\d\d|Uvicorn running on|server is listening on https?:\/\//i.test(TAIL)) return false;
      return /Failed to build\b|subprocess-exited-with-error|Could not build wheels|metadata-generation-failed/i.test(TAIL);
    },
    message: 'A dependency failed to build during install — usually an older package whose build breaks on this Python version, not a server problem. The install did not finish.',
    suggestion: 'Suggested action: check the captured output for the package that failed to build; it may need a newer release or a patch to install on this Python version.',
    fixes: [],
  },
  {
    // vLLM-specific traceback: only offer the kill-processes recovery when the
    // output is actually about vLLM. Tail-only + healthy-server suppression so
    // a one-shot startup traceback doesn't stick on the panel forever while
    // the server happily serves /v1/models.
    match: (text) => {
      const TAIL = text.slice(-4096);
      if (!/Traceback \(most recent call last\)/i.test(TAIL)) return false;
      if (/Application startup complete|"GET \/v1\/[^"]+ HTTP\/[\d.]+" 2\d\d|Uvicorn running on/i.test(TAIL)) return false;
      return /vllm/i.test(TAIL);
    },
    message: 'A vLLM process hit a Python traceback and may be wedged.',
    fixes: [
      { label: 'Kill vLLM processes', action: (panel) => _runQuickCmd(panel, 'pkill -f vllm') },
    ],
  },
  {
    // Generic traceback (not vLLM, not a pip build): surface it without
    // suggesting an unrelated vLLM kill. Same tail-only + healthy suppression.
    match: (text) => {
      const TAIL = text.slice(-4096);
      if (!/Traceback \(most recent call last\)/i.test(TAIL)) return false;
      if (/Application startup complete|"GET \/v1\/[^"]+ HTTP\/[\d.]+" 2\d\d|Uvicorn running on/i.test(TAIL)) return false;
      return true;
    },
    message: 'Python traceback detected — check the captured output below for the underlying error.',
    suggestion: 'Suggested action: read the captured output for the failing step; copy the troubleshooting bundle if you need help.',
    fixes: [],
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
  // Left side carries the diagnosis text (message + suggestion); buttons
  // stay on the right. Was a separate body row below the toolbar, but
  // the message reads more like "this is what the toolbar is for" when
  // it sits inline with Copy / × Dismiss.
  toolbar.style.cssText = 'display:flex;align-items:flex-start;gap:8px;margin-bottom:-2px;';

  const textWrap = document.createElement('div');
  textWrap.style.cssText = 'flex:1;min-width:0;font-size:11px;line-height:1.35;';
  const msg = document.createElement('div');
  msg.className = 'cookbook-diag-message';
  msg.textContent = diagnosis.message;
  textWrap.appendChild(msg);
  const suggestion = document.createElement('div');
  suggestion.className = 'cookbook-diag-suggestion';
  suggestion.textContent = suggestionText;
  suggestion.style.cssText = 'opacity:0.75;margin-top:1px;';
  textWrap.appendChild(suggestion);
  toolbar.appendChild(textWrap);

  const copyBtn = document.createElement('button');
  copyBtn.type = 'button';
  copyBtn.className = 'cookbook-diag-copy';
  copyBtn.title = 'Copy diagnosis details';
  copyBtn.setAttribute('aria-label', 'Copy diagnosis');
  copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  copyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const bundle = _diagnosisCopyBundle(task, diagnosis, sourceText, suggestionText);
    // Use the shared helper which falls back to execCommand('copy') on
    // non-HTTPS origins (Tailscale IPs, LAN IPs, etc.) — navigator.clipboard
    // is silently a no-op on those, which is why the button appeared dead
    // for users on http://100.113.161.2:7011 over Tailscale/mobile.
    const ok = await _copyText(bundle);
    if (ok) {
      copyBtn.classList.add('copied');
      setTimeout(() => { if (copyBtn.isConnected) copyBtn.classList.remove('copied'); }, 1200);
    }
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
    diag.appendChild(row);
  }
}

export function _clearDiagnosis(panel) {
  panel._lastDiagMsg = null;
  const diag = panel.querySelector('.cookbook-diagnosis');
  if (diag) { diag.innerHTML = ''; diag.classList.add('hidden'); }
}

// ── Quick command ──

export async function _runQuickCmd(panel, cmd) {
  const task = _taskForDiagnosisPanel(panel);
  let fullCmd = cmd;
  const host = task?.remoteHost || _envState.remoteHost || '';
  const port = task?.sshPort || task?.payload?.ssh_port || _envState.sshPort || '';
  if (host) {
    fullCmd = _sshCmd(host, cmd, port);
  }
  const diag = panel.querySelector('.cookbook-diagnosis');
  if (diag) {
    diag.classList.remove('hidden');
    diag.innerHTML = '<div class="cookbook-diag-message">Running command...</div>';
  }

  try {
    const res = await fetch('/api/shell/exec', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: fullCmd, timeout: 60 }),
    });
    const data = await res.json().catch(() => ({}));
    const out = [data.stdout, data.stderr].filter(Boolean).join('\n').trim();
    const ok = res.ok && Number(data.exit_code ?? 1) === 0;
    if (diag) {
      diag.innerHTML = ''
        + `<div class="cookbook-diag-message">${ok ? 'Command completed.' : 'Command failed.'}</div>`
        + `<div class="cookbook-diag-suggestion" style="opacity:0.75;margin-top:1px;">Exit code: ${_diagEsc(data.exit_code ?? 'unknown')}</div>`
        + (out ? `<pre class="cookbook-diag-output" style="margin:6px 0 0;white-space:pre-wrap;max-height:180px;overflow:auto;font-size:10px;line-height:1.35;">${_diagEsc(out)}</pre>` : '');
    }
  } catch (e) {
    if (diag) {
      diag.innerHTML = `<div class="cookbook-diag-message">Command error.</div><div class="cookbook-diag-suggestion">${_diagEsc(e.message)}</div>`;
    }
  }
}

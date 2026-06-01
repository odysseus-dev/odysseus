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
import spinnerModule from './spinner.js';

// ── Error diagnosis ──

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
    message: 'Model quantization format incompatible with this vLLM version. Try a different quant (AWQ) or update vLLM.',
    fixes: [
      { label: 'Update vLLM on server', action: (panel) => {
        const taskEl = panel.closest('.cookbook-task');
        const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
        const host = task?.remoteHost || '';
        const prefix = _buildEnvPrefix();
        const pipCmd = prefix ? prefix + ' pip install -U vllm' : 'pip install -U vllm';
        const cmd = host ? _sshCmd(host, pipCmd) : pipCmd;
        _launchServeTask('update-vllm', 'pip-update', cmd);
      }},
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
      { label: 'Check environment is set', action: (panel) => {
        const el = panel.querySelector('[data-field="env_type"]');
        if (el) { el.focus(); el.style.borderColor = 'var(--red)'; }
      }},
    ],
  },
  {
    pattern: /sglang.*command not found|No module named sglang|SGLang is not installed/i,
    message: 'SGLang is not installed or not in PATH. Open Cookbook → Dependencies and install sglang on this server.',
    fixes: [
      { label: 'Copy install command', action: () => _copyText('python3 -m pip install "sglang[all]"') },
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
    message: 'CUDA not available in this environment.',
    fixes: [],
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
      { label: 'Update vLLM on server', action: (panel) => {
        const taskEl = panel.closest('.cookbook-task');
        const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
        const host = task?.remoteHost || '';
        const prefix = _buildEnvPrefix();
        const pipCmd = prefix ? prefix + ' pip install -U vllm transformers' : 'pip install -U vllm transformers';
        const cmd = host ? _sshCmd(host, pipCmd) : pipCmd;
        // Run in tmux so it doesn't timeout
        const name = 'update-vllm';
        _launchServeTask(name, 'pip-update', cmd);
      }},
    ],
  },
  {
    pattern: /Either a revision or a version must be specified|transformers\.integrations\.hub_kernels|kernels\/layer/i,
    message: 'vLLM/Transformers kernel package mismatch.',
    fixes: [
      { label: 'Update vLLM/Transformers/kernels', action: (panel) => {
        const taskEl = panel.closest('.cookbook-task');
        const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
        const host = task?.remoteHost || '';
        const prefix = _buildEnvPrefix();
        const pipCmd = prefix ? prefix + ' python3 -m pip install -U vllm transformers kernels' : 'python3 -m pip install -U vllm transformers kernels';
        const cmd = host ? _sshCmd(host, pipCmd) : pipCmd;
        _launchServeTask('update-vllm-stack', 'pip-update', cmd);
      }},
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
      { label: 'Copy install command', action: () => _copyText('pip install "llama-cpp-python[server]"') },
    ],
  },
  {
    pattern: /No module named ['"]?torch|No module named ['"]?diffusers|diffusers.*command not found/i,
    message: 'Diffusion serving needs PyTorch and diffusers. Install diffusers from Cookbook → Dependencies.',
    fixes: [
      { label: 'Copy install command', action: () => _copyText('python3 -m pip install "diffusers[torch]"') },
    ],
  },
  {
    pattern: /Triton kernels.*Failed to import|cannot import name '\w+' from 'triton_kernels/i,
    message: 'Triton kernels version mismatch. Non-fatal warning — model will still run, just without optimized MoE kernels.',
    fixes: [
      { label: 'Update triton on server', action: (panel) => {
        const taskEl = panel.closest('.cookbook-task');
        const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
        const host = task?.remoteHost || '';
        const prefix = _buildEnvPrefix();
        const pipCmd = prefix ? prefix + ' pip install -U triton triton-kernels' : 'pip install -U triton triton-kernels';
        const cmd = host ? _sshCmd(host, pipCmd) : pipCmd;
        _launchServeTask('update-triton', 'pip-update', cmd);
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
      { label: 'Update vLLM on server', action: (panel) => {
        const taskEl = panel.closest('.cookbook-task');
        const task = taskEl ? _loadTasks().find(t => t.sessionId === taskEl.dataset.taskId) : null;
        const host = task?.remoteHost || '';
        const prefix = _buildEnvPrefix();
        const pipCmd = prefix ? prefix + ' pip install -U vllm' : 'pip install -U vllm';
        const cmd = host ? _sshCmd(host, pipCmd) : pipCmd;
        _launchServeTask('update-vllm', 'pip-update', cmd);
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

export function _showDiagnosis(panel, diagnosis, sourceText) {
  if (panel._lastDiagMsg === diagnosis.message) return;
  if (panel._diagDismissed === diagnosis.message) return; // stay dismissed until new error
  panel._lastDiagMsg = diagnosis.message;

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

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;justify-content:space-between;';

  const msg = document.createElement('div');
  msg.className = 'cookbook-diag-message';
  msg.textContent = diagnosis.message;
  header.appendChild(msg);

  const dismiss = document.createElement('button');
  dismiss.className = 'close-btn';
  dismiss.style.cssText = 'width:16px;height:16px;font-size:9px;flex-shrink:0;';
  dismiss.textContent = '\u2715';
  dismiss.addEventListener('click', () => { panel._diagDismissed = diagnosis.message; _clearDiagnosis(panel); });
  header.appendChild(dismiss);

  diag.appendChild(header);

  if (diagnosis.fixes && diagnosis.fixes.length) {
    const row = document.createElement('div');
    row.className = 'cookbook-diag-fixes';
    for (const fix of diagnosis.fixes) {
      const btn = document.createElement('button');
      btn.className = 'cookbook-btn cookbook-diag-btn';
      btn.textContent = fix.label;
      btn.addEventListener('click', async () => {
        if (btn.dataset.busy) return;
        btn.dataset.busy = '1';
        // Spinner feedback while the fix runs (kill + relaunch takes a moment).
        const _orig = btn.textContent;
        const wp = spinnerModule.createWhirlpool(12);
        wp.element.style.cssText = 'display:inline-block;vertical-align:middle;width:12px;height:12px;margin-right:5px;';
        btn.textContent = '';
        btn.appendChild(wp.element);
        const _lbl = document.createElement('span');
        _lbl.textContent = _orig;
        _lbl.style.verticalAlign = 'middle';
        btn.appendChild(_lbl);
        try {
          await fix.action(panel, sourceText);
        } catch (e) {
          console.error('[cookbook] diagnosis fix failed', e);
        } finally {
          // Retries animate the whole card away (button goes with it). For fixes
          // that leave the card in place, restore the label.
          if (btn.isConnected) { try { wp.destroy(); } catch {} btn.textContent = _orig; delete btn.dataset.busy; }
        }
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

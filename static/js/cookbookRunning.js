// ============================================
// COOKBOOK RUNNING SUB-MODULE
// Running tasks tab: task cards, status monitoring,
// stop/restart, diagnosis, auto-fix, background monitor
// ============================================

import uiModule from './ui.js';
import { _diagnose, _showDiagnosis, _clearDiagnosis } from './cookbook-diagnosis.js';
import { registerMenuDismiss } from './escMenuStack.js';
import { computeProgressSignal } from './cookbookProgressSignal.js';

// Human-friendly badge label for a task's internal status. Avoids surfacing
// the word "error" in the sidebar — a server the user stopped or one that
// quit cleanly reads as "stopped", not "error".
function _statusLabel(status, type) {
  if (status === 'running' && type === 'download') return 'downloading';
  if (status === 'done' && type === 'download') return 'finished';
  if (status === 'error') return 'stopped';
  return status || '';
}

// Single source of truth for what a task's status badge shows + its style class.
// Crucially, a serve task that's still coming up shows its live phase
// ("loading 45%", "warming up", …) rather than the generic "running" — they're
// the same state, so the badge shouldn't flip between two different labels on
// every re-render. Returns { text, cls } where cls is appended after
// "cookbook-task-status" ('' = the neutral loading style).
function _taskBadge(task) {
  if (task._unreachable && task.status === 'running') return { text: 'unreachable', cls: 'cookbook-task-error' };
  if (task.type === 'serve' && task.status === 'running' && task.progress) {
    // Same green "running" pill — just with dynamic phase text, so it doesn't
    // read as a different status while the server is coming up.
    return { text: task.progress, cls: 'cookbook-task-running' };
  }
  return { text: _statusLabel(task.status, task.type), cls: 'cookbook-task-' + task.status };
}

// A download task whose tmux output still shows an active per-shard line
// (e.g. "model-00012-of-00082.safetensors: 56%|") is NOT actually finished —
// the cookbook just lost track. The clear pill becomes a "reconnect" affordance
// in that case (click → revive the row + reattach the poll loop).
function _downloadOutputLooksActive(task) {
  if (!task || task.type !== 'download') return false;
  const out = task.output || '';
  if (!out) return false;
  if (out.includes('DOWNLOAD_OK') || out.includes('DOWNLOAD_FAILED')) return false;
  // An active shard line: filename + a colon + a percentage that isn't 100%.
  // We catch any in-flight shard or "Downloading 'X' to ..." line (no %).
  return /model-\d+-of-\d+\.[a-z]+:\s+(?!100%)\d+%/i.test(out)
      || /Downloading\s+'[^']+'\s+to\s+'[^']*\.incomplete'/i.test(out);
}

function _canClearTask(task) {
  if (!task || task.status === 'running') return false;
  if (task.type === 'serve' && (task.status === 'ready' || task._serveReady)) return false;
  // If the tmux output still shows an in-flight download, the task isn't
  // actually finished — hide the clear/check pill so it doesn't show on a
  // task that's still doing work. (The next render will reflect this and
  // ideally the self-heal flips status back to running.)
  if (_downloadOutputLooksActive(task)) return false;
  return ['done', 'stopped', 'error', 'crashed', 'failed'].includes(task.status);
}

function _clearPillLabel(task) {
  if (_downloadOutputLooksActive(task)) return 'reconnect';
  return 'clear';
}

// A pip dependency/driver install (payload._dep) reports success with the
// runner's "=== Process exited with code 0 ===" sentinel and pip's
// "Successfully installed" line — never the HuggingFace download markers
// (DONE / 100% / /snapshots/ / DOWNLOAD_OK) that the download heuristics look
// for. Without this, a clean install whose tmux pane has already gone away is
// misread as crashed/stopped even though pip exited 0. Prefer the authoritative
// exit-code sentinel; fall back to pip's success line when no sentinel was
// captured (and there's no install error in the same output).
function _depInstallSucceeded(output) {
  const text = String(output || '');
  if (!text) return false;
  const exitMatch = text.match(/=== Process exited with code (-?\d+) ===/);
  if (exitMatch) return Number(exitMatch[1]) === 0;
  return /\b(?:Successfully installed|Requirement already satisfied)\b/.test(text)
    && !/\bERROR\b|No matching distribution|Could not find a version|Traceback \(most recent call last\)/.test(text);
}

function _shouldOfferCrashReport(task) {
  if (!task) return false;
  if (task._unreachable && task.type === 'serve') return true;
  return ['error', 'crashed', 'failed'].includes(task.status);
}

function _serveTaskLooksAwqOnLocalBackend(task, outputText = '') {
  const repo = `${task?.payload?.repo_id || ''} ${task?.name || ''}`.toLowerCase();
  const cmd = `${task?.payload?._cmd || ''} ${outputText || ''}`.toLowerCase();
  return /\b(awq|gptq|fp8)\b/.test(repo) && /(llama-server|llama_cpp\.server|ollama|ggml_cuda_enable_unified_memory)/.test(cmd);
}

function _serveTaskLooksAwqWithoutUsableAccelerator(task, outputText = '') {
  const repo = `${task?.payload?.repo_id || ''} ${task?.name || ''}`.toLowerCase();
  const out = String(outputText || '').toLowerCase();
  return /\b(awq|gptq|fp8)\b/.test(repo)
    && /(no accelerator|no cuda runtime|failed to infer device type|triton is not supported|0 active driver)/i.test(out);
}

async function _openDownloadForGgufTask(task) {
  const raw = task?.payload?.repo_id || task?.name || '';
  const modelName = String(raw)
    .split('/').pop()
    .replace(/[-_](?:AWQ|GPTQ|FP8|4bit|8bit|Int4|Int8).*$/i, '')
    .replace(/[-_]+$/g, '')
    || String(raw).split('/').pop()
    || raw;
  const cookbook = window.cookbookModule;
  if (cookbook && typeof cookbook.open === 'function') {
    cookbook.open({ tab: 'Search' });
  } else {
    document.getElementById('tool-cookbook-btn')?.click();
  }
  setTimeout(async () => {
    const modal = document.getElementById('cookbook-modal');
    const tab = modal?.querySelector('.cookbook-tab[data-backend="Search"]');
    if (tab && !tab.classList.contains('active')) tab.click();
    const search = document.getElementById('hwfit-search');
    if (search) {
      search.value = modelName;
      search.dispatchEvent(new Event('input', { bubbles: true }));
      search.focus();
    }
    const quant = document.getElementById('hwfit-quant');
    if (quant) {
      quant.value = 'Q4_K_M';
      quant.dispatchEvent(new Event('change', { bubbles: true }));
    }
    try {
      const hwfit = await import('./cookbook-hwfit.js');
      if (typeof hwfit._hwfitFetch === 'function') hwfit._hwfitFetch(true);
    } catch {}
  }, 80);
}

function _terminalServeDiagnosis(task, outputText) {
  const out = String(outputText || task?.output || '');
  if (!task || task.type !== 'serve' || !['stopped', 'error', 'crashed', 'failed'].includes(task.status) || !out.trim()) return null;
  // Pip tasks (Reinstall vLLM, Upgrade torch, etc.) ride on the serve task
  // type so they get a tmux session + show up in Running tab — but they are
  // NOT serve invocations. Their output is pip's own; the generic
  // "Serve stopped before the model became reachable" message + Edit-serve
  // fix make no sense. Bail so the panel just shows pip's output.
  const _isPipTask = ((task.payload?.repo_id || '').startsWith('pip-'))
    || /python3? -m pip\b/.test(task.payload?._cmd || '');
  if (_isPipTask) return null;
  if (_serveTaskLooksAwqOnLocalBackend(task, out)) {
    return {
      message: 'AWQ/GPTQ/FP8 cannot be served through llama.cpp/Ollama unified-memory mode.',
      suggestion: 'Suggested action: use vLLM/SGLang on a compatible CUDA/ROCm GPU server, or download a GGUF version for llama.cpp/Ollama/unified-memory serving.',
      fixes: [
        { label: 'Find GGUF download', action: () => _openDownloadForGgufTask(task) },
        { label: 'Edit serve', action: (panel) => _openServeEditForTask(task) },
      ],
    };
  }
  if (_serveTaskLooksAwqWithoutUsableAccelerator(task, out)) {
    return {
      message: 'AWQ/GPTQ/FP8 needs a working vLLM/SGLang accelerator path; this server did not expose one.',
      suggestion: 'Suggested action: choose a CUDA/ROCm server where vLLM/SGLang can see the GPU, or download a GGUF version and serve it with llama.cpp/Ollama.',
      fixes: [
        { label: 'Find GGUF download', action: () => _openDownloadForGgufTask(task) },
        { label: 'Edit serve', action: (panel) => _openServeEditForTask(task) },
      ],
    };
  }
  return _diagnose(out) || {
    message: /Native llama-server not found|building llama-server|llama\.cpp/i.test(out)
      ? 'llama.cpp build stopped before the server became reachable.'
      : 'Serve stopped before the model became reachable.',
    suggestion: /Native llama-server not found|building llama-server|llama\.cpp/i.test(out)
      ? 'Suggested action: copy the troubleshooting bundle, then edit serve settings. For the quickest local/CPU path, use Ollama or a prebuilt llama-server; source builds can take several minutes and fail if build dependencies are incomplete.'
      : 'Suggested action: copy the troubleshooting bundle, then edit serve settings or relaunch with a CPU/backend fallback.',
    fixes: [{ label: 'Edit serve', action: (panel) => _openServeEditForTask(task) }],
  };
}

function _redactCrashReportText(text) {
  if (!text) return '';
  return String(text)
    .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}/gi, '$1[redacted]')
    .replace(/\b(hf_[A-Za-z0-9]{16,})\b/g, '[redacted-hf-token]')
    .replace(/\b(sk-[A-Za-z0-9_-]{16,})\b/g, '[redacted-api-key]')
    .replace(/\b(xox[baprs]-[A-Za-z0-9-]{16,})\b/g, '[redacted-slack-token]')
    .replace(/\b(AIza[0-9A-Za-z_-]{20,})\b/g, '[redacted-google-key]')
    .replace(/\b((?:HF_TOKEN|HUGGING_FACE_HUB_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|BRAVE_API_KEY|TAVILY_API_KEY|SERPER_API_KEY|GOOGLE_API_KEY|API_KEY|TOKEN|PASSWORD)\s*=\s*)(['"]?)[^\s'"\\]+/gi, '$1$2[redacted]')
    .replace(/\b(--(?:api-key|token|hf-token|password)\s+)([^\s]+)/gi, '$1[redacted]');
}

function _lastLines(text, count = 160) {
  const clean = _redactCrashReportText(text || '').trimEnd();
  if (!clean) return '(no captured output)';
  return clean.split('\n').slice(-count).join('\n');
}

function _codeFence(text) {
  return String(text || '').replace(/```/g, '` ` `');
}

function _taskHostLabel(task) {
  if (!task?.remoteHost) return 'local';
  return task.remoteHost + (task.sshPort ? `:${task.sshPort}` : '');
}

function _taskPort(task) {
  const cmd = task?.payload?._cmd || '';
  const match = cmd.match(/--port\s+(\d+)/);
  return match ? match[1] : '';
}

function _buildCrashReport(task, outputText) {
  const capturedOutput = outputText || task?.output || '';
  const cmd = _redactCrashReportText(task?.payload?._cmd || '');
  const diag = _diagnose(capturedOutput);
  const started = task?.ts ? new Date(task.ts).toISOString() : '';
  const report = [
    '## Odysseus Cookbook crash report',
    '',
    'Please review this report for secrets before posting it publicly.',
    '',
    '### Task',
    `- ID: \`${task?.sessionId || task?.id || 'unknown'}\``,
    `- Type: \`${task?.type || 'unknown'}\``,
    `- Status: \`${task?._unreachable ? 'unreachable' : (task?.status || 'unknown')}\``,
    `- Model/repo: \`${task?.payload?.repo_id || task?.name || 'unknown'}\``,
    `- Host: \`${_taskHostLabel(task)}\``,
  ];
  if (task?.platform) report.push(`- Platform: \`${task.platform}\``);
  if (started) report.push(`- Started: \`${started}\``);
  const port = _taskPort(task);
  if (port) report.push(`- Port: \`${port}\``);
  if (diag?.message) report.push(`- Diagnosis: ${diag.message}`);
  if (cmd) {
    report.push('', '### Command', '```bash', _codeFence(cmd), '```');
  }
  report.push('', '### Last captured output', '```text', _codeFence(_lastLines(capturedOutput)), '```');
  return report.join('\n');
}

// Shared state/functions injected by init()
let _envState;
let _sshCmd;
let _getPort;
let _sshPrefix;
let _getPlatform;
let _isWindows;
let _buildEnvPrefix;
let _loadPresets;
let _savePresets;
let _copyText;
let _persistEnvState;
let _refreshDependencies;
let modelLogo;
let esc;
let _detectBackend;
let _detectToolParser;
let _detectModelOptimizations;
let _buildServeCmd;

// When a new action is started (download / dependency / serve), this holds the
// new task's id so the next render collapses every other card and leaves only
// the new one open. Consumed (cleared) by _renderRunningTab.
let _soloExpandTaskId = null;

// Storage keys
const TASKS_KEY = 'cookbook-tasks';
const STORAGE_KEY = 'cookbook-presets';
const SERVE_STATE_KEY = 'cookbook-serve-state';

// Polling / timeout intervals
const TASK_POLL_INTERVAL_MS = 3000;       // delay between reconnect-loop iterations
const BG_MONITOR_INTERVAL_MS = 5000;      // background task status poll
const STALE_PROGRESS_MS = 5 * 60 * 1000;  // download with no progress this long = stale
const STARTUP_STALE_PROGRESS_MS = 45 * 1000; // 0%-forever startup stall: retry much sooner

// ── Phase detection (mirrors Python _parse_serve_phase in cookbook_routes.py) ──
// Single source of truth for serve task status. KEEP IN SYNC with the Python version.
export function _parseServePhase(snapshot) {
  if (!snapshot) return {};
  // Strip newlines so tmux line-wrapping doesn't break regex matching
  const flat = snapshot.replace(/\s+/g, ' ');
  const loadMatches = [...flat.matchAll(/Loading safetensors.*?(\d+)%/g)];
  // "Downloading (incomplete total...)" tracks real aggregate bytes; prefer it
  // over "Fetching N files" which only counts fully-closed files and lags badly
  // with hf_transfer's parallel-chunk strategy (often sits at 0/N for most of the run).
  const downloadingMatches = [...flat.matchAll(/Downloading.*?(\d+)%/g)];
  const fetchingMatches = [...flat.matchAll(/Fetching.*?(\d+)%/g)];
  const dlMatches = downloadingMatches.length ? downloadingMatches : fetchingMatches;
  // "Avg generation throughput: X tokens/s, Running: N reqs"
  const tpsMatches = [...flat.matchAll(/(?:Avg )?generation throughput:\s*([\d.]+)\s*tokens\/s.*?Running:\s*(\d+)\s*reqs/g)];

  // Throughput FIRST — its log line contains "GPU KV cache usage" which would
  // otherwise false-match the warmup check
  if (tpsMatches.length) {
    const m = tpsMatches[tpsMatches.length - 1];
    const tps = parseFloat(m[1]);
    const reqs = parseInt(m[2]);
    return {
      phase: reqs > 0 ? `${m[1]} tok/s` : 'idle',
      status: 'ready',
      tps,
      reqs,
    };
  }
  if (flat.includes('Application startup complete')) {
    return { phase: 'ready', status: 'ready' };
  }
  if (/Ollama API ready on port\s+\d+/i.test(flat)) {
    return { phase: 'ready', status: 'ready' };
  }
  const llamaBuildMatches = [...flat.matchAll(/\[\s*(\d{1,3})%\]\s*(?:Building|Linking)/gi)];
  if (llamaBuildMatches.length) {
    const pct = Math.min(100, parseInt(llamaBuildMatches[llamaBuildMatches.length - 1][1], 10));
    return { phase: `building llama.cpp ${pct}%`, status: 'running', pct };
  }
  if (/Native llama-server not found|building from source/i.test(flat)) {
    if (/Cloning into ['"]?llama\.cpp/i.test(flat) && !/Receiving objects:\s*100%/i.test(flat)) {
      return { phase: 'cloning llama.cpp', status: 'running' };
    }
    if (/Configuring incomplete|CMake Error/i.test(flat)) {
      return {};
    }
    if (/CMAKE_BUILD_TYPE|Detecting CXX|Found Threads|Including CPU backend|CUDA nvcc found|building llama-server/i.test(flat)) {
      return { phase: 'configuring llama.cpp', status: 'running' };
    }
    return { phase: 'building llama.cpp', status: 'running' };
  }
  // HTTP access logs (e.g. GET /v1/models 200 OK) mean the server is up
  if (/(?:GET|POST)\s+\/[^\s]*\s+HTTP\/[\d.]+"\s*\d{3}/.test(flat)) {
    return { phase: 'idle', status: 'ready' };
  }
  if (flat.includes('Loading weights took')) {
    return { phase: 'initializing', status: 'running' };
  }
  // "GPU KV cache" alone (during allocation) — not "GPU KV cache usage" (runtime log)
  if (flat.includes('GPU KV cache') && !flat.includes('GPU KV cache usage')) {
    return { phase: 'warming up', status: 'running' };
  }
  if (loadMatches.length) {
    const pct = parseInt(loadMatches[loadMatches.length - 1][1]);
    return { phase: `loading ${pct}%`, status: 'running', pct };
  }
  if (dlMatches.length) {
    const pct = parseInt(dlMatches[dlMatches.length - 1][1]);
    return { phase: `downloading ${pct}%`, status: 'running', pct };
  }
  return {};
}

// ── Port auto-increment ──

function _nextAvailablePort() {
  const tasks = _loadTasks();
  const presets = _loadPresets();
  const usedPorts = new Set();
  tasks.forEach(t => {
    if (t.type === 'serve' && (t.status === 'running' || t.status === 'queued')) {
      const m = t.payload?._cmd?.match(/--port\s+(\d+)/);
      if (m) usedPorts.add(parseInt(m[1]));
    }
  });
  presets.forEach(p => {
    if (p.port) usedPorts.add(parseInt(p.port));
  });
  let port = 8000;
  while (usedPorts.has(port)) port++;
  return String(port);
}

// ── Endpoint cleanup ──

async function _removeEndpointByUrl(baseUrl) {
  try {
    const res = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
    if (!res.ok) return;
    const endpoints = await res.json();
    const hostPort = baseUrl.replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    const ep = endpoints.find(e => e.base_url === baseUrl)
            || endpoints.find(e => e.base_url.includes(hostPort));
    if (ep) {
      await fetch(`/api/model-endpoints/${ep.id}`, { method: 'DELETE', credentials: 'same-origin' });
      _refreshModelsAfterEndpointChange();
    }
  } catch {}
}

function _refreshModelsAfterEndpointChange() {
  const pickerLabel = document.getElementById('model-picker-label');
  if (pickerLabel) {
    pickerLabel.dataset.prevHtml = pickerLabel.innerHTML;
    pickerLabel.innerHTML = '<span style="opacity:0.4;">refreshing…</span>';
  }
  if (window.modelsModule && window.modelsModule.refreshModels) {
    window.modelsModule.refreshModels(true);
  }
  setTimeout(() => {
    if (!window.sessionModule) return;
    const currentModel = window.sessionModule.getCurrentModel ? window.sessionModule.getCurrentModel() : null;
    if (currentModel) {
      const items = (window.modelsModule && window.modelsModule.getCachedItems) ? window.modelsModule.getCachedItems() : [];
      const allModels = [];
      items.forEach(item => {
        if (item.offline) return;
        (item.models || []).concat(item.models_extra || []).forEach(m => allModels.push({ mid: m, url: item.url, endpointId: item.endpoint_id }));
      });
      const stillExists = allModels.some(m => m.mid === currentModel);
      if (!stillExists && allModels.length > 0) {
        const fallback = allModels[0];
        if (window.sessionModule.createDirectChat) {
          window.sessionModule.createDirectChat(fallback.url, fallback.mid, fallback.endpointId);
        }
      }
    }
    if (window.sessionModule.updateModelPicker) {
      window.sessionModule.updateModelPicker();
    }
  }, 1500);
}

function _appendCookbookEndpointScope(fd, remoteHost) {
  const host = String(remoteHost || '').trim();
  if (!host || host === 'local' || host === 'localhost' || host === '127.0.0.1') {
    fd.append('container_local', 'true');
  }
}

function _connectHostFromRemote(remoteHost, fallback = 'localhost') {
  const host = String(remoteHost || '').trim();
  if (!host || host === 'local') return fallback;
  return host.includes('@') ? host.split('@').pop() : host;
}

function _isAnyBindHost(host) {
  const h = String(host || '').trim().toLowerCase();
  return h === '0.0.0.0' || h === '::' || h === '[::]';
}

function _endpointFromAdvertisedUrl(rawUrl, currentHost, fallbackPort = '11434') {
  try {
    const u = new URL(rawUrl);
    const host = _isAnyBindHost(u.hostname) ? currentHost : (u.hostname || currentHost);
    const port = u.port || fallbackPort;
    const bracketedHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
    return { host, port, baseUrl: `${u.protocol}//${bracketedHost}${port ? `:${port}` : ''}/v1` };
  } catch {
    return null;
  }
}

// ── Download queue — runs one at a time per server ──

function _processQueue() {
  const tasks = _loadPrunedTasks();
  const running = tasks.filter(t => t.type === 'download' && t.status === 'running');
  const queued = tasks.filter(t => t.type === 'download' && t.status === 'queued');
  if (!queued.length) return;

  const busyHosts = new Set(running.map(t => t.remoteHost || 'local'));

  for (const task of queued) {
    const host = task.remoteHost || 'local';
    if (busyHosts.has(host)) continue;
    busyHosts.add(host);
    _startQueuedDownload(task);
  }
}

async function _startQueuedDownload(task) {
  if (!task.payload) {
    _updateTask(task.sessionId, { status: 'error', output: 'No payload' });
    _renderRunningTab();
    return;
  }
  // Flip to 'running' SYNCHRONOUSLY (before the async POST) so a concurrent
  // _processQueue — or a second "Start now" — can't see it as still 'queued' and
  // launch the same download a second time. Without this, finishing another
  // download mid-POST re-queued this one into a duplicate task.
  {
    const _pre = _loadTasks();
    const _pt = _pre.find(t => t.sessionId === task.sessionId);
    if (_pt) {
      if (_pt.status === 'running' && _pt._startLaunched) return;  // already being started
      _pt.status = 'running';
      _pt._startLaunched = true;
      _saveTasks(_pre);
    }
  }
  try {
    const res = await fetch('/api/model/download', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(task.payload),
    });
    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      _updateTask(task.sessionId, { status: 'error', output: `HTTP ${res.status}: ${errText.slice(0, 200)}` });
      _renderRunningTab();
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      _updateTask(task.sessionId, { status: 'error', output: data.error || 'Unknown error' });
      _renderRunningTab();
      return;
    }
    const oldId = task.sessionId;
    const launchedTask = { ...task, sessionId: data.session_id, id: data.session_id, status: 'running' };
    const key = _downloadDedupeKey(launchedTask);
    let found = false;
    const tasks = _loadTasks().filter(t => {
      if (t.sessionId === oldId) {
        found = true;
        t.sessionId = data.session_id;
        t.id = data.session_id;
        t.status = 'running';
        t._startLaunched = true;
        return true;
      }
      if (t.sessionId === data.session_id) return false;
      return !(key && t.type === 'download' && t.status === 'queued' && _downloadDedupeKey(t) === key);
    });
    if (!found) tasks.push(_stripTaskSecrets(launchedTask));
    _saveTasks(tasks);
    _renderRunningTab();
    _startBackgroundMonitor();
    await new Promise(r => setTimeout(r, 2000));
    _renderRunningTab();
  } catch (e) {
    _updateTask(task.sessionId, { status: 'error', output: e.message || 'Network error' });
    _renderRunningTab();
  }
}

// ── Task CRUD ──

function _serveOutputLooksReady(task) {
  const out = String(task?.output || '');
  return !!task?._serveReady
    || /Application startup complete/i.test(out)
    || /Ollama API ready on port\s+\d+/i.test(out)
    || /(?:GET|POST)\s+\/[^\s]*\s+HTTP\/[\d.]+"\s*2\d\d/i.test(out);
}

function _normalizeTaskForDisplay(task) {
  if (!task || typeof task !== 'object') return task;
  // Pip tasks (Reinstall vLLM / Upgrade torch / etc.) ride on the serve task
  // type so they get tmux + the Running tab. They are NOT serves — their
  // "ready" markers are pip's `Successfully installed` / `Requirement already
  // satisfied`, not "Application startup complete".
  const _isPipTask = ((task.payload?.repo_id || '').startsWith('pip-'))
    || /python3? -m pip\b/.test(task.payload?._cmd || '');
  if (_isPipTask) {
    // Override stale status: any pip task whose output carries pip's own
    // success markers gets displayed as `done` regardless of what's in
    // localStorage. Old pre-fix runs landed in error/stopped state and
    // stuck there even after we taught the rest of the flow about pip
    // tasks — this is the catch-all that flips them to Finished on render.
    const out = String(task.output || '');
    const ranOk = /Successfully installed|Requirement already (?:satisfied|up-to-date)/i.test(out)
      && !/error:|ERROR:/.test(out.slice(-1024));
    if (ranOk && task.status !== 'done' && task.status !== 'running') {
      return { ...task, status: 'done' };
    }
    return task;
  }
  if (task.type === 'serve' && task.status === 'done' && !_serveOutputLooksReady(task)) {
    return { ...task, status: 'error' };
  }
  return task;
}

export function _loadTasks() {
  try { return (JSON.parse(localStorage.getItem(TASKS_KEY)) || []).map(_normalizeTaskForDisplay); }
  catch { return []; }
}

function _downloadRepoKey(task) {
  return String(task?.payload?.repo_id || task?.repo_id || task?.repo || task?.name || '').trim();
}

function _downloadHostKey(task) {
  return String(task?.remoteHost || task?.payload?.remote_host || 'local').trim() || 'local';
}

function _downloadDedupeKey(task) {
  if (!task || task.type !== 'download') return '';
  const repo = _downloadRepoKey(task);
  if (!repo) return '';
  return `${_downloadHostKey(task)}\n${repo}`;
}

function _pruneQueuedDownloadDuplicates(tasks) {
  if (!Array.isArray(tasks) || !tasks.length) return tasks || [];
  const launched = new Set();
  for (const task of tasks) {
    if (task?.type !== 'download' || task.status === 'queued') continue;
    const key = _downloadDedupeKey(task);
    if (key) launched.add(key);
  }

  let changed = false;
  const seenQueued = new Set();
  const next = tasks.filter(task => {
    if (task?.type !== 'download' || task.status !== 'queued') return true;
    const key = _downloadDedupeKey(task);
    if (!key) return true;
    if (launched.has(key) || seenQueued.has(key)) {
      changed = true;
      return false;
    }
    seenQueued.add(key);
    return true;
  });
  return changed ? next : tasks;
}

function _loadPrunedTasks() {
  const tasks = _loadTasks();
  const pruned = _pruneQueuedDownloadDuplicates(tasks);
  if (pruned !== tasks) _saveTasks(pruned);
  return pruned;
}

// Tombstones for removed tasks. Without these, removing a task only deletes it
// locally — but the server still has it (its own POST guard even re-preserves
// recently-added ones), so the next sync/poll merges it right back ("I removed
// it and it came back"). A tombstone makes the removal stick: merges skip any
// id the user removed, until the entry expires.
const _REMOVED_KEY = 'cookbook-removed-tasks';
const _TOMBSTONE_TTL_MS = 24 * 3600 * 1000;
function _loadTombstones() {
  try { return JSON.parse(localStorage.getItem(_REMOVED_KEY)) || {}; }
  catch { return {}; }
}
function _tombstoneTask(id) {
  if (!id) return;
  const tomb = _loadTombstones();
  const now = Date.now();
  tomb[id] = now;
  for (const k in tomb) { if (now - tomb[k] > _TOMBSTONE_TTL_MS) delete tomb[k]; }
  localStorage.setItem(_REMOVED_KEY, JSON.stringify(tomb));
}
function _isTombstoned(id) {
  const ts = _loadTombstones()[id];
  return ts != null && (Date.now() - ts) <= _TOMBSTONE_TTL_MS;
}

function _stripTaskSecrets(task) {
  if (!task || typeof task !== 'object') return task;
  const safe = { ...task };
  if (safe.payload && typeof safe.payload === 'object') {
    safe.payload = { ...safe.payload };
    delete safe.payload.hf_token;
  }
  return safe;
}

function _stripStateSecrets(state) {
  const safe = { ...state };
  if (safe.env && typeof safe.env === 'object') {
    const { hfToken, ...env } = safe.env;
    if (hfToken) env.hfToken = hfToken;
    safe.env = env;
  }
  if (Array.isArray(safe.tasks)) safe.tasks = safe.tasks.map(_stripTaskSecrets);
  return safe;
}

export function _saveTasks(tasks) {
  localStorage.setItem(TASKS_KEY, JSON.stringify((tasks || []).map(_stripTaskSecrets)));
  _syncToServer();
}

export function _addTask(sessionId, name, type, payload) {
  let tasks = _loadTasks();
  const remoteHost = (payload && payload.remote_host) || _envState.remoteHost || '';
  const sshPort = (payload && payload.ssh_port) || _getPort(remoteHost) || '';
  const platform = (payload && payload.platform) || _getPlatform(remoteHost) || '';
  // Serving a model supersedes its finished download — clear the matching
  // finished download card (covers serving directly from the Serve tab, not just
  // via the download card's "Serve →" button).
  if (type === 'serve' && payload && payload.repo_id) {
    const _repoId = payload.repo_id;
    tasks = tasks.filter(t => !(t.type === 'download' && t.status === 'done' && t.payload && t.payload.repo_id === _repoId));
  }
  if (type === 'download' && payload && payload.repo_id) {
    const key = _downloadDedupeKey({ type: 'download', payload, remoteHost });
    tasks = tasks.filter(t => {
      if (t.sessionId === sessionId) return false;
      return !(key && t.type === 'download' && t.status === 'queued' && _downloadDedupeKey(t) === key);
    });
  }
  const task = _stripTaskSecrets({ id: sessionId, sessionId, name, type, status: 'running', output: '', ts: Date.now(), payload: payload || null, remoteHost, sshPort, platform });
  tasks.push(task);
  _saveTasks(tasks);
  // New action → collapse all other cards, leave only this one open.
  _soloExpandTaskId = sessionId;
  _renderRunningTab();
  // Always start the background monitor when a task is added — works even
  // when modal is closed and ensures the sidebar shows live status immediately
  _startBackgroundMonitor();
  // Switch to Running tab
  const body = document.querySelector('#cookbook-modal .cookbook-body');
  if (body) {
    const tab = body.querySelector('.cookbook-tab[data-backend="Running"]');
    if (tab) tab.click();
  }
  return task;
}

function _updateTask(sessionId, updates) {
  const tasks = _loadTasks();
  const task = tasks.find(t => t.sessionId === sessionId);
  if (task) {
    Object.assign(task, updates);
    _saveTasks(tasks);
  }
  if ('status' in updates || '_unreachable' in updates) {
    _refreshServerDots();
  }
  if (updates.status && updates.status !== 'running') {
    const el = document.querySelector(`.cookbook-task[data-task-id="${sessionId}"]`);
    if (el) {
      if (el._uptimeInterval) { clearInterval(el._uptimeInterval); el._uptimeInterval = null; }
      const wave = el.querySelector('.cookbook-task-wave');
      if (wave) wave.style.display = 'none';
      const uptime = el.querySelector('.cookbook-task-uptime');
      if (uptime) uptime.style.display = 'none';
    }
  }
}

function _refreshDepsAfterInstall(task) {
  if (!task || task.type !== 'download' || !task.payload?._dep) return;
  try {
    _refreshDependencies?.({ host: task.remoteHost || '', port: task.sshPort || '', venv: task.payload?.env_path || '' });
  } catch {}
}

export function _removeTask(sessionId) {
  _tombstoneTask(sessionId);  // so sync/poll can't resurrect it
  const tasks = _loadTasks().filter(t => t.sessionId !== sessionId);
  _saveTasks(tasks);
  _renderRunningTab();
}

// Fade/slide the task card out, then remove it — so the smooth exit is the same
// whether a task auto-stops or the user removes/kills it manually.
function _animateOutThenRemove(el, sessionId) {
  if (!el || !el.style) { _removeTask(sessionId); return; }
  if (el._abort) el._abort.abort();
  el.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
  el.style.opacity = '0';
  el.style.transform = 'translateX(-10px)';
  setTimeout(() => _removeTask(sessionId), 360);
}

// ── tmux / Windows session commands ──

export function _tmuxCmd(task, tmuxArgs) {
  if (_isWindows(task)) {
    return _winSessionCmd(task, tmuxArgs);
  }
  if (task.remoteHost) {
    return `ssh ${_sshPrefix(_getPort(task))}${task.remoteHost} 'tmux ${tmuxArgs}' 2>/dev/null`;
  }
  return `tmux ${tmuxArgs} 2>/dev/null`;
}

function _winSessionCmd(task, tmuxArgs) {
  const host = task.remoteHost;
  const sd = host ? '$env:TEMP\\odysseus-sessions' : '$env:TEMP\\odysseus-tmux';
  const sid = task.sessionId;
  const pf = _sshPrefix(_getPort(task));
  if (tmuxArgs.includes('capture-pane')) {
    const lines = tmuxArgs.match(/-S\s*-?(\d+)/)?.[1] || '200';
    const ps = host
      ? `Get-Content '${sd}\\${sid}.log' -Tail ${lines} -ErrorAction SilentlyContinue`
      : `Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.log') -Tail ${lines} -ErrorAction SilentlyContinue`;
    return host ? `ssh ${pf}${host} "powershell -Command \\"${ps}\\""` : `powershell -Command "${ps}"`;
  }
  if (tmuxArgs.includes('has-session')) {
    const ps = host
      ? `$p = Get-Content '${sd}\\${sid}.pid' -ErrorAction SilentlyContinue; if ($p) { Get-Process -Id $p -ErrorAction SilentlyContinue | Out-Null; if ($?) { exit 0 } else { exit 1 } } else { exit 1 }`
      : `$p = Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.pid') -ErrorAction SilentlyContinue; if ($p) { Get-Process -Id $p -ErrorAction SilentlyContinue | Out-Null; if ($?) { exit 0 } else { exit 1 } } else { exit 1 }`;
    return host ? `ssh ${pf}${host} "powershell -Command \\"${ps}\\""` : `powershell -Command "${ps}"`;
  }
  if (tmuxArgs.includes('kill-session')) {
    const ps = host
      ? `$p = Get-Content '${sd}\\${sid}.pid' -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }; Remove-Item '${sd}\\${sid}.*' -Force -ErrorAction SilentlyContinue`
      : `$p = Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.pid') -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }; Remove-Item (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.*') -Force -ErrorAction SilentlyContinue`;
    return host ? `ssh ${pf}${host} "powershell -Command \\"${ps}\\""` : `powershell -Command "${ps}"`;
  }
  if (tmuxArgs.includes('send-keys') && tmuxArgs.includes('C-c')) {
    const ps = host
      ? `$p = Get-Content '${sd}\\${sid}.pid' -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -ErrorAction SilentlyContinue }`
      : `$p = Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.pid') -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -ErrorAction SilentlyContinue }`;
    return host ? `ssh ${pf}${host} "powershell -Command \\"${ps}\\""` : `powershell -Command "${ps}"`;
  }
  return host ? `ssh ${pf}${host} 'tmux ${tmuxArgs}' 2>/dev/null` : `tmux ${tmuxArgs} 2>/dev/null`;
}

function _tmuxGracefulKill(task) {
  if (_isWindows(task)) {
    const host = task.remoteHost;
    const sd = host ? '$env:TEMP\\odysseus-sessions' : '$env:TEMP\\odysseus-tmux';
    const sid = task.sessionId;
    const pf = _sshPrefix(_getPort(task));
    const ps = host
      ? `$p = Get-Content '${sd}\\${sid}.pid' -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }; Remove-Item '${sd}\\${sid}.*' -Force -ErrorAction SilentlyContinue`
      : `$p = Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.pid') -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }; Remove-Item (Join-Path $env:TEMP 'odysseus-tmux\\${sid}.*') -Force -ErrorAction SilentlyContinue`;
    return host ? `ssh ${pf}${host} "powershell -Command \\"${ps}\\""` : `powershell -Command "${ps}"`;
  }
  if (task.remoteHost) {
    return `ssh ${_sshPrefix(_getPort(task))}${task.remoteHost} 'tmux send-keys -t ${task.sessionId} C-c 2>/dev/null; sleep 2; tmux kill-session -t ${task.sessionId} 2>/dev/null'`;
  }
  return `tmux send-keys -t ${task.sessionId} C-c 2>/dev/null; sleep 2; tmux kill-session -t ${task.sessionId} 2>/dev/null`;
}

function _shQuote(value) {
  return "'" + String(value ?? '').replace(/'/g, "'\\''") + "'";
}

function _taskLooksOllama(task, outputText = '') {
  const haystack = `${task?.payload?.backend || ''} ${task?.payload?._cmd || ''} ${task?.payload?._fields?.backend || ''} ${outputText || ''}`;
  return /\bollama\b/i.test(haystack) || /Ollama API ready on port\s+\d+/i.test(haystack);
}

function _ollamaBaseUrlForTask(task, outputText = '') {
  const out = String(outputText || '');
  const ready = out.match(/Ollama API ready on port\s+\d+:\s*(http:\/\/[^\s]+)/i);
  if (ready) return ready[1].replace(/\/+$/, '');
  const cmd = String(task?.payload?._cmd || '');
  const host = cmd.match(/OLLAMA_HOST=([^\s]+)/)?.[1] || '';
  const port = host.match(/:(\d+)$/)?.[1] || '11434';
  return `http://127.0.0.1:${port}`;
}

function _ollamaModelForTask(task) {
  return String(task?.payload?.model || task?.payload?.repo_id || task?.name || '').trim();
}

function _ollamaUnloadCommand(task, outputText = '') {
  if (!_taskLooksOllama(task, outputText)) return '';
  const model = _ollamaModelForTask(task);
  if (!model) return '';
  const base = _ollamaBaseUrlForTask(task, outputText);
  const body = JSON.stringify({ model, prompt: '', keep_alive: 0, stream: false });
  const inner = `curl -sf -X POST ${_shQuote(base + '/api/generate')} -H 'Content-Type: application/json' -d ${_shQuote(body)} >/dev/null 2>&1 || true`;
  if (task.remoteHost) {
    return `ssh ${_sshPrefix(_getPort(task))}${task.remoteHost} ${_shQuote(inner)}`;
  }
  return inner;
}

function _endpointUrlForTask(task, outputText = '') {
  if (_taskLooksOllama(task, outputText)) {
    return _ollamaBaseUrlForTask(task, outputText) + '/v1';
  }
  const host = _connectHostFromRemote(task.remoteHost);
  const portMatch = task.payload?._cmd?.match(/--port\s+(\d+)/);
  const port = portMatch ? portMatch[1] : '8000';
  return `http://${host}:${port}/v1`;
}

// ── Wave animation ──

const _waveFrames = ['▁▂▃', '▂▃▄', '▃▄▅', '▄▅▆', '▅▆▅', '▆▅▄', '▅▄▃', '▄▃▂', '▃▂▁'];
let _waveIdx = 0;
let _waveTimer = null;
const _waveEls = new Set();

function _startWaveSync() {
  if (_waveTimer) return;
  _waveTimer = setInterval(() => {
    _waveIdx = (_waveIdx + 1) % _waveFrames.length;
    for (const el of _waveEls) {
      if (!el.isConnected) { _waveEls.delete(el); continue; }
      if (el.style.display !== 'none') el.textContent = _waveFrames[_waveIdx];
    }
    if (!_waveEls.size) { clearInterval(_waveTimer); _waveTimer = null; }
  }, 200);
}

function _registerWaveEl(el) { _waveEls.add(el); _startWaveSync(); }

// ── Notifications ──

function _showCookbookNotif(isError = false) {
  const dot = document.getElementById('cookbook-notif-dot');
  if (dot) {
    dot.style.display = '';
    dot.classList.toggle('cookbook-notif-error', isError);
  }
  const btn = document.getElementById('tool-cookbook-btn');
  if (btn) { btn.style.opacity = '1'; btn.classList.add('cookbook-notif-active'); }
  const railBtn = document.getElementById('rail-cookbook');
  if (railBtn) {
    railBtn.classList.remove('rail-notify-success', 'rail-notify-error');
    railBtn.classList.add('rail-notify', isError ? 'rail-notify-error' : 'rail-notify-success', 'cookbook-notif-active');
  }
  if (window._syncRailDynamic) window._syncRailDynamic();
}

export function _clearCookbookNotif() {
  const dot = document.getElementById('cookbook-notif-dot');
  if (dot) dot.style.display = 'none';
  const btn = document.getElementById('tool-cookbook-btn');
  if (btn) { btn.style.opacity = ''; btn.classList.remove('cookbook-notif-active'); }
  const railBtn = document.getElementById('rail-cookbook');
  if (railBtn) {
    railBtn.classList.remove('rail-notify', 'rail-notify-success', 'cookbook-notif-active');
  }
  if (window._syncRailDynamic) window._syncRailDynamic();
}

// ── Presets helper (for save-from-task) ──

// A preset must carry the venv + activated GPUs, not just the command — without
// them a relaunch has no environment activated and no GPU pinning, so a config
// that worked when saved fails on reload. Pull them from the launch payload
// (_env/_envPath/_gpus, captured by _launchServeTask) and fold them into the
// serve-form `fields` the Serve panel restores from.
function _presetEnvFields(task) {
  const p = task.payload || {};
  const fields = { ...(p._fields || {}) };
  // The Serve panel's venv field is a path; conda/venv both activate from it.
  if (p._envPath && (p._env === 'venv' || p._env === 'conda')) fields.venv = fields.venv || p._envPath;
  if (p._gpus) fields.gpus = p._gpus;
  return {
    fields: Object.keys(fields).length ? fields : undefined,
    env: p._env || '',
    envPath: p._envPath || '',
    gpus: p._gpus || '',
  };
}

function _saveTaskAsPreset(task, label) {
  const host = task.remoteHost || 'localhost';
  const portMatch = task.payload?._cmd?.match(/--port\s+(\d+)/);
  const port = portMatch ? portMatch[1] : '8000';
  const presets = _loadPresets();
  if (presets.some(p => p.cmd === task.payload._cmd)) return false;
  presets.push({ name: task.name, model: task.payload.repo_id, backend: 'vllm', host, port, cmd: task.payload._cmd, remoteHost: task.remoteHost || '', label: label || task.name, ..._presetEnvFields(task) });
  _savePresets(presets);
  return true;
}

// Same model-matching as cookbookServe's _presetsForModel, so the auto-save cap
// counts the exact slots the Serve tab shows for this model.
function _presetsForModelLocal(presets, repo) {
  const short = (repo || '').split('/').pop();
  return presets.filter(p => {
    const pm = p.model || '', pn = p.name || '';
    return pm === repo || pn === repo || pm.split('/').pop() === short || pn === short;
  });
}

// Build a short auto-label from the launched command so an auto-saved config is
// recognizable in the Saved dropdown (e.g. "TP2 · 16k ctx · AWQ").
function _autoConfigLabel(task) {
  const cmd = task.payload?._cmd || '';
  const bits = [];
  const tp = cmd.match(/--tensor-parallel-size[=\s]+(\d+)/);
  if (tp && tp[1] !== '1') bits.push('TP' + tp[1]);
  const ml = cmd.match(/--max-model-len[=\s]+(\d+)/);
  if (ml) { const n = parseInt(ml[1]); bits.push((n >= 1024 ? Math.round(n / 1024) + 'k' : n) + ' ctx'); }
  const q = (task.name || '').match(/AWQ|GPTQ|FP8|Q4|Q5|Q6|Q8|INT8|INT4/i);
  if (q) bits.push(q[0].toUpperCase());
  return bits.length ? bits.join(' · ') : 'working';
}

// Auto-save a serve config the moment its endpoint registers successfully, and
// flag it confirmed-working. Dedups by exact command: if the same settings are
// already saved we just upgrade that slot's badge instead of duplicating it.
// Runs at most once per task.
function _autoSaveWorkingConfig(task) {
  if (!task || task.type !== 'serve' || !task.payload?._cmd) return;
  if (task._autoSaved) return;
  const cmd = task.payload._cmd;
  // Diffusion/image servers aren't vLLM presets — skip them.
  if (cmd.includes('diffusion_server')) { task._autoSaved = true; return; }
  const model = task.payload.repo_id || task.name;
  const presets = _loadPresets();
  const existing = presets.find(p => p.cmd === cmd);
  if (existing) {
    task._autoSaved = true;
    if (!existing.confirmedWorking) { existing.confirmedWorking = true; _savePresets(presets); }
    return;   // already saved → just confirm it, no duplicate, no toast
  }
  // Respect the per-model cap the manual save flow uses (max 5).
  if (_presetsForModelLocal(presets, model).length >= 5) { task._autoSaved = true; return; }
  const host = task.remoteHost || 'localhost';
  const portMatch = cmd.match(/--port[=\s]+(\d+)/);
  const port = portMatch ? portMatch[1] : '8000';
  presets.push({
    name: task.name, model, backend: 'vllm', host, port,
    cmd, remoteHost: task.remoteHost || '',
    label: _autoConfigLabel(task), confirmedWorking: true, autoSaved: true,
    ..._presetEnvFields(task),
  });
  _savePresets(presets);
  task._autoSaved = true;
  uiModule.showToast('Saved working config');
}

// ── Cross-device sync ──

let _syncTimer = null;
function _syncToServer() {
  // Debounce to coalesce bursts of writes, but keep latency low so the server
  // is effectively authoritative across devices
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(async () => {
    try {
      // Don't push a not-yet-hydrated state. A legit state always has at
      // least the "Local" server, so an empty servers list means we loaded
      // before GET /state populated _envState — syncing it would wipe the
      // saved servers. (The server has an anti-wipe guard too; this avoids
      // the needless round-trip.)
      if (!_envState || !Array.isArray(_envState.servers) || _envState.servers.length === 0) return;
      const state = {
        tasks: _loadTasks(),
        presets: _loadPresets(),
        env: _envState,
        serveState: null,
      };
      try { state.serveState = JSON.parse(localStorage.getItem(SERVE_STATE_KEY)); } catch {}
      await fetch('/api/cookbook/state', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_stripStateSecrets(state)),
      });
    } catch {}
  }, 400);
}

// Normalize state from server: collapse legacy duplicate keys to canonical form.
// - server.modelDir (singular) → server.modelDirs[0] (canonical)
// - strip ✕/✖ pollution from modelDirs
// - dedupe modelDirs
function _normalizeState(state) {
  if (!state || typeof state !== 'object') return state;
  if (state.env && Array.isArray(state.env.servers)) {
    for (const s of state.env.servers) {
      // Collapse legacy modelDir → modelDirs
      let dirs = Array.isArray(s.modelDirs) ? s.modelDirs : [];
      if (s.modelDir && !dirs.includes(s.modelDir)) dirs.push(s.modelDir);
      dirs = dirs
        .map(d => (d || '').replaceAll('\u2715', '').replaceAll('\u2716', '').trim())
        .filter(Boolean);
      if (!dirs.includes('~/.cache/huggingface/hub')) dirs.unshift('~/.cache/huggingface/hub');
      s.modelDirs = [...new Set(dirs)];
      delete s.modelDir; // Drop the legacy singular form
      // A download target that's no longer in the dir list falls back to the
      // default HF cache (empty) so we never download into an unscanned dir.
      if (s.downloadDir && !s.modelDirs.includes(s.downloadDir)) s.downloadDir = '';
    }
  }
  return state;
}

export async function _syncFromServer() {
  try {
    const res = await fetch('/api/cookbook/state', { credentials: 'same-origin' });
    if (!res.ok) return false;
    const state = _normalizeState(await res.json());
    if (!state || !state.env) return false;

    const localTasks = _loadTasks();
    const serverTasks = state.tasks || [];

    const localIds = new Set(localTasks.map(t => t.sessionId));
    const merged = [...localTasks];
    for (const t of serverTasks) {
      if (!localIds.has(t.sessionId) && !_isTombstoned(t.sessionId)) {
        merged.push(t);
      }
    }
    localStorage.setItem(TASKS_KEY, JSON.stringify(merged.map(_stripTaskSecrets)));

    if (state.env) {
      // The active server selection (remoteHost + its env/path/platform) is a
      // per-device, live choice. NEVER let the server's stored copy overwrite
      // it here — doing so silently snapped the active host back to whatever was
      // saved server-side, so downloads/scans ignored what the user just
      // picked. Sync only the shared non-secret settings (servers list, gpus, paths).
      const { remoteHost: _rh, env: _e, envPath: _ep, platform: _pf, ...settings } = state.env;
      delete settings.hfToken;
      Object.assign(_envState, settings);
      const { hfToken, ...safeState } = _envState;
      localStorage.setItem('cookbook-last-state', JSON.stringify(safeState));
    }
    if (state.presets) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.presets));
    }
    if (state.serveState) {
      localStorage.setItem(SERVE_STATE_KEY, JSON.stringify(state.serveState));
    }
    return true;
  } catch { return false; }
}

// ── Retry download ──

// Bounded auto-retry counter for downloads, keyed by model — network blips on
// big multi-file downloads are common and HF resumes from the .incomplete parts.
const _dlRetryCount = new Map();
const _DL_MAX_AUTO_RETRY = 2;

// Kill + relaunch a task (download or serve). Shared by the ⋮ → Restart action
// and the click-to-retry on a stalled download badge.
async function _retryTask(el, task) {
  if (el && el._abort) el._abort.abort();
  const badge = el?.querySelector('.cookbook-task-status');
  if (badge) { badge.textContent = 'restarting...'; badge.className = 'cookbook-task-status'; }
  try {
    await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: _tmuxGracefulKill(task) }),
    });
  } catch {}
  if (task.payload) {
    if (task.type === 'serve' && task.payload._cmd) {
      _removeTask(task.sessionId);
      _launchServeTask(task.name, task.payload.repo_id, task.payload._cmd, task.payload._fields, task.remoteHost || '');
    } else {
      uiModule.showToast('Retrying download — progress may look reset while HuggingFace checks cached files, then it should resume.', 7000);
      _updateTask(task.sessionId, {
        status: 'running',
        output: `${task.output || ''}\n\n[odysseus] Retrying download. Progress may briefly look like a fresh download while HuggingFace checks cached/incomplete files; cached partial files will be reused when available.`.trim(),
        _retrying: true,
      });
      _retryDownload(task.name, task.payload, task.sessionId);
    }
  }
}

async function _retryDownload(name, payload, replaceSessionId = '') {
  try {
    // A retry means the fast hf_transfer path already failed once — fall back to
    // the plain, reliable downloader for this and any further attempt (it resumes
    // from the cached .incomplete files, so no progress is lost).
    const _payload = { ...(payload || {}), disable_hf_transfer: true };
    const res = await fetch('/api/model/download', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_payload),
    });
    if (!res.ok) {
      uiModule.showToast('Download failed: HTTP ' + res.status);
      if (replaceSessionId) _updateTask(replaceSessionId, { status: 'crashed', _retrying: false });
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      uiModule.showToast('Download failed: ' + (data.error || ''));
      if (replaceSessionId) _updateTask(replaceSessionId, { status: 'crashed', _retrying: false });
      return;
    }
    if (replaceSessionId) {
      const tasks = _loadTasks();
      const task = tasks.find(t => t.sessionId === replaceSessionId);
      if (task) {
        task.id = data.session_id;
        task.sessionId = data.session_id;
        task.status = 'running';
        task.output = '';
        task.ts = Date.now();
        task.payload = _payload;
        task._retrying = false;
        _saveTasks(tasks);
        _soloExpandTaskId = data.session_id;
        _renderRunningTab();
        _startBackgroundMonitor();
      } else {
        _addTask(data.session_id, name, 'download', _payload);
      }
    } else {
      _addTask(data.session_id, name, 'download', _payload);
    }
    uiModule.showToast(`Downloading ${name}...`);
  } catch (e) {
    uiModule.showToast('Download failed: ' + e.message);
    if (replaceSessionId) _updateTask(replaceSessionId, { status: 'crashed', _retrying: false });
  }
}

// ── Serve auto-fix (kill + relaunch with env var) ──

// Block stacked retries: once any "Retry with X" is clicked for a task, ignore
// every further retry click for it. Each retry fires its own _launchServeTask,
// so clicking several options — or one repeatedly during the fade-out / while a
// relaunch was loading — used to stack up multiple servers (e.g. 6 launches).
// The flag rides on the card element (removed right after), so it can't re-arm.
function _guardServeRetry(panel, taskEl) {
  if (!taskEl || taskEl.dataset.retrying) return false;
  taskEl.dataset.retrying = '1';
  panel.querySelectorAll('button').forEach(b => {
    b.disabled = true;
    b.style.opacity = '0.5';
    b.style.pointerEvents = 'none';
  });
  return true;
}

export async function _serveAutoFix(panel, envVar) {
  const taskEl = panel.closest('.cookbook-task');
  if (!taskEl) return;
  const taskId = taskEl.dataset.taskId;
  const tasks = _loadTasks();
  const task = tasks.find(t => t.sessionId === taskId);
  if (!task || !task.payload) return;
  if (!_guardServeRetry(panel, taskEl)) return;

  const killCmd = _tmuxCmd(task, `kill-session -t ${taskId}`);
  try {
    await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: killCmd }),
    });
  } catch {}

  _animateOutThenRemove(taskEl, taskId);

  const origCmd = task.payload._cmd || '';
  const newCmd = `export ${envVar} && ${origCmd}`;

  const origHost = _envState.remoteHost;
  if (task.remoteHost) _envState.remoteHost = task.remoteHost;
  try {
    uiModule.showToast(`Retrying with ${envVar}...`);
    await _launchServeTask(task.name, task.payload.repo_id, newCmd);
  } finally {
    // Always restore — otherwise a thrown launch leaves the global host stuck
    // on this serve task, so later downloads/scans hit it.
    _envState.remoteHost = origHost;
  }
}

// Open the Serve panel pre-filled for a task — the same flow as the task's
// Edit button, but optionally with a modified command (used by the diagnosis
// "Retry with X" buttons so a retry lands in the editable Serve panel with the
// adjusted setting, instead of blindly relaunching).
async function _openServeEditForTask(task, cmdOverride, fieldOverrides = null) {
  const repo = task.payload?.repo_id;
  if (!repo) { uiModule.showToast('No model info on this task'); return; }
  const cmd = cmdOverride || task.payload?._cmd;
  // A modified cmd must be re-parsed; otherwise prefer the exact launch fields.
  let fields = cmdOverride
    ? _parseServeCmdToFields(cmd)
    : (task.payload?._fields || (cmd ? _parseServeCmdToFields(cmd) : null));
  if (fieldOverrides && typeof fieldOverrides === 'object') {
    fields = { ...(fields || {}), ...fieldOverrides };
  }
  // Switch the active server to the one this serve ran on (mirrors _openEdit).
  const _tHost = task.remoteHost || '';
  _envState.remoteHost = _tHost;
  const _tSrv = _envState.servers.find(s => s.host === _tHost);
  if (_tSrv) { _envState.env = _tSrv.env || 'none'; _envState.envPath = _tSrv.envPath || ''; _envState.platform = _tSrv.platform || ''; }
  else if (!_tHost) { _envState.env = 'none'; _envState.envPath = ''; _envState.platform = ''; }
  document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
    if (!sel || sel.tagName !== 'SELECT') return;
    sel.value = _tHost || 'local';
  });
  try {
    const { openServePanelForRepo } = await import('./cookbookServe.js');
    await openServePanelForRepo(repo, fields);
  } catch (err) {
    console.error('[cookbook] open serve panel failed', err);
    uiModule.showToast('Could not open serve panel');
  }
}

export async function _serveAutoRetryReplace(panel, flag, value) {
  const taskEl = panel.closest('.cookbook-task');
  if (!taskEl) return;
  const taskId = taskEl.dataset.taskId;
  const tasks = _loadTasks();
  const task = tasks.find(t => t.sessionId === taskId);
  if (!task || !task.payload || !task.payload._cmd) return;
  if (!_guardServeRetry(panel, taskEl)) return;

  try {
    await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${taskId}`) }),
    });
  } catch {}

  _animateOutThenRemove(taskEl, taskId);

  let newCmd = task.payload._cmd;
  const re = new RegExp(flag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s+\\S+');
  if (re.test(newCmd)) {
    newCmd = newCmd.replace(re, `${flag} ${value}`);
  } else {
    newCmd += ` ${flag} ${value}`;
  }

  const origHost = _envState.remoteHost;
  if (task.remoteHost) _envState.remoteHost = task.remoteHost;
  try {
    uiModule.showToast(`Retrying with ${flag} ${value}...`);
    await _launchServeTask(task.name, task.payload.repo_id, newCmd);
  } finally {
    _envState.remoteHost = origHost;
  }
}

export async function _serveAutoRetryRemove(panel, flag) {
  const taskEl = panel.closest('.cookbook-task');
  if (!taskEl) return;
  const taskId = taskEl.dataset.taskId;
  const tasks = _loadTasks();
  const task = tasks.find(t => t.sessionId === taskId);
  if (!task || !task.payload || !task.payload._cmd) return;
  if (!_guardServeRetry(panel, taskEl)) return;

  try {
    await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${taskId}`) }),
    });
  } catch {}

  _animateOutThenRemove(taskEl, taskId);

  let newCmd = task.payload._cmd;
  const re = new RegExp(flag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s+\\S+');
  newCmd = newCmd.replace(re, '').replace(/\s{2,}/g, ' ').trim();

  const origHost = _envState.remoteHost;
  if (task.remoteHost) _envState.remoteHost = task.remoteHost;
  try {
    uiModule.showToast(`Retrying without ${flag}...`);
    await _launchServeTask(task.name, task.payload.repo_id, newCmd);
  } finally {
    _envState.remoteHost = origHost;
  }
}

export async function _serveAutoRetry(panel, flag) {
  const taskEl = panel.closest('.cookbook-task');
  if (!taskEl) return;
  const taskId = taskEl.dataset.taskId;
  const tasks = _loadTasks();
  const task = tasks.find(t => t.sessionId === taskId);
  if (!task || !task.payload || !task.payload._cmd) return;
  if (!_guardServeRetry(panel, taskEl)) return;

  try {
    await fetch('/api/shell/exec', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${taskId}`) }),
    });
  } catch {}

  _animateOutThenRemove(taskEl, taskId);

  let newCmd = task.payload._cmd;
  if (!newCmd.includes(flag)) {
    newCmd += ' ' + flag;
  }

  const origHost = _envState.remoteHost;
  if (task.remoteHost) _envState.remoteHost = task.remoteHost;
  try {
    uiModule.showToast(`Retrying with ${flag}...`);
    await _launchServeTask(task.name, task.payload.repo_id, newCmd);
  } finally {
    _envState.remoteHost = origHost;
  }
}

// ── Edit-command prompt ──
// Shows a small modal with a textarea pre-filled with the current serve cmd.
// Resolves to the edited string on Save, or null on Cancel.
function _promptEditServeCmd(currentCmd) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'cookbook-edit-overlay';
    overlay.innerHTML = `
      <div class="cookbook-edit-modal">
        <div class="cookbook-edit-title">Edit serve command</div>
        <textarea class="cookbook-edit-textarea" spellcheck="false"></textarea>
        <div class="cookbook-edit-actions">
          <button class="cookbook-edit-cancel memory-toolbar-btn">Cancel</button>
          <button class="cookbook-edit-save memory-toolbar-btn">Save &amp; relaunch</button>
        </div>
      </div>`;
    const ta = overlay.querySelector('.cookbook-edit-textarea');
    ta.value = currentCmd || '';
    document.body.appendChild(overlay);
    setTimeout(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }, 0);

    const close = (result) => {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      resolve(result);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(null);
      else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) close(ta.value.trim() || null);
    };
    overlay.querySelector('.cookbook-edit-cancel').addEventListener('click', () => close(null));
    overlay.querySelector('.cookbook-edit-save').addEventListener('click', () => close(ta.value.trim() || null));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
  });
}

// ── Launch serve task ──

// Best-effort reconstruction of serve-form field values from a raw launch
// command. Fallback for tasks created before _fields capture existed.
// Mirrors the regex parser in cookbookServe.js's _loadSlotIntoPanel.
function _parseServeCmdToFields(cmd) {
  if (!cmd) return null;
  const ex = (re) => { const m = cmd.match(re); return m ? m[1] : ''; };
  const fields = {
    backend: cmd.includes('llama_cpp') || cmd.includes('llama-server') ? 'llamacpp'
      : cmd.includes('diffusion_server') ? 'diffusers'
      : cmd.includes('sglang') ? 'sglang'
      : cmd.includes('ollama') ? 'ollama' : 'vllm',
    port: ex(/--port\s+(\d+)/) || '8000',
    tp: ex(/--tensor-parallel-size\s+(\d+)/) || '1',
    ctx: ex(/--max-model-len\s+(\d+)/) || ex(/--n_ctx\s+(\d+)/) || ex(/-c\s+(\d+)/) || '8192',
    gpu_mem: ex(/--gpu-memory-utilization\s+([\d.]+)/) || '0.90',
    swap: ex(/--swap-space\s+(\d+)/) || '',
    dtype: ex(/--dtype\s+(\w+)/) || 'auto',
    vllm_kv_cache_dtype: ex(/--kv-cache-dtype\s+([\w.-]+)/) || 'auto',
    max_seqs: ex(/--max-num-seqs\s+(\d+)/) || '',
    gpus: ex(/CUDA_VISIBLE_DEVICES=(\S+)/) || '',
    cache_type: ex(/(?:--cache-type-k|-ctk)\s+(\S+)/) || '',
    llama_fit: ex(/(?:--fit|-fit)\s+(on|off)/) || '',
    llama_split_mode: ex(/(?:--split-mode|-sm)\s+(none|layer|row|tensor)/) || '',
    llama_tensor_split: ex(/(?:--tensor-split|-ts)\s+([0-9.,]+)/) || '',
    llama_main_gpu: ex(/(?:--main-gpu|-mg)\s+(\d+)/) || '',
    llama_parallel: ex(/(?:--parallel|-np)\s+(\d+)/) || '',
    llama_batch_size: ex(/(?:--batch-size|-b)\s+(\d+)/) || '',
    llama_ubatch_size: ex(/(?:--ubatch-size|-ub)\s+(\d+)/) || '',
    llama_spec_tokens: ex(/--spec-draft-n-max\s+(\d+)/) || '3',
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
  const spec = cmd.match(/--speculative-config\s+'?\{[^}]*"method"\s*:\s*"([^"]+)"[^}]*"num_speculative_tokens"\s*:\s*(\d+)/);
  if (spec) { fields.spec_method = spec[1]; fields.spec_tokens = spec[2]; }
  return fields;
}

export async function _launchServeTask(shortName, repo, cmd, fields, hostOverride) {
  // Host resolution mirrors the download path: when the caller passes an explicit
  // host (resolved from the dropdown the user actually picked), use it and look
  // up that server's port/platform from the shared servers list. Only fall back
  // to _envState.remoteHost for legacy callers (diagnosis/pip-update).
  const _host = (hostOverride !== undefined) ? (hostOverride || '') : (_envState.remoteHost || '');
  const _hsrv = _envState.servers.find(s => s.host === _host) || {};
  const _hplatform = _host ? (_hsrv.platform || '') : (_envState.platform || '');

  // Replace any serve already targeting this same host:port — you can't run two
  // servers on one port, so re-serving (or retrying) should stop & remove the
  // old one instead of leaving a dead duplicate behind. (The retry buttons
  // already removed their own task, so this is a no-op for them.)
  try {
    const _pm = cmd.match(/--port[=\s]+(\d+)/) || cmd.match(/(?:^|\s)-p[=\s]+(\d+)/);
    const _newPort = _pm ? _pm[1] : '';
    if (_newPort) {
      for (const _t of _loadTasks()) {
        if (_t.type !== 'serve' || !_t.payload || !_t.payload._cmd) continue;
        const _tm = _t.payload._cmd.match(/--port[=\s]+(\d+)/) || _t.payload._cmd.match(/(?:^|\s)-p[=\s]+(\d+)/);
        if ((_tm ? _tm[1] : '') === _newPort && (_t.remoteHost || '') === _host) {
          try {
            await fetch('/api/shell/exec', {
              method: 'POST', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ command: _tmuxGracefulKill(_t) }),
            });
          } catch {}
          _removeTask(_t.sessionId);
        }
      }
    }
  } catch {}
  // Capture the env + GPU pin used for THIS launch BEFORE building the request.
  // The serve panel sets _envState.env/envPath/gpus, calls us, then restores them
  // synchronously — and our payload is built after an `await`, so reading
  // _envState there would see the restored (wrong) values. Persisting these lets
  // a saved preset relaunch with the same venv + GPUs (otherwise a confirmed
  // working config fails: no venv activation, no GPU pinning).
  const _usedEnv = _envState.env;
  const _usedEnvPath = _envState.envPath;
  const _usedGpus = _envState.gpus || '';
  let envPrefix = '';
  if (_isWindows()) {
    if (_envState.env === 'venv' && _envState.envPath) {
      envPrefix = '& ' + (_envState.envPath.endsWith('\\Scripts\\Activate.ps1') ? _envState.envPath : _envState.envPath + '\\Scripts\\Activate.ps1');
    } else if (_envState.env === 'conda' && _envState.envPath) {
      envPrefix = 'conda activate ' + _envState.envPath;
    }
  } else {
    if (_envState.env === 'venv' && _envState.envPath) {
      const p = _envState.envPath;
      envPrefix = 'source ' + (p.endsWith('/bin/activate') ? p : p + '/bin/activate');
    } else if (_envState.env === 'conda' && _envState.envPath) {
      envPrefix = 'eval "$(conda shell.bash hook)" && conda activate ' + _envState.envPath;
    }
  }

  const reqBody = {
    repo_id: repo,
    cmd: cmd,
    remote_host: _host || undefined,
    ssh_port: _getPort(_host) || undefined,
    env_prefix: envPrefix || undefined,
    hf_token: _envState.hfToken || undefined,
    gpus: _envState.gpus || undefined,
    platform: _hplatform || undefined,
  };

  try {
    const res = await fetch('/api/model/serve', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody),
    });
    const data = await res.json();
    if (!data.ok) {
      // Two error shapes: `{ok:false, error}` (tmux launch failed) or
      // `{detail}` (FastAPI HTTPException). Show whichever is present
      // + log full payload so the user can copy the error.
      const err = data.error || data.detail || res.statusText || 'unknown';
      console.error('[cookbook] /api/model/serve failed', { status: res.status, body: data });
      uiModule.showToast('Failed to start: ' + String(err).slice(0, 200), 9000);
      return;
    }

    const _sp = _getPort(_host);
    // _fields = the exact structured serve-form values used for this launch,
    // so the "Edit / relaunch" button can re-open the Serve panel pre-filled
    // with these precise settings (not just the last-used-for-repo state).
    const payload = { repo_id: repo, remote_host: _host || undefined, ssh_port: _sp || undefined, _cmd: cmd, _fields: fields || undefined, _env: _usedEnv, _envPath: _usedEnvPath, _gpus: _usedGpus };
    _addTask(data.session_id, shortName, 'serve', payload);
    uiModule.showToast(`Serving ${shortName}...`);
  } catch (e) {
    uiModule.showToast('Failed: ' + e.message);
  }
}

// ── Render Running tab ──

export function _renderRunningTab() {
  // Auto-clear the sidebar notif (the bright-icon highlight) when no tasks
  // are actively running or errored. _showCookbookNotif fires on each task
  // event but the matching clear only ran on modal-open, so the highlight
  // persisted indefinitely after tasks finished in the background.
  try {
    const _activeTasks = _loadPrunedTasks().filter(t => t.status === 'running' || t.status === 'queued' || t.status === 'error');
    if (!_activeTasks.length) _clearCookbookNotif();
  } catch {}

  const body = document.querySelector('#cookbook-modal .cookbook-body');
  if (!body) return;

  // Capture expansion state so re-renders don't collapse whatever the user
  // had open. Task output: presence of .cookbook-task-collapsed means collapsed.
  // Section body: inline display:none means collapsed.
  const _collapsedTaskIds = new Set();
  const _expandedTaskIds = new Set();  // mobile: tasks the user explicitly opened
  body.querySelectorAll('.cookbook-task').forEach(tEl => {
    const id = tEl.dataset.taskId;
    if (!id) return;
    const wrap = tEl.querySelector('.cookbook-output-wrap');
    if (!wrap) return;
    if (wrap.classList.contains('cookbook-task-collapsed')) _collapsedTaskIds.add(id);
    else _expandedTaskIds.add(id);
  });
  // A new action was just started — collapse every existing card and open only
  // the new one (works on both desktop and the mobile collapse-by-default path).
  if (_soloExpandTaskId) {
    const _allIds = new Set([..._collapsedTaskIds, ..._expandedTaskIds]);
    _collapsedTaskIds.clear();
    _expandedTaskIds.clear();
    _allIds.forEach(id => { if (id !== _soloExpandTaskId) _collapsedTaskIds.add(id); });
    _expandedTaskIds.add(_soloExpandTaskId);
    _soloExpandTaskId = null;
  }
  // On mobile, task outputs start COLLAPSED — having every running window
  // expanded on entry meant a lot of tapping to collapse them. User-expanded
  // ones are re-opened from _expandedTaskIds below.
  const _mobileCollapseDefault = window.innerWidth <= 768;
  const _collapsedSectionIds = new Set();
  body.querySelectorAll('.cookbook-section-body').forEach(sb => {
    if (sb.style.display === 'none' && sb.id) _collapsedSectionIds.add(sb.id);
  });

  const tasks = _loadTasks();
  const hasContent = tasks.length > 0;
  // Count anything that's really active: explicit 'running'/'queued' status,
  // OR a download whose tmux output is still showing live shard progress.
  // Without the output check, a task whose status got stuck at 'done' /
  // 'crashed' (before auto-reconnect catches it) would read as "Running 0"
  // even when the model is actively downloading on the host.
  const activeCount = tasks.filter(t =>
    t.status === 'running'
    || t.status === 'queued'
    || _downloadOutputLooksActive(t)
  ).length;
  const activeCountHtml = activeCount ? ` <span class="cookbook-tab-count">${activeCount}</span>` : '';

  let tabBar = body.querySelector('.cookbook-tabs');
  if (!tabBar) return;
  let runTab = tabBar.querySelector('.cookbook-tab[data-backend="Running"]');
  if (hasContent && !runTab) {
    runTab = document.createElement('button');
    runTab.className = 'cookbook-tab';
    runTab.dataset.backend = 'Running';
    const _errCount = tasks.filter(t => t.status === 'error' || t.status === 'crashed').length;
    runTab.innerHTML = `Running${activeCountHtml}${_errCount ? `<span class="cookbook-tab-error-dot"></span>` : ''}`;
    tabBar.insertBefore(runTab, tabBar.firstChild);
    runTab.addEventListener('click', () => {
      tabBar.querySelectorAll('.cookbook-tab').forEach(t => t.classList.remove('active'));
      runTab.classList.add('active');
      body.querySelectorAll('.cookbook-group').forEach(g => {
        g.classList.toggle('hidden', g.dataset.backendGroup !== 'Running');
      });
    });
  } else if (runTab) {
    const _errCount2 = tasks.filter(t => t.status === 'error' || t.status === 'crashed').length;
    runTab.innerHTML = tasks.length ? `Running${activeCountHtml}${_errCount2 ? '<span class="cookbook-tab-error-dot"></span>' : ''}` : 'Running';
    if (!hasContent) {
      if (runTab.classList.contains('active')) {
        const wfTab = tabBar.querySelector('.cookbook-tab[data-backend="Search"]');
        if (wfTab) wfTab.click();
      }
      runTab.remove();
    }
  }

  let group = body.querySelector('.cookbook-group[data-backend-group="Running"]');
  if (hasContent && !group) {
    group = document.createElement('div');
    group.className = 'cookbook-group hidden';
    group.dataset.backendGroup = 'Running';
    group.innerHTML = '<div class="admin-card" style="flex:1;display:flex;flex-direction:column;overflow:hidden;">' +
      '<div style="display:flex;align-items:baseline;gap:8px;margin-bottom:2px;">' +
      '<h2 style="margin:0;padding:0;line-height:1;">Running <span id="running-count" class="memory-count" style="font-size:0.6em;opacity:0.6;font-weight:normal">' + activeCount + '</span></h2>' +
      '</div>' +
      '<p class="memory-desc doclib-desc" style="margin-top:6px;">Active downloads and serving processes.</p>' +
      '</div>';
    const firstGroup = body.querySelector('.cookbook-group');
    if (firstGroup) body.insertBefore(group, firstGroup);
    else body.appendChild(group);
  }

  if (!group) return;

  const countEl = group.querySelector('#running-count');
  if (countEl) countEl.textContent = activeCount;

  if (!hasContent) {
    group.remove();
    return;
  }

  const _adminCard = group.querySelector('.admin-card');
  function _ensureSection(cls, label, items) {
    let sec = group.querySelector('.' + cls);
    if (!sec) {
      sec = document.createElement('div');
      sec.className = cls;
      (_adminCard || group).appendChild(sec);
    }
    if (!items || !items.length) {
      sec.style.display = 'none';
      return sec;
    }
    sec.style.display = '';
    return sec;
  }

  // Group tasks by server
  const _serverName = (host) => {
    if (!host) return 'Local';
    const srv = _envState.servers.find(s => s.host === host);
    return srv?.name || host;
  };
  const serverGroups = {};
  for (const t of tasks) {
    const key = t.remoteHost || '';
    if (!serverGroups[key]) serverGroups[key] = { name: _serverName(key), serve: [], download: [] };
    serverGroups[key][t.type === 'serve' ? 'serve' : 'download'].push(t);
  }


  // ── Server-grouped sections ──
  group.querySelectorAll('.cookbook-serve-section, .cookbook-dl-section').forEach(el => el.remove());

  const serverKeys = Object.keys(serverGroups).sort((a, b) => {
    if (!a) return -1; if (!b) return 1;
    return serverGroups[a].name.localeCompare(serverGroups[b].name);
  });

  // Prune stale server sections: a server that no longer has ANY tasks isn't in
  // serverKeys, so its section header/dropdown would otherwise linger until the
  // user manually cleared it. Drop those automatically on each render.
  const _liveSafeKeys = new Set(serverKeys.map(k => (k || 'local').replace(/[^a-zA-Z0-9-]/g, '_')));
  (_adminCard || group).querySelectorAll('[class*="cookbook-server-section-"]').forEach(el => {
    const cls = [...el.classList].find(c => c.startsWith('cookbook-server-section-'));
    if (cls && !_liveSafeKeys.has(cls.replace('cookbook-server-section-', ''))) el.remove();
  });

  for (const key of serverKeys) {
    const sg = serverGroups[key];
    const allTasks = [...sg.serve, ...sg.download];
    const safeKey = (key || 'local').replace(/[^a-zA-Z0-9-]/g, '_');
    const sectionCls = `cookbook-server-section-${safeKey}`;
    const bodyId = `server-body-${safeKey}`;
    let sec = _ensureSection(sectionCls, sg.name, allTasks);
    if (allTasks.length && !sec.querySelector('.cookbook-section-header')) {
      const clearId = `clear-server-${key || 'local'}`;
      // Glowy status dot next to the server name (like the Settings server card):
      // green when reachable, red if any serve task on it is crashed/unreachable.
      const _secDot = (key && allTasks.some(_serveTaskFailed)) ? 'fail' : 'ok';
      const _dotTitle = key ? (_secDot === 'fail' ? 'Server not responding' : 'Reachable') : 'Local (this machine)';
      sec.insertAdjacentHTML('afterbegin', `<div class="cookbook-section-header" data-collapse="${bodyId}"><svg class="cookbook-section-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg><span class="cookbook-srv-status ${_secDot}" title="${_dotTitle}" style="flex-shrink:0;position:relative;top:0px;"></span><span class="cookbook-section-title" style="margin:0;">${esc(sg.name)}</span><button class="cookbook-btn cookbook-stop-all-btn" data-stop-server="${esc(key)}">Stop all</button><button class="cookbook-btn cookbook-clear-btn" data-clear-server="${esc(key)}">Clear finished</button></div><div id="${bodyId}" class="cookbook-section-body"></div>`);
    }
  }

  // Wire clear all buttons
  group.querySelectorAll('[data-clear-server]').forEach(btn => {
    if (btn._bound) return;
    btn._bound = true;
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();  // don't toggle the section collapse (was an inline onclick, blocked by CSP)
      const host = btn.dataset.clearServer;
      if (!await window.styledConfirm(`Clear finished tasks on ${_serverName(host)}?`, { confirmText: 'Clear' })) return;
      const allTasks = _loadTasks();
      const toRemove = allTasks.filter(t => (t.remoteHost || '') === host && _canClearTask(t));
      const remaining = allTasks.filter(t => (t.remoteHost || '') !== host || !_canClearTask(t));
      _saveTasks(remaining);
      // Fade/slide each finished card out (same exit as the per-card clear)
      // instead of yanking them instantly.
      toRemove.forEach(t => {
        const el = document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`);
        if (el) {
          if (el._abort) el._abort.abort();
          if (el._uptimeInterval) clearInterval(el._uptimeInterval);
          el.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
          el.style.opacity = '0';
          el.style.transform = 'translateX(-10px)';
        }
      });
      // After the animation, remove the cards and tidy up the now-empty section.
      setTimeout(() => {
        toRemove.forEach(t => document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`)?.remove());
        // If this server's section is now empty (only finished tasks lived here),
        // remove the whole section so its header/title doesn't linger.
        const _sk = (host || 'local').replace(/[^a-zA-Z0-9-]/g, '_');
        const _sec = group.querySelector(`.cookbook-server-section-${_sk}`);
        if (_sec && !_sec.querySelector('.cookbook-task')) _sec.remove();
        if (!remaining.length) _renderRunningTab();
      }, 360);
    });
  });

  // Wire "Stop all" buttons — stop every running task on that server.
  group.querySelectorAll('[data-stop-server]').forEach(btn => {
    if (btn._bound) return;
    btn._bound = true;
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();  // don't toggle the section collapse
      const host = btn.dataset.stopServer;
      const running = _loadTasks().filter(t => (t.remoteHost || '') === host && t.status === 'running');
      if (!running.length) { uiModule.showToast(`Nothing running on ${_serverName(host)}`); return; }
      if (!await window.styledConfirm(`Stop ${running.length} running task${running.length > 1 ? 's' : ''} on ${_serverName(host)}?`, { confirmText: 'Stop all' })) return;
      // Mark every task as user-stopped BEFORE firing the kills so that the
      // download auto-retry logic never restarts a task the user just stopped.
      running.forEach(t => _updateTask(t.sessionId, { _userStopped: true }));
      // Reuse each task's own Stop action so it does the full teardown
      // (send C-c, drop the endpoint, mark stopped) consistently.
      running.forEach(t => {
        const el = document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`);
        el?.querySelector('.cookbook-task-action-stop')?.click();
      });
      uiModule.showToast(`Stopped ${running.length} task${running.length > 1 ? 's' : ''} on ${_serverName(host)}`);
    });
  });

  // Section collapse/expand
  group.querySelectorAll('.cookbook-section-header[data-collapse]').forEach(hdr => {
    if (hdr._bound) return;
    hdr._bound = true;
    hdr.addEventListener('click', () => {
      const bodyId = hdr.dataset.collapse;
      const body = document.getElementById(bodyId);
      if (!body) return;
      const isHidden = body.style.display === 'none';
      body.style.display = isHidden ? '' : 'none';
      const chevron = hdr.querySelector('.cookbook-section-chevron');
      if (chevron) {
        // Collapsed → point right (▶, click to expand); expanded → down (▼).
        chevron.style.transform = isHidden ? '' : 'rotate(-90deg)';
        chevron.style.opacity = '';
      }
    });
  });

  // Only add new tasks or update existing ones
  const existingIds = new Set();
  group.querySelectorAll('.cookbook-task').forEach(el => {
    const id = el.dataset.taskId;
    existingIds.add(id);
    const task = tasks.find(t => t.sessionId === id);
    if (task) {
      el.dataset.status = task.status;
      const isDone = task.status === 'done';
      // Type chip doubles as the "finished" badge once a task completes — both
      // download and serve show the same green FINISHED chip.
      const typeChip = el.querySelector('.cookbook-task-type');
      if (typeChip) {
        // Only DOWNLOAD tasks flip to "finished" when done — serve tasks keep
        // saying "serve" because the model is still running on that port.
        const isDoneDl = isDone && task.type === 'download';
        typeChip.textContent = isDoneDl ? 'finished' : task.type;
        typeChip.classList.toggle('cookbook-task-type-done', isDoneDl);
      }
      const badge = el.querySelector('.cookbook-task-status');
      if (badge) {
        const _bdg = _taskBadge(task);
        badge.textContent = _bdg.text;
        badge.className = 'cookbook-task-status' + (_bdg.cls ? ' ' + _bdg.cls : '');
        badge.style.display = '';
      }
      // Indicator: spinning wave while running, green check when finished.
      const wave = el.querySelector('.cookbook-task-wave');
      if (wave) wave.style.display = task.status === 'running' ? '' : 'none';
      const check = el.querySelector('.cookbook-task-check');
      if (check) {
        check.style.display = _canClearTask(task) ? '' : 'none';
        const label = check.querySelector('.cookbook-task-done-label');
        if (label) label.textContent = _clearPillLabel(task);
      }
      const startNow = el.querySelector('.cookbook-task-start-now');
      if (startNow) startNow.style.display = (task.type === 'download' && task.status === 'queued') ? '' : 'none';
      const terminalDiag = _terminalServeDiagnosis(task, el.querySelector('.cookbook-output-pre')?.textContent || task.output || '');
      if (terminalDiag) _showDiagnosis(el, terminalDiag, el.querySelector('.cookbook-output-pre')?.textContent || task.output || '');
    }
    if (!task) {
      if (el._uptimeInterval) { clearInterval(el._uptimeInterval); el._uptimeInterval = null; }
      el.remove();
    }
  });

  // Add new task entries
  for (const task of tasks) {
    if (existingIds.has(task.sessionId)) continue;

    const el = document.createElement('div');
    el.className = 'cookbook-task' + (task._unreachable && task.status === 'running' ? ' cookbook-task-unreachable' : '');
    el.dataset.taskId = task.sessionId;
    el.dataset.status = task.status;
    el.dataset.type = task.type || '';

    const _bdg = _taskBadge(task);
    const _bdgTitle = (task._unreachable && task.status === 'running') ? ' title="Server not responding — it may have crashed"' : '';
    el.innerHTML = `
      <div class="cookbook-task-header">
        <span class="cookbook-task-type${(task.status === 'done' && task.type === 'download') ? ' cookbook-task-type-done' : ''}" data-type="${esc(task.type)}">${esc((task.status === 'done' && task.type === 'download') ? 'finished' : task.type)}</span>
        <span class="cookbook-task-name">${modelLogo(task.name)}${esc(task.name)}</span>
        <span class="cookbook-task-indicator"><span class="cookbook-task-wave" style="display:${task.status === 'running' ? '' : 'none'}"></span><span class="cookbook-task-check" title="Clear" style="display:${_canClearTask(task) ? '' : 'none'}"><svg class="cookbook-task-check-ico" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#50fa7b" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><svg class="cookbook-task-clear-ico" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg><span class="cookbook-task-done-label">${esc(_clearPillLabel(task))}</span><span class="cookbook-task-clear-label">clear</span></span></span>
        <button type="button" class="cookbook-task-start-now" title="Start this queued download now" style="display:${(task.type === 'download' && task.status === 'queued') ? '' : 'none'}"><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="8 5 19 12 8 19 8 5"/></svg><span>start now</span></button>
        <span class="cookbook-task-status ${_bdg.cls}"${_bdgTitle}>${esc(_bdg.text)}</span>
        <button class="cookbook-task-menu-btn" title="Actions">&#8942;</button>
      </div>
      <div class="cookbook-task-sub"><span class="cookbook-task-session">${esc(task.sessionId)}</span><span class="cookbook-task-uptime" style="display:${((task.type === 'serve' || task.type === 'download') && task.status === 'running') ? '' : 'none'}"></span>${(task.type === 'download') ? `<span class="cookbook-task-dldir" title="Download destination" style="font-size:9px;color:var(--fg-muted);font-family:'Fira Code',monospace;opacity:0.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:40ch;">Dir: ${esc(task.payload?.local_dir || '~/.cache/huggingface/hub')}</span>` : ''}</div>
      <div class="cookbook-output-wrap cookbook-task-collapsible${_mobileCollapseDefault ? ' cookbook-task-collapsed' : ''}"><pre class="cookbook-output-pre">${esc(task.output || '')}</pre><button type="button" class="copy-code cookbook-output-copy"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></div>
    `;

    const _waveEl = el.querySelector('.cookbook-task-wave');
    if (_waveEl && task.status === 'running') _registerWaveEl(_waveEl);

    const terminalDiag = _terminalServeDiagnosis(task, task.output || '');
    if (terminalDiag) _showDiagnosis(el, terminalDiag, task.output || '');
    if (!terminalDiag && (task.status === 'error' || task.status === 'crashed') && task._backendDiagnosis) {
      _showDiagnosis(el, task._backendDiagnosis, task.output || '');
    }

    const _uptimeEl = el.querySelector('.cookbook-task-uptime');
    if (_uptimeEl && (task.type === 'serve' || task.type === 'download') && task.status === 'running') {
      const _startedAt = task.ts || Date.now();
      const _prefix = task.type === 'download' ? 'downloading' : 'uptime';
      el._uptimeInterval = setInterval(() => {
        const secs = Math.floor((Date.now() - _startedAt) / 1000);
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = secs % 60;
        const _timer = h > 0
          ? `${_prefix}: ${h}h ${String(m).padStart(2,'0')}m`
          : `${_prefix}: ${m}m ${String(s).padStart(2,'0')}s`;
        // ETA — only for downloads, only when we have a meaningful overall %.
        // Reads the badge text (which already shows the true overall % we
        // compute in the live-polling block) and back-derives a remaining-time
        // estimate from elapsed/done. Hidden until pct >= 3% so the early-job
        // wild estimates don't show.
        let _eta = '';
        if (task.type === 'download') {
          const _badge = el.querySelector('.cookbook-task-status');
          const _m = _badge && /^(\d+)%/.exec(_badge.textContent || '');
          const _pct = _m ? parseInt(_m[1], 10) : 0;
          if (_pct >= 3 && _pct < 100 && secs > 5) {
            const _totalSec = Math.round(secs * (100 / _pct));
            const _remain = Math.max(0, _totalSec - secs);
            const _eh = Math.floor(_remain / 3600);
            const _em = Math.floor((_remain % 3600) / 60);
            const _es = _remain % 60;
            _eta = _eh > 0
              ? ` · ETA ${_eh}h ${String(_em).padStart(2,'0')}m`
              : (_em > 0 ? ` · ETA ${_em}m ${String(_es).padStart(2,'0')}s` : ` · ETA ${_es}s`);
          }
        }
        _uptimeEl.textContent = _timer + _eta;
      }, 1000);
    }

    // Re-open the Serve panel for this model, pre-filled with the EXACT
    // settings this instance launched with, and on the SERVER it runs on.
    const _openEdit = () => _openServeEditForTask(task);
    el.addEventListener('cookbook:edit-serve', (e) => {
      e.stopPropagation();
      _openServeEditForTask(task, null, e.detail?.fields || null);
    });

    // Finished download → an explicit "Serve →" button jumps straight to the
    // Serve tab with this model pre-selected (on the server it downloaded to).
    if (task.type === 'download') {
      const _serveBtn = el.querySelector('.cookbook-task-serve-btn');
      if (_serveBtn) {
        _serveBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const repo = task.payload?.repo_id || task.name;
          if (!repo) { uiModule.showToast('No model info on this task'); return; }
          // Point the active server at the one it downloaded to.
          const _tHost = task.remoteHost || '';
          _envState.remoteHost = _tHost;
          const _tSrv = _envState.servers.find(s => s.host === _tHost);
          if (_tSrv) { _envState.env = _tSrv.env || 'none'; _envState.envPath = _tSrv.envPath || ''; _envState.platform = _tSrv.platform || ''; }
          else if (!_tHost) { _envState.env = 'none'; _envState.envPath = ''; _envState.platform = ''; }
          document.querySelectorAll('#hwfit-server-select, #hwfit-dl-server, #hwfit-cache-server, #hwfit-deps-server').forEach(sel => {
            if (sel && sel.tagName === 'SELECT') sel.value = _tHost || 'local';
          });
          try {
            const { openServePanelForRepo } = await import('./cookbookServe.js');
            await openServePanelForRepo(repo);
            // Serving it supersedes the finished download — clear the card from
            // the Running tab (smooth exit) now that we've jumped to Serve.
            _animateOutThenRemove(el, task.sessionId);
          } catch (err) { uiModule.showToast('Could not open Serve: ' + err.message); }
        });
      }
    }

    // Finished tasks show a green check — make it click-to-clear so the user can
    // dismiss a completed download/update (we no longer auto-remove them). It
    // morphs to a red ✕ on hover (see CSS).
    const _clearChk = el.querySelector('.cookbook-task-check');
    if (_clearChk) {
      _clearChk.addEventListener('click', (e) => {
        e.stopPropagation();
        // If the output still shows an active shard line, the task isn't
        // actually finished — clicking is "reconnect" (flip back to running
        // + let _reconnectTask reattach to the live tmux session), not
        // "clear". The pill label already reflects this via _clearPillLabel.
        if (_downloadOutputLooksActive(task)) {
          const _fresh = _loadTasks();
          const _ft = _fresh.find(t => t.sessionId === task.sessionId);
          if (_ft) {
            _ft.status = 'running';
            _ft._selfHealed = true;
            _saveTasks(_fresh);
          }
          // Visually flip without waiting for a full re-render — same path the
          // self-heal uses on cookbook open.
          const _chk = el.querySelector('.cookbook-task-check');
          if (_chk) _chk.style.display = 'none';
          const _wave = el.querySelector('.cookbook-task-wave');
          if (_wave) _wave.style.display = '';
          const _up = el.querySelector('.cookbook-task-uptime');
          if (_up) _up.style.display = '';
          el.dataset.status = 'running';
          _renderRunningTab();
          return;
        }
        // Otherwise: real clear. Kill the tmux session as belt-and-suspenders,
        // then animate out + remove the row.
        try {
          fetch('/api/shell/exec', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${task.sessionId}`) }),
          }).catch(() => {});
        } catch {}
        _animateOutThenRemove(el, task.sessionId);
      });
    }

    const _startNowBtn = el.querySelector('.cookbook-task-start-now');
    if (_startNowBtn) {
      _startNowBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _startQueuedDownload(task);
      });
    }

    // Wire header click to collapse/expand output
    el.querySelector('.cookbook-task-header').addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const wrap = el.querySelector('.cookbook-output-wrap');
      if (wrap) wrap.classList.toggle('cookbook-task-collapsed');
    });

    // Wire menu button (also fire from a long-press anywhere on the card so
    // mobile users don't have to hit the small ⋮ target precisely).
    const menuBtn = el.querySelector('.cookbook-task-menu-btn');
    if (menuBtn) {
      // Long-press detection on the card: ~500ms hold without scroll movement
      // re-uses the menu button's click path (so we don't duplicate logic).
      let _lpTimer = null;
      let _lpStartY = 0;
      let _lpCanceled = false;
      const _lpStart = (e) => {
        _lpCanceled = false;
        _lpStartY = (e.touches?.[0]?.clientY) ?? 0;
        _lpTimer = setTimeout(() => {
          if (_lpCanceled) return;
          _lpCanceled = true;  // suppress the subsequent click-through
          try { menuBtn.click(); } catch {}
        }, 500);
      };
      const _lpCancel = () => {
        if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
      };
      const _lpMove = (e) => {
        const y = (e.touches?.[0]?.clientY) ?? 0;
        if (Math.abs(y - _lpStartY) > 8) _lpCancel();
      };
      el.addEventListener('touchstart', (e) => {
        // Skip if the user is starting touch on a button / link inside the
        // card — those already have their own tap handlers.
        if (e.target.closest('button, a, input, textarea, .cookbook-task-dropdown')) return;
        _lpStart(e);
      }, { passive: true });
      el.addEventListener('touchmove', _lpMove, { passive: true });
      el.addEventListener('touchend', _lpCancel, { passive: true });
      el.addEventListener('touchcancel', _lpCancel, { passive: true });
      menuBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        document.querySelectorAll('.cookbook-task-dropdown').forEach(d => { if (typeof d._dismiss === 'function') d._dismiss(); else d.remove(); });

        const dropdown = document.createElement('div');
        dropdown.className = 'cookbook-task-dropdown';

        const items = [];
        // Queued download: let the user jump the queue and start it immediately
        // (downloads otherwise run one-at-a-time per server).
        if (task.type === 'download' && task.status === 'queued') {
          items.push({ label: 'Start now', action: 'start-now', custom: () => {
            _startQueuedDownload(task);
            _renderRunningTab();
          }});
        }
        if (task.status !== 'running' && task.status !== 'queued') {
          items.push({ label: 'Reconnect', action: 'reconnect' });
        }
        if (task.status === 'running') {
          items.push({ label: 'Stop', action: 'stop', danger: true });
        }
        items.push({ label: 'Restart', action: 'retry' });
        // Edit serve — open the full serve panel (same as the edit icon),
        // switching to this task's server first so the model is found.
        if (task.type === 'serve' && task.payload?.repo_id) {
          items.push({ label: 'Edit in serve panel', action: 'edit-panel', tooltip: 'Open the full Serve config panel pre-filled with this task — pick a different backend, change GPUs, edit env vars, then Launch from there', custom: () => _openEdit() });
        }
        // Save serve — save current launch config as a preset.
        if (task.type === 'serve' && task.payload?._cmd) {
          items.push({ label: 'Save serve', action: 'save', custom: () => {
            if (!_saveTaskAsPreset(task)) { uiModule.showToast('Already saved'); return; }
            uiModule.showToast('Saved to presets');
            _renderRunningTab();
          }});
        }
        // Edit command — only meaningful for serve tasks that aren't running.
        // Lets the user tweak flags after a crash/error and relaunch.
        if (task.type === 'serve' && task.status !== 'running' && task.payload?._cmd) {
          items.push({ label: 'Edit cmd & relaunch', action: 'edit', tooltip: 'Edit the raw vllm/llama-server cmd string in a dialog and relaunch immediately on the same host', custom: async () => {
            const newCmd = await _promptEditServeCmd(task.payload._cmd);
            if (newCmd == null) return; // cancelled
            try {
              await fetch('/api/shell/exec', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: _tmuxGracefulKill(task) }),
              });
            } catch {}
            _removeTask(task.sessionId);
            // Relaunch on the task's OWN host, not the current global selection.
            _launchServeTask(task.name, task.payload.repo_id, newCmd, task.payload._fields, task.remoteHost || '');
          }});
        }
        // Manual endpoint registration — fallback for when auto-add fails
        // (e.g. probe timeout on a remote that's slow). Forces adding this
        // serve to the model-endpoints list regardless of prior flag state.
        if (task.type === 'serve' && task.payload?._cmd) {
          items.push({ label: 'Register endpoint', action: 'register-endpoint', custom: async () => {
            const host = _connectHostFromRemote(task.remoteHost);
            const portMatch = task.payload?._cmd?.match(/--port\s+(\d+)/);
            const port = portMatch ? portMatch[1] : '8000';
            const baseUrl = `http://${host}:${port}/v1`;
            try {
              // Check existing first — offer to overwrite if present
              const eps = await (await fetch('/api/model-endpoints', { credentials: 'same-origin' })).json();
              const existing = eps.find(e => e.base_url === baseUrl);
              if (existing) {
                uiModule.showToast(`Already registered as "${existing.name}"`);
                task._endpointAdded = true;
                _updateTask(task.sessionId, { _endpointAdded: true });
                _refreshModelsAfterEndpointChange();
                // If it's still offline (registered before the server finished
                // loading), keep probing until it answers instead of leaving it
                // stuck offline until a manual delete/re-add.
                if (existing.id && !(existing.models || []).length) _probeEndpointUntilOnline(existing.id, host, port);
                return;
              }
              const fd = new FormData();
              fd.append('base_url', baseUrl);
              fd.append('name', task.name);
              fd.append('skip_probe', 'true');
              _appendCookbookEndpointScope(fd, task.remoteHost || '');
              if (task.payload?._cmd?.includes('diffusion_server')) fd.append('model_type', 'image');
              const res = await fetch('/api/model-endpoints', { method: 'POST', credentials: 'same-origin', body: fd });
              if (res.ok) {
                task._endpointAdded = true;
                _updateTask(task.sessionId, { _endpointAdded: true });
                uiModule.showToast(`Endpoint registered: ${host}:${port}`);
                _refreshModelsAfterEndpointChange();
                // Added with skip_probe → probe until the (possibly still
                // warming) server answers, so it flips online on its own.
                const _ep = await res.json().catch(() => ({}));
                if (_ep && _ep.id) _probeEndpointUntilOnline(_ep.id, host, port);
              } else {
                const body = await res.text().catch(() => '');
                uiModule.showError(`Register failed: ${res.status} ${body.slice(0, 140)}`);
              }
            } catch (e) {
              uiModule.showError(`Register failed: ${e.message || e}`);
            }
          }});
        }
        if (_isWindows(task)) {
          const host = task.remoteHost;
          const sd = host ? '$env:TEMP\\odysseus-sessions' : '$env:TEMP\\odysseus-tmux';
          const logCmd = host
            ? `ssh ${_sshPrefix(_getPort(task))}${host} "powershell -Command \\"Get-Content '${sd}\\${task.sessionId}.log' -Wait\\""`
            : `powershell -Command "Get-Content (Join-Path $env:TEMP 'odysseus-tmux\\${task.sessionId}.log') -Wait"`;
          items.push({ label: 'Copy log cmd', action: 'copy-tmux', custom: () => {
            _copyText(logCmd);
          }});
        } else {
          // Just the tmux command itself — no ssh wrapper.
          const tmuxAttach = `tmux attach -t ${task.sessionId}`;
          items.push({ label: 'Copy tmux', action: 'copy-tmux', custom: () => {
            _copyText(tmuxAttach);
          }});
        }
        if (_shouldOfferCrashReport(task)) {
          items.push({ label: 'Copy crash report', action: 'copy-crash-report', custom: () => {
            const out = (el.querySelector('.cookbook-output-pre')?.textContent || task.output || '');
            _copyText(_buildCrashReport(task, out));
            uiModule.showToast('Copied crash report');
          }});
        }
        // Copy the last 50 lines of the task's output/log.
        items.push({ label: 'Copy last 50 lines', action: 'copy-log', custom: () => {
          const out = (el.querySelector('.cookbook-output-pre')?.textContent || task.output || '');
          const last = out.split('\n').slice(-50).join('\n');
          _copyText(last);
          uiModule.showToast('Copied last 50 lines');
        }});
        // Label matches behavior — the kill handler ALWAYS first kills
        // the live tmux session and (for serve tasks) deletes the
        // matching model-endpoint, THEN animates the task card out.
        // Just "Remove" hid that it stops the live serve too.
        const _isLive = task.type === 'serve' && ['running', 'ready', 'loading', 'warming', 'starting'].includes(task.status || '');
        items.push({
          label: _isLive ? 'Stop and remove' : 'Remove',
          action: 'kill',
          tooltip: _isLive
            ? 'Kill the live tmux session, deregister the chat endpoint, and remove this row'
            : 'Remove this row',
          danger: true,
        });
        // Cancel = mobile-only dismiss item. Same pattern as the email kebab:
        // the `dropdown-cancel-mobile` class is hidden on desktop and styled
        // as a separated bottom row on mobile (border-top + extra padding).
        items.push({ label: 'Cancel', action: 'cancel', mobileOnly: true, custom: () => {} });

        const _MENU_ICONS = {
          'start-now': '<polygon points="6 4 20 12 6 20 6 4"/>',
          reconnect: '<path d="M1 4v6h6"/><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/>',
          retry: '<path d="M1 4v6h6"/><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/>',
          stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
          edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>',
          'edit-panel': '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>',
          'register-endpoint': '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
          save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
          'copy-tmux': '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
          'copy-crash-report': '<path d="M10.3 2.3 1.8 17a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 2.3a2 2 0 0 0-3.4 0z"/><path d="M12 8v5M12 17h.01"/>',
          'copy-log': '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
          kill: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
          cancel: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
        };
        for (const item of items) {
          const div = document.createElement('div');
          div.className = 'dropdown-item-compact'
            + (item.danger ? ' cookbook-dropdown-danger' : '')
            + (item.mobileOnly ? ' dropdown-cancel-mobile' : '');
          div.style.cssText = 'display:flex;align-items:center;gap:8px;';
          if (item.tooltip) div.title = item.tooltip;
          const ic = _MENU_ICONS[item.action] || '';
          div.innerHTML = `<span style="display:inline-flex;flex-shrink:0;opacity:0.7;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ic}</svg></span><span>${item.label}</span>`;
          div.addEventListener('click', () => {
            _cleanup();
            if (item.custom) { item.custom(); return; }
            el.querySelector('.cookbook-task-action-' + item.action)?.click();
          });
          dropdown.appendChild(div);
        }

        const rect = menuBtn.getBoundingClientRect();
        dropdown.style.position = 'fixed';
        dropdown.style.top = rect.bottom + 2 + 'px';
        dropdown.style.right = (window.innerWidth - rect.right) + 'px';
        document.body.appendChild(dropdown);
        // Clamp into the *visible* area. On mobile (esp. Firefox) window.innerHeight
        // includes the strip hidden under the dynamic toolbar, so a menu that "fits"
        // by innerHeight still lands off-screen at the bottom. visualViewport gives
        // the real visible region. Flip above the button if there's no room below,
        // else clamp to the bottom edge.
        {
          const vv = window.visualViewport;
          const viewTop = vv ? vv.offsetTop : 0;
          const viewBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
          const dh = dropdown.offsetHeight;
          const m = 8;
          let top = rect.bottom + 2;
          if (top + dh > viewBottom - m) {
            const above = rect.top - 2 - dh;
            top = above >= viewTop + m ? above : Math.max(viewTop + m, viewBottom - dh - m);
          }
          dropdown.style.top = top + 'px';
        }

        const closeHandler = (ev) => {
          if (!dropdown.contains(ev.target) && ev.target !== menuBtn) {
            _cleanup();
          }
        };
        // Close on scroll too — once the page scrolls, the dropdown's
        // fixed position no longer matches the originating ⋮ button, so
        // it visually drifts. Matches the email kebab behaviour.
        const scrollClose = () => _cleanup();
        let _unreg = () => {};
        const _cleanup = () => {
          _unreg(); _unreg = () => {};
          dropdown.remove();
          document.removeEventListener('click', closeHandler);
          window.removeEventListener('scroll', scrollClose, true);
          window.visualViewport?.removeEventListener('scroll', scrollClose);
        };
        dropdown._dismiss = _cleanup;
        setTimeout(() => {
          document.addEventListener('click', closeHandler);
          window.addEventListener('scroll', scrollClose, true);
          window.visualViewport?.addEventListener('scroll', scrollClose);
        }, 0);
        _unreg = registerMenuDismiss(_cleanup);
      });
    }

    // Hidden action buttons for menu dispatch
    const _actionBtns = document.createElement('div');
    _actionBtns.style.display = 'none';
    _actionBtns.innerHTML = `
      <button class="cookbook-task-action-reconnect"></button>
      <button class="cookbook-task-action-retry"></button>
      <button class="cookbook-task-action-stop"></button>
      <button class="cookbook-task-action-kill"></button>
    `;
    el.appendChild(_actionBtns);

    // Wire reconnect
    el.querySelector('.cookbook-task-action-reconnect').addEventListener('click', () => {
      _updateTask(task.sessionId, { status: 'running' });
      el.dataset.status = 'running';
      const badge = el.querySelector('.cookbook-task-status');
      if (badge) { badge.textContent = _statusLabel('running', task.type); badge.className = 'cookbook-task-status cookbook-task-running'; }
      _reconnectTask(el, task);
    });

    // Wire stop
    el.querySelector('.cookbook-task-action-stop').addEventListener('click', async () => {
      // Abort the reconnect loop before sending kill so that a DOWNLOAD_FAILED
      // marker written by the shell wrapper (on SIGINT/non-zero exit) cannot
      // trigger an auto-retry after a manual stop.
      if (el._abort) el._abort.abort();
      const badge = el.querySelector('.cookbook-task-status');
      if (badge) { badge.textContent = 'stopping...'; badge.className = 'cookbook-task-status cookbook-task-stopping'; }
      el.dataset.status = 'stopped';
      _updateTask(task.sessionId, { _userStopped: true });
      const outputText = el.querySelector('.cookbook-output-pre')?.textContent || task.output || '';
      // Drop the model endpoint so the picker stops listing it.
      if (task.type === 'serve' && task.payload) {
        _removeEndpointByUrl(_endpointUrlForTask(task, outputText));
      }
      const ollamaUnload = _ollamaUnloadCommand(task, outputText);
      if (ollamaUnload) {
        try {
          await fetch('/api/shell/exec', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: ollamaUnload }),
          });
        } catch {}
      }
      // Gracefully stop (C-c, then kill the session) so it's fully down...
      try {
        await fetch('/api/shell/exec', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: _tmuxGracefulKill(task) }),
        });
      } catch {}
      // ...then smoothly fade/slide the card out and auto-remove it — no manual
      // ⋮ → Remove needed.
      _animateOutThenRemove(el, task.sessionId);
    });

    // Wire kill — awaits the SSH/tmux kill and verifies the session is
    // actually gone before removing the row. Previously fire-and-forget,
    // which meant a failed kill (wrong remoteHost, SSH error, tmux server
    // already exited) silently left the live serve running while the
    // row disappeared from the UI.
    el.querySelector('.cookbook-task-action-kill').addEventListener('click', async () => {
      const outputText = el.querySelector('.cookbook-output-pre')?.textContent || task.output || '';
      const isLive = task.type === 'serve' && ['running', 'ready', 'loading', 'warming', 'starting'].includes(task.status || '');
      const ollamaUnload = _ollamaUnloadCommand(task, outputText);
      if (ollamaUnload) {
        try {
          await fetch('/api/shell/exec', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: ollamaUnload }),
          });
        } catch (_) { /* unload best-effort */ }
      }
      let killOk = true;
      try {
        const r = await fetch('/api/shell/exec', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: _tmuxGracefulKill(task) }),
        });
        if (r.ok) {
          const out = await r.json();
          // Don't trust exit_code alone — tmux kill returns 0 even when
          // there was nothing to kill. Verify the session is actually gone.
          if (task.sessionId && isLive) {
            try {
              const probe = await fetch('/api/shell/exec', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: _tmuxCmd(task, `has-session -t ${task.sessionId}`) }),
              });
              if (probe.ok) {
                const pj = await probe.json();
                // has-session exits 0 when session STILL exists; non-zero = gone.
                if ((pj.exit_code || 0) === 0) killOk = false;
              }
            } catch (_) { /* probe best-effort; trust kill */ }
          }
        } else {
          killOk = false;
        }
      } catch (_) { killOk = false; }
      if (!killOk) {
        try { uiModule.showToast('Kill failed — session may still be running. Check `tmux ls` on the server.', 'error'); } catch (_) {}
        return;  // leave the row so the user can retry
      }
      if (task.type === 'serve' && task.payload) {
        const endpointUrl = _endpointUrlForTask(task, outputText);
        _removeEndpointByUrl(endpointUrl);
        const modelName = task.payload.model || task.name || '';
        if (modelName) {
          fetch('/api/model-endpoints', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(eps => {
              const ep = eps.find(e => e.name === modelName || e.base_url === endpointUrl);
              if (ep) fetch(`/api/model-endpoints/${ep.id}`, { method: 'DELETE', credentials: 'same-origin' }).then(() => _refreshModelsAfterEndpointChange());
            }).catch(() => {});
        }
      }
      _animateOutThenRemove(el, task.sessionId);
    });

    // Wire retry
    el.querySelector('.cookbook-task-action-retry').addEventListener('click', () => _retryTask(el, task));

    // Wire copy button
    el.querySelector('.cookbook-output-copy').addEventListener('click', (e) => {
      e.stopPropagation();
      const text = el.querySelector('.cookbook-output-pre')?.textContent || '';
      _copyText(text).then(() => {
        const btn = el.querySelector('.cookbook-output-copy');
        const origHTML = btn.innerHTML;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        btn.classList.add('copied');
        setTimeout(() => { btn.innerHTML = origHTML; btn.classList.remove('copied'); }, 1500);
      });
    });

    // Route to the right server section body
    const serverBodyId = `server-body-${(task.remoteHost || 'local').replace(/[^a-zA-Z0-9-]/g, '_')}`;
    const targetBody = document.getElementById(serverBodyId);
    if (targetBody) targetBody.appendChild(el);
    else group.appendChild(el);

    // Auto-attach the tmux output stream for any task whose underlying
    // session could still be alive — not just 'running'. Scheduler-
    // launched serves transition to 'ready' as soon as /v1/models
    // responds; without this, the user opens the Running tab and sees
    // only the placeholder ("Launched by scheduled task …") because
    // _reconnectTask never fires for status 'ready'/'loading'/'warming'.
    if (['running', 'ready', 'loading', 'warming', 'starting'].includes(task.status)) {
      _reconnectTask(el, task);
    }
  }

  if (tasks.some(t => t.status === 'running')) _startWaveSync();

  // Re-apply captured expansion state so re-renders don't fold open tasks/sections.
  _collapsedTaskIds.forEach((id) => {
    const wrap = body.querySelector(`.cookbook-task[data-task-id="${id}"] .cookbook-output-wrap`);
    if (wrap) wrap.classList.add('cookbook-task-collapsed');
  });
  // Mobile defaults to collapsed (above), so re-open whatever the user had
  // explicitly expanded before this re-render.
  if (_mobileCollapseDefault) {
    _expandedTaskIds.forEach((id) => {
      const wrap = body.querySelector(`.cookbook-task[data-task-id="${id}"] .cookbook-output-wrap`);
      if (wrap) wrap.classList.remove('cookbook-task-collapsed');
    });
  }
  _collapsedSectionIds.forEach((sid) => {
    const sb = document.getElementById(sid);
    if (sb) sb.style.display = 'none';
    const hdr = body.querySelector(`.cookbook-section-header[data-collapse="${sid}"]`);
    const chevron = hdr?.querySelector('.cookbook-section-chevron');
    if (chevron) { chevron.style.transform = 'rotate(-90deg)'; chevron.style.opacity = ''; }
  });
}

// ── Reconnect task (polling loop) ──

async function _reconnectTask(el, task) {
  const output = el.querySelector('.cookbook-output-pre');
  const controller = new AbortController();
  el._abort = controller;
  let failCount = 0;

  while (!controller.signal.aborted) {
    if (!el.isConnected) {
      controller.abort();
      break;
    }
    try {
      const res = await fetch('/api/shell/exec', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: _tmuxCmd(task, `capture-pane -t ${task.sessionId} -p -S -200`), timeout: 15 }),
      });
      const data = await res.json();

      if (data.exit_code !== 0) {
        failCount++;
        if (failCount < 5) {
          await new Promise(r => setTimeout(r, 3000));
          continue;
        }
        try {
          const verify = await fetch('/api/shell/exec', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: _tmuxCmd(task, `has-session -t ${task.sessionId}`) }),
          });
          const vData = await verify.json();
          if (vData.exit_code === 0) {
            failCount = 0;
            await new Promise(r => setTimeout(r, 5000));
            continue;
          }
        } catch {
          await new Promise(r => setTimeout(r, 10000));
          continue;
        }

        const lastOutput = output.textContent || '';
        // Pip tasks (Reinstall vLLM / Upgrade torch / etc.) must skip the
        // generic serve `_diagnose` step. Their output is pip's own and the
        // error patterns there (torch ABI traceback, "No module named torch",
        // etc.) are routinely matched against the previous tmux scrollback,
        // tagging a clean pip success as a crashed serve. Detection is the
        // same shape as the looksSuccessful branch below.
        const _isPipTaskDiag = ((task.payload?.repo_id || '').startsWith('pip-'))
          || /python3? -m pip\b/.test(task.payload?._cmd || '');
        const diag = _isPipTaskDiag ? null : _diagnose(lastOutput);
        if (diag) {
          let diagEl = el.querySelector('.cookbook-diagnosis');
          if (!diagEl) {
            diagEl = document.createElement('div');
            diagEl.className = 'cookbook-diagnosis';
            el.appendChild(diagEl);
          }
          _showDiagnosis(el, diag, lastOutput);
          _updateTask(task.sessionId, { status: 'error' });
          el.dataset.status = 'error';
          const badge = el.querySelector('.cookbook-task-status');
          if (badge) { badge.textContent = _statusLabel('error', task.type); badge.className = 'cookbook-task-status cookbook-task-error'; }
          _showCookbookNotif(true);
        } else {
          const downloadLooksSuccessful = !lastOutput.includes('DOWNLOAD_FAILED')
            && (lastOutput.includes('DONE') || lastOutput.includes('100%') || lastOutput.includes('/snapshots/') || lastOutput.includes('Download complete') || lastOutput.includes('DOWNLOAD_OK'));
          // Pip install / reinstall tasks are launched via _launchServeTask (so
          // they show up in the Running tab + use tmux) but they aren't real
          // serves — the cmd is `python3 -m pip ...` and the success markers
          // are pip's own. Without this branch, a successful reinstall ends
          // with no "Uvicorn running on" line and gets mis-flagged as a crashed
          // serve.
          const _isPipTask = ((task.payload?.repo_id || '').startsWith('pip-'))
            || /python3? -m pip\b/.test(task.payload?._cmd || '');
          const pipLooksSuccessful = _isPipTask
            && /Successfully installed|Requirement already (?:satisfied|up-to-date)/i.test(lastOutput)
            && !/error:|ERROR:/.test(lastOutput.slice(-1024));
          const serveLooksReady = task.type === 'serve' && _serveOutputLooksReady({ ...task, output: lastOutput });
          // Dependency installs are tracked as download tasks but finish with a
          // pip exit-0 sentinel, not HF download markers — check that too.
          // Standalone pip-* serves finish with pip's own success line, not
          // HF or "Uvicorn running on".
          const depInstallSucceeded = !!task.payload?._dep && _depInstallSucceeded(lastOutput);
          const looksSuccessful = depInstallSucceeded
            || (task.type === 'download'
              ? downloadLooksSuccessful
              : (_isPipTask ? pipLooksSuccessful : serveLooksReady));
          if (!lastOutput.trim() || !looksSuccessful) {
            _updateTask(task.sessionId, { status: 'crashed' });
            el.dataset.status = 'crashed';
            const badge = el.querySelector('.cookbook-task-status');
            if (badge) { badge.textContent = _statusLabel('crashed', task.type); badge.className = 'cookbook-task-status cookbook-task-crashed'; }
            if (_isPipTask) {
              // Pip tasks: don't run the serve diagnosis (which would yell
              // "Serve stopped before the model became reachable"). Show a
              // pip-tailored message; the user can read pip's own error output
              // directly above.
              const _ranOk = /Successfully installed|Requirement already (?:satisfied|up-to-date)/i.test(lastOutput);
              if (!_ranOk) {
                _showDiagnosis(el, {
                  message: 'Pip install did not finish with a success marker. Check the output for the underlying error.',
                  suggestion: 'Suggested action: copy the troubleshooting bundle. Common causes: missing build deps, network blip, mismatched torch ABI.',
                  fixes: [],
                }, lastOutput);
              }
            } else if (task.type === 'serve') {
              const diag = _diagnose(lastOutput) || {
                message: _serveTaskLooksAwqOnLocalBackend(task, lastOutput)
                  ? 'AWQ/GPTQ/FP8 cannot be served through llama.cpp/Ollama unified-memory mode.'
                  : /Native llama-server not found|building llama-server|llama\.cpp/i.test(lastOutput)
                  ? 'llama.cpp build stopped before the server became reachable.'
                  : 'Serve stopped before the model became reachable.',
                suggestion: _serveTaskLooksAwqOnLocalBackend(task, lastOutput)
                  ? 'Suggested action: use vLLM/SGLang on a compatible CUDA/ROCm GPU server, or download a GGUF version for llama.cpp/Ollama/unified-memory serving.'
                  : /Native llama-server not found|building llama-server|llama\.cpp/i.test(lastOutput)
                  ? 'Suggested action: copy the troubleshooting bundle, then edit serve settings. For the quickest local/CPU path, use Ollama or a prebuilt llama-server; source builds can take several minutes and fail if build dependencies are incomplete.'
                  : 'Suggested action: copy the troubleshooting bundle, then edit serve settings or relaunch with a CPU/backend fallback.',
                fixes: [{ label: 'Edit serve', action: (panel) => _openServeEditForTask(task) }],
              };
              _showDiagnosis(el, diag, lastOutput);
            } else if (task.type === 'download') {
              const isDisk = /no space left|disk quota|enospc/i.test(lastOutput);
              const isNetwork = /connection|timeout|timed out|incompleteread|chunkedencoding|reset by peer|protocolerror|all connection attempts failed/i.test(lastOutput);
              const progressMatch = String(lastOutput || '').match(/(\d+)%\|/);
              const nearDone = progressMatch && Number(progressMatch[1]) >= 80;
              // Reconnect: most "crashed" downloads near the end are actually
              // finished — we just missed the DOWNLOAD_OK / /snapshots/ marker
              // because output rolled over, or the tmux session ended a tick
              // before we polled. Probing has-session and re-attaching to
              // capture-pane lets the existing _reconnectTask flow pick up
              // the real state (running, finished, or truly dead).
              const _reconnectFix = {
                label: 'Reconnect',
                action: () => {
                  _updateTask(task.sessionId, { status: 'running' });
                  el.dataset.status = 'running';
                  const badge2 = el.querySelector('.cookbook-task-status');
                  if (badge2) { badge2.textContent = _statusLabel('running', task.type); badge2.className = 'cookbook-task-status'; }
                  const _diagEl = el.querySelector('.cookbook-diagnosis');
                  if (_diagEl) _diagEl.remove();
                  const _wave = el.querySelector('.cookbook-task-wave'); if (_wave) _wave.style.display = '';
                  const _up = el.querySelector('.cookbook-task-uptime'); if (_up) _up.style.display = '';
                  _reconnectTask(el, task);
                },
              };
              const diag = {
                message: isDisk
                  ? 'Download stopped because this server ran out of disk space.'
                  : isNetwork
                  ? 'Download stopped after the HuggingFace connection was interrupted.'
                  : nearDone
                  ? 'Download stopped near the end before the final completion marker was captured.'
                  : 'Download stopped before HuggingFace reported completion.',
                suggestion: isDisk
                  ? 'Suggested action: free disk space, then retry the download. HuggingFace resumes incomplete files when possible.'
                  : nearDone
                  ? 'Suggested action: hit Reconnect first — the download may have finished after the output buffer rolled over. Retry only if reconnect cannot recover.'
                  : 'Suggested action: hit Reconnect to re-attach to the tmux session. If that fails, retry — HuggingFace resumes incomplete files when possible.',
                fixes: isDisk
                  ? [
                      { label: 'Retry download', action: () => _retryTask(el, task) },
                      { label: 'Copy last 50 lines', action: () => {
                        const last = String(lastOutput || '').split('\n').slice(-50).join('\n');
                        _copyText(last || 'No download log available.');
                      } },
                    ]
                  : [
                      _reconnectFix,
                      { label: 'Retry download', action: () => _retryTask(el, task) },
                      { label: 'Copy last 50 lines', action: () => {
                        const last = String(lastOutput || '').split('\n').slice(-50).join('\n');
                        _copyText(last || 'No download log available.');
                      } },
                    ],
              };
              _showDiagnosis(el, diag, lastOutput);
              // Auto-probe: if the tmux session is still alive (download
              // genuinely still in progress), _selfHealStaleTasks flips the
              // task back to running and the diagnosis disappears without
              // the user needing to click Reconnect.
              if (nearDone) setTimeout(() => { _selfHealStaleTasks().catch(() => {}); }, 1200);
            }
            _showCookbookNotif(true);
          } else {
            // Strong completion markers — `DOWNLOAD_OK` is emitted by our
            // downloader wrapper AFTER the model snapshot is on disk, and
            // `/snapshots/` only appears once HF has resolved the cached
            // tree. Either is conclusive. Finalize as done immediately, skip
            // the 30s debounce — the debounce only exists to guard against
            // ambiguous markers (bare "100%" / "Download complete") which can
            // appear mid-stream during multi-file downloads.
            const _strongDone = task.type === 'download'
              && (lastOutput.includes('DOWNLOAD_OK') || lastOutput.includes('/snapshots/'));
            if (_strongDone) {
              _updateTask(task.sessionId, { status: 'done', _doneConfirmAt: null, _lastStatusFlipAt: Date.now() });
              el.dataset.status = 'done';
              const badge = el.querySelector('.cookbook-task-status');
              if (badge) { badge.textContent = _statusLabel('done', task.type); badge.className = 'cookbook-task-status cookbook-task-done'; }
              const _chk = el.querySelector('.cookbook-task-check'); if (_chk) _chk.style.display = '';
              const _sb = el.querySelector('.cookbook-task-serve-btn'); if (_sb) _sb.style.display = '';
              _showCookbookNotif();
              _refreshDepsAfterInstall(task);
              _renderRunningTab();
              _processQueue();
              break;
            }
            // Debounce the done flip. Tmux capture-pane can fail transiently
            // (network blip, ssh reconnect), and the verify has-session right
            // above can briefly report dead even when the session is in the
            // middle of finalizing. Marking done immediately + the periodic
            // _selfHealStaleTasks then flipping back to running causes the
            // status badge to oscillate between Finished and Downloading.
            // Wait 30s and re-probe: only finalize as done if tmux is STILL
            // gone. If the session resurfaces, restart _reconnectTask so live
            // capture resumes without the user seeing a fake "done" first.
            if (!task._doneConfirmAt) {
              _updateTask(task.sessionId, { _doneConfirmAt: Date.now() + 30000 });
              setTimeout(async () => {
                try {
                  const fresh = _loadTasks().find(t => t.sessionId === task.sessionId);
                  if (!fresh) return;
                  let stillAlive = false;
                  try {
                    const probe = await fetch('/api/shell/exec', {
                      method: 'POST', credentials: 'same-origin',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ command: _tmuxCmd(task, `has-session -t ${task.sessionId}`), timeout: 5 }),
                    });
                    const pData = await probe.json();
                    stillAlive = pData.exit_code === 0;
                  } catch { /* network blip — treat as inconclusive, prefer running */ stillAlive = true; }
                  if (stillAlive) {
                    _updateTask(task.sessionId, { status: 'running', _doneConfirmAt: null, _lastStatusFlipAt: Date.now() });
                    const _el = document.querySelector(`.cookbook-task[data-task-id="${task.sessionId}"]`);
                    if (_el) {
                      _el.dataset.status = 'running';
                      const _badge = _el.querySelector('.cookbook-task-status');
                      if (_badge) { _badge.textContent = _statusLabel('running', task.type); _badge.className = 'cookbook-task-status'; }
                      const _wave = _el.querySelector('.cookbook-task-wave'); if (_wave) _wave.style.display = '';
                      const _up = _el.querySelector('.cookbook-task-uptime'); if (_up) _up.style.display = '';
                      _reconnectTask(_el, _loadTasks().find(t => t.sessionId === task.sessionId));
                    }
                    return;
                  }
                  _updateTask(task.sessionId, { status: 'done', _doneConfirmAt: null, _lastStatusFlipAt: Date.now() });
                  const _el = document.querySelector(`.cookbook-task[data-task-id="${task.sessionId}"]`);
                  if (_el) {
                    _clearDiagnosis(_el);
                    _el.dataset.status = 'done';
                    const _badge = _el.querySelector('.cookbook-task-status');
                    if (_badge) { _badge.textContent = _statusLabel('done', task.type); _badge.className = 'cookbook-task-status cookbook-task-done'; }
                    const _chk = _el.querySelector('.cookbook-task-check'); if (_chk) _chk.style.display = '';
                    const _sb = _el.querySelector('.cookbook-task-serve-btn'); if (_sb) _sb.style.display = '';
                  }
                  _showCookbookNotif();
                  _refreshDepsAfterInstall(task);
                  _renderRunningTab();
                  _processQueue();
                } catch { /* swallow — next polling cycle will retry */ }
              }, 30000);
            }
          }
        }
        _renderRunningTab();
        _processQueue();
        break;
      }

      const snapshot = (data.stdout || '').trim();
      if (snapshot) {
        // Only auto-scroll to bottom if the user was already there. When
        // they've scrolled up to read earlier output, leave their position
        // alone so a fresh snapshot doesn't yank them back to the tail.
        // 40px tolerance covers sub-pixel rounding + the moment between
        // releasing the scrollbar and the next poll arriving.
        const _atBottom = (output.scrollHeight - output.scrollTop - output.clientHeight) < 40;
        output.textContent = snapshot;
        if (_atBottom) output.scrollTop = output.scrollHeight;

        // Live status parsing for download tasks
        if (task.type === 'download') {
          const badge = el.querySelector('.cookbook-task-status');
          if (badge) {
            const completed = (snapshot.match(/Download complete/g) || []).length;
            const downloading = snapshot.match(/Downloading '([^']+)'/g) || [];
            const totalFiles = downloading.length;
            const pctMatches = [...snapshot.matchAll(/(\d+)%\|/g)];
            const lastPct = pctMatches.length ? pctMatches[pctMatches.length - 1][1] : null;
            const speedMatch = [...snapshot.matchAll(/([\d.]+)(?:MB|GB)\/s/g)];
            const lastSpeed = speedMatch.length ? speedMatch[speedMatch.length - 1][0] : null;
            // hf_transfer prints "Downloading (incomplete total...): 73% | 1.81G/2.49G"
            // — the real aggregate byte progress. The "Fetching N files" line (often
            // last in the output) sits at 0%, so lastPct/_fetchPct can read 0 even at
            // 73% done. Prefer this aggregate when present.
            const _dlAggMatches = [...snapshot.matchAll(/Downloading\s*\(incomplete[^)]*\):\s*(\d+)%/g)];
            const _dlAgg = _dlAggMatches.length ? parseInt(_dlAggMatches[_dlAggMatches.length - 1][1]) : null;

            // Stale download detection.
            // Use the DOWNLOADED-BYTE count ("1.81G" from "1.81G/2.49G") as the
            // progress signal: it climbs continuously while transferring (even when
            // the % plateaus during a big hf_transfer chunk) and FREEZES when stuck.
            // The % alone plateaus (false stall), and a frozen frame still shows a
            // stale speed/ETA — so keying off speed masked real stalls (that's why a
            // 97%-stuck download went undetected). Bytes are the honest signal; fall
            // back to %/aggregate only when no byte counter is present.
            const _byteMatches = [...snapshot.matchAll(/([\d.]+\s?[KMGT])B?\s*\/\s*[\d.]+\s?[KMGT]B?/gi)];
            const _bytes = _byteMatches.length ? _byteMatches[_byteMatches.length - 1][1].replace(/\s/g, '') : null;
            // When there's no byte counter (pip resolve / native build phase of a
            // dependency install), key off the output tail so new build lines count
            // as progress — otherwise a long quiet build is falsely declared stale
            // and restarted mid-build, looping forever (#1568).
            const curProgress = computeProgressSignal(_bytes, _dlAgg, lastPct, snapshot);
            const _fetchPctMatches = [...snapshot.matchAll(/Fetching\s+\d+\s+files:\s*(\d+)%/g)];
            const _fetchPct = _fetchPctMatches.length ? parseInt(_fetchPctMatches[_fetchPctMatches.length - 1][1]) : null;
            const isPipDep = !!(task.payload && task.payload._dep);
            const _startupStalled = !_bytes && ((_dlAgg === 0) || (_fetchPct === 0)) && curProgress === '0';
            const _STALE_TIMEOUT = _startupStalled ? STARTUP_STALE_PROGRESS_MS : STALE_PROGRESS_MS;
            if (!el._lastProgress) { el._lastProgress = curProgress; el._lastProgressTime = Date.now(); }
            if (curProgress !== el._lastProgress) {
              el._lastProgress = curProgress;
              el._lastProgressTime = Date.now();
            } else if (!isPipDep && Date.now() - (el._lastProgressTime || 0) > _STALE_TIMEOUT && task._autoRestarted) {
              const mins = Math.floor((Date.now() - (el._lastProgressTime || 0)) / 60000);
              // Already auto-restarted once and stalled again — make the badge a
              // one-click retry (resumes from the cached partial files) so the
              // user doesn't have to dig into the ⋮ menu.
              badge.textContent = `stalled ${mins}m ↻`;
              badge.className = 'cookbook-task-status cookbook-task-error';
              badge.title = 'Click to retry — resumes where it stopped';
              badge.style.cursor = 'pointer';
              if (!badge._retryBound) {
                badge._retryBound = true;
                badge.addEventListener('click', (e) => { e.stopPropagation(); _retryTask(el, task); });
              }
            } else if (!isPipDep && Date.now() - (el._lastProgressTime || 0) > _STALE_TIMEOUT && !task._autoRestarted) {
              task._autoRestarted = true;
              _updateTask(task.sessionId, { _autoRestarted: true });
              badge.textContent = _startupStalled ? '0% stall — retrying' : 'stale — restarting';
              badge.className = 'cookbook-task-status cookbook-task-error';
              _showCookbookNotif(true);
              try {
                await fetch('/api/shell/exec', {
                  method: 'POST', credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${task.sessionId}`) }),
                });
              } catch {}
              try {
                // Reuse original payload so the full repo_id (e.g. "Qwen/Qwen3.5-...")
                // is preserved — rebuilding from task.repo/task.name drops the org prefix.
                const dlPayload = task.payload
                  ? { ...task.payload }
                  : { repo_id: task.repo || task.name, remote_host: task.remoteHost || '' };
                if (_envState.hfToken) dlPayload.hf_token = _envState.hfToken;
                // Stalled with hf_transfer — restart on the reliable downloader.
                dlPayload.disable_hf_transfer = true;
                // Don't overwrite env_prefix — task.payload already has the correct
                // "source <path>" form. The bare envPath would miss the `source` and
                // the venv never activates (so hf CLI falls off PATH).
                const res = await fetch('/api/model/download', {
                  method: 'POST', credentials: 'same-origin',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(dlPayload),
                });
                const data = await res.json();
                if (data.ok && data.session_id) {
                  _updateTask(task.sessionId, { sessionId: data.session_id, status: 'running', output: '' });
                  task.sessionId = data.session_id;
                  el._lastProgress = null;
                  el._lastProgressTime = Date.now();
                  badge.textContent = 'restarted';
                  badge.className = 'cookbook-task-status cookbook-task-running';
                  continue;
                }
              } catch {}
              badge.textContent = 'stale — restart failed';
              badge.className = 'cookbook-task-status cookbook-task-error';
              _showCookbookNotif(true);
              break;
            }

            // When the snapshot includes a shard-of-N marker (e.g.
            // "model-00006-of-00082.safetensors"), TRUE overall progress is
            // ((shard-1) + currentShardFraction) / totalShards. Before, _dlAgg
            // (hf_transfer's per-current-shard aggregate, e.g. 53% of shard 6)
            // was treated as overall and the row read "53%" while only 5 of
            // 82 shards were actually done.
            const _shardPat = [...snapshot.matchAll(/model-(\d+)-of-(\d+)\.(?:safetensors|bin)/g)];
            const _lastShard = _shardPat.length ? _shardPat[_shardPat.length - 1] : null;
            const _curShardNum = _lastShard ? parseInt(_lastShard[1], 10) : null;
            const _totalShards = _lastShard ? parseInt(_lastShard[2], 10) : null;
            const _useShardAgg = _curShardNum && _totalShards && _totalShards > 1;

            // HF's own "Fetching N files: X%" aggregate counts ALL files,
            // including ones already finished in a previous session (resume) —
            // so on a resumed download it reflects the true overall progress,
            // whereas completed/totalFiles only see this session's files (→ 0%).
            // Take the higher of the two so resume doesn't read as 0%.
            if (_useShardAgg) {
              // Multi-shard download: compute TRUE overall as completed shards
              // plus the current shard's fraction. _dlAgg / lastPct represent
              // *this shard's* progress, not the whole download.
              const curShardFrac = (_dlAgg != null)
                ? _dlAgg / 100
                : (lastPct ? parseInt(lastPct, 10) / 100 : 0);
              let overallPct = Math.round((((_curShardNum - 1) + curShardFrac) / _totalShards) * 100);
              if (_fetchPct != null) overallPct = Math.max(overallPct, _fetchPct);
              let text = `${overallPct}%`;
              if (lastSpeed) text += ` · ${lastSpeed}`;
              badge.textContent = text;
              badge.className = 'cookbook-task-status cookbook-task-running';
            } else if (_dlAgg != null) {
              // Real aggregate byte progress — most accurate; take the max of all signals.
              let pct = _dlAgg;
              if (_fetchPct != null) pct = Math.max(pct, _fetchPct);
              let text = `${pct}%`;
              if (lastSpeed) text += ` · ${lastSpeed}`;
              badge.textContent = text;
              badge.className = 'cookbook-task-status cookbook-task-running';
            } else if (totalFiles > 0 && completed < totalFiles) {
              const curFilePct = lastPct ? parseInt(lastPct) / 100 : 0;
              let overallPct = Math.round(((completed + curFilePct) / totalFiles) * 100);
              if (_fetchPct != null) overallPct = Math.max(overallPct, _fetchPct);
              let text = `${overallPct}%`;
              if (lastSpeed) text += ` · ${lastSpeed}`;
              badge.textContent = text;
              badge.className = 'cookbook-task-status cookbook-task-running';
            } else if (_fetchPct != null && _fetchPct < 100) {
              // Resume start: only the aggregate is meaningful yet.
              let text = `${_fetchPct}%`;
              if (lastSpeed) text += ` · ${lastSpeed}`;
              badge.textContent = text;
              badge.className = 'cookbook-task-status cookbook-task-running';
            } else if (completed > 0 && completed >= totalFiles) {
              badge.textContent = 'finishing';
              badge.className = 'cookbook-task-status cookbook-task-running';
            }
            if (snapshot.includes('DOWNLOAD_FAILED')) {
              // The wrapper prints DOWNLOAD_FAILED but exits 0, and per-file
              // "Download complete"/"100%" lines make it look successful — so
              // catch the explicit failure marker and handle it.
              // A gated/auth failure can NEVER be fixed by retrying (the HF token
              // is sent, but its account isn't approved for this repo) — skip the
              // auto-retries and surface the gated diagnosis straight away.
              const _accessDenied = /Access to model.*is restricted|gated repo|GatedRepoError|401 Unauthorized|403 Forbidden|not in the authorized list|awaiting a review|must (?:be authenticated|have access)/i.test(snapshot);
              const _dlKey = task.payload?.repo_id || task.name;
              const _dlN = _dlRetryCount.get(_dlKey) || 0;
              if (!controller.signal.aborted && !_accessDenied && task.type === 'download' && task.payload && _dlN < _DL_MAX_AUTO_RETRY) {
                // Auto-retry: kill the dead session and re-launch (resumes from
                // the cached .incomplete files) after a short delay.
                _dlRetryCount.set(_dlKey, _dlN + 1);
                badge.textContent = `retrying (${_dlN + 1}/${_DL_MAX_AUTO_RETRY})…`;
                badge.className = 'cookbook-task-status cookbook-task-running';
                uiModule.showToast(`Download interrupted — retrying (${_dlN + 1}/${_DL_MAX_AUTO_RETRY}), resumes where it stopped…`, 6000);
                const _p = task.payload, _nm = task.name;
                try {
                  await fetch('/api/shell/exec', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${task.sessionId}`) }),
                  });
                } catch {}
                _removeTask(task.sessionId);
                setTimeout(() => { _retryDownload(_nm, _p); }, 8000);
                break;
              }
              // Out of auto-retries (or not a download) — surface the error; the
              // card's Retry button stays available to resume manually.
              badge.textContent = _statusLabel('error', task.type);
              badge.className = 'cookbook-task-status cookbook-task-error';
              _updateTask(task.sessionId, { status: 'error' });
              el.dataset.status = 'error';
              // Explain a gated/access failure with actionable buttons (request
              // access on HF, check token) — otherwise it's just raw red text.
              if (_accessDenied) {
                const _diag = _diagnose(snapshot);
                if (_diag) {
                  let diagEl = el.querySelector('.cookbook-diagnosis');
                  if (!diagEl) { diagEl = document.createElement('div'); diagEl.className = 'cookbook-diagnosis'; el.appendChild(diagEl); }
                  _showDiagnosis(el, _diag, snapshot);
                }
              }
              _showCookbookNotif(true);
              break;
            }
            if (snapshot.includes('DOWNLOAD_OK') || (snapshot.includes('/snapshots/') && completed >= totalFiles && totalFiles > 0)) {
              _clearDiagnosis(el);
              _dlRetryCount.delete(task.payload?.repo_id || task.name);
              badge.textContent = _statusLabel('done', task.type);
              badge.className = 'cookbook-task-status cookbook-task-done';
              // Flip the type chip from "download" to the green "finished"
              // badge so the header reads as completed without a stale label.
              const _typeChip = el.querySelector('.cookbook-task-type');
              if (_typeChip) { _typeChip.textContent = 'finished'; _typeChip.classList.add('cookbook-task-type-done'); }
              _updateTask(task.sessionId, { status: 'done' });
              const _sb2 = el.querySelector('.cookbook-task-serve-btn'); if (_sb2) _sb2.style.display = '';
              _showCookbookNotif();
              _refreshDepsAfterInstall(task);
              fetch('/api/shell/exec', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: _tmuxCmd(task, `kill-session -t ${task.sessionId}`) }),
              }).catch(() => {});
              _processQueue();
              break;
            }
          }
        }

        // Live status parsing for serve tasks — uses shared _parseServePhase
        if (task.type === 'serve') {
          const badge = el.querySelector('.cookbook-task-status');
          if (badge) {
            const info = _parseServePhase(snapshot);
            if (info.status === 'ready' && !task._serveReady) {
              task._serveReady = true;
              _updateTask(task.sessionId, { _serveReady: true });
            }
            if (info.phase) {
              badge.textContent = info.phase;
              // Always the green "running" style — loading/warming is the same
              // state, just with dynamic text (don't switch to a neutral style).
              badge.className = 'cookbook-task-status cookbook-task-running';
              // Live output reporting 'ready' is direct proof the server is up —
              // clear a stale "unreachable" flag here too. The HTTP probe can lag,
              // miss a remote endpoint, or cache a down result, leaving the card
              // stuck red even after the server recovered ("doesn't recheck").
              if (info.status === 'ready' && task._unreachable) {
                task._unreachable = false;
                _updateTask(task.sessionId, { _unreachable: false });
                el.classList.remove('cookbook-task-unreachable');
                _refreshServerDots();
              }
              // Persist the loading phase so a re-render keeps showing "loading 45%"
              // instead of resetting the badge to the generic "running". Clear it
              // once ready so the badge falls back to "running".
              if (info.status !== 'ready') {
                if (task.progress !== info.phase) _updateTask(task.sessionId, { progress: info.phase });
              } else if (task.progress) {
                _updateTask(task.sessionId, { progress: '' });
              }
            }
          }
        }

        // Run error diagnosis on serve tasks
        const diag = _diagnose(snapshot);
        if (diag) {
          let diagEl = el.querySelector('.cookbook-diagnosis');
          if (!diagEl) {
            diagEl = document.createElement('div');
            diagEl.className = 'cookbook-diagnosis';
            el.appendChild(diagEl);
          }
          _showDiagnosis(el, diag, snapshot);
        }
        // Detect serve ready — auto-add to model endpoints. Don't flip
        // `_endpointAdded` until the POST succeeds; otherwise a transient
        // error silently prevents any future retry. An in-flight guard
        // prevents a second poll from firing a duplicate POST before the
        // first one's dedup check can observe the newly-added row.
        if (task.type === 'serve' && !task._endpointAdded && !task._endpointAddInFlight && task._serveReady) {
          task._endpointAddInFlight = true;
          let host = _connectHostFromRemote(task.remoteHost);
          const portMatch = task.payload?._cmd?.match(/--port[=\s]+(\d+)/)
            || task.payload?._cmd?.match(/(?:^|\s)-p[=\s]+(\d+)/)
            || snapshot.match(/Uvicorn running on\D*?:(\d+)/i)
            || snapshot.match(/running on\D*?:(\d+)/i)
            || snapshot.match(/listening on\D*?:(\d+)/i)
            || snapshot.match(/port[:=\s]+(\d+)/i);
          let port = portMatch ? portMatch[1] : '8000';
          let baseUrl = `http://${host}:${port}/v1`;
          const ollamaUrlMatch = snapshot.match(/Ollama API ready on port\s+\d+:\s*(http:\/\/[^\s]+)/i);
          if (ollamaUrlMatch) {
            const endpoint = _endpointFromAdvertisedUrl(ollamaUrlMatch[1], host, '11434');
            if (endpoint) ({ host, port, baseUrl } = endpoint);
          }
          fetch('/api/model-endpoints', { credentials: 'same-origin' })
            .then(r => r.json())
            .then(async (eps) => {
              // Match only exact base_url — don't dedup by friendly name,
              // because other endpoints may happen to share a model name.
              const exists = eps.some(e => e.base_url === baseUrl);
              if (exists) {
                // Already registered — e.g. the backend pre-registers diffusion
                // endpoints server-side. Mark so we don't retry, but STILL
                // refresh the picker (and probe until online) so the new model
                // shows up without the user having to manually refresh.
                task._endpointAdded = true;
                _updateTask(task.sessionId, { _endpointAdded: true });
                _autoSaveWorkingConfig(task);   // endpoint live → remember these settings
                if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
                if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
                window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', { detail: { baseUrl, host, port, model: task.name } }));
                const _ex = eps.find(e => e.base_url === baseUrl);
                if (_ex && _ex.id && !(_ex.models || []).length) _probeEndpointUntilOnline(_ex.id, host, port);
                return null;
              }
              const _isDiffusion = task.payload?._cmd?.includes('diffusion_server');
              const fd = new FormData();
              fd.append('base_url', baseUrl);
              fd.append('name', task.name);
              fd.append('skip_probe', 'true');
              _appendCookbookEndpointScope(fd, task.remoteHost || '');
              if (_isDiffusion) fd.append('model_type', 'image');
              return fetch('/api/model-endpoints', { method: 'POST', credentials: 'same-origin', body: fd });
            })
            .then(async (res) => {
              if (res && res.ok) {
                // Flip the flag only on confirmed success
                task._endpointAdded = true;
                _updateTask(task.sessionId, { _endpointAdded: true });
                _autoSaveWorkingConfig(task);   // endpoint live → remember these settings
                uiModule.showToast(`Model endpoint added: ${host}:${port}`);
                // Retry-probe until the warming server answers, so it
                // flips online without a manual enable/disable toggle.
                const _epData = await res.json().catch(() => ({}));
                if (_epData && _epData.id && !(_epData.models || []).length) {
                  _probeEndpointUntilOnline(_epData.id, host, port);
                }
                window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', { detail: { baseUrl, host, port, model: task.name } }));
                const _trySelectModel = async (attempt) => {
                  if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
                  const items = window.modelsModule?.getCachedItems?.() || [];
                  for (const item of items) {
                    if (item.offline) continue;
                    const url = item.url || '';
                    if (url.includes(host) || url.includes(port)) {
                      const mid = (item.models || [])[0];
                      if (mid && window.sessionModule?.createDirectChat) {
                        window.sessionModule.createDirectChat(url, mid, item.endpoint_id);
                        if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
                        uiModule.showToast(`Switched to ${mid.split('/').pop()}`);
                        return;
                      }
                    }
                  }
                  if (attempt < 3) setTimeout(() => _trySelectModel(attempt + 1), 2000);
                  else if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
                };
                setTimeout(() => _trySelectModel(0), 1000);
              } else if (res && !res.ok) {
                const body = await res.text().catch(() => '');
                console.warn('Endpoint auto-add failed', res.status, body);
                uiModule.showError(`Auto-register endpoint failed (${res.status}). Use ⋮ → Register endpoint to retry.`);
              }
            })
            .catch((e) => {
              console.warn('Endpoint auto-add error', e);
              uiModule.showError(`Auto-register endpoint error: ${e.message || e}. Use ⋮ → Register endpoint to retry.`);
            })
            .finally(() => { task._endpointAddInFlight = false; });
          _updateTask(task.sessionId, { status: 'running' });
          const badge = el.querySelector('.cookbook-task-status');
          if (badge) { badge.textContent = 'running'; badge.className = 'cookbook-task-status cookbook-task-running'; }
          _showCookbookNotif();
        }
        // Detect process exit
        if (snapshot.includes('=== Process exited with code')) {
          const codeMatch = snapshot.match(/=== Process exited with code (\d+)/);
          const code = codeMatch ? parseInt(codeMatch[1]) : -1;
          // Serve tasks that exit without reaching ready state are always errors —
          // a serve process should run indefinitely
          const status = (task.type === 'serve' && !task._serveReady) ? 'error'
            : (code === 0 ? 'done' : 'error');
          _updateTask(task.sessionId, { status });
          const badge = el.querySelector('.cookbook-task-status');
          if (badge) { badge.textContent = status; badge.className = `cookbook-task-status cookbook-task-${status}`; }
          _renderRunningTab();
        }
        _updateTask(task.sessionId, { output: snapshot.slice(-5000) });
      }
    } catch {
      failCount++;
      if (failCount > 10) break;
      await new Promise(r => setTimeout(r, 10000));
      continue;
    }

    failCount = 0;
    await new Promise(r => setTimeout(r, TASK_POLL_INTERVAL_MS));
  }
}

// ── Background monitor ──

let _bgMonitorInterval = null;

// Reachability check for running serve tasks. The tmux pane can stay alive
// while the model server inside it has crashed (so no "Process exited" line
// ever appears) — leaving the card showing "running" forever. So we actively
// probe the registered endpoint (same /probe-local the model picker uses) and
// flag the card "unreachable" (red) when the server stops answering.
async function _checkServeReachability() {
  let serveTasks;
  try {
    serveTasks = _loadTasks().filter(t => t.type === 'serve' && t.status === 'running');
  } catch { return; }
  if (!serveTasks.length) return;
  let eps = [], probe = {};
  try {
    [eps, probe] = await Promise.all([
      fetch('/api/model-endpoints', { credentials: 'same-origin' }).then(r => r.json()).catch(() => []),
      fetch('/api/model-endpoints/probe-local', { credentials: 'same-origin' }).then(r => r.json()).catch(() => ({})),
    ]);
  } catch { return; }
  for (const task of serveTasks) {
    const host = _connectHostFromRemote(task.remoteHost);
    const portMatch = task.payload?._cmd?.match(/--port\s+(\d+)/);
    const port = portMatch ? portMatch[1] : '8000';
    const baseUrl = `http://${host}:${port}/v1`;
    const ep = (eps || []).find(e => e.base_url === baseUrl);
    if (!ep) continue;                       // not registered yet — can't judge
    const pr = probe[ep.id];
    if (!pr || pr.alive === undefined) continue;  // not probed (non-local) — skip
    // Record the first time it actually answers. Until then the server is still
    // LOADING/warming (the endpoint can get registered on the 300s timeout for a
    // big model that hasn't finished loading), and a not-yet-answering server is
    // not "unreachable" — flagging it as such while you're launching is a false
    // alarm. Only treat it as unreachable once it has been reachable at least once.
    if (pr.alive === true && !task._everReachable) {
      task._everReachable = true;
      _updateTask(task.sessionId, { _everReachable: true });
    }
    const unreachable = pr.alive === false;
    if (unreachable && !task._everReachable) continue;  // still coming up, not crashed
    if (!!task._unreachable !== unreachable) {
      _updateTask(task.sessionId, { _unreachable: unreachable });
    }
    const el = document.querySelector(`.cookbook-task[data-task-id="${task.sessionId}"]`);
    if (el) {
      el.classList.toggle('cookbook-task-unreachable', unreachable);
      const badge = el.querySelector('.cookbook-task-status');
      if (badge) {
        if (unreachable) {
          badge.textContent = 'unreachable';
          badge.className = 'cookbook-task-status cookbook-task-error';
          badge.title = pr.error || 'Server not responding — it may have crashed';
        } else if (badge.textContent === 'unreachable') {
          // Recovered — restore the normal running label.
          badge.textContent = _statusLabel('running', task.type);
          badge.className = 'cookbook-task-status cookbook-task-running';
          badge.title = '';
        }
      }
    }
    if (unreachable) _showCookbookNotif(true);
  }
  _refreshServerDots();
}

function _serveTaskFailed(task) {
  if (!task || task.type !== 'serve') return false;
  return !!task._unreachable || ['error', 'crashed', 'failed'].includes(task.status);
}

function _setServerDot(dot, failed, title) {
  if (!dot) return;
  dot.classList.toggle('fail', !!failed);
  dot.classList.toggle('ok', !failed);
  dot.title = title;
}

function _syncSettingsServerDots(byKey) {
  document.querySelectorAll('.cookbook-server-entry').forEach(entry => {
    const hostEl = entry.querySelector('.cookbook-srv-host');
    const dot = entry.querySelector('.cookbook-srv-status');
    const msg = entry.querySelector('.cookbook-srv-test-msg');
    if (!hostEl || !dot) return;

    const host = hostEl.value?.trim() || '';
    if (!host || hostEl.readOnly || hostEl.disabled) {
      _setServerDot(dot, false, 'Local (this machine)');
      return;
    }

    const list = byKey[host] || [];
    if (!list.length) return;

    const failed = list.some(_serveTaskFailed);
    _setServerDot(dot, failed, failed ? 'Server not responding - running serve may have crashed' : 'Reachable');
    if (!msg) return;

    if (failed) {
      msg.textContent = 'Server not responding';
      msg.title = 'Server not responding - running serve may have crashed';
      msg.style.color = 'var(--red,#e06c75)';
      msg.style.opacity = '0.75';
    } else if (/failed|crashed|not responding|unreachable/i.test(msg.textContent || '')) {
      msg.textContent = 'Reachable';
      msg.title = 'Reachable';
      msg.style.color = 'var(--green,#50fa7b)';
      msg.style.opacity = '0.75';
    }
  });
}

// Keep each server section's status dot (green ↔ red) in sync with the live
// health of its SERVE tasks. The header dot is only built once, so without
// this it got stuck on its first value. Downloads never count because they have
// no endpoint to be "unreachable".
function _refreshServerDots() {
  let tasks;
  try { tasks = _loadTasks(); } catch { return; }
  const byKey = {};
  for (const t of tasks) { (byKey[t.remoteHost || ''] = byKey[t.remoteHost || ''] || []).push(t); }
  document.querySelectorAll('.cookbook-section-header').forEach(header => {
    const dot = header.querySelector('.cookbook-srv-status');
    if (!dot) return;
    const key = header.querySelector('[data-stop-server]')?.dataset.stopServer || '';
    const list = byKey[key] || [];
    const fail = !!key && list.some(_serveTaskFailed);
    _setServerDot(dot, fail, key ? (fail ? 'Server not responding' : 'Reachable') : 'Local (this machine)');
  });
  _syncSettingsServerDots(byKey);
}

// Self-heal: scan persisted download tasks marked done/error/crashed and
// check whether their tmux session is still alive on the host. If yes —
// the task isn't actually finished, the cookbook just lost the in-flight
// status during restart — flip status back to 'running' so _reconnectTask
// picks it up. The one-shot guard is enforced by callers (open path) or
// time-throttled inside (background-monitor path).
let _selfHealRan = false;
let _selfHealLastTs = 0;
export async function _selfHealStaleTasks(opts = {}) {
  // Open-path call: one-shot per page load.
  if (opts.oneShot) {
    if (_selfHealRan) return;
    _selfHealRan = true;
  } else {
    // Background-monitor call: throttle to once every 8s (the bg monitor
    // itself fires every 10s, so this almost always fires too, but the
    // guard keeps a fast manual call from doubling up).
    const now = Date.now();
    if (now - _selfHealLastTs < 4000) return;
    _selfHealLastTs = now;
  }
  const tasks = _loadTasks();
  const candidates = tasks.filter(t => {
    if (t.type !== 'download') return false;
    if (!['done', 'error', 'crashed', 'stopped'].includes(t.status)) return false;
    if (!t.sessionId || String(t.sessionId).startsWith('queue-')) return false;
    // Finished downloads with strong completion markers (DOWNLOAD_OK or HF
    // /snapshots/ resolution) are demonstrably done — do not flip them back
    // to running just because the tmux session is still alive (e.g., a
    // long-lived shell that hosted the download or a flapping SSH that
    // reports the session as up). This was the main source of finished↔
    // downloading oscillation on a flaky connection.
    if (t.status === 'done' && /DOWNLOAD_OK|\/snapshots\//.test(t.output || '')) return false;
    // Cooldown: never flip the same task more than once every 45s. A flapping
    // SSH connection used to drive the badge back-and-forth on every probe
    // cycle; this enforces a stable view between flaps.
    if (t._lastStatusFlipAt && (Date.now() - t._lastStatusFlipAt < 45000)) return false;
    return true;
  });
  if (!candidates.length) return;
  let flipped = 0;
  for (const t of candidates) {
    try {
      const res = await fetch('/api/shell/exec', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: _tmuxCmd(t, `has-session -t ${t.sessionId}`), timeout: 5 }),
      });
      const data = await res.json();
      if (data.exit_code === 0) {
        // Session still alive → the task is actually still running.
        const fresh = _loadTasks();
        const ft = fresh.find(x => x.sessionId === t.sessionId);
        if (ft && ft.status !== 'running') {
          ft.status = 'running';
          ft._selfHealed = true;
          ft._lastStatusFlipAt = Date.now();
          _saveTasks(fresh);
          flipped++;
          const _el = document.querySelector(`.cookbook-task[data-task-id="${t.sessionId}"]`);
          if (_el) {
            const _chk = _el.querySelector('.cookbook-task-check');
            if (_chk) _chk.style.display = 'none';
            const _wave = _el.querySelector('.cookbook-task-wave');
            if (_wave) _wave.style.display = '';
            const _up = _el.querySelector('.cookbook-task-uptime');
            if (_up) _up.style.display = '';
            _el.dataset.status = 'running';
          }
        }
      }
    } catch { /* network blip — skip this one */ }
  }
  if (flipped) {
    console.log(`[cookbook] auto-reconnect: revived ${flipped} task(s) whose tmux session was still alive`);
    _renderRunningTab();
  }
}

export function _startBackgroundMonitor() {
  if (_bgMonitorInterval) return;
  _bgMonitorInterval = setInterval(() => {
    _pollBackgroundStatus();
    _checkServeReachability();
    // Auto-reconnect: every cycle, look for download tasks marked finished/
    // crashed/etc. whose tmux session is actually still running, and flip
    // them back to running. Internally throttled to 8s so a manual call from
    // the open path or a fast invocation doesn't double up.
    _selfHealStaleTasks().catch(() => {});
  }, BG_MONITOR_INTERVAL_MS);
  _pollBackgroundStatus();
  _checkServeReachability();
}

function _stopBackgroundMonitor() {
  if (_bgMonitorInterval) {
    clearInterval(_bgMonitorInterval);
    _bgMonitorInterval = null;
  }
  const statusEl = document.getElementById('cookbook-bg-status');
  if (statusEl) statusEl.style.display = 'none';
}

// Retry-probe a freshly-added endpoint until its model server answers.
// A model that just reached "ready" in the cookbook often can't satisfy
// the 1s add-time probe (remote, weights still mmap-ing), so it's added
// offline. This polls the per-endpoint /probe (which uses a longer
// server-side timeout + persists cached_models) every few seconds until
// the endpoint reports models, then refreshes the picker. Bounded so a
// genuinely-dead server doesn't poll forever.
async function _probeEndpointUntilOnline(epId, host, port) {
  if (!epId) return;
  // Big models (e.g. 70B+) can take several minutes to load weights before
  // the server answers /v1/models. Probe for up to ~5 min, easing the
  // interval out so we're not hammering during a long warmup.
  const MAX_TRIES = 40;
  for (let i = 0; i < MAX_TRIES; i++) {
    const interval = i < 12 ? 5000 : 10000;   // 5s for the first minute, then 10s
    await new Promise(r => setTimeout(r, interval));
    try {
      // Hit the probe endpoint — it re-probes server-side and updates
      // cached_models. We consume (and discard) the SSE stream.
      await fetch(`/api/model-endpoints/${epId}/probe`, { credentials: 'same-origin' }).then(r => r.text()).catch(() => {});
      const eps = await fetch('/api/model-endpoints', { credentials: 'same-origin' }).then(r => r.json()).catch(() => []);
      const ep = (eps || []).find(e => e.id === epId);
      if (ep && (ep.models || []).length) {
        if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
        if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
        window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated', {
          detail: { baseUrl: ep.base_url || `http://${host}:${port}/v1`, host, port, model: (ep.models || [])[0] || '' },
        }));
        uiModule.showToast(`${host}:${port} is online`);
        return;
      }
    } catch (_) { /* keep retrying */ }
  }
}

async function _pollBackgroundStatus() {
  try {
    // Pull any tasks the server knows about that aren't in localStorage
    // yet (e.g. agent-spawned downloads/serves). Without this merge,
    // _syncToServer keeps clobbering server-added tasks on every poll.
    try {
      const stateRes = await fetch('/api/cookbook/state', { credentials: 'same-origin' });
      if (stateRes.ok) {
        const serverState = await stateRes.json();
        const serverTasks = (serverState && Array.isArray(serverState.tasks)) ? serverState.tasks : [];
        if (serverTasks.length) {
          const localTasks = _loadTasks();
          const localIds = new Set(localTasks.map(t => t.sessionId));
          const merged = [...localTasks];
          let added = 0;
          for (const t of serverTasks) {
            if (t && t.sessionId && !localIds.has(t.sessionId) && !_isTombstoned(t.sessionId)) {
              merged.push(t);
              added++;
            }
          }
          if (added > 0) {
            localStorage.setItem(TASKS_KEY, JSON.stringify(merged.map(_stripTaskSecrets)));
            _renderRunningTab();
          }
        }
      }
    } catch (_) { /* non-fatal */ }

    const res = await fetch('/api/cookbook/tasks/status', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    const tasks = data.tasks || [];

    // Reconcile the authoritative tmux/process status back into the persisted
    // client task list. The Running-tab reconnect loop also does this, but it
    // only exists while cards are rendered; after a page refresh or closed modal
    // dependency installs could finish server-side while localStorage stayed
    // stuck at "running".
    try {
      const statusById = new Map(tasks.map(t => [t.session_id, t]));
      const localTasks = _loadTasks();
      let changed = false;
      const completedDeps = [];
      for (const task of localTasks) {
        const live = statusById.get(task.sessionId);
        if (!live) continue;
        const updates = {};
        // A finished dependency install whose tmux pane is gone is reported
        // "stopped" by the backend (its pip package is never in the HF cache the
        // dead-session check inspects). Recover "done" from the retained output's
        // exit-0 sentinel so a clean install isn't downgraded to crashed.
        const depDone = !!task.payload?._dep && _depInstallSucceeded(task.output);
        const nextStatus = live.status === 'completed'
          ? 'done'
          : (live.status === 'error'
            ? 'error'
            : (live.status === 'stopped'
                ? (depDone ? 'done' : (task.type === 'download' ? 'crashed' : 'stopped'))
                : null));
        if (nextStatus && task.status !== nextStatus) {
          updates.status = nextStatus;
          if (nextStatus === 'done' && task.payload?._dep) completedDeps.push(task);
        }
        if ((live.status === 'running' || live.status === 'ready') && task.status !== live.status) {
          updates.status = live.status === 'ready' ? 'ready' : 'running';
        }
        if (live.progress && live.progress !== task.progress) updates.progress = live.progress;
        if (live.output_tail) {
          const previous = String(task.output || '');
          const tail = String(live.output_tail || '');
          if (tail && !previous.endsWith(tail)) {
            updates.output = `${previous ? `${previous}\n` : ''}${tail}`.slice(-5000);
          }
        }
        if (live.diagnosis && !task._diagnosisDismissed) {
          updates._backendDiagnosis = live.diagnosis;
        }
        if (live.cmd && !task.payload?._cmd) {
          updates.payload = { ...(task.payload || {}), _cmd: live.cmd };
        }
        if (Object.keys(updates).length) {
          Object.assign(task, updates);
          changed = true;
        }
      }
      if (changed) {
        _saveTasks(localTasks);
        _renderRunningTab();
        for (const task of localTasks) {
          if (!task._backendDiagnosis) continue;
          const el = document.querySelector(`[data-session-id="${CSS.escape(task.sessionId)}"]`);
          if (!el || el.querySelector('.cookbook-diagnosis')) continue;
          _showDiagnosis(el, task._backendDiagnosis, task.output || '');
        }
        completedDeps.forEach(t => _refreshDepsAfterInstall(t));
      }
    } catch (_) { /* non-fatal: background status should never break polling */ }

    const statusEl = document.getElementById('cookbook-bg-status');
    const activeTasks = tasks.filter(t => t.status === 'running' || t.status === 'ready');
    const errorTasks = tasks.filter(t => t.status === 'error');
    const completedTasks = tasks.filter(t => t.status === 'completed');

    // Auto-add serve endpoints that became ready (works even when modal is closed)
    const readyServes = tasks.filter(t => t.type === 'serve' && t.status === 'ready');
    for (const t of readyServes) {
      const localTasks = _loadTasks();
      const localTask = localTasks.find(lt => lt.sessionId === t.session_id);
      if (localTask && localTask._endpointAdded) continue;

      let host = _connectHostFromRemote(localTask?.remoteHost || t.remote);
      const portMatch = localTask?.payload?._cmd?.match(/--port\s+(\d+)/)
        || localTask?.payload?._cmd?.match(/OLLAMA_HOST=[^\s:]+:(\d+)/);
      let port = portMatch ? portMatch[1] : '8000';
      let baseUrl = `http://${host}:${port}/v1`;
      const snapshot = t.output || localTask?.output || '';
      const ollamaUrlMatch = snapshot.match(/Ollama API ready on port\s+\d+:\s*(http:\/\/[^\s]+)/i);
      if (ollamaUrlMatch) {
        const endpoint = _endpointFromAdvertisedUrl(ollamaUrlMatch[1], host, '11434');
        if (endpoint) ({ host, port, baseUrl } = endpoint);
      }
      const _isDiffusion = localTask?.payload?._cmd?.includes('diffusion_server');

      _updateTask(t.session_id, { _serveReady: true, _endpointAdded: true });
      if (localTask) _autoSaveWorkingConfig(localTask);   // remember working settings (modal may be closed)

      // Auto-detect function-calling support from the serve cmd.
      // vLLM emits OpenAI-style tool_calls only when launched with
      // `--enable-auto-tool-choice`; local-only models otherwise
      // hallucinate a fake [TOOL_CALL]...[/TOOL_CALL] text format
      // the backend can't parse.
      const _cmd = localTask?.payload?._cmd || '';
      const _supportsTools = _cmd.includes('--enable-auto-tool-choice') || _isDiffusion === false && /(?:^|\s)(?:deepseek|gpt-[45o]|claude|gemini|qwen3|qwen2\.5|mixtral|llama-[34]|minimax|kimi|hermes|glm-4)/i.test(t.model);

      fetch('/api/model-endpoints', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(eps => {
          const hostPort = `${host}:${port}`;
          const existing = eps.find(e => e.base_url === baseUrl || e.base_url.includes(hostPort) || e.name === t.model);
          if (existing) {
            // Already registered — but it may be showing offline because
            // it was added while the server was still warming. Kick a
            // re-probe so it flips online without manual toggle.
            if (!(existing.models || []).length) _probeEndpointUntilOnline(existing.id, host, port);
            return null;
          }
          const fd = new FormData();
          fd.append('base_url', baseUrl);
          fd.append('name', t.model);
          fd.append('skip_probe', 'true');
          _appendCookbookEndpointScope(fd, localTask?.remoteHost || t.remote || '');
          if (_isDiffusion) fd.append('model_type', 'image');
          if (_supportsTools) fd.append('supports_tools', 'true');
          return fetch('/api/model-endpoints', { method: 'POST', credentials: 'same-origin', body: fd });
        })
        .then(async (res) => {
          if (res && res.ok) {
            uiModule.showToast(`Model endpoint added: ${host}:${port}`);
            const data = await res.json().catch(() => ({}));
            // A just-started server often can't answer the 1s add-time
            // probe, so it lands "offline". Retry-probe in the background
            // until /v1/models responds — no manual enable/disable needed.
            if (data && data.id) _probeEndpointUntilOnline(data.id, host, port);
            if (window.modelsModule?.refreshModels) await window.modelsModule.refreshModels(true);
            if (window.sessionModule?.updateModelPicker) window.sessionModule.updateModelPicker();
          }
        })
        .catch(() => {});
    }

    if (errorTasks.length > 0) {
      _showCookbookNotif(true);
    } else if (completedTasks.length > 0) {
      _showCookbookNotif(false);
    } else if (activeTasks.length > 0) {
      _showCookbookNotif(false);
    } else {
      _clearCookbookNotif();
      _stopBackgroundMonitor();
    }

    if (statusEl) {
      if (activeTasks.length > 0) {
        const t = activeTasks[0];
        if (t.type === 'serve') {
          if (t.progress) {
            // Show serve phase from backend (e.g. "loading 45%", "warming up", "idle", "12.5 tok/s")
            statusEl.textContent = t.progress;
          } else if (t.status === 'ready') {
            statusEl.textContent = 'ready';
          } else {
            statusEl.textContent = 'cooking';
          }
        } else {
          var _dlProgress = '';
          if (t.progress) {
            var _pctMatch = t.progress.match(/(\d+)%/);
            _dlProgress = _pctMatch ? ` ${_pctMatch[0]}` : '';
          }
          statusEl.textContent = `downloading${_dlProgress}`;
        }
        statusEl.style.display = '';
      } else if (errorTasks.length > 0) {
        statusEl.textContent = 'error';
        statusEl.style.display = '';
        statusEl.style.color = 'var(--color-error, #f44)';
      } else if (completedTasks.length > 0) {
        statusEl.textContent = 'done';
        statusEl.style.display = '';
        statusEl.style.color = 'var(--color-success, #4caf50)';
      } else {
        statusEl.style.display = 'none';
        statusEl.style.color = '';
      }
    }
    // Also clear the sidebar/rail icon highlight when no tasks are alive.
    // Without this, the cookbook icon stays at full opacity ("highlighted")
    // indefinitely once any task fires the notif, because the modal-open
    // clear only runs when the user actually reopens Cookbook.
    if (!activeTasks.length && !errorTasks.length) {
      _clearCookbookNotif();
    }
  } catch (e) {
    // Silent fail
  }
}

// ── Init: receive shared state/functions ──

export function initRunning(shared) {
  _envState = shared._envState;
  _sshCmd = shared._sshCmd;
  _getPort = shared._getPort;
  _sshPrefix = shared._sshPrefix;
  _getPlatform = shared._getPlatform;
  _isWindows = shared._isWindows;
  _buildEnvPrefix = shared._buildEnvPrefix;
  _loadPresets = shared._loadPresets;
  _savePresets = shared._savePresets;
  _copyText = shared._copyText;
  _persistEnvState = shared._persistEnvState;
  _refreshDependencies = shared._refreshDependencies;
  modelLogo = shared.modelLogo;
  esc = shared.esc;
  _detectBackend = shared._detectBackend;
  _detectToolParser = shared._detectToolParser;
  _detectModelOptimizations = shared._detectModelOptimizations;
  _buildServeCmd = shared._buildServeCmd;

  // App boot: pull authoritative state from server, then auto-start
  // the background monitor unconditionally. Used to gate on "already
  // has running tasks" but that meant when the agent (or anyone)
  // added a task after boot, the UI never noticed. 10s poll of a
  // small status endpoint is cheap and gives the agent + the UI a
  // shared live picture.
  (async () => {
    try {
      await _syncFromServer();
    } catch {}
    _startBackgroundMonitor();
  })();
}

// Also export _retryDownload and _nextAvailablePort for use by other modules
export { _retryDownload, _nextAvailablePort, _processQueue };

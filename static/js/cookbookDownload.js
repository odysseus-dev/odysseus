// ============================================
// COOKBOOK DOWNLOAD SUB-MODULE
// Download tab: SSE streaming, model download,
// panel rendering, command building
// ============================================

import uiModule from './ui.js';
import { _diagnose, _showDiagnosis, _clearDiagnosis } from './cookbook-diagnosis.js';

// Shared state/functions injected by init()
let _envState;
let _sshCmd;
let _getPort;
let _getPlatform;
let _isWindows;
let _buildEnvPrefix;
let _buildServeCmd;
let _detectBackend;
let _detectToolParser;
let _loadPresets;
let _savePresets;
let _copyText;
let _persistEnvState;
let modelLogo;
let esc;
let _addTask;
let _renderRunningTab;
let _loadTasks;
let _saveTasks;

// Storage keys
const SERVE_STATE_KEY = 'cookbook-serve-state';

// ── Panel field helpers ──

export function _setPanelField(panel, field, value) {
  const input = panel.querySelector(`[data-field="${field}"]`);
  if (!input) return;
  if (input.tagName === 'SELECT') {
    input.value = value;
  } else if (input.type === 'checkbox') {
    input.checked = !!value;
  } else {
    input.value = value;
  }
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

export function _setPanelCheckbox(panel, field, checked) {
  const cb = panel.querySelector(`[data-field="${field}"]`);
  if (cb) {
    cb.checked = checked;
    cb.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

// ── Command builder: download ──

function _firstGgufSource(model) {
  const sources = Array.isArray(model?.gguf_sources) ? model.gguf_sources : [];
  return sources.find(src => src && src.repo) || null;
}

function _looksLikeGgufRepo(model) {
  const haystack = `${model?.quant_repo || ''} ${model?.repo_id || ''} ${model?.path || ''} ${model?.name || ''}`.toLowerCase();
  return !!model?.is_gguf || haystack.includes('gguf') || haystack.includes('.gguf');
}

function _ggufDownloadSource(model, backend) {
  if (backend !== 'llamacpp') return null;
  const source = _firstGgufSource(model);
  if (source) return source;
  if (_looksLikeGgufRepo(model)) {
    const repo = model?.quant_repo || model?.repo_id || model?.name;
    if (repo) return { repo };
  }
  return null;
}

function _ggufIncludePattern(model, source) {
  if (source?.file) return source.file;
  if (model?.quant) return `*${model.quant}*`;
  return '*.gguf';
}

function _missingGgufMessage(model) {
  const name = model?.name || 'this model';
  if (/\bnvfp4\b/i.test(name)) {
    return `${name} is an NVIDIA NVFP4 checkpoint, not a GGUF download. Pick the base model row with an Unsloth GGUF source, or paste the GGUF repo directly.`;
  }
  return `No GGUF source is configured for ${name}. Pick a model with a GGUF source, or paste the GGUF repo in Download.`;
}

function _bashQuote(value) {
  return "'" + String(value ?? '').replace(/'/g, "'\\''") + "'";
}

function _missingGgufCommand(model) {
  const msg = _missingGgufMessage(model);
  if (_isWindows()) {
    return `Write-Error ${JSON.stringify(msg)}; exit 1`;
  }
  return `printf '%s\\n' ${_bashQuote(msg)} >&2; exit 1`;
}

export function _buildDownloadCmd(model, backend) {
  let cmd = '';
  if (backend === 'ollama') {
    cmd = `ollama pull ${model.name.split('/').pop().toLowerCase()}`;
  } else {
    const ggufSource = _ggufDownloadSource(model, backend);
    if (backend === 'llamacpp' && !ggufSource) {
      cmd = _missingGgufCommand(model);
    } else {
      const repo = ggufSource?.repo || model.name;
      const includePattern = backend === 'llamacpp' ? _ggufIncludePattern(model, ggufSource) : null;
      const includeArg = includePattern ? `, allow_patterns=["${includePattern.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]` : '';
      // Reflect the server's download target in the preview (matches the real
      // download path built server-side). '' = default HF cache.
      const _dlDir = (_envState.servers.find(s => s.host === (_envState.remoteHost || '')) || {}).downloadDir || '';
      const _localDirArg = _dlDir ? `, local_dir=os.path.expanduser('${_dlDir.replace(/\/$/, '')}/${repo.split('/').pop()}')` : '';
      const _py = _isWindows() ? 'python' : 'python3';
      cmd = `${_py} -u -c "
import sys, time, os
os.environ['HF_HUB_DISABLE_PROGRESS_BARS']='0'
os.environ['TQDM_DISABLE']='0'
_lp={}
class T:
 def __init__(s,*a,**k):
  s.it=a[0] if a else k.get('iterable');s.total=k.get('total');s.desc=k.get('desc','');s.n=0;s.st=time.time();s._c=False
  if s.it is not None and s.total is None:
   try: s.total=len(s.it)
   except: pass
 def __iter__(s):
  if s.it is None: return
  for i in s.it: yield i; s.update(1)
 def __enter__(s): return s
 def __exit__(s,*a): s.close()
 def __len__(s): return s.total or 0
 def update(s,n=1):
  s.n+=n;t=s.total or 0
  if t==0: return
  now=time.time();k=id(s)
  if now-_lp.get(k,0)<0.5 and s.n<t: return
  _lp[k]=now;p=int(100*s.n/t);e=now-s.st;sp=s.n/e if e>0 else 0;d=(s.desc or '').strip()
  if t>=1073741824: ds=f'{s.n/1073741824:.2f}';ts=f'{t/1073741824:.2f}GB';ss=f'{sp/1048576:.1f}MB/s'
  elif t>=1048576: ds=f'{s.n/1048576:.1f}';ts=f'{t/1048576:.1f}MB';ss=f'{sp/1048576:.1f}MB/s'
  else: ds=str(s.n);ts=str(t);ss=f'{sp:.0f}/s'
  f=int(20*s.n/t);bar='#'*f+'-'*(20-f)
  print(f'FILE {d} [{bar}] {p}% {ds}/{ts} {ss}',flush=True)
 def set_description(s,d=None,refresh=True): s.desc=d or ''
 def set_postfix(s,*a,**k): pass
 def set_postfix_str(s,st='',refresh=True): pass
 def reset(s,total=None): s.n=0;s.total=total if total is not None else s.total;s.st=time.time()
 def refresh(s): pass
 def close(s): s._c=True
 def clear(s): pass
 def display(s,msg=None,pos=None): pass
 @property
 def format_dict(s): return {'n':s.n,'total':s.total,'elapsed':time.time()-s.st}
import tqdm;tqdm.tqdm=T
try: import tqdm.auto;tqdm.auto.tqdm=T
except: pass
try:
 import huggingface_hub.utils;huggingface_hub.utils.tqdm=T
 if hasattr(huggingface_hub.utils,'_tqdm'): huggingface_hub.utils._tqdm.tqdm=T
except: pass
from huggingface_hub import snapshot_download
repo='${repo}'
print(f'START {repo}',flush=True)
try:
 path=snapshot_download(repo${includeArg}${_localDirArg})
 print(f'DONE {path}',flush=True)
except Exception as e:
 print(f'ERROR {e}',file=sys.stderr,flush=True);sys.exit(1)
"`;
    }
  }
  const prefix = _buildEnvPrefix();
  let full = prefix ? prefix + ' ' + cmd : cmd;
  if (_envState.remoteHost) {
    full = _sshCmd(_envState.remoteHost, full, _getPort(_envState.remoteHost));
  }
  return full;
}

// ── Panel rendering helpers ──

function _getPanelFields(panel) {
  const vals = {};
  panel.querySelectorAll('.hwfit-f').forEach(el => {
    const key = el.dataset.field;
    if (!key) return;
    if (el.type === 'checkbox') {
      vals[key] = el.checked;
    } else {
      vals[key] = el.value;
    }
  });
  return vals;
}

function _syncEnvFromPanel(panel) {
  const f = _getPanelFields(panel);
  if (f.env_type !== undefined) _envState.env = f.env_type;
  if (f.env_path !== undefined) _envState.envPath = f.env_path;
  if (f.hf_token !== undefined) _envState.hfToken = f.hf_token;
  if (f.gpus !== undefined) _envState.gpus = f.gpus;
}

export function _wirePanelEvents(panel, model, backend) {
  // Populate env fields from _envState
  const envFields = {
    env_type: _envState.env || 'none',
    env_path: _envState.envPath || '',
    hf_token: _envState.hfToken || '',
    gpus: _envState.gpus || '',
  };
  for (const [field, val] of Object.entries(envFields)) {
    const el = panel.querySelector(`[data-field="${field}"]`);
    if (el && val) el.value = val;
  }

  // All inputs: update cmd preview + sync env state
  panel.querySelectorAll('.hwfit-f').forEach(input => {
    const evts = input.tagName === 'SELECT' ? ['change'] : ['input', 'change'];
    for (const evt of evts) {
      input.addEventListener(evt, () => {
        _updatePanelCmd(panel, model, backend);
        const f = input.dataset.field;
        if (f === 'env_type') { _envState.env = input.value; _persistEnvState(); }
        else if (f === 'env_path') { _envState.envPath = input.value; _persistEnvState(); }
        else if (f === 'hf_token') { _envState.hfToken = input.value; _persistEnvState(); }
        else if (f === 'gpus') { _envState.gpus = input.value; _persistEnvState(); }
      });
    }
  });

  // Download button
  const dlBtn = panel.querySelector('.hwfit-dl-btn');
  if (dlBtn) {
    dlBtn.addEventListener('click', () => {
      if (backend === 'ollama') {
        _runPanelCmd(panel, _buildDownloadCmd(model, backend), { timeout: 0 });
      } else {
        _runModelDownload(panel, model, backend);
      }
    });
  }

  // Stop button
  const stopBtn = panel.querySelector('.hwfit-stop-btn');
  if (stopBtn) {
    stopBtn.addEventListener('click', () => {
      if (panel._cookbookAbort) panel._cookbookAbort.abort();
    });
  }

  // Kill & close output button
  const killBtn = panel.querySelector('.cookbook-output-kill');
  if (killBtn) {
    killBtn.addEventListener('click', () => {
      if (panel._cookbookAbort) panel._cookbookAbort.abort();
      const outputText = panel.querySelector('.cookbook-output-pre')?.textContent || '';
      const tmuxMatch = outputText.match(/Started tmux session: (cookbook-[a-f0-9]+)/);
      if (tmuxMatch) {
        fetch('/api/shell/exec', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: `tmux kill-session -t ${tmuxMatch[1]} 2>/dev/null` }),
        }).catch(() => {});
      }
      const wrap = panel.querySelector('.cookbook-output-wrap');
      if (wrap) wrap.classList.add('hidden');
      const output = panel.querySelector('.cookbook-output-pre');
      if (output) output.textContent = '';
      _clearDiagnosis(panel);
    });
  }

  // Copy button
  const copyBtn = panel.querySelector('.hwfit-copy-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const cmd = panel.querySelector('.hwfit-panel-cmd')?.textContent || '';
      _copyText(cmd).then(() => {
        copyBtn.textContent = 'Copied';
        setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
      });
    });
  }

  // Save button
  const saveBtn = panel.querySelector('.hwfit-save-btn');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      const shortName = model.name.split('/').pop() || model.name;
      const name = prompt('Preset name:', shortName);
      if (!name) return;
      const fields = _getPanelFields(panel);
      const presets = _loadPresets();
      presets.push({ name, model: model.name, backend, fields });
      _savePresets(presets);
      uiModule.showToast('Preset saved');
    });
  }

  // Output copy button
  const outputCopyBtn = panel.querySelector('.cookbook-output-copy');
  if (outputCopyBtn) {
    outputCopyBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const text = panel.querySelector('.cookbook-output-pre')?.textContent || '';
      _copyText(text).then(() => {
        const origHTML = outputCopyBtn.innerHTML;
        outputCopyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        outputCopyBtn.classList.add('copied');
        setTimeout(() => {
          outputCopyBtn.innerHTML = origHTML;
          outputCopyBtn.classList.remove('copied');
        }, 1500);
      });
    });
  }
}

function _updatePanelCmd(panel, model, backend) {
  const pre = panel.querySelector('.hwfit-panel-cmd');
  if (!pre) return;
  const f = _getPanelFields(panel);
  _syncEnvFromPanel(panel);
  if (backend === 'llamacpp') {
    f._gguf_path = (model.gguf_sources && model.gguf_sources.length)
      ? model.gguf_sources[0].file || 'model.gguf'
      : 'model.gguf';
  }
  const cmd = _buildServeCmd(f, model.name, backend);
  const prefix = _buildEnvPrefix();
  let full = prefix ? prefix + ' ' + cmd : cmd;
  if (f.extra && f.extra.trim()) full += ' ' + f.extra.trim();
  if (_envState.remoteHost) full = _sshCmd(_envState.remoteHost, full, _getPort(_envState.remoteHost));
  pre.textContent = full;
}

// ── SSE streaming ──

export async function _runPanelCmd(panel, cmd, opts = {}) {
  const outputWrap = panel.querySelector('.cookbook-output-wrap');
  const output = panel.querySelector('.cookbook-output-pre');
  if (outputWrap) outputWrap.classList.remove('hidden');
  output.classList.remove('cookbook-output-error');
  output.textContent = '';
  _clearDiagnosis(panel);

  const controller = new AbortController();
  panel._cookbookAbort = controller;

  const serveBtn = panel.querySelector('.hwfit-serve-btn');
  const stopBtn = panel.querySelector('.hwfit-stop-btn');
  if (serveBtn) serveBtn.style.display = 'none';
  if (stopBtn) stopBtn.style.display = '';

  let fullOutput = '';
  const payload = { command: cmd };
  if (opts.timeout !== undefined) payload.timeout = opts.timeout;
  if (opts.use_pty) payload.use_pty = true;
  if (opts.use_tmux) payload.use_tmux = true;

  try {
    const res = await fetch('/api/shell/stream', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    if (!res.ok) {
      output.classList.add('cookbook-output-error');
      output.textContent = 'HTTP ' + res.status + ': ' + (await res.text());
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let exitCode = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.data !== undefined) {
              const isProgress = /^FILE .+\d+%/.test(ev.data) || /\d+%\|/.test(ev.data);
              if (isProgress && output.textContent) {
                const lines = output.textContent.split('\n');
                const lastLine = lines[lines.length - 1] || '';
                const curFile = ev.data.match(/^FILE\s+(\S+)/)?.[1];
                const prevFile = lastLine.match(/^FILE\s+(\S+)/)?.[1];
                if (curFile && prevFile && curFile === prevFile) {
                  lines[lines.length - 1] = ev.data;
                  output.textContent = lines.join('\n');
                } else {
                  output.textContent += '\n' + ev.data;
                }
              } else {
                output.textContent += (output.textContent ? '\n' : '') + ev.data;
              }
              output.scrollTop = output.scrollHeight;
              fullOutput += ev.data + '\n';
              const diag = _diagnose(fullOutput);
              if (diag) _showDiagnosis(panel, diag, fullOutput);
            }
            if (ev.exit_code !== undefined) {
              exitCode = ev.exit_code;
            }
          } catch (_) {}
        }
      }
    }

    if (!output.textContent) output.textContent = '(no output)';
    if (exitCode !== null && exitCode !== 0) {
      output.classList.add('cookbook-output-error');
      const diag = _diagnose(fullOutput);
      if (diag) _showDiagnosis(panel, diag, fullOutput);
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      output.textContent += (output.textContent ? '\n' : '') + '(stopped)';
    } else {
      output.classList.add('cookbook-output-error');
      output.textContent += (output.textContent ? '\n' : '') + 'Request failed: ' + err.message;
    }
  } finally {
    if (serveBtn) serveBtn.style.display = '';
    if (stopBtn) stopBtn.style.display = 'none';
    delete panel._cookbookAbort;
  }
}

// ── Model download (dedicated endpoint, tmux-backed) ──

export async function _runModelDownload(panel, model, backend, hostOverride) {
  const ggufSource = _ggufDownloadSource(model, backend);
  if (backend === 'llamacpp' && !ggufSource) {
    uiModule.showToast(_missingGgufMessage(model));
    return;
  }
  const repo = ggufSource?.repo || model.quant_repo || model.name;
  const include = backend === 'llamacpp' ? _ggufIncludePattern(model, ggufSource) : null;

  _syncEnvFromPanel(panel);

  // The host is whatever the caller resolved from the dropdown the user picked
  // (passed explicitly as hostOverride). We do NOT trust _envState.remoteHost
  // here: there can be more than one copy of the cookbook state in memory and
  // they disagree on the active host. The servers LIST is consistent, so we look
  // up the matching server to get its env / path / platform / port.
  let host;
  if (hostOverride !== undefined) {
    host = hostOverride || '';
  } else {
    // No explicit host passed: resolve from the visible server dropdown rather
    // than _envState.remoteHost (unreliable — multiple state copies disagree).
    const ssEl = document.getElementById('hwfit-server-select') || document.getElementById('hwfit-dl-server');
    // Dropdown values are host strings now ('local' for local); resolve by host
    // (numeric fallback for any stale value).
    const _ssv = ssEl ? ssEl.value : null;
    const _dsrv = (_ssv && _ssv !== 'local') ? (_envState.servers.find(s => s.host === _ssv) || _envState.servers[parseInt(_ssv)]) : null;
    if (_dsrv) {
      host = _dsrv.host;
    } else if (ssEl && ssEl.value === 'local') {
      host = '';
    } else {
      host = _envState.remoteHost || '';
    }
  }
  const srv = _envState.servers.find(s => s.host === host) || {};
  const env = host ? (srv.env || 'none') : (_envState.env || 'none');
  const envPath = host ? (srv.envPath || '') : (_envState.envPath || '');
  const platform = host ? (srv.platform || '') : (_envState.platform || '');
  const isWin = host ? (platform === 'windows') : _isWindows();

  const payload = { repo_id: repo };
  if (include) payload.include = include;
  // Large downloads are where hf_transfer most often dies near the end. Use the
  // plain HuggingFace downloader up front for big model files; it is slower, but
  // resumes cached partials more reliably.
  if ((model.required_gb || 0) >= 10 || backend === 'llamacpp') payload.disable_hf_transfer = true;
  if (_envState.hfToken) payload.hf_token = _envState.hfToken;
  if (host) { payload.remote_host = host; const _sp = _getPort(host); if (_sp) payload.ssh_port = _sp; }
  if (platform) payload.platform = platform;
  // If this server has a directory flagged as the download target, send it so
  // the backend downloads into <dir>/<model> instead of the default HF cache.
  if (srv.downloadDir) payload.local_dir = srv.downloadDir;
  if (isWin) {
    if (env === 'venv' && envPath) {
      payload.env_prefix = '& ' + (envPath.endsWith('\\Scripts\\Activate.ps1') ? envPath : envPath + '\\Scripts\\Activate.ps1');
    } else if (env === 'conda' && envPath) {
      payload.env_prefix = 'conda activate ' + envPath;
    }
  } else {
    if (env === 'venv' && envPath) {
      payload.env_prefix = 'source ' + (envPath.endsWith('/bin/activate') ? envPath : envPath + '/bin/activate');
    } else if (env === 'conda' && envPath) {
      payload.env_prefix = 'eval "$(conda shell.bash hook)" && conda activate ' + envPath;
    }
  }

  const shortName = (model.name || repo).split('/').pop();
  const targetHost = host || 'local';

  const tasks = _loadTasks();
  const sameDownload = (t) => {
    if (!t || t.type !== 'download') return false;
    const tRepo = t?.payload?.repo_id || t?.repo_id || t?.repo || t?.name || '';
    const tHost = t?.remoteHost || t?.payload?.remote_host || 'local';
    return String(tRepo) === String(payload.repo_id) && String(tHost || 'local') === String(targetHost);
  };
  const duplicate = tasks.find(t => sameDownload(t) && (t.status === 'running' || t.status === 'queued'));
  if (duplicate) {
    _renderRunningTab();
    uiModule.showToast(`${shortName} is already ${duplicate.status === 'queued' ? 'queued' : 'downloading'}`);
    return;
  }
  // Also catch zombie "done" tasks — the cookbook may have lost track of a
  // download (server restart, stale state) while its tmux session is still
  // alive on the host. Probe it; if alive, flip back to running + treat as
  // duplicate so we don't kick off a second concurrent download writing to
  // the same target dir.
  const zombieCandidate = tasks.find(t => sameDownload(t)
    && ['done', 'error', 'crashed', 'stopped'].includes(t.status)
    && t.sessionId && !String(t.sessionId).startsWith('queue-'));
  if (zombieCandidate) {
    try {
      const _zh = zombieCandidate.remoteHost || '';
      const _zPort = (_envState.servers || []).find(s => s.host === _zh)?.port;
      const _sshPf = _zh ? `ssh ${_zPort && _zPort !== '22' ? `-p ${_zPort} ` : ''}${_zh} '` : '';
      const _sshSf = _zh ? `'` : '';
      const _probeCmd = `${_sshPf}tmux has-session -t ${zombieCandidate.sessionId} 2>/dev/null${_sshSf}`;
      const _r = await fetch('/api/shell/exec', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: _probeCmd, timeout: 5 }),
      });
      const _d = await _r.json();
      if (_d.exit_code === 0) {
        // tmux still alive → not actually done. Revive + tell the user.
        const _fresh = _loadTasks();
        const _ft = _fresh.find(t => t.sessionId === zombieCandidate.sessionId);
        if (_ft) {
          _ft.status = 'running';
          _ft._selfHealed = true;
          _saveTasks(_fresh);
        }
        _renderRunningTab();
        uiModule.showToast(`${shortName} is still downloading (was marked finished after a restart — revived)`);
        return;
      }
    } catch { /* probe failed — fall through and let the user launch */ }
  }
  const activeOnHost = tasks.find(t => t.type === 'download' && (t.status === 'running' || t.status === 'queued') && (t.remoteHost || 'local') === targetHost);

  if (activeOnHost) {
    const queueId = `queue-${Date.now().toString(36)}`;
    const allTasks = _loadTasks();
    allTasks.push({ id: queueId, sessionId: queueId, name: shortName, type: 'download', status: 'queued', output: '', ts: Date.now(), payload, remoteHost: host });
    _saveTasks(allTasks);
    _renderRunningTab();
    uiModule.showToast(`Queued ${shortName} — waiting for current download`);
    return;
  }

  try {
    const res = await fetch('/api/model/download', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      // Errors carry actionable text (e.g. "tmux is required …"); keep them up
      // long enough to read, matching the serve path's duration (issue #1355).
      uiModule.showToast('Download failed: HTTP ' + res.status, 9000);
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      uiModule.showToast('Download failed: ' + (data.error || ''), 9000);
      return;
    }
    _addTask(data.session_id, shortName, 'download', payload);
    uiModule.showToast(`Downloading ${shortName}...`);
  } catch (e) {
    uiModule.showToast('Download failed: ' + e.message, 9000);
  }
}

// ── Init ──

export function initDownload(shared) {
  _envState = shared._envState;
  _sshCmd = shared._sshCmd;
  _getPort = shared._getPort;
  _getPlatform = shared._getPlatform;
  _isWindows = shared._isWindows;
  _buildEnvPrefix = shared._buildEnvPrefix;
  _buildServeCmd = shared._buildServeCmd;
  _detectBackend = shared._detectBackend;
  _detectToolParser = shared._detectToolParser;
  _loadPresets = shared._loadPresets;
  _savePresets = shared._savePresets;
  _copyText = shared._copyText;
  _persistEnvState = shared._persistEnvState;
  modelLogo = shared.modelLogo;
  esc = shared.esc;
  _addTask = shared._addTask;
  _renderRunningTab = shared._renderRunningTab;
  _loadTasks = shared._loadTasks;
  _saveTasks = shared._saveTasks;
}

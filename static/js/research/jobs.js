/**
 * Research job queue — add, start, monitor, cancel research jobs.
 */

let _jobs = [];
let _apiBase = '';
let _renderCb = null;
let _idCounter = 0;

// Dismissed-from-panel IDs persist across reloads so Clear actually sticks.
// (Items still live on disk and in the Library; this just hides them here.)
const _DISMISSED_KEY = 'odysseus-research-dismissed';
function _loadDismissed() {
  try {
    const raw = localStorage.getItem(_DISMISSED_KEY);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}
function _saveDismissed(set) {
  try { localStorage.setItem(_DISMISSED_KEY, JSON.stringify([...set])); } catch {}
}
function _isDismissed(id) { return _loadDismissed().has(id); }
function _markDismissed(ids) {
  const set = _loadDismissed();
  for (const id of ids) set.add(id);
  _saveDismissed(set);
}

let _activePollInterval = null;

export function init(apiBase) {
  _apiBase = apiBase;
  _reconnectActive();
  // Poll for active sessions periodically so research started elsewhere
  // (e.g. by the agent via trigger_research) gets adopted into the
  // sidebar — _reconnectActive only ran once at load before, so
  // agent-started jobs never appeared until a page reload.
  if (_activePollInterval) clearInterval(_activePollInterval);
  _activePollInterval = setInterval(() => { _reconnectActive(); }, 12000);
}

// Allow an immediate adopt when the chat stream signals a new research
// session (research_started ui_event) — faster than the 12s poll.
export function adoptSession(sessionId) {
  if (!sessionId || _jobs.some(j => j.id === sessionId)) return;
  _reconnectActive();
}

async function _reconnectActive() {
  try {
    // Reconnect to running tasks
    const res = await fetch(`${_apiBase}/api/research/active`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      for (const task of (data.active || [])) {
        if (_jobs.some(j => j.id === task.session_id)) continue;
        const job = {
          id: task.session_id, query: task.query, status: 'running',
          progress: task.progress || {},
          startedAt: task.started_at ? task.started_at * 1000 : Date.now(),
          elapsed: task.started_at ? Date.now() - task.started_at * 1000 : 0,
          result: null, sources: null, findings: null,
          errorMsg: null, avgDuration: null, modelName: null,
          settings: {}, _es: null, _timerInterval: null,
        };
        _jobs.push(job);
        _connectStream(job);
      }
    }

    // Load recent completed research from disk
    const libRes = await fetch(`${_apiBase}/api/research/library?sort=recent&limit=20`, { credentials: 'same-origin' });
    if (libRes.ok) {
      const libData = await libRes.json();
      const dismissed = _loadDismissed();
      for (const item of (libData.research || [])) {
        if (item.status !== 'done') continue;
        if (dismissed.has(item.id)) continue;
        if (_jobs.some(j => j.id === item.id)) continue;
        const elapsed = item.duration ? _parseDuration(item.duration) : 0;
        _jobs.push({
          id: item.id, query: item.query, status: 'done',
          progress: {}, startedAt: (item.started_at || 0) * 1000,
          elapsed, result: null, sources: null, findings: null,
          sourceCount: item.source_count || 0,
          category: item.category || '',
          errorMsg: null, avgDuration: null, modelName: null,
          settings: { max_rounds: item.rounds || 8 },
          _es: null, _timerInterval: null, _fromLibrary: true,
        });
      }
    }

    _notify();
  } catch {}
}

function _parseDuration(s) {
  if (!s) return 0;
  const m = s.match(/(\d+)/);
  return m ? parseInt(m[1], 10) * 1000 : 0;
}
export function setRenderCallback(cb) { _renderCb = cb; }
export function getJobs() { return _jobs; }

export function addToQueue(query, settings) {
  const job = _makeJob(query, settings);
  _jobs.push(job);
  _notify();
  return job;
}

export async function startJob(query, settings) {
  const job = addToQueue(query, settings);
  await _launchJob(job);
  return job;
}

export async function startQueued(jobId) {
  const job = _jobs.find(j => j.id === jobId);
  if (!job || job.status !== 'queued') return;
  await _launchJob(job);
}

export async function startAllQueued() {
  const queued = _jobs.filter(j => j.status === 'queued');
  await Promise.all(queued.map(j => _launchJob(j)));
}

/** Run queued jobs one at a time — waits for each to finish before launching
 *  the next. Useful when you want to avoid hammering the same model server. */
export async function startAllQueuedSequential() {
  const queued = _jobs.filter(j => j.status === 'queued');
  for (const job of queued) {
    await _launchJob(job);
    // Wait until this specific job is no longer running
    await new Promise(resolve => {
      const tick = setInterval(() => {
        if (job.status !== 'running') { clearInterval(tick); resolve(); }
      }, 1000);
    });
  }
}

export async function retryJob(jobId) {
  const job = _jobs.find(j => j.id === jobId);
  if (!job) return;
  job.status = 'queued';
  job.progress = {};
  job.errorMsg = null;
  job.result = null;
  job.sources = null;
  job.findings = null;
  job.elapsed = 0;
  job.avgDuration = null;
  _notify();
  await _launchJob(job);
}

export async function cancelJob(id) {
  const job = _jobs.find(j => j.id === id);
  if (!job) return;
  if (job.status === 'queued') { job.status = 'cancelled'; _notify(); return; }
  try { await fetch(`${_apiBase}/api/research/cancel/${id}`, { method: 'POST', credentials: 'same-origin' }); } catch {}
  _finishJob(job, 'cancelled');
}

export function removeJob(id) {
  const idx = _jobs.findIndex(j => j.id === id);
  if (idx >= 0) {
    const job = _jobs[idx];
    // Persist dismissal so it doesn't reappear from the library on reload.
    if (job.status === 'done') _markDismissed([id]);
    _jobs.splice(idx, 1);
  }
  _notify();
}

export function clearAll() {
  // Mark all completed jobs as dismissed so they don't reappear on reload.
  const doneIds = _jobs.filter(j => j.status === 'done').map(j => j.id);
  if (doneIds.length) _markDismissed(doneIds);
  for (const job of _jobs) {
    if (job._es) { job._es.close(); job._es = null; }
    if (job._timerInterval) { clearInterval(job._timerInterval); job._timerInterval = null; }
  }
  _jobs = [];
  _notify();
}

export function formatElapsed(ms) {
  if (!ms) return '0:00';
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function formatPhase(progress, maxRounds) {
  if (!progress || !progress.phase) return 'Starting...';
  const p = progress;
  const rn = p.round ? (maxRounds ? `Round ${p.round}/${maxRounds}: ` : `Round ${p.round}: `) : '';
  switch (p.phase) {
    case 'probing': return 'Probing model...';
    case 'planning': return 'Planning research strategy...';
    case 'searching': return `${rn}Searching (${p.queries || 0} queries)`;
    case 'reading': return `${rn}Reading ${p.total_sources || 0} sources`;
    case 'analyzing': return `${rn}Analyzing ${p.total_findings || 0} findings`;
    case 'writing': return `Writing report -- ${p.total_sources || 0} sources`;
    default: return p.phase;
  }
}

function _makeJob(query, settings) {
  return {
    id: `pending-${++_idCounter}`,
    query, settings, status: 'queued',
    progress: {}, startedAt: null, elapsed: 0,
    result: null, sources: null, findings: null,
    category: settings?.category || '',
    errorMsg: null, avgDuration: null,
    modelName: null, endpointName: null,
    _es: null, _timerInterval: null,
  };
}

async function _launchJob(job) {
  const body = { query: job.query, ...job.settings };
  let data;
  try {
    const res = await fetch(`${_apiBase}/api/research/start`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const txt = await res.text();
      try { job.errorMsg = JSON.parse(txt).detail || txt; } catch { job.errorMsg = txt; }
      job.status = 'error';
      _notify();
      return;
    }
    data = await res.json();
  } catch (e) {
    job.errorMsg = e.message;
    job.status = 'error';
    _notify();
    return;
  }
  job.id = data.session_id;
  job.status = 'running';
  job.startedAt = Date.now();
  _connectStream(job);
  _notify();
}

function _connectStream(job) {
  job._timerInterval = setInterval(() => {
    job.elapsed = Date.now() - job.startedAt;
    _notify();
  }, 1000);

  const es = new EventSource(`${_apiBase}/api/research/stream/${job.id}`);
  job._es = es;

  es.onmessage = (evt) => {
    try {
      const d = JSON.parse(evt.data);
      if (d.status === 'not_found') { _finishJob(job, 'error'); return; }
      job.progress = d;
      if (d.model && !job.modelName) job.modelName = d.model;
      if (d.final) {
        if (d.error) job.errorMsg = d.error;
        _finishJob(job, d.status === 'done' ? 'done' : d.status === 'cancelled' ? 'cancelled' : 'error');
        if (d.status === 'done') _fetchResult(job);
        return;
      }
      _notify();
    } catch {}
  };

  es.onerror = () => {
    es.close();
    if (job.status === 'running') setTimeout(() => _pollFallback(job), 3000);
  };
}

async function _pollFallback(job) {
  if (job.status !== 'running') return;
  try {
    const res = await fetch(`${_apiBase}/api/research/status/${job.id}`, { credentials: 'same-origin' });
    if (!res.ok) { _finishJob(job, 'error'); return; }
    const d = await res.json();
    job.progress = d.progress || {};
    if (d.avg_duration) job.avgDuration = d.avg_duration;
    if (d.status !== 'running') {
      _finishJob(job, d.status === 'done' ? 'done' : 'error');
      if (d.status === 'done') _fetchResult(job);
      return;
    }
    setTimeout(() => _pollFallback(job), 2000);
  } catch { _finishJob(job, 'error'); }
}

function _finishJob(job, status) {
  job.status = status;
  if (job._es) { job._es.close(); job._es = null; }
  if (job._timerInterval) { clearInterval(job._timerInterval); job._timerInterval = null; }
  job.elapsed = Date.now() - (job.startedAt || Date.now());
  if (status === 'done') {
    if ('Notification' in window && Notification.permission === 'granted') {
      try { new Notification('Research Complete', { body: job.query.slice(0, 80) }); } catch {}
    }
    if (_onCompleteCb) _onCompleteCb(job);
  }
  _notify();
}

let _onCompleteCb = null;
export function onComplete(cb) { _onCompleteCb = cb; }

async function _fetchResult(job) {
  try {
    const res = await fetch(`${_apiBase}/api/research/result-peek/${job.id}`, {
      method: 'POST', credentials: 'same-origin',
    });
    if (!res.ok) return;
    const d = await res.json();
    job.result = d.result;
    job.sources = d.sources;
    job.findings = d.raw_findings;
    if (d.category && !job.category) job.category = d.category;
    _notify();
  } catch {}
}

function _notify() { if (_renderCb) _renderCb(); }

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

function _normalizeQuery(query) {
  return String(query || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

function _jobFingerprint(query, settings = {}) {
  const s = settings || {};
  return JSON.stringify({
    query: _normalizeQuery(query),
    max_rounds: String(s.max_rounds || ''),
    depth: String(s.depth || ''),
    report_layout: String(s.report_layout || ''),
    search_provider: String(s.search_provider || ''),
    endpoint_id: String(s.endpoint_id || ''),
    model: String(s.model || ''),
    category: String(s.category || ''),
  });
}

function _isActiveJob(job) {
  return !!job && (job.status === 'queued' || job.status === 'running' || job._launching);
}

function _isInFlightJob(job) {
  return !!job && (job.status === 'running' || job._launching);
}

function _ensureFingerprint(job) {
  if (!job) return '';
  if (!job.fingerprint) job.fingerprint = _jobFingerprint(job.query, job.settings || {});
  return job.fingerprint;
}

function _findMatchingJob(query, settings, predicate, exceptJob = null) {
  const fp = _jobFingerprint(query, settings);
  return _jobs.find(j => j !== exceptJob && _ensureFingerprint(j) === fp && predicate(j));
}

function _removeQueuedDuplicates(keeper) {
  const fp = _ensureFingerprint(keeper);
  if (!fp) return;
  for (let i = _jobs.length - 1; i >= 0; i--) {
    const job = _jobs[i];
    if (job === keeper) continue;
    if (job.status === 'queued' && !_isInFlightJob(job) && _ensureFingerprint(job) === fp) {
      _jobs.splice(i, 1);
    }
  }
}

let _activePollInterval = null;

export function init(apiBase) {
  _apiBase = apiBase;
  _loadRecentCompleted();
  if (_activePollInterval) clearInterval(_activePollInterval);
  _activePollInterval = setInterval(() => { _loadRecentCompleted(); }, 12000);
}

export function adoptSession(sessionId) {
  if (!sessionId) return;
  _loadRecentCompleted();
}

async function _loadRecentCompleted() {
  try {
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
          errorMsg: item.error_summary || null, avgDuration: null, modelName: null,
          settings: { max_rounds: item.rounds || 8, report_layout: item.report_layout || 'auto' },
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
  const existing = _findMatchingJob(query, settings, _isActiveJob);
  if (existing) return existing;
  const job = _makeJob(query, settings);
  _jobs.push(job);
  _notify();
  return job;
}

export async function startJob(query, settings) {
  const existing = _findMatchingJob(query, settings, _isActiveJob);
  if (existing) {
    if (existing.status === 'queued' && !existing._launching) await _launchJob(existing);
    return existing;
  }
  const job = addToQueue(query, settings);
  await _launchJob(job);
  return job;
}

export async function startQueued(jobId) {
  const job = _jobs.find(j => j.id === jobId);
  if (!job || job.status !== 'queued' || job._launching) return;
  await _launchJob(job);
}

export async function startAllQueued() {
  const queued = _jobs.filter(j => j.status === 'queued' && !j._launching);
  await Promise.all(queued.map(j => _launchJob(j)));
}

/** Run queued jobs one at a time — waits for each to finish before launching
 *  the next. Useful when you want to avoid hammering the same model server. */
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

function _researchFailureMessage(text) {
  const raw = String(text || '');
  if (!raw) return '';
  const match = raw.match(/\*\*Search unavailable\*\*\s*[—-]\s*([\s\S]*?)(?:\n\s*\n|$)/);
  if (match && match[1]) {
    return `Search unavailable — ${match[1].replace(/\s+/g, ' ').trim()}`;
  }
  if (raw.includes('No information could be gathered')) {
    return 'No information could be gathered for this question.';
  }
  return '';
}

export function failureMessage(job) {
  return job?.errorMsg || _researchFailureMessage(job?.result) || '';
}

function _makeJob(query, settings) {
  return {
    id: `pending-${++_idCounter}`,
    query, settings, status: 'queued',
    fingerprint: _jobFingerprint(query, settings),
    progress: {}, startedAt: null, elapsed: 0,
    result: null, sources: null, findings: null,
    category: settings?.category || '',
    errorMsg: null, avgDuration: null,
    modelName: null, endpointName: null,
    _es: null, _timerInterval: null,
  };
}

async function _launchJob(job) {
  if (!job || job._launching) return job;
  if (job.status === 'running' && !String(job.id || '').startsWith('pending-')) return job;
  const inFlight = _findMatchingJob(job.query, job.settings || {}, _isInFlightJob, job);
  if (inFlight) {
    if (job.status === 'queued') {
      const idx = _jobs.indexOf(job);
      if (idx >= 0) _jobs.splice(idx, 1);
      _notify();
    }
    return inFlight;
  }
  job._launching = true;
  job.status = 'running';
  job.startedAt = job.startedAt || Date.now();
  job.progress = job.progress && Object.keys(job.progress).length ? job.progress : { phase: 'planning' };
  _removeQueuedDuplicates(job);
  _notify();

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
      job._launching = false;
      _notify();
      return;
    }
    data = await res.json();
  } catch (e) {
    job.errorMsg = e.message;
    job.status = 'error';
    job._launching = false;
    _notify();
    return;
  }
  job.id = data.session_id;
  job._launching = false;
  job.startedAt = job.startedAt || Date.now();
  if (!['done', 'cancelled', 'error'].includes(job.status)) {
    job.status = 'running';
    _connectStream(job);
  }
  _notify();
  return job;
}

function _connectStream(job) {
  if (!job || job._es) return;
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
  job._launching = false;
  if (job._es) { job._es.close(); job._es = null; }
  if (job._timerInterval) { clearInterval(job._timerInterval); job._timerInterval = null; }
  job.elapsed = Date.now() - (job.startedAt || Date.now());
  if (status === 'done') {
    const nativeNotified = _notifyAndroidResearchComplete(job);
    if (!nativeNotified && 'Notification' in window && Notification.permission === 'granted') {
      try { new Notification('Research Complete', { body: job.query.slice(0, 80) }); } catch {}
    }
    if (_onCompleteCb) _onCompleteCb(job);
  }
  _notify();
}

function _notifyAndroidResearchComplete(job) {
  try {
    const bridge = window.OdysseusAndroid;
    if (!bridge || typeof bridge.notifyResearchComplete !== 'function') return false;
    // Standalone Android research is notified by the native backend worker even
    // when the WebView is paused. Avoid double-alerting that local path.
    const href = String(window.location.href || '');
    if (/^https?:\/\/127\.0\.0\.1:70[1-3][0-9]\b/i.test(href)) return false;
    bridge.notifyResearchComplete(String(job?.id || ''), String(job?.query || ''));
    return true;
  } catch {
    return false;
  }
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
    job.errorMsg = d.error_summary || _researchFailureMessage(d.result) || job.errorMsg;
    if (d.category && !job.category) job.category = d.category;
    if (d.report_layout) {
      job.settings = { ...(job.settings || {}), report_layout: d.report_layout };
    }
    _notify();
  } catch {}
}

function _notify() { if (_renderCb) _renderCb(); }

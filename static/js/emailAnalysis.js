/**
 * emailAnalysis.js — Email sender analysis panel + modal.
 * Renders category breakdown as a monochrome SVG bar chart.
 * Follows the existing sidebar list-item pattern and the
 * email-library modal pattern for the full-results view.
 */

import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;

let _analysisPanel = null;
let _analysisData = null;

const _acct = () => window.__odysseusActiveEmailAccount
  ? `&account_id=${encodeURIComponent(window.__odysseusActiveEmailAccount)}`
  : '';

function _esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function _fetchStats() {
  try {
    const r = await fetch(`${API_BASE}/api/email/analysis/stats?_=${Date.now()}${_acct()}`);
    return await r.json();
  } catch { return null; }
}

async function _fetchSenders() {
  try {
    const r = await fetch(`${API_BASE}/api/email/analysis/senders?_=${Date.now()}${_acct()}`);
    return await r.json();
  } catch { return null; }
}

function _showError(msg, container) {
  const area = container || _analysisPanel?.querySelector('.email-analysis-chart');
  if (area) area.innerHTML = `<div style="padding:8px;color:var(--color-warning);font-size:11px;">${_esc(msg)}</div>`;
  const listArea = container ? null : _analysisPanel?.querySelector('.email-analysis-senders');
  if (listArea) listArea.innerHTML = '';
}

function _fmtProgress(completed, total) {
  return `${completed} of ${total}`;
}

async function _pollProgress(taskId, btn) {
  const statusEl = _analysisPanel?.querySelector('.email-analysis-status');
  let running = true;
  while (running) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const res = await fetch(`${API_BASE}/api/email/analysis/progress/${taskId}?_=${Date.now()}${_acct()}`);
      if (!res.ok) continue;
      const p = await res.json();
      if (!p.ok) continue;
      const prog = _fmtProgress(p.completed, p.total);
      if (btn) btn.textContent = prog;
      if (statusEl) { statusEl.textContent = p.current || prog; statusEl.style.color = ''; }
      if (p.status === 'done') {
        running = false;
        if (p.analyzed > 0) {
          if (statusEl) {
            const _src = p.source === 'unread' ? 'unread' : p.source === 'batch' ? 'historical' : 'recent';
            statusEl.textContent = `Analyzed ${p.analyzed} ${_src} emails`;
            setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 4000);
          }
        } else {
          if (statusEl) {
            statusEl.textContent = 'Scan done — no emails analyzed';
            statusEl.style.color = 'var(--color-muted)';
          }
        }
        await _renderPanel();
      } else if (p.status === 'error') {
        running = false;
        _showError(p.error || 'Scan failed');
      }
    } catch (e) {
      console.warn('Progress poll error', e);
    }
  }
}

let _scanning = false;

async function _runAnalysis() {
  if (_scanning) return;
  const btn = document.getElementById('email-analysis-run-btn');
  if (!btn) return;
  _scanning = true;
  btn.disabled = true;
  btn.textContent = 'scanning…';
  const statusEl = _analysisPanel?.querySelector('.email-analysis-status');
  if (statusEl) { statusEl.textContent = 'Starting scan…'; statusEl.style.color = ''; }
  const reset = () => { _scanning = false; if (btn) { btn.disabled = false; btn.textContent = 'Scan'; } };
  try {
    const res = await fetch(`${API_BASE}/api/email/analysis/run?_=${Date.now()}${_acct()}`, { method: 'POST' });
    if (!res.ok) {
      _showError(`Scan failed (HTTP ${res.status})`);
      reset();
      return;
    }
    const data = await res.json();
    if (!data.ok) {
      _showError(data.error || 'Scan failed');
      reset();
      return;
    }
    if (data.task_id) {
      await _pollProgress(data.task_id, btn);
    } else {
      if (data.analyzed > 0) {
        if (statusEl) {
          const _src = data.source === 'unread' ? 'unread' : data.source === 'batch' ? 'historical' : 'recent';
          statusEl.textContent = `Analyzed ${data.analyzed} ${_src} emails`;
          setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 4000);
        }
      } else {
        if (statusEl) {
          statusEl.textContent = 'No emails to analyze — check email settings';
          statusEl.style.color = 'var(--color-muted)';
        }
      }
      await _renderPanel();
    }
    reset();
  } catch (e) {
    _showError(e?.message || 'Request failed');
    reset();
  }
}

// ── Analysis Modal (full-size, like email library) ──

let _analysisModal = null;

function _modalChart(categories) {
  if (!categories || categories.length === 0) return '<div style="padding:12px;opacity:0.6;font-size:12px;">No data yet</div>';
  const maxCount = Math.max(...categories.map(c => c.count), 1);
  const barH = 20;
  const gap = 6;
  const totalH = categories.length * (barH + gap);
  const w = 260;

  let bars = '';
  let labels = '';
  categories.forEach((cat, i) => {
    const pct = (cat.count / maxCount) * 100;
    const y = i * (barH + gap);
    const color = cat.color || 'var(--color-muted)';
    bars += `<rect x="0" y="${y}" width="${Math.max(pct * 0.7, 2)}" height="${barH}" fill="${color}" rx="3" opacity="0.85"/>`;
    labels += `<text x="${w + 6}" y="${y + barH - 3}" fill="var(--fg)" font-size="12" font-family="Fira Code,monospace">${_esc(cat.name)} ${cat.count}</text>`;
  });

  return `<svg width="${w + 100}" height="${totalH}" viewBox="0 0 ${w + 100} ${totalH}" fill="none" style="display:block;margin:8px 0;">${bars}${labels}</svg>`;
}

function _modalSenderList(senders) {
  if (!senders || senders.length === 0) return '<div style="padding:12px;opacity:0.6;font-size:12px;">No senders analyzed</div>';
  const catMap = {};
  senders.forEach(s => {
    const cat = s.category || 'uncategorized';
    if (!catMap[cat]) catMap[cat] = [];
    catMap[cat].push(s);
  });

  let html = '';
  for (const [cat, list] of Object.entries(catMap)) {
    const color = list[0].color || 'var(--color-muted)';
    html += `<div style="margin-top:10px;"><span style="color:${color};font-size:12px;font-weight:600;text-transform:uppercase;">${_esc(cat)}</span> <span style="opacity:0.5;font-size:11px;">(${list.length})</span></div>`;
    list.forEach(s => {
      html += `<div style="display:flex;align-items:center;gap:6px;padding:4px 6px;font-size:12px;border-radius:4px;transition:background 0.15s;" onmouseover="this.style.background='var(--hover-bg)'" onmouseout="this.style.background=''">`;
      html += `<span style="width:8px;height:8px;border-radius:50%;background:${color};flex-shrink:0;"></span>`;
      html += `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(s.sender_name || s.sender)}</span>`;
      html += `<span style="font-size:11px;opacity:0.5;font-family:Fira Code,monospace;">${_esc(s.sender)}</span>`;
      html += `<span style="opacity:0.5;flex-shrink:0;font-size:11px;">${s.email_count} emails${s.spam_count ? ` · ${s.spam_count} spam` : ''}</span>`;
      html += `</div>`;
    });
  }
  return html;
}

async function _renderModal() {
  const modal = document.getElementById('email-analysis-modal');
  if (!modal) return;
  const body = modal.querySelector('.modal-body');
  if (!body) return;
  const stats = await _fetchStats();
  const senders = await _fetchSenders();
  _analysisData = { stats, senders };

  if (stats && stats.ok === false) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-warning);font-size:12px;">Stats error: ${_esc(stats.error || 'unknown')}</div>`;
    return;
  }
  if (senders && senders.ok === false) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-warning);font-size:12px;">Senders error: ${_esc(senders.error || 'unknown')}</div>`;
    return;
  }

  const totalSenders = stats?.total_senders || 0;
  const totalSpam = stats?.total_spam || 0;
  body.innerHTML = `
    <div style="display:flex;gap:12px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);margin-bottom:8px;">
      <span style="font-size:12px;opacity:0.7;">${totalSenders} senders</span>
      <span style="font-size:12px;opacity:0.7;">${totalSpam} spam</span>
    </div>
    <div class="analysis-modal-chart">${_modalChart(stats?.categories || [])}</div>
    <div class="analysis-modal-senders" style="margin-top:8px;">${_modalSenderList(senders?.senders || [])}</div>
  `;
}

export function openAnalysisModal() {
  const existing = document.getElementById('email-analysis-modal');
  if (existing) { existing.classList.remove('hidden'); return; }

  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'email-analysis-modal';
  modal.innerHTML = `
    <div class="modal-content doclib-modal-content" style="width:min(680px, 90vw);max-height:85vh;background:var(--bg);">
      <div class="modal-header">
        <h4>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">
            <path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>
          </svg>
          Email Analysis
        </h4>
        <div style="display:flex;align-items:center;gap:8px;">
          <button id="email-analysis-modal-scan-btn" type="button" style="font-size:11px;padding:3px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);cursor:pointer;">Scan</button>
          <button id="email-analysis-modal-history-btn" type="button" style="font-size:11px;padding:3px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);cursor:pointer;">History</button>
          <button class="close-btn" id="email-analysis-modal-close">&#10006;</button>
        </div>
      </div>
      <div id="batch-options" style="display:none;padding:8px 16px;border-bottom:1px solid var(--border);font-size:12px;background:var(--bg-alt, transparent);">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <span style="opacity:0.7;">Scan read emails (history):</span>
          <label style="opacity:0.6;font-size:11px;">Max <input type="number" id="batch-limit-input" value="500" min="1" max="5000" style="width:70px;font-size:11px;padding:2px 4px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--fg);"></label>
          <label style="opacity:0.6;font-size:11px;">Since <input type="date" id="batch-date-input" style="font-size:11px;padding:2px 4px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--fg);"></label>
          <button id="start-batch-btn" type="button" style="font-size:11px;padding:3px 10px;border:1px solid var(--border);border-radius:4px;background:var(--color-save-green);color:#fff;cursor:pointer;">Start Batch</button>
        </div>
      </div>
      <div class="modal-body" style="overflow-y:auto;padding:0 16px 16px;"></div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.style.display = 'block';
  modal.style.cssText += 'pointer-events:none;background:transparent;';

  const content = modal.querySelector('.modal-content');
  const body = modal.querySelector('.modal-body');
  // Debug log — appended to body when scan runs
  const debugLog = document.createElement('div');
  debugLog.id = 'analysis-debug-log';
  debugLog.style.cssText = 'margin-top:8px;padding:6px;border-top:1px solid var(--border);font-size:10px;font-family:Fira Code,monospace;max-height:120px;overflow-y:auto;opacity:0.7;';
  function _debug(msg) {
    if (!body) return;
    if (!body.contains(debugLog)) body.appendChild(debugLog);
    const line = document.createElement('div');
    line.textContent = `${new Date().toLocaleTimeString()} ${msg}`;
    debugLog.appendChild(line);
    debugLog.scrollTop = debugLog.scrollHeight;
  }

  if (content) {
    content.style.position = 'fixed';
    content.style.pointerEvents = 'auto';
    requestAnimationFrame(() => {
      const w = content.offsetWidth;
      const refH = Math.min(window.innerHeight * 0.85, 700);
      content.style.left = Math.max(20, (window.innerWidth - w) / 2) + 'px';
      content.style.top = Math.max(20, (window.innerHeight - refH) / 2) + 'px';
      content.style.transform = 'none';
    });
  }

  const _escHandler = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeAnalysisModal();
    }
  };
  document.addEventListener('keydown', _escHandler, true);
  modal._escHandler = _escHandler;

  Modals.register('email-analysis-modal', {
    label: 'Analysis',
    icon: 'M18 20V10 M12 20V4 M6 20v-6',
    closeFn: () => {
      const m = document.getElementById('email-analysis-modal');
      if (m) m.classList.add('hidden');
    },
    restoreFn: () => {},
  });

  document.getElementById('email-analysis-modal-close').addEventListener('click', closeAnalysisModal);
  document.getElementById('email-analysis-modal-scan-btn').addEventListener('click', async () => {
    const opts = document.getElementById('batch-options');
    if (opts) opts.style.display = 'none';
    const btn = document.getElementById('email-analysis-modal-scan-btn');
    const chartArea = body?.querySelector('.analysis-modal-chart') || body;
    if (btn) { btn.textContent = 'scanning…'; btn.disabled = true; }
    const reset = () => { if (btn) { btn.disabled = false; btn.textContent = 'Scan'; } };
    if (body && !body.contains(debugLog)) body.appendChild(debugLog);
    _debug('Starting scan...');
    try {
      const res = await fetch(`${API_BASE}/api/email/analysis/run?_=${Date.now()}${_acct()}`, { method: 'POST' });
      if (!res.ok) { _showError(`HTTP ${res.status}`, chartArea); _debug(`HTTP ${res.status}`); reset(); return; }
      const data = await res.json();
      if (!data.ok) { _showError(data.error || 'Scan failed', chartArea); _debug(data.error || 'Scan failed'); reset(); return; }
      if (data.task_id) {
        _debug(`Task ${data.task_id} launched — ${data.total} email(s) to scan`);
        if (body) body.innerHTML = `<div style="padding:16px;opacity:0.6;font-size:12px;">Scanning ${data.total} emails…</div>`;
        let pollCount = 0;
        const MAX_POLLS = 120; // 120 × 1.5s = 3 min timeout
        const poll = setInterval(async () => {
          pollCount++;
          try {
            const pr = await fetch(`${API_BASE}/api/email/analysis/progress/${data.task_id}?_=${Date.now()}${_acct()}`);
            if (!pr.ok) { _debug(`Progress HTTP ${pr.status}`); if (pollCount > MAX_POLLS) { clearInterval(poll); _showError('Scan timed out', chartArea); reset(); } return; }
            const p = await pr.json();
            if (!p.ok) { _debug(`Progress error: ${p.error}`); if (pollCount > MAX_POLLS) { clearInterval(poll); _showError(p.error, chartArea); reset(); } return; }
            if (p.status === 'running') {
              if (btn) btn.textContent = `${p.completed} of ${p.total}`;
              if (body) body.innerHTML = `<div style="padding:16px;opacity:0.6;font-size:12px;">${_esc(p.current)}</div>`;
              _debug(`${p.completed}/${p.total} — ${p.current}`);
            } else if (p.status === 'done') {
              clearInterval(poll);
              _debug(`Done — ${p.analyzed} analyzed`);
              _debug(`Raw: ${JSON.stringify(p).slice(0,500)}`);
              if (p.email_logs && p.email_logs.length) {
                p.email_logs.forEach(l => _debug(`  ${l}`));
              } else {
                _debug(`  (no email logs)`);
              }
              if (p.logs && p.logs.length) {
                p.logs.forEach(l => _debug(`  ${l}`));
              }
              reset();
              if (p.analyzed === 0) {
                _showError(`Scan done — ${p.analyzed} of ${p.total} analyzed`, chartArea);
              } else {
                await _renderModal();
              }
            } else if (p.status === 'error') {
              clearInterval(poll);
              _debug(`Error: ${p.error}`);
              reset();
              _showError(p.error || 'Scan failed', chartArea);
            }
          } catch (e) {
            _debug(`Poll error: ${e?.message}`);
            if (pollCount > MAX_POLLS) { clearInterval(poll); _showError('Scan timed out', chartArea); reset(); }
          }
        }, 1500);
      } else {
        _debug(`No task — ${data.analyzed} analyzed`);
        reset();
        if (data.analyzed === 0) {
          _showError('No emails to analyze', chartArea);
        } else {
          await _renderModal();
        }
      }
    } catch (e) {
      _showError(e?.message || 'Request failed', chartArea);
      _debug(e?.message || 'Request failed');
      reset();
    }
  });

  // History button toggles batch options panel
  document.getElementById('email-analysis-modal-history-btn').addEventListener('click', () => {
    const opts = document.getElementById('batch-options');
    if (opts) opts.style.display = opts.style.display === 'none' ? '' : 'none';
  });

  // Start Batch button
  document.getElementById('start-batch-btn').addEventListener('click', async () => {
    const btn = document.getElementById('start-batch-btn');
    const body = document.getElementById('email-analysis-modal')?.querySelector('.modal-body');
    const chartArea = body?.querySelector('.analysis-modal-chart') || body;
    if (!btn || !body) return;
    const limit = parseInt(document.getElementById('batch-limit-input')?.value || '500', 10);
    const dateVal = document.getElementById('batch-date-input')?.value || '';
    btn.textContent = 'starting…';
    btn.disabled = true;
    const reset = () => { btn.disabled = false; btn.textContent = 'Start Batch'; };
    _debug('Starting batch scan...');
    try {
      let url = `${API_BASE}/api/email/analysis/run?_=${Date.now()}${_acct()}&mode=batch&batch_limit=${limit}`;
      if (dateVal) url += `&batch_date=${encodeURIComponent(dateVal)}`;
      const res = await fetch(url, { method: 'POST' });
      if (!res.ok) { _showError(`HTTP ${res.status}`, chartArea); _debug(`HTTP ${res.status}`); reset(); return; }
      const data = await res.json();
      if (!data.ok) { _showError(data.error || 'Batch scan failed', chartArea); _debug(data.error || 'Batch scan failed'); reset(); return; }
      if (data.task_id) {
        _debug(`Batch task ${data.task_id} — ${data.total} email(s)`);
        if (body) body.innerHTML = `<div style="padding:16px;opacity:0.6;font-size:12px;">Batch scanning ${data.total} emails…</div>`;
        let pollCount = 0;
        const MAX_POLLS = 600;
        const poll = setInterval(async () => {
          pollCount++;
          try {
            const pr = await fetch(`${API_BASE}/api/email/analysis/progress/${data.task_id}?_=${Date.now()}${_acct()}`);
            if (!pr.ok) { _debug(`Progress HTTP ${pr.status}`); if (pollCount > MAX_POLLS) { clearInterval(poll); _showError('Batch scan timed out', chartArea); reset(); } return; }
            const p = await pr.json();
            if (!p.ok) { _debug(`Progress error: ${p.error}`); if (pollCount > MAX_POLLS) { clearInterval(poll); _showError(p.error, chartArea); reset(); } return; }
            if (p.status === 'running') {
              btn.textContent = `${p.completed} of ${p.total}`;
              if (body) body.innerHTML = `<div style="padding:16px;opacity:0.6;font-size:12px;">${_esc(p.current)}</div>`;
              _debug(`${p.completed}/${p.total} — ${p.current}`);
            } else if (p.status === 'done') {
              clearInterval(poll);
              _debug(`Batch done — ${p.analyzed} analyzed`);
              _debug(`Raw: ${JSON.stringify(p).slice(0,500)}`);
              if (p.email_logs && p.email_logs.length) p.email_logs.forEach(l => _debug(`  ${l}`));
              if (p.logs && p.logs.length) p.logs.forEach(l => _debug(`  ${l}`));
              reset();
              if (p.analyzed === 0) {
                _showError(`Batch done — ${p.analyzed} of ${p.total} analyzed`, chartArea);
              } else {
                await _renderModal();
              }
            } else if (p.status === 'error') {
              clearInterval(poll);
              _debug(`Error: ${p.error}`);
              reset();
              _showError(p.error || 'Batch scan failed', chartArea);
            }
          } catch (e) {
            _debug(`Poll error: ${e?.message}`);
            if (pollCount > MAX_POLLS) { clearInterval(poll); _showError('Batch scan timed out', chartArea); reset(); }
          }
        }, 1500);
      } else {
        _debug(`No task — ${data.analyzed} analyzed`);
        reset();
        if (data.analyzed === 0) {
          _showError('No emails to analyze', chartArea);
        } else {
          await _renderModal();
        }
      }
    } catch (e) {
      _showError(e?.message || 'Request failed', chartArea);
      _debug(e?.message || 'Request failed');
      reset();
    }
  });

  makeWindowDraggable(modal, {
    content,
    header: content?.querySelector('.modal-header'),
    fsClass: 'analysis-modal-fullscreen',
    enableLeftDock: true,
  });

  _renderModal();
}

export function closeAnalysisModal() {
  const modal = document.getElementById('email-analysis-modal');
  if (modal) {
    if (modal._escHandler) {
      document.removeEventListener('keydown', modal._escHandler, true);
    }
    modal.remove();
  }
  _analysisModal = null;
  try { Modals.close('email-analysis-modal'); } catch (_) {}
}

function _svgBarChart(categories) {
  if (!categories || categories.length === 0) return '<div style="padding:8px;opacity:0.6;font-size:11px;">No data yet</div>';
  const maxCount = Math.max(...categories.map(c => c.count), 1);
  const barH = 14;
  const gap = 4;
  const totalH = categories.length * (barH + gap);
  const w = 180;

  let bars = '';
  let labels = '';
  categories.forEach((cat, i) => {
    const pct = (cat.count / maxCount) * 100;
    const y = i * (barH + gap);
    const color = cat.color || 'var(--color-muted)';
    bars += `<rect x="0" y="${y}" width="${Math.max(pct * 0.7, 2)}" height="${barH}" fill="${color}" rx="2" opacity="0.85"/>`;
    labels += `<text x="${w + 4}" y="${y + barH - 2}" fill="var(--fg)" font-size="10" font-family="Fira Code,monospace">${_esc(cat.name)} ${cat.count}</text>`;
  });

  return `<svg width="${w + 80}" height="${totalH}" viewBox="0 0 ${w + 80} ${totalH}" fill="none" style="display:block;margin:4px 0;">${bars}${labels}</svg>`;
}

function _senderList(senders) {
  if (!senders || senders.length === 0) return '<div style="padding:8px;opacity:0.6;font-size:11px;">No senders analyzed</div>';
  const catMap = {};
  senders.forEach(s => {
    const cat = s.category || 'uncategorized';
    if (!catMap[cat]) catMap[cat] = [];
    catMap[cat].push(s);
  });

  let html = '';
  for (const [cat, list] of Object.entries(catMap)) {
    const color = list[0].color || 'var(--color-muted)';
    html += `<div style="margin-top:6px;"><span style="color:${color};font-size:10px;font-weight:600;text-transform:uppercase;">${_esc(cat)}</span> <span style="opacity:0.5;font-size:10px;">(${list.length})</span></div>`;
    list.slice(0, 10).forEach(s => {
      html += `<div style="display:flex;align-items:center;gap:4px;padding:2px 4px;font-size:10px;border-radius:3px;">`;
      html += `<span style="width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;"></span>`;
      html += `<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(s.sender_name || s.sender)}</span>`;
      html += `<span style="opacity:0.5;flex-shrink:0;">${s.email_count}</span>`;
      html += `</div>`;
    });
    if (list.length > 10) {
      html += `<div style="opacity:0.4;font-size:9px;padding-left:10px;">+${list.length - 10} more</div>`;
    }
  }
  return html;
}

async function _renderPanel() {
  if (!_analysisPanel) return;
  const stats = await _fetchStats();
  const senders = await _fetchSenders();
  _analysisData = { stats, senders };

  if (stats && stats.ok === false) {
    _showError(stats.error || 'Stats error');
    return;
  }
  if (senders && senders.ok === false) {
    _showError(senders.error || 'Senders error');
    return;
  }

  const chartArea = _analysisPanel.querySelector('.email-analysis-chart');
  const listArea = _analysisPanel.querySelector('.email-analysis-senders');
  if (chartArea) {
    chartArea.innerHTML = _svgBarChart(stats?.categories || []);
  }
  if (listArea) {
    listArea.innerHTML = _senderList(senders?.senders || []);
  }
  const countEl = _analysisPanel.querySelector('.email-analysis-count');
  if (countEl && stats) {
    countEl.textContent = `${stats.total_senders || 0} senders`;
  }
  const spamEl = _analysisPanel.querySelector('.email-analysis-spam');
  if (spamEl && stats) {
    spamEl.textContent = `${stats.total_spam || 0} spam`;
  }
}

function _togglePanel() {
  if (!_analysisPanel) return;
  const isHidden = _analysisPanel.style.display === 'none';
  _analysisPanel.style.display = isHidden ? '' : 'none';
  if (isHidden) _renderPanel();
}

export function init() {
  const emailSection = document.getElementById('email-section');
  if (!emailSection) return;

  // Analysis list item — click opens the full modal
  const item = document.createElement('div');
  item.className = 'list-item';
  item.id = 'email-analysis-toggle';
  item.style.cssText = 'cursor:pointer;font-size:11px;';
  item.innerHTML = `
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;">
      <path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>
    </svg>
    <span class="grow">Analysis</span>
    <span class="email-analysis-count" style="opacity:0.4;font-size:10px;"></span>
  `;
  item.addEventListener('click', openAnalysisModal);
  emailSection.appendChild(item);

  // Collapsible quick-preview panel (scan + mini chart)
  _analysisPanel = document.createElement('div');
  _analysisPanel.id = 'email-analysis-panel';
  _analysisPanel.style.cssText = 'display:none;padding:4px 8px 4px 22px;border-top:1px solid var(--border);margin-top:2px;';
  _analysisPanel.innerHTML = `
    <div style="display:flex;gap:6px;align-items:center;margin:4px 0;">
      <button id="email-analysis-run-btn" type="button" style="font-size:10px;padding:2px 8px;border:1px solid var(--border);border-radius:3px;background:var(--bg);color:var(--fg);cursor:pointer;">Scan</button>
      <span class="email-analysis-spam" style="font-size:10px;opacity:0.5;"></span>
      <span class="email-analysis-status" style="font-size:10px;opacity:0.6;margin-left:auto;"></span>
    </div>
    <div class="email-analysis-chart"></div>
    <div class="email-analysis-senders" style="margin-top:4px;max-height:240px;overflow-y:auto;"></div>
    <div style="text-align:right;margin-top:4px;">
      <button type="button" id="email-analysis-open-modal-btn" style="font-size:9px;padding:1px 6px;border:none;background:transparent;color:var(--color-link);cursor:pointer;opacity:0.7;">Full Results →</button>
    </div>
  `;

  _analysisPanel.querySelector('#email-analysis-run-btn').addEventListener('click', _runAnalysis);
  _analysisPanel.querySelector('#email-analysis-open-modal-btn').addEventListener('click', openAnalysisModal);

  emailSection.appendChild(_analysisPanel);
}

export function open() {
  openAnalysisModal();
}

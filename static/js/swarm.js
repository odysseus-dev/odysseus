/**
 * static/js/swarm.js
 * Visualises Swarm Intelligence execution trees in the chat interface.
 */

import uiModule from './ui.js';
import markdownModule from './markdown.js';

function esc(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

function md(text) {
  const raw = text == null ? '' : String(text);
  try {
    if (markdownModule.process) return markdownModule.process(raw);
    if (markdownModule.mdToHtml) return markdownModule.mdToHtml(raw);
  } catch (_) {}
  return esc(raw).replace(/\n/g, '<br>');
}

function slugFromEvent(data) {
  return data.worker_slug || data.worker || data.role_slug || data.role || '';
}

function shortToolLabel(data) {
  return data.tool || data.name || data.command || data.type || 'tool';
}

export const swarmModule = {
  activeSwarms: new Map(),

  handleEvent(data, messageHolder) {
    if (!data || !messageHolder) return;

    if (data.type === 'swarm_start') {
      this._initSwarmUI(data, messageHolder);
    } else if (data.type === 'swarm_plan') {
      this._updatePlan(data);
    } else if (data.type === 'swarm_merge') {
      this._updateStatus(data.execution_id, 'Merging results...');
    } else if (data.type === 'swarm_done') {
      this._finalizeSwarm(data);
    } else if (data.type === 'swarm_error') {
      this._updateStatus(data.execution_id, `Error: ${data.error}`, true);
    } else if (['worker_start', 'worker_delta', 'worker_done', 'worker_failed'].includes(data.type)) {
      this._handleWorkerEvent(data);
    } else if (data.worker_slug) {
      this._handleWorkerToolEvent(data);
    }
  },

  _initSwarmUI(data, holder) {
    const body = holder.querySelector('.body');
    if (!body) return;

    const container = document.createElement('div');
    container.className = 'swarm-execution-container';
    container.innerHTML = `
      <div class="swarm-header">
        <span class="swarm-icon" aria-hidden="true">SW</span>
        <div class="swarm-title">
          <strong>${esc(data.swarm || 'Swarm')}</strong>
          <span>${esc(data.domain || 'general')} / ${esc(data.master || 'Coordinator')}</span>
        </div>
        <span class="swarm-status">Planning...</span>
      </div>
      <div class="swarm-tree"></div>
    `;

    body.appendChild(container);

    this.activeSwarms.set(data.execution_id, {
      container,
      treeEl: container.querySelector('.swarm-tree'),
      statusEl: container.querySelector('.swarm-status'),
      workers: new Map(),
    });

    uiModule.scrollHistory();
  },

  _runFor(data) {
    if (data.execution_id && this.activeSwarms.has(data.execution_id)) {
      return this.activeSwarms.get(data.execution_id);
    }
    return this.activeSwarms.values().next().value || null;
  },

  _updatePlan(data) {
    const run = this._runFor(data);
    if (!run) return;

    run.statusEl.textContent = 'Executing';

    const reasoning = document.createElement('details');
    reasoning.className = 'swarm-reasoning';
    reasoning.open = true;
    reasoning.innerHTML = `
      <summary>Master plan</summary>
      <div class="swarm-detail-body">${md(data.reasoning || 'No reasoning provided.')}</div>
    `;
    run.treeEl.appendChild(reasoning);

    const taskList = document.createElement('ul');
    taskList.className = 'swarm-task-list';

    (data.tasks || []).forEach((task) => {
      const slug = task.role_slug || task.worker || '';
      const li = document.createElement('li');
      li.className = 'swarm-task pending';
      li.dataset.worker = slug;
      li.dataset.taskId = task.id || '';
      li.innerHTML = `
        <div class="swarm-task-row">
          <span class="worker-badge">${esc(slug)}</span>
          <span class="worker-prompt">${esc(task.prompt || '')}</span>
          <span class="worker-status">pending</span>
        </div>
        <details class="swarm-task-details">
          <summary>Details</summary>
          <div class="swarm-log"></div>
          <div class="swarm-tools"></div>
          <div class="swarm-output"></div>
        </details>
      `;
      taskList.appendChild(li);
      run.workers.set(slug, li);
    });

    if (Array.isArray(data.skipped) && data.skipped.length) {
      const skipped = document.createElement('div');
      skipped.className = 'swarm-skipped';
      skipped.innerHTML = data.skipped.map(item =>
        `<span title="${esc(item.reason || '')}">${esc(item.worker || '')}</span>`
      ).join('');
      run.treeEl.appendChild(skipped);
    }

    run.treeEl.appendChild(taskList);
    uiModule.scrollHistory();
  },

  _ensureWorker(run, slug) {
    if (!run || !slug) return null;
    let workerEl = run.workers.get(slug);
    if (workerEl) return workerEl;

    const taskList = run.treeEl.querySelector('.swarm-task-list') || (() => {
      const ul = document.createElement('ul');
      ul.className = 'swarm-task-list';
      run.treeEl.appendChild(ul);
      return ul;
    })();

    workerEl = document.createElement('li');
    workerEl.className = 'swarm-task pending';
    workerEl.dataset.worker = slug;
    workerEl.innerHTML = `
      <div class="swarm-task-row">
        <span class="worker-badge">${esc(slug)}</span>
        <span class="worker-prompt"></span>
        <span class="worker-status">pending</span>
      </div>
      <details class="swarm-task-details" open>
        <summary>Details</summary>
        <div class="swarm-log"></div>
        <div class="swarm-tools"></div>
        <div class="swarm-output"></div>
      </details>
    `;
    taskList.appendChild(workerEl);
    run.workers.set(slug, workerEl);
    return workerEl;
  },

  _setWorkerStatus(workerEl, status) {
    if (!workerEl) return;
    workerEl.classList.remove('pending', 'running', 'done', 'failed', 'skipped');
    workerEl.classList.add(status);
    const statusEl = workerEl.querySelector('.worker-status');
    if (statusEl) statusEl.textContent = status;
  },

  _appendLog(workerEl, text, className = '') {
    const log = workerEl?.querySelector('.swarm-log');
    if (!log || !text) return;
    const line = document.createElement('div');
    line.className = `swarm-log-line ${className}`.trim();
    line.textContent = String(text);
    log.appendChild(line);
    const details = workerEl.querySelector('.swarm-task-details');
    if (details && className !== 'quiet') details.open = true;
  },

  _handleWorkerEvent(data) {
    const run = this._runFor(data);
    if (!run) return;
    const slug = slugFromEvent(data);
    const workerEl = this._ensureWorker(run, slug);
    if (!workerEl) return;

    if (data.type === 'worker_start') {
      this._setWorkerStatus(workerEl, 'running');
      const prompt = workerEl.querySelector('.worker-prompt');
      if (prompt && data.task) prompt.textContent = data.task;
      this._appendLog(workerEl, `${data.worker || slug} started`, 'quiet');
    } else if (data.type === 'worker_delta') {
      this._setWorkerStatus(workerEl, 'running');
      this._appendLog(workerEl, data.delta || data.content || '');
    } else if (data.type === 'worker_done') {
      this._setWorkerStatus(workerEl, 'done');
      const output = workerEl.querySelector('.swarm-output');
      const result = data.result || data.output || data.text || '';
      if (output && result) {
        output.innerHTML = `<div class="swarm-output-label">Output preview</div><div>${md(result.slice(0, 3000))}</div>`;
      }
      if (data.metrics) this._appendMetricPills(workerEl, data.metrics);
    } else if (data.type === 'worker_failed') {
      this._setWorkerStatus(workerEl, 'failed');
      this._appendLog(workerEl, data.error || 'Worker failed', 'error');
    }

    uiModule.scrollHistory();
  },

  _handleWorkerToolEvent(data) {
    const run = this._runFor(data);
    if (!run) return;
    const workerEl = this._ensureWorker(run, slugFromEvent(data));
    const tools = workerEl?.querySelector('.swarm-tools');
    if (!tools) return;

    const pill = document.createElement('span');
    pill.className = `swarm-tool-pill ${data.type || ''}`.trim();
    pill.textContent = `${shortToolLabel(data)} ${data.status || data.phase || ''}`.trim();
    if (data.error) pill.title = data.error;
    tools.appendChild(pill);

    const preview = data.output || data.content || data.message || '';
    if (preview) this._appendLog(workerEl, `${shortToolLabel(data)}: ${String(preview).slice(0, 500)}`, 'tool');
    uiModule.scrollHistory();
  },

  _appendMetricPills(workerEl, metrics) {
    const tools = workerEl?.querySelector('.swarm-tools');
    if (!tools || !metrics) return;
    Object.entries(metrics).slice(0, 4).forEach(([key, value]) => {
      if (value == null || value === '') return;
      const pill = document.createElement('span');
      pill.className = 'swarm-tool-pill metric';
      pill.textContent = `${key}: ${value}`;
      tools.appendChild(pill);
    });
  },

  _updateStatus(executionId, statusText, isError = false) {
    const run = this.activeSwarms.get(executionId);
    if (!run) return;
    run.statusEl.textContent = statusText;
    run.statusEl.classList.toggle('error', !!isError);
  },

  _finalizeSwarm(data) {
    const run = this._runFor(data);
    if (!run) return;

    run.statusEl.textContent = 'Completed';
    run.statusEl.classList.add('success');

    const metrics = document.createElement('div');
    metrics.className = 'swarm-metrics';
    metrics.innerHTML = `
      <span>${((data.duration_ms || 0) / 1000).toFixed(2)}s</span>
      <span>${data.total_tokens || 0} tokens</span>
      <span>${data.workers_activated || 0} active</span>
    `;
    run.container.querySelector('.swarm-header')?.appendChild(metrics);

    this.activeSwarms.delete(data.execution_id);
    uiModule.scrollHistory();
  },

  updateWorkerStatus(workerSlug, status, resultText = null) {
    for (const run of this.activeSwarms.values()) {
      const workerEl = this._ensureWorker(run, workerSlug);
      if (workerEl) {
        this._setWorkerStatus(workerEl, status);
        if (resultText) this._appendLog(workerEl, resultText);
        break;
      }
    }
  },
};

export default swarmModule;

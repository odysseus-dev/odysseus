// static/js/usage.js
// Usage dashboard: token analytics by day.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { providerLogo } from './providers.js';

const MODAL_ID = 'usage-modal';
const ALL_USERS = '__all__';
const OTHER_MODELS = '__other__';
const UNKNOWN_MODEL = 'unknown';
const RANGE_KEY = 'ody-usage-range';
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const AVG_COLORS = ['#f6c177', '#9ccfd8', '#c4a7e7', '#eb6f92', '#31748f', '#ea9a97'];
const MODEL_COLORS = ['#7aa2f7', '#f6c177', '#9ccfd8', '#c4a7e7', '#eb6f92'];

let modalEl = null;
let chartResizeObserver = null;
let chartResizeRaf = 0;
let lastChartRenderWidth = 0;
let lastChartRenderHeight = 0;
let documentClickHandler = null;
let state = {
  tab: 'daily',
  range: 'last30',
  start: '',
  end: '',
  user: ALL_USERS,
  models: [],
  data: null,
  loading: false,
  showInput: true,
  showOutput: true,
  showMovingAverage: false,
  splitByModel: false,
};

let picker = {
  open: false,
  month: null,
  draftStart: '',
  draftEnd: '',
};

let modelPicker = {
  open: false,
};

function esc(s) {
  return uiModule && uiModule.esc ? uiModule.esc(String(s ?? '')) : String(s ?? '');
}

function pad(n) {
  return String(n).padStart(2, '0');
}

function ymd(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function parseYmd(s) {
  const [y, m, d] = String(s || '').split('-').map(Number);
  if (!y || !m || !d) return new Date();
  return new Date(y, m - 1, d);
}

function addDays(d, days) {
  const out = new Date(d);
  out.setDate(out.getDate() + days);
  return out;
}

function addMonths(d, months) {
  return new Date(d.getFullYear(), d.getMonth() + months, 1);
}

function rangeFor(key) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let start = today;
  if (key === 'week') {
    start = addDays(today, -today.getDay());
  } else if (key === 'month') {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  } else if (key === 'year') {
    start = new Date(today.getFullYear(), 0, 1);
  } else if (key === 'last7') {
    start = addDays(today, -6);
  } else if (key === 'last14') {
    start = addDays(today, -13);
  } else if (key === 'last30') {
    start = addDays(today, -29);
  }
  return { start: ymd(start), end: ymd(today) };
}

function fmtNum(n) {
  return Number(n || 0).toLocaleString();
}

function fmtCompact(n) {
  n = Number(n || 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`;
  return String(n);
}

function fmtDate(value) {
  return parseYmd(value).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}

function rangeLabel(start, end) {
  if (!start || !end) return 'Choose date range';
  return `${fmtDate(start)} - ${fmtDate(end)}`;
}

function sortedRange(start, end) {
  if (!start) return { start: '', end: '' };
  if (!end) return { start, end: start };
  return start <= end ? { start, end } : { start: end, end: start };
}

function movingAverage(values, windowSize = 7) {
  return values.map((_, index) => {
    const start = Math.max(0, index - windowSize + 1);
    const window = values.slice(start, index + 1);
    const total = window.reduce((sum, value) => sum + Number(value || 0), 0);
    return total / window.length;
  });
}

function movingAverageSeries(data) {
  if (!state.showMovingAverage) return [];
  const daily = effectiveUsage(data).daily || [];
  if (!daily.length) return [];
  const visibleValues = daily.map(day => (
    (state.showInput ? day.input_tokens || 0 : 0)
    + (state.showOutput ? day.output_tokens || 0 : 0)
  ));
  const averages = movingAverage(visibleValues);
  return [{
    user: 'Visible usage',
    color: AVG_COLORS[0],
    points: averages.map((value, pointIndex) => ({
      date: daily[pointIndex]?.date || '',
      value,
    })),
  }];
}

function smoothPath(points) {
  if (!points.length) return '';
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  const d = [`M ${points[0].x} ${points[0].y}`];
  for (let i = 0; i < points.length - 1; i++) {
    const previous = points[i - 1] || points[i];
    const current = points[i];
    const next = points[i + 1];
    const following = points[i + 2] || next;
    const cp1x = current.x + (next.x - previous.x) / 6;
    const cp1y = current.y + (next.y - previous.y) / 6;
    const cp2x = next.x - (following.x - current.x) / 6;
    const cp2y = next.y - (following.y - current.y) / 6;
    d.push(`C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${next.x} ${next.y}`);
  }
  return d.join(' ');
}

function closeUsage() {
  if (chartResizeObserver) chartResizeObserver.disconnect();
  chartResizeObserver = null;
  if (chartResizeRaf) cancelAnimationFrame(chartResizeRaf);
  chartResizeRaf = 0;
  lastChartRenderWidth = 0;
  picker.open = false;
  modelPicker.open = false;
  document.getElementById('usage-range-popover')?.remove();
  document.getElementById('usage-model-popover')?.remove();
  if (documentClickHandler) {
    document.removeEventListener('click', documentClickHandler);
    documentClickHandler = null;
  }
  if (modalEl) modalEl.remove();
  modalEl = null;
  Modals.unregister(MODAL_ID);
}

function minimizeUsage() {
  closeRangePicker();
  closeModelPicker();
  Modals.minimize(MODAL_ID);
}

function restoreUsage() {
  if (!modalEl) return;
  modalEl.classList.remove('hidden', 'modal-minimized');
}

function ensureDefaults() {
  if (state.start && state.end) return;
  const saved = (() => {
    try { return JSON.parse(localStorage.getItem(RANGE_KEY) || '{}'); } catch (_) { return {}; }
  })();
  if (saved.user) state.user = saved.user;
  state = { ...state, range: 'last30', ...rangeFor('last30') };
}

function saveRange() {
  try {
    localStorage.setItem(RANGE_KEY, JSON.stringify({ user: state.user }));
  } catch (_) {}
}

function modelDisplayName(model) {
  if (!model || model === UNKNOWN_MODEL) return 'Unknown model';
  return String(model);
}

function truncateModelLabel(label, max = 20) {
  const text = String(label || '');
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function modelIcon(model) {
  const logo = providerLogo(model);
  return logo ? `<span class="provider-logo usage-provider-logo">${logo}</span>` : '';
}

function emptyTotals(messageCount = 0) {
  return {
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    message_count: messageCount,
  };
}

function modelDailyLookup(data) {
  const lookup = new Map();
  (data?.daily_by_model || []).forEach(series => {
    const byDate = new Map();
    (series.daily || []).forEach(day => {
      byDate.set(day.date, day);
    });
    lookup.set(series.model, byDate);
  });
  return lookup;
}

function availableModelIds(data = state.data) {
  return new Set((data?.models || []).map(model => model.model));
}

function pruneSelectedModels(data = state.data) {
  if (!state.models.length) return;
  const available = availableModelIds(data);
  state.models = state.models.filter(model => available.has(model));
}

function effectiveModelTotals(data) {
  const models = data?.models || [];
  if (!state.models.length) return models;
  const selected = new Set(state.models);
  return models.filter(model => selected.has(model.model));
}

function effectiveUsage(data) {
  const baseDaily = data?.daily || [];
  const baseTotals = data?.totals || emptyTotals();
  if (!state.models.length) {
    return {
      daily: baseDaily,
      totals: baseTotals,
      modelTotals: data?.models || [],
    };
  }

  const selected = new Set(state.models);
  const lookup = modelDailyLookup(data);
  const totals = emptyTotals(baseTotals.message_count || 0);
  const daily = baseDaily.map(base => {
    const row = {
      date: base.date,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      message_count: base.message_count || 0,
    };
    selected.forEach(model => {
      const modelDay = lookup.get(model)?.get(base.date);
      if (!modelDay) return;
      row.input_tokens += modelDay.input_tokens || 0;
      row.output_tokens += modelDay.output_tokens || 0;
      row.total_tokens += modelDay.total_tokens || 0;
    });
    totals.input_tokens += row.input_tokens;
    totals.output_tokens += row.output_tokens;
    totals.total_tokens += row.total_tokens;
    return row;
  });

  return {
    daily,
    totals,
    modelTotals: effectiveModelTotals(data),
  };
}

function modelPeriodTotals(data) {
  return (data?.daily_by_model || [])
    .map(series => {
      const totals = (series.daily || []).reduce((sum, day) => {
        sum.input_tokens += day.input_tokens || 0;
        sum.output_tokens += day.output_tokens || 0;
        sum.total_tokens += day.total_tokens || 0;
        return sum;
      }, { input_tokens: 0, output_tokens: 0, total_tokens: 0 });
      return {
        model: series.model || UNKNOWN_MODEL,
        ...totals,
      };
    })
    .filter(model => (model.total_tokens || 0) > 0)
    .sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0) || String(a.model).localeCompare(String(b.model)));
}

function splitModelGroups(data) {
  const modelTotals = effectiveModelTotals(data)
    .filter(model => (model.total_tokens || 0) > 0)
    .sort((a, b) => (b.total_tokens || 0) - (a.total_tokens || 0) || String(a.model).localeCompare(String(b.model)));
  const top = modelTotals.slice(0, 4);
  const rest = modelTotals.slice(4);
  const groups = top.map((model, index) => ({
    id: model.model,
    label: modelDisplayName(model.model),
    models: [model.model],
    color: MODEL_COLORS[index % MODEL_COLORS.length],
  }));
  if (rest.length) {
    groups.push({
      id: OTHER_MODELS,
      label: 'Other',
      models: rest.map(model => model.model),
      color: MODEL_COLORS[4],
    });
  }
  return groups;
}

function renderSkeleton() {
  return `
    <div class="modal-content usage-modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 17V9"/><path d="M12 17V3"/><path d="M16 17v-6"/></svg>Usage</h4>
        <button type="button" class="minimize-btn" id="usage-minimize-btn" title="Minimize">_</button>
        <button type="button" class="modal-close" id="usage-close-btn" title="Close">×</button>
      </div>
      <div class="modal-body usage-body">
        <div class="usage-tabs" role="tablist" aria-label="Usage views">
          <button type="button" class="usage-tab active" data-usage-tab="daily" role="tab" aria-selected="true">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;margin-right:5px"><path d="M3 19h18"/><path d="M3 5v14"/><polyline points="5 15 9 11 13 14 20 7"/></svg>Daily
          </button>
          <button type="button" class="usage-tab" data-usage-tab="models" role="tab" aria-selected="false">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;margin-right:5px"><rect x="3" y="5" width="18" height="4" rx="1"/><rect x="3" y="10" width="14" height="4" rx="1"/><rect x="3" y="15" width="10" height="4" rx="1"/></svg>Models
          </button>
        </div>
        <div class="usage-toolbar">
          <div class="usage-filter-left">
            <label>Period
              <select id="usage-preset" class="usage-select">
                <option value="week">Week to date</option>
                <option value="month">Month to date</option>
                <option value="year">Year to date</option>
                <option value="last7">Last 7 days</option>
                <option value="last14">Last 14 days</option>
                <option value="last30">Last 30 days</option>
                <option value="custom" hidden>Custom range</option>
              </select>
            </label>
            <div class="usage-range-wrap" id="usage-range-wrap">
              <span class="usage-range-label">Date range</span>
              <button type="button" class="usage-range-trigger" id="usage-range-trigger">
                <span id="usage-range-text"></span>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <rect x="3" y="4" width="18" height="18" rx="2"></rect>
                  <line x1="16" y1="2" x2="16" y2="6"></line>
                  <line x1="8" y1="2" x2="8" y2="6"></line>
                  <line x1="3" y1="10" x2="21" y2="10"></line>
                </svg>
              </button>
              <div class="usage-range-popover" id="usage-range-popover" hidden></div>
            </div>
          </div>
          <div class="usage-filter-right">
            <div class="usage-model-wrap" id="usage-model-wrap">
              <span class="usage-range-label">Models</span>
              <button type="button" class="usage-model-trigger" id="usage-model-trigger" aria-haspopup="listbox" aria-expanded="false">
                <span id="usage-model-text">All models</span>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
              </button>
              <div class="usage-model-popover" id="usage-model-popover" hidden></div>
            </div>
            <label id="usage-user-wrap" style="display:none;">Users <select id="usage-user" class="usage-select"></select></label>
            <button type="button" class="usage-refresh" id="usage-refresh" title="Refresh from database" aria-label="Refresh from database">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
            </button>
          </div>
        </div>
        <div class="usage-summary" id="usage-summary"></div>
        <div class="usage-tab-panel" data-usage-panel="daily" role="tabpanel">
          <div class="usage-chart-wrap" id="usage-chart-wrap">
            <div class="usage-loading">Loading usage...</div>
          </div>
        </div>
        <div class="usage-tab-panel hidden" data-usage-panel="models" role="tabpanel">
          <div class="usage-chart-wrap" id="usage-model-chart-wrap">
            <div class="usage-loading">Loading usage...</div>
          </div>
        </div>
      </div>
    </div>
  `;
}

function syncTabUI({ closePopovers = false } = {}) {
  if (!modalEl) return;
  const showModelsTab = state.tab === 'models';
  if (closePopovers) picker.open = false;
  if (closePopovers || showModelsTab) modelPicker.open = false;

  modalEl.querySelectorAll('.usage-tab[data-usage-tab]').forEach(tab => {
    const active = tab.dataset.usageTab === state.tab;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  modalEl.querySelectorAll('.usage-tab-panel[data-usage-panel]').forEach(panel => {
    panel.classList.toggle('hidden', panel.dataset.usagePanel !== state.tab);
  });

  const modelWrap = modalEl.querySelector('#usage-model-wrap');
  if (modelWrap) modelWrap.style.display = showModelsTab ? 'none' : '';

  renderRangePicker();
  renderModelPicker();
}

function syncControls() {
  if (!modalEl) return;
  const preset = modalEl.querySelector('#usage-preset');
  if (preset) preset.value = state.range;
  const rangeText = modalEl.querySelector('#usage-range-text');
  if (rangeText) rangeText.textContent = rangeLabel(state.start, state.end);
  syncTabUI();
}

function renderSummary(data) {
  const totals = state.tab === 'models'
    ? data?.totals || emptyTotals()
    : effectiveUsage(data).totals || {};
  const summary = modalEl?.querySelector('#usage-summary');
  if (!summary) return;
  summary.innerHTML = `
    <div class="usage-stat"><span>Total</span><strong>${fmtNum(totals.total_tokens)}</strong><small>tokens</small></div>
    <div class="usage-stat"><span>Input</span><strong>${fmtNum(totals.input_tokens)}</strong><small>tokens</small></div>
    <div class="usage-stat"><span>Output</span><strong>${fmtNum(totals.output_tokens)}</strong><small>tokens</small></div>
    <div class="usage-stat"><span>Messages</span><strong>${fmtNum(totals.message_count)}</strong><small>messages</small></div>
  `;
}

function renderUserSelect(data) {
  const wrap = modalEl?.querySelector('#usage-user-wrap');
  const select = modalEl?.querySelector('#usage-user');
  if (!wrap || !select) return;
  if (!data?.is_admin) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';
  const users = data.users || [];
  const options = [`<option value="${ALL_USERS}">All users</option>`]
    .concat(users.map(u => `<option value="${esc(u.username)}">${esc(u.username)}${u.is_admin ? ' (admin)' : ''}</option>`));
  select.innerHTML = options.join('');
  select.value = state.user || ALL_USERS;
}

function renderModelPicker(data = state.data) {
  const trigger = modalEl?.querySelector('#usage-model-trigger');
  const text = modalEl?.querySelector('#usage-model-text');
  const popover = document.getElementById('usage-model-popover');
  if (!trigger || !text || !popover) return;

  pruneSelectedModels(data);
  const models = data?.models || [];
  trigger.disabled = !models.length;
  trigger.setAttribute('aria-expanded', String(modelPicker.open));
  text.textContent = state.models.length ? `${state.models.length} model${state.models.length === 1 ? '' : 's'}` : 'All models';

  popover.hidden = !modelPicker.open;
  if (!modelPicker.open) {
    popover.innerHTML = '';
    return;
  }
  if (popover.parentElement !== document.body) {
    document.body.appendChild(popover);
  }

  const selected = new Set(state.models);
  const modelRows = models.length
    ? models.map(({ model, total_tokens }) => `
        <label class="usage-model-option" title="${esc(modelDisplayName(model))}">
          <input type="checkbox" data-usage-model="${esc(model)}" ${selected.has(model) ? 'checked' : ''}>
          ${modelIcon(model)}
          <span>${esc(modelDisplayName(model))}</span>
          <small>${fmtCompact(total_tokens || 0)}</small>
        </label>
      `).join('')
    : '<div class="usage-empty-models">No model usage in this range.</div>';

  popover.innerHTML = `
    <label class="usage-model-option usage-model-all">
      <input type="checkbox" data-usage-model-all ${state.models.length ? '' : 'checked'}>
      <span>All models</span>
    </label>
    <div class="usage-model-list">${modelRows}</div>
  `;

  popover.querySelector('[data-usage-model-all]')?.addEventListener('change', e => {
    e.stopPropagation();
    state.models = [];
    renderModelPicker(data);
    renderSummary(state.data);
    renderActiveChart(state.data);
  });

  popover.querySelectorAll('[data-usage-model]').forEach(input => {
    input.addEventListener('change', e => {
      e.stopPropagation();
      const model = input.dataset.usageModel;
      if (!model) return;
      const next = new Set(state.models);
      if (input.checked) next.add(model);
      else next.delete(model);
      state.models = Array.from(next);
      // Selecting specific models implies a per-model breakdown, so turn the
      // split-by-model view on automatically.
      if (state.models.length) state.splitByModel = true;
      renderModelPicker(data);
      renderSummary(state.data);
      renderActiveChart(state.data);
    });
  });

  clampModelPopover(popover);
}

function openModelPicker() {
  modelPicker.open = true;
  renderModelPicker();
}

function closeModelPicker() {
  modelPicker.open = false;
  renderModelPicker();
}

function clampModelPopover(popover) {
  const trigger = modalEl?.querySelector('#usage-model-trigger');
  if (!trigger) return;
  popover.style.right = 'auto';
  if (!modelPicker.open) return;
  const margin = 8;
  const triggerRect = trigger.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();
  const popW = popRect.width;
  const popH = popRect.height;

  let left = triggerRect.left;
  const maxLeft = window.innerWidth - margin - popW;
  left = Math.min(left, maxLeft);
  left = Math.max(margin, left);

  let top = triggerRect.bottom + 6;
  if (top + popH > window.innerHeight - margin) {
    const above = triggerRect.top - 6 - popH;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
  }

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function monthGrid(baseDate) {
  const first = new Date(baseDate.getFullYear(), baseDate.getMonth(), 1);
  const start = addDays(first, -first.getDay());
  const selected = sortedRange(picker.draftStart, picker.draftEnd);
  const todayStr = ymd(new Date());
  let days = '';
  for (let i = 0; i < 42; i++) {
    const d = addDays(start, i);
    const day = ymd(d);
    const muted = d.getMonth() !== baseDate.getMonth();
    const future = day > todayStr;
    const inRange = selected.start && selected.end && day > selected.start && day < selected.end;
    const isSelected = day === selected.start || day === selected.end;
    days += `<button type="button" class="usage-range-day${muted ? ' muted' : ''}${future ? ' future' : ''}${inRange ? ' in-range' : ''}${isSelected ? ' selected' : ''}" data-day="${day}"${future ? ' disabled' : ''}>${d.getDate()}</button>`;
  }
  return `
    <div class="usage-range-month">
      <div class="usage-range-month-title">${MONTHS[baseDate.getMonth()]} ${baseDate.getFullYear()}</div>
      <div class="usage-range-weekdays">${WEEKDAYS.map(d => `<span>${d}</span>`).join('')}</div>
      <div class="usage-range-grid">${days}</div>
    </div>
  `;
}

function openRangePicker() {
  picker.open = true;
  picker.draftStart = state.start;
  picker.draftEnd = state.end;
  const startDate = parseYmd(state.start);
  picker.month = new Date(startDate.getFullYear(), startDate.getMonth(), 1);
  renderRangePicker();
}

function closeRangePicker() {
  picker.open = false;
  renderRangePicker();
}

function applyRangePicker() {
  const selected = sortedRange(picker.draftStart, picker.draftEnd);
  if (!selected.start) return;
  state.start = selected.start;
  state.end = selected.end;
  state.range = 'custom';
  picker.open = false;
  syncControls();
  loadUsage();
}

function renderRangePicker() {
  const popover = document.getElementById('usage-range-popover');
  if (!popover) return;
  popover.hidden = !picker.open;
  if (!picker.open) {
    popover.innerHTML = '';
    return;
  }
  // Portal the popover to <body> so it isn't clipped by the modal's
  // overflow:hidden, nor re-anchored as a fixed element by the modal's
  // (animation-held) transform. This keeps it visible and correctly
  // positioned even when the modal is narrow.
  if (popover.parentElement !== document.body) {
    document.body.appendChild(popover);
  }
  if (!picker.month) {
    const startDate = parseYmd(state.start);
    picker.month = new Date(startDate.getFullYear(), startDate.getMonth(), 1);
  }
  const nextMonth = addMonths(picker.month, 1);
  const selected = sortedRange(picker.draftStart, picker.draftEnd);
  popover.innerHTML = `
    <div class="usage-range-head">
      <button type="button" data-usage-cal-nav="-1" aria-label="Previous month">‹</button>
      <div class="usage-range-title">${selected.start ? rangeLabel(selected.start, selected.end) : 'Choose date range'}</div>
      <button type="button" data-usage-cal-nav="1" aria-label="Next month">›</button>
    </div>
    <div class="usage-range-months">
      ${monthGrid(picker.month)}
      ${monthGrid(nextMonth)}
    </div>
    <div class="usage-range-actions">
      <span class="usage-range-hint">${picker.draftStart && !picker.draftEnd ? 'Choose an end date' : 'Choose start and end dates'}</span>
      <div>
        <button type="button" data-usage-range-cancel>Cancel</button>
        <button type="button" class="usage-range-apply" data-usage-range-apply>Apply</button>
      </div>
    </div>
  `;
  popover.querySelectorAll('[data-usage-cal-nav]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const next = addMonths(picker.month, Number(btn.dataset.usageCalNav || 0));
      const now = new Date();
      const maxMonth = new Date(now.getFullYear(), now.getMonth(), 1);
      picker.month = next > maxMonth ? maxMonth : next;
      renderRangePicker();
    });
  });
  popover.querySelectorAll('[data-day]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const day = btn.dataset.day;
      if (!picker.draftStart || (picker.draftStart && picker.draftEnd)) {
        picker.draftStart = day;
        picker.draftEnd = '';
      } else if (day < picker.draftStart) {
        picker.draftEnd = picker.draftStart;
        picker.draftStart = day;
      } else {
        picker.draftEnd = day;
      }
      renderRangePicker();
    });
  });
  popover.querySelector('[data-usage-range-cancel]')?.addEventListener('click', e => {
    e.stopPropagation();
    closeRangePicker();
  });
  popover.querySelector('[data-usage-range-apply]')?.addEventListener('click', e => {
    e.stopPropagation();
    applyRangePicker();
  });
  clampRangePopover(popover);
}

function clampRangePopover(popover) {
  // On mobile the stylesheet pins the popover with fixed positioning; leave it
  // alone there and let CSS handle it.
  if (window.matchMedia && window.matchMedia('(max-width: 768px)').matches) {
    popover.style.left = '';
    popover.style.right = '';
    popover.style.top = '';
    return;
  }
  // The popover is position:fixed so it can spill outside the (overflow:hidden)
  // modal when it's narrow. Anchor it to the trigger in viewport coordinates and
  // clamp to the viewport so it never runs off-screen.
  const trigger = modalEl?.querySelector('#usage-range-trigger');
  if (!trigger) return;
  popover.style.right = 'auto';
  if (!picker.open) return;
  const margin = 8;
  const triggerRect = trigger.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();
  const popW = popRect.width;
  const popH = popRect.height;

  let left = triggerRect.left;
  const maxLeft = window.innerWidth - margin - popW;
  left = Math.min(left, maxLeft);
  left = Math.max(margin, left);

  let top = triggerRect.bottom + 6;
  if (top + popH > window.innerHeight - margin) {
    const above = triggerRect.top - 6 - popH;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
  }

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function chartInnerSvg(data, width, height) {
  const effective = effectiveUsage(data);
  const daily = effective.daily || [];
  const avgSeries = movingAverageSeries(data);
  const visibleTotal = d => (state.showInput ? d.input_tokens || 0 : 0) + (state.showOutput ? d.output_tokens || 0 : 0);
  const maxVisibleTotal = Math.max(0, ...daily.map(visibleTotal));
  const maxAverageTotal = Math.max(0, ...avgSeries.flatMap(series => series.points.map(point => point.value || 0)));
  const maxTotal = Math.max(maxVisibleTotal, maxAverageTotal, 1);
  if (!daily.length) {
    return '<div class="usage-empty">No token usage found for this range.</div>';
  }

  const left = 54;
  const top = 18;
  const bottom = 56;
  const right = 12;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const slotW = plotW / daily.length;
  const barGap = Math.min(Math.max(slotW * 0.18, 4), 10);
  const barW = Math.max(slotW - barGap, 1);
  const yFor = val => top + plotH - (val / maxTotal) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(p => Math.round(maxTotal * p));
  const every = Math.max(1, Math.ceil(daily.length / 10));
  const splitGroups = state.splitByModel ? splitModelGroups(data) : [];
  const modelLookup = modelDailyLookup(data);

  const grid = ticks.map(t => {
    const y = yFor(t);
    return `<g><line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="usage-grid-line"></line><text x="${left - 8}" y="${y + 4}" class="usage-axis-label" text-anchor="end">${fmtCompact(t)}</text></g>`;
  }).join('');

  const modelTokensForDay = (group, date) => group.models.reduce((sum, model) => {
    const row = modelLookup.get(model)?.get(date);
    sum.input_tokens += row?.input_tokens || 0;
    sum.output_tokens += row?.output_tokens || 0;
    return sum;
  }, { input_tokens: 0, output_tokens: 0 });

  const bars = daily.map((d, i) => {
    const laneX = left + i * slotW;
    const x = laneX + barGap / 2;
    const rawInput = d.input_tokens || 0;
    const rawOutput = d.output_tokens || 0;
    const centerX = laneX + slotW / 2;
    const label = i % every === 0 ? `<text x="${centerX}" y="${height - 24}" class="usage-axis-label usage-x-label" text-anchor="middle">${d.date.slice(5)}</text>` : '';
    let barRects = '';

    if (state.splitByModel && splitGroups.length) {
      let cursorY = top + plotH;
      splitGroups.forEach(group => {
        const values = modelTokensForDay(group, d.date);
        const input = state.showInput ? values.input_tokens : 0;
        const output = state.showOutput ? values.output_tokens : 0;
        const inputH = (input / maxTotal) * plotH;
        const outputH = (output / maxTotal) * plotH;
        if (inputH > 0) {
          cursorY -= inputH;
          barRects += `<rect x="${x}" y="${cursorY}" width="${Math.max(barW, 1)}" height="${inputH}" class="usage-model-segment usage-model-segment-input" fill="${esc(group.color)}"><title>${esc(group.label)} input</title></rect>`;
        }
        if (outputH > 0) {
          cursorY -= outputH;
          barRects += `<rect x="${x}" y="${cursorY}" width="${Math.max(barW, 1)}" height="${outputH}" class="usage-model-segment usage-model-segment-output" fill="${esc(group.color)}"><title>${esc(group.label)} output</title></rect>`;
        }
      });
    } else {
      const input = state.showInput ? rawInput : 0;
      const output = state.showOutput ? rawOutput : 0;
      const inputH = (input / maxTotal) * plotH;
      const outputH = (output / maxTotal) * plotH;
      const inputY = top + plotH - inputH;
      const outputY = inputY - outputH;
      barRects = `
        <rect x="${x}" y="${outputY}" width="${Math.max(barW, 1)}" height="${outputH}" class="usage-bar-output"></rect>
        <rect x="${x}" y="${inputY}" width="${Math.max(barW, 1)}" height="${inputH}" class="usage-bar-input"></rect>
      `;
    }

    return `
      <rect x="${laneX}" y="${top}" width="${slotW}" height="${plotH}" class="usage-hover-highlight" data-highlight-date="${esc(d.date)}"></rect>
      <g class="usage-bar" data-bar-date="${esc(d.date)}">
        ${barRects}
        ${label}
      </g>
      <rect x="${laneX}" y="${top}" width="${slotW}" height="${plotH}" class="usage-hover-target" data-date="${esc(d.date)}" data-input="${rawInput}" data-output="${rawOutput}" data-total="${d.total_tokens || 0}" data-messages="${d.message_count || 0}"></rect>
    `;
  }).join('');

  const leftEdge = left;
  const rightEdge = width - right;
  const avgLines = avgSeries.map(series => {
    const points = series.points.map((point, i) => {
      const x = left + i * slotW + slotW / 2;
      return { x, y: yFor(point.value || 0) };
    });
    // Stretch the line out to the chart edges instead of stopping at the
    // centers of the first/last bars.
    if (points.length) {
      if (points[0].x > leftEdge) points.unshift({ x: leftEdge, y: points[0].y });
      const lastPoint = points[points.length - 1];
      if (lastPoint.x < rightEdge) points.push({ x: rightEdge, y: lastPoint.y });
    }
    return `<path class="usage-ma-line" d="${smoothPath(points)}" stroke="${esc(series.color)}" fill="none" vector-effect="non-scaling-stroke"><title>${esc(series.user)} 7-day average</title></path>`;
  }).join('');

  return `
    <svg class="usage-chart" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Daily token usage chart">
      ${grid}
      <line x1="${left}" y1="${top + plotH}" x2="${width - right}" y2="${top + plotH}" class="usage-axis-line"></line>
      ${bars}
      ${avgLines}
    </svg>
  `;
}

function chartShell(data) {
  const topLegend = state.splitByModel
    ? splitModelGroups(data).map(group => {
        const icon = group.id === OTHER_MODELS ? '' : modelIcon(group.id);
        return `
        <span class="usage-model-swatch" title="${esc(group.label)}">
          <i style="background:${esc(group.color)}"></i>${icon}${esc(truncateModelLabel(group.label))}
        </span>
      `;
      }).join('')
    : '';
  return `
    ${topLegend ? `<div class="usage-model-top-legend">${topLegend}</div>` : ''}
    <div class="usage-chart-scroll"></div>
    <div class="usage-tooltip" id="usage-tooltip" hidden></div>
    <div class="usage-legend">
      <button type="button" class="usage-legend-toggle${state.showInput ? ' active' : ''}" data-usage-series="input" aria-pressed="${state.showInput}">
        <i class="usage-legend-input"></i>Input tokens
      </button>
      <button type="button" class="usage-legend-toggle${state.showOutput ? ' active' : ''}" data-usage-series="output" aria-pressed="${state.showOutput}">
        <i class="usage-legend-output"></i>Output tokens
      </button>
      <button type="button" class="usage-legend-toggle${state.showMovingAverage ? ' active' : ''}" data-usage-series="average" aria-pressed="${state.showMovingAverage}">
        <i class="usage-legend-average"></i>7-day average
      </button>
      <button type="button" class="usage-legend-toggle usage-split-toggle${state.splitByModel ? ' active' : ''}" data-usage-series="split" aria-pressed="${state.splitByModel}">
        Split by model
      </button>
    </div>
  `;
}

function modelChartInnerSvg(data, width, minHeight = 0) {
  const rows = modelPeriodTotals(data);
  if (!rows.length) {
    return '<div class="usage-empty">No model usage found for this range.</div>';
  }

  const left = 8;
  const right = 8;
  const compactLabels = width < 640;
  const top = 10;
  const bottom = 30;
  const minRowH = compactLabels ? 30 : 44;
  const plotH = Math.max(1, Math.max(minHeight - top - bottom, rows.length * minRowH));
  const rowH = plotH / rows.length;
  const labelH = compactLabels ? 0 : 18;
  const barH = compactLabels
    ? Math.max(18, Math.min(56, rowH * 0.72))
    : Math.max(18, Math.min(58, (rowH - labelH - 6) * 0.88));
  const height = top + bottom + plotH;
  const plotW = Math.max(1, width - left - right);
  const visibleTotal = row => (
    (state.showInput ? row.input_tokens || 0 : 0)
    + (state.showOutput ? row.output_tokens || 0 : 0)
  );
  const maxTotal = Math.max(1, ...rows.map(visibleTotal));
  const periodTotal = Math.max(1, data?.totals?.total_tokens || 0);
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  const grid = ticks.map(p => {
    const x = left + p * plotW;
    const value = Math.round(maxTotal * p);
    return `
      <g>
        <line x1="${x}" y1="${top}" x2="${x}" y2="${height - bottom}" class="usage-grid-line"></line>
        <text x="${x}" y="${height - 12}" class="usage-axis-label usage-model-x-label" text-anchor="${p === 0 ? 'start' : p === 1 ? 'end' : 'middle'}">${fmtCompact(value)}</text>
      </g>
    `;
  }).join('');

  const bars = rows.map((row, index) => {
    const rowTop = top + index * rowH;
    const centerY = rowTop + rowH / 2;
    const barY = compactLabels
      ? centerY - barH / 2
      : rowTop + labelH + Math.max(3, (rowH - labelH - barH) / 2);
    const input = state.showInput ? row.input_tokens || 0 : 0;
    const output = state.showOutput ? row.output_tokens || 0 : 0;
    const inputW = (input / maxTotal) * plotW;
    const outputW = (output / maxTotal) * plotW;
    const rowVisibleTotal = input + output;
    const label = modelDisplayName(row.model);
    const labelText = truncateModelLabel(label, 32);
    const share = ((row.total_tokens || 0) / periodTotal) * 100;
    const labelW = Math.min(Math.max(width * 0.34, 160), Math.max(160, width - 116));
    const labelY = compactLabels ? barY + barH / 2 - 12 : rowTop + Math.max(1, (labelH - 14) / 2);

    return `
      <rect x="0" y="${centerY - rowH / 2 + 2}" width="${width}" height="${rowH - 4}" class="usage-model-row-highlight" data-model-row="${index}"></rect>
      <g class="usage-model-horizontal-bar" data-model-bar="${index}">
        <rect x="${left}" y="${barY}" width="${Math.max(inputW, 0)}" height="${barH}" rx="3" class="usage-bar-input usage-model-horizontal-segment"></rect>
        <rect x="${left + inputW}" y="${barY}" width="${Math.max(outputW, 0)}" height="${barH}" rx="3" class="usage-bar-output usage-model-horizontal-segment"></rect>
        <foreignObject x="${left + 8}" y="${labelY}" width="${labelW}" height="24">
          <div xmlns="http://www.w3.org/1999/xhtml" class="usage-model-axis-label${compactLabels ? ' compact' : ''}" title="${esc(label)}">
            ${modelIcon(row.model)}<span>${esc(labelText)}</span>
          </div>
        </foreignObject>
        <text x="${left + plotW - 8}" y="${barY + barH / 2 + 4}" class="usage-axis-label usage-model-total-label" text-anchor="end">${fmtCompact(rowVisibleTotal)}</text>
      </g>
      <rect x="0" y="${centerY - rowH / 2}" width="${width}" height="${rowH}" class="usage-model-hover-target" data-model-index="${index}" data-display="${esc(label)}" data-input="${row.input_tokens || 0}" data-output="${row.output_tokens || 0}" data-total="${row.total_tokens || 0}" data-share="${share.toFixed(1)}"></rect>
    `;
  }).join('');

  return `
    <svg class="usage-model-chart" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="Model token usage chart">
      ${grid}
      <line x1="${left}" y1="${height - bottom}" x2="${left + plotW}" y2="${height - bottom}" class="usage-axis-line"></line>
      ${bars}
    </svg>
  `;
}

function modelChartShell() {
  return `
    <div class="usage-model-chart-scroll"></div>
    <div class="usage-tooltip" id="usage-model-tooltip" hidden></div>
    <div class="usage-legend usage-model-chart-legend">
      <button type="button" class="usage-legend-toggle${state.showInput ? ' active' : ''}" data-usage-series="input" aria-pressed="${state.showInput}">
        <i class="usage-legend-input"></i>Input tokens
      </button>
      <button type="button" class="usage-legend-toggle${state.showOutput ? ' active' : ''}" data-usage-series="output" aria-pressed="${state.showOutput}">
        <i class="usage-legend-output"></i>Output tokens
      </button>
    </div>
  `;
}

function wireModelChartTooltips() {
  const wrap = modalEl?.querySelector('#usage-model-chart-wrap');
  const scroll = modalEl?.querySelector('.usage-model-chart-scroll');
  const tip = modalEl?.querySelector('#usage-model-tooltip');
  if (!wrap || !tip) return;

  const clearActive = () => {
    wrap.querySelectorAll('.usage-model-row-highlight.active, .usage-model-horizontal-bar.active').forEach(el => {
      el.classList.remove('active');
    });
  };
  const hideTooltip = () => {
    clearActive();
    tip.hidden = true;
  };
  const positionTooltip = e => {
    const rect = wrap.getBoundingClientRect();
    const padding = 8;
    const preferredLeft = e.clientX - rect.left + 12;
    const preferredTop = e.clientY - rect.top + 12;
    const maxLeft = Math.max(padding, wrap.clientWidth - tip.offsetWidth - padding);
    const maxTop = Math.max(padding, wrap.clientHeight - tip.offsetHeight - padding);
    tip.style.left = `${Math.max(padding, Math.min(preferredLeft, maxLeft))}px`;
    tip.style.top = `${Math.max(padding, Math.min(preferredTop, maxTop))}px`;
  };

  wrap.querySelectorAll('.usage-model-hover-target').forEach(target => {
    target.addEventListener('mousemove', e => {
      clearActive();
      const index = target.dataset.modelIndex;
      wrap.querySelector(`[data-model-row="${index}"]`)?.classList.add('active');
      wrap.querySelector(`[data-model-bar="${index}"]`)?.classList.add('active');
      tip.hidden = false;
      tip.innerHTML = `
        <strong>${esc(target.dataset.display)}</strong>
        <span>Total: ${fmtNum(target.dataset.total)} tokens</span>
        <span>Input: ${fmtNum(target.dataset.input)}</span>
        <span>Output: ${fmtNum(target.dataset.output)}</span>
        <span>Share: ${esc(target.dataset.share)}%</span>
      `;
      positionTooltip(e);
    });
  });

  scroll?.addEventListener('mouseleave', hideTooltip);
  wrap.addEventListener('mouseleave', hideTooltip);
  wrap.querySelectorAll('[data-usage-series]').forEach(btn => {
    btn.addEventListener('click', () => {
      const series = btn.dataset.usageSeries;
      if (series === 'input') state.showInput = !state.showInput;
      if (series === 'output') state.showOutput = !state.showOutput;
      renderModelChart(state.data);
    });
  });
}

function renderModelChart(data) {
  const wrap = modalEl?.querySelector('#usage-model-chart-wrap');
  if (!wrap) return;
  if (state.loading && data) return;
  if (state.loading) {
    wrap.innerHTML = '<div class="usage-loading">Loading usage...</div>';
    return;
  }
  lastChartRenderWidth = Math.round(wrap.clientWidth);
  lastChartRenderHeight = Math.round(wrap.clientHeight);
  wrap.innerHTML = modelChartShell();
  const scroll = wrap.querySelector('.usage-model-chart-scroll');
  if (scroll) {
    const cs = getComputedStyle(scroll);
    const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const w = Math.max(1, Math.floor(scroll.clientWidth - padX));
    const h = Math.max(1, Math.floor(scroll.clientHeight - padY));
    scroll.innerHTML = modelChartInnerSvg(data, w, h);
  }
  wireModelChartTooltips();
}

function activeChartWrap() {
  return modalEl?.querySelector(state.tab === 'models' ? '#usage-model-chart-wrap' : '#usage-chart-wrap');
}

function renderActiveChart(data) {
  if (state.tab === 'models') renderModelChart(data);
  else renderChart(data);
}

function wireChartTooltips() {
  const wrap = modalEl?.querySelector('#usage-chart-wrap');
  const scroll = modalEl?.querySelector('.usage-chart-scroll');
  const tip = modalEl?.querySelector('#usage-tooltip');
  if (!wrap || !tip) return;
  const clearActive = () => {
    wrap.querySelectorAll('.usage-hover-highlight.active, .usage-bar.active').forEach(el => {
      el.classList.remove('active');
    });
  };
  const hideTooltip = () => {
    clearActive();
    tip.hidden = true;
  };
  const positionTooltip = e => {
    const rect = wrap.getBoundingClientRect();
    const padding = 8;
    const preferredLeft = e.clientX - rect.left + 12;
    const preferredTop = e.clientY - rect.top + 12;
    const maxLeft = Math.max(padding, wrap.clientWidth - tip.offsetWidth - padding);
    const maxTop = Math.max(padding, wrap.clientHeight - tip.offsetHeight - padding);
    tip.style.left = `${Math.max(padding, Math.min(preferredLeft, maxLeft))}px`;
    tip.style.top = `${Math.max(padding, Math.min(preferredTop, maxTop))}px`;
  };
  wrap.querySelectorAll('.usage-hover-target').forEach(target => {
    target.addEventListener('mousemove', e => {
      clearActive();
      wrap.querySelector(`[data-highlight-date="${target.dataset.date}"]`)?.classList.add('active');
      wrap.querySelector(`[data-bar-date="${target.dataset.date}"]`)?.classList.add('active');
      tip.hidden = false;
      tip.innerHTML = `
        <strong>${esc(target.dataset.date)}</strong>
        <span>Total: ${fmtNum(target.dataset.total)} tokens</span>
        <span>Input: ${fmtNum(target.dataset.input)}</span>
        <span>Output: ${fmtNum(target.dataset.output)}</span>
        <span>Messages: ${fmtNum(target.dataset.messages)}</span>
      `;
      positionTooltip(e);
    });
  });
  scroll?.addEventListener('mouseleave', hideTooltip);
  wrap.addEventListener('mouseleave', hideTooltip);
  wrap.querySelectorAll('[data-usage-series]').forEach(btn => {
    btn.addEventListener('click', () => {
      const series = btn.dataset.usageSeries;
      if (series === 'input') state.showInput = !state.showInput;
      if (series === 'output') state.showOutput = !state.showOutput;
      if (series === 'average') state.showMovingAverage = !state.showMovingAverage;
      if (series === 'split') state.splitByModel = !state.splitByModel;
      renderChart(state.data);
    });
  });
}

function renderChart(data) {
  const wrap = modalEl?.querySelector('#usage-chart-wrap');
  if (!wrap) return;
  if (state.loading && data) return;
  if (state.loading) {
    wrap.innerHTML = '<div class="usage-loading">Loading usage...</div>';
    return;
  }
  lastChartRenderWidth = Math.round(wrap.clientWidth);
  lastChartRenderHeight = Math.round(wrap.clientHeight);
  wrap.innerHTML = chartShell(data);
  const scroll = wrap.querySelector('.usage-chart-scroll');
  if (scroll) {
    const cs = getComputedStyle(scroll);
    const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    const padY = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const w = Math.max(1, Math.floor(scroll.clientWidth - padX));
    const h = Math.max(1, Math.floor(scroll.clientHeight - padY));
    scroll.innerHTML = chartInnerSvg(data, w, h);
  }
  wireChartTooltips();
}

function watchChartSize() {
  const wraps = modalEl ? Array.from(modalEl.querySelectorAll('#usage-chart-wrap, #usage-model-chart-wrap')) : [];
  if (!wraps.length || chartResizeObserver) return;
  chartResizeObserver = new ResizeObserver(() => {
    if (!state.data || state.loading) return;
    if (chartResizeRaf) cancelAnimationFrame(chartResizeRaf);
    chartResizeRaf = requestAnimationFrame(() => {
      chartResizeRaf = 0;
      const wrap = activeChartWrap();
      if (!wrap || wrap.clientWidth <= 0 || wrap.clientHeight <= 0) return;
      const nextWidth = Math.round(wrap.clientWidth);
      const nextHeight = Math.round(wrap.clientHeight);
      if (Math.abs(nextWidth - lastChartRenderWidth) < 2 && Math.abs(nextHeight - lastChartRenderHeight) < 2) return;
      renderActiveChart(state.data);
    });
  });
  wraps.forEach(wrap => chartResizeObserver.observe(wrap));
}

async function loadUsage() {
  if (!modalEl) return;
  state.loading = true;
  renderActiveChart(state.data);
  saveRange();
  const params = new URLSearchParams({
    start: state.start,
    end: state.end,
    tz_offset_minutes: String(new Date().getTimezoneOffset()),
  });
  if (state.user) params.set('user', state.user);
  try {
    const res = await fetch(`/api/usage/tokens?${params}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.data = data;
    state.loading = false;
    pruneSelectedModels(data);
    renderUserSelect(data);
    renderModelPicker(data);
    renderSummary(data);
    renderActiveChart(data);
  } catch (err) {
    state.loading = false;
    const wrap = activeChartWrap();
    if (wrap) wrap.innerHTML = `<div class="usage-empty usage-error">Failed to load usage: ${esc(err.message || err)}</div>`;
  }
}

function wireControls() {
  modalEl.querySelector('#usage-close-btn')?.addEventListener('click', closeUsage);
  modalEl.querySelector('#usage-minimize-btn')?.addEventListener('click', minimizeUsage);
  modalEl.querySelector('#usage-refresh')?.addEventListener('click', loadUsage);
  modalEl.querySelectorAll('.usage-tab[data-usage-tab]').forEach(tab => {
    tab.addEventListener('click', () => {
      const next = tab.dataset.usageTab || 'daily';
      if (next === state.tab) return;
      state.tab = next;
      lastChartRenderWidth = 0;
      lastChartRenderHeight = 0;
      syncTabUI({ closePopovers: true });
      renderSummary(state.data);
      renderActiveChart(state.data);
    });
  });
  modalEl.querySelector('#usage-preset')?.addEventListener('change', e => {
    const next = e.target.value;
    state.range = next;
    if (next === 'custom') {
      syncControls();
      openRangePicker();
      return;
    }
    Object.assign(state, rangeFor(next));
    closeRangePicker();
    syncControls();
    loadUsage();
  });
  modalEl.querySelector('#usage-range-trigger')?.addEventListener('click', e => {
    e.stopPropagation();
    e.preventDefault();
    closeModelPicker();
    if (picker.open) closeRangePicker();
    else openRangePicker();
  });
  modalEl.querySelector('#usage-model-trigger')?.addEventListener('click', e => {
    e.stopPropagation();
    e.preventDefault();
    closeRangePicker();
    if (modelPicker.open) closeModelPicker();
    else openModelPicker();
  });
  modalEl.querySelector('#usage-user')?.addEventListener('change', e => {
    state.user = e.target.value || ALL_USERS;
    loadUsage();
  });
  modalEl.addEventListener('click', e => {
    if (e.target === modalEl) closeUsage();
    else if (picker.open && !e.target.closest('#usage-range-wrap')) closeRangePicker();
    else if (modelPicker.open && !e.target.closest('#usage-model-wrap')) closeModelPicker();
  });
  documentClickHandler = e => {
    if (picker.open && !e.target.closest('#usage-range-wrap') && !e.target.closest('#usage-range-popover')) {
      closeRangePicker();
    }
    if (modelPicker.open && !e.target.closest('#usage-model-wrap') && !e.target.closest('#usage-model-popover')) {
      closeModelPicker();
    }
  };
  document.addEventListener('click', documentClickHandler);
}

export function openUsage() {
  ensureDefaults();
  if (modalEl) {
    if (Modals.isMinimized(MODAL_ID)) Modals.restore(MODAL_ID);
    else modalEl.classList.remove('hidden');
    return;
  }
  modalEl = document.createElement('div');
  modalEl.className = 'modal usage-modal';
  modalEl.id = MODAL_ID;
  modalEl.innerHTML = renderSkeleton();
  document.body.appendChild(modalEl);
  makeWindowDraggable(modalEl, {
    content: modalEl.querySelector('.modal-content'),
    header: modalEl.querySelector('.modal-header'),
  });
  Modals.register(MODAL_ID, {
    restoreFn: restoreUsage,
    closeFn: closeUsage,
    sidebarBtnId: 'tool-usage-btn',
    label: 'Usage',
    icon: 'M4 19V5M4 19h16M8 17V9M12 17V3M16 17v-6',
  });
  syncControls();
  wireControls();
  watchChartSize();
  loadUsage();
}

export default {
  openUsage,
};

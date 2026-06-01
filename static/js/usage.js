// static/js/usage.js
// Usage dashboard: token analytics by day.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

const MODAL_ID = 'usage-modal';
const ALL_USERS = '__all__';
const RANGE_KEY = 'ody-usage-range';
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

let modalEl = null;
let state = {
  range: 'month',
  start: '',
  end: '',
  user: ALL_USERS,
  data: null,
  loading: false,
};

let picker = {
  open: false,
  month: null,
  draftStart: '',
  draftEnd: '',
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

function closeUsage() {
  if (modalEl) modalEl.remove();
  modalEl = null;
  Modals.unregister(MODAL_ID);
}

function minimizeUsage() {
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
  state = { ...state, range: 'month', ...rangeFor('month') };
}

function saveRange() {
  try {
    localStorage.setItem(RANGE_KEY, JSON.stringify({
      range: state.range,
      start: state.start,
      end: state.end,
      user: state.user,
    }));
  } catch (_) {}
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
            <label id="usage-user-wrap" style="display:none;">Users <select id="usage-user"></select></label>
            <button type="button" class="usage-refresh" id="usage-refresh" title="Refresh from database" aria-label="Refresh from database">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 4v6h6"/><path d="M23 20v-6h-6"/><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/></svg>
            </button>
          </div>
        </div>
        <div class="usage-summary" id="usage-summary"></div>
        <div class="usage-chart-wrap" id="usage-chart-wrap">
          <div class="usage-loading">Loading usage...</div>
        </div>
      </div>
    </div>
  `;
}

function syncControls() {
  if (!modalEl) return;
  const preset = modalEl.querySelector('#usage-preset');
  if (preset) preset.value = state.range;
  const rangeText = modalEl.querySelector('#usage-range-text');
  if (rangeText) rangeText.textContent = rangeLabel(state.start, state.end);
  renderRangePicker();
}

function renderSummary(data) {
  const totals = data?.totals || {};
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

function monthGrid(baseDate) {
  const first = new Date(baseDate.getFullYear(), baseDate.getMonth(), 1);
  const start = addDays(first, -first.getDay());
  const selected = sortedRange(picker.draftStart, picker.draftEnd);
  let days = '';
  for (let i = 0; i < 42; i++) {
    const d = addDays(start, i);
    const day = ymd(d);
    const muted = d.getMonth() !== baseDate.getMonth();
    const inRange = selected.start && selected.end && day > selected.start && day < selected.end;
    const isSelected = day === selected.start || day === selected.end;
    days += `<button type="button" class="usage-range-day${muted ? ' muted' : ''}${inRange ? ' in-range' : ''}${isSelected ? ' selected' : ''}" data-day="${day}">${d.getDate()}</button>`;
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
  const popover = modalEl?.querySelector('#usage-range-popover');
  if (!popover) return;
  popover.hidden = !picker.open;
  if (!picker.open) {
    popover.innerHTML = '';
    return;
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
      picker.month = addMonths(picker.month, Number(btn.dataset.usageCalNav || 0));
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
}

function chartSvg(data) {
  const daily = data?.daily || [];
  const maxTotal = Math.max(0, ...daily.map(d => d.total_tokens || 0));
  if (!daily.length || maxTotal === 0) {
    return '<div class="usage-empty">No token usage found for this range.</div>';
  }

  const width = Math.max(720, daily.length * 18);
  const height = 320;
  const left = 54;
  const top = 18;
  const bottom = 42;
  const right = 12;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const barW = plotW / daily.length;
  const yFor = val => top + plotH - (val / maxTotal) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(p => Math.round(maxTotal * p));
  const every = Math.max(1, Math.ceil(daily.length / 10));

  const grid = ticks.map(t => {
    const y = yFor(t);
    return `<g><line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" class="usage-grid-line"></line><text x="${left - 8}" y="${y + 4}" class="usage-axis-label" text-anchor="end">${fmtCompact(t)}</text></g>`;
  }).join('');

  const bars = daily.map((d, i) => {
    const x = left + i * barW;
    const input = d.input_tokens || 0;
    const output = d.output_tokens || 0;
    const inputH = (input / maxTotal) * plotH;
    const outputH = (output / maxTotal) * plotH;
    const inputY = top + plotH - inputH;
    const outputY = inputY - outputH;
    const label = i % every === 0 ? `<text x="${x + barW / 2}" y="${height - 18}" class="usage-axis-label usage-x-label" text-anchor="middle">${d.date.slice(5)}</text>` : '';
    return `
      <g class="usage-bar" data-date="${esc(d.date)}" data-input="${input}" data-output="${output}" data-total="${d.total_tokens || 0}" data-messages="${d.message_count || 0}">
        <rect x="${x}" y="${outputY}" width="${Math.max(barW, 1)}" height="${outputH}" class="usage-bar-output"></rect>
        <rect x="${x}" y="${inputY}" width="${Math.max(barW, 1)}" height="${inputH}" class="usage-bar-input"></rect>
        ${label}
      </g>
    `;
  }).join('');

  return `
    <div class="usage-chart-scroll">
      <svg class="usage-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Daily token usage chart">
        ${grid}
        <line x1="${left}" y1="${top + plotH}" x2="${width - right}" y2="${top + plotH}" class="usage-axis-line"></line>
        ${bars}
      </svg>
      <div class="usage-tooltip" id="usage-tooltip" hidden></div>
    </div>
    <div class="usage-legend">
      <span><i class="usage-legend-input"></i>Input tokens</span>
      <span><i class="usage-legend-output"></i>Output tokens</span>
    </div>
  `;
}

function wireChartTooltips() {
  const wrap = modalEl?.querySelector('#usage-chart-wrap');
  const tip = modalEl?.querySelector('#usage-tooltip');
  if (!wrap || !tip) return;
  wrap.querySelectorAll('.usage-bar').forEach(bar => {
    bar.addEventListener('mousemove', e => {
      tip.hidden = false;
      tip.innerHTML = `
        <strong>${esc(bar.dataset.date)}</strong>
        <span>Total: ${fmtNum(bar.dataset.total)} tokens</span>
        <span>Input: ${fmtNum(bar.dataset.input)}</span>
        <span>Output: ${fmtNum(bar.dataset.output)}</span>
        <span>Messages: ${fmtNum(bar.dataset.messages)}</span>
      `;
      const rect = wrap.getBoundingClientRect();
      tip.style.left = `${e.clientX - rect.left + 12}px`;
      tip.style.top = `${e.clientY - rect.top + 12}px`;
    });
    bar.addEventListener('mouseleave', () => { tip.hidden = true; });
  });
}

function renderChart(data) {
  const wrap = modalEl?.querySelector('#usage-chart-wrap');
  if (!wrap) return;
  wrap.innerHTML = state.loading
    ? '<div class="usage-loading">Loading usage...</div>'
    : chartSvg(data);
  wireChartTooltips();
}

async function loadUsage() {
  if (!modalEl) return;
  state.loading = true;
  renderChart(state.data);
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
    renderUserSelect(data);
    renderSummary(data);
    renderChart(data);
  } catch (err) {
    state.loading = false;
    const wrap = modalEl.querySelector('#usage-chart-wrap');
    if (wrap) wrap.innerHTML = `<div class="usage-empty usage-error">Failed to load usage: ${esc(err.message || err)}</div>`;
  }
}

function wireControls() {
  modalEl.querySelector('#usage-close-btn')?.addEventListener('click', closeUsage);
  modalEl.querySelector('#usage-minimize-btn')?.addEventListener('click', minimizeUsage);
  modalEl.querySelector('#usage-refresh')?.addEventListener('click', loadUsage);
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
    if (picker.open) closeRangePicker();
    else openRangePicker();
  });
  modalEl.querySelector('#usage-user')?.addEventListener('change', e => {
    state.user = e.target.value || ALL_USERS;
    loadUsage();
  });
  modalEl.addEventListener('click', e => {
    if (e.target === modalEl) closeUsage();
    else if (picker.open && !e.target.closest('#usage-range-wrap')) closeRangePicker();
  });
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
  loadUsage();
}

export function isUsageOpen() {
  return !!modalEl && !modalEl.classList.contains('hidden');
}

export default {
  openUsage,
  closeUsage,
  isUsageOpen,
};

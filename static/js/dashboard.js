// static/js/dashboard.js — Usage Metrics (admin-only)

const CHART_CDN = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
let chartJsLoaded = false;
let dashboardLoaded = false;

function el(id) { return document.getElementById(id); }

function ensureChartJs() {
  if (chartJsLoaded) return Promise.resolve();
  return new Promise((resolve, reject) => {
    if (window.Chart) { chartJsLoaded = true; resolve(); return; }
    const s = document.createElement('script');
    s.src = CHART_CDN;
    s.onload = () => { chartJsLoaded = true; resolve(); };
    s.onerror = () => reject(new Error('Failed to load Chart.js'));
    document.head.appendChild(s);
  });
}

async function fetchDashboard(days = 30) {
  const res = await fetch(`/api/admin/dashboard?days=${days}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Dashboard API returned ${res.status}`);
  return res.json();
}

function fmtTokens(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}
function fmtCost(n) { return '$' + n.toFixed(2); }

const PALETTE = [
  '#4dc9f6', '#f67019', '#f53794', '#537bc4', '#acc236',
  '#166a8f', '#00a950', '#58595b', '#8549ba', '#e6194b',
  '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4',
];
function palette(i) { return PALETTE[i % PALETTE.length]; }

const charts = {};
function destroyChart(key) {
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

function renderSummaryCards(data) {
  const s = data.summary;
  const grid = el('dash-summary-grid');
  if (!grid) return;

  const cards = [
    { label: 'Total Sessions',     value: s.total_sessions.toLocaleString() },
    { label: 'Total Messages',     value: s.total_messages.toLocaleString() },
    { label: 'Total Tokens',       value: fmtTokens(s.total_tokens) },
    { label: 'Cloud Cost',         value: fmtCost(s.total_cost_usd), cls: 'dash-cost' },
    { label: 'Local Sessions',     value: s.local_sessions.toLocaleString() },
    { label: 'Cloud Sessions',     value: s.cloud_sessions.toLocaleString() },
    { label: 'Local Savings',      value: fmtCost(s.total_local_savings_usd), cls: 'dash-savings' },
  ];

  grid.innerHTML = cards.map(c =>
    `<div class="dash-stat-card${c.cls ? ' ' + c.cls : ''}">
       <div class="dash-stat-value">${c.value}</div>
       <div class="dash-stat-label">${c.label}</div>
     </div>`
  ).join('');
}

function renderTokenChart(data) {
  const ctx = el('dash-token-chart');
  if (!ctx) return;
  destroyChart('tokens');

  const daily = data.daily_usage;
  if (daily.length === 0) {
    ctx.parentElement.innerHTML = '<div class="dash-chart-empty">No token usage in this period.</div>';
    return;
  }
  charts.tokens = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.date.slice(5)),  // MM-DD
      datasets: [
        {
          label: 'Input Tokens',
          data: daily.map(d => d.input_tokens),
          backgroundColor: 'rgba(77, 201, 246, 0.7)',
          borderRadius: 3,
        },
        {
          label: 'Output Tokens',
          data: daily.map(d => d.output_tokens),
          backgroundColor: 'rgba(246, 112, 25, 0.7)',
          borderRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: 'rgba(255,255,255,0.7)', font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${fmtTokens(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 10 }, maxRotation: 45 },
          grid: { display: false },
        },
        y: {
          stacked: true,
          ticks: { color: 'rgba(255,255,255,0.5)', callback: v => fmtTokens(v) },
          grid: { color: 'rgba(255,255,255,0.06)' },
        },
      },
    },
  });
}

function renderModelChart(data) {
  const ctx = el('dash-model-chart');
  if (!ctx) return;
  destroyChart('models');

  const models = data.model_distribution.slice(0, 10);
  if (models.length === 0) {
    ctx.parentElement.innerHTML = '<div class="dash-chart-empty">No model usage in this period.</div>';
    return;
  }
  charts.models = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: models.map(m => m.model),
      datasets: [{
        data: models.map(m => m.sessions),
        backgroundColor: models.map((_, i) => palette(i)),
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: { color: 'rgba(255,255,255,0.7)', font: { size: 11 }, padding: 8, boxWidth: 12 },
        },
      },
    },
  });
}

function renderCostChart(data) {
  const ctx = el('dash-cost-chart');
  if (!ctx) return;
  destroyChart('cost');

  const costs = data.cost_by_model.slice(0, 8);
  if (costs.length === 0) {
    ctx.parentElement.querySelector('.dash-chart-empty')?.classList.remove('hidden');
    return;
  }

  charts.cost = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: costs.map(c => c.model),
      datasets: [{
        label: 'Cost (USD)',
        data: costs.map(c => c.cost_usd),
        backgroundColor: costs.map((_, i) => palette(i)),
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => fmtCost(ctx.parsed.x) } },
      },
      scales: {
        x: {
          ticks: { color: 'rgba(255,255,255,0.5)', callback: v => fmtCost(v) },
          grid: { color: 'rgba(255,255,255,0.06)' },
        },
        y: {
          ticks: { color: 'rgba(255,255,255,0.7)', font: { size: 11 } },
          grid: { display: false },
        },
      },
    },
  });
}

function renderSavingsCard(data) {
  const container = el('dash-savings-detail');
  if (!container) return;

  const savings = data.local_savings_by_model;
  if (savings.length === 0) {
    container.innerHTML = '<div class="dash-chart-empty">No local model usage in this period.</div>';
    return;
  }

  const totalSaved = data.summary.total_local_savings_usd;
  let html = `<div class="dash-savings-headline">
    <span class="dash-savings-amount">${fmtCost(totalSaved)}</span>
    <span class="dash-savings-label">estimated saved by running locally</span>
  </div>
  <div class="dash-savings-breakdown">`;

  for (const item of savings.slice(0, 8)) {
    const pct = totalSaved > 0 ? Math.round((item.savings_usd / totalSaved) * 100) : 0;
    html += `<div class="dash-savings-row">
      <span class="dash-savings-model">${item.model}</span>
      <div class="dash-savings-bar-wrap">
        <div class="dash-savings-bar" style="width:${pct}%"></div>
      </div>
      <span class="dash-savings-val">${fmtCost(item.savings_usd)}</span>
    </div>`;
  }

  html += '</div>';
  container.innerHTML = html;
}

function renderModeChart(data) {
  const ctx = el('dash-mode-chart');
  if (!ctx) return;
  destroyChart('mode');

  const modes = data.mode_distribution;
  const labels = Object.keys(modes);
  if (labels.length === 0) {
    ctx.parentElement.innerHTML = '<div class="dash-chart-empty">No usage in this period.</div>';
    return;
  }

  charts.mode = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: labels.map(l => l.charAt(0).toUpperCase() + l.slice(1)),
      datasets: [{
        data: labels.map(l => modes[l]),
        backgroundColor: ['#4dc9f6', '#f67019', '#acc236', '#f53794'],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: 'rgba(255,255,255,0.7)', font: { size: 11 }, padding: 8, boxWidth: 12 },
        },
      },
    },
  });
}

async function loadDashboard(days = 30) {
  const panel = el('dash-panel');
  if (!panel) return;

  const loading = el('dash-loading');
  const content = el('dash-content');
  if (loading) loading.classList.remove('hidden');
  if (content) content.classList.add('hidden');

  try {
    const [data] = await Promise.all([fetchDashboard(days), ensureChartJs()]);
    if (loading) loading.classList.add('hidden');

    if (data.summary.total_sessions === 0) {
      if (content) {
        content.classList.remove('hidden');
        content.innerHTML = '<div class="dash-chart-empty" style="padding:60px 20px;font-size:14px;">No usage data yet. Start chatting and metrics will appear here.</div>';
      }
      dashboardLoaded = true;
      return;
    }

    if (content) content.classList.remove('hidden');

    renderSummaryCards(data);
    renderTokenChart(data);
    renderModelChart(data);
    renderCostChart(data);
    renderSavingsCard(data);
    renderModeChart(data);
    dashboardLoaded = true;
  } catch (err) {
    console.error('Dashboard load failed:', err);
    if (loading) loading.innerHTML = `<div style="color:#e55;">Failed to load dashboard: ${err.message}</div>`;
  }
}

export function initDashboard() {
  if (dashboardLoaded) return;

  const select = el('dash-period-select');
  if (select && !select._dashBound) {
    select._dashBound = true;
    select.addEventListener('change', () => {
      dashboardLoaded = false;
      loadDashboard(parseInt(select.value, 10));
    });
  }

  const refreshBtn = el('dash-refresh-btn');
  if (refreshBtn && !refreshBtn._dashBound) {
    refreshBtn._dashBound = true;
    refreshBtn.addEventListener('click', () => refreshDashboard());
  }

  loadDashboard();
}

export function refreshDashboard() {
  dashboardLoaded = false;
  const select = el('dash-period-select');
  const days = select ? parseInt(select.value, 10) : 30;
  loadDashboard(days);
}

export default { initDashboard, refreshDashboard };

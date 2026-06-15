// static/js/chartBubble.js
// Interactive Chart.js bubbles for plot_chart tool artifacts.

const CHART_JS_SOURCES = [
  '/static/lib/chart.umd.min.js',
];
const CHART_JS_TIMEOUT_MS = 6000;
const DOWNLOAD_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const CHECK_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';

let _chartLoadPromise = null;

function _scriptKey(src) {
  try {
    return new URL(src, window.location.origin).href;
  } catch (_) {
    return src;
  }
}

function _removeFailedChartScripts() {
  document.querySelectorAll('script[data-odysseus-chartjs="1"][data-load-state="failed"]').forEach(script => script.remove());
}

function _loadChartScript(src) {
  return new Promise((resolve, reject) => {
    const key = _scriptKey(src);
    const existing = Array.from(document.querySelectorAll('script[data-odysseus-chartjs="1"]'))
      .find(script => script.dataset.srcKey === key);
    if (existing?.dataset.loadState === 'loaded') {
      if (window.Chart) resolve(window.Chart);
      else reject(new Error('Chart.js loaded without exposing window.Chart'));
      return;
    }
    if (existing?.dataset.loadState === 'loading') {
      existing.addEventListener('load', () => resolve(window.Chart), { once: true });
      existing.addEventListener('error', () => reject(new Error('Chart.js failed to load')), { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.dataset.odysseusChartjs = '1';
    script.dataset.loadState = 'loading';
    script.dataset.srcKey = key;
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      script.dataset.loadState = 'failed';
      script.remove();
      reject(new Error('Chart.js load timed out'));
    }, CHART_JS_TIMEOUT_MS);
    script.addEventListener('load', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.dataset.loadState = 'loaded';
      if (window.Chart) resolve(window.Chart);
      else {
        script.dataset.loadState = 'failed';
        reject(new Error('Chart.js loaded without exposing window.Chart'));
      }
    }, { once: true });
    script.addEventListener('error', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.dataset.loadState = 'failed';
      script.remove();
      reject(new Error('Chart.js failed to load'));
    }, { once: true });
    document.head.appendChild(script);
  });
}

function _loadChartJs() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (_chartLoadPromise) return _chartLoadPromise;
  _removeFailedChartScripts();
  _chartLoadPromise = (async () => {
    let lastError = null;
    for (const src of CHART_JS_SOURCES) {
      try {
        return await _loadChartScript(src);
      } catch (err) {
        lastError = err;
      }
    }
    throw lastError || new Error('Chart.js failed to load');
  })();
  _chartLoadPromise.catch(() => {
    _chartLoadPromise = null;
  });
  return _chartLoadPromise;
}

function _cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function _theme() {
  return {
    bg: _cssVar('--bg', '#111'),
    fg: _cssVar('--fg', '#eee'),
    panel: _cssVar('--panel', _cssVar('--bg', '#111')),
    border: _cssVar('--border', '#444'),
    accent: _cssVar('--red', _cssVar('--fg', '#eee')),
  };
}

function _safeText(value, fallback) {
  const text = String(value ?? '').trim();
  return text || fallback || '';
}

function _safeFileName(value) {
  const name = _safeText(value, 'chart').slice(0, 48).replace(/[^a-zA-Z0-9 _-]/g, '').trim();
  return (name || 'chart') + '.png';
}

function _clamp(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(n, min), max);
}

function _isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function _axisLabel(value) {
  if (_isFiniteNumber(value)) return value;
  return String(value ?? '');
}

function _safeCssColor(raw, fallback) {
  const value = String(raw || '').trim();
  if (/^#[0-9a-f]{3,8}$/i.test(value)) return value;
  if (/^rgba?\(\s*[\d.\s%,]+\)$/i.test(value)) return value;
  if (/^hsla?\(\s*[\d.\s%,]+\)$/i.test(value)) return value;
  return fallback;
}

function _hexToRgb(value) {
  const text = String(value || '').trim();
  const m = text.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  let hex = m[1];
  if (hex.length === 3) hex = hex.split('').map(ch => ch + ch).join('');
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

function _alpha(color, opacity) {
  const rgb = _hexToRgb(color);
  if (rgb) return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${opacity})`;
  if (/^rgb\(/i.test(color)) return color.replace(/^rgb\(/i, 'rgba(').replace(/\)$/, `, ${opacity})`);
  return color;
}

function _palette(theme) {
  return [
    theme.accent,
    theme.fg,
    _alpha(theme.accent, 0.68),
    _alpha(theme.fg, 0.62),
    _alpha(theme.accent, 0.42),
    _alpha(theme.fg, 0.38),
  ];
}

function _commonOptions(spec, theme) {
  const gridColor = _alpha(theme.border, 0.55);
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 220 },
    color: theme.fg,
    interaction: { intersect: false, mode: 'nearest' },
    plugins: {
      legend: {
        display: spec.legend !== false,
        labels: {
          color: theme.fg,
          boxWidth: 10,
          boxHeight: 10,
          usePointStyle: true,
        },
      },
      title: {
        display: !!spec.title,
        text: spec.title || '',
        color: theme.fg,
        font: { weight: '600', size: 14 },
        padding: { bottom: 12 },
      },
      tooltip: {
        enabled: true,
        backgroundColor: theme.bg,
        titleColor: theme.fg,
        bodyColor: theme.fg,
        borderColor: theme.border,
        borderWidth: 1,
        displayColors: true,
      },
    },
    scales: {
      x: {
        grid: { color: spec.grid === false ? 'transparent' : gridColor },
        ticks: { color: theme.fg, maxRotation: 0, autoSkip: true },
        title: { display: !!spec.x_label, text: spec.x_label || '', color: theme.fg },
      },
      y: {
        grid: { color: spec.grid === false ? 'transparent' : gridColor },
        ticks: { color: theme.fg },
        title: { display: !!spec.y_label, text: spec.y_label || '', color: theme.fg },
      },
    },
  };
}

function _axisLabels(series) {
  const labels = [];
  const seen = new Set();
  (Array.isArray(series) ? series : []).forEach(item => {
    const xs = Array.isArray(item.x) ? item.x : [];
    xs.forEach(value => {
      const label = _axisLabel(value);
      const key = String(label);
      if (!seen.has(key)) {
        seen.add(key);
        labels.push(label);
      }
    });
  });
  return labels;
}

function _alignedY(item, labels) {
  if (!labels.length) return Array.isArray(item.y) ? item.y : [];
  const xs = Array.isArray(item.x) ? item.x : [];
  const ys = Array.isArray(item.y) ? item.y : [];
  const byLabel = new Map();
  xs.forEach((x, idx) => byLabel.set(String(_axisLabel(x)), ys[idx]));
  return labels.map(label => byLabel.has(String(label)) ? byLabel.get(String(label)) : null);
}

function _axisDatasets(spec, theme, mode, labels) {
  const colors = _palette(theme);
  const series = Array.isArray(spec.series) ? spec.series : [];
  return series.map((item, idx) => {
    const color = _safeCssColor(item.color, colors[idx % colors.length]);
    const dataset = {
      label: _safeText(item.name, `Series ${idx + 1}`),
      data: _alignedY(item, labels),
      borderColor: color,
      backgroundColor: mode === 'area' ? _alpha(color, 0.22) : _alpha(color, 0.72),
      pointBackgroundColor: color,
      pointBorderColor: theme.panel,
      pointRadius: mode === 'line' || mode === 'area' ? 3 : 4,
      pointHoverRadius: 5,
      borderWidth: 2,
      tension: mode === 'line' || mode === 'area' ? 0.28 : 0,
      fill: mode === 'area',
      _odyPaletteIndex: idx,
      _odyPinnedColor: !!item.color,
    };
    if (mode === 'scatter-categorical') {
      dataset.showLine = false;
    }
    return dataset;
  });
}

function _histogram(values, bins) {
  const nums = (Array.isArray(values) ? values : []).filter(_isFiniteNumber);
  if (!nums.length) return { labels: [], data: [] };
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const count = Math.max(1, Math.min(100, Math.floor(Number(bins) || 20)));
  if (min === max) return { labels: [String(min)], data: [nums.length] };
  const width = (max - min) / count;
  const data = Array(count).fill(0);
  nums.forEach(value => {
    const idx = Math.min(count - 1, Math.floor((value - min) / width));
    data[idx] += 1;
  });
  const labels = data.map((_n, idx) => {
    const start = min + idx * width;
    const end = idx === count - 1 ? max : start + width;
    return `${start.toFixed(2)}-${end.toFixed(2)}`;
  });
  return { labels, data };
}

function _configFromSpec(spec) {
  const theme = _theme();
  const type = _safeText(spec.chart_type || spec.type, 'line').toLowerCase();
  const options = _commonOptions(spec, theme);
  const colors = _palette(theme);

  if (type === 'pie') {
    const labels = Array.isArray(spec.labels) ? spec.labels.map(_axisLabel) : [];
    const values = Array.isArray(spec.values) ? spec.values : [];
    delete options.scales;
    options.plugins.legend.display = spec.legend !== false;
    return {
      type: 'pie',
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: values.map((_v, i) => _alpha(colors[i % colors.length], 0.78)),
          borderColor: theme.panel,
          borderWidth: 2,
          _odyPaletteIndex: 0,
        }],
      },
      options,
    };
  }

  if (type === 'histogram') {
    const hist = _histogram(spec.values, spec.bins);
    return {
      type: 'bar',
      data: {
        labels: hist.labels,
        datasets: [{
          label: _safeText(spec.y_label, 'Count'),
          data: hist.data,
          backgroundColor: _alpha(colors[0], 0.72),
          borderColor: colors[0],
          borderWidth: 1,
          _odyPaletteIndex: 0,
        }],
      },
      options,
    };
  }

  const labels = _axisLabels(spec.series);
  const firstSeries = Array.isArray(spec.series) && spec.series[0] ? spec.series[0] : {};
  const numericScatter = type === 'scatter' && Array.isArray(firstSeries.x) && firstSeries.x.every(_isFiniteNumber);
  if (numericScatter) {
    options.scales.x.type = 'linear';
    const datasets = (Array.isArray(spec.series) ? spec.series : []).map((item, idx) => {
      const color = _safeCssColor(item.color, colors[idx % colors.length]);
      const xs = Array.isArray(item.x) ? item.x : [];
      const ys = Array.isArray(item.y) ? item.y : [];
      return {
        label: _safeText(item.name, `Series ${idx + 1}`),
        data: ys.map((y, i) => ({ x: xs[i], y })),
        borderColor: color,
        backgroundColor: _alpha(color, 0.72),
        pointBackgroundColor: color,
        pointBorderColor: theme.panel,
        pointRadius: 4,
        pointHoverRadius: 5,
        _odyPaletteIndex: idx,
        _odyPinnedColor: !!item.color,
      };
    });
    return { type: 'scatter', data: { datasets }, options };
  }

  const chartType = type === 'bar' ? 'bar' : 'line';
  const mode = type === 'scatter' ? 'scatter-categorical' : type;
  const datasets = _axisDatasets(spec, theme, mode, labels);
  if (type === 'bar') {
    datasets.forEach(ds => {
      ds.borderWidth = 1;
      ds.borderRadius = 4;
      ds.pointRadius = 0;
    });
  }
  return { type: chartType, data: { labels, datasets }, options };
}

function _applyTheme(chart, spec) {
  const theme = _theme();
  const colors = _palette(theme);
  const opts = chart.options || {};
  opts.color = theme.fg;
  if (opts.plugins) {
    if (opts.plugins.legend?.labels) opts.plugins.legend.labels.color = theme.fg;
    if (opts.plugins.title) opts.plugins.title.color = theme.fg;
    if (opts.plugins.tooltip) {
      opts.plugins.tooltip.backgroundColor = theme.bg;
      opts.plugins.tooltip.titleColor = theme.fg;
      opts.plugins.tooltip.bodyColor = theme.fg;
      opts.plugins.tooltip.borderColor = theme.border;
    }
  }
  for (const axis of ['x', 'y']) {
    if (!opts.scales?.[axis]) continue;
    opts.scales[axis].grid.color = spec.grid === false ? 'transparent' : _alpha(theme.border, 0.55);
    opts.scales[axis].ticks.color = theme.fg;
    opts.scales[axis].title.color = theme.fg;
  }
  (chart.data.datasets || []).forEach((dataset, idx) => {
    if (dataset._odyPinnedColor) return;
    const color = colors[(dataset._odyPaletteIndex ?? idx) % colors.length];
    if (Array.isArray(dataset.backgroundColor)) {
      dataset.backgroundColor = dataset.backgroundColor.map((_v, i) => _alpha(colors[i % colors.length], 0.78));
      dataset.borderColor = theme.panel;
    } else {
      dataset.borderColor = color;
      dataset.backgroundColor = spec.chart_type === 'area' ? _alpha(color, 0.22) : _alpha(color, 0.72);
      dataset.pointBackgroundColor = color;
      dataset.pointBorderColor = theme.panel;
    }
  });
  chart.update('none');
}

function _downloadPng(chart, spec, button) {
  try {
    const a = document.createElement('a');
    a.href = chart.toBase64Image('image/png', 1);
    a.download = _safeFileName(spec.title || spec.chart_type || 'chart');
    document.body.appendChild(a);
    a.click();
    a.remove();
    button.innerHTML = CHECK_ICON;
    setTimeout(() => { button.innerHTML = DOWNLOAD_ICON; }, 1500);
  } catch (_) {
    button.textContent = 'x';
    setTimeout(() => { button.innerHTML = DOWNLOAD_ICON; }, 1500);
  }
}

function _renderChart(canvas, spec, status, downloadBtn) {
  status.textContent = 'Loading Chart.js...';
  _loadChartJs()
    .then((Chart) => {
      if (!canvas.isConnected) return;
      const config = _configFromSpec(spec);
      const chart = new Chart(canvas, config);
      canvas._odysseusChart = chart;
      status.remove();
      downloadBtn.disabled = false;
      downloadBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _downloadPng(chart, spec, downloadBtn);
      });
      const observer = new MutationObserver(() => _applyTheme(chart, spec));
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
      canvas._odysseusThemeObserver = observer;
    })
    .catch((err) => {
      status.textContent = '';
      const msg = document.createElement('span');
      msg.textContent = err?.message || 'Chart.js failed to load.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'generated-chart-retry';
      retry.textContent = 'Retry';
      retry.addEventListener('click', (e) => {
        e.stopPropagation();
        status.textContent = 'Loading Chart.js...';
        _renderChart(canvas, spec, status, downloadBtn);
      });
      status.appendChild(msg);
      status.appendChild(retry);
    });
}

export function buildChartBubble(chartSpec) {
  const spec = chartSpec && typeof chartSpec === 'object' ? chartSpec : {};
  const chartType = _safeText(spec.chart_type || spec.type, 'chart').toLowerCase();
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-ai generated-chart-wrap';

  const role = document.createElement('div');
  role.className = 'role';
  role.textContent = `chart:${chartType}`;
  wrap.appendChild(role);

  const body = document.createElement('div');
  body.className = 'body';
  const shell = document.createElement('div');
  shell.className = 'generated-chart';
  const canvasWrap = document.createElement('div');
  canvasWrap.className = 'generated-chart-canvas-wrap';
  const height = spec.size && _clamp(spec.size.height, 220, 900, 360);
  if (height) canvasWrap.style.height = `${height}px`;
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', _safeText(spec.title, `${chartType} chart`));
  const status = document.createElement('div');
  status.className = 'generated-chart-status';
  status.textContent = 'Loading Chart.js...';
  canvasWrap.appendChild(canvas);
  canvasWrap.appendChild(status);
  shell.appendChild(canvasWrap);
  body.appendChild(shell);
  wrap.appendChild(body);

  const footer = document.createElement('div');
  footer.className = 'msg-footer generated-chart-footer';
  const actions = document.createElement('span');
  actions.className = 'msg-actions';
  const dlBtn = document.createElement('button');
  dlBtn.className = 'footer-copy-btn';
  dlBtn.type = 'button';
  dlBtn.title = 'Download chart PNG';
  dlBtn.disabled = true;
  dlBtn.innerHTML = DOWNLOAD_ICON;
  actions.appendChild(dlBtn);
  footer.appendChild(actions);
  const metrics = document.createElement('span');
  metrics.className = 'response-metrics';
  metrics.textContent = `Chart.js - ${chartType}`;
  footer.appendChild(metrics);
  wrap.appendChild(footer);

  _renderChart(canvas, spec, status, dlBtn);
  return wrap;
}

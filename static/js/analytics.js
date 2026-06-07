/* Usage Analytics Dashboard — SVG charts, vanilla JS, no dependencies. */

(function() {
  'use strict';

  var DASH = document.getElementById('analytics-dashboard');
  if (!DASH) return;

  var _range = '30d';
  var _ranges = [
    { key: '7d',  label: '7 days' },
    { key: '30d', label: '30 days' },
    { key: '90d', label: '90 days' },
    { key: '1y',  label: '1 year' },
    { key: 'all', label: 'All time' },
  ];

  /* ── Fetch helper ── */
  function api(path) {
    return fetch('/api/analytics/' + path, { credentials: 'same-origin' })
      .then(function(r) { return r.json(); })
      .catch(function(e) { console.warn('analytics fetch failed:', e); return null; });
  }

  /* ── Summary cards ── */
  function renderSummary(data) {
    if (!data) return '<div class="analytics-loading">No data yet.</div>';
    var cards = [
      { label: 'Total Tokens',     value: (data.total_tokens || 0).toLocaleString(), icon: '#' },
      { label: 'Est. Cost',        value: '$' + (data.estimated_cost || 0).toFixed(4), icon: '$' },
      { label: 'Requests',         value: (data.total_requests || 0).toLocaleString(), icon: 'R' },
      { label: 'Sessions',         value: (data.total_sessions || 0).toLocaleString(), icon: 'S' },
      { label: 'Avg Response',     value: (data.avg_response_time_ms || 0) + ' ms', icon: 'T' },
      { label: 'Avg Tok/s',        value: (data.avg_tokens_per_second || 0).toFixed(1), icon: 'Z' },
    ];
    return '<div class="analytics-cards">' + cards.map(function(c) {
      return '<div class="analytics-card"><div class="analytics-card-icon">' + c.icon + '</div><div class="analytics-card-body"><div class="analytics-card-value">' + c.value + '</div><div class="analytics-card-label">' + c.label + '</div></div></div>';
    }).join('') + '</div>';
  }

  /* ── SVG line chart ── */
  function renderLineChart(data, id) {
    if (!data || !data.days || data.days.length < 2) return '<div class="analytics-chart-empty">Not enough data for a trend chart.</div>';
    var W = 600, H = 200, PAD = 10, PAD_B = 24;
    var cw = W - PAD * 2, ch = H - PAD - PAD_B;

    var series = data[id] || [];
    var max = Math.max.apply(null, series) || 1;
    var len = series.length;

    // Grid lines (5 horizontal)
    var gridLines = '';
    for (var i = 0; i <= 4; i++) {
      var y = PAD + ch - (ch * i / 4);
      gridLines += '<line x1="' + PAD + '" y1="' + y + '" x2="' + (PAD + cw) + '" y2="' + y + '" stroke="var(--border)" stroke-width="0.5"/>';
      gridLines += '<text x="' + (PAD - 4) + '" y="' + (y + 3) + '" text-anchor="end" fill="var(--fg)" opacity="0.4" font-size="9">' + Math.round(max * i / 4) + '</text>';
    }

    // Build polyline points
    var pts = [];
    for (var j = 0; j < len; j++) {
      var x = PAD + (cw * j / Math.max(len - 1, 1));
      var y = PAD + ch - (ch * series[j] / max);
      pts.push(x + ',' + y);
    }

    // Gradient fill
    var fillId = 'analytics-fill-' + id;
    var grad = '<defs><linearGradient id="' + fillId + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.2"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0.01"/></linearGradient></defs>';

    // X-axis labels (show ~6 evenly spaced)
    var xLabels = '';
    var step = Math.max(1, Math.floor(len / 6));
    for (var k = 0; k < len; k += step) {
      var lx = PAD + (cw * k / Math.max(len - 1, 1));
      xLabels += '<text x="' + lx + '" y="' + (H - 2) + '" text-anchor="middle" fill="var(--fg)" opacity="0.4" font-size="8">' + (data.days[k] || '').slice(5) + '</text>';
    }

    // Area fill
    var fillPts = pts.slice();
    fillPts.unshift(PAD + ',' + (PAD + ch));
    fillPts.push((PAD + cw) + ',' + (PAD + ch));
    var area = '<polygon points="' + fillPts.join(' ') + '" fill="url(#' + fillId + ')" />';
    var line = '<polyline points="' + pts.join(' ') + '" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>';

    return '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;max-width:' + W + 'px;height:' + H + 'px;" class="analytics-chart-svg">' + grad + gridLines + area + line + xLabels + '</svg>';
  }

  /* ── Model breakdown horizontal bars ── */
  function renderModelBars(models) {
    if (!models || models.length === 0) return '<div class="analytics-chart-empty">No model data yet.</div>';
    var maxRequests = models[0].requests || 1;
    var rows = models.map(function(m) {
      var pct = Math.min(100, (m.requests / maxRequests) * 100);
      return '<div class="analytics-bar-row"><div class="analytics-bar-label">' + m.model + '</div><div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:' + pct + '%"></div></div><div class="analytics-bar-stats"><span>' + m.total_tokens.toLocaleString() + ' tok</span><span>$' + m.estimated_cost.toFixed(4) + '</span><span>' + m.requests + ' req</span></div></div>';
    }).join('');
    return '<div class="analytics-bars">' + rows + '</div>';
  }

  /* ── Heatmap grid ── */
  function renderHeatmap(data) {
    if (!data || !data.days || data.days.length === 0) return '<div class="analytics-chart-empty">No activity data for heatmap.</div>';

    var maxVal = 0;
    data.data.forEach(function(row) { row.forEach(function(v) { if (v > maxVal) maxVal = v; }); });
    maxVal = maxVal || 1;

    var labels = data.days.map(function(d) { return d.slice(5); });
    var hours = ['12a','1a','2a','3a','4a','5a','6a','7a','8a','9a','10a','11a','12p','1p','2p','3p','4p','5p','6p','7p','8p','9p','10p','11p'];

    var html = '<div class="analytics-heatmap-scroll"><div class="analytics-heatmap">';

    // Hour labels (top)
    html += '<div class="analytics-heatmap-row"><div class="analytics-heatmap-day"></div>';
    hours.forEach(function(h) {
      html += '<div class="analytics-heatmap-cell analytics-heatmap-hour-label">' + h + '</div>';
    });
    html += '</div>';

    for (var di = 0; di < data.data.length; di++) {
      var row = data.data[di];
      html += '<div class="analytics-heatmap-row"><div class="analytics-heatmap-day">' + labels[di] + '</div>';
      for (var hi = 0; hi < 24; hi++) {
        var val = row[hi] || 0;
        var intensity = Math.ceil((val / maxVal) * 4);
        html += '<div class="analytics-heatmap-cell analytics-heatmap-l' + intensity + '" title="' + labels[di] + ' ' + hours[hi] + ': ' + val + ' req">' + (val || '') + '</div>';
      }
      html += '</div>';
    }

    html += '</div></div>';
    return html;
  }

  /* ── Top sessions table ── */
  function renderSessions(sessions) {
    if (!sessions || sessions.length === 0) return '<div class="analytics-chart-empty">No session data.</div>';
    var rows = sessions.map(function(s) {
      var name = s.session_name && s.session_name !== s.session_id ? s.session_name : '(session)';
      return '<tr><td class="analytics-td-name">' + name + '</td><td>' + s.total_tokens.toLocaleString() + '</td><td>$' + s.estimated_cost.toFixed(4) + '</td><td>' + s.requests + '</td><td class="analytics-td-date">' + (s.last_used ? s.last_used.slice(0, 10) : '') + '</td></tr>';
    }).join('');
    return '<table class="analytics-table"><thead><tr><th>Session</th><th>Tokens</th><th>Cost</th><th>Req</th><th>Last</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  /* ── Range selector ── */
  function renderRange() {
    return '<div class="analytics-range">' + _ranges.map(function(r) {
      return '<button class="analytics-range-btn' + (r.key === _range ? ' active' : '') + '" data-range="' + r.key + '">' + r.label + '</button>';
    }).join('') + '</div>';
  }

  /* ── Section wrapper ── */
  function section(title, content, cls) {
    return '<div class="analytics-section' + (cls ? ' ' + cls : '') + '"><div class="analytics-section-title">' + title + '</div>' + content + '</div>';
  }

  /* ── Main render ── */
  function render() {
    DASH.innerHTML = '<div class="analytics-loading">Loading analytics...</div>' + renderRange();

    // Bind range buttons
    Array.from(DASH.querySelectorAll('.analytics-range-btn')).forEach(function(btn) {
      btn.addEventListener('click', function() {
        _range = this.dataset.range;
        render();
      });
    });

    var range = _range;
    Promise.all([
      api('summary?range=' + range),
      api('timeseries?range=' + range),
      api('models?range=' + range),
      api('heatmap?range=' + range),
      api('sessions?range=' + range),
    ]).then(function(results) {
      console.log('[analytics] API results:', results.map(function(r, i) { return i + ':' + (r ? Object.keys(r).length + ' keys' : 'null'); }));
      var summary = results[0], timeseries = results[1], modelData = results[2], heatmap = results[3], sessions = results[4];
      var html = renderRange() +
        section('Summary', renderSummary(summary)) +
        section('Token Usage Trend', '<div class="analytics-chart-row">' +
          '<div class="analytics-chart-col">' + renderLineChart(timeseries, 'input_tokens') + '<div class="analytics-chart-label">Input Tokens</div></div>' +
          '<div class="analytics-chart-col">' + renderLineChart(timeseries, 'output_tokens') + '<div class="analytics-chart-label">Output Tokens</div></div>' +
        '</div>') +
        section('Requests Over Time', renderLineChart(timeseries, 'requests')) +
        section('Models by Usage', renderModelBars(modelData ? modelData.models : null)) +
        section('Activity Heatmap (Hourly)', renderHeatmap(heatmap), 'analytics-section-heatmap') +
        section('Top Sessions', renderSessions(sessions ? sessions.sessions : null));
      DASH.innerHTML = html;

      // Re-bind range buttons after re-render
      Array.from(DASH.querySelectorAll('.analytics-range-btn')).forEach(function(btn) {
        btn.addEventListener('click', function() {
          _range = this.dataset.range;
          render();
        });
      });
    });
  }

  /* ── Init: expose to settings tab system ── */
  window.initAnalytics = function() {
    console.log('[analytics] initAnalytics called');
    render();
  };

  // Auto-init if the dashboard is visible on load
  if (DASH.offsetParent !== null) {
    render();
  }
})();

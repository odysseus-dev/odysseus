// DOM-free view-model + renderer for per-account ChatGPT Subscription usage.
//
// The backend (/api/chatgpt-subscription/accounts/{auth_id}/usage) returns a
// normalized, credential-free payload. This module turns it into something the
// Settings "Added Models" card can render, defensively: unknown buckets are
// kept, missing windows are tolerated and nothing (reset times, percentages)
// is ever invented.

function _num(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function _clampPercent(value) {
  const n = _num(value);
  if (n === null) return null;
  return Math.max(0, Math.min(100, n));
}

function _defaultEsc(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function formatDuration(totalSeconds) {
  const secs = _num(totalSeconds);
  if (secs === null) return '';
  if (secs <= 0) return 'now';
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  if (minutes > 0) return `${minutes}m`;
  return '<1m';
}

export function formatResetIn(resetsAt, nowSeconds) {
  const at = _num(resetsAt);
  if (at === null || at <= 0) return '';
  const now = _num(nowSeconds);
  if (now === null) return '';
  const delta = at - now;
  if (delta <= 0) return 'resets now';
  return `resets in ${formatDuration(delta)}`;
}

export function formatPercent(value) {
  const n = _clampPercent(value);
  if (n === null) return '';
  const rounded = Math.round(n);
  return `${rounded}%`;
}

export function planDisplayName(planType) {
  if (typeof planType !== 'string' || !planType.trim()) return '';
  const key = planType.trim().toLowerCase();
  const known = {
    free: 'Free', go: 'Go', plus: 'Plus', pro: 'Pro', prolite: 'Pro Lite', team: 'Team',
    business: 'Business', enterprise: 'Enterprise', edu: 'Edu', education: 'Education',
    guest: 'Guest',
  };
  if (known[key]) return known[key];
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function _windowViewModel(win, nowSeconds) {
  if (!win || typeof win !== 'object') return null;
  const used = _clampPercent(win.used_percent);
  const remaining = used === null ? _clampPercent(win.remaining_percent) : Math.round((100 - used) * 100) / 100;
  const windowMinutes = _num(win.window_minutes);
  const resetsAt = _num(win.resets_at);
  return {
    kind: typeof win.kind === 'string' ? win.kind : '',
    name: typeof win.name === 'string' && win.name ? win.name : 'LIMIT',
    usedPercent: used,
    remainingPercent: remaining,
    usedLabel: used === null ? 'usage unknown' : `${formatPercent(used)} used`,
    remainingLabel: remaining === null ? '' : `${formatPercent(remaining)} remaining`,
    windowMinutes,
    resetsAt,
    resetLabel: formatResetIn(resetsAt, nowSeconds),
    exhausted: used !== null && used >= 100,
  };
}

/**
 * Build a render-ready view model from the backend usage payload.
 *
 * `payload` is the JSON body of the usage route. `nowSeconds` is injected so
 * reset countdowns are deterministic in tests.
 */
export function buildUsageViewModel(payload, nowSeconds = Math.floor(Date.now() / 1000)) {
  const account = (payload && typeof payload.account === 'object' && payload.account) || {};
  const base = {
    authId: typeof account.auth_id === 'string' ? account.auth_id : '',
    label: typeof account.label === 'string' ? account.label : '',
    name: typeof account.name === 'string' ? account.name : '',
  };
  if (!payload || typeof payload !== 'object' || payload.available !== true || !payload.usage || typeof payload.usage !== 'object') {
    const reason = payload && typeof payload.reason === 'string' ? payload.reason : 'unavailable';
    const messages = {
      reauth: 'Usage unavailable — account may need reconnecting',
      rate_limited: 'Usage temporarily unavailable (rate limited)',
      timeout: 'Usage unavailable (timed out)',
      network: 'Usage unavailable (network)',
      malformed: 'Usage unavailable (unexpected response)',
      upstream: 'Usage unavailable',
    };
    return {
      ...base,
      available: false,
      reason,
      reconnectSuggested: !!(payload && payload.reconnect_suggested),
      message: messages[reason] || 'Usage unavailable',
      plan: '',
      limits: [],
    };
  }
  const usage = payload.usage;
  const limits = [];
  const rawLimits = Array.isArray(usage.limits) ? usage.limits : [];
  rawLimits.forEach((limit) => {
    if (!limit || typeof limit !== 'object') return;
    const windows = (Array.isArray(limit.windows) ? limit.windows : [])
      .map((w) => _windowViewModel(w, nowSeconds))
      .filter(Boolean);
    const limitId = typeof limit.limit_id === 'string' ? limit.limit_id : '';
    const limitName = typeof limit.limit_name === 'string' ? limit.limit_name : '';
    limits.push({
      limitId,
      title: limitId === 'codex' ? '' : (limitName || limitId || 'Additional limit'),
      modelSlug: typeof limit.normal_model_slug === 'string' ? limit.normal_model_slug : '',
      limitReached: limit.limit_reached === true,
      windows,
    });
  });
  return {
    ...base,
    available: true,
    reason: '',
    reconnectSuggested: false,
    message: '',
    plan: planDisplayName(usage.plan_type),
    ordinaryUsageAllowed: typeof usage.ordinary_usage_allowed === 'boolean' ? usage.ordinary_usage_allowed : null,
    rateLimitReachedType: typeof usage.rate_limit_reached_type === 'string' ? usage.rate_limit_reached_type : '',
    cached: payload.usage.cached === true,
    fetchedAt: _num(usage.fetched_at),
    limits,
    hasWindows: limits.some((l) => l.windows.length > 0),
  };
}

function _barHtml(win, esc) {
  const used = win.usedPercent === null ? 0 : win.usedPercent;
  const tone = win.usedPercent === null ? 'unknown' : (used >= 90 ? 'critical' : (used >= 70 ? 'warn' : 'ok'));
  return (
    `<div class="adm-chatgpt-usage-row" data-usage-window="${esc(win.kind || win.name)}">` +
      `<div class="adm-chatgpt-usage-head">` +
        `<span class="adm-chatgpt-usage-name">${esc(win.name)}</span>` +
        `<span class="adm-chatgpt-usage-remaining">${esc(win.remainingLabel || win.usedLabel)}</span>` +
      `</div>` +
      `<div class="adm-chatgpt-usage-bar adm-chatgpt-usage-${tone}" role="progressbar" aria-valuemin="0" aria-valuemax="100"` +
        (win.usedPercent === null ? '' : ` aria-valuenow="${Math.round(used)}"`) +
        ` aria-label="${esc(win.name)} ${esc(win.usedLabel)}">` +
        `<span class="adm-chatgpt-usage-fill" style="width:${Math.max(0, Math.min(100, used)).toFixed(0)}%"></span>` +
      `</div>` +
      `<div class="adm-chatgpt-usage-meta">` +
        `<span>${esc(win.usedLabel)}</span>` +
        (win.resetLabel ? `<span>${esc(win.resetLabel)}</span>` : '') +
      `</div>` +
    `</div>`
  );
}

/**
 * Render the compact usage card body for one account. `esc` is the host
 * page's HTML escaper (defaults to a local one). Buttons carry data
 * attributes with the exact auth/endpoint ids so handlers target only this
 * account.
 */
export function renderUsageCardHtml(viewModel, options = {}) {
  const esc = typeof options.esc === 'function' ? options.esc : _defaultEsc;
  const vm = viewModel || {};
  const authId = esc(vm.authId || '');
  const endpointId = esc(options.endpointId || '');
  const includeReconnect = options.includeReconnect !== false;
  const buttons =
    `<div class="adm-chatgpt-usage-actions">` +
      `<button type="button" class="admin-btn-sm" data-adm-chatgpt-usage-refresh="${authId}" data-chatgpt-endpoint-id="${endpointId}">Refresh usage</button>` +
      (includeReconnect ? `<button type="button" class="admin-btn-sm" data-adm-chatgpt-reconnect="${authId}" data-chatgpt-endpoint-id="${endpointId}">Reconnect</button>` : '') +
    `</div>`;
  if (!vm.available) {
    return (
      `<div class="adm-chatgpt-usage adm-chatgpt-usage-unavailable" data-adm-chatgpt-usage="${authId}">` +
        `<div class="adm-chatgpt-usage-status">${esc(vm.message || 'Usage unavailable')}</div>` +
        buttons +
      `</div>`
    );
  }
  const plan = vm.plan ? `<span class="admin-badge adm-chatgpt-plan">${esc(vm.plan)}</span>` : '';
  const limitsHtml = (vm.limits || []).map((limit) => {
    const title = limit.title
      ? `<div class="adm-chatgpt-usage-limit-title">${esc(limit.title)}${limit.modelSlug ? ` <span class="adm-chatgpt-usage-model">${esc(limit.modelSlug)}</span>` : ''}</div>`
      : '';
    return `<div class="adm-chatgpt-usage-limit" data-usage-limit="${esc(limit.limitId)}">${title}${limit.windows.length ? limit.windows.map((w) => _barHtml(w, esc)).join('') : '<div class="adm-chatgpt-usage-status">No rate-limit windows reported</div>'}</div>`;
  }).join('');
  const blocked = vm.ordinaryUsageAllowed === false
    ? `<div class="adm-chatgpt-usage-status adm-chatgpt-usage-blocked">Usage currently blocked${vm.rateLimitReachedType ? ` (${esc(vm.rateLimitReachedType.replace(/_/g, ' '))})` : ''}</div>`
    : '';
  return (
    `<div class="adm-chatgpt-usage" data-adm-chatgpt-usage="${authId}">` +
      (plan ? `<div class="adm-chatgpt-usage-plan">${plan}</div>` : '') +
      blocked +
      limitsHtml +
      buttons +
    `</div>`
  );
}

/** True when an endpoint row from /api/model-endpoints is a ChatGPT account. */
export function isChatgptSubscriptionEndpoint(ep) {
  return !!(ep && ep.provider === 'chatgpt-subscription' && ep.provider_auth_id);
}

/** Owner-visible account title, e.g. "ChatGPT · codex00". */
export function accountTitle(ep) {
  if (!ep) return '';
  if (ep.account_label) return `ChatGPT · ${ep.account_label}`;
  return ep.name || 'ChatGPT Subscription';
}

export default {
  buildUsageViewModel,
  renderUsageCardHtml,
  formatResetIn,
  formatDuration,
  formatPercent,
  planDisplayName,
  isChatgptSubscriptionEndpoint,
  accountTitle,
};

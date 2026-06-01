// static/js/codexConnect.js — subscription (OAuth) provider connect UI
//
// Owner-scoped, self-service: any signed-in user links their own provider
// subscription (e.g. ChatGPT Plus/Pro) via the device-code flow. The OAuth
// dance runs server-side (Odysseus is a remote host), so this module only ever
// shows the user-facing `user_code` + `verification_uri` and polls connection
// STATE — never any token material.
//
// Two mounts inside the Services tab, both data-driven from the registry below:
//   #codex-sub-providers  (Add Models → Subscription)   provider picker + OAuth card
//   #adm-epList-sub       (Added Models → Subscription)  connected rows + disconnect
//
// The Add Models side mirrors the API subsection's shape: a provider dropdown
// (same .adm-provider-* classes, own ids) selects one provider, and a single
// generic OAuth card renders beneath it from that provider's descriptor —
// adding a provider is one registry entry plus its backend routes, no new UI.
//
// Connected rows render from each provider's owner-scoped /status endpoint,
// NOT from GET /api/model-endpoints — that list is admin-only, so regular
// users would never see their own connection through it.
//
// Backend (see routes/codex_oauth_routes.py), per provider under `api`:
//   POST  /connect                  -> {attempt_id, endpoint_id, user_code, verification_uri, interval, expires_at}
//   GET   /connect/{attempt_id}     -> {status, user_code, verification_uri, expires_at, ...}
//   POST  /connect/{attempt_id}/cancel
//   GET   /status                   -> {connected:[...], pending:[...]}
//   POST  /disconnect/{endpoint_id}

import uiModule from './ui.js';
import { providerLogo } from './providers.js';

// Registry of subscription/OAuth providers. The UI is data-driven from this
// list — a future provider costs one entry here plus its backend routes.
// Descriptor: {id, label, sub, logoKey, api, urlPrefix} (+ placeholder:true
// for a not-yet-wired provider: selectable, renders the card, no backend calls).
const SUBSCRIPTION_PROVIDERS = [
  {
    id: 'openai-codex',
    label: 'ChatGPT',
    sub: 'Plus / Pro',
    logoKey: 'openai',
    api: '/api/providers/openai-codex',
    // Owns ModelEndpoint rows whose base_url starts with this prefix; such
    // rows are managed here and filtered out of the generic endpoint list
    // (its Delete bypasses the provider's disconnect/token-purge path).
    urlPrefix: 'https://chatgpt.com/backend-api/codex',
  },
];

function providerById(id) {
  return SUBSCRIPTION_PROVIDERS.find(p => p.id === id) || null;
}

/** True if `url` belongs to a subscription provider's endpoint (used by
 *  admin.js to keep those rows out of the generic Added Models lists). */
export function isSubscriptionEndpointUrl(url) {
  const u = String(url || '').replace(/\/+$/, '');
  return SUBSCRIPTION_PROVIDERS.some(p => p.urlPrefix && u.startsWith(p.urlPrefix));
}

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(String(s == null ? '' : s)); }

async function api(provider, path, opts = {}) {
  const res = await fetch(provider.api + path, { credentials: 'same-origin', ...opts });
  let body = null;
  try { body = await res.json(); } catch (_) { /* empty / non-JSON */ }
  if (!res.ok) {
    const detail = (body && (body.detail || body.error)) || `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body || {};
}

// ─── Module state ───────────────────────────────────────────────────────────
let tick = null;        // 1s interval handle
let attempt = null;     // {provider, id, intervalSec, expiresMs, sinceLastPoll}
let selectedId = null;  // no auto-selection — the user picks, like the API picker
let initialized = false;

function flowEl(provider) {
  return document.querySelector(`[data-codex-flow="${provider.id}"]`);
}

function setMsg(provider, html, kind = '') {
  const m = document.querySelector(`[data-codex-msg="${provider.id}"]`);
  if (!m) return;
  m.className = kind ? `codex-msg codex-msg-${kind}` : 'codex-msg';
  m.innerHTML = html;
}

// ─── Provider picker (Add Models → Subscription) ───────────────────────────
// Same look as the API subsection's picker (.adm-provider-* classes), own ids.
function renderPickerMenu() {
  const menu = el('codex-provider-menu');
  if (!menu) return;
  menu.innerHTML = SUBSCRIPTION_PROVIDERS.map(p => {
    const logo = providerLogo(p.logoKey || p.label) || '';
    const active = p.id === selectedId ? ' active' : '';
    return `<div class="adm-provider-item${active}" role="option" data-value="${esc(p.id)}">
      <span class="adm-provider-logo">${logo}</span>
      <span>${esc(p.label)}</span>
    </div>`;
  }).join('');
}

function syncPickerCurrent() {
  const current = document.querySelector('#codex-provider-btn .adm-provider-current');
  if (!current) return;
  const p = providerById(selectedId);
  current.querySelector('.adm-provider-logo').innerHTML = p ? (providerLogo(p.logoKey || p.label) || '') : '';
  current.querySelector('.adm-provider-name').textContent = p ? p.label : 'Select Provider';
}

// While a device-code login is mid-flight, switching provider would orphan the
// visible flow — lock the picker until it completes or is cancelled.
function syncPickerLock() {
  const btn = el('codex-provider-btn');
  if (!btn) return;
  btn.disabled = !!attempt;
  btn.title = attempt ? 'Finish or cancel the sign-in in progress first' : 'Pick provider';
}

function selectProvider(id) {
  const p = providerById(id);
  if (!p || id === selectedId) return;
  selectedId = id;
  renderPickerMenu();
  syncPickerCurrent();
  renderOAuthCard();
}

// ─── Generic OAuth card (one per selected provider, registry-driven) ───────
function renderOAuthCard() {
  const mount = el('codex-oauth-card');
  if (!mount) return;
  const p = providerById(selectedId);
  if (!p) { mount.innerHTML = ''; return; }  // nothing picked yet — just the dropdown
  const logo = providerLogo(p.logoKey || p.label) || '';
  const signIn = p.placeholder
    ? '<button class="admin-btn-add" disabled title="Not yet available">Coming soon</button>'
    : `<button class="admin-btn-add" data-codex-action="connect" data-provider="${esc(p.id)}">Sign in</button>`;
  mount.innerHTML = `
    <div class="codex-provider" data-codex-provider="${esc(p.id)}">
      <div class="codex-provider-head">
        <span class="codex-provider-logo">${logo}</span>
        <div class="codex-provider-titles">
          <span class="codex-provider-name">Connect ${esc(p.label)} <span class="codex-provider-plan">(${esc(p.sub)})</span></span>
          <span class="codex-provider-desc">Sign in with your ${esc(p.label)} subscription.</span>
        </div>
        ${signIn}
      </div>
      <div data-codex-flow="${esc(p.id)}"></div>
      <div data-codex-msg="${esc(p.id)}"></div>
    </div>`;
}

function renderIdle(provider) {
  const flow = flowEl(provider);
  if (flow) flow.innerHTML = '';
  const btn = document.querySelector(`[data-codex-action="connect"][data-provider="${provider.id}"]`);
  if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
}

// ─── Render: pending device-code ───────────────────────────────────────────
function renderPending(provider, info) {
  const flow = flowEl(provider);
  if (!flow) return;
  const btn = document.querySelector(`[data-codex-action="connect"][data-provider="${provider.id}"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Signing in…'; }
  flow.innerHTML = `
    <div class="codex-pending">
      <div class="codex-step">1. Open the ${esc(provider.label)} login page:</div>
      <a class="admin-btn-add codex-open-btn" href="${esc(info.verification_uri)}" target="_blank" rel="noopener noreferrer" data-codex-action="open">Open login page ↗</a>
      <div class="codex-step">2. Enter this code when asked:</div>
      <div class="codex-code-row">
        <code class="codex-code" id="codex-usercode">${esc(info.user_code || '')}</code>
        <button class="admin-btn-sm" data-codex-action="copy" title="Copy code">Copy</button>
      </div>
      <div class="codex-wait"><span class="admin-spinner"></span><span id="codex-countdown">Waiting for you to authorize…</span></div>
      <button class="admin-btn-sm" data-codex-action="cancel">Cancel</button>
    </div>`;
}

// ─── Render: connected rows (Added Models → Subscription) ──────────────────
// Takes [{provider, connected:[...]}] across ALL providers and renders once —
// a per-provider render would erase the previous provider's rows.
function renderConnected(results) {
  const list = el('adm-epList-sub');
  if (!list) return;
  const rows = results.flatMap(({ provider, connected }) =>
    (connected || []).map(c => rowHtml(provider, c)));
  // Mirror loadEndpoints' empty-group rendering: keep the section visible
  // with a "None" placeholder so the group lineup matches Local and API.
  list.innerHTML = rows.length ? rows.join('') : '<div class="admin-empty">None</div>';
}

function rowHtml(provider, c) {
  const ok = c.enabled && c.status === 'active';
  const badge = ok
    ? '<span class="admin-badge">connected</span>'
    : c.last_error
      ? `<span class="admin-badge admin-badge-off" title="${esc(c.last_error)}">needs attention</span>`
      : `<span class="admin-badge admin-badge-off">${esc(c.status || 'inactive')}</span>`;
  const reconnect = ok ? '' :
    `<button class="admin-btn-sm" data-codex-action="reconnect" data-provider="${esc(provider.id)}">Sign in again</button>`;
  return `
    <div class="admin-user-row">
      <div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;">
        <div class="admin-user-info" style="flex:1;flex-wrap:wrap;gap:0.3rem;">
          <span class="codex-dot ${ok ? 'codex-dot-on' : ''}"></span>
          <span class="admin-user-name">${esc(c.name || provider.label)}</span>
          ${badge}
        </div>
        <div style="display:flex;gap:4px;align-items:center;">
          ${reconnect}
          <button class="admin-btn-delete" data-codex-action="disconnect" data-provider="${esc(provider.id)}" data-ep="${esc(c.endpoint_id)}">Disconnect</button>
        </div>
      </div>
    </div>`;
}

function fmtRemaining(ms) {
  if (ms <= 0) return 'expiring…';
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return `Waiting for you to authorize… (${m}:${String(s % 60).padStart(2, '0')} left)`;
}

// ─── Poll loop (single 1s ticker drives countdown + interval-paced poll) ────
function stopFlow() {
  if (tick) { clearInterval(tick); tick = null; }
  attempt = null;
  syncPickerLock();
}

function beginFlow(provider, info) {
  stopFlow();
  const expiresMs = info.expires_at ? Date.parse(info.expires_at) : (Date.now() + 600000);
  attempt = {
    provider,
    id: info.attempt_id,
    intervalSec: Math.max(2, info.interval || 5),
    expiresMs,
    sinceLastPoll: 0,
  };
  // The flow renders into the selected provider's card — make sure it's the
  // one that owns this attempt (matters when resuming after a reload).
  selectProvider(provider.id);
  syncPickerLock();
  renderPending(provider, info);
  tick = setInterval(onTick, 1000);
}

async function onTick() {
  if (!attempt) return;
  const provider = attempt.provider;
  const remaining = attempt.expiresMs - Date.now();
  const cd = el('codex-countdown');
  if (cd) cd.textContent = fmtRemaining(remaining);
  if (remaining <= 0) {
    const id = attempt.id;
    stopFlow();
    try { await api(provider, `/connect/${id}/cancel`, { method: 'POST' }); } catch (_) {}
    renderIdle(provider);
    setMsg(provider, 'Login code expired. Try signing in again.', 'warn');
    loadStatus();
    return;
  }
  attempt.sinceLastPoll += 1;
  if (attempt.sinceLastPoll < attempt.intervalSec) return;
  attempt.sinceLastPoll = 0;

  let st;
  try {
    st = await api(provider, `/connect/${attempt.id}`);
  } catch (e) {
    // Transient poll error — keep waiting; surface only if it persists.
    return;
  }
  if (!attempt) return; // cancelled while awaiting
  switch (st.status) {
    case 'authorized':
      stopFlow();
      renderIdle(provider);
      setMsg(provider, `${esc(provider.label)} connected. Its models are now available in the model picker.`, 'ok');
      loadStatus();
      refreshModelsCache();
      break;
    case 'expired':
      stopFlow();
      renderIdle(provider);
      setMsg(provider, 'Login code expired. Try signing in again.', 'warn');
      loadStatus();
      break;
    case 'cancelled':
      stopFlow();
      renderIdle(provider);
      loadStatus();
      break;
    case 'error':
      stopFlow();
      renderIdle(provider);
      setMsg(provider, 'Login failed. Please try again.', 'err');
      loadStatus();
      break;
    // 'pending' → keep ticking
  }
}

// The picker builds from modelsModule's cache; refresh it after connect /
// disconnect so the subscription models (dis)appear without a manual reload.
function refreshModelsCache() {
  try {
    if (window.modelsModule && window.modelsModule.refreshModels) {
      window.modelsModule.refreshModels(true);
    }
  } catch (_) { /* picker refreshes on its own cadence as a fallback */ }
}

// ─── Actions ───────────────────────────────────────────────────────────────
async function startConnect(provider, btn) {
  if (attempt || provider.placeholder) return; // one login at a time; no backend yet
  setMsg(provider, '');
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  try {
    const info = await api(provider, '/connect', { method: 'POST' });
    beginFlow(provider, info);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
    setMsg(provider, `Could not start login: ${esc(e.message)}`, 'err');
  }
}

// "Sign in again" on a stale connected row: expand the Add Models →
// Subscription section, select that provider, and start a fresh device-code
// login there. The old row keeps its Disconnect button — nothing is removed
// until the user says so.
function reconnect(provider) {
  const sec = el('adm-add-sub');
  if (sec) {
    sec.classList.remove('collapsed');
    const head = sec.querySelector('.adm-section-toggle');
    if (head) head.setAttribute('aria-expanded', 'true');
    try { localStorage.setItem('odysseus.addModels.adm-add-sub.open', '1'); } catch {}
    sec.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
  if (attempt) return; // a login is already mid-flight — just reveal it
  selectProvider(provider.id);
  const btn = document.querySelector(`[data-codex-action="connect"][data-provider="${provider.id}"]`);
  startConnect(provider, btn);
}

async function cancelAttempt() {
  if (!attempt) return;
  const { provider, id } = attempt;
  stopFlow();
  renderIdle(provider);
  try { await api(provider, `/connect/${id}/cancel`, { method: 'POST' }); } catch (_) {}
  loadStatus();
}

async function disconnect(provider, endpointId, btn) {
  if (!endpointId) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Removing…'; }
  try {
    await api(provider, `/disconnect/${endpointId}`, { method: 'POST' });
    setMsg(provider, 'Disconnected.', '');
    loadStatus();
    refreshModelsCache();
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Disconnect'; }
    setMsg(provider, `Could not disconnect: ${esc(e.message)}`, 'err');
  }
}

function copyCode(btn) {
  const code = el('codex-usercode');
  if (!code) return;
  const text = code.textContent || '';
  const flash = (label) => { if (btn) { const p = btn.textContent; btn.textContent = label; setTimeout(() => { btn.textContent = p; }, 1200); } };
  // navigator.clipboard needs a secure context (HTTPS / localhost). Odysseus is
  // commonly served over plain HTTP on a LAN IP, where it's undefined — fall
  // back to a hidden-textarea execCommand copy so the button still works.
  const legacyCopy = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      flash(ok ? 'Copied' : 'Copy failed');
    } catch (_) { flash('Copy failed'); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => flash('Copied')).catch(legacyCopy);
  } else {
    legacyCopy();
  }
}

// ─── Status load (also resumes an interrupted pending attempt) ─────────────
async function loadStatus() {
  const results = [];
  for (const provider of SUBSCRIPTION_PROVIDERS) {
    if (provider.placeholder) continue; // no backend to ask yet
    try {
      const data = await api(provider, '/status');
      results.push({ provider, connected: data.connected || [] });
      // If a device-code login is mid-flight and we aren't already tracking it
      // (e.g. modal reopened), resume the poll so the user sees it complete.
      const pend = (data.pending || [])[0];
      if (pend && pend.status === 'pending' && !attempt) {
        beginFlow(provider, {
          attempt_id: pend.attempt_id,
          user_code: pend.user_code,
          verification_uri: pend.verification_uri,
          expires_at: pend.expires_at,
          interval: 5,
        });
      } else if (!attempt || attempt.provider !== provider) {
        renderIdle(provider);
      }
    } catch (e) {
      // Owner-scoped endpoint; on 401/unknown just show the idle action.
      results.push({ provider, connected: [] });
      if (!attempt || attempt.provider !== provider) renderIdle(provider);
    }
  }
  renderConnected(results);
}

// ─── Public API ────────────────────────────────────────────────────────────
export function initCodexConnect() {
  if (initialized) return;
  const pickerMount = el('codex-sub-providers');
  const listMount = el('adm-epList-sub');
  if (!pickerMount && !listMount) return;
  initialized = true;
  renderPickerMenu();
  syncPickerCurrent();
  renderOAuthCard();

  // Picker open/select/close — mirrors the API subsection's picker wiring.
  const pickerBtn = el('codex-provider-btn');
  const pickerMenu = el('codex-provider-menu');
  const picker = el('codex-provider-picker');
  if (pickerBtn && pickerMenu && picker) {
    pickerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      pickerMenu.classList.toggle('hidden');
    });
    pickerMenu.addEventListener('click', (e) => {
      const item = e.target.closest('.adm-provider-item');
      if (!item) return;
      selectProvider(item.dataset.value);
      pickerMenu.classList.add('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!picker.contains(e.target)) pickerMenu.classList.add('hidden');
    });
  }

  // One delegated click handler per mount for card/row actions.
  [pickerMount, listMount].filter(Boolean).forEach(mount =>
    mount.addEventListener('click', (e) => {
      const t = e.target.closest('[data-codex-action]');
      if (!t) return;
      const action = t.dataset.codexAction;
      if (action === 'open') return; // real <a>, let it navigate
      e.preventDefault();
      const provider = providerById(t.dataset.provider) || (attempt && attempt.provider);
      switch (action) {
        case 'connect': if (provider) startConnect(provider, t); break;
        case 'reconnect': if (provider) reconnect(provider); break;
        case 'cancel': cancelAttempt(); break;
        case 'copy': copyCode(t); break;
        case 'disconnect': if (provider) disconnect(provider, t.dataset.ep, t); break;
      }
    }));
}

export function refreshCodexStatus() {
  if (!initialized) return;
  loadStatus();
}

const codexConnectModule = { initCodexConnect, refreshCodexStatus, isSubscriptionEndpointUrl };
export default codexConnectModule;

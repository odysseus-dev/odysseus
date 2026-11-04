// Per-model generation settings box (Settings → Added Models).
// No top-level DOM access: the pure helpers below are exercised under node by
// tests/test_model_customization_panel_js.py.

export const SETTING_NAMES = [
  'temperature', 'top_p', 'top_k', 'min_p',
  'presence_penalty', 'repeat_penalty', 'num_ctx', 'thinking',
];

const INTEGER_SETTINGS = new Set(['top_k', 'num_ctx']);
const TEXT_SETTINGS = new Set(['thinking']);

// Blank or disabled controls are "unset" and left out, so saving with every
// control blank removes the entry.
export function buildSettingsPayload({ endpoint_id, model, controls }) {
  const settings = {};
  for (const name of SETTING_NAMES) {
    const control = controls[name];
    if (!control || control.disabled) continue;
    const raw = String(control.value ?? '').trim();
    if (!raw) continue;
    if (TEXT_SETTINGS.has(name)) settings[name] = raw;
    else settings[name] = INTEGER_SETTINGS.has(name) ? parseInt(raw, 10) : Number(raw);
  }
  return { endpoint_id, model, settings };
}

export function applyReadOnly(controls, saveButton, readOnly) {
  for (const name of SETTING_NAMES) {
    const control = controls[name];
    if (control) control.disabled = Boolean(readOnly);
  }
  if (saveButton) saveButton.disabled = Boolean(readOnly);
}

export function fillControls(controls, stored) {
  const values = stored || {};
  for (const name of SETTING_NAMES) {
    const control = controls[name];
    if (!control) continue;
    control.value = values[name] === undefined || values[name] === null ? '' : String(values[name]);
  }
}

export function saveErrorMessage(body) {
  const detail = body && body.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length && detail[0] && detail[0].msg) return String(detail[0].msg);
  return 'Could not save the model settings';
}

// ── DOM wiring ────────────────────────────────────────────────────────────

const API = '/api/model-customizations';
let cascadeData = [];
let wired = false;

const byId = (id) => document.getElementById(id);
const panelControls = () => Object.fromEntries(
  SETTING_NAMES.map((name) => [name, byId('adm-mc-' + name)]).filter(([, node]) => node));

function showMessage(text, isError) {
  const msg = byId('adm-mcMsg');
  if (!msg) return;
  msg.textContent = text;
  msg.style.color = isError ? 'var(--red)' : 'var(--fg)';
}

function fillSelect(select, placeholder, items) {
  const keep = select.value;
  select.length = 0;
  select.appendChild(new Option(placeholder, ''));
  for (const [value, label] of items) select.appendChild(new Option(label, value));
  select.value = items.some(([value]) => value === keep) ? keep : '';
}

function selectedEndpoint() {
  return cascadeData.find((ep) => ep.id === byId('adm-mcEndpoint').value) || null;
}

function selectedModel() {
  const endpoint = selectedEndpoint();
  const name = byId('adm-mcModel').value;
  return endpoint ? endpoint.models.find((m) => m.model === name) || null : null;
}

function renderModel() {
  const entry = selectedModel();
  const controls = panelControls();
  showMessage('', false);
  fillControls(controls, entry ? entry.settings : {});
  applyReadOnly(controls, byId('adm-mcSaveBtn'), !entry || Boolean(selectedEndpoint()?.read_only));
}

function renderEndpoint() {
  const endpoint = selectedEndpoint();
  const modelSel = byId('adm-mcModel');
  fillSelect(modelSel, 'Select a model', endpoint ? endpoint.models.map((m) => [m.model, m.model]) : []);
  modelSel.disabled = !endpoint;
  renderModel();
}

async function refresh() {
  const res = await fetch(API, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  cascadeData = Array.isArray(data && data.endpoints) ? data.endpoints : [];
  fillSelect(byId('adm-mcEndpoint'), 'Select an endpoint', cascadeData.map((ep) => [ep.id, ep.name || ep.id]));
  renderEndpoint();
}

async function save() {
  const endpoint = selectedEndpoint();
  const entry = selectedModel();
  if (!endpoint || !entry) return;
  if (endpoint.read_only) return;
  const payload = buildSettingsPayload({ endpoint_id: endpoint.id, model: entry.model, controls: panelControls() });
  try {
    const res = await fetch(API, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      showMessage(res.status === 403 ? 'Admin access is required to change these settings.' : saveErrorMessage(body), true);
      return;
    }
    await refresh();
    showMessage(body && body.warning ? body.warning : 'Saved', Boolean(body && body.warning));
  } catch (e) {
    showMessage(saveErrorMessage(null), true);
  }
}

export async function initModelCustomization() {
  if (!byId('adm-modelCustomCard') || !window._isAdmin) return;
  if (!wired) {
    wired = true;
    byId('adm-mcEndpoint').addEventListener('change', renderEndpoint);
    byId('adm-mcModel').addEventListener('change', renderModel);
    byId('adm-mcSaveBtn').addEventListener('click', save);
    byId('adm-mcReloadBtn')?.addEventListener('click', refresh);
  }
  try {
    await refresh();
  } catch (e) {
    showMessage('Could not load the model list', true);
  }
}

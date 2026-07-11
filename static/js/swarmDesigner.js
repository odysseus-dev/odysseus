import Storage from './storage.js';
import sessionModule from './sessions.js';
import uiModule from './ui.js';

const API_BASE = '';
const OPENROUTER_URL = 'https://openrouter.ai/api/v1';
const DEFAULT_MODEL = 'openrouter/free';
const FREE_MODELS = [
  ['openrouter/free', 'OpenRouter Free Router'],
  ['nvidia/nemotron-3-ultra-550b-a55b:free', 'NVIDIA Nemotron 3 Ultra free'],
  ['nvidia/nemotron-3-super-120b-a12b:free', 'NVIDIA Nemotron 3 Super free'],
  ['nvidia/nemotron-3-nano-30b-a3b:free', 'NVIDIA Nemotron 3 Nano free'],
  ['qwen/qwen3-coder:free', 'Qwen3 Coder free'],
  ['deepseek/deepseek-r1:free', 'DeepSeek R1 free'],
  ['google/gemma-4-26b-a4b-it:free', 'Gemma 4 26B free'],
  ['meta-llama/llama-4-maverick:free', 'Llama 4 Maverick free'],
];
const TOOL_OPTIONS = [
  'all', 'web_search', 'web_fetch', 'bash', 'python',
  'read_file', 'write_file', 'edit_file', 'grep', 'glob', 'ls',
];

let swarms = [];
let selectedSwarmId = '';
let editingId = '';
let editingDefinition = null;

function el(id) { return document.getElementById(id); }
function esc(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}
function slugify(text) {
  return String(text || 'agent').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'agent';
}
function uniqueSlug(base, workers, currentIndex = -1) {
  const clean = slugify(base);
  const used = new Set(workers.map((w, i) => i === currentIndex ? '' : w.slug).filter(Boolean));
  if (!used.has(clean)) return clean;
  let n = 2;
  while (used.has(`${clean}_${n}`)) n += 1;
  return `${clean}_${n}`;
}
function loadState() {
  const state = Storage.loadToggleState();
  selectedSwarmId = state.swarm_id || selectedSwarmId || 'openrouter_software_engineering';
}
function saveSelectedSwarm(id) {
  selectedSwarmId = id || '';
  const state = Storage.loadToggleState();
  state.swarm_id = selectedSwarmId;
  Storage.saveToggleState(state);
}
function roleTemplate(name, slug) {
  return {
    name,
    slug,
    description: '',
    system_prompt: `You are ${name}. Handle the tasks delegated to you clearly and concisely.`,
    tools_allowed: ['all'],
    tools_denied: [],
    model: DEFAULT_MODEL,
    endpoint_url: OPENROUTER_URL,
    priority: 0,
  };
}
function blankDefinition() {
  return {
    name: 'Custom Swarm Team',
    description: 'A custom cloud swarm using free OpenRouter/NVIDIA model routes.',
    domain: 'general',
    master: {
      ...roleTemplate('Coordinator', 'coordinator'),
      system_prompt: 'You are the coordinator. Select the right workers, delegate focused tasks, and merge their results into one answer.',
      model: 'nvidia/nemotron-3-ultra-550b-a55b:free',
    },
    workers: [
      roleTemplate('Researcher', 'researcher'),
      roleTemplate('Implementer', 'implementer'),
      roleTemplate('Reviewer', 'reviewer'),
    ],
    routing_rules: {
      'research|source|web': ['researcher'],
      'build|code|implement': ['implementer'],
      'review|quality|risk': ['reviewer'],
    },
    memory_config: { shared: true, persist_after: true },
    max_parallel: 3,
    version: '1.0.0',
  };
}
function modelOptions(selected) {
  const extra = selected && !FREE_MODELS.some(([id]) => id === selected) ? [[selected, selected]] : [];
  return [...FREE_MODELS, ...extra].map(([id, label]) =>
    `<option value="${esc(id)}"${id === selected ? ' selected' : ''}>${esc(label)}</option>`
  ).join('');
}
function roleCard(role, index) {
  const allowed = new Set(role.tools_allowed || ['all']);
  const denied = new Set(role.tools_denied || []);
  const tools = TOOL_OPTIONS.map(tool => `
    <label class="swarm-tool-check">
      <input type="checkbox" data-worker-tool="${index}" value="${esc(tool)}" ${allowed.has(tool) ? 'checked' : ''}>
      <span>${esc(tool)}</span>
    </label>
  `).join('');
  const deniedTools = TOOL_OPTIONS.filter(t => t !== 'all').map(tool => `
    <label class="swarm-tool-check denied">
      <input type="checkbox" data-worker-deny="${index}" value="${esc(tool)}" ${denied.has(tool) ? 'checked' : ''}>
      <span>${esc(tool)}</span>
    </label>
  `).join('');
  return `
    <div class="swarm-agent-card" data-worker-index="${index}">
      <div class="swarm-agent-head">
        <strong>${esc(role.name || 'Worker')}</strong>
        <div class="swarm-agent-actions">
          <button type="button" data-dup-worker="${index}" title="Duplicate agent">Duplicate</button>
          <button type="button" data-remove-worker="${index}" title="Remove agent">Remove</button>
        </div>
      </div>
      <div class="swarm-grid two">
        <label>Name<input data-worker-field="${index}:name" value="${esc(role.name || '')}"></label>
        <label>Slug<input data-worker-field="${index}:slug" value="${esc(role.slug || '')}"></label>
      </div>
      <label>Description<input data-worker-field="${index}:description" value="${esc(role.description || '')}"></label>
      <label>Model<select data-worker-field="${index}:model">${modelOptions(role.model || DEFAULT_MODEL)}</select></label>
      <label>System prompt<textarea rows="4" data-worker-field="${index}:system_prompt">${esc(role.system_prompt || '')}</textarea></label>
      <div class="swarm-tool-groups">
        <fieldset><legend>Allowed tools</legend>${tools}</fieldset>
        <fieldset><legend>Denied tools</legend>${deniedTools}</fieldset>
      </div>
    </div>
  `;
}
function routingText(def) {
  return Object.entries(def.routing_rules || {}).map(([pattern, slugs]) =>
    `${pattern} => ${(slugs || []).join(', ')}`
  ).join('\n');
}
function parseRouting(text) {
  const rules = {};
  String(text || '').split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const [pattern, targets] = trimmed.split(/\s*=>\s*/);
    if (!pattern || !targets) return;
    rules[pattern.trim()] = targets.split(',').map(s => s.trim()).filter(Boolean);
  });
  return rules;
}
function renderSelector() {
  const select = el('swarm-select');
  if (!select) return;
  const grouped = swarms.map(s =>
    `<option value="${esc(s.id)}"${s.id === selectedSwarmId ? ' selected' : ''}>${s.is_builtin ? 'Built-in: ' : 'Custom: '}${esc(s.name)}</option>`
  ).join('');
  select.innerHTML = `<option value="">No swarm</option>${grouped}`;
  select.value = selectedSwarmId || '';
}
function renderDesigner() {
  const form = el('swarm-designer-form');
  if (!form || !editingDefinition) return;
  const def = editingDefinition;
  form.innerHTML = `
    <div class="swarm-grid two">
      <label>Team name<input id="swarm-edit-name" value="${esc(def.name || '')}"></label>
      <label>Domain<input id="swarm-edit-domain" value="${esc(def.domain || 'general')}"></label>
    </div>
    <label>Description<input id="swarm-edit-description" value="${esc(def.description || '')}"></label>
    <div class="swarm-section-title">Master agent</div>
    <div class="swarm-grid two">
      <label>Name<input id="swarm-master-name" value="${esc(def.master?.name || 'Coordinator')}"></label>
      <label>Slug<input id="swarm-master-slug" value="${esc(def.master?.slug || 'coordinator')}"></label>
    </div>
    <label>Master model<select id="swarm-master-model">${modelOptions(def.master?.model || DEFAULT_MODEL)}</select></label>
    <label>Master prompt<textarea rows="4" id="swarm-master-prompt">${esc(def.master?.system_prompt || '')}</textarea></label>
    <div class="swarm-section-title">Worker agents</div>
    <div id="swarm-worker-list">${(def.workers || []).map(roleCard).join('')}</div>
    <button type="button" class="swarm-secondary" id="swarm-add-worker">Add worker</button>
    <div class="swarm-grid two">
      <label>Max parallel<input id="swarm-max-parallel" type="number" min="1" max="8" value="${esc(def.max_parallel || 3)}"></label>
      <label>Memory
        <select id="swarm-memory-mode">
          <option value="persist"${def.memory_config?.persist_after !== false ? ' selected' : ''}>Shared and persistent</option>
          <option value="shared"${def.memory_config?.persist_after === false ? ' selected' : ''}>Shared for this run only</option>
        </select>
      </label>
    </div>
    <label>Routing rules<textarea rows="5" id="swarm-routing-rules" placeholder="database|sql => backend, reviewer">${esc(routingText(def))}</textarea></label>
  `;
}
function collectDefinition() {
  const def = editingDefinition || blankDefinition();
  def.name = el('swarm-edit-name')?.value.trim() || 'Custom Swarm Team';
  def.description = el('swarm-edit-description')?.value.trim() || '';
  def.domain = el('swarm-edit-domain')?.value.trim() || 'general';
  def.master = {
    ...(def.master || roleTemplate('Coordinator', 'coordinator')),
    name: el('swarm-master-name')?.value.trim() || 'Coordinator',
    slug: slugify(el('swarm-master-slug')?.value || 'coordinator'),
    model: el('swarm-master-model')?.value || DEFAULT_MODEL,
    endpoint_url: OPENROUTER_URL,
    system_prompt: el('swarm-master-prompt')?.value || '',
    tools_allowed: ['all'],
    tools_denied: [],
  };
  def.max_parallel = Math.max(1, Math.min(8, Number(el('swarm-max-parallel')?.value || 3)));
  def.memory_config = { shared: true, persist_after: el('swarm-memory-mode')?.value !== 'shared' };
  def.routing_rules = parseRouting(el('swarm-routing-rules')?.value || '');
  return def;
}
async function applySelection(id, { patchSession = true } = {}) {
  saveSelectedSwarm(id);
  renderSelector();
  const pending = sessionModule.getPendingChat && sessionModule.getPendingChat();
  if (pending) pending.swarmId = id || '';
  const sid = sessionModule.getCurrentSessionId && sessionModule.getCurrentSessionId();
  if (patchSession && sid) {
    const fd = new FormData();
    fd.append('swarm_id', id || '');
    try {
      await fetch(`${API_BASE}/api/session/${sid}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
      if (sessionModule.loadSessions) sessionModule.loadSessions().catch(() => {});
    } catch (e) {
      uiModule.showError?.('Could not attach swarm: ' + e.message);
    }
  }
}
async function loadSwarms() {
  loadState();
  try {
    const res = await fetch(`${API_BASE}/api/swarms`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    swarms = await res.json();
    if (!selectedSwarmId && swarms[0]) selectedSwarmId = swarms[0].id;
    renderSelector();
  } catch (e) {
    swarms = [];
    renderSelector();
    console.warn('Failed to load swarms', e);
  }
}
async function openDesigner(id = selectedSwarmId) {
  const modal = el('swarm-designer-modal');
  if (!modal) return;
  editingId = '';
  editingDefinition = blankDefinition();
  if (id) {
    try {
      const res = await fetch(`${API_BASE}/api/swarms/${encodeURIComponent(id)}`, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        editingId = data.is_builtin ? '' : id;
        editingDefinition = data.definition || blankDefinition();
        if (data.is_builtin) {
          editingDefinition.name = `${editingDefinition.name || data.name} Copy`;
        }
      }
    } catch (_) {}
  }
  renderDesigner();
  modal.classList.remove('hidden');
}
function closeDesigner() {
  el('swarm-designer-modal')?.classList.add('hidden');
}
async function saveDesigner() {
  const def = collectDefinition();
  def.workers = def.workers || [];
  if (!def.workers.length) {
    uiModule.showError?.('Add at least one worker agent.');
    return;
  }
  const method = editingId ? 'PUT' : 'POST';
  const url = editingId ? `${API_BASE}/api/swarms/${encodeURIComponent(editingId)}` : `${API_BASE}/api/swarms`;
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(def),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.detail || `HTTP ${res.status}`);
  await loadSwarms();
  await applySelection(payload.id || editingId, { patchSession: true });
  closeDesigner();
  uiModule.showToast?.('Swarm saved');
}
function wireDesignerForm() {
  const form = el('swarm-designer-form');
  if (!form) return;
  const applyWorkerField = (event) => {
    if (!editingDefinition) return;
    const target = event.target;
    const field = target?.dataset?.workerField;
    if (field) {
      const [indexRaw, key] = field.split(':');
      const index = Number(indexRaw);
      const worker = editingDefinition.workers[index];
      if (!worker) return;
      worker[key] = key === 'slug'
        ? uniqueSlug(target.value, editingDefinition.workers, index)
        : target.value;
      if (key === 'model') worker.endpoint_url = OPENROUTER_URL;
    }
  };
  form.addEventListener('input', applyWorkerField);
  form.addEventListener('change', applyWorkerField);
  form.addEventListener('change', (event) => {
    const target = event.target;
    const allowIndex = target?.dataset?.workerTool;
    const denyIndex = target?.dataset?.workerDeny;
    if (allowIndex != null) {
      const worker = editingDefinition.workers[Number(allowIndex)];
      worker.tools_allowed = Array.from(form.querySelectorAll(`[data-worker-tool="${allowIndex}"]:checked`)).map(i => i.value);
      if (!worker.tools_allowed.length) worker.tools_allowed = ['all'];
    }
    if (denyIndex != null) {
      const worker = editingDefinition.workers[Number(denyIndex)];
      worker.tools_denied = Array.from(form.querySelectorAll(`[data-worker-deny="${denyIndex}"]:checked`)).map(i => i.value);
    }
  });
  form.addEventListener('click', (event) => {
    const add = event.target.closest('#swarm-add-worker');
    const dup = event.target.closest('[data-dup-worker]');
    const rem = event.target.closest('[data-remove-worker]');
    if (add) {
      const name = `Worker ${editingDefinition.workers.length + 1}`;
      editingDefinition.workers.push(roleTemplate(name, uniqueSlug(name, editingDefinition.workers)));
      renderDesigner();
    } else if (dup) {
      const index = Number(dup.dataset.dupWorker);
      const copy = JSON.parse(JSON.stringify(editingDefinition.workers[index]));
      copy.name = `${copy.name} Copy`;
      copy.slug = uniqueSlug(copy.slug || copy.name, editingDefinition.workers);
      editingDefinition.workers.splice(index + 1, 0, copy);
      renderDesigner();
    } else if (rem) {
      const index = Number(rem.dataset.removeWorker);
      editingDefinition.workers.splice(index, 1);
      renderDesigner();
    }
  });
}
function initModeBridge() {
  const swarmBtn = el('mode-swarm-btn');
  if (!swarmBtn) return;
  const original = window.__odysseusSetChatMode;
  const sync = (mode) => {
    const toggle = swarmBtn.closest('.mode-toggle');
    const agentBtn = el('mode-agent-btn');
    const chatBtn = el('mode-chat-btn');
    [agentBtn, swarmBtn, chatBtn].forEach(btn => {
      if (!btn) return;
      const active = btn === agentBtn ? mode === 'agent' : btn === swarmBtn ? mode === 'swarm' : mode === 'chat';
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-pressed', String(active));
    });
    if (toggle) {
      toggle.classList.add('mode-toggle-three');
      toggle.classList.toggle('mode-mid', mode === 'swarm');
      toggle.classList.toggle('mode-third', mode === 'chat');
      toggle.classList.toggle('mode-chat', false);
    }
    el('swarm-select-wrap')?.classList.toggle('hidden', mode !== 'swarm');
  };
  window.__odysseusSetChatMode = (mode) => {
    if (mode === 'swarm') {
      if (original) original('agent');
      const state = Storage.loadToggleState();
      state.mode = 'swarm';
      if (!state.swarm_id && selectedSwarmId) state.swarm_id = selectedSwarmId;
      Storage.saveToggleState(state);
      sync('swarm');
      return;
    }
    if (original) original(mode);
    sync(mode);
  };
  swarmBtn.addEventListener('click', () => window.__odysseusSetChatMode('swarm'));
  sync(Storage.loadToggleState().mode || 'chat');
}
export async function init() {
  await loadSwarms();
  initModeBridge();
  wireDesignerForm();
  el('swarm-select')?.addEventListener('change', async (event) => {
    await applySelection(event.target.value);
    if (event.target.value && typeof window.__odysseusSetChatMode === 'function') {
      window.__odysseusSetChatMode('swarm');
    }
  });
  el('swarm-designer-open')?.addEventListener('click', () => openDesigner(selectedSwarmId));
  el('swarm-new-btn')?.addEventListener('click', () => openDesigner(''));
  el('close-swarm-designer')?.addEventListener('click', closeDesigner);
  el('swarm-cancel-btn')?.addEventListener('click', closeDesigner);
  el('swarm-save-btn')?.addEventListener('click', () => saveDesigner().catch(e => uiModule.showError?.('Save failed: ' + e.message)));
  el('swarm-refresh-btn')?.addEventListener('click', loadSwarms);
}

const swarmDesigner = { init, loadSwarms, openDesigner, applySelection };
export default swarmDesigner;

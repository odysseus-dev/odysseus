// Orquestador DAG — editor visual + runs sobre /api/orchestrator.
'use strict';

const API = '/api/orchestrator';
const $ = (id) => document.getElementById(id);
const NODE_W = 196;
const NODE_H = 100;
const STORAGE_KEY = 'odysseus-dag-graph-v1';
const state = {
  selected: null,
  selectedTask: null,
  graph: null,
  es: null,
  listTimer: null,
  busy: false,
  linking: null,
  drag: null,
  jsonTimer: null,
};

const EXAMPLE = {
  name: 'informe',
  model: 'deepseek-flash',
  max_parallel: 2,
  tasks: [
    {
      id: 'investiga',
      prompt: 'Investiga el tema y devuelve 6 hechos verificables, cada uno con una fuente. Sé breve.',
      max_tokens: 700,
    },
    {
      id: 'borrador',
      prompt: 'Escribe un borrador de 500 palabras usando sólo esos hechos. No inventes datos.',
      depends_on: ['investiga'],
      max_tokens: 900,
    },
    {
      id: 'critica',
      prompt: 'Señala huecos, exageraciones y afirmaciones sin fuente del borrador. Lista concreta.',
      depends_on: ['borrador'],
      max_tokens: 500,
    },
    {
      id: 'final',
      prompt: 'Reescribe el borrador corrigiendo la crítica. Devuelve sólo el texto final.',
      depends_on: ['borrador', 'critica'],
      max_tokens: 900,
    },
  ],
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function clampPos(value) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.min(20000, parsed)) : 0;
}

function uniqueId(base) {
  const clean = String(base || 'tarea').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'tarea';
  let candidate = clean;
  let index = 2;
  while (state.graph && state.graph.tasks.some((task) => task.id === candidate)) {
    candidate = `${clean}-${index++}`;
  }
  return candidate;
}

function normalizeAttachment(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = String(raw.id || '').trim();
  if (!/^[0-9a-f]{32}$/.test(id)) return null;
  return {
    id,
    name: String(raw.name || 'adjunto').slice(0, 160),
    bytes: Math.max(0, Number(raw.bytes) || 0),
  };
}

function normalizeTask(raw, index) {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    id: String(source.id || `tarea-${index + 1}`).trim().slice(0, 80) || `tarea-${index + 1}`,
    prompt: String(source.prompt || ''),
    depends_on: Array.isArray(source.depends_on)
      ? [...new Set(source.depends_on.map((dep) => String(dep || '').trim()).filter(Boolean))]
      : [],
    system: String(source.system || ''),
    model: String(source.model || ''),
    endpoint_url: String(source.endpoint_url || ''),
    endpoint_id: String(source.endpoint_id || ''),
    max_tokens: clampInt(source.max_tokens, 32, 8000, 800),
    retries: clampInt(source.retries, 0, 5, 0),
    timeout: clampInt(source.timeout, 10, 1800, 180),
    attachments: Array.isArray(source.attachments)
      ? source.attachments.map(normalizeAttachment).filter(Boolean).slice(0, 10)
      : [],
    x: clampPos(source.x),
    y: clampPos(source.y),
  };
}

function normalizeGraph(raw) {
  const source = raw && typeof raw === 'object' ? raw : {};
  const tasks = Array.isArray(source.tasks) ? source.tasks.map(normalizeTask) : [];
  const graph = {
    name: String(source.name || 'DAG').slice(0, 120) || 'DAG',
    model: String(source.model || ''),
    endpoint_url: String(source.endpoint_url || ''),
    endpoint_id: String(source.endpoint_id || ''),
    system: String(source.system || ''),
    max_parallel: clampInt(source.max_parallel, 1, 8, 2),
    tasks,
  };
  // Los DAG antiguos no tienen posiciones: se dibujan por capas.
  if (graph.tasks.length && !graph.tasks.some((task) => task.x || task.y)) {
    autoLayoutGraph(graph);
  }
  return graph;
}

function autoLayoutGraph(graph) {
  const byId = new Map(graph.tasks.map((task) => [task.id, task]));
  const depth = new Map();
  const indegree = new Map(graph.tasks.map((task) => [task.id, 0]));
  const edges = new Map(graph.tasks.map((task) => [task.id, []]));
  for (const task of graph.tasks) {
    for (const dep of task.depends_on) {
      if (!byId.has(dep) || dep === task.id) continue;
      edges.get(dep).push(task.id);
      indegree.set(task.id, (indegree.get(task.id) || 0) + 1);
    }
  }
  const queue = graph.tasks.filter((task) => (indegree.get(task.id) || 0) === 0).map((task) => task.id);
  const order = [];
  while (queue.length) {
    const id = queue.shift();
    order.push(id);
    depth.set(id, depth.get(id) || 0);
    for (const next of edges.get(id) || []) {
      depth.set(next, Math.max(depth.get(next) || 0, (depth.get(id) || 0) + 1));
      indegree.set(next, (indegree.get(next) || 0) - 1);
      if ((indegree.get(next) || 0) === 0) queue.push(next);
    }
  }
  // Un ciclo no se puede ordenar; se coloca al final sin bloquear el editor.
  for (const task of graph.tasks) {
    if (!depth.has(task.id)) depth.set(task.id, order.length);
    if (!order.includes(task.id)) order.push(task.id);
  }
  const rows = new Map();
  for (const id of order) {
    const task = byId.get(id);
    if (!task) continue;
    const level = depth.get(id) || 0;
    const row = rows.get(level) || 0;
    rows.set(level, row + 1);
    task.x = 48 + level * (NODE_W + 78);
    task.y = 48 + row * (NODE_H + 34);
  }
}

function graphPayload() {
  return {
    name: state.graph.name,
    model: state.graph.model,
    endpoint_url: state.graph.endpoint_url,
    endpoint_id: state.graph.endpoint_id,
    system: state.graph.system,
    max_parallel: state.graph.max_parallel,
    tasks: state.graph.tasks.map((task) => ({
      id: task.id,
      prompt: task.prompt,
      depends_on: [...task.depends_on],
      system: task.system,
      model: task.model,
      endpoint_url: task.endpoint_url,
      endpoint_id: task.endpoint_id,
      max_tokens: task.max_tokens,
      retries: task.retries,
      timeout: task.timeout,
      attachments: task.attachments.map((att) => ({ ...att })),
      x: task.x,
      y: task.y,
    })),
  };
}

function serializedGraph() {
  return JSON.stringify(graphPayload(), null, 2);
}

function writeJson() {
  const area = $('dag-json');
  if (document.activeElement === area) return;
  area.value = serializedGraph();
}

function persistGraph() {
  try {
    localStorage.setItem(STORAGE_KEY, serializedGraph());
  } catch (_) { /* storage may be disabled */ }
}

function setMsg(text, kind) {
  const node = $('msg');
  node.textContent = text || '';
  node.className = 'msg' + (kind ? ' ' + kind : '');
}

function taskById(id) {
  return state.graph.tasks.find((task) => task.id === id) || null;
}

function selectedTask() {
  return taskById(state.selectedTask);
}

function commitGraph() {
  writeJson();
  persistGraph();
}

function renderGraph() {
  const graphEl = $('graph');
  const nodesEl = $('nodes');
  const edgesEl = $('edges');
  nodesEl.textContent = '';
  for (const path of [...edgesEl.querySelectorAll('.edge, .edge-hit, .edge-temp')]) path.remove();

  const tasks = state.graph.tasks;
  $('graph-empty').hidden = tasks.length > 0;
  let width = 900;
  let height = 560;
  for (const task of tasks) {
    width = Math.max(width, task.x + NODE_W + 80);
    height = Math.max(height, task.y + NODE_H + 80);
  }
  graphEl.style.width = `${width}px`;
  graphEl.style.height = `${height}px`;
  edgesEl.setAttribute('width', String(width));
  edgesEl.setAttribute('height', String(height));
  edgesEl.setAttribute('viewBox', `0 0 ${width} ${height}`);

  for (const task of tasks) {
    const node = el('div', 'node' + (task.id === state.selectedTask ? ' selected' : ''));
    node.dataset.id = task.id;
    node.style.left = `${task.x}px`;
    node.style.top = `${task.y}px`;

    const head = el('div', 'node-head');
    head.append(el('span', 'node-id', task.id));
    if (task.attachments.length) head.append(el('span', 'node-badge', `📎 ${task.attachments.length}`));
    const prompt = el('div', 'node-prompt', task.prompt || 'Sin prompt');
    const foot = el('div', 'node-foot');
    foot.append(el('span', 'node-deps', task.depends_on.length ? `↑ ${task.depends_on.join(', ')}` : 'sin dependencias'));
    node.append(head, prompt, foot);
    node.append(makePort('in', task.id), makePort('out', task.id));

    node.addEventListener('pointerdown', (event) => {
      if (event.target.closest('.port')) return;
      event.preventDefault();
      selectTask(task.id);
      startDrag(event, task.id);
    });
    nodesEl.appendChild(node);
  }

  drawEdges();
  renderInspector();
}

function makePort(kind, taskId) {
  const port = el('span', `port ${kind}`);
  port.dataset.port = kind;
  port.dataset.task = taskId;
  port.setAttribute('role', 'button');
  port.setAttribute('aria-label', kind === 'in' ? 'Entrada de dependencia' : 'Salida para conectar');
  port.title = kind === 'in' ? 'Entrada: suelta aquí una conexión' : 'Salida: arrastra hasta otra tarea';
  if (kind === 'out') {
    port.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      startLinking(taskId);
    });
  }
  return port;
}

function nodeCenter(task) {
  return { x: task.x + NODE_W / 2, y: task.y + NODE_H / 2 };
}

function edgePath(from, to) {
  const a = nodeCenter(from);
  const b = nodeCenter(to);
  const x1 = from.x + NODE_W;
  const y1 = a.y;
  const x2 = to.x;
  const y2 = b.y;
  const dx = Math.max(48, Math.abs(x2 - x1) * 0.45);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

function drawEdges() {
  const edgesEl = $('edges');
  for (const path of [...edgesEl.querySelectorAll('.edge, .edge-hit')]) path.remove();
  for (const task of state.graph.tasks) {
    for (const dep of task.depends_on) {
      const from = taskById(dep);
      const to = taskById(task.id);
      if (!from || !to) continue;
      const d = edgePath(from, to);
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      hit.setAttribute('d', d);
      hit.setAttribute('class', 'edge-hit');
      hit.dataset.from = from.id;
      hit.dataset.to = to.id;
      hit.addEventListener('click', () => {
        to.depends_on = to.depends_on.filter((item) => item !== from.id);
        commitGraph();
        renderGraph();
        setMsg(`Dependencia ${from.id} → ${to.id} eliminada`);
      });
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d);
      path.setAttribute('class', 'edge');
      path.setAttribute('marker-end', 'url(#arrow)');
      edgesEl.append(path, hit);
    }
  }
}

function startDrag(event, id) {
  const task = taskById(id);
  if (!task) return;
  const startX = event.clientX;
  const startY = event.clientY;
  const originX = task.x;
  const originY = task.y;
  let moved = false;
  const node = $('nodes').querySelector(`[data-id="${CSS.escape(id)}"]`);

  const move = (moveEvent) => {
    const dx = moveEvent.clientX - startX;
    const dy = moveEvent.clientY - startY;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    moved = true;
    task.x = Math.max(0, Math.min(20000, originX + dx));
    task.y = Math.max(0, Math.min(20000, originY + dy));
    if (node) {
      node.style.left = `${task.x}px`;
      node.style.top = `${task.y}px`;
    }
    drawEdges();
  };
  const up = () => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    if (moved) {
      commitGraph();
      renderGraph();
    }
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
}

function startLinking(fromId) {
  const from = taskById(fromId);
  if (!from) return;
  state.linking = { from: fromId };
  const edgesEl = $('edges');
  const temp = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  temp.setAttribute('class', 'edge-temp');
  temp.setAttribute('marker-end', 'url(#arrow)');
  edgesEl.appendChild(temp);

  const move = (event) => {
    const start = nodeCenter(from);
    const x1 = from.x + NODE_W;
    const y1 = start.y;
    const x2 = event.clientX - $('graph').getBoundingClientRect().left;
    const y2 = event.clientY - $('graph').getBoundingClientRect().top;
    const dx = Math.max(30, Math.abs(x2 - x1) * 0.45);
    temp.setAttribute('d', `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`);
  };

  const up = (event) => {
    document.removeEventListener('pointermove', move);
    document.removeEventListener('pointerup', up);
    temp.remove();
    state.linking = null;
    const target = document.elementFromPoint(event.clientX, event.clientY);
    const port = target && target.closest ? target.closest('.port.in') : null;
    if (port) {
      connectTasks(fromId, port.dataset.task);
    }
  };
  document.addEventListener('pointermove', move);
  document.addEventListener('pointerup', up);
}

function connectTasks(fromId, toId) {
  if (!fromId || !toId || fromId === toId) return;
  const to = taskById(toId);
  if (!to) return;
  if (to.depends_on.includes(fromId)) {
    setMsg('Esa dependencia ya existe');
    return;
  }
  // Evita ciclos obvios antes de mandar el grafo al servidor.
  const stack = [fromId];
  const seen = new Set();
  while (stack.length) {
    const current = stack.pop();
    if (current === toId) {
      setMsg('Esa conexión crearía un ciclo', 'err');
      return;
    }
    if (seen.has(current)) continue;
    seen.add(current);
    for (const dep of taskById(current)?.depends_on || []) stack.push(dep);
  }
  to.depends_on.push(fromId);
  commitGraph();
  renderGraph();
  setMsg(`Dependencia ${fromId} → ${toId} añadida`, 'ok');
}

function selectTask(id) {
  state.selectedTask = id;
  for (const node of $('nodes').querySelectorAll('.node')) {
    node.classList.toggle('selected', node.dataset.id === id);
  }
  renderInspector();
}

function renderInspector() {
  const task = selectedTask();
  $('inspector-empty').hidden = Boolean(task);
  $('inspector-form').hidden = !task;
  if (!task) return;
  $('inspector-title').textContent = `Tarea · ${task.id}`;
  $('node-id').value = task.id;
  $('node-prompt').value = task.prompt;
  $('node-model').value = task.model;
  $('node-max-tokens').value = task.max_tokens;
  $('node-retries').value = task.retries;
  $('node-timeout').value = task.timeout;
  $('node-system').value = task.system;
  renderDeps(task);
  renderAttachments(task);
}

function renderDeps(task) {
  const list = $('dep-list');
  list.textContent = '';
  if (!task.depends_on.length) list.append(el('span', 'micro', 'Sin dependencias'));
  for (const dep of task.depends_on) {
    const chip = el('span', 'chip');
    chip.append(el('span', '', dep));
    const remove = el('button', 'chip-x', '×');
    remove.type = 'button';
    remove.title = `Quitar ${dep}`;
    remove.addEventListener('click', () => {
      task.depends_on = task.depends_on.filter((item) => item !== dep);
      commitGraph();
      renderGraph();
    });
    chip.append(remove);
    list.append(chip);
  }

  const select = $('dep-select');
  select.textContent = '';
  const candidates = state.graph.tasks.filter(
    (item) => item.id !== task.id && !task.depends_on.includes(item.id),
  );
  if (!candidates.length) {
    const option = el('option', '', 'No hay más tareas');
    option.value = '';
    select.append(option);
    $('dep-add').disabled = true;
    return;
  }
  $('dep-add').disabled = false;
  for (const item of candidates) {
    const option = el('option', '', item.id);
    option.value = item.id;
    select.append(option);
  }
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function renderAttachments(task) {
  const list = $('att-list');
  list.textContent = '';
  if (!task.attachments.length) list.append(el('span', 'micro', 'Sin adjuntos'));
  for (const att of task.attachments) {
    const chip = el('span', 'chip att');
    chip.append(el('span', 'chip-name', att.name), el('span', 'chip-size', formatBytes(att.bytes)));
    const remove = el('button', 'chip-x', '×');
    remove.type = 'button';
    remove.title = `Quitar ${att.name}`;
    remove.addEventListener('click', () => {
      task.attachments = task.attachments.filter((item) => item.id !== att.id);
      commitGraph();
      renderAttachments(task);
      renderGraphNodesBadges();
    });
    chip.append(remove);
    list.append(chip);
  }
}

function renderGraphNodesBadges() {
  for (const task of state.graph.tasks) {
    const node = $('nodes').querySelector(`[data-id="${CSS.escape(task.id)}"]`);
    if (!node) continue;
    let badge = node.querySelector('.node-badge');
    if (!task.attachments.length) {
      badge?.remove();
      continue;
    }
    if (!badge) {
      badge = el('span', 'node-badge');
      node.querySelector('.node-head')?.append(badge);
    }
    badge.textContent = `📎 ${task.attachments.length}`;
  }
}

async function request(path, options = {}) {
  const response = await fetch(API + path, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    let detail = data.detail || data.error;
    if (Array.isArray(detail)) {
      detail = detail.map((item) => {
        const where = Array.isArray(item.loc) ? item.loc.join('.') : '';
        return (where ? where + ': ' : '') + (item.msg || JSON.stringify(item));
      }).join('; ');
    }
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

function renderRuns(items) {
  const list = $('run-list');
  list.textContent = '';
  if (!items.length) {
    list.append(el('div', 'empty', 'Todavía no hay runs. Ejecuta el DAG de la izquierda.'));
    return;
  }
  for (const item of items) {
    const row = el('button', 'run-row' + (state.selected === item.id ? ' active' : ''));
    row.type = 'button';
    row.dataset.runId = item.id;
    const left = el('div');
    left.append(el('div', 'name', item.name || item.id));
    const counts = item.counts || {};
    const summary = Object.entries(counts).map(([key, value]) => `${key} ${value}`).join(' · ');
    left.append(el('div', 'meta', `${item.model || 'modelo auto'} · ${item.task_count || 0} tareas${summary ? ' · ' + summary : ''}`));
    row.append(left, statusPill(item.status));
    row.addEventListener('click', () => openRun(item.id));
    list.append(row);
  }
}

function statusPill(status) {
  return el('span', 'pill ' + (status || 'pending'), status || 'pending');
}

async function loadRuns() {
  try {
    const data = await request('/runs');
    renderRuns(data.items || []);
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

function renderTask(task) {
  const card = el('div', 'task ' + (task.status || 'pending'));
  card.dataset.taskId = task.id;
  const head = el('div', 'task-head');
  head.append(el('span', 'task-id', task.id));
  head.append(el('span', 'task-status', task.status || 'pending'));
  head.append(el('span', 'task-meta', `${task.attempts || 0} intento(s)`));
  card.append(head);
  if (task.error) card.append(el('div', 'task-error', task.error));
  if (task.output) {
    const details = document.createElement('details');
    details.append(el('summary', '', 'Salida'), el('pre', '', task.output));
    card.append(details);
  }
  return card;
}

function renderRun(run) {
  const detail = $('detail');
  detail.hidden = false;
  detail.textContent = '';

  const head = el('div', 'detail-head');
  const titles = el('div');
  titles.append(el('div', 'detail-title', run.name || run.id));
  titles.append(el('div', 'detail-sub', `${run.id} · ${run.model || 'modelo auto'} · ${run.status}`));
  const actions = el('div', 'detail-actions');
  if (run.status === 'running' || run.status === 'pending') {
    const cancel = el('button', 'danger small', 'Cancelar');
    cancel.type = 'button';
    cancel.addEventListener('click', () => cancelRun(run.id));
    actions.append(cancel);
  }
  actions.append(statusPill(run.status));
  head.append(titles, actions);
  detail.append(head);

  const tasks = el('div', 'tasks');
  for (const id of run.order || Object.keys(run.tasks || {})) {
    const task = (run.tasks || {})[id];
    if (task) tasks.append(renderTask({ ...task, id }));
  }
  detail.append(tasks);
  detail.dataset.runId = run.id;
}

function applySnapshot(snapshot) {
  const detail = $('detail');
  if (!detail || detail.dataset.runId !== snapshot.id) return;
  for (const [id, task] of Object.entries(snapshot.tasks || {})) {
    const card = detail.querySelector(`[data-task-id="${CSS.escape(id)}"]`);
    if (!card) continue;
    card.className = 'task ' + (task.status || 'pending');
    const status = card.querySelector('.task-status');
    if (status) status.textContent = task.status || 'pending';
    const meta = card.querySelector('.task-meta');
    if (meta) meta.textContent = `${task.attempts || 0} intento(s)`;
  }
  const pill = detail.querySelector('.detail-head .pill');
  if (pill) {
    pill.className = 'pill ' + (snapshot.status || 'pending');
    pill.textContent = snapshot.status || 'pending';
  }
  if (!['running', 'pending'].includes(snapshot.status)) {
    closeEvents();
    loadRuns();
    openRun(snapshot.id);
  }
}

function closeEvents() {
  if (state.es) {
    state.es.close();
    state.es = null;
  }
}

function subscribe(runId) {
  closeEvents();
  if (!window.EventSource) return;
  const es = new EventSource(`${API}/runs/${encodeURIComponent(runId)}/events`);
  state.es = es;
  es.onmessage = (event) => {
    try {
      applySnapshot(JSON.parse(event.data));
    } catch (_) { /* ignore malformed */ }
  };
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) closeEvents();
  };
}

async function openRun(runId) {
  try {
    const run = await request(`/runs/${encodeURIComponent(runId)}`);
    state.selected = run.id;
    renderRun(run);
    for (const row of document.querySelectorAll('.run-row')) {
      row.classList.toggle('active', row.dataset.runId === run.id);
    }
    if (run.status === 'running' || run.status === 'pending') subscribe(run.id);
    else closeEvents();
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

function parseJsonGraph() {
  const raw = $('dag-json').value.trim();
  if (!raw) throw new Error('El JSON está vacío');
  const parsed = JSON.parse(raw);
  if (!parsed || !Array.isArray(parsed.tasks) || !parsed.tasks.length) {
    throw new Error('El DAG necesita al menos una tarea');
  }
  return normalizeGraph(parsed);
}

function loadGraph(raw, message) {
  state.graph = normalizeGraph(raw);
  state.selectedTask = null;
  renderGraph();
  writeJson();
  persistGraph();
  if (message) setMsg(message, 'ok');
}

function onJsonInput() {
  clearTimeout(state.jsonTimer);
  state.jsonTimer = setTimeout(() => {
    try {
      state.graph = normalizeGraph(JSON.parse($('dag-json').value));
      state.selectedTask = null;
      renderGraph();
      setMsg('JSON aplicado al grafo');
    } catch (_) {
      setMsg('JSON incompleto o inválido…');
    }
  }, 260);
}

function applyJson() {
  try {
    loadGraph(parseJsonGraph(), 'JSON aplicado');
  } catch (error) {
    setMsg('JSON inválido: ' + error.message, 'err');
  }
}

function addNode() {
  const id = uniqueId(`tarea-${state.graph.tasks.length + 1}`);
  const maxY = state.graph.tasks.reduce((max, task) => Math.max(max, task.y), 0);
  state.graph.tasks.push(normalizeTask({
    id,
    prompt: 'Describe la tarea.',
    x: 48,
    y: maxY + NODE_H + 34,
  }, state.graph.tasks.length));
  state.selectedTask = id;
  commitGraph();
  renderGraph();
  setMsg(`Tarea ${id} añadida`, 'ok');
}

async function addFiles(files) {
  const task = selectedTask();
  if (!task) return;
  for (const file of files) {
    if (file.size > 200000) {
      setMsg(`${file.name} supera 200 KB`, 'err');
      continue;
    }
    if (task.attachments.length >= 10) {
      setMsg('Máximo 10 adjuntos por tarea', 'err');
      break;
    }
    try {
      const content = await file.text();
      if (content.includes('\u0000')) {
        setMsg(`${file.name} parece binario`, 'err');
        continue;
      }
      const ref = await request('/attachments', {
        method: 'POST',
        body: JSON.stringify({ name: file.name, content }),
      });
      task.attachments.push(ref);
      commitGraph();
      renderAttachments(task);
      renderGraphNodesBadges();
      setMsg(`${file.name} adjuntado`, 'ok');
    } catch (error) {
      setMsg(`${file.name}: ${error.message}`, 'err');
    }
  }
  $('att-files').value = '';
}

async function startRun() {
  if (state.busy) return;
  let payload;
  try {
    const graph = parseJsonGraph();
    state.graph = graph;
    renderGraph();
    commitGraph();
    payload = graphPayload();
  } catch (error) {
    setMsg('JSON inválido: ' + error.message, 'err');
    return;
  }
  state.busy = true;
  $('run').disabled = true;
  setMsg('Ejecutando…');
  try {
    const run = await request('/run', { method: 'POST', body: JSON.stringify(payload) });
    setMsg(`Run ${run.id} iniciado`, 'ok');
    await loadRuns();
    await openRun(run.id);
  } catch (error) {
    setMsg(error.message, 'err');
  } finally {
    state.busy = false;
    $('run').disabled = false;
  }
}

async function cancelRun(runId) {
  try {
    await request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
    setMsg('Cancelación solicitada', 'ok');
    await openRun(runId);
  } catch (error) {
    setMsg(error.message, 'err');
  }
}

function updateSelectedField(field, value) {
  const task = selectedTask();
  if (!task) return;
  task[field] = value;
  if (field === 'prompt') {
    const node = document.querySelector(`#nodes .node[data-id="${CSS.escape(task.id)}"] .node-prompt`);
    if (node) node.textContent = value || 'Sin prompt';
  }
  commitGraph();
}

function wire() {
  let initial = EXAMPLE;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) initial = JSON.parse(saved);
  } catch (_) { /* ignore */ }
  loadGraph(initial);

  $('run').addEventListener('click', startRun);
  $('example').addEventListener('click', () => loadGraph(EXAMPLE, 'Ejemplo cargado'));
  $('clear').addEventListener('click', () => {
    state.graph = normalizeGraph({ name: 'DAG', max_parallel: 2, tasks: [] });
    state.selectedTask = null;
    commitGraph();
    renderGraph();
    setMsg('Lienzo vacío');
  });
  $('refresh').addEventListener('click', loadRuns);
  $('add-node').addEventListener('click', addNode);
  $('auto-layout').addEventListener('click', () => {
    autoLayoutGraph(state.graph);
    commitGraph();
    renderGraph();
    setMsg('Grafo ordenado por capas', 'ok');
  });
  $('apply-json').addEventListener('click', applyJson);
  $('dag-json').addEventListener('input', onJsonInput);

  $('dep-add').addEventListener('click', () => {
    const task = selectedTask();
    const dep = $('dep-select').value;
    if (task && dep) {
      task.depends_on.push(dep);
      commitGraph();
      renderGraph();
    }
  });

  $('node-delete').addEventListener('click', () => {
    const task = selectedTask();
    if (!task) return;
    if (!window.confirm(`¿Eliminar la tarea ${task.id}?`)) return;
    state.graph.tasks = state.graph.tasks.filter((item) => item.id !== task.id);
    for (const item of state.graph.tasks) {
      item.depends_on = item.depends_on.filter((dep) => dep !== task.id);
    }
    state.selectedTask = null;
    commitGraph();
    renderGraph();
    setMsg(`Tarea ${task.id} eliminada`, 'ok');
  });

  $('node-id').addEventListener('change', () => {
    const task = selectedTask();
    if (!task) return;
    const next = $('node-id').value.trim().slice(0, 80);
    if (!next) {
      $('node-id').value = task.id;
      setMsg('El id no puede estar vacío', 'err');
      return;
    }
    if (next !== task.id && state.graph.tasks.some((item) => item.id === next)) {
      $('node-id').value = task.id;
      setMsg(`Ya existe una tarea ${next}`, 'err');
      return;
    }
    if (next === task.id) return;
    const old = task.id;
    task.id = next;
    for (const item of state.graph.tasks) {
      item.depends_on = item.depends_on.map((dep) => (dep === old ? next : dep));
    }
    state.selectedTask = next;
    commitGraph();
    renderGraph();
  });

  $('node-prompt').addEventListener('input', (event) => {
    updateSelectedField('prompt', event.target.value);
  });
  $('node-model').addEventListener('input', (event) => {
    updateSelectedField('model', event.target.value.trim());
  });
  $('node-system').addEventListener('input', (event) => {
    updateSelectedField('system', event.target.value);
  });
  for (const [id, field, min, max] of [
    ['node-max-tokens', 'max_tokens', 32, 8000],
    ['node-retries', 'retries', 0, 5],
    ['node-timeout', 'timeout', 10, 1800],
  ]) {
    $(id).addEventListener('change', (event) => {
      const value = clampInt(event.target.value, min, max, field === 'max_tokens' ? 800 : 0);
      event.target.value = value;
      updateSelectedField(field, value);
    });
  }
  $('att-files').addEventListener('change', (event) => {
    addFiles([...event.target.files]);
  });

  loadRuns();
  state.listTimer = setInterval(loadRuns, 8000);
}

document.addEventListener('DOMContentLoaded', wire);

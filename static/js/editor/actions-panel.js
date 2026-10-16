/**
 * Actions panel (PhotoCraft parity, Phase 11) — a lightweight macro
 * recorder/player. Every step maps to a known editor command by CSS
 * selector; recording captures clicks on those selectors, and play
 * replays them in order. Named actions persist to localStorage.
 *
 * The module owns no editor state: it only dispatches clicks on the
 * selectors it is given.
 */

export const ACTION_COMMANDS = Object.freeze([
  ['flip-h', 'Flip Horizontal', '[data-image-action="flip-h"]'],
  ['flip-v', 'Flip Vertical', '[data-image-action="flip-v"]'],
  ['rotate-90', 'Rotate 90° CW', '[data-image-action="rotate-90"]'],
  ['rotate-180', 'Rotate 180°', '[data-image-action="rotate-180"]'],
  ['fill', 'Fill Foreground', '[data-image-action="fill"]'],
  ['merge-down', 'Merge Down', '#ge-merge-down'],
  ['flatten', 'Flatten', '#ge-flatten'],
  ['select-all', 'Select All', '[data-selection-action="all"]'],
  ['deselect', 'Deselect', '[data-selection-action="deselect"]'],
  ['invert-selection', 'Invert Selection', '[data-selection-action="invert"]'],
  ['blur-gaussian', 'Gaussian Blur', '[data-filter-action="blur-gaussian"]'],
  ['blur-motion', 'Motion Blur', '[data-filter-action="blur-motion"]'],
  ['noise-add', 'Add Noise', '[data-filter-action="noise-add"]'],
  ['pixelate-mosaic', 'Pixelate', '[data-filter-action="pixelate-mosaic"]'],
  ['sharpen', 'Sharpen', '[data-filter-action="sharpen"]'],
  ['stylize-emboss', 'Emboss', '[data-filter-action="stylize-emboss"]'],
  ['distort-twirl', 'Twirl', '[data-filter-action="distort-twirl"]'],
  ['rembg', 'Remove Background', '[data-tool-option="rembg"]'],
  ['inpaint', 'Inpaint', '[data-tool="inpaint"]'],
]);

const STORAGE_KEY = 'ge-actions';

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function commandById(id) {
  return ACTION_COMMANDS.find(command => command[0] === id) || null;
}

export function createActionsPanel({ showToast = () => {}, getLayers = null, selectLayer = null } = {}) {
  let steps = [];
  let recording = false;
  let saved = loadSaved();

  const body = el('div', 'ge-panel-body ge-actions-body');

  const nameRow = el('div', 'ge-actions-name-row');
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.placeholder = 'Action name';
  nameInput.className = 'ge-actions-name';
  const saveButton = el('button', 'ge-btn ge-btn-sm', 'Save');
  saveButton.type = 'button';
  nameRow.append(nameInput, saveButton);

  const addRow = el('div', 'ge-actions-add-row');
  const addSelect = document.createElement('select');
  addSelect.className = 'ge-actions-command';
  ACTION_COMMANDS.forEach(([id, label]) => addSelect.appendChild(new Option(label, id)));
  const addButton = el('button', 'ge-btn ge-btn-sm', 'Add');
  addButton.type = 'button';
  addRow.append(addSelect, addButton);

  const controls = el('div', 'ge-actions-controls');
  const recordButton = el('button', 'ge-btn ge-btn-sm', 'Record');
  recordButton.type = 'button';
  const playButton = el('button', 'ge-btn ge-btn-sm', 'Play');
  playButton.type = 'button';
  const clearButton = el('button', 'ge-btn ge-btn-sm', 'Clear');
  clearButton.type = 'button';
  controls.append(recordButton, playButton, clearButton);

  const batchRow = el('div', 'ge-actions-batch-row');
  const batchSelect = document.createElement('select');
  batchSelect.className = 'ge-actions-batch-select';
  const batchButton = el('button', 'ge-btn ge-btn-sm', 'Batch');
  batchButton.type = 'button';
  batchButton.title = 'Apply the selected action to every layer';
  const batchDocsButton = el('button', 'ge-btn ge-btn-sm', 'Batch docs');
  batchDocsButton.type = 'button';
  batchDocsButton.title = 'Apply the selected action to every open document';
  batchRow.append(batchSelect, batchButton, batchDocsButton);

  const list = el('div', 'ge-actions-list');
  const savedList = el('div', 'ge-actions-saved');

  body.append(nameRow, addRow, controls, batchRow, list, savedList);

  function renderList() {
    list.innerHTML = '';
    if (!steps.length) {
      list.appendChild(el('p', 'ge-panel-empty', 'No steps. Add commands or record.'));
      return;
    }
    steps.forEach((step, index) => {
      const command = commandById(step);
      const row = el('div', 'ge-actions-step', `<span class="ge-actions-step-index">${index + 1}</span><span class="ge-actions-step-label">${command ? command[1] : step}</span>`);
      const remove = el('button', 'ge-action-remove', '✕');
      remove.type = 'button';
      remove.addEventListener('click', () => { steps.splice(index, 1); renderList(); });
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  function renderSaved() {
    savedList.innerHTML = '';
    syncBatchSelect();
    const names = Object.keys(saved);
    if (!names.length) return;
    savedList.appendChild(el('div', 'ge-panel-title', 'SAVED'));
    names.forEach(name => {
      const row = el('div', 'ge-actions-saved-row');
      const loadButton = el('button', 'ge-btn ge-btn-sm', name);
      loadButton.type = 'button';
      loadButton.addEventListener('click', () => { steps = saved[name].slice(); renderList(); });
      const deleteButton = el('button', 'ge-action-remove', '✕');
      deleteButton.type = 'button';
      deleteButton.addEventListener('click', () => { delete saved[name]; persist(); renderSaved(); });
      row.append(loadButton, deleteButton);
      savedList.appendChild(row);
    });
  }

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(saved)); } catch { /* ignore */ }
  }

  function syncBatchSelect() {
    const names = Object.keys(saved);
    const previous = batchSelect.value;
    batchSelect.innerHTML = '';
    if (!names.length) {
      batchSelect.appendChild(new Option('No saved actions', ''));
      batchButton.disabled = true;
      batchDocsButton.disabled = true;
      return;
    }
    batchButton.disabled = false;
    batchDocsButton.disabled = false;
    names.forEach(name => batchSelect.appendChild(new Option(name, name)));
    if (names.includes(previous)) batchSelect.value = previous;
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch { return {}; }
  }

  function runStep(step) {
    const command = commandById(step);
    if (!command) return;
    document.querySelector(command[2])?.click();
  }

  function play() {
    steps.forEach((step, index) => setTimeout(() => runStep(step), index * 60));
    showToast(`Playing ${steps.length} action${steps.length === 1 ? '' : 's'}`);
  }

  async function runOnAllLayers() {
    const name = batchSelect.value;
    const action = name && saved[name] ? saved[name].slice() : null;
    if (!action || !action.length) { showToast('Select a saved action to batch'); return; }
    if (!getLayers || !selectLayer) { showToast('Batch is unavailable'); return; }
    const layers = getLayers() || [];
    if (layers.length < 1) { showToast('No layers to batch'); return; }
    showToast(`Batching "${name}" over ${layers.length} layer${layers.length === 1 ? '' : 's'}`);
    for (const layer of layers) {
      selectLayer(layer.id);
      for (const step of action) {
        runStep(step);
        await new Promise(resolve => setTimeout(resolve, 80));
      }
    }
    showToast('Batch complete');
  }

  function onDocumentClick(event) {
    if (!recording) return;
    const match = ACTION_COMMANDS.find(command => event.target.closest?.(command[2]));
    if (match && !steps.includes(match[0])) {
      steps.push(match[0]);
      renderList();
    }
  }

  function toggleRecord() {
    recording = !recording;
    recordButton.classList.toggle('active', recording);
    recordButton.textContent = recording ? 'Stop' : 'Record';
    showToast(recording ? 'Recording actions…' : 'Recording stopped');
  }

  addButton.addEventListener('click', () => { steps.push(addSelect.value); renderList(); });
  recordButton.addEventListener('click', toggleRecord);
  playButton.addEventListener('click', play);
  batchButton.addEventListener('click', () => { runOnAllLayers(); });
  batchDocsButton.addEventListener('click', async () => {
    const name = batchSelect.value;
    if (!name) { showToast('Select a saved action to batch'); return; }
    try {
      const response = await window.geEditorControl?.invoke?.('actions.batchDocuments', { name });
      const results = (response && response.result) || [];
      const ok = results.filter((entry) => entry.ok).length;
      showToast(`Batch docs: ${ok}/${results.length} document${results.length === 1 ? '' : 's'}`);
    } catch (error) {
      showToast('Batch docs failed: ' + (error?.message || error));
    }
  });
  clearButton.addEventListener('click', () => { steps = []; renderList(); });
  saveButton.addEventListener('click', () => {
    const name = (nameInput.value || '').trim();
    if (!name) return;
    saved[name] = steps.slice();
    persist();
    renderSaved();
    showToast(`Saved action "${name}"`);
  });
  document.addEventListener('click', onDocumentClick, true);

  const element = el('div', 'ge-panel');
  element.id = 'ge-panel-actions';
  element.appendChild(el('div', 'ge-panel-title', 'ACTIONS'));
  element.appendChild(body);
  renderList();
  renderSaved();

  return { element, play, run: runStep, runOnAllLayers, getSteps: () => steps.slice(), setSteps: (next) => { steps = next.slice(); renderList(); } };
}

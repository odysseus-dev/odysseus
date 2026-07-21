import { applyEdgeDock, clearRightDock } from './modalSnap.js';

const MIME = 'application/x-nomad-component';

const COMPONENTS = {
  chats:    { open: 'chats-library-btn', target: 'doclib-modal' },
  brain:    { open: 'tool-memory-btn',   target: 'memory-modal' },
  calendar: { open: 'tool-calendar-btn', target: 'calendar-modal' },
  compare:  { open: 'tool-compare-btn',  target: 'compare-model-overlay' },
  cookbook: { open: 'tool-cookbook-btn', target: 'cookbook-modal' },
  research: { open: 'tool-research-btn', target: 'research-overlay' },
  email:    { open: 'email-section-title', target: 'email-lib-modal' },
  gallery:  { open: 'tool-gallery-btn',  target: 'gallery-modal' },
  library:  { open: 'tool-library-btn',  target: 'doclib-modal' },
  notes:    { open: 'tool-notes-btn',    target: 'notes-pane' },
  tasks:    { open: 'tool-tasks-btn',    target: 'tasks-modal' },
  theme:    { open: 'tool-theme-btn',    target: 'theme-modal' },
  settings: { open: 'user-bar-settings', target: 'settings-modal' },
};

const SOURCES = {
  chats: ['rail-chats', 'chats-library-btn'],
  brain: ['rail-memory', 'tool-memory-btn'],
  calendar: ['rail-calendar', 'tool-calendar-btn'],
  compare: ['rail-compare', 'tool-compare-btn'],
  cookbook: ['rail-cookbook', 'tool-cookbook-btn'],
  research: ['rail-research', 'tool-research-btn'],
  email: ['rail-email', 'email-section-title'],
  gallery: ['rail-gallery', 'tool-gallery-btn'],
  library: ['rail-archive', 'tool-library-btn'],
  notes: ['rail-notes', 'tool-notes-btn'],
  tasks: ['rail-tasks', 'tool-tasks-btn'],
  theme: ['rail-theme', 'tool-theme-btn'],
  settings: ['rail-settings', 'user-bar-settings'],
};

let layer = null;
let draggedKey = '';
let draggedSource = null;

function buildLayer() {
  if (layer) return layer;
  layer = document.createElement('div');
  layer.className = 'nomad-drop-layer';
  layer.setAttribute('aria-hidden', 'true');
  layer.innerHTML = `
    <div class="nomad-drop-zone" data-side="left" data-index="01 / PORT">DOCK LEFT</div>
    <div class="nomad-drop-zone" data-side="center" data-index="02 / FIELD">OPEN FLOATING</div>
    <div class="nomad-drop-zone" data-side="right" data-index="03 / STARBOARD">DOCK RIGHT</div>`;
  document.body.appendChild(layer);

  layer.querySelectorAll('.nomad-drop-zone').forEach((zone) => {
    zone.addEventListener('dragenter', (event) => {
      event.preventDefault();
      layer.querySelectorAll('.is-target').forEach(el => el.classList.remove('is-target'));
      zone.classList.add('is-target');
    });
    zone.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });
    zone.addEventListener('dragleave', (event) => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-target');
    });
    zone.addEventListener('drop', async (event) => {
      event.preventDefault();
      const key = event.dataTransfer?.getData(MIME) || draggedKey;
      const side = zone.dataset.side;
      cleanupDrag();
      if (key && COMPONENTS[key]) await openComponent(key, side);
    });
  });
  return layer;
}

function isVisible(element) {
  if (!element || element.classList.contains('hidden')) return false;
  const style = getComputedStyle(element);
  return style.display !== 'none' && style.visibility !== 'hidden';
}

function waitForTarget(id, tries = 50) {
  return new Promise((resolve) => {
    const inspect = () => {
      const target = document.getElementById(id);
      if (isVisible(target)) return resolve(target);
      if (--tries <= 0) return resolve(null);
      setTimeout(inspect, 20);
    };
    inspect();
  });
}

async function openComponent(key, side) {
  const component = COMPONENTS[key];
  const existing = document.getElementById(component.target);
  if (!isVisible(existing)) document.getElementById(component.open)?.click();
  if (side === 'center') {
    if (existing?.classList.contains('modal-left-docked') ||
        existing?.classList.contains('modal-right-docked')) {
      clearRightDock(existing);
    }
    return;
  }

  const target = await waitForTarget(component.target);
  if (!target) return;
  applyEdgeDock(target, side);
}

function cleanupDrag() {
  draggedSource?.classList.remove('nomad-drag-source');
  draggedSource = null;
  draggedKey = '';
  if (layer) {
    layer.classList.remove('is-visible');
    layer.querySelectorAll('.is-target').forEach(el => el.classList.remove('is-target'));
  }
  document.body.classList.remove('nomad-component-dragging');
}

function wireSource(element, key) {
  if (!element || element.dataset.nomadDragReady) return;
  element.dataset.nomadTool = key;
  element.dataset.nomadDragReady = '1';
  element.draggable = true;
  const oldTitle = element.getAttribute('title') || COMPONENTS[key]?.open || key;
  if (!oldTitle.includes('Drag to workspace')) {
    element.setAttribute('title', `${oldTitle} · Drag to workspace`);
  }
  element.addEventListener('dragstart', (event) => {
    draggedKey = key;
    draggedSource = element;
    element.classList.add('nomad-drag-source');
    document.body.classList.add('nomad-component-dragging');
    buildLayer().classList.add('is-visible');
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData(MIME, key);
      event.dataTransfer.setData('text/plain', `NOMAD:${key}`);
    }
  });
  element.addEventListener('dragend', cleanupDrag);
}

function wireAllSources() {
  Object.entries(SOURCES).forEach(([key, ids]) => {
    ids.forEach(id => wireSource(document.getElementById(id), key));
  });
}

function init() {
  buildLayer();
  wireAllSources();
  // Some tool rows are mounted or replaced after boot.
  const observer = new MutationObserver(wireAllSources);
  observer.observe(document.body, { childList: true, subtree: true });
  window.addEventListener('blur', cleanupDrag);
  document.addEventListener('drop', (event) => {
    if (draggedKey && !event.target.closest?.('.nomad-drop-zone')) cleanupDrag();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}

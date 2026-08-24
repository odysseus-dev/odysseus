// Universal Ctrl+K launcher: chats, projects, actions, and message search.

import uiModule from './ui.js';

let API_BASE = '';
let debounceTimer = null;
let selectedIndex = 0;
let visibleItems = [];
let querySequence = 0;

const el = (id) => document.getElementById(id);
const esc = (value) => uiModule.esc(String(value || ''));
const sessionApi = () => window.sessionModule || {};

function hideMobileSidebarForSearch() {
  if (window.innerWidth >= 768) return;
  const sidebar = el('sidebar');
  const rail = el('icon-rail');
  const backdrop = el('sidebar-backdrop');
  let changed = false;
  if (sidebar && !sidebar.classList.contains('hidden')) { sidebar.classList.add('hidden'); changed = true; }
  if (rail && rail.classList.contains('mobile-mini')) {
    rail.classList.remove('mobile-mini');
    rail.style.cssText = '';
    changed = true;
  }
  backdrop?.classList.remove('visible');
  if (changed) { try { window.syncRailSide?.(); } catch (_) {} }
}

function icon(kind) {
  const paths = {
    action: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    project: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    message: '<circle cx="10" cy="10" r="7"/><path d="M21 21l-4.35-4.35"/>',
    permission: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/><path d="m9 12 2 2 4-4"/>',
    activity: '<path d="M3 12h4l2.2-6 4.1 12 2.2-6H21"/>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[kind] || paths.action}</svg>`;
}

function currentPermissionMode() {
  return window.odysseusPermissions?.getMode?.() || 'auto';
}

function localItems() {
  const sessions = (sessionApi().getSessions?.() || [])
    .filter((session) => !session.archived && session.folder !== 'Assistant' && session.folder !== 'Tasks')
    .sort((a, b) => String(b.last_message_at || b.updated_at || '').localeCompare(String(a.last_message_at || a.updated_at || '')));
  const projects = window.workspaceModule?.getProjectEntries?.() || [];
  const mode = currentPermissionMode();
  const actions = [
    { kind: 'action', group: 'Actions', title: 'New chat', subtitle: 'Start in the current project', keywords: 'create conversation', shortcut: 'Ctrl N', action: () => el('sidebar-new-chat-btn')?.click() },
    { kind: 'activity', group: 'Actions', title: 'Agent activity', subtitle: 'Running work, results, failures, and approvals', keywords: 'status agents background results', action: () => window.odysseusActivity?.open?.() },
    { kind: 'action', group: 'Actions', title: 'Start a task', subtitle: 'Create a scheduled or one-off agent task', keywords: 'schedule automation add task', action: () => window.tasksModule?.openTasks?.(null, { tab: 'new' }) },
    { kind: 'activity', group: 'Actions', title: 'Open full activity', subtitle: 'Inspect recent task runs', keywords: 'tasks runs completed failed', action: () => window.tasksModule?.openTasks?.(null, { tab: 'activity' }) },
  ];
  const permissionLabels = {
    sandboxed_workspace: ['Sandboxed workspace', 'Workspace files only; commands need approval and web is blocked'],
    sandboxed_workspace: ['Sandboxed workspace', 'Workspace files only; commands need approval and web is blocked'],
    auto: ['Full access', 'Run tools without asking'],
    ask_actions: ['Approve for me', 'Ask only for potentially unsafe actions'],
    ask_all: ['Ask for approval', 'Ask before every tool'],
    read_only: ['Read only', 'Block all changes'],
  };
  Object.entries(permissionLabels).forEach(([value, copy]) => actions.push({
    kind: 'permission', group: 'Permission mode', title: copy[0], subtitle: copy[1], keywords: `permissions ${value}`, current: mode === value,
    action: () => window.odysseusPermissions?.setMode?.(value),
  }));

  const projectItems = projects.map((project) => ({
    kind: 'project', group: 'Projects', title: project.name, subtitle: project.path, meta: `${project.sessionCount} chat${project.sessionCount === 1 ? '' : 's'}`, keywords: project.path,
    action: () => window.workspaceModule?.activateProject?.(project.path),
  }));
  const chatItems = sessions.map((session) => ({
    kind: 'chat', group: 'Chats', title: session.name || 'Untitled chat', subtitle: session.workspace || 'No project', meta: formatTimestamp(session.last_message_at || session.updated_at), keywords: `${session.workspace || ''} ${session.model || ''}`,
    action: async () => {
      if (session.workspace) window.workspaceModule?.activateProject?.(session.workspace);
      await sessionApi().selectSession?.(session.id);
    },
  }));
  return [...actions, ...projectItems, ...chatItems];
}

function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const days = Math.floor((Date.now() - date.getTime()) / 86400000);
  if (days <= 0) return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (days < 7) return date.toLocaleDateString([], { weekday: 'short' });
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function rank(item, query) {
  const title = item.title.toLocaleLowerCase();
  const haystack = `${item.title} ${item.subtitle || ''} ${item.keywords || ''}`.toLocaleLowerCase();
  if (title === query) return 0;
  if (title.startsWith(query)) return 1;
  if (haystack.includes(query)) return 2;
  return 99;
}

function highlight(value, query) {
  const safe = esc(value);
  if (!query) return safe;
  const expression = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig');
  return safe.replace(expression, '<mark class="search-highlight">$1</mark>');
}

function render(items, query = '') {
  visibleItems = items;
  selectedIndex = items.length ? Math.min(selectedIndex, items.length - 1) : -1;
  const container = el('search-results');
  if (!container) return;
  if (!items.length) {
    container.innerHTML = `<div class="launcher-empty"><strong>No matches</strong><span>Try a chat title, project, action, or message text.</span></div>`;
    return;
  }
  let lastGroup = '';
  container.innerHTML = items.map((item, index) => {
    const group = item.group !== lastGroup ? `<div class="launcher-group-label">${esc(item.group)}</div>` : '';
    lastGroup = item.group;
    return `${group}<button type="button" class="launcher-item${index === selectedIndex ? ' selected' : ''}" data-index="${index}">
      <span class="launcher-item-icon ${esc(item.kind)}">${icon(item.kind)}</span>
      <span class="launcher-item-copy"><strong>${highlight(item.title, query)}</strong><small>${highlight(item.subtitle || '', query)}</small></span>
      ${item.current ? '<span class="launcher-current">Current</span>' : item.meta ? `<span class="launcher-item-meta">${esc(item.meta)}</span>` : ''}
      ${item.shortcut ? `<kbd class="launcher-shortcut">${esc(item.shortcut)}</kbd>` : ''}
    </button>`;
  }).join('');
  container.querySelectorAll('.launcher-item').forEach((button) => {
    button.addEventListener('pointermove', () => { selectedIndex = Number(button.dataset.index); updateSelection(false); });
    button.addEventListener('click', () => execute(Number(button.dataset.index)));
  });
}

function defaultItems(items) {
  const actions = items.filter((item) => item.group === 'Actions').slice(0, 4);
  const projects = items.filter((item) => item.group === 'Projects').slice(0, 5);
  const chats = items.filter((item) => item.group === 'Chats').slice(0, 6);
  return [...actions, ...projects, ...chats];
}

function renderLocal(query) {
  const items = localItems();
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) {
    selectedIndex = 0;
    render(defaultItems(items), '');
    return;
  }
  const filtered = items.map((item) => ({ item, score: rank(item, normalized) }))
    .filter((entry) => entry.score < 99)
    .sort((a, b) => a.score - b.score)
    .map((entry) => entry.item)
    .slice(0, 24);
  selectedIndex = 0;
  render(filtered, normalized);
}

async function searchMessages(query, sequence) {
  try {
    const response = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=12`);
    if (!response.ok || sequence !== querySequence) return;
    const rows = await response.json();
    if (!Array.isArray(rows) || sequence !== querySequence) return;
    const messages = rows.map((row) => ({
      kind: 'message', group: 'Message matches', title: row.session_name || 'Untitled chat', subtitle: row.content_snippet || '', meta: formatTimestamp(row.timestamp), keywords: '',
      action: () => sessionApi().selectSession?.(row.session_id),
    }));
    const local = visibleItems.filter((item) => item.group !== 'Message matches');
    render([...local, ...messages].slice(0, 30), query.toLocaleLowerCase());
  } catch (_) {}
}

function updateSelection(scroll = true) {
  const items = Array.from(el('search-results')?.querySelectorAll('.launcher-item') || []);
  items.forEach((item, index) => item.classList.toggle('selected', index === selectedIndex));
  if (scroll && selectedIndex >= 0) items[selectedIndex]?.scrollIntoView({ block: 'nearest' });
}

async function execute(index) {
  const item = visibleItems[index];
  if (!item) return;
  closeSearch();
  await item.action?.();
}

function handleInput(event) {
  const query = event.target.value.trim();
  querySequence += 1;
  const sequence = querySequence;
  if (debounceTimer) clearTimeout(debounceTimer);
  renderLocal(query);
  if (query.length >= 2) debounceTimer = setTimeout(() => searchMessages(query, sequence), 220);
}

function handleKeydown(event) {
  if (!isOpen()) return;
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    selectedIndex = visibleItems.length ? (selectedIndex + 1) % visibleItems.length : -1;
    updateSelection();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    selectedIndex = visibleItems.length ? (selectedIndex - 1 + visibleItems.length) % visibleItems.length : -1;
    updateSelection();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    execute(selectedIndex);
  }
}

export function openSearch() {
  hideMobileSidebarForSearch();
  const overlay = el('search-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  requestAnimationFrame(() => overlay.classList.add('visible'));
  const input = el('search-input');
  if (input) { input.value = ''; input.focus(); }
  querySequence += 1;
  renderLocal('');
}

export function closeSearch() {
  const overlay = el('search-overlay');
  if (!overlay || overlay.classList.contains('hidden')) return;
  overlay.classList.remove('visible');
  setTimeout(() => {
    if (!overlay.classList.contains('visible')) overlay.classList.add('hidden');
  }, 170);
  visibleItems = [];
  selectedIndex = 0;
}

export function isOpen() {
  const overlay = el('search-overlay');
  return !!overlay && !overlay.classList.contains('hidden');
}

export function init(apiBase) {
  API_BASE = apiBase || '';
  el('search-input')?.addEventListener('input', handleInput);
  el('search-input')?.addEventListener('keydown', handleKeydown);
  el('search-overlay')?.addEventListener('pointerdown', (event) => { if (event.target === el('search-overlay')) closeSearch(); });
  document.addEventListener('keydown', (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLocaleLowerCase() !== 'k') return;
    event.preventDefault();
    isOpen() ? closeSearch() : openSearch();
  });
}

export default { init, openSearch, closeSearch, isOpen };

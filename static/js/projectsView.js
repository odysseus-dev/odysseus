/**
 * projectsView.js — Projects List Page + Project View
 *
 * Two screens rendered as a fixed overlay (z-index 900):
 *   1. Projects List — grid of project cards, search, sort, new project
 *   2. Project View  — new-chat box + sessions list (left) + right panel
 *                      (Memory, Instructions, Files) — mirrors Claude.ai layout
 *
 * Entry points (window.projectsViewModule):
 *   openProjectsListView()   — show the list page
 *   openProjectView(id)      — jump directly to a project
 *   closeProjectsView()      — close overlay and return to chat
 *   isProjectsViewActive()   — boolean
 *   getActiveProjectId()     — current project id or null
 */

import { loadProjects, loadSessions, selectSession } from './sessions.js';

const API_BASE = window.location.origin;

let _activeView     = null;   // null | 'list' | 'project'
let _activeProjectId = null;
let _projectsCache  = [];
let _sortOrder      = 'activity';
let _searchQuery    = '';
let _navToken       = 0;
let _pendingProjectId = null;

// ── i18n strings ────────────────────────────────────────────────────────── //
const T = {
  projects:           'Projects',
  allProjects:        'All projects',
  newProject:         'New project',
  sortBy:             'Sort by',
  sortActivity:       'Activity',
  sortName:           'Name',
  sortCreated:        'Date created',
  searchPlaceholder:  'Search projects…',
  noProjectsEmpty:    'No projects yet. Click "New project" to get started.',
  noProjectsSearch:   'No projects found.',
  updatedAt:          'Updated',
  projectName:        'Project name',
  description:        'Description (optional)',
  cancel:             'Cancel',
  create:             'Create',
  creating:           'Creating…',
  save:               'Save',
  saving:             'Saving…',
  rename:             'Rename',
  archive:            'Archive project',
  archiveConfirm:     (name) => `Archive project "${name}"?`,
  newConvPlaceholder: 'How can I help you today?',
  noConversations:    'No conversations in this project yet.',
  untitledConv:       'Untitled conversation',
  lastMessage:        'Last message',
  renameConv:         'Rename',
  removeFromProject:  'Remove from project',
  renameConvTitle:    'New conversation name:',
  projectOptions:     'Project options',
  renameProject:      'Rename project',
  memory:             'Memory',
  onlyYou:            'Only you',
  synthesize:         'Synthesize from sessions',
  synthesizing:       'Synthesizing…',
  noMemory:           'No memory yet. Use synthesize to create one.',
  synthFailed:        'Synthesis failed.',
  lastUpdated:        'Last updated',
  instructions:       'Instructions',
  editInstructions:   'Edit instructions',
  instrPlaceholder:   'Add custom instructions for every conversation in this project…',
  noInstructions:     'No custom instructions yet.',
  files:              'Files',
  addFile:            'Add file',
  addFilesHint:       'Add PDFs, documents, or other texts to use as reference in this project.',
  noDocuments:        'No documents yet.',
  remove:             'Remove',
  loadingProject:     'Loading project…',
  loadingProjects:    'Loading projects…',
  errorProject:       'Error loading project.',
  addDocument:        'Add document',
  searchDocuments:    'Search documents…',
};

// ── Date helper ──────────────────────────────────────────────────────────── //
function _relDate(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), diff = (now - d) / 1000;
  if (diff < 60)          return 'just now';
  if (diff < 3600)        return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)       return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30)  return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

// ── API ──────────────────────────────────────────────────────────────────── //
async function _api(method, path, body) {
  const opts = { method };
  if (body) opts.body = body;
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  return res.json();
}
async function _fetchProjects()          { try { return await _api('GET', '/api/projects'); } catch { return []; } }
async function _fetchProject(id)         { return _api('GET', `/api/projects/${id}`); }
async function _fetchProjectSessions(id) { try { return await _api('GET', `/api/projects/${id}/sessions`); } catch { return []; } }
async function _fetchProjectDocuments(id){ try { return await _api('GET', `/api/projects/${id}/documents`); } catch { return []; } }
async function _fetchProjectMemories(id) { try { return await _api('GET', `/api/projects/${id}/memories`); } catch { return []; } }
async function _fetchUserDocuments(q='') {
  try {
    const qs = q ? `&search=${encodeURIComponent(q)}` : '';
    const data = await _api('GET', `/api/documents/library?limit=50&sort=recent${qs}`);
    return Array.isArray(data) ? data : (data.documents || []);
  } catch { return []; }
}

// ── Overlay mount/unmount ─────────────────────────────────────────────────── //
function _removePreviousOverlay() {
  // Remove the DOM element only — don't touch _activeView, _closedAt, or listeners.
  const el = window._projectsOverlayEl || document.getElementById('projects-view-overlay');
  if (el && el.parentNode) el.parentNode.removeChild(el);
  window._projectsOverlayEl = null;
  document.removeEventListener('click', _outsideClickHandler, true);
  ++_navToken;
}

function _mountOverlay() {
  _removePreviousOverlay();
  const overlay = document.createElement('div');
  overlay.id = 'projects-view-overlay';
  overlay.style.cssText = [
    'position:fixed', 'top:0', 'bottom:0', 'right:0',
    'left:calc(var(--icon-rail-w,48px) + var(--sidebar-w,0px))',
    'z-index:900', 'display:flex', 'flex-direction:column',
    'overflow:hidden', 'background:var(--bg)',
  ].join(';');
  const wrapper = document.createElement('div');
  wrapper.id = 'projects-view-wrapper';
  wrapper.style.cssText = 'flex:1;overflow:hidden;display:flex;flex-direction:column;';
  overlay.appendChild(wrapper);
  document.body.appendChild(overlay);
  window._projectsOverlayEl = overlay;
  document.getElementById('rail-projects')?.classList.add('active');
  document.getElementById('sidebar-projects-header')?.classList.add('active');
  // Register outside-click handler with a tick delay so the opening click
  // doesn't immediately close the overlay
  setTimeout(() => document.addEventListener('click', _outsideClickHandler, true), 50);
  return wrapper;
}

function _unmountOverlay() {
  ++_navToken;
  _closedAt = Date.now();
  // Remove the overlay element directly — don't rely solely on window._projectsViewClose
  // because it may have been reassigned or may not execute in time.
  const el = window._projectsOverlayEl || document.getElementById('projects-view-overlay');
  if (el && el.parentNode) el.parentNode.removeChild(el);
  window._projectsOverlayEl = null;
  if (window._projectsViewClose) window._projectsViewClose();
  _activeView = null;
  _activeProjectId = null;
  document.getElementById('rail-projects')?.classList.remove('active');
  document.getElementById('sidebar-projects-header')?.classList.remove('active');
  // Remove the global outside-click listener so it doesn't linger
  document.removeEventListener('click', _outsideClickHandler, true);
}

// Close the overlay whenever the user clicks something outside of it.
// stopPropagation prevents the same click from re-opening the overlay via
// sidebar item listeners (e.g. project item → openProjectView).
function _outsideClickHandler(e) {
  const overlay = document.getElementById('projects-view-overlay');
  if (!overlay) { _unmountOverlay(); return; }
  // Let clicks inside the overlay through normally
  if (overlay.contains(e.target)) return;
  // Also let dialog/modal overlays (z-index > 900) through
  const zIndex = parseInt(getComputedStyle(e.target.closest?.('[style*="z-index"]') || e.target).zIndex, 10);
  if (zIndex > 900) return;
  e.stopPropagation();
  _unmountOverlay();
}

// Guard: prevents openProjectView/openProjectsListView from running for a
// short window after a programmatic close (e.g. when a click that closed
// the overlay bubbles to a sidebar project item and re-opens it).
let _closedAt = 0;
function _recentlyClosed() { return Date.now() - _closedAt < 300; }

// ── Public API ─────────────────────────────────────────────────────────────── //
export async function openProjectsListView() {
  if (_recentlyClosed()) return;
  _activeView = 'list';
  _activeProjectId = null;
  const wrapper = _mountOverlay();
  const tok = ++_navToken;
  wrapper.innerHTML = `<div style="padding:40px;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;">${T.loadingProjects}</div>`;
  _projectsCache = await _fetchProjects();
  if (_navToken !== tok) return;
  _renderListPage(wrapper);
}

export async function openProjectView(projectId) {
  if (_recentlyClosed()) return;
  _activeView = 'project';
  _activeProjectId = projectId;
  const wrapper = _mountOverlay();
  const tok = ++_navToken;
  wrapper.innerHTML = `<div style="padding:40px;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;">${T.loadingProject}</div>`;
  try {
    const [proj, sessions, docs, mems] = await Promise.all([
      _fetchProject(projectId),
      _fetchProjectSessions(projectId),
      _fetchProjectDocuments(projectId),
      _fetchProjectMemories(projectId),
    ]);
    if (_navToken !== tok) return;
    _renderProjectPage(wrapper, proj, sessions, docs, mems);
  } catch {
    if (_navToken !== tok) return;
    wrapper.innerHTML = `<div style="padding:40px;color:color-mix(in srgb, var(--fg) 55%, transparent);">${T.errorProject}</div>`;
  }
}

export function closeProjectsView() { if (_activeView) _unmountOverlay(); }
export function isProjectsViewActive() { return _activeView !== null; }
export function getActiveProjectId() { return _activeView === 'project' ? _activeProjectId : null; }
export function consumePendingProjectId() { const id = _pendingProjectId; _pendingProjectId = null; return id; }

// ══════════════════════════════════════════════════════════════════════════════
// PROJECTS LIST PAGE
// ══════════════════════════════════════════════════════════════════════════════
function _renderListPage(wrapper) {
  wrapper.innerHTML = '';
  wrapper.style.cssText = 'flex:1;overflow-y:auto;padding:40px 56px;max-width:1200px;width:100%;margin:0 auto;box-sizing:border-box;';

  // Header
  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;flex-wrap:wrap;gap:12px;';

  const title = document.createElement('h1');
  title.textContent = T.projects;
  title.style.cssText = 'margin:0;font-size:30px;font-weight:700;color:var(--fg);';

  const controls = document.createElement('div');
  controls.style.cssText = 'display:flex;align-items:center;gap:10px;';

  // Sort selector
  const sortWrap = document.createElement('div');
  sortWrap.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:13px;color:color-mix(in srgb, var(--fg) 55%, transparent);white-space:nowrap;';
  sortWrap.innerHTML = `<span>${T.sortBy}</span>`;
  const sortSel = document.createElement('select');
  sortSel.style.cssText = 'background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer;outline:none;font-weight:600;';
  [['activity', T.sortActivity], ['name', T.sortName], ['created', T.sortCreated]].forEach(([v, l]) => {
    const o = Object.assign(document.createElement('option'), { value: v, textContent: l });
    if (v === _sortOrder) o.selected = true;
    sortSel.appendChild(o);
  });
  sortSel.addEventListener('change', () => { _sortOrder = sortSel.value; _renderGrid(grid, _filtered()); });
  sortWrap.appendChild(sortSel);

  const newBtn = document.createElement('button');
  newBtn.textContent = T.newProject;
  newBtn.style.cssText = 'background:var(--fg);color:var(--bg);border:none;border-radius:8px;padding:8px 18px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap;';
  newBtn.addEventListener('click', () => _showCreateDialog(wrapper));

  controls.appendChild(sortWrap);
  controls.appendChild(newBtn);
  hdr.appendChild(title);
  hdr.appendChild(controls);
  wrapper.appendChild(hdr);

  // Search bar
  const searchWrap = document.createElement('div');
  searchWrap.style.cssText = 'position:relative;margin-bottom:28px;';
  searchWrap.innerHTML = `<svg style="position:absolute;left:14px;top:50%;transform:translateY(-50%);color:color-mix(in srgb, var(--fg) 55%, transparent);pointer-events:none;" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="10" cy="10" r="7"/><path d="M21 21l-4.35-4.35"/></svg>`;
  const searchInp = document.createElement('input');
  searchInp.type = 'text';
  searchInp.placeholder = T.searchPlaceholder;
  searchInp.value = _searchQuery;
  searchInp.style.cssText = 'width:100%;box-sizing:border-box;background:var(--panel);color:var(--fg);border:1.5px solid var(--border);border-radius:10px;padding:12px 14px 12px 40px;font-size:14px;outline:none;transition:border-color 0.15s;';
  searchInp.addEventListener('focus',  () => { searchInp.style.borderColor = 'var(--accent-primary, var(--red))'; });
  searchInp.addEventListener('blur',   () => { searchInp.style.borderColor = 'var(--border)'; });
  let dbTimer;
  searchInp.addEventListener('input', () => {
    clearTimeout(dbTimer);
    dbTimer = setTimeout(() => { _searchQuery = searchInp.value; _renderGrid(grid, _filtered()); }, 120);
  });
  searchWrap.appendChild(searchInp);
  wrapper.appendChild(searchWrap);

  // Grid
  const grid = document.createElement('div');
  grid.id = 'projects-grid';
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;';
  wrapper.appendChild(grid);
  _renderGrid(grid, _filtered());
}

function _filtered() {
  let list = [..._projectsCache];
  if (_searchQuery.trim()) {
    const q = _searchQuery.toLowerCase();
    list = list.filter(p => (p.name || '').toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q));
  }
  if (_sortOrder === 'name')    list.sort((a, b) => (a.name||'').localeCompare(b.name||''));
  else if (_sortOrder==='created') list.sort((a,b) => new Date(b.created_at||0)-new Date(a.created_at||0));
  else list.sort((a,b) => new Date(b.updated_at||0)-new Date(a.updated_at||0));
  return list;
}

function _renderGrid(grid, projects) {
  grid.innerHTML = '';
  if (!projects.length) {
    const empty = document.createElement('div');
    empty.style.cssText = 'grid-column:1/-1;text-align:center;padding:60px 0;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:14px;';
    empty.textContent = _searchQuery ? T.noProjectsSearch : T.noProjectsEmpty;
    grid.appendChild(empty);
    return;
  }
  projects.forEach(p => {
    const card = document.createElement('div');
    card.style.cssText = 'background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:20px;cursor:pointer;transition:border-color 0.15s,background 0.15s;display:flex;flex-direction:column;min-height:130px;';
    card.addEventListener('mouseenter', () => { card.style.borderColor='var(--accent-primary, var(--red))'; card.style.background='color-mix(in srgb, var(--red) 8%, transparent)'; });
    card.addEventListener('mouseleave', () => { card.style.borderColor='var(--border)'; card.style.background='var(--panel)'; });
    card.addEventListener('click', () => openProjectView(p.id));

    const name = document.createElement('div');
    name.textContent = p.name;
    name.style.cssText = 'font-weight:700;font-size:15px;color:var(--fg);margin-bottom:6px;';
    card.appendChild(name);

    if (p.description) {
      const desc = document.createElement('div');
      desc.textContent = p.description;
      desc.style.cssText = 'font-size:12px;color:color-mix(in srgb, var(--fg) 55%, transparent);flex:1;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;';
      card.appendChild(desc);
    } else {
      card.appendChild(Object.assign(document.createElement('div'), { style: 'flex:1' }));
    }

    const meta = document.createElement('div');
    meta.style.cssText = 'font-size:11px;color:color-mix(in srgb, var(--fg) 55%, transparent);margin-top:auto;padding-top:12px;border-top:1px solid color-mix(in srgb,var(--border) 50%,transparent);';
    meta.textContent = T.updatedAt + ' ' + _relDate(p.updated_at);
    card.appendChild(meta);
    grid.appendChild(card);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// PROJECT VIEW — mirrors Claude.ai layout
// ══════════════════════════════════════════════════════════════════════════════
function _renderProjectPage(wrapper, proj, sessions, docs, mems) {
  wrapper.innerHTML = '';
  wrapper.style.cssText = 'flex:1;overflow:hidden;display:flex;flex-direction:column;';

  // ── Top bar (back link + title + menu) ──────────────────────────────────
  const topBar = document.createElement('div');
  topBar.style.cssText = 'padding:28px 40px 0;flex-shrink:0;';

  const back = document.createElement('button');
  back.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/></svg> ${T.allProjects}`;
  back.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);cursor:pointer;font-size:13px;padding:0;margin-bottom:18px;display:inline-flex;align-items:center;gap:6px;';
  back.addEventListener('click', () => openProjectsListView());
  topBar.appendChild(back);

  const titleRow = document.createElement('div');
  titleRow.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:0;';

  const projTitle = document.createElement('h1');
  projTitle.textContent = proj.name;
  projTitle.id = 'pv-proj-title';
  projTitle.style.cssText = 'margin:0;font-size:28px;font-weight:700;color:var(--fg);flex:1;';

  const menuBtn = document.createElement('button');
  menuBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>';
  menuBtn.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);cursor:pointer;padding:4px 8px;border-radius:6px;';
  menuBtn.title = T.projectOptions;
  menuBtn.addEventListener('click', e => _showProjectMenu(e, proj));

  titleRow.appendChild(projTitle);
  titleRow.appendChild(menuBtn);
  topBar.appendChild(titleRow);
  wrapper.appendChild(topBar);

  // ── Two-column body ──────────────────────────────────────────────────────
  const body = document.createElement('div');
  body.style.cssText = 'flex:1;overflow:hidden;display:flex;gap:0;padding:24px 40px 0;box-sizing:border-box;';
  wrapper.appendChild(body);

  // Left column: new-chat box + sessions
  const left = document.createElement('div');
  left.style.cssText = 'flex:1;min-width:0;overflow-y:auto;padding-right:28px;padding-bottom:40px;';
  body.appendChild(left);

  // Right column: Memory / Instructions / Files cards
  const right = document.createElement('div');
  right.style.cssText = 'width:300px;flex-shrink:0;overflow-y:auto;padding-bottom:40px;display:flex;flex-direction:column;gap:10px;';
  body.appendChild(right);

  // New chat box
  _buildNewChatBox(left, proj.id);

  // Sessions list
  _buildSessionsList(left, sessions, proj.id, proj);

  // Side panel cards
  _buildMemoryCard(right, proj, mems);
  _buildInstructionsCard(right, proj);
  _buildFilesCard(right, proj.id, docs);
}

// ── New chat input ────────────────────────────────────────────────────────── //
function _buildNewChatBox(container, projectId) {
  const box = document.createElement('div');
  box.style.cssText = [
    'background:var(--panel)',
    'border:1.5px solid var(--border)',
    'border-radius:14px',
    'padding:18px 20px 14px',
    'margin-bottom:28px',
    'cursor:text',
    'transition:border-color 0.15s',
  ].join(';');

  const inp = document.createElement('input');
  inp.type = 'text';
  inp.placeholder = T.newConvPlaceholder;
  inp.style.cssText = 'background:none;border:none;outline:none;color:var(--fg);font-size:15px;width:100%;';

  inp.addEventListener('keydown', async e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const msg = inp.value.trim();
    await _startNewProjectChat(projectId, msg);
  });

  box.addEventListener('mouseenter', () => { box.style.borderColor = 'var(--accent-primary, var(--red))'; });
  box.addEventListener('mouseleave', () => { box.style.borderColor = 'var(--border)'; });
  box.addEventListener('click', () => inp.focus());
  box.appendChild(inp);
  container.appendChild(box);
}

function _startNewProjectChat(projectId, initialMessage) {
  // Use the same "pending chat" flow as the New Chat button — this gives
  // the user the full chat UI (model picker, send button, etc.) immediately.
  // The session is only written to the DB on the first message send, at which
  // point sessions.js will assign it to the project (pending.projectId).
  const sm = window.sessionModule;
  if (!sm) return;

  // Read the currently selected model/endpoint from the model picker
  const pending = sm.getPendingChat && sm.getPendingChat();
  const sessions = sm.getSessions ? sm.getSessions() : [];
  const currentId = sm.getCurrentSessionId ? sm.getCurrentSessionId() : null;
  const currentMeta = sessions.find(s => s.id === currentId);

  const url       = pending?.url       || currentMeta?.endpoint_url || '';
  const modelId   = pending?.modelId   || currentMeta?.model        || '';
  const endpointId= pending?.endpointId|| currentMeta?.endpoint_id  || '';

  // Close overlay first so the chat UI becomes visible
  closeProjectsView();

  // createDirectChat sets up the "new chat" UI exactly like the sidebar button
  sm.createDirectChat(url, modelId, endpointId);

  // Tag the pending chat with the project so materializePendingSession assigns it
  if (sm.setPendingChat && sm.getPendingChat) {
    const p = sm.getPendingChat();
    if (p) sm.setPendingChat({ ...p, projectId });
  }

  // Pre-fill composer if user typed something
  if (initialMessage) {
    setTimeout(() => {
      const c = document.getElementById('message') ||
                document.getElementById('message-input') ||
                document.querySelector('.chat-input textarea');
      if (c) { c.value = initialMessage; c.dispatchEvent(new Event('input', {bubbles:true})); c.focus(); }
    }, 200);
  }
}

// ── Sessions list ─────────────────────────────────────────────────────────── //
function _buildSessionsList(container, sessions, projectId, proj) {
  const wrap = document.createElement('div');

  if (!sessions.length) {
    const empty = document.createElement('div');
    empty.style.cssText = 'color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;padding:8px 0;';
    empty.textContent = T.noConversations;
    wrap.appendChild(empty);
    container.appendChild(wrap);
    return;
  }

  sessions.forEach(s => {
    // Use the same .list-item pattern as the sidebar session list
    const row = document.createElement('div');
    row.className = 'list-item session-item';

    // Session type icon — matches sidebar .session-icon convention
    const icon = document.createElement('span');
    icon.className = 'session-icon';
    icon.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
    row.appendChild(icon);

    // Session name — .grow matches sidebar convention
    const nameSpan = document.createElement('span');
    nameSpan.className = 'grow';
    const label = s.name || T.untitledConv;
    nameSpan.textContent = label;
    nameSpan.title = label + ' · ' + _relDate(s.last_accessed || s.updated_at);
    row.appendChild(nameSpan);

    // Context-menu button — hidden until hover (matches sidebar .session-menu-btn opacity pattern)
    const moreBtn = document.createElement('button');
    moreBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>';
    moreBtn.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);cursor:pointer;padding:2px 4px;border-radius:4px;flex-shrink:0;opacity:0;transition:opacity 0.1s;min-height:0;height:auto;';
    row.appendChild(moreBtn);

    row.addEventListener('mouseenter', () => { moreBtn.style.opacity = '1'; });
    row.addEventListener('mouseleave', () => { moreBtn.style.opacity = '0'; });
    moreBtn.addEventListener('click', e => { e.stopPropagation(); _showSessionMenu(e, s, projectId, proj); });
    row.addEventListener('click', e => { if (moreBtn.contains(e.target)) return; closeProjectsView(); selectSession(s.id); });

    wrap.appendChild(row);
  });

  container.appendChild(wrap);
}

// ══════════════════════════════════════════════════════════════════════════════
// RIGHT-PANEL CARDS
// ══════════════════════════════════════════════════════════════════════════════

// ── Memory card ───────────────────────────────────────────────────────────── //
function _buildMemoryCard(panel, proj, initialMems) {
  const { card, body: cardBody } = _sideCard(T.memory, () => _openMemoryEditor(proj, card));

  // "Only you" badge
  const badge = document.createElement('div');
  badge.style.cssText = 'display:inline-flex;align-items:center;gap:4px;font-size:10px;color:color-mix(in srgb, var(--fg) 55%, transparent);background:var(--bg);border:1px solid var(--border);border-radius:20px;padding:2px 8px;margin-bottom:10px;';
  badge.innerHTML = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> ${T.onlyYou}`;
  cardBody.appendChild(badge);

  const preview = document.createElement('div');
  preview.style.cssText = 'font-size:12px;color:color-mix(in srgb, var(--fg) 55%, transparent);line-height:1.55;';

  const meta = document.createElement('div');
  meta.style.cssText = 'font-size:10px;color:color-mix(in srgb, var(--fg) 55%, transparent);margin-top:6px;opacity:0.6;';

  const _render = (mems) => {
    if (!mems || !mems.length) {
      preview.textContent = T.noMemory;
      meta.textContent = '';
    } else {
      const m = mems[0];
      const text = m.content || '';
      preview.textContent = text.substring(0, 180) + (text.length > 180 ? '…' : '');
      meta.textContent = T.lastUpdated + ' ' + _relDate(m.synthesized_at);
    }
  };
  _render(initialMems);
  cardBody.appendChild(preview);
  cardBody.appendChild(meta);
  panel.appendChild(card);
}

function _openMemoryEditor(proj, card) {
  // Replace the card content temporarily with the memory editor
  const overlay = _makeDialogOverlay();
  const dialog = _makeDialogBox('min(580px,92vw)', '80vh');
  dialog.style.cssText += ';display:flex;flex-direction:column;';

  const hdr = _dialogHdr(T.memory, () => overlay.remove());
  dialog.appendChild(hdr);

  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:8px;margin-bottom:12px;';

  const synthBtn = document.createElement('button');
  synthBtn.style.cssText = 'background:var(--accent-primary, var(--red));color:#fff;border:none;border-radius:7px;padding:7px 14px;font-size:12px;cursor:pointer;font-weight:600;';
  synthBtn.textContent = T.synthesize;

  actions.appendChild(synthBtn);
  dialog.appendChild(actions);

  const memList = document.createElement('div');
  memList.style.cssText = 'flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:10px;min-height:0;';
  dialog.appendChild(memList);

  const statusEl = document.createElement('div');
  statusEl.style.cssText = 'font-size:11px;color:var(--accent-primary, var(--red));min-height:16px;margin-top:6px;';
  dialog.appendChild(statusEl);

  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  const loadMems = async () => {
    memList.innerHTML = '<div style="color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:12px;padding:4px;">Loading…</div>';
    const mems = await _fetchProjectMemories(proj.id);
    memList.innerHTML = '';
    if (!mems.length) {
      memList.innerHTML = `<div style="color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:12px;">${T.noMemory}</div>`;
      return;
    }
    mems.forEach(m => {
      const card2 = document.createElement('div');
      card2.style.cssText = 'background:var(--panel);border-radius:8px;padding:12px;';
      const header = document.createElement('div');
      header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';
      const dt = document.createElement('div');
      dt.style.cssText = 'font-size:10px;color:color-mix(in srgb, var(--fg) 55%, transparent);';
      dt.textContent = _relDate(m.synthesized_at) + (m.session_count ? ` · ${m.session_count} session(s)` : '');
      header.appendChild(dt);
      card2.appendChild(header);
      const content = document.createElement('div');
      content.style.cssText = 'font-size:12px;color:var(--fg);white-space:pre-wrap;line-height:1.55;';
      content.textContent = m.content;
      card2.appendChild(content);
      memList.appendChild(card2);
    });
  };

  loadMems();

  synthBtn.addEventListener('click', async () => {
    synthBtn.disabled = true;
    synthBtn.textContent = T.synthesizing;
    statusEl.textContent = '';
    try {
      await _api('POST', `/api/projects/${proj.id}/synthesize`);
      await loadMems();
      statusEl.textContent = 'Synthesized successfully.';
    } catch {
      statusEl.textContent = T.synthFailed;
    } finally {
      synthBtn.disabled = false;
      synthBtn.textContent = T.synthesize;
    }
  });
}

// ── Instructions card ─────────────────────────────────────────────────────── //
function _buildInstructionsCard(panel, proj) {
  const { card, body: cardBody } = _sideCard(T.instructions, () => _toggleInstrEdit());

  const preview = document.createElement('div');
  preview.style.cssText = 'font-size:12px;color:color-mix(in srgb, var(--fg) 55%, transparent);line-height:1.55;max-height:90px;overflow:hidden;';

  const ta = document.createElement('textarea');
  ta.rows = 6;
  ta.placeholder = T.instrPlaceholder;
  ta.value = proj.instructions || '';
  ta.style.cssText = 'display:none;width:100%;box-sizing:border-box;background:var(--bg);color:var(--fg);border:1px solid var(--accent-primary, var(--red));border-radius:8px;padding:8px 10px;font-size:12px;font-family:inherit;resize:vertical;outline:none;line-height:1.5;';

  const saveBtn = document.createElement('button');
  saveBtn.textContent = T.save;
  saveBtn.style.cssText = 'display:none;margin-top:8px;background:var(--accent-primary, var(--red));color:#fff;border:none;border-radius:7px;padding:6px 16px;cursor:pointer;font-size:12px;font-weight:600;';

  const _setPreview = () => {
    const txt = proj.instructions;
    preview.textContent = txt ? txt.substring(0, 160) + (txt.length > 160 ? '…' : '') : T.noInstructions;
    preview.style.color = txt ? 'color-mix(in srgb, var(--fg) 55%, transparent)' : 'color-mix(in srgb, var(--fg) 55%, transparent)';
    preview.style.fontStyle = txt ? '' : 'italic';
  };
  _setPreview();

  let editing = false;
  const _toggleInstrEdit = () => {
    editing = !editing;
    preview.style.display = editing ? 'none' : '';
    ta.style.display = editing ? '' : 'none';
    saveBtn.style.display = editing ? '' : 'none';
    if (editing) { ta.style.removeProperty('display'); setTimeout(() => ta.focus(), 30); }
  };

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = T.saving;
    const fd = new FormData();
    fd.append('instructions', ta.value);
    try {
      const updated = await _api('PATCH', `/api/projects/${proj.id}`, fd);
      proj.instructions = updated.instructions || '';
      _setPreview();
      editing = false;
      preview.style.display = '';
      ta.style.display = 'none';
      saveBtn.style.display = 'none';
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = T.save;
    }
  });

  cardBody.appendChild(preview);
  cardBody.appendChild(ta);
  cardBody.appendChild(saveBtn);
  panel.appendChild(card);
}

// ── Files card ────────────────────────────────────────────────────────────── //
function _buildFilesCard(panel, projectId, initialDocs) {
  const { card, body: cardBody } = _sideCard(T.files, () => _openLibraryForProject(projectId, renderDocs));

  // Replace the edit pencil icon with a "+" button
  const addBtn = card.querySelector('button');
  if (addBtn) {
    addBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
    addBtn.title = T.addFile;
  }

  const fileList = document.createElement('div');
  cardBody.appendChild(fileList);

  const renderDocs = docs => {
    fileList.innerHTML = '';
    if (!docs.length) {
      // Empty state with dashed border + illustration (matches Claude.ai)
      const empty = document.createElement('div');
      empty.style.cssText = 'border:1.5px dashed var(--border);border-radius:10px;padding:22px 14px;text-align:center;margin-top:4px;';
      empty.innerHTML = `
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" style="opacity:0.28;margin:0 auto 10px;display:block;color:color-mix(in srgb, var(--fg) 55%, transparent);">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="12" y1="12" x2="12" y2="18"/>
          <line x1="9" y1="15" x2="15" y2="15"/>
        </svg>
        <div style="font-size:11px;color:color-mix(in srgb, var(--fg) 55%, transparent);line-height:1.55;">${T.addFilesHint}</div>`;
      fileList.appendChild(empty);
      return;
    }
    docs.forEach(d => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:7px;padding:6px 2px;border-bottom:1px solid color-mix(in srgb,var(--border) 28%,transparent);';
      row.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="flex-shrink:0;opacity:0.45;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
      const nm = document.createElement('span');
      nm.textContent = d.title || d.id;
      nm.style.cssText = 'flex:1;font-size:11px;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      const rm = document.createElement('button');
      rm.textContent = '×';
      rm.title = T.remove;
      rm.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);cursor:pointer;font-size:15px;padding:0 3px;line-height:1;opacity:0.6;';
      rm.addEventListener('click', async () => {
        await _api('DELETE', `/api/projects/${projectId}/documents/${d.id}`);
        renderDocs(await _fetchProjectDocuments(projectId));
      });
      row.appendChild(nm);
      row.appendChild(rm);
      fileList.appendChild(row);
    });
  };

  renderDocs(initialDocs);
  panel.appendChild(card);
}

// ── Project Files modal — Document Library with pin checkboxes ──────────── //
// A self-contained modal that replicates the Document Library UI but adds
// per-row checkboxes to pin/unpin docs to the project. Does NOT open or
// call the existing library modal — it is a completely separate overlay.
async function _openLibraryForProject(projectId, renderFn) {
  if (document.getElementById('pv-files-modal')) return;

  const overlay = document.createElement('div');
  overlay.id = 'pv-files-modal';
  overlay.style.cssText = 'position:fixed;inset:0;z-index:2100;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;';

  const panel = document.createElement('div');
  panel.style.cssText = [
    'background:var(--bg)',
    'border:1px solid var(--border)',
    'border-radius:12px',
    'width:min(720px,92vw)',
    'height:80vh',
    'display:flex',
    'flex-direction:column',
    'box-shadow:0 8px 40px rgba(0,0,0,0.5)',
    'overflow:hidden',
  ].join(';');

  // ── Header ───────────────────────────────────────────────────────────────
  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--border);flex-shrink:0;';

  const hdrTitle = document.createElement('h3');
  hdrTitle.textContent = 'Project Files';
  hdrTitle.style.cssText = 'margin:0;font-size:15px;font-weight:700;';

  const hdrRight = document.createElement('div');
  hdrRight.style.cssText = 'display:flex;align-items:center;gap:8px;';

  const closeBtn = document.createElement('button');
  closeBtn.textContent = '×';
  closeBtn.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:22px;cursor:pointer;padding:0 4px;line-height:1;';
  closeBtn.addEventListener('click', () => { overlay.remove(); renderFn && _fetchProjectDocuments(projectId).then(renderFn); });

  hdrRight.appendChild(closeBtn);
  hdr.appendChild(hdrTitle);
  hdr.appendChild(hdrRight);
  panel.appendChild(hdr);

  // ── Toolbar: sort + search + new + import ────────────────────────────────
  const toolbar = document.createElement('div');
  toolbar.style.cssText = 'display:flex;align-items:center;gap:8px;padding:12px 20px;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent);flex-shrink:0;';

  const sortSel = document.createElement('select');
  sortSel.className = 'memory-sort-select';
  sortSel.innerHTML = '<option value="recent">Recent</option><option value="oldest">Oldest</option><option value="alpha">A–Z</option>';
  sortSel.style.cssText = 'font-size:12px;background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:5px 8px;cursor:pointer;';

  const searchInp = document.createElement('input');
  searchInp.type = 'text';
  searchInp.placeholder = 'Search documents…';
  searchInp.style.cssText = 'flex:1;background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:13px;outline:none;';

  const newDocBtn = document.createElement('button');
  newDocBtn.textContent = '+ New';
  newDocBtn.style.cssText = 'background:var(--panel);border:1px solid var(--border);color:var(--fg);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;font-weight:600;';
  newDocBtn.addEventListener('click', async () => {
    if (window.documentModule && window.documentModule.newDocument) {
      overlay.remove();
      await window.documentModule.newDocument();
      // Reopen after user creates
      setTimeout(() => _openLibraryForProject(projectId, renderFn), 400);
    }
  });

  const importBtn = document.createElement('button');
  importBtn.textContent = '↑ Import';
  importBtn.style.cssText = 'background:var(--panel);border:1px solid var(--border);color:var(--fg);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;white-space:nowrap;font-weight:600;';
  importBtn.addEventListener('click', async () => {
    // Open a hidden file input
    const fi = document.createElement('input');
    fi.type = 'file';
    fi.accept = '.txt,.md,.pdf,.docx,.csv,.json,.html,.xml';
    fi.multiple = true;
    fi.style.display = 'none';
    document.body.appendChild(fi);
    fi.click();
    fi.addEventListener('change', async () => {
      if (!fi.files.length) { fi.remove(); return; }
      const uploadStatus = document.createElement('div');
      uploadStatus.style.cssText = 'position:absolute;bottom:16px;left:50%;transform:translateX(-50%);background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:12px;color:var(--fg);z-index:10;';
      uploadStatus.textContent = `Uploading ${fi.files.length} file(s)…`;
      panel.style.position = 'relative';
      panel.appendChild(uploadStatus);
      for (const file of fi.files) {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('title', file.name);
        await fetch(`${API_BASE}/api/documents`, { method: 'POST', body: fd }).catch(() => {});
      }
      uploadStatus.remove();
      fi.remove();
      await _reloadDocs();
    });
  });

  toolbar.appendChild(sortSel);
  toolbar.appendChild(searchInp);
  toolbar.appendChild(newDocBtn);
  toolbar.appendChild(importBtn);
  panel.appendChild(toolbar);

  // ── Hint ─────────────────────────────────────────────────────────────────
  const hint = document.createElement('div');
  hint.style.cssText = 'padding:8px 20px 0;font-size:11px;color:color-mix(in srgb, var(--fg) 55%, transparent);flex-shrink:0;';
  hint.textContent = 'Check a document to pin it to this project. Pinned documents are injected as context into every conversation.';
  panel.appendChild(hint);

  // ── Document list ─────────────────────────────────────────────────────────
  const listWrap = document.createElement('div');
  listWrap.style.cssText = 'flex:1;overflow-y:auto;padding:8px 0;';
  panel.appendChild(listWrap);

  overlay.addEventListener('click', e => { if (e.target === overlay) { overlay.remove(); renderFn && _fetchProjectDocuments(projectId).then(renderFn); } });
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  let allDocs = [], pinnedIds = new Set(), _sort = 'recent', _search = '';

  const _reloadDocs = async () => {
    listWrap.innerHTML = '<div style="padding:20px;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;">Loading…</div>';
    const [docs, pinned] = await Promise.all([_fetchUserDocuments(_search), _fetchProjectDocuments(projectId)]);
    allDocs = docs;
    pinnedIds = new Set(pinned.map(d => d.id));
    _renderList();
  };

  const _renderList = () => {
    listWrap.innerHTML = '';
    let filtered = allDocs.filter(d =>
      !_search || (d.title || '').toLowerCase().includes(_search.toLowerCase())
    );
    if (_sort === 'alpha') filtered.sort((a, b) => (a.title||'').localeCompare(b.title||''));
    else if (_sort === 'oldest') filtered.reverse();

    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.style.cssText = 'padding:32px 20px;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;text-align:center;';
      empty.textContent = _search ? 'No documents found.' : 'No documents in your library yet.';
      listWrap.appendChild(empty);
      return;
    }

    filtered.forEach(d => {
      const row = document.createElement('div');
      const pinned = pinnedIds.has(d.id);
      row.style.cssText = [
        'display:flex', 'align-items:center', 'gap:12px',
        'padding:10px 20px', 'cursor:pointer', 'transition:background 0.1s',
        'border-bottom:1px solid color-mix(in srgb,var(--border) 30%,transparent)',
        pinned ? 'background:color-mix(in srgb,var(--accent-primary, var(--red)) 8%,transparent)' : '',
      ].join(';');

      row.addEventListener('mouseenter', () => { if (!pinnedIds.has(d.id)) row.style.background = 'var(--panel)'; });
      row.addEventListener('mouseleave', () => { row.style.background = pinnedIds.has(d.id) ? 'color-mix(in srgb,var(--accent-primary, var(--red)) 8%,transparent)' : ''; });

      // Checkbox
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = pinned;
      chk.style.cssText = 'width:15px;height:15px;flex-shrink:0;cursor:pointer;accent-color:var(--accent-primary, var(--red));';

      // Doc icon — SVG that matches the Documents library style
      const icon = document.createElement('div');
      icon.style.cssText = 'flex-shrink:0;width:32px;height:38px;display:flex;flex-direction:column;align-items:center;justify-content:center;position:relative;';
      const rawExt = (d.title || '').includes('.') ? (d.title || '').split('.').pop().toLowerCase() : (d.language || '');
      const extColors = { pdf: '#e05252', md: '#5b8abf', csv: '#52c052', docx: '#5b7abf', txt: '#aaa', json: '#e8b84b', html: '#e87d4b', xml: '#b84be8' };
      const extColor = extColors[rawExt] || '#888';
      icon.innerHTML = `
        <svg width="28" height="34" viewBox="0 0 28 34" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="1" y="1" width="26" height="32" rx="3" fill="var(--panel)" stroke="var(--border,#555)" stroke-width="1.2"/>
          <path d="M17 1v7h9" fill="none" stroke="var(--border,#555)" stroke-width="1.2" stroke-linecap="round"/>
          <path d="M17 1l9 7" fill="${extColor}" opacity="0.25"/>
          <rect x="5" y="14" width="14" height="1.5" rx="0.75" fill="color-mix(in srgb, var(--fg) 55%, transparent)" opacity="0.6"/>
          <rect x="5" y="18" width="18" height="1.5" rx="0.75" fill="color-mix(in srgb, var(--fg) 55%, transparent)" opacity="0.4"/>
          <rect x="5" y="22" width="11" height="1.5" rx="0.75" fill="color-mix(in srgb, var(--fg) 55%, transparent)" opacity="0.3"/>
        </svg>
        <span style="position:absolute;bottom:1px;right:0;font-size:7px;font-weight:800;color:${extColor};text-transform:uppercase;line-height:1;">${rawExt || 'doc'}</span>
      `;

      // Info
      const info = document.createElement('div');
      info.style.cssText = 'flex:1;min-width:0;';
      const titleEl = document.createElement('div');
      titleEl.textContent = d.title || 'Untitled document';
      titleEl.style.cssText = 'font-size:13px;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500;';
      const meta = document.createElement('div');
      const metaParts = [];
      if (d.language && d.language !== 'text') metaParts.push(d.language);
      if (d.created_at) metaParts.push(_relDate(d.created_at));
      meta.textContent = metaParts.join(' · ') || 'document';
      meta.style.cssText = 'font-size:10px;color:color-mix(in srgb, var(--fg) 55%, transparent);margin-top:2px;';
      info.appendChild(titleEl);
      info.appendChild(meta);

      // Pin badge
      if (pinned) {
        const badge = document.createElement('span');
        badge.textContent = 'Pinned';
        badge.style.cssText = 'font-size:10px;color:var(--accent-primary, var(--red));border:1px solid var(--accent-primary, var(--red));border-radius:10px;padding:1px 7px;flex-shrink:0;';
        row.appendChild(chk);
        row.appendChild(icon);
        row.appendChild(info);
        row.appendChild(badge);
      } else {
        row.appendChild(chk);
        row.appendChild(icon);
        row.appendChild(info);
      }

      const _toggle = async () => {
        chk.disabled = true;
        if (pinnedIds.has(d.id)) {
          await _api('DELETE', `/api/projects/${projectId}/documents/${d.id}`);
          pinnedIds.delete(d.id);
        } else {
          await _api('POST', `/api/projects/${projectId}/documents/${d.id}`);
          pinnedIds.add(d.id);
        }
        chk.disabled = false;
        _renderList();
        renderFn && _fetchProjectDocuments(projectId).then(renderFn);
      };

      row.addEventListener('click', e => { if (e.target !== chk) _toggle(); });
      chk.addEventListener('change', _toggle);
      listWrap.appendChild(row);
    });
  };

  sortSel.addEventListener('change', () => { _sort = sortSel.value; _renderList(); });
  let _debTimer;
  searchInp.addEventListener('input', () => {
    clearTimeout(_debTimer);
    _debTimer = setTimeout(async () => { _search = searchInp.value.trim(); await _reloadDocs(); }, 200);
  });

  await _reloadDocs();
}

// ── Document picker dialog ─────────────────────────────────────────────────── //
async function _showDocPicker(projectId, renderFn) {
  const overlay = _makeDialogOverlay();
  const dialog = _makeDialogBox('min(560px,92vw)', '78vh');
  dialog.style.cssText += ';display:flex;flex-direction:column;';

  const hdr = _dialogHdr(T.addDocument, () => overlay.remove());
  dialog.appendChild(hdr);

  const searchInp = _makeInput('text', T.searchDocuments);
  searchInp.style.marginBottom = '10px';
  dialog.appendChild(searchInp);

  const listEl = document.createElement('div');
  listEl.style.cssText = 'overflow-y:auto;flex:1;min-height:0;border:1px solid var(--border);border-radius:8px;';
  dialog.appendChild(listEl);

  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  searchInp.focus();

  let allDocs = [], pinnedIds = new Set();
  const [docs, pinned] = await Promise.all([_fetchUserDocuments(), _fetchProjectDocuments(projectId)]);
  allDocs = docs;
  pinnedIds = new Set(pinned.map(d => d.id));

  const renderList = items => {
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = `<div style="padding:16px;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:13px;">${T.noDocuments}</div>`;
      return;
    }
    items.forEach(d => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;border-bottom:1px solid color-mix(in srgb,var(--border) 30%,transparent);transition:background 0.1s;';
      row.addEventListener('mouseenter', () => { row.style.background = 'var(--panel)'; });
      row.addEventListener('mouseleave', () => { row.style.background = ''; });
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.checked = pinnedIds.has(d.id);
      chk.style.cssText = 'width:14px;height:14px;flex-shrink:0;cursor:pointer;accent-color:var(--accent-primary, var(--red));';
      const info = document.createElement('div');
      info.style.cssText = 'flex:1;min-width:0;';
      const nm = document.createElement('div');
      nm.textContent = d.title || d.id;
      nm.style.cssText = 'font-size:13px;color:var(--fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      const sub = document.createElement('div');
      sub.textContent = (d.language || 'text') + (d.session_name ? ' · ' + d.session_name : '');
      sub.style.cssText = 'font-size:10px;color:color-mix(in srgb, var(--fg) 55%, transparent);margin-top:1px;';
      info.appendChild(nm);
      info.appendChild(sub);
      row.appendChild(chk);
      row.appendChild(info);
      row.addEventListener('click', async () => {
        if (pinnedIds.has(d.id)) {
          await _api('DELETE', `/api/projects/${projectId}/documents/${d.id}`);
          pinnedIds.delete(d.id);
          chk.checked = false;
        } else {
          await _api('POST', `/api/projects/${projectId}/documents/${d.id}`);
          pinnedIds.add(d.id);
          chk.checked = true;
        }
        renderFn(await _fetchProjectDocuments(projectId));
      });
      listEl.appendChild(row);
    });
  };

  renderList(allDocs);

  let dbTimer;
  searchInp.addEventListener('input', () => {
    clearTimeout(dbTimer);
    dbTimer = setTimeout(async () => {
      allDocs = await _fetchUserDocuments(searchInp.value.trim());
      renderList(allDocs);
    }, 200);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// CONTEXT MENUS
// ══════════════════════════════════════════════════════════════════════════════

function _showSessionMenu(e, session, projectId, proj) {
  _dismissMenu('pv-sess-menu');
  const menu = _makeMenu('pv-sess-menu');
  _menuItem(menu, T.renameConv, () => {
    menu.remove();
    _renameDialog(T.renameConvTitle, session.name || '', async n => {
      const fd = new FormData(); fd.append('name', n);
      await _api('PATCH', `/api/sessions/${session.id}`, fd);
      loadSessions().catch(() => {});
      openProjectView(projectId);
    });
  });
  _menuItem(menu, T.removeFromProject, async () => {
    menu.remove();
    await _api('DELETE', `/api/projects/${projectId}/sessions/${session.id}`);
    loadSessions().catch(() => {});
    loadProjects().catch(() => {});
    openProjectView(projectId);
  });
  _positionMenu(menu, e);
}

function _showProjectMenu(e, proj) {
  _dismissMenu('pv-proj-menu');
  const menu = _makeMenu('pv-proj-menu');
  _menuItem(menu, T.renameProject, () => {
    menu.remove();
    _renameDialog(T.renameProject + ':', proj.name, async n => {
      const fd = new FormData(); fd.append('name', n);
      const updated = await _api('PATCH', `/api/projects/${proj.id}`, fd);
      proj.name = updated.name;
      const el = document.getElementById('pv-proj-title');
      if (el) el.textContent = proj.name;
      _projectsCache = await _fetchProjects();
      loadProjects().catch(() => {});
    });
  });
  const del = _menuItem(menu, T.archive, async () => {
    menu.remove();
    if (!confirm(T.archiveConfirm(proj.name))) return;
    await _api('DELETE', `/api/projects/${proj.id}`);
    _projectsCache = await _fetchProjects();
    loadProjects().catch(() => {});
    openProjectsListView();
  });
  del.style.color = 'var(--danger, var(--red))';
  _positionMenu(menu, e);
}

// ══════════════════════════════════════════════════════════════════════════════
// CREATE PROJECT DIALOG
// ══════════════════════════════════════════════════════════════════════════════
function _showCreateDialog() {
  const overlay = _makeDialogOverlay();
  const dialog = _makeDialogBox();

  const hdr = _dialogHdr(T.newProject, () => overlay.remove());
  dialog.appendChild(hdr);

  const nameInp = _makeInput('text', T.projectName);
  const descInp = _makeInput('text', T.description);
  descInp.style.marginBottom = '16px';
  dialog.appendChild(nameInp);
  dialog.appendChild(descInp);

  const btnRow = _makeBtnRow();
  const cancelBtn = _makeBtn(T.cancel, false);
  cancelBtn.addEventListener('click', () => overlay.remove());
  const createBtn = _makeBtn(T.create, true);
  createBtn.addEventListener('click', async () => {
    const name = nameInp.value.trim();
    if (!name) { nameInp.focus(); return; }
    createBtn.disabled = true;
    createBtn.textContent = T.creating;
    try {
      const fd = new FormData();
      fd.append('name', name);
      fd.append('description', descInp.value.trim());
      const proj = await _api('POST', '/api/projects', fd);
      overlay.remove();
      _projectsCache = await _fetchProjects();
      loadProjects().catch(() => {});
      openProjectView(proj.id);
    } catch {
      createBtn.disabled = false;
      createBtn.textContent = T.create;
    }
  });
  [nameInp, descInp].forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') createBtn.click(); }));
  btnRow.appendChild(cancelBtn);
  btnRow.appendChild(createBtn);
  dialog.appendChild(btnRow);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  setTimeout(() => nameInp.focus(), 50);
}

// ══════════════════════════════════════════════════════════════════════════════
// RENAME DIALOG
// ══════════════════════════════════════════════════════════════════════════════
function _renameDialog(label, currentValue, onSave) {
  const overlay = _makeDialogOverlay();
  const dialog = _makeDialogBox();
  const lbl = document.createElement('div');
  lbl.textContent = label;
  lbl.style.cssText = 'font-size:13px;margin-bottom:10px;';
  const inp = _makeInput('text', '');
  inp.value = currentValue;
  inp.style.marginBottom = '14px';
  const btnRow = _makeBtnRow();
  const cancelB = _makeBtn(T.cancel, false);
  cancelB.addEventListener('click', () => overlay.remove());
  const saveB = _makeBtn(T.save, true);
  saveB.addEventListener('click', async () => {
    const v = inp.value.trim();
    if (!v) return;
    saveB.disabled = true; saveB.textContent = T.saving;
    try { await onSave(v); overlay.remove(); }
    catch { saveB.disabled = false; saveB.textContent = T.save; }
  });
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') saveB.click(); });
  btnRow.appendChild(cancelB);
  btnRow.appendChild(saveB);
  dialog.appendChild(lbl);
  dialog.appendChild(inp);
  dialog.appendChild(btnRow);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  setTimeout(() => { inp.focus(); inp.select(); }, 50);
}

// ══════════════════════════════════════════════════════════════════════════════
// UI PRIMITIVES
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Side panel card with title + edit button.
 * Returns { card, body } where body is where content should be appended.
 */
function _sideCard(title, onEdit) {
  const card = document.createElement('div');
  card.style.cssText = 'background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px;';

  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;';

  const ttl = document.createElement('div');
  ttl.textContent = title;
  ttl.style.cssText = 'font-weight:700;font-size:13px;color:var(--fg);';

  const editBtn = document.createElement('button');
  editBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>';
  editBtn.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);cursor:pointer;padding:2px 4px;border-radius:4px;display:flex;align-items:center;';
  if (onEdit) editBtn.addEventListener('click', onEdit);

  hdr.appendChild(ttl);
  hdr.appendChild(editBtn);
  card.appendChild(hdr);

  // body is a separate div so callers get a clean insertion point
  const body = document.createElement('div');
  card.appendChild(body);

  return { card, body, editBtn };
}

function _makeDialogOverlay() {
  const o = document.createElement('div');
  o.style.cssText = 'position:fixed;inset:0;z-index:2100;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;';
  o.addEventListener('click', e => { if (e.target === o) o.remove(); });
  return o;
}

function _makeDialogBox(w = 'min(400px,90vw)', h = 'auto') {
  const d = document.createElement('div');
  d.style.cssText = `background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:24px;width:${w};max-height:${h};box-shadow:0 8px 32px rgba(0,0,0,0.4);overflow:hidden;box-sizing:border-box;`;
  return d;
}

function _dialogHdr(title, onClose) {
  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;';
  const t = document.createElement('h3');
  t.textContent = title;
  t.style.cssText = 'margin:0;font-size:15px;font-weight:700;';
  const x = document.createElement('button');
  x.textContent = '×';
  x.style.cssText = 'background:none;border:none;color:color-mix(in srgb, var(--fg) 55%, transparent);font-size:20px;cursor:pointer;padding:0 4px;line-height:1;';
  x.addEventListener('click', onClose);
  hdr.appendChild(t);
  hdr.appendChild(x);
  return hdr;
}

function _makeInput(type, placeholder) {
  const i = document.createElement('input');
  i.type = type;
  i.placeholder = placeholder;
  i.style.cssText = 'width:100%;box-sizing:border-box;background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;font-size:14px;outline:none;margin-bottom:10px;display:block;';
  return i;
}

function _makeBtnRow() {
  const r = document.createElement('div');
  r.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
  return r;
}

function _makeBtn(label, primary) {
  const b = document.createElement('button');
  b.textContent = label;
  b.style.cssText = primary
    ? 'background:var(--fg);color:var(--bg);border:none;border-radius:8px;padding:8px 18px;font-size:13px;font-weight:700;cursor:pointer;'
    : 'background:none;border:1px solid var(--border);color:color-mix(in srgb, var(--fg) 55%, transparent);border-radius:8px;padding:8px 16px;cursor:pointer;font-size:13px;';
  return b;
}

function _makeMenu(id) {
  const m = document.createElement('div');
  m.id = id;
  m.className = 'dropdown';
  m.style.cssText = 'position:fixed;z-index:3000;display:block;min-width:170px;';
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', () => m.remove(), { once: true }), 10);
  return m;
}

function _menuItem(menu, label, action) {
  const el = document.createElement('div');
  el.className = 'dropdown-item-compact';
  el.textContent = label;
  el.addEventListener('click', action);
  menu.appendChild(el);
  return el;
}

function _positionMenu(menu, e) {
  const rect = e.currentTarget.getBoundingClientRect();
  let left = rect.left - 170 + rect.width;
  if (left < 8) left = 8;
  let top = rect.bottom + 2;
  if (top + 90 > window.innerHeight) top = rect.top - 90;
  menu.style.left = left + 'px';
  menu.style.top = top + 'px';
}

function _dismissMenu(id) { document.getElementById(id)?.remove(); }

// ── Keyboard + init ───────────────────────────────────────────────────────── //
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _activeView) closeProjectsView();
});

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('rail-projects')?.addEventListener('click', () => openProjectsListView());
  document.getElementById('sidebar-projects-header')?.addEventListener('click', () => openProjectsListView());
}, { once: true });

// ── Global DOM cleanup helper ─────────────────────────────────────────────── //
window._projectsViewClose = function () {
  const el = window._projectsOverlayEl || document.getElementById('projects-view-overlay');
  if (el && el.parentNode) el.parentNode.removeChild(el);
  window._projectsOverlayEl = null;
  document.getElementById('rail-projects')?.classList.remove('active');
  document.getElementById('sidebar-projects-header')?.classList.remove('active');
};

// ── Exports ───────────────────────────────────────────────────────────────── //
window.projectsViewModule = {
  openProjectsListView, openProjectView, closeProjectsView,
  isProjectsViewActive, getActiveProjectId, consumePendingProjectId,
};

export default {
  openProjectsListView, openProjectView, closeProjectsView,
  isProjectsViewActive, getActiveProjectId, consumePendingProjectId,
};

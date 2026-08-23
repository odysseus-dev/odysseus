// static/js/workspace.js
//
// Workspace picker: browse server directories in a draggable modal, choose a
// folder, and show it as a persistent workspace chip in the chat input bar.
// The server supplies a sensible default plus sibling projects so coding can
// start immediately without choosing the same folder for every conversation.
// chat request sends `workspace` so the agent's file/shell tools are confined
// to that folder (see routes/chat_routes.py + src/tool_execution.py).

import Storage, { KEYS } from './storage.js';
import uiModule from './ui.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
// Same folder glyph as the overflow menu item + pill (not an emoji).
const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
let _modal = null;
let _curPath = '';
let _projectCatalog = [];
let _defaultRoot = '';
const _expandedProjectChats = new Set();
const _projectStatusCache = new Map();
let _visibleProjectEntries = [];

export function getWorkspace() {
  return Storage.get(KEYS.WORKSPACE, '') || '';
}

function _basename(p) {
  if (!p) return '';
  // Handle both POSIX (/) and Windows (\) separators.
  const parts = p.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

function _pathKey(path) {
  return String(path || '').replace(/[\\/]+$/, '').toLocaleLowerCase();
}

function _parentPath(path) {
  const clean = String(path || '').replace(/[\\/]+$/, '');
  const idx = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
  return idx > 0 ? clean.slice(0, idx) : clean;
}

function _recentWorkspaces() {
  const saved = Storage.getJSON(KEYS.WORKSPACE_RECENTS, []);
  return Array.isArray(saved) ? saved.filter((path) => typeof path === 'string' && path) : [];
}

function _rememberWorkspace(path) {
  if (!path) return;
  const key = _pathKey(path);
  const next = [path, ..._recentWorkspaces().filter((item) => _pathKey(item) !== key)].slice(0, 8);
  Storage.setJSON(KEYS.WORKSPACE_RECENTS, next);
}

function _projectFolds() {
  const saved = Storage.getJSON(KEYS.WORKSPACE_FOLDS, {});
  return saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
}

function _setProjectFold(path, collapsed) {
  const folds = _projectFolds();
  folds[_pathKey(path)] = !!collapsed;
  Storage.setJSON(KEYS.WORKSPACE_FOLDS, folds);
}

function _setProjectCollapsed(wrapper, path, collapsed) {
  _setProjectFold(path, collapsed);
  const main = wrapper?.querySelector('.workspace-project-main');
  const reveal = wrapper?.querySelector('.workspace-project-chat-reveal');
  main?.setAttribute('aria-expanded', String(!collapsed));
  wrapper?.classList.toggle('is-collapsed', collapsed);
  reveal?.classList.toggle('is-collapsed', collapsed);
  reveal?.setAttribute('aria-hidden', String(collapsed));
  if (reveal) reveal.inert = collapsed;
}

function _projectSectionCollapsed() {
  return Storage.get(KEYS.WORKSPACE_SECTION_FOLDED, '0') === '1';
}

function _setProjectSectionCollapsed(collapsed) {
  Storage.set(KEYS.WORKSPACE_SECTION_FOLDED, collapsed ? '1' : '0');
  const section = document.getElementById('workspace-projects-section');
  const toggle = document.getElementById('projects-section-toggle');
  const reveal = document.getElementById('projects-section-reveal');
  section?.classList.toggle('projects-section-collapsed', collapsed);
  toggle?.setAttribute('aria-expanded', String(!collapsed));
  reveal?.setAttribute('aria-hidden', String(collapsed));
  if (reveal) reveal.inert = collapsed;
}

async function _projectStatus(path) {
  const key = _pathKey(path);
  const cached = _projectStatusCache.get(key);
  if (cached && Date.now() - cached.at < 30000) return cached.data;
  try {
    const response = await fetch(`${API_BASE}/api/workspace/status?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!response.ok) return null;
    const data = await response.json();
    _projectStatusCache.set(key, { at: Date.now(), data });
    return data;
  } catch (_) {
    return null;
  }
}

function _renderProjectPulse(container, status, activity) {
  if (!container) return;
  const pieces = [];
  const labels = [];
  if (status?.is_git && status.branch) {
    pieces.push(`<span class="project-pulse-branch" title="Branch: ${uiModule.esc(status.branch)}"><svg viewBox="0 0 24 24"><circle cx="6" cy="4" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="20" r="2"/><path d="M6 6v12M8 8c5 0 8 0 8-2"/></svg><span>${uiModule.esc(status.branch)}</span></span>`);
    labels.push(`branch ${status.branch}`);
  }
  if (status?.changed_files) {
    pieces.push(`<span class="project-pulse-changes" title="${status.changed_files} changed file${status.changed_files === 1 ? '' : 's'}">${status.changed_files}</span>`);
    labels.push(`${status.changed_files} changed files`);
  }
  if (activity?.running) {
    pieces.push(`<span class="project-pulse-state running" title="${activity.running} running agent${activity.running === 1 ? '' : 's'}"></span>`);
    labels.push(`${activity.running} running`);
  }
  if (activity?.ready) {
    pieces.push(`<span class="project-pulse-state ready" title="${activity.ready} result${activity.ready === 1 ? '' : 's'} ready"></span>`);
    labels.push(`${activity.ready} results ready`);
  }
  if (activity?.failed) {
    pieces.push(`<span class="project-pulse-state failed" title="${activity.failed} failed run${activity.failed === 1 ? '' : 's'}"></span>`);
    labels.push(`${activity.failed} failed`);
  }
  if (activity?.approvals) {
    pieces.push(`<span class="project-pulse-approval" title="${activity.approvals} pending approval${activity.approvals === 1 ? '' : 's'}">!</span>`);
    labels.push(`${activity.approvals} approvals`);
  }
  container.innerHTML = pieces.join('');
  container.setAttribute('aria-label', labels.join(', '));
  container.hidden = pieces.length === 0;
}

function _startProjectChat(project) {
  setWorkspace(project.path);
  _setProjectFold(project.path, false);
  document.getElementById('sidebar-new-chat-btn')?.click();
}

function _closeProjectMenu() {
  document.querySelectorAll('.project-context-menu, .chat-context-menu').forEach((menu) => menu.remove());
}

function _openChatMenu(anchor, session) {
  _closeProjectMenu();
  const menu = document.createElement('div');
  menu.className = 'project-context-menu chat-context-menu';
  menu.setAttribute('role', 'menu');
  const action = (label, svg, callback, { danger = false } = {}) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `project-context-action${danger ? ' is-danger' : ''}`;
    button.innerHTML = `${svg}<span>${uiModule.esc(label)}</span>`;
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      _closeProjectMenu();
      await callback();
    });
    menu.appendChild(button);
  };
  const sessionApi = window.sessionModule;
  action('Rename', '<svg viewBox="0 0 24 24"><path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>', () => sessionApi?.renameSession?.(session.id));
  action(session.is_important ? 'Unpin chat' : 'Pin chat', '<svg viewBox="0 0 24 24"><path d="M12 17v5M5 3h14l-3 7 3 4H5l3-4Z"/></svg>', () => sessionApi?.setSessionImportant?.(session.id, !session.is_important));
  if (sessionApi?.getSessionActivityState?.(session.id) === 'ready') {
    action('Mark as read', '<svg viewBox="0 0 24 24"><path d="m4 12 5 5L20 6"/></svg>', () => sessionApi.clearStreamComplete(session.id));
  }
  action('Copy chat', '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>', () => sessionApi?.copySession?.(session.id));
  const divider = document.createElement('div');
  divider.className = 'project-context-divider';
  menu.appendChild(divider);
  action('Archive', '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/></svg>', () => sessionApi?.archiveSession?.(session.id));
  action('Delete', '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v5M14 11v5"/></svg>', () => sessionApi?.deleteSession?.(session.id), { danger: true });
  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  const width = 220;
  menu.style.left = `${Math.min(window.innerWidth - width - 10, rect.right - width)}px`;
  menu.style.top = '-9999px';
  requestAnimationFrame(() => {
    const below = rect.bottom + 5;
    menu.style.top = `${below + menu.offsetHeight > window.innerHeight - 10 ? Math.max(10, rect.top - menu.offsetHeight - 5) : below}px`;
  });
  setTimeout(() => document.addEventListener('click', _closeProjectMenu, { once: true }), 0);
}

function _openProjectMenu(anchor, project, collapsed) {
  _closeProjectMenu();
  const menu = document.createElement('div');
  menu.className = 'project-context-menu';
  menu.setAttribute('role', 'menu');
  const addAction = (label, svg, action) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'project-context-action';
    button.innerHTML = `${svg}<span>${uiModule.esc(label)}</span>`;
    button.addEventListener('click', async (event) => {
      event.stopPropagation();
      _closeProjectMenu();
      await action();
    });
    menu.appendChild(button);
  };
  addAction('New chat in project', '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>', () => _startProjectChat(project));
  addAction('Use as workspace', '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>', () => {
    setWorkspace(project.path);
    if (uiModule?.showToast) uiModule.showToast(`Workspace: ${project.name}`);
  });
  addAction(collapsed ? 'Expand chats' : 'Collapse chats', '<svg viewBox="0 0 24 24"><path d="m8 10 4 4 4-4"/></svg>', () => {
    _setProjectCollapsed(anchor.closest('.workspace-project-group'), project.path, !collapsed);
  });
  addAction('Copy workspace path', '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>', async () => {
    try {
      await navigator.clipboard.writeText(project.path);
      if (uiModule?.showToast) uiModule.showToast('Workspace path copied');
    } catch (_) {
      if (uiModule?.showError) uiModule.showError('Could not copy workspace path');
    }
  });
  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  const width = 232;
  menu.style.left = `${Math.min(window.innerWidth - width - 10, rect.right - width)}px`;
  menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 10, rect.bottom + 5)}px`;
  setTimeout(() => document.addEventListener('click', _closeProjectMenu, { once: true }), 0);
}

function _renderProjects() {
  const list = document.getElementById('workspace-project-list');
  if (!list) return;
  const current = getWorkspace();
  const sessions = window.sessionModule?.getSessions?.() || [];
  let inferredWorkspace = false;
  if (_defaultRoot) {
    sessions.forEach((session) => {
      if (!session.workspace && session.folder !== 'Assistant' && session.folder !== 'Tasks') {
        session.workspace = _defaultRoot;
        session.workspace_inferred = true;
        inferredWorkspace = true;
      }
    });
  }

  const combined = [];
  const seen = new Set();
  const add = (path, name, root = false) => {
    const key = _pathKey(path);
    if (!path || seen.has(key)) return;
    seen.add(key);
    combined.push({ path, name: name || _basename(path), root });
  };
  add(_defaultRoot, _basename(_defaultRoot), true);
  _recentWorkspaces().forEach((path) => add(path, _basename(path)));
  sessions.forEach((session) => add(session.workspace, _basename(session.workspace)));
  _projectCatalog.forEach((project) => add(project.path, project.name));

  list.replaceChildren();
  const activeSessionId = window.sessionModule?.getCurrentSessionId?.() || '';
  const folds = _projectFolds();
  const visibleProjects = combined.map((project) => {
    const key = _pathKey(project.path);
    const projectSessions = sessions
      .filter((session) => !session.archived && _pathKey(session.workspace) === key && session.folder !== 'Assistant' && session.folder !== 'Tasks')
      .sort((a, b) => {
        if (!!a.is_important !== !!b.is_important) return a.is_important ? -1 : 1;
        return String(b.last_message_at || b.updated_at || b.created_at || '').localeCompare(String(a.last_message_at || a.updated_at || a.created_at || ''));
      });
    return { project, key, projectSessions };
  }).filter(({ projectSessions }) => projectSessions.length > 0);
  _visibleProjectEntries = visibleProjects.map(({ project, projectSessions }) => ({ ...project, sessionCount: projectSessions.length }));

  const section = document.getElementById('workspace-projects-section');
  if (section) section.hidden = visibleProjects.length === 0;
  if (!visibleProjects.length) {
    if (inferredWorkspace) window.sessionModule?.renderSessionList?.();
    return;
  }

  visibleProjects.forEach(({ project, key, projectSessions }) => {
    const collapsed = folds[key] === true;
    const wrapper = document.createElement('div');
    wrapper.className = `workspace-project-group${key === _pathKey(current) ? ' current-workspace' : ''}${project.root ? ' root-workspace' : ''}${collapsed ? ' is-collapsed' : ''}`;

    const row = document.createElement('div');
    row.className = 'workspace-project-row';
    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'workspace-project-main';
    main.title = project.path;
    main.setAttribute('aria-expanded', String(!collapsed));
    main.innerHTML = `<svg class="workspace-project-chevron" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>${_FOLDER_SVG}<span class="workspace-project-name">${uiModule.esc(project.name)}</span><span class="workspace-project-pulse" hidden></span>`;
    main.addEventListener('click', () => {
      const isCollapsed = main.getAttribute('aria-expanded') !== 'true';
      _setProjectCollapsed(wrapper, project.path, !isCollapsed);
    });
    row.appendChild(main);

    const actions = document.createElement('div');
    actions.className = 'workspace-project-actions';
    const newChat = document.createElement('button');
    newChat.type = 'button';
    newChat.className = 'workspace-project-action workspace-project-new-chat';
    newChat.title = `New chat in ${project.name}`;
    newChat.setAttribute('aria-label', `New chat in ${project.name}`);
    newChat.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>';
    newChat.addEventListener('click', (event) => { event.stopPropagation(); _startProjectChat(project); });
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'workspace-project-action workspace-project-more';
    more.title = `${project.name} actions`;
    more.setAttribute('aria-label', `${project.name} actions`);
    more.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';
    more.addEventListener('click', (event) => {
      event.stopPropagation();
      _openProjectMenu(more, project, main.getAttribute('aria-expanded') !== 'true');
    });
    actions.append(newChat, more);
    row.appendChild(actions);
    wrapper.appendChild(row);

    const pulse = main.querySelector('.workspace-project-pulse');
    const activity = window.odysseusActivity?.getProjectActivity?.(project.path, sessions) || {};
    _renderProjectPulse(pulse, _projectStatusCache.get(key)?.data || null, activity);
    _projectStatus(project.path).then((status) => {
      if (!wrapper.isConnected) return;
      _renderProjectPulse(pulse, status, window.odysseusActivity?.getProjectActivity?.(project.path, window.sessionModule?.getSessions?.() || sessions) || {});
    });

    const reveal = document.createElement('div');
    reveal.className = `workspace-project-chat-reveal${collapsed ? ' is-collapsed' : ''}`;
    reveal.setAttribute('aria-hidden', String(collapsed));
    reveal.inert = collapsed;
    const chats = document.createElement('div');
    chats.className = 'workspace-project-chats';
    const showAll = _expandedProjectChats.has(key);
    const visible = showAll ? projectSessions : projectSessions.slice(0, 5);
    visible.forEach((session) => {
      const chatRow = document.createElement('div');
      chatRow.className = 'workspace-project-chat-row';
      const chat = document.createElement('button');
      chat.type = 'button';
      chat.className = `workspace-project-chat${session.id === activeSessionId ? ' active-session' : ''}`;
      chat.dataset.sessionId = session.id;
      const chatState = window.sessionModule?.getSessionActivityState?.(session.id) || '';
      chat.innerHTML = `<span class="workspace-project-chat-label">${uiModule.esc(session.name || 'Untitled chat')}</span>${chatState ? `<span class="workspace-chat-state ${chatState}" aria-label="${chatState === 'running' ? 'Agent running' : 'Result ready'}"></span>` : ''}`;
      chat.title = `${session.name || 'Untitled chat'}${chatState === 'running' ? ' — agent running' : chatState === 'ready' ? ' — result ready' : ''}`;
      chat.addEventListener('click', async () => {
        setWorkspace(project.path);
        await window.sessionModule?.selectSession?.(session.id);
        _renderProjects();
      });
      const chatMore = document.createElement('button');
      chatMore.type = 'button';
      chatMore.className = 'workspace-project-chat-more';
      chatMore.title = `${session.name || 'Untitled chat'} actions`;
      chatMore.setAttribute('aria-label', `${session.name || 'Untitled chat'} actions`);
      chatMore.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>';
      chatMore.addEventListener('click', (event) => {
        event.stopPropagation();
        _openChatMenu(chatMore, session);
      });
      chatRow.append(chat, chatMore);
      chats.appendChild(chatRow);
    });
    if (projectSessions.length > 5) {
      const showMore = document.createElement('button');
      showMore.type = 'button';
      showMore.className = 'workspace-project-show-more';
      showMore.textContent = showAll ? 'Show less' : `Show ${projectSessions.length - 5} more`;
      showMore.addEventListener('click', () => {
        if (showAll) _expandedProjectChats.delete(key);
        else _expandedProjectChats.add(key);
        _renderProjects();
      });
      chats.appendChild(showMore);
    }
    const revealBody = document.createElement('div');
    revealBody.className = 'workspace-project-chat-reveal-body';
    revealBody.appendChild(chats);
    reveal.appendChild(revealBody);
    wrapper.appendChild(reveal);
    list.appendChild(wrapper);
  });

  if (inferredWorkspace) window.sessionModule?.renderSessionList?.();
}

export function getProjectEntries() {
  return _visibleProjectEntries.slice();
}

export function activateProject(path) {
  if (!path) return;
  setWorkspace(path);
  _setProjectFold(path, false);
  _renderProjects();
}

// Workspace only applies to agent mode (it scopes the file/shell tools), so the
// pill + overflow entry are hidden in chat mode, like the bash toggle.
function _isChatMode() {
  const b = document.getElementById('mode-chat-btn');
  return !!(b && b.classList.contains('active'));
}

export function syncWorkspaceIndicator(path) {
  const chat = _isChatMode();
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const pathLabel = document.getElementById('workspace-indicator-path');
  const overflow = document.getElementById('overflow-workspace-btn');
  if (pill) {
    pill.style.display = (path && !chat) ? '' : 'none';
    pill.classList.toggle('active', !!path);
    if (path) pill.title = `Workspace: ${path}\nFile tools are confined here; shell commands start here but are not sandboxed and can reach outside it.\nClick to change.`;
  }
  if (name) name.textContent = path ? _basename(path) : '';
  if (pathLabel) pathLabel.textContent = path ? _parentPath(path) : '';
  if (overflow) {
    overflow.style.display = chat ? 'none' : '';
    overflow.classList.toggle('active', !!path);
  }
  // Recompute the "+" overflow dot (app.js owns updatePlusDot via this event).
  try { document.dispatchEvent(new CustomEvent('overflow-state-change')); } catch (_) {}
  _renderProjects();
}

// Called by the agent/chat mode toggle so the pill + overflow entry follow mode.
export function applyMode(_mode) {
  syncWorkspaceIndicator(getWorkspace());
}

export function setWorkspace(path) {
  if (path) {
    Storage.set(KEYS.WORKSPACE, path);
    _rememberWorkspace(path);
  }
  else Storage.remove(KEYS.WORKSPACE);
  syncWorkspaceIndicator(path || '');
  try { document.dispatchEvent(new CustomEvent('odysseus-workspace-change', { detail: { path: path || '' } })); } catch (_) {}
}

/**
 * Validate a manually entered path server-side, then persist the canonical
 * form. Returns {ok, path|null}. Without this, a typo / file path / deleted
 * folder / filesystem root would be stored and shown as active while the
 * backend silently refuses to bind it on every send.
 */
export async function vetAndSetWorkspace(path) {
  try {
    const res = await fetch(`${API_BASE}/api/workspace/vet?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!res.ok) return { ok: false, path: null };
    const data = await res.json();
    if (data.ok && data.path) {
      setWorkspace(data.path);
      return { ok: true, path: data.path };
    }
    return { ok: false, path: null };
  } catch (e) {
    return { ok: false, path: null };
  }
}

export function clearWorkspace() {
  setWorkspace('');
  if (uiModule && uiModule.showToast) uiModule.showToast('Workspace cleared');
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`browse failed: ${res.status}`);
  return res.json();
}

function _render(data) {
  _curPath = data.path;
  const body = _modal.querySelector('#workspace-body');
  const pathEl = _modal.querySelector('#workspace-cur-path');
  if (pathEl) {
    // Reflect the resolved (realpath) location back into the editable field.
    pathEl.value = data.path;
    pathEl.title = data.path;
  }
  let rows = '';
  if (data.parent) {
    rows += `<div class="workspace-row workspace-up" data-path="${encodeURIComponent(data.parent)}">↑ ..</div>`;
  }
  for (const d of data.dirs) {
    // Backend supplies the full child path (os.path.join → cross-platform).
    rows += `<div class="workspace-row" data-path="${encodeURIComponent(d.path)}">${_FOLDER_SVG}<span>${uiModule.esc(d.name)}</span></div>`;
  }
  if (data.truncated) {
    rows += '<div class="workspace-empty">Too many folders to list. Type or paste a path above to jump in.</div>';
  }
  if (!data.dirs.length && !data.parent) rows = '<div class="workspace-empty">No subfolders</div>';
  body.innerHTML = rows || '<div class="workspace-empty">No subfolders</div>';
  body.querySelectorAll('.workspace-row').forEach((row) => {
    row.addEventListener('click', () => _navigate(decodeURIComponent(row.dataset.path)));
  });
  // Filesystem roots (and sensitive dirs) can be browsed through but never
  // bound as the workspace; the backend rejects them too.
  const useBtn = _modal.querySelector('#workspace-use');
  if (useBtn) {
    useBtn.disabled = data.selectable === false;
    useBtn.title = data.selectable === false ? 'This folder cannot be used as a workspace' : '';
  }
}

async function _navigate(path) {
  try {
    _render(await _load(path));
  } catch (e) {
    if (uiModule && uiModule.showError) uiModule.showError('Could not open folder');
  }
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'workspace-modal';
  _modal.className = 'modal';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Select workspace</h4>
        <button class="close-btn" id="workspace-close" aria-label="Close">✖</button>
      </div>
      <input type="text" class="styled-prompt-input workspace-cur" id="workspace-cur-path"
             spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off"
             placeholder="Type or paste a folder path, then press Enter" />
      <p class="muted workspace-note">File tools are <strong>confined</strong> to this folder. Shell commands start here but are <strong>not sandboxed</strong> and can reach outside it. A workspace scopes the tools; it is not a security boundary.</p>
      <div class="modal-body workspace-body" id="workspace-body"></div>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-primary" id="workspace-use">Use this folder</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#workspace-close').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cancel').addEventListener('click', closeWorkspaceBrowser);
  // Editable path bar: Enter navigates to a typed/pasted folder.
  _modal.querySelector('#workspace-cur-path').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = e.target.value.trim();
      if (v) _navigate(v);
    }
  });
  _modal.querySelector('#workspace-use').addEventListener('click', () => {
    setWorkspace(_curPath);
    if (uiModule && uiModule.showToast) uiModule.showToast(`Workspace set: ${_basename(_curPath)}`);
    closeWorkspaceBrowser();
  });
  const content = _modal.querySelector('.modal-content');
  const header = _modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(_modal, { content, header });
  return _modal;
}

export async function openWorkspaceBrowser() {
  const modal = _getModal();
  modal.style.display = 'flex';
  try {
    _render(await _load(getWorkspace() || ''));
  } catch (e) {
    if (uiModule && uiModule.showError) uiModule.showError('Could not browse folders');
  }
}

export function closeWorkspaceBrowser() {
  if (_modal) _modal.style.display = 'none';
}

async function _loadWorkspaceDefaults() {
  try {
    const [defaultRes, projectsRes] = await Promise.all([
      fetch(`${API_BASE}/api/workspace/default`, { credentials: 'same-origin' }),
      fetch(`${API_BASE}/api/workspace/projects`, { credentials: 'same-origin' }),
    ]);
    if (defaultRes.ok) {
      const data = await defaultRes.json();
      _defaultRoot = data.path || '';
    }
    if (projectsRes.ok) {
      const data = await projectsRes.json();
      _defaultRoot = data.root || _defaultRoot;
      _projectCatalog = Array.isArray(data.projects) ? data.projects : [];
    }
    if (!getWorkspace() && _defaultRoot) setWorkspace(_defaultRoot);
    else _renderProjects();
  } catch (_) {
    _renderProjects();
  }
}

export async function initWorkspace() {
  // Restore immediately, then resolve the server default/project catalog.
  syncWorkspaceIndicator(getWorkspace());
  const overflow = document.getElementById('overflow-workspace-btn');
  if (overflow) overflow.addEventListener('click', openWorkspaceBrowser);
  const pill = document.getElementById('workspace-indicator-btn');
  if (pill) pill.addEventListener('click', openWorkspaceBrowser);
  const addProject = document.getElementById('projects-add-btn');
  if (addProject) addProject.addEventListener('click', openWorkspaceBrowser);
  const projectsToggle = document.getElementById('projects-section-toggle');
  if (projectsToggle) projectsToggle.addEventListener('click', () => {
    _setProjectSectionCollapsed(projectsToggle.getAttribute('aria-expanded') === 'true');
  });
  _setProjectSectionCollapsed(_projectSectionCollapsed());
  document.addEventListener('odysseus-sessions-loaded', _renderProjects);
  document.addEventListener('odysseus-session-activity-change', _renderProjects);
  document.addEventListener('odysseus-activity-change', _renderProjects);
  await _loadWorkspaceDefaults();
}

export default { initWorkspace, openWorkspaceBrowser, getWorkspace, setWorkspace, vetAndSetWorkspace, clearWorkspace, syncWorkspaceIndicator, applyMode, getProjectEntries, activateProject };

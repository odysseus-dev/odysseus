import uiModule from './ui.js';

const API_BASE = window.location.origin;

function shortPath(path) {
  const value = (path || '').trim();
  if (!value) return 'Home';
  const parts = value.split('/').filter(Boolean);
  if (!parts.length) return value;
  const tail = parts.slice(-2).join('/');
  return value.startsWith('/') ? '/' + tail : tail;
}

function setStatus(el, text, ok = true) {
  if (!el) return;
  el.textContent = text || '';
  el.classList.toggle('error', !ok);
}

function positionMenu(btn, menu) {
  const rect = btn.getBoundingClientRect();
  const width = Math.min(360, window.innerWidth - 24);
  menu.style.width = width + 'px';
  let left = rect.left;
  if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
  menu.style.left = Math.max(12, left) + 'px';
  menu.style.bottom = Math.max(12, window.innerHeight - rect.top + 8) + 'px';
}

async function loadSettings() {
  const res = await fetch(`${API_BASE}/api/auth/settings`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`settings ${res.status}`);
  return res.json();
}

async function saveWorkspace(path) {
  const res = await fetch(`${API_BASE}/api/auth/settings`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_dir: path }),
  });
  if (!res.ok) {
    let message = `save ${res.status}`;
    try {
      const data = await res.json();
      message = data.detail || data.error || message;
    } catch (_) {}
    throw new Error(message);
  }
  return res.json();
}

async function testPwd() {
  const res = await fetch(`${API_BASE}/api/shell/exec`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: 'pwd' }),
  });
  if (!res.ok) throw new Error(`shell ${res.status}`);
  return res.json();
}

async function initWorkspaceControl() {
  const wrap = document.getElementById('workspace-control-wrap');
  const btn = document.getElementById('workspace-cwd-btn');
  const label = document.getElementById('workspace-cwd-label');
  const menu = document.getElementById('workspace-cwd-menu');
  const input = document.getElementById('workspace-cwd-input');
  const saveBtn = document.getElementById('workspace-cwd-save');
  const resetBtn = document.getElementById('workspace-cwd-reset');
  const testBtn = document.getElementById('workspace-cwd-test');
  const status = document.getElementById('workspace-cwd-status');
  if (!wrap || !btn || !label || !menu || !input || !saveBtn || !resetBtn || !testBtn) return;

  let workspaceDir = '';
  try {
    const auth = await fetch(`${API_BASE}/api/auth/status`, { credentials: 'same-origin' }).then(r => r.json());
    if (auth && auth.configured && !auth.is_admin) return;
    const settings = await loadSettings();
    workspaceDir = settings.workspace_dir || '';
  } catch (_) {
    return;
  }

  function render() {
    input.value = workspaceDir;
    label.textContent = shortPath(workspaceDir);
    btn.title = workspaceDir ? `CWD: ${workspaceDir}` : 'CWD: home directory';
  }

  function close() {
    menu.classList.add('hidden');
    btn.classList.remove('active');
    btn.setAttribute('aria-expanded', 'false');
  }

  function open() {
    positionMenu(btn, menu);
    menu.classList.remove('hidden');
    btn.classList.add('active');
    btn.setAttribute('aria-expanded', 'true');
    input.focus();
    input.select();
  }

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    if (menu.classList.contains('hidden')) open();
    else close();
  });

  saveBtn.addEventListener('click', async () => {
    const next = (input.value || '').trim();
    setStatus(status, 'Saving...');
    try {
      await saveWorkspace(next);
      workspaceDir = next;
      render();
      setStatus(status, next ? 'Saved' : 'Using home directory');
      uiModule.showToast('Workspace saved');
    } catch (e) {
      setStatus(status, e.message || 'Save failed', false);
    }
  });

  resetBtn.addEventListener('click', async () => {
    input.value = '';
    saveBtn.click();
  });

  testBtn.addEventListener('click', async () => {
    setStatus(status, 'Running pwd...');
    try {
      const data = await testPwd();
      const out = (data.stdout || '').trim();
      setStatus(status, data.exit_code === 0 ? out : (data.stderr || 'pwd failed'), data.exit_code === 0);
    } catch (e) {
      setStatus(status, 'pwd failed', false);
    }
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveBtn.click();
    if (e.key === 'Escape') close();
  });

  document.addEventListener('click', (e) => {
    if (!wrap.contains(e.target) && !menu.contains(e.target)) close();
  });
  window.addEventListener('resize', () => {
    if (!menu.classList.contains('hidden')) positionMenu(btn, menu);
  });

  render();
  wrap.style.display = '';
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initWorkspaceControl);
} else {
  initWorkspaceControl();
}

// nextcloud.js — read-only Nextcloud Files explorer.
//
// Opens a modal file browser over /api/nextcloud/* (added in routes/nextcloud_routes.py).
// Self-contained: it builds its DOM with createElement/textContent so untrusted
// file/folder names from the server can't inject markup, reuses the app's CSS
// variables (--panel/--border/--fg/--accent) and inline monochrome SVG icons,
// and uses no Unicode emoji. Credentials ride the same-origin session cookie.
//
// Public entry point: window.openNextcloudExplorer(accountId, label)

function _ncIcon(name) {
  // Monochrome inline SVGs matching the rest of the UI's icon style.
  const icons = {
    folder: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    file: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    download: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    back: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
  };
  return icons[name] || '';
}

function _ncHumanSize(n) {
  if (n === null || n === undefined || isNaN(n)) return '';
  const f = Number(n);
  if (f < 1024) return f + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = f / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(1) + ' ' + units[i];
}

// Extensions/mime types the built-in document editor can edit. Everything else
// (xlsx, pdf, docx, png, …) falls back to view/download in a new tab.
const _NC_TEXT_EXT = ['txt', 'md', 'markdown', 'py', 'js', 'ts', 'json', 'csv', 'xml', 'yaml', 'yml', 'html', 'htm', 'css', 'svg', 'log', 'ini', 'toml', 'cfg', 'sh', 'bash', 'sql', 'java', 'c', 'cpp', 'h', 'hpp', 'cc', 'go', 'rs', 'rb', 'php', 'tex', 'env', 'mdx'];

function _ncIsTextLike(entry) {
  const ct = (entry.content_type || '').toLowerCase();
  if (ct.startsWith('text/')) return true;
  if (ct === 'application/json' || ct === 'application/xml' || ct === 'application/javascript' || ct === 'application/x-yaml') return true;
  const ext = (entry.name.split('.').pop() || '').toLowerCase();
  return _NC_TEXT_EXT.indexOf(ext) !== -1;
}

function _ncFileUrl(accountId, path) {
  return '/api/nextcloud/file?account=' + encodeURIComponent(accountId) + '&path=' + encodeURIComponent(path);
}

// Open a text/markdown/code file in the built-in editor. Seeds a new library
// document with the file content, registers it for save→Nextcloud writeback,
// then opens it via the #document-<id> deep-link. Binary files are opened in a
// new tab instead (see _ncOpenFile).
async function _ncOpenInEditor(accountId, entry) {
  let text;
  try {
    const r = await fetch(_ncFileUrl(accountId, entry.path), { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    text = await r.text();
  } catch (_) {
    window.open(_ncFileUrl(accountId, entry.path), '_blank');  // fallback to view
    return;
  }
  try {
    const cr = await fetch('/api/document', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: entry.name, content: text }),
    });
    if (!cr.ok) throw new Error('create doc HTTP ' + cr.status);
    const d = await cr.json();
    // Register so subsequent saves mirror back to Nextcloud (best-effort).
    fetch('/api/nextcloud/register', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ doc_id: d.id, account: accountId, path: entry.path }),
    }).catch(() => {});
    const closeBtn = document.getElementById('doclib-close');  // close Library modal if open
    if (closeBtn) closeBtn.click();
    window.location.hash = '#document-' + d.id;  // deep-link opens the editor
  } catch (_) {
    window.open(_ncFileUrl(accountId, entry.path), '_blank');  // fallback to view
  }
}

function _ncOpenFile(accountId, entry) {
  if (_ncIsTextLike(entry)) _ncOpenInEditor(accountId, entry);
  else window.open(_ncFileUrl(accountId, entry.path), '_blank');
}

async function _ncFetchList(accountId, path) {
  const url = '/api/nextcloud/list?account=' + encodeURIComponent(accountId) + '&path=' + encodeURIComponent(path);
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) {
    let detail = '';
    try { detail = (await r.json()).detail || ''; } catch (_) {}
    throw new Error(detail || ('HTTP ' + r.status));
  }
  return (await r.json()).entries || [];
}

window.openNextcloudExplorer = function (accountId, label) {
  if (!accountId) return;
  // Backdrop.
  const backdrop = document.createElement('div');
  backdrop.className = 'nc-explorer-backdrop';
  backdrop.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;padding:24px;';

  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--panel,#111);border:1px solid var(--border);border-radius:10px;width:min(760px,100%);height:min(82vh,680px);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,0.4);';

  // Header.
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border);';
  const title = document.createElement('div');
  title.style.cssText = 'font-weight:600;font-size:13px;display:flex;align-items:center;gap:6px;color:var(--accent,var(--red));flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
  title.innerHTML = _ncIcon('folder');
  const titleText = document.createElement('span');
  titleText.textContent = 'Nextcloud' + (label ? ' — ' + label : '');
  title.appendChild(titleText);
  const closeBtn = document.createElement('button');
  closeBtn.title = 'Close';
  closeBtn.style.cssText = 'background:none;border:none;color:var(--fg);cursor:pointer;padding:4px;display:inline-flex;opacity:0.7;';
  closeBtn.innerHTML = _ncIcon('close');
  closeBtn.onclick = () => backdrop.remove();
  header.appendChild(title);
  header.appendChild(closeBtn);

  // Breadcrumbs.
  const crumbs = document.createElement('div');
  crumbs.style.cssText = 'display:flex;align-items:center;gap:4px;padding:8px 12px;border-bottom:1px solid var(--border);font-size:12px;flex-wrap:wrap;';

  // Body (scrollable listing).
  const body = document.createElement('div');
  body.style.cssText = 'flex:1;overflow-y:auto;padding:6px 8px;font-size:12px;';

  // Status line.
  const status = document.createElement('div');
  status.style.cssText = 'padding:8px 12px;border-top:1px solid var(--border);font-size:11px;opacity:0.6;min-height:20px;';

  panel.appendChild(header);
  panel.appendChild(crumbs);
  panel.appendChild(body);
  panel.appendChild(status);
  backdrop.appendChild(panel);
  document.body.appendChild(backdrop);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.remove(); });
  const onKey = (e) => { if (e.key === 'Escape') { backdrop.remove(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);

  let currentPath = '';

  function renderCrumbs() {
    crumbs.innerHTML = '';
    const root = document.createElement('button');
    root.textContent = '/';
    root.style.cssText = 'background:none;border:none;color:var(--accent,var(--red));cursor:pointer;font:inherit;padding:2px 4px;';
    root.onclick = () => navigate('');
    crumbs.appendChild(root);
    if (!currentPath) return;
    const segs = currentPath.split('/').filter(Boolean);
    let acc = '';
    segs.forEach((seg, i) => {
      acc = acc ? acc + '/' + seg : seg;
      const sep = document.createElement('span');
      sep.textContent = '/';
      sep.style.opacity = '0.4';
      crumbs.appendChild(sep);
      const isLast = i === segs.length - 1;
      const b = document.createElement('button');
      b.textContent = seg;
      b.style.cssText = 'background:none;border:none;font:inherit;padding:2px 4px;cursor:pointer;color:' + (isLast ? 'var(--fg)' : 'var(--accent,var(--red))') + ';';
      if (!isLast) b.onclick = () => navigate(acc);
      crumbs.appendChild(b);
    });
  }

  function navigate(path) {
    currentPath = (path || '').replace(/^\/+|\/+$/g, '');
    renderCrumbs();
    body.innerHTML = '';
    const loading = document.createElement('div');
    loading.textContent = 'Loading…';
    loading.style.cssText = 'padding:16px;opacity:0.6;';
    body.appendChild(loading);
    status.textContent = '/' + (currentPath || '');
    _ncFetchList(accountId, currentPath).then((entries) => {
      body.innerHTML = '';
      const dirs = entries.filter(e => e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
      const files = entries.filter(e => !e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
      if (dirs.length === 0 && files.length === 0) {
        const empty = document.createElement('div');
        empty.textContent = 'This folder is empty.';
        empty.style.cssText = 'padding:16px;opacity:0.6;';
        body.appendChild(empty);
        return;
      }
      const rowFor = (entry) => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:6px;cursor:pointer;';
        const ico = document.createElement('span');
        ico.style.cssText = 'display:inline-flex;color:var(--accent,var(--red));opacity:0.8;flex-shrink:0;';
        ico.innerHTML = entry.is_dir ? _ncIcon('folder') : _ncIcon('file');
        const name = document.createElement('span');
        name.textContent = entry.name + (entry.is_dir ? '/' : '');
        name.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        const meta = document.createElement('span');
        meta.style.cssText = 'opacity:0.5;font-size:11px;flex-shrink:0;';
        meta.textContent = entry.is_dir ? '' : _ncHumanSize(entry.size);
        row.appendChild(ico);
        row.appendChild(name);
        row.appendChild(meta);
        row.onmouseenter = () => { row.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; };
        row.onmouseleave = () => { row.style.background = 'transparent'; };
        if (entry.is_dir) {
          row.onclick = () => navigate(entry.path);
        } else {
          row.onclick = () => _ncOpenFile(accountId, entry);
        }
        return row;
      };
      dirs.forEach(d => body.appendChild(rowFor(d)));
      files.forEach(f => body.appendChild(rowFor(f)));
      status.textContent = '/' + (currentPath || '') + '  ·  ' + dirs.length + ' folder' + (dirs.length === 1 ? '' : 's') + ', ' + files.length + ' file' + (files.length === 1 ? '' : 's');
    }).catch((e) => {
      body.innerHTML = '';
      const err = document.createElement('div');
      err.style.cssText = 'padding:16px;color:var(--red);';
      err.textContent = 'Could not list this folder: ' + (e.message || e);
      body.appendChild(err);
      status.textContent = '';
    });
  }

  navigate('');
};

// ── Library tab: read-only folder tree of all configured Nextcloud accounts ──

const _NC_CHEV = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';

function _ncRow(depth) {
  const row = document.createElement('div');
  row.style.cssText = 'display:flex;align-items:center;gap:7px;padding:4px 6px;cursor:pointer;border-radius:5px;';
  row.style.paddingLeft = (6 + depth * 14) + 'px';
  return row;
}

function _ncHover(row) {
  row.onmouseenter = () => { row.style.background = 'color-mix(in srgb, var(--fg) 8%, transparent)'; };
  row.onmouseleave = () => { row.style.background = 'transparent'; };
}

function _ncFileNode(accountId, entry, depth) {
  const row = _ncRow(depth);
  row.style.cursor = 'pointer';
  const spacer = document.createElement('span');
  spacer.style.cssText = 'display:inline-block;width:10px;flex-shrink:0;';
  const ico = document.createElement('span');
  ico.style.cssText = 'display:inline-flex;color:var(--accent,var(--red));opacity:0.7;flex-shrink:0;';
  ico.innerHTML = _ncIcon('file');
  const name = document.createElement('span');
  name.textContent = entry.name;
  name.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
  const meta = document.createElement('span');
  meta.style.cssText = 'opacity:0.5;font-size:11px;flex-shrink:0;';
  meta.textContent = _ncHumanSize(entry.size);
  row.appendChild(spacer); row.appendChild(ico); row.appendChild(name); row.appendChild(meta);
  _ncHover(row);
  row.onclick = () => _ncOpenFile(accountId, entry);
  return row;
}

function _ncFolderNode(accountId, label, path, depth) {
  const wrap = document.createElement('div');
  const row = _ncRow(depth);
  const chev = document.createElement('span');
  chev.style.cssText = 'display:inline-flex;opacity:0.6;flex-shrink:0;transition:transform 0.15s;';
  chev.innerHTML = _NC_CHEV;
  const ico = document.createElement('span');
  ico.style.cssText = 'display:inline-flex;color:var(--accent,var(--red));opacity:0.85;flex-shrink:0;';
  ico.innerHTML = _ncIcon('folder');
  const name = document.createElement('span');
  name.textContent = label + (path ? '' : '');
  name.style.cssText = 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:' + (depth === 0 ? '600' : '400') + ';';
  row.appendChild(chev); row.appendChild(ico); row.appendChild(name);
  _ncHover(row);
  const kids = document.createElement('div');
  kids.style.cssText = 'display:none;';
  wrap.appendChild(row); wrap.appendChild(kids);
  let loaded = false;
  row.onclick = () => {
    const open = kids.style.display !== 'none';
    if (open) {
      kids.style.display = 'none';
      chev.style.transform = 'rotate(0deg)';
      return;
    }
    kids.style.display = '';
    chev.style.transform = 'rotate(90deg)';
    if (loaded) return;
    loaded = true;
    const loading = document.createElement('div');
    loading.textContent = 'Loading…';
    loading.style.cssText = 'padding:4px 6px;opacity:0.6;';
    kids.appendChild(loading);
    _ncFetchList(accountId, path).then((entries) => {
      kids.innerHTML = '';
      const dirs = entries.filter(e => e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
      const files = entries.filter(e => !e.is_dir).sort((a, b) => a.name.localeCompare(b.name));
      if (!dirs.length && !files.length) {
        const empty = document.createElement('div');
        empty.textContent = 'Empty folder';
        empty.style.cssText = 'padding:4px 6px;opacity:0.5;';
        kids.appendChild(empty);
        return;
      }
      dirs.forEach(d => kids.appendChild(_ncFolderNode(accountId, d.name, d.path, depth + 1)));
      files.forEach(f => kids.appendChild(_ncFileNode(accountId, f, depth + 1)));
    }).catch((e) => {
      kids.innerHTML = '';
      const err = document.createElement('div');
      err.style.cssText = 'padding:4px 6px;color:var(--red);';
      err.textContent = 'Could not list folder: ' + (e.message || e);
      kids.appendChild(err);
    });
  };
  return wrap;
}

window.renderNextcloudLibrary = function () {
  const root = document.getElementById('doclib-nextcloud-tree');
  if (!root) return;
  root.innerHTML = '';
  const loading = document.createElement('div');
  loading.textContent = 'Loading…';
  loading.style.cssText = 'padding:10px;opacity:0.6;';
  root.appendChild(loading);
  fetch('/api/nextcloud/accounts', { credentials: 'same-origin' })
    .then(r => r.ok ? r.json() : { accounts: [] })
    .then((d) => {
      root.innerHTML = '';
      const accounts = (d && d.accounts) || [];
      if (!accounts.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'padding:10px;opacity:0.6;';
        empty.textContent = 'No Nextcloud account configured. Add one in Settings \u2192 Integrations.';
        root.appendChild(empty);
        return;
      }
      accounts.forEach(acc => {
        const label = acc.label || acc.username || 'Nextcloud';
        root.appendChild(_ncFolderNode(acc.id, label, '', 0));
      });
    })
    .catch(() => {
      root.innerHTML = '';
      const err = document.createElement('div');
      err.style.cssText = 'padding:10px;color:var(--red);';
      err.textContent = 'Could not load Nextcloud accounts.';
      root.appendChild(err);
    });
};


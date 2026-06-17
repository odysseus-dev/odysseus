/**
 * Workspace Notes — sidebar widget
 * Shows sticky notes for the currently active Docker workspace.
 * Registers itself in the sidebar via window.OdysseusPkg.addWidget().
 */
(() => {
  const API = '/api/workspace-notes';

  // ── State ──────────────────────────────────────────────────────────────────
  let _wsId = null;
  let _notes = [];
  let _panel = null;

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  async function _api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(API + path, opts);
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    return r.json();
  }

  // ── Load notes for current workspace ──────────────────────────────────────
  async function _load() {
    if (!_wsId) { _render(); return; }
    try {
      const data = await _api('GET', `/${encodeURIComponent(_wsId)}`);
      _notes = data.notes || [];
    } catch {
      _notes = [];
    }
    _render();
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  function _render() {
    if (!_panel) return;
    if (!_wsId) {
      _panel.innerHTML = '<div style="font-size:11px;opacity:0.5;padding:8px 4px">No workspace selected</div>';
      return;
    }
    _panel.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px">
        ${_notes.map(n => `
          <div class="admin-user-row" data-note-id="${_esc(n.id)}" style="padding:6px 8px">
            ${n.title ? `<div style="font-size:11px;font-weight:600;margin-bottom:3px">${_esc(n.title)}</div>` : ''}
            <div style="font-size:11px;white-space:pre-wrap;word-break:break-word">${_esc(n.content)}</div>
            <div style="display:flex;gap:4px;margin-top:5px">
              <button class="admin-btn-sm ws-note-edit-btn" data-id="${_esc(n.id)}" style="font-size:10px;padding:1px 6px">Edit</button>
              <button class="admin-btn-delete ws-note-del-btn" data-id="${_esc(n.id)}" style="font-size:10px;padding:1px 6px">×</button>
            </div>
          </div>
        `).join('') || '<div style="font-size:11px;opacity:0.45;padding:4px 2px">No notes yet.</div>'}
        <div id="ws-note-form" style="display:flex;flex-direction:column;gap:4px;margin-top:4px">
          <input id="ws-note-title" type="text" placeholder="Title (optional)" style="width:100%;box-sizing:border-box;font-size:11px;padding:4px 6px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--fg)">
          <textarea id="ws-note-content" placeholder="Note content…" rows="3" style="width:100%;box-sizing:border-box;font-size:11px;padding:4px 6px;border:1px solid var(--border);border-radius:5px;background:var(--bg);color:var(--fg);resize:vertical"></textarea>
          <button class="admin-btn-add" id="ws-note-add-btn" style="font-size:11px;padding:3px 8px;align-self:flex-start">Add Note</button>
        </div>
      </div>
    `;

    _panel.querySelectorAll('.ws-note-del-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.id;
        try { await _api('DELETE', `/${id}`); await _load(); } catch (e) { alert(e.message); }
      });
    });

    _panel.querySelectorAll('.ws-note-edit-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.id;
        const note = _notes.find(n => n.id === id);
        if (!note) return;
        const newContent = prompt('Edit note:', note.content);
        if (newContent === null) return;
        _api('PATCH', `/${id}`, { content: newContent }).then(_load).catch(e => alert(e.message));
      });
    });

    _panel.querySelector('#ws-note-add-btn')?.addEventListener('click', async () => {
      const title = _panel.querySelector('#ws-note-title')?.value.trim() || '';
      const content = _panel.querySelector('#ws-note-content')?.value.trim() || '';
      if (!content) return;
      try {
        await _api('POST', '', { workspace_id: _wsId, title, content });
        _panel.querySelector('#ws-note-title').value = '';
        _panel.querySelector('#ws-note-content').value = '';
        await _load();
      } catch (e) { alert(e.message); }
    });
  }

  // ── Watch the workspace selector for changes ───────────────────────────────
  function _syncWorkspace() {
    const newId = window.OdysseusShellWS?.getSelected?.() || null;
    if (newId !== _wsId) {
      _wsId = newId;
      _load();
    }
  }

  // ── Register with the sidebar widget slot ─────────────────────────────────
  function _mount() {
    const pkg = window.OdysseusPkg;
    if (!pkg) return;

    const container = document.createElement('div');
    container.style.cssText = 'padding:6px 8px 4px';

    const header = document.createElement('div');
    header.style.cssText = 'font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;opacity:0.55;margin-bottom:6px;display:flex;align-items:center;gap:5px';
    header.innerHTML = `
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      Workspace Notes
    `;

    _panel = document.createElement('div');
    container.appendChild(header);
    container.appendChild(_panel);

    pkg.addWidget('sidebarWidget', container);

    // Poll workspace selection every 2s (lightweight, no websocket needed)
    _syncWorkspace();
    setInterval(_syncWorkspace, 2000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _mount);
  } else {
    _mount();
  }
})();

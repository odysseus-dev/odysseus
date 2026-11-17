// MCP server integration form. Extracted from the unified integrations host.

import { byId as el } from '../../dom.js';

// ── MCP form — full management view ──
export async function showMcpForm({ formEl, renderList, closeForm }, editId) {
  // Toggle an in-flight loading state on a button (disabled + dimmed + label).
  function _setBtnLoading(btn, loading, label) {
    if (!btn) return;
    btn.disabled = loading;
    btn.style.opacity = loading ? '0.6' : '';
    btn.style.cursor = loading ? 'progress' : '';
    if (label != null) btn.textContent = label;
  }
  function _showMcpPasteback(id) {
    const msg = el('uf-mcp-msg'); if (!msg) return;
    if (el('uf-mcp-pasteback')) return;  // already shown
    msg.innerHTML =
      'Authorize in the opened tab. If the redirect fails (remote access), paste the resulting URL here: ' +
      '<input id="uf-mcp-pasteback" class="settings-input" placeholder="http://localhost:7000/api/mcp/oauth/callback?code=..." style="margin-top:4px">' +
      '<button class="admin-btn-sm" id="uf-mcp-paste-go" style="margin-top:4px">Submit</button>';
    const pasteGo = el('uf-mcp-paste-go');
    if (pasteGo) pasteGo.addEventListener('click', async () => {
      const cb = el('uf-mcp-pasteback').value.trim();
      if (!cb) return;
      const pf = new FormData(); pf.append('callback_url', cb);
      _setBtnLoading(pasteGo, true, 'Submitting…');
      try {
        await fetch(`/api/mcp/oauth/exchange/${id}`, { method: 'POST', credentials: 'same-origin', body: pf });
      } finally {
        _setBtnLoading(pasteGo, false, 'Submit');
      }
    });
  }

  // Drives the OAuth flow: waits for the auth_url (discovery+DCR may lag),
  // opens it once, then resolves on connected/error.
  async function _handleMcpAuth(id, initialAuthUrl, tries = 90) {
    let opened = false;
    const openAuth = (u) => { if (!opened && u) { opened = true; window.open(u, '_blank', 'noopener'); _showMcpPasteback(id); } };
    openAuth(initialAuthUrl);
    const msg = el('uf-mcp-msg');
    let fails = 0;
    for (let i = 0; i < tries; i++) {
      await new Promise(res => setTimeout(res, 2000));
      try {
        const r = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const list = await r.json();
        fails = 0;
        const s = Array.isArray(list) ? list.find(x => x.id === id) : null;
        if (!s) continue;
        if (s.auth_url) openAuth(s.auth_url);
        if (s.status === 'connected') {
          if (msg) msg.textContent = `Connected (${s.tool_count || 0} tools)`;
          await renderList(); return;
        }
        if (s.status === 'error') {
          if (msg) msg.textContent = `Failed: ${s.error || 'unknown'}`; return;
        }
      } catch (e) {
        // Tolerate a single blip, but surface persistent failures instead of
        // silently polling until timeout.
        if (++fails >= 5 && msg) msg.textContent = `Status check failing (${e.message || 'network error'}) — still retrying…`;
      }
    }
    if (msg) msg.textContent = 'Authorization timed out. Reconnect from the server list to retry.';
  }
  if (editId && editId !== 'new') {
    // Show management view for existing server
    formEl.innerHTML = '<div class="admin-card" style="margin-top:8px"><span style="opacity:0.5;font-size:11px">Loading...</span></div>';
    try {
      const res = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
      const servers = await res.json();
      const srv = servers.find(s => (s.id || s.name) === editId);
      if (!srv) { formEl.innerHTML = '<div class="admin-card" style="margin-top:8px">Server not found</div>'; return; }
      const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
      const statusColor = srv.needs_oauth ? '#e5a33a' : srv.status === 'connected' ? 'var(--green,#50fa7b)' : srv.status === 'error' ? 'var(--red)' : 'var(--fg)';
      const toolInfo = srv.status === 'connected' ? `${srv.enabled_tool_count}/${srv.tool_count} tools` : '';
      const statusText = srv.needs_oauth ? 'Needs authorization' : srv.status === 'connected' ? `Connected (${toolInfo})` : srv.status === 'error' ? `Error: ${esc(srv.error || 'unknown')}` : 'Disconnected';
      formEl.innerHTML = `
          <div class="admin-card" style="margin-top:8px">
            <h2 style="font-size:13px">${esc(srv.name)}</h2>
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px">
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${statusColor}"></span>
              <span style="font-size:11px;opacity:0.7">${statusText}</span>
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px;justify-content:flex-end;">
              <span id="uf-mcp-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
              ${srv.needs_oauth ? `<a href="/api/mcp/oauth/authorize/${srv.id}" target="_blank" class="admin-btn-add" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));text-decoration:none;font-weight:600;">Authorize</a>` : ''}
              <button class="admin-btn-add" id="uf-mcp-reconnect" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Reconnect</button>
              <button class="admin-btn-add" id="uf-mcp-toggle" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">${srv.is_enabled ? 'Disable' : 'Enable'}</button>
              <button class="admin-btn-add" id="uf-mcp-cancel" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Close</button>
            </div>
            <div id="uf-mcp-tools-panel"></div>
          </div>`;
      // Reconnect
      el('uf-mcp-reconnect').addEventListener('click', async () => {
        const msg = el('uf-mcp-msg'); msg.textContent = 'Reconnecting...';
        try {
          const r = await fetch(`/api/mcp/servers/${srv.id}/reconnect`, { method: 'POST', credentials: 'same-origin' });
          const d = await r.json();
          msg.textContent = d.connected ? `Connected (${d.tool_count} tools)` : `Failed: ${d.error || 'unknown'}`;
          await renderList();
          showMcpForm({ formEl, renderList, closeForm }, editId); // refresh this view
        } catch (e) { msg.textContent = 'Failed'; }
      });
      // Toggle enable/disable
      el('uf-mcp-toggle').addEventListener('click', async () => {
        const fd = new FormData(); fd.append('is_enabled', String(!srv.is_enabled));
        await fetch(`/api/mcp/servers/${srv.id}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
        await renderList();
        showMcpForm({ formEl, renderList, closeForm }, editId);
      });
      el('uf-mcp-cancel').addEventListener('click', () => {
        if (typeof closeForm === 'function') closeForm();
        else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
      });
      // Load tools list
      if (srv.status === 'connected' && srv.tool_count > 0) {
        const panel = el('uf-mcp-tools-panel');
        try {
          const tr = await fetch(`/api/mcp/servers/${srv.id}/tools`, { credentials: 'same-origin' });
          const tools = await tr.json();
          if (tools.length) {
            const disabled = new Set(tools.filter(t => t.is_disabled).map(t => t.name));
            panel.innerHTML = `<div class="mcp-tools-header"><span>Tools</span><span style="display:flex;gap:8px;align-items:center"><span class="mcp-tools-count">${tools.length - disabled.size}/${tools.length} enabled</span><a href="#" id="uf-mcp-all">All</a> <a href="#" id="uf-mcp-none">None</a></span></div><div class="mcp-tools-list">${tools.map(t => `<label title="${esc(t.description)}"><input type="checkbox" data-mcp-tool-name="${esc(t.name)}" ${!t.is_disabled ? 'checked' : ''}><span><strong>${esc(t.name)}</strong> <span style="opacity:0.5">— ${esc((t.description||'').slice(0,80))}</span></span></label>`).join('')}</div>`;
            const saveFn = async () => {
              const dis = [];
              panel.querySelectorAll('input[type=checkbox]').forEach(cb => { if (!cb.checked) dis.push(cb.dataset.mcpToolName); });
              await fetch(`/api/mcp/servers/${srv.id}/tools`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify({ disabled: dis }) });
              const cnt = panel.querySelector('.mcp-tools-count');
              if (cnt) cnt.textContent = `${tools.length - dis.length}/${tools.length} enabled`;
            };
            panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.addEventListener('change', saveFn));
            el('uf-mcp-all')?.addEventListener('click', (e) => { e.preventDefault(); panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = true); saveFn(); });
            el('uf-mcp-none')?.addEventListener('click', (e) => { e.preventDefault(); panel.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = false); saveFn(); });
          }
        } catch (_) { panel.innerHTML = '<span style="opacity:0.5;font-size:11px">Failed to load tools</span>'; }
      }
    } catch (_) { formEl.innerHTML = '<div class="admin-card" style="margin-top:8px">Failed to load server</div>'; }
  } else {
    // Add new MCP server form
    formEl.innerHTML = `
        <div class="admin-card" style="margin-top:8px">
          <h2 style="font-size:13px">Add MCP Server</h2>
          <div class="settings-col">
            <div class="settings-row"><label class="settings-label" for="uf-mcp-name">Name</label><input id="uf-mcp-name" class="settings-input" placeholder="Server name" required aria-required="true"></div>
            <div class="settings-row"><label class="settings-label" for="uf-mcp-transport">Transport</label><select id="uf-mcp-transport" class="settings-input"><option value="stdio">stdio</option><option value="sse">SSE</option><option value="http">Streamable HTTP</option></select></div>
            <div id="uf-mcp-stdio-fields" style="display:flex;flex-direction:column;gap:6px;">
              <div class="settings-row"><label class="settings-label" for="uf-mcp-cmd">Command</label><input id="uf-mcp-cmd" class="settings-input" placeholder="npx"></div>
              <div class="settings-row"><label class="settings-label" for="uf-mcp-args">Args</label><input id="uf-mcp-args" class="settings-input" placeholder='["-y", "@modelcontextprotocol/server-filesystem"]'></div>
              <div class="settings-row"><label class="settings-label" for="uf-mcp-env">Env</label><input id="uf-mcp-env" class="settings-input" placeholder='{"KEY": "value"}'></div>
            </div>
            <div id="uf-mcp-sse-fields" style="display:none;flex-direction:column;gap:6px;">
              <div class="settings-row"><label class="settings-label" for="uf-mcp-url">URL</label><input id="uf-mcp-url" class="settings-input" placeholder="http://localhost:3001/sse"></div>
            </div>
            <div class="settings-row" style="margin-top:10px;align-items:center;justify-content:flex-end;gap:6px;">
              <span id="uf-mcp-msg" role="status" aria-live="polite" style="font-size:11px;flex:1;margin-right:8px"></span>
              <button class="admin-btn-add" id="uf-mcp-save" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));font-weight:600;">Save</button>
              <button class="admin-btn-add" id="uf-mcp-cancel" style="background:transparent;color:var(--accent, var(--red));border-color:color-mix(in srgb, var(--accent, var(--red)) 45%, var(--border));">Cancel</button>
            </div>
          </div>
        </div>`;
    el('uf-mcp-transport').addEventListener('change', () => {
      const v = el('uf-mcp-transport').value;
      const isUrl = (v === 'sse' || v === 'http');
      el('uf-mcp-stdio-fields').style.display = isUrl ? 'none' : 'flex';
      el('uf-mcp-sse-fields').style.display = isUrl ? 'flex' : 'none';
      const urlInput = el('uf-mcp-url');
      if (urlInput) urlInput.placeholder = (v === 'http') ? 'https://mcp.example.com/mcp' : 'http://localhost:3001/sse';
    });
    el('uf-mcp-cancel').addEventListener('click', () => {
      if (typeof closeForm === 'function') closeForm();
      else { formEl.style.display = 'none'; formEl.innerHTML = ''; }
    });
    el('uf-mcp-save').addEventListener('click', async () => {
      const transport = el('uf-mcp-transport').value;
      // routes/mcp_routes.py uses FastAPI Form(...) — send multipart, not JSON.
      const fd = new FormData();
      fd.append('name', el('uf-mcp-name').value);
      fd.append('transport', transport);
      if (transport === 'stdio') {
        fd.append('command', el('uf-mcp-cmd').value);
        // Unlike env below, an unparseable args value is not silently
        // defaulted: it would spawn the subprocess with an empty argv.
        let args;
        try { args = JSON.stringify(JSON.parse(el('uf-mcp-args').value || '[]')); }
        catch (_) { el('uf-mcp-msg').textContent = 'Args must be valid JSON, e.g. ["-y", "pkg"]'; return; }
        let env  = '{}'; try { env  = JSON.stringify(JSON.parse(el('uf-mcp-env').value  || '{}')); } catch (_) {}
        fd.append('args', args);
        fd.append('env', env);
      } else {
        fd.append('url', el('uf-mcp-url').value);
      }
      const saveBtn = el('uf-mcp-save'), cancelBtn = el('uf-mcp-cancel');
      const _origLabel = saveBtn.textContent;
      _setBtnLoading(saveBtn, true, 'Saving…'); if (cancelBtn) cancelBtn.disabled = true;
      try {
        const r = await fetch('/api/mcp/servers', { method: 'POST', credentials: 'same-origin', body: fd });
        const data = await r.json().catch(() => ({}));
        if (r.ok && data.needs_auth) {
          el('uf-mcp-msg').textContent = 'Preparing authorization…';
          _handleMcpAuth(data.id, data.auth_url);
        } else if (r.ok && (data.connected || data.status === 'connected')) {
          el('uf-mcp-msg').textContent = `Connected (${data.tool_count || 0} tools)`;
          formEl.style.display = 'none'; await renderList();
        } else if (r.ok) {
          el('uf-mcp-msg').textContent = 'Saved'; formEl.style.display = 'none'; await renderList();
        } else {
          // Surface the server's reason. The Args validation above rejects
          // unparseable JSON, but `"x"` and `{}` parse and are refused by
          // routes/mcp/mcp_routes.py with a message naming the expected
          // shape; a bare status code sends the user looking in the wrong
          // place. Matches what admin.js shows for the same endpoint.
          el('uf-mcp-msg').textContent = data.detail || `Failed (${r.status})`;
        }
      } catch (_) { el('uf-mcp-msg').textContent = 'Failed'; }
      finally { _setBtnLoading(saveBtn, false, _origLabel); if (cancelBtn) cancelBtn.disabled = false; }
    });
  }
}

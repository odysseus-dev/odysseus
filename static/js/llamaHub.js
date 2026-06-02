// Local LLM hub — provision llama.cpp, download GGUF models, serve them with
// GPU offload, and auto-register the running server in the chat model picker.
//
// Self-contained panel (same pattern as terminal.js): injects its own launch
// button + modal, talks to /api/llama/*. No framework, vanilla JS.

let _injected = false;
let _statusTimer = null;

async function api(path, opts = {}) {
  const res = await fetch('/api/llama' + path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  return res.json();
}

function injectMarkup() {
  if (_injected) return;
  _injected = true;

  const btn = document.createElement('button');
  btn.id = 'llama-hub-btn';
  btn.title = 'Local LLM hub (llama.cpp)';
  btn.textContent = 'Local LLM';
  btn.style.cssText =
    'position:fixed;bottom:14px;right:140px;z-index:240;padding:6px 12px;' +
    'font-size:0.8rem;border-radius:6px;cursor:pointer;' +
    'background:var(--panel,#1e1e1e);color:var(--fg,#eee);' +
    'border:1px solid var(--border,#444);opacity:0.82;';
  btn.addEventListener('mouseenter', () => (btn.style.opacity = '1'));
  btn.addEventListener('mouseleave', () => (btn.style.opacity = '0.82'));
  btn.addEventListener('click', () => toggle());
  document.body.appendChild(btn);

  const modal = document.createElement('div');
  modal.id = 'llama-modal';
  modal.className = 'modal hidden';
  modal.innerHTML =
    '<div class="modal-content" style="width:min(720px,94vw);max-height:86vh;' +
    'display:flex;flex-direction:column;background:var(--bg,#111);overflow:hidden;">' +
      '<div class="modal-header" style="cursor:move;">' +
        '<h4 style="margin:0;">Local LLM Hub</h4>' +
        '<button class="close-btn" id="close-llama-modal" aria-label="Close">✖</button>' +
      '</div>' +
      '<div class="modal-body" style="flex:1;overflow:auto;padding:12px 16px;font-size:0.85rem;">' +
        '<div id="llama-status" style="margin-bottom:12px;opacity:0.85;">Loading…</div>' +
        '<div id="llama-provision-row" style="margin-bottom:14px;"></div>' +
        '<hr style="border-color:var(--border,#333);opacity:0.4;">' +
        '<h5 style="margin:10px 0 6px;">Download a GGUF model</h5>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">' +
          '<input id="llama-repo" placeholder="HF repo (e.g. Qwen/Qwen2.5-0.5B-Instruct-GGUF)" ' +
          'style="flex:1;min-width:260px;padding:5px 8px;background:var(--panel,#1e1e1e);' +
          'color:var(--fg,#eee);border:1px solid var(--border,#444);border-radius:4px;">' +
          '<button id="llama-download-btn" class="btn" style="padding:5px 12px;cursor:pointer;">Download</button>' +
        '</div>' +
        '<input id="llama-file" placeholder="optional: specific .gguf filename (blank = smallest/Q4_K_M)" ' +
        'style="width:100%;padding:5px 8px;background:var(--panel,#1e1e1e);color:var(--fg,#eee);' +
        'border:1px solid var(--border,#444);border-radius:4px;margin-bottom:4px;">' +
        '<div id="llama-download-msg" style="font-size:0.78rem;opacity:0.7;min-height:1em;"></div>' +
        '<hr style="border-color:var(--border,#333);opacity:0.4;">' +
        '<h5 style="margin:10px 0 6px;">Local models</h5>' +
        '<div id="llama-models">none</div>' +
        '<hr style="border-color:var(--border,#333);opacity:0.4;">' +
        '<h5 style="margin:10px 0 6px;">Running servers</h5>' +
        '<div id="llama-servers">none</div>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);
  modal.querySelector('#close-llama-modal').addEventListener('click', () => close());
  modal.querySelector('#llama-download-btn').addEventListener('click', doDownload);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function refresh() {
  const st = await api('/status');
  const sEl = document.getElementById('llama-status');
  if (sEl) {
    sEl.innerHTML =
      `Release <b>${esc(st.release_tag)}</b> · backend <b>${esc(st.backend)}</b> · ` +
      `binary ${st.binary_provisioned ? '<span style="color:var(--fg,#5d5)">provisioned</span>' :
        '<span style="color:var(--fg,#d95)">not provisioned</span>'}`;
  }
  const pRow = document.getElementById('llama-provision-row');
  if (pRow) {
    if (!st.binary_provisioned) {
      pRow.innerHTML =
        '<button id="llama-provision-btn" class="btn" style="padding:5px 12px;cursor:pointer;">' +
        `Provision llama.cpp (${esc(st.backend)} build)</button>` +
        '<span id="llama-provision-msg" style="margin-left:8px;font-size:0.78rem;opacity:0.7;"></span>';
      pRow.querySelector('#llama-provision-btn').addEventListener('click', doProvision);
    } else {
      pRow.innerHTML = '';
    }
  }
  await refreshModels();
  renderServers(st.servers || []);
}

async function refreshModels() {
  const data = await api('/models/local');
  const el = document.getElementById('llama-models');
  if (!el) return;
  const models = data.models || [];
  if (!models.length) { el.textContent = 'none downloaded yet'; return; }
  el.innerHTML = models.map(m =>
    `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border,#222);">` +
      `<span style="flex:1;">${esc(m.name)} <span style="opacity:0.6;">(${m.size_mb} MB · ${esc(m.repo)})</span></span>` +
      `<button class="btn llama-serve-one" data-path="${esc(m.path)}" data-id="${esc(m.name.replace('.gguf',''))}" ` +
      `style="padding:3px 10px;cursor:pointer;">Serve</button>` +
    `</div>`).join('');
  el.querySelectorAll('.llama-serve-one').forEach(b =>
    b.addEventListener('click', () => doServe(b.dataset.path, b.dataset.id)));
}

function renderServers(servers) {
  const el = document.getElementById('llama-servers');
  if (!el) return;
  if (!servers.length) { el.textContent = 'none running'; return; }
  el.innerHTML = servers.map(s =>
    `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border,#222);">` +
      `<span style="flex:1;">${esc(s.model_id)} <span style="opacity:0.6;">:${s.port} · ngl ${s.n_gpu_layers} · ` +
      `${s.alive ? '<span style="color:var(--fg,#5d5)">alive</span>' : '<span style="color:var(--color-error,#d55)">dead</span>'} · ${s.uptime_s}s</span></span>` +
      `<button class="btn llama-stop-one" data-id="${esc(s.model_id)}" style="padding:3px 10px;cursor:pointer;">Stop</button>` +
    `</div>`).join('');
  el.querySelectorAll('.llama-stop-one').forEach(b =>
    b.addEventListener('click', async () => { await api('/stop', { method: 'POST', body: JSON.stringify({ model_id: b.dataset.id }) }); refresh(); }));
}

async function doProvision() {
  const msg = document.getElementById('llama-provision-msg');
  if (msg) msg.textContent = 'downloading prebuilt binary… (this can take a minute)';
  const r = await api('/provision', { method: 'POST', body: '{}' });
  if (msg) msg.textContent = r.ok ? 'provisioned' : ('failed: ' + (r.error || ''));
  refresh();
}

async function doDownload() {
  const repo = document.getElementById('llama-repo').value.trim();
  const file = document.getElementById('llama-file').value.trim();
  const msg = document.getElementById('llama-download-msg');
  if (!repo) { if (msg) msg.textContent = 'enter a HF repo id'; return; }
  if (msg) msg.textContent = 'downloading… (large files take a while)';
  const r = await api('/download', { method: 'POST', body: JSON.stringify({ repo_id: repo, filename: file || null }) });
  if (msg) msg.textContent = r.ok ? ('downloaded → ' + r.path) : ('failed: ' + (r.error || ''));
  refreshModels();
}

async function doServe(path, modelId) {
  const msg = document.getElementById('llama-download-msg');
  if (msg) msg.textContent = `starting server for ${modelId}…`;
  const r = await api('/serve', { method: 'POST', body: JSON.stringify({ gguf_path: path, model_id: modelId, register: true }) });
  if (msg) {
    msg.textContent = r.ok
      ? `serving ${modelId} on :${r.server.port} (registered in chat picker)`
      : ('serve failed: ' + (r.error || ''));
  }
  refresh();
}

export function open() {
  injectMarkup();
  const modal = document.getElementById('llama-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  refresh();
  if (_statusTimer) clearInterval(_statusTimer);
  _statusTimer = setInterval(refresh, 5000);
}

export function close() {
  const modal = document.getElementById('llama-modal');
  if (modal) modal.classList.add('hidden');
  if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
}

export function isVisible() {
  const modal = document.getElementById('llama-modal');
  return !!modal && !modal.classList.contains('hidden');
}

export function toggle() { if (isVisible()) close(); else open(); }

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectMarkup);
} else {
  injectMarkup();
}

const llamaHubModule = { open, close, isVisible, toggle };
export default llamaHubModule;
if (typeof window !== 'undefined') window.llamaHubModule = llamaHubModule;

/**
 * whatsapp.js - Painel de conversas WhatsApp via Evolution API
 * Estilo: modal flutuante com lista de chats + painel de mensagens
 */

const API_BASE = window.location.origin;
const WA_GREEN = '#25D366';
const WA_DARK  = '#128C7E';

let _modal = null;
let _currentJid = null;
let _chats = [];
let _refreshTimer = null;
let _open = false;

// ─── helpers ───────────────────────────────────────────────────────────────

function _ts(unix) {
  if (!unix) return '';
  const d = new Date(unix * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

function _name(chat) {
  if (chat.push_name) return chat.push_name;
  return chat.remote_jid.split('@')[0];
}

function _escape(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── API calls ─────────────────────────────────────────────────────────────

async function _fetchChats() {
  const r = await fetch(`${API_BASE}/api/whatsapp/chats?limit=60`);
  if (!r.ok) throw new Error('Falha ao carregar chats');
  return r.json();
}

async function _fetchMessages(jid) {
  const r = await fetch(`${API_BASE}/api/whatsapp/messages/${encodeURIComponent(jid)}?limit=60`);
  if (!r.ok) throw new Error('Falha ao carregar mensagens');
  return r.json();
}

async function _sendMessage(jid, text) {
  const r = await fetch(`${API_BASE}/api/whatsapp/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jid, text }),
  });
  if (!r.ok) throw new Error('Falha ao enviar mensagem');
  return r.json();
}

async function _fetchStatus() {
  const r = await fetch(`${API_BASE}/api/whatsapp/status`);
  if (!r.ok) return null;
  return r.json();
}

// ─── render ────────────────────────────────────────────────────────────────

function _renderChatList(chats) {
  const el = _modal.querySelector('#wa-chat-list');
  if (!chats.length) {
    el.innerHTML = '<div style="padding:20px;opacity:0.5;font-size:13px;text-align:center;">Nenhuma conversa ainda.<br>Aguardando mensagens…</div>';
    return;
  }
  el.innerHTML = chats.map(c => `
    <div class="wa-chat-item ${c.remote_jid === _currentJid ? 'wa-chat-active' : ''}"
         data-jid="${_escape(c.remote_jid)}" title="${_escape(c.remote_jid)}">
      <div class="wa-chat-avatar">${_escape(_name(c).charAt(0).toUpperCase())}</div>
      <div class="wa-chat-info">
        <div class="wa-chat-name">${_escape(_name(c))}</div>
        <div class="wa-chat-preview">${_escape((c.last_message || '').substring(0, 50))}</div>
      </div>
      <div class="wa-chat-meta">
        <div class="wa-chat-time">${_ts(c.last_ts)}</div>
        ${c.msg_count ? `<div class="wa-chat-count">${c.msg_count}</div>` : ''}
      </div>
    </div>
  `).join('');

  el.querySelectorAll('.wa-chat-item').forEach(item => {
    item.addEventListener('click', () => _openChat(item.dataset.jid));
  });
}

function _renderMessages(msgs, name) {
  const pane = _modal.querySelector('#wa-msg-pane');
  const header = _modal.querySelector('#wa-msg-header-name');
  if (header) header.textContent = name || 'Conversa';

  if (!msgs.length) {
    pane.innerHTML = '<div style="padding:20px;opacity:0.5;font-size:13px;text-align:center;">Sem mensagens.</div>';
    return;
  }

  pane.innerHTML = msgs.map(m => {
    const isMe = !!m.from_me;
    const sender = isMe ? 'Eu' : _escape(m.push_name || m.remote_jid.split('@')[0]);
    const content = _escape(m.content || '');
    return `
      <div class="wa-msg ${isMe ? 'wa-msg-out' : 'wa-msg-in'}">
        ${!isMe ? `<div class="wa-msg-sender">${sender}</div>` : ''}
        <div class="wa-msg-bubble">${content.replace(/\n/g,'<br>')}</div>
        <div class="wa-msg-time">${_ts(m.timestamp)}</div>
      </div>`;
  }).join('');

  // scroll to bottom
  pane.scrollTop = pane.scrollHeight;
}

async function _openChat(jid) {
  _currentJid = jid;
  // highlight active
  _modal.querySelectorAll('.wa-chat-item').forEach(i => {
    i.classList.toggle('wa-chat-active', i.dataset.jid === jid);
  });

  const msgArea = _modal.querySelector('#wa-messages-area');
  const emptyState = _modal.querySelector('#wa-empty-state');
  if (emptyState) emptyState.style.display = 'none';
  if (msgArea) msgArea.style.display = 'flex';

  const pane = _modal.querySelector('#wa-msg-pane');
  pane.innerHTML = '<div style="padding:20px;opacity:0.5;font-size:13px;text-align:center;">Carregando…</div>';

  try {
    const msgs = await _fetchMessages(jid);
    const chat = _chats.find(c => c.remote_jid === jid);
    _renderMessages(msgs, chat ? _name(chat) : jid.split('@')[0]);
  } catch (e) {
    pane.innerHTML = `<div style="padding:20px;color:var(--accent-error,#e06c75)">Erro: ${_escape(e.message)}</div>`;
  }
}

async function _loadChats(silent) {
  try {
    _chats = await _fetchChats();
    if (_modal) _renderChatList(_chats);
  } catch (e) {
    if (!silent) {
      const el = _modal?.querySelector('#wa-chat-list');
      if (el) el.innerHTML = `<div style="padding:16px;color:var(--accent-error,#e06c75);font-size:12px;">Erro: ${_escape(e.message)}</div>`;
    }
  }
}

async function _updateStatus() {
  const dot = _modal?.querySelector('#wa-status-dot');
  const label = _modal?.querySelector('#wa-status-label');
  if (!dot || !label) return;
  try {
    const s = await _fetchStatus();
    const state = s?.evolution_status?.instance?.connectionStatus || s?.evolution_status?.state || 'unknown';
    const ok = state === 'open' || state === 'CONNECTED';
    dot.style.color = ok ? WA_GREEN : '#e06c75';
    dot.textContent = '●';
    label.textContent = ok ? 'Conectado' : state;
  } catch (_) {
    dot.textContent = '●';
    dot.style.color = '#888';
    label.textContent = 'offline';
  }
}

// ─── modal HTML ────────────────────────────────────────────────────────────

function _buildModal() {
  const div = document.createElement('div');
  div.id = 'wa-modal';
  div.innerHTML = `
<div id="wa-modal-backdrop" style="position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:1200;display:flex;align-items:center;justify-content:center;">
  <div id="wa-modal-box" style="
    background:var(--panel,#1a1a1e);border:1px solid var(--border,#333);border-radius:10px;
    width:min(900px,95vw);height:min(650px,90vh);display:flex;flex-direction:column;
    box-shadow:0 8px 40px rgba(0,0,0,.6);overflow:hidden;position:relative;">

    <!-- header -->
    <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--bg,#111);border-bottom:1px solid var(--border,#333);flex-shrink:0;">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" style="flex-shrink:0;">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" stroke="${WA_GREEN}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span style="font-weight:600;font-size:14px;">WhatsApp</span>
      <span id="wa-status-dot" style="color:#888;font-size:10px;margin-left:4px;">●</span>
      <span id="wa-status-label" style="font-size:11px;opacity:0.6;">verificando…</span>
      <select id="wa-model-select" style="
        background:var(--input-bg,#222);color:var(--fg,#ddd);
        border:1px solid var(--border,#444);border-radius:6px;
        padding:3px 8px;font-size:11px;cursor:pointer;max-width:200px;
      " title="Modelo de IA para respostas automaticas no WhatsApp">
        <option value="">Carregando modelos…</option>
      </select>
      <div style="flex:1"></div>
      <button id="wa-refresh-btn" title="Atualizar" style="background:none;border:none;color:var(--fg,#ccc);cursor:pointer;padding:4px;opacity:0.7;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
      </button>
      <button id="wa-close-btn" style="background:none;border:none;color:var(--fg,#ccc);cursor:pointer;font-size:18px;padding:0 4px;line-height:1;">&#x2715;</button>
    </div>

    <!-- body -->
    <div style="display:flex;flex:1;overflow:hidden;">

      <!-- chat list -->
      <div id="wa-chat-list" style="
        width:280px;min-width:200px;flex-shrink:0;overflow-y:auto;
        border-right:1px solid var(--border,#333);background:var(--bg,#111);">
        <div style="padding:16px;opacity:0.5;font-size:12px;text-align:center;">Carregando…</div>
      </div>

      <!-- messages area -->
      <div style="flex:1;display:flex;flex-direction:column;overflow:hidden;">

        <!-- empty state -->
        <div id="wa-empty-state" style="flex:1;display:flex;align-items:center;justify-content:center;opacity:0.4;font-size:13px;flex-direction:column;gap:8px;">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" style="opacity:0.4;">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
          </svg>
          Selecione uma conversa
        </div>

        <!-- messages + input (hidden until chat selected) -->
        <div id="wa-messages-area" style="flex:1;display:none;flex-direction:column;overflow:hidden;">
          <!-- msg header -->
          <div style="padding:8px 14px;border-bottom:1px solid var(--border,#333);font-weight:600;font-size:13px;background:var(--bg,#111);flex-shrink:0;">
            <span id="wa-msg-header-name"></span>
          </div>
          <!-- messages -->
          <div id="wa-msg-pane" style="flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:4px;"></div>
          <!-- input -->
          <div style="display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border,#333);background:var(--bg,#111);flex-shrink:0;">
            <textarea id="wa-input" placeholder="Digite uma mensagem…" rows="2" style="
              flex:1;resize:none;background:var(--input-bg,#222);color:var(--fg,#ddd);
              border:1px solid var(--input-border,#444);border-radius:8px;
              padding:8px 10px;font-size:13px;font-family:inherit;line-height:1.4;"></textarea>
            <button id="wa-send-btn" style="
              background:${WA_GREEN};color:#fff;border:none;border-radius:8px;
              padding:0 18px;font-size:13px;font-weight:600;cursor:pointer;
              white-space:nowrap;">Enviar</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>`;

  // styles
  const style = document.createElement('style');
  style.textContent = `
    .wa-chat-item {
      display:flex;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;
      border-bottom:1px solid var(--border,#222);transition:background .15s;
    }
    .wa-chat-item:hover { background:var(--hover,rgba(255,255,255,.05)); }
    .wa-chat-active { background:var(--hover,rgba(255,255,255,.07)) !important; border-left:3px solid ${WA_GREEN}; padding-left:9px; }
    .wa-chat-avatar {
      width:36px;height:36px;border-radius:50%;background:${WA_DARK};
      display:flex;align-items:center;justify-content:center;
      font-size:15px;font-weight:600;color:#fff;flex-shrink:0;
    }
    .wa-chat-info { flex:1;min-width:0; }
    .wa-chat-name { font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
    .wa-chat-preview { font-size:11px;opacity:0.55;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px; }
    .wa-chat-meta { flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;gap:4px; }
    .wa-chat-time { font-size:10px;opacity:0.5; }
    .wa-chat-count { background:${WA_GREEN};color:#fff;border-radius:10px;padding:1px 6px;font-size:10px; }
    .wa-msg { max-width:72%;display:flex;flex-direction:column;margin-bottom:6px; }
    .wa-msg-in { align-self:flex-start; }
    .wa-msg-out { align-self:flex-end; }
    .wa-msg-bubble {
      padding:7px 10px;border-radius:8px;font-size:13px;line-height:1.45;word-break:break-word;
    }
    .wa-msg-in .wa-msg-bubble { background:var(--input-bg,#2a2a2e); }
    .wa-msg-out .wa-msg-bubble { background:${WA_DARK};color:#fff; }
    .wa-msg-sender { font-size:10px;font-weight:600;opacity:0.7;margin-bottom:2px;padding-left:2px; }
    .wa-msg-time { font-size:10px;opacity:0.45;margin-top:2px;padding:0 2px; }
    .wa-msg-in .wa-msg-time { text-align:left; }
    .wa-msg-out .wa-msg-time { text-align:right; }
  `;
  document.head.appendChild(style);
  document.body.appendChild(div);
  _modal = div;

  // events
  div.querySelector('#wa-close-btn').addEventListener('click', close);
  div.querySelector('#wa-modal-backdrop').addEventListener('click', e => {
    if (e.target === div.querySelector('#wa-modal-backdrop')) close();
  });
  div.querySelector('#wa-refresh-btn').addEventListener('click', () => {
    _loadChats();
    if (_currentJid) _openChat(_currentJid);
    _updateStatus();
  });

  const input = div.querySelector('#wa-input');
  const sendBtn = div.querySelector('#wa-send-btn');

  sendBtn.addEventListener('click', _doSend);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _doSend();
    }
  });
}

async function _doSend() {
  if (!_currentJid) return;
  const input = _modal.querySelector('#wa-input');
  const text = (input.value || '').trim();
  if (!text) return;
  input.value = '';

  const sendBtn = _modal.querySelector('#wa-send-btn');
  sendBtn.disabled = true;
  sendBtn.textContent = '…';

  try {
    await _sendMessage(_currentJid, text);
    await _openChat(_currentJid);  // reload messages
    await _loadChats(true);
  } catch (e) {
    alert('Erro ao enviar: ' + e.message);
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = 'Enviar';
  }
}


async function _loadModelSelector() {
  const sel = document.getElementById('wa-model-select');
  if (!sel) return;
  try {
    const [epRes, cfgRes] = await Promise.all([
      fetch('/api/whatsapp/models'),
      fetch('/api/whatsapp/config'),
    ]);
    const eps = epRes.ok ? await epRes.json() : [];
    const cfg = cfgRes.ok ? await cfgRes.json() : {};
    const currentModel = cfg.wa_model || '';
    const currentEpId = cfg.wa_endpoint_id || '';

    sel.innerHTML = '';
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = '— modelo automatico —';
    sel.appendChild(defaultOpt);

    const epList = Array.isArray(eps) ? eps : [];
    let found = false;
    for (const ep of epList) {
      const models = [...(ep.pinned_models || []), ...(ep.cached_models || [])];
      const seen = new Set();
      for (const m of models) {
        if (seen.has(m)) continue;
        seen.add(m);
        const opt = document.createElement('option');
        const epId = ep.id || ep.name || '';
        opt.value = JSON.stringify({ model: m, ep_id: epId });
        opt.textContent = (ep.name || epId) + ' / ' + m;
        if (m === currentModel && (!currentEpId || epId === currentEpId)) {
          opt.selected = true;
          found = true;
        }
        sel.appendChild(opt);
      }
    }

    if (!found && currentModel) {
      // modelo salvo nao esta mais disponivel — mostra mesmo assim
      const opt = document.createElement('option');
      opt.value = JSON.stringify({ model: currentModel, ep_id: currentEpId });
      opt.textContent = currentModel + ' (salvo)';
      opt.selected = true;
      sel.appendChild(opt);
    }

    sel.onchange = async () => {
      const val = sel.value;
      if (!val) {
        await fetch('/api/whatsapp/config', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ wa_model: '', wa_endpoint_id: '' })
        });
        return;
      }
      try {
        const { model, ep_id } = JSON.parse(val);
        await fetch('/api/whatsapp/config', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ wa_model: model, wa_endpoint_id: ep_id })
        });
      } catch(e) {}
    };
  } catch(e) {
    sel.innerHTML = '<option value="">Erro ao carregar</option>';
  }
}

// ─── public API ────────────────────────────────────────────────────────────

export function init() {
  // noop — setup happens on first open
}

export function open() {
  if (_open) { _modal && (_modal.style.display = ''); return; }
  _buildModal();
  _open = true;
  _loadChats();
  _updateStatus();
  _loadModelSelector();
  _refreshTimer = setInterval(() => {
    _loadChats(true);
    _updateStatus();
    if (_currentJid) _openChat(_currentJid);
  }, 30000);
}

export function close() {
  if (_modal) _modal.remove();
  _modal = null;
  _currentJid = null;
  _open = false;
  if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
}

export function toggle() {
  _open ? close() : open();
}

export default { init, open, close, toggle };
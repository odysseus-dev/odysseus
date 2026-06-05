// static/js/slashCommands.js
// Slash command handlers and dispatcher, extracted from chat.js

window.cancelActiveTour = function cancelActiveTour() {
  document.querySelectorAll('.odysseus-highlight, .odysseus-highlight-click')
    .forEach(e => e.classList.remove('odysseus-highlight', 'odysseus-highlight-click'));
  document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  document.getElementById('tour-tooltip')?.remove();
  document.body?.classList.remove('tour-active');
};

import Storage from './storage.js';
import uiModule from './ui.js';
import sessionModule from './sessions.js';
import modelsModule from './models.js';
import chatRenderer from './chatRenderer.js';
import spinnerModule from './spinner.js';
import themeModule from './theme.js';
import documentModule from './document.js';
import workspaceModule from './workspace.js';
import settingsModule from './settings.js';
import cookbookModule from './cookbook.js';
import { EVAL_PROMPTS } from './compare/index.js';

// ── Module state ──────────────────────────────────────────────────────

let API_BASE = '';
let setupMode = false;
let pendingSetupApiKey = '';
let pendingSetupProvider = null;
let setupIntroShown = false;

// External references set via initSlashCommands
let _addMessage = chatRenderer.addMessage;
let _hideWelcomeScreen = chatRenderer.hideWelcomeScreen;
let _isStreamingFn = () => false;  // callback to check streaming state

// API key patterns for provider auto-detection
const PROVIDER_PATTERNS = [
  { re: /^sk-ant-/,          name: 'Anthropic',  url: 'https://api.anthropic.com/v1' },
  { re: /^sk-or-/,           name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  { re: /^sk-proj-/,         name: 'OpenAI',     url: 'https://api.openai.com/v1' },
  { re: /^gsk_/,             name: 'Groq',       url: 'https://api.groq.com/openai/v1' },
  { re: /^AIza/,             name: 'Gemini',     url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  { re: /^xai-/,             name: 'xAI',        url: 'https://api.x.ai/v1' },
];
const SETUP_PROVIDER_URLS = {
  deepseek: { name: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
  openai: { name: 'OpenAI', url: 'https://api.openai.com/v1' },
  openrouter: { name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  ollama: { name: 'Ollama Cloud', url: 'https://ollama.com/api' },
  xai: { name: 'xAI', url: 'https://api.x.ai/v1' },
  anthropic: { name: 'Anthropic', url: 'https://api.anthropic.com/v1' },
  groq: { name: 'Groq', url: 'https://api.groq.com/openai/v1' },
  gemini: { name: 'Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  google: { name: 'Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
};
const SETUP_PROVIDER_NAMES = ['deepseek', 'openai', 'openrouter', 'ollama', 'xai', 'anthropic', 'groq', 'gemini'];
const SETUP_PROVIDER_HINT = SETUP_PROVIDER_NAMES.slice(0, -1).join(', ') + ', or ' + SETUP_PROVIDER_NAMES[SETUP_PROVIDER_NAMES.length - 1];
const SETUP_LOCAL_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>';
const SETUP_API_ICON = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-1px;margin-right:5px;"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>';
const SETUP_SETTINGS_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';

function _setupProviderFromInput(input) {
  const raw = (input || '').trim().toLowerCase().replace(/\s+/g, '');
  const aliases = {
    deepseekai: 'deepseek',
    deepseek: 'deepseek',
    openai: 'openai',
    chatgpt: 'openai',
    openrouter: 'openrouter',
    ollama: 'ollama',
    ollamacloud: 'ollama',
    anthropic: 'anthropic',
    claude: 'anthropic',
    groq: 'groq',
    gemini: 'gemini',
    google: 'gemini',
    xai: 'xai',
    grok: 'xai',
  };
  return SETUP_PROVIDER_URLS[aliases[raw] || raw] || null;
}

function _extractSetupProviderCredential(input) {
  const raw = (input || '').trim();
  if (!raw) return null;
  const providerAliases = [
    ['deepseek ai', 'deepseek'], ['deepseek', 'deepseek'],
    ['open router', 'openrouter'], ['openrouter', 'openrouter'],
    ['ollama cloud', 'ollama'], ['ollama', 'ollama'],
    ['open ai', 'openai'], ['openai', 'openai'], ['chatgpt', 'openai'],
    ['anthropic', 'anthropic'], ['claude', 'anthropic'],
    ['groq', 'groq'],
    ['google', 'gemini'], ['gemini', 'gemini'],
    ['x ai', 'xai'], ['xai', 'xai'], ['grok', 'xai'],
  ];
  for (const [alias, key] of providerAliases) {
    const re = new RegExp('(^|\\s|[,;:])(' + alias.replace(/\s+/g, '\\s+') + ')(?=$|\\s|[,;:])', 'i');
    const match = raw.match(re);
    if (!match) continue;
    const provider = SETUP_PROVIDER_URLS[key];
    const credential = raw.replace(match[0], match[1] || '').replace(/^[\s,;:]+|[\s,;:]+$/g, '');
    return { provider, credential };
  }
  return null;
}

function _normalizeSetupBaseUrl(raw) {
  let u = (raw || '').trim();
  u = u.replace(/^https?:\/(?!\/)/, m => m + '/');
  u = u.replace(/^htp:/, 'http:').replace(/^htps:/, 'https:');
  if (!/^https?:\/\//i.test(u)) u = 'http://' + u;
  u = u.replace(/\/+$/, '');
  u = u.replace(/\/v1\/(models|chat\/completions|completions|messages)\/?$/i, '/v1');
  u = u.replace(/\/(models|chat\/completions|completions|v1\/messages)\/?$/i, '');
  u = u.replace(/\/v1\/v1$/i, '/v1');
  if (!u.includes('api.') && !u.includes('openrouter') && !u.endsWith('/v1')) {
    try {
      const parsed = new URL(u);
      if (!parsed.pathname || parsed.pathname === '/') u += '/v1';
    } catch (_) {}
  }
  return u;
}

function _clearSetupGuideMessages() {
  Storage.remove('odysseus-setup-guide-messages');
}

async function _showSetupRetryPrompt() {
  _showSetupEndpointChoices();
  setupMode = 'endpoint-provider-first';
}

function _showSetupUserBubble(input, isUrl) {
  const masked = isUrl ? input : maskKey(input);
  _addMessage('user', masked);
  if (!isUrl) {
    const allBubbles = document.querySelectorAll('.msg-user .body');
    const lastBubble = allBubbles[allBubbles.length - 1];
    if (lastBubble) {
      lastBubble.style.filter = 'blur(4px)';
      lastBubble.style.userSelect = 'none';
      lastBubble.title = 'API key (hidden)';
      lastBubble.style.cursor = 'pointer';
      lastBubble.addEventListener('click', () => {
        lastBubble.style.filter = lastBubble.style.filter ? '' : 'blur(4px)';
      }, { once: false });
    }
  }
}

function _setupReply(text, remember = true) {
  return typewriterReply(text);
}

function _showSetupEndpointChoices() {
  const providers = SETUP_PROVIDER_NAMES.map(name =>
    '<span class="setup-clickable-provider" style="cursor:pointer;text-decoration:underline;margin-right:8px;" title="Click to setup ' + name + '">' + name + '</span>'
  ).join(' ');
  return slashReply(
    '<div class="setup-guide-no-censor" style="display:grid;gap:10px;">' +
      '<div>' +
        '<div>Quick start: add your first AI endpoint by pasting it in chat.</div>' +
      '</div>' +
      '<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:color-mix(in srgb,var(--bg) 88%,var(--fg) 12%);">' +
        '<div style="font-weight:700;margin-bottom:6px;">' + SETUP_LOCAL_ICON + 'Local setup</div>' +
        '<div>Paste endpoint URL in chat (example):</div>' +
        '<pre style="margin:4px 0 0;"><code class="setup-clickable-code" style="cursor:pointer;text-decoration:underline;" title="Click to fill in chat">http://localhost:11434/v1</code></pre>' +
        '<div style="margin-top:4px;">or</div>' +
        '<pre style="margin:2px 0 0;"><code class="setup-clickable-code" style="cursor:pointer;text-decoration:underline;" title="Click to fill in chat">http://llm-host.local:8000/v1</code></pre>' +
      '</div>' +
      '<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:color-mix(in srgb,var(--bg) 88%,var(--fg) 12%);">' +
        '<div style="font-weight:700;margin-bottom:6px;">' + SETUP_API_ICON + 'API setup</div>' +
        '<div>Paste provider name then API key (example):</div>' +
        '<pre style="margin:4px 0 0;"><code class="setup-clickable-code" style="cursor:pointer;text-decoration:underline;" title="Click to fill in chat">deepseek sk-...</code></pre>' +
        '<div style="margin-top:8px;font-size:1em;"><span>Supported providers:</span><br>' + providers + '</div>' +
      '</div>' +
    '</div>'
  );
}

function _showSetupEndpointChoicesStreamed(options = {}) {
  const blocks = [
    options.simple
      ? { kind: 'p', text: 'Paste in chat below either' }
      : { kind: 'p', html: '<strong>Quick start:</strong> add your first AI endpoint by pasting it in chat.' },
    { kind: 'heading', html: SETUP_LOCAL_ICON + 'Local setup' },
    { kind: 'p', text: 'Paste endpoint URL in chat (example):' },
    {
      kind: 'code',
      text: 'http://localhost:11434/v1',
      copyText: 'http://localhost:11434/v1',
    },
    { kind: 'p', text: 'or' },
    {
      kind: 'code',
      text: 'http://llm-host.local:8000/v1',
      copyText: 'http://llm-host.local:8000/v1',
    },
    { kind: 'heading', html: SETUP_API_ICON + 'API setup' },
    { kind: 'p', text: 'Paste provider name then API key (example):' },
    {
      kind: 'code',
      text: 'deepseek sk-...',
      copyText: 'deepseek sk-...',
    },
    { kind: 'p', html: '<strong>Supported providers:</strong><br>' + SETUP_PROVIDER_NAMES.map(name =>
      '<span class="setup-clickable-provider" style="cursor:pointer;text-decoration:underline;margin-right:8px;" title="Click to setup ' + name + '">' + name + '</span>'
    ).join(' ') },
  ];
  return typewriterBlocksReply(blocks, { gap: '4px', bodyClass: 'setup-guide-no-censor', interval: 3 });
}

async function _hasConfiguredModels() {
  const modelsBox = document.getElementById('models');
  if (modelsBox && modelsBox.querySelector('.models-row')) return true;
  try {
    const res = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin' });
    if (!res.ok) return false;
    const data = await res.json();
    return (data.items || []).some(item =>
      ((item.models || []).length > 0 || (item.models_extra || []).length > 0) && item.url
    );
  } catch {
    return false;
  }
}

function _setupProviderPrompt() {
  const chips = SETUP_PROVIDER_NAMES.map(name =>
    '<span style="font-weight:650;">' + name + '</span>'
  ).join('  ');
  slashReply('<b>Supported providers:</b><br>' + chips);
  return Promise.resolve();
}

// -----------------------------------------------------------------------
// Slash commands — execute directly without AI
// -----------------------------------------------------------------------

/** Persist a message to the current session (fire-and-forget) */
function _persistMsg(role, content, metadata) {
  const sid = sessionModule.getCurrentSessionId();
  if (!sid || !content) return;
  const payload = { role, content };
  if (metadata) payload.metadata = metadata;
  fetch(`${API_BASE}/api/session/${sid}/message`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(() => {});
}

function slashReply(text) {
  const chatBox = document.getElementById('chat-history');
  const div = document.createElement('div');
  div.className = 'msg msg-ai';
  const role = document.createElement('div');
  role.className = 'role';
  role.textContent = 'Odysseus';
  div.appendChild(role);
  const body = document.createElement('div');
  body.className = 'body';
  body.innerHTML = text;
  // Add copy buttons to any <pre> blocks
  body.querySelectorAll('pre').forEach(pre => {
    if (!pre.querySelector('.copy-code')) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy-code';
      btn.setAttribute('data-code', pre.textContent);
      btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      pre.appendChild(btn);
    }
  });
  div.appendChild(body);
  div.dataset.raw = body.textContent;
  div.appendChild(_slashFooter(div));
  chatBox.appendChild(div);
  uiModule.scrollHistory();
  _persistMsg('assistant', body.textContent, { source: 'slash' });
  return { el: div, body };
}

/** Minimal footer for slash replies: copy + dismiss */
function _slashFooter(msgEl) {
  const footer = document.createElement('div');
  footer.className = 'msg-footer';
  const actions = document.createElement('span');
  actions.className = 'msg-actions';
  // Copy
  const copyBtn = document.createElement('button');
  copyBtn.className = 'footer-copy-btn';
  copyBtn.type = 'button';
  copyBtn.title = 'Copy message';
  const _copySvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const _checkSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  copyBtn.innerHTML = _copySvg;
  copyBtn.onclick = (e) => {
    e.stopPropagation();
    uiModule.copyToClipboard(msgEl.dataset.raw || msgEl.querySelector('.body')?.textContent || '');
    copyBtn.innerHTML = _checkSvg;
    setTimeout(() => { copyBtn.innerHTML = _copySvg; }, 1500);
  };
  // Dismiss
  const delBtn = document.createElement('button');
  delBtn.className = 'msg-action-btn msg-delete-btn';
  delBtn.type = 'button';
  delBtn.title = 'Dismiss';
  delBtn.textContent = '\u2715';
  delBtn.onclick = (e) => { e.stopPropagation(); msgEl.remove(); };
  actions.appendChild(copyBtn);
  actions.appendChild(delBtn);
  footer.appendChild(actions);
  return footer;
}

/**
 * Typewriter-style reply that looks like a streamed AI response.
 * Returns a promise that resolves when the animation finishes.
 */
function typewriterReply(text, options = {}) {
  return new Promise(resolve => {
    const chatBox = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'msg msg-ai';
    const role = document.createElement('div');
    role.className = 'role';
    role.textContent = 'Odysseus';
    div.appendChild(role);
    const body = document.createElement('div');
    body.className = 'body';
    body.style.whiteSpace = 'pre-wrap';
    div.appendChild(body);
    chatBox.appendChild(div);
    uiModule.scrollHistory();
    let i = 0;
    const interval = Number.isFinite(options.interval) ? Math.max(1, options.interval) : 10;
    const iv = setInterval(() => {
      body.textContent = text.slice(0, ++i);
      uiModule.scrollHistory();
      if (i >= text.length) {
        clearInterval(iv);
        if (options.renderMarkdown) {
          requestAnimationFrame(() => {
            body.style.whiteSpace = '';
            body.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(text));
            if (markdownModule.renderMermaid) markdownModule.renderMermaid(body);
            uiModule.scrollHistory();
          });
        }
        div.dataset.raw = text;
        div.appendChild(_slashFooter(div));
        _persistMsg('assistant', text, { source: 'slash' });
        resolve(body);
      }
    }, interval);
  });
}

function typewriterBlocksReply(blocks, options = {}) {
  const plain = blocks.map(block => block.text || block.html?.replace(/<[^>]+>/g, '') || '').join('\n\n');
  return new Promise(resolve => {
    const chatBox = document.getElementById('chat-history');
    const div = document.createElement('div');
    div.className = 'msg msg-ai';
    const role = document.createElement('div');
    role.className = 'role';
    role.textContent = 'Odysseus';
    div.appendChild(role);
    const body = document.createElement('div');
    body.className = 'body';
    if (options.bodyClass) body.classList.add(options.bodyClass);
    body.style.display = 'grid';
    body.style.gap = options.gap || '8px';
    div.appendChild(body);
    chatBox.appendChild(div);
    uiModule.scrollHistory();

    let blockIndex = 0;
    let charIndex = 0;
    let current = null;
    let currentText = '';

    function makeBlock(block) {
      if (block.kind === 'heading') {
        const el = document.createElement('div');
        el.style.fontWeight = '700';
        return el;
      }
      if (block.kind === 'code') {
        const pre = document.createElement('pre');
        pre.style.margin = '0';
        const code = document.createElement('code');
        pre.appendChild(code);
        const useBtn = document.createElement('button');
        useBtn.type = 'button';
        useBtn.className = 'use-code';
        useBtn.title = 'Use in Chat';
        useBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7"/></svg>';
        const copyText = block.copyText || block.text || '';
        const useNow = (e) => {
          e.preventDefault();
          e.stopPropagation();
          e.stopImmediatePropagation();
          let text = copyText;
          if (text.includes('sk-...')) {
            text = text.replace('sk-...', 'sk-');
          }
          const messageInput = document.getElementById('message');
          if (messageInput) {
            messageInput.value = text;
            messageInput.dispatchEvent(new Event('input', { bubbles: true }));
            messageInput.focus();
            messageInput.setSelectionRange(text.length, text.length);
          }
          useBtn.classList.add('used');
          setTimeout(() => useBtn.classList.remove('used'), 1200);
        };
        useBtn.addEventListener('pointerdown', useNow);
        useBtn.addEventListener('click', useNow);
        pre.appendChild(useBtn);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'copy-code';
        btn.setAttribute('data-code', copyText);
        btn.title = 'Copy';
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
        const copyNow = (e) => {
          e.preventDefault();
          e.stopPropagation();
          e.stopImmediatePropagation();
          uiModule.copyToClipboard(copyText);
          btn.classList.add('copied');
          setTimeout(() => btn.classList.remove('copied'), 1200);
        };
        btn.addEventListener('pointerdown', copyNow);
        btn.addEventListener('click', copyNow);
        pre.appendChild(btn);
        return code;
      }
      const el = document.createElement('div');
      return el;
    }

    function appendContainer(block, target) {
      if (block.kind === 'code') body.appendChild(target.parentNode);
      else body.appendChild(target);
    }

    const interval = Number.isFinite(options.interval) ? Math.max(1, options.interval) : 10;
    const iv = setInterval(() => {
      if (!current) {
        const block = blocks[blockIndex];
        if (!block) {
          clearInterval(iv);
          div.dataset.raw = plain;
          div.appendChild(_slashFooter(div));
          _persistMsg('assistant', plain, { source: 'slash' });
          resolve(body);
          return;
        }
        current = makeBlock(block);
        currentText = block.text || block.html?.replace(/<[^>]+>/g, '') || '';
        charIndex = 0;
        appendContainer(block, current);
      }

      const block = blocks[blockIndex];
      charIndex += 1;
      const visible = currentText.slice(0, charIndex);
      if (block.html && charIndex >= currentText.length) current.innerHTML = block.html;
      else current.textContent = visible;
      uiModule.scrollHistory();

      if (charIndex >= currentText.length) {
        current = null;
        blockIndex += 1;
      }
    }, interval);
  });
}

/**
 * Typewriter effect into an existing element (for error messages during streaming).
 */
export function typewriterInto(el, text) {
  el.textContent = '';
  el.style.color = 'var(--red)';
  el.style.fontStyle = 'italic';
  let i = 0;
  const iv = setInterval(() => {
    el.textContent = text.slice(0, ++i);
    uiModule.scrollHistory();
    if (i >= text.length) clearInterval(iv);
  }, 10);
}

/**
 * Mask an API key for safe display: show first 6 and last 4 chars.
 */
function maskKey(key) {
  if (key.length <= 12) return key.slice(0, 4) + '...' + key.slice(-2);
  return key.slice(0, 6) + '...' + key.slice(-4);
}

/**
 * Detect provider from a pasted API key or URL.
 * Returns { base_url, api_key, name } or null if unrecognised.
 */
function detectProvider(input) {
  const trimmed = input.trim();
  // URL or bare IP/hostname — self-hosted endpoint
  // Matches: http://..., https://..., llm-host:8080, localhost:8000, myserver:8080/v1
  if (/^https?:\/\//i.test(trimmed) || /^(\d{1,3}\.){1,3}\d{1,3}(:\d+)?/i.test(trimmed) || /^(localhost|[\w.-]+:\d{2,5})/i.test(trimmed)) {
    let url = trimmed.replace(/\/+$/, '');
    if (!/^https?:\/\//i.test(url)) url = 'http://' + url;
    // Strip trailing path segments to get a clean base
    for (const suffix of ['/models', '/chat/completions', '/completions', '/v1/messages']) {
      if (url.endsWith(suffix)) url = url.slice(0, -suffix.length).replace(/\/+$/, '');
    }
    url = url.replace(/\/api\/(chat|tags|generate)\/?$/i, '/api');
    try {
      const parsed = new URL(url);
      if (parsed.hostname.endsWith('ollama.com')) url = 'https://ollama.com/api';
    } catch(e) {}
    // Add /v1 if bare host:port
    if (/^https?:\/\/[^/]+$/.test(url) && !url.includes('api.') && !url.includes('ollama.com')) url += '/v1';
    return { base_url: url, api_key: '', name: '' };
  }
  // Known key patterns
  for (const p of PROVIDER_PATTERNS) {
    if (p.re.test(input)) {
      return { base_url: p.url, api_key: input, name: p.name };
    }
  }
  // Generic sk- keys are ambiguous (OpenAI legacy, DeepSeek, and others).
  // Never guess a provider for a secret: asking avoids sending the key to
  // OpenRouter/OpenAI/etc. by mistake during setup probing.
  if (/^sk-[a-zA-Z0-9_\-]{20,}$/.test(input)) {
    return { ambiguous: true, api_key: input };
  }
  return null;
}

function setupChatUrlForEndpoint(detected) {
  const base = (detected.base_url || '').replace(/\/+$/, '');
  if (detected.name === 'Anthropic') return base.replace(/\/v1$/, '') + '/v1/messages';
  if (base.includes('ollama.com')) return 'https://ollama.com/api/chat';
  return base + '/chat/completions';
}

async function connectDetectedSetupEndpoint(detected) {
  const providerLabel = detected.name || 'custom endpoint';
  const chatBox = document.getElementById('chat-history');
  const spinnerDiv = document.createElement('div');
  spinnerDiv.className = 'msg msg-ai';
  const spinnerRole = document.createElement('div');
  spinnerRole.className = 'role';
  spinnerRole.textContent = 'Odysseus';
  spinnerDiv.appendChild(spinnerRole);
  const spinnerBody = document.createElement('div');
  spinnerBody.className = 'body';
  spinnerDiv.appendChild(spinnerBody);
  chatBox.appendChild(spinnerDiv);
  const setupSpinner = spinnerModule.create(`Detected ${providerLabel}. Connecting`, 'right', 'wave');
  spinnerBody.appendChild(setupSpinner.createElement());
  setupSpinner.start(150);
  uiModule.scrollHistory();

  const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/i.test(detected.base_url);

  try {
    const fd = new FormData();
    fd.append('base_url', detected.base_url);
    if (detected.api_key) fd.append('api_key', detected.api_key);
    if (detected.name) fd.append('name', detected.name);
    fd.append('require_models', 'true');
    if (!isLocal) fd.append('skip_probe', 'true');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    const res = await fetch(`${API_BASE}/api/model-endpoints`, { method: 'POST', body: fd, credentials: 'same-origin', signal: controller.signal });
    clearTimeout(timer);
    const data = await res.json();

    if (!res.ok) {
      setupSpinner.destroy();
      spinnerDiv.remove();
      setupMode = 'endpoint-provider-first';
      await typewriterReply(`Endpoint was not saved: ${data.detail || 'connection failed'}`);
      return;
    }

    const count = (data.models || []).length;
    if (count > 0) {
      setupSpinner.destroy();
      spinnerDiv.remove();
      await typewriterReply(`Found ${count} model${count > 1 ? 's' : ''} on ${providerLabel}. Starting a chat...`);
      if (modelsModule) await modelsModule.refreshModels(true);
      const firstModel = data.models[0];
      const chatUrl = setupChatUrlForEndpoint(detected);
      if (sessionModule) {
        await sessionModule.createDirectChat(chatUrl, firstModel, data.id);
      }
      await typewriterReply("You're all set. Type /tour for a walkthrough, or /setup endpoint to add another endpoint or key.");
      _clearSetupGuideMessages();
      return;
    }

    setupSpinner.destroy();
    spinnerDiv.remove();
    setupMode = 'endpoint-provider-first';
    await typewriterReply("Endpoint saved, but no models were found. Check the provider, key, or service status, then try /setup endpoint again.");
    if (modelsModule) modelsModule.refreshModels(true);
  } catch {
    setupSpinner.destroy();
    spinnerDiv.remove();
    setupMode = 'endpoint-provider-first';
    await typewriterReply("Endpoint setup failed before it could finish. Check the provider, key, or service status, then try /setup endpoint again.");
  }
}

/**
 * Handle setup mode input — user pasted an API key or URL.
 */
async function handleSetupInput(input) {
  // Show masked user bubble (don't display raw key)
  const isUrl = /^https?:\/\//i.test(input) || /^(\d{1,3}\.){1,3}\d{1,3}/i.test(input) || /^localhost/i.test(input);
  _showSetupUserBubble(input, isUrl);

  const paired = _extractSetupProviderCredential(input);
  if (paired && paired.provider) {
    if (paired.credential) {
      await connectDetectedSetupEndpoint({
        base_url: paired.provider.url,
        api_key: paired.credential,
        name: paired.provider.name,
      });
    } else {
      pendingSetupProvider = paired.provider;
      setupMode = 'endpoint-key-for-provider';
      await _setupReply(`Paste your ${paired.provider.name} API key now.`);
    }
    return;
  }

  const detected = detectProvider(input);
  if (!detected) {
    setupMode = false;
    await typewriterReply("Unrecognised format. Type /setup endpoint to try again.");
    return;
  }
  if (detected.ambiguous) {
    pendingSetupApiKey = detected.api_key;
    setupMode = 'endpoint-provider';
    await _setupProviderPrompt();
    return;
  }

  await connectDetectedSetupEndpoint(detected);
}

/**
 * Handle setup wizard sub-modes (endpoint, theme, features).
 */
async function handleSetupWizard(mode, input) {
  if (mode === 'endpoint-provider-first') {
    const detected = detectProvider(input);
    if (detected && !detected.ambiguous) {
      await handleSetupInput(input);
      return;
    }
    if (detected?.ambiguous) {
      pendingSetupApiKey = detected.api_key;
      setupMode = 'endpoint-provider';
      _showSetupUserBubble(input, false);
      await _setupProviderPrompt();
      return;
    }
    const paired = _extractSetupProviderCredential(input);
    const provider = paired?.provider || _setupProviderFromInput(input);
    if (!provider) {
      _addMessage('user', input);
      setupMode = false;
      await _setupReply('Provider not recognised. Try ' + SETUP_PROVIDER_HINT + '. Type /setup endpoint to try again.');
      return;
    }
    if (paired?.credential) {
      _showSetupUserBubble(input, false);
      await connectDetectedSetupEndpoint({ base_url: provider.url, api_key: paired.credential, name: provider.name });
      return;
    }
    _addMessage('user', provider.name);
    pendingSetupProvider = provider;
    setupMode = 'endpoint-key-for-provider';
    await _setupReply(`Paste your ${provider.name} API key.`);
    return;
  }

  if (mode === 'endpoint-key-for-provider') {
    const provider = pendingSetupProvider;
    pendingSetupProvider = null;
    if (!provider) {
      await _setupReply('No provider selected. Type /setup endpoint and choose a provider again.');
      return;
    }
    _showSetupUserBubble(input, /^https?:\/\//i.test(input));
    const paired = _extractSetupProviderCredential(input);
    const credential = paired?.credential || input.trim();
    await connectDetectedSetupEndpoint({ base_url: provider.url, api_key: credential, name: provider.name });
    return;
  }

  if (mode === 'endpoint-provider') {
    const raw = input.trim();
    const key = pendingSetupApiKey;
    pendingSetupApiKey = '';
    _addMessage('user', input);

    // User may have re-typed "provider key" together (matching the
    // original /setup prompt's example). Honor the freshly-pasted
    // key in that case — _setupProviderFromInput strips whitespace
    // and would otherwise see "deepseeksk-..." and bail.
    const paired = _extractSetupProviderCredential(raw);
    if (paired?.provider) {
      const credential = paired.credential || key;
      if (!credential) {
        await typewriterReply('No API key found. Type /setup endpoint and paste the key again.');
        return;
      }
      await connectDetectedSetupEndpoint({ base_url: paired.provider.url, api_key: credential, name: paired.provider.name });
      return;
    }

    if (!key) {
      await typewriterReply('No pending API key. Type /setup endpoint and paste the key again.');
      return;
    }
    let provider = _setupProviderFromInput(raw);
    if (!provider && /^https?:\/\//i.test(raw)) {
      provider = { name: '', url: raw };
    }
    if (!provider) {
      pendingSetupApiKey = '';
      setupMode = false;
      await typewriterReply('Provider not recognised. Try ' + SETUP_PROVIDER_HINT + '. Type /setup endpoint to try again.');
      return;
    }
    await connectDetectedSetupEndpoint({ base_url: provider.url, api_key: key, name: provider.name });
    return;
  }

  _addMessage('user', input);

  if (mode === 'theme') {
    const name = input.trim().toLowerCase();
    const tm = themeModule;
    const custom = tm && tm.getCustomThemes ? tm.getCustomThemes() : {};
    const colors = (tm && tm.THEMES && tm.THEMES[name]) || custom[name];
    if (tm && colors) {
      tm.applyColors(colors);
      tm.save(name, colors);
      await typewriterReply(`Theme switched to "${name}".`);
    } else if (tm && tm.applyTheme) {
      tm.applyTheme(name);
      await typewriterReply(`Theme switched to "${name}".`);
    } else {
      slashReply(`Unknown theme "${name}". Try /theme to see available themes.`);
    }
    return;
  }

  if (mode === 'features') {
    const name = input.trim().toLowerCase();
    try {
      const res = await fetch(`${API_BASE}/api/auth/features`, { credentials: 'same-origin' });
      const features = await res.json();
      if (name in features) {
        features[name] = !features[name];
        await fetch(`${API_BASE}/api/auth/features`, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(features),
        });
        await typewriterReply(`${name}: ${features[name] ? 'on' : 'off'}`);
      } else {
        await typewriterReply(`Unknown feature "${name}". Available: ${Object.keys(features).join(', ')}`);
      }
    } catch { await typewriterReply('Could not update features.'); }
    return;
  }

  await typewriterReply("I didn't understand that. Try /setup to see options.");
}

function _syncToggleUI(name, state) {
  const btnMap = { web: 'web-toggle-btn', bash: 'bash-toggle-btn', incognito: 'incognito-btn' };
  if (name === 'rag' && window._syncRagIndicator) {
    window._syncRagIndicator(state);
  } else if (name === 'research' && window._syncResearchIndicator) {
    window._syncResearchIndicator(state);
  } else {
    const btn = document.getElementById(btnMap[name]);
    if (btn) btn.classList.toggle('active', state);
  }
}

async function _quickToggle(name) {
  const toggleMap = { web: 'web-toggle', bash: 'bash-toggle', research: 'research-toggle' };
  const chk = document.getElementById(toggleMap[name]);
  if (!chk) return false;
  chk.checked = !chk.checked;
  _syncToggleUI(name, chk.checked);
  Storage.setToggle(name, chk.checked);
  await typewriterReply(`${name}: ${chk.checked ? 'on' : 'off'}`);
  return true;
}

async function _applyToggle(name, val) {
  const toggleMap = { web: 'web-toggle', bash: 'bash-toggle', research: 'research-toggle' };
  const chk = document.getElementById(toggleMap[name]);
  if (!chk) return;
  const newState = val === 'on' ? true : val === 'off' ? false : !chk.checked;
  chk.checked = newState;
  _syncToggleUI(name, newState);
  Storage.setToggle(name, newState);
  await typewriterReply(`${name}: ${newState ? 'on' : 'off'}`);
}

// ── Extracted handler functions ─────────────────────────────────────
// Each _cmd* receives (args, ctx) where args is the remaining tokens
// and ctx = { sid, esc }.  They return true to signal "handled".

/** Resolve a short ID or name to a full session UUID */
function _resolveSession(idOrName) {
  if (!idOrName || idOrName.length === 36) return idOrName;
  const sessions = sessionModule.getSessions();
  const q = idOrName.toLowerCase();
  const match = sessions.find(s => s.id.startsWith(q) || (s.name || '').toLowerCase() === q);
  return match ? match.id : idOrName;
}

async function _cmdSessionNew(args, ctx) {
  const name = args.join(' ') || `Chat ${new Date().toLocaleTimeString()}`;
  const sessions = sessionModule.getSessions();
  const curSess = sessions.find(s => s.id === ctx.sid);
  let endpointUrl = curSess ? curSess.endpoint_url || '' : '';
  let model = curSess ? curSess.model || '' : '';
  let endpointId = curSess ? curSess.endpoint_id || '' : '';

  // No current session — try default chat, then any recent session with a model
  if (!endpointUrl || !model) {
    try {
      const dcRes = await fetch(`${API_BASE}/api/default-chat`);
      const dc = await dcRes.json();
      if (dc.endpoint_url && dc.model) {
        endpointUrl = dc.endpoint_url;
        model = dc.model;
        endpointId = dc.endpoint_id || '';
      }
    } catch (e) { /* ignore */ }
  }
  if (!endpointUrl || !model) {
    const withModel = sessions.filter(s => s.endpoint_url && s.model && !s.archived);
    if (withModel.length > 0) {
      endpointUrl = withModel[0].endpoint_url;
      model = withModel[0].model;
      endpointId = withModel[0].endpoint_id || '';
    }
  }
  // Last resort — pull first model from /api/models
  if (!endpointUrl || !model) {
    try {
      const mRes = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin' });
      const mData = await mRes.json();
      for (const ep of (mData.items || [])) {
        if (ep.models && ep.models.length && ep.url) {
          endpointUrl = ep.url;
          model = ep.models[0];
          endpointId = ep.endpoint_id || '';
          break;
        }
      }
    } catch (e) { /* ignore */ }
  }
  if (!endpointUrl || !model) {
    slashReply('No model available — open the model picker and use the <code>+</code> button to add a model endpoint.');
    return true;
  }

  const fd = new FormData();
  fd.append('name', name);
  fd.append('endpoint_url', endpointUrl);
  fd.append('model', model);
  fd.append('skip_validation', 'true');
  if (endpointId) fd.append('endpoint_id', endpointId);
  const res = await fetch(`${API_BASE}/api/session`, { method: 'POST', body: fd, credentials: 'same-origin' });
  if (res.ok) {
    const data = await res.json();
    await sessionModule.loadSessions();
    await sessionModule.selectSession(data.id);
    _hideWelcomeScreen();
    const shortModel = (model || '').split('/').pop();
    await typewriterReply(`New session — ${shortModel || 'ready'}.`);
  } else { const err = await res.json().catch(() => null); slashReply('Failed to create session' + (err?.detail ? ': ' + ctx.esc(err.detail) : '')); }
  return true;
}

async function _cmdSessionDelete(args, ctx) {
  const raw = args.join(' ').trim();
  const force = /-(rf|fr)\b/.test(raw);
  const cleanArg = raw.replace(/\s*-(rf|fr)\b\s*/, '').trim();

  // /s del all  or  /s rm -rf
  if (cleanArg === 'all' || (force && !cleanArg)) {
    const sessions = sessionModule.getSessions().filter(s => !s.archived);
    const targets = force ? sessions : sessions.filter(s => !s.important);
    const skipped = sessions.length - targets.length;
    if (!targets.length) { slashReply('Nothing to delete' + (skipped ? ` (${skipped} starred)` : '')); return true; }
    let deleted = 0, failed = 0;
    for (const s of targets) {
      const res = await fetch(`${API_BASE}/api/session/${s.id}`, { method: 'DELETE', credentials: 'same-origin' });
      if (res.ok) deleted++; else failed++;
    }
    await sessionModule.loadSessions();
    let msg = `Deleted ${deleted} session${deleted !== 1 ? 's' : ''}`;
    if (skipped && !force) msg += `, kept ${skipped} starred`;
    if (failed) msg += `, ${failed} failed`;
    slashReply(msg);
    return true;
  }

  // Single session delete
  const target = _resolveSession(cleanArg) || ctx.sid;
  if (!target) { slashReply('No session to delete'); return true; }
  const sessions = sessionModule.getSessions();
  const sess = sessions.find(s => s.id === target);
  const label = sess ? `"${ctx.esc(sess.name || target.slice(0,8))}"` : target.slice(0,8);
  const res = await fetch(`${API_BASE}/api/session/${target}`, { method: 'DELETE', credentials: 'same-origin' });
  if (res.ok) {
    await typewriterReply(`Deleted ${label}`);
    await sessionModule.loadSessions();
  } else if (res.status === 403) {
    slashReply('Cannot delete a starred session — unstar it first, or use <code>/s rm -rf</code>');
  } else { const err = await res.json().catch(() => null); slashReply('Delete failed' + (err?.detail ? ': ' + ctx.esc(err.detail) : '')); }
  return true;
}

async function _cmdSessionArchive(args, ctx) {
  const target = _resolveSession(args[0]) || ctx.sid;
  if (!target) { slashReply('No session to archive'); return true; }
  const sessions = sessionModule.getSessions();
  const sess = sessions.find(s => s.id === target);
  const label = sess ? `"${ctx.esc(sess.name || target.slice(0,8))}"` : target.slice(0,8);
  if (sess && sess.archived) { await typewriterReply(`${label} is already archived`); return true; }
  const res = await fetch(`${API_BASE}/api/session/${target}/archive`, { method: 'POST', credentials: 'same-origin' });
  if (res.ok) { await typewriterReply(`Archived ${label}`); await sessionModule.loadSessions(); }
  else { slashReply('Archive failed'); }
  return true;
}

async function _cmdSessionRename(args, ctx) {
  const newName = args.join(' ');
  if (!newName) { slashReply('Usage: /rename New Name'); return true; }
  const fd = new FormData(); fd.append('name', newName);
  const res = await fetch(`${API_BASE}/api/session/${ctx.sid}`, { method: 'PATCH', body: fd, credentials: 'same-origin' });
  if (res.ok) { await typewriterReply(`Renamed to "${ctx.esc(newName)}"`); await sessionModule.loadSessions(); }
  else { slashReply('Rename failed'); }
  return true;
}

async function _cmdSessionImportant(args, ctx) {
  const fd = new FormData(); fd.append('important', 'true');
  await fetch(`${API_BASE}/api/session/${ctx.sid}/important`, { method: 'POST', body: fd, credentials: 'same-origin' });
  await typewriterReply('Session marked as important');
  return true;
}

async function _cmdSessionUnimportant(args, ctx) {
  const fd = new FormData(); fd.append('important', 'false');
  await fetch(`${API_BASE}/api/session/${ctx.sid}/important`, { method: 'POST', body: fd, credentials: 'same-origin' });
  await typewriterReply('Session unmarked');
  return true;
}

async function _cmdSessionFork(args, ctx) {
  if (!ctx.sid) { slashReply('No active session'); return true; }
  const keepCount = parseInt(args[0]) || 0;
  const res = await fetch(`${API_BASE}/api/session/${ctx.sid}/fork`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_count: keepCount })
  });
  if (res.ok) {
    const data = await res.json();
    await sessionModule.loadSessions();
    await sessionModule.selectSession(data.id);
    await typewriterReply(`Forked session (${data.kept || 0} messages)`);
  } else { slashReply('Fork failed'); }
  return true;
}

async function _cmdSessionTruncate(args, ctx) {
  if (!ctx.sid) { slashReply('No active session'); return true; }
  const keep = parseInt(args[0]);
  if (!keep || keep < 1) { slashReply('Usage: /truncate N — deletes older messages, keeps the last N'); return true; }
  const res = await fetch(`${API_BASE}/api/session/${ctx.sid}/truncate`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_count: keep })
  });
  if (res.ok) { await typewriterReply(`Truncated to ${keep} messages`); }
  else { slashReply('Truncate failed'); }
  return true;
}

async function _cmdSessionList(args, ctx) {
  const sessions = sessionModule.getSessions();
  const active = sessions.filter(s => !s.archived);
  if (!active.length) { slashReply('No active sessions'); return true; }
  const lines = active.slice(0, 40).map(s => {
    const current = s.id === ctx.sid ? ' <b>(current)</b>' : '';
    return `${ctx.esc(s.name || 'Untitled')} <span style="opacity:0.5">${s.id.slice(0,8)}</span>${current}`;
  });
  if (active.length > 40) lines.push(`... and ${active.length - 40} more`);
  slashReply(`<pre>${lines.join('\n')}</pre>`);
  return true;
}

async function _cmdSessionSwitch(args, ctx) {
  const query = args.join(' ').toLowerCase();
  if (!query) { slashReply('Usage: /switch &lt;name or id&gt;'); return true; }
  const sessions = sessionModule.getSessions();
  const match = sessions.find(s => !s.archived && (
    s.id.startsWith(query) || (s.name || '').toLowerCase().includes(query)
  ));
  if (match) {
    await sessionModule.selectSession(match.id);
    await typewriterReply(`Switched to "${ctx.esc(match.name)}"`);
  } else { await typewriterReply(`No session matching "${ctx.esc(query)}"`); }
  return true;
}

async function _cmdSessionSort(args, ctx) {
  slashReply('Auto-sorting sessions...');
  const res = await fetch(`${API_BASE}/api/sessions/auto-sort`, { method: 'POST', credentials: 'same-origin' });
  if (res.ok) {
    const data = await res.json();
    await sessionModule.loadSessions();
    // Handle skipped status
    if (data.status === 'skipped') {
      await typewriterReply(`Auto-sort skipped: ${data.reason || 'No sessions to sort'}`);
    } else {
      const del_msg = data.deleted_empty ? ` (${data.deleted_empty} empty deleted)` : '';
      await typewriterReply(`Sorted ${data.updated || 0} sessions into ${data.folders?.length || 0} folders${del_msg}`);
    }
  } else { slashReply('Auto-sort failed'); }
  return true;
}

async function _cmdSessionInfo(args, ctx) {
  if (!ctx.sid) { slashReply('No active session'); return true; }
  const sessions = sessionModule.getSessions();
  const s = sessions.find(ss => ss.id === ctx.sid);
  if (!s) { slashReply('Session not found'); return true; }
  slashReply(`<pre>Session: ${ctx.esc(s.name || 'Untitled')}
ID:      ${s.id}
Model:   ${ctx.esc(s.model || '?')}
Folder:  ${ctx.esc(s.folder || '(none)')}
Messages: ${s.message_count || '?'}
Created: ${s.created_at || '?'}</pre>`);
  return true;
}

async function _cmdSessionClear(args, ctx) {
  document.getElementById('chat-history').innerHTML = '';
  slashReply('Chat display cleared');
  return true;
}

async function _cmdSessionExport(args, ctx) {
  if (!ctx.sid) { slashReply('No active session'); return true; }
  // Parse linux-style: cat > file.json, cat > notes.txt, cat > chat.html
  let filename = '';
  let fmt = 'md';
  const raw = args.join(' ').trim();
  const redir = raw.match(/^>\s*(.+)/);
  if (redir) {
    filename = redir[1].trim();
    const ext = filename.split('.').pop().toLowerCase();
    if (['json','txt','html','md'].includes(ext)) fmt = ext;
  } else if (raw) {
    const a = raw.toLowerCase();
    if (['json','txt','html','md'].includes(a)) fmt = a;
  }
  const params = new URLSearchParams({ fmt });
  if (filename) params.set('filename', filename);
  window.open(`${API_BASE}/api/session/${ctx.sid}/export?${params}`, '_blank');
  slashReply(`Exporting as .${fmt}${filename ? ' → ' + filename : ''}...`);
  return true;
}

// ── Toggle handlers ──

async function _cmdToggleWeb(args, ctx) { const v = (args[0]||'').toLowerCase(); if (v === 'on' || v === 'off') _applyToggle('web', v); else _quickToggle('web'); return true; }
async function _cmdToggleBash(args, ctx) { const v = (args[0]||'').toLowerCase(); if (v === 'on' || v === 'off') _applyToggle('bash', v); else _quickToggle('bash'); return true; }
async function _cmdToggleRag(args, ctx) { const v = (args[0]||'').toLowerCase(); if (v === 'on' || v === 'off') _applyToggle('rag', v); else _quickToggle('rag'); return true; }
async function _cmdToggleResearch(args, ctx) { const v = (args[0]||'').toLowerCase(); if (v === 'on' || v === 'off') _applyToggle('research', v); else _quickToggle('research'); return true; }
async function _cmdToggleIncognito(args, ctx) {
  const sessions = sessionModule.getSessions();
  const sess = ctx.sid ? sessions.find(s => s.id === ctx.sid) : null;
  if (sess && sess.message_count > 0) {
    slashReply(`Can't toggle Nobody mode mid-conversation — start a new session first`);
    return true;
  }
  const v = (args[0]||'').toLowerCase();
  if (v === 'on' || v === 'off') _applyToggle('incognito', v); else _quickToggle('incognito');
  return true;
}

async function _cmdToggleDoc(args, ctx) {
  if (documentModule) {
    if (documentModule.isPanelOpen()) {
      documentModule.closePanel();
      const btn = document.getElementById('overflow-doc-btn');
      if (btn) btn.classList.remove('active');
      slashReply('Document editor: closed');
    } else {
      const sessionId = sessionModule && sessionModule.getCurrentSessionId();
      if (sessionId) {
        await documentModule.loadSessionDocs(sessionId);
      } else {
        await documentModule.ensureDocPanel();
      }
      const btn = document.getElementById('overflow-doc-btn');
      if (btn) btn.classList.add('active');
      slashReply('Document editor: opened');
    }
  } else { slashReply('Document module not available'); }
  return true;
}

// Workspace: confine the agent's file/shell tools to a folder. Not a boolean —
// show / set <path> / clear / pick (open the directory browser).
async function _cmdWorkspace(args, ctx) {
  const sub = (args[0] || '').toLowerCase();
  const rest = args.slice(1).join(' ').trim();
  const cur = workspaceModule.getWorkspace();
  if (!sub || sub === 'show' || sub === 'status' || sub === 'info') {
    slashReply(cur ? `Workspace: <code>${uiModule.esc(cur)}</code>` : 'No workspace set. <code>/workspace pick</code> or <code>/workspace set /path</code>.');
    return true;
  }
  if (sub === 'set' || sub === 'cd' || sub === 'use') {
    if (!rest) { slashReply('Usage: <code>/workspace set /absolute/path</code>'); return true; }
    workspaceModule.setWorkspace(rest);
    slashReply(`Workspace set: <code>${uiModule.esc(rest)}</code>`);
    return true;
  }
  if (sub === 'clear' || sub === 'off' || sub === 'none' || sub === 'unset') {
    workspaceModule.clearWorkspace();
    slashReply('Workspace cleared.');
    return true;
  }
  if (sub === 'pick' || sub === 'browse' || sub === 'open') {
    workspaceModule.openWorkspaceBrowser();
    return true;
  }
  slashReply('Usage: <code>/workspace</code> · <code>set /path</code> · <code>clear</code> · <code>pick</code>');
  return true;
}

async function _cmdToggleShow(args, ctx) {
  const name = (args[0] || '').toLowerCase();
  const val = (args[1] || '').toLowerCase();
  const toggleMap = { web: 'web-toggle', bash: 'bash-toggle', research: 'research-toggle' };
  if (!name || !toggleMap[name]) {
    const status = Object.keys(toggleMap).map(k => {
      const chk = document.getElementById(toggleMap[k]);
      return `  ${k}: ${chk && chk.checked ? 'on' : 'off'}`;
    }).join('\n');
    slashReply(`<pre>Toggles:\n${status}\n\nUsage: /toggle &lt;name&gt; [on|off]</pre>`);
    return true;
  }
  _applyToggle(name, val);
  return true;
}

async function _cmdToggleSidebar(args, ctx) {
  const sidebar = document.getElementById('sidebar');
  const iconRail = document.getElementById('icon-rail');
  if (!sidebar) { slashReply('Sidebar not found'); return true; }

  const sidebarHidden = sidebar.classList.contains('hidden');
  const railHidden = iconRail ? iconRail.classList.contains('rail-hidden') : true;

  // Determine target state
  const arg = (args[0] || '').toLowerCase();
  let target;
  if (arg === '1' || arg === 'full')  target = 'full';
  else if (arg === '2' || arg === 'mini') target = 'mini';
  else if (arg === '3' || arg === 'off' || arg === 'hide') target = 'off';
  else {
    // Cycle: full → mini → off → full
    if (!sidebarHidden) target = 'mini';
    else if (!railHidden) target = 'off';
    else target = 'full';
  }

  // Apply
  if (target === 'full') {
    sidebar.classList.remove('hidden');
    if (iconRail) iconRail.classList.remove('rail-hidden');
  } else if (target === 'mini') {
    sidebar.classList.add('hidden');
    if (iconRail) iconRail.classList.remove('rail-hidden');
  } else {
    sidebar.classList.add('hidden');
    if (iconRail) iconRail.classList.add('rail-hidden');
  }
  if (window.syncRailSide) window.syncRailSide();
  await typewriterReply(`Sidebar: ${target}`);
  return true;
}

// ── Settings ──

async function _cmdOpen(args, ctx) {
  const target = (args[0] || '').trim().toLowerCase();
  if (!target) {
    slashReply('Open what? Try /open Cookbook, /open Settings, /open Gallery, /open Notes, /open Tasks, /open Library, /open Research, or /open Compare.');
    return true;
  }
  const clickFirst = (...ids) => {
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) { el.click(); return true; }
    }
    return false;
  };
  try {
    if (target === 'cookbook' || target === 'cook') {
      if (cookbookModule && typeof cookbookModule.open === 'function') await cookbookModule.open({ tab: 'Download' });
      else clickFirst('tool-cookbook-btn', 'rail-cookbook');
      return true;
    }
    if (target === 'settings' || target === 'setting' || target === 'config') {
      if (settingsModule && typeof settingsModule.open === 'function') settingsModule.open();
      else clickFirst('user-bar-settings', 'rail-settings');
      return true;
    }
    const targets = {
      gallery: ['tool-gallery-btn', 'rail-gallery'],
      notes: ['tool-notes-btn', 'rail-notes'],
      tasks: ['tool-tasks-btn', 'rail-tasks'],
      library: ['tool-library-btn', 'rail-archive'],
      documents: ['tool-library-btn', 'rail-archive'],
      docs: ['tool-library-btn', 'rail-archive'],
      archive: ['tool-library-btn', 'rail-archive'],
      brain: ['tool-memory-btn', 'rail-memory'],
      memory: ['tool-memory-btn', 'rail-memory'],
      memories: ['tool-memory-btn', 'rail-memory'],
      research: ['tool-research-btn', 'rail-research'],
      compare: ['tool-compare-btn', 'rail-compare'],
      theme: ['tool-theme-btn', 'rail-theme'],
    };
    const ids = targets[target];
    if (ids && clickFirst(...ids)) return true;
  } catch (e) {
    console.warn('/open failed', target, e);
  }
  slashReply(`I don't know how to open "${ctx.esc(target)}" yet.`);
  return true;
}

async function _cmdToolPanel(tool, args, ctx) {
  const target = String(tool || '').toLowerCase();
  const rest = (args || []).join(' ').trim();
  if (target === 'cookbook') {
    const sub = (args[0] || '').toLowerCase();
    if (sub === 'serve') {
      const query = args.slice(1).join(' ').trim();
      try {
        if (cookbookModule && typeof cookbookModule.open === 'function') {
          await cookbookModule.open({ tab: 'Serve', serveSearch: query });
          if (query) {
            try {
              const mod = await import('./cookbookServe.js');
              if (mod && typeof mod.openServePanelForRepo === 'function') {
                setTimeout(() => { mod.openServePanelForRepo(query).catch(() => {}); }, 80);
              }
            } catch (_) {}
          }
        } else {
          document.getElementById('tool-cookbook-btn')?.click();
        }
      } catch (e) {
        slashReply(`Could not open Cookbook Serve${e?.message ? `: ${ctx.esc(e.message)}` : ''}`);
      }
      return true;
    }
    if (sub === 'download' || sub === 'scan') {
      await cookbookModule?.open?.({ tab: 'Download', usecase: args.slice(1).join(' ').trim() || undefined });
      return true;
    }
    await cookbookModule?.open?.({ tab: 'Download', usecase: rest || undefined });
    return true;
  }
  if (target === 'email') {
    const btn = document.getElementById('rail-email') || document.getElementById('email-section-title');
    if (btn) btn.click();
    else slashReply('Could not open Email.');
    return true;
  }
  if (target === 'settings') {
    if (settingsModule && typeof settingsModule.open === 'function') settingsModule.open(rest || undefined);
    else document.getElementById('user-bar-settings')?.click();
    return true;
  }
  return _cmdOpen([target], ctx);
}

async function _cmdSettings(args, ctx) {
  // Opens the Settings modal — primarily useful when the user has hidden the
  // Settings cog in Appearance and needs a way back in.
  const tab = (args[0] || '').toLowerCase() || undefined;
  try {
    if (settingsModule && typeof settingsModule.open === 'function') {
      settingsModule.open(tab);
    } else {
      // Fallback: click the cog directly if the module isn't loaded.
      const cog = document.getElementById('user-bar-settings');
      if (cog) cog.click();
    }
  } catch (e) {
    console.warn('/settings open failed', e);
    slashReply('Could not open Settings.');
    return true;
  }
  return true;
}

// ── Theme ──

async function _cmdTheme(args, ctx) {
  const tm = themeModule;
  const sub = (args[0] || '').toLowerCase();
  const custom = tm && tm.getCustomThemes ? tm.getCustomThemes() : {};
  const customNames = Object.keys(custom);
  const presetNames = tm && tm.THEMES ? Object.keys(tm.THEMES) : [];
  if (!sub || !tm || !tm.THEMES) {
    const customLabel = customNames.length ? `\nCustom: ${customNames.join(', ')}` : '';
    slashReply(`Usage:\n  /theme &lt;name&gt; — Apply a preset or custom theme\n  /theme save &lt;name&gt; — Save current colors as a custom theme\n  /theme delete &lt;name&gt; — Delete a custom theme\nPresets: ${presetNames.join(', ')}${customLabel}`);
    return true;
  }
  if (sub === 'save' && args[1]) {
    const saveName = args[1].toLowerCase().replace(/\s+/g, '-');
    if (tm.THEMES[saveName]) { slashReply('Cannot overwrite a built-in theme.'); return true; }
    const s = tm.getSaved();
    const colors = s ? s.colors : tm.THEMES.dark;
    tm.saveCustomTheme(saveName, colors);
    tm.save(saveName, colors);
    await typewriterReply(`Custom theme "${saveName}" saved`);
    return true;
  }
  if (sub === 'delete' || sub === 'del' || sub === 'rm' || sub === 'remove') {
    if (!args[1]) { slashReply('Usage: /theme delete &lt;name&gt; or /theme delete all'); return true; }
    const delArg = args[1].toLowerCase().replace(/\s+/g, '-');
    if (delArg === 'all') {
      if (!customNames.length) { slashReply('No custom themes to delete'); return true; }
      for (const n of customNames) { if (tm.deleteCustomTheme) tm.deleteCustomTheme(n); }
      await typewriterReply(`Deleted ${customNames.length} custom theme${customNames.length !== 1 ? 's' : ''}`);
      return true;
    }
    if (tm.deleteCustomTheme) tm.deleteCustomTheme(delArg);
    await typewriterReply(`Theme "${delArg}" deleted`);
    return true;
  }
  const name = sub;
  const colors = tm.THEMES[name] || custom[name];
  if (!colors) {
    const customLabel = customNames.length ? ` | Custom: ${customNames.join(', ')}` : '';
    slashReply(`Unknown theme "${name}". Available: ${presetNames.join(', ')}${customLabel}`);
    return true;
  }
  tm.applyColors(colors);
  tm.save(name, colors);
  const grid = document.getElementById('themeGrid');
  if (grid) {
    grid.querySelectorAll('.theme-swatch').forEach(s => s.classList.remove('active'));
    const sw = grid.querySelector(`[data-theme="${name}"]`);
    if (sw) sw.classList.add('active');
  }
  await typewriterReply(`Theme: ${name}`);
  return true;
}

// ── Models ──

async function _cmdModels(args, ctx) {
  slashReply('Fetching models...');
  const res = await fetch(`${API_BASE}/api/models`, { credentials: 'same-origin' });
  const data = await res.json();
  let lines = [];
  (data.items || []).forEach(ep => {
    lines.push(`<b>${ctx.esc(ep.endpoint_name || ep.url)}</b>`);
    (ep.models || []).forEach(m => lines.push(`  ${ctx.esc(m)}`));
  });
  slashReply(`<pre>${lines.join('\n') || 'No models found'}</pre>`);
  return true;
}

// ── Memory ──

async function _cmdMemoryList(args, ctx) {
  const res = await fetch(`${API_BASE}/api/memory`, { credentials: 'same-origin' });
  const data = await res.json();
  const mems = data.memory || [];
  if (!mems.length) { slashReply('No memories stored'); return true; }
  const lines = mems.slice(0, 40).map(m => `[${m.category||'fact'}] ${m.id.slice(0,8)} — ${ctx.esc(m.text)}`);
  if (mems.length > 40) lines.push(`... and ${mems.length - 40} more`);
  slashReply(`<pre>${lines.join('\n')}</pre>`);
  return true;
}

async function _cmdMemoryAdd(args, ctx) {
  const text = args.join(' ');
  if (!text) { slashReply('Usage: /memory add Your text here'); return true; }
  const res = await fetch(`${API_BASE}/api/memory/add`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, category: 'fact', source: 'user' })
  });
  if (res.ok) await typewriterReply(`Memory added: ${ctx.esc(text)}`);
  else slashReply('Failed to add memory');
  return true;
}

async function _cmdMemoryDelete(args, ctx) {
  const raw = args.join(' ').trim();
  const force = /-(rf|fr)\b/.test(raw);
  const cleanArg = raw.replace(/\s*-(rf|fr)\b\s*/, '').trim();

  if (cleanArg === 'all' || (force && !cleanArg)) {
    const listRes = await fetch(`${API_BASE}/api/memory`, { credentials: 'same-origin' });
    const listData = await listRes.json();
    const mems = listData.memory || [];
    if (!mems.length) { slashReply('No memories to delete'); return true; }
    if (!force) {
      slashReply(`This will delete all ${mems.length} memories. Use <code>/m rm -rf</code> to confirm.`);
      return true;
    }
    let deleted = 0;
    for (const m of mems) {
      const res = await fetch(`${API_BASE}/api/memory/${m.id}`, { method: 'DELETE', credentials: 'same-origin' });
      if (res.ok) deleted++;
    }
    await typewriterReply(`Deleted ${deleted}/${mems.length} memories`);
    return true;
  }

  let memId = cleanArg;
  if (!memId) { slashReply('Usage: /memory delete &lt;id&gt; or /m rm -rf to wipe all'); return true; }
  // Resolve short ID to full UUID and get preview
  let preview = memId.slice(0, 8);
  if (memId.length < 36) {
    const listRes = await fetch(`${API_BASE}/api/memory`, { credentials: 'same-origin' });
    const listData = await listRes.json();
    const match = (listData.memory || []).find(m => m.id.startsWith(memId));
    if (match) { memId = match.id; preview = match.text.slice(0, 50); }
  }
  const res = await fetch(`${API_BASE}/api/memory/${memId}`, { method: 'DELETE', credentials: 'same-origin' });
  if (res.ok) await typewriterReply(`Deleted: ${preview}${preview.length >= 50 ? '...' : ''}`);
  else slashReply('Delete failed — check the ID');
  return true;
}

async function _cmdMemorySearch(args, ctx) {
  const query = args.join(' ');
  if (!query) { slashReply('Usage: /memory search query'); return true; }
  const fd = new FormData(); fd.append('query', query);
  const res = await fetch(`${API_BASE}/api/memory/search`, { method: 'POST', body: fd, credentials: 'same-origin' });
  const data = await res.json();
  const mems = data.memories || [];
  if (!mems.length) { await typewriterReply(`No memories matching "${ctx.esc(query)}"`); return true; }
  const lines = mems.map(m => `[${m.category||'fact'}] ${ctx.esc(m.text)}`);
  slashReply(`<pre>${lines.join('\n')}</pre>`);
  return true;
}

// ── Note (quick Notes shortcut) ──

async function _cmdNote(args, ctx) {
  const text = args.join(' ');
  if (!text) { slashReply('Usage: /note Your note here'); return true; }
  const res = await fetch(`${API_BASE}/api/notes`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: text, content: '', note_type: 'note', source: 'slash' })
  });
  if (res.ok) await typewriterReply(`Note added: ${ctx.esc(text)}`);
  else slashReply('Failed to save note');
  return true;
}

// ── Todo / Remind / Event ───────────────────────────────────────────────
// Quick deterministic wrappers over /api/notes and /api/calendar/events.
// They never involve the LLM — they parse the string locally and hit the
// API directly, so they work instantly regardless of chat/agent mode.

function _pad2(n) { return String(n).padStart(2, '0'); }

/** Local-time ISO-8601 string (no Z, no offset) — what the calendar API wants. */
function _toLocalIso(d) {
  return `${d.getFullYear()}-${_pad2(d.getMonth()+1)}-${_pad2(d.getDate())}T${_pad2(d.getHours())}:${_pad2(d.getMinutes())}:00`;
}

/**
 * Parse a natural-language time spec from the *start* of the string.
 * Returns { date: Date, rest: string } or null if nothing matched.
 * Supported:
 *   "in 30m" / "in 2h" / "in 1d"
 *   "today 14:00" / "tomorrow 9am"
 *   "HH:MM" / "9am" / "9pm"   (today, or tomorrow if already past)
 *   "YYYY-MM-DD HH:MM"
 * Swallows common stop words: "me", "at", "on", "to".
 */
function _parseTimeSpec(input) {
  let s = (input || '').trim().replace(/^(me\s+)/i, '').trim();
  const now = new Date();

  // "in 30m" / "in 2h" / "in 1d"
  let m = s.match(/^in\s+(\d+)\s*(m|min|mins|minutes|h|hr|hrs|hours|d|day|days)\b\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const n = parseInt(m[1], 10);
    const unit = m[2].toLowerCase();
    const d = new Date(now);
    if (unit.startsWith('m')) d.setMinutes(d.getMinutes() + n);
    else if (unit.startsWith('h')) d.setHours(d.getHours() + n);
    else d.setDate(d.getDate() + n);
    return { date: d, rest: m[3].trim() };
  }

  // "YYYY-MM-DD HH:MM"
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T\s]+(\d{1,2}):(\d{2})\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5]);
    return { date: d, rest: m[6].trim() };
  }

  // "today HH:MM" / "tomorrow HH:MM" / "today 9am" / "tomorrow 9pm"
  m = s.match(/^(today|tomorrow)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(now);
    if (m[1].toLowerCase() === 'tomorrow') d.setDate(d.getDate() + 1);
    let hh = parseInt(m[2], 10);
    const mm = m[3] ? parseInt(m[3], 10) : 0;
    const mer = (m[4] || '').toLowerCase();
    if (mer === 'pm' && hh < 12) hh += 12;
    if (mer === 'am' && hh === 12) hh = 0;
    if (hh > 23 || mm > 59) return null;
    d.setHours(hh, mm, 0, 0);
    return { date: d, rest: m[5].trim() };
  }

  // bare "HH:MM" / "9am" / "9pm" / "at HH:MM" — today, or tomorrow if past
  m = s.match(/^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b\s*(?:to\s+)?(.*)$/i);
  if (m) {
    const d = new Date(now);
    let hh = parseInt(m[1], 10);
    const mm = m[2] ? parseInt(m[2], 10) : 0;
    const mer = (m[3] || '').toLowerCase();
    if (mer === 'pm' && hh < 12) hh += 12;
    if (mer === 'am' && hh === 12) hh = 0;
    // Require a valid hour/minute and either a minute field or am/pm to
    // avoid eating plain numbers like "3 apples".
    if (hh > 23 || mm > 59) return null;
    if (m[2] == null && !mer) return null;
    d.setHours(hh, mm, 0, 0);
    if (d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1);
    return { date: d, rest: m[4].trim() };
  }

  return null;
}

async function _cmdTodo(args, ctx) {
  const sub = (args[0] || '').toLowerCase();
  if (sub === 'list' || sub === 'ls') {
    const res = await fetch(`${API_BASE}/api/notes?note_type=note`, { credentials: 'same-origin' });
    if (!res.ok) { slashReply('Failed to load todos'); return true; }
    const data = await res.json();
    const items = (data.notes || data || []).filter(n => !n.archived).slice(0, 30);
    if (!items.length) { slashReply('No todos'); return true; }
    const lines = items.map(n => `• ${ctx.esc(n.title || n.content || '').slice(0, 80)}`);
    slashReply(`<pre>${lines.join('\n')}</pre>`);
    return true;
  }
  // Treat everything after /todo (or after /todo add) as the todo text
  const rest = (sub === 'add' ? args.slice(1) : args).join(' ').trim();
  if (!rest) { slashReply('Usage: /todo Your task here  ·  /todo list'); return true; }
  const res = await fetch(`${API_BASE}/api/notes`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: rest, note_type: 'note', source: 'slash', label: 'todo' }),
  });
  if (res.ok) await typewriterReply(`Todo added: ${ctx.esc(rest)}`);
  else slashReply('Failed to add todo');
  return true;
}

async function _cmdEvent(args, ctx) {
  const raw = args.join(' ').trim();
  if (!raw) { slashReply('Usage: /event tomorrow 14:00 Title  ·  /event in 30m Title  ·  /event 2026-04-20 15:00 Title'); return true; }
  const parsed = _parseTimeSpec(raw);
  if (!parsed || !parsed.rest) { slashReply(`Could not parse time from: ${ctx.esc(raw)}`); return true; }
  const start = parsed.date;
  const end = new Date(start.getTime() + 60 * 60 * 1000); // default 1h block
  const body = {
    summary: parsed.rest,
    dtstart: _toLocalIso(start),
    dtend: _toLocalIso(end),
    all_day: false,
  };
  const res = await fetch(`${API_BASE}/api/calendar/events`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    await typewriterReply(`Event: ${ctx.esc(parsed.rest)} — ${start.toLocaleString()}`);
  } else {
    const err = await res.text().catch(() => '');
    slashReply(`Failed to create event${err ? `: ${ctx.esc(err.slice(0,200))}` : ''}`);
  }
  return true;
}

// ── Shell (user command execution) ──

async function _cmdShell(args, ctx) {
  const cmd = args.join(' ');
  if (!cmd) { slashReply('Usage: /sh command'); return true; }
  slashReply(`<pre>$ ${ctx.esc(cmd)}\nRunning...</pre>`);
  try {
    const res = await fetch(`${API_BASE}/api/shell/exec`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    });
    const data = await res.json();
    let out = '';
    if (data.stdout) out += data.stdout;
    if (data.stderr) out += (out ? '\n' : '') + data.stderr;
    if (!out) out = '(no output)';
    const code = data.exit_code != null ? data.exit_code : '?';
    slashReply(`<pre>$ ${ctx.esc(cmd)}\n${ctx.esc(out)}\n[exit ${code}]</pre>`);
  } catch (e) {
    slashReply(`<pre>$ ${ctx.esc(cmd)}\nError: ${ctx.esc(e.message)}</pre>`);
  }
  return true;
}

// ── RAG ──

async function _cmdRagList(args, ctx) {
  const res = await fetch(`${API_BASE}/api/personal`, { credentials: 'same-origin' });
  const data = await res.json();
  let lines = [];
  if (data.directories && data.directories.length) {
    lines.push('<b>Directories:</b>');
    data.directories.forEach(d => lines.push(`  ${ctx.esc(typeof d === 'string' ? d : d.path || JSON.stringify(d))}`));
  }
  if (data.files && data.files.length) {
    lines.push(`<b>Files (${data.files.length}):</b>`);
    data.files.slice(0, 30).forEach(f => lines.push(`  ${ctx.esc(f.name || f.path || String(f))}`));
    if (data.files.length > 30) lines.push(`  ... and ${data.files.length - 30} more`);
  }
  slashReply(lines.length ? `<pre>${lines.join('\n')}</pre>` : 'No files or directories indexed');
  return true;
}

async function _cmdRagAdd(args, ctx) {
  const dir = args.join(' ');
  if (!dir) { slashReply('Usage: /rag add /path/to/directory'); return true; }
  const res = await fetch(`${API_BASE}/api/personal/add_directory`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ directory: dir })
  });
  if (res.ok) {
    const data = await res.json();
    await typewriterReply(`Indexed "${ctx.esc(dir)}" (${data.indexed_count || 0} files)`);
  } else { slashReply('Failed to add directory'); }
  return true;
}

async function _cmdRagRemove(args, ctx) {
  const raw = args.join(' ').trim();
  const force = /-(rf|fr)\b/.test(raw);
  const cleanArg = raw.replace(/\s*-(rf|fr)\b\s*/, '').trim();

  if (cleanArg === 'all' || (force && !cleanArg)) {
    const listRes = await fetch(`${API_BASE}/api/personal`, { credentials: 'same-origin' });
    const listData = await listRes.json();
    const dirs = listData.directories || [];
    if (!dirs.length) { slashReply('No RAG directories to remove'); return true; }
    if (!force) {
      slashReply(`This will remove all ${dirs.length} directories from RAG. Use <code>/rag rm -rf</code> to confirm.`);
      return true;
    }
    let removed = 0;
    for (const d of dirs) {
      const path = typeof d === 'string' ? d : d.path || '';
      if (!path) continue;
      const res = await fetch(`${API_BASE}/api/personal/remove_directory?directory=${encodeURIComponent(path)}`, { method: 'DELETE', credentials: 'same-origin' });
      if (res.ok) removed++;
    }
    await typewriterReply(`Removed ${removed}/${dirs.length} directories from RAG`);
    return true;
  }

  const dir = cleanArg;
  if (!dir) { slashReply('Usage: /rag remove /path or /rag rm -rf to remove all'); return true; }
  const res = await fetch(`${API_BASE}/api/personal/remove_directory?directory=${encodeURIComponent(dir)}`, {
    method: 'DELETE', credentials: 'same-origin'
  });
  if (res.ok) await typewriterReply(`Removed "${ctx.esc(dir)}" from RAG`);
  else slashReply('Failed to remove directory');
  return true;
}

// ── Web Search ──

async function _cmdWebSearch(args, ctx) {
  const query = args.join(' ');
  if (!query) { slashReply('Usage: /search &lt;query&gt;'); return true; }
  // Enable web toggle for this search, then fall through to normal chat
  const chk = document.getElementById('web-toggle');
  const btn = document.getElementById('web-toggle-btn');
  if (chk) chk.checked = true;
  if (btn) btn.classList.add('active');
  uiModule.el('message').value = query;
  return false; // fall through to normal chat submit
}

// ── Search ──

async function _cmdSearch(args, ctx) {
  const query = args.join(' ');
  if (!query) { slashReply('Usage: /find &lt;query&gt;'); return true; }
  const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=20`, { credentials: 'same-origin' });
  if (res.ok) {
    const data = await res.json();
    const results = Array.isArray(data) ? data : (data.results || []);
    if (!results.length) { slashReply(`No results for "${ctx.esc(query)}"`); return true; }
    const lines = results.slice(0, 20).map(r => {
      const name = ctx.esc(r.session_name || r.name || 'Untitled');
      const snippet = ctx.esc((r.content_snippet || r.content || r.snippet || '').slice(0, 100));
      const sid = r.session_id || '';
      return `<a href="#${sid}" style="color:var(--red);text-decoration:none">${name}</a>  ${snippet}`;
    });
    slashReply(`<pre>${lines.join('\n')}</pre>`);
  } else { slashReply('Search failed'); }
  return true;
}

// ── Stats ──

async function _cmdStats(args, ctx) {
  const res = await fetch(`${API_BASE}/api/db/stats`, { credentials: 'same-origin' });
  if (res.ok) {
    const d = await res.json();
    slashReply(`<pre>Sessions:  ${d.sessions || '?'}
Messages:  ${d.messages || '?'}
Memories:  ${d.memories || '?'}
Documents: ${d.documents || '?'}
Uploads:   ${d.uploads || '?'}</pre>`);
  } else { slashReply('Failed to fetch stats'); }
  return true;
}

// ── Context compaction ──

async function _cmdCompact(args, ctx) {
  if (!ctx.sid) { slashReply('No active chat to compact'); return true; }
  const reply = slashReply('Compacting context ');
  const compactSpinner = spinnerModule.create('Compacting context', 'inline', 'whirlpool');
  if (reply?.body) {
    const spinnerEl = compactSpinner.createElement();
    spinnerEl.style.position = 'relative';
    spinnerEl.style.top = '2px';
    reply.body.appendChild(spinnerEl);
    compactSpinner.start(110);
  }
  const res = await fetch(`${API_BASE}/api/session/${encodeURIComponent(ctx.sid)}/compact`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
  });
  compactSpinner.destroy();
  if (res.ok) {
    const d = await res.json();
    slashReply(`Conversation compacted. Summarized ${d.summarized || 0} older messages, kept ${d.kept || 0} recent messages.`);
    if (sessionModule?.selectSession) await sessionModule.selectSession(ctx.sid);
  } else {
    let detail = 'Compaction failed';
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch {}
    slashReply(ctx.esc(detail));
  }
  return true;
}

// ── TTS ──

async function _cmdTts(args, ctx) {
  const text = args.join(' ');
  if (!text) { slashReply('Usage: /tts &lt;text to speak&gt;'); return true; }
  slashReply('Synthesizing...');
  try {
    const res = await fetch(`${API_BASE}/api/tts/synthesize`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, format: 'base64' })
    });
    if (res.ok) {
      const data = await res.json();
      if (data.audio) {
        const audio = new Audio('data:audio/wav;base64,' + data.audio);
        audio.play();
        slashReply('Playing...');
      } else { slashReply('No audio returned'); }
    } else { slashReply('TTS failed (is Kokoro running?)'); }
  } catch(e) { slashReply('TTS service unavailable'); }
  return true;
}

// ── Demo ──

async function _cmdDemo(args, ctx) {
  const hasModels = await _hasConfiguredModels();
  if (!hasModels) {
    await typewriterReply('Before the tour, add your first AI endpoint with /setup or in /settings.');
    return true;
  }

  // ── Interactive guided tour ──
  // Highlights elements with red outline, shows tooltip with pointer arrow.
  // Navigation: ← back, skip tour, → next.

  // _onTyped / _draftPoll / _draftObserver get bound below; declare so they
  // can be cleaned up here.
  let _onTyped = null;
  let _msgEl = null;
  let _draftObserver = null;
  let _draftPoll = null;
  const _clearTour = () => {
    document.querySelectorAll('.odysseus-highlight, .odysseus-highlight-click').forEach(e => {
      e.classList.remove('odysseus-highlight', 'odysseus-highlight-click');
    });
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
    document.getElementById('tour-tooltip')?.remove();
    document.body.classList.remove('tour-active');
    // Keep the draft-restore mechanism alive for a few seconds AFTER the
    // tour visually ends, because the closing `typewriterReply` and any
    // async stragglers can clear #message in between resolve('next') and
    // the user actually reading the text. Hand-off to a deferred cleanup.
    setTimeout(() => {
      if (_msgEl && _onTyped) _msgEl.removeEventListener('input', _onTyped);
      if (_draftObserver) _draftObserver.disconnect();
      if (_draftPoll) clearInterval(_draftPoll);
    }, 3000);
  };
  // Body flag lets CSS lift overflow:hidden on parents (e.g. .sidebar) so
  // the highlight halo isn't clipped while the tour is running.
  document.body.classList.add('tour-active');

  // Persist anything the user types during the tour. Several actions inside
  // the flow (createDirectChat, slash-command handling) intentionally clear
  // #message, which would also wipe what the user typed for the final step.
  // We watch the textarea for non-tour-driven mutations and restore on the
  // next tick.
  let _typedDraft = '';
  _msgEl = document.getElementById('message');
  _onTyped = () => { if (_msgEl) _typedDraft = _msgEl.value; };
  const _restoreIfCleared = () => {
    if (!_msgEl || !_typedDraft) return;
    if (_msgEl.value === '' && _typedDraft) {
      _msgEl.value = _typedDraft;
      _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  if (_msgEl) _msgEl.addEventListener('input', _onTyped);
  _draftObserver = new MutationObserver(() => _restoreIfCleared());
  if (_msgEl) _draftObserver.observe(_msgEl, { attributes: true, attributeFilter: ['value'] });
  // Polling fallback — MutationObserver doesn't catch assignment to `.value`.
  _draftPoll = setInterval(_restoreIfCleared, 200);

  // Inject styles once
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent = `
      #tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);
        border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;
        font-family:inherit;font-size:0.8rem;line-height:1.5;
        box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;
        opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}
      #tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}
      #tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}
      .tour-arrow{position:absolute;width:10px;height:10px;background:var(--bg);
        border:1px solid var(--border);transform:rotate(45deg);pointer-events:none}
      .tour-nav{display:flex;align-items:center;justify-content:space-between}
      .tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);
        cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}
      .tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}
      .tour-nav button:active{background:color-mix(in srgb,var(--fg) 16%,transparent);transform:scale(0.95)}
      .tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}
      .tour-btn-arrow:hover{opacity:1}
      .tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}
      .tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}
      .tour-btn-skip:hover{opacity:0.6}
      .tour-btn-arrow-pulse{opacity:1;border-color:var(--accent,var(--red));color:var(--accent,var(--red));
        animation:tour-arrow-pulse 1.2s ease-in-out infinite}
      @keyframes tour-arrow-pulse{
        0%,100%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent,var(--red)) 50%,transparent)}
        50%    {box-shadow:0 0 0 6px color-mix(in srgb,var(--accent,var(--red)) 0%,transparent)}
      }
    `;
    document.head.appendChild(s);
  }

  // Create tooltip
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let cancelled = false;

  function positionTooltip(target) {
    // Remove old arrow
    tooltip.querySelector('.tour-arrow')?.remove();
    const r = target.getBoundingClientRect();
    const ttW = 280;
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const ttH = tooltip.offsetHeight || 100;

    const arrow = document.createElement('div');
    arrow.className = 'tour-arrow';

    const gap = 12;
    let top, left, arrowSide;

    // Prefer below
    if (r.bottom + gap + ttH < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - ttW / 2;
      arrowSide = 'top';
    // Try above
    } else if (r.top - gap - ttH > 10) {
      top = r.top - gap - ttH;
      left = r.left + r.width / 2 - ttW / 2;
      arrowSide = 'bottom';
    // Try right
    } else {
      top = r.top + r.height / 2 - ttH / 2;
      left = r.right + gap;
      arrowSide = 'left';
    }

    // Clamp to viewport
    if (left + ttW > window.innerWidth - 10) left = window.innerWidth - ttW - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;

    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';

    // Position arrow pointing at target
    if (arrowSide === 'top') {
      arrow.style.cssText = `top:-6px;left:${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-right:none;border-bottom:none`;
    } else if (arrowSide === 'bottom') {
      arrow.style.cssText = `bottom:-6px;left:${Math.min(Math.max(r.left + r.width / 2 - left - 5, 10), ttW - 20)}px;border-left:none;border-top:none`;
    } else {
      arrow.style.cssText = `left:-6px;top:${Math.min(Math.max(r.top + r.height / 2 - top - 5, 10), ttH - 20)}px;border-right:none;border-top:none`;
    }
    tooltip.appendChild(arrow);
    tooltip.style.visibility = '';
  }

  // Stream HTML into an element character by character, skipping tag
  // boundaries instantly so <b>, <i> etc stay intact. Returns a handle so we
  // can cancel if the step ends before the stream finishes.
  function streamHTML(el, html, speedMs = 14) {
    el.innerHTML = '';
    let i = 0, out = '';
    let timer = setInterval(() => {
      if (i >= html.length) { clearInterval(timer); timer = null; return; }
      if (html[i] === '<') {
        const end = html.indexOf('>', i);
        if (end === -1) { out += html.slice(i); i = html.length; }
        else { out += html.slice(i, end + 1); i = end + 1; }
      } else {
        out += html[i];
        i++;
      }
      el.innerHTML = out;
    }, speedMs);
    return { cancel: () => { if (timer) { clearInterval(timer); el.innerHTML = html; } } };
  }

  // Floating halo overlay — positioned over a target via getBoundingClientRect.
  // Returns a handle with .update() and .destroy(). We use this instead of a
  // CSS class on the target because per-target styles (outline, box-shadow)
  // and clipping ancestors otherwise eat the glow.
  function makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return {
      el: halo,
      update,
      destroy() {
        window.removeEventListener('resize', update);
        window.removeEventListener('scroll', update, true);
        halo.remove();
      },
    };
  }

  function showStep(sel, text, mode = 'next', isFirst = false, stepOpts = {}) {
    return new Promise(resolve => {
      if (cancelled) return resolve('cancel');
      document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
      document.querySelectorAll('.tour-halo').forEach(e => e.remove());

      // Support multiple selectors (comma-separated)
      const sels = sel.split(',').map(s => s.trim());
      const targets = sels.map(s => document.querySelector(s)).filter(Boolean);
      if (!targets.length) return resolve('skip');

      const clickMode = mode === 'click';
      // Steps that advance on a domain event (message submitted) also get the
      // click-style "breathing" halo so they feel inviting. We intentionally
      // exclude `#model-picker-btn` from this list — the model-picker step
      // used to hide its arrows AND not click-advance, leaving the user with
      // a halo that did nothing if they didn't actually pick a model. It now
      // renders with normal arrows + `advanceOnClick`, see the steps array.
      const waitsForEvent = sels.includes('#message');
      const breathing = clickMode || waitsForEvent;
      const advanceOnClick = !!stepOpts.advanceOnClick;
      const pulseNext = !!stepOpts.pulseNext;

      targets.forEach(t => t.classList.add('odysseus-highlight'));
      const halos = breathing ? targets.map(makeHalo) : [];
      // Reset tooltip into the "pre-fade" state so the new step phases in.
      tooltip.classList.remove('tour-fade-in');
      targets[0].scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      tooltip.innerHTML = `<div class="tour-text">${text}</div>
        ${breathing ? '<div style="font-size:0.72rem;opacity:0.35;margin-bottom:6px">Click the highlighted element to continue</div>' : ''}
        <div class="tour-nav" style="${breathing ? 'justify-content:center' : ''}">
          ${breathing ? '' : `<button class="tour-btn-arrow${isFirst ? ' disabled' : ''}" data-act="back">\u2190</button>`}
          <button class="tour-btn-skip" data-act="skip">${stepOpts.finishLabel ? 'finish tour' : 'skip tour'}</button>
          ${breathing ? '' : `<button class="tour-btn-arrow${pulseNext ? ' tour-btn-arrow-pulse' : ''}" data-act="next">\u2192</button>`}
        </div>`;

      // Position based on the fully-rendered tooltip so it doesn't jump as
      // text streams in, then stream the text into .tour-text and fade
      // everything in so the transition between steps isn't jarring.
      let streamHandle = null;
      requestAnimationFrame(() => {
        positionTooltip(targets[0]);
        tooltip.classList.add('tour-fade-in');
        halos.forEach(h => h.el.classList.add('tour-fade-in'));
        const textEl = tooltip.querySelector('.tour-text');
        if (textEl) streamHandle = streamHTML(textEl, text);
      });

      let messageInputListener = null;
      let modelListener = null;

      const onClick = (e) => {
        const act = e.target.closest('[data-act]')?.dataset.act;
        if (!act) return;
        cleanup();
        if (act === 'skip') { cancelled = true; resolve('cancel'); }
        else resolve(act);
      };
      // Document-level capture so we hear the click before any inner handler
      // that might preventDefault / stopPropagation. We walk up from e.target
      // via .closest(selector) — more robust than t.contains(e.target) when
      // the click lands on a SVG/path child or a textNode wrapper. Guarded so
      // the multiple bound event types (click/pointerdown/mousedown) can't
      // double-resolve.
      let _advanced = false;
      const onDocClickCapture = (e) => {
        if (_advanced) return;
        const t = e.target;
        const matches = sels.some(s => {
          try { return t.closest && t.closest(s); } catch { return false; }
        });
        if (!matches) return;
        _advanced = true;
        // resolve first — if anything in cleanup throws we still advance.
        resolve('clicked');
        try { cleanup(); } catch (err) { console.warn('tour cleanup:', err); }
      };
      // Advance on Enter so the user can hit "send" naturally to finish
      // the tour. We deliberately do NOT advance on every input event —
      // doing so used to tear down the tooltip's click handler the moment
      // the user typed a single character, leaving the `→` button visible
      // but unclickable, and the typed draft vulnerable to later clears.
      // We also stopPropagation+preventDefault on the Enter so it can't
      // ALSO submit the chat form — otherwise the message would get sent
      // (and the input cleared) the moment the user finishes the tour.
      const onMessageInput = (e) => {
        if (e.type !== 'keydown') return;
        if (e.key !== 'Enter' || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
        const ta = document.getElementById('message');
        if (!ta || !ta.value.trim()) return;
        // Snapshot what the user typed. If anything async clears the
        // textarea between now and the next paint (typewriterReply, the
        // submit-debounce reset, etc.), we explicitly put it back.
        const saved = ta.value;
        e.preventDefault();
        e.stopImmediatePropagation();
        cleanup();
        resolve('next');
        const _restore = () => {
          if (ta && !ta.value && saved) {
            ta.value = saved;
            ta.dispatchEvent(new Event('input', { bubbles: true }));
          }
        };
        // Multiple ticks — synchronous, micro-task, and a couple frames
        // out — to catch whatever is clearing it.
        _restore();
        Promise.resolve().then(_restore);
        requestAnimationFrame(_restore);
        setTimeout(_restore, 50);
        setTimeout(_restore, 200);
      };
      const onModelPicked = () => { cleanup(); resolve('next'); };

      const cleanup = () => {
        tooltip.removeEventListener('click', onClick);
        ['click', 'pointerdown', 'mousedown'].forEach(evt => {
          document.removeEventListener(evt, onDocClickCapture, true);
          targets.forEach(t => t.removeEventListener(evt, onDocClickCapture, true));
        });
        if (messageInputListener) document.removeEventListener('keydown', messageInputListener, true);
        if (modelListener) document.removeEventListener('odysseus:model-picked', modelListener);
        if (streamHandle) streamHandle.cancel();
        halos.forEach(h => h.destroy());
      };

      if (sels.includes('#message')) {
        const msg = document.getElementById('message');
        if (msg) {
          // Listen on `document` in CAPTURE phase so we fire BEFORE
          // chat.js's bubble-phase Enter handler on #message (which sends
          // the message and clears the input). Listeners on the same
          // element fire in insertion order regardless of phase, so we
          // have to attach a level up to win the race.
          messageInputListener = (e) => {
            if (e.target !== msg) return;
            onMessageInput(e);
          };
          document.addEventListener('keydown', messageInputListener, true);
        }
      }
      if (sels.includes('#model-picker-btn')) {
        modelListener = onModelPicked;
        document.addEventListener('odysseus:model-picked', modelListener, { once: true });
      }

      tooltip.addEventListener('click', onClick);
      if (clickMode || advanceOnClick) {
        // Listen on click + pointerdown + mousedown in capture phase, at both
        // document and target, so we still catch even if any handler upstream
        // calls preventDefault/stopPropagation. We resolve only once via the
        // resolved guard inside cleanup().
        ['click', 'pointerdown', 'mousedown'].forEach(evt => {
          document.addEventListener(evt, onDocClickCapture, true);
          targets.forEach(t => t.addEventListener(evt, onDocClickCapture, true));
        });
      }
    });
  }

  const delay = ms => new Promise(r => setTimeout(r, ms));

  // ── Welcome ──
  await typewriterReply('Welcome to Odysseus! Lets begin the tour!');
  // Beat between the welcome line and the first hint so it doesn't snap in.
  await delay(900);

  // Reset to a known starting state so the interactive steps (switch to Agent,
  // turn Web on) actually have something to do.
  try {
    const _agentBtn = document.getElementById('mode-agent-btn');
    const _chatBtn  = document.getElementById('mode-chat-btn');
    if (_agentBtn && _chatBtn) {
      _agentBtn.classList.remove('active');
      _chatBtn.classList.add('active');
      const _t = _agentBtn.closest('.mode-toggle');
      if (_t) _t.classList.add('mode-chat');
    }
    // Web is persisted per-mode under web_chat / web_agent. Zero both so the
    // toggle is genuinely off when the user reaches the "turn it on" step.
    const _st = Storage.getJSON(Storage.KEYS.TOGGLES, {});
    _st.mode = 'chat';
    _st.web_chat = false;
    _st.web_agent = false;
    Storage.setJSON(Storage.KEYS.TOGGLES, _st);
    // If the web button is currently on, click it to fully unwind it via the
    // existing handler (covers any state the click handler tracks that we
    // can't see from here).
    const _wbtn = document.getElementById('web-toggle-btn');
    if (_wbtn && _wbtn.classList.contains('active')) _wbtn.click();
    _wbtn?.classList.remove('active');
    const _webCb = document.getElementById('web-toggle');
    if (_webCb) _webCb.checked = false;
  } catch {}

  const sidebar = document.getElementById('sidebar');

  const steps = [
    { sel: '#sidebar-new-chat-btn', text: 'Start a new chat here. <b>Click it.</b> You can do it!', mode: 'click',
      before() { if (sidebar?.classList.contains('hidden')) sidebar.classList.remove('hidden'); } },
    { sel: '#model-picker-btn',   text: 'Pick your LLM, Local or API.', advanceOnClick: true },
    { sel: '#mode-agent-btn',     text: '<b>Agent mode</b> gives Odysseus more control of the app when your model supports tools: create a theme, download a model, make a daily task, organize things, and more.', mode: 'click' },
    { sel: '#web-toggle-btn',     text: 'Toggle tools like <b>web search</b>. Odysseus comes with private built-in <b>SearXNG</b> search.', mode: 'click' },
    { sel: '#overflow-plus-btn',  text: 'More tools can be found here, or in your sidebar. <b>Click to peek.</b>',
      advanceOnClick: true, pulseNext: true, afterDelay: 2200 },
    { sel: '#message',            text: 'Write your prompt here. Drag and drop files to attach them. <b>/prompt</b> for random prompt, <b>/help</b> for more.',
      finishLabel: true,
      before() { document.getElementById('overflow-menu')?.classList.add('hidden'); } },
  ];

  let i = 0;
  while (i < steps.length) {
    const step = steps[i];
    if (step.before) step.before();
    const res = await showStep(step.sel, step.text, step.mode || 'next', i === 0, step);
    if (res === 'cancel') { _clearTour(); return true; }
    if (res === 'back') { if (i > 0) i--; continue; }
    i++;
    // Breather between steps so the tour doesn't feel like it's racing ahead.
    await delay(step.afterDelay || 750);
    // After the message input step, wait for any active stream to finish
    if (step.sel === '#message' && _isStreamingFn()) {
      document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
      tooltip.style.display = 'none';
      await new Promise(r => {
        const check = setInterval(() => { if (!_isStreamingFn()) { clearInterval(check); r(); } }, 300);
      });
      await delay(400);
    }
  }

  _clearTour();
  await typewriterReply('Odysseus is yours to explore, enjoy the voyage!');
  return true;
}

// ── Compare tour ──
async function _cmdTourCompare(args, ctx) {
  // The slash dispatcher doesn't auto-clear the input, so explicitly
  // wipe it — otherwise "/tour-compare" stays parked in the textarea
  // and visually competes with the tour walkthrough.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  let overlay = document.getElementById('compare-model-overlay');
  if (!overlay) {
    const opener = document.getElementById('tool-compare-btn') || document.getElementById('rail-compare');
    if (opener) opener.click();
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 80));
      overlay = document.getElementById('compare-model-overlay');
      if (overlay) break;
    }
  }
  if (!overlay) {
    slashReply('Could not open Model Comparison. Try clicking the Compare tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  // Track halos so we can destroy them between steps. Halos sit on the
  // body (above modals) so the outline isn't clipped by modal-content's
  // overflow:auto — same pattern as _cmdDemo's makeHalo.
  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return {
      destroy() {
        window.removeEventListener('resize', update);
        window.removeEventListener('scroll', update, true);
        halo.remove();
      },
    };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }

  const _clear = () => {
    document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target) {
    const r = target.getBoundingClientRect();
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const advanceOnClick = !!opts.advanceOnClick;
    return new Promise(resolve => {
      _clearHalos();
      const target = document.querySelector(sel);
      if (!target) return resolve('skip');
      _halos.push(_makeHalo(target));
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      tooltip.classList.remove('tour-fade-in');
      const hint = advanceOnClick
        ? '<div style="font-size:0.72rem;opacity:0.45;margin-bottom:6px;">Click the highlighted element to continue.</div>'
        : '';
      tooltip.innerHTML =
        '<div class="tour-text">' + text + '</div>' + hint +
        '<div class="tour-nav">' +
          '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
          '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
          '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
        '</div>';
      requestAnimationFrame(() => {
        _positionTooltip(target);
        tooltip.classList.add('tour-fade-in');
      });

      let resolved = false;
      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        const act = hit && hit.dataset.act;
        if (!act) return;
        if (resolved) return;
        resolved = true;
        tooltip.removeEventListener('click', onClick);
        if (advanceOnClick) document.removeEventListener('click', onTargetClick, true);
        resolve(act);
      };
      // Capture-phase listener so we hear the target click before any
      // child handler that might stopPropagation.
      const onTargetClick = (e) => {
        if (resolved) return;
        if (!target.contains(e.target) && e.target !== target) return;
        resolved = true;
        tooltip.removeEventListener('click', onClick);
        document.removeEventListener('click', onTargetClick, true);
        resolve('next');
      };
      tooltip.addEventListener('click', onClick);
      if (advanceOnClick) {
        document.addEventListener('click', onTargetClick, true);
      }
    });
  }

  // ── Phase 1: model-selector modal ──
  // Scope every selector to #compare-model-overlay so we don't accidentally
  // match the Group Chat panel's .compare-parallel-toggle (line 1053 of
  // index.html), which has the same class name and is hidden — its zero
  // bounding-rect was putting the tooltip in the top-left corner.
  const phase1 = [
    { sel: '#compare-model-overlay .modal-body',
      text: 'Pick what type of test you want to run. <b>Chat</b>, <b>Agent</b>, <b>Search</b> or <b>Deep Research</b>.',
      placement: 'center-above',
      before: () => {
        const modalBody = document.querySelector('#compare-model-overlay .modal-body');
        if (modalBody) modalBody.scrollTop = 0;
      } },
    { sel: '#compare-model-overlay .compare-blind-toggle',
      text: '<b>Blind Mode</b> hides model names so you don’t know which model gives what output.' },
    { sel: '#compare-model-overlay .compare-parallel-toggle',
      text: '<b>Parallel</b> runs side by side, toggle to <b>Sequential</b> as well.' },
    { sel: '#compare-model-overlay .compare-dice-toggle',
      text: '<b>Shuffle</b> picks the models in your entire list of endpoints. Combine with <b>Blind Mode</b> and you get the cleanest evaluation.' },
  ];

  for (let i = 0; i < phase1.length; i++) {
    const step = phase1[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: false,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  // ── Wait for the modal to close and the compare panes to come up ──
  _clearHalos();
  tooltip.innerHTML =
    '<div class="tour-text">Click <b>Start</b> when ready — it will probe the models before beginning.</div>' +
    '<div class="tour-nav">' +
      '<button class="tour-btn-skip" data-act="skip">skip</button>' +
    '</div>';
  // Anchor the tooltip next to the actual "Start" button so
  // the user's eye is drawn to the next click. Halo on it too so it
  // glows the same way as the previous steps.
  const startBtn = document.querySelector('#compare-model-overlay .research-start-btn');
  if (startBtn) {
    _halos.push(_makeHalo(startBtn));
    requestAnimationFrame(() => _positionTooltip(startBtn));
  } else {
    // Fallback: park near the top if the start button isn't around (yet).
    tooltip.style.left = ((window.innerWidth / 2) - 140) + 'px';
    tooltip.style.top  = '20px';
  }

  const skipDuringWait = new Promise(resolve => {
    const onClick = (e) => {
      const hit = e.target.closest && e.target.closest('[data-act="skip"]');
      if (!hit) return;
      tooltip.removeEventListener('click', onClick);
      resolve('skip');
    };
    tooltip.addEventListener('click', onClick);
  });
  const modalClosed = new Promise(resolve => {
    const tick = () => {
      if (!document.getElementById('compare-model-overlay')
          && (document.getElementById('compare-check-btn') || document.getElementById('cmp-eval-btn'))) {
        resolve('ready');
      } else {
        setTimeout(tick, 200);
      }
    };
    tick();
  });
  const waitRes = await Promise.race([skipDuringWait, modalClosed]);
  if (waitRes === 'skip') { _clear(); return true; }

  // Small breather so any entry animation finishes before we measure.
  await new Promise(r => setTimeout(r, 300));

  // ── Phase 2: compare panes (post-modal) ──
  // Note: the Probe button (`#compare-check-btn`) is dynamic — only
  // visible when there's at least one unverified model — so we don't
  // tour it here; the user will discover it naturally when needed.
  const phase2 = [
    { sel: '#compare-add-btn',
      text: 'Add more <b>Models</b> here, keep stacking, who’s stopping ya? (you can also remove btw).' },
    { sel: '#compare-shuffle-btn',
      text: 'After adding, <b>Shuffle</b> to randomize the order again.' },
    { sel: '#cmp-eval-btn',
      text: 'When you’re ready to test, feel free to use curated <b>evaluation prompts</b>.',
      advanceOnClick: true },
  ];

  for (let i = 0; i < phase2.length; i++) {
    const step = phase2[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === phase2.length - 1,
      advanceOnClick: !!step.advanceOnClick,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  await typewriterReply('That’s it, you’ll figure out the rest! Have fun!');
  return true;
}

// ── Cookbook tour ──
async function _cmdTourCookbook(args, ctx) {
  // Clear the chat input so "/tour-cookbook" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Idempotent tour-styles injection (shared with /tour and /tour-compare).
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the cookbook modal if it's not already up.
  let modal = document.getElementById('cookbook-modal');
  if (!modal || modal.classList.contains('hidden')) {
    const opener = document.getElementById('tool-cookbook-btn') || document.getElementById('rail-cookbook');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('cookbook-modal');
      if (modal && !modal.classList.contains('hidden')) break;
    }
  }
  if (!modal || modal.classList.contains('hidden')) {
    slashReply('Could not open Cookbook. Try clicking the Cookbook tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      // Centered horizontally, sitting in the upper third of the viewport.
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      const target = document.querySelector(sel);
      if (!target) return resolve('skip');
      _halos.push(_makeHalo(target));
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      tooltip.classList.remove('tour-fade-in');
      tooltip.innerHTML =
        '<div class="tour-text">' + text + '</div>' +
        '<div class="tour-nav">' +
          '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
          '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
          '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
        '</div>';
      requestAnimationFrame(() => {
        _positionTooltip(target, placement);
        tooltip.classList.add('tour-fade-in');
      });

      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        const act = hit && hit.dataset.act;
        if (!act) return;
        tooltip.removeEventListener('click', onClick);
        resolve(act);
      };
      tooltip.addEventListener('click', onClick);
    });
  }

  function _clickTab(name) {
    const tab = modal.querySelector('.cookbook-tab[data-backend="' + name + '"]');
    if (tab) tab.click();
  }

  // ── Steps ──
  // Tabs auto-switch via `before()` so the user sees the relevant section
  // without having to navigate manually. Keep copy tight — no walls of text.
  const steps = [
    { sel: '#cookbook-modal .modal-content',
      text: '<b>Welcome to Cookbook!</b> Download / Cook / Serve models here!',
      placement: 'center-above' },
    { sel: '#cookbook-modal .cookbook-tab[data-backend="Settings"]',
      text: 'Hosting on another machine? Configure it under <b>Settings</b>.' },
    { sel: '#cookbook-dl-repo',
      text: 'Paste a HuggingFace URL or <code>org/model-name</code> to download. Quantizations like <code>org/model:Q4_K_M</code> work too.',
      before: () => _clickTab('Search') },
    { sel: '#cookbook-modal .admin-card:has(> #hwfit-list)',
      text: '<b>Scan / Download</b> — reads your hardware and lists every model that\'ll run on it.',
      before: () => _clickTab('Search') },
    { sel: '#hwfit-hw-manual-btn',
      text: 'Your detected hardware appears here. You can also manually edit it to see what would fit on other setups.',
      before: () => _clickTab('Search') },
    { sel: '#cookbook-hf-latest-toggle',
      text: 'Check <b>latest trending models</b> here.',
      before: () => _clickTab('Search') },
    { sel: '#cookbook-modal .cookbook-tab[data-backend="Serve"]',
      text: '<b>Serve</b> — fire up downloaded models with vLLM, Ollama, llama.cpp, and diffusion models too.',
      before: () => _clickTab('Serve') },
    { sel: '#cookbook-modal .cookbook-tab[data-backend="Dependencies"]',
      text: '<b>Dependencies</b> — install missing Python packages or check GPU drivers.',
      before: () => _clickTab('Dependencies') },
  ];

  // Running tab is only present when there are active tasks. If it exists,
  // tack it on as the final stop.
  const runTab = modal.querySelector('.cookbook-tab[data-backend="Running"]');
  if (runTab) {
    steps.push({
      sel: '#cookbook-modal .cookbook-tab[data-backend="Running"]',
      text: '<b>Running</b> — live status, tail logs, downloads, kill.',
      before: () => _clickTab('Running'),
    });
  }

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  // Leave Cookbook on the Download tab so the user can start downloading immediately.
  _clickTab('Search');
  _clear();
  await typewriterReply('That’s Cookbook. Pick a model that catches your eye and let it cook.');
  return true;
}

// ── Theme tour ──
async function _cmdTourTheme(args, ctx) {
  // Clear the chat input so "/tour-theme" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Idempotent tour-styles injection (shared with other tours).
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the theme modal if it isn't already up. Same hamburger / rail
  // opener pattern as the other tours.
  let modal = document.getElementById('theme-modal');
  if (!modal || modal.classList.contains('hidden')) {
    const opener = document.getElementById('tool-theme-btn')
      || document.getElementById('rail-theme')
      || document.getElementById('open-theme-btn');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('theme-modal');
      if (modal && !modal.classList.contains('hidden')) break;
    }
  }
  if (!modal || modal.classList.contains('hidden')) {
    slashReply('Could not open Theme. Try clicking the Theme tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  // Interactive step — show tooltip + halo over one or more targets and
  // resolve 'next' when the user actually clicks one of the highlighted
  // elements. Skip button still exits. `extraSel` (optional) adds a
  // second highlight target whose click also advances the step.
  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    const extraSel = opts.extraSel;
    const interactive = !!opts.interactive;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(sel);
        if (!target) return resolve('skip');
        _halos.push(_makeHalo(target));
        const extra = extraSel ? document.querySelector(extraSel) : null;
        if (extra) _halos.push(_makeHalo(extra));
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          _positionTooltip(target, placement);
          tooltip.classList.add('tour-fade-in');
        });

        let _onTarget;
        const cleanup = () => {
          tooltip.removeEventListener('click', onClick);
          if (_onTarget) {
            target.removeEventListener('click', _onTarget, true);
            if (extra) extra.removeEventListener('click', _onTarget, true);
          }
        };
        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          const act = hit && hit.dataset.act;
          if (!act) return;
          cleanup();
          resolve(act);
        };
        tooltip.addEventListener('click', onClick);
        // Interactive: clicking the highlighted target advances. We let
        // the original click propagate so the user's real action (apply
        // theme, switch tab, etc.) actually happens.
        if (interactive) {
          _onTarget = () => { cleanup(); resolve('next'); };
          target.addEventListener('click', _onTarget, true);
          if (extra) extra.addEventListener('click', _onTarget, true);
        }
      }, before ? 160 : 0);
    });
  }

  // Clicks one of the theme modal's top-level tabs by data-tab id.
  function _clickTab(tabId) {
    const tab = modal.querySelector('.admin-tab[data-tab="' + tabId + '"]');
    if (tab) tab.click();
  }

  // ── Steps ──
  // Interactive flow: the user actually clicks each highlighted element
  // to progress. Skip button exits at any point; arrow buttons still
  // work as a fallback (read past without touching anything).
  const steps = [
    { sel: '#theme-popup',
      text: '<b>Welcome to Theme.</b> Odysseus is yours to customize!',
      placement: 'center-above',
      before: () => _clickTab('theme-tab-browse') },
    { sel: '#themeGrid',
      text: 'Try a <b>default theme</b> — or build your own with <b>Customize</b>.',
      extraSel: '#theme-tabs .admin-tab[data-tab="theme-tab-customize"]',
      interactive: true },
    { sel: '#theme-harmony-card',
      text: 'Build a quick theme with <b>color harmony</b> — pick one accent color, hit Generate, and a matching palette falls out.',
      before: () => _clickTab('theme-tab-customize'),
      interactive: true },
    { sel: '#themeCustom',
      text: 'Want finer control? <b>Edit each color individually</b> here — the page updates live.',
      before: () => _clickTab('theme-tab-customize'),
      interactive: true },
    { sel: '#theme-bg-pattern-select',
      text: 'Add a <b>background animation</b> — rain, petals, constellations, sparkles, embers…',
      before: () => _clickTab('theme-tab-customize'),
      interactive: true },
    { sel: '#theme-opacity-wrap',
      text: '<b>Peek</b> fades this window so you can see the page behind it while you tweak.',
      before: () => _clickTab('theme-tab-customize'),
      interactive: true },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
      extraSel: step.extraSel,
      interactive: step.interactive,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  await typewriterReply('That’s Theme. Make it yours.');
  return true;
}

// ── Settings tour ──
async function _cmdTourSettings(args, ctx) {
  // Clear the chat input so "/tour-settings" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Idempotent tour-styles injection.
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the settings modal.
  let modal = document.getElementById('settings-modal');
  if (!modal || modal.classList.contains('hidden')) {
    const opener = document.getElementById('rail-settings')
      || document.getElementById('tool-settings-btn');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('settings-modal');
      if (modal && !modal.classList.contains('hidden')) break;
    }
  }
  if (!modal || modal.classList.contains('hidden')) {
    slashReply('Could not open Settings. Try clicking the gear icon first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    // Track the modal-enter scale animation (see task-tour notes).
    const _tStart = performance.now();
    let _rafId = 0;
    const tick = () => {
      update();
      if (performance.now() - _tStart < 500) _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      if (_rafId) cancelAnimationFrame(_rafId);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(sel);
        if (!target) return resolve('skip');
        _halos.push(_makeHalo(target));
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          _positionTooltip(target, placement);
          tooltip.classList.add('tour-fade-in');
        });

        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          const act = hit && hit.dataset.act;
          if (!act) return;
          tooltip.removeEventListener('click', onClick);
          resolve(act);
        };
        tooltip.addEventListener('click', onClick);
      }, before ? 160 : 0);
    });
  }

  function _clickNav(tab) {
    const btn = modal.querySelector('.settings-nav-item[data-settings-tab="' + tab + '"]');
    if (btn) btn.click();
  }

  const steps = [
    { sel: '#settings-modal .modal-content',
      text: '<b>Welcome to Settings.</b> HOW EXCITING.',
      placement: 'center-above' },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="services"]',
      text: '<b>Add Models</b> — add a local endpoint first, like Ollama, vLLM, or llama.cpp. Cloud providers are optional.',
      before: () => _clickNav('services') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="ai"]',
      text: '<b>AI Defaults</b> — three roles share the work. Let\'s walk through them.',
      before: () => _clickNav('ai') },
    { sel: '#settings-modal .admin-card:has(#set-defaultModelSelect)',
      text: '<b>Default Chat Model</b> — your main model. The one Odysseus reaches for whenever you start a new chat.',
      before: () => _clickNav('ai') },
    { sel: '#settings-modal .admin-card:has(#set-utilityModelSelect)',
      text: '<b>Utility Model</b> — your hard-working sidekick. Runs background tasks (compaction, cleanup, auto-naming, summarization) so your chat model doesn\'t burn cycles on chores. <b>Recommend a small local model</b> here — it\'s free and always on.',
      before: () => _clickNav('ai') },
    { sel: '#settings-modal .admin-card:has(#set-vlModelSelect)',
      text: '<b>Vision</b> — powers any image-recognition feature: drop a photo in chat, ask what\'s in it, OCR, etc.',
      before: () => _clickNav('ai') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="integrations"]',
      text: '<b>Integrations</b> — wire up email, calendar, contacts here (per-account).',
      before: () => _clickNav('integrations') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="search"]',
      text: '<b>Search</b> — plug in your own search provider, or use the bundled <b>SearXNG</b> out of the box.',
      before: () => _clickNav('search') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="appearance"]',
      text: '<b>Appearance</b> — too many tools you don\'t need? Adjust them here! Toggle sidebar buttons, tool icons, and section visibility.',
      before: () => _clickNav('appearance') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="email"]',
      text: '<b>Email</b> — sync schedule, drafts, snooze defaults — everything email-flow related.',
      before: () => _clickNav('email') },
    { sel: '#settings-modal .settings-nav-item[data-settings-tab="reminders"]',
      text: '<b>Reminders</b> — quiet hours and how Odysseus nudges you about calendar + urgent email.',
      before: () => _clickNav('reminders') },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  // Land on the first tab so the user has a familiar starting point.
  _clickNav('services');
  _clear();
  await typewriterReply('See? Not so bad. Tweak away.');
  return true;
}

// ── Gallery tour ──
async function _cmdTourGallery(args, ctx) {
  // Clear the chat input so "/tour-gallery" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }
  try { localStorage.setItem('odysseus-notes-first-open-hint-v1', '1'); } catch (_) {}
  document.getElementById('notes-first-open-hint')?.remove();

  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the gallery modal.
  let modal = document.getElementById('gallery-modal');
  if (!modal || modal.classList.contains('hidden')) {
    const opener = document.getElementById('tool-gallery-btn')
      || document.getElementById('rail-gallery');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('gallery-modal');
      if (modal && !modal.classList.contains('hidden')) break;
    }
  }
  if (!modal || modal.classList.contains('hidden')) {
    slashReply('Could not open Gallery. Try clicking the Gallery tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    const _tStart = performance.now();
    let _rafId = 0;
    const tick = () => {
      update();
      if (performance.now() - _tStart < 500) _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      if (_rafId) cancelAnimationFrame(_rafId);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(sel);
        if (!target) return resolve('skip');
        _halos.push(_makeHalo(target));
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          _positionTooltip(target, placement);
          tooltip.classList.add('tour-fade-in');
        });

        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          const act = hit && hit.dataset.act;
          if (!act) return;
          tooltip.removeEventListener('click', onClick);
          resolve(act);
        };
        tooltip.addEventListener('click', onClick);
      }, before ? 160 : 0);
    });
  }

  function _clickTab(tab) {
    const btn = modal.querySelector('.gallery-tab[data-tab="' + tab + '"]');
    if (btn) btn.click();
  }

  const steps = [
    { sel: '#gallery-modal .modal-content',
      text: '<b>Welcome to Gallery.</b> Photos and albums live here.',
      placement: 'center-above',
      before: () => _clickTab('images') },
    { sel: '#gallery-modal .gallery-tab[data-tab="images"]',
      text: '<b>Photos</b> — every image you\'ve uploaded, in one grid.',
      before: () => _clickTab('images') },
    { sel: '#gallery-upload-tile',
      text: 'Drop or click this tile to <b>upload</b> photos and videos.',
      before: () => _clickTab('images') },
    { sel: '#gallery-modal .gallery-tab[data-tab="albums"]',
      text: '<b>Albums</b> — group images into collections.',
      before: () => _clickTab('albums') },
    { sel: '#gallery-modal .gallery-tab[data-tab="editor"]',
      text: '<b>Editor</b> — honestly still WIP, so explore as you want.',
      before: () => _clickTab('editor') },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  // Land on Photos so the user has a familiar starting point.
  _clickTab('images');
  _clear();
  await typewriterReply('That\'s Gallery. Editor is rough — feedback welcome.');
  return true;
}

// ── Notes tour ──
async function _cmdTourNotes(args, ctx) {
  // Clear the chat input so "/tour-notes" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the notes pane (it's a side sheet, not a .modal).
  let pane = document.getElementById('notes-pane');
  if (!pane) {
    const opener = document.getElementById('tool-notes-btn')
      || document.getElementById('rail-notes');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      pane = document.getElementById('notes-pane');
      if (pane) break;
    }
  }
  if (!pane) {
    slashReply('Could not open Notes. Try clicking the Notes tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    const _tStart = performance.now();
    let _rafId = 0;
    const tick = () => {
      update();
      if (performance.now() - _tStart < 500) _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      if (_rafId) cancelAnimationFrame(_rafId);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(sel);
        if (!target) return resolve('skip');
        _halos.push(_makeHalo(target));
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          _positionTooltip(target, placement);
          tooltip.classList.add('tour-fade-in');
        });

        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          const act = hit && hit.dataset.act;
          if (!act) return;
          tooltip.removeEventListener('click', onClick);
          resolve(act);
        };
        tooltip.addEventListener('click', onClick);
      }, before ? 160 : 0);
    });
  }

  const steps = [
    { sel: '#notes-pane',
      text: '<b>Notes</b> is your basic todo list, and also where reminders are managed.',
      placement: 'center-above' },
    { sel: '#notes-pane .notes-pane-body',
      text: 'Your notes show up here. You can also <b>ask Odysseus in chat</b> to take a note for you.' },
    { sel: '#notes-search',
      text: '<b>Search</b> across every note — title, body, tags, the works.' },
    { sel: '#notes-view-toggle',
      text: 'Switch between <b>grid</b> and <b>list</b> views — pick whichever fits your brain.' },
    { sel: '#notes-archive-toggle',
      text: '<b>Archive</b> stashes old notes you don\'t want cluttering the active view but still want to keep.' },
    { sel: '#notes-select-btn',
      text: '<b>Select</b> drops you into multi-select mode for bulk archive or delete.' },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  await typewriterReply('That\'s Notes. Write down whatever you want to remember.');
  return true;
}

// ── Tour: Brain ──
async function _cmdTourBrain(args, ctx) {
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  let modal = document.getElementById('memory-modal');
  if (!modal || modal.classList.contains('hidden')) {
    const opener = document.getElementById('tool-memory-btn') || document.getElementById('rail-memory');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('memory-modal');
      if (modal && !modal.classList.contains('hidden')) break;
    }
  }
  if (!modal || modal.classList.contains('hidden')) {
    slashReply('Could not open Brain. Try clicking the Brain tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    const _tStart = performance.now();
    let _rafId = 0;
    const tick = () => {
      update();
      if (performance.now() - _tStart < 500) _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      if (_rafId) cancelAnimationFrame(_rafId);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(sel);
        if (!target) return resolve('skip');
        _halos.push(_makeHalo(target));
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          _positionTooltip(target, placement);
          tooltip.classList.add('tour-fade-in');
        });

        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          const act = hit && hit.dataset.act;
          if (!act) return;
          tooltip.removeEventListener('click', onClick);
          resolve(act);
        };
        tooltip.addEventListener('click', onClick);
      }, before ? 180 : 0);
    });
  }

  const _tab = (name) => document.querySelector(`.memory-tab[data-memory-tab="${name}"]`)?.click();
  const steps = [
    { sel: '#memory-modal .memory-modal-content',
      text: '<b>Brain</b> is where your memories are. You can edit them, or add new ones under <b>Add</b>. Wow.',
      before: () => _tab('browse'),
      placement: 'center-above' },
    { sel: '#memory-tidy-btn',
      text: '<b>Tidy</b> runs your model to clear out irrelevant memories and duplicates. It also triggers automatically from Tasks.',
      before: () => _tab('browse') },
    { sel: '.memory-tab-panel[data-memory-panel="skills"]',
      text: '<b>Skills</b> are basically your AI’s memory for improving its abilities.',
      before: () => _tab('skills') },
    { sel: '.memory-tab-panel[data-memory-panel="settings"]',
      text: '<b>Settings</b> lets you turn off auto extraction and set how strong skills need to be before they are tagged.',
      before: () => _tab('settings') },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  await typewriterReply('That’s Brain — memories, skills, tidy, and settings in one place.');
  return true;
}

// ── Task tours ──
async function _openTasksForTour() {
  let modal = document.getElementById('tasks-modal');
  if (!modal) {
    const opener = document.getElementById('tool-tasks-btn') || document.getElementById('rail-tasks');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      modal = document.getElementById('tasks-modal');
      if (modal) break;
    }
  }
  return modal;
}

async function _runTaskTour(steps, doneText, opts) {
  opts = opts || {};
  // When `continueLabel` is set, the tour ends with a centered "continue?"
  // tooltip instead of going straight to doneText. The user can pick to
  // keep going (returns 'continue') or stop here.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  const modal = await _openTasksForTour();
  if (!modal) {
    slashReply('Could not open Tasks. Try clicking the Tasks tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);
  let halos = [];

  function clearHalos() {
    halos.forEach(h => h.destroy());
    halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  function makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top = (r.top - 4) + 'px';
      halo.style.left = (r.left - 4) + 'px';
      halo.style.width = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    // The tasks modal-content runs a 250ms `modal-enter` scale animation
    // when it first opens. A one-shot getBoundingClientRect() captures
    // the mid-animation (scaled-down) rect and the halo gets locked to
    // a "cropped" version. Re-sync every animation frame for ~500ms so
    // we track the entrance to its final size.
    const _tStart = performance.now();
    let _rafId = 0;
    const tick = () => {
      update();
      if (performance.now() - _tStart < 500) _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      if (_rafId) cancelAnimationFrame(_rafId);
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function clear() {
    clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  }
  function positionTooltip(target) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top = r.bottom + gap;
    let left = r.left + r.width / 2 - tw / 2;
    if (top + th > window.innerHeight - 10) top = r.top - gap - th;
    if (top < 10) top = 10;
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }
  function showStep(step, i) {
    return new Promise(resolve => {
      clearHalos();
      if (step.before) { try { step.before(); } catch (_) {} }
      setTimeout(() => {
        const target = document.querySelector(step.sel);
        if (!target) return resolve('skip');
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        halos.push(makeHalo(target));
        tooltip.classList.remove('tour-fade-in');
        tooltip.innerHTML =
          '<div class="tour-text">' + step.text + '</div>' +
          '<div class="tour-nav">' +
            '<button class="tour-btn-arrow' + (i === 0 ? ' disabled' : '') + '" data-act="back">←</button>' +
            '<button class="tour-btn-skip" data-act="skip">' + (i === steps.length - 1 ? 'done' : 'skip tour') + '</button>' +
            '<button class="tour-btn-arrow" data-act="next">' + (i === steps.length - 1 ? '✓' : '→') + '</button>' +
          '</div>';
        requestAnimationFrame(() => {
          positionTooltip(target);
          tooltip.classList.add('tour-fade-in');
        });
        const onClick = (e) => {
          const hit = e.target.closest && e.target.closest('[data-act]');
          if (!hit) return;
          tooltip.removeEventListener('click', onClick);
          // Always fire step.after when leaving the step, regardless of
          // direction — it's the symmetric pair to `before` (undo the
          // temporary state change), and a user clicking "back" on the
          // chat-input step still needs the tasks modal restored.
          if (step.after) { try { step.after(); } catch (_) {} }
          resolve(hit.dataset.act);
        };
        tooltip.addEventListener('click', onClick);
      }, step.before ? 160 : 0);
    });
  }

  for (let i = 0; i < steps.length; i++) {
    const res = await showStep(steps[i], i);
    if (res === 'skip') { clear(); return 'skipped'; }
    if (res === 'back' && i > 0) i -= 2;
  }
  // Optional "Continue to part X?" prompt — show a centered tooltip
  // with two buttons before tearing down the tour overlay.
  if (opts.continueLabel) {
    clearHalos();
    tooltip.classList.remove('tour-fade-in');
    tooltip.innerHTML =
      '<div class="tour-text">' + (opts.continueText || 'Want to keep going?') + '</div>' +
      '<div class="tour-nav">' +
        '<button class="tour-btn-skip" data-act="stop">no thanks</button>' +
        '<button class="tour-btn-arrow" data-act="continue">' + opts.continueLabel + '</button>' +
      '</div>';
    // Centered in the upper third of the viewport.
    tooltip.style.visibility = 'hidden';
    requestAnimationFrame(() => {
      const tw = tooltip.offsetWidth || 260;
      const th = tooltip.offsetHeight || 100;
      tooltip.style.top = Math.max(10, window.innerHeight * 0.32 - th / 2) + 'px';
      tooltip.style.left = Math.max(10, window.innerWidth / 2 - tw / 2) + 'px';
      tooltip.style.visibility = '';
      tooltip.classList.add('tour-fade-in');
    });
    const choice = await new Promise(resolve => {
      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        if (!hit) return;
        tooltip.removeEventListener('click', onClick);
        resolve(hit.dataset.act);
      };
      tooltip.addEventListener('click', onClick);
    });
    clear();
    if (choice === 'continue') return 'continue';
  } else {
    clear();
  }
  if (doneText) await typewriterReply(doneText);
  return 'done';
}

async function _cmdTourTask1(args, ctx) {
  const result = await _runTaskTour([
    { sel: '#tasks-modal .modal-content',
      text: '<b>Welcome to Tasks.</b> Manage all your AI background work here.' },
    { sel: '#tasks-pause-all-btn',
      text: 'Tasks are <b>paused by default</b> — resume whichever ones make sense for you. (Or pause anything that\'s running.)' },
    { sel: '#tasks-modal .modal-body',
      text: 'When enabled, Tasks use the <b>utility model configured in Settings</b> for cleanup and organization jobs.' },
  ], 'Use Tasks when you want Odysseus to handle background housekeeping.', {
    continueLabel: 'continue →',
    continueText: '<b>Part 1 done.</b> Want to keep going into <b>adding & managing tasks</b>?',
  });
  if (result === 'continue') return _cmdTourTask2(args, ctx);
  return true;
}

async function _cmdTourTask2(args, ctx) {
  return _runTaskTour([
    { sel: '#tasks-modal .tasks-tab[data-tab="new"]',
      text: '<b>Add</b> creates scheduled prompts, research jobs, actions, event triggers, or webhooks.',
      before: () => document.querySelector('#tasks-modal .tasks-tab[data-tab="new"]')?.click() },
    { sel: '#task-ai-input',
      text: 'You can just describe the task in plain chat language. Example: “weekday mornings summarize unread email”.' },
    { sel: '#tasks-modal .memory-item[data-idx="0"]',
      text: 'Or pick a template and fill out the form manually.' },
    { sel: '#task-form-save, #tasks-modal .tasks-tab[data-tab="tasks"]',
      text: 'Tasks can be edited, paused, resumed, run now, or deleted from their cards.',
      before: () => document.querySelector('#tasks-modal .tasks-tab[data-tab="tasks"]')?.click() },
    // Tuck the modal out of the way so the chatbox is unmistakable, then
    // re-show it when the user moves past this step so the tour lands
    // back where it started.
    { sel: '#message',
      text: 'You can also <b>just ask in chat</b> — say "every weekday at 9am check for urgent emails" and Odysseus will create the task for you.',
      before: () => document.getElementById('tasks-modal')?.classList.add('hidden'),
      after:  () => document.getElementById('tasks-modal')?.classList.remove('hidden') },
  ], 'That\'s Tasks. Have it run the background bits so you can stay in chat.');
}

// ── Tour: Deep Research ──

async function _cmdTourResearch(args, ctx) {
  // Clear the chat input so "/tour-research" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Shared tour-styles injection (same block as /tour, /tour-compare, /tour-cookbook).
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the research overlay if it's not already up.
  let overlay = document.getElementById('research-overlay');
  if (!overlay) {
    const opener = document.getElementById('tool-research-btn') || document.getElementById('rail-research');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      overlay = document.getElementById('research-overlay');
      if (overlay) break;
    }
  }
  if (!overlay) {
    slashReply('Could not open Deep Research. Try clicking the Deep Research tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      const target = document.querySelector(sel);
      if (!target) return resolve('skip');
      _halos.push(_makeHalo(target));
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      tooltip.classList.remove('tour-fade-in');
      tooltip.innerHTML =
        '<div class="tour-text">' + text + '</div>' +
        '<div class="tour-nav">' +
          '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
          '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
          '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
        '</div>';
      requestAnimationFrame(() => {
        _positionTooltip(target, placement);
        tooltip.classList.add('tour-fade-in');
      });

      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        const act = hit && hit.dataset.act;
        if (!act) return;
        tooltip.removeEventListener('click', onClick);
        resolve(act);
      };
      tooltip.addEventListener('click', onClick);
    });
  }

  function _ensureSettingsOpen() {
    const body = document.getElementById('research-settings-body');
    const toggle = document.getElementById('research-settings-toggle');
    if (body && toggle && body.style.display === 'none') toggle.click();
  }

  const steps = [
    { sel: '#research-pane',
      text: '<b>Welcome to Deep Research!</b> An LLM-in-the-loop agent that plans the search, queries the web, extracts findings, and writes you a full report.',
      placement: 'center-above' },
    { sel: '#research-query',
      text: 'Type what you want to researched here. Be specific — <i>"compare X vs Y for Z"</i> beats <i>"tell me about X"</i>.' },
    { sel: '#research-settings-body',
      text: '<b>Rounds</b> is how long the model will keep searching for. You can set to <b>Auto</b>, or go deeper/quicker depending on preference.',
      before: _ensureSettingsOpen },
    { sel: '#research-pane',
      text: 'When a report finishes you can <b>discuss the results with the LLM</b> in chat, or open the full <b>visual HTML report</b> — sources, images, the works.',
      placement: 'center-above' },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: i === steps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  {
    const _body = await typewriterReply('That’s Deep Research — hit Start or queue up many. You can also view past research in your ');
    const libLink = document.createElement('button');
    libLink.type = 'button';
    libLink.textContent = 'Library';
    libLink.style.cssText = 'background:none;border:none;padding:0;margin:0;color:var(--accent,var(--red));font:inherit;text-decoration:underline;cursor:pointer;';
    libLink.addEventListener('click', () => {
      if (window.documentModule && window.documentModule.openLibrary) {
        window.documentModule.openLibrary({ tab: 'research' });
      } else {
        document.getElementById('tool-library-btn')?.click();
      }
    });
    _body.appendChild(libLink);
    _body.appendChild(document.createTextNode('.'));
  }
  return true;
}

// ── Tour: Library + Document editor ──

async function _cmdTourLibrary(args, ctx) {
  // Clear the chat input so "/tour-library" doesn't linger.
  const _msgEl = document.getElementById('message');
  if (_msgEl) {
    _msgEl.value = '';
    _msgEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Shared tour-styles injection.
  if (!document.getElementById('tour-styles')) {
    const s = document.createElement('style');
    s.id = 'tour-styles';
    s.textContent =
      '#tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;' +
      'font-family:inherit;font-size:0.8rem;line-height:1.5;' +
      'box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;' +
      'opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}' +
      '#tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}' +
      '#tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}' +
      '.tour-nav{display:flex;align-items:center;justify-content:space-between}' +
      '.tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);' +
      'cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}' +
      '.tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}' +
      '.tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}' +
      '.tour-btn-arrow:hover{opacity:1}' +
      '.tour-btn-arrow.disabled{opacity:0.15;pointer-events:none}' +
      '.tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}' +
      '.tour-btn-skip:hover{opacity:0.6}';
    document.head.appendChild(s);
  }

  // Open the library modal if it's not already up.
  let libModal = document.getElementById('doclib-modal');
  if (!libModal) {
    const opener = document.getElementById('tool-library-btn') || document.getElementById('rail-archive');
    if (opener) opener.click();
    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 80));
      libModal = document.getElementById('doclib-modal');
      if (libModal) break;
    }
  }
  if (!libModal) {
    slashReply('Could not open Library. Try clicking the Library tool first.');
    return true;
  }

  document.body.classList.add('tour-active');
  const tooltip = document.createElement('div');
  tooltip.id = 'tour-tooltip';
  document.body.appendChild(tooltip);

  let _halos = [];
  function _makeHalo(target) {
    const halo = document.createElement('div');
    halo.className = 'tour-halo';
    document.body.appendChild(halo);
    const update = () => {
      const r = target.getBoundingClientRect();
      halo.style.top    = (r.top - 4) + 'px';
      halo.style.left   = (r.left - 4) + 'px';
      halo.style.width  = (r.width + 8) + 'px';
      halo.style.height = (r.height + 8) + 'px';
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
    return { destroy() {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    } };
  }
  function _clearHalos() {
    _halos.forEach(h => h.destroy());
    _halos = [];
    document.querySelectorAll('.tour-halo').forEach(e => e.remove());
  }
  const _clear = () => {
    document.querySelectorAll('.odysseus-highlight').forEach(e => e.classList.remove('odysseus-highlight'));
    _clearHalos();
    tooltip.remove();
    document.body.classList.remove('tour-active');
  };

  function _positionTooltip(target, placement) {
    tooltip.style.visibility = 'hidden';
    tooltip.style.display = '';
    const tw = tooltip.offsetWidth || 260;
    const th = tooltip.offsetHeight || 100;
    if (placement === 'center-above') {
      const top = Math.max(10, window.innerHeight * 0.32 - th / 2);
      const left = Math.max(10, window.innerWidth / 2 - tw / 2);
      tooltip.style.top = top + 'px';
      tooltip.style.left = left + 'px';
      tooltip.style.visibility = '';
      return;
    }
    const r = target.getBoundingClientRect();
    const gap = 12;
    let top, left;
    if (r.bottom + gap + th < window.innerHeight - 10) {
      top = r.bottom + gap;
      left = r.left + r.width / 2 - tw / 2;
    } else if (r.top - gap - th > 10) {
      top = r.top - gap - th;
      left = r.left + r.width / 2 - tw / 2;
    } else {
      top = r.top + r.height / 2 - th / 2;
      left = r.right + gap;
      if (left + tw > window.innerWidth - 10) left = r.left - tw - gap;
    }
    if (left + tw > window.innerWidth - 10) left = window.innerWidth - tw - 10;
    if (left < 10) left = 10;
    if (top < 10) top = 10;
    tooltip.style.top = top + 'px';
    tooltip.style.left = left + 'px';
    tooltip.style.visibility = '';
  }

  function _showStep(sel, text, opts) {
    opts = opts || {};
    const isFirst = !!opts.isFirst;
    const isLast = !!opts.isLast;
    const before = opts.before;
    const placement = opts.placement;
    const interactive = !!opts.interactive;
    const optional = !!opts.optional;
    return new Promise(resolve => {
      _clearHalos();
      if (before) { try { before(); } catch (_) {} }
      const target = document.querySelector(sel);
      if (!target) return resolve(optional ? 'next' : 'skip');
      _halos.push(_makeHalo(target));
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      tooltip.classList.remove('tour-fade-in');
      tooltip.innerHTML =
        '<div class="tour-text">' + text + '</div>' +
        '<div class="tour-nav">' +
          '<button class="tour-btn-arrow' + (isFirst ? ' disabled' : '') + '" data-act="back">←</button>' +
          '<button class="tour-btn-skip" data-act="skip">' + (isLast ? 'done' : 'skip tour') + '</button>' +
          '<button class="tour-btn-arrow" data-act="next">' + (isLast ? '✓' : '→') + '</button>' +
        '</div>';
      requestAnimationFrame(() => {
        _positionTooltip(target, placement);
        tooltip.classList.add('tour-fade-in');
      });

      let _onTarget;
      const cleanup = () => {
        tooltip.removeEventListener('click', onClick);
        if (_onTarget) target.removeEventListener('click', _onTarget, true);
      };
      const onClick = (e) => {
        const hit = e.target.closest && e.target.closest('[data-act]');
        const act = hit && hit.dataset.act;
        if (!act) return;
        cleanup();
        resolve(act);
      };
      tooltip.addEventListener('click', onClick);
      // Interactive steps advance when the user clicks the highlighted
      // element — letting the original click through so the real action
      // (open the Create modal, in the Library case) actually fires.
      if (interactive) {
        _onTarget = () => { cleanup(); resolve('next'); };
        target.addEventListener('click', _onTarget, true);
      }
    });
  }

  // ── Phase 1: Library overview ──
  const libSteps = [
    { sel: '#doclib-modal .doclib-modal-content',
      text: '<b>Welcome to Library!</b> Your hub for <b>Chats</b>, <b>Documents</b>, <b>Research</b>, and <b>Archive</b> — search, sort and tidy!',
      placement: 'center-above',
      before: () => {
        // Force the modal box to fill its intended frame so the halo wraps the
        // whole library window, not just the (possibly collapsed) content.
        const c = document.querySelector('#doclib-modal .doclib-modal-content');
        if (c) {
          c.style.height = '85vh';
          c.style.minHeight = '85vh';
        }
      } },
    { sel: '#doclib-create-btn',
      text: '<b>Create</b> a fresh blank document — click it to try it out! (Or hit <b>Import</b> next to it to bring in a file from disk.)',
      interactive: true },
    { sel: '#doclib-grid .doclib-card',
      text: 'Each card is a saved document. It’s linked to the chat you created it in — so either <b>clone</b> it for a new chat, or <b>open</b> it in its original.',
      optional: true },
  ];

  for (let i = 0; i < libSteps.length; i++) {
    const step = libSteps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: i === 0,
      isLast: false,
      before: step.before,
      placement: step.placement,
      interactive: step.interactive,
      optional: step.optional,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  // ── Phase 2: open a document & walk the editor ──
  // Try to load the user's most recent document. If none exist, end with a hint.
  let firstDocId = null;
  try {
    const r = await fetch('/api/documents/library?limit=1&sort=recent', { credentials: 'same-origin' });
    if (r.ok) {
      const data = await r.json();
      if (data.documents && data.documents.length) firstDocId = data.documents[0].id;
    }
  } catch (_) {}

  if (!firstDocId || !window.documentModule || !window.documentModule.loadDocument) {
    _clear();
    await typewriterReply('All yours — create or import a doc, then run /tour-library again to see the editor.');
    return true;
  }

  // Close library, open the doc in the editor, wait for the pane to mount.
  document.getElementById('doclib-close')?.click();
  await new Promise(r => setTimeout(r, 200));
  try { await window.documentModule.loadDocument(firstDocId); } catch (_) {}
  for (let i = 0; i < 25; i++) {
    if (document.getElementById('doc-editor-pane')) break;
    await new Promise(r => setTimeout(r, 80));
  }
  if (!document.getElementById('doc-editor-pane')) {
    _clear();
    await typewriterReply('All yours — open a doc and run /tour-library again for the editor walkthrough.');
    return true;
  }

  const editorSteps = [
    { sel: '#doc-editor-pane',
      text: '<b>This is your document editor.</b> You can write here, but so can your model.',
      placement: 'center-above' },
    { sel: '#message',
      text: 'Just tell your model what to write or edit.',
      placement: 'center-above' },
    { sel: '#doc-tab-bar',
      text: 'Multiple docs as <b>tabs</b>. Drag to reorder, click <b>+</b> for a new one, click the dots for rename / clone / export / delete.' },
    { sel: '#doc-language-select',
      text: 'Switch the <b>document type</b> — markdown shows a preview, email shows To/Subject/Send, PDF lets you fill blanks with AI.' },
    { sel: '#doc-editor-textarea',
      text: 'Ask the LLM to <i>draft</i>, <i>rewrite</i>, <i>summarize</i>, <i>feedback</i> — edits stream live.' },
  ];

  for (let i = 0; i < editorSteps.length; i++) {
    const step = editorSteps[i];
    const res = await _showStep(step.sel, step.text, {
      isFirst: false,
      isLast: i === editorSteps.length - 1,
      before: step.before,
      placement: step.placement,
    });
    if (res === 'skip') { _clear(); return true; }
    if (res === 'back') { if (i > 0) i -= 2; continue; }
  }

  _clear();
  await typewriterReply('All yours — write away!');
  return true;
}

// ── Prompt ──

async function _cmdPrompt(args, ctx) {
  // Pull chat-appropriate prompts from compare templates. Skip the
  // `image` category (raw image-gen prompts — wrong for a text chat)
  // and `search` (bare keyword queries, not full prompts).
  const CHAT_CATS = ['chat', 'code', 'agent', 'html'];
  const all = [];
  for (const cat of CHAT_CATS) {
    const list = EVAL_PROMPTS[cat] || [];
    for (const p of list) all.push(p.prompt);
  }
  if (!all.length) { slashReply('No prompts available'); return true; }
  const firstUseKey = 'odysseus_prompt_command_used';
  const firstUse = localStorage.getItem(firstUseKey) !== '1';
  const prompt = firstUse
    ? 'i have no imagination help me'
    : all[Math.floor(Math.random() * all.length)];
  if (firstUse) localStorage.setItem(firstUseKey, '1');
  const ta = document.getElementById('message');
  if (ta) {
    // Use setTimeout so this runs AFTER the caller clears the input
    setTimeout(() => {
      ta.value = prompt;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.focus();
    }, 0);
  }
  return true;
}

// ── Setup ──

function _ensureSetupSpotlightStyles() {
  if (document.getElementById('setup-spotlight-styles')) return;
  const s = document.createElement('style');
  s.id = 'setup-spotlight-styles';
  s.textContent = `
    .setup-spotlight-halo{position:fixed;z-index:10000;pointer-events:none;border:2px solid var(--accent,var(--red));
      border-radius:10px;box-shadow:0 0 0 4px color-mix(in srgb,var(--accent,var(--red)) 18%,transparent),
      0 0 22px color-mix(in srgb,var(--accent,var(--red)) 42%,transparent);
      opacity:0;transition:opacity .22s ease-out,transform .22s ease-out;transform:scale(.985)}
    .setup-spotlight-halo.visible{opacity:1;transform:scale(1)}
    .setup-spotlight-halo.breathing{animation:setupSpotlightBreathe 1.65s ease-in-out infinite}
    @keyframes setupSpotlightBreathe{
      0%,100%{box-shadow:0 0 0 3px color-mix(in srgb,var(--accent,var(--red)) 14%,transparent),0 0 16px color-mix(in srgb,var(--accent,var(--red)) 30%,transparent);transform:scale(.992)}
      50%{box-shadow:0 0 0 6px color-mix(in srgb,var(--accent,var(--red)) 24%,transparent),0 0 30px color-mix(in srgb,var(--accent,var(--red)) 54%,transparent);transform:scale(1.006)}
    }
    .setup-inline-link{appearance:none;border:0;background:transparent;color:var(--accent,var(--red));font:inherit;font-weight:700;
      padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
    .setup-inline-link:hover{color:var(--fg)}
  `;
  document.head.appendChild(s);
}

function _visibleSetupTarget(selector) {
  const targets = Array.from(document.querySelectorAll(selector));
  return targets.find(el => {
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
  });
}

function _showSetupSpotlight(selector, duration = 1800, options = {}) {
  _ensureSetupSpotlightStyles();
  const target = _visibleSetupTarget(selector);
  if (!target) return Promise.resolve();
  target.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  const halo = document.createElement('div');
  halo.className = 'setup-spotlight-halo';
  if (options.breathe) halo.classList.add('breathing');
  document.body.appendChild(halo);
  const update = () => {
    const r = target.getBoundingClientRect();
    halo.style.top = (r.top - 5) + 'px';
    halo.style.left = (r.left - 5) + 'px';
    halo.style.width = (r.width + 10) + 'px';
    halo.style.height = (r.height + 10) + 'px';
  };
  update();
  window.addEventListener('resize', update);
  window.addEventListener('scroll', update, true);
  requestAnimationFrame(() => halo.classList.add('visible'));
  return new Promise(resolve => {
    let done = false;
    let timer = null;
    const inputEl = document.getElementById('message');
    const cleanup = () => {
      if (done) return;
      done = true;
      halo.classList.remove('visible');
      setTimeout(() => {
        window.removeEventListener('resize', update);
        window.removeEventListener('scroll', update, true);
        if (options.cancelOnType && inputEl) {
          inputEl.removeEventListener('input', cleanup);
          inputEl.removeEventListener('keydown', cleanup);
          inputEl.removeEventListener('paste', cleanup);
          inputEl.removeEventListener('focus', cleanup);
          inputEl.removeEventListener('pointerdown', cleanup);
          inputEl.removeEventListener('mousedown', cleanup);
        }
        if (timer) clearTimeout(timer);
        halo.remove();
        resolve();
      }, 240);
    };
    if (options.cancelOnType && inputEl) {
      inputEl.addEventListener('input', cleanup);
      inputEl.addEventListener('keydown', cleanup);
      inputEl.addEventListener('paste', cleanup);
      inputEl.addEventListener('focus', cleanup);
      inputEl.addEventListener('pointerdown', cleanup);
      inputEl.addEventListener('mousedown', cleanup);
    }
    timer = setTimeout(cleanup, duration);
  });
}

function _runSetupEndpointSpotlight() {
  const input = document.getElementById('message');
  if (!input) return;
  input.disabled = false;
  input.focus();
}

function _showSetupEndpointGuide(options = {}) {
  if (options.instant) {
    _showSetupEndpointChoices();
    setupMode = 'endpoint-provider-first';
    _runSetupEndpointSpotlight();
    return true;
  }
  const replyPromise = _showSetupEndpointChoicesStreamed(options);
  setupMode = 'endpoint-provider-first';
  replyPromise.finally(() => _runSetupEndpointSpotlight());
  return true;
}

function _clearSetupCommandInput() {
  const input = document.getElementById('message');
  if (!input) return;
  const value = String(input.value || '').trim().toLowerCase();
  if (value === '/setup' || value.startsWith('/setup ') || value === '/seutp' || value.startsWith('/seutp ')) {
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

// GitHub Copilot device-flow sign-in, driven from chat (mirrors the Settings
// "Connect GitHub Copilot" button). Replies via the setup guide messages.
async function _setupCopilot() {
  _clearSetupGuideMessages();
  await _setupReply('Starting GitHub Copilot sign-in…');
  let start;
  try {
    const r = await fetch(`${API_BASE}/api/copilot/device/start`, { method: 'POST', body: new FormData(), credentials: 'same-origin' });
    start = await r.json();
    if (!r.ok) { await _setupReply(start.detail || 'Failed to start Copilot sign-in.'); return; }
  } catch (e) { await _setupReply('Request failed.'); return; }
  const authUrl = start.verification_uri_complete || start.verification_uri || '';
  await _setupReply(`Opening GitHub — approve the request (code ${start.user_code}). Waiting…`);
  try { if (authUrl) window.open(authUrl, '_blank', 'noopener'); } catch (e) {}
  const deadline = Date.now() + (start.expires_in || 900) * 1000;
  const stepMs = Math.max((start.interval || 5), 2) * 1000;
  const poll = async () => {
    if (Date.now() > deadline) { await _setupReply('Copilot sign-in expired — run /setup copilot again.'); return; }
    try {
      const fd = new FormData(); fd.append('poll_id', start.poll_id);
      const r = await fetch(`${API_BASE}/api/copilot/device/poll`, { method: 'POST', body: fd, credentials: 'same-origin' });
      const d = await r.json();
      if (d.status === 'authorized') {
        const n = ((d.endpoint && d.endpoint.models) || []).length;
        await _setupReply(`Connected — ${n} Copilot model${n !== 1 ? 's' : ''} available.`);
        if (modelsModule) modelsModule.refreshModels(true);
        return;
      }
      if (d.status === 'failed') { await _setupReply('Copilot sign-in failed (' + (d.error || 'denied') + ').'); return; }
    } catch (e) { /* transient — keep polling */ }
    setTimeout(poll, stepMs);
  };
  setTimeout(poll, stepMs);
}

async function _cmdSetup(args, ctx) {
  _hideWelcomeScreen();
  _clearSetupCommandInput();
  const topic = (args[0] || '').trim().toLowerCase();
  const topicArgs = args.slice(1);
  if (topic === 'copilot' || topic === 'github') { await _setupCopilot(); return true; }
  const provider = _setupProviderFromInput(topic);
  if (provider) {
    _clearSetupGuideMessages();
    const credential = topicArgs.join(' ').trim();
    if (credential) {
      await connectDetectedSetupEndpoint({ base_url: provider.url, api_key: credential, name: provider.name });
    } else {
      pendingSetupProvider = provider;
      setupMode = 'endpoint-key-for-provider';
      // Show the canonical "/setup <provider> <key>" usage so the user
      // learns the one-shot form instead of relying on the pasted-key
      // mode that always greets them with a generic prompt.
      // _setupReply renders as plain text (no HTML) — use markdown
      // backticks for the inline code instead of <code> + &lt;&gt;.
      const _slug = (topic || '').toLowerCase();
      await _setupReply(
        `Paste your ${provider.name} API key, or run \`/setup ${_slug} <api-key>\` to set it in one step.`
      );
    }
    return true;
  }
  if (topic === 'local') {
    _clearSetupGuideMessages();
    const rawUrl = topicArgs.join(' ').trim();
    if (rawUrl) {
      const normalized = _normalizeSetupBaseUrl(rawUrl);
      await connectDetectedSetupEndpoint({ base_url: normalized, api_key: '', name: 'Local' });
    } else {
      setupMode = 'endpoint-provider-first';
      await _setupReply('Paste your local endpoint URL, for example http://100.x.x.x:11434/v1.');
    }
    return true;
  }

  // Check if models are already configured
  const modelsBox = document.getElementById('models');
  const hasModels = modelsBox && modelsBox.querySelector('.models-row');

  if (hasModels) {
    if (!topic) {
      _clearSetupGuideMessages();
      return _showSetupEndpointGuide();
    }

    if (topic === 'endpoint' || topic === 'api' || topic === 'key') {
      _clearSetupGuideMessages();
      return _showSetupEndpointGuide({ simple: true, instant: true });
    }

    if (topic === 'theme' || topic === 'themes') {
      const tm = themeModule;
      const presets = tm && tm.THEMES ? Object.keys(tm.THEMES) : [];
      const customObj = tm && tm.getCustomThemes ? tm.getCustomThemes() : {};
      const customKeys = Object.keys(customObj);

      // One-shot: /setup theme <name> -> apply directly
      const themeName = topicArgs.join(' ').trim().toLowerCase().replace(/\s+/g, '-');
      if (themeName && tm) {
        const colors = (tm.THEMES && tm.THEMES[themeName]) || customObj[themeName];
        if (colors) {
          tm.applyColors(colors);
          tm.save(themeName, colors);
          await typewriterReply(`Theme: ${themeName}`);
        } else {
          const customLabel = customKeys.length ? ` | Custom: ${customKeys.join(', ')}` : '';
          slashReply(`Unknown theme "${themeName}". Available: ${presets.join(', ')}${customLabel}`);
        }
        return true;
      }

      const current = (Storage.getJSON(Storage.KEYS.THEME, {}).name) || 'dark';
      const customLabel = customKeys.length ? `\n\nCustom: ${customKeys.join(', ')}` : '';
      await typewriterReply(`Current theme: ${current}\n\nAvailable: ${presets.join(', ')}${customLabel}\n\nType a theme name to switch.`);
      setupMode = 'theme';
      return true;
    }

    if (topic === 'memory' || topic === 'memories') {
      try {
        const res = await fetch(`${API_BASE}/api/memory`, { credentials: 'same-origin' });
        const memories = await res.json();
        const count = Array.isArray(memories) ? memories.length : 0;
        await typewriterReply(`You have ${count} saved memor${count === 1 ? 'y' : 'ies'}.\n\nType a memory to save, or use /memory to manage them.`);
      } catch {
        await typewriterReply('Could not load memories.');
      }
      return true;
    }

    if (topic === 'features') {
      try {
        const res = await fetch(`${API_BASE}/api/auth/features`, { credentials: 'same-origin' });
        const features = await res.json();
        const lines = Object.entries(features).map(([k, v]) => `${k}: ${v ? 'on' : 'off'}`).join('\n');
        await typewriterReply(`Feature toggles:\n\n${lines}\n\nType a feature name to toggle it.`);
        setupMode = 'features';
      } catch {
        await typewriterReply('Could not load features. Check the Admin Panel.');
      }
      return true;
    }

    // Unknown topic — hint
    await typewriterReply(`I don't have a setup wizard for "${topic}" yet. Try: endpoint, theme, memory, or features.`);
    return true;
  }

  // First-time setup — paste API key flow
  _clearSetupGuideMessages();
  if (setupIntroShown) {
    return _showSetupEndpointGuide();
  }
  setupIntroShown = true;
  return _showSetupEndpointGuide();
}

// ── Shortcuts ──

async function _cmdShortcuts(args, ctx) {
  // Try to load user keybinds from settings
  let keybinds = {
    search: 'ctrl+k',
    toggle_sidebar: 'ctrl+b',
    new_session: 'ctrl+alt+n',
    star_session: 'ctrl+alt+s',
    delete_session: 'ctrl+alt+d',
    admin_panel: 'ctrl+shift+u',
    cancel: 'escape',
  };

  try {
    const res = await fetch(`${API_BASE}/api/auth/settings`, { credentials: 'same-origin' });
    const settings = await res.json();
    if (settings.keybinds) {
      keybinds = { ...keybinds, ...settings.keybinds };
    }
  } catch (e) {}

  const formatCombo = (combo) => combo.split('+').map(p => {
    if (p === 'ctrl') return 'Ctrl';
    if (p === 'alt') return 'Alt';
    if (p === 'shift') return 'Shift';
    if (p === 'escape') return 'Esc';
    return p.charAt(0).toUpperCase() + p.slice(1);
  }).join('+');

  const entries = [
    [formatCombo(keybinds.search), 'Search conversations'],
    [formatCombo(keybinds.toggle_sidebar), 'Toggle sidebar'],
    [formatCombo(keybinds.new_session), 'New session'],
    [formatCombo(keybinds.star_session), 'Star / unstar session'],
    [formatCombo(keybinds.delete_session), 'Delete session'],
    [formatCombo(keybinds.admin_panel), 'Admin panel'],
    [formatCombo(keybinds.cancel), 'Cancel stream / close panel'],
    ['Enter', 'Send message'],
    ['Shift+Enter', 'New line'],
  ];
  const maxKey = Math.max(...entries.map(e => e[0].length));
  const lines = entries.map(([key, desc]) => `  ${key.padEnd(maxKey + 2)}${desc}`);
  const body = await typewriterReply('Keyboard shortcuts:');
  const pre = document.createElement('pre');
  pre.style.lineHeight = '1.7';
  pre.textContent = lines.join('\n');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'copy-code';
  btn.setAttribute('data-code', pre.textContent);
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  pre.appendChild(btn);
  body.appendChild(pre);
  uiModule.scrollHistory();
  return true;
}

// ── Easter eggs ──

const _ODYSSEY_QUOTES = [
  "Tell me, O Muse, of that ingenious hero who travelled far and wide...",
  "Of all creatures that breathe and move upon the earth, nothing is bred that is weaker than man.",
  "There is a time for many words, and there is also a time for sleep.",
  "Even his griefs are a joy long after to one that remembers all that he wrought and endured.",
  "Be strong, saith my heart; I am a soldier; I have seen worse sights than this.",
  "There is nothing more admirable than when two people who see eye to eye keep house as man and wife.",
  "A man who has been through bitter experiences and travelled far enjoys even his sufferings after a time.",
  "For a friend with an understanding heart is worth no less than a brother.",
  "The wine urges me on, the bewitching wine, which sets even a wise man to singing and to laughing gently.",
  "I am Odysseus, son of Laertes, known to all for my cunning. My fame reaches even unto heaven.",
];

const _8BALL = [
  "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes, definitely.",
  "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
  "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
  "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
  "Don't count on it.", "My reply is no.", "My sources say no.",
  "Outlook not so good.", "Very doubtful.",
];

const _FORTUNES = [
  "A beautiful, smart, and loving person will be coming into your life.",
  "A dubious friend may be an enemy in camouflage.",
  "A faithful friend is a strong defense.",
  "A fresh start will put you on your way.",
  "A golden egg of opportunity falls into your lap this month.",
  "A good time to finish up old tasks.",
  "A lifetime of happiness lies ahead of you.",
  "A light heart carries you through all the hard times.",
  "All your hard work will soon pay off.",
  "An important person will offer you support.",
  "Be patient: the best things in life are worth waiting for.",
  "Curiosity kills boredom. Nothing can kill curiosity.",
  "Do not underestimate yourself. Human potential is limitless.",
  "Every exit is an entrance to a new experience.",
  "Failure is the mother of all success.",
  "Good news will come to you by mail.",
  "In the middle of difficulty lies opportunity.",
  "The best way to predict the future is to create it.",
  "You will be rewarded for your patience and diligence.",
  "Your ability to juggle many tasks will take you far.",
];

// Easter egg visual helper — renders inside a regular chat bubble
function _eggRender(html) {
  const chatBox = document.getElementById('chat-history');
  const div = document.createElement('div');
  div.className = 'msg msg-ai';
  const role = document.createElement('div');
  role.className = 'role';
  role.textContent = 'Odysseus';
  div.appendChild(role);
  const body = document.createElement('div');
  body.className = 'body';
  body.innerHTML = html;
  div.appendChild(body);
  chatBox.appendChild(div);
  uiModule.scrollHistory();
}

async function _cmdFlip(args, ctx) {
  const isHeads = Math.random() < 0.5;
  const edge = Math.random() < 0.001;
  const coin = document.createElement('div');
  coin.style.cssText = 'width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:700;border:3px solid var(--border);color:var(--fg);background:var(--panel);animation:egg-spin 0.6s ease-out;cursor:pointer;user-select:none;transition:transform 0.15s;';
  coin.textContent = edge ? '!' : (isHeads ? 'H' : 'T');
  coin.title = edge ? 'Edge?!' : (isHeads ? 'Heads' : 'Tails');
  coin.addEventListener('click', () => {
    const r = Math.random() < 0.5;
    coin.style.animation = 'none'; coin.offsetHeight; coin.style.animation = 'egg-spin 0.6s ease-out';
    coin.textContent = r ? 'H' : 'T'; coin.title = r ? 'Heads' : 'Tails';
  });
  const chatBox = document.getElementById('chat-history');
  const wrap = document.createElement('div');
  wrap.style.cssText = 'display:flex;flex-direction:column;align-items:center;padding:16px 0;gap:6px;';
  wrap.appendChild(coin);
  if (edge) { const lbl = document.createElement('div'); lbl.style.cssText='font-size:0.8em;opacity:0.5;';lbl.textContent='The coin landed on its edge.';wrap.appendChild(lbl); }
  chatBox.appendChild(wrap);
  uiModule.scrollHistory();
  // Inject keyframes if not present
  if (!document.getElementById('egg-styles')) {
    const s = document.createElement('style');
    s.id = 'egg-styles';
    s.textContent = '@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';
    document.head.appendChild(s);
  }
  return true;
}

async function _cmdRoll(args, ctx) {
  const spec = (args[0] || '6').toLowerCase();
  const m = spec.match(/^(\d+)?d(\d+)$/);
  const count = m ? Math.min(parseInt(m[1] || '1'), 20) : 1;
  const sides = m ? Math.min(parseInt(m[2]), 1000) : Math.min(parseInt(spec) || 6, 1000);
  const results = Array.from({ length: count }, () => Math.floor(Math.random() * sides) + 1);
  const total = results.reduce((a, b) => a + b, 0);
  const dice = results.map((v, i) => {
    return `<div style="min-width:42px;height:42px;border-radius:6px;border:2px solid var(--border);background:var(--panel);display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700;color:var(--red);animation:egg-spin 0.5s ease-out ${i*0.08}s both;cursor:pointer" title="d${sides}" onclick="this.style.animation='none';this.offsetHeight;var r=Math.floor(Math.random()*${sides})+1;this.textContent=r;this.style.animation='egg-shake 0.3s ease'">${v}</div>`;
  }).join('');
  const totalHtml = count > 1 ? `<div style="font-size:0.8em;opacity:0.5;margin-top:4px">${count}d${sides} = ${total}</div>` : '';
  _eggRender(`<div style="display:flex;flex-direction:column;align-items:center;gap:4px"><div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center">${dice}</div>${totalHtml}</div>`);
  if (!document.getElementById('egg-styles')) {
    const s = document.createElement('style'); s.id = 'egg-styles';
    s.textContent = '@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';
    document.head.appendChild(s);
  }
  return true;
}

async function _cmd8Ball(args, ctx) {
  const q = args.join(' ');
  if (!q) { slashReply('Ask a yes/no question.'); return true; }
  const answer = _8BALL[Math.floor(Math.random() * _8BALL.length)];
  const positive = _8BALL.indexOf(answer) < 9;
  const neutral = _8BALL.indexOf(answer) >= 9 && _8BALL.indexOf(answer) < 14;
  const clr = positive ? 'var(--red)' : neutral ? 'var(--border)' : 'var(--fg)';
  _eggRender(`<div style="display:flex;flex-direction:column;align-items:center;gap:10px">
    <div style="width:80px;height:80px;border-radius:50%;background:#111;border:3px solid #333;display:flex;align-items:center;justify-content:center;animation:egg-spin 0.8s ease-out">
      <div style="width:36px;height:36px;border-radius:50%;background:#1a1a3e;display:flex;align-items:center;justify-content:center">
        <span style="color:#fff;font-size:18px;font-weight:900">8</span>
      </div>
    </div>
    <div style="font-size:0.8em;opacity:0.5;max-width:300px;text-align:center">${ctx.esc(q)}</div>
    <div style="color:${clr};font-weight:600;animation:egg-fade 0.5s 0.8s both;text-align:center">${answer}</div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdFortune(args, ctx) {
  const f = _FORTUNES[Math.floor(Math.random() * _FORTUNES.length)];
  _eggRender(`<div style="max-width:360px;border:1px dashed var(--border);border-radius:4px;padding:12px 16px;text-align:center;position:relative;animation:egg-fade 0.4s ease-out">
    <div style="font-size:0.7em;text-transform:uppercase;letter-spacing:2px;opacity:0.35;margin-bottom:8px">Fortune Cookie</div>
    <div style="font-style:italic;line-height:1.5">${f}</div>
    <div style="margin-top:8px;font-size:0.75em;opacity:0.3">${String(Math.floor(Math.random()*90)+10)} ${String(Math.floor(Math.random()*90)+10)} ${String(Math.floor(Math.random()*90)+10)} ${String(Math.floor(Math.random()*90)+10)} ${String(Math.floor(Math.random()*90)+10)} ${String(Math.floor(Math.random()*90)+10)}</div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdOdyssey(args, ctx) {
  const q = _ODYSSEY_QUOTES[Math.floor(Math.random() * _ODYSSEY_QUOTES.length)];
  _eggRender(`<div style="max-width:420px;border-left:3px solid var(--red);padding:8px 16px;animation:egg-fade 0.5s ease-out">
    <div style="font-style:italic;line-height:1.6;opacity:0.9">${q}</div>
    <div style="margin-top:8px;font-size:0.8em;opacity:0.4">Homer, The Odyssey</div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdAscii(args, ctx) {
  const text = args.join(' ') || 'Odysseus';
  const FONT = {
    'A':'  #  \n # # \n#####\n#   #\n#   #','B':'#### \n#   #\n#### \n#   #\n#### ','C':' ####\n#    \n#    \n#    \n ####',
    'D':'#### \n#   #\n#   #\n#   #\n#### ','E':'#####\n#    \n###  \n#    \n#####','F':'#####\n#    \n###  \n#    \n#    ',
    'G':' ####\n#    \n# ###\n#   #\n ####','H':'#   #\n#   #\n#####\n#   #\n#   #','I':'#####\n  #  \n  #  \n  #  \n#####',
    'J':'#####\n    #\n    #\n#   #\n ### ','K':'#   #\n#  # \n###  \n#  # \n#   #','L':'#    \n#    \n#    \n#    \n#####',
    'M':'#   #\n## ##\n# # #\n#   #\n#   #','N':'#   #\n##  #\n# # #\n#  ##\n#   #','O':' ### \n#   #\n#   #\n#   #\n ### ',
    'P':'#### \n#   #\n#### \n#    \n#    ','Q':' ### \n#   #\n# # #\n#  # \n ## #','R':'#### \n#   #\n#### \n#  # \n#   #',
    'S':' ####\n#    \n ### \n    #\n#### ','T':'#####\n  #  \n  #  \n  #  \n  #  ','U':'#   #\n#   #\n#   #\n#   #\n ### ',
    'V':'#   #\n#   #\n#   #\n # # \n  #  ','W':'#   #\n#   #\n# # #\n## ##\n#   #','X':'#   #\n # # \n  #  \n # # \n#   #',
    'Y':'#   #\n # # \n  #  \n  #  \n  #  ','Z':'#####\n   # \n  #  \n #   \n#####',
    '0':' ### \n#  ##\n# # #\n##  #\n ### ','1':'  #  \n ##  \n  #  \n  #  \n#####','2':' ### \n#   #\n  ## \n #   \n#####',
    '3':' ### \n#   #\n  ## \n#   #\n ### ','4':'#   #\n#   #\n#####\n    #\n    #','5':'#####\n#    \n#### \n    #\n#### ',
    '6':' ### \n#    \n#### \n#   #\n ### ','7':'#####\n   # \n  #  \n #   \n#    ','8':' ### \n#   #\n ### \n#   #\n ### ',
    '9':' ### \n#   #\n ####\n    #\n ### ',' ':'     \n     \n     \n     \n     ',
    '!':'  #  \n  #  \n  #  \n     \n  #  ','?':' ### \n#   #\n  ## \n     \n  #  ',
  };
  const chars = text.toUpperCase().split('').map(c => (FONT[c] || FONT['?']).split('\n'));
  const rows = [0,1,2,3,4].map(r => chars.map(c => c[r] || '     ').join(' '));
  _eggRender(`<pre style="color:var(--red);font-size:10px;line-height:1.15;background:none;border:none;padding:0;margin:0;animation:egg-fade 0.3s ease-out">${rows.join('\n')}</pre>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdMatrix(args, ctx) {
  const chatBox = document.getElementById('chat-history');
  const wrap = document.createElement('div');
  wrap.style.cssText = 'padding:8px 0;display:flex;justify-content:center;';
  const canvas = document.createElement('canvas');
  canvas.width = 400; canvas.height = 180;
  canvas.style.cssText = 'border-radius:4px;background:#000;max-width:100%;';
  wrap.appendChild(canvas);
  chatBox.appendChild(wrap);
  const c = canvas.getContext('2d');
  const cols = Math.floor(canvas.width / 12);
  const drops = Array.from({ length: cols }, () => Math.random() * -20);
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%^&*';
  let frames = 0;
  const iv = setInterval(() => {
    c.fillStyle = 'rgba(0,0,0,0.06)';
    c.fillRect(0, 0, canvas.width, canvas.height);
    c.font = '12px monospace';
    for (let i = 0; i < cols; i++) {
      const ch = chars[Math.floor(Math.random() * chars.length)];
      const bright = Math.random() > 0.8;
      c.fillStyle = bright ? '#fff' : `hsl(120,100%,${30 + Math.random()*40}%)`;
      c.fillText(ch, i * 12, drops[i] * 14);
      if (drops[i] * 14 > canvas.height && Math.random() > 0.97) drops[i] = 0;
      drops[i] += 0.5 + Math.random() * 0.5;
    }
    if (++frames > 120) {
      clearInterval(iv);
      c.fillStyle = 'rgba(0,0,0,0.7)'; c.fillRect(0,0,canvas.width,canvas.height);
      c.fillStyle = '#00ff41'; c.font = '14px monospace';
      c.fillText('Wake up, Neo...', canvas.width/2 - 70, canvas.height/2);
    }
  }, 50);
  uiModule.scrollHistory();
  return true;
}

async function _cmdSay(args, ctx) {
  const text = args.join(' ') || 'moo';
  const pad = Math.max(text.length + 2, 4);
  const top = ' ' + '_'.repeat(pad);
  const mid = '< ' + text + ' '.repeat(pad - text.length - 2) + ' >';
  const bot = ' ' + '-'.repeat(pad);
  const cow = `${top}\n${mid}\n${bot}\n        \\   ^__^\n         \\  (oo)\\_______\n            (__)\\       )\\/\\\n                ||----w |\n                ||     ||`;
  _eggRender(`<pre style="font-size:11px;line-height:1.3;animation:egg-fade 0.3s ease-out">${ctx.esc(cow)}</pre>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdWisdom(args, ctx) {
  const wisdoms = [
    ["The only way to do great work is to love what you do.", "Steve Jobs"],
    ["Simplicity is the ultimate sophistication.", "Leonardo da Vinci"],
    ["First, solve the problem. Then, write the code.", "John Johnson"],
    ["Any fool can write code that a computer can understand. Good programmers write code that humans can understand.", "Martin Fowler"],
    ["Talk is cheap. Show me the code.", "Linus Torvalds"],
    ["Programs must be written for people to read, and only incidentally for machines to execute.", "Abelson & Sussman"],
    ["The best error message is the one that never shows up.", "Thomas Fuchs"],
    ["Code is like humor. When you have to explain it, it's bad.", "Cory House"],
    ["Make it work, make it right, make it fast.", "Kent Beck"],
    ["Perfection is achieved not when there is nothing more to add, but when there is nothing left to take away.", "Antoine de Saint-Exupery"],
    ["It works on my machine.", "Every developer ever"],
    ["There are only two hard things in computer science: cache invalidation, naming things, and off-by-one errors.", "Anonymous"],
    ["A SQL query walks into a bar, walks up to two tables, and asks... 'Can I join you?'", "Anonymous"],
    ["!false -- it's funny because it's true.", "Anonymous"],
    ["To understand recursion, you must first understand recursion.", "Anonymous"],
  ];
  const [quote, author] = wisdoms[Math.floor(Math.random() * wisdoms.length)];
  _eggRender(`<div style="max-width:400px;border-left:3px solid var(--border);padding:8px 16px;animation:egg-fade 0.4s ease-out">
    <div style="font-style:italic;line-height:1.6">${quote}</div>
    <div style="margin-top:6px;font-size:0.8em;opacity:0.4">${author}</div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

async function _cmdUptime(args, ctx) {
  const now = Date.now();
  const loaded = window._odysseusLoadTime || now;
  const diff = now - loaded;
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  const s = Math.floor((diff % 60000) / 1000);
  const parts = [];
  if (h) parts.push(`${h}h`);
  parts.push(`${m}m`);
  parts.push(`${s}s`);
  const pct = Math.min(100, (diff / 86400000) * 100);
  _eggRender(`<div style="display:flex;flex-direction:column;align-items:center;gap:6px;animation:egg-fade 0.3s ease-out">
    <div style="font-size:1.4em;font-weight:700;font-variant-numeric:tabular-nums">${parts.join(' ')}</div>
    <div style="width:120px;height:4px;border-radius:2px;background:var(--border);overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--red);border-radius:2px;transition:width 0.5s"></div></div>
    <div style="font-size:0.7em;opacity:0.35">session uptime</div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s2=document.createElement('style');s2.id='egg-styles';s2.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s2); }
  return true;
}

async function _cmdPing(args, ctx) {
  slashReply('<span style="opacity:0.5">Pinging endpoints...</span>');
  try {
    const res = await fetch(`${API_BASE}/api/ping`, { credentials: 'same-origin' });
    const data = await res.json();
    const eps = data.endpoints || [];
    if (!eps.length) { slashReply('No endpoints configured.'); return true; }
    let html = '<div style="font-family:inherit;font-size:0.9em">';
    for (const ep of eps) {
      const isUp = ep.status === 'online';
      const dot = isUp ? '\u25CF' : '\u25CB';
      const color = isUp ? 'var(--color-success)' : 'var(--color-error)';
      const latency = ep.latency_ms != null ? ep.latency_ms + 'ms' : '--';
      const latencyColor = !isUp ? 'var(--color-error)' : ep.latency_ms < 150 ? 'var(--color-success)' : ep.latency_ms < 500 ? 'var(--color-blind-orange)' : 'var(--color-error)';
      const models = ep.model_count || 0;
      const err = ep.error ? ' <span style="opacity:0.4;font-size:0.85em">(' + ctx.esc(ep.error).slice(0, 60) + ')</span>' : '';
      html += '<div style="display:flex;align-items:center;gap:8px;padding:3px 0">';
      html += '<span style="color:' + color + ';font-size:12px">' + dot + '</span>';
      html += '<span style="min-width:140px">' + ctx.esc(ep.name) + '</span>';
      html += '<code style="min-width:60px;text-align:right;color:' + latencyColor + '">' + latency + '</code>';
      html += '<span style="opacity:0.4;font-size:0.85em">' + models + ' model' + (models !== 1 ? 's' : '') + '</span>';
      html += err;
      html += '</div>';
    }
    html += '</div>';
    slashReply(html);
  } catch (e) {
    slashReply('Failed to ping: ' + ctx.esc(e.message));
  }
  return true;
}

async function _cmdProbe(args, ctx) {
  // Find endpoint by name if provided
  const query = args.join(' ').trim();
  let url = `${API_BASE}/api/probe`;
  if (query) {
    // Fetch endpoint list to resolve name -> id
    try {
      const epRes = await fetch(`${API_BASE}/api/model-endpoints`, { credentials: 'same-origin' });
      const eps = await epRes.json();
      const match = eps.find(e =>
        e.name.toLowerCase() === query.toLowerCase() ||
        e.name.toLowerCase().includes(query.toLowerCase())
      );
      if (match) {
        url += '?endpoint_id=' + encodeURIComponent(match.id);
      } else {
        slashReply('No endpoint matching "' + ctx.esc(query) + '". Run <code>/ping</code> to see endpoints.');
        return true;
      }
    } catch (e) {
      slashReply('Failed to look up endpoints: ' + ctx.esc(e.message));
      return true;
    }
  }

  slashReply('<span style="opacity:0.5">Probing models... this may take a while.</span>');
  // Get reference to the message we just added so we can update it live
  const chatBox = document.getElementById('chat-history');
  const msgEl = chatBox ? chatBox.lastElementChild : null;
  const bodyEl = msgEl ? msgEl.querySelector('.body') : null;
  if (!bodyEl) return true;

  let html = '<div style="font-family:inherit;font-size:0.9em">';
  let currentEndpoint = '';
  let summary = { total: 0, ok: 0 };

  try {
    const res = await fetch(url, { credentials: 'same-origin' });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));

          if (data.type === 'probe_start') {
            currentEndpoint = data.endpoint;
            const skipNote = data.skipped ? ' + ' + data.skipped + ' non-chat skipped' : '';
            html += '<div style="margin-top:8px;font-weight:600;color:var(--fg);opacity:0.8">'
              + ctx.esc(data.endpoint) + ' <span style="opacity:0.4;font-weight:400">(' + data.model_count + ' chat models' + skipNote + ')</span></div>';
            if (data.error) {
              html += '<div style="padding:2px 0 2px 20px;opacity:0.5;font-size:0.9em">' + ctx.esc(data.error) + '</div>';
            }
            bodyEl.innerHTML = html + '</div>';

          } else if (data.type === 'probe_result') {
            const isOk = data.status === 'ok';
            const isTimeout = data.status === 'timeout';
            const dot = isOk ? '\u25CF' : (isTimeout ? '\u25D0' : '\u25CB');
            const color = isOk ? 'var(--color-success)' : (isTimeout ? 'var(--color-blind-orange)' : 'var(--color-error)');
            const latency = data.latency_ms != null ? data.latency_ms + 'ms' : '--';
            const latencyColor = isOk
              ? (data.latency_ms < 2000 ? 'var(--color-success)' : 'var(--color-blind-orange)')
              : 'var(--color-error)';
            const modelName = (data.model || '').split('/').pop();
            const err = data.error ? ' <span style="opacity:0.4;font-size:0.85em">(' + ctx.esc(data.error) + ')</span>' : '';
            html += '<div style="display:flex;align-items:center;gap:8px;padding:2px 0 2px 20px">';
            html += '<span style="color:' + color + ';font-size:12px">' + dot + '</span>';
            html += '<span style="min-width:180px">' + ctx.esc(modelName) + '</span>';
            html += '<code style="min-width:60px;text-align:right;color:' + latencyColor + '">' + latency + '</code>';
            html += err;
            html += '</div>';
            bodyEl.innerHTML = html + '</div>';
            if (uiModule) uiModule.scrollHistory();

          } else if (data.type === 'probe_done') {
            summary = { total: data.total || 0, ok: data.ok || 0 };
          }
        } catch (e) { /* skip parse errors */ }
      }
    }

    // Final summary
    const pct = summary.total > 0 ? Math.round((summary.ok / summary.total) * 100) : 0;
    const sumColor = pct === 100 ? 'var(--color-success)' : pct >= 50 ? 'var(--color-blind-orange)' : 'var(--color-error)';
    html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--border);font-weight:600;color:' + sumColor + '">';
    html += summary.ok + '/' + summary.total + ' models responding (' + pct + '%)';
    html += '</div>';
    bodyEl.innerHTML = html + '</div>';
    if (uiModule) uiModule.scrollHistory();

  } catch (e) {
    bodyEl.innerHTML = 'Failed to probe: ' + ctx.esc(e.message);
  }
  return true;
}

async function _cmdColor(args, ctx) {
  const hex = args[0] || '#' + Math.floor(Math.random()*16777215).toString(16).padStart(6,'0');
  const c = hex.startsWith('#') ? hex : '#' + hex;
  _eggRender(`<div style="display:flex;align-items:center;gap:12px;animation:egg-fade 0.3s ease-out">
    <div style="width:48px;height:48px;border-radius:4px;border:1px solid var(--border);background:${ctx.esc(c)};cursor:pointer" title="Click to copy" onclick="navigator.clipboard.writeText('${ctx.esc(c)}');this.style.transform='scale(0.9)';setTimeout(()=>this.style.transform='',150)"></div>
    <div style="display:flex;flex-direction:column;gap:2px"><code style="font-size:1.1em">${ctx.esc(c)}</code>
      <span style="font-size:0.75em;opacity:0.4">click swatch to copy</span>
    </div>
  </div>`);
  if (!document.getElementById('egg-styles')) { const s=document.createElement('style');s.id='egg-styles';s.textContent='@keyframes egg-spin{0%{transform:rotateY(0) scale(0.5);opacity:0}50%{transform:rotateY(540deg) scale(1.2)}100%{transform:rotateY(720deg) scale(1)}} @keyframes egg-shake{0%,100%{transform:rotate(0)}25%{transform:rotate(-8deg)}75%{transform:rotate(8deg)}} @keyframes egg-fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}';document.head.appendChild(s); }
  return true;
}

// ── Help (generated dynamically from COMMANDS) ──

async function _cmdHelp(args, ctx) {
  const categories = {};
  for (const [name, def] of Object.entries(COMMANDS)) {
    if (def.hidden) continue;
    const cat = def.category || 'Other';
    if (!categories[cat]) categories[cat] = [];
    if (def.subs) {
      for (const [sub, sDef] of Object.entries(def.subs)) {
        if (sub.startsWith('_')) continue; // skip internal subs
        const usage = sDef.usage || `/${name} ${sub}`;
        const desc = sDef.help || '';
        categories[cat].push(`  ${usage.padEnd(21)}${desc}`);
      }
    } else {
      const usage = def.usage || `/${name}`;
      const desc = def.help || '';
      categories[cat].push(`  ${usage.padEnd(21)}${desc}`);
    }
  }
  const order = ['Getting started', 'Tours', 'Chats', 'Settings', 'Memory', 'Productivity', 'AI Tools'];
  let lines = [];
  for (const cat of order) {
    if (categories[cat] && categories[cat].length) {
      lines.push(`${cat}:`);
      lines = lines.concat(categories[cat]);
      lines.push('');
    }
  }
  // Any remaining categories not in the predefined order
  for (const cat of Object.keys(categories)) {
    if (!order.includes(cat) && categories[cat].length) {
      lines.push(`${cat}:`);
      lines = lines.concat(categories[cat]);
      lines.push('');
    }
  }
  lines.push('Tip: /<command> --help for details');
  lines.push('Shortcuts: /new /rename /fork /web /bash /memories /forget');
  slashReply(`<pre style="line-height:1.7">${lines.join('\n')}</pre>`);
  return true;
}

// ── Command registry ──────────────────────────────────────────────
// Each top-level key is a command group.  Flat commands have a handler
// directly; grouped commands use `subs`.  `default` is the sub run
// when the command is invoked bare (e.g. `/chats` -> info).

const COMMANDS = {
  chats: {
    alias: ['chat', 'session', 'sessions', 's'],
    category: 'Chats',
    help: 'Manage chat sessions',
    default: 'info',
    subs: {
      'new':         { handler: _cmdSessionNew,         alias: ['create','mkdir'], help: 'Create new chat',             usage: '/chats new [name]' },
      'delete':      { handler: _cmdSessionDelete,      alias: ['del','rm'],       help: 'Delete chat',                 usage: '/chats delete [id]' },
      'archive':     { handler: _cmdSessionArchive,     alias: ['tar'],            help: 'Archive chat',                usage: '/chats archive [id]' },
      'rename':      { handler: _cmdSessionRename,      alias: ['mv'],             help: 'Rename current chat',         usage: '/chats rename Name' },
      'favorite':    { handler: _cmdSessionImportant,   alias: ['pin','important'], help: 'Mark as favorite',          usage: '/chats favorite' },
      'unfavorite':  { handler: _cmdSessionUnimportant, alias: ['unpin','unimportant'], help: 'Unmark favorite',       usage: '/chats unfavorite' },
      'fork':        { handler: _cmdSessionFork,        alias: ['cp'],             help: 'Fork chat (keep first N msgs)', usage: '/chats fork [N]' },
      'truncate':    { handler: _cmdSessionTruncate,    alias: [],                 help: 'Delete older messages, keep last N', usage: '/chats truncate N' },
      'switch':      { handler: _cmdSessionSwitch,      alias: ['goto','cd'],      help: 'Switch to chat by name/id',    usage: '/chats switch name' },
      'sort':        { handler: _cmdSessionSort,        alias: [],                 help: 'Auto-sort into folders',      usage: '/chats sort' },
      'info':        { handler: _cmdSessionInfo,        alias: ['stat'],           help: 'Show chat details',           usage: '/chats info' },
      'clear':       { handler: _cmdSessionClear,       alias: [],                 help: 'Clear chat display',          usage: '/chats clear' },
      'export':      { handler: _cmdSessionExport,      alias: ['cat'],            help: 'Download as markdown',        usage: '/chats export' }
    }
  },
  toggle: {
    alias: ['t'],
    category: 'Quick toggles',
    hidden: true,
    help: 'Toggle features on/off',
    default: '_show',
    subs: {
      'web':       { handler: _cmdToggleWeb,       alias: ['search','s','w'],  help: 'Toggle web search',       usage: '/toggle web' },
      'bash':      { handler: _cmdToggleBash,      alias: ['b','shell'],       help: 'Toggle bash/shell',       usage: '/toggle bash' },
      'research':  { handler: _cmdToggleResearch,  alias: ['r'],               help: 'Toggle deep research',    usage: '/toggle research' },
      'doc':       { handler: _cmdToggleDoc,       alias: [],     help: 'Toggle document editor',  usage: '/toggle doc' },
      'sidebar':   { handler: _cmdToggleSidebar,   alias: ['sb'], help: 'Cycle sidebar (full/mini/off)', usage: '/toggle sidebar [1|2|3]' },
      '_show':     { handler: _cmdToggleShow,      alias: [],     help: 'Show all toggle states',  usage: '/toggle' }
    }
  },
  workspace: {
    alias: ['ws'],
    category: 'Agent',
    help: 'Set the folder the agent works in',
    handler: _cmdWorkspace,
    noUserBubble: true,
    usage: '/workspace [set <path> | clear | pick]',
  },
  memory: {
    alias: ['m'],
    category: 'Memory',
    help: 'Manage persistent memories',
    default: 'list',
    subs: {
      'list':   { handler: _cmdMemoryList,   alias: ['ls'],          help: 'List all memories',   usage: '/memory list' },
      'add':    { handler: _cmdMemoryAdd,    alias: ['echo'],        help: 'Save a memory',       usage: '/memory add text' },
      'delete': { handler: _cmdMemoryDelete, alias: ['del', 'rm'],   help: 'Delete by ID',        usage: '/memory delete id' },
      'search': { handler: _cmdMemorySearch, alias: ['grep'],        help: 'Search memories',     usage: '/memory search q' }
    }
  },
  rag: {
    alias: [],
    category: 'RAG',
    hidden: true,
    help: 'Manage document indexing',
    default: 'list',
    subs: {
      'list':   { handler: _cmdRagList,   alias: ['ls'],       help: 'List indexed files',    usage: '/rag list' },
      'add':    { handler: _cmdRagAdd,    alias: [],           help: 'Add directory',         usage: '/rag add /path' },
      'remove': { handler: _cmdRagRemove, alias: ['rm'],       help: 'Remove directory',      usage: '/rag remove /path' }
    }
  },
  todo: {
    alias: ['td'],
    category: 'Productivity',
    help: 'Add or list todos',
    handler: _cmdTodo,
    noUserBubble: true,
    usage: '/todo Your task  ·  /todo list',
  },
  event: {
    alias: ['ev'],
    category: 'Productivity',
    help: 'Create a calendar event',
    handler: _cmdEvent,
    noUserBubble: true,
    usage: '/event tomorrow 14:00 Team call',
  },
  setup: {
    alias: ['su', 'seutp'],
    category: 'Getting started',
    help: 'Add local or API model endpoints',
    handler: _cmdSetup,
    usage: '/setup local URL  ·  /setup groq KEY  ·  /setup copilot  ·  /setup endpoint',
    // Provider subs so the autocomplete popup surfaces "/setup deepseek",
    // "/setup openai", etc. when the user types "/setup de". Each sub's
    // handler is a thin wrapper that re-prepends the sub name and
    // re-dispatches into _cmdSetup, which already knows how to handle
    // bare-provider (prompts for the key) AND provider-with-key (saves it).
    // Without the explicit handler, the slash-dispatcher errors with
    // "subDef.handler is not a function".
    subs: {
      deepseek:   { help: 'DeepSeek',      usage: '/setup deepseek sk-...',     handler: (a, c) => _cmdSetup(['deepseek',   ...a], c) },
      openai:     { help: 'OpenAI',        usage: '/setup openai sk-proj-...',  handler: (a, c) => _cmdSetup(['openai',     ...a], c) },
      anthropic:  { help: 'Anthropic',     usage: '/setup anthropic sk-ant-...',handler: (a, c) => _cmdSetup(['anthropic',  ...a], c) },
      openrouter: { help: 'OpenRouter',    usage: '/setup openrouter sk-or-...',handler: (a, c) => _cmdSetup(['openrouter', ...a], c) },
      groq:       { help: 'Groq',          usage: '/setup groq gsk_...',        handler: (a, c) => _cmdSetup(['groq',       ...a], c) },
      gemini:     { help: 'Google Gemini', alias: ['google'], usage: '/setup gemini AIza...', handler: (a, c) => _cmdSetup(['gemini', ...a], c) },
      xai:        { help: 'xAI (Grok)',    alias: ['grok'],   usage: '/setup xai xai-...',   handler: (a, c) => _cmdSetup(['xai',    ...a], c) },
      ollama:     { help: 'Ollama Cloud',  usage: '/setup ollama KEY',          handler: (a, c) => _cmdSetup(['ollama',     ...a], c) },
      copilot:    { help: 'GitHub Copilot', usage: '/setup copilot',            handler: (a, c) => _cmdSetup(['copilot',    ...a], c) },
      local:      { help: 'Local model server (vLLM / LM Studio / llama.cpp / Ollama)',
                    usage: '/setup local http://localhost:8000/v1',
                    handler: (a, c) => _cmdSetup(['local', ...a], c) },
      endpoint:   { help: 'Open the endpoint manager in Settings',
                    usage: '/setup endpoint',
                    handler: (a, c) => _cmdSetup(['endpoint', ...a], c) },
    },
  },
  demo: {
    alias: ['tour'],
    category: 'Tours',
    help: 'Full guided product tour',
    handler: _cmdDemo,
    usage: '/demo'
  },
  'tour-compare': {
    alias: ['compare-tour'],
    category: 'Tours',
    help: 'Model comparison tour',
    handler: _cmdTourCompare,
    usage: '/tour-compare'
  },
  'tour-cookbook': {
    alias: ['cookbook-tour'],
    category: 'Tours',
    help: 'Cookbook tour: hardware, downloads, serving',
    handler: _cmdTourCookbook,
    usage: '/tour-cookbook'
  },
  'tour-research': {
    alias: ['research-tour'],
    category: 'Tours',
    help: 'Deep Research tour',
    handler: _cmdTourResearch,
    usage: '/tour-research'
  },
  'tour-library': {
    alias: ['library-tour', 'tour-doc', 'tour-document', 'doc-tour', 'document-tour'],
    category: 'Tours',
    help: 'Library and document editor tour',
    handler: _cmdTourLibrary,
    usage: '/tour-library'
  },
  'tour-theme': {
    alias: ['theme-tour'],
    category: 'Tours',
    help: 'Theme editor tour',
    handler: _cmdTourTheme,
    usage: '/tour-theme'
  },
  'tour-settings': {
    alias: ['tour-setting', 'settings-tour'],
    category: 'Tours',
    help: 'Settings tour: models, integrations, appearance',
    handler: _cmdTourSettings,
    usage: '/tour-settings'
  },
  'tour-gallery': {
    alias: ['gallery-tour'],
    category: 'Tours',
    help: 'Gallery tour: photos, albums, editor',
    handler: _cmdTourGallery,
    usage: '/tour-gallery'
  },
  'tour-brain': {
    alias: ['brain-tour', 'tour-memory', 'memory-tour'],
    category: 'Tours',
    help: 'Brain tour: memories, tidy, skills, settings',
    handler: _cmdTourBrain,
    usage: '/tour-brain'
  },
  'tour-task-1': {
    alias: ['tour-task', 'tour-tasks', 'tour-tasks-1', 'tasks-tour', 'tasks-tour-1'],
    category: 'Tours',
    help: 'Tasks tour: built-ins, runs, pause controls',
    handler: _cmdTourTask1,
    usage: '/tour-task-1'
  },
  'tour-task-2': {
    alias: ['tour-tasks-2', 'tasks-tour-2'],
    category: 'Tours',
    help: 'Tasks tour: adding and managing tasks',
    handler: _cmdTourTask2,
    usage: '/tour-task-2'
  },
  prompt: {
    alias: [],
    category: 'Getting started',
    help: 'Send a random starter prompt',
    handler: _cmdPrompt,
    usage: '/prompt'
  },
  theme: {
    alias: [],
    category: 'Settings',
    help: 'Change color theme',
    handler: _cmdTheme,
    usage: '/theme name'
  },
  settings: {
    alias: ['cfg', 'preferences', 'config'],
    category: 'Settings',
    help: 'Open the Settings panel',
    handler: _cmdSettings,
    usage: '/settings [tab]'
  },
  open: {
    alias: ['show'],
    category: 'Utility',
    hidden: true,
    help: 'Open a tool panel',
    handler: _cmdOpen,
    usage: '/open Cookbook'
  },
  cookbook: {
    alias: ['cook'],
    category: 'Tools',
    help: 'Open Cookbook; use "serve" to jump to model serving',
    handler: (args, ctx) => _cmdToolPanel('cookbook', args, ctx),
    usage: '/cookbook  ·  /cookbook serve qwen'
  },
  email: {
    alias: ['mail', 'inbox'],
    category: 'Tools',
    help: 'Open Email',
    handler: (args, ctx) => _cmdToolPanel('email', args, ctx),
    usage: '/email'
  },
  notes: {
    alias: [],
    category: 'Tools',
    help: 'Open Notes',
    handler: (args, ctx) => _cmdToolPanel('notes', args, ctx),
    usage: '/notes'
  },
  tasks: {
    alias: [],
    category: 'Tools',
    help: 'Open Tasks',
    handler: (args, ctx) => _cmdToolPanel('tasks', args, ctx),
    usage: '/tasks'
  },
  brain: {
    alias: ['memories'],
    category: 'Tools',
    help: 'Open Brain',
    handler: (args, ctx) => _cmdToolPanel('brain', args, ctx),
    usage: '/brain'
  },
  library: {
    alias: ['docs', 'documents'],
    category: 'Tools',
    help: 'Open Library',
    handler: (args, ctx) => _cmdToolPanel('library', args, ctx),
    usage: '/library'
  },
  gallery: {
    alias: ['photos'],
    category: 'Tools',
    help: 'Open Gallery',
    handler: (args, ctx) => _cmdToolPanel('gallery', args, ctx),
    usage: '/gallery'
  },
  research: {
    alias: [],
    category: 'Tools',
    help: 'Open Deep Research',
    handler: (args, ctx) => _cmdToolPanel('research', args, ctx),
    usage: '/research'
  },
  compare: {
    alias: [],
    category: 'Tools',
    help: 'Open Compare',
    handler: (args, ctx) => _cmdToolPanel('compare', args, ctx),
    usage: '/compare'
  },
  models: {
    alias: ['model'],
    category: 'Settings',
    help: 'List available models',
    handler: _cmdModels,
    usage: '/models'
  },
  search: {
    alias: ['ws', 'websearch'],
    category: 'Utility',
    hidden: true,
    help: 'Web search (sends query with web enabled)',
    handler: _cmdWebSearch,
    noUserBubble: true,
    usage: '/search query'
  },
  find: {
    alias: ['search-history'],
    category: 'Utility',
    hidden: true,
    help: 'Search all conversations',
    handler: _cmdSearch,
    usage: '/find query'
  },
  stats: {
    alias: ['df'],
    category: 'Utility',
    hidden: true,
    help: 'Database statistics',
    handler: _cmdStats,
    usage: '/stats'
  },
  compact: {
    alias: [],
    category: 'Utility',
    hidden: true,
    help: 'Compact older chat messages',
    handler: _cmdCompact,
    usage: '/compact'
  },
  sh: {
    alias: ['exec', 'run', 'shell'],
    category: 'Utility',
    hidden: true,
    help: 'Run a shell command',
    handler: _cmdShell,
    usage: '/sh command'
  },
  shortcuts: {
    alias: ['keys', 'keybinds', 'bind'],
    category: 'Utility',
    hidden: true,
    help: 'Show keyboard shortcuts',
    handler: _cmdShortcuts,
    usage: '/shortcuts'
  },
  help: {
    alias: ['?', 'man', 'commands'],
    category: 'Utility',
    hidden: true,
    help: 'This help',
    handler: _cmdHelp,
    usage: '/help'
  },
  note: {
    alias: ['n'],
    category: 'Memory',
    help: 'Quick-save a note',
    handler: _cmdNote,
    usage: '/note text'
  },
  // ── Easter eggs (hidden from /help) ──
  flip:    { alias: ['coin'],       hidden: true, handler: _cmdFlip,    usage: '/flip' },
  roll:    { alias: ['dice', 'r'],  hidden: true, handler: _cmdRoll,    usage: '/roll [NdN|sides]' },
  '8ball': { alias: ['8-ball'],     hidden: true, handler: _cmd8Ball,   usage: '/8ball question' },
  fortune: { alias: ['cookie'],     hidden: true, handler: _cmdFortune, usage: '/fortune' },
  odyssey: { alias: ['homer','quote'],hidden: true, handler: _cmdOdyssey,usage: '/odyssey' },
  ascii:   { alias: ['banner'],     hidden: true, handler: _cmdAscii,   usage: '/ascii [text]' },
  matrix:  { alias: [],             hidden: true, handler: _cmdMatrix,  usage: '/matrix' },
  cowsay:  { alias: ['moo', 'say'], hidden: true, handler: _cmdSay,     usage: '/cowsay [text]' },
  wisdom:  { alias: ['inspire'],    hidden: true, handler: _cmdWisdom,  usage: '/wisdom' },
  uptime:  { alias: [],             hidden: true, handler: _cmdUptime,  usage: '/uptime' },
  ping:    { alias: ['pong'], category: 'Utility', hidden: true, help: 'Check if model endpoints are alive', handler: _cmdPing, usage: '/ping' },
  probe:   { alias: ['test-models'], category: 'Utility', hidden: true, help: 'Test which models actually respond', handler: _cmdProbe, usage: '/probe [endpoint]' },
  color:   { alias: ['colour'],     hidden: true, handler: _cmdColor,   usage: '/color [hex]' },
};

// ── Legacy aliases ────────────────────────────────────────────────
// Maps old flat command names to { parent, sub } so `/new` still works.

export const LEGACY_ALIASES = {
  'new':         { parent: 'chats', sub: 'new' },
  'create':      { parent: 'chats', sub: 'new' },
  'delete':      { parent: 'chats', sub: 'delete' },
  'del':         { parent: 'chats', sub: 'delete' },
  'archive':     { parent: 'chats', sub: 'archive' },
  'rename':      { parent: 'chats', sub: 'rename' },
  'favorite':    { parent: 'chats', sub: 'favorite' },
  'important':   { parent: 'chats', sub: 'favorite' },
  'star':        { parent: 'chats', sub: 'favorite' },
  'unfavorite':  { parent: 'chats', sub: 'unfavorite' },
  'unimportant': { parent: 'chats', sub: 'unfavorite' },
  'unstar':      { parent: 'chats', sub: 'unfavorite' },
  'fork':        { parent: 'chats', sub: 'fork' },
  'truncate':    { parent: 'chats', sub: 'truncate' },
  'sessions':    { parent: 'chats', sub: 'info' },
  'switch':      { parent: 'chats', sub: 'switch' },
  'goto':        { parent: 'chats', sub: 'switch' },
  'sort':        { parent: 'chats', sub: 'sort' },
  'info':        { parent: 'chats', sub: 'info' },
  'clear':       { parent: 'chats', sub: 'clear' },
  'export':      { parent: 'chats', sub: 'export' },
  'web':         { parent: 'toggle', sub: 'web' },
  'bash':        { parent: 'toggle', sub: 'bash' },
  'research':    { parent: 'toggle', sub: 'research' },
  'doc':         { parent: 'toggle', sub: 'doc' },
  'sidebar':     { parent: 'toggle', sub: 'sidebar' },
  'memories':    { parent: 'memory', sub: 'list' },
  'forget':      { parent: 'memory', sub: 'delete' },
  // Linux-style aliases
  'rm':          { parent: 'chats', sub: 'delete' },
  'mv':          { parent: 'chats', sub: 'rename' },
  'cd':          { parent: 'chats', sub: 'switch' },
  'cp':          { parent: 'chats', sub: 'fork' },
  'cat':         { parent: 'chats', sub: 'export' },
  'stat':        { parent: 'chats', sub: 'info' },
  'tar':         { parent: 'chats', sub: 'archive' },
  'mkdir':       { parent: 'chats', sub: 'new' },
  'status':      { parent: 'toggle', sub: '_show' }
};

// ── Dispatch helpers ──────────────────────────────────────────────

/** Build context object for handlers */
function _makeCtx() {
  return {
    sid: sessionModule.getCurrentSessionId(),
    esc: uiModule.esc
  };
}

/** Build a flat map: alias -> canonical command name (from COMMANDS alias arrays) */
function _buildAliasMap() {
  const map = {};
  for (const [name, def] of Object.entries(COMMANDS)) {
    map[name] = name;
    if (def.alias) def.alias.forEach(a => { map[a] = name; });
  }
  return map;
}
const _ALIAS_MAP = _buildAliasMap();

/** Resolve a typed command to its canonical COMMANDS key */
function _resolveCommand(cmd) {
  return _ALIAS_MAP[cmd] || null;
}

/** Resolve a subcommand within a command definition, checking sub aliases */
function _resolveSubcommand(def, sub) {
  if (!def.subs) return null;
  if (def.subs[sub]) return sub;
  for (const [name, sDef] of Object.entries(def.subs)) {
    if (sDef.alias && sDef.alias.includes(sub)) return name;
  }
  return null;
}

/** Levenshtein distance for fuzzy matching */
function _levenshtein(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i-1] === b[j-1]
        ? dp[i-1][j-1]
        : 1 + Math.min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]);
    }
  }
  return dp[m][n];
}

/** Suggest close matches for a mistyped command */
function _fuzzyMatch(typed, maxDist) {
  maxDist = maxDist || 2;
  const candidates = Object.keys(_ALIAS_MAP);
  // Also include legacy alias keys
  Object.keys(LEGACY_ALIASES).forEach(k => { if (!candidates.includes(k)) candidates.push(k); });
  const matches = [];
  for (const c of candidates) {
    const d = _levenshtein(typed, c);
    if (d > 0 && d <= maxDist) matches.push(c);
  }
  return matches;
}

// ── Command prefix ──────────────────────────────────────────────

function _isCmd(str) { return str.startsWith('/') || str.startsWith('!'); }

// ── Main dispatcher ───────────────────────────────────────────────

async function handleSlashCommand(input) {
  const parts = input.slice(1).split(/\s+/);
  const rawCmd = parts[0].toLowerCase();
  let args = parts.slice(1);
  const ctx = _makeCtx();
  let _userShown = false;
  // Tag the echoed command with source:'slash' so it renders in the transcript
  // but is excluded from LLM context (get_context_messages), like the replies.
  function _showUser() { if (!_userShown) { _userShown = true; _addMessage('user', input); _persistMsg('user', input, { source: 'slash' }); } }

  try {
    // --- Check for --help / -h on any command ---
    const wantsHelp = args.includes('--help') || args.includes('-h');

    // --- 1. Try direct command resolution ---
    let cmdKey = _resolveCommand(rawCmd);
    let cmdDef = cmdKey ? COMMANDS[cmdKey] : null;

    // --- 2. Try legacy alias ---
    if (!cmdDef && LEGACY_ALIASES[rawCmd]) {
      const leg = LEGACY_ALIASES[rawCmd];
      cmdDef = COMMANDS[leg.parent];
      cmdKey = leg.parent;
      if (cmdDef && cmdDef.subs) {
        const subDef = cmdDef.subs[leg.sub];
        if (subDef) {
          _showUser();
          if (wantsHelp) {
            const usage = subDef.usage || `/${leg.parent} ${leg.sub}`;
            slashReply(`<pre>${usage}\n${subDef.help || 'No help available.'}</pre>`);
            return true;
          }
          return await subDef.handler(args, ctx);
        }
      } else if (cmdDef && cmdDef.handler) {
        _showUser();
        if (wantsHelp) {
          const usage = cmdDef.usage || `/${cmdKey}`;
          slashReply(`<pre>${usage}\n${cmdDef.help || 'No help available.'}</pre>`);
          return true;
        }
        return await cmdDef.handler(args, ctx);
      }
    }

    // --- 3. Resolved to a known command ---
    if (cmdDef) {
      if (!cmdDef.noUserBubble) _showUser();
      // Command with subcommands
      if (cmdDef.subs) {
        // Show help for the whole group
        if (wantsHelp && !args.filter(a => a !== '--help' && a !== '-h').length) {
          let lines = [`${cmdDef.help || cmdKey}`];
          if (cmdDef.alias && cmdDef.alias.length) lines[0] += ` (aliases: ${cmdDef.alias.map(a => '/'+a).join(', ')})`;
          lines.push('');
          for (const [sub, sDef] of Object.entries(cmdDef.subs)) {
            if (sub.startsWith('_')) continue; // skip internal subs like _show
            const usage = sDef.usage || `/${cmdKey} ${sub}`;
            lines.push(`  ${usage.padEnd(25)}${sDef.help || ''}`);
          }
          slashReply(`<pre>${lines.join('\n')}</pre>`);
          return true;
        }

        // Try to match first arg as subcommand
        const subArg = (args[0] || '').toLowerCase();
        const subKey = subArg ? _resolveSubcommand(cmdDef, subArg) : null;

        if (subKey) {
          const subDef = cmdDef.subs[subKey];
          const subArgs = args.slice(1);
          // Help for specific subcommand
          if (wantsHelp || subArgs.includes('--help') || subArgs.includes('-h')) {
            const usage = subDef.usage || `/${cmdKey} ${subKey}`;
            slashReply(`<pre>${usage}\n${subDef.help || 'No help available.'}</pre>`);
            return true;
          }
          return await subDef.handler(subArgs, ctx);
        }

        // No matching sub — use default if defined
        if (cmdDef.default) {
          const defKey = cmdDef.default;
          const defSub = cmdDef.subs[defKey];
          if (defSub) {
            // For the default sub, pass all args through (they weren't consumed as a sub name)
            return await defSub.handler(args, ctx);
          }
        }

        // Unknown sub — show usage
        slashReply(`Unknown subcommand. Try /${cmdKey} --help`);
        return true;
      }

      // Flat command (no subs)
      if (wantsHelp) {
        const usage = cmdDef.usage || `/${cmdKey}`;
        slashReply(`<pre>${usage}\n${cmdDef.help || 'No help available.'}</pre>`);
        return true;
      }
      return await cmdDef.handler(args, ctx);
    }

    // --- 4. Skill invocation: /<skill-name> [request] ---
    // If `rawCmd` matches a published skill, pin its SKILL.md to the user's
    // message and re-submit. Lets you fire a stored procedure on demand
    // without the model having to discover the skill itself.
    try {
      const skillRes = await fetch(`${API_BASE}/api/skills/${encodeURIComponent(rawCmd)}/markdown`, { credentials: 'same-origin' });
      if (skillRes.ok) {
        const skillData = await skillRes.json();
        const md = skillData.markdown || '';
        if (md) {
          _showUser();
          const request = args.join(' ').trim();
          const msgInput = document.getElementById('message');
          const composed =
            `Apply the skill below to my request, following its Procedure / Pitfalls / Verification.\n\n` +
            `--- BEGIN SKILL ---\n${md}\n--- END SKILL ---\n\n` +
            (request ? `Request: ${request}` : `Request: (use the skill as appropriate)`);
          if (msgInput) {
            msgInput.value = composed;
            const form = document.getElementById('chat-form');
            if (form && typeof form.requestSubmit === 'function') {
              form.requestSubmit();
            } else if (form) {
              form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
            }
          }
          return true;
        }
      }
    } catch (_) { /* fall through to fuzzy match */ }

    // --- 5. Fuzzy match for typos ---
    const suggestions = _fuzzyMatch(rawCmd);
    if (suggestions.length) {
      _showUser();
      slashReply(`Unknown command "/${ctx.esc(rawCmd)}". Did you mean: ${suggestions.map(s => '<b>/'+s+'</b>').join(', ')}?`);
      return true;
    }

  } catch (err) {
    _showUser();
    slashReply(`Error: ${ctx.esc(err.message)}`);
    return true;
  }

  // Unknown slash command — pass through to AI
  return false;
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Initialize the slash commands module.
 * @param {object} deps - Dependencies from chat.js
 * @param {string} deps.apiBase - The API base URL
 * @param {function} deps.isStreaming - Callback returning current streaming state
 */
export function initSlashCommands(deps) {
  API_BASE = deps.apiBase || '';
  if (deps.isStreaming) _isStreamingFn = deps.isStreaming;

  // Global delegation for onboarding and setup clicks
  document.addEventListener('click', (e) => {
    // 1. Check for clicking the "/setup" trigger link on the welcome screen
    const trigger = e.target.closest('.setup-trigger-link');
    if (trigger) {
      e.preventDefault();
      const messageInput = document.getElementById('message');
      if (messageInput) {
        messageInput.value = '/setup';
        messageInput.dispatchEvent(new Event('input', { bubbles: true }));
        messageInput.focus();
        const chatForm = document.getElementById('chat-form');
        if (chatForm) {
          chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
        }
      }
      return;
    }

    // 2. Check for clicking a clickable provider inside the setup guide
    const providerEl = e.target.closest('.setup-clickable-provider');
    if (providerEl) {
      e.preventDefault();
      const providerName = providerEl.textContent.trim();
      const messageInput = document.getElementById('message');
      if (messageInput) {
        const text = providerName + ' sk-';
        messageInput.value = text;
        messageInput.dispatchEvent(new Event('input', { bubbles: true }));
        messageInput.focus();
        messageInput.setSelectionRange(text.length, text.length);
      }
      return;
    }

    // 3. Check for clicking a clickable code block inside the setup guide
    const codeEl = e.target.closest('.setup-clickable-code');
    if (codeEl) {
      e.preventDefault();
      let text = codeEl.textContent.trim();
      if (text.includes('sk-...')) {
        text = text.replace('sk-...', 'sk-');
      }
      const messageInput = document.getElementById('message');
      if (messageInput) {
        messageInput.value = text;
        messageInput.dispatchEvent(new Event('input', { bubbles: true }));
        messageInput.focus();
        messageInput.setSelectionRange(text.length, text.length);
      }
      return;
    }
  });
}

/**
 * Check if input looks like a slash command.
 */
export function isCommand(str) {
  return _isCmd(str);
}

/**
 * Get the current setupMode state.
 */
export function getSetupMode() {
  return setupMode;
}

/**
 * Clear setup mode (e.g. when a slash command is typed during setup).
 */
export function clearSetupMode(preservePendingState = false) {
  setupMode = false;
  if (!preservePendingState) {
    pendingSetupApiKey = '';
    pendingSetupProvider = null;
  }
}

export { handleSlashCommand, handleSetupInput, handleSetupWizard, slashReply, typewriterReply, COMMANDS };

const slashCommands = {
  initSlashCommands,
  isCommand,
  getSetupMode,
  clearSetupMode,
  handleSlashCommand,
  handleSetupInput,
  handleSetupWizard,
  slashReply,
  typewriterReply,
  typewriterInto,
};

export default slashCommands;

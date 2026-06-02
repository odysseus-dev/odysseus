// selectionAssist.js — context-aware learning assistant (#931)
// Text selection in chat/doc panes -> floating popup -> AI chat dispatch.

const ALLOWED_CONTAINERS = ['#chat-history', '#doc-editor-pane'];

const ACTIONS = [
  { label: 'Explain',    prompt: 'Explain the following clearly and concisely' },
  { label: 'Simplify',   prompt: 'Rewrite the following in simpler language' },
  { label: 'Define',     prompt: 'Define the key terms in the following' },
  { label: 'Example',    prompt: 'Give a concrete example of the following' },
  { label: 'ELI5',       prompt: 'Explain the following like I\'m five' },
];

let popup = null;
let currentText = '';

function isInsideAllowed(node) {
  if (!node) return false;
  const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
  return ALLOWED_CONTAINERS.some(s => el?.closest(s));
}

function sendToChat(message) {
  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) return;

  input.value = message;
  input.dispatchEvent(new Event('input', { bubbles: true }));

  if (typeof form.requestSubmit === 'function') {
    form.requestSubmit();
  } else {
    form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
  }
}

function buildPrompt(action, text) {
  return `${action}:\n\n> ${text}`;
}

// -- Popup lifecycle --

function createPopup() {
  const el = document.createElement('div');
  el.className = 'sa-popup';
  el.setAttribute('role', 'toolbar');
  el.setAttribute('aria-label', 'Selection actions');

  const row = document.createElement('div');
  row.className = 'sa-actions';

  for (const { label, prompt } of ACTIONS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sa-btn';
    btn.textContent = label;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      sendToChat(buildPrompt(prompt, currentText));
      hidePopup();
    });
    row.appendChild(btn);
  }

  el.appendChild(row);

  const inputRow = document.createElement('form');
  inputRow.className = 'sa-input-row';
  inputRow.addEventListener('submit', (e) => {
    e.preventDefault();
    const q = inputRow.querySelector('.sa-input').value.trim();
    if (!q) return;
    sendToChat(`${q}\n\nContext:\n> ${currentText}`);
    hidePopup();
  });

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'sa-input';
  input.placeholder = 'Ask about selection...';
  input.setAttribute('aria-label', 'Ask about selection');
  inputRow.appendChild(input);

  const send = document.createElement('button');
  send.type = 'submit';
  send.className = 'sa-send';
  send.textContent = 'Ask';
  inputRow.appendChild(send);

  el.appendChild(inputRow);
  document.body.appendChild(el);
  return el;
}

function positionPopup(rect) {
  if (!popup) return;

  const pad = 8;
  const pw = popup.offsetWidth;
  const ph = popup.offsetHeight;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let top = rect.top - ph - pad;
  let left = rect.left + (rect.width - pw) / 2;

  // flip below if no room above
  if (top < pad) top = rect.bottom + pad;
  // clamp horizontal
  if (left < pad) left = pad;
  if (left + pw > vw - pad) left = vw - pw - pad;
  // clamp vertical
  if (top + ph > vh - pad) top = vh - ph - pad;

  popup.style.top = `${top}px`;
  popup.style.left = `${left}px`;
}

function showPopup(rect, text) {
  currentText = text;
  if (!popup) popup = createPopup();

  const input = popup.querySelector('.sa-input');
  if (input) input.value = '';

  popup.classList.add('sa-visible');
  positionPopup(rect);
}

function hidePopup() {
  if (!popup) return;
  popup.classList.remove('sa-visible');
  currentText = '';
}

// -- Listeners --

let debounceTimer = null;

document.addEventListener('selectionchange', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) { hidePopup(); return; }

    const text = sel.toString().trim();
    if (!text || !isInsideAllowed(sel.anchorNode)) { hidePopup(); return; }

    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    if (!rect.width && !rect.height) { hidePopup(); return; }

    showPopup(rect, text);
  }, 250);
});

document.addEventListener('mousedown', (e) => {
  if (popup && !popup.contains(e.target)) hidePopup();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') hidePopup();
});
/**
 * Reusable wizard chat panel (Lore / Hero steps).
 */

export function createWizardChat({ placeholder = 'Message…', onSend, disabled = false }) {
  const root = document.createElement('div');
  root.className = 'fugassa-wizard-chat';
  root.setAttribute('aria-label', 'Wizard assistant chat');

  const chatLabel = document.createElement('div');
  chatLabel.className = 'fugassa-wizard-chat-label';
  chatLabel.textContent = 'Assistant chat';

  const log = document.createElement('div');
  log.className = 'fugassa-wizard-chat-log';

  const form = document.createElement('form');
  form.className = 'fugassa-wizard-chat-form';
  form.innerHTML = `
    <textarea class="fugassa-wizard-chat-input" rows="2" placeholder="${escapeAttr(placeholder)}"></textarea>
    <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Send</button>
  `;

  const input = form.querySelector('textarea');
  const submitBtn = form.querySelector('button[type="submit"]');
  let _busy = false;
  let _messages = [];

  function setDisabled(v) {
    input.disabled = v;
    submitBtn.disabled = v;
  }

  function renderLog() {
    log.innerHTML = _messages.length
      ? _messages.map((m) => `
          <div class="fugassa-wizard-chat-msg fugassa-wizard-chat-msg--${m.role === 'user' ? 'user' : 'assistant'}">
            <div class="fugassa-wizard-chat-bubble">${escapeHtml(m.content)}</div>
          </div>`).join('')
      : '<p class="fugassa-wizard-chat-empty">Zatím žádné zprávy. Pošli prompt nebo vyber možnost z návrhů.</p>';
    log.scrollTop = log.scrollHeight;
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (_busy || disabled) return;
    const text = (input.value || '').trim();
    if (!text) return;
    input.value = '';
    _messages = [..._messages, { role: 'user', content: text }];
    renderLog();
    _busy = true;
    setDisabled(true);
    try {
      const reply = await onSend(text, _messages);
      if (reply) {
        _messages = [..._messages, { role: 'assistant', content: reply }];
        renderLog();
      }
    } finally {
      _busy = false;
      setDisabled(disabled);
      input.focus();
    }
  });

  root.appendChild(chatLabel);
  root.appendChild(log);
  root.appendChild(form);

  return {
    el: root,
    getMessages: () => _messages.slice(),
    setMessages(msgs) {
      _messages = (msgs || []).slice();
      renderLog();
    },
    appendAssistant(text) {
      if (!text) return;
      _messages = [..._messages, { role: 'assistant', content: text }];
      renderLog();
    },
    setBusy(v) {
      _busy = v;
      setDisabled(v || disabled);
    },
    setDisabled,
  };
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, '&quot;');
}

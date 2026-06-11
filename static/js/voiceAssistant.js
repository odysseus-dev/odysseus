// static/js/voiceAssistant.js
// Dedicated Voice Assistant — speak to the AI, hear it speak back.
// Flow: tap mic → STT → send to AI → TTS → tap mic again (or auto-loop).

const API_BASE = '';

let _modalEl = null;
let _sessionId = null;
let _abortController = null;
let _isActive = false;
let _isListening = false;
let _isSpeaking = false;
let _autoLoop = false;
let _transcript = [];     // { role: 'user'|'ai', text: string }[]
let _vaRecognition = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

function _esc(s) {
  return (s || '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function _el(id) { return document.getElementById(id); }

// Strip markdown/code so TTS speaks cleanly
function _stripForSpeech(text) {
  return text
    .replace(/```[\s\S]*?```/g, ' code block ')
    .replace(/`[^`]+`/g, '')
    .replace(/#{1,6}\s/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/<[^>]+>/g, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim();
}

// ── Session ──────────────────────────────────────────────────────────────────

async function _ensureSession() {
  if (_sessionId) return _sessionId;
  try {
    const fd = new FormData();
    fd.append('name', 'Voice Chat');
    fd.append('skip_validation', 'true');
    const res = await fetch(`${API_BASE}/api/session`, {
      method: 'POST',
      credentials: 'same-origin',
      body: fd,
    });
    if (res.ok) {
      const data = await res.json();
      _sessionId = data.id || data.session_id;
      return _sessionId;
    }
  } catch (e) {
    console.error('Voice Assistant: failed to create session', e);
  }
  return null;
}

// ── Chat stream ───────────────────────────────────────────────────────────────

async function _sendToAI(text) {
  const sessionId = await _ensureSession();
  if (!sessionId) throw new Error('No AI session');

  const fd = new FormData();
  fd.append('message', text);
  fd.append('session', sessionId);

  _abortController = new AbortController();

  const res = await fetch(`${API_BASE}/api/chat_stream`, {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
    signal: _abortController.signal,
  });

  if (!res.ok) throw new Error(`Chat error ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let accumulated = '';
  let buffer = '';

  // Show live typing in the panel
  _upsertTypingBubble('');

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      if (line === 'data: [DONE]') continue;
      try {
        const json = JSON.parse(line.slice(6));
        const delta =
          json.choices?.[0]?.delta?.content ??
          (json.delta !== undefined ? json.delta : null);
        if (delta !== null) {
          accumulated += delta;
          _upsertTypingBubble(accumulated);
        }
      } catch {}
    }
  }

  return accumulated;
}

// ── UI: transcript ────────────────────────────────────────────────────────────

function _upsertTypingBubble(text) {
  const hist = _el('va-history');
  if (!hist) return;
  let bubble = hist.querySelector('.va-typing');
  if (!bubble) {
    bubble = document.createElement('div');
    bubble.className = 'va-msg va-msg-ai va-typing';
    hist.appendChild(bubble);
  }
  bubble.textContent = text;
  hist.scrollTop = hist.scrollHeight;
}

function _commitTypingBubble() {
  const hist = _el('va-history');
  if (!hist) return;
  hist.querySelectorAll('.va-typing').forEach(b => b.classList.remove('va-typing'));
}

function _addBubble(role, text) {
  _transcript.push({ role, text });
  const hist = _el('va-history');
  if (!hist) return;
  const div = document.createElement('div');
  div.className = `va-msg va-msg-${role}`;
  const label = document.createElement('span');
  label.className = 'va-msg-label';
  label.textContent = role === 'user' ? 'You' : 'AI';
  const body = document.createElement('span');
  body.className = 'va-msg-text';
  body.textContent = text;
  div.appendChild(label);
  div.appendChild(body);
  hist.appendChild(div);
  hist.scrollTop = hist.scrollHeight;
}

function _clearHistory() {
  _transcript = [];
  const hist = _el('va-history');
  if (hist) hist.innerHTML = '';
  const interim = _el('va-interim');
  if (interim) interim.textContent = '';
}

// ── Status / mic ring ─────────────────────────────────────────────────────────

function _setStatus(text, cls) {
  const el = _el('va-status');
  if (!el) return;
  el.textContent = text;
  el.className = 'va-status ' + (cls || '');
}

function _setMicState(state) {
  // state: 'idle' | 'listening' | 'thinking' | 'speaking'
  const btn = _el('va-mic-btn');
  if (btn) btn.dataset.state = state;
  const ring = _el('va-mic-ring');
  if (ring) ring.className = `va-mic-ring va-mic-ring-${state}`;
}

// ── TTS ──────────────────────────────────────────────────────────────────────

async function _speakText(text) {
  _isSpeaking = true;
  _setStatus('Speaking…', 'speaking');
  _setMicState('speaking');

  const plain = _stripForSpeech(text);
  if (!plain) { _isSpeaking = false; return; }

  try {
    const mgr = window.aiTTSManager;

    // Prefer the existing AI TTS manager if it's available
    if (mgr && mgr.available && mgr._provider !== 'disabled') {
      await new Promise((resolve) => {
        if (mgr.useBrowserTTS) {
          const utt = new SpeechSynthesisUtterance(plain);
          const voice = mgr._findBrowserVoice ? mgr._findBrowserVoice() : null;
          if (voice) utt.voice = voice;
          utt.rate = mgr.playbackSpeed || 1;
          utt.onend = resolve;
          utt.onerror = resolve;
          window.speechSynthesis.speak(utt);
        } else {
          mgr.synthesize(plain).then((url) => {
            const audio = new Audio(url);
            audio.playbackRate = mgr.playbackSpeed || 1;
            audio.onended = resolve;
            audio.onerror = resolve;
            audio.play().catch(resolve);
          }).catch(resolve);
        }
      });
    } else if ('speechSynthesis' in window) {
      // Fallback: browser Web Speech API
      await new Promise((resolve) => {
        window.speechSynthesis.cancel();
        const utt = new SpeechSynthesisUtterance(plain);
        utt.onend = resolve;
        utt.onerror = resolve;
        window.speechSynthesis.speak(utt);
      });
    }
  } catch (e) {
    if (e.name !== 'AbortError') console.warn('VA TTS error:', e);
  } finally {
    _isSpeaking = false;
  }
}

// ── STT ───────────────────────────────────────────────────────────────────────

async function _startListening() {
  if (_isListening || _isSpeaking) return;
  _isListening = true;

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  if (SpeechRecognition) {
    await _listenBrowserSTT();
  } else {
    // Fallback: mic recording → server Whisper
    await _listenServerSTT();
  }
}

function _listenBrowserSTT() {
  return new Promise((resolve) => {
    _setStatus('Listening…', 'listening');
    _setMicState('listening');

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SpeechRecognition();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = '';
    _vaRecognition = rec;

    let finalText = '';
    const interimEl = _el('va-interim');

    rec.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          finalText += event.results[i][0].transcript + ' ';
        } else {
          interim += event.results[i][0].transcript;
        }
      }
      if (interimEl) interimEl.textContent = (finalText + interim).trim();
    };

    rec.onerror = (e) => {
      _isListening = false;
      _vaRecognition = null;
      if (interimEl) interimEl.textContent = '';
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        _setStatus('Microphone access denied', 'error');
        _setMicState('idle');
      } else if (e.error !== 'no-speech' && e.error !== 'aborted') {
        console.warn('VA STT error:', e.error);
        _setStatus('Tap mic to speak', 'idle');
        _setMicState('idle');
      }
      resolve('');
    };

    rec.onend = async () => {
      _isListening = false;
      _vaRecognition = null;
      if (interimEl) interimEl.textContent = '';
      const text = finalText.trim();
      if (text && _isActive) {
        await _handleUserInput(text);
      } else if (_autoLoop && _isActive && !text) {
        // No speech detected — re-listen if auto-loop
        setTimeout(_startListening, 400);
      } else {
        _setStatus('Tap mic to speak', 'idle');
        _setMicState('idle');
      }
      resolve(text);
    };

    rec.start();
  });
}

let _serverSTTChunks = [];
let _serverSTTRecorder = null;

function _listenServerSTT() {
  return new Promise((resolve) => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      _setStatus('Microphone not available', 'error');
      _isListening = false;
      resolve('');
      return;
    }
    _setStatus('Listening… (tap again to send)', 'listening');
    _setMicState('listening');
    _serverSTTChunks = [];

    navigator.mediaDevices.getUserMedia({ audio: true })
      .then((stream) => {
        const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' });
        _serverSTTRecorder = mr;
        mr.ondataavailable = (e) => { if (e.data.size > 0) _serverSTTChunks.push(e.data); };
        mr.onstop = async () => {
          stream.getTracks().forEach(t => t.stop());
          _serverSTTRecorder = null;
          _isListening = false;
          const blob = new Blob(_serverSTTChunks, { type: 'audio/webm' });
          _setStatus('Transcribing…', 'thinking');
          _setMicState('thinking');
          try {
            const fd = new FormData();
            fd.append('file', blob, 'audio.webm');
            const res = await fetch('/api/stt/transcribe', {
              method: 'POST', credentials: 'same-origin', body: fd,
            });
            if (res.ok) {
              const data = await res.json();
              const text = (data.text || '').trim();
              if (text && _isActive) {
                await _handleUserInput(text);
              } else {
                _setStatus('No speech detected — tap to try again', 'idle');
                _setMicState('idle');
              }
              resolve(text);
            } else {
              _setStatus('Transcription failed — tap to try again', 'error');
              _setMicState('idle');
              resolve('');
            }
          } catch (e) {
            console.error('VA server STT error:', e);
            _setStatus('Error — tap to try again', 'error');
            _setMicState('idle');
            resolve('');
          }
        };
        mr.start();
      })
      .catch((err) => {
        _isListening = false;
        _serverSTTRecorder = null;
        if (err.name === 'NotAllowedError') {
          _setStatus('Microphone access denied', 'error');
        } else {
          _setStatus('Microphone error — tap to retry', 'error');
        }
        _setMicState('idle');
        resolve('');
      });
  });
}

// ── Main interaction loop ─────────────────────────────────────────────────────

async function _handleUserInput(text) {
  if (!text || !_isActive) return;

  // Show user message
  _addBubble('user', text);
  _setStatus('Thinking…', 'thinking');
  _setMicState('thinking');

  try {
    const aiText = await _sendToAI(text);
    _commitTypingBubble();
    // Remove the typing placeholder and add proper bubble
    const hist = _el('va-history');
    if (hist) {
      const last = hist.lastElementChild;
      if (last && !last.classList.contains('va-msg-ai')) {
        _addBubble('ai', aiText);
      } else if (last) {
        // Already shown via typing bubble, just commit it
        _transcript.push({ role: 'ai', text: aiText });
      }
    } else {
      _addBubble('ai', aiText);
    }

    if (!_isActive) return;
    await _speakText(aiText);
  } catch (e) {
    if (e.name === 'AbortError') return;
    console.error('VA AI error:', e);
    _setStatus('AI error — tap to try again', 'error');
    _setMicState('idle');
    return;
  }

  if (!_isActive) return;

  if (_autoLoop) {
    // Hands-free: auto-listen after speaking
    _setStatus('Listening…', 'listening');
    setTimeout(_startListening, 400);
  } else {
    _setStatus('Tap mic to speak', 'idle');
    _setMicState('idle');
  }
}

// ── Stop/interrupt ────────────────────────────────────────────────────────────

function _stopAll(keepActive) {
  if (!keepActive) _isActive = false;
  _isListening = false;
  _isSpeaking = false;

  if (_vaRecognition) {
    try { _vaRecognition.abort(); } catch {}
    _vaRecognition = null;
  }
  if (_serverSTTRecorder && _serverSTTRecorder.state === 'recording') {
    try { _serverSTTRecorder.stop(); } catch {}
    _serverSTTRecorder = null;
  }
  if (_abortController) { _abortController.abort(); _abortController = null; }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  if (window.aiTTSManager) window.aiTTSManager.stop();
}

// ── Mic button handler ────────────────────────────────────────────────────────

function _onMicClick() {
  const btn = _el('va-mic-btn');
  if (!btn) return;
  const state = btn.dataset.state;

  if (state === 'listening') {
    // Stop listening and submit what we have (or just stop)
    _isListening = false;
    if (_vaRecognition) {
      try { _vaRecognition.stop(); } catch {} // triggers onend → _handleUserInput
    } else if (_serverSTTRecorder && _serverSTTRecorder.state === 'recording') {
      _serverSTTRecorder.stop(); // triggers onstop → transcribe
    } else {
      _setMicState('idle');
      _setStatus('Tap mic to speak', 'idle');
    }
  } else if (state === 'thinking' || state === 'speaking') {
    // Interrupt AI response
    _stopAll(true);
    _isActive = true;
    const typingEl = _el('va-history')?.querySelector('.va-typing');
    if (typingEl) {
      typingEl.classList.remove('va-typing');
    }
    _setMicState('idle');
    _setStatus('Tap mic to speak', 'idle');
  } else {
    // Idle → start listening
    _isActive = true;
    _startListening();
  }
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function _buildModal() {
  const el = document.createElement('div');
  el.id = 'voice-assistant-modal';
  el.className = 'modal hidden';
  el.innerHTML = `
    <div class="modal-content va-modal-content" role="dialog" aria-label="Voice Assistant">
      <div class="modal-header">
        <h4>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            style="vertical-align:-2px;margin-right:6px;">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" y1="19" x2="12" y2="23"/>
            <line x1="8" y1="23" x2="16" y2="23"/>
          </svg>
          Voice Assistant
        </h4>
        <label class="va-autoloop-label" title="Automatically start listening after AI finishes speaking">
          <input type="checkbox" id="va-autoloop-toggle" />
          <span>Hands-free</span>
        </label>
        <button class="close-btn" id="va-close-btn" aria-label="Close">✖</button>
      </div>

      <div class="modal-body va-body">
        <!-- Conversation transcript -->
        <div id="va-history" class="va-history" aria-live="polite" aria-label="Conversation"></div>

        <!-- Interim live transcript (while user speaks) -->
        <div id="va-interim" class="va-interim" aria-live="polite"></div>

        <!-- Mic area -->
        <div class="va-mic-area">
          <div id="va-mic-ring" class="va-mic-ring va-mic-ring-idle">
            <button id="va-mic-btn" class="va-mic-btn" data-state="idle"
              title="Tap to speak — tap again to stop">
              <!-- Mic icon (idle / thinking / speaking) -->
              <svg class="va-icon-mic" width="32" height="32" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" y1="19" x2="12" y2="23"/>
                <line x1="8" y1="23" x2="16" y2="23"/>
              </svg>
              <!-- Stop icon (shown while listening) -->
              <svg class="va-icon-stop" width="28" height="28" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="3"/>
              </svg>
            </button>
          </div>
          <div id="va-status" class="va-status">Tap the mic to start</div>
          <button id="va-clear-btn" class="va-clear-btn" title="Clear conversation and start fresh">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:4px;">
              <path d="M3 6h18"/><path d="M8 6V4h8v2"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
            </svg>
            Clear
          </button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(el);

  // Wire events
  el.querySelector('#va-mic-btn').addEventListener('click', _onMicClick);

  el.querySelector('#va-autoloop-toggle').addEventListener('change', (e) => {
    _autoLoop = e.target.checked;
  });

  el.querySelector('#va-clear-btn').addEventListener('click', () => {
    _stopAll(false);
    _sessionId = null;
    _clearHistory();
    _setMicState('idle');
    _setStatus('Tap the mic to start', 'idle');
  });

  el.querySelector('#va-close-btn').addEventListener('click', _closeModal);
  el.addEventListener('click', (e) => { if (e.target === el) _closeModal(); });

  return el;
}

function _closeModal() {
  _stopAll(false);
  if (_modalEl) {
    _modalEl.classList.add('hidden');
    _modalEl.style.display = '';
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export function openVoiceAssistant() {
  if (!_modalEl) _modalEl = _buildModal();
  _modalEl.classList.remove('hidden');
  _modalEl.style.display = 'flex';
  _isActive = false;
  _setMicState('idle');
  _setStatus('Tap the mic to start', 'idle');
}

const voiceAssistantModule = { openVoiceAssistant };
export default voiceAssistantModule;

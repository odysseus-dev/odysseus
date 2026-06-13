// static/js/voiceDialog.js

/**
 * Voice dialog mode v2 — hands-free conversation loop.
 *
 * listen (Silero VAD, RMS fallback) → transcribe → auto-send →
 * streaming TTS speaks → (barge-in allowed) → listen again.
 *
 * v2a: vadEngine, barge-in during playback, waveform + state chip.
 */

import { createVadEngine } from './vadEngine.js';
import { createSttStream } from './sttStream.js';

const BARGE_SUSTAIN_MS = 500;       // speech must persist this long during playback
const THRESHOLD_LISTEN = 0.5;
const THRESHOLD_PLAYBACK = 0.8;     // raised while TTS audible (self-echo guard)
const MAX_ANSWER_WAIT_MS = 180000;

// States: 'off' | 'listening' | 'transcribing' | 'waiting' | 'speaking' | 'standby'
let _state = 'off';
let _btn = null;
let _chip = null;
let _wave = null;
let _waveRAF = null;
let _engine = null;
let _bargeTimer = null;
let _watchTimer = null;
let _watchStartedAt = 0;
let _sawTTSActivity = false;
let _stt = null;        // sttStream instance or null
let _useStream = false; // false → v1 single-shot POST fallback
let _pendingAudio = null;   // VAD audio kept for fallback while awaiting stream final
let _finalTimer = null;     // watchdog: no final within window → fallback
let _standbyEnabled = false; // toggle cycle: off → dialog → dialog+standby → off
let _browserMode = false;    // stt_provider=browser → Web Speech API loop, no VAD/WS
let _rec = null;             // active SpeechRecognition (browser mode)

const ICON_DIALOG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M9 10h.01M12 10h.01M15 10h.01"/></svg>';

function _mgr() { return window.aiTTSManager || null; }

function _micStream() {
  if (!_engine) return null;
  if (_engine._vad && _engine._vad.stream) return _engine._vad.stream;   // silero
  if (_engine._stream) return _engine._stream;                          // rms
  return null;
}

// ── UI ──

function _injectStyles() {
  if (document.getElementById('voice-dialog-style')) return;
  const css = `
    .voice-dialog-btn { background: none; border: 1px solid var(--input-border, #555);
      border-radius: 8px; cursor: pointer; padding: 6px 8px; margin-right: 4px;
      color: var(--fg, #ccc); opacity: 0.75; flex: none; }
    .voice-dialog-btn:hover { opacity: 1; }
    .voice-dialog-btn.vd-on { opacity: 1; border-color: var(--accent-primary, #7aa2f7);
      color: var(--accent-primary, #7aa2f7); }
    .voice-dialog-btn.vd-listening { animation: vd-pulse 1.2s ease-in-out infinite; }
    .voice-dialog-btn.vd-speaking { color: var(--accent-error, #f7768e);
      border-color: var(--accent-error, #f7768e); }
    @keyframes vd-pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(122,162,247,0.45); }
      50% { box-shadow: 0 0 0 5px rgba(122,162,247,0.05); } }
    .vd-chip { font-size: 10px; padding: 1px 7px; border-radius: 9px; margin-right: 4px;
      border: 1px solid var(--input-border, #555); color: var(--fg, #aaa);
      align-self: center; flex: none; display: none; }
    .vd-chip.show { display: inline-block; }
    .vd-wave { width: 56px; height: 22px; margin-right: 4px; align-self: center;
      flex: none; display: none; }
    .vd-wave.show { display: inline-block; }
  `;
  const style = document.createElement('style');
  style.id = 'voice-dialog-style';
  style.textContent = css;
  document.head.appendChild(style);
}

function _setUI(stateClass, chipText, title) {
  if (!_btn) return;
  _btn.classList.remove('vd-listening', 'vd-transcribing', 'vd-waiting', 'vd-speaking', 'vd-on');
  if (stateClass) _btn.classList.add('vd-on', stateClass);
  _btn.title = title;
  if (_chip) {
    _chip.textContent = chipText || '';
    _chip.classList.toggle('show', !!chipText);
  }
  if (_wave) _wave.classList.toggle('show', _state !== 'off');
}

function _drawWave() {
  if (!_wave || !_engine || !_engine.analyserNode || _state === 'off') { _waveRAF = null; return; }
  const ctx2d = _wave.getContext('2d');
  const an = _engine.analyserNode;
  const buf = new Float32Array(an.fftSize);
  an.getFloatTimeDomainData(buf);
  const w = _wave.width, h = _wave.height;
  ctx2d.clearRect(0, 0, w, h);
  ctx2d.strokeStyle = _state === 'speaking' ? '#f7768e' : '#7aa2f7';
  ctx2d.lineWidth = 1.5;
  ctx2d.beginPath();
  const step = Math.max(1, Math.floor(buf.length / w));
  for (let x = 0; x < w; x++) {
    const v = buf[x * step] || 0;
    const y = h / 2 + v * h * 1.6;
    if (x === 0) ctx2d.moveTo(x, y); else ctx2d.lineTo(x, y);
  }
  ctx2d.stroke();
  _waveRAF = requestAnimationFrame(_drawWave);
}

function _startWave() { if (!_waveRAF) _waveRAF = requestAnimationFrame(_drawWave); }

// ── Audio encoding (Float32Array @16k → WAV blob) ──

function _float32ToWavBlob(f32, sampleRate = 16000) {
  const len = f32.length;
  const buf = new ArrayBuffer(44 + len * 2);
  const v = new DataView(buf);
  const wstr = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
  wstr(0, 'RIFF'); v.setUint32(4, 36 + len * 2, true); wstr(8, 'WAVE');
  wstr(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
  v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  wstr(36, 'data'); v.setUint32(40, len * 2, true);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

// ── Speech events ──

function _onSpeechStart() {
  if (_useStream && _stt && _state === 'listening') _stt.abortUtterance();
  if (_state === 'speaking') {
    // Barge-in candidate: must sustain BARGE_SUSTAIN_MS
    if (_bargeTimer) clearTimeout(_bargeTimer);
    _bargeTimer = setTimeout(() => {
      if (_state !== 'speaking') return;
      const mgr = _mgr();
      if (mgr) { try { mgr.stop(); } catch (_) {} }
      _resumeListening();
    }, BARGE_SUSTAIN_MS);
  }
}

async function _fallbackTranscribe(audio) {
  let blob;
  if (audio instanceof Blob) blob = audio;
  else if (audio && audio.length) blob = _float32ToWavBlob(audio);
  else { _resumeListening(); return; }

  let text = '';
  try {
    const fd = new FormData();
    fd.append('file', blob, 'dialog.wav');
    const res = await fetch('/api/stt/transcribe', { method: 'POST', body: fd, credentials: 'same-origin' });
    if (res.ok) text = ((await res.json()).text || '').trim();
    else console.warn('Voice dialog: STT HTTP', res.status);
  } catch (err) { console.warn('Voice dialog: STT failed', err); }
  if (_state === 'off') return;

  if (!text) { _toast('Heard nothing — listening again'); _resumeListening(); return; }
  _send(text);
}

async function _onSpeechEnd(audio) {
  if (_bargeTimer) { clearTimeout(_bargeTimer); _bargeTimer = null; }
  if (_state !== 'listening') return;

  _state = 'transcribing';
  _setUI('vd-transcribing', 'transcribing', 'Voice dialog: transcribing…');
  _engine.pause();

  if (_useStream && _stt && _stt.connected) {
    _pendingAudio = audio;
    _stt.endUtterance(); // final arrives via onFinal → _send
    if (_finalTimer) clearTimeout(_finalTimer);
    _finalTimer = setTimeout(() => {
      if (_state === 'transcribing') {
        console.warn('Voice dialog: no stream final within 10s → single-shot fallback');
        _useStream = false;
        _fallbackTranscribe(_pendingAudio);
      }
    }, 10000);
    return;
  }

  await _fallbackTranscribe(audio);
}

function _resumeListening() {
  if (_state === 'off') return;
  if (_browserMode) { _startBrowserTurn(); return; }
  _state = 'listening';
  _engine.setThreshold(THRESHOLD_LISTEN);
  _engine.start();
  if (_useStream && _stt && _stt.connected) {
    _stt.abortUtterance();                       // drop any stale server-side audio
    const mic = _micStream();
    if (mic) { _stt.attach(mic).catch(() => { _useStream = false; }); }
  }
  _setUI('vd-listening', 'listening', 'Voice dialog: listening… (click to stop)');
  _startWave();
}

// ── Browser STT mode (Web Speech API — stt_provider=browser) ──
// One SpeechRecognition per turn: the browser endpoints on silence and emits
// a final result; interims render greyed in the input. No VAD, no WS — the
// phone/OS recognizer (Apple/Google) handles languages natively.

function _stopBrowserRec() {
  if (_rec) {
    _rec.onresult = _rec.onend = _rec.onerror = null;
    try { _rec.abort(); } catch (_) {}
    _rec = null;
  }
}

function _startBrowserTurn() {
  if (_state === 'off') return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { _toast('Browser speech recognition unavailable'); stop(); return; }
  _stopBrowserRec();

  _state = 'listening';
  _setUI('vd-listening', 'listening (browser)', 'Voice dialog: listening… (click to stop)');

  const input = document.getElementById('message');
  let finalText = '';

  _rec = new SR();
  _rec.continuous = false;        // browser endpoints the turn on silence
  _rec.interimResults = true;
  _rec.lang = '';                 // OS/page locale decides (fr/en native)

  _rec.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) finalText += event.results[i][0].transcript + ' ';
      else interim += event.results[i][0].transcript;
    }
    if (input) {
      input.value = (finalText + interim).trim();
      input.style.opacity = '0.55';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };

  _rec.onerror = (e) => {
    if (e.error === 'no-speech' || e.error === 'aborted') return; // onend restarts
    console.warn('Voice dialog: browser STT error:', e.error);
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
      _toast('Microphone permission denied');
      stop();
    }
  };

  _rec.onend = () => {
    if (_state !== 'listening') return;
    if (input) input.style.opacity = '';
    const text = finalText.trim();
    if (text) { _rec = null; _send(text); }
    else _startBrowserTurn();      // heard nothing — listen again
  };

  try { _rec.start(); } catch (err) {
    console.warn('Voice dialog: recognition start failed', err);
    _toast('Speech recognition failed to start');
    stop();
  }
}

function _enterStandby() {
  if (_state === 'off') return;
  if (!_standbyEnabled || !_useStream || !_stt || !_stt.connected) {
    _resumeListening(); // no wake channel → stay in open-mic dialog
    return;
  }
  _state = 'standby';
  _engine.pause();                       // VAD off — server listens for the wake word
  _stt.setMode('wake');
  const mic = _micStream();
  if (mic) { _stt.attach(mic).catch(() => { _useStream = false; _resumeListening(); }); }
  _setUI('vd-on', 'standby', 'Voice dialog: standby — say "hey soloway" (click to stop)');
}

// ── Send + answer watch ──

function _send(text) {
  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) { stop(); return; }

  input.value = text;
  input.dispatchEvent(new Event('input', { bubbles: true }));

  _state = 'waiting';
  _setUI('vd-waiting', 'thinking', 'Voice dialog: answering…');
  _sawTTSActivity = false;
  _watchStartedAt = Date.now();

  if (_engine) {
    // Keep the mic open at the raised threshold so barge-in works during the answer
    _engine.setThreshold(THRESHOLD_PLAYBACK);
    _engine.start();
    _startWave();
  }
  if (_useStream && _stt) _stt.detach(); // stop streaming frames during the answer
  if (_browserMode) _stopBrowserRec();   // no recognition while the answer plays (echo)

  if (typeof form.onsubmit === 'function') form.onsubmit(new Event('submit'));
  else if (form.requestSubmit) form.requestSubmit();

  _watchAnswer();
}

function _watchAnswer() {
  if (_watchTimer) { clearTimeout(_watchTimer); _watchTimer = null; }
  if (_state !== 'waiting' && _state !== 'speaking') return;

  const mgr = _mgr();
  const ttsBusy = !!(mgr && (mgr._streamActive || mgr._processing || (mgr._queue && mgr._queue.length)));

  if (ttsBusy) {
    _sawTTSActivity = true;
    if (_state !== 'speaking') {
      _state = 'speaking';
      _setUI('vd-speaking', 'speaking', 'Voice dialog: speaking… (speak to interrupt, click to stop)');
    }
  } else if (_sawTTSActivity) {
    if (_browserMode) _startBrowserTurn();
    else _enterStandby();   // falls back to _resumeListening() when standby is off
    return;
  }

  if (Date.now() - _watchStartedAt > MAX_ANSWER_WAIT_MS) { _resumeListening(); return; }
  _watchTimer = setTimeout(_watchAnswer, 400);
}

// ── Public toggle ──

async function start() {
  const mgr = _mgr();
  if (!window.isSecureContext) { _toast('Voice dialog requires HTTPS'); return; }
  if (mgr && !mgr.available) {
    // The manager probes /api/tts/stats once at page load; a server restart
    // under an open tab leaves it stale. Re-probe live before refusing.
    try {
      const res = await fetch('/api/tts/stats', { credentials: 'same-origin' });
      const stats = await res.json();
      if (stats.available && stats.ready) {
        mgr.available = true;
        mgr._provider = stats.provider || mgr._provider;
        mgr.playbackSpeed = stats.speed || mgr.playbackSpeed;
      }
    } catch (_) { /* fall through to the refusal below */ }
  }
  if (!mgr || !mgr.available) { _toast('Enable TTS first (Settings → TTS)'); return; }

  _state = 'listening';
  mgr.autoPlay = true;
  _setUI('vd-listening', 'starting…', 'Voice dialog: starting…');

  // Provider decides the capture path: browser → Web Speech API (no VAD/WS).
  _browserMode = false;
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    const stats = await res.json();
    _browserMode = stats.provider === 'browser';
  } catch (_) { /* default to server path */ }

  if (_browserMode) {
    _startBrowserTurn();
    return;
  }

  _setUI('vd-listening', 'loading VAD…', 'Voice dialog: starting…');
  try {
    _engine = await createVadEngine({
      onSpeechStart: _onSpeechStart,
      onSpeechEnd: _onSpeechEnd,
      positiveSpeechThreshold: THRESHOLD_LISTEN,
    });
    await _engine.start();
  } catch (err) {
    console.warn('Voice dialog: engine start failed', err);
    _toast('Microphone unavailable');
    stop();
    return;
  }
  if (_state === 'off') return;
  try {
    _stt = await createSttStream({
      onPartial: (text) => {
        if (_state !== 'listening') return;
        const input = document.getElementById('message');
        if (input) {
          input.value = text;
          input.style.opacity = '0.55';
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      },
      onFinal: (text) => {
        if (_finalTimer) { clearTimeout(_finalTimer); _finalTimer = null; }
        _pendingAudio = null;
        const input = document.getElementById('message');
        if (input) input.style.opacity = '';
        if (_state !== 'transcribing') return;
        if (!text || !text.trim()) { _toast('Heard nothing — listening again'); _resumeListening(); return; }
        _send(text.trim());
      },
      onError: (err) => {
        console.warn('Voice dialog: stt stream degraded → single-shot fallback', err);
        _useStream = false;
        if (_stt) { try { _stt.detach(); } catch (_) {} }
        if (_finalTimer) { clearTimeout(_finalTimer); _finalTimer = null; }
        if (_state === 'transcribing') _fallbackTranscribe(_pendingAudio);
        if (_state === 'standby') _resumeListening(); // wake channel dead → open-mic dialog
      },
      onWake: () => {
        if (_state !== 'standby') return;
        _resumeListening();
      },
    });
    const micStream = _micStream();
    if (micStream) { await _stt.attach(micStream); _useStream = true; }
  } catch (err) {
    console.warn('Voice dialog: stt stream unavailable → single-shot fallback', err);
    _useStream = false;
    _stt = null;
  }
  _setUI('vd-listening', `listening (${_engine.kind})`, 'Voice dialog: listening… (click to stop)');
  _startWave();
}

function stop() {
  _standbyEnabled = false;
  _state = 'off';
  _stopBrowserRec();
  _browserMode = false;
  if (_watchTimer) { clearTimeout(_watchTimer); _watchTimer = null; }
  if (_bargeTimer) { clearTimeout(_bargeTimer); _bargeTimer = null; }
  if (_finalTimer) { clearTimeout(_finalTimer); _finalTimer = null; }
  _pendingAudio = null;
  if (_waveRAF) { cancelAnimationFrame(_waveRAF); _waveRAF = null; }
  if (_engine) { try { _engine.stop(); } catch (_) {} _engine = null; }
  const mgr = _mgr();
  if (mgr) { mgr.autoPlay = false; try { mgr.stop(); } catch (_) {} }
  if (_stt) { try { _stt.close(); } catch (_) {} _stt = null; }
  _useStream = false;
  const inputEl = document.getElementById('message');
  if (inputEl) inputEl.style.opacity = '';
  _setUI(null, '', 'Voice dialog mode');
}

function _toggle() {
  if (_state === 'off') {
    _standbyEnabled = false;
    start();
  } else if (!_standbyEnabled && !_browserMode) {
    _standbyEnabled = true;
    _toast('Standby mode: after each reply, say "hey soloway" to continue');
    if (_state === 'listening') _enterStandby();
  } else {
    // Browser mode has no wake-word channel — second tap turns off.
    stop();
  }
}

function _toast(msg) {
  if (window.uiModule && window.uiModule.showToast) window.uiModule.showToast(msg);
  else console.info('Voice dialog:', msg);
}

// ── Init ──

function init() {
  const sendBtn = document.querySelector('.send-btn');
  if (!sendBtn || !sendBtn.parentElement) { setTimeout(init, 1000); return; }
  if (document.getElementById('voice-dialog-btn')) return;

  _injectStyles();

  _wave = document.createElement('canvas');
  _wave.className = 'vd-wave';
  _wave.width = 56; _wave.height = 22;

  _chip = document.createElement('span');
  _chip.className = 'vd-chip';

  _btn = document.createElement('button');
  _btn.type = 'button';
  _btn.id = 'voice-dialog-btn';
  _btn.className = 'voice-dialog-btn';
  _btn.title = 'Voice dialog mode';
  _btn.setAttribute('aria-label', 'Voice dialog mode');
  _btn.innerHTML = ICON_DIALOG;
  _btn.addEventListener('click', (e) => { e.preventDefault(); _toggle(); });

  sendBtn.parentElement.insertBefore(_wave, sendBtn);
  sendBtn.parentElement.insertBefore(_chip, sendBtn);
  sendBtn.parentElement.insertBefore(_btn, sendBtn);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _state !== 'off') stop();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export default { start, stop, getState: () => _state };

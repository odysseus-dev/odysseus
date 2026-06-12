# Voice Dialog v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Odysseus voice dialog mode with Silero VAD, barge-in, live UI feedback, WebSocket streaming STT with partial transcripts, and "hey soloway" wake word.

**Architecture:** Three shippable slices. v2a is frontend-only: vendored `@ricky0123/vad-web` replaces the RMS loop, mic stays open during playback for barge-in, waveform+state chip added. v2b adds `WS /api/stt/stream` (rolling faster-whisper partials) and an AudioWorklet PCM pipeline. v2c reuses the v2b socket in `wake` mode with server-side openwakeword.

**Tech Stack:** Plain ES modules (no build step — vendor dist files), FastAPI WebSocket, faster-whisper (ndarray input), openwakeword, pytest + fastapi TestClient.

**Spec:** `docs/superpowers/specs/2026-06-12-voice-dialog-v2-design.md`

**Repo note:** This is the upstream clone on branch `dev`. ALL work happens on a feature branch (Task 0). Never commit to `dev` directly.

---

## File Structure

```
static/vendor/vad/              NEW  vendored vad-web + onnxruntime-web dist (v2a)
static/js/vadEngine.js          NEW  VAD wrapper: start/stop/onSpeechStart/onSpeechEnd, RMS fallback (v2a)
static/js/voiceDialog.js        EDIT FSM only; uses vadEngine; barge-in; 3-state toggle; chip+waveform (v2a,b,c)
static/js/sttStream.js          NEW  WS client + AudioWorklet PCM16/16k capture (v2b)
static/js/pcmWorklet.js         NEW  AudioWorkletProcessor: downsample to 16k PCM16 (v2b)
static/index.html               EDIT script tags (v2a, v2b)
services/stt/stt_service.py     EDIT add transcribe_array(ndarray) (v2b)
routes/stt_stream_routes.py     NEW  WS /api/stt/stream: dictation + wake modes (v2b, v2c)
services/stt/wakeword.py        NEW  OpenWakeWord wrapper (v2c)
app.py                          EDIT register stream router after line 635 (v2b)
tests/test_stt_stream.py        NEW  WS route tests w/ mock service (v2b, v2c)
tests/test_wakeword_service.py  NEW  wakeword wrapper tests w/ mock model (v2c)
```

---

### Task 0: Feature branch

**Files:** none

- [ ] **Step 1: Create branch**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
git checkout -b feature/voice-dialog-v2
```

- [ ] **Step 2: Commit the two spec/plan docs**

```bash
git add docs/superpowers/specs/2026-06-12-voice-dialog-v2-design.md docs/superpowers/plans/2026-06-12-voice-dialog-v2.md
git commit -m "docs: voice dialog v2 spec + implementation plan"
```

---

## Slice v2a — Silero VAD + barge-in + UI

### Task 1: Vendor VAD assets

**Files:**
- Create: `static/vendor/vad/` (bundle.min.js, vad.worklet.bundle.min.js, *.onnx, ort.min.js, *.wasm)

- [ ] **Step 1: Download and extract dist files**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
mkdir -p static/vendor/vad /tmp/vadpkg && cd /tmp/vadpkg
curl -sLO https://registry.npmjs.org/@ricky0123/vad-web/-/vad-web-0.0.30.tgz
curl -sLO https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.17.1.tgz
tar xzf vad-web-0.0.30.tgz --transform 's,^package,vad-web,'
tar xzf onnxruntime-web-1.17.1.tgz --transform 's,^package,ort-web,'
DEST=/run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus/static/vendor/vad
cp vad-web/dist/bundle.min.js vad-web/dist/vad.worklet.bundle.min.js vad-web/dist/*.onnx "$DEST/"
cp ort-web/dist/ort.min.js ort-web/dist/*.wasm "$DEST/"
ls -la "$DEST"
```

Expected: `bundle.min.js`, `vad.worklet.bundle.min.js`, at least one `silero_vad*.onnx`, `ort.min.js`, several `ort-wasm*.wasm` files. If `vad-web/dist` filenames differ, list `vad-web/dist/` and copy the equivalents — the names above are from vad-web 0.0.30.

- [ ] **Step 2: Verify the files are served**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7000/static/vendor/vad/bundle.min.js
```

Expected: `200`

- [ ] **Step 3: Commit**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
git add static/vendor/vad
git commit -m "feat(voice): vendor @ricky0123/vad-web 0.0.30 + onnxruntime-web 1.17.1 dist"
```

### Task 2: vadEngine.js

**Files:**
- Create: `static/js/vadEngine.js`

The wrapper hides which engine is active (Silero via vad-web, or RMS fallback). Consumer contract:

```js
const engine = await createVadEngine({
  onSpeechStart: () => {},
  onSpeechEnd: (float32Audio /* Float32Array @16k, may be null for RMS engine */) => {},
  positiveSpeechThreshold: 0.5,   // raised during TTS playback for barge-in guard
});
engine.start();      // begin listening (acquires mic)
engine.pause();      // keep mic, stop emitting events
engine.setThreshold(0.5 | 0.8);
engine.stop();       // release mic
engine.analyserNode; // AnalyserNode for the waveform (null until start())
engine.kind;         // 'silero' | 'rms'
```

- [ ] **Step 1: Write the file**

```js
// static/js/vadEngine.js

/**
 * VAD engine wrapper for voice dialog mode.
 * Primary: Silero VAD via vendored @ricky0123/vad-web (static/vendor/vad/).
 * Fallback: RMS threshold loop (the v1 algorithm) when the vendored assets
 * fail to load. Consumers only see the contract documented above.
 */

const VENDOR = '/static/vendor/vad/';
const RMS_THRESHOLD = 0.012;
const RMS_SILENCE_MS = 1500;
const RMS_MIN_SPEECH_MS = 300;

let _libLoaded = null; // Promise — load vendored scripts once

function _loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error('load failed: ' + src));
    document.head.appendChild(s);
  });
}

async function _loadSileroLib() {
  if (_libLoaded) return _libLoaded;
  _libLoaded = (async () => {
    await _loadScript(VENDOR + 'ort.min.js');
    // Tell onnxruntime-web where its wasm files live
    if (window.ort && window.ort.env && window.ort.env.wasm) {
      window.ort.env.wasm.wasmPaths = VENDOR;
    }
    await _loadScript(VENDOR + 'bundle.min.js'); // exposes window.vad
    if (!window.vad || !window.vad.MicVAD) throw new Error('vad-web bundle missing MicVAD');
  })();
  return _libLoaded;
}

class SileroEngine {
  constructor(opts) {
    this._opts = opts;
    this._vad = null;
    this._threshold = opts.positiveSpeechThreshold || 0.5;
    this.analyserNode = null;
    this.kind = 'silero';
    this._paused = true;
  }

  async init() {
    await _loadSileroLib();
    this._vad = await window.vad.MicVAD.new({
      baseAssetPath: VENDOR,
      onnxWASMBasePath: VENDOR,
      positiveSpeechThreshold: this._threshold,
      negativeSpeechThreshold: Math.max(0.15, this._threshold - 0.15),
      redemptionFrames: 12,           // ~1.2s of trailing silence at 96ms frames
      minSpeechFrames: 4,             // ignore blips < ~0.4s
      additionalAudioConstraints: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
      onSpeechStart: () => { if (!this._paused) this._opts.onSpeechStart(); },
      onSpeechEnd: (audio) => { if (!this._paused) this._opts.onSpeechEnd(audio); },
    });
    // vad-web exposes its AudioContext + source node; build an analyser for the waveform
    try {
      const ctx = this._vad.audioContext;
      const stream = this._vad.stream;
      if (ctx && stream) {
        const src = ctx.createMediaStreamSource(stream);
        this.analyserNode = ctx.createAnalyser();
        this.analyserNode.fftSize = 512;
        src.connect(this.analyserNode);
      }
    } catch (_) { /* waveform is optional */ }
  }

  start() { this._paused = false; this._vad.start(); }
  pause() { this._paused = true; try { this._vad.pause(); } catch (_) {} }
  setThreshold(t) {
    this._threshold = t;
    try { this._vad.setOptions({ positiveSpeechThreshold: t }); } catch (_) { /* older vad-web: recreate not worth it */ }
  }
  stop() {
    this._paused = true;
    try { this._vad.destroy(); } catch (_) {}
    this._vad = null;
    this.analyserNode = null;
  }
}

class RmsEngine {
  constructor(opts) {
    this._opts = opts;
    this.analyserNode = null;
    this.kind = 'rms';
    this._stream = null;
    this._ctx = null;
    this._timer = null;
    this._paused = true;
    this._speechStartedAt = 0;
    this._recorder = null;
    this._chunks = [];
  }

  async init() { /* nothing to preload */ }

  async start() {
    this._paused = false;
    if (!this._stream) {
      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = this._ctx.createMediaStreamSource(this._stream);
      this.analyserNode = this._ctx.createAnalyser();
      this.analyserNode.fftSize = 1024;
      src.connect(this.analyserNode);
    }
    this._beginUtterance();
  }

  _beginUtterance() {
    this._chunks = [];
    this._speechStartedAt = 0;
    this._recorder = new MediaRecorder(this._stream);
    this._recorder.ondataavailable = (e) => { if (e.data && e.data.size) this._chunks.push(e.data); };
    this._recorder.onstop = () => {
      const had = !!this._speechStartedAt;
      const blob = new Blob(this._chunks, { type: this._chunks[0]?.type || 'audio/webm' });
      if (!this._paused && had && blob.size > 1000) this._opts.onSpeechEnd(blob); // Blob, not Float32Array
      else if (!this._paused) this._beginUtterance();
    };
    this._recorder.start(250);
    const buf = new Float32Array(this.analyserNode.fftSize);
    let silenceSince = 0;
    const tick = () => {
      if (this._paused || !this._recorder || this._recorder.state !== 'recording') return;
      this.analyserNode.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      const now = Date.now();
      if (rms >= RMS_THRESHOLD) {
        if (!this._speechStartedAt) { this._speechStartedAt = now; this._opts.onSpeechStart(); }
        silenceSince = 0;
      } else if (this._speechStartedAt) {
        if (!silenceSince) silenceSince = now;
        if ((silenceSince - this._speechStartedAt) >= RMS_MIN_SPEECH_MS &&
            (now - silenceSince) >= RMS_SILENCE_MS) {
          this._recorder.stop();
          return;
        }
      }
      this._timer = setTimeout(tick, 60);
    };
    tick();
  }

  pause() {
    this._paused = true;
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
    if (this._recorder && this._recorder.state === 'recording') { try { this._recorder.stop(); } catch (_) {} }
  }
  setThreshold(_t) { /* RMS engine has a fixed threshold */ }
  stop() {
    this.pause();
    if (this._stream) { this._stream.getTracks().forEach(t => t.stop()); this._stream = null; }
    if (this._ctx) { try { this._ctx.close(); } catch (_) {} this._ctx = null; }
    this.analyserNode = null;
  }
}

export async function createVadEngine(opts) {
  try {
    const engine = new SileroEngine(opts);
    await engine.init();
    return engine;
  } catch (err) {
    console.warn('VAD: silero engine unavailable, falling back to RMS:', err);
    const engine = new RmsEngine(opts);
    await engine.init();
    return engine;
  }
}
```

- [ ] **Step 2: Syntax check**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
node --check static/js/vadEngine.js
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add static/js/vadEngine.js
git commit -m "feat(voice): vadEngine wrapper — silero via vad-web with RMS fallback"
```

### Task 3: voiceDialog.js v2a refactor — FSM + barge-in + chip + waveform

**Files:**
- Modify: `static/js/voiceDialog.js` (full rewrite below)

Key behavior changes vs v1:
- VAD via `createVadEngine`; `onSpeechEnd` delivers Float32Array (silero) or Blob (rms).
- Float32Array → WAV 16k mono encoded client-side for `/api/stt/transcribe`.
- Mic stays OPEN during `speaking`; threshold raised to 0.8; sustained speech (silero `onSpeechStart` + still speaking 500ms later) → `aiTTSManager.stop()` → barge-in.
- State chip + waveform canvas next to the toggle.

- [ ] **Step 1: Rewrite the file**

```js
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

const BARGE_SUSTAIN_MS = 500;       // speech must persist this long during playback
const THRESHOLD_LISTEN = 0.5;
const THRESHOLD_PLAYBACK = 0.8;     // raised while TTS audible (self-echo guard)
const MAX_ANSWER_WAIT_MS = 180000;

// States: 'off' | 'listening' | 'transcribing' | 'waiting' | 'speaking'
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

const ICON_DIALOG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M9 10h.01M12 10h.01M15 10h.01"/></svg>';

function _mgr() { return window.aiTTSManager || null; }

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
  if (_state === 'speaking') {
    // Barge-in candidate: must sustain BARGE_SUSTAIN_MS
    if (_bargeTimer) clearTimeout(_bargeTimer);
    _bargeTimer = setTimeout(() => {
      if (_state !== 'speaking') return;
      const mgr = _mgr();
      if (mgr) { try { mgr.stop(); } catch (_) {} }
      _state = 'listening';
      _engine.setThreshold(THRESHOLD_LISTEN);
      _setUI('vd-listening', 'listening', 'Voice dialog: listening… (click to stop)');
    }, BARGE_SUSTAIN_MS);
  }
}

async function _onSpeechEnd(audio) {
  if (_bargeTimer) { clearTimeout(_bargeTimer); _bargeTimer = null; }
  if (_state !== 'listening') return; // utterances during 'speaking' that didn't barge are dropped

  _state = 'transcribing';
  _setUI('vd-transcribing', 'transcribing', 'Voice dialog: transcribing…');
  _engine.pause();

  let blob;
  if (audio instanceof Blob) blob = audio;                       // RMS engine
  else if (audio && audio.length) blob = _float32ToWavBlob(audio); // silero
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

function _resumeListening() {
  if (_state === 'off') return;
  _state = 'listening';
  _engine.setThreshold(THRESHOLD_LISTEN);
  _engine.start();
  _setUI('vd-listening', 'listening', 'Voice dialog: listening… (click to stop)');
  _startWave();
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

  // Keep the mic open at the raised threshold so barge-in works during the answer
  _engine.setThreshold(THRESHOLD_PLAYBACK);
  _engine.start();
  _startWave();

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
    _resumeListening();
    return;
  }

  if (Date.now() - _watchStartedAt > MAX_ANSWER_WAIT_MS) { _resumeListening(); return; }
  _watchTimer = setTimeout(_watchAnswer, 400);
}

// ── Public toggle ──

async function start() {
  const mgr = _mgr();
  if (!window.isSecureContext) { _toast('Voice dialog requires HTTPS'); return; }
  if (!mgr || !mgr.available) { _toast('Enable TTS first (Settings → TTS)'); return; }

  _state = 'listening';
  mgr.autoPlay = true;
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
  _setUI('vd-listening', `listening (${_engine.kind})`, 'Voice dialog: listening… (click to stop)');
  _startWave();
}

function stop() {
  _state = 'off';
  if (_watchTimer) { clearTimeout(_watchTimer); _watchTimer = null; }
  if (_bargeTimer) { clearTimeout(_bargeTimer); _bargeTimer = null; }
  if (_waveRAF) { cancelAnimationFrame(_waveRAF); _waveRAF = null; }
  if (_engine) { try { _engine.stop(); } catch (_) {} _engine = null; }
  const mgr = _mgr();
  if (mgr) { mgr.autoPlay = false; try { mgr.stop(); } catch (_) {} }
  _setUI(null, '', 'Voice dialog mode');
}

function _toggle() { if (_state === 'off') start(); else stop(); }

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
```

- [ ] **Step 2: Syntax check**

```bash
node --check static/js/voiceDialog.js
```

Expected: exit 0.

- [ ] **Step 3: Manual verify (Chrome desktop, https tailnet URL)**

Checklist:
1. Hard-reload → 💬 button + hidden chip/wave appear left of send.
2. Toggle on → chip "listening (silero)" (or "(rms)" if vendor assets broken) + waveform moves.
3. Speak, pause → transcribes → sends → answer speaks → chip cycles listening→transcribing→thinking→speaking→listening.
4. While speaking: interrupt loudly for >0.5 s → playback stops, your speech becomes next message (barge-in).
5. Fan/keyboard noise alone must NOT trigger (silero).
6. Esc → everything off, mic indicator in browser tab gone.

- [ ] **Step 4: Commit**

```bash
git add static/js/voiceDialog.js
git commit -m "feat(voice): dialog v2a — silero VAD, barge-in, waveform + state chip"
```

---

## Slice v2b — Chunked streaming STT

### Task 4: STTService.transcribe_array

**Files:**
- Modify: `services/stt/stt_service.py` (add method after `transcribe`)
- Test: `tests/test_stt_stream.py` (created here, extended in Task 5)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stt_stream.py
import numpy as np

from services.stt.stt_service import STTService


class _FakeSegment:
    def __init__(self, text):
        self.text = text


def test_transcribe_array_joins_segments(monkeypatch):
    service = STTService()

    class _FakeModel:
        def transcribe(self, audio, **kwargs):
            assert isinstance(audio, np.ndarray)
            assert audio.dtype == np.float32
            return [_FakeSegment(" hello"), _FakeSegment(" world")], None

    monkeypatch.setattr(service, "_get_whisper", lambda: _FakeModel())
    audio = np.zeros(16000, dtype=np.float32)
    assert service.transcribe_array(audio) == "hello world"


def test_transcribe_array_no_model(monkeypatch):
    service = STTService()
    monkeypatch.setattr(service, "_get_whisper", lambda: None)
    assert service.transcribe_array(np.zeros(100, dtype=np.float32)) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
./venv/bin/python -m pytest tests/test_stt_stream.py -v
```

Expected: FAIL — `AttributeError: 'STTService' object has no attribute 'transcribe_array'`.

- [ ] **Step 3: Implement**

Add to `services/stt/stt_service.py`, directly after the existing `transcribe` method:

```python
    def transcribe_array(self, audio, language: Optional[str] = None) -> Optional[str]:
        """Transcribe a mono float32 numpy array at 16 kHz.

        Used by the streaming STT WebSocket: the rolling utterance buffer is
        re-transcribed in-process, no temp files. Returns joined text or None
        when the local model is unavailable.
        """
        model = self._get_whisper()
        if not model:
            return None
        settings = self._load_settings()
        lang = language or settings.get("stt_language") or None
        try:
            segments, _info = model.transcribe(audio, language=lang, beam_size=1)
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            logger.error(f"Array transcription failed: {e}")
            return None
```

Note: only the `local` provider supports array input; the WS route (Task 5)
falls back to a 501 close for `endpoint:` providers.

- [ ] **Step 4: Run test to verify it passes**

```bash
./venv/bin/python -m pytest tests/test_stt_stream.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/stt/stt_service.py tests/test_stt_stream.py
git commit -m "feat(stt): transcribe_array — ndarray input for streaming STT"
```

### Task 5: WS route /api/stt/stream

**Files:**
- Create: `routes/stt_stream_routes.py`
- Test: `tests/test_stt_stream.py` (extend)

Protocol:
- Client → binary frames: PCM16 mono 16 kHz.
- Client → text frames: `{"event":"end"}` (finalize) | `{"event":"abort"}` (drop buffer, keep socket).
- Server → `{"partial": str}` every PARTIAL_INTERVAL_S while buffer grows; `{"final": str}` after `end`; `{"error": str}` on failure.
- Utterance cap 60 s → forced final.

- [ ] **Step 1: Write the failing tests (append to tests/test_stt_stream.py)**

```python
import json

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.stt_stream_routes import setup_stt_stream_routes


class _StubSTT:
    """Stub service: 'transcribes' by reporting how many samples it saw."""
    available = True

    def transcribe_array(self, audio, language=None):
        return f"len={len(audio)}"


def _client(service=None):
    app = FastAPI()
    app.include_router(setup_stt_stream_routes(service or _StubSTT()))
    return TestClient(app)


def _pcm16(n_samples, value=1000):
    return np.full(n_samples, value, dtype=np.int16).tobytes()


def test_stream_partial_and_final():
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(16000))          # 1s of audio
        ws.send_text(json.dumps({"event": "flush"}))   # deterministic partial for tests
        msg = ws.receive_json()
        assert "partial" in msg and msg["partial"] == "len=16000"
        ws.send_bytes(_pcm16(8000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=24000"


def test_stream_abort_clears_buffer():
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(16000))
        ws.send_text(json.dumps({"event": "abort"}))
        ws.send_bytes(_pcm16(4000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=4000"


def test_stream_unavailable_service():
    class _Down:
        available = False
        def transcribe_array(self, *a, **k): return None
    client = _client(_Down())
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(1000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert "error" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/python -m pytest tests/test_stt_stream.py -v
```

Expected: first 2 PASS (Task 4), 3 new FAIL — `ModuleNotFoundError: routes.stt_stream_routes`.

- [ ] **Step 3: Implement the route**

```python
# routes/stt_stream_routes.py
"""Streaming STT over WebSocket.

WS /api/stt/stream — dictation mode:
  client sends PCM16 mono 16 kHz binary frames + JSON control messages
  ({"event": "end" | "abort" | "flush"}). Server pushes {"partial": text}
  on a rolling interval while audio accumulates, and {"final": text} after
  "end". "flush" forces an immediate partial (used by tests; harmless live).

The rolling buffer is re-transcribed in-process via
STTService.transcribe_array — local (faster-whisper) provider only.
"""
import asyncio
import json
import logging
import time

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
PARTIAL_INTERVAL_S = 1.2
MAX_UTTERANCE_S = 60


def setup_stt_stream_routes(stt_service):
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.websocket("/stream")
    async def stt_stream(ws: WebSocket):
        await ws.accept()
        buf = bytearray()
        last_partial_at = 0.0
        last_partial_text = None

        async def _transcribe() -> str | None:
            if not buf:
                return ""
            audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
            return await asyncio.to_thread(stt_service.transcribe_array, audio)

        async def _send_partial(force: bool = False):
            nonlocal last_partial_at, last_partial_text
            now = time.monotonic()
            if not force and (now - last_partial_at) < PARTIAL_INTERVAL_S:
                return
            last_partial_at = now
            text = await _transcribe()
            if text is None:
                await ws.send_json({"error": "STT local provider unavailable"})
                return
            if text and text != last_partial_text:
                last_partial_text = text
                await ws.send_json({"partial": text})

        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break

                if msg.get("bytes") is not None:
                    if not getattr(stt_service, "available", False):
                        await ws.send_json({"error": "STT service not available"})
                        continue
                    buf.extend(msg["bytes"])
                    if len(buf) >= SAMPLE_RATE * 2 * MAX_UTTERANCE_S:
                        text = await _transcribe()
                        await ws.send_json({"final": text or ""})
                        buf.clear()
                        last_partial_text = None
                        continue
                    await _send_partial()
                    continue

                if msg.get("text") is not None:
                    try:
                        event = json.loads(msg["text"]).get("event")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if event == "abort":
                        buf.clear()
                        last_partial_text = None
                    elif event == "flush":
                        await _send_partial(force=True)
                    elif event == "end":
                        text = await _transcribe()
                        if text is None:
                            await ws.send_json({"error": "STT local provider unavailable"})
                        else:
                            await ws.send_json({"final": text})
                        buf.clear()
                        last_partial_text = None
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"STT stream error: {e}", exc_info=True)
            try:
                await ws.send_json({"error": str(e)})
            except Exception:
                pass

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/python -m pytest tests/test_stt_stream.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/stt_stream_routes.py tests/test_stt_stream.py
git commit -m "feat(stt): WS /api/stt/stream with rolling partials"
```

### Task 6: Register router in app.py

**Files:**
- Modify: `app.py` (after line 635, the `setup_stt_routes` block)

- [ ] **Step 1: Add registration**

Find in `app.py`:

```python
from routes.stt_routes import setup_stt_routes
app.include_router(setup_stt_routes(stt_service))
```

Add directly below:

```python
from routes.stt_stream_routes import setup_stt_stream_routes
app.include_router(setup_stt_stream_routes(stt_service))
```

- [ ] **Step 2: Restart Odysseus and smoke-test**

The running instance must be restarted to pick up app.py. Find how it was
launched (`ps -o args -p $(ss -ltnp | grep :7000 | grep -oP 'pid=\K[0-9]+')`)
and restart the same way, or ask the operator. Then:

```bash
./venv/bin/python - <<'EOF'
import json
from websockets.sync.client import connect
import numpy as np
ws = connect("ws://127.0.0.1:7000/api/stt/stream")
ws.send(np.zeros(16000, dtype=np.int16).tobytes())
ws.send(json.dumps({"event": "end"}))
print(ws.recv())
ws.close()
EOF
```

Expected: `{"final": ""}` or `{"final": "..."}` (silence transcribes to empty/noise). If `websockets` is not in the venv: `./venv/bin/pip install websockets`.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(stt): register streaming STT WebSocket route"
```

### Task 7: PCM capture worklet + sttStream.js client

**Files:**
- Create: `static/js/pcmWorklet.js`
- Create: `static/js/sttStream.js`

- [ ] **Step 1: Write the worklet**

```js
// static/js/pcmWorklet.js

/**
 * AudioWorkletProcessor: mono input at the context rate → PCM16 @16 kHz frames.
 * Posts Int16Array buffers (~128 ms each) to the main thread.
 */
class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / 16000; // context rate (usually 48000) → 16k
    this._acc = [];
    this._accLen = 0;
    this._cursor = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;

    const out = [];
    while (this._cursor < ch.length) {
      out.push(ch[Math.floor(this._cursor)]);
      this._cursor += this._ratio;
    }
    this._cursor -= ch.length;

    this._acc.push(...out);
    this._accLen += out.length;

    if (this._accLen >= 2048) { // ~128ms at 16k
      const f32 = Float32Array.from(this._acc);
      const i16 = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      this.port.postMessage(i16.buffer, [i16.buffer]);
      this._acc = [];
      this._accLen = 0;
    }
    return true;
  }
}

registerProcessor('pcm-downsampler', PcmDownsampler);
```

- [ ] **Step 2: Write the WS client**

```js
// static/js/sttStream.js

/**
 * Streaming STT client: mic MediaStream → AudioWorklet (PCM16 @16k) →
 * WS /api/stt/stream. Emits partial/final transcripts.
 *
 * const s = await createSttStream({ onPartial, onFinal, onError });
 * await s.attach(mediaStream);  // begin streaming this mic
 * s.endUtterance();             // ask server for the final transcript
 * s.abortUtterance();           // drop buffered audio server-side
 * s.detach();                   // stop sending, keep socket
 * s.close();                    // tear down
 * s.connected                   // boolean
 */

const RECONNECT_MAX = 3;

export async function createSttStream(opts) {
  const state = {
    ws: null,
    ctx: null,
    node: null,
    source: null,
    attached: false,
    closed: false,
    connected: false,
    reconnects: 0,
  };

  function _wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${location.host}/api/stt/stream`;
  }

  function _connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(_wsUrl());
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => { state.connected = true; state.reconnects = 0; resolve(ws); };
      ws.onmessage = (e) => {
        let data;
        try { data = JSON.parse(e.data); } catch (_) { return; }
        if (data.partial !== undefined && opts.onPartial) opts.onPartial(data.partial);
        if (data.final !== undefined && opts.onFinal) opts.onFinal(data.final);
        if (data.error && opts.onError) opts.onError(new Error(data.error));
      };
      ws.onerror = () => reject(new Error('WS connect failed'));
      ws.onclose = async () => {
        state.connected = false;
        if (state.closed) return;
        if (state.reconnects < RECONNECT_MAX) {
          state.reconnects++;
          try { state.ws = await _connect(); } catch (_) {
            if (opts.onError) opts.onError(new Error('STT stream lost'));
          }
        } else if (opts.onError) {
          opts.onError(new Error('STT stream lost'));
        }
      };
    });
  }

  state.ws = await _connect();

  return {
    get connected() { return state.connected; },

    async attach(mediaStream) {
      if (state.attached) this.detach();
      state.ctx = state.ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (!state.ctx.__pcmWorkletLoaded) {
        await state.ctx.audioWorklet.addModule('/static/js/pcmWorklet.js');
        state.ctx.__pcmWorkletLoaded = true;
      }
      state.source = state.ctx.createMediaStreamSource(mediaStream);
      state.node = new AudioWorkletNode(state.ctx, 'pcm-downsampler');
      state.node.port.onmessage = (e) => {
        if (state.connected && state.attached) state.ws.send(e.data);
      };
      state.source.connect(state.node);
      state.attached = true;
    },

    detach() {
      state.attached = false;
      if (state.source && state.node) { try { state.source.disconnect(state.node); } catch (_) {} }
      state.node = null;
      state.source = null;
    },

    endUtterance() {
      if (state.connected) state.ws.send(JSON.stringify({ event: 'end' }));
    },

    abortUtterance() {
      if (state.connected) state.ws.send(JSON.stringify({ event: 'abort' }));
    },

    close() {
      state.closed = true;
      this.detach();
      if (state.ws) { try { state.ws.close(); } catch (_) {} }
      if (state.ctx) { try { state.ctx.close(); } catch (_) {} state.ctx = null; }
    },
  };
}
```

- [ ] **Step 3: Syntax check**

```bash
node --check static/js/sttStream.js && node --check static/js/pcmWorklet.js
```

Expected: exit 0 for both. (`node --check` on the worklet flags nothing despite `AudioWorkletProcessor` being browser-global — it is a syntax check only.)

- [ ] **Step 4: Commit**

```bash
git add static/js/pcmWorklet.js static/js/sttStream.js
git commit -m "feat(voice): streaming STT client — AudioWorklet PCM16 capture + WS"
```

### Task 8: voiceDialog.js v2b integration — live partials

**Files:**
- Modify: `static/js/voiceDialog.js`

Wiring: when the dialog starts, also open an `sttStream`. During `listening`,
the engine's mic stream is attached; VAD events still segment utterances:
`onSpeechStart` → `abortUtterance()` then attach (clean buffer); partials render
greyed in `#message`; `onSpeechEnd` → `endUtterance()`; `onFinal` → `_send(text)`.
If the stream is unavailable (`onError` / connect fail), `_useStream=false` and
the v1 single-shot POST path (already in `_onSpeechEnd`) takes over.

- [ ] **Step 1: Apply the edits**

1. Add import at top, after the existing import:

```js
import { createSttStream } from './sttStream.js';
```

2. Add module state, after `let _sawTTSActivity = false;`:

```js
let _stt = null;        // sttStream instance or null
let _useStream = false; // false → v1 single-shot POST fallback
```

3. In `start()`, after the engine is created and started successfully, add:

```js
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
        const input = document.getElementById('message');
        if (input) input.style.opacity = '';
        if (_state !== 'transcribing') return;
        if (!text || !text.trim()) { _toast('Heard nothing — listening again'); _resumeListening(); return; }
        _send(text.trim());
      },
      onError: (err) => {
        console.warn('Voice dialog: stt stream degraded → single-shot fallback', err);
        _useStream = false;
      },
    });
    const micStream = _engine && _engine._vad ? _engine._vad.stream
                    : (_engine && _engine._stream) ? _engine._stream : null;
    if (micStream) { await _stt.attach(micStream); _useStream = true; }
  } catch (err) {
    console.warn('Voice dialog: stt stream unavailable → single-shot fallback', err);
    _useStream = false;
    _stt = null;
  }
```

4. In `_onSpeechStart()`, add as the FIRST line:

```js
  if (_useStream && _stt && _state === 'listening') _stt.abortUtterance();
```

(A speech start discards any stale buffered audio from before this utterance.)

5. In `_onSpeechEnd(audio)`, replace the transcription block. The method becomes:

```js
async function _onSpeechEnd(audio) {
  if (_bargeTimer) { clearTimeout(_bargeTimer); _bargeTimer = null; }
  if (_state !== 'listening') return;

  _state = 'transcribing';
  _setUI('vd-transcribing', 'transcribing', 'Voice dialog: transcribing…');
  _engine.pause();

  if (_useStream && _stt && _stt.connected) {
    _stt.endUtterance(); // final arrives via onFinal → _send
    return;
  }

  // v1 single-shot fallback
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
```

6. In `stop()`, before `_setUI(null, '', 'Voice dialog mode');` add:

```js
  if (_stt) { try { _stt.close(); } catch (_) {} _stt = null; }
  _useStream = false;
  const inputEl = document.getElementById('message');
  if (inputEl) inputEl.style.opacity = '';
```

- [ ] **Step 2: Syntax check**

```bash
node --check static/js/voiceDialog.js
```

Expected: exit 0.

- [ ] **Step 3: Manual verify (after Task 6 restart)**

1. Toggle dialog on → speak a long sentence → words appear greyed in the input WHILE speaking.
2. Pause → input un-greys → message sends → answer speaks.
3. Kill the server mid-utterance (`Ctrl-C` + restart) → next utterance falls back to single-shot POST (console shows "single-shot fallback"), loop survives.

- [ ] **Step 4: Commit**

```bash
git add static/js/voiceDialog.js
git commit -m "feat(voice): dialog v2b — live partial transcripts over WS, POST fallback"
```

---

## Slice v2c — Wake word "hey soloway"

### Task 9: openwakeword dependency + model presence

**Files:** none (venv + model check)

- [ ] **Step 1: Install openwakeword (workspace pip cache per storage rule)**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
export PIP_CACHE_DIR=/run/media/soloway/workspace/Devel/Tools/ai/pip-cache
./venv/bin/pip install --cache-dir "$PIP_CACHE_DIR" openwakeword
./venv/bin/python -c "import openwakeword; print(openwakeword.__version__)"
```

Expected: version prints. openwakeword is CPU-only (onnxruntime/tflite), no VRAM.

- [ ] **Step 2: Verify the trained model exists**

```bash
ls -la ~/.hermes/voice/hey_soloway_v0.1.onnx
./venv/bin/python -c "
from openwakeword.model import Model
m = Model(wakeword_models=['$HOME/.hermes/voice/hey_soloway_v0.1.onnx'], inference_framework='onnx')
print('models loaded:', list(m.models.keys()))"
```

Expected: file exists; `models loaded: ['hey_soloway_v0.1']` (key = filename stem).

- [ ] **Step 3: Commit requirement note**

Append to `requirements-optional.txt`:

```
# Wake-word standby for voice dialog mode (server-side detection on the
# streaming STT WebSocket). CPU-only.
openwakeword
```

```bash
git add requirements-optional.txt
git commit -m "feat(voice): openwakeword optional dependency for standby wake word"
```

### Task 10: services/stt/wakeword.py

**Files:**
- Create: `services/stt/wakeword.py`
- Test: `tests/test_wakeword_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wakeword_service.py
import numpy as np

from services.stt.wakeword import WakeWordDetector


class _FakeOwwModel:
    def __init__(self, scores):
        self._scores = list(scores)
        self.fed = []

    def predict(self, frame):
        self.fed.append(len(frame))
        s = self._scores.pop(0) if self._scores else 0.0
        return {"hey_soloway_v0.1": s}

    def reset(self):
        self.fed.clear()


def _detector(scores, threshold=0.7):
    d = WakeWordDetector.__new__(WakeWordDetector)  # skip __init__ (no real model)
    d._model = _FakeOwwModel(scores)
    d._threshold = threshold
    d._buffer = np.array([], dtype=np.int16)
    return d


def test_detects_above_threshold():
    d = _detector([0.1, 0.9])
    pcm = np.zeros(1280, dtype=np.int16).tobytes()
    assert d.feed(pcm) is False
    assert d.feed(pcm) is True


def test_below_threshold_never_fires():
    d = _detector([0.5, 0.69, 0.2])
    pcm = np.zeros(1280, dtype=np.int16).tobytes()
    assert d.feed(pcm) is False
    assert d.feed(pcm) is False
    assert d.feed(pcm) is False


def test_buffers_partial_frames():
    d = _detector([0.0, 0.9])
    half = np.zeros(640, dtype=np.int16).tobytes()
    assert d.feed(half) is False        # only 640 samples buffered — no predict yet
    assert d._model.fed == []
    assert d.feed(half) is False        # 1280 → one predict (score 0.0)
    assert d._model.fed == [1280]
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/python -m pytest tests/test_wakeword_service.py -v
```

Expected: FAIL — `ModuleNotFoundError: services.stt.wakeword`.

- [ ] **Step 3: Implement**

```python
# services/stt/wakeword.py
"""Server-side wake-word detection for voice dialog standby mode.

Wraps openwakeword for use on the streaming STT WebSocket: PCM16 mono 16 kHz
chunks are fed in arbitrary sizes; the detector buffers to openwakeword's
80 ms frame (1280 samples) and reports detection against a threshold.

Model: a custom openwakeword .onnx (e.g. ~/.hermes/voice/hey_soloway_v0.1.onnx,
trained in the hermes-voice-manager project). CPU-only.
"""
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 1280  # openwakeword expects 80 ms @ 16 kHz

DEFAULT_MODEL_PATH = os.environ.get(
    "WAKEWORD_MODEL",
    str(Path.home() / ".hermes" / "voice" / "hey_soloway_v0.1.onnx"),
)
DEFAULT_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.7"))


class WakeWordDetector:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH,
                 threshold: float = DEFAULT_THRESHOLD):
        from openwakeword.model import Model
        self._model = Model(wakeword_models=[model_path], inference_framework="onnx")
        self._threshold = threshold
        self._buffer = np.array([], dtype=np.int16)

    def feed(self, pcm16_bytes: bytes) -> bool:
        """Feed PCM16 bytes; True the moment any model score >= threshold."""
        chunk = np.frombuffer(pcm16_bytes, dtype=np.int16)
        self._buffer = np.concatenate([self._buffer, chunk])
        fired = False
        while len(self._buffer) >= FRAME_SAMPLES:
            frame = self._buffer[:FRAME_SAMPLES]
            self._buffer = self._buffer[FRAME_SAMPLES:]
            scores = self._model.predict(frame)
            if any(s >= self._threshold for s in scores.values()):
                fired = True
        return fired

    def reset(self):
        self._buffer = np.array([], dtype=np.int16)
        try:
            self._model.reset()
        except Exception:
            pass


_detector: Optional[WakeWordDetector] = None


def get_wakeword_detector() -> Optional[WakeWordDetector]:
    """Singleton; None when openwakeword or the model file is missing."""
    global _detector
    if _detector is None:
        try:
            _detector = WakeWordDetector()
            logger.info("Wake-word detector loaded: %s", DEFAULT_MODEL_PATH)
        except Exception as e:
            logger.warning("Wake-word detector unavailable: %s", e)
            return None
    return _detector
```

- [ ] **Step 4: Run tests**

```bash
./venv/bin/python -m pytest tests/test_wakeword_service.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/stt/wakeword.py tests/test_wakeword_service.py
git commit -m "feat(voice): wake-word detector service (openwakeword wrapper)"
```

### Task 11: Wake mode on the WS route

**Files:**
- Modify: `routes/stt_stream_routes.py`
- Test: `tests/test_stt_stream.py` (extend)

Protocol addition: client text frame `{"mode":"wake"}` switches the connection
to wake mode (audio goes to the detector, no transcription); `{"mode":"dictate"}`
switches back. Server sends `{"wake": true}` on detection and auto-switches the
connection to dictate mode.

- [ ] **Step 1: Write the failing tests (append to tests/test_stt_stream.py)**

```python
class _FakeDetector:
    def __init__(self, fire_on_feed=2):
        self._n = 0
        self._fire_on = fire_on_feed
        self.resets = 0

    def feed(self, pcm):
        self._n += 1
        return self._n >= self._fire_on

    def reset(self):
        self.resets += 1


def test_wake_mode_fires_and_switches_to_dictate(monkeypatch):
    import routes.stt_stream_routes as mod
    detector = _FakeDetector(fire_on_feed=2)
    monkeypatch.setattr(mod, "get_wakeword_detector", lambda: detector)
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_text(json.dumps({"mode": "wake"}))
        ws.send_bytes(_pcm16(1280))     # feed 1 — no wake
        ws.send_bytes(_pcm16(1280))     # feed 2 — fires
        msg = ws.receive_json()
        assert msg == {"wake": True}
        # auto-switched to dictate: audio now buffers for transcription
        ws.send_bytes(_pcm16(16000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=16000"


def test_wake_mode_unavailable_detector(monkeypatch):
    import routes.stt_stream_routes as mod
    monkeypatch.setattr(mod, "get_wakeword_detector", lambda: None)
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_text(json.dumps({"mode": "wake"}))
        msg = ws.receive_json()
        assert "error" in msg
```

- [ ] **Step 2: Run to verify failure**

```bash
./venv/bin/python -m pytest tests/test_stt_stream.py -v
```

Expected: previous 5 PASS, 2 new FAIL (`get_wakeword_detector` not defined / no wake handling).

- [ ] **Step 3: Implement**

In `routes/stt_stream_routes.py`:

1. Add import after the numpy import:

```python
from services.stt.wakeword import get_wakeword_detector
```

2. Inside `stt_stream`, after `last_partial_text = None` add:

```python
        mode = "dictate"            # 'dictate' | 'wake'
        detector = None
```

3. Replace the binary-frame branch (`if msg.get("bytes") is not None:` block) with:

```python
                if msg.get("bytes") is not None:
                    if mode == "wake":
                        if detector is None:
                            continue
                        fired = await asyncio.to_thread(detector.feed, msg["bytes"])
                        if fired:
                            detector.reset()
                            mode = "dictate"
                            buf.clear()
                            last_partial_text = None
                            await ws.send_json({"wake": True})
                        continue

                    if not getattr(stt_service, "available", False):
                        await ws.send_json({"error": "STT service not available"})
                        continue
                    buf.extend(msg["bytes"])
                    if len(buf) >= SAMPLE_RATE * 2 * MAX_UTTERANCE_S:
                        text = await _transcribe()
                        await ws.send_json({"final": text or ""})
                        buf.clear()
                        last_partial_text = None
                        continue
                    await _send_partial()
                    continue
```

4. In the text-frame branch, extend the parsed handling. Replace:

```python
                    try:
                        event = json.loads(msg["text"]).get("event")
                    except (json.JSONDecodeError, AttributeError):
                        continue
```

with:

```python
                    try:
                        payload = json.loads(msg["text"])
                        event = payload.get("event")
                        req_mode = payload.get("mode")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if req_mode == "wake":
                        detector = get_wakeword_detector()
                        if detector is None:
                            await ws.send_json({"error": "wake word unavailable"})
                        else:
                            detector.reset()
                            mode = "wake"
                            buf.clear()
                            last_partial_text = None
                        continue
                    if req_mode == "dictate":
                        mode = "dictate"
                        continue
```

- [ ] **Step 4: Run all stream tests**

```bash
./venv/bin/python -m pytest tests/test_stt_stream.py tests/test_wakeword_service.py -v
```

Expected: 10 PASS (5 + 2 wake + 3 wakeword service).

- [ ] **Step 5: Commit**

```bash
git add routes/stt_stream_routes.py tests/test_stt_stream.py
git commit -m "feat(voice): wake mode on streaming STT WebSocket"
```

### Task 12: voiceDialog.js v2c — standby state + 3-state toggle

**Files:**
- Modify: `static/js/voiceDialog.js`

Toggle cycle: off → dialog → dialog+standby → off. In standby (after a reply,
or on entry), the mic streams to the WS in wake mode; `{"wake":true}` flips to
active listening. Chip shows "standby".

- [ ] **Step 1: Apply the edits**

1. Module state — add after `let _useStream = false;`:

```js
let _standbyEnabled = false; // toggle cycle: off → dialog → dialog+standby → off
```

2. Add the wake handler support to `createSttStream` opts in `start()` — add to the opts object:

```js
      onWake: () => {
        if (_state !== 'standby') return;
        _resumeListening();
      },
```

   And in `static/js/sttStream.js`, inside `ws.onmessage` after the error line, add:

```js
        if (data.wake && opts.onWake) opts.onWake();
```

3. Add standby entry function after `_resumeListening()`:

```js
function _enterStandby() {
  if (_state === 'off') return;
  if (!_standbyEnabled || !_useStream || !_stt || !_stt.connected) {
    _resumeListening(); // no wake channel → stay in open-mic dialog
    return;
  }
  _state = 'standby';
  _engine.pause();                       // VAD off — server listens for the wake word
  _stt.setMode('wake');
  _setUI('vd-on', 'standby', 'Voice dialog: standby — say "hey soloway" (click to stop)');
}
```

4. In `sttStream.js` returned object, add a `setMode` method:

```js
    setMode(mode) {
      if (state.connected) state.ws.send(JSON.stringify({ mode }));
    },
```

5. `_resumeListening()` — ensure dictate mode and mic attach when coming from standby. Replace the function with:

```js
function _resumeListening() {
  if (_state === 'off') return;
  _state = 'listening';
  if (_useStream && _stt && _stt.connected) _stt.setMode('dictate');
  _engine.setThreshold(THRESHOLD_LISTEN);
  _engine.start();
  _setUI('vd-listening', 'listening', 'Voice dialog: listening… (click to stop)');
  _startWave();
}
```

6. In `_watchAnswer()`, the spoken-reply-finished branch becomes standby-aware. Replace:

```js
  } else if (_sawTTSActivity) {
    _resumeListening();
    return;
  }
```

with:

```js
  } else if (_sawTTSActivity) {
    _enterStandby();   // falls back to _resumeListening() when standby is off
    return;
  }
```

7. Replace `_toggle()` with the 3-state cycle:

```js
function _toggle() {
  if (_state === 'off') {
    _standbyEnabled = false;
    start();
  } else if (!_standbyEnabled) {
    _standbyEnabled = true;
    _toast('Standby mode: after each reply, say "hey soloway" to continue');
    if (_state === 'listening') _enterStandby();
  } else {
    stop();
  }
}
```

8. In `stop()`, add `_standbyEnabled = false;` as the first line.

9. In `_watchAnswer`'s `mode wake` interplay: standby state must also be accepted by the watch guard. Replace the early-return line:

```js
  if (_state !== 'waiting' && _state !== 'speaking') return;
```

stays as-is (standby never runs `_watchAnswer`) — no change needed; listed here to confirm it was considered.

- [ ] **Step 2: Syntax check**

```bash
node --check static/js/voiceDialog.js && node --check static/js/sttStream.js
```

Expected: exit 0.

- [ ] **Step 3: Manual verify**

1. Tap toggle once → normal dialog (chip "listening").
2. Tap again → toast about standby; after the next reply finishes, chip "standby"; VAD paused.
3. Say "hey soloway" → chip flips to "listening" → speak → normal loop.
4. TV/radio speech in standby must NOT wake (threshold 0.7).
5. Tap third time → off; mic indicator gone.

- [ ] **Step 4: Commit**

```bash
git add static/js/voiceDialog.js static/js/sttStream.js
git commit -m "feat(voice): dialog v2c — hey-soloway standby via WS wake mode"
```

### Task 13: Full regression + wrap-up

**Files:** none

- [ ] **Step 1: Run the whole backend test suite**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
./venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -15
```

Expected: all pass (pre-existing failures, if any, must match a pre-branch baseline run — record `git stash` + baseline if unsure).

- [ ] **Step 2: Full manual flow on phone over tailnet**

`https://wwwsolowaytechcom.tail9c73fe.ts.net:7443` — repeat Task 3 + Task 8 + Task 12 manual checklists on mobile (Safari or Chrome).

- [ ] **Step 3: Codex review (user rule: review code before commit-to-mainline decision)**

```bash
cd /run/media/soloway/workspace/Devel/Projects/soloway/tools/odysseus
graphify update . 2>/dev/null || true
echo "Review the voice-dialog-v2 branch diff vs dev for: WS resource leaks, asyncio blocking, VAD/barge-in race conditions, XSS in partial-transcript rendering, openwakeword failure paths. Query graphify-out/graph.json for impact." | codex review --base dev -
```

Address findings, commit fixes.

- [ ] **Step 4: Hand back to user**

Branch `feature/voice-dialog-v2` stays unmerged. User decides: merge to local `dev`, keep as patch series, or PR upstream (it is an upstream clone — upstream PR would need their contribution flow).

---

## Self-Review Notes

- Spec coverage: v2a Tasks 1-3 (VAD, barge-in, UI), v2b Tasks 4-8 (ndarray STT, WS route, registration, worklet+client, integration), v2c Tasks 9-12 (dep, detector, wake mode, standby), regression Task 13. Error-handling spec items live in: RMS fallback (Task 2), WS reconnect + POST fallback (Tasks 7-8), wake-unavailable error (Task 11), empty-transcript re-listen (Task 3/8).
- Type consistency: `createVadEngine` contract (Task 2) matches usage (Tasks 3, 8, 12); `createSttStream` API (`attach/detach/endUtterance/abortUtterance/setMode/close/connected`) consistent across Tasks 7, 8, 12; WS protocol (`partial/final/error/wake`, `event end/abort/flush`, `mode wake/dictate`) consistent between route (5, 11) and client (7, 12).
- Known seam: `_engine._vad.stream` / `_engine._stream` access in Task 8 reaches into engine internals — acceptable slice-1 shortcut, noted for cleanup if vadEngine grows a `get stream()` accessor.

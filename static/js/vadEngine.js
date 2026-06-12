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

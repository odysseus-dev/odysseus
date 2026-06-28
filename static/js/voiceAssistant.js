// static/js/voiceAssistant.js
// Voice User Interface (VUI) module — Web Speech API (STT + TTS)
// Hardy module: no backend calls, pure browser Speech API.

/**
 * VoiceAssistant — unified speech-to-text & text-to-speech.
 *
 * STT (SpeechRecognition):
 *   - ru-RU default, falls back to browser language.
 *   - Interim results streamed live via oninterim callback.
 *   - Explicit user gesture required (click to start).
 *
 * TTS (speechSynthesis):
 *   - ru-RU voice preferred, falls back to any available.
 *   - Queue for sequential playback.
 *   - stop() interrupts immediately.
 *
 * Usage:
 *   const va = new VoiceAssistant();
 *   va.startSTT({ onResult: (text) => ..., onInterim: (partial) => ... });
 *   va.stopSTT();
 *   va.speak("Привет");
 *   va.stop();
 */
export class VoiceAssistant {
  constructor() {
    // --- STT state ---
    this._recognition = null;
    this._sttActive = false;
    this._sttFinal = '';

    // --- TTS state ---
    this._utterance = null;
    this._speaking = false;
    this._ttsQueue = [];
    this._ttsProcessing = false;

    // --- capabilities ---
    this.sttSupported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    this.ttsSupported = 'speechSynthesis' in window;
  }

  // ── Speech-to-Text ──

  /**
   * Start speech recognition.
   * @param {object} opts
   * @param {function(string)} opts.onResult   — final transcript
   * @param {function(string)} opts.onInterim  — live partial transcript
   * @param {function(string)} opts.onError    — error description
   * @param {string}           opts.lang       — BCP 47 tag (default 'ru-RU')
   * @param {boolean}          opts.continuous — keep listening after pause (default true)
   */
  startSTT(opts = {}) {
    if (!this.sttSupported) {
      if (opts.onError) opts.onError('Speech recognition not supported in this browser. Use Chrome/Edge.');
      return;
    }
    if (this._sttActive) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.continuous = opts.continuous !== false;
    recognition.interimResults = true;
    recognition.lang = opts.lang || 'ru-RU';

    this._sttFinal = '';
    this._sttActive = true;

    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          this._sttFinal += transcript + ' ';
        } else {
          interim += transcript;
        }
      }
      if (interim && opts.onInterim) {
        opts.onInterim(this._sttFinal + interim);
      }
    };

    recognition.onend = () => {
      // Continuous mode restarts automatically; this fires when stopped manually.
      this._sttActive = false;
      const final = this._sttFinal.trim();
      if (final && opts.onResult) {
        opts.onResult(final);
      }
    };

    recognition.onerror = (e) => {
      let msg = '';
      switch (e.error) {
        case 'not-allowed':      msg = 'Microphone access denied. Check browser permissions.'; break;
        case 'no-speech':        msg = 'No speech detected.'; break;
        case 'audio-capture':    msg = 'No microphone found.'; break;
        case 'aborted':          return; // user called stopSTT — silent
        default:                 msg = `Speech recognition error: ${e.error}`;
      }
      this._sttActive = false;
      if (opts.onError) opts.onError(msg);
    };

    try {
      recognition.start();
      this._recognition = recognition;
    } catch (e) {
      this._sttActive = false;
      if (opts.onError) opts.onError(`Failed to start: ${e.message}`);
    }
  }

  /** Stop speech recognition and return final transcript. */
  stopSTT() {
    if (this._recognition) {
      try { this._recognition.abort(); } catch (_) { /* ignore */ }
      this._recognition = null;
    }
    this._sttActive = false;
    return this._sttFinal.trim();
  }

  /** True while recognition is running. */
  get sttActive() { return this._sttActive; }

  // ── Text-to-Speech ──

  /**
   * Speak text via speechSynthesis.
   * Queued if another utterance is in progress — plays sequentially.
   * @param {string}   text
   * @param {object}   opts
   * @param {string}   opts.lang  — BCP 47 (default 'ru-RU')
   * @param {number}   opts.rate  — speech rate (0.1–10, default 1)
   * @param {function} opts.onEnd — called when finished
   * @param {function} opts.onError
   */
  speak(text, opts = {}) {
    if (!this.ttsSupported || !text) return;

    this._ttsQueue.push({ text, ...opts });
    if (!this._ttsProcessing) {
      this._processQueue();
    }
  }

  _processQueue() {
    if (this._ttsProcessing || this._ttsQueue.length === 0) return;
    this._ttsProcessing = true;

    const item = this._ttsQueue.shift();
    const utterance = new SpeechSynthesisUtterance(item.text);
    utterance.lang = item.lang || 'ru-RU';
    utterance.rate = item.rate || 1;

    // Prefer a voice that matches the language
    const voices = window.speechSynthesis.getVoices();
    const langPrefix = utterance.lang.split('-')[0];
    const match = voices.find(v => v.lang && v.lang.startsWith(langPrefix));
    if (match) utterance.voice = match;

    this._utterance = utterance;
    this._speaking = true;

    utterance.onend = () => {
      this._speaking = false;
      this._utterance = null;
      this._ttsProcessing = false;
      if (item.onEnd) item.onEnd();
      if (this._ttsQueue.length > 0) this._processQueue();
    };

    utterance.onerror = (e) => {
      this._speaking = false;
      this._utterance = null;
      this._ttsProcessing = false;
      if (item.onError) item.onError(e.error || 'speech synthesis error');
      if (this._ttsQueue.length > 0) this._processQueue();
    };

    window.speechSynthesis.speak(utterance);
  }

  /** Stop TTS immediately and clear queue. */
  stop() {
    window.speechSynthesis.cancel();
    this._speaking = false;
    this._utterance = null;
    this._ttsQueue = [];
    this._ttsProcessing = false;
  }

  /** True while speaking. */
  get speaking() { return this._speaking; }

  /** Check browser support. */
  get supported() { return this.sttSupported || this.ttsSupported; }
}

// Pre-warm voices list (some browsers load voices asynchronously)
if (window.speechSynthesis) {
  window.speechSynthesis.getVoices(); // trigger async load
  if (window.speechSynthesis.onvoiceschanged !== undefined) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }
}

const voiceAssistant = new VoiceAssistant();
export default voiceAssistant;

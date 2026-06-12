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

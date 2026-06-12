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
    sink: null,
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
        if (data.wake && opts.onWake) opts.onWake();
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
      // Keep the worklet inside an active render graph: some engines only
      // pull destination-connected subgraphs. Muted gain → destination.
      state.sink = state.ctx.createGain();
      state.sink.gain.value = 0;
      state.node.connect(state.sink);
      state.sink.connect(state.ctx.destination);
      state.attached = true;
    },

    detach() {
      state.attached = false;
      if (state.source && state.node) { try { state.source.disconnect(state.node); } catch (_) {} }
      if (state.node && state.sink) { try { state.node.disconnect(state.sink); } catch (_) {} }
      if (state.sink) { try { state.sink.disconnect(); } catch (_) {} state.sink = null; }
      state.node = null;
      state.source = null;
    },

    endUtterance() {
      if (state.connected) state.ws.send(JSON.stringify({ event: 'end' }));
    },

    abortUtterance() {
      if (state.connected) state.ws.send(JSON.stringify({ event: 'abort' }));
    },

    setMode(mode) {
      if (state.connected) state.ws.send(JSON.stringify({ mode }));
    },

    close() {
      state.closed = true;
      this.detach();
      if (state.ws) { try { state.ws.close(); } catch (_) {} }
      if (state.ctx) { try { state.ctx.close(); } catch (_) {} state.ctx = null; }
    },
  };
}

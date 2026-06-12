# Voice Dialog v2 — VAD, Barge-in, Streaming STT, Wake Word

**Date:** 2026-06-12
**Status:** Approved design
**Scope:** Odysseus (`tools/odysseus`) voice dialog mode v2. Builds on the shipped v1
(`static/js/voiceDialog.js`: RMS-VAD loop → `/api/stt/transcribe` → auto-send →
streaming TTS → loop).

## Context

v1 works but is basic: crude RMS silence detection (false triggers from ambient
noise, mid-sentence cut-offs), no interruption while the assistant speaks, no
feedback while listening, transcription only after the utterance ends, and the
loop must be started by tapping the toggle.

Voice stack in place: STT = faster-whisper `base` (local, CPU), TTS = Higgs v3 via
`endpoint:` provider (`:8801`), streaming sentence-by-sentence TTS path in
`tts-ai.js` driven by `aiTTSManager.autoPlay`.

Constraint: Odysseus frontend is plain ES modules, **no build step** — third-party
JS must be vendored as dist files under `static/vendor/`.

## Slices (each independently shippable, in order)

### v2a — Silero VAD + barge-in + UI feedback (frontend only)

**VAD.** Vendor `@ricky0123/vad-web` dist + onnxruntime-web wasm + Silero model
into `static/vendor/vad/`. New `static/js/vadEngine.js` wraps it:
`start(stream, opts)`, `stop()`, `onSpeechStart`, `onSpeechEnd(audio)`. Replaces
the RMS loop in `voiceDialog.js`; the RMS engine is kept as fallback when the
vendored model fails to load (offline asset corruption, old browser).
Tunables move to vad-web's `positiveSpeechThreshold` / `redemptionFrames`
(replacing `RMS_THRESHOLD` / `SILENCE_MS`).

**Barge-in.** Mic + VAD remain open during the `speaking` state. Mic constraints:
`echoCancellation: true, noiseSuppression: true, autoGainControl: true`.
Sustained speech ≥ 500 ms during playback → `aiTTSManager.stop()` + queue flush →
the interrupting speech is captured as the next utterance. Self-echo guard:
raised VAD threshold while TTS is playing (browser AEC cancels tab audio in
Chrome; the threshold covers the remainder). A barged-out reply is not resumable
(acceptable: the user chose to speak).

**UI.** Small waveform canvas fed by the existing analyser node + a state chip
(listening / thinking / speaking) rendered next to the dialog toggle.
`voiceDialog.js` is refactored to FSM-only; audio concerns live in `vadEngine.js`.

### v2b — Chunked streaming STT (backend + frontend)

**Backend.** NEW `routes/stt_stream_routes.py`: `WS /api/stt/stream`.
Protocol: client sends 16 kHz PCM16 mono binary frames; JSON control messages
`{"event":"end"}` / `{"event":"abort"}`. Server accumulates the utterance buffer;
every ~1.2 s runs faster-whisper on the rolling buffer
(`WhisperModel.transcribe(ndarray)` — float32 conversion in-process, no temp
files) and sends `{"partial":"..."}`. On `end`, a final pass returns
`{"final":"..."}`. Utterance cap 60 s. Same (open) auth posture as the existing
`/api/stt/transcribe`.

**Frontend.** NEW `static/js/sttStream.js`: AudioWorklet capture → 16 kHz PCM16
frames → WS. Partials render live into `#message` (greyed); `final` replaces and
triggers send. Latency win ≈ 1 s (transcription overlaps speech).

**Fallback.** WS connect/stream failure → auto-reconnect ×3 → drop to the v1
single-shot POST path (which stays intact).

**Cost.** Rolling re-transcription of one utterance with `base`/int8 on CPU is
affordable; capped by the 60 s utterance limit.

### v2c — Wake word "hey soloway" (server-side, reuses hermes)

New dialog state `standby`. Opt-in: the dialog toggle cycles three states —
off → dialog → dialog+standby → off (chip shows which). In dialog+standby, after a reply
is spoken, instead of open capture, the client keeps streaming audio over the
**same v2b WS** with `{"mode":"wake"}`. Server runs openwakeword
(`StreamWakeWordDetector` pattern from `apps/ai/hermes-voice-manager`, model
`~/.hermes/voice/hey_soloway_v0.1.onnx`, threshold 0.7, CPU-only) and replies
`{"wake":true}` on detection → client flips to active listening.

Dependency: `openwakeword` installed into the Odysseus venv (pip cache on
workspace per storage rule). v2c is blocked until v2b ships (needs the WS
channel).

## Error handling (all slices)

- WS drop → reconnect ×3 → v1 fallback path.
- VAD asset load failure → RMS fallback engine.
- Barge-in misfire (echo leak) → captured utterance transcribes ~empty → loop
  re-enters listening; no crash, reply already stopped (cost: one lost reply tail).
- Wake-word false accept in standby → captures noise → empty transcript → back to
  standby.
- STT/TTS service errors → same self-healing as v1 (toast + re-listen).

## Testing

- **Backend (pytest):** WS route fed a wav fixture in chunks → asserts ≥1
  `partial`, correct `final`; `abort` mid-stream; 60 s cap; wake-mode frame
  handling (v2c) with a positive + negative fixture.
- **Frontend (manual matrix, Chrome desktop + phone Safari/Chrome over tailnet):**
  trigger words vs fan-noise no-trigger; mid-sentence barge-in; WS killed
  mid-utterance (fallback engages); standby wake on "hey soloway", no wake on TV
  speech.

## Files

v2a NEW: `static/js/vadEngine.js`, `static/vendor/vad/*`.
v2a EDIT: `static/js/voiceDialog.js` (FSM-only refactor), `static/index.html`
(script tag + vendor preload).
v2b NEW: `routes/stt_stream_routes.py`, `static/js/sttStream.js`,
`tests/test_stt_stream.py`.
v2b EDIT: `app.py` or router registration point, `services/stt/stt_service.py`
(ndarray transcribe entry), `static/js/voiceDialog.js` (use sttStream).
v2c NEW: `services/stt/wakeword.py` (detector wrapper).
v2c EDIT: `routes/stt_stream_routes.py` (wake mode), `static/js/voiceDialog.js`
(standby state), venv dep `openwakeword`.

## Out of scope

- Resumable barged-out replies.
- Browser-side wake word (onnx-web port of the openwakeword chain).
- Voice cloning / custom Higgs voice mapping (separate task).
- Any multi-agent work (separate spec).

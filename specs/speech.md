# Speech

Last updated: Phase 1 #6319 (dedicated mic button, transcribe-upload, STT settings, language return)

## Scope

This spec covers speech behavior in:

- app service initialization and route registration in `app.py`;
- `services/stt/stt_service.py`;
- `services/tts/tts_service.py`;
- `routes/stt_routes.py`;
- `routes/tts_routes.py`;
- `src/upload_limits.py`;
- settings defaults/cache in `src/settings.py`;
- settings routes in `routes/auth_routes.py`;
- model endpoint cleanup in `routes/model_routes.py`;
- settings/tool aliases in `src/tool_implementations.py`;
- frontend modules `static/js/voiceRecorder.js`, `static/js/fileHandler.js`, `static/js/tts-ai.js`, `static/app.js`, `static/js/chat.js`, `static/js/slashCommands.js`, `static/js/keyboard-shortcuts.js`, `static/js/settings.js`, and `static/index.html`;
- optional dependency declarations in `requirements-optional.txt`;
- runtime cache path `data/tts_cache/`;
- tests covering speech service toggles, TTS speed/cache, STT temp cleanup, upload limits, settings scrubbing, model endpoint cleanup, STT Phase 1 validation/cache-invalidation/language/attachment-transcribe routes.

## Current Call Sites Include

- dedicated composer mic button (`#mic-btn`) plus Send-button send/new-chat/streaming behavior;
- browser and server STT recording paths;
- chat message read-aloud buttons and streaming TTS queueing;
- `/tts` slash command playback;
- keyboard shortcut TTS activation;
- admin/settings API writes and `manage_settings` aliases;
- model endpoint deletion cleanup for `endpoint:<id>` speech providers.

## STT

`services.stt.STTService` owns speech-to-text provider behavior. `routes/stt_routes.py` owns `/api/stt/transcribe`, `/api/stt/transcribe-upload`, and `/api/stt/stats`. `static/js/voiceRecorder.js` owns microphone capture, browser STT, server upload, and audio-attachment fallback. `static/js/sttTranscribeQueue.js` (shared singleton, concurrency 1) serializes all uploaded-audio jobs; `static/js/sttTranscriptDoc.js` owns `<stem>_raw.md` titling/save/open via `POST /api/document`; `static/js/fileHandler.js` (pending chips) starts transcription the moment a file is attached (`window._sttAutoEnqueuePending`, blob `POST /api/stt/transcribe`); chips show only status text plus the equalizer while active, Retry for failed items, and drop themselves shortly after completion. `static/js/chatRenderer.js` (sent audio cards) adopts the pending job by file id (`window._sttAdoptSentFiles`, same bytes never transcribed twice) and only enqueues genuinely new uploads (by upload id, `POST /api/stt/transcribe-upload`); the active card shows the equalizer, batch documents are created automatically and the final document opens once. Batch state is shared on `window.__sttBatchState` because chatRenderer.js is instantiated several times per load (different `?v=` copies). There is no output-mode setting: the drained batch size decides — one success yields its own `<stem>_raw.md`, two or more yield exactly one combined document (`# Audio Transcripts` plus one verbatim `## <recording>` section each, queue order, failures skipped). Mic dictation bypasses the queue straight into the composer.

Provider runtime:

- `disabled` returns unavailable and avoids provider calls;
- `browser` is client-side only through Web Speech API and does not call `/api/stt/transcribe`;
- `local` lazily imports `faster-whisper`, writes uploaded audio to a temporary file (suffix follows the original container: webm/weba/wav/mp3/m4a/ogg/oga/opus/flac/aac/aiff/aif, default webm), transcribes, and deletes the temp file in `finally`. Unavailable STT now reports the cause (disabled toggle vs missing faster-whisper install vs load failure) instead of a generic error. The model loads only on a real transcription request — never at startup, on selection, or from stats/availability checks — under a lock so concurrent requests instantiate it once. `keep_model_loaded=false` (default) releases it after each transcription; `true` keeps it resident. A `stt_model`/provider change invalidates the cache at settings-save time; the replacement loads on the next real request.
- `endpoint:<id>` resolves a `ModelEndpoint`: Google Gemini bases (`generativelanguage.googleapis.com`) use the native Interactions transcription API — Files API resumable upload, then `POST {root}/interactions` with `{model, input: [{type: audio, uri, mime_type}]}` plus documented `generation_config.transcription_config.language_codes` when a language is configured (omitted for auto-detect); the create call may return only the initial interaction object, in which case `GET {root}/interactions/{id}` is polled (5 s cadence, ~4 min budget, same worker thread, 429 backs off) until `completed`, and the transcript is read from `output_text`/trailing `outputs` text (raw text preserved); `failed`/`cancelled`/`requires_action`/timeout yield a logged, sanitized diagnosis, never partial text; all other endpoints post to OpenAI-compatible `/audio/transcriptions` with model and optional language (filename/MIME follows the original container).
- malformed `stt_model`/`stt_language`/`keep_model_loaded` values are rejected at the settings API and ignored defensively in the service.

Route behavior:

- audio uploads are capped by the shared STT upload limit from `src.upload_limits`, including environment override validation;
- empty uploads return a route error;
- `/api/stt/transcribe` returns additive `{"text", "language"}` (`text` preserved for old consumers; `language` is the detected/requested code or "");
- `/api/stt/transcribe-upload` transcribes an existing upload by `file_id`: validates the ID, verifies ownership via `upload_handler.resolve_upload` (404 when unreadable), requires `is_audio_file` (WAV/MP3/M4A/WEBM/OGG, 400 otherwise), enforces the STT size cap (413), and returns `{"text", "language", "file_id", "file_name"}`;
- local inference runs off the event loop (`asyncio.to_thread`) and `/api/stt` is exempt from the app hard-request timeout (cf. `/api/image` precedent), so minute-long CPU transcriptions no longer 504 while holding the loop;
- local limits: the app cap is 25 MiB per request (`STT_MAX_AUDIO_BYTES`, env-overridable); the pipeline passes no VAD/chunking options, so faster-whisper decodes each file in a single `transcribe()` call — the practical bound is host RAM plus wall-clock time (expect minutes-per-minute-of-audio on small CPUs with medium/large models);
- local observability: model load start/done (size/device/compute/elapsed), transcription start (bytes/container/model/language), real segment progress every 25 segments (segment count + audio timestamp reached — counting, not percentages), completion summary, and model release when `keep_model_loaded` is false;
- direct audio attachments for capable models are unchanged (`document_processor.build_user_content` audio branch);
- endpoint providers report optimistic availability and fail at request time if offline/misconfigured.

Frontend behavior:

- dedicated `#mic-btn` beside Send (visible only when STT is enabled); Send never starts recording;
- browser microphone capture requires a secure context — HTTPS or localhost — plus microphone permission; over plain HTTP on a non-local host the mic cannot start (server networking is unchanged; open Odysseus via HTTPS or localhost instead);
- uploading an existing audio file and transcribing it does not need microphone permission or a secure context — only capture does;
- server transcription success inserts raw text into the input (verbatim user data, reviewable before send);
- failed server transcription can attach the recorded audio file to chat instead; empty transcription shows a no-speech message;
- pending audio chips offer Transcribe (in-memory file, no double upload) and Transcribe-all when 2+ are pending; sent audio cards offer Transcribe (by upload ID); every job runs through the shared sequential queue (concurrency 1, FIFO, per-card Queued · i/n / Transcribing… / Transcribed / Failed states, failure isolation, duplicate-enqueue protection, identical-repeat document reuse) with a layout-stable card (fixed-width buttons, ellipsis filename, inline player, status line, Open-transcript view, Copy action, Open-doc action). Each successful item saves its `<stem>_raw.md` raw document (verbatim transcript, existing `POST /api/document` route, no LLM step) before the next item starts. Uploaded-audio transcripts never enter the composer and are never auto-sent; mic dictation still inserts into the composer directly.

## TTS

`services.tts.TTSService` owns text-to-speech provider behavior, speed parsing, cache behavior, and local/provider-specific synthesis. `routes/tts_routes.py` owns `/api/tts/stats`, `/api/tts/synthesize`, and cache clearing. `static/js/tts-ai.js` owns frontend playback, client object-URL caching, browser TTS, queueing, and streaming button state.

Provider runtime:

- `disabled` returns unavailable and avoids provider calls;
- `browser` is client-side only through `speechSynthesis`;
- `local` currently means Kokoro and requires `torch`, `kokoro`, `soundfile`, and CUDA/import availability;
- `endpoint:<id>` resolves a `ModelEndpoint` and posts to `/audio/speech`.
- unknown or non-string `tts_provider` values are treated as unavailable rather
  than being parsed as endpoint strings.

Route behavior:

- `/api/tts/synthesize` supports binary `audio` responses and JSON `base64` responses;
- binary responses choose WAV or MP3 MIME by audio magic bytes;
- synthesis input is passed to the service as submitted and capped there;
- malformed or nonpositive `tts_speed` falls back to `1.0`;
- provider unavailable returns 503; failed synthesis/transcription generally returns route-level failure.

## Settings, Endpoints, And Cache

Speech providers are global settings under `data/settings.json`, with defaults in `src/settings.py`. Settings reads are scrubbed for non-admin callers, writes are admin-only (with per-key validation for `stt_provider`/`stt_model`/`stt_language`/`stt_enabled`/`keep_model_loaded`), and `manage_settings` can change non-secret speech settings through aliases.

Visible UI state: the STT card (enabled/provider/model/language/keep-loaded) lives in Settings → AI Defaults, with inline help per provider type (browser Web Speech API vs local faster-whisper vs generic OpenAI-compatible vs native Gemini endpoint) and approximate parameter counts on local model labels as reference values; the TTS settings card remains hidden, and the STT settings JS still exits when its DOM nodes are absent (minimal builds).

`routes.model_routes` clears `tts_provider` and `stt_provider` references when a referenced model endpoint is deleted.

TTS cache behavior:

- server cache lives under `data/tts_cache/`;
- cache keys include provider, model, voice, safe speed, and text;
- cache files are stored as MP3 or WAV;
- route stats expose global cache state;
- cache clear is global;
- frontend TTS has a separate object-URL cache.

`ODYSSEUS_TTS_CACHE_MAX_BYTES` bounds server cache growth and is forwarded by all Compose variants. The default is 500 MiB; invalid integers fall back to that default and values at or below zero disable eviction. After a cache write, enforcement scans only `.mp3`/`.wav`, ignores files that disappear or cannot be stated, and when over limit removes oldest-by-mtime entries toward 80% of the ceiling. Sort/stat/unlink failures are logged and do not fail synthesis.

## Security And Provenance

Speech routes rely on app-wide authentication and do not implement route-local admin or scope checks. Bearer-token callers that pass app auth can reach speech stats/synthesis/transcription/cache-clear surfaces using global speech settings.

Endpoint providers send user audio or assistant text to configured `ModelEndpoint` URLs with optional bearer keys. Endpoint lookup is by configured endpoint ID and currently does not enforce per-request owner filtering. `ModelEndpoint.api_key` is encrypted at rest and forwarded only process-side.

Microphone audio, uploaded audio, endpoint transcripts, and assistant text sent to TTS are untrusted/user/provider-visible data flows. Transcripts become user input; they are not trusted system instructions. Phase 1 inserts raw STT text into the composer (persisted via the normal `Session.add_message` path on send) and never interpolates it into system/developer prompts.

TTS cached audio can contain sensitive assistant text rendered as speech. The cache is global, has no owner partition or TTL, and is served inline/base64 by POST responses without a dedicated generated-file route.

## Degraded Behavior

- Optional local speech packages may be absent. In particular, the default Docker image excludes `requirements-optional.txt`, so `faster-whisper` is missing and the local provider reports unavailable: rebuild with `docker compose build --build-arg INSTALL_OPTIONAL=true` (see `website/setup.md`) or use the Browser/API providers.
- Local STT can run CPU-only and tolerates missing/broken torch by falling back to CPU/int8 behavior.
- Local TTS/Kokoro extras are declared as `kokoro==0.9.4` plus `soundfile` only for Python 3.11-3.12; Python 3.13+ intentionally skips them because Kokoro excludes those runtimes. Even where installed, local Kokoro remains unavailable without a CUDA-capable torch build/GPU.
- External endpoint providers can be offline or misconfigured and may only fail at request time.
- Browser `speechSynthesis`, `SpeechRecognition`, `webkitSpeechRecognition`, secure context, and microphone permissions can be absent.
- Docker GPU overlays are passthrough-only and do not install speech engines by themselves.
- Optional dependency errors and route error wording are not fully consistent across STT and TTS.

## Testing Coverage

Existing coverage includes speech service toggles, malformed/non-string TTS provider and speed handling, cache stats plus configured eviction/disable/file filtering/error handling, STT temp cleanup, direct upload limits, model routes, settings scrubbing, STT model/language sanitizers, model cache invalidation, additive language responses, attachment transcribe-upload (ownership/validation/size/failure) routes, settings validation, lazy lifecycle/keep-loaded/concurrency, Gemini adapter (Files API transport, request construction incl. language_codes, response extraction, interaction polling incl. requires_action/429/timeout, error handling), sequential queue lifecycle (order, concurrency, isolation, dedup, positions, verbatim, reuse), destination separation, local observability logging, and transcribe-UI static invariants.

Missing coverage includes route-level STT/TTS success and failure shapes, auth/API-token behavior, endpoint owner isolation, STT type/magic rejection, TTS request-size/no-store/cache privacy behavior, degraded optional dependency paths, and frontend recorder/TTS fallback states.

## Current Gaps

- Speech routes need a deliberate API-token/scope policy.
- Endpoint speech providers need owner-isolation or explicit global-settings documentation.
- TTS cache needs privacy policy: owner partition, TTL, no-store response headers, or accepted global cache semantics.
- STT direct-blob uploads still accept any content type (extension/MIME/magic policy only enforced on the transcribe-upload path); magic-byte sniffing is future work.
- Compare-view STT mic behavior needs a product decision or regression test because compare can force send-button visuals (dedicated mic button is independent of compare).
- Whisper model catalog/download/status (Phase 2), LLM transcript post-processing (Phase 2), and long-audio async/chunking (Phase 3) are deliberately deferred.

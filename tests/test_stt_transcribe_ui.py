"""STT transcribe UI invariants: automatic audio flow (no buttons), equalizer
animation on the active card only, stable audio-card layout, settings-card
alignment, and destination separation (uploaded audio -> document,
mic -> composer).

Source-text assertions here are the narrow exception allowed by
TESTING_STANDARD.md: the invariants are CSS geometry / static markup, and
driving them at runtime would need a layout engine plus microphone
permissions, which the suite has no runner for.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
CHAT_RENDERER = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
FILE_HANDLER = (ROOT / "static" / "js" / "fileHandler.js").read_text(encoding="utf-8")
VOICE_RECORDER = (ROOT / "static" / "js" / "voiceRecorder.js").read_text(encoding="utf-8")
TRANSCRIPT_DOC = (ROOT / "static" / "js" / "sttTranscriptDoc.js").read_text(encoding="utf-8")


def _block(selector: str) -> str:
    m = re.search(rf"^[ \t]*{re.escape(selector)}[ \t]*\{{", CSS, re.MULTILINE)
    assert m, f"no {selector} rule found"
    return CSS[m.end(): CSS.index("}", m.end())]


def _px(block: str, prop: str) -> float | None:
    m = re.search(rf"(?:^|;)\s*{prop}\s*:\s*(-?[\d.]+)px", block)
    return float(m.group(1)) if m else None


def test_equalizer_animation_is_visible_and_bounded():
    # The automatic flow shows one animated equalizer on the active card —
    # clearly larger than the old 14px spinner, no fake progress.
    assert ".stt-eq" in CSS, "equalizer indicator rule must exist"
    assert "@keyframes stt-eq-bounce" in CSS, "equalizer bars must animate"
    eq = _block(".stt-eq")
    assert re.search(r"height\s*:\s*22px", eq), "equalizer must be clearly visible (22px tall)"
    assert ".stt-eq[hidden]" in CSS, "equalizer must hide completely when not active"
    assert ".stt-retry-btn" in CSS, "failed-item retry button rule must exist"


def test_no_transcribe_buttons_remain_in_audio_flow():
    # The normal workflow must not offer Transcribe / Transcribe-all /
    # Open-document actions anywhere in the audio upload path.
    assert "Transcribe all" not in CHAT_RENDERER
    assert "Transcribe all" not in FILE_HANDLER
    assert "thumb-transcribe-all" not in FILE_HANDLER
    assert "thumb-transcribe-btn" not in FILE_HANDLER
    assert "attach-transcribe-btn" not in CHAT_RENDERER, \
        "sent audio cards must not create transcribe buttons"
    assert "attach-copy-btn" not in CHAT_RENDERER
    assert "attach-open-btn" not in CHAT_RENDERER
    assert "attach-doc-btn" not in CHAT_RENDERER
    assert "Open doc" not in CHAT_RENDERER
    assert "Open doc" not in FILE_HANDLER
    assert "_transcribeSentAudio" not in CHAT_RENDERER, \
        "manual transcribe entry point must be gone"
    assert "_transcribePendingAudio" not in FILE_HANDLER
    assert "_pendingAudioJob" not in FILE_HANDLER


def test_pending_chips_auto_enqueue_at_attach_time():
    # Attaching a file must start transcription immediately (no Send, no
    # button): addFiles calls the chatRenderer hook with the file + key.
    assert "_sttAutoEnqueuePending" in FILE_HANDLER
    assert "_pendingAudioKey" in FILE_HANDLER
    assert "_pendingByKey" in FILE_HANDLER
    # Failed chips offer Retry only.
    assert "_sttRetryPending" in FILE_HANDLER
    assert "stt-retry-btn" in FILE_HANDLER
    # Chip carries equalizer + status, keyed for queue updates.
    assert "stt-eq" in FILE_HANDLER
    assert "thumb-stt-status" in FILE_HANDLER
    assert "data-stt-key" in FILE_HANDLER or "dataset.sttKey" in FILE_HANDLER


def test_sent_cards_adopt_pending_jobs_without_retranscribing():
    # uploadPending links file ids to pending keys; sent cards resolve the
    # original job key so the same bytes are never transcribed twice.
    assert "_sttAdoptSentFiles" in FILE_HANDLER
    assert "_sttAdoptSentFiles" in CHAT_RENDERER
    assert "_resolveJobKey" in CHAT_RENDERER
    # Sent auto-enqueue fires only for genuinely new uploads: any known
    # state (queued/transcribing/completed/failed) merely repaints, so
    # re-renders, history loads and regenerations never re-transcribe.
    auto_audio = _function_body(
        CHAT_RENDERER,
        "function _autoEnqueueAudio(att, opts)",
        "/** Link uploaded file ids",
    )
    assert "state !== undefined" in auto_audio
    # Pending enqueue is synchronous up to queue.enqueue (mode resolves
    # lazily in saveDoc), so multi-file order can never interleave.
    pending_audio = _function_body(
        CHAT_RENDERER,
        "function _autoEnqueuePending(file, key, opts)",
        "async function _transcribePendingBlob(file)",
    )
    assert "queue.enqueue({" in pending_audio
    assert "_readAudioOutputMode" not in CHAT_RENDERER
    assert "_saveJobDoc" in CHAT_RENDERER


def test_audio_card_layout_is_stable():
    card = _block(".attach-card-audio")
    assert "flex-direction" in card and "column" in card
    name_rule = _block(".attach-card-audio .attach-card-name")
    assert "max-width" in name_rule, "filename must ellipsize instead of resizing"


def test_speech_heading_matches_neighbor_cards():
    # Headings are single-line elements; match within one line only.
    headings = re.findall(r"<h2[^>\n]*>[^\n]*?Speech to Text.*?</h2>", INDEX)
    assert len(headings) == 1, f"expected exactly one Speech to Text heading, found {len(headings)}"
    assert "margin-right:1px" in headings[0], "icon must use the shared 1px icon gap"
    assert "flex-shrink:0" in headings[0], "icon must not shrink like neighbors"
    assert "vertical-align:-2px" not in headings[0], "no legacy vertical-align nudge"
    assert "margin-right:5px" not in headings[0], "no legacy 5px icon gap"


def test_settings_has_no_notes_block_or_long_descriptions():
    assert "set-sttNotes" not in INDEX, "notes block must stay removed"
    assert "Notes:" not in INDEX.split("Speech to Text")[1].split("Text to Speech")[0], \
        "no large notes paragraph in the STT card"
    # Help rows exist as empty shells filled minimally by settings.js.
    assert 'id="set-sttProviderHelp"' in INDEX
    assert 'id="set-sttModelHelp"' in INDEX


def test_api_providers_stay_marked_experimental():
    assert "API (Experimental)" in INDEX, "provider optgroup must flag API as experimental"
    assert SETTINGS_JS.count("Experimental") >= 4, "endpoint help texts must keep the flag"


def test_language_autodetect_help_preserved():
    assert "auto (empty)" in INDEX, "language auto-detect hint must survive the polish"


def test_keep_loaded_uses_standard_switch():
    m = re.search(r'<label class="admin-switch"[^>]*><input type="checkbox" id="set-sttKeepLoadedToggle">', INDEX)
    assert m, "Keep model loaded must use the standard admin-switch pattern"


def _function_body(src: str, marker: str, end_marker: str) -> str:
    """Slice one top-level function body between two markers."""
    start = src.index(marker)
    end = src.index(end_marker, start)
    return src[start:end]


def test_audio_cards_auto_enqueue_without_buttons():
    # Cards join the queue the moment they are built — no click required.
    assert "_autoEnqueueAudio" in CHAT_RENDERER
    assert "_syncAudioCardFromQueue(card, att)" in CHAT_RENDERER
    # Batch completion opens the final document exactly once via the
    # existing viewer (never by simulating a button click).
    assert "_maybeFinalizeBatch" in CHAT_RENDERER
    assert "openRawDocument(" in CHAT_RENDERER
    assert ".click()" not in _function_body(
        CHAT_RENDERER,
        "async function _maybeFinalizeBatch()",
        "/** Fresh sent card:",
    )
    # Status vocabulary for the automatic flow.
    for label in ("Queued ·", "Transcribing", "Transcribed", "Failed"):
        assert label in CHAT_RENDERER, f"missing status label {label!r}"
    # Combined document shape: one header plus per-recording sections.
    assert "# Audio Transcripts" in TRANSCRIPT_DOC
    assert "buildCombinedMarkdown" in TRANSCRIPT_DOC
    assert "buildCombinedMarkdown" in CHAT_RENDERER


def test_no_audio_output_setting_remains():
    # The count decides (1 = separate doc, 2+ = combined doc) — no setting
    # UI, no wiring, no backend default may remain.
    assert 'set-sttOutputSelect' not in INDEX
    assert 'set-sttOutputRow' not in INDEX
    assert "audio_document_output" not in SETTINGS_JS
    settings_py = (ROOT / "src" / "settings.py").read_text(encoding="utf-8")
    assert "audio_document_output" not in settings_py


def test_batch_size_decides_separate_vs_combined():
    # One success -> its own <stem>_raw.md; several -> exactly one combined
    # document with per-recording headings; the document opens exactly once.
    finalize = _function_body(
        CHAT_RENDERER,
        "async function _maybeFinalizeBatch()",
        "/** Fresh sent card:",
    )
    assert "ok.length === 1" in finalize
    assert "buildCombinedMarkdown" in finalize
    assert "openRawDocument(" in finalize
    assert finalize.count("openRawDocument(") == 2, \
        "exactly two open sites: single doc and combined doc"
    assert "audio_document_output" not in CHAT_RENDERER, \
        "no output-mode setting may drive the flow"
    assert "_lockedBatchMode" not in CHAT_RENDERER
    assert "_readAudioOutputMode" not in CHAT_RENDERER
    assert "# Audio Transcripts" in TRANSCRIPT_DOC


def test_batch_state_is_shared_across_module_instances():
    # chatRenderer.js is instantiated several times per load (different ?v=
    # copies); the batch must live on window or batches split in two.
    assert "__sttBatchState" in CHAT_RENDERER


def test_queue_watchdog_timeouts_exist():
    # A wedged transcribe/save must fail the item instead of freezing the
    # whole queue forever (transcribe ran, document never followed).
    QUEUE = (ROOT / "static" / "js" / "sttTranscribeQueue.js").read_text(encoding="utf-8")
    assert "transcribeTimeoutMs" in QUEUE
    assert "saveTimeoutMs" in QUEUE
    assert "timed out" in QUEUE


def test_audio_format_sets_agree_everywhere():
    # Client detectors and the server gate must accept the same formats —
    # otherwise files transcribe on one path and vanish silently on another.
    for ext in ("weba", "oga", "opus", "flac", "aac", "aiff", "aif"):
        assert ext in FILE_HANDLER, f"fileHandler must detect .{ext}"
        assert ext in CHAT_RENDERER, f"chatRenderer must detect .{ext}"
    import sys
    sys.path.insert(0, str(ROOT))
    from src.upload_handler import UploadHandler
    h = UploadHandler.__new__(UploadHandler)
    for ext in ("webm", "weba", "wav", "mp3", "m4a", "ogg", "oga", "opus", "flac", "aac", "aiff", "aif"):
        assert h.is_audio_file("rec." + ext, ""), f"server must accept .{ext}"
    assert h.is_audio_file("rec.opus", "audio/opus")
    assert not h.is_audio_file("doc.pdf", "application/pdf")
    # Unsupported-but-audio-looking files are announced, never swallowed.
    assert "_isUnsupportedAudio" in FILE_HANDLER


def test_frontend_build_stamp_and_sw_refresh():
    # Devtools-checkable stamp proving the running code has the auto-flow,
    # plus a service-worker cache bump so updated JS/CSS actually arrives.
    assert "__sttBuild" in CHAT_RENDERER
    SW = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "stt-autoflow" in SW, "SW CACHE_NAME must be bumped for the STT flow"


def test_finished_chips_drop_from_the_strip():
    # A transcribed recording did its job: after a brief "Transcribed"
    # flash it vanishes from the attached files on its own.
    assert "_sttDropPendingFile" in CHAT_RENDERER
    assert "_sttDropPendingFile" in FILE_HANDLER


def test_uploaded_audio_never_touches_composer():
    # Scope to the automatic upload flow (the module also hosts unrelated
    # composer features such as quote/reply, so whole-file assertions would
    # be wrong). The auto flow may not use the mic insertion helper or write
    # the composer input.
    auto_audio = _function_body(
        CHAT_RENDERER,
        "function _autoEnqueueAudio(att, opts)",
        "// Build the `.attach-cards` element",
    )
    assert "insertTranscription" not in auto_audio
    assert "getElementById('message')" not in auto_audio
    assert 'getElementById("message")' not in auto_audio
    assert "insertTranscription" not in FILE_HANDLER, \
        "pending-chip flow must not use the mic composer-insertion helper"


def test_mic_path_still_inserts_into_composer():
    assert "insertTranscription" in VOICE_RECORDER, "mic dictation must keep its composer path"

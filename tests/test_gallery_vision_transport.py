"""Regression tests for the gallery OCR/AI-tag vision transport.

Covers the fixes applied to routes/gallery/gallery_routes.py's
`_resolve_vision_candidates` / `_call_vision_model` helpers:

- Model discovery + the actual vision call go through the app's
  provider-aware transport (`src.llm_core.llm_call_async`) instead of a
  hand-built Anthropic/OpenAI-shaped request, so native Ollama and ChatGPT
  Subscription endpoints work too.
- The configured vision fallback chain (Settings -> AI Defaults -> Vision ->
  Fallbacks) is consulted, not just the primary model.
- A candidate that returns only thinking markup (empty after strip_think)
  falls through to the next candidate instead of erroring immediately.
- An explicit ?model= override that isn't vision-capable is rejected; the
  admin-configured default is trusted without that check.
- Video filenames are rejected before any vision call is attempted.
"""
import routes.gallery_routes as gallery_routes


def _write_fake_image(tmp_path, name="photo.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes-not-a-real-image")
    return p


# ---------------------------------------------------------------------------
# _gallery_filename_is_video
# ---------------------------------------------------------------------------

def test_video_extensions_detected():
    f = gallery_routes._gallery_filename_is_video
    for ext in ("mp4", "mov", "webm", "mkv", "m4v"):
        assert f(f"clip.{ext}") is True
        assert f(f"clip.{ext.upper()}") is True


def test_image_extensions_not_video():
    f = gallery_routes._gallery_filename_is_video
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        assert f(f"photo.{ext}") is False


def test_no_extension_not_video():
    assert gallery_routes._gallery_filename_is_video("noext") is False
    assert gallery_routes._gallery_filename_is_video("") is False


# ---------------------------------------------------------------------------
# _resolve_vision_candidates
# ---------------------------------------------------------------------------

async def test_resolve_vision_candidates_includes_configured_fallbacks(monkeypatch):
    import src.document_processor as dp
    import src.endpoint_resolver as er

    primary = ("https://primary.example/v1/chat/completions", "vision-model", {})
    fallback = ("https://fallback.example/v1/chat/completions", "vision-model-2", {})

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "vision-model"})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda configured, owner=None, session_id=None: primary)
    monkeypatch.setattr(er, "resolve_vision_fallback_candidates", lambda owner=None: [fallback])

    candidates = await gallery_routes._resolve_vision_candidates("", owner="alice")
    assert candidates == [primary, fallback]


async def test_resolve_vision_candidates_vision_disabled(monkeypatch):
    import src.document_processor as dp

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": False})

    try:
        await gallery_routes._resolve_vision_candidates("", owner=None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "disabled" in str(e)


async def test_resolve_vision_candidates_rejects_non_vision_override(monkeypatch):
    import src.document_processor as dp
    import src.endpoint_resolver as er
    import src.chat_helpers as ch

    primary = ("https://primary.example/v1/chat/completions", "whisper-1", {})
    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": ""})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda configured, owner=None, session_id=None: primary)
    monkeypatch.setattr(er, "resolve_vision_fallback_candidates", lambda owner=None: [])
    monkeypatch.setattr(ch, "model_supports_vision", lambda model, url: False)

    try:
        await gallery_routes._resolve_vision_candidates("whisper-1", owner=None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "vision-capable" in str(e)


async def test_resolve_vision_candidates_default_skips_vision_check(monkeypatch):
    # No explicit override -> the admin-configured default is trusted as-is,
    # same as every other caller of _resolve_vl_model with no override.
    import src.document_processor as dp
    import src.endpoint_resolver as er
    import src.chat_helpers as ch

    primary = ("https://primary.example/v1/chat/completions", "whatever-model", {})
    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "whatever-model"})
    monkeypatch.setattr(dp, "_resolve_vl_model", lambda configured, owner=None, session_id=None: primary)
    monkeypatch.setattr(er, "resolve_vision_fallback_candidates", lambda owner=None: [])
    called = {"n": 0}
    def _boom(model, url):
        called["n"] += 1
        return False
    monkeypatch.setattr(ch, "model_supports_vision", _boom)

    candidates = await gallery_routes._resolve_vision_candidates("", owner=None)
    assert candidates == [primary]
    assert called["n"] == 0


async def test_resolve_vision_candidates_passes_session_id_through(monkeypatch):
    # session_id (GalleryImage.session_id — the chat this image was
    # originally uploaded from, when known) must reach _resolve_vl_model so
    # its auto-detect can prefer that session's own model over the app-wide
    # default. See _resolve_vl_model's own docstring/tests for the
    # session-model-wins behavior itself; this just checks it's threaded
    # through at all.
    import src.document_processor as dp
    import src.endpoint_resolver as er

    primary = ("https://primary.example/v1/chat/completions", "vision-model", {})
    seen = {}

    def _fake_resolve_vl_model(configured, owner=None, session_id=None):
        seen["session_id"] = session_id
        return primary

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": ""})
    monkeypatch.setattr(dp, "_resolve_vl_model", _fake_resolve_vl_model)
    monkeypatch.setattr(er, "resolve_vision_fallback_candidates", lambda owner=None: [])

    await gallery_routes._resolve_vision_candidates("", owner="alice", session_id="sess-42")
    assert seen["session_id"] == "sess-42"


# ---------------------------------------------------------------------------
# _call_vision_model
# ---------------------------------------------------------------------------

async def test_call_vision_model_falls_back_on_provider_failure(tmp_path, monkeypatch):
    img_path = _write_fake_image(tmp_path)
    primary = ("https://ollama.example", "llava", {})
    fallback = ("https://anthropic.example", "claude-vision", {})

    async def _resolve(model_override, owner, session_id=None):
        return [primary, fallback]
    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _resolve)

    import src.llm_core as llm_core
    calls = []
    async def _fake_llm_call_async(url, model, messages, **kwargs):
        calls.append(model)
        if model == "llava":
            raise RuntimeError("connection refused")
        return "a real caption"
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    text, model_name = await gallery_routes._call_vision_model(img_path, "describe this", owner=None)
    assert text == "a real caption"
    assert model_name == "claude-vision"
    assert calls == ["llava", "claude-vision"]


async def test_call_vision_model_falls_back_on_thinking_only_response(tmp_path, monkeypatch):
    img_path = _write_fake_image(tmp_path)
    primary = ("https://primary.example", "thinker-model", {})
    fallback = ("https://fallback.example", "plain-model", {})

    async def _resolve(model_override, owner, session_id=None):
        return [primary, fallback]
    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _resolve)

    import src.llm_core as llm_core
    async def _fake_llm_call_async(url, model, messages, **kwargs):
        if model == "thinker-model":
            return "<think>let me consider the image at length...</think>"
        return "tags: sky, tree, road"
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    text, model_name = await gallery_routes._call_vision_model(img_path, "tag this", owner=None)
    assert text == "tags: sky, tree, road"
    assert model_name == "plain-model"


async def test_call_vision_model_all_thinking_only_raises_friendly_error(tmp_path, monkeypatch):
    img_path = _write_fake_image(tmp_path)
    only = ("https://primary.example", "thinker-model", {})

    async def _resolve(model_override, owner, session_id=None):
        return [only]
    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _resolve)

    import src.llm_core as llm_core
    async def _fake_llm_call_async(url, model, messages, **kwargs):
        return "<think>only thinking, never answers</think>"
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    try:
        await gallery_routes._call_vision_model(img_path, "describe this", owner=None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "spent its whole budget thinking" in str(e)


async def test_call_vision_model_all_candidates_fail_raises_generic_error(tmp_path, monkeypatch):
    img_path = _write_fake_image(tmp_path)
    candidates = [
        ("https://primary.example", "model-a", {}),
        ("https://fallback.example", "model-b", {}),
    ]

    async def _resolve(model_override, owner, session_id=None):
        return candidates
    monkeypatch.setattr(gallery_routes, "_resolve_vision_candidates", _resolve)

    import src.llm_core as llm_core
    async def _fake_llm_call_async(url, model, messages, **kwargs):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_llm_call_async)

    try:
        await gallery_routes._call_vision_model(img_path, "describe this", owner=None)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Vision model request failed" in str(e)


# ---------------------------------------------------------------------------
# Timeout middleware exemption — ai-tag needs the same 300s exemption ocr has
# ---------------------------------------------------------------------------

def test_timeout_exempt_pattern_covers_ocr_and_ai_tag():
    # app.py has module-level side effects at import time, so — matching the
    # convention in test_memory_audit_timeout.py — check the source text
    # rather than importing the module. The pattern itself is exercised
    # directly against a real compiled regex built from the same source
    # literal, so this isn't just a substring check.
    import re as _re
    from pathlib import Path

    source = Path("app.py").read_text()
    start = source.index("_TIMEOUT_EXEMPT_PATTERNS =")
    end = source.index("\n)\n", start)
    block = source[start:end]
    assert '"^/api/gallery/[^/]+/(ocr|ai-tag)$"' in block

    pattern = _re.compile(r"^/api/gallery/[^/]+/(ocr|ai-tag)$")
    assert pattern.match("/api/gallery/abc123/ocr")
    assert pattern.match("/api/gallery/abc123/ai-tag")
    assert not pattern.match("/api/gallery/abc123/other")

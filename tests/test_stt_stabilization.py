"""Stabilization tests (#6319 follow-up): lazy local lifecycle, keep_model_loaded,
no eager loads, concurrent-load protection, Gemini native adapter.

Conventions follow tests/test_stt_phase1.py: direct service unit tests with
fakes, no TestClient, no network.
"""

import sys
import threading
import types
from types import SimpleNamespace

import pytest

from services.stt.stt_service import STTService


# ── fakes ──

def _install_fake_whisper(monkeypatch, seen, delay=None):
    mod = types.ModuleType("faster_whisper")

    class FakeModel:
        def __init__(self, size, device="cpu", compute_type="int8"):
            if delay:
                import time
                time.sleep(delay)
            seen.append(size)

        def transcribe(self, *args, **kwargs):
            class _Seg:
                text = "hi"
            class _Info:
                language = "en"
                language_probability = 0.9
            return iter([_Seg()]), _Info()

    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def _local_settings(model="base", keep=False, enabled=True):
    return {
        "stt_enabled": enabled,
        "stt_provider": "local",
        "stt_model": model,
        "stt_language": "",
        "keep_model_loaded": keep,
    }


def _install_fake_db(monkeypatch, base_url, api_key="k"):
    db_mod = types.ModuleType("src.database")

    class FakeQuery:
        def filter(self, *a, **k):
            return self

        def first(self):
            return SimpleNamespace(base_url=base_url, api_key=api_key)

    class FakeSession:
        def query(self, *a, **k):
            return FakeQuery()

        def close(self):
            pass

    db_mod.SessionLocal = lambda: FakeSession()
    db_mod.ModelEndpoint = SimpleNamespace(id=None)
    monkeypatch.setitem(sys.modules, "src.database", db_mod)


# ── lazy lifecycle ──

def test_available_and_stats_do_not_load_model(monkeypatch):
    _install_fake_whisper(monkeypatch, [])
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings())
    monkeypatch.setattr(STTService, "_is_faster_whisper_importable", staticmethod(lambda: True))
    calls = {"n": 0}
    orig = service._get_whisper
    monkeypatch.setattr(service, "_get_whisper", lambda: (calls.__setitem__("n", calls["n"] + 1), orig())[1])
    assert service.available is True
    stats = service.get_stats()
    assert stats["model_loaded"] is False
    assert calls["n"] == 0


def test_available_local_false_when_package_missing(monkeypatch):
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings())
    monkeypatch.setattr(STTService, "_is_faster_whisper_importable", staticmethod(lambda: False))
    assert service.available is False


def test_default_unloads_after_transcription(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=False))
    text, lang = service.transcribe_with_info(b"audio", "a.wav")
    assert text == "hi"
    assert seen == ["base"]
    assert service.is_model_loaded() is False


def test_keep_loaded_true_retains_model(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=True))
    service.transcribe_with_info(b"audio", "a.wav")
    service.transcribe_with_info(b"audio", "a.wav")
    assert seen == ["base"]  # loaded once, reused
    assert service.is_model_loaded() is True


def test_model_selection_invalidates_without_eager_load(monkeypatch):
    """Saving a new stt_model drops the cache but loads nothing.

    Goes through the real set_settings route with a stubbed STT service
    module: invalidation is observable, eager loads are impossible (the
    stub records every load attempt).
    """
    import asyncio
    import routes.auth_routes as auth_routes
    import src.settings as settings_mod

    store = {**settings_mod.DEFAULT_SETTINGS, "stt_model": "base"}

    class AuthManager:
        def get_username_for_token(self, token):
            return "admin" if token == "admin-session" else None

        def is_admin(self, username):
            return username == "admin"

    class Req:
        def __init__(self, body):
            self.cookies = {auth_routes.SESSION_COOKIE: "admin-session"}
            self._body = body

        async def json(self):
            return self._body

    events = {"invalidated": 0, "loads": 0}

    class FakeService:
        def invalidate_model(self):
            events["invalidated"] += 1

        def _get_whisper(self):  # pragma: no cover
            events["loads"] += 1
            return None

    fake_mod = types.ModuleType("services.stt")
    fake_mod.get_stt_service = lambda: FakeService()
    monkeypatch.setitem(sys.modules, "services.stt", fake_mod)
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(store))

    def save_settings(updated):
        store.clear()
        store.update(updated)

    monkeypatch.setattr(auth_routes, "_save_settings", save_settings)
    router = auth_routes.setup_auth_routes(AuthManager())
    set_settings = next(
        r.endpoint for r in router.routes
        if r.path == "/api/auth/settings" and "POST" in r.methods
    )
    asyncio.run(set_settings(Req({"stt_model": "small"})))
    assert store["stt_model"] == "small"
    assert events == {"invalidated": 1, "loads": 0}
    # Same value again: no spurious invalidation.
    asyncio.run(set_settings(Req({"stt_model": "small"})))
    assert events == {"invalidated": 1, "loads": 0}


def test_replacement_loads_on_next_request(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    current = {"model": "base"}
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(
        model=current["model"], keep=True))
    service.transcribe_with_info(b"audio", "a.wav")
    assert seen == ["base"]
    # External invalidation (as set_settings performs on selection change).
    service.invalidate_model()
    assert service.is_model_loaded() is False
    current["model"] = "small"
    service.transcribe_with_info(b"audio", "a.wav")
    assert seen == ["base", "small"]


def test_concurrent_transcriptions_load_once(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen, delay=0.2)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=True))
    errors = []

    def work():
        try:
            service.transcribe_with_info(b"audio", "a.wav")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert seen == ["base"]


def test_keep_model_loaded_default_false():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["keep_model_loaded"] is False


def test_keep_model_loaded_settings_roundtrip(monkeypatch):
    import asyncio
    import routes.auth_routes as auth_routes
    import src.settings as settings_mod
    from fastapi import HTTPException

    store = dict(settings_mod.DEFAULT_SETTINGS)

    class AuthManager:
        def get_username_for_token(self, token):
            return "admin" if token == "admin-session" else None

        def is_admin(self, username):
            return username == "admin"

    class Req:
        def __init__(self, body):
            self.cookies = {auth_routes.SESSION_COOKIE: "admin-session"}
            self._body = body

        async def json(self):
            return self._body

    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(store))

    def save_settings(updated):
        store.clear()
        store.update(updated)

    monkeypatch.setattr(auth_routes, "_save_settings", save_settings)
    router = auth_routes.setup_auth_routes(AuthManager())
    set_settings = next(
        r.endpoint for r in router.routes
        if r.path == "/api/auth/settings" and "POST" in r.methods
    )
    assert store["keep_model_loaded"] is False  # default
    asyncio.run(set_settings(Req({"keep_model_loaded": True})))
    assert store["keep_model_loaded"] is True
    with pytest.raises(HTTPException):
        asyncio.run(set_settings(Req({"keep_model_loaded": "yes"})))


# ── Gemini adapter ──

def _gemini_settings(model=""):
    return {
        "stt_enabled": True,
        "stt_provider": "endpoint:gem1",
        "stt_model": model,
        "stt_language": "",
        "keep_model_loaded": False,
    }


def _fake_httpx(monkeypatch, status=200, payload=None, raw=None, capture=None):
    import httpx as real_httpx

    class FakeResp:
        status_code = status

        def __init__(self):
            self.text = raw if raw is not None else ""
            self.headers = {}

        def json(self):
            if payload is None:
                raise ValueError("no json")
            return payload

    def fake_post(url, headers=None, json=None, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["json"] = json
        return FakeResp()

    monkeypatch.setattr(real_httpx, "post", fake_post)


def _fake_gemini_httpx(monkeypatch, transcript_payload=None, file_uri="https://generativelanguage.googleapis.com/v1beta/files/abc123", capture=None, generate_status=200, upload_status=200):
    """Route Gemini's 3-step flow: upload-start -> finalize -> interactions."""
    import httpx as real_httpx

    session_url = "https://generativelanguage.googleapis.com/upload/session-1"

    class FakeResp:
        def __init__(self, status_code, payload=None, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}
            self.text = "" if payload is not None else "err"

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, **kwargs):
        if url.endswith("/upload/v1beta/files"):
            if capture is not None:
                capture["start_url"] = url
                capture["start_headers"] = headers
                capture["start_json"] = json
            if upload_status != 200:
                return FakeResp(upload_status)
            return FakeResp(200, {}, {"x-goog-upload-url": session_url})
        if url == session_url:
            if capture is not None:
                capture["upload_content"] = content
            if upload_status != 200:
                return FakeResp(upload_status)
            return FakeResp(200, {"file": {"uri": file_uri, "mimeType": "audio/mp4"}})
        if capture is not None:
            capture["url"] = url
            capture["headers"] = headers
            capture["json"] = json
        return FakeResp(generate_status, transcript_payload)

    monkeypatch.setattr(real_httpx, "post", fake_post)


def _gemini_ok_payload(text="ahoj svet"):
    # Completed Interactions API shape: transcript in output_text.
    return {"id": "int-1", "status": "completed", "model": "gemini-3.5-transcribe",
            "output_text": text}


def _gemini_pending_payload(interaction_id="int-9"):
    # Runtime-observed initial object: 200, no outputs/output_text yet.
    return {"id": interaction_id, "object": "interaction",
            "model": "gemini-3.5-transcribe", "status": "queued",
            "created": 1, "updated": 1, "service_tier": "standard", "usage": {}}


def _fake_gemini_poll(monkeypatch, script, capture=None):
    """Fake httpx.get for interaction polling; pops one response per call."""
    import httpx as real_httpx

    calls = {"n": 0}

    class PollResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.headers = {}
            self.text = "" if payload is not None else "err"

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    def fake_get(url, headers=None, **kwargs):
        calls["n"] += 1
        if capture is not None:
            capture.setdefault("poll_urls", []).append(url)
            capture["poll_headers"] = headers
        item = script[min(calls["n"] - 1, len(script) - 1)]
        return PollResp(*item)

    monkeypatch.setattr(real_httpx, "get", fake_get)
    monkeypatch.setattr(STTService, "GEMINI_POLL_INTERVAL_SECONDS", 0)
    return calls


def _gemini_candidates_payload(text="ahoj svet"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_request_construction(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta/openai")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings(model=""))
    text, lang = service.transcribe_with_info(b"\x01\x02", "talk.m4a")
    assert text == "ahoj svet"
    # Files API first: resumable session start on the upload host.
    assert capture["start_url"] == "https://generativelanguage.googleapis.com/upload/v1beta/files"
    assert capture["start_headers"]["X-Goog-Upload-Protocol"] == "resumable"
    assert capture["start_headers"]["X-Goog-Upload-Header-Content-Type"] == "audio/m4a"
    assert capture["upload_content"] == b"\x01\x02"
    # Native root: /openai suffix stripped, interactions path used.
    assert capture["url"] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert capture["headers"]["x-goog-api-key"] == "k"
    body = capture["json"]
    # Default model kept (user-selectable, not hardcoded elsewhere).
    assert body["model"] == "gemini-3.5-transcribe"
    # Dedicated transcribe model: documented audio-only input.
    assert body["input"] == [{
        "type": "audio",
        "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123",
        "mime_type": "audio/m4a",
    }]
    # No API key leakage into URL or body.
    assert "k" not in capture["url"]
    assert "k" not in str(body)


def test_gemini_uses_configured_model_and_language(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        **_gemini_settings(model="gemini-2.5-flash"), "stt_language": "sk",
    })
    text, lang = service.transcribe_with_info(b"a", "a.mp3")
    assert text == "ahoj svet"
    assert lang == "sk"
    assert capture["url"].endswith("/interactions")
    body = capture["json"]
    assert body["model"] == "gemini-2.5-flash"
    # Generic models keep the verbatim instruction alongside the audio input.
    assert len(body["input"]) == 2
    assert body["input"][0]["type"] == "text"
    assert "sk" in body["input"][0]["text"]
    assert body["input"][1] == {
        "type": "audio",
        "uri": "https://generativelanguage.googleapis.com/v1beta/files/abc123",
        "mime_type": "audio/mp3",
    }
    assert "k" not in capture["url"]
    assert capture["headers"]["x-goog-api-key"] == "k"


def test_gemini_upload_failure_aborts(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), upload_status=401)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert "Files API" in caplog.text


def test_gemini_pending_interaction_polls_to_completion(monkeypatch, caplog):
    import logging
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload(), capture=capture)
    poll_calls = _fake_gemini_poll(monkeypatch, [
        (200, {"id": "int-9", "status": "in_progress"}),
        (200, {"id": "int-9", "status": "completed",
               "steps": [{"type": "model_output", "content": [{"type": "text", "text": "prvý "}, {"type": "text", "text": "prepis"}]}]}),
    ], capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        out = service.transcribe_with_info(b"a", "a.wav")
    assert out == ("prvý prepis", "")
    assert poll_calls["n"] == 2
    assert capture["poll_urls"][0].endswith("/interactions/int-9")
    assert capture["poll_headers"]["x-goog-api-key"] == "k"
    assert "k" not in capture["poll_urls"][0]
    assert "pending" in caplog.text or "polling" in caplog.text


def test_gemini_failed_interaction_returns_none(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload())
    _fake_gemini_poll(monkeypatch, [(200, {"id": "int-9", "status": "failed"})])
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert "failed" in caplog.text


def test_gemini_poll_timeout_returns_none(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload())
    poll_calls = _fake_gemini_poll(monkeypatch, [(200, {"id": "int-9", "status": "in_progress"})])
    monkeypatch.setattr(STTService, "GEMINI_POLL_MAX_ATTEMPTS", 3)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert poll_calls["n"] == 3
    assert "timed out" in caplog.text


def test_gemini_requires_action_fails_fast(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload())
    poll_calls = _fake_gemini_poll(monkeypatch, [(200, {"id": "int-9", "status": "requires_action"})])
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert poll_calls["n"] == 1
    assert "requires action" in caplog.text


def test_gemini_poll_429_backs_off_and_continues(monkeypatch):
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload())
    poll_calls = _fake_gemini_poll(monkeypatch, [
        (429, None),
        (200, {"id": "int-9", "status": "completed", "output_text": "po pauze"}),
    ])
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    assert service.transcribe_with_info(b"a", "a.wav") == ("po pauze", "")
    assert poll_calls["n"] == 2


def test_gemini_steps_fallback_when_no_output_text(monkeypatch):
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_pending_payload())
    _fake_gemini_poll(monkeypatch, [(200, {"id": "int-9", "status": "completed",
                                           "steps": [{"type": "model_output", "content": [{"type": "text", "text": "záložný kľúč"}]}]})])
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    assert service.transcribe_with_info(b"a", "a.wav") == ("záložný kľúč", "")


def test_gemini_sends_language_codes_when_configured(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        **_gemini_settings(model=""), "stt_language": "sk",
    })
    assert service.transcribe_with_info(b"a", "a.wav")[0] == "ahoj svet"
    assert capture["json"]["generation_config"] == {
        "transcription_config": {"language_codes": ["sk"]}
    }


def test_gemini_omits_language_codes_on_autodetect(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings(model=""))
    assert service.transcribe_with_info(b"a", "a.wav")[0] == "ahoj svet"
    assert "generation_config" not in capture["json"]


def _realistic_gemini_response(text="Ahoj, toto je skúšobná nahrávka."):
    # Shape mirrors a real generateContent 200 response: candidates with
    # role/parts plus sibling top-level keys (modelVersion, responseId,
    # usageMetadata) that must not confuse extraction.
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "modelVersion": "gemini-3.5-transcribe",
        "responseId": "resp-123",
        "usageMetadata": {
            "promptTokenCount": 1900,
            "candidatesTokenCount": 12,
            "totalTokenCount": 1912,
        },
    }


def _realistic_gemini_interactions_response(text="Ahoj, toto je skúšobná nahrávka."):
    # Shape mirrors a real Interactions API 200 response with steps/content:
    # candidates with role/parts plus sibling top-level keys (modelVersion, responseId,
    # usageMetadata) that must not confuse extraction.
    return {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": text}
                ],
                "finishReason": "STOP"
            }
        ],
        "modelVersion": "gemini-3.5-transcribe",
        "responseId": "resp-123",
        "usageMetadata": {
            "promptTokenCount": 1900,
            "candidatesTokenCount": 12,
            "totalTokenCount": 1912,
        },
    }


def test_gemini_interactions_steps_content_extraction():
    # Test the new steps/content/text extraction path
    text, diagnosis = STTService._extract_interaction_text(
        _realistic_gemini_interactions_response())
    assert text == "Ahoj, toto je skúšobná nahrávka."
    assert diagnosis == ""


def test_gemini_interactions_steps_multiple_parts():
    payload = {
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": "First segment "},
                    {"type": "text", "text": "second segment."},
                ]
            },
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": " Third part."},
                ]
            },
        ],
        "modelVersion": "gemini-3.5-transcribe",
        "responseId": "resp-123",
        "usageMetadata": {},
    }
    text, diagnosis = STTService._extract_interaction_text(payload)
    assert text == "First segment second segment. Third part."
    assert diagnosis == ""


def test_gemini_interactions_ignores_non_text_content():
    payload = {
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "audio", "uri": "gs://bucket/audio.wav"},
                    {"type": "text", "text": "World"},
                ]
            }
        ],
    }
    text, diagnosis = STTService._extract_interaction_text(payload)
    assert text == "HelloWorld"
    assert diagnosis == ""


def test_gemini_empty_response_without_id_does_not_poll(monkeypatch):
    import httpx as real_httpx
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload={"status": "completed"})
    polled = {"n": 0}

    orig_get = real_httpx.get

    def counting_get(*a, **k):
        polled["n"] += 1
        return orig_get(*a, **k)

    monkeypatch.setattr(real_httpx, "get", counting_get)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    assert service.transcribe_with_info(b"a", "a.wav") is None
    assert polled["n"] == 0


def test_gemini_openai_shim_path_not_used(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta/openai/")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_gemini_ok_payload(), capture=capture)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    service.transcribe_with_info(b"a", "a.wav")
    assert "/audio/transcriptions" not in capture["url"]
    assert ":generateContent" not in capture["url"]
    assert capture["url"].endswith("/interactions")


def test_gemini_interaction_text_joins_trailing_blocks_only():
    extract = STTService._extract_interaction_text
    # Trailing consecutive text blocks join (SDK output_text semantics).
    text, _ = extract({"outputs": [{"type": "text", "text": "a "}, {"type": "text", "text": "b"}]})
    assert text == "a b"
    # Earlier text separated by non-text content is excluded.
    text, _ = extract({"outputs": [
        {"type": "text", "text": "stará "},
        {"type": "audio", "uri": "x"},
        {"type": "text", "text": "nová"},
    ]})
    assert text == "nová"


def test_gemini_empty_output_text_is_failure(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload={"output_text": ""})
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert "empty response" in caplog.text


def test_gemini_candidates_fallback_when_no_output_text(monkeypatch):
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    # Use a variable to avoid complex nesting in the test call
    payload = {
        "status": "completed",
        "candidates": [{
            "content": {
                "parts": [{"text": "záloha funguje"}]
            }
        }]
    }
    _fake_gemini_httpx(monkeypatch, transcript_payload=payload)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    assert service.transcribe_with_info(b"a", "a.wav") == ("záloha funguje", "")


def test_gemini_error_handling(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    # 404 -> None with actionable log
    _fake_gemini_httpx(monkeypatch, transcript_payload=None, generate_status=404)
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        assert service.transcribe_with_info(b"a", "a.wav") is None
    assert "404" in caplog.text
    # non-JSON 200 -> None
    import httpx as real_httpx

    class NonJsonResp:
        status_code = 200
        text = "not json"
        headers = {}

        def json(self):
            raise ValueError("no json")

    def fake_upload_then_nonjson(url, headers=None, json=None, content=None, **kwargs):
        if url.endswith("/upload/v1beta/files"):
            class StartResp:
                status_code = 200
                text = ""
                headers = {"x-goog-upload-url": "https://example.invalid/session"}

                def json(self):
                    return {}

            return StartResp()
        if url == "https://example.invalid/session":
            class FinResp:
                status_code = 200
                text = ""
                headers = {}

                def json(self):
                    return {"file": {"uri": "https://example.invalid/files/1"}}
            return FinResp()
        return NonJsonResp()

    monkeypatch.setattr(real_httpx, "post", fake_upload_then_nonjson)
    assert service.transcribe_with_info(b"a", "a.wav") is None
    # empty candidates -> None
    _fake_gemini_httpx(monkeypatch, transcript_payload={"candidates": []})
    assert service.transcribe_with_info(b"a", "a.wav") is None
    # transport exception -> None
    def boom(*a, **k):
        raise real_httpx.ConnectError("down")

    monkeypatch.setattr(real_httpx, "post", boom)
    assert service.transcribe_with_info(b"a", "a.wav") is None


def test_gemini_error_messages():
    m = STTService._gemini_error_message
    assert "API key" in m(401, "", "x")
    assert "gemini-3.5-transcribe" in m(404, "", "gemini-3.5-transcribe")
    assert "429" in m(429, "", "x")
    assert "400" in m(400, "", "x")


def _realistic_gemini_response(text="Ahoj, toto je skúšobná nahrávka."):
    # Shape mirrors a real generateContent 200 response: candidates with
    # role/parts plus sibling top-level keys (modelVersion, responseId,
    # usageMetadata) that must not confuse extraction.
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": "STOP",
                "index": 0,
            }
        ],
        "modelVersion": "gemini-3.5-transcribe",
        "responseId": "resp-123",
        "usageMetadata": {
            "promptTokenCount": 1900,
            "candidatesTokenCount": 12,
            "totalTokenCount": 1912,
        },
    }


def test_gemini_extract_realistic_response():
    text, diagnosis = STTService._extract_gemini_transcript(
        _realistic_gemini_response())
    assert text == "Ahoj, toto je skúšobná nahrávka."
    assert diagnosis == ""


def test_gemini_extract_joins_split_parts_and_candidates():
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": "Ahoj, "}, {"text": "svet."}]},
             "finishReason": "STOP"},
            {"content": {"parts": [{"text": " Druhá veta."}]},
             "finishReason": "STOP"},
        ],
        "modelVersion": "gemini-3.5-transcribe",
    }
    text, diagnosis = STTService._extract_gemini_transcript(payload)
    assert text == "Ahoj, svet. Druhá veta."
    assert diagnosis == ""


def test_gemini_extract_skips_thought_parts():
    payload = {
        "candidates": [
            {"content": {"parts": [
                {"text": "internal reasoning", "thought": True},
                {"text": "prepis reči"},
            ]},
             "finishReason": "STOP"},
        ],
    }
    text, diagnosis = STTService._extract_gemini_transcript(payload)
    assert text == "prepis reči"
    assert diagnosis == ""


def test_gemini_extract_thought_only_reports_diagnosis():
    payload = {
        "candidates": [
            {"content": {"parts": [{"text": "reasoning", "thought": True}]},
             "finishReason": "STOP"},
        ],
        "modelVersion": "gemini-3.5-transcribe",
    }
    text, diagnosis = STTService._extract_gemini_transcript(payload)
    assert text == ""
    assert "thought-only" in diagnosis
    assert "STOP" in diagnosis


def test_gemini_extract_blocked_response_reports_reason():
    payload = {
        "promptFeedback": {"blockReason": "SAFETY"},
        "modelVersion": "gemini-3.5-transcribe",
    }
    text, diagnosis = STTService._extract_gemini_transcript(payload)
    assert text == ""
    assert "SAFETY" in diagnosis


def test_gemini_extract_tolerates_malformed_shapes():
    assert STTService._extract_gemini_transcript({}) == ("", (
        "no candidates/parts (top-level keys: [])"))
    assert STTService._extract_gemini_transcript(None)[0] == ""
    assert STTService._extract_gemini_transcript({"candidates": "nope"})[0] == ""
    # Bare parts list as content stays readable.
    text, _ = STTService._extract_gemini_transcript(
        {"candidates": [{"content": [{"text": "priamy text"}]}]})
    assert text == "priamy text"


def test_gemini_full_path_with_realistic_response(monkeypatch, caplog):
    import logging
    _install_fake_db(monkeypatch, "https://generativelanguage.googleapis.com/v1beta")
    _fake_gemini_httpx(monkeypatch, transcript_payload=_realistic_gemini_interactions_response())
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings())
    with caplog.at_level(logging.ERROR, logger="services.stt.stt_service"):
        out = service.transcribe_with_info(b"a", "a.wav")
    assert out == ("Ahoj, toto je skúšobná nahrávka.", "")
    assert "empty response" not in caplog.text


def test_gemini_response_structure_is_sanitized():
    sketch = STTService._gemini_response_structure(_realistic_gemini_interactions_response())
    assert "steps=1" in sketch
    assert "finish=STOP" in sketch
    assert "text[32]" in sketch
    # Structure only: no transcript content, no keys, no audio.
    assert "skúšobná" not in sketch
    assert STTService._gemini_response_structure(None) == "non-dict response (NoneType)"


def test_google_base_detection_and_root():
    assert STTService.is_google_base_url("https://generativelanguage.googleapis.com/v1beta/openai")
    assert STTService.is_google_base_url("https://generativelanguage.googleapis.com/v1beta")
    assert not STTService.is_google_base_url("https://api.openai.com/v1")
    assert not STTService.is_google_base_url(None)
    s = STTService()
    assert s._gemini_native_root("https://generativelanguage.googleapis.com/v1beta/openai") == \
        "https://generativelanguage.googleapis.com/v1beta"
    assert s._gemini_native_root("https://generativelanguage.googleapis.com/v1beta") == \
        "https://generativelanguage.googleapis.com/v1beta"


def test_non_google_endpoint_still_openai_compatible(monkeypatch):
    capture = {}
    _install_fake_db(monkeypatch, "https://api.openai.com/v1")
    import httpx as real_httpx

    class FakeResp:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "hello"}

    def fake_post(url, headers=None, files=None, data=None, **kwargs):
        capture["url"] = url
        return FakeResp()

    monkeypatch.setattr(real_httpx, "post", fake_post)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _gemini_settings(model="whisper-1"))
    text, _ = service.transcribe_with_info(b"a", "a.wav")
    assert text == "hello"
    assert capture["url"] == "https://api.openai.com/v1/audio/transcriptions"


# ── local observability logging ──

def _install_progress_whisper(monkeypatch, seen, n_segments=60):
    """Fake model yielding segments with real start/end/text attributes."""
    mod = types.ModuleType("faster_whisper")

    class FakeSeg:
        def __init__(self, i):
            self.start = float(i * 2)
            self.end = float(i * 2 + 2)
            self.text = f" word{i}"

    class FakeModel:
        def __init__(self, size, device="cpu", compute_type="int8"):
            seen.append((size, device, compute_type))

        def transcribe(self, *args, **kwargs):
            class _Info:
                language = "sk"
                language_probability = 1.0
                duration = 120.0
            return (FakeSeg(i) for i in range(n_segments)), _Info()

    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_model_load_logs_start_and_done(monkeypatch, caplog):
    import logging
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings())
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        assert service._get_whisper() is not None
    assert "Loading faster-whisper model 'base'" in caplog.text
    assert "device=cpu" in caplog.text
    assert "loaded on cpu in" in caplog.text


def test_unload_logs_release_only_when_loaded(monkeypatch, caplog):
    import logging
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=False))
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        service.transcribe_with_info(b"audio", "a.wav")
    assert "Released faster-whisper model 'base' from memory" in caplog.text
    # Second call with nothing loaded: no spurious release log.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        service._maybe_unload()
    assert "Released faster-whisper" not in caplog.text


def test_keep_loaded_true_skips_release_log(monkeypatch, caplog):
    import logging
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=True))
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        service.transcribe_with_info(b"audio", "a.wav")
    assert "Released faster-whisper" not in caplog.text
    assert service.is_model_loaded() is True


def test_transcription_logs_start_progress_and_summary(monkeypatch, caplog):
    import logging
    seen = []
    _install_progress_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=True))
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        text, lang = service.transcribe_with_info(b"x" * 100, "talk.m4a")
    assert lang == "sk"
    assert "word0" in text and "word59" in text
    # Start line carries bytes/container/model/language.
    assert "Local STT started: 100 bytes" in caplog.text
    assert ".m4a" in caplog.text
    # Real progress counting (segment count + audio timestamp), no percentages.
    assert "Local STT progress: 25 segments" in caplog.text
    assert "Local STT progress: 50 segments" in caplog.text
    assert "%" not in [line for line in caplog.text.splitlines() if "progress" in line][0]
    # Completion summary.
    assert "60 segments" in caplog.text
    assert "audio 120.0s in" in caplog.text
    assert seen == [("base", "cpu", "int8")]


def test_progress_tolerates_segments_without_timestamps(monkeypatch, caplog):
    import logging
    seen = []
    _install_fake_whisper(monkeypatch, seen)  # segments expose only .text
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: _local_settings(keep=True))
    with caplog.at_level(logging.INFO, logger="services.stt.stt_service"):
        text, _ = service.transcribe_with_info(b"audio", "a.wav")
    assert text == "hi"

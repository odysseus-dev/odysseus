"""Native xAI TTS support.

xAI does not implement the OpenAI-compatible /audio/speech contract. Its
native endpoint is ``POST /v1/tts`` with ``voice_id`` and ``output_format`` and
returns raw audio bytes. These tests pin URL selection, payload shape, endpoint
detection, speed clamping, and redacted errors without calling real APIs.
"""
import sys
import types

import httpx
import pytest

from services.tts import tts_service as tts_module
from services.tts.tts_service import (
    TTSError,
    TTSService,
    _is_xai_endpoint,
    _xai_tts_url,
)


def _settings(**overrides):
    base = {
        "tts_enabled": True,
        "tts_provider": "custom",
        "tts_model": "tts-1",
        "tts_voice": "eve",
        "tts_speed": "1",
        "tts_endpoint_url": "https://api.x.ai/v1",
        "tts_api_key": "",
    }
    base.update(overrides)
    return base


def _response(url, *, status=200, content=b"ID3xai-audio", text=None):
    return httpx.Response(
        status,
        content=content if text is None else text.encode(),
        request=httpx.Request("POST", url),
    )


def test_xai_tts_url_resolution():
    assert _xai_tts_url("https://api.x.ai") == "https://api.x.ai/v1/tts"
    assert _xai_tts_url("https://api.x.ai/") == "https://api.x.ai/v1/tts"
    assert _xai_tts_url("https://api.x.ai/v1") == "https://api.x.ai/v1/tts"
    assert _xai_tts_url("https://api.x.ai/v1/") == "https://api.x.ai/v1/tts"
    assert _xai_tts_url("https://api.x.ai/v1/tts") == "https://api.x.ai/v1/tts"
    assert _xai_tts_url("https://api.x.ai/v1/audio/speech") == "https://api.x.ai/v1/tts"


def test_xai_endpoint_detection_uses_host_and_hints():
    assert _is_xai_endpoint("https://api.x.ai/v1") is True
    assert _is_xai_endpoint("https://api.openai.com/v1") is False
    assert _is_xai_endpoint("https://proxy.example.com/v1", "Grok API") is True
    assert _is_xai_endpoint("https://proxy.example.com/v1", "grok-4") is True
    assert _is_xai_endpoint("https://proxy.example.com/v1", "tts-1") is False


def test_custom_xai_url_uses_native_tts_contract(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(
        tts_api_key="xai-secret", tts_voice="eve", tts_speed="1.25",
        tts_endpoint_url="https://api.x.ai/v1/audio/speech",
    ))
    calls = {}

    def fake_post(url, json, headers, timeout):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return _response(url)

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    audio = service.synthesize("hello", use_cache=False)

    assert audio == b"ID3xai-audio"
    assert calls["url"] == "https://api.x.ai/v1/tts"
    assert calls["headers"]["Authorization"] == "Bearer xai-secret"
    assert calls["json"] == {
        "text": "hello",
        "voice_id": "eve",
        "language": "en",
        "output_format": {"codec": "mp3"},
        "speed": 1.25,
    }
    assert "voice" not in calls["json"]
    assert "model" not in calls["json"]
    assert calls["timeout"] == 60


def test_xai_speed_is_clamped_to_native_range(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(tts_speed="2"))

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(json)
        return _response(url)

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    assert service.synthesize("hello", use_cache=False) == b"ID3xai-audio"
    assert captured["speed"] == 1.5


def test_configured_xai_endpoint_uses_native_tts(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "tts_enabled": True,
        "tts_provider": "endpoint:xai",
        "tts_model": "tts-1",
        "tts_voice": "ara",
        "tts_speed": "1",
    })

    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return types.SimpleNamespace(
                base_url="https://api.x.ai/v1", api_key="xai-secret", name="xAI"
            )

    class FakeDB:
        def query(self, *args):
            return FakeQuery()

        def close(self):
            pass

    fake_database = types.ModuleType("src.database")
    fake_database.SessionLocal = lambda: FakeDB()
    fake_database.ModelEndpoint = types.SimpleNamespace(id=None)
    monkeypatch.setitem(sys.modules, "src.database", fake_database)

    calls = {}

    def fake_post(url, json, headers, timeout):
        calls.update(url=url, json=json, headers=headers)
        return _response(url)

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    assert service.synthesize("hello", use_cache=False) == b"ID3xai-audio"
    assert calls["url"] == "https://api.x.ai/v1/tts"
    assert calls["json"]["voice_id"] == "ara"
    assert calls["headers"]["Authorization"] == "Bearer xai-secret"


def test_xai_http_error_is_actionable_and_redacts_key(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(
        tts_api_key="xai-secret",
    ))

    def fake_post(url, json, headers, timeout):
        return _response(
            url,
            status=404,
            text='{"error":"unknown voice for xai-secret"}',
        )

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    with pytest.raises(TTSError) as excinfo:
        service.synthesize("hello", use_cache=False)

    message = str(excinfo.value)
    assert "HTTP 404" in message
    assert "/v1/tts" in message
    assert "xai-secret" not in message

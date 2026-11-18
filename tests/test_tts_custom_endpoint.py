"""Custom OpenAI-compatible TTS endpoint support.

The custom provider lets an admin point TTS at any server implementing
``POST /audio/speech`` without first creating a ModelEndpoint. These tests pin
the URL resolution, auth forwarding, payload shape, and actionable errors.
"""
import httpx
import pytest

from services.tts import tts_service as tts_module
from services.tts.tts_service import TTSError, TTSService, _speech_url


def _settings(**overrides):
    base = {
        "tts_enabled": True,
        "tts_provider": "custom",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
        "tts_endpoint_url": "https://api.example.com/v1",
        "tts_api_key": "",
    }
    base.update(overrides)
    return base


def test_speech_url_accepts_base_or_full_endpoint():
    assert _speech_url("https://api.example.com/v1") == "https://api.example.com/v1/audio/speech"
    assert _speech_url("https://api.example.com/v1/") == "https://api.example.com/v1/audio/speech"
    assert _speech_url("https://api.example.com/v1/audio/speech") == "https://api.example.com/v1/audio/speech"
    assert _speech_url("https://api.example.com/v1/audio/speech/") == "https://api.example.com/v1/audio/speech"


def test_custom_provider_posts_openai_speech(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(
        tts_api_key="sk-secret", tts_model="gpt-4o-mini-tts",
        tts_voice="nova", tts_speed="1.25",
    ))
    calls = {}

    def fake_post(url, json, headers, timeout):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return httpx.Response(200, content=b"ID3fake-mp3", request=httpx.Request("POST", url))

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    audio = service.synthesize("hello", use_cache=False)

    assert audio == b"ID3fake-mp3"
    assert calls["url"] == "https://api.example.com/v1/audio/speech"
    assert calls["headers"]["Authorization"] == "Bearer sk-secret"
    assert calls["json"] == {
        "model": "gpt-4o-mini-tts",
        "input": "hello",
        "voice": "nova",
        "response_format": "mp3",
        "speed": 1.25,
    }
    assert calls["timeout"] == 60


def test_custom_provider_without_key_omits_authorization(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())

    def fake_post(url, json, headers, timeout):
        assert "Authorization" not in headers
        return httpx.Response(200, content=b"ID3ok", request=httpx.Request("POST", url))

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)
    assert service.synthesize("hello", use_cache=False) == b"ID3ok"


def test_custom_provider_missing_url_is_actionable(tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    service._load_settings = lambda: _settings(tts_endpoint_url="")

    with pytest.raises(TTSError, match="No TTS endpoint URL"):
        service.synthesize("hello", use_cache=False)


def test_custom_provider_http_error_is_actionable_and_redacts_key(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(tts_api_key="sk-secret"))

    def fake_post(url, json, headers, timeout):
        return httpx.Response(
            404,
            text='{"error":"no /audio/speech for sk-secret"}',
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(tts_module.httpx, "post", fake_post)

    with pytest.raises(TTSError) as excinfo:
        service.synthesize("hello", use_cache=False)
    message = str(excinfo.value)
    assert "HTTP 404" in message
    assert "/audio/speech" in message
    assert "sk-secret" not in message

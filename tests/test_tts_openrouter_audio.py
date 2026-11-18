"""OpenRouter GPT Audio TTS adapter.

OpenRouter's GPT Audio models are chat models, not OpenAI ``/audio/speech``
providers, and streaming audio output only supports ``pcm16``. These tests pin
host/model detection, URL building, the streaming audio request payload, SSE
PCM16 chunk accumulation/final message parsing, WAV wrapping/sample rate,
route/cache MIME behavior, and actionable errors without making real network
calls.
"""
import base64
import contextlib
import io
import json
import sys
import types
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.tts_routes import setup_tts_routes
from services.tts import tts_service as tts_module
from services.tts.tts_service import (
    TTSError,
    TTSService,
    _chat_completions_url,
    _is_openrouter_audio_endpoint,
)


def _settings(**overrides):
    base = {
        "tts_enabled": True,
        "tts_provider": "custom",
        "tts_model": "openai/gpt-audio",
        "tts_voice": "alloy",
        "tts_speed": "1",
        "tts_endpoint_url": "https://openrouter.ai/api/v1",
        "tts_api_key": "",
    }
    base.update(overrides)
    return base


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _delta_audio(data):
    return {"choices": [{"delta": {"audio": {"data": data}}}]}


def _delta_text(text):
    return {"choices": [{"delta": {"content": text}}]}


def _final_audio(data):
    return {"choices": [{"message": {"audio": {"data": data}}}]}


def _wav_frames(data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        return (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.readframes(wav.getnframes()),
        )


def _sse_response(url, events):
    lines = []
    for event in events:
        if isinstance(event, str):
            data = event
        else:
            data = json.dumps(event)
        lines.append(f"data: {data}\n\n")
    return httpx.Response(
        200,
        content="".join(lines).encode("utf-8"),
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", url),
    )


def _stream_mock(calls, events, status=200, text=None):
    @contextlib.contextmanager
    def fake_stream(method, url, json=None, headers=None, timeout=None):
        calls.update(method=method, url=url, json=json, headers=headers, timeout=timeout)
        if status != 200:
            yield httpx.Response(
                status,
                text=text or "",
                request=httpx.Request(method, url),
            )
        else:
            yield _sse_response(url, events)

    return fake_stream


def test_openrouter_audio_detection_and_url_resolution():
    assert _is_openrouter_audio_endpoint("https://openrouter.ai/api/v1", "openai/gpt-audio")
    assert _is_openrouter_audio_endpoint("https://openrouter.ai/api/v1", "openai/gpt-audio-mini")
    assert not _is_openrouter_audio_endpoint("https://api.openai.com/v1", "gpt-audio")
    assert not _is_openrouter_audio_endpoint("https://openrouter.ai/api/v1", "google/lyria-002")
    assert not _is_openrouter_audio_endpoint("https://openrouter.ai/api/v1", "deepseek/deepseek-chat")

    assert _chat_completions_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1/chat/completions"
    assert _chat_completions_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1/chat/completions"
    assert _chat_completions_url("https://openrouter.ai/api/v1/chat/completions") == "https://openrouter.ai/api/v1/chat/completions"
    assert _chat_completions_url("https://openrouter.ai/api/v1/audio/speech") == "https://openrouter.ai/api/v1/chat/completions"


def test_custom_openrouter_gpt_audio_posts_streaming_pcm16_chat_completions(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(
        tts_api_key="sk-or-secret",
        tts_model="openai/gpt-audio-mini",
        tts_voice="nova",
        tts_speed="1.5",
    ))
    calls = {}
    pcm = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    events = [
        _delta_audio(_b64(pcm)),
        "[DONE]",
    ]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    audio = service.synthesize("hello there", use_cache=False)

    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"
    assert _wav_frames(audio) == (1, 2, 24000, pcm)
    assert calls["method"] == "POST"
    assert calls["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls["headers"]["Authorization"] == "Bearer sk-or-secret"
    assert calls["json"] == {
        "model": "openai/gpt-audio-mini",
        "modalities": ["text", "audio"],
        "audio": {"voice": "nova", "format": "pcm16"},
        "messages": [{"role": "user", "content": "hello there"}],
        "stream": True,
    }
    assert calls["json"]["stream"] is True
    assert "speed" not in calls["json"]
    assert calls["timeout"] == 60


def test_openrouter_audio_accumulates_sse_pcm_chunks(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    calls = {}
    chunks = [b"\x01\x02", b"\x03\x04\x05", b"\x06"]
    events = [_delta_text("ignored text")]
    events.extend(_delta_audio(_b64(chunk)) for chunk in chunks)
    events.append("[DONE]")
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    audio = service.synthesize("hello", use_cache=False)

    assert _wav_frames(audio) == (1, 2, 24000, b"".join(chunks))
    assert calls["json"]["stream"] is True
    assert calls["json"]["audio"]["format"] == "pcm16"


def test_openrouter_audio_handles_base64_split_across_deltas(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    calls = {}
    pcm = b"\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa"
    encoded = _b64(pcm)
    events = [
        _delta_audio(encoded[:1]),
        _delta_audio(encoded[1:5]),
        _delta_audio(encoded[5:]),
        "[DONE]",
    ]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    assert _wav_frames(service.synthesize("hello", use_cache=False))[3] == pcm


def test_openrouter_audio_handles_padding_split_across_deltas(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    calls = {}
    pcm = b"\x21\x22\x23\x24"  # base64 ends in "=="
    encoded = _b64(pcm)
    assert encoded.endswith("==")
    events = [
        _delta_audio(encoded[:-1]),
        _delta_audio(encoded[-1:]),
        "[DONE]",
    ]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    assert _wav_frames(service.synthesize("hello", use_cache=False))[3] == pcm


def test_openrouter_audio_appends_final_message_audio(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    calls = {}
    first, second = b"\x01\x02\x03\x04", b"\x05\x06\x07\x08"
    events = [
        _delta_audio(_b64(first)),
        _final_audio(_b64(second)),
        "[DONE]",
    ]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    assert _wav_frames(service.synthesize("hello", use_cache=False))[3] == first + second


def test_openrouter_pcm16_is_wrapped_as_valid_24khz_mono_wav(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    pcm = b"\x00\x01" * (24000 // 4)
    monkeypatch.setattr(
        tts_module.httpx,
        "stream",
        _stream_mock({}, [_delta_audio(_b64(pcm)), "[DONE]"]),
    )

    audio = service.synthesize("hello", use_cache=False)

    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"
    assert int.from_bytes(audio[24:28], "little") == 24000
    assert int.from_bytes(audio[22:24], "little") == 1
    assert int.from_bytes(audio[34:36], "little") == 16
    channels, sample_width, sample_rate, frames = _wav_frames(audio)
    assert (channels, sample_width, sample_rate) == (1, 2, 24000)
    assert frames == pcm


def test_openrouter_wav_cache_route_and_base64_are_playback_bytes(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    pcm = b"\x10\x20\x30\x40\x50\x60"
    monkeypatch.setattr(
        tts_module.httpx,
        "stream",
        _stream_mock({}, [_delta_audio(_b64(pcm)), "[DONE]"]),
    )

    encoded = service.synthesize_to_base64("hello")
    audio = base64.b64decode(encoded)

    assert _wav_frames(audio)[3] == pcm
    cached = list(tmp_path.glob("*.*"))
    assert len(cached) == 1
    assert cached[0].suffix == ".wav"
    assert cached[0].read_bytes() == audio

    app = FastAPI()
    app.include_router(setup_tts_routes(service))
    response = TestClient(app).post("/api/tts/synthesize", json={"text": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert "speech.wav" in response.headers["content-disposition"]
    assert response.content == audio


def test_configured_openrouter_endpoint_uses_streaming_chat_audio(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "tts_enabled": True,
        "tts_provider": "endpoint:openrouter",
        "tts_model": "openai/gpt-audio",
        "tts_voice": "sage",
        "tts_speed": "1",
    })

    class FakeQuery:
        def filter(self, *args):
            return self

        def first(self):
            return types.SimpleNamespace(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-or-secret",
                name="OpenRouter",
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
    pcm = b"\x0a\x0b\x0c\x0d"
    events = [_delta_audio(_b64(pcm)), "[DONE]"]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    assert _wav_frames(service.synthesize("hello", use_cache=False))[3] == pcm
    assert calls["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert calls["json"]["model"] == "openai/gpt-audio"
    assert calls["json"]["audio"] == {"voice": "sage", "format": "pcm16"}
    assert calls["json"]["stream"] is True


def test_openrouter_audio_missing_data_is_actionable(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings())
    calls = {}
    events = [_delta_text("no audio here"), "[DONE]"]
    monkeypatch.setattr(tts_module.httpx, "stream", _stream_mock(calls, events))

    with pytest.raises(TTSError, match="did not include audio data"):
        service.synthesize("hello", use_cache=False)


def test_openrouter_audio_http_error_redacts_key(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: _settings(tts_api_key="sk-or-secret"))
    calls = {}
    monkeypatch.setattr(
        tts_module.httpx,
        "stream",
        _stream_mock(
            calls,
            [],
            status=400,
            text='{"error":{"message":"Audio output requires stream: true","key":"sk-or-secret"}}',
        ),
    )

    with pytest.raises(TTSError) as excinfo:
        service.synthesize("hello", use_cache=False)

    message = str(excinfo.value)
    assert "HTTP 400" in message
    assert "Audio output requires stream: true" in message
    assert "sk-or-secret" not in message

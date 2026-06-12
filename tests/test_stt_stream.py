# tests/test_stt_stream.py
import numpy as np

from services.stt.stt_service import STTService


class _FakeSegment:
    def __init__(self, text):
        self.text = text


def test_transcribe_array_joins_segments(monkeypatch):
    service = STTService()

    class _FakeModel:
        def transcribe(self, audio, **kwargs):
            assert isinstance(audio, np.ndarray)
            assert audio.dtype == np.float32
            return [_FakeSegment(" hello"), _FakeSegment(" world")], None

    monkeypatch.setattr(service, "_get_whisper", lambda: _FakeModel())
    audio = np.zeros(16000, dtype=np.float32)
    assert service.transcribe_array(audio) == "hello world"


def test_transcribe_array_no_model(monkeypatch):
    service = STTService()
    monkeypatch.setattr(service, "_get_whisper", lambda: None)
    assert service.transcribe_array(np.zeros(100, dtype=np.float32)) is None


import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.stt_stream_routes import setup_stt_stream_routes


class _StubSTT:
    """Stub service: 'transcribes' by reporting how many samples it saw."""
    available = True
    local_stream_capable = True

    def transcribe_array(self, audio, language=None):
        return f"len={len(audio)}"


def _client(service=None):
    app = FastAPI()
    app.include_router(setup_stt_stream_routes(service or _StubSTT()))
    return TestClient(app)


def _pcm16(n_samples, value=1000):
    return np.full(n_samples, value, dtype=np.int16).tobytes()


def test_stream_partial_and_final():
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(16000))          # 1s of audio
        ws.send_text(json.dumps({"event": "flush"}))   # deterministic partial for tests
        msg = ws.receive_json()
        assert "partial" in msg and msg["partial"] == "len=16000"
        ws.send_bytes(_pcm16(8000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=24000"


def test_stream_abort_clears_buffer():
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(16000))
        ws.send_text(json.dumps({"event": "abort"}))
        ws.send_bytes(_pcm16(4000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=4000"


def test_stream_unavailable_service():
    class _Down:
        available = False
        def transcribe_array(self, *a, **k): return None
    client = _client(_Down())
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(1000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert "error" in msg


class _FakeDetector:
    def __init__(self, fire_on_feed=2):
        self._n = 0
        self._fire_on = fire_on_feed
        self.resets = 0

    def feed(self, pcm):
        self._n += 1
        return self._n >= self._fire_on

    def reset(self):
        self.resets += 1


def test_wake_mode_fires_and_switches_to_dictate(monkeypatch):
    import routes.stt_stream_routes as mod
    detector = _FakeDetector(fire_on_feed=2)
    monkeypatch.setattr(mod, "new_wakeword_detector", lambda: detector)
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_text(json.dumps({"mode": "wake"}))
        ws.send_bytes(_pcm16(1280))     # feed 1 — no wake
        ws.send_bytes(_pcm16(1280))     # feed 2 — fires
        msg = ws.receive_json()
        assert msg == {"wake": True}
        # auto-switched to dictate: audio now buffers for transcription
        ws.send_bytes(_pcm16(16000))
        ws.send_text(json.dumps({"event": "end"}))
        msg = ws.receive_json()
        assert msg["final"] == "len=16000"


def test_wake_mode_unavailable_detector(monkeypatch):
    import routes.stt_stream_routes as mod
    monkeypatch.setattr(mod, "new_wakeword_detector", lambda: None)
    client = _client()
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_text(json.dumps({"mode": "wake"}))
        msg = ws.receive_json()
        assert "error" in msg


def test_ws_rejects_when_auth_check_fails():
    app = FastAPI()
    app.include_router(setup_stt_stream_routes(_StubSTT(), auth_check=lambda ws: False))
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/stt/stream") as ws:
            ws.receive_json()


def test_ws_allows_when_auth_check_passes():
    app = FastAPI()
    app.include_router(setup_stt_stream_routes(_StubSTT(), auth_check=lambda ws: True))
    client = TestClient(app)
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(4000))
        ws.send_text(json.dumps({"event": "end"}))
        assert ws.receive_json()["final"] == "len=4000"


def test_stream_rejects_non_local_provider():
    class _EndpointProvider:
        available = True
        local_stream_capable = False
        def transcribe_array(self, *a, **k): return "should-not-run"
    client = _client(_EndpointProvider())
    with client.websocket_connect("/api/stt/stream") as ws:
        ws.send_bytes(_pcm16(1000))
        msg = ws.receive_json()
        assert "error" in msg

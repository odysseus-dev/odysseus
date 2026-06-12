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

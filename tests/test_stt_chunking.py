"""Chunked local transcription: long audio splits into overlapping windows.

Direct service unit tests with fakes (no TestClient, no network). The fake
faster_whisper.audio returns plain float lists so window boundaries are
exactly assertable without numpy.
"""

import sys
import types
from types import SimpleNamespace

import pytest

from services.stt.stt_service import STTService

_SR = 16000


def _install_fake_stack(monkeypatch, calls, decode_seconds=25.0, fail_decode=False):
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = object  # never instantiated; model is injected directly
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    audio_mod = types.ModuleType("faster_whisper.audio")

    def fake_decode(path, sampling_rate=16000):
        if fail_decode:
            raise RuntimeError("boom")
        assert sampling_rate == _SR
        return [0.0] * int(decode_seconds * sampling_rate)

    audio_mod.decode_audio = fake_decode
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", audio_mod)

    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


class _FakeModel:
    def __init__(self, calls):
        self._calls = calls

    def transcribe(self, audio, **kwargs):
        self._calls.append(audio)

        class _Seg:
            text = f"part{len(self._calls)}"

        class _Info:
            language = "sk"
            language_probability = 0.8

        return iter([_Seg()]), _Info()


def _service_with_fake_model(monkeypatch, calls):
    service = STTService()
    service._whisper_model = _FakeModel(calls)
    return service


def test_long_audio_splits_into_windows(monkeypatch):
    calls = []
    _install_fake_stack(monkeypatch, calls, decode_seconds=25.0)
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_SECONDS", "10")
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_OVERLAP_SECONDS", "0")
    service = _service_with_fake_model(monkeypatch, calls)
    assert service._transcribe_local(b"audio", "") == "part1 part2"
    # 25s with 10s windows and a <15s tail merge: (0,10) + (10,25).
    assert len(calls) == 2
    assert len(calls[0]) == 10 * _SR
    assert len(calls[1]) == 15 * _SR


def test_overlap_shifts_windows(monkeypatch):
    calls = []
    _install_fake_stack(monkeypatch, calls, decode_seconds=30.0)
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_SECONDS", "10")
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_OVERLAP_SECONDS", "2")
    service = _service_with_fake_model(monkeypatch, calls)
    service._transcribe_local(b"audio", "")
    # step 8: (0,10), then the 20s remainder stretches to the end: (8,30).
    assert len(calls) == 2
    assert len(calls[0]) == 10 * _SR
    assert len(calls[-1]) == 22 * _SR


def test_short_audio_single_pass(monkeypatch):
    calls = []
    _install_fake_stack(monkeypatch, calls, decode_seconds=5.0)
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_SECONDS", "10")
    service = _service_with_fake_model(monkeypatch, calls)
    assert service._transcribe_local(b"audio", "") == "part1"
    assert len(calls) == 1
    assert isinstance(calls[0], str)  # file path, not a sample slice


def test_chunking_disabled_single_pass(monkeypatch):
    calls = []
    _install_fake_stack(monkeypatch, calls, decode_seconds=25.0)
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_SECONDS", "0")
    service = _service_with_fake_model(monkeypatch, calls)
    service._transcribe_local(b"audio", "")
    assert len(calls) == 1
    assert isinstance(calls[0], str)


def test_decode_failure_falls_back_to_single_pass(monkeypatch):
    calls = []
    _install_fake_stack(monkeypatch, calls, fail_decode=True)
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_SECONDS", "10")
    service = _service_with_fake_model(monkeypatch, calls)
    assert service._transcribe_local(b"audio", "") == "part1"
    assert len(calls) == 1
    assert isinstance(calls[0], str)


def test_chunk_spans_geometry(monkeypatch):
    spans = STTService._chunk_spans(200.0, 120.0, 3.0)
    assert spans == [(0.0, 120.0), (117.0, 200.0)]
    # Short tail merges into a single pass.
    assert STTService._chunk_spans(125.0, 120.0, 3.0) is None
    # Overlap clamps to half a chunk (via the env-reading helper).
    monkeypatch.setenv("ODYSSEUS_STT_CHUNK_OVERLAP_SECONDS", "1000")
    assert STTService._chunk_overlap_seconds(120.0) == 60.0
    spans = STTService._chunk_spans(200.0, 120.0, 60.0)
    assert spans == [(0.0, 120.0), (60.0, 180.0), (120.0, 200.0)]

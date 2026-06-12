import sys
import types

from services.stt.stt_service import STTService
from services.tts.tts_service import TTSService


def test_tts_disabled_toggle_blocks_synthesis(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    calls = {"endpoint": 0, "kokoro": 0}

    monkeypatch.setattr(service, "_load_settings", lambda: {
        "tts_enabled": False,
        "tts_provider": "endpoint:voice-endpoint",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    })

    def fake_endpoint(*args, **kwargs):
        calls["endpoint"] += 1
        return b"audio"

    def fake_kokoro():
        calls["kokoro"] += 1
        return None

    monkeypatch.setattr(service, "_synthesize_api", fake_endpoint)
    monkeypatch.setattr(service, "_get_kokoro", fake_kokoro)

    assert service.available is False
    assert service.synthesize("hello") is None
    assert calls == {"endpoint": 0, "kokoro": 0}


def test_stt_disabled_toggle_blocks_transcription(monkeypatch):
    service = STTService()
    calls = {"endpoint": 0, "whisper": 0}

    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": False,
        "stt_provider": "endpoint:transcribe-endpoint",
        "stt_model": "whisper-1",
        "stt_language": "",
    })

    def fake_endpoint(*args, **kwargs):
        calls["endpoint"] += 1
        return "transcript"

    def fake_whisper():
        calls["whisper"] += 1
        return None

    monkeypatch.setattr(service, "_transcribe_api", fake_endpoint)
    monkeypatch.setattr(service, "_get_whisper", fake_whisper)

    assert service.available is False
    assert service.transcribe(b"audio") is None
    assert calls == {"endpoint": 0, "whisper": 0}


def test_stt_auto_uses_supported_compute_on_apple_silicon(monkeypatch):
    service = STTService()

    monkeypatch.setattr("services.stt.stt_service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("services.stt.stt_service.platform.machine", lambda: "arm64")

    device, compute_type = service._resolve_local_backend({
        "stt_device": "auto",
        "stt_compute_type": "auto",
    })

    assert device == "cpu"
    assert compute_type == "int8_float32"


def test_stt_float16_auto_falls_back_to_int8(monkeypatch):
    service = STTService()
    calls = []

    class FakeWhisperModel:
        def __init__(self, model_size, **kwargs):
            calls.append((model_size, kwargs))
            if kwargs["compute_type"] == "float16":
                raise RuntimeError("float16 unsupported")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_model": "tiny",
        "stt_device": "cpu",
        "stt_compute_type": "auto",
    })
    monkeypatch.setattr(service, "_resolve_local_backend", lambda settings: ("cpu", "float16"))

    assert service._get_whisper() is not None
    assert calls == [
        ("tiny", {"device": "cpu", "compute_type": "float16"}),
        ("tiny", {"device": "cpu", "compute_type": "int8"}),
    ]
    assert service._whisper_config == ("tiny", "cpu", "int8", ())


def test_stt_reloads_model_when_backend_config_changes(monkeypatch):
    service = STTService()
    settings = {
        "stt_model": "base",
        "stt_device": "cpu",
        "stt_compute_type": "int8",
    }
    calls = []

    class FakeWhisperModel:
        def __init__(self, model_size, **kwargs):
            calls.append((model_size, kwargs))

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    monkeypatch.setattr(service, "_load_settings", lambda: settings)

    first = service._get_whisper()
    assert service._get_whisper() is first

    settings["stt_compute_type"] = "float32"
    second = service._get_whisper()

    assert second is not first
    assert calls == [
        ("base", {"device": "cpu", "compute_type": "int8"}),
        ("base", {"device": "cpu", "compute_type": "float32"}),
    ]

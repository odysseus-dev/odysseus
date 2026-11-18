from services.stt.stt_service import STTService
from services.tts.tts_service import TTSService


def test_tts_disabled_toggle_blocks_synthesis(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    calls = {"endpoint": 0}

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

    monkeypatch.setattr(service, "_synthesize_api", fake_endpoint)

    assert service.available is False
    assert service.synthesize("hello") is None
    assert calls == {"endpoint": 0}


def test_load_settings_migrates_retired_local_provider(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr("src.settings.load_settings", lambda: {
        "tts_enabled": True,
        "tts_provider": "local",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    })

    assert service._load_settings()["tts_provider"] == "disabled"


def test_retired_local_tts_provider_is_unavailable(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    service._load_settings = lambda: {
        "tts_enabled": True,
        "tts_provider": "local",
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    }

    assert service.available is False
    assert service.synthesize("hello", use_cache=False) is None


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

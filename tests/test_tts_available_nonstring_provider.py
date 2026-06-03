from services.tts.tts_service import TTSService


def test_available_tolerates_non_string_provider(tmp_path):
    """A hand-edited/corrupt data/settings.json can store a non-string
    tts_provider (e.g. null or a number). available reads it and calls
    provider.startswith("endpoint:"), which raised AttributeError on a
    non-str. It must instead fall through and report unavailable."""
    service = TTSService(cache_dir=str(tmp_path))
    service._load_settings = lambda: {
        "tts_enabled": True,
        "tts_provider": 123,
        "tts_model": "tts-1",
        "tts_voice": "alloy",
        "tts_speed": "1",
    }
    assert service.available is False

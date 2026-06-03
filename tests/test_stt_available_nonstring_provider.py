from services.stt.stt_service import STTService


def test_available_tolerates_non_string_provider():
    """A hand-edited/corrupt data/settings.json can store a non-string
    stt_provider (e.g. null or a number). available reads it and calls
    provider.startswith("endpoint:"), which raised AttributeError on a
    non-str. It must instead fall through and report unavailable."""
    service = STTService()
    service._load_settings = lambda: {
        "stt_enabled": True,
        "stt_provider": None,
        "stt_model": "base",
        "stt_language": "",
    }
    assert service.available is False

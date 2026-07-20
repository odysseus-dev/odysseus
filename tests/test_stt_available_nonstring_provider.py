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


def test_get_stats_tolerates_non_string_provider():
    """get_stats has the same endpoint: branch as available, so the same
    corrupt non-string stt_provider raised AttributeError there and turned
    /api/stt/stats into HTTP 500. It must return unavailable stats instead."""
    service = STTService()
    service._load_settings = lambda: {
        "stt_enabled": True,
        "stt_provider": None,
        "stt_model": "base",
        "stt_language": "",
    }
    stats = service.get_stats()
    assert stats["available"] is False
    assert stats["provider"] is None
    assert "endpoint_id" not in stats

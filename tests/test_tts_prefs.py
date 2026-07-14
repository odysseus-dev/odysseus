"""Tests for per-save tts_prefs normalization."""

from titan.fugassa.game_session import normalize_tts_prefs


def test_tts_prefs_defaults():
    prefs = normalize_tts_prefs(None)
    assert prefs["mode"] == "manual"
    assert prefs["enabled"] is True
    assert prefs["speaker_id"] == 0
    assert prefs["speed"] == 1.0
    assert prefs["lang"] in ("en", "cs", "uk")


def test_tts_prefs_clamps():
    prefs = normalize_tts_prefs({
        "speaker_id": 99,
        "speed": 5.0,
        "mode": "auto",
        "lang": "en",
    })
    assert prefs["speaker_id"] == 9
    assert prefs["speed"] == 1.5
    assert prefs["mode"] == "auto"
    assert prefs["lang"] == "en"

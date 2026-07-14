"""Tests for Supertonic voice manifest."""

from services.tts.voice_manifest import list_voices_for_lang, load_voice_manifest


def test_manifest_has_ten_speakers():
    data = load_voice_manifest()
    assert data.get("engine") == "supertonic-3"
    assert len(data.get("speakers") or []) == 10


def test_list_voices_cs_labels():
    voices = list_voices_for_lang("cs")
    assert len(voices) == 10
    assert voices[0]["label"] == "M1 — mužský"
    assert voices[0]["id"] == 0
    assert voices[0]["code"] == "M1"
    assert voices[0]["gender"] == "male"
    assert voices[5]["code"] == "F1"


def test_list_voices_unknown_lang_falls_back_to_cs():
    voices = list_voices_for_lang("xx")
    assert voices[0]["lang"] == "cs"

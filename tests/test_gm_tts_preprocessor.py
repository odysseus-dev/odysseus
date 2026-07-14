"""Tests for GM TTS text preprocessor."""

from titan.fugassa.gm_tts_preprocessor import extract_narrative_for_tts


def test_strips_markdown_table():
    raw = """| Time of Day | Weather |
| --- | --- |
| Morning | Clear |

The tavern door creaks open. Smoke hangs in the air.

Round summary:
You may rest or leave."""
    out = extract_narrative_for_tts(raw)
    assert "|" not in out
    assert "tavern door" in out
    assert "Round summary" not in out


def test_stops_at_suggestions_heading():
    raw = """A cold wind blows across the moor.

Suggestions:
- Go north
- Return to town"""
    out = extract_narrative_for_tts(raw)
    assert "cold wind" in out
    assert "Go north" not in out


def test_empty_input():
    assert extract_narrative_for_tts("") == ""
    assert extract_narrative_for_tts("   ") == ""

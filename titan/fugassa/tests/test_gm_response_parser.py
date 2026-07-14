"""Tests for GM response section parsing."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.gm_response_parser import (
    assistant_text_from_response,
    extract_current_scene_narrative,
    truncate_duplicate_gm_reply,
)


def test_extract_current_scene_narrative_with_headers():
    raw = """
| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |
| Morning | 08:00 AM | Age 1 101 3 12 | Waxing | Market Square | Spring | Clear |

Recap
You previously haggled with a merchant and left the alley behind.

Current scene
The guard captain slams his fist on the crate. Splinters fly across the cobblestones.
Lantern light catches the drawn sword at his hip as traders scatter.

Round summary
Tension rose; the guard now blocks the gate.

Suggestions
- Talk him down
- Slip into the crowd

What do you do next?
"""
    scene = extract_current_scene_narrative(raw)
    assert "guard captain" in scene
    assert "Splinters fly" in scene
    assert "previously haggled" not in scene
    assert "Tension rose" not in scene
    assert "Talk him down" not in scene


def test_extract_current_scene_narrative_without_scene_header():
    raw = """
| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |
| Night | 10:00 PM | Age 1 101 3 12 | Full | Docks | Autumn | Fog |

Recap
The party reached the waterfront earlier.

Rain drums on the warehouse roof. A rope ladder swings in the wind over dark water.

Round summary
The dockside chase ended at the ladder.

What do you do next?
"""
    scene = extract_current_scene_narrative(raw)
    assert "Rain drums" in scene
    assert "reached the waterfront" not in scene
    assert "chase ended" not in scene


def test_truncate_duplicate_gm_reply_at_closing_hook():
    raw = (
        "Lucas walks toward the ad.\n\n"
        "**Round summary:** He approached the hologram.\n\n"
        "- Speak to the clerk\n\n"
        "What do you do next?\n\n"
        "I'm standing before this hologram and thinking about my coins.\n\n"
        "**Round summary:** duplicate loop.\n"
    )
    trimmed = truncate_duplicate_gm_reply(raw)
    assert trimmed.endswith("What do you do next?")
    assert "I'm standing" not in trimmed
    assert trimmed.count("**Round summary:**") == 1


def test_assistant_text_from_response_strips_reasoning_loop():
    raw = (
        "Lucas studies the hologram.\n\n"
        "**Round summary:** He learned the price.\n\n"
        "What do you do next?\n\n"
        "I'm calculating whether I can afford her.\n"
    )
    text = assistant_text_from_response(raw)
    assert "Lucas studies" in text
    assert "Round summary" not in text
    assert "I'm calculating" not in text


def test_assistant_text_keeps_suggestion_bullets():
    raw = (
        "Lucas waits by the door.\n\n"
        "**Round summary:** He waited.\n\n"
        "- Knock on the door\n"
        "- Leave quietly\n\n"
        "What do you do next?"
    )
    text = assistant_text_from_response(raw)
    assert "Lucas waits" in text
    assert "Round summary" not in text
    assert "Knock on the door" in text
    assert "What do you do next?" in text

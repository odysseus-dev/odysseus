"""XP gating for mid-campaign level-up."""

from __future__ import annotations

from titan.fugassa.level_progression import level_up_eligible, level_up_preview


def _state(*, level: int = 1, xp: int = 0):
    return {
        "party": [{"name": "Hero", "level": level, "xp": xp, "xp_to_next": 300}],
        "character_sheet": {
            "stable_sheet": {
                "identity": {"level": level, "name": "Hero", "character_class": "Fighter"},
            }
        },
        "wizard_draft_snapshot": {
            "player_class_idx": 4,
            "player_race_idx": 16,
            "abilities": {"str": 15, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 10},
        },
    }


def test_level_up_not_eligible_without_enough_xp():
    assert level_up_eligible(_state(level=1, xp=100)) is False
    preview = level_up_preview(_state(level=1, xp=100), 2)
    assert preview["ok"] is False


def test_level_up_eligible_at_threshold():
    assert level_up_eligible(_state(level=1, xp=300)) is True
    preview = level_up_preview(_state(level=1, xp=300), 2)
    assert preview["ok"] is True

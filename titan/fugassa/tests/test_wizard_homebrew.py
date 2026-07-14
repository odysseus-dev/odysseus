"""Homebrew normalization — nested LLM class/race blobs."""

from __future__ import annotations

import pytest

from titan.fugassa.dnd5e_character_builder import build
from titan.fugassa.dnd5e_database import Dnd5eDatabase
from titan.fugassa.homebrew_normalize import flatten_homebrew_details


@pytest.fixture(scope="module")
def db() -> Dnd5eDatabase:
    database = Dnd5eDatabase()
    assert database.load_all()
    return database


def test_flatten_nested_class_race_blobs() -> None:
    raw = {
        "class": {
            "name": "Scavenger",
            "hit_die": 8,
            "skill_proficiency_options": ["Stealth", "Perception"],
            "optional_skill_proficiency_choose": 2,
            "class_features": [{"name": "Scavenge", "level": 1, "desc": "Salvage."}],
            "spellcasting": {
                "has": True,
                "ability": "int",
                "model": "known",
                "cantrips_known": 2,
                "spells_known": 4,
                "spells_prepared_estimate": None,
                "slots_by_level": {"1": 2},
            },
        },
        "race": {
            "name": "Human",
            "racial_traits": [{"name": "Versatile", "desc": "Extra skill."}],
        },
        "spell_catalog": [{"name": "Shield", "level": 1, "desc": "AC bonus."}],
    }
    flat = flatten_homebrew_details(raw)
    assert flat["hit_die"] == 8
    assert flat["skill_proficiency_choose"] == 2
    assert flat["class_features"][0]["name"] == "Scavenge"
    assert flat["racial_traits"][0]["name"] == "Versatile"
    assert flat["spellcasting"]["spells_known"] == 4
    assert "class" not in flat
    assert "race" not in flat


def test_build_handles_homebrew_spellcasting_with_null_prepared(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Scavenger",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 10, "dex": 14, "con": 10, "int": 14, "wis": 10, "cha": 10},
            "homebrew_details": flatten_homebrew_details(
                {
                    "class": {
                        "hit_die": 8,
                        "class_features": [{"name": "Scavenge", "level": 1, "desc": "x"}],
                        "spellcasting": {
                            "has": True,
                            "ability": "int",
                            "model": "known",
                            "cantrips_known": 2,
                            "spells_known": 4,
                            "spells_prepared_estimate": None,
                            "slots_by_level": {"1": 2},
                        },
                    }
                }
            ),
        },
    )
    assert sheet["spellcasting"]["has"] is True
    assert sheet["spellcasting"]["spells_known"] == 4
    assert len(sheet["class_features"]) >= 1

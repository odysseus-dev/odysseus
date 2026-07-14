"""Tests for class-specific mechanic pickers (infusions, fighting style, …)."""

from __future__ import annotations

import pytest

from titan.fugassa.class_mechanics import (
    class_mechanic_pickers,
    validate_class_mechanic_choices,
)
from titan.fugassa.dnd5e_character_builder import build, validate_sheet_input
from titan.fugassa.dnd5e_database import Dnd5eDatabase, get_dnd5e_database


@pytest.fixture
def db() -> Dnd5eDatabase:
    return get_dnd5e_database()


def test_ranger_favored_pickers_at_level_1(db: Dnd5eDatabase) -> None:
    pickers = class_mechanic_pickers(
        class_id="ranger",
        template_class_id="ranger",
        level=1,
        class_resources={"favored_enemies": 1, "favored_terrain": 1},
        class_features=[],
        is_homebrew_class=False,
    )
    ids = {p["id"] for p in pickers}
    assert ids == {"favored_enemy", "favored_terrain"}


def test_warlock_invocations_not_before_level_2(db: Dnd5eDatabase) -> None:
    pickers = class_mechanic_pickers(
        class_id="warlock",
        template_class_id="warlock",
        level=1,
        class_resources={"invocations_known": 0},
        class_features=[],
        is_homebrew_class=False,
    )
    assert pickers == []


def test_infusion_choices_validate_and_merge(db: Dnd5eDatabase) -> None:
    build_input = {
        "class_label": "Scavenger",
        "race_label": "Human",
        "level": 1,
        "abilities_pre_race": {"str": 13, "dex": 17, "con": 9, "int": 12, "wis": 13, "cha": 15},
        "spell_list_class_id": "artificer",
        "class_mechanic_choices": {
            "infusions": ["enhanced-weapon", "homunculus-servant"],
        },
        "homebrew_details": {
            "hit_die": 8,
            "class_resources": {"infusions_known": 2},
            "class_features": [{"name": "Infusions", "level": 1, "desc": "Two infusions."}],
            "spellcasting": {"has": False},
        },
    }
    sheet = build(db, build_input)
    assert sheet["class_resources"].get("infusions") == ["Enhanced Weapon", "Homunculus Servant"]
    errors = validate_class_mechanic_choices(
        sheet.get("class_mechanic_pickers") or [],
        build_input["class_mechanic_choices"],
    )
    assert errors == []


def test_fighter_fighting_style_picker(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Fighter",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8},
        },
    )
    pickers = sheet.get("class_mechanic_pickers") or []
    assert any(p.get("id") == "fighting_style" for p in pickers)


def test_validate_class_mechanic_choices_multi_cap() -> None:
    pickers = [
        {
            "id": "infusions",
            "label": "Infusions",
            "type": "multi_pick",
            "cap": 2,
            "options": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        }
    ]
    assert validate_class_mechanic_choices(pickers, {"infusions": ["a"]})
    assert validate_class_mechanic_choices(pickers, {})
    assert validate_class_mechanic_choices(pickers, {"infusions": ["a", "b", "a"]})
    assert validate_class_mechanic_choices(pickers, {"infusions": ["a", "b"]}) == []

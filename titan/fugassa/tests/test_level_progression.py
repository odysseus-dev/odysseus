"""Level-up preview/apply from gameplay state."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import dnd5e_options as opt
from titan.fugassa.dnd5e_database import get_dnd5e_database
from titan.fugassa.level_progression import level_up_apply, level_up_preview
from titan.fugassa.sheet_persistence import build_sheet_from_draft, sheet_to_game_json


def _fighter_l1_state():
    fighter_idx = opt.CLASS_CHOICES.index("Fighter")
    human_idx = opt.RACE_CHOICES.index("Human")
    draft = {
        "player_name": "Bran",
        "player_age": "30",
        "level": 1,
        "player_class_idx": fighter_idx,
        "player_race_idx": human_idx,
        "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        "skill_proficiencies": {"athletics": True, "intimidation": True},
        "class_mechanic_choices": {"fighting_style": ["dueling"]},
        "playstyle": "adventure",
        "rules_mode": "5e-style",
    }
    sheet, build_input = build_sheet_from_draft(draft)
    identity = {
        "name": "Bran",
        "level": 1,
        "race": "Human",
        "character_class": "Fighter",
        "background": "Soldier",
    }
    cs = sheet_to_game_json(
        sheet,
        build_input,
        identity=identity,
        weapon_name="Longsword",
        armor_name="Chain mail",
        loc_name="Camp",
        hp_current=int(sheet.get("hp") or 12),
    )
    return {
        "character_sheet": cs,
        "party": [{
            "name": "Bran",
            "level": 1,
            "character_class": "Fighter",
            "race": "Human",
            "max_hp": sheet.get("hp"),
            "xp": 300,
            "xp_to_next": opt.xp_to_next_for_level(1),
        }],
        "wizard_draft_snapshot": {
            "skill_proficiencies": draft["skill_proficiencies"],
            "class_mechanic_choices": draft["class_mechanic_choices"],
            "selected_cantrips": [],
            "selected_spells_by_level": {},
            "asi_choices": {},
            "homebrew_choices": {},
            "expertise": {},
        },
        "playstyle_framework": "rules_based",
        "rules_mode": "5e-style",
    }


def test_level_up_preview_requires_higher_level():
    state = _fighter_l1_state()
    preview = level_up_preview(state, 1)
    assert preview["ok"] is False


def test_level_up_preview_fighter_to_2():
    state = _fighter_l1_state()
    preview = level_up_preview(state, 2)
    assert preview["ok"] is True
    assert preview["target_level"] == 2
    assert preview["hp_new"] >= preview["sheet"]["hp"]


def test_level_up_apply_updates_state():
    state = _fighter_l1_state()
    result = level_up_apply(
        state,
        target_level=2,
        class_mechanic_choices={"fighting_style": ["dueling"]},
    )
    assert result["ok"] is True
    identity = result["state"]["character_sheet"]["stable_sheet"]["identity"]
    assert identity["level"] == 2
    assert result["state"]["party"][0]["level"] == 2
    fighting = result["state"]["character_sheet"]["stable_sheet"].get("class_resources", {}).get("fighting_style")
    assert fighting == "Dueling"


def test_level_up_apply_rejects_invalid_target():
    db = get_dnd5e_database()
    state = _fighter_l1_state()
    result = level_up_apply(state, target_level=1, db=db)
    assert result["ok"] is False

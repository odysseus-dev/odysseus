"""New save initial-state generation must not leak Fugassa II's dummy
placeholder content (flat 100 HP regardless of class/level/CON, a hardcoded
"Village Elder" quest-giver, a generic starter kit that ignores whatever the
player actually built in the wizard's Inventory/Gear tabs, etc.).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import dnd5e_options as opt
from titan.fugassa import game_bootstrap as gb


_FIGHTER_IDX = opt.CLASS_CHOICES.index("Fighter")


def _base_draft(**overrides):
    draft = {
        "player_name": "Lucas",
        "level": 1,
        "abilities": {"str": 10, "dex": 12, "con": 10, "int": 14, "wis": 10, "cha": 10},
        "player_class_idx": _FIGHTER_IDX,
        "start_weapon": "Basic Sword",
        "start_armor": "Cloth Vest",
    }
    draft.update(overrides)
    return draft


def test_max_hp_scales_with_class_level_and_con():
    lvl1_wizard = opt.max_hp_for_level("Wizard", 1, con_score=10)
    lvl1_barbarian = opt.max_hp_for_level("Barbarian", 1, con_score=10)
    assert lvl1_wizard == 6, f"d6 class + 0 CON mod should be 6, got {lvl1_wizard}"
    assert lvl1_barbarian == 12, f"d12 class + 0 CON mod should be 12, got {lvl1_barbarian}"
    assert lvl1_barbarian > lvl1_wizard

    lvl10_fighter = opt.max_hp_for_level("Fighter", 10, con_score=14)
    lvl1_fighter = opt.max_hp_for_level("Fighter", 1, con_score=14)
    assert lvl10_fighter > lvl1_fighter, "a level 10 veteran must have more HP than a level 1 rookie"
    assert lvl1_fighter != 100 and lvl10_fighter != 100, "must never be the old flat dummy 100"


def test_max_hp_for_custom_class_falls_back_to_medium_die():
    # e.g. a sci-fi "Scientist" class that matches none of the 5e SRD classes.
    hp = opt.max_hp_for_level("Scientist", 1, con_score=10)
    assert hp == opt.hit_die_for_class("Scientist") == 8


def test_xp_to_next_scales_with_starting_level():
    assert opt.xp_to_next_for_level(1) == 300  # unchanged from the old flat default
    assert opt.xp_to_next_for_level(5) == 14000 - 6500
    assert opt.xp_to_next_for_level(5) != 300, "a level 5 character must not show the level-1 threshold"


def test_apply_wizard_draft_computes_real_hp_ac_instead_of_dummy_100_12():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(level=1, abilities={"str": 10, "dex": 12, "con": 10, "int": 14, "wis": 10, "cha": 10})
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    hero = state["party"][0]
    assert hero["hp"] == hero["max_hp"]
    assert hero["max_hp"] != 100, "must not be the old dummy flat 100 HP"
    assert hero["max_hp"] == opt.max_hp_for_level("Fighter", 1, 10)
    assert hero["ac"] != 12 or hero["ac"] == 10 + opt.ability_modifier(12)  # dex 12 -> +1 -> ac 11
    assert hero["xp_to_next"] == 300


def test_apply_wizard_draft_higher_level_gets_more_hp_and_scaled_xp():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(level=8, abilities={"str": 10, "dex": 10, "con": 16, "int": 10, "wis": 10, "cha": 10})
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    hero = state["party"][0]
    assert hero["level"] == 8
    assert hero["max_hp"] > 60, f"a level 8 fighter with +3 CON should have well over 60 hp, got {hero['max_hp']}"
    assert hero["xp_to_next"] == opt.xp_to_next_for_level(8)
    assert hero["xp_to_next"] != 300


def test_apply_wizard_draft_uses_gear_structured_armor_ac_and_weapon_damage():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(
        gear_structured={
            "weapon": {"name": "Flame Rapier", "damage": "1d10", "attack_bonus": 5},
            "armor": {"name": "Chain Shirt", "ac": 15},
        }
    )
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    hero = state["party"][0]
    assert hero["ac"] == 15
    assert hero["damage_dice"] == "1d10"


def test_apply_wizard_draft_invalid_gear_damage_falls_back_safely():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(gear_structured={"weapon": {"name": "Mystery Blade", "damage": "lots"}, "armor": {}})
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    hero = state["party"][0]
    assert hero["damage_dice"] == "1d8"


def test_apply_wizard_draft_gear_damage_and_defense_tolerate_llm_prose_variants():
    # Real-world LLM output: trailing damage-type prose on weapon damage,
    # and "defense" (a string) instead of the prompted "ac" key on armor.
    state = gb.build_initial_game_state("Test Save", "Sci-fi")
    draft = _base_draft(
        gear_structured={
            "weapon": {"name": "Salvage Pistol", "damage": "1d6+2 piercing"},
            "armor": {"name": "Reclaimed Leather Jack", "defense": "12"},
        }
    )
    state = gb.apply_wizard_draft(state, draft, theme="Sci-fi")
    hero = state["party"][0]
    assert hero["damage_dice"] == "1d6+2"
    assert hero["ac"] == 12


def test_apply_wizard_draft_copies_inventory_structured_items_into_shared():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(
        inventory_structured={
            "items": [
                {"item_id": "med_patch_01", "name": "Med Patch", "quantity": 3, "description": "Heals wounds."},
                {"item_id": "flare_gun", "name": "Flare Gun", "quantity": 1},
            ]
        }
    )
    state = gb.apply_wizard_draft(state, draft, theme="Sci-fi")
    shared_names = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert shared_names["Med Patch"] == 3
    assert shared_names["Flare Gun"] == 1
    # The old dummy starter kit must be gone entirely, not merged alongside it.
    assert "Rations" not in shared_names and "Torch" not in shared_names and "Health Potion" not in shared_names


def test_build_initial_game_state_has_no_dummy_quest_npcs_or_loot():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    assert state["quests"]["active"] == [], "no hardcoded 'First Steps'/'Village Elder' quest"
    assert state["location_state"]["npcs"] == [], "no hardcoded 'Village Elder'/'Merchant' NPCs"
    assert state["location_state"]["loot"] == []
    assert state["inventory"]["shared"] == [], "no hardcoded Rations/Torch/Health Potion starter kit"
    assert state["inventory"]["equipped"] == {}


def test_apply_wizard_draft_never_seeds_dummy_quest_or_npcs():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft()
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    assert state["quests"]["active"] == []
    assert state["location_state"]["npcs"] == []


def test_apply_wizard_draft_grants_starting_currency_from_background():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    wanderer = _base_draft(character_background="A penniless wanderer on the road")
    state = gb.apply_wizard_draft(state, wanderer, theme="Fantasy")
    shared = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert shared.get("bronze") == 10

    state = gb.build_initial_game_state("Test Save 2", "Fantasy")
    noble = _base_draft(
        character_background="Scion of a minor noble house",
        currency=["bronze", "silver", "gold"],
    )
    state = gb.apply_wizard_draft(state, noble, theme="Fantasy")
    shared = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert shared.get("gold") == 15
    assert shared.get("silver") == 8


def test_apply_wizard_draft_skips_currency_when_wizard_already_added_it():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(
        character_background="Wanderer",
        inventory_structured={"items": [{"name": "gold", "quantity": 3}]},
    )
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    shared = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert shared == {"gold": 3}


def test_apply_wizard_draft_uses_opening_hook_when_structured_missing():
    state = gb.build_initial_game_state("Test Save", "Fantasy")
    draft = _base_draft(
        opening_hook="You arrive at the gate at dusk.",
        opening_time_hint="Evening, first day of spring.",
    )
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    assert state["opening_scene"] == {
        "opening_text": "You arrive at the gate at dusk.",
        "time_hint": "Evening, first day of spring.",
    }


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

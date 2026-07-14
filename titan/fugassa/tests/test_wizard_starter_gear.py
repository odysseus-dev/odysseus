"""Starter gear/inventory prompts and weapon damage dice normalization."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import wizard_json as wj


def test_normalize_weapon_damage_extracts_dice_from_prose():
    assert wj.normalize_weapon_damage("1d6+2 piercing") == "1d6+2"
    assert wj.normalize_weapon_damage("1d8") == "1d8"


def test_normalize_weapon_damage_fixes_flat_number_by_weapon_hint():
    assert wj.normalize_weapon_damage("12", weapon_name="Longsword") == "1d8"
    assert wj.normalize_weapon_damage("dmg 12", weapon_name="Dagger") == "1d4"


def test_normalize_gear_json_coerces_weapon_damage():
    gear = wj.normalize_gear_json(
        {
            "weapon": {"name": "Rusty Sword", "damage": "dmg 12", "weapon_type": "sword"},
            "armor": {"name": "Leather Vest", "ac": 12},
        }
    )
    assert gear["weapon"]["damage"] == "1d8"


def test_build_character_context_block_includes_level_and_class():
    block = wj.build_character_context_block(
        {"level": 3, "character_class": "Fighter", "background": "Soldier"}
    )
    assert "level: 3" in block.lower()
    assert "Fighter" in block
    assert "Soldier" in block


def test_build_backstory_anchor_block_includes_full_backstory():
    block = wj.build_backstory_anchor_block(
        {"background": "She keeps her father's hunting knife and a worn leather satchel."}
    )
    assert "hunting knife" in block
    assert "leather satchel" in block
    assert block.startswith("Character backstory")


def test_build_backstory_anchor_block_empty_when_missing():
    assert wj.build_backstory_anchor_block({}) == ""
    assert wj.build_backstory_anchor_block(None) == ""


def test_inventory_and_gear_prompts_include_backstory_anchor_guidance():
    from titan.fugassa import wizard_engine as we

    assert "BACKSTORY ANCHOR" in we.BACKSTORY_ANCHOR_GUIDANCE
    assert "BACKSTORY GEAR ANCHOR" in we.BACKSTORY_GEAR_ANCHOR_GUIDANCE
    assert "CAMPAIGN CURRENCY" in we.CURRENCY_GUIDANCE
    assert "backstory" in we.BACKSTORY_ANCHOR_GUIDANCE.lower()
    assert "weapon" in we.BACKSTORY_GEAR_ANCHOR_GUIDANCE.lower()


def test_salvage_gear_options_from_malformed_json():
    raw = (
        '{"options":[{"title":"Father\'s Steel Blade","weapon":{"name":"Steel Longsword","damage":"1d8+1"},'
        '"armor":{"name":"Leather Vest","ac":12}},'
        '{"title":"Arcane Resonance Staff","weapon":{"name":"Elderwood Staff","damage":"1d6"},'
        '"armor":{"name":"Studded Leather","ac":14,"special_effects":["Reinforced by Scavenger"]},'
        '{"title":"Scavenger\'s Sidearm and Coat","weapon":{"name":"Iron Dagger","damage":"1d4+1"},'
        '"armor":{"name":"Traveler\'s Coat","ac":12}}],"selection_hint":"Choose one"}'
    )
    options = wj.salvage_gear_options(raw)
    assert len(options) == 3
    formatted = wj.format_gear_options(options, 1)
    assert "Option 1: Father's Steel Blade" in formatted
    assert "Option 3: Scavenger's Sidearm and Coat" in formatted
    assert "Choose one option (1/2/3)" in formatted


def test_try_format_gear_options_json_never_returns_raw_wrapper():
    raw = (
        '{"options":[{"title":"Loadout A","weapon":{"name":"Sword","damage":"1d8","description":"x"},'
        '"armor":{"name":"Leather","ac":11,"description":"y"}}]}'
    )
    formatted = wj.try_format_gear_options_json(raw, 1)
    assert formatted
    assert formatted.startswith("Option 1:")
    assert '"options"' not in formatted

"""Tests for equipment_slots classification and item_engine equip/unequip."""

from __future__ import annotations

import pytest

from titan.fugassa import equipment_slots, item_engine


def _state(**overrides):
    base = {
        "party": [{"name": "Lucas", "ac": 12, "damage_dice": "1d8"}],
        "inventory": {"shared": [], "equipped": {}},
        "character_sheet": {"stable_sheet": {"abilities": {"dexterity": 14}, "inventory": {}}},
    }
    base.update(overrides)
    return base


class TestClassifyItemCategory:
    def test_potion_has_no_slot(self):
        assert equipment_slots.classify_item_category({"name": "Health Potion", "description": "A red vial."}) is None

    def test_helmet_classifies_as_head(self):
        assert equipment_slots.classify_item_category({"name": "Iron Helmet"}) == "head"

    def test_breastplate_classifies_as_body(self):
        assert equipment_slots.classify_item_category({"name": "Steel Breastplate"}) == "body"

    def test_sword_classifies_as_weapon(self):
        assert equipment_slots.classify_item_category({"name": "Rusty Sword"}) == "weapon"

    def test_explicit_armor_type_wins_over_name(self):
        assert equipment_slots.classify_item_category({"name": "Mystery Gear", "armor_type": "light"}) == "body"

    def test_explicit_weapon_type_wins_over_name(self):
        assert equipment_slots.classify_item_category({"name": "Mystery Gear", "weapon_type": "melee"}) == "weapon"

    def test_boots_classify_as_feet(self):
        assert equipment_slots.classify_item_category({"name": "Leather Boots"}) == "feet"

    def test_backpack_classifies_as_backpack(self):
        assert equipment_slots.classify_item_category({"name": "Traveler's Backpack"}) == "backpack"

    def test_belt_classifies_as_belt(self):
        assert equipment_slots.classify_item_category({"name": "Leather Belt"}) == "belt"


class TestSlotAccepts:
    def test_body_slot_rejects_potion(self):
        assert not equipment_slots.slot_accepts("body", {"name": "Health Potion"})

    def test_body_slot_accepts_armor(self):
        assert equipment_slots.slot_accepts("body", {"name": "Chain Mail"})

    def test_weapon_main_and_off_both_accept_weapon(self):
        sword = {"name": "Longsword"}
        assert equipment_slots.slot_accepts("weapon_main", sword)
        assert equipment_slots.slot_accepts("weapon_off", sword)

    def test_head_slot_rejects_weapon(self):
        assert not equipment_slots.slot_accepts("head", {"name": "Longsword"})


class TestExtractAcAndDamage:
    def test_extract_ac_bonus_from_field(self):
        assert equipment_slots.extract_ac_bonus({"ac": 15}) == 15

    def test_extract_ac_bonus_from_description(self):
        assert equipment_slots.extract_ac_bonus({"description": "Grants AC 14 while worn."}) == 14

    def test_extract_ac_bonus_none_when_absent(self):
        assert equipment_slots.extract_ac_bonus({"description": "A cozy scarf."}) is None

    def test_extract_damage_dice_tolerates_prose(self):
        assert equipment_slots.extract_damage_dice({"damage": "1d6+2 piercing"}) == "1d6+2"

    def test_extract_damage_dice_none_when_absent(self):
        assert equipment_slots.extract_damage_dice({"description": "Just a stick."}) is None


class TestEquipItem:
    def test_equip_moves_item_from_shared_to_equipped(self):
        state = _state(inventory={"shared": [{"name": "Chain Mail", "qty": 1}], "equipped": {}})
        item_engine.equip_item(state, "Lucas", "Chain Mail", "body")
        assert state["inventory"]["shared"] == []
        assert state["inventory"]["equipped"]["Lucas"]["body"]["name"] == "Chain Mail"

    def test_equip_rejects_wrong_slot(self):
        state = _state(inventory={"shared": [{"name": "Health Potion", "qty": 3}], "equipped": {}})
        with pytest.raises(item_engine.EquipError):
            item_engine.equip_item(state, "Lucas", "Health Potion", "body")
        # Rejected equip must not mutate inventory.
        assert state["inventory"]["shared"] == [{"name": "Health Potion", "qty": 3}]

    def test_equip_unknown_item_raises(self):
        state = _state()
        with pytest.raises(item_engine.EquipError):
            item_engine.equip_item(state, "Lucas", "Nonexistent Sword", "weapon_main")

    def test_equip_unknown_slot_raises(self):
        state = _state(inventory={"shared": [{"name": "Longsword", "qty": 1}], "equipped": {}})
        with pytest.raises(item_engine.EquipError):
            item_engine.equip_item(state, "Lucas", "Longsword", "tail")

    def test_equip_body_armor_updates_hero_ac(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Plate Armor", "qty": 1, "description": "Heavy plate, AC 18."}]
        item_engine.equip_item(state, "Lucas", "Plate Armor", "body")
        assert state["party"][0]["ac"] == 18

    def test_equip_weapon_main_updates_damage_dice(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Greatsword", "qty": 1, "damage": "2d6+3 slashing"}]
        item_engine.equip_item(state, "Lucas", "Greatsword", "weapon_main")
        assert state["party"][0]["damage_dice"] == "2d6+3"

    def test_equip_swaps_previous_item_back_to_shared(self):
        state = _state()
        state["inventory"]["shared"] = [
            {"name": "Iron Helmet", "qty": 1},
            {"name": "Bronze Helmet", "qty": 1},
        ]
        item_engine.equip_item(state, "Lucas", "Iron Helmet", "head")
        item_engine.equip_item(state, "Lucas", "Bronze Helmet", "head")
        shared_names = [i["name"] for i in state["inventory"]["shared"]]
        assert "Iron Helmet" in shared_names
        assert state["inventory"]["equipped"]["Lucas"]["head"]["name"] == "Bronze Helmet"

    def test_equip_decrements_stacked_quantity(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Throwing Dagger", "qty": 3}]
        item_engine.equip_item(state, "Lucas", "Throwing Dagger", "weapon_off")
        remaining = next(i for i in state["inventory"]["shared"] if i["name"] == "Throwing Dagger")
        assert remaining["qty"] == 2

    def test_equip_unknown_hero_raises(self):
        state = _state(inventory={"shared": [{"name": "Longsword", "qty": 1}], "equipped": {}})
        with pytest.raises(item_engine.EquipError):
            item_engine.equip_item(state, "Nobody", "Longsword", "weapon_main")

    def test_equip_mirrors_into_gm_context_sheet(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Greatsword", "qty": 1, "damage": "2d6"}]
        item_engine.equip_item(state, "Lucas", "Greatsword", "weapon_main")
        assert state["character_sheet"]["stable_sheet"]["inventory"]["weapon"] == "Greatsword"


class TestUnequipItem:
    def test_unequip_returns_item_to_shared(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Longsword", "qty": 1}]
        item_engine.equip_item(state, "Lucas", "Longsword", "weapon_main")
        item_engine.unequip_item(state, "Lucas", "weapon_main")
        assert "weapon_main" not in state["inventory"]["equipped"]["Lucas"]
        assert any(i["name"] == "Longsword" for i in state["inventory"]["shared"])

    def test_unequip_body_resets_ac_to_dex_based_default(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Plate Armor", "qty": 1, "description": "AC 18"}]
        item_engine.equip_item(state, "Lucas", "Plate Armor", "body")
        assert state["party"][0]["ac"] == 18
        item_engine.unequip_item(state, "Lucas", "body")
        # dexterity 14 -> +2 mod -> base AC 12
        assert state["party"][0]["ac"] == 12

    def test_unequip_empty_slot_raises(self):
        state = _state()
        with pytest.raises(item_engine.EquipError):
            item_engine.unequip_item(state, "Lucas", "body")

    def test_unequip_restacks_onto_remaining_stack(self):
        state = _state()
        state["inventory"]["shared"] = [{"name": "Dagger", "qty": 3}]
        item_engine.equip_item(state, "Lucas", "Dagger", "weapon_main")
        # 2 left in shared after equipping 1; unequipping should merge back
        # into that same stack rather than creating a duplicate entry.
        item_engine.unequip_item(state, "Lucas", "weapon_main")
        daggers = [i for i in state["inventory"]["shared"] if i["name"] == "Dagger"]
        assert len(daggers) == 1
        assert daggers[0]["qty"] == 3

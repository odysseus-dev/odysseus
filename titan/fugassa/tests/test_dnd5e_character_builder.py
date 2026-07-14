"""Tests for D&D 5e database + character builder."""

from __future__ import annotations

import pytest

from titan.fugassa.dnd5e_database import Dnd5eDatabase
from titan.fugassa.dnd5e_character_builder import (
    build,
    can_select_spell,
    normalize_cantrip_set,
    normalize_spells_by_level,
    spell_budgets,
    validate_sheet_input,
)


@pytest.fixture(scope="module")
def db() -> Dnd5eDatabase:
    database = Dnd5eDatabase()
    assert database.load_all()
    return database


def test_database_loads_srd(db: Dnd5eDatabase) -> None:
    assert db.list_classes()
    assert db.list_races()
    assert db.get_class_data("wizard")
    assert db.get_race("dwarf")


def test_list_spells_for_wizard_cantrips(db: Dnd5eDatabase) -> None:
    cantrips = db.list_spells_for("wizard", 0)
    assert len(cantrips) >= 10
    assert all(int(s.get("level", -1)) == 0 for s in cantrips)


def test_human_fighter_level_1(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Fighter",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 15, "dex": 14, "con": 13, "int": 10, "wis": 12, "cha": 8},
            "skill_proficiencies": {"athletics": True, "intimidation": True},
        },
    )
    assert sheet["resolved"]["class_id"] == "fighter"
    assert sheet["resolved"]["race_id"] == "human"
    assert sheet["hp"] >= 1
    assert sheet["proficiency_bonus"] == 2
    assert not sheet["spellcasting"]["has"]


def test_hill_dwarf_cleric_level_3(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Cleric",
            "race_label": "Dwarf",
            "subrace_label": "Hill Dwarf",
            "level": 3,
            "abilities_pre_race": {"str": 10, "dex": 10, "con": 14, "int": 10, "wis": 15, "cha": 10},
            "skill_proficiencies": {"insight": True, "religion": True},
        },
    )
    assert sheet["resolved"]["subrace_id"] == "hill-dwarf"
    assert sheet["spellcasting"]["has"]
    assert sheet["spellcasting"]["max_castable_spell_level"] >= 1
    assert sheet["hp"] >= 20


def test_wizard_level_1_spell_budgets(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Wizard",
            "race_label": "Elf",
            "level": 1,
            "abilities_pre_race": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
            "skill_proficiencies": {"arcana": True, "history": True},
        },
    )
    budgets = spell_budgets(sheet["spellcasting"])
    assert budgets["cantrip_max"] == 3
    assert budgets["leveled_cap"] >= 1
    assert budgets["max_spell_level"] >= 1


def test_can_select_spell_cantrip_cap(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Wizard",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        },
    )
    sc = sheet["spellcasting"]
    cantrips = {"fire-bolt": True, "light": True, "mage-hand": True}
    result = can_select_spell(sc, cantrips, {}, 0, "prestidigitation", True)
    assert result["ok"] is False


def test_validate_blocks_incomplete_wizard(db: Dnd5eDatabase) -> None:
    draft = {
        "player_class_idx": 12,
        "player_race_idx": 5,
        "player_subrace_idx": 0,
        "level": 1,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "skill_proficiencies": {"arcana": True},
        "selected_cantrips": ["fire-bolt"],
        "selected_spells_by_level": {"1": ["magic-missile"]},
        "playstyle_framework": "rules_based",
        "rules_mode": "5e-style",
    }
    result = validate_sheet_input(draft, db=db)
    assert result["ok"] is False
    assert any("skill" in err.lower() for err in result["errors"])
    assert any("cantrip" in err.lower() for err in result["errors"])


def test_validate_same_for_homebrew_rules_mode(db: Dnd5eDatabase) -> None:
    draft = {
        "player_class_idx": 12,
        "player_race_idx": 5,
        "level": 1,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "skill_proficiencies": {"arcana": True},
        "playstyle_framework": "rules_based",
        "rules_mode": "homebrew",
    }
    result = validate_sheet_input(draft, db=db)
    assert result["ok"] is False


def test_validate_freeform_skips_spell_requirements(db: Dnd5eDatabase) -> None:
    draft = {
        "player_class_idx": 12,
        "player_race_idx": 5,
        "level": 1,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "playstyle": "slice_of_life",
        "playstyle_framework": "freeform",
    }
    result = validate_sheet_input(draft, db=db)
    assert result["ok"] is True


def test_homebrew_class_skill_cap_uses_optional_choose_key(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Scavenger",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 13, "dex": 17, "con": 9, "int": 12, "wis": 13, "cha": 15},
            "skill_proficiencies": {
                "acrobatics": True,
                "athletics": True,
                "insight": True,
                "persuasion": True,
            },
            "homebrew_details": {
                "hit_die": 8,
                "skill_proficiency_options": [
                    "Acrobatics",
                    "Athletics",
                    "History",
                    "Insight",
                    "Perception",
                    "Survival",
                ],
                "optional_skill_proficiency_choose": 3,
            },
        },
    )
    assert sheet["skill_proficiency_cap"] == 3
    prof = [s for s in sheet["skills"] if s["proficient"]]
    assert len(prof) == 3
    assert all(s["index"] in {"acrobatics", "athletics", "insight"} for s in prof)


def test_normalize_spell_formats() -> None:
    assert normalize_cantrip_set(["fire-bolt", "light"]) == {"fire-bolt": True, "light": True}
    spells = normalize_spells_by_level({"1": ["magic-missile"], "2": {"shield": True}})
    assert spells[1]["magic-missile"]
    assert spells[2]["shield"]


def test_homebrew_supplements_srd_cantrips_when_catalog_lacks_level_zero(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Scavenger",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 13, "dex": 17, "con": 9, "int": 12, "wis": 13, "cha": 15},
            "spell_list_class_id": "artificer",
            "homebrew_details": {
                "hit_die": 8,
                "spell_catalog": [
                    {"name": "Thunderwave", "level": 1, "desc": "Boom."},
                ],
                "spellcasting": {
                    "has": True,
                    "ability": "int",
                    "model": "known",
                    "cantrips_known": 2,
                    "spells_known": 4,
                    "slots_by_level": {"1": 2},
                },
            },
        },
    )
    cantrips = [s for s in sheet["homebrew_spell_catalog"] if int(s.get("level", -1)) == 0]
    assert len(cantrips) >= 2
    assert sheet.get("homebrew_cantrips_supplemented") is True
    assert sheet.get("cantrip_pool_source") == "wizard"
    assert "Eldritch Blast" not in [s.get("name") for s in cantrips]
    assert sheet.get("homebrew_pending_choices") == []


def test_homebrew_merges_srd_spells_even_when_catalog_has_cantrips(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Scavenger",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 13, "dex": 17, "con": 9, "int": 12, "wis": 13, "cha": 15},
            "spell_list_class_id": "artificer",
            "homebrew_details": {
                "hit_die": 8,
                "spell_catalog": [
                    {"name": "Mending", "level": 0, "desc": "Fix."},
                    {"name": "Guidance", "level": 0, "desc": "Help."},
                    {"name": "Healing Word", "level": 1, "desc": "Heal."},
                    {"name": "Identify", "level": 1, "desc": "Read."},
                    {"name": "Find Familiar", "level": 1, "desc": "Pet."},
                ],
                "spellcasting": {
                    "has": True,
                    "ability": "int",
                    "model": "prepared",
                    "cantrips_known": 2,
                    "spells_prepared_estimate": 4,
                    "slots_by_level": {"1": 2},
                },
            },
        },
    )
    cantrips = [s for s in sheet["homebrew_spell_catalog"] if int(s.get("level", -1)) == 0]
    level_one = [s for s in sheet["homebrew_spell_catalog"] if int(s.get("level", -1)) == 1]
    assert len(cantrips) >= 10
    assert len(level_one) >= 4
    assert sheet.get("homebrew_cantrips_supplemented") is True
    assert sheet.get("cantrip_pool_source") == "wizard"
    names = {s.get("name") for s in cantrips}
    assert "Mending" in names
    assert "Fire Bolt" in names


def test_homebrew_cantrip_pool_respects_warlock_template(db: Dnd5eDatabase) -> None:
    sheet = build(
        db,
        {
            "class_label": "Scavenger",
            "race_label": "Human",
            "level": 1,
            "abilities_pre_race": {"str": 10, "dex": 10, "con": 10, "int": 14, "wis": 10, "cha": 10},
            "spell_list_class_id": "warlock",
            "homebrew_details": {
                "hit_die": 8,
                "spell_catalog": [{"name": "Hex", "level": 1, "desc": "Curse."}],
                "spellcasting": {
                    "has": True,
                    "ability": "cha",
                    "model": "known",
                    "cantrips_known": 2,
                    "spells_known": 2,
                    "slots_by_level": {"1": 1},
                },
            },
        },
    )
    cantrips = [s.get("name") for s in sheet["homebrew_spell_catalog"] if int(s.get("level", -1)) == 0]
    assert "Eldritch Blast" in cantrips
    assert sheet.get("cantrip_pool_source") == "warlock"


def test_homebrew_pending_choices_detected_and_validated(db: Dnd5eDatabase) -> None:
    draft = {
        "player_class_idx": 13,
        "player_class_custom": "Scavenger",
        "player_race_idx": 16,
        "level": 1,
        "abilities": {"str": 13, "dex": 17, "con": 9, "int": 12, "wis": 13, "cha": 15},
        "skill_proficiencies": {"insight": True, "perception": True},
        "selected_cantrips": ["hbspell:fire-bolt", "hbspell:light"],
        "selected_spells_by_level": {"1": ["hbspell:thunderwave"]},
        "spell_list_class_id": "artificer",
        "playstyle_framework": "rules_based",
        "homebrew_details": {
            "hit_die": 8,
            "skill_proficiency_options": ["Perception", "Insight"],
            "skill_proficiency_choose": 2,
            "class_features": [
                {
                    "name": "Tool Proficiency",
                    "level": 1,
                    "desc": "Choose from: tinker's tools, leatherworker's tools, or mason's tools.",
                },
                {
                    "name": "Infusions",
                    "level": 1,
                    "desc": "You learn two Infusions to enhance items.",
                },
            ],
            "racial_traits": [
                {
                    "name": "Versatile",
                    "desc": "You gain proficiency in one skill of your choice from any class's list.",
                }
            ],
            "spell_catalog": [
                {"name": "Fire Bolt", "level": 0, "desc": "Zap."},
                {"name": "Light", "level": 0, "desc": "Glow."},
                {"name": "Thunderwave", "level": 1, "desc": "Boom."},
                {"name": "Shield", "level": 1, "desc": "Wall."},
                {"name": "Identify", "level": 1, "desc": "Read."},
                {"name": "Magic Stone", "level": 1, "desc": "Stone."},
            ],
            "spellcasting": {
                "has": True,
                "ability": "int",
                "model": "known",
                "cantrips_known": 2,
                "spells_known": 4,
                "slots_by_level": {"1": 2},
            },
            "class_resources": {"infusions_known": 2},
        },
    }
    preview = validate_sheet_input(draft, db=db)
    pending = preview["sheet"].get("homebrew_pending_choices") or []
    assert len(pending) == 2
    assert preview["ok"] is False
    assert any("Tool Proficiency" in err for err in preview["errors"])
    assert any("Versatile" in err for err in preview["errors"])

    draft["homebrew_choices"] = {
        "feature:tool-proficiency": "tinker's tools",
        "trait:versatile": "stealth",
    }
    draft["class_mechanic_choices"] = {
        "infusions": ["enhanced-weapon", "homunculus-servant"],
    }
    draft["selected_spells_by_level"] = {
        "1": ["hbspell:thunderwave", "hbspell:shield", "hbspell:identify", "hbspell:magic-stone"],
    }
    done = validate_sheet_input(draft, db=db)
    assert done["ok"] is True


def test_fixture_vectors_match(db: Dnd5eDatabase) -> None:
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "character_sheet_vectors.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for name, case in data.items():
        sheet = build(db, case["input"])
        expected = case["expected"]
        assert sheet["resolved"] == expected["resolved"], name
        assert sheet["hp"] == expected["hp"], name
        assert sheet["proficiency_bonus"] == expected["proficiency_bonus"], name
        assert sheet["spellcasting"]["has"] == expected["spellcasting"]["has"], name

    assert normalize_cantrip_set(["fire-bolt", "light"]) == {"fire-bolt": True, "light": True}
    spells = normalize_spells_by_level({"1": ["magic-missile"], "2": {"shield": True}})
    assert spells[1]["magic-missile"]
    assert spells[2]["shield"]

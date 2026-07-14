"""Sheet persistence + bootstrap integration tests."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import dnd5e_options as opt
from titan.fugassa import game_bootstrap as gb
from titan.fugassa.db import seed as db_seed
from titan.fugassa.db import sqlite_store
from titan.fugassa.sheet_persistence import build_sheet_from_draft, sheet_to_game_json


def _wizard_draft_wizard_l1():
    wizard_idx = opt.CLASS_CHOICES.index("Wizard")
    elf_idx = opt.RACE_CHOICES.index("Elf")
    return {
        "player_name": "Elara",
        "player_age": "42",
        "level": 1,
        "player_class_idx": wizard_idx,
        "player_race_idx": elf_idx,
        "player_subrace_idx": 0,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "skill_proficiencies": {"arcana": True, "history": True},
        "selected_cantrips": ["fire-bolt", "light", "mage-hand"],
        "selected_spells_by_level": {"1": ["magic-missile", "shield"]},
        "playstyle": "adventure",
        "rules_mode": "5e-style",
    }


def test_sheet_to_game_json_includes_spellcasting():
    draft = _wizard_draft_wizard_l1()
    sheet, build_input = build_sheet_from_draft(draft)
    out = sheet_to_game_json(
        sheet,
        build_input,
        identity={"name": "Elara", "level": 1, "race": "Elf", "character_class": "Wizard", "background": ""},
        weapon_name="Staff",
        armor_name="Robes",
        loc_name="Tower",
        hp_current=7,
    )
    sc = out["stable_sheet"]["spellcasting"]
    assert sc is not None
    assert sc["cantrips"] == ["fire-bolt", "light", "mage-hand"]
    assert "magic-missile" in sc["spells_known"]
    assert out["llm_summary"]["spell_summary"]


def test_apply_wizard_draft_populates_character_sheet_spellcasting():
    state = gb.build_initial_game_state("Test", "Fantasy")
    draft = _wizard_draft_wizard_l1()
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    sc = state["character_sheet"]["stable_sheet"].get("spellcasting")
    assert sc is not None
    assert len(sc.get("cantrips") or []) == 3


def test_seed_populates_player_spells_rows():
    draft = _wizard_draft_wizard_l1()
    state = gb.build_initial_game_state("SpellTest", "Fantasy")
    state = gb.apply_wizard_draft(state, draft, theme="Fantasy")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "SpellTest", theme="Fantasy")
        db_seed.bootstrap_from_wizard(db_path, draft=draft, state=state)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute("SELECT COUNT(*) AS c FROM player_spells").fetchone()["c"]
        conn.close()
        assert count >= 5

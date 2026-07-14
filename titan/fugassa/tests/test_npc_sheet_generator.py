"""NPC full-sheet generator tests (T2/T3 humanoid)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import npc_generator
from titan.fugassa.db import sqlite_store


def _make_db(tmp: str) -> str:
    db_path = os.path.join(tmp, "game.db")
    sqlite_store.init_game_db(db_path, "NpcSheetTest", theme="Fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at) VALUES ('loc1', 'Town', '', 1, datetime('now'), datetime('now'))"
    )
    conn.commit()
    loc_id = conn.execute("SELECT id FROM locations WHERE code = 'loc1'").fetchone()[0]
    conn.close()
    return db_path, int(loc_id)


def test_spawn_t0_wolf_has_no_spellbook():
    with tempfile.TemporaryDirectory() as tmp:
        db_path, loc_id = _make_db(tmp)
        res = npc_generator.spawn_npc(
            db_path,
            name="Grey Wolf",
            tier="T0",
            location_id=loc_id,
            race="Beast",
            class_role="wolf",
        )
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM npc_spellbooks WHERE npc_id = ?", (res["npc_id"],)).fetchone()[0]
        conn.close()
        assert count == 0


def test_spawn_t2_elf_wizard_has_spellbook_and_skills():
    with tempfile.TemporaryDirectory() as tmp:
        db_path, loc_id = _make_db(tmp)
        res = npc_generator.spawn_npc(
            db_path,
            name="Elara Moonwhisper",
            tier="T2",
            location_id=loc_id,
            race="Elf",
            class_role="Wizard",
            code="elara_wizard",
        )
        assert res["created"] is True
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        spells = conn.execute(
            "SELECT spell_index, is_cantrip FROM npc_spellbooks WHERE npc_id = ?",
            (res["npc_id"],),
        ).fetchall()
        skills = conn.execute(
            "SELECT skill_name FROM npc_skills WHERE npc_id = ?",
            (res["npc_id"],),
        ).fetchall()
        conn.close()
        assert len(spells) >= 3
        assert any(r["is_cantrip"] for r in spells)
        assert len(skills) >= 1


def test_npc_spell_selection_is_deterministic():
    with tempfile.TemporaryDirectory() as tmp:
        db_path, loc_id = _make_db(tmp)
        a = npc_generator.build_npc_sheet(
            race="Elf",
            class_role="Wizard",
            tier="T2",
            cr=0.5,
            npc_code="deterministic_wiz",
        )[0]
        b = npc_generator.build_npc_sheet(
            race="Elf",
            class_role="Wizard",
            tier="T2",
            cr=0.5,
            npc_code="deterministic_wiz",
        )[0]
        assert a.get("spellcasting", {}).get("has") == b.get("spellcasting", {}).get("has")

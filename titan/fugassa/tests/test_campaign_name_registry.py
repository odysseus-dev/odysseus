"""Tests for campaign_name_registry."""

from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import campaign_name_registry as cnr
from titan.fugassa.db import sqlite_store


def _seed_npc(conn: sqlite3.Connection, *, name: str, code: str) -> int:
    conn.execute(
        """
        INSERT INTO npcs (code, name, status, created_at, updated_at)
        VALUES (?, ?, 'alive', '2026-01-01', '2026-01-01')
        """,
        (code, name),
    )
    return int(conn.execute("SELECT id FROM npcs WHERE code = ?", (code,)).fetchone()[0])


def test_split_person_name():
    assert cnr.split_person_name("Elara Voss") == ("Elara", "Voss")
    assert cnr.split_person_name("Theron") == ("Theron", "")


def test_name_collision_blocks_duplicate_first_and_last(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "NameTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    try:
        _seed_npc(conn, name="Elara Voss", code="elara_voss")
        conn.commit()
    finally:
        conn.close()

    registry = cnr.seed_registry_from_npcs(db_path)
    assert cnr.name_collision(registry, "Elara Voss")
    assert cnr.name_collision(registry, "Elara Moonwhisper")
    assert cnr.name_collision(registry, "Merchant Voss")


def test_uniquify_name_picks_alternate_suffix(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "NameTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    try:
        _seed_npc(conn, name="Kaelen Driscoll", code="kaelen_driscoll")
        conn.commit()
    finally:
        conn.close()

    registry = cnr.seed_registry_from_npcs(db_path)
    alt = cnr.uniquify_name(registry, "Kaelen Voss", role="merchant")
    assert alt != "Kaelen Voss"
    assert not cnr.name_collision(registry, alt)


def test_sanitize_population_plan_renames_collisions(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "NameTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    try:
        _seed_npc(conn, name="Elara Voss", code="elara_voss")
        conn.commit()
    finally:
        conn.close()

    plan = {
        "populate": True,
        "present_npcs": [{"name": "Seraphina Voss", "role": "merchant"}],
        "hidden_npcs": [],
    }
    out = cnr.sanitize_population_plan(plan, db_path)
    assert out["present_npcs"][0]["name"] != "Seraphina Voss"
    assert out["name_registry_renames"] == 1


def test_register_spawned_npc_persists_to_save_meta(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "NameTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    try:
        npc_id = _seed_npc(conn, name="Harven Vale", code="harven_vale")
        conn.commit()
    finally:
        conn.close()

    cnr.register_spawned_npc(db_path, npc_id=npc_id, name="Harven Vale")
    reloaded = cnr.load_registry(db_path)
    assert any(e.get("full_name") == "Harven Vale" for e in reloaded.entries)
    row = sqlite3.connect(db_path).execute(
        "SELECT value FROM save_meta WHERE key = ?",
        (cnr.REGISTRY_META_KEY,),
    ).fetchone()
    assert row and json.loads(row[0]).get("entries")

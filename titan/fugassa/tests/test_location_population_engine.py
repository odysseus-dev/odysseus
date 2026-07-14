"""Tests for location_population_engine — parse, idempotency, cache merge, movement restore."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import grid_engine, location_population_engine as lpe
from titan.fugassa.db import sqlite_store


def _insert_test_location(conn: sqlite3.Connection, *, code: str = "loc_market", name: str = "Market Square") -> int:
    now = "2026-01-01T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, description_long, is_discovered, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (code, name, "A busy square.", "A busy square with vendors.", now, now),
    )
    conn.commit()
    return int(conn.execute("SELECT id FROM locations WHERE code = ?", (code,)).fetchone()[0])


def test_parse_population_plan_requires_populate_flag_and_npcs():
    raw = json.dumps(
        {
            "populate": True,
            "reason": "busy market",
            "present_npcs": [{"name": "Vendor Kira", "role": "merchant"}],
            "hidden_npcs": [],
        }
    )
    plan = lpe.parse_population_plan(raw)
    assert plan["populate"] is True
    assert plan["present_npcs"][0]["name"] == "Vendor Kira"

    empty = lpe.parse_population_plan(json.dumps({"populate": True, "present_npcs": [], "hidden_npcs": []}))
    assert empty["populate"] is False

    skip = lpe.parse_population_plan(json.dumps({"populate": False, "reason": "mountain peak"}))
    assert skip["populate"] is False


def test_deterministic_plan_skips_procedural_wilderness():
    state = {"player": {"x": 5, "y": 5, "z": 0, "map_code": "world"}}
    loc_row = {"name": "Forest", "description_short": "A forest area."}
    plan = lpe.deterministic_population_plan(state, loc_row=loc_row)
    assert plan["populate"] is False
    assert plan["source"] == "deterministic"


def test_apply_population_plan_conn_idempotent(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "PopTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        loc_id = _insert_test_location(conn)
        code = str(conn.execute("SELECT code FROM locations WHERE id = ?", (loc_id,)).fetchone()[0])
        plan = {
            "populate": True,
            "reason": "test",
            "present_npcs": [{"name": "Test Guard", "role": "guard", "race": "human"}],
            "hidden_npcs": [],
            "source": "test",
        }
        first = lpe.apply_population_plan_conn(conn, location_id=loc_id, location_code=code, plan=plan)
        conn.commit()
        assert first["applied"] is True
        assert "Test Guard" in first["present"]

        second = lpe.apply_population_plan_conn(conn, location_id=loc_id, location_code=code, plan=plan)
        assert second["applied"] is False
    finally:
        conn.close()


def test_merge_population_into_state_updates_cache():
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": "world"},
        "location_state": {"name": "Square", "description": "Market", "npcs": []},
        "cell_location_cache": {},
    }
    lpe.merge_population_into_state(state, present=["Merchant"], hidden=["Pickpocket"], population_done=True)
    loc = state["location_state"]
    assert loc["npcs"] == ["Merchant"]
    assert loc["hidden_npcs"] == ["Pickpocket"]
    key = grid_engine.coord_key(0, 0, 0, "world")
    cached = state["cell_location_cache"][key]
    assert cached["npcs"] == ["Merchant"]
    assert cached["hidden_npcs"] == ["Pickpocket"]
    assert cached["population_done"] is True


def test_spawn_npc_moves_existing_npc_to_new_location(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "MoveTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        loc_a = _insert_test_location(conn, code="loc_a", name="Square")
        loc_b = _insert_test_location(conn, code="loc_b", name="Residence")
        first = lpe.apply_population_plan_conn(
            conn,
            location_id=loc_a,
            location_code="loc_a",
            plan={
                "populate": True,
                "reason": "test",
                "present_npcs": [{"name": "Shared Guard", "role": "guard"}],
                "hidden_npcs": [],
                "source": "test",
            },
        )
        conn.commit()
        assert first["applied"] is True
        npc_row = conn.execute(
            "SELECT current_location_id FROM npcs WHERE name = 'Shared Guard'"
        ).fetchone()
        assert int(npc_row["current_location_id"]) == loc_a

        second = lpe.apply_population_plan_conn(
            conn,
            location_id=loc_b,
            location_code="loc_b",
            plan={
                "populate": True,
                "reason": "test",
                "present_npcs": [{"name": "Shared Guard", "role": "guard"}],
                "hidden_npcs": [],
                "source": "test",
            },
        )
        conn.commit()
        assert second["applied"] is True
        npc_row = conn.execute(
            "SELECT current_location_id FROM npcs WHERE name = 'Shared Guard'"
        ).fetchone()
        assert int(npc_row["current_location_id"]) == loc_b
    finally:
        conn.close()


def test_merge_population_skips_cell_cache_when_inside_sublocation():
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": "world", "sublocation_id": 9},
        "location_state": {"name": "Interior", "description": "Inside", "npcs": []},
        "cell_location_cache": {"world:0,0,0": {"name": "Square", "description": "Market", "npcs": ["Old Guard"]}},
    }
    lpe.merge_population_into_state(state, present=["Interior Clerk"], hidden=[], population_done=True)
    assert state["location_state"]["npcs"] == ["Interior Clerk"]
    assert state["cell_location_cache"]["world:0,0,0"]["npcs"] == ["Old Guard"]


def test_move_player_to_restores_npcs_from_cache():
    map_code = grid_engine.DEFAULT_MAP_CODE
    key_square = grid_engine.coord_key(0, 0, 0, map_code)
    key_alley = grid_engine.coord_key(1, 0, 0, map_code)
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": map_code},
        "location_state": {
            "name": "Square",
            "description": "Market",
            "npcs": ["A"],
            "hidden_npcs": ["B"],
        },
        "cell_location_cache": {
            key_alley: {
                "name": "Alley",
                "description": "Dark alley",
                "npcs": [],
                "hidden_npcs": [],
            },
            key_square: {
                "name": "Square",
                "description": "Market",
                "npcs": ["A"],
                "hidden_npcs": ["B"],
                "population_done": True,
            },
        },
        "turn": 1,
    }
    grid_engine.move_player_to(state, 1, 0, 0)
    assert state["location_state"]["name"] == "Alley"
    grid_engine.move_player_to(state, 0, 0, 0)
    assert state["location_state"]["npcs"] == ["A"]
    assert state["location_state"]["hidden_npcs"] == ["B"]


def test_should_not_persist_when_llm_unavailable():
    plan = {
        "populate": False,
        "reason": "llm_disabled",
        "source": "deterministic",
        "present_npcs": [],
        "hidden_npcs": [],
    }
    assert lpe.should_persist_population_plan(plan) is False


def test_should_persist_wilderness_skip():
    plan = {
        "populate": False,
        "reason": "procedural wilderness cell",
        "source": "deterministic",
        "present_npcs": [],
        "hidden_npcs": [],
    }
    assert lpe.should_persist_population_plan(plan) is True

    manifest = {
        "spawned_hidden": ["Spy"],
        "plan": {"hidden_npcs": [{"name": "Spy"}, {"name": "Thief"}]},
    }
    names = lpe.hidden_npc_names_from_manifest(manifest)
    assert names == {"Spy", "Thief"}


@pytest.mark.asyncio
async def test_run_population_marks_empty_wilderness(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "EmptyWild", theme="fantasy")
    biome = grid_engine.biome_label(3, 3).capitalize()
    conn = sqlite3.connect(db_path)
    try:
        loc_id = _insert_test_location(
            conn,
            code="loc_wild",
            name=biome,
        )
        conn.execute(
            "UPDATE locations SET description_short = ?, description_long = ? WHERE id = ?",
            (f"A {biome.lower()} area.", f"A {biome.lower()} area.", loc_id),
        )
        conn.commit()
    finally:
        conn.close()
    state = {
        "player": {"x": 3, "y": 3, "z": 0, "map_code": "world"},
        "location_state": {"name": biome, "description": f"A {biome.lower()} area."},
        "world_profile": {"theme": "fantasy"},
    }
    result = await lpe.run_population_for_location(
        "EmptyWild",
        db_path,
        state,
        location_id=loc_id,
        llm_enabled=False,
    )
    assert result.get("applied") is True
    assert result.get("populate") is False

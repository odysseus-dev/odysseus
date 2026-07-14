"""Tests for NPC location repair and GM scene context."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

from titan.fugassa import gm_runner, npc_generator
from titan.fugassa.db import sqlite_store
from titan.fugassa.save_state_repair import dedupe_sublocations, repair_orphan_npc_locations


def _insert_grid_location(conn: sqlite3.Connection, *, code: str = "grid_square", name: str = "Town Square") -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at)
        VALUES (?, ?, 'Outdoor square.', NULL, 1, '2026-01-01', '2026-01-01')
        """,
        (code, name),
    )
    return int(conn.execute("SELECT id FROM locations WHERE code = ?", (code,)).fetchone()[0])


def test_dedupe_sublocations_moves_npcs_to_parent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Dedupe", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid_location(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('1_residence', 'Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        residence_id = int(
            conn.execute("SELECT id FROM locations WHERE code = '1_residence'").fetchone()[0]
        )
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('2_nested', 'Nested Residence', 'Deeper.', ?, 1, '2026-01-01', '2026-01-01')",
            (residence_id,),
        )
        nested_id = int(conn.execute("SELECT id FROM locations WHERE code = '2_nested'").fetchone()[0])
        npc_generator.spawn_npc(conn, name="Lady Veyra", tier="T2", location_id=nested_id)
        conn.commit()
        conn.close()

        removed = dedupe_sublocations(db_path)
        assert removed == 1

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT current_location_id FROM npcs WHERE name = 'Lady Veyra'").fetchone()
        nested_exists = conn.execute("SELECT 1 FROM locations WHERE id = ?", (nested_id,)).fetchone()
        conn.close()
        assert int(row["current_location_id"]) == residence_id
        assert nested_exists is None


def test_repair_orphan_npc_locations_uses_manifest_owner():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Orphan", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid_location(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at, notes) "
            "VALUES ('1_residence', 'Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01', ?)",
            (
                grid_id,
                json.dumps(
                    {
                        "population_applied": True,
                        "spawned_present": ["Lady Veyra"],
                        "spawned_hidden": [],
                    }
                ),
            ),
        )
        residence_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO npcs (code, name, current_location_id, status, created_at, updated_at) "
            "VALUES ('lady_veyra', 'Lady Veyra', 99, 'alive', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        moved = repair_orphan_npc_locations(db_path)
        assert moved >= 1

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT current_location_id FROM npcs WHERE name = 'Lady Veyra'").fetchone()
        conn.close()
        assert int(row[0]) == residence_id


def test_relocate_stray_npcs_from_populated_interior():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Stray", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid_location(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at, notes) "
            "VALUES ('1_residence', 'Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01', ?)",
            (
                grid_id,
                json.dumps(
                    {
                        "population_applied": True,
                        "spawned_present": ["Lady Veyra"],
                        "spawned_hidden": ["Spy"],
                    }
                ),
            ),
        )
        residence_id = int(conn.execute("SELECT id FROM locations WHERE code = '1_residence'").fetchone()[0])
        npc_generator.spawn_npc(conn, name="Lady Veyra", tier="T2", location_id=residence_id)
        npc_generator.spawn_npc(conn, name="Archivist Extra", tier="T2", location_id=residence_id)
        conn.commit()
        conn.close()

        from titan.fugassa.save_state_repair import relocate_stray_npcs_from_populated_locations

        moved = relocate_stray_npcs_from_populated_locations(db_path)
        assert moved == 1

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        extra = conn.execute("SELECT current_location_id FROM npcs WHERE name = 'Archivist Extra'").fetchone()
        lady = conn.execute("SELECT current_location_id FROM npcs WHERE name = 'Lady Veyra'").fetchone()
        conn.close()
        assert int(extra["current_location_id"]) == grid_id
        assert int(lady["current_location_id"]) == residence_id


def test_sync_location_state_npcs_respects_manifest_present_only():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Manifest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid_location(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at, notes) "
            "VALUES ('1_residence', 'Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01', ?)",
            (
                grid_id,
                json.dumps(
                    {
                        "population_applied": True,
                        "spawned_present": ["Lady Veyra"],
                        "spawned_hidden": ["Spy"],
                    }
                ),
            ),
        )
        residence_id = int(conn.execute("SELECT id FROM locations WHERE code = '1_residence'").fetchone()[0])
        npc_generator.spawn_npc(conn, name="Lady Veyra", tier="T2", location_id=residence_id)
        npc_generator.spawn_npc(conn, name="Noise NPC", tier="T2", location_id=residence_id)
        npc_generator.spawn_npc(conn, name="Spy", tier="T2", location_id=residence_id)
        conn.commit()
        conn.close()

        from titan.fugassa.db import state_repository

        state = {"location_state": {"name": "Residence", "location_id": residence_id}}
        state_repository.sync_location_state_npcs(db_path, state, residence_id)
        loc = state["location_state"]
        assert loc["npcs"] == ["Lady Veyra"]
        assert loc["hidden_npcs"] == ["Spy"]
        assert "Noise NPC" not in (loc.get("npcs") or [])
        assert "Noise NPC" not in (loc.get("hidden_npcs") or [])

        loc["narrative_npcs"] = ["Bram Alden"]
        loc["npcs"] = ["Lady Veyra", "Bram Alden"]
        state["location_state"] = loc
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO npcs (code, name, current_location_id, status, created_at, updated_at) "
            "VALUES ('bram_alden', 'Bram Alden', ?, 'alive', '2026-01-01', '2026-01-01')",
            (residence_id,),
        )
        conn.commit()
        conn.close()
        state_repository.sync_location_state_npcs(db_path, state, residence_id)
        loc = state["location_state"]
        assert "Bram Alden" in loc["npcs"]


def test_gm_context_includes_hidden_npcs_as_concealed():
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "sublocation_id": 2},
        "location_state": {
            "name": "Residence",
            "description": "Inside.",
            "location_id": 2,
            "npcs": ["Lady Veyra"],
            "hidden_npcs": ["Silas Thorn"],
        },
        "party": [],
        "inventory": {},
        "world_profile": {},
        "world_time": {"day": 1, "hour": 10},
    }
    prompt = gm_runner.build_system_prompt(state)
    assert "npcs (visible): ['Lady Veyra']" in prompt
    assert "hidden_npcs (concealed" in prompt
    assert "Silas Thorn" in prompt

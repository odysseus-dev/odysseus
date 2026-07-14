"""Layer 3 — archivist create npc promoted to scene cast when GM prose uses them."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

from titan.fugassa import archivist
from titan.fugassa.db import sqlite_store, state_repository
from titan.fugassa.turn_resolution import TurnResolution


def _insert_grid(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at)
        VALUES ('grid', 'Town Square', 'Square.', NULL, 1, '2026-01-01', '2026-01-01')
        """
    )
    return int(conn.execute("SELECT id FROM locations WHERE code = 'grid'").fetchone()[0])


def test_promote_narrative_npc_when_mentioned_in_gm_prose():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Narr", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at, notes) "
            "VALUES ('res', 'Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01', ?)",
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
        res_id = int(conn.execute("SELECT id FROM locations WHERE code = 'res'").fetchone()[0])
        conn.execute(
            "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Hero', ?)",
            (res_id,),
        )
        conn.commit()
        conn.close()

        state = {
            "location_state": {
                "location_id": res_id,
                "name": "Residence",
                "npcs": ["Lady Veyra"],
                "hidden_npcs": [],
            }
        }
        gm_prose = "A clerk named Bram Alden steps forward and bows."
        ops = [
            {
                "op": "create",
                "entity": "npc",
                "name": "Bram Alden",
                "race": "human",
                "role": "clerk",
                "is_hostile": False,
            }
        ]
        result = archivist.apply_ops(db_path, ops, state)
        assert result["applied"] == 1
        promoted = state_repository.promote_narrative_npcs_to_scene(
            db_path, state, gm_prose=gm_prose, npc_names=result["created_npc_names"]
        )
        assert promoted == ["Bram Alden"]
        assert "Bram Alden" in state["location_state"]["npcs"]
        assert "Bram Alden" in state["location_state"]["narrative_npcs"]

        state_repository.sync_location_state_npcs(db_path, state, res_id)
        assert "Bram Alden" in state["location_state"]["npcs"]


def test_promote_skipped_when_npc_not_in_gm_prose():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Skip", theme="fantasy")
        conn = sqlite3.connect(db_path)
        grid_id = _insert_grid(conn)
        conn.execute(
            "INSERT INTO locations (code, name, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('res', 'Residence', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        res_id = int(conn.execute("SELECT id FROM locations WHERE code = 'res'").fetchone()[0])
        conn.execute(
            "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Hero', ?)",
            (res_id,),
        )
        conn.commit()
        conn.close()

        state = {"location_state": {"location_id": res_id, "name": "Residence", "npcs": []}}
        ops = [{"op": "create", "entity": "npc", "name": "Ghost NPC", "is_hostile": False}]
        result = archivist.apply_ops(db_path, ops, state)
        promoted = state_repository.promote_narrative_npcs_to_scene(
            db_path,
            state,
            gm_prose="You look around the empty room.",
            npc_names=result["created_npc_names"],
        )
        assert promoted == []
        assert "Ghost NPC" not in (state["location_state"].get("npcs") or [])


def test_run_llm_patch_promotes_created_npc():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Patch", theme="fantasy")
        conn = sqlite3.connect(db_path)
        grid_id = _insert_grid(conn)
        conn.execute(
            "INSERT INTO locations (code, name, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('res', 'Residence', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        res_id = int(conn.execute("SELECT id FROM locations WHERE code = 'res'").fetchone()[0])
        conn.execute(
            "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Hero', ?)",
            (res_id,),
        )
        conn.commit()
        conn.close()

        state = {"location_state": {"location_id": res_id, "name": "Residence", "npcs": []}}
        resolution = TurnResolution(mode="action", intent="narrative_only")
        gm_prose = "Mira Solari emerges from the shadows."
        raw = json.dumps(
            {
                "ops": [
                    {
                        "op": "create",
                        "entity": "npc",
                        "name": "Mira Solari",
                        "race": "elf",
                        "role": "scout",
                        "is_hostile": False,
                    }
                ]
            }
        )
        ops = archivist.parse_patch_ops(raw)
        valid = archivist.validate_ops(db_path, ops, resolution)
        op_result = archivist.apply_ops(db_path, valid, state)
        promoted = state_repository.promote_narrative_npcs_to_scene(
            db_path, state, gm_prose=gm_prose, npc_names=op_result["created_npc_names"]
        )
        assert promoted == ["Mira Solari"]
        assert any(d.get("name") == "Mira Solari" for d in state["location_state"].get("npc_details") or [])

"""Tests for world_time_engine and narrative_movement."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from titan.fugassa import narrative_movement, world_time_engine
from titan.fugassa.db import sqlite_store
from titan.fugassa.turn_resolver import classify_intent, resolve_turn


def _insert_grid_location(conn: sqlite3.Connection, *, code: str = "grid_square", name: str = "Town Square") -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at)
        VALUES (?, ?, 'Outdoor square.', NULL, 1, '2026-01-01', '2026-01-01')
        """,
        (code, name),
    )
    return int(conn.execute("SELECT id FROM locations WHERE code = ?", (code,)).fetchone()[0])


def test_split_era_year_comma_format():
    out: dict = {}
    from titan.fugassa.gm_response_parser import _split_era_year

    _split_era_year("Era of Conquest, 2566, November, 12", out)
    assert out["era"] == "Era of Conquest"
    assert out["year"] == "2566"
    assert out["month"] == "November"
    assert out["day"] == 12


def test_apply_time_delta_updates_hhmm():
    state = {"world_time": {"day": 12, "hour": 10, "minute": 45, "hhmm": "10:45 AM"}}
    world_time_engine.apply_time_delta(state, 30)
    wt = state["world_time"]
    assert wt["hour"] == 11
    assert wt["minute"] == 15
    assert "11:15 AM" in wt["hhmm"]


def test_format_world_time_no_duplicate_clock():
    label = world_time_engine.format_world_time_label(
        {"time_of_day": "Late Morning", "hhmm": "10:45 AM", "hour": 10, "minute": 45}
    )
    assert label.count("10:45") == 1
    assert "Late Morning" in label


def test_classify_narrative_travel():
    assert classify_intent("Visit the concubine's residence") == "narrative_travel"
    assert classify_intent("Make your way toward the market") == "narrative_travel"
    assert classify_intent("I check my inventory") == "narrative_only"


def test_narrative_travel_enters_embedded_sublocation():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE locations SET description_short = ? WHERE id = 1",
            (
                "The square contains the concubine's residence — a corner building with arched windows.",
            ),
        )
        conn.commit()
        conn.close()

        state = {
            "player": {"x": 0, "y": 0, "z": 0, "map_code": "overworld"},
            "location_state": {
                "location_id": 1,
                "name": "Town Square",
                "description": "The square contains the concubine's residence — a corner building.",
                "npcs": [],
            },
            "_current_location_id": 1,
            "turn": 3,
        }
        resolution = resolve_turn(
            state,
            "Visit the concubine's residence to observe her.",
            db_path=db_path,
        )
        assert resolution.intent == "narrative_travel"
        assert state["player"].get("sublocation_id")
        assert "residence" in (state.get("location_state") or {}).get("name", "").lower()


def test_discover_embedded_places():
    desc = "The Market District contains the concubine's residence—a corner building."
    places = narrative_movement.discover_embedded_places(desc)
    assert any("residence" in p.lower() for p in places)


def test_format_parent_area():
    assert narrative_movement.format_parent_area("City Town Square — Market District") == "Market District"


def test_enrich_location_context_marks_sublocation():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('sub', 'Concubine Residence', 'Inside.', 1, 1, '2026-01-01', '2026-01-01')"
        )
        sub_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "UPDATE locations SET name = ? WHERE id = 1",
            ("City Town Square — Market District",),
        )
        conn.commit()
        conn.close()

        loc = narrative_movement.enrich_location_context(
            db_path,
            {"name": "Concubine Residence", "location_id": sub_id},
            location_id=sub_id,
        )
        assert loc["is_sublocation"] is True
        assert loc["parent_location_id"] == 1
        assert loc["parent_area"] == "Market District"


def test_ensure_sublocation_backfills_connection_for_existing_row():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('1_residence', 'Concubine Residence', 'Inside.', 1, 1, '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        loc_id = narrative_movement.ensure_sublocation(
            db_path,
            parent_location_id=1,
            name="Concubine Residence",
            parent_description="Market district prose.",
        )
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT connection_type FROM location_connections WHERE from_location_id = 1 AND to_location_id = ?",
            (loc_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "contains"


def test_ensure_sublocation_flattens_nested_parent_to_grid_level():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        grid_id = _insert_grid_location(conn, code="grid", name="Town Square")
        conn.commit()
        conn.close()

        sub_id = narrative_movement.ensure_sublocation(
            db_path,
            parent_location_id=grid_id,
            name="Concubine Residence",
            parent_description="Market district prose.",
        )
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('2_nested', 'Nested Room', 'Deeper.', ?, 1, '2026-01-01', '2026-01-01')",
            (sub_id,),
        )
        conn.commit()
        conn.close()

        loc_id = narrative_movement.ensure_sublocation(
            db_path,
            parent_location_id=sub_id,
            name="Concubine Residence",
            parent_description="Market district prose.",
        )
        assert loc_id == sub_id


def test_enter_sublocation_refreshes_npcs_from_sql():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        grid_id = _insert_grid_location(conn)
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('1_residence', 'Concubine Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        sub_id = int(conn.execute("SELECT id FROM locations WHERE code = '1_residence'").fetchone()[0])
        conn.execute(
            "INSERT INTO npcs (code, name, current_location_id, status, created_at, updated_at) "
            "VALUES ('lady_veyra', 'Lady Veyra', ?, 'alive', '2026-01-01', '2026-01-01')",
            (sub_id,),
        )
        conn.commit()
        conn.close()

        state = {
            "player": {"x": 0, "y": 0, "z": 0, "map_code": "overworld"},
            "location_state": {
                "location_id": 1,
                "name": "Town Square",
                "description": "Busy square.",
                "npcs": ["Town Guard"],
                "hidden_npcs": ["Pickpocket"],
            },
        }
        narrative_movement.enter_sublocation(db_path, state, sub_id, label="Concubine Residence")
        loc = state["location_state"]
        assert loc["npcs"] == ["Lady Veyra"]
        assert loc.get("hidden_npcs") in ([], None)
        assert "Town Guard" not in (loc.get("npcs") or [])


def test_narrative_exit_sublocation_to_parent_market():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        grid_id = _insert_grid_location(conn, code="market_grid", name="City Town Square — Market District")
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('residence', 'Concubine Residence', 'Inside the residence.', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        sub_id = int(conn.execute("SELECT id FROM locations WHERE code = 'residence'").fetchone()[0])
        conn.commit()
        conn.close()

        state = {
            "player": {
                "x": 0,
                "y": 0,
                "z": 0,
                "map_code": "overworld",
                "sublocation_id": sub_id,
                "sublocation_anchor": {"map_code": "overworld", "x": 0, "y": 0, "z": 0},
            },
            "location_state": {
                "location_id": sub_id,
                "name": "Concubine Residence",
                "description": "Interior.",
                "npcs": [],
                "parent_location_id": grid_id,
                "grid_location_id": grid_id,
                "parent_name": "City Town Square — Market District",
                "parent_area": "Market District",
                "is_sublocation": True,
            },
            "_current_location_id": sub_id,
            "cell_location_cache": {
                "0,0,0,overworld": {
                    "name": "City Town Square — Market District",
                    "description": "Busy market square with kiosks.",
                }
            },
            "turn": 5,
        }
        result = narrative_movement.resolve_narrative_travel(
            db_path,
            state,
            "GO to market district and find kiosk",
        )
        assert result and result.get("success")
        assert not state["player"].get("sublocation_id")
        assert "market" in (state.get("location_state") or {}).get("name", "").lower()


def test_sync_post_gm_movement_prioritizes_player_intent_over_stuck_timestamp():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Test", theme="fantasy")
        conn = sqlite3.connect(db_path)
        grid_id = _insert_grid_location(conn, code="market_grid", name="City Town Square — Market District")
        conn.execute(
            "INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at) "
            "VALUES ('residence', 'Concubine Residence', 'Inside.', ?, 1, '2026-01-01', '2026-01-01')",
            (grid_id,),
        )
        sub_id = int(conn.execute("SELECT id FROM locations WHERE code = 'residence'").fetchone()[0])
        conn.commit()
        conn.close()

        state = {
            "player": {
                "x": 0,
                "y": 0,
                "z": 0,
                "map_code": "overworld",
                "sublocation_id": sub_id,
                "sublocation_anchor": {"map_code": "overworld", "x": 0, "y": 0, "z": 0},
            },
            "location_state": {
                "location_id": sub_id,
                "name": "Concubine Residence",
                "description": "Interior.",
                "npcs": [],
                "parent_location_id": grid_id,
                "grid_location_id": grid_id,
                "parent_name": "City Town Square — Market District",
                "parent_area": "Market District",
                "is_sublocation": True,
            },
            "_current_location_id": sub_id,
            "cell_location_cache": {
                "0,0,0,overworld": {
                    "name": "City Town Square — Market District",
                    "description": "Busy market square.",
                }
            },
            "turn": 5,
        }
        moved = narrative_movement.sync_post_gm_movement(
            db_path,
            state,
            gm_prose="Lucas walks through the Market District toward a kiosk.",
            gm_location="Concubine Residence",
            player_text="GO to market district",
        )
        assert moved and moved.get("success")
        assert not state["player"].get("sublocation_id")

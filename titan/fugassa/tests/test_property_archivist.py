"""Integration tests — archivist property ops, visit, debug snapshot."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import archivist, debug_snapshot, property_validator
from titan.fugassa.db import sqlite_store
from titan.fugassa.property_repository import list_holdings_conn, set_active_residence
from titan.fugassa import narrative_movement


def _seed_hero(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
        VALUES ('town', 'Town Square', 'A square.', 1, '2026-01-01', '2026-01-01')
        """
    )
    loc_id = int(conn.execute("SELECT id FROM locations WHERE code = 'town'").fetchone()[0])
    conn.execute(
        "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Lucas', ?)",
        (loc_id,),
    )
    return loc_id


def test_validator_rejects_property_without_deed():
    err = property_validator.validate_archivist_property_op(
        {"name": "House", "property_kind": "townhouse", "deed_summary": "short"}
    )
    assert err


def test_archivist_create_property_and_room():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "PropTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        conn.commit()
        conn.close()

        state = {"turn": 3, "player": {"x": 0, "y": 0, "z": 0}, "location_state": {"name": "Town Square"}}
        ops = [
            {
                "op": "create",
                "entity": "property",
                "name": "House Driscoll — City Residence",
                "property_kind": "townhouse",
                "root_location_name": "Driscoll Townhouse",
                "acquired_via": "inheritance",
                "deed_summary": "Lucas inherited the family townhouse in Crownstone quarter.",
                "specs": {"prestige": 2, "bedrooms": 3},
            },
            {
                "op": "create",
                "entity": "property_room",
                "property_name": "House Driscoll — City Residence",
                "room_name": "Study",
                "description": "Oak shelves and a locked ledger cabinet.",
            },
        ]
        result = archivist.apply_ops(db_path, ops, state)
        assert result["applied"] == 2
        assert state.get("property_portfolio")
        assert state["property_portfolio"]["holdings"][0]["property_kind"] == "townhouse"

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        holdings = list_holdings_conn(conn)
        assert len(holdings) == 1
        rooms = conn.execute(
            "SELECT name FROM locations WHERE parent_location_id = ?",
            (holdings[0]["root_location_id"],),
        ).fetchall()
        conn.close()
        assert any(r["name"] == "Study" for r in rooms)


def test_debug_snapshot_includes_property_holdings():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "DebugProp", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        conn.commit()
        conn.close()

        state = {"turn": 1}
        archivist.apply_ops(
            db_path,
            [
                {
                    "op": "create",
                    "entity": "property",
                    "name": "Modest Flat",
                    "property_kind": "apartment",
                    "deed_summary": "A rented flat above the chandler's shop.",
                }
            ],
            state,
        )

        snap = debug_snapshot.build_debug_snapshot(db_path, "DebugProp")
        assert len(snap["property_holdings"]) == 1
        assert snap["property_holdings"][0]["sql_row"]
        assert "rooms" in snap["property_holdings"][0]
        assert "fixtures" in snap["property_holdings"][0]


def test_set_active_residence_and_visit():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "VisitProp", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        conn.commit()
        conn.close()

        state = {
            "turn": 1,
            "party": [{"name": "Lucas", "hp": 20, "max_hp": 20, "ac": 12}],
            "player": {"x": 0, "y": 0, "z": 0, "map_code": "default"},
            "location_state": {"name": "Town Square", "description": "Busy square."},
        }
        archivist.apply_ops(
            db_path,
            [
                {
                    "op": "create",
                    "entity": "property",
                    "name": "Lucas Cottage",
                    "property_kind": "cottage",
                    "deed_summary": "A small cottage at the edge of town.",
                }
            ],
            state,
        )
        code = state["property_portfolio"]["holdings"][0]["code"]
        root_id = int(state["property_portfolio"]["holdings"][0]["root_location_id"])
        assert set_active_residence(state, code)
        assert state["property_portfolio"]["active_residence_code"] == code

        visit = narrative_movement.enter_sublocation(db_path, state, root_id, label="Lucas Cottage")
        assert visit.get("success")
        assert state["player"].get("sublocation_id") == root_id


def test_archivist_create_fixture_and_assign_staff():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "FixtureTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        conn.execute(
            """
            INSERT INTO npcs (code, name, status, current_location_id, created_at, updated_at)
            VALUES ('old_marta', 'Old Marta', 'alive', (SELECT id FROM locations WHERE code='town'), '2026-01-01', '2026-01-01')
            """
        )
        conn.commit()
        conn.close()

        state = {"turn": 2, "player": {"x": 0, "y": 0, "z": 0}, "location_state": {"name": "Town Square"}}
        archivist.apply_ops(
            db_path,
            [
                {
                    "op": "create",
                    "entity": "property",
                    "name": "House Driscoll — City Residence",
                    "property_kind": "townhouse",
                    "deed_summary": "Lucas inherited the family townhouse in Crownstone quarter.",
                },
                {
                    "op": "create",
                    "entity": "property_room",
                    "property_name": "House Driscoll — City Residence",
                    "room_name": "Study",
                    "description": "Oak shelves and a locked ledger cabinet.",
                },
                {
                    "op": "create",
                    "entity": "property_fixture",
                    "property_name": "House Driscoll — City Residence",
                    "room_name": "Study",
                    "name": "Family Ledgers Cabinet",
                    "fixture_kind": "storage",
                    "description": "Locked oak cabinet holding House Driscoll ledgers.",
                },
                {
                    "op": "assign",
                    "entity": "property_staff",
                    "property_name": "House Driscoll — City Residence",
                    "npc_name": "Old Marta",
                    "role": "steward",
                },
            ],
            state,
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        fixtures = conn.execute("SELECT name FROM property_fixtures").fetchall()
        staff = conn.execute(
            "SELECT name, assigned_role FROM npcs WHERE assigned_property_id IS NOT NULL"
        ).fetchall()
        conn.close()
        assert any(f["name"] == "Family Ledgers Cabinet" for f in fixtures)
        assert any(s["name"] == "Old Marta" and s["assigned_role"] == "steward" for s in staff)
        assert state["property_portfolio"]["holdings"][0].get("staff_names")


def test_go_home_intent_resolves_to_active_residence():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "GoHome", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        conn.commit()
        conn.close()

        state = {
            "turn": 1,
            "party": [{"name": "Lucas", "hp": 20, "max_hp": 20, "ac": 12}],
            "player": {"x": 0, "y": 0, "z": 0, "map_code": "default"},
            "location_state": {"name": "Town Square", "description": "Busy square."},
        }
        archivist.apply_ops(
            db_path,
            [
                {
                    "op": "create",
                    "entity": "property",
                    "name": "Lucas Cottage",
                    "code": "lucas_cottage",
                    "property_kind": "cottage",
                    "deed_summary": "A small cottage at the edge of town.",
                }
            ],
            state,
        )
        from titan.fugassa.property_repository import set_active_residence

        set_active_residence(state, "lucas_cottage")
        result = narrative_movement.resolve_go_home(db_path, state, "I go home")
        assert result and result.get("success")
        assert result.get("intent") == "go_home"
        assert state["location_state"].get("property_code") == "lucas_cottage"


def test_dedupe_spurious_driscoll_holding():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "DedupeProp", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_hero(conn)
        pc_id = int(conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()[0])
        now = "2026-01-01"
        conn.execute(
            """
            INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
            VALUES ('driscoll_townhouse', 'Driscoll Townhouse', 'Home', 1, ?, ?)
            """,
            (now, now),
        )
        root_good = int(conn.execute("SELECT id FROM locations WHERE code='driscoll_townhouse'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO locations (code, name, description_short, parent_location_id, is_discovered, created_at, updated_at)
            VALUES ('study', 'Study', 'Study room', ?, 1, ?, ?)
            """,
            (root_good, now, now),
        )
        conn.execute(
            """
            INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
            VALUES ('crownstone', 'Crownstone', 'Settlement', 1, ?, ?)
            """,
            (now, now),
        )
        root_bad = int(conn.execute("SELECT id FROM locations WHERE code='crownstone'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO property_holdings (
                code, player_character_id, root_location_id, name, property_kind, title_status,
                acquired_via, deed_summary, specs_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'townhouse', 'owned', 'inheritance', 'Good home', '{}', ?, ?)
            """,
            ("house_driscoll_city", pc_id, root_good, "House Driscoll — City Residence", now, now),
        )
        conn.execute(
            """
            INSERT INTO property_holdings (
                code, player_character_id, root_location_id, name, property_kind, title_status,
                acquired_via, deed_summary, specs_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'townhouse', 'owned', 'narrative', 'Bad duplicate', '{}', ?, ?)
            """,
            ("house_driscoll_s_townhouse", pc_id, root_bad, "House Driscoll's Townhouse", now, now),
        )
        conn.commit()
        conn.close()

        state = {"property_portfolio": {"active_residence_code": "house_driscoll_s_townhouse", "holdings": []}}
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from titan.fugassa.property_repository import dedupe_spurious_holdings_conn

        removed = dedupe_spurious_holdings_conn(conn, state)
        conn.commit()
        remaining = conn.execute("SELECT code FROM property_holdings ORDER BY id").fetchall()
        conn.close()
        assert removed == ["house_driscoll_s_townhouse"]
        assert [r[0] for r in remaining] == ["house_driscoll_city"]
        assert state["property_portfolio"]["active_residence_code"] == "house_driscoll_city"

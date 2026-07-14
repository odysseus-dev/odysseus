"""A wizard opening whose "Current Location" cell is "Parent (Sub)" (e.g.
"Oakhaven Reach (Lucas's quarters)", see
`game_bootstrap.starting_location_from_opening`) must start the game
*mechanically* inside the sublocation graph — not just show that text as a
flat label on the overworld grid cell. Without this, `leave_sublocation`/
`move_sublocation` and any location-scoped tracking (e.g. Investigate) can't
tell the difference between "standing outside" and "inside the room".
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.db import seed as db_seed
from titan.fugassa.db import sqlite_store


def make_db_path() -> str:
    d = tempfile.mkdtemp(prefix="fugassa_seed_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Seed Campaign", theme="fantasy")
    return db_path


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _base_draft(**overrides):
    draft = {"player_name": "Lucas", "level": 1, "abilities": {}}
    draft.update(overrides)
    return draft


def _base_state(location_name: str, location_desc: str = "") -> dict:
    return {
        "player": {"x": 0, "y": 0, "z": 0},
        "location_state": {"name": location_name, "description": location_desc, "npcs": []},
        "party": [{"hp": 20, "max_hp": 20, "ac": 12}],
        "quests": {"active": []},
    }


def test_parent_sub_name_creates_two_locations_and_connection():
    db_path = make_db_path()
    state = _base_state("Oakhaven Reach (Lucas's quarters)", "A cramped room above the tavern.")
    result = db_seed.bootstrap_from_wizard(db_path, draft=_base_draft(), state=state)

    assert result["sublocation_id"] is not None
    assert result["location_id"] == result["sublocation_id"]
    assert result["grid_location_id"] != result["sublocation_id"]

    conn = _connect(db_path)
    parent = conn.execute("SELECT * FROM locations WHERE id = ?", (result["grid_location_id"],)).fetchone()
    sub = conn.execute("SELECT * FROM locations WHERE id = ?", (result["sublocation_id"],)).fetchone()
    assert parent["name"] == "Oakhaven Reach"
    assert sub["name"] == "Oakhaven Reach (Lucas's quarters)"
    assert sub["parent_location_id"] == parent["id"]

    conn_row = conn.execute(
        "SELECT * FROM location_connections WHERE from_location_id = ? AND to_location_id = ?",
        (parent["id"], sub["id"]),
    ).fetchone()
    assert conn_row is not None
    assert conn_row["connection_type"] == "contains"


def test_parent_sub_name_binds_grid_cell_to_parent_not_sublocation():
    db_path = make_db_path()
    state = _base_state("Harbor Town (Captain's cabin)")
    result = db_seed.bootstrap_from_wizard(db_path, draft=_base_draft(), state=state)

    conn = _connect(db_path)
    cell = conn.execute(
        "SELECT * FROM grid_cells WHERE map_code = 'overworld' AND x = 0 AND y = 0 AND z = 0"
    ).fetchone()
    assert cell["location_id"] == result["grid_location_id"]
    assert cell["location_id"] != result["sublocation_id"]


def test_parent_sub_name_sets_pc_current_location_to_sublocation():
    db_path = make_db_path()
    state = _base_state("Harbor Town (Captain's cabin)")
    result = db_seed.bootstrap_from_wizard(db_path, draft=_base_draft(), state=state)

    conn = _connect(db_path)
    pc = conn.execute("SELECT current_location_id FROM player_characters WHERE code = 'pc_hero'").fetchone()
    assert pc["current_location_id"] == result["sublocation_id"]


def test_plain_location_name_without_parens_keeps_single_location_behavior():
    db_path = make_db_path()
    state = _base_state("Starter Crossroads")
    result = db_seed.bootstrap_from_wizard(db_path, draft=_base_draft(), state=state)

    assert result["sublocation_id"] is None
    assert result["location_id"] == result["grid_location_id"]

    conn = _connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    assert count == 1


def test_save_store_mirrors_sublocation_id_onto_game_json_state():
    from titan.fugassa import save_store
    from titan.fugassa.game_bootstrap import read_game_json

    world_name = f"SublocTest_{os.getpid()}_{abs(hash(tempfile.mkdtemp()))}"
    draft = {
        "world_name": world_name,
        "theme_mode": "Fantasy",
        "player_name": "Lucas",
        "level": 1,
        "opening_time_hint": (
            "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | "
            "Current Location | Season | Weather |\n"
            "|---|---|---|---|---|---|---|\n"
            "| Morning | 08:00 AM | Year 1 | Full | Oakhaven Reach (Lucas's quarters) | Spring | Clear |"
        ),
        "opening_hook": "You wake up.",
    }
    save_id = save_store.normalize_save_name(world_name)
    try:
        save_store.create_save_from_wizard(draft)
        game_json = read_game_json(save_store.save_dir(save_id))
        assert game_json is not None
        assert game_json["player"].get("sublocation_id")
        assert game_json["player"].get("sublocation_anchor") == {"map_code": "overworld", "x": 0, "y": 0, "z": 0}
        assert game_json["location_state"]["location_id"] == game_json["player"]["sublocation_id"]
    finally:
        try:
            save_store.delete_save(save_id)
        except Exception:
            pass


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

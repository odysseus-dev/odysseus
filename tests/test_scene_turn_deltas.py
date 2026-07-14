"""Scene turn delta field — Sprint 2 G4."""

import os
import sqlite3
import tempfile

import pytest

from titan.fugassa import memory_context, scene_summary_engine
from titan.fugassa.db import sqlite_store


@pytest.fixture
def db_path():
    d = tempfile.mkdtemp(prefix="fugassa_delta_")
    path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(path, "Delta Test", theme="fantasy")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO locations (code, name) VALUES ('loc_market', 'Market District')")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return path, int(loc_id)


def test_compose_turn_delta_prefers_player_action():
    delta = scene_summary_engine.compose_turn_delta(
        "Present the letter to the auctioneer.",
        "Harven reads it carefully.",
        turn_number=12,
    )
    assert "Present the letter" in delta
    assert "Harven" not in delta


def test_record_turn_delta_persists(db_path):
    path, loc_id = db_path
    written = scene_summary_engine.record_turn_delta(
        path,
        location_id=loc_id,
        turn_number=5,
        player_text="GO to the market kiosk.",
        gm_prose="Lucas walks through the crowd.",
    )
    assert written
    rows = scene_summary_engine.latest_turn_deltas_for_location(path, loc_id)
    assert len(rows) == 1
    assert rows[0]["turn_number"] == 5


def test_build_scene_summary_block_includes_visit_deltas(db_path):
    path, loc_id = db_path
    scene_summary_engine.record_turn_delta(
        path,
        location_id=loc_id,
        turn_number=2,
        player_text="Ask about Elara's price.",
        gm_prose="The auctioneer quotes two hundred sovereigns.",
    )
    state = {
        "location_state": {"location_id": loc_id},
        "_location_entry_turn": {str(loc_id): 1},
    }
    block = memory_context.build_scene_summary_block(path, state)
    assert "WHAT CHANGED THIS VISIT" in block
    assert "Turn 2:" in block
    assert "Elara" in block


def test_location_exit_stores_delta_text(db_path):
    path, loc_id = db_path
    scene_summary_engine.record_turn_delta(
        path,
        location_id=loc_id,
        turn_number=3,
        player_text="Confirm the debt is owed to House Driscoll.",
        gm_prose="Harven agrees.",
    )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO event_log (code, event_type, title, summary, turn_id, location_id) "
        "VALUES ('ev3', 'turn', 'Turn 3', 'Turn 3: Confirm the debt', 3, ?)",
        (loc_id,),
    )
    row_id = scene_summary_engine.generate_on_location_exit_conn(
        conn, from_location_id=loc_id, turn_start=1, turn_end=3
    )
    conn.commit()
    row = conn.execute(
        "SELECT delta_text, summary_text FROM scene_summaries WHERE id = ?",
        (row_id,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert "House Driscoll" in str(row["delta_text"])
    assert "Market District" in str(row["summary_text"])

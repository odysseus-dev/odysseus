"""ADR C7 — engine_only chronicle emits."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

from titan.fugassa import campaign_chronicle
from titan.fugassa.db import sqlite_store


def _init_db() -> str:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "game.db")
    sqlite_store.init_game_db(db_path, "EngineChronicle", theme="fantasy")
    return db_path


def test_travel_event_recorded():
    db_path = _init_db()
    ev = campaign_chronicle.make_travel_event(
        hero_name="Lucas",
        from_label="Market",
        to_label="Harbor",
        turn_id=5,
        location_id=None,
        mode="walk",
    )
    ids = campaign_chronicle.record_events(db_path, [ev])
    assert ids
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT event_type, summary FROM event_log WHERE id = ?",
            (ids[0],),
        ).fetchone()
        assert row["event_type"] == "travel"
        assert "Harbor" in row["summary"]
    finally:
        conn.close()


def test_discovery_and_inventory_events():
    db_path = _init_db()
    events = [
        campaign_chronicle.make_discovery_event(
            location_name="Cellar",
            summary="Hidden lever behind the barrel.",
            turn_id=8,
            location_id=None,
        ),
        campaign_chronicle.make_inventory_change_event(
            hero_name="Lucas",
            item_summary="Gold coin×3",
            turn_id=8,
            location_id=None,
            action="picked up",
        ),
    ]
    ids = campaign_chronicle.record_events(db_path, events)
    assert len(ids) == 2
    conn = sqlite3.connect(db_path)
    try:
        types = [r[0] for r in conn.execute("SELECT event_type FROM event_log ORDER BY id").fetchall()]
        assert types == ["discovery", "inventory_change"]
    finally:
        conn.close()


def test_purge_events_after_turn():
    db_path = _init_db()
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_travel_event(
                hero_name="Lucas",
                from_label="A",
                to_label="B",
                turn_id=10,
                location_id=None,
            ),
            campaign_chronicle.make_travel_event(
                hero_name="Lucas",
                from_label="B",
                to_label="C",
                turn_id=11,
                location_id=None,
                mode="walk",
            ),
        ],
    )
    stats = campaign_chronicle.purge_events_after_turn(db_path, 10)
    assert stats["events_deactivated"] >= 1
    conn = sqlite3.connect(db_path)
    try:
        active = conn.execute(
            "SELECT turn_id FROM event_log WHERE is_active = 1 ORDER BY turn_id"
        ).fetchall()
        assert [int(r[0]) for r in active] == [10]
    finally:
        conn.close()


def test_purge_events_after_turn_removes_vec_index_rows():
    db_path = _init_db()
    ids = campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_travel_event(
                hero_name="Lucas",
                from_label="A",
                to_label="B",
                turn_id=10,
                location_id=None,
            ),
            campaign_chronicle.make_travel_event(
                hero_name="Lucas",
                from_label="B",
                to_label="C",
                turn_id=11,
                location_id=None,
                mode="walk",
            ),
        ],
    )
    removed: list[int] = []

    def _track_remove(path: str, kind: str, row_id: int) -> None:
        removed.append(int(row_id))

    with patch("titan.fugassa.campaign_chronicle.vec_index.remove", side_effect=_track_remove):
        stats = campaign_chronicle.purge_events_after_turn(db_path, 10)

    assert stats["events_deactivated"] >= 1
    assert stats["vec_removed"] == len(removed)
    assert int(ids[1]) in removed
    assert int(ids[0]) not in removed


def test_level_up_event_recorded():
    db_path = _init_db()
    ev = campaign_chronicle.make_level_up_event(
        hero_name="Lucas",
        from_level=1,
        to_level=2,
        turn_id=4,
        location_id=None,
    )
    ids = campaign_chronicle.record_events(db_path, [ev])
    assert ids
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT event_type, summary FROM event_log WHERE id = ?",
            (ids[0],),
        ).fetchone()
        assert row["event_type"] == "level_up"
        assert "level 2" in row["summary"].lower()
    finally:
        conn.close()


def test_engine_only_visit_grid_completes_quest_and_chronicle():
    from titan.fugassa.turn_resolution import TurnResolution
    from titan.fugassa.turn_resolver import run_engine_only_checks

    db_path = _init_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Lucas')")
        conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Lucas')")
        conn.execute(
            "INSERT INTO quests (code, title, description, status, created_at, updated_at) VALUES (?, ?, ?, 'active', '2026-01-01', '2026-01-01')",
            ("scout_tile", "Scout the marker", "Reach the marker"),
        )
        quest_id = conn.execute("SELECT id FROM quests WHERE code = 'scout_tile'").fetchone()[0]
        conn.execute(
            """
            INSERT INTO quest_objectives (
                quest_id, sort_order, objective_type, condition_json, description_text, status, optional, completion_mode, created_at
            ) VALUES (?, 0, 'visit_grid_cell', ?, 'Reach the marker', 'pending', 0, 'auto', '2026-01-01')
            """,
            (quest_id, '{"x": 2, "y": 3, "z": 0}'),
        )
        conn.commit()
    finally:
        conn.close()

    state = {
        "player": {"x": 2, "y": 3, "z": 0},
        "party": [{"name": "Lucas", "role": "player"}],
        "location_state": {"name": "Wilds"},
        "turn": 7,
        "quests": {"active": [], "closed": []},
    }
    resolution = TurnResolution(mode="engine_only", intent="engine_only")
    run_engine_only_checks(db_path, state, resolution=resolution)
    assert resolution.quest
    assert "Scout the marker" in (resolution.quest.get("quests_completed") or [])
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        quest_row = conn.execute("SELECT status FROM quests WHERE code = 'scout_tile'").fetchone()
        chronicle_row = conn.execute(
            "SELECT event_type, summary FROM event_log WHERE event_type = 'quest_complete' LIMIT 1"
        ).fetchone()
        assert quest_row["status"] == "completed"
        assert chronicle_row is not None
        assert "Scout" in chronicle_row["summary"]
    finally:
        conn.close()

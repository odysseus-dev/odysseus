"""Unit tests — campaign_chronicle typed events, pin rules, quest hooks."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import archivist, campaign_chronicle, campaign_facts, quest_engine
from titan.fugassa.db import sqlite_store
from titan.fugassa.turn_resolution import TurnResolution


def _seed_hero(conn: sqlite3.Connection, *, loc_code: str = "town") -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
        VALUES (?, 'Town Square', 'A square.', 1, '2026-01-01', '2026-01-01')
        """,
        (loc_code,),
    )
    loc_id = int(conn.execute("SELECT id FROM locations WHERE code = ?", (loc_code,)).fetchone()[0])
    conn.execute(
        "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Lucas', ?)",
        (loc_id,),
    )
    return loc_id


def test_record_events_dedupes_by_code():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        ev = campaign_chronicle.ChronicleEvent(
            event_type="quest_progress",
            title="Test",
            summary="Lucas explored the market.",
            turn_id=2,
            code="quest_progress_test_t2",
            source="engine",
        )
        ids1 = campaign_chronicle.record_events(db_path, [ev])
        ids2 = campaign_chronicle.record_events(db_path, [ev])
        assert len(ids1) == 1
        assert ids2 == ids1
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM event_log WHERE code = ?", (ev.code,)).fetchone()[0]
        conn.close()
        assert count == 1


def test_quest_complete_major_pins_fact():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        loc_id = _seed_hero(conn)
        quest_cols = {r[1] for r in conn.execute("PRAGMA table_info(quests)").fetchall()}
        if "quest_scale" in quest_cols:
            conn.execute(
                """
                INSERT INTO quests (code, title, status, quest_scale, rewards_json, related_location_id, created_at, updated_at)
                VALUES ('q_major', 'The Unnamed Concubine', 'active', 'major', '{}', ?, '2026-01-01', '2026-01-01')
                """,
                (loc_id,),
            )
        else:
            conn.execute(
                """
                INSERT INTO quests (code, title, status, rewards_json, related_location_id, created_at, updated_at)
                VALUES ('q_major', 'The Unnamed Concubine', 'active', '{}', ?, '2026-01-01', '2026-01-01')
                """,
                (loc_id,),
            )
        qid = int(conn.execute("SELECT id FROM quests WHERE code = 'q_major'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO quest_objectives (quest_id, objective_type, description_text, status, sort_order, completion_mode)
            VALUES (?, 'custom', 'Finish story', 'complete', 1, 'auto')
            """,
            (qid,),
        )
        conn.commit()
        conn.close()

        state = {"turn": 5, "player": {"x": 0, "y": 0, "z": 0}, "party": [], "inventory": {"shared": []}}
        resolution = TurnResolution(mode="narrative_only", intent="talk")
        result = quest_engine.evaluate_quests(db_path, state, resolution)
        quest_engine.record_quest_chronicle(db_path, result, turn_resolution=resolution)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT event_type, summary FROM event_log WHERE event_type = 'quest_complete' LIMIT 1"
        ).fetchone()
        pins = campaign_facts.list_pinned_facts(db_path)
        conn.close()
        assert row is not None
        assert row[0] == "quest_complete"
        assert "Unnamed Concubine" in row[1]
        if "quest_scale" in quest_cols:
            assert any("Unnamed Concubine" in p for p in pins)


def test_companion_join_chronicle_on_quest_reward():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        loc_id = _seed_hero(conn)
        conn.execute(
            "INSERT INTO npcs (code, name, status, current_location_id, created_at, updated_at) VALUES ('elara_voss', 'Elara Voss', 'alive', ?, '2026-01-01', '2026-01-01')",
            (loc_id,),
        )
        rewards = json.dumps({"companion": {"npc_code": "elara_voss", "role": "companion"}})
        conn.execute(
            """
            INSERT INTO quests (code, title, status, rewards_json, related_location_id, created_at, updated_at)
            VALUES ('q_comp', 'Rescue Elara', 'active', ?, ?, '2026-01-01', '2026-01-01')
            """,
            (rewards, loc_id),
        )
        qid = int(conn.execute("SELECT id FROM quests WHERE code = 'q_comp'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO quest_objectives (quest_id, objective_type, description_text, status, sort_order, completion_mode)
            VALUES (?, 'custom', 'Done', 'complete', 1, 'auto')
            """,
            (qid,),
        )
        conn.commit()
        conn.close()

        state = {"turn": 8, "player": {"x": 0, "y": 0, "z": 0}, "party": [], "inventory": {"shared": []}}
        resolution = TurnResolution(mode="narrative_only", intent="talk")
        result = quest_engine.evaluate_quests(db_path, state, resolution)
        quest_engine.record_quest_chronicle(db_path, result, turn_resolution=resolution)

        conn = sqlite3.connect(db_path)
        join = conn.execute(
            "SELECT event_type, summary FROM event_log WHERE event_type = 'companion_join' LIMIT 1"
        ).fetchone()
        conn.close()
        assert join is not None
        assert "Elara" in join[1]
        assert any(m.get("npc_code") == "elara_voss" for m in state.get("party") or [])


def test_archivist_property_emits_property_acquired():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
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
                "deed_summary": "Lucas inherited the family townhouse in Crownstone quarter.",
            },
        ]
        archivist.apply_ops(db_path, ops, state)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT event_type, code FROM event_log WHERE event_type = 'property_acquired' LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert "property_acquired" in row[1]


def test_query_recent_includes_source():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        campaign_chronicle.record_events(
            db_path,
            [
                campaign_chronicle.ChronicleEvent(
                    event_type="travel",
                    title="Travel",
                    summary="Lucas traveled north.",
                    turn_id=1,
                    source="engine",
                    code="travel_t1",
                )
            ],
        )
        recent = campaign_chronicle.query_recent(db_path, limit=5)
        assert recent
        assert recent[0]["event_type"] == "travel"
        assert recent[0]["source"] == "engine"


def test_build_embedding_debug_shape():
    info = campaign_chronicle.build_embedding_debug(None)
    assert "sqlite_vec_loaded" in info
    assert "available" in info


def test_compose_turn_summary_prefers_gm_prose():
    summary = campaign_chronicle.compose_turn_summary(
        turn_id=7,
        player_text="I talk to Elara.",
        gm_excerpt="Elara Voss signs the contract and joins House Driscoll with a quiet smile.",
        turn_resolution={"quest": {"summary": "Quest completed: The Unnamed Concubine"}},
    )
    assert "Unnamed Concubine" in summary
    assert summary.startswith("Turn 7:")


def test_compose_turn_summary_gm_before_player():
    summary = campaign_chronicle.compose_turn_summary(
        turn_id=3,
        player_text="I wave at the guard.",
        gm_excerpt="The guard nods and steps aside, allowing passage into the market.",
        turn_resolution={},
    )
    assert "guard" in summary.lower()
    assert "wave" not in summary.lower()


def test_query_by_turn_range_orders_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        campaign_chronicle.record_events(
            db_path,
            [
                campaign_chronicle.ChronicleEvent(
                    event_type="quest_progress",
                    title="Obj",
                    summary="Step one.",
                    turn_id=4,
                    code="qp_4_a",
                    source="engine",
                ),
                campaign_chronicle.ChronicleEvent(
                    event_type="quest_complete",
                    title="Done",
                    summary="Quest done.",
                    turn_id=4,
                    code="qc_4",
                    source="engine",
                ),
            ],
        )
        rows = campaign_chronicle.query_by_turn_range(db_path, 4, 4)
        assert len(rows) == 2
        assert rows[0]["event_type"] == "quest_progress"
        assert rows[1]["event_type"] == "quest_complete"


def test_quest_chronicle_events_precede_turn_event():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "ChronicleTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        loc_id = _seed_hero(conn)
        conn.execute(
            """
            INSERT INTO quests (code, title, status, rewards_json, related_location_id, created_at, updated_at)
            VALUES ('q_pipe', 'Pipeline Quest', 'active', '{}', ?, '2026-01-01', '2026-01-01')
            """,
            (loc_id,),
        )
        qid = int(conn.execute("SELECT id FROM quests WHERE code = 'q_pipe'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO quest_objectives (quest_id, objective_type, description_text, status, sort_order, completion_mode)
            VALUES (?, 'custom', 'Finish', 'complete', 1, 'auto')
            """,
            (qid,),
        )
        conn.commit()
        conn.close()

        state = {"turn": 10, "player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {"location_id": loc_id}}
        resolution = TurnResolution(mode="narrative_only", intent="talk")
        quest_result = quest_engine.evaluate_quests_after_gm(db_path, state, resolution)
        assert quest_result.get("quests_completed")

        campaign_chronicle.record_archivist_events(
            db_path,
            turn_id=10,
            player_text="Finish the quest.",
            gm_excerpt="The deed is done and the quest closes.",
            location_id=loc_id,
            turn_resolution=resolution,
        )

        conn = sqlite3.connect(db_path)
        quest_id = conn.execute(
            "SELECT id FROM event_log WHERE event_type = 'quest_complete' AND turn_id = 10 LIMIT 1"
        ).fetchone()[0]
        turn_id = conn.execute(
            "SELECT id FROM event_log WHERE event_type = 'turn' AND turn_id = 10 LIMIT 1"
        ).fetchone()[0]
        conn.close()
        assert quest_id < turn_id

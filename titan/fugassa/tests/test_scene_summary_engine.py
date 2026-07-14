"""ADR C4 — GM-first scene deltas, typed rollup, digest engine appendix."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from titan.fugassa import campaign_chronicle, campaign_digest, scene_summary_engine
from titan.fugassa.db import sqlite_store
from titan.fugassa.turn_resolution import TurnResolution


def _init_db() -> str:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "game.db")
    sqlite_store.init_game_db(db_path, "SceneTest", theme="fantasy")
    return db_path


def _seed_location(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
            VALUES ('market', 'Market', 'Busy', 1, '2026-01-01', '2026-01-01')
            """
        )
        loc_id = int(conn.execute("SELECT id FROM locations WHERE code = 'market'").fetchone()[0])
        conn.commit()
        return loc_id
    finally:
        conn.close()


def test_compose_turn_delta_prefers_gm_over_player():
    player = "Walk to the kiosk and ask about Elara."
    gm = "Elara meets your gaze across the crowd, amber eyes unreadable."
    delta = scene_summary_engine.compose_turn_delta(player, gm, turn_number=5)
    assert "Elara" in delta
    assert "kiosk" not in delta.lower()


def test_compose_turn_delta_strips_scene_cast_metadata():
    player = "Ask Elara about the debt."
    gm = (
        "[Scene cast — hero: Lucas; npc: Elara Voss] "
        "Elara folds her arms and waits for you to speak first."
    )
    delta = scene_summary_engine.compose_turn_delta(player, gm, turn_number=6)
    assert "Elara" in delta
    assert "Scene cast" not in delta
    assert "hero:" not in delta.lower()


def test_compose_turn_delta_falls_back_to_engine_chronicle():
    db_path = _init_db()
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_quest_complete_event(
                quest_code="q1",
                quest_title="The Debt",
                hero_name="Lucas",
                turn_id=8,
                location_id=None,
                scale="major",
                chain_code=None,
                chain_position=None,
            )
        ],
    )
    delta = scene_summary_engine.compose_turn_delta(
        "repeat player intent",
        "",
        turn_number=8,
        db_path=db_path,
    )
    assert "Debt" in delta
    assert "repeat player" not in delta


def test_compose_turn_delta_uses_turn_resolution_quest_before_player():
    resolution = TurnResolution(mode="narrative_only", intent="talk")
    resolution.quest = {"summary": "Quest completed: The Unnamed Concubine"}
    delta = scene_summary_engine.compose_turn_delta(
        "player only text",
        "",
        turn_number=12,
        turn_resolution=resolution,
    )
    assert "Unnamed Concubine" in delta


def test_compose_turn_delta_player_is_last_resort():
    delta = scene_summary_engine.compose_turn_delta(
        "Inspect the ledger carefully.",
        "",
        turn_number=3,
    )
    assert "ledger" in delta.lower()


def test_location_exit_rollup_prefers_typed_events():
    db_path = _init_db()
    loc_id = _seed_location(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO event_log (code, event_type, title, summary, turn_id, location_id, is_active, details_json)
            VALUES ('turn_9', 'turn', 'Turn 9', 'Turn 9: player walked around', 9, ?, 1, '{}')
            """,
            (loc_id,),
        )
        conn.execute(
            """
            INSERT INTO event_log (code, event_type, title, summary, turn_id, location_id, is_active, details_json)
            VALUES ('qc_9', 'quest_complete', 'Quest complete', 'Lucas completed The Debt.', 9, ?, 1, '{"source":"engine"}')
            """,
            (loc_id,),
        )
        conn.commit()
        bullets = scene_summary_engine._events_for_location_exit(
            conn,
            from_location_id=loc_id,
            turn_start=9,
            turn_end=9,
        )
        assert bullets[0] == "Lucas completed The Debt."
        assert any("player walked" in b for b in bullets)
    finally:
        conn.close()


def test_build_engine_appendix_from_typed_events():
    db_path = _init_db()
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_companion_join_event(
                npc_code="elara_voss",
                npc_name="Elara Voss",
                hero_name="Lucas",
                turn_id=4,
                location_id=None,
            )
        ],
    )
    appendix = campaign_chronicle.build_engine_appendix(db_path, 4, 4)
    assert "ENGINE APPENDIX" in appendix
    assert "Elara" in appendix


def test_deterministic_condense_appends_engine_appendix():
    db_path = _init_db()
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_quest_complete_event(
                quest_code="q2",
                quest_title="Paid in Full",
                hero_name="Lucas",
                turn_id=1,
                location_id=None,
                scale="standard",
                chain_code=None,
                chain_position=None,
            )
        ],
    )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for i in range(1, 16):
            conn.execute(
                """
                INSERT INTO turn_history (turn_number, player_text, ai_text, is_active)
                VALUES (?, ?, ?, 1)
                """,
                (i, f"action {i}", f"gm {i}"),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT id, turn_number, player_text, ai_text FROM turn_history WHERE is_active = 1 ORDER BY turn_number"
        ).fetchall()
        batch = rows[:15]
        text = campaign_digest._deterministic_condense(batch, db_path=db_path)
        assert "ENGINE APPENDIX" in text
        assert "Paid in Full" in text
    finally:
        conn.close()

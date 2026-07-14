"""ADR C9 — undo_last_turn restores autosave and purges chronicle after turn."""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import campaign_chronicle, game_session, save_store
from titan.fugassa.db import snapshot


@pytest.fixture
def save_id():
    world_name = f"UndoChronicleTest_{os.getpid()}_{id(object())}"
    draft = {"world_name": world_name, "theme_mode": "Fantasy", "player_name": "Lucas", "level": 1}
    sid = save_store.normalize_save_name(world_name)
    save_store.create_save_from_wizard(draft)
    yield sid
    try:
        save_store.delete_save(sid)
    except Exception:
        pass


def test_undo_last_turn_restores_state_and_purges_chronicle(save_id):
    state = game_session.load_game_state(save_id)
    state["turn"] = 10
    state["can_undo"] = True
    state["party"] = [{"name": "Lucas", "role": "hero"}]
    game_session.save_game_state(save_id, state)

    snapshot.create_autosave_prev(save_store.save_dir(save_id))

    state = game_session.load_game_state(save_id)
    state["turn"] = 11
    state["party"] = [
        {"name": "Lucas", "role": "hero"},
        {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss"},
    ]
    game_session.save_game_state(save_id, state)

    db_path = save_store.game_db_path(save_id)
    campaign_chronicle.record_events(
        db_path,
        [
            campaign_chronicle.make_travel_event(
                hero_name="Lucas",
                from_label="Harbor",
                to_label="Market",
                turn_id=11,
                location_id=None,
            )
        ],
    )

    result = game_session.undo_last_turn(save_id)

    assert int(result["state"]["turn"]) == 10
    assert len(result["state"]["party"]) == 1
    assert result["state"]["party"][0]["name"] == "Lucas"
    assert result["state"]["can_undo"] is False

    conn = sqlite3.connect(db_path)
    try:
        active_turns = [
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT turn_id FROM event_log WHERE is_active = 1 ORDER BY turn_id"
            ).fetchall()
        ]
        assert 11 not in active_turns
        assert all(t <= 10 for t in active_turns)
    finally:
        conn.close()

    summary = game_session.get_summary(save_id)
    assert int(summary["campaign_state"]["turn"]) == 10
    assert len(summary["campaign_state"]["party"]) == 1

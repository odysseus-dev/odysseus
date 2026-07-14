"""Tests for campaign_chronicle.repair_chronicle backfill."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import campaign_chronicle
from titan.fugassa.db import sqlite_store


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
        VALUES ('town', 'Town Square', 'Square', 1, '2026-01-01', '2026-01-01')
        """
    )
    loc_id = int(conn.execute("SELECT id FROM locations WHERE code = 'town'").fetchone()[0])
    conn.execute(
        "INSERT INTO player_characters (code, player_id, name, current_location_id) VALUES ('pc_hero', 1, 'Lucas', ?)",
        (loc_id,),
    )
    conn.execute(
        """
        INSERT INTO quests (code, title, status, rewards_json, created_at, updated_at)
        VALUES ('q1', 'Debt Quest', 'completed', '{}', '2026-01-01', '2026-01-01')
        """
    )
    conn.execute(
        """
        INSERT INTO turn_history (turn_number, player_text, ai_text, resolution_json, is_active, created_at)
        VALUES (5, 'done', 'ok', '{}', 1, '2026-01-01')
        """
    )
    conn.execute(
        """
        INSERT INTO event_log (code, event_type, title, summary, turn_id, created_at)
        VALUES ('turn_5_abc', 'turn', 'Turn 5', 'Turn 5: done', 5, '2026-01-01')
        """
    )


def test_repair_chronicle_synthesizes_quest_complete():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "RepairTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        _seed(conn)
        conn.commit()
        conn.close()

        state = {"turn": 5, "party": []}
        dry = campaign_chronicle.repair_chronicle(db_path, state, dry_run=True)
        assert dry["ok"]
        assert any(s["event_type"] == "quest_complete" for s in dry["synthesized"])

        result = campaign_chronicle.repair_chronicle(db_path, state)
        assert result["ok"]
        assert result["events_after"].get("quest_complete") == 1

        again = campaign_chronicle.repair_chronicle(db_path, state)
        assert again["events_after"].get("quest_complete") == 1


def test_repair_chronicle_companion_from_party():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "RepairTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed(conn)
        conn.execute(
            "INSERT INTO npcs (code, name, status, created_at, updated_at) VALUES ('elara_voss', 'Elara Voss', 'alive', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        state = {
            "turn": 5,
            "party": [{"name": "Lucas", "role": "player"}, {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss"}],
        }
        result = campaign_chronicle.repair_chronicle(db_path, state)
        assert result["ok"]
        assert result["events_after"].get("companion_join") == 1

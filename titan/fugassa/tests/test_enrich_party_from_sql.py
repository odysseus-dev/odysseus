"""ADR C6 — enrich_party_from_sql companion enrichment."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from titan.fugassa.db import sqlite_store
from titan.fugassa.db.state_repository import enrich_party_from_sql


def _init_db() -> str:
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "game.db")
    sqlite_store.init_game_db(db_path, "PartyTest", theme="fantasy")
    return db_path


def test_enrich_party_adds_portrait_and_backstory():
    db_path = _init_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO players (id, code, display_name, created_at, updated_at) VALUES (1, 'player_1', 'Player', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO player_characters (code, player_id, name, created_at, updated_at) VALUES ('pc_hero', 1, 'Lucas', '2026-01-01', '2026-01-01')"
        )
        pc_id = int(conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO npcs (code, name, race, class_role, backstory_summary, portrait_path, status, created_at, updated_at)
            VALUES ('elara_voss', 'Elara Voss', 'Elf', 'Concubine', 'Former slave seeking freedom.', 'portraits/elara.png', 'alive', '2026-01-01', '2026-01-01')
            """
        )
        npc_id = int(conn.execute("SELECT id FROM npcs WHERE code = 'elara_voss'").fetchone()[0])
        conn.execute(
            """
            INSERT INTO npc_relationships (source_npc_id, target_type, target_id, attitude, trust, summary, created_at, updated_at)
            VALUES (?, 'player_character', ?, 'friendly', 3, 'Wary but loyal.', '2026-01-01', '2026-01-01')
            """,
            (npc_id, pc_id),
        )
        conn.commit()
        state = {
            "party": [
                {"name": "Lucas", "role": "hero", "hp": 20, "max_hp": 20},
                {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss", "hp": 10, "max_hp": 10},
            ]
        }
        enrich_party_from_sql(conn, state)
        elara = state["party"][1]
        assert elara["portrait_file"] == "portraits/elara.png"
        assert "freedom" in elara["backstory_summary"].lower()
        assert elara["relationship"]["attitude"] == "friendly"
        assert elara["npc_id"] == npc_id
    finally:
        conn.close()

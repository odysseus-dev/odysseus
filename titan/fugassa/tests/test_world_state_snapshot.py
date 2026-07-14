"""Unit tests — world_state_snapshot (ADR §5.4 / C3)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import world_state_snapshot
from titan.fugassa.db import sqlite_store
from titan.fugassa.property_repository import create_holding_conn, sync_property_portfolio


def _seed_base(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, is_discovered, created_at, updated_at)
        VALUES ('market', 'Market District', 'Busy stalls.', 1, '2026-01-01', '2026-01-01')
        """
    )
    loc_id = int(conn.execute("SELECT id FROM locations WHERE code = 'market'").fetchone()[0])
    conn.execute(
        "INSERT INTO players (code, display_name, created_at, updated_at) VALUES ('p1', 'Lucas', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO player_characters (code, player_id, name, level, current_location_id) VALUES ('pc_hero', 1, 'Lucas', 3, ?)",
        (loc_id,),
    )
    conn.execute(
        "INSERT INTO npcs (code, name, status, current_location_id, created_at, updated_at) VALUES ('elara_voss', 'Elara Voss', 'alive', ?, '2026-01-01', '2026-01-01')",
        (loc_id,),
    )
    return loc_id


def test_snapshot_dict_includes_party_quests_property_titles():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "SnapshotTest", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _seed_base(conn)
        pc_id = int(conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()["id"])
        conn.execute(
            """
            INSERT INTO quests (code, title, status, related_location_id, created_at, updated_at)
            VALUES ('q_active', 'Patron Test', 'active', 1, '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO quests (code, title, status, related_location_id, created_at, updated_at)
            VALUES ('q_done', 'The Unnamed Concubine', 'completed', 1, '2026-01-01', '2026-01-02')
            """
        )
        conn.execute(
            """
            INSERT INTO player_renown (
                player_character_id, renown_code, title_display, scope_type, impact_tier, granted_at_turn, created_at
            ) VALUES (?, 'patron_driscoll', 'Patron of House Driscoll', 'region', 4, 10, '2026-01-01')
            """,
            (pc_id,),
        )
        create_holding_conn(
            conn,
            player_character_id=pc_id,
            proposal={
                "granted": True,
                "code": "house_driscoll_city",
                "name": "House Driscoll — City Residence",
                "deed_summary": "Family townhouse in Crownstone.",
                "property_kind": "townhouse",
            },
            acquired_at_turn=5,
        )
        conn.commit()
        conn.close()

        state = {
            "turn": 16,
            "in_combat": False,
            "world_time": {"day": 3, "hour": 14, "minute": 30},
            "party": [
                {"name": "Lucas", "role": "hero", "level": 3},
                {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss"},
            ],
            "location_state": {
                "name": "Market District",
                "settlement_name": "Crownstone",
                "place_label": "Market District",
            },
            "quests": {
                "active": [{"name": "Patron Test", "scale": "standard"}],
                "closed": [],
            },
        }
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        sync_property_portfolio(conn, state)
        conn.close()
        state["player_titles"] = {
            "active_display": "Patron of House Driscoll",
            "active_code": "patron_driscoll",
            "titles": [{"code": "patron_driscoll", "display": "Patron of House Driscoll", "impact_tier": 4}],
        }

        snap = world_state_snapshot.build_snapshot_dict(db_path, state)
        assert snap["turn"] == 16
        assert len(snap["party"]) == 2
        assert snap["party"][1]["npc_code"] == "elara_voss"
        assert snap["quests"]["active"][0]["name"] == "Patron Test"
        assert any(q["title"] == "The Unnamed Concubine" for q in snap["quests"]["recently_completed"])
        assert snap["titles"]["active_display"] == "Patron of House Driscoll"
        assert snap["property"]["holdings"]
        assert snap["in_combat"] is False


def test_snapshot_text_matches_adr_header():
    state = {
        "turn": 15,
        "in_combat": False,
        "world_time": {"day": 3, "hour": 14, "minute": 30},
        "party": [
            {"name": "Lucas Driscoll", "role": "hero", "level": 3},
            {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss"},
        ],
        "location_state": {
            "settlement_name": "Crownstone",
            "place_label": "Market District",
            "name": "Market District",
        },
        "quests": {"active": [], "closed": []},
        "player_titles": {
            "active_display": "Patron of House Driscoll",
            "active_code": "patron_driscoll",
            "titles": [{"code": "patron_driscoll", "display": "Patron of House Driscoll", "impact_tier": 4}],
        },
        "property_portfolio": {
            "active_residence_code": "house_driscoll_city",
            "holdings": [
                {
                    "code": "house_driscoll_city",
                    "name": "House Driscoll — City Residence",
                    "room_count": 4,
                    "staff_names": ["Elara Voss"],
                }
            ],
        },
    }
    text = world_state_snapshot.build_snapshot_text(None, state)
    assert "CAMPAIGN STATE SNAPSHOT" in text
    assert "Turn: 15" in text
    assert "Lucas Driscoll" in text
    assert "Elara Voss" in text
    assert "Quests active: none" in text
    assert "Patron of House Driscoll" in text
    assert "House Driscoll" in text
    assert "In combat: no" in text


def test_gm_runner_uses_snapshot_not_legacy_meta_blocks():
    from titan.fugassa.gm_runner import build_system_prompt

    state = {
        "save_id": "",
        "chat_history": [],
        "location_state": {"name": "Town"},
        "party": [{"name": "Hero", "role": "hero", "level": 1}],
        "quests": {"active": []},
        "world_time": {"day": 1, "hour": 8, "minute": 0},
        "game_state": {"playstyle": "freeform", "rules_mode": "homebrew", "resolution_mode": "narrative"},
    }
    prompt = build_system_prompt(state)
    assert "CAMPAIGN STATE SNAPSHOT" in prompt
    assert "PLAYER PROPERTY (canonical)" not in prompt
    assert "ACTIVE QUESTS (canonical" not in prompt


def test_context_builder_snapshot_block():
    from titan.fugassa import context_builder

    state = {
        "turn": 4,
        "party": [{"name": "Lucas", "role": "hero", "level": 2}],
        "location_state": {"name": "Kiosk", "settlement_name": "Crownstone"},
        "world_time": {"day": 1, "hour": 9, "minute": 0},
        "quests": {"active": [{"name": "Debt", "scale": "minor"}]},
    }
    block = context_builder.build_world_state_snapshot_block(state)
    assert "CAMPAIGN STATE SNAPSHOT" in block
    assert "Debt" in block


def test_format_chronicle_for_api_shape():
    rows = world_state_snapshot.format_chronicle_for_api(
        [
            {
                "event_type": "quest_complete",
                "title": "Quest complete: Foo",
                "summary": "Lucas completed Foo.",
                "turn_id": 12,
                "created_at": "2026-01-01",
                "source": "engine",
                "event_id": 99,
            }
        ]
    )
    assert rows == [
        {
            "event_type": "quest_complete",
            "title": "Quest complete: Foo",
            "summary": "Lucas completed Foo.",
            "turn_id": 12,
            "created_at": "2026-01-01",
        }
    ]


def test_party_context_block_enriches_companion_from_sql():
    from titan.fugassa import context_builder

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "PartyCtx", theme="fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_base(conn)
        conn.execute(
            "UPDATE npcs SET backstory_summary = 'Former concubine of House Driscoll.', race = 'Human', class_role = 'Advisor' WHERE code = 'elara_voss'"
        )
        conn.commit()
        conn.close()
        state = {
            "party": [
                {"name": "Lucas", "role": "hero", "level": 3},
                {"name": "Elara Voss", "role": "companion", "npc_code": "elara_voss"},
            ]
        }
        block = context_builder.build_party_context_block(state, db_path)
        assert "PARTY (canonical" in block
        assert "Elara Voss" in block
        assert "elara_voss" in block
        assert "Driscoll" in block

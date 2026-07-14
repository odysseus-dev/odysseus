"""Tests for portrait prompt backfill on existing saves."""

from __future__ import annotations

import sqlite3

from titan.fugassa.db import asset_repository, sqlite_store
from titan.fugassa.save_state_repair import backfill_portrait_prompts


def _seed_db(tmp_path) -> str:
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "Portrait Backfill Test", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO player_characters (code, player_id, name, portrait_prompt, created_at, updated_at)
        VALUES ('pc_hero', 1, 'Hero', '', datetime('now'), datetime('now'))
        """
    )
    pc_id = int(conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()[0])
    cur = conn.execute(
        """
        INSERT INTO npcs (code, name, race, class_role, status, portrait_prompt, created_at, updated_at)
        VALUES ('npc_merchant', 'Merchant', 'Human', 'Trader', 'alive', '', datetime('now'), datetime('now'))
        """
    )
    npc_id = int(cur.lastrowid)
    asset_repository.insert_asset(
        conn,
        code="pc_portrait_1",
        entity_type="player_character",
        entity_id=pc_id,
        asset_type="portrait",
        status="ready",
        prompt="brave knight portrait",
        file_path="portraits/pc_1.png",
    )
    asset_repository.insert_asset(
        conn,
        code="npc_portrait_5",
        entity_type="npc",
        entity_id=npc_id,
        asset_type="portrait",
        status="ready",
        prompt="old merchant portrait",
        file_path="portraits/npc_5.png",
    )
    conn.commit()
    conn.close()
    return db_path


def test_backfill_portrait_prompts_from_active_assets(tmp_path):
    db_path = _seed_db(tmp_path)
    stats = backfill_portrait_prompts(db_path, state={})
    assert stats["player"] == 1
    assert stats["npc"] == 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pc = conn.execute(
        "SELECT portrait_prompt FROM player_characters WHERE code = 'pc_hero'"
    ).fetchone()
    npc = conn.execute(
        "SELECT portrait_prompt FROM npcs WHERE code = 'npc_merchant'"
    ).fetchone()
    conn.close()
    assert pc["portrait_prompt"] == "brave knight portrait"
    assert npc["portrait_prompt"] == "old merchant portrait"


def test_backfill_portrait_prompts_from_wizard_snapshot_text(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "Portrait Wizard Text", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO player_characters (code, player_id, name, portrait_prompt, created_at, updated_at)
        VALUES ('pc_hero', 1, 'Hero', '', datetime('now'), datetime('now'))
        """
    )
    conn.commit()
    conn.close()

    stats = backfill_portrait_prompts(
        db_path,
        state={
            "wizard_draft_snapshot": {
                "portrait_sd_prompt_text": "Positive\nmysterious elf ranger\nNegative\nblurry",
            }
        },
    )
    assert stats["player"] == 1
    conn = sqlite3.connect(db_path)
    prompt = conn.execute(
        "SELECT portrait_prompt FROM player_characters WHERE code = 'pc_hero'"
    ).fetchone()[0]
    conn.close()
    assert prompt == "mysterious elf ranger"


def test_backfill_portrait_prompts_from_appearance_rows(tmp_path):
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "Portrait Appearance Rows", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO player_characters (code, player_id, name, portrait_prompt, created_at, updated_at)
        VALUES ('pc_hero', 1, 'Lucas', '', datetime('now'), datetime('now'))
        """
    )
    pc_id = int(conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()[0])
    asset_repository.insert_asset(
        conn,
        code="player_character:pc_hero:portrait:v1",
        asset_type="portrait",
        entity_type="player_character",
        entity_id=pc_id,
        status="ready",
        file_path="portraits/pc_1_v1.png",
        title="Portrait pc_hero",
    )
    conn.commit()
    conn.close()

    state = {
        "party": [{"name": "Lucas", "race": "Human", "character_class": "Scavenger", "age": "16"}],
        "world_profile": {"theme": "dark fantasy", "image_style": "anime"},
        "wizard_draft_snapshot": {
            "portrait_appearance": {
                "rows": {"hair_color": {"i": 2, "t": ""}, "facial_hair": {"i": 5, "t": ""}}
            }
        },
    }
    stats = backfill_portrait_prompts(db_path, state=state)
    assert stats["player"] == 1
    assert "portrait_sd_prompt_text" in state["wizard_draft_snapshot"]
    conn = sqlite3.connect(db_path)
    prompt = conn.execute(
        "SELECT portrait_prompt FROM player_characters WHERE code = 'pc_hero'"
    ).fetchone()[0]
    asset = conn.execute("SELECT prompt, negative_prompt FROM assets WHERE entity_id = ?", (pc_id,)).fetchone()
    conn.close()
    assert "Lucas" in prompt
    assert "Dark brown" in prompt
    assert asset[0] == prompt
    assert asset[1]

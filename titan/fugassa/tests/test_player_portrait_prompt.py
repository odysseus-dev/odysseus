"""Tests for player portrait prompt resolution."""

from __future__ import annotations

import sqlite3

from titan.fugassa.db import asset_repository, sqlite_store
from titan.fugassa.player_portrait_prompt import (
    portrait_appearance_to_text,
    prompt_from_wizard_state,
    resolve_player_portrait_prompt,
)
from titan.fugassa.save_state_repair import backfill_portrait_prompts


def _seed_pc(tmp_path) -> tuple[str, int]:
    db_path = str(tmp_path / "game.db")
    sqlite_store.init_game_db(db_path, "Player Portrait Prompt", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO player_characters (code, player_id, name, portrait_prompt, created_at, updated_at)
        VALUES ('pc_hero', 1, 'Hero', '', datetime('now'), datetime('now'))
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
        prompt="single character portrait, Hero, Human, Fighter, fantasy RPG character art, waist-up, detailed",
        file_path="portraits/pc_1_v1.png",
        title="Portrait pc_hero",
    )
    conn.commit()
    conn.close()
    return db_path, pc_id


def test_portrait_appearance_to_text_reads_rows():
    text = portrait_appearance_to_text(
        {
            "rows": {
                "hair_color": {"i": 2, "t": ""},
                "facial_hair": {"i": 5, "t": ""},
            }
        }
    )
    assert "Dark brown" in text
    assert "Full beard" in text


def test_prompt_from_wizard_state_synthesizes_from_appearance_rows():
    state = {
        "party": [{"name": "Lucas", "race": "Human", "character_class": "Scavenger", "age": "16", "gender": "Man"}],
        "world_profile": {"theme": "dark fantasy", "image_style": "anime"},
        "wizard_draft_snapshot": {
            "portrait_appearance": {
                "rows": {
                    "hair_color": {"i": 2, "t": ""},
                    "facial_hair": {"i": 5, "t": ""},
                }
            }
        },
    }
    pos, neg = prompt_from_wizard_state(state)
    assert "Lucas" in pos
    assert "Dark brown" in pos
    assert "lowres" in neg


def test_resolve_prefers_wizard_snapshot_over_generic_asset(tmp_path):
    db_path, pc_id = _seed_pc(tmp_path)
    state = {
        "wizard_draft_snapshot": {
            "portrait_sd_prompt_text": "Positive\nmysterious elf ranger with green cloak\nNegative\nblurry",
        }
    }
    pos, neg = resolve_player_portrait_prompt(db_path, pc_id, state)
    assert "elf ranger" in pos
    assert "blurry" in neg


def test_backfill_prefers_wizard_snapshot_over_generic_asset(tmp_path):
    db_path, _pc_id = _seed_pc(tmp_path)
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

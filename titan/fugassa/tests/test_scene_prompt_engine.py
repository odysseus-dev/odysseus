"""Tests for scene_prompt_engine — deterministic fallback + location distill helpers."""

from __future__ import annotations

import os
import sys
import asyncio
import sqlite3

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import scene_prompt_engine as spe
from titan.fugassa import asset_prompts
from titan.fugassa.scene_character_context import format_characters_for_scene_prompt


def test_sanitize_scene_generation_prompt_strips_portrait_framing():
    raw = "Elara undressing, waist-up portrait, Lucas watching in foreground"
    out = asset_prompts.sanitize_scene_generation_prompt(
        raw, cast_count=2, has_hero=True, supporting_count=1,
    )
    assert "waist-up portrait" not in out.lower()
    assert "focal subject" in out.lower()
    assert len(out) <= 420


def test_sanitize_scene_generation_prompt_hero_solo():
    out = asset_prompts.sanitize_scene_generation_prompt(
        "Lucas draws his sword in the rain",
        cast_count=1,
        has_hero=True,
        supporting_count=0,
    )
    assert "hero as focal subject" in out.lower()


def test_generate_scene_prompts_fallback_when_llm_disabled():
    asset = {
        "asset_type": "scene",
        "entity_type": "location",
        "entity_id": 1,
        "metadata_json": '{"prompt_seed":{"name":"Tavern","description":"A smoky hall"}}',
    }
    state = {
        "location_state": {"name": "Tavern", "description": "A smoky hall"},
        "world_profile": {"theme": "fantasy"},
        "world_time": {"time_of_day": "evening", "weather": "rain"},
    }
    result = asyncio.run(
        spe.generate_scene_prompts_for_asset(
            asset,
            state=state,
            db_path="",
            llm_enabled=False,
        )
    )
    assert result["valid"] is True
    assert "Tavern" in result["positive_prompt"]
    assert result["source"] == "deterministic"


def test_asset_needs_scene_prompt_llm_for_auto_queued_scene():
    assert spe.asset_needs_scene_prompt_llm(
        {"asset_type": "scene", "prompt_source": "auto", "prompt": None}
    )
    assert not spe.asset_needs_scene_prompt_llm(
        {"asset_type": "scene", "prompt_source": "manual", "prompt": "custom"}
    )
    assert not spe.asset_needs_scene_prompt_llm(
        {"asset_type": "portrait", "prompt_source": "auto", "prompt": None}
    )


def test_apply_prompts_to_asset_uses_valid_prompt_source(tmp_path):
    db_path = str(tmp_path / "game.db")
    from titan.fugassa.db import sqlite_store

    sqlite_store.init_game_db(db_path, "PromptSourceTest", theme="fantasy")
    conn = sqlite3.connect(db_path)
    try:
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO assets (
                code, asset_type, entity_type, entity_id, status, prompt_source, created_at, updated_at
            ) VALUES ('scene_1', 'scene', 'location', 1, 'queued', 'auto', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
        asset_id = int(conn.execute("SELECT id FROM assets").fetchone()[0])
    finally:
        conn.close()

    spe.apply_prompts_to_asset(db_path, asset_id, positive="forest clearing", negative="blurry")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT prompt, prompt_source FROM assets WHERE id = ?", (asset_id,)).fetchone()
        assert row["prompt"] == "forest clearing"
        assert row["prompt_source"] == "auto"
    finally:
        conn.close()


def test_is_generic_location_desc():
    assert spe._is_generic_location_desc("You find yourself in Oakhaven.", "Oakhaven")
    assert not spe._is_generic_location_desc("A narrow alley smells of salt and smoke.", "Port Row")


def test_scene_context_for_chat_turn_uses_current_scene_only(tmp_path):
    db_path = str(tmp_path / "game.db")
    from titan.fugassa.db import sqlite_store

    sqlite_store.init_game_db(db_path, "ChatSceneTest", theme="fantasy")
    gm_text = """
| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |
| Day | 12:00 PM | Age 1 101 3 12 | New | Tavern | Spring | Clear |

Recap
You entered the tavern and ordered ale.

Current scene
The barmaid drops a tray; mugs shatter and ale foams across the floorboards.

Round summary
A commotion drew every patron's eye.

What do you do next?
"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO turn_history (turn_number, player_text, ai_text, is_active) VALUES (?, ?, ?, 1)",
            (2, "I wave the barmaid over.", gm_text),
        )
        conn.commit()
    finally:
        conn.close()

    asset = {"asset_type": "scene", "entity_type": "other", "entity_id": 2, "metadata_json": "{}"}
    state = {
        "location_state": {"name": "Tavern", "description": "A smoky common room with low beams."},
        "world_profile": {"theme": "fantasy"},
        "world_time": {"time_of_day": "day", "weather": "clear"},
    }
    ctx = spe._scene_context_for_asset(asset, state=state, db_path=db_path)
    assert ctx["scene_kind"] == "chat_message"
    assert "barmaid drops a tray" in ctx["scene_narrative"]
    assert "entered the tavern" not in ctx["scene_narrative"]
    assert "commotion drew" not in ctx["scene_narrative"]
    assert ctx["player_action"] == "I wave the barmaid over."


def test_generate_chat_scene_prompt_fallback_prioritizes_action(tmp_path):
    db_path = str(tmp_path / "game.db")
    from titan.fugassa.db import sqlite_store

    sqlite_store.init_game_db(db_path, "ChatScenePrompt", theme="fantasy")
    gm_text = """
Recap
You walked into the tavern earlier.

Current scene
A brawl erupts; chairs fly across the taproom as the crowd roars.

Round summary
Violence broke out near the bar.

What do you do next?
"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO turn_history (turn_number, player_text, ai_text, is_active) VALUES (?, ?, ?, 1)",
            (1, "I shove the ruffian away.", gm_text),
        )
        conn.commit()
    finally:
        conn.close()

    asset = {"asset_type": "scene", "entity_type": "other", "entity_id": 1, "metadata_json": "{}"}
    state = {
        "location_state": {"name": "Tavern", "description": "A smoky common room."},
        "world_profile": {"theme": "fantasy"},
        "world_time": {"time_of_day": "evening", "weather": "rain"},
    }
    result = asyncio.run(
        spe.generate_scene_prompts_for_asset(
            asset,
            state=state,
            db_path=db_path,
            llm_enabled=False,
        )
    )
    assert result["valid"] is True
    prompt = result["positive_prompt"].lower()
    assert "brawl" in prompt
    assert "walked into the tavern" not in prompt
    assert prompt.find("brawl") < prompt.find("background environment")


def test_build_chat_scene_prompt_hero_before_backdrop():
    cast = format_characters_for_scene_prompt(
        [
            {"name": "Lucas", "entity_type": "player_character", "appearance_tags": "Human scavenger"},
            {"name": "Elara", "entity_type": "npc", "appearance_tags": "Elf mage"},
        ],
        player_action="Lucas confronts the guard",
    )
    prompt = asset_prompts.build_chat_scene_prompt(
        scene_action="Lucas confronts the guard",
        location_name="Market square",
        scene_characters=cast,
        theme="fantasy",
    )
    low = prompt.lower()
    assert "focal hero" in low
    assert low.find("lucas") < low.find("market square")
    assert "supporting cast" in low


def test_location_prompt_user_message_focuses_on_place_not_story():
    ctx = {
        "theme": "dark fantasy",
        "location_name": "Whispering Crypt",
        "location_description": "Stone arches drip with moss. Cold torchlight flickers on sarcophagi.",
        "biome": "underground tomb",
        "time_of_day": "night",
        "weather": "damp",
        "season": "winter",
        "scene_narrative": "A skeleton lurches from the shadows and attacks the party.",
    }
    msg = spe._scene_prompt_user_message(ctx, "location", style_hint="oil painting")
    assert "dark fantasy" in msg
    assert "Whispering Crypt" in msg
    assert "underground tomb" in msg
    assert "skeleton lurches" not in msg
    assert "tags only" in msg.lower()


def test_generate_location_prompt_fallback_uses_tag_format():
    asset = {
        "asset_type": "scene",
        "entity_type": "location",
        "entity_id": 1,
        "metadata_json": '{"prompt_seed":{"name":"Crypt","description":"A damp stone hall. Moss covers the walls."}}',
    }
    state = {
        "location_state": {"name": "Crypt", "description": "A damp stone hall. Moss covers the walls."},
        "world_profile": {"theme": "dark fantasy", "image_style": "oil painting"},
        "world_time": {"time_of_day": "night", "weather": "fog"},
    }
    result = asyncio.run(
        spe.generate_scene_prompts_for_asset(
            asset,
            state=state,
            db_path="",
            llm_enabled=False,
        )
    )
    prompt = result["positive_prompt"]
    assert "dark fantasy" in prompt
    assert "Crypt" in prompt
    assert prompt.count(",") >= 4
    assert "A damp stone hall" not in prompt or "Moss covers the walls" in prompt


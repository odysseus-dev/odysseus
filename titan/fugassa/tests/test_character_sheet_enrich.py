"""Character sheet enrich + portrait prompt parsing tests."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.db import sqlite_store
from titan.fugassa.sheet_persistence import enrich_character_sheet_from_sql
from titan.fugassa.wizard_json import parse_portrait_sd_prompt_text


def test_enrich_skills_adds_modifier_str_from_bonus():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "SkillTest", theme="Fantasy")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO players (code, display_name, created_at, updated_at)
            VALUES ('player_1', 'Hero', datetime('now'), datetime('now'))
            """
        )
        player_id = conn.execute("SELECT id FROM players WHERE code = 'player_1'").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO player_characters (
                code, player_id, name, race, class_name, level, proficiency_bonus,
                str_score, dex_score, con_score, int_score, wis_score, cha_score,
                created_at, updated_at
            ) VALUES (
                'pc_hero', ?, 'Hero', 'Human', 'Fighter', 1, 2,
                10, 16, 10, 10, 10, 10,
                datetime('now'), datetime('now')
            )
            """,
            (player_id,),
        )
        pc_id = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero'").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO player_skills (player_character_id, skill_id, skill_name, bonus, proficient, expertise)
            VALUES (?, 'acrobatics', 'Acrobatics', 5, 1, 0)
            """,
            (pc_id,),
        )
        conn.commit()
        state = enrich_character_sheet_from_sql(
            conn,
            pc_id,
            {"character_sheet": {"stable_sheet": {"abilities": {"dex": 16}}}},
        )
        conn.close()
        skills = state["character_sheet"]["stable_sheet"]["skills"]
        acro = next(s for s in skills if s["id"] == "acrobatics")
        assert acro["modifier_str"] == "+5"
        assert acro["bonus"] == 5


def test_parse_portrait_sd_prompt_text_splits_positive_negative():
    raw = "Positive\nmasterpiece, elf wizard\n\nNegative\nblurry, low quality"
    parsed = parse_portrait_sd_prompt_text(raw)
    assert "elf wizard" in parsed["positive_prompt"]
    assert "blurry" in parsed["negative_prompt"]

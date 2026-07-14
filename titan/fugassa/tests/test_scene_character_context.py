"""Tests for scene character matching in SD prompts."""

from __future__ import annotations

import sqlite3

from titan.fugassa.scene_character_context import (
    cast_prompt_stats,
    classify_scene_cast,
    collect_scene_characters,
    format_characters_for_scene_prompt,
    format_scene_cast_for_llm,
    primary_portrait_reference,
)


def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "game.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE player_characters (
            id INTEGER PRIMARY KEY,
            code TEXT,
            name TEXT,
            race TEXT,
            class_name TEXT,
            portrait_prompt TEXT,
            portrait_path TEXT
        );
        CREATE TABLE npcs (
            id INTEGER PRIMARY KEY,
            name TEXT,
            race TEXT,
            class_role TEXT,
            portrait_prompt TEXT,
            portrait_path TEXT,
            backstory_summary TEXT,
            is_important INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        """
        INSERT INTO player_characters (id, code, name, race, class_name, portrait_prompt, portrait_path)
        VALUES (1, 'pc_hero', 'Aldric', 'Human', 'Fighter', 'tall human with scar', 'portraits/pc_1.png')
        """
    )
    conn.execute(
        """
        INSERT INTO npcs (id, name, race, class_role, portrait_prompt, portrait_path, backstory_summary, is_important)
        VALUES (2, 'Elara', 'Elf', 'Mage', 'silver hair elf mage', 'portraits/npc_2.png', '', 1)
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_collect_scene_characters_includes_player_and_mentioned_npc(tmp_path):
    db_path = _make_db(tmp_path)
    state = {
        "party": [{"name": "Aldric"}],
        "location_state": {
            "npc_details": [{"name": "Elara", "npc_id": 2}],
        },
    }
    chars = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative="Aldric greets Elara by the fire.",
        player_action="",
    )
    names = [c["name"] for c in chars]
    assert names == ["Aldric", "Elara"]
    assert chars[0]["entity_type"] == "player_character"
    assert chars[1]["portrait_path"] == "portraits/npc_2.png"
    assert "silver hair" in chars[1]["appearance_tags"]


def test_collect_scene_characters_can_omit_player(tmp_path):
    db_path = _make_db(tmp_path)
    state = {
        "party": [{"name": "Aldric"}],
        "location_state": {"npc_details": []},
    }
    chars = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative="Aldric studies his reflection.",
        include_player=False,
    )
    assert [c["name"] for c in chars] == []


def test_format_characters_for_scene_prompt_lists_cast():
    narrative = "Lucas holds his gaze on Elara as she stands before him naked."
    block = format_characters_for_scene_prompt(
        [
            {"name": "Lucas", "entity_type": "player_character", "appearance_tags": "Human, Scavenger"},
            {"name": "Elara", "entity_type": "npc", "appearance_tags": "Mage, Elf, silver hair"},
        ],
        narrative=narrative,
    )
    assert "HERO" in block
    assert "- Elara:" in block
    assert "SUPPORTING CAST" in block
    assert "- Lucas:" in block
    assert "Elf" in block


def test_format_characters_npc_focal_without_narrative_defaults_npc():
    block = format_characters_for_scene_prompt(
        [
            {"name": "Lucas", "entity_type": "player_character", "appearance_tags": "Human"},
            {"name": "Elara", "entity_type": "npc", "appearance_tags": "Elf"},
        ],
    )
    assert "- Elara:" in block.split("SUPPORTING")[0]


def test_cast_prompt_stats():
    block = format_characters_for_scene_prompt(
        [
            {"name": "Lucas", "entity_type": "player_character", "appearance_tags": "Human"},
            {"name": "Elara", "entity_type": "npc", "appearance_tags": "Elf"},
            {"name": "Harven", "entity_type": "npc", "appearance_tags": "Merchant"},
        ],
        narrative="Elara greets Lucas while Harven watches.",
    )
    has_hero, supporting, total = cast_prompt_stats(block)
    assert has_hero is True
    assert supporting == 2
    assert total == 3


def test_classify_scene_cast_player_is_secondary():
    cast = classify_scene_cast(
        [
            {"name": "Lucas", "entity_type": "player_character"},
            {"name": "Elara Voss", "entity_type": "npc"},
        ]
    )
    assert cast["primary"] == ["Elara Voss"]
    assert cast["secondary"] == ["Lucas"]


def test_format_scene_cast_for_llm():
    line = format_scene_cast_for_llm({"primary": ["Elara Voss"], "secondary": ["Lucas"]})
    assert "primary: Elara Voss" in line
    assert "secondary: Lucas" in line


def test_gm_runner_chat_entry_includes_scene_cast():
    from titan.fugassa.gm_runner import _chat_entry_content

    content = _chat_entry_content(
        {
            "role": "assistant",
            "content": "Elara greets Lucas.",
            "scene_cast": {"primary": ["Elara Voss"], "secondary": ["Lucas"]},
        }
    )
    assert content.startswith("[Scene cast")
    assert "Elara greets Lucas." in content


def test_primary_portrait_reference_prefers_player(tmp_path):
    db_path = _make_db(tmp_path)
    state = {
        "party": [{"name": "Aldric"}],
        "location_state": {"npc_details": [{"name": "Elara", "npc_id": 2}]},
    }
    chars = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative="Aldric and Elara stand together.",
    )
    ref = primary_portrait_reference(chars)
    assert ref is not None
    assert ref["entity_type"] == "player_character"
    assert ref["portrait_path"] == "portraits/pc_1.png"


def test_collect_scene_characters_matches_first_name_alias(tmp_path):
    db_path = str(tmp_path / "game.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE player_characters (
            id INTEGER PRIMARY KEY, code TEXT, name TEXT, race TEXT, class_name TEXT,
            portrait_prompt TEXT, portrait_path TEXT
        );
        CREATE TABLE npcs (
            id INTEGER PRIMARY KEY, name TEXT, race TEXT, class_role TEXT,
            portrait_prompt TEXT, portrait_path TEXT, backstory_summary TEXT,
            is_important INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO player_characters VALUES (1,'pc_hero','Lucas','Human','Scavenger','','portraits/pc_1.png')"
    )
    conn.execute(
        "INSERT INTO npcs VALUES (4,'Elara Voss','Elf','Concubine','amber eyes','portraits/npc_4.png','',1)"
    )
    conn.execute(
        "INSERT INTO npcs VALUES (5,'Harven Vale','Human','Merchant','goatee','portraits/npc_5.png','',0)"
    )
    conn.commit()
    conn.close()
    state = {
        "party": [{"name": "Lucas"}],
        "location_state": {"npc_details": []},
    }
    narrative = (
        "Elara undressing in the market square, Lucas watching intently, "
        "Harven Vale bowing nearby holding a silk shawl."
    )
    chars = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative=narrative,
        player_action="",
    )
    names = [c["name"] for c in chars]
    assert names[0] == "Lucas"
    assert set(names) == {"Lucas", "Elara Voss", "Harven Vale"}


def test_collect_scene_characters_finds_npc_globally_when_not_at_location(tmp_path):
    db_path = _make_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO npcs (id, name, race, class_role, portrait_prompt, portrait_path, backstory_summary, is_important)
        VALUES (3, 'Harven', 'Human', 'Merchant', 'grizzled trader in leather apron', 'portraits/npc_3.png', '', 0)
        """
    )
    conn.commit()
    conn.close()
    state = {
        "party": [{"name": "Aldric"}],
        "location_state": {"npc_details": []},
    }
    chars = collect_scene_characters(
        state=state,
        db_path=db_path,
        narrative="You remember Harven handing you the sealed letter yesterday.",
    )
    names = [c["name"] for c in chars]
    assert names == ["Aldric", "Harven"]
    harven = next(c for c in chars if c["name"] == "Harven")
    assert harven["entity_id"] == 3
    assert "grizzled trader" in harven["appearance_tags"]

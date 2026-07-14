"""Unit tests for investigate_engine — real d20+WIS+prof roll, per-type DC,
permanent per-location exhaustion, and hidden_* -> visible reveal.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import investigate_engine as ie
from titan.fugassa.db import sqlite_store


def make_db_with_hero(wis_score: int = 14, proficiency_bonus: int = 2) -> str:
    d = tempfile.mkdtemp(prefix="fugassa_investigate_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Investigate Test Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    now = "2024-01-01T00:00:00"
    conn.execute(
        """
        INSERT INTO players (code, display_name, created_at, updated_at) VALUES ('player_1', 'Hero', ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO player_characters (
            code, player_id, name, race, class_name, level, proficiency_bonus,
            str_score, dex_score, con_score, int_score, wis_score, cha_score,
            armor_class, hit_points_current, hit_points_max, status, created_at, updated_at
        ) VALUES ('pc_hero', 1, 'Hero', 'Human', 'Ranger', 1, ?, 10, 10, 10, 10, ?, 10, 12, 10, 10, 'active', ?, ?)
        """,
        (proficiency_bonus, wis_score, now, now),
    )
    conn.commit()
    conn.close()
    return db_path


def _base_state(**overrides) -> dict:
    state = {
        "player": {"x": 2, "y": 3, "z": 0, "map_code": "overworld"},
        "location_state": {"name": "Ruins", "npcs": [], "enemies": [], "loot": []},
        "turn": 1,
    }
    state.update(overrides)
    return state


def test_location_key_differs_by_coords_and_sublocation():
    s1 = _base_state()
    s2 = _base_state(player={"x": 5, "y": 3, "z": 0, "map_code": "overworld"})
    s3 = _base_state(player={"x": 2, "y": 3, "z": 0, "map_code": "overworld", "sublocation_id": 7})
    assert ie.location_key(s1) != ie.location_key(s2)
    assert ie.location_key(s1) != ie.location_key(s3)
    assert ie.location_key(s1) == "2,3,0"
    assert ie.location_key(s3) == "2,3,0#sub7"


def test_successful_search_rolls_real_d20_and_marks_history(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)  # +3 WIS mod
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)  # 15+3+2=20 vs DC 10 -> success
    state = _base_state()

    result = ie.resolve_investigate(db_path, state, ["items"], 30)

    items_result = result["results"]["items"]
    assert items_result["attempted"] is True
    assert items_result["roll"] == 15
    assert items_result["wis_mod"] == 3
    assert items_result["proficiency_bonus"] == 2
    assert items_result["total"] == 20
    assert items_result["dc"] == 10
    assert items_result["success"] is True
    assert result["time_delta_minutes"] == 30

    key = ie.location_key(state)
    assert state["search_history"][key]["items"]["success"] is True


def test_failed_roll_still_exhausts_the_type(monkeypatch):
    db_path = make_db_with_hero(wis_score=8, proficiency_bonus=2)  # -1 WIS mod
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 1)  # 1-1+2=2 vs DC 10 -> failure
    state = _base_state()

    result = ie.resolve_investigate(db_path, state, ["items"], 30)
    assert result["results"]["items"]["success"] is False

    # Exhaustion is permanent even on failure — repeating immediately is a no-op.
    result2 = ie.resolve_investigate(db_path, state, ["items"], 30)
    assert result2["results"]["items"]["already_searched"] is True
    assert result2["results"]["items"]["attempted"] is False
    assert result2["time_delta_minutes"] == 0


def test_different_search_type_at_same_location_is_independent(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state()

    ie.resolve_investigate(db_path, state, ["items"], 30)
    result = ie.resolve_investigate(db_path, state, ["enemy"], 30)

    assert result["results"]["enemy"]["already_searched"] is False
    assert result["results"]["enemy"]["attempted"] is True


def test_same_search_type_at_different_location_is_independent(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 1)
    state = _base_state()
    ie.resolve_investigate(db_path, state, ["items"], 30)

    state["player"] = {"x": 99, "y": 99, "z": 0, "map_code": "overworld"}
    result = ie.resolve_investigate(db_path, state, ["items"], 30)
    assert result["results"]["items"]["already_searched"] is False


def test_successful_search_reveals_hidden_loot_into_visible_list(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state(
        location_state={
            "name": "Ruins",
            "npcs": [],
            "enemies": [],
            "loot": [],
            "hidden_loot": [{"name": "Ancient Coin", "qty": 1}],
        }
    )

    result = ie.resolve_investigate(db_path, state, ["hidden"], 30)

    assert result["revealed_any"] is True
    assert result["results"]["hidden"]["revealed"] == [{"name": "Ancient Coin", "qty": 1}]
    assert state["location_state"]["loot"] == [{"name": "Ancient Coin", "qty": 1}]
    assert state["location_state"]["hidden_loot"] == []


def test_successful_search_with_no_hidden_content_finds_nothing_but_still_exhausts(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state()

    result = ie.resolve_investigate(db_path, state, ["hidden"], 30)

    assert result["results"]["hidden"]["success"] is True
    assert result["results"]["hidden"]["revealed"] == []
    assert result["revealed_any"] is False
    key = ie.location_key(state)
    assert state["search_history"][key]["hidden"]["success"] is True


def test_options_for_location_reports_exhausted_types(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state()
    ie.resolve_investigate(db_path, state, ["items"], 30)

    options = ie.options_for_location(state)
    by_type = {t["type"]: t for t in options["types"]}
    assert by_type["items"]["exhausted"] is True
    assert by_type["items"]["available"] is False
    assert by_type["enemy"]["exhausted"] is False
    assert by_type["enemy"]["available"] is True


def test_unknown_search_types_are_ignored_and_default_to_items(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state()

    result = ie.resolve_investigate(db_path, state, ["not_a_real_type"], 30)
    assert "items" in result["results"]


def test_duration_is_clamped_to_valid_range(monkeypatch):
    db_path = make_db_with_hero(wis_score=16, proficiency_bonus=2)
    monkeypatch.setattr(ie.random, "randint", lambda a, b: 15)
    state = _base_state()

    result = ie.resolve_investigate(db_path, state, ["items"], 999999)
    assert result["time_delta_minutes"] == ie.MAX_DURATION_MINUTES


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

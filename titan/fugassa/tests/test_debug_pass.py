"""Regression checks for the ADR-vs-implementation debug pass (2026-07-09).

Run inside the titan-odysseus container:
    docker exec titan-odysseus-1 python3 /tmp/fugassa_test/test_debug_pass.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store, state_repository  # noqa: E402
from titan.fugassa import quest_engine, turn_resolver, combat_engine, social_engine  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def make_db() -> str:
    d = tempfile.mkdtemp(prefix="fugassa_dbg_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Debug Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute(
        """
        INSERT INTO player_characters (code, player_id, name, str_score, dex_score, proficiency_bonus,
            hit_points_current, hit_points_max, armor_class)
        VALUES ('pc_hero', 1, 'Hero', 16, 12, 2, 20, 20, 14)
        """
    )
    conn.commit()
    conn.close()
    return db_path


def base_state() -> dict:
    return {
        "player": {"x": 0, "y": 0, "z": 0},
        "party": [{"name": "Hero", "hp": 20, "max_hp": 20, "ac": 14}],
        "location_state": {"name": "Start", "description": "start cell", "npcs": [], "enemies": [], "loot": []},
        "quests": {"active": [], "closed": []},
        "inventory": {"shared": []},
        "turn": 0,
        "in_combat": False,
    }


def test_visit_location_same_turn():
    """Before the fix: quest_engine ran against last turn's SQL location
    (sync_from_state happened *after* resolve_turn in game_session), so a
    visit_location objective completed one full turn late. Now sync happens
    inside resolve_turn, right after travel/move mutate state."""
    db_path = make_db()
    state = base_state()
    # bootstrap current location at (0,0,0)
    state_repository.sync_from_state(db_path, state, turn_number=0)

    qid = quest_engine.create_quest(
        db_path,
        code="q_visit",
        title="Find the shrine",
        objectives=[{"objective_type": "visit_location", "target_code": "grid_overworld_1_0_0", "description_text": "Go to (1,0,0)"}],
    )
    check("quest created", qid is not None)

    resolution = turn_resolver.resolve_turn(state, "go to cell 1,0", db_path=db_path)
    check("travel resolved", bool(resolution.travel), str(resolution.travel))

    status = sqlite3.connect(db_path).execute(
        "SELECT status FROM quest_objectives WHERE quest_id = ?", (qid,)
    ).fetchone()[0]
    check("visit_location objective completes in the SAME turn as the move", status == "complete", f"status={status}")

    quest_status = sqlite3.connect(db_path).execute("SELECT status FROM quests WHERE id = ?", (qid,)).fetchone()[0]
    check("quest auto-completes same turn", quest_status == "completed", f"quest_status={quest_status}")


def test_scene_asset_uses_real_location_id():
    # cardinal step (adjacent) — long-range "go to cell X,Y" is correctly
    # rejected by grid_engine without intel/an adjacent path, which isn't
    # what this check is about.
    db_path = make_db()
    state = base_state()
    state_repository.sync_from_state(db_path, state, turn_number=0)
    resolution = turn_resolver.resolve_turn(state, "move east", db_path=db_path)
    reqs = resolution.asset_requests
    check("scene asset request emitted", len(reqs) == 1, str(reqs))
    if reqs:
        loc_id = reqs[0]["entity_id"]
        real_row = sqlite3.connect(db_path).execute(
            "SELECT id FROM locations WHERE code = 'grid_overworld_1_0_0'"
        ).fetchone()
        check(
            "asset entity_id matches the actual destination location (not hardcoded 1)",
            real_row is not None and loc_id == real_row[0],
            f"loc_id={loc_id} real={real_row}",
        )


def test_hud_move_runs_quest_and_combat_checks():
    """game_session.travel()/move_direction() previously never called quest_engine
    or combat_engine.evaluate_combat_trigger at all — Map-driven movement could
    never complete a quest or start combat. Simulate the same call sequence
    game_session.move_direction() now performs."""
    db_path = make_db()
    state = base_state()
    state_repository.sync_from_state(db_path, state, turn_number=0)
    qid = quest_engine.create_quest(
        db_path,
        code="q_explore",
        title="Scout north",
        objectives=[{"objective_type": "explore", "target_code": "grid_overworld_0_-1_0", "description_text": "Scout north"}],
    )
    from titan.fugassa.grid_engine import move_cardinal

    move_cardinal(state, 0, -1, 0)
    state_repository.sync_from_state(db_path, state, turn_number=1)
    resolution = turn_resolver.run_engine_only_checks(db_path, state)
    status = sqlite3.connect(db_path).execute(
        "SELECT status FROM quest_objectives WHERE quest_id = ?", (qid,)
    ).fetchone()[0]
    check("HUD move (engine_only) completes explore objective", status == "complete", f"status={status}")
    check("run_engine_only_checks reports the completion", bool(resolution.quest), str(resolution.quest))


def test_social_crit_deltas_match_adr():
    """ADR §D1: crit success/fail deltas are +2/-1, not the old +3/-3."""
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_a', 'A', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code = 'pc_hero'", (loc_id,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_x', 'Elder', ?, 'alive')", (loc_id,))
    npc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_personality_hex (npc_id) VALUES (?)", (npc_id,))
    conn.commit()
    conn.close()

    import random as _random

    state = base_state()
    orig_randint = _random.randint
    _random.randint = lambda a, b: 20 if (a, b) == (1, 20) else orig_randint(a, b)
    try:
        result = social_engine.resolve_social(db_path, state, "I persuade Elder")
    finally:
        _random.randint = orig_randint
    check("crit success delta is +2 (ADR §D1), not +3", result.get("relationship_delta") == 2, str(result))

    _random.randint = lambda a, b: 1 if (a, b) == (1, 20) else orig_randint(a, b)
    try:
        result2 = social_engine.resolve_social(db_path, state, "I persuade Elder")
    finally:
        _random.randint = orig_randint
    check("crit fail delta is -1 (ADR §D1), not -3", result2.get("relationship_delta") == -1, str(result2))


def test_combat_attack_bonus_from_sheet():
    """STR 16 (+3) + proficiency 2 = +5, not the old hardcoded 4."""
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_b', 'B', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code = 'pc_hero'", (loc_id,))
    conn.execute(
        "INSERT INTO npcs (code, name, current_location_id, status, is_hostile) VALUES ('npc_w', 'Wolf', ?, 'alive', 1)",
        (loc_id,),
    )
    npc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_stats (npc_id, armor_class, hit_points_current, hit_points_max) VALUES (?, 12, 10, 10)", (npc_id,))
    conn.commit()
    conn.close()

    state = base_state()
    result = combat_engine.resolve_player_attack(db_path, state, "I attack Wolf")
    check("attack_bonus derived from STR16+prof2 = 5, not hardcoded 4", result.get("attack_bonus") == 5, str(result))


if __name__ == "__main__":
    test_visit_location_same_turn()
    test_scene_asset_uses_real_location_id()
    test_hud_move_runs_quest_and_combat_checks()
    test_social_crit_deltas_match_adr()
    test_combat_attack_bonus_from_sheet()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

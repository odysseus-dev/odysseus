import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import quest_engine, turn_resolver, social_engine, item_engine
from titan.fugassa.turn_resolution import TurnResolution

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_qf_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Quest Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_a', 'A', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code='pc_hero'", (loc_id,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_giver', 'Elder', ?, 'alive')", (loc_id,))
    giver_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_relationships (source_npc_id, target_type, trust) VALUES (?, 'player', 5)", (giver_id,))
    conn.execute("INSERT INTO npc_personality_hex (npc_id) VALUES (?)", (giver_id,))
    conn.commit()
    conn.close()
    return db_path, giver_id

def base_state():
    return {
        "player": {"x": 0, "y": 0, "z": 0},
        "party": [{"name": "Hero", "hp": 20, "max_hp": 20}],
        "location_state": {"name": "A"},
        "inventory": {"shared": []},
        "world_time": {"day": 1, "hour": 8},
        "turn": 0,
    }

def test_giver_dead_fails_quest():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q1", title="Find the herb", giver_npc_code="npc_giver",
        objectives=[{"objective_type": "obtain_item", "condition": {"item_name": "Herb"}, "description_text": "Get herb"}])
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE npcs SET status = 'dead' WHERE id = ?", (giver_id,))
    conn.commit(); conn.close()
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    status, reason = sqlite3.connect(db_path).execute("SELECT status, fail_reason FROM quests WHERE id=?", (qid,)).fetchone()
    check("giver_dead fails quest", status == "failed" and reason == "giver_dead", f"status={status} reason={reason}")
    flag = sqlite3.connect(db_path).execute("SELECT value FROM world_flags WHERE key=?", (f"quest_failed:q1",)).fetchone()
    check("quest_failed world flag set", flag is not None)
    check("resolution.quest reports the failure", any(f["reason"] == "giver_dead" for f in res.quest.get("quests_failed", [])), str(res.quest))

def test_time_expired_fails_quest():
    db_path, giver_id = make_db()
    state = base_state()
    deadline = quest_engine.deadline_from_now(state, duration_hours=1)  # 1h from day1/hour8
    qid = quest_engine.create_quest(db_path, code="q2", title="Urgent delivery", giver_npc_code="npc_giver",
        deadline_ingame_at=deadline,
        objectives=[{"objective_type": "obtain_item", "condition": {"item_name": "Package"}, "description_text": "Get package"}])
    state["world_time"] = {"day": 1, "hour": 10}  # 2h later -> past deadline
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    status, reason = sqlite3.connect(db_path).execute("SELECT status, fail_reason FROM quests WHERE id=?", (qid,)).fetchone()
    check("time_expired fails quest", status == "failed" and reason == "time_expired", f"status={status} reason={reason}")

def test_event_flag_fails_quest():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q3", title="Save the caravan", giver_npc_code="npc_giver",
        objectives=[
            {"objective_type": "visit_location", "target_code": "loc_a", "description_text": "go there"},
            {"objective_type": "fail_on_event_flag", "target_code": "caravan_destroyed", "description_text": "(hidden fail trigger)"},
        ])
    quest_engine.set_world_flag(db_path, "caravan_destroyed")
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    status, reason = sqlite3.connect(db_path).execute("SELECT status, fail_reason FROM quests WHERE id=?", (qid,)).fetchone()
    check("event_flag fails quest", status == "failed" and reason == "event_flag", f"status={status} reason={reason}")

def test_reward_granting_on_completion():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q4", title="Deliver bread", giver_npc_code="npc_giver",
        rewards={"gold": 50, "xp": 100, "items": [{"name": "Silver Ring", "qty": 1}]},
        objectives=[{"objective_type": "visit_location", "target_code": "loc_a", "description_text": "go there"}])
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    status = sqlite3.connect(db_path).execute("SELECT status FROM quests WHERE id=?", (qid,)).fetchone()[0]
    check("quest completes", status == "completed", status)
    shared = state["inventory"]["shared"]
    gold_item = next((i for i in shared if i["name"] == "gold"), None)
    ring_item = next((i for i in shared if i["name"] == "Silver Ring"), None)
    check("gold granted to JSON inventory", gold_item is not None and gold_item["qty"] == 50, str(shared))
    check("item granted to JSON inventory", ring_item is not None and ring_item["qty"] == 1, str(shared))
    xp = sqlite3.connect(db_path).execute("SELECT experience_points FROM player_characters WHERE code='pc_hero'").fetchone()[0]
    check("xp granted to SQL sheet", xp == 100, xp)
    check("resolution.quest lists rewards_granted", "q4" in res.quest.get("rewards_granted", {}), str(res.quest))

def test_merit_bonus_granted_when_optional_done():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q5", title="Clear the road", giver_npc_code="npc_giver",
        rewards={"gold": 20}, bonus_rewards={"gold": 10},
        objectives=[
            {"objective_type": "visit_location", "target_code": "loc_a", "description_text": "go there"},
            {"objective_type": "visit_location", "target_code": "loc_a", "description_text": "extra help", "optional": True},
        ])
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    shared = state["inventory"]["shared"]
    gold_total = sum(i["qty"] for i in shared if i["name"] == "gold")
    check("merit bonus stacks with base reward (20+10=30)", gold_total == 30, str(shared))

def test_renounce_quest_via_dialog():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q6", title="Long errand", giver_npc_code="npc_giver",
        objectives=[{"objective_type": "obtain_item", "condition": {"item_name": "Thing"}, "description_text": "get thing"}])
    result = social_engine.resolve_social(db_path, state, "I give up on this quest, Elder.")
    check("renounce dialog succeeds", "quest_renounced" in result, str(result))
    status, reason = sqlite3.connect(db_path).execute("SELECT status, fail_reason FROM quests WHERE id=?", (qid,)).fetchone()
    check("quest marked failed with player_choice", status == "failed" and reason == "player_choice", f"status={status} reason={reason}")

def test_negotiate_reward_success():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q7", title="Escort mission", giver_npc_code="npc_giver",
        rewards={"gold": 40}, bonus_rewards={"gold": 10}, negotiation_rules={"bonus_pct_max": 25},
        objectives=[{"objective_type": "visit_location", "target_code": "loc_a", "description_text": "go there"}])
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)  # completes quest, grants base 40 gold
    status = sqlite3.connect(db_path).execute("SELECT status FROM quests WHERE id=?", (qid,)).fetchone()[0]
    check("escort quest completed before negotiation", status == "completed", status)

    import random as _random
    orig = _random.randint
    _random.randint = lambda a, b: 20 if (a, b) == (1, 20) else orig(a, b)
    try:
        result = social_engine.resolve_social(db_path, state, "I ask for more gold, Elder, I deserve more.")
    finally:
        _random.randint = orig
    check("negotiation crit success grants bonus", result.get("quest_negotiation", {}).get("granted_gold", 0) > 0, str(result))
    gold_total = sum(i["qty"] for i in state["inventory"]["shared"] if i["name"] == "gold")
    check("negotiated bonus gold added on top of base 40", gold_total > 40, gold_total)

def test_negotiate_reward_greedy_rejected():
    db_path, giver_id = make_db()
    state = base_state()
    qid = quest_engine.create_quest(db_path, code="q8", title="Fetch water", giver_npc_code="npc_giver",
        rewards={"gold": 10}, bonus_rewards={"gold": 2},
        objectives=[{"objective_type": "visit_location", "target_code": "loc_a", "description_text": "go there"}])
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    result = social_engine.resolve_social(db_path, state, "I demand 1000 gold or nothing, Elder!")
    neg = result.get("quest_negotiation", {})
    check("greedy demand auto-rejected", neg.get("outcome") == "auto_fail_greedy" and neg.get("granted_gold") == 0, str(neg))

def test_item_use_desync_fixed():
    db_path, giver_id = make_db()
    state = base_state()
    state["inventory"]["shared"] = [{"name": "Torch", "qty": 2}]
    from titan.fugassa.db import state_repository
    state_repository.sync_from_state(db_path, state, turn_number=0)
    item_result = item_engine.resolve_use_item(db_path, state, "I light the torch")
    check("item used", item_result.get("used"), str(item_result))
    check("JSON mirror updated to qty=1", state["inventory"]["shared"][0]["qty"] == 1, str(state["inventory"]))
    # Now simulate the turn-pipeline's later full sync_from_state — this used to
    # silently revert the SQL decrement back to the stale JSON qty.
    state_repository.sync_from_state(db_path, state, turn_number=1)
    qty_after = sqlite3.connect(db_path).execute("SELECT quantity FROM items WHERE name='Torch'").fetchone()[0]
    check("SQL quantity survives the subsequent sync_from_state (no revert)", qty_after == 1, qty_after)

if __name__ == "__main__":
    test_giver_dead_fails_quest()
    test_time_expired_fails_quest()
    test_event_flag_fails_quest()
    test_reward_granting_on_completion()
    test_merit_bonus_granted_when_optional_done()
    test_renounce_quest_via_dialog()
    test_negotiate_reward_success()
    test_negotiate_reward_greedy_rejected()
    test_item_use_desync_fixed()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

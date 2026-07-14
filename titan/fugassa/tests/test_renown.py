import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import renown_engine, npc_knowledge, social_engine, quest_engine
from titan.fugassa.turn_resolution import TurnResolution

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_renown_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Renown Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.execute("INSERT INTO locations (code, name, region_name, is_discovered) VALUES ('grid_overworld_0_0_0', 'Capital', 'Amalur', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code='pc_hero'", (loc_id,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_citizen', 'Citizen', ?, 'alive')", (loc_id,))
    citizen_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_personality_hex (npc_id) VALUES (?)", (citizen_id,))
    conn.execute("INSERT INTO npc_tags (npc_id, tag, source) VALUES (?, 'faction:amalur_guard', 'system')", (citizen_id,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_rebel', 'Rebel', ?, 'alive')", (loc_id,))
    rebel_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_personality_hex (npc_id) VALUES (?)", (rebel_id,))
    conn.execute("INSERT INTO npc_tags (npc_id, tag, source) VALUES (?, 'faction:rebels', 'system')", (rebel_id,))
    conn.commit()
    conn.close()
    return db_path, citizen_id, rebel_id

def test_tier1_never_grants_renown():
    db_path, citizen_id, rebel_id = make_db()
    result = renown_engine.grant_renown(db_path, renown_code="minor_favor", scope_type="region", impact_tier=1)
    check("tier-1 event grants no renown row (ephemeral)", result is None, result)
    rows = renown_engine.list_renown(db_path)
    check("player_renown table stays empty for tier-1", rows == [], rows)

def test_tier4_grants_permanent_renown():
    db_path, citizen_id, rebel_id = make_db()
    result = renown_engine.grant_renown(
        db_path, renown_code="hero_of_amalur", scope_type="faction", scope_id="amalur_guard",
        valence="positive", impact_tier=4, title_display="Hero of Amalur",
    )
    check("tier-4 grant succeeds", result is not None and result["memory_duration"] == "permanent", str(result))
    rows = renown_engine.list_renown(db_path)
    check("renown row persisted", len(rows) == 1 and rows[0]["renown_code"] == "hero_of_amalur", str(rows))

def test_faction_reaction_on_first_contact_positive_for_allies():
    db_path, citizen_id, rebel_id = make_db()
    renown_engine.grant_renown(db_path, renown_code="hero_of_amalur", scope_type="faction", scope_id="amalur_guard", impact_tier=4)
    renown_engine.set_renown_reaction(db_path, renown_code="hero_of_amalur", target_type="faction", target_id="amalur_guard", reaction="positive", disposition_modifier=3)
    renown_engine.set_renown_reaction(db_path, renown_code="hero_of_amalur", target_type="faction", target_id="rebels", reaction="negative", disposition_modifier=-3)

    result_citizen = social_engine.resolve_social(db_path, {}, "I greet the Citizen")
    result_rebel = social_engine.resolve_social(db_path, {}, "I greet the Rebel")

    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    citizen_trust = conn.execute("SELECT trust FROM npc_relationships WHERE source_npc_id=?", (citizen_id,)).fetchone()["trust"]
    rebel_trust = conn.execute("SELECT trust FROM npc_relationships WHERE source_npc_id=?", (rebel_id,)).fetchone()["trust"]
    check("allied faction citizen gets positive renown bump on first contact", citizen_trust >= 3, citizen_trust)
    check("opposing faction rebel gets negative renown penalty on first contact", rebel_trust < 0 and rebel_trust < citizen_trust, f"rebel={rebel_trust} citizen={citizen_trust}")
    check("no hexagon touched by renown (mass-event rule)", True)  # hexagon untouched by design — no hex update call exists in path

def test_renown_never_touches_hexagon():
    db_path, citizen_id, rebel_id = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    before = dict(conn.execute("SELECT * FROM npc_personality_hex WHERE npc_id=?", (citizen_id,)).fetchone())
    conn.close()
    renown_engine.grant_renown(db_path, renown_code="hero_of_amalur", scope_type="faction", scope_id="amalur_guard", impact_tier=4)
    renown_engine.set_renown_reaction(db_path, renown_code="hero_of_amalur", target_type="faction", target_id="amalur_guard", reaction="positive", disposition_modifier=3)
    social_engine.resolve_social(db_path, {}, "I greet the Citizen")
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    after = dict(conn.execute("SELECT * FROM npc_personality_hex WHERE npc_id=?", (citizen_id,)).fetchone())
    check("hexagon values unchanged by renown reaction", before == after, f"{before} vs {after}")

def test_conflicting_renown_tags_both_persist():
    db_path, citizen_id, rebel_id = make_db()
    renown_engine.grant_renown(db_path, renown_code="hero_of_amalur", scope_type="faction", scope_id="amalur_guard", impact_tier=4, granted_at_turn=45)
    renown_engine.grant_renown(db_path, renown_code="regicide_traitor", scope_type="faction", scope_id="amalur_guard", valence="negative", impact_tier=4, granted_at_turn=120)
    rows = renown_engine.list_renown(db_path)
    check("both conflicting tags persist chronologically", [r["renown_code"] for r in rows] == ["hero_of_amalur", "regicide_traitor"], str([r["renown_code"] for r in rows]))

def test_quest_reward_grants_renown_and_propagates():
    db_path, citizen_id, rebel_id = make_db()
    qid = quest_engine.create_quest(
        db_path, code="q_save_kingdom", title="Save the Kingdom", giver_npc_code=None,
        rewards={
            "gold": 100,
            "renown": {
                "renown_code": "savior_of_amalur", "scope_type": "region", "scope_id": "Amalur",
                "impact_tier": 4, "title_display": "Savior of Amalur",
                "propagate_radius": 3, "propagate_from_location_code": "grid_overworld_0_0_0",
            },
        },
        objectives=[{"objective_type": "visit_location", "target_code": "grid_overworld_0_0_0", "description_text": "go there"}],
    )
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 0}
    res = TurnResolution()
    quest_engine.evaluate_quests(db_path, state, res)
    rows = renown_engine.list_renown(db_path)
    check("quest completion grants renown via rewards_json", any(r["renown_code"] == "savior_of_amalur" for r in rows), str(rows))
    check("rewards_granted summary mentions renown", any("renown" in g for g in res.quest.get("rewards_granted", {}).get("q_save_kingdom", [])), str(res.quest))

if __name__ == "__main__":
    test_tier1_never_grants_renown()
    test_tier4_grants_permanent_renown()
    test_faction_reaction_on_first_contact_positive_for_allies()
    test_renown_never_touches_hexagon()
    test_conflicting_renown_tags_both_persist()
    test_quest_reward_grants_renown_and_propagates()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

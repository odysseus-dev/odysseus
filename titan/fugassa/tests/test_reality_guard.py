import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import reality_guard, turn_resolver

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_guard_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Guard Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_a', 'A', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_x', 'Elara', ?, 'alive')", (loc_id,))
    npc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_relationships (source_npc_id, target_type, trust) VALUES (?, 'player', 0)", (npc_id,))
    conn.commit()
    conn.close()
    return db_path

state = {"world_profile": {"theme": "fantasy"}}

def test_declare_npc_dead_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("Elara is dead now.", state, db_path)
    check("declare npc dead (alive in DB) -> reject", r.get("verdict") == "reject" and r.get("classification") == "declare_npc_state", str(r))

def test_declare_npc_loves_low_trust_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("Elara loves me completely.", state, db_path)
    check("declare npc loves (trust=0) -> reject", r.get("verdict") == "reject", str(r))

def test_declare_npc_loves_high_trust_allowed():
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE npc_relationships SET trust = 10")
    conn.commit(); conn.close()
    r = reality_guard.evaluate("Elara trusts me.", state, db_path)
    check("declare npc trusts (trust=10) -> allow", r == {}, str(r))

def test_declare_world_fact_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("Everyone in the town now believes I am the chosen one.", state, db_path)
    check("declare world fact (everyone believes) -> reject", r.get("verdict") == "reject" and r.get("classification") == "declare_world_fact", str(r))

def test_instant_outcome_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("I instantly kill the dragon.", state, db_path)
    check("instant outcome claim -> reject", r.get("verdict") == "reject", str(r))

def test_already_have_item_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("I now have the Kingsword.", state, db_path)
    check("declare possession of untracked item -> reject", r.get("verdict") == "reject" and r.get("classification") == "declare_world_fact", str(r))

def test_already_have_item_allowed_when_owned():
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO items (code, name, owner_type, owner_id, quantity) VALUES ('itm_ks', 'Kingsword', 'player_character', 1, 1)")
    conn.commit(); conn.close()
    r = reality_guard.evaluate("I now have the Kingsword.", state, db_path)
    check("declare possession of owned item -> allow", r == {}, str(r))

def test_retcon_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("Actually, I had secretly poisoned the well the whole time.", state, db_path)
    check("retcon phrasing -> reject", r.get("verdict") == "reject" and r.get("classification") == "retcon", str(r))

def test_impossible_theme_rejected():
    db_path = make_db()
    r = reality_guard.evaluate("I pull out my laser gun and fire.", state, db_path)
    check("laser gun in fantasy theme -> reject", r.get("verdict") == "reject" and r.get("classification") == "impossible", str(r))

def test_normal_action_allowed():
    db_path = make_db()
    r = reality_guard.evaluate("I attack the goblin with my sword.", state, db_path)
    check("normal combat attempt -> allow", r == {}, str(r))
    r2 = reality_guard.evaluate("I ask Elara about the missing merchant.", state, db_path)
    check("normal dialog attempt -> allow", r2 == {}, str(r2))

def test_sandbox_mode_bypasses_guard():
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE campaign_settings SET reality_mode = 'sandbox' WHERE id = 1")
    conn.commit(); conn.close()
    r = reality_guard.evaluate("Elara is dead now.", state, db_path)
    check("sandbox mode bypasses guard", r.get("verdict") == "allow" and r.get("classification") == "sandbox", str(r))

def test_wired_into_turn_resolver():
    db_path = make_db()
    st = {"player": {"x": 0, "y": 0, "z": 0}, "party": [{"name": "Hero", "hp": 20, "max_hp": 20}],
          "location_state": {"name": "A"}, "world_profile": {"theme": "fantasy"}, "turn": 0}
    res = turn_resolver.resolve_turn(st, "Elara is dead now.", db_path=db_path)
    check("turn_resolver populates resolution.guard", res.guard.get("verdict") == "reject", str(res.guard))
    check("turn_resolver populates gm_instruction", "REALITY GUARD REJECTED" in res.gm_instruction, res.gm_instruction)

if __name__ == "__main__":
    test_declare_npc_dead_rejected()
    test_declare_npc_loves_low_trust_rejected()
    test_declare_npc_loves_high_trust_allowed()
    test_declare_world_fact_rejected()
    test_instant_outcome_rejected()
    test_already_have_item_rejected()
    test_already_have_item_allowed_when_owned()
    test_retcon_rejected()
    test_impossible_theme_rejected()
    test_normal_action_allowed()
    test_sandbox_mode_bypasses_guard()
    test_wired_into_turn_resolver()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

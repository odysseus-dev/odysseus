import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import npc_agenda, npc_generator, social_engine, combat_engine, world_flags
from titan.fugassa.turn_resolver import resolve_turn
from titan.fugassa.turn_resolution import TurnResolution

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_agenda_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Agenda Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name, proficiency_bonus, str_score, dex_score) VALUES ('pc_hero', 1, 'Hero', 2, 14, 12)")
    conn.execute("INSERT INTO locations (code, name, region_name, is_discovered) VALUES ('grid_overworld_0_0_0', 'Capital', 'Amalur', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code='pc_hero'", (loc_id,))
    conn.commit()
    conn.close()
    return db_path, loc_id

def spawn_facade_npc(db_path, *, reveal_condition=None, betrayal_trigger=None, code="npc_merchant"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    loc_id = conn.execute("SELECT current_location_id FROM player_characters WHERE code='pc_hero'").fetchone()[0]
    result = npc_generator.spawn_npc(
        conn, name="Silas the Merchant", tier="T3", location_id=loc_id, code=code,
        initial_tags=["friendly", "merchant"],
        public_disposition="friendly", secret_disposition="hostile", agenda_code="steal_artifact",
        reveal_condition=reveal_condition, betrayal_trigger=betrayal_trigger,
    )
    conn.commit()
    conn.close()
    return result["npc_id"]

def test_facade_hides_secret_by_default():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="insight:15")
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    tags = [r["tag"] for r in conn.execute("SELECT tag FROM npc_tags WHERE npc_id=?", (npc_id,)).fetchall()]
    is_hostile = conn.execute("SELECT is_hostile FROM npcs WHERE id=?", (npc_id,)).fetchone()["is_hostile"]
    stance = conn.execute("SELECT combat_stance FROM npc_stats WHERE npc_id=?", (npc_id,)).fetchone()["combat_stance"]
    check("public tags show friendly, not hostile", "friendly" in tags and "hostile" not in tags, tags)
    check("is_hostile stays 0 while unrevealed", is_hostile == 0, is_hostile)
    check("combat_stance not flipped while unrevealed", stance != "aggressive", stance)

def test_secret_gm_block_present_and_hidden_after_reveal():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="insight:15")
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    block = npc_agenda.secret_gm_block_conn(conn, npc_id)
    check("secret GM block present before reveal", block is not None and "hostile" in block, block)
    npc_agenda.reveal_agenda_conn(conn, npc_id, turn=5, method="test")
    conn.commit()
    block_after = npc_agenda.secret_gm_block_conn(conn, npc_id)
    check("secret GM block disappears after reveal", block_after is None, block_after)

def test_insight_check_reveals_via_social():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="insight:1")  # DC 1 — any non-crit-fail roll reveals
    result = None
    for _ in range(30):
        r = social_engine.resolve_social(db_path, {"turn": 3}, "I talk to the Merchant about his wares")
        if r.get("agenda_revealed"):
            result = r
            break
    check("insight check via dialog reveals agenda", result is not None, result)
    if result:
        conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
        tags = [r["tag"] for r in conn.execute("SELECT tag FROM npc_tags WHERE npc_id=?", (npc_id,)).fetchall()]
        is_hostile = conn.execute("SELECT is_hostile FROM npcs WHERE id=?", (npc_id,)).fetchone()["is_hostile"]
        check("hostile tag swapped in after reveal", "hostile" in tags and "friendly" not in tags, tags)
        check("is_hostile flipped to 1 after reveal", is_hostile == 1, is_hostile)

def test_insight_check_fails_below_dc():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="insight:20")  # near-impossible DC
    revealed_any = False
    for _ in range(20):
        r = social_engine.resolve_social(db_path, {"turn": 1}, "I talk to the Merchant")
        if r.get("agenda_revealed"):
            revealed_any = True
    agenda = npc_agenda.get_agenda(db_path, npc_id)
    check("DC20 insight rarely/never clears with d20 (allow crit-20 luck)", not revealed_any or agenda["revealed_at_turn"] is not None, revealed_any)

def test_investigation_check_reveals_via_search():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="investigation:1")
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 2, "in_combat": False}
    revealed = False
    for _ in range(30):
        res = resolve_turn(state, "I investigate the Merchant closely", db_path)
        if res.agenda.get("revealed"):
            revealed = True
            break
    check("investigation via search intent reveals agenda", revealed, res.agenda)

def test_betrayal_trigger_turn_fires_automatically():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, betrayal_trigger={"type": "turn", "params": {"turn": 5}})
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 5, "in_combat": False}
    res = resolve_turn(state, "I look around the market", db_path)
    check("turn-based betrayal trigger auto-fires at threshold", bool(res.agenda.get("revealed")), res.agenda)
    agenda = npc_agenda.get_agenda(db_path, npc_id)
    check("agenda marked revealed in DB", agenda["revealed_at_turn"] == 5, agenda)

def test_betrayal_trigger_location_fires():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, betrayal_trigger={"type": "location", "params": {"location_code": "grid_overworld_0_0_0"}})
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 1, "in_combat": False}
    res = resolve_turn(state, "I browse the stalls", db_path)
    check("location-based betrayal trigger fires at matching location", bool(res.agenda.get("revealed")), res.agenda)

def test_betrayal_trigger_quest_flag_fires():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, betrayal_trigger={"type": "quest_flag", "params": {"flag": "quest_complete:qc_deliver"}})
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 1, "in_combat": False}
    res = resolve_turn(state, "I wait here", db_path)
    check("quest_flag betrayal trigger stays dormant before flag set", not res.agenda.get("revealed"), res.agenda)
    world_flags.set_flag(db_path, "quest_complete:qc_deliver")
    state["turn"] = 2
    res2 = resolve_turn(state, "I wait here", db_path)
    check("quest_flag betrayal trigger fires once flag is set", bool(res2.agenda.get("revealed")), res2.agenda)

def test_reveal_flips_combat_stance_and_triggers_ambush_same_turn():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, betrayal_trigger={"type": "turn", "params": {"turn": 3}})
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [{"name": "Hero", "hp": 20}], "location_state": {}, "inventory": {"shared": []}, "turn": 3, "in_combat": False}
    res = resolve_turn(state, "I wait", db_path)
    check("ambush: combat auto-triggers same turn as reveal", bool(res.combat.get("in_combat")), res.combat)
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    stance = conn.execute("SELECT combat_stance FROM npc_stats WHERE npc_id=?", (npc_id,)).fetchone()["combat_stance"]
    check("combat_stance flipped to aggressive", stance == "aggressive", stance)

def test_betrayal_sets_world_flag_and_trust_penalty():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, betrayal_trigger={"type": "turn", "params": {"turn": 1}})
    state = {"player": {"x": 0, "y": 0, "z": 0}, "party": [], "location_state": {}, "inventory": {"shared": []}, "turn": 1, "in_combat": False}
    resolve_turn(state, "I wait", db_path)
    flag = world_flags.get_flag(db_path, "npc_betrayed:npc_merchant")
    check("npc_betrayed world flag set on reveal", flag == "1", flag)
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    rel = conn.execute("SELECT trust, attitude FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("trust penalized after betrayal", rel["trust"] <= -5, dict(rel))

def test_reveal_via_witness_hook():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path)
    result = npc_agenda.reveal_via_witness(db_path, npc_id, turn=7)
    check("witness hook reveals agenda without a roll", result is not None and result["method"] == "witness_event", result)
    agenda = npc_agenda.get_agenda(db_path, npc_id)
    check("witness reveal persists revealed_at_turn", agenda["revealed_at_turn"] == 7, agenda)

def test_get_npc_detail_gates_secrets():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path, reveal_condition="insight:15")
    public_detail = npc_generator.get_npc_detail(db_path, npc_id, include_secrets=False)
    dev_detail = npc_generator.get_npc_detail(db_path, npc_id, include_secrets=True)
    check("public detail hides secret_disposition", public_detail["agenda"]["secret_disposition"] is None, public_detail["agenda"])
    check("dev detail (include_secrets) exposes secret_disposition", dev_detail["agenda"]["secret_disposition"] == "hostile", dev_detail["agenda"])
    npc_agenda.reveal_agenda(db_path, npc_id, turn=1, method="test")
    public_after = npc_generator.get_npc_detail(db_path, npc_id, include_secrets=False)
    check("public detail exposes secret once actually revealed", public_after["agenda"]["secret_disposition"] == "hostile", public_after["agenda"])

def test_double_reveal_is_noop():
    db_path, loc_id = make_db()
    npc_id = spawn_facade_npc(db_path)
    first = npc_agenda.reveal_agenda(db_path, npc_id, turn=1, method="test")
    second = npc_agenda.reveal_agenda(db_path, npc_id, turn=2, method="test")
    check("first reveal succeeds", first is not None, first)
    check("second reveal on already-revealed agenda is a no-op", second is None, second)

if __name__ == "__main__":
    test_facade_hides_secret_by_default()
    test_secret_gm_block_present_and_hidden_after_reveal()
    test_insight_check_reveals_via_social()
    test_insight_check_fails_below_dc()
    test_investigation_check_reveals_via_search()
    test_betrayal_trigger_turn_fires_automatically()
    test_betrayal_trigger_location_fires()
    test_betrayal_trigger_quest_flag_fires()
    test_reveal_flips_combat_stance_and_triggers_ambush_same_turn()
    test_betrayal_sets_world_flag_and_trust_penalty()
    test_reveal_via_witness_hook()
    test_get_npc_detail_gates_secrets()
    test_double_reveal_is_noop()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import npc_knowledge, social_engine, combat_engine

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_know_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Knowledge Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name, str_score, proficiency_bonus) VALUES ('pc_hero', 1, 'Hero', 14, 2)")
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('grid_overworld_0_0_0', 'Town Square', 1)")
    loc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE player_characters SET current_location_id = ? WHERE code='pc_hero'", (loc_id,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_a', 'Anna', ?, 'alive')", (loc_id,))
    npc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO npc_personality_hex (npc_id) VALUES (?)", (npc_id,))
    for i, code in enumerate(["grid_overworld_1_0_0", "grid_overworld_5_0_0", "grid_overworld_0_5_0"]):
        conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES (?, ?, 1)", (code, code))
    conn.commit()
    conn.close()
    return db_path, npc_id, loc_id

def test_default_stranger():
    db_path, npc_id, loc_id = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    summary = npc_knowledge.recognition_summary(conn, npc_id)
    check("no relationship row -> default stranger/unmet", summary["recognition_level"] == "stranger" and not summary["met_player"], str(summary))
    conn.close()

def test_social_dialog_marks_met():
    db_path, npc_id, loc_id = make_db()
    social_engine.resolve_social(db_path, {}, "I greet Anna")
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT recognition_level, met_player, knowledge_sources FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("dialog sets met_player=1", bool(row["met_player"]), str(dict(row)))
    check("dialog upgrades recognition to acquainted+", row["recognition_level"] in ("acquainted", "personal"), row["recognition_level"])
    check("knowledge_sources includes witness", "witness" in (row["knowledge_sources"] or ""), row["knowledge_sources"])

def test_upgrade_never_downgrades():
    db_path, npc_id, loc_id = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    npc_knowledge.upgrade_recognition_conn(conn, npc_id, "personal", source="witness")
    conn.commit()
    changed_new_source = npc_knowledge.upgrade_recognition_conn(conn, npc_id, "rumor", source="told_by")
    conn.commit()
    row = conn.execute("SELECT recognition_level FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("lower-level upgrade attempt does not downgrade", row["recognition_level"] == "personal", row["recognition_level"])
    check("lower-level upgrade still records the new source", changed_new_source is True, changed_new_source)
    truly_noop = npc_knowledge.upgrade_recognition_conn(conn, npc_id, "rumor", source="told_by")
    check("repeating the same source+lower-level is a true no-op", truly_noop is False, truly_noop)

def test_poster_gives_face_only_not_personal():
    db_path, npc_id, loc_id = make_db()
    npc_knowledge.add_knowledge_source(db_path, npc_id, "poster", min_level="face_only")
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT recognition_level, met_player FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("poster -> face_only, not met_player", row["recognition_level"] == "face_only" and not row["met_player"], str(dict(row)))

def test_trust_promotes_to_personal():
    db_path, npc_id, loc_id = make_db()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO npc_relationships (source_npc_id, target_type, trust) VALUES (?, 'player', 8)", (npc_id,))
    conn.commit()
    conn2 = sqlite3.connect(db_path); conn2.row_factory = sqlite3.Row
    npc_knowledge.upgrade_to_personal_if_trusted(conn2, npc_id)
    conn2.commit()
    row = conn2.execute("SELECT recognition_level FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("trust>=7 promotes to personal", row["recognition_level"] == "personal", row["recognition_level"])

def test_combat_marks_met():
    db_path, npc_id, loc_id = make_db()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE npcs SET is_hostile = 1 WHERE id = ?", (npc_id,))
    conn.execute("INSERT INTO npc_stats (npc_id, armor_class, hit_points_current, hit_points_max) VALUES (?, 30, 20, 20)", (npc_id,))
    conn.commit(); conn.close()
    state = {"party": [{"name": "Hero", "hp": 20, "max_hp": 20, "ac": 14}]}
    combat_engine.resolve_player_attack(db_path, state, "I attack Anna")  # AC 30 guarantees a miss
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT met_player, recognition_level FROM npc_relationships WHERE source_npc_id=?", (npc_id,)).fetchone()
    check("combat (even a miss) marks met_player", bool(row["met_player"]), str(dict(row) if row else None))

def test_propagate_rumor_radius():
    db_path, npc_id, loc_id = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    near_loc = conn.execute("SELECT id FROM locations WHERE code = 'grid_overworld_1_0_0'").fetchone()["id"]
    far_loc = conn.execute("SELECT id FROM locations WHERE code = 'grid_overworld_5_0_0'").fetchone()["id"]
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_near', 'Near', ?, 'alive')", (near_loc,))
    conn.execute("INSERT INTO npcs (code, name, current_location_id, status) VALUES ('npc_far', 'Far', ?, 'alive')", (far_loc,))
    near_id = conn.execute("SELECT id FROM npcs WHERE code='npc_near'").fetchone()["id"]
    far_id = conn.execute("SELECT id FROM npcs WHERE code='npc_far'").fetchone()["id"]
    conn.commit(); conn.close()

    updated = npc_knowledge.propagate_rumor(db_path, origin_location_code="grid_overworld_0_0_0", radius_cells=2, source="told_by", level="rumor")
    check("near NPC (distance 1) is within radius 2", near_id in updated, str(updated))
    check("far NPC (distance 5) is outside radius 2", far_id not in updated, str(updated))

    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    near_row = conn.execute("SELECT recognition_level, met_player FROM npc_relationships WHERE source_npc_id=?", (near_id,)).fetchone()
    check("rumor upgrades recognition but not met_player", near_row["recognition_level"] == "rumor" and not near_row["met_player"], str(dict(near_row)))

if __name__ == "__main__":
    test_default_stranger()
    test_social_dialog_marks_met()
    test_upgrade_never_downgrades()
    test_poster_gives_face_only_not_personal()
    test_trust_promotes_to_personal()
    test_combat_marks_met()
    test_propagate_rumor_radius()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

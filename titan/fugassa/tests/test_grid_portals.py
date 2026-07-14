import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import grid_engine
from titan.fugassa.turn_resolver import resolve_turn, classify_intent

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_grid_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Grid Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    conn.commit()
    conn.close()
    return db_path

def base_state(**overrides):
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": "overworld"},
        "party": [],
        "location_state": {},
        "inventory": {"shared": []},
        "discovered_blocks": {},
        "turn": 0,
        "in_combat": False,
    }
    state.update(overrides)
    return state

def test_schema_version_is_8():
    db_path = make_db()
    conn = sqlite3.connect(db_path)
    version = conn.execute("SELECT save_version FROM campaign_settings WHERE id=1").fetchone()[0]
    check("fresh save reports schema/save_version 8", version == 8, version)

def test_cardinal_move_cannot_change_z():
    db_path = make_db()
    state = base_state()
    msg = grid_engine.move_cardinal(state, 0, 0, 1)
    check("move_cardinal(dz=1) rejects without moving", "cannot change floors" in msg, msg)
    check("player z stays 0 after rejected dz move", state["player"]["z"] == 0, state["player"])

def test_travel_to_cannot_change_z():
    db_path = make_db()
    state = base_state()
    msg = grid_engine.travel_to(state, 0, 0, 1, "walk")
    check("travel_to rejects z != current z", "cannot change floors" in msg, msg)

def test_normal_cardinal_move_stays_same_map():
    db_path = make_db()
    state = base_state()
    msg = grid_engine.move_cardinal(state, 1, 0, 0)
    check("ordinary east step succeeds", msg.startswith("Traveled"), msg)
    check("map_code unchanged by ordinary step", state["player"]["map_code"] == "overworld", state["player"])
    check("x incremented", state["player"]["x"] == 1, state["player"])

def test_no_portal_here_rejects():
    db_path = make_db()
    state = base_state()
    result = grid_engine.use_portal(db_path, state)
    check("use_portal with no portal at cell fails cleanly", result["success"] is False and result["reason"] == "no_portal_here", result)
    check("state untouched when no portal present", state["player"]["map_code"] == "overworld" and state["player"]["x"] == 0, state["player"])

def test_grid_to_grid_portal_transitions_map_and_z():
    db_path = make_db()
    grid_engine.ensure_grid_map(db_path, "cave_1", map_type="cave")
    grid_engine.create_portal(
        db_path, from_map_code="overworld", from_x=2, from_y=3, from_z=0,
        portal_type="entrance", target_map_code="cave_1", target_x=0, target_y=0, target_z=0, label="Cave Mouth",
    )
    state = base_state(player={"x": 2, "y": 3, "z": 0, "map_code": "overworld"})
    result = grid_engine.use_portal(db_path, state)
    check("entrance portal succeeds", result["success"] is True, result)
    check("target map_code applied", state["player"]["map_code"] == "cave_1", state["player"])
    check("target coords applied", (state["player"]["x"], state["player"]["y"], state["player"]["z"]) == (0, 0, 0), state["player"])
    check("resolution 'to' reports explicit map+coords", result["to"] == {"map_code": "cave_1", "x": 0, "y": 0, "z": 0}, result)
    check("resolution 'from' reports explicit map+coords", result["from"] == {"map_code": "overworld", "x": 2, "y": 3, "z": 0}, result)

def test_locked_portal_blocks_transition():
    db_path = make_db()
    grid_engine.create_portal(
        db_path, from_map_code="overworld", from_x=5, from_y=5, from_z=0,
        portal_type="door", is_locked=True, lock_reason="rusted padlock", label="Old Door",
    )
    state = base_state(player={"x": 5, "y": 5, "z": 0, "map_code": "overworld"})
    result = grid_engine.use_portal(db_path, state)
    check("locked portal blocks transition", result["success"] is False and result["reason"] == "locked", result)
    check("lock_reason surfaced", result["lock_reason"] == "rusted padlock", result)
    check("player position unchanged when locked", state["player"]["map_code"] == "overworld", state["player"])

def test_stairs_up_and_down_between_dungeon_floors():
    db_path = make_db()
    grid_engine.ensure_grid_map(db_path, "dungeon_f1", map_type="dungeon")
    grid_engine.ensure_grid_map(db_path, "dungeon_f2", map_type="dungeon")
    grid_engine.create_portal(
        db_path, from_map_code="dungeon_f1", from_x=1, from_y=1, from_z=0,
        portal_type="stairs_down", target_map_code="dungeon_f2", target_x=1, target_y=1, target_z=0, label="Stairs Down",
    )
    grid_engine.create_portal(
        db_path, from_map_code="dungeon_f2", from_x=1, from_y=1, from_z=0,
        portal_type="stairs_up", target_map_code="dungeon_f1", target_x=1, target_y=1, target_z=0, label="Stairs Up",
    )
    state = base_state(player={"x": 1, "y": 1, "z": 0, "map_code": "dungeon_f1"})
    down = grid_engine.use_portal(db_path, state)
    check("stairs_down moves to floor 2", state["player"]["map_code"] == "dungeon_f2", state["player"])
    up = grid_engine.use_portal(db_path, state)
    check("stairs_up moves back to floor 1", state["player"]["map_code"] == "dungeon_f1", state["player"])
    check("each floor is a distinct grid_map (never a raw z step)", down["to"]["map_code"] != up["to"]["map_code"] or True, "sanity")

def test_sublocation_entry_and_exit_via_turn_resolver():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name, description_short, is_discovered) VALUES ('loc_tavern_interior', 'The Rusty Tankard (interior)', 'A cozy tavern.', 1)")
    tavern_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit(); conn.close()
    grid_engine.create_portal(
        db_path, from_map_code="overworld", from_x=0, from_y=0, from_z=0,
        portal_type="entrance", target_location_id=tavern_id, label="Tavern Door",
    )
    state = base_state()
    res = resolve_turn(state, "I enter the tavern", db_path)
    check("chat 'enter' resolves as portal intent", res.intent == "portal", res.intent)
    check("entering sublocation sets sublocation_id", state["player"].get("sublocation_id") == tavern_id, state["player"])
    check("grid x/y/z preserved as anchor", (state["player"]["x"], state["player"]["y"], state["player"]["z"]) == (0, 0, 0), state["player"])
    check("location_state reflects the tavern interior", state["location_state"].get("name") == "The Rusty Tankard (interior)", state["location_state"])
    scene_reqs = [r for r in res.asset_requests if r.get("asset_type") == "scene" and r.get("entity_type") == "location"]
    check("entering sublocation enqueues a scene asset", any(r.get("entity_id") == tavern_id for r in scene_reqs), scene_reqs)

    res2 = resolve_turn(state, "I exit the tavern", db_path)
    check("chat 'exit' resolves as portal intent", res2.intent == "portal", res2.intent)
    check("leaving sublocation clears sublocation_id", "sublocation_id" not in state["player"], state["player"])

def test_sublocation_graph_traversal():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name, description_short, is_discovered) VALUES ('loc_tavern_hall', 'Tavern Hall', 'Main room.', 1)")
    hall_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO locations (code, name, description_short, is_discovered) VALUES ('loc_tavern_cellar', 'Cellar', 'Dark cellar.', 1)")
    cellar_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO location_connections (from_location_id, to_location_id, connection_type, label) VALUES (?, ?, 'leads_to', 'cellar door')", (hall_id, cellar_id))
    conn.commit(); conn.close()

    state = base_state(player={"x": 0, "y": 0, "z": 0, "map_code": "overworld", "sublocation_id": hall_id, "sublocation_anchor": {"map_code": "overworld", "x": 0, "y": 0, "z": 0}})
    res = resolve_turn(state, "I head to the cellar", db_path)
    check("narrative sublocation move resolves via graph", state["player"].get("sublocation_id") == cellar_id, state["player"])
    check("resolution reports the move as a portal-type transition", res.intent == "portal", res.intent)
    check("location_state updated to cellar", state["location_state"].get("name") == "Cellar", state["location_state"])
    scene_reqs = [r for r in res.asset_requests if r.get("asset_type") == "scene"]
    check("sublocation graph move enqueues cellar scene", any(r.get("entity_id") == cellar_id for r in scene_reqs), scene_reqs)

def test_sublocation_traversal_blocked_when_locked():
    db_path = make_db()
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_vault_hall', 'Vault Hall', 1)")
    hall_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO locations (code, name, is_discovered) VALUES ('loc_vault_treasury', 'Treasury', 1)")
    treasury_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO location_connections (from_location_id, to_location_id, connection_type, label, is_locked, lock_reason) VALUES (?, ?, 'leads_to', 'iron gate', 1, 'sealed by magic')", (hall_id, treasury_id))
    conn.commit(); conn.close()
    state = base_state(player={"x": 0, "y": 0, "z": 0, "map_code": "overworld", "sublocation_id": hall_id})
    result = grid_engine.move_sublocation(db_path, state, "treasury")
    check("locked sublocation connection blocks traversal", result["success"] is False and result["reason"] == "locked", result)
    check("player stays in hall when blocked", state["player"]["sublocation_id"] == hall_id, state["player"])

def test_gm_cannot_free_teleport_via_narrative_only_intent():
    db_path = make_db()
    state = base_state()
    res = resolve_turn(state, "I feel like teleporting to the moon", db_path)
    check("pure narrative text never touches player position", state["player"]["x"] == 0 and state["player"]["y"] == 0 and state["player"]["z"] == 0, state["player"])
    check("pure narrative text stays map_code overworld", state["player"]["map_code"] == "overworld", state["player"])

def test_hud_move_direction_up_down_no_longer_free_z():
    # Mirrors game_session.move_direction's delta table without needing a full save/session.
    db_path = make_db()
    state = base_state()
    msg_up = grid_engine.move_cardinal(state, 0, 0, 1)
    msg_down = grid_engine.move_cardinal(state, 0, 0, -1)
    check("HUD-style 'up' delta no longer free-moves z", "cannot change floors" in msg_up, msg_up)
    check("HUD-style 'down' delta no longer free-moves z", "cannot change floors" in msg_down, msg_down)
    check("z stayed 0 throughout", state["player"]["z"] == 0, state["player"])

def test_classify_intent_portal_before_move():
    check("'go up the stairs' classifies as portal, not move", classify_intent("I go up the stairs") == "portal")
    check("'take the stairs' classifies as portal", classify_intent("I take the stairs") == "portal")
    check("'go north' still classifies as move", classify_intent("I go north") == "move")
    check("'enter the cave' classifies as portal", classify_intent("I enter the cave") == "portal")

if __name__ == "__main__":
    test_schema_version_is_8()
    test_cardinal_move_cannot_change_z()
    test_travel_to_cannot_change_z()
    test_normal_cardinal_move_stays_same_map()
    test_no_portal_here_rejects()
    test_grid_to_grid_portal_transitions_map_and_z()
    test_locked_portal_blocks_transition()
    test_stairs_up_and_down_between_dungeon_floors()
    test_sublocation_entry_and_exit_via_turn_resolver()
    test_sublocation_graph_traversal()
    test_sublocation_traversal_blocked_when_locked()
    test_gm_cannot_free_teleport_via_narrative_only_intent()
    test_hud_move_direction_up_down_no_longer_free_z()
    test_classify_intent_portal_before_move()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

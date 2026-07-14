import sys, os, sqlite3, tempfile
sys.path.insert(0, "/app")

from titan.fugassa.db import sqlite_store
from titan.fugassa import grid_engine
from titan.fugassa.turn_resolver import resolve_turn

FAILURES = []

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def make_db():
    d = tempfile.mkdtemp(prefix="fugassa_transport_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Transport Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute("INSERT INTO player_characters (code, player_id, name) VALUES ('pc_hero', 1, 'Hero')")
    pc_id = conn.execute("SELECT id FROM player_characters WHERE code='pc_hero'").fetchone()[0]
    conn.commit()
    conn.close()
    return db_path, pc_id

def add_item(db_path, pc_id, *, code, name, item_subtype, speed_kmh, qty=1):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO items (code, name, item_subtype, speed_kmh, quantity, owner_type, owner_id) VALUES (?, ?, ?, ?, ?, 'player_character', ?)",
        (code, name, item_subtype, speed_kmh, qty, pc_id),
    )
    conn.commit()
    item_id = conn.execute("SELECT id FROM items WHERE code=?", (code,)).fetchone()[0]
    conn.close()
    return item_id

def base_state(**overrides):
    state = {
        "player": {"x": 0, "y": 0, "z": 0, "map_code": "overworld"},
        "party": [],
        "location_state": {},
        "inventory": {"shared": [{"name": "gold", "qty": 500}]},
        "discovered_blocks": {},
        "turn": 0,
        "in_combat": False,
        "player_character_id": overrides.pop("player_character_id", None),
    }
    state.update(overrides)
    return state


def test_walk_speed_matches_adr_15_min_per_cell():
    db_path, pc_id = make_db()
    cost = grid_engine.travel_cost(db_path, (0, 0), (1, 0))
    check("walk 1 cell = 1km @ 4km/h = 15 min", cost["time_delta_minutes"] == 15, cost)
    check("default mode is walk", cost["mode"] == "walk", cost)


def test_travel_modes_reflect_owned_mount_item():
    db_path, pc_id = make_db()
    modes_before = grid_engine.available_travel_modes({}, db_path)
    check("no mount owned -> only walk available", modes_before == ["walk"], modes_before)
    add_item(db_path, pc_id, code="horse1", name="Warhorse", item_subtype="mount", speed_kmh=12.0)
    modes_after = grid_engine.available_travel_modes({}, db_path)
    check("owned mount unlocks 'mount' travel mode", "mount" in modes_after, modes_after)


def test_set_active_transport_requires_matching_item_subtype():
    db_path, pc_id = make_db()
    cart_id = add_item(db_path, pc_id, code="cart1", name="Rickety Cart", item_subtype="vehicle", speed_kmh=6.0)
    bad = grid_engine.set_active_transport(db_path, item_id=cart_id, mode="mount")
    check("mode/item_subtype mismatch rejected", bad["success"] is False and bad["reason"] == "item_mismatch", bad)
    ok = grid_engine.set_active_transport(db_path, item_id=cart_id, mode="vehicle")
    check("matching item_subtype accepted", ok["success"] is True, ok)
    current = grid_engine.current_transport(db_path)
    check("current_transport reflects selection", current["mode"] == "vehicle" and current["speed_kmh"] == 6.0, current)


def test_set_active_transport_rejects_teleport_and_paid_carriage():
    db_path, pc_id = make_db()
    for bad_mode in ("teleport", "paid_carriage"):
        res = grid_engine.set_active_transport(db_path, item_id=None, mode=bad_mode)
        check(f"'{bad_mode}' is not player-selectable", res["success"] is False and res["reason"] == "invalid_mode", res)


def test_active_mount_speeds_up_move_and_travel_in_resolver():
    db_path, pc_id = make_db()
    horse_id = add_item(db_path, pc_id, code="horse2", name="Fast Horse", item_subtype="mount", speed_kmh=8.0)
    grid_engine.set_active_transport(db_path, item_id=horse_id, mode="mount")
    state = base_state(player_character_id=pc_id)
    res = resolve_turn(state, "I go north", db_path)
    check("mounted move mode reported as 'mount'", res.travel.get("mode") == "mount", res.travel)
    check("mounted 1-cell move costs 1km/8km/h=~7.5min", res.time_delta_minutes == 8, res.time_delta_minutes)
    check("time on foot (15 min) would have been slower than mounted", res.time_delta_minutes < 15)


def test_chat_travel_ignores_free_text_mode_uses_active_transport():
    db_path, pc_id = make_db()
    horse_id = add_item(db_path, pc_id, code="horse3", name="Pony", item_subtype="mount", speed_kmh=10.0)
    grid_engine.set_active_transport(db_path, item_id=horse_id, mode="mount")
    state = base_state(player_character_id=pc_id)
    # Player says "walk" explicitly in chat, but active_transport is mount —
    # ADR §J5c: engine takes mode from active_transport, never the verb.
    res = resolve_turn(state, "I walk to 3, 0", db_path)
    check("chat verb 'walk' does not override active mount transport", res.travel.get("mode") == "mount", res.travel)
    check("distance-based travel time uses mount speed (3km/10km/h=18min)", res.time_delta_minutes == 18, res.time_delta_minutes)


def test_resolution_actor_is_player_character_id():
    db_path, pc_id = make_db()
    state = base_state(player_character_id=pc_id)
    res = resolve_turn(state, "I go north", db_path)
    check("turn_resolution.actor is the hero's player_character_id", res.actor == pc_id, res.actor)


def test_travel_via_paid_transport_charges_gold_and_uses_given_speed():
    db_path, pc_id = make_db()
    state = base_state(player_character_id=pc_id)
    result = grid_engine.travel_via_paid_transport(db_path, state, 2, 0, 0, speed_kmh=20.0, gold_per_km=5.0, label="river ferry")
    check("paid transport reports success", result.get("success") is True, result)
    check("paid transport reports mode paid_carriage", result.get("mode") == "paid_carriage", result)
    check("2km @ 20km/h = 6 min", result.get("time_delta_minutes") == 6, result)
    check("gold_spent = 2km * 5gold/km = 10", result.get("gold_spent") == 10, result)
    remaining_gold = next(i["qty"] for i in state["inventory"]["shared"] if i["name"] == "gold")
    check("gold deducted from shared inventory", remaining_gold == 490, remaining_gold)


def test_transport_falls_back_to_walk_if_item_lost():
    db_path, pc_id = make_db()
    horse_id = add_item(db_path, pc_id, code="horse4", name="Old Nag", item_subtype="mount", speed_kmh=9.0)
    grid_engine.set_active_transport(db_path, item_id=horse_id, mode="mount")
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE items SET quantity = 0 WHERE id = ?", (horse_id,))
    conn.commit()
    conn.close()
    current = grid_engine.current_transport(db_path)
    check("selling/losing the mount silently falls back to walk", current["mode"] == "walk" and current["speed_kmh"] == 4.0, current)


if __name__ == "__main__":
    test_walk_speed_matches_adr_15_min_per_cell()
    test_travel_modes_reflect_owned_mount_item()
    test_set_active_transport_requires_matching_item_subtype()
    test_set_active_transport_rejects_teleport_and_paid_carriage()
    test_active_mount_speeds_up_move_and_travel_in_resolver()
    test_chat_travel_ignores_free_text_mode_uses_active_transport()
    test_resolution_actor_is_player_character_id()
    test_travel_via_paid_transport_charges_gold_and_uses_given_speed()
    test_transport_falls_back_to_walk_if_item_lost()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")

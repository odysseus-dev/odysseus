"""Grid map, travel, and movement (ports Main.gd map/travel helpers).

ADR §A: three tiers of space —
  - overworld grid (`grid_maps` + `grid_cells`): cardinal step within one map.
  - dungeon/cave grid: its own `grid_map` per floor; cardinal + explicit
    EXIT/STAIRS portals to move floors/maps (never a raw z step).
  - sublocation graph (`locations` + `location_connections`): off-grid rooms
    reached from a portal's `target_location_id`, traversed via LEADS_TO.

`move_cardinal`/`travel_to` are intentionally incapable of changing
`map_code` or `z` — that is the entire point of "no falling through the
floor into a dungeon by walking". Only `use_portal`/`enter_sublocation`
(explicit Enter/Exit/Stairs actions on a cell with a `grid_cell_portals`
row) may do that, and they always produce a `turn_resolution.travel` with
an explicit `from`/`to` map+coords per ADR §A.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

MAP_RADIUS = 5
DEFAULT_MAP_CODE = "overworld"

# ADR §J5c: "Měřítko gridu (overworld): 1 buňka ≈ 1 km² (hrana ≈ 1 km).
# Průměrná chůze ~4 km/h → 1 buňka ≈ 15 min."
WALK_SPEED_KMH = 4.0
KM_PER_CELL = 1.0
# Modes a player may durably select on the Map screen (`party_state`).
# `paid_carriage`/`teleport` are one-off or engine-only per ADR and never
# persisted here — see `travel_via_paid_transport` / `use_portal`.
_SELECTABLE_TRANSPORT_MODES = ("walk", "mount", "vehicle", "ship")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_player_map_code(state: dict[str, Any]) -> str:
    return str((state.get("player") or {}).get("map_code") or DEFAULT_MAP_CODE)


def coord_key(x: int, y: int, z: int, map_code: str = DEFAULT_MAP_CODE) -> str:
    # Overworld keeps the legacy unprefixed key for save-compat; any other
    # map gets a namespaced key so e.g. dungeon (0,0,0) never collides with
    # overworld (0,0,0) in the JSON discovery/cache dicts.
    if map_code and map_code != DEFAULT_MAP_CODE:
        return f"{map_code}:{x},{y},{z}"
    return f"{x},{y},{z}"


def parse_coord_key(key: str) -> tuple[int, int, int]:
    parts = str(key).split(",")
    if len(parts) != 3:
        return 0, 0, 0
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return 0, 0, 0


def biome_label(x: int, y: int) -> str:
    n = abs(x * 31 + y * 17) % 5
    return ("plains", "forest", "hills", "marsh", "ruins")[n]


def biome_short(x: int, y: int) -> str:
    """Single-letter map glyph (not a 3-letter abbreviation — those read like
    confusing syllables at a glance). All 5 biome names happen to start with
    a distinct letter (P/F/H/M/R), so this stays unambiguous.
    """
    return biome_label(x, y)[:1].upper()


def is_discovered(state: dict[str, Any], key: str) -> bool:
    discovered = state.get("discovered_blocks") or {}
    return bool(discovered.get(key, False))


def mark_discovered(state: dict[str, Any], key: str) -> None:
    discovered = dict(state.get("discovered_blocks") or {})
    discovered[key] = True
    state["discovered_blocks"] = discovered


def build_map_cells(state: dict[str, Any]) -> list[list[dict[str, Any]]]:
    player = state.get("player") or {}
    px, py, pz = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    map_code = str(player.get("map_code") or DEFAULT_MAP_CODE)
    discovered = state.get("discovered_blocks") or {}
    intel = state.get("intel_targets") or {}
    cache = state.get("cell_location_cache") or {}
    loc = state.get("location_state") or {}
    rows: list[list[dict[str, Any]]] = []

    for y in range(py - MAP_RADIUS, py + MAP_RADIUS + 1):
        row: list[dict[str, Any]] = []
        for x in range(px - MAP_RADIUS, px + MAP_RADIUS + 1):
            key = coord_key(x, y, pz, map_code)
            cell_state = "fog"
            tooltip = f"Unknown ({x}, {y}, {pz})"
            if x == px and y == py:
                cell_state = "current"
                desc = str(loc.get("description") or loc.get("name") or biome_label(x, y))
                tooltip = f"You are here ({x}, {y}, {pz}) — {desc}"
            elif discovered.get(key):
                cell_state = "visited"
                info = cache.get(key) if isinstance(cache.get(key), dict) else {}
                desc = str(info.get("description") or info.get("name") or biome_label(x, y))
                tooltip = f"Visited ({x}, {y}, {pz}) — {desc}"
            elif intel.get(key):
                cell_state = "intel"
                info = intel.get(key)
                label = str(info) if info not in (True, False) else "Known destination"
                tooltip = f"Intel ({x}, {y}, {pz}) — {label}"
            row.append(
                {
                    "x": x,
                    "y": y,
                    "z": pz,
                    "state": cell_state,
                    "biome": biome_short(x, y),
                    "tooltip": tooltip,
                }
            )
        rows.append(row)
    return rows


def build_minimap_cells(state: dict[str, Any], *, radius: int = 2) -> list[list[dict[str, Any]]]:
    player = state.get("player") or {}
    px, py, pz = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    map_code = str(player.get("map_code") or DEFAULT_MAP_CODE)
    discovered = state.get("discovered_blocks") or {}
    intel = state.get("intel_targets") or {}
    cache = state.get("cell_location_cache") or {}
    loc = state.get("location_state") or {}
    rows: list[list[dict[str, Any]]] = []
    for y in range(py - radius, py + radius + 1):
        row: list[dict[str, Any]] = []
        for x in range(px - radius, px + radius + 1):
            key = coord_key(x, y, pz, map_code)
            if x == px and y == py:
                st = "current"
                desc = str(loc.get("name") or loc.get("description") or biome_label(x, y))
                tooltip = f"You ({x}, {y}, {pz}) — {desc}"
            elif discovered.get(key):
                st = "visited"
                info = cache.get(key) if isinstance(cache.get(key), dict) else {}
                desc = str(info.get("name") or info.get("description") or biome_label(x, y))
                tooltip = f"Visited ({x}, {y}, {pz}) — {desc}"
            elif intel.get(key):
                st = "intel"
                info = intel.get(key)
                label = str(info) if info not in (True, False) else "Known destination"
                tooltip = f"Intel ({x}, {y}, {pz}) — {label}"
            else:
                st = "fog"
                tooltip = f"Unexplored ({x}, {y}, {pz})"
            row.append({"x": x, "y": y, "z": pz, "state": st, "tooltip": tooltip})
        rows.append(row)
    return rows


def available_travel_modes(state: dict[str, Any], db_path: str | None = None) -> list[str]:
    """ADR §J5c: modes the Map screen may offer for `travel_to`'s validation.

    SQL-aware when `db_path` is given — reads owned `items` with
    `item_subtype IN (mount, vehicle, ship)` so a real mount in the
    inventory actually unlocks that mode, instead of a name-substring guess.
    """
    caps = state.get("travel_capabilities") or {}
    modes: list[str] = []
    if caps.get("walk", True):
        modes.append("walk")
    if db_path and os.path.isfile(db_path):
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT item_subtype FROM items
                WHERE owner_type = 'player_character' AND item_subtype IN ('mount', 'vehicle', 'ship')
                  AND speed_kmh IS NOT NULL AND quantity > 0
                """
            ).fetchall()
            for r in rows:
                modes.append(str(r["item_subtype"]))
        finally:
            conn.close()
    else:
        # No DB handle (e.g. pure-JSON unit tests) — fall back to the legacy
        # name-substring heuristic so callers without SQL access still work.
        shared = (state.get("inventory") or {}).get("shared") or []
        has_transport = any(
            isinstance(it, dict) and "cart" in str(it.get("name", "")).lower()
            for it in shared
        )
        if caps.get("ride") or has_transport:
            modes.append("vehicle")
    if caps.get("teleport"):
        modes.append("teleport")
    if caps.get("fly"):
        modes.append("fly")
    return modes or ["walk"]


def list_transport_options(db_path: str | None) -> list[dict[str, Any]]:
    """ADR §J5c Map-screen picker: walk + every owned mount/vehicle/ship.

    Each entry carries the `item_id`/`speed_kmh` the Map UI needs to render
    the icon/speed/ETA and to call `set_active_transport`.
    """
    options: list[dict[str, Any]] = [
        {"mode": "walk", "item_id": None, "label": "On foot", "speed_kmh": WALK_SPEED_KMH}
    ]
    if not db_path or not os.path.isfile(db_path):
        return options
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, name, item_subtype, speed_kmh FROM items
            WHERE owner_type = 'player_character' AND item_subtype IN ('mount', 'vehicle', 'ship')
              AND speed_kmh IS NOT NULL AND quantity > 0
            ORDER BY item_subtype, name
            """
        ).fetchall()
        for r in rows:
            options.append(
                {
                    "mode": str(r["item_subtype"]),
                    "item_id": int(r["id"]),
                    "label": str(r["name"]),
                    "speed_kmh": float(r["speed_kmh"]),
                }
            )
    finally:
        conn.close()
    return options


def get_party_state_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.execute("INSERT OR IGNORE INTO party_state (id, active_transport_mode) VALUES (1, 'walk')")
    row = conn.execute("SELECT * FROM party_state WHERE id = 1").fetchone()
    return dict(row) if row else {"active_transport_item_id": None, "active_transport_mode": "walk"}


def get_party_state(db_path: str | None) -> dict[str, Any]:
    if not db_path or not os.path.isfile(db_path):
        return {"active_transport_item_id": None, "active_transport_mode": "walk"}
    conn = _connect(db_path)
    try:
        state = get_party_state_conn(conn)
        conn.commit()
        return state
    finally:
        conn.close()


def set_active_transport(db_path: str | None, *, item_id: int | None, mode: str) -> dict[str, Any]:
    """ADR §J5c Map-screen selection — persists which mount/vehicle/ship is
    in use so `travel_to`/`move_cardinal` price the next moves correctly.

    `teleport`/`paid_carriage` are intentionally rejected here: teleport is
    engine-only (portal/spell/quest, never player-picked — ADR "#1"), and
    paid_carriage is a one-off hired route priced at booking time via
    `travel_via_paid_transport`, not a durable owned transport.
    """
    mode = str(mode or "walk").strip().lower()
    if mode not in _SELECTABLE_TRANSPORT_MODES:
        return {"success": False, "reason": "invalid_mode"}
    if not db_path or not os.path.isfile(db_path):
        return {"success": False, "reason": "no_db"}
    conn = _connect(db_path)
    try:
        if mode == "walk":
            item_id = None
        else:
            if not item_id:
                return {"success": False, "reason": "item_required"}
            row = conn.execute(
                "SELECT id FROM items WHERE id = ? AND owner_type = 'player_character' AND item_subtype = ? AND quantity > 0",
                (item_id, mode),
            ).fetchone()
            if not row:
                return {"success": False, "reason": "item_mismatch"}
        conn.execute("INSERT OR IGNORE INTO party_state (id, active_transport_mode) VALUES (1, 'walk')")
        conn.execute(
            "UPDATE party_state SET active_transport_item_id = ?, active_transport_mode = ? WHERE id = 1",
            (item_id, mode),
        )
        conn.commit()
        return {"success": True, "mode": mode, "item_id": item_id}
    finally:
        conn.close()


def current_transport_conn(conn: sqlite3.Connection) -> dict[str, Any]:
    """Speed + mode for the travel resolver — falls back to walk if the
    selected transport item vanished (sold, lost, consumed) since picked.
    """
    ps = get_party_state_conn(conn)
    mode = str(ps.get("active_transport_mode") or "walk")
    item_id = ps.get("active_transport_item_id")
    speed = WALK_SPEED_KMH
    if mode != "walk" and item_id:
        row = conn.execute("SELECT speed_kmh FROM items WHERE id = ? AND quantity > 0", (item_id,)).fetchone()
        if row and row["speed_kmh"]:
            speed = float(row["speed_kmh"])
        else:
            mode, item_id = "walk", None
    return {"mode": mode, "item_id": item_id, "speed_kmh": speed}


def current_transport(db_path: str | None) -> dict[str, Any]:
    if not db_path or not os.path.isfile(db_path):
        return {"mode": "walk", "item_id": None, "speed_kmh": WALK_SPEED_KMH}
    conn = _connect(db_path)
    try:
        return current_transport_conn(conn)
    finally:
        conn.close()


def travel_time_minutes(distance_km: float, speed_kmh: float) -> int:
    if speed_kmh <= 0:
        speed_kmh = WALK_SPEED_KMH
    return max(1, round(distance_km / speed_kmh * 60))


def travel_cost(db_path: str | None, origin: tuple[int, int], dest: tuple[int, int]) -> dict[str, Any]:
    """ADR §J5c `time_delta = f(km, speed_kmh)` — prices a move/travel using
    the party's currently active transport (`party_state`), not a flat rate.
    """
    distance_km = max(abs(dest[0] - origin[0]), abs(dest[1] - origin[1])) * KM_PER_CELL
    transport = current_transport(db_path)
    minutes = travel_time_minutes(distance_km, transport["speed_kmh"])
    return {
        "distance_km": distance_km,
        "mode": transport["mode"],
        "item_id": transport["item_id"],
        "speed_kmh": transport["speed_kmh"],
        "time_delta_minutes": minutes,
    }


def _spend_gold(state: dict[str, Any], amount: int, *, reason: str = "travel") -> int:
    """Deduct up to `amount` of the high campaign currency tier. Returns amount spent."""
    from titan.fugassa import currency_engine

    return currency_engine.spend_currency(state, amount, reason=reason)


def travel_via_paid_transport(
    db_path: str | None,
    state: dict[str, Any],
    x: int,
    y: int,
    z: int,
    *,
    speed_kmh: float,
    gold_per_km: float = 0.0,
    label: str = "paid carriage",
) -> dict[str, Any]:
    """ADR §J5c `paid_carriage` / port-payment `ship` — a one-off hired route
    whose price+speed is set by the NPC/line at booking time, rather than a
    durable `active_transport_item_id`. Reuses `travel_to`'s adjacency/intel/
    z rules (mode="walk" internally — hiring transport doesn't grant
    knowledge of the destination) but reports the real mode/cost.
    """
    player = state.get("player") or {}
    ox, oy = int(player.get("x", 0)), int(player.get("y", 0))
    msg = travel_to(state, x, y, z, "walk", db_path=db_path)
    if not msg.startswith("Traveled"):
        return {"success": False, "summary": msg}
    distance_km = max(abs(x - ox), abs(y - oy)) * KM_PER_CELL
    minutes = travel_time_minutes(distance_km, speed_kmh)
    gold_cost = round(distance_km * gold_per_km)
    spent = _spend_gold(state, gold_cost)
    return {
        "success": True,
        "summary": f"You travel by {label} to ({x}, {y}, {z}).",
        "distance_km": distance_km,
        "time_delta_minutes": minutes,
        "gold_spent": spent,
        "mode": "paid_carriage",
    }


def move_player_to(
    state: dict[str, Any],
    x: int,
    y: int,
    z: int,
    *,
    map_code: str | None = None,
    increment_turn: bool = True,
) -> None:
    """Mutate the player's grid position.

    `map_code` is only ever passed by the portal engine below — ordinary
    `travel_to`/`move_cardinal` callers never change maps, so their calls
    implicitly keep the player's current map.
    """
    player = dict(state.get("player") or {})
    cur_map = str(player.get("map_code") or DEFAULT_MAP_CODE)
    new_map = str(map_code) if map_code else cur_map
    old_key = coord_key(int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0)), cur_map)
    loc = state.get("location_state") or {}
    cache = dict(state.get("cell_location_cache") or {})
    if loc:
        prev_cached = cache.get(old_key) if isinstance(cache.get(old_key), dict) else {}
        cache[old_key] = {
            "name": loc.get("name", ""),
            "description": loc.get("description", ""),
            "npcs": list(loc.get("npcs") or []),
            "hidden_npcs": list(loc.get("hidden_npcs") or []),
            "population_done": bool(prev_cached.get("population_done")),
        }

    player["x"] = x
    player["y"] = y
    player["z"] = z
    player["map_code"] = new_map
    state["player"] = player
    if increment_turn:
        state["turn"] = int(state.get("turn") or 0) + 1
        state["can_undo"] = True
    new_key = coord_key(x, y, z, new_map)
    mark_discovered(state, new_key)

    if new_key not in cache:
        biome = biome_label(x, y)
        new_loc = {
            "name": biome.capitalize(),
            "description": f"A {biome} area.",
            "npcs": [],
            "hidden_npcs": [],
            "enemies": [],
            "loot": [],
            "sublocations": [],
        }
        cache[new_key] = {"name": new_loc["name"], "description": new_loc["description"], "npcs": [], "hidden_npcs": []}
        state["location_state"] = new_loc
    else:
        cached = cache[new_key] if isinstance(cache[new_key], dict) else {}
        state["location_state"] = {
            "name": cached.get("name", ""),
            "description": cached.get("description", ""),
            "npcs": list(cached.get("npcs") or []),
            "hidden_npcs": list(cached.get("hidden_npcs") or []),
            "enemies": [],
            "loot": [],
            "sublocations": [],
            "population_done": bool(cached.get("population_done")),
        }
    state["cell_location_cache"] = cache


def travel_to(
    state: dict[str, Any],
    x: int,
    y: int,
    z: int,
    travel_mode: str,
    *,
    db_path: str | None = None,
    increment_turn: bool = True,
) -> str:
    mode = str(travel_mode or "walk").strip().lower()
    allowed_modes = available_travel_modes(state, db_path)
    if mode not in allowed_modes:
        return f"Travel mode '{mode}' is unavailable."

    player = state.get("player") or {}
    map_code = str(player.get("map_code") or DEFAULT_MAP_CODE)
    px, py, pz = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))

    # ADR §A: "na gridu nelze změnit mapu/patro jen pohybem" — z only ever
    # changes via an explicit portal (`use_portal`), never plain travel.
    if z != pz:
        return "You cannot change floors or maps by walking or fast travel — find a portal (entrance, exit, or stairs) first."

    dst_key = coord_key(x, y, z, map_code)
    is_adjacent = (
        abs(x - px) <= 1
        and abs(y - py) <= 1
        and not (x == px and y == py)
    )
    known = is_discovered(state, dst_key)
    intel = state.get("intel_targets") or {}
    has_intel = bool(intel.get(dst_key))

    if not known and not is_adjacent and not has_intel and mode == "walk":
        return "You need intel or an adjacent path to walk there."
    if not known and not is_adjacent and not has_intel and mode != "walk":
        return "Destination requires intel before fast travel."

    move_player_to(state, x, y, z, increment_turn=increment_turn)
    return f"Traveled ({mode}) to ({x}, {y}, {z}) on {map_code}."


def move_cardinal(
    state: dict[str, Any],
    dx: int,
    dy: int,
    dz: int = 0,
    *,
    db_path: str | None = None,
    increment_turn: bool = True,
) -> str:
    if dz != 0:
        return "You cannot change floors or maps by walking — stand on a portal cell (stairs/entrance/exit) and use it."
    player = state.get("player") or {}
    x = int(player.get("x", 0)) + dx
    y = int(player.get("y", 0)) + dy
    z = int(player.get("z", 0))
    # ADR §J5c: "move (1 buňka): aktivní transport z mapy" — cardinal step
    # uses whatever the party currently has selected, not a hardcoded walk.
    mode = current_transport(db_path)["mode"] if db_path else "walk"
    return travel_to(state, x, y, z, mode, db_path=db_path, increment_turn=increment_turn)


# --- Portals & multi-map transitions (ADR §A) --------------------------------


def ensure_grid_map(db_path: str, code: str, *, map_type: str = "overworld", name: str | None = None, parent_location_id: int | None = None) -> None:
    if not db_path or not os.path.isfile(db_path):
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO grid_maps (code, map_type, name, parent_location_id) VALUES (?, ?, ?, ?)",
            (code, map_type, name or code, parent_location_id),
        )
        conn.commit()
    finally:
        conn.close()


def create_portal(
    db_path: str,
    *,
    from_map_code: str,
    from_x: int,
    from_y: int,
    from_z: int = 0,
    portal_type: str,
    target_map_code: str | None = None,
    target_x: int | None = None,
    target_y: int | None = None,
    target_z: int = 0,
    target_location_id: int | None = None,
    is_locked: bool = False,
    lock_reason: str | None = None,
    label: str | None = None,
) -> int:
    """Seed-time only (wizard/generator/quest) — never invented by the GM mid-chat."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO grid_cell_portals (
                from_map_code, from_x, from_y, from_z, portal_type,
                target_map_code, target_x, target_y, target_z, target_location_id,
                is_locked, lock_reason, label, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_map_code, from_x, from_y, from_z) DO UPDATE SET
                portal_type = excluded.portal_type,
                target_map_code = excluded.target_map_code,
                target_x = excluded.target_x,
                target_y = excluded.target_y,
                target_z = excluded.target_z,
                target_location_id = excluded.target_location_id,
                is_locked = excluded.is_locked,
                lock_reason = excluded.lock_reason,
                label = excluded.label
            """,
            (
                from_map_code, from_x, from_y, from_z, portal_type,
                target_map_code, target_x, target_y, target_z, target_location_id,
                1 if is_locked else 0, lock_reason, label, _utc_now(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM grid_cell_portals WHERE from_map_code=? AND from_x=? AND from_y=? AND from_z=?",
            (from_map_code, from_x, from_y, from_z),
        ).fetchone()
        return int(row["id"]) if row else int(cur.lastrowid)
    finally:
        conn.close()


def get_portal_at(db_path: str, map_code: str, x: int, y: int, z: int) -> dict[str, Any] | None:
    """A cell holds at most one portal (schema UNIQUE(from_map_code, from_x, from_y, from_z))."""
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM grid_cell_portals WHERE from_map_code=? AND from_x=? AND from_y=? AND from_z=?",
            (map_code, x, y, z),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def use_portal(db_path: str | None, state: dict[str, Any], *, portal_type_hint: str | None = None) -> dict[str, Any]:
    """Explicit Enter/Exit/Stairs action — the only path that may change `map_code`/`z`.

    `portal_type_hint` is advisory only (surfaced in a mismatch note, never a
    hard block) since `grid_cell_portals` allows at most one portal per cell
    (schema `UNIQUE(from_map_code, from_x, from_y, from_z)`) — there is never
    an actual choice to disambiguate.

    Always produces an explicit `from`/`to` map+coords payload (ADR §A
    "Výstup → turn_resolution.travel s explicitním from/to map+coords").
    """
    player = state.get("player") or {}
    map_code = str(player.get("map_code") or DEFAULT_MAP_CODE)
    x, y, z = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    if not db_path or not os.path.isfile(db_path):
        return {"success": False, "reason": "no_db"}

    row = get_portal_at(db_path, map_code, x, y, z)
    if not row:
        return {
            "success": False,
            "reason": "no_portal_here",
            "from": {"map_code": map_code, "x": x, "y": y, "z": z},
            "summary": "There is no entrance, exit, or stairway at this exact spot.",
        }
    if row["is_locked"]:
        return {
            "success": False,
            "reason": "locked",
            "lock_reason": row["lock_reason"],
            "label": row["label"],
            "summary": f"The {row['portal_type'].replace('_', ' ')} is locked" + (f" ({row['lock_reason']})" if row["lock_reason"] else "") + ".",
        }

    from_summary = {"map_code": map_code, "x": x, "y": y, "z": z}

    if row["target_location_id"] and row["target_map_code"] is None and row["target_x"] is None:
        # Grid -> sublocation graph entry (off-grid interior); grid position
        # itself does not move, only the SQL location-of-record does — the
        # anchor cell is remembered so exiting returns the player here.
        player_out = dict(player)
        player_out["sublocation_id"] = int(row["target_location_id"])
        player_out["sublocation_anchor"] = {"map_code": map_code, "x": x, "y": y, "z": z}
        state["player"] = player_out
        state["turn"] = int(state.get("turn") or 0) + 1
        state["can_undo"] = True
        conn = _connect(db_path)
        try:
            loc_row = conn.execute(
                "SELECT name, description_short, description_long FROM locations WHERE id = ?",
                (int(row["target_location_id"]),),
            ).fetchone()
        finally:
            conn.close()
        state["location_state"] = {
            "name": loc_row["name"] if loc_row else (row["label"] or "Interior"),
            "description": (loc_row["description_long"] or loc_row["description_short"] or "") if loc_row else "",
            "npcs": [],
            "enemies": [],
            "loot": [],
            "sublocations": [],
        }
        to_summary = {"location_id": int(row["target_location_id"])}
        return {
            "success": True,
            "portal_type": row["portal_type"],
            "label": row["label"],
            "from": from_summary,
            "to": to_summary,
            "target_location_id": int(row["target_location_id"]),
            "summary": f"{row['portal_type'].replace('_', ' ').title()} — you step off the grid into {row['label'] or 'the interior'}.",
        }

    target_map = row["target_map_code"] or map_code
    tx = row["target_x"] if row["target_x"] is not None else x
    ty = row["target_y"] if row["target_y"] is not None else y
    tz = row["target_z"] if row["target_z"] is not None else 0
    ensure_grid_map(db_path, target_map)
    move_player_to(state, tx, ty, tz, map_code=target_map, increment_turn=True)
    to_summary = {"map_code": target_map, "x": tx, "y": ty, "z": tz}
    return {
        "success": True,
        "portal_type": row["portal_type"],
        "label": row["label"],
        "from": from_summary,
        "to": to_summary,
        "target_location_id": row["target_location_id"],
        "summary": (
            f"{row['portal_type'].replace('_', ' ').title()} — "
            f"{from_summary['map_code']}({x},{y},{z}) -> {to_summary['map_code']}({tx},{ty},{tz})"
        ),
    }


def leave_sublocation(db_path: str | None, state: dict[str, Any]) -> dict[str, Any]:
    """Exit the off-grid sublocation graph back to the grid anchor cell it was entered from."""
    player = state.get("player") or {}
    anchor = player.get("sublocation_anchor")
    if not player.get("sublocation_id") or not isinstance(anchor, dict):
        return {"success": False, "reason": "not_in_sublocation"}
    player_out = dict(player)
    player_out.pop("sublocation_id", None)
    player_out.pop("sublocation_anchor", None)
    state["player"] = player_out
    state["turn"] = int(state.get("turn") or 0) + 1
    state["can_undo"] = True
    anchor_key = coord_key(int(anchor.get("x", 0)), int(anchor.get("y", 0)), int(anchor.get("z", 0)), str(anchor.get("map_code") or DEFAULT_MAP_CODE))
    cache = state.get("cell_location_cache") or {}
    cached = cache.get(anchor_key) if isinstance(cache.get(anchor_key), dict) else {}
    state["location_state"] = {
        "name": cached.get("name", ""),
        "description": cached.get("description", ""),
        "npcs": [],
        "enemies": [],
        "loot": [],
        "sublocations": [],
    }
    return {
        "success": True,
        "summary": f"You step back out onto the grid at {anchor.get('map_code')}({anchor.get('x')},{anchor.get('y')},{anchor.get('z')}).",
        "to": anchor,
    }


def move_sublocation(db_path: str | None, state: dict[str, Any], label_hint: str) -> dict[str, Any]:
    """Traverse `location_connections` (LEADS_TO) from the current sublocation — tavern -> cellar, etc."""
    player = state.get("player") or {}
    current_id = player.get("sublocation_id")
    if not current_id or not db_path or not os.path.isfile(db_path):
        return {"success": False, "reason": "not_in_sublocation"}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT lc.to_location_id, lc.label, lc.is_locked, lc.lock_reason, l.name
            FROM location_connections lc JOIN locations l ON l.id = lc.to_location_id
            WHERE lc.from_location_id = ?
            """,
            (current_id,),
        ).fetchall()
        hint = (label_hint or "").lower()
        best = None
        for r in rows:
            name = (r["name"] or "").lower()
            label = (r["label"] or "").lower()
            if (name and name in hint) or (label and label in hint):
                best = r
                break
        if not best and rows:
            best = rows[0]
        if not best:
            return {"success": False, "reason": "no_connection", "summary": "There is nowhere to go from here."}
        if best["is_locked"]:
            return {"success": False, "reason": "locked", "lock_reason": best["lock_reason"]}
        loc_row = conn.execute(
            "SELECT description_short, description_long FROM locations WHERE id = ?", (int(best["to_location_id"]),)
        ).fetchone()
        player_out = dict(player)
        player_out["sublocation_id"] = int(best["to_location_id"])
        state["player"] = player_out
        state["turn"] = int(state.get("turn") or 0) + 1
        state["can_undo"] = True
        state["location_state"] = {
            "name": best["name"],
            "description": (loc_row["description_long"] or loc_row["description_short"] or "") if loc_row else "",
            "npcs": [],
            "enemies": [],
            "loot": [],
            "sublocations": [],
        }
        return {
            "success": True,
            "to_location_id": int(best["to_location_id"]),
            "summary": f"You make your way to {best['name']}.",
        }
    finally:
        conn.close()

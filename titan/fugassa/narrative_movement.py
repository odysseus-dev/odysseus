"""Narrative travel — map player intent + GM location hints to grid/sublocation moves."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

LOG = logging.getLogger("titan.fugassa.narrative_movement")

_GO_HOME_RE = re.compile(
    r"\b(?:go\s+home|return\s+home|head\s+home|back\s+home|"
    r"return\s+to\s+(?:my\s+)?(?:home|residence|house|townhouse)|"
    r"go\s+to\s+(?:my\s+)?(?:home|residence|house|townhouse)|"
    r"my\s+residence|my\s+townhouse)\b",
    re.I,
)


def is_go_home_intent(player_text: str) -> bool:
    return bool(_GO_HOME_RE.search(str(player_text or "")))


def resolve_go_home(db_path: str | None, state: dict[str, Any], player_text: str) -> dict[str, Any] | None:
    """Travel to active_residence_code when player says go home / return home."""
    if not is_go_home_intent(player_text):
        return None
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    code = str(portfolio.get("active_residence_code") or "").strip()
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    holding = next((h for h in holdings if isinstance(h, dict) and h.get("code") == code), None)
    if not holding and holdings:
        holding = holdings[0] if isinstance(holdings[0], dict) else None
    if not holding:
        if db_path and os.path.isfile(db_path):
            conn = _connect(db_path)
            try:
                from titan.fugassa.property_repository import list_holdings_conn, sync_property_portfolio

                sync_property_portfolio(conn, state)
                portfolio = state.get("property_portfolio") or {}
                code = str(portfolio.get("active_residence_code") or "").strip()
                holdings = portfolio.get("holdings") or []
                holding = next((h for h in holdings if isinstance(h, dict) and h.get("code") == code), None)
                if not holding and holdings:
                    holding = holdings[0]
            finally:
                conn.close()
    if not holding:
        return None
    root_id = int(holding.get("root_location_id") or 0)
    if not root_id:
        return None
    result = enter_sublocation(
        db_path,
        state,
        root_id,
        label=str(holding.get("name") or "your residence"),
    )
    if result.get("success"):
        result["time_delta_minutes"] = 10
        result["intent"] = "go_home"
        result["property_code"] = holding.get("code")
    return result


_DESTINATION_RE = re.compile(
    r"\b(?:visit|go\s+to|head\s+to|make\s+(?:my|your)\s+way\s+(?:to|toward|towards)|"
    r"walk\s+to|travel\s+to|approach|enter|step\s+into|go\s+inside|go\s+into|"
    r"proceed\s+to|move\s+to|make\s+for|rush\s+to|run\s+to)\s+(?:the\s+)?(.{3,80}?)(?:\.|,|$|\band\b|\bto\b)",
    re.I,
)
_TOWARD_RE = re.compile(
    r"\b(?:make\s+(?:my|your)\s+way\s+toward(?:s)?|head\s+toward(?:s)?|walk\s+toward(?:s)?|go\s+toward(?:s)?)\s+(?:the\s+)?(.{3,80}?)(?:\.|,|$|\band\b)",
    re.I,
)
_EMBEDDED_PLACE_RE = re.compile(
    r"(?:contains|houses|includes|features)\s+(?:the\s+)?([A-Za-z][\w\s'\-—–]{4,60}?)(?:\s*[—–-]\s*|\s*[,;.]|\s+a\s+|\s+with\s+)",
    re.I,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:48] or "place")


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9']+", str(text or "").lower()) if len(t) > 2}


def _score_match(hint: str, candidate: str) -> float:
    h = str(hint or "").strip().lower()
    c = str(candidate or "").strip().lower()
    if not h or not c:
        return 0.0
    if h in c or c in h:
        return 0.95
    ht, ct = _tokenize(h), _tokenize(c)
    if not ht or not ct:
        return SequenceMatcher(None, h, c).ratio()
    overlap = len(ht & ct) / max(len(ht), 1)
    ratio = SequenceMatcher(None, h, c).ratio()
    return max(overlap * 0.85 + ratio * 0.15, ratio)


def extract_destination_hint(player_text: str) -> str | None:
    text = str(player_text or "").strip()
    if not text:
        return None
    for pattern in (_DESTINATION_RE, _TOWARD_RE):
        m = pattern.search(text)
        if m:
            hint = m.group(1).strip(" .,\"'")
            if len(hint) >= 3:
                return hint
    return None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _current_location_id(db_path: str, state: dict[str, Any]) -> int | None:
    player = state.get("player") or {}
    if player.get("sublocation_id"):
        return int(player["sublocation_id"])
    loc = state.get("location_state") or {}
    if loc.get("location_id"):
        return int(loc["location_id"])
    anchor = state.get("_current_location_id")
    if anchor:
        return int(anchor)
    if not db_path or not os.path.isfile(db_path):
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        return int(row["current_location_id"]) if row and row["current_location_id"] else None
    finally:
        conn.close()


def _grid_location_id(db_path: str, state: dict[str, Any]) -> int | None:
    """Outdoor/grid location-of-record (parent), even when inside a sublocation."""
    player = state.get("player") or {}
    if player.get("sublocation_id") and player.get("sublocation_anchor"):
        anchor = player["sublocation_anchor"]
        if not db_path:
            return None
        conn = _connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT location_id FROM grid_cells
                WHERE map_code = ? AND x = ? AND y = ? AND z = ?
                """,
                (
                    str(anchor.get("map_code") or "overworld"),
                    int(anchor.get("x", 0)),
                    int(anchor.get("y", 0)),
                    int(anchor.get("z", 0)),
                ),
            ).fetchone()
            if row and row["location_id"]:
                return int(row["location_id"])
        finally:
            conn.close()
    return _current_location_id(db_path, state)


def discover_embedded_places(description: str) -> list[str]:
    places: list[str] = []
    for m in _EMBEDDED_PLACE_RE.finditer(str(description or "")):
        name = m.group(1).strip(" .,\"'—–-")
        if len(name) >= 4 and name not in places:
            places.append(name)
    return places


def list_travel_candidates(db_path: str | None, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Known destinations from SQL graph + location description + runtime sublocations."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    parent_id = _grid_location_id(db_path, state)
    loc = state.get("location_state") or {}

    for sub in loc.get("sublocations") or []:
        if isinstance(sub, dict):
            name = str(sub.get("name") or "").strip()
            if name:
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append({"name": name, "location_id": sub.get("location_id"), "source": "runtime"})

    for name in discover_embedded_places(str(loc.get("description") or "")):
        key = name.lower()
        if key not in seen:
            seen.add(key)
            candidates.append({"name": name, "location_id": None, "source": "description"})

    if db_path and os.path.isfile(db_path) and parent_id:
        conn = _connect(db_path)
        try:
            player = state.get("player") or {}
            if player.get("sublocation_id"):
                parent_row = conn.execute(
                    "SELECT id, name FROM locations WHERE id = ?",
                    (int(parent_id),),
                ).fetchone()
                if parent_row:
                    key = str(parent_row["name"]).lower()
                    if key not in seen:
                        seen.add(key)
                        candidates.append(
                            {
                                "name": parent_row["name"],
                                "location_id": int(parent_row["id"]),
                                "source": "parent_grid",
                            }
                        )
            for row in conn.execute(
                "SELECT id, name FROM locations WHERE parent_location_id = ?",
                (int(parent_id),),
            ):
                key = str(row["name"]).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append({"name": row["name"], "location_id": int(row["id"]), "source": "sql_child"})
            for row in conn.execute(
                """
                SELECT l2.id, l2.name
                FROM location_connections lc
                JOIN locations l2 ON l2.id = lc.to_location_id
                WHERE lc.from_location_id = ?
                """,
                (int(parent_id),),
            ):
                key = str(row["name"]).lower()
                if key not in seen:
                    seen.add(key)
                    candidates.append({"name": row["name"], "location_id": int(row["id"]), "source": "sql_connection"})
        finally:
            conn.close()
    return candidates


def _description_for_sublocation(parent_desc: str, place_name: str) -> str:
    """Pull the clause about *place_name* from the parent area description, or a short fallback."""
    text = str(parent_desc or "").strip()
    name = str(place_name or "").strip()
    if not name:
        return "An interior space."
    tokens = [t for t in _tokenize(name) if len(t) > 3]
    if text and tokens:
        for token in tokens:
            m = re.search(
                rf"([^.!?]*\b{re.escape(token)}\b[^.!?]*(?:[—–-][^.!?]+)?)",
                text,
                re.I,
            )
            if m:
                snippet = m.group(1).strip(" ,;")
                if len(snippet) >= 20:
                    return snippet
        for sent in re.split(r"(?<=[.!?])\s+", text):
            sl = sent.lower()
            if any(t in sl for t in tokens):
                return sent.strip()
    return f"You are inside {name}."


def _is_stale_parent_description(desc: str, place_name: str) -> bool:
    d = str(desc or "").lower()
    name_l = str(place_name or "").lower()
    if not d:
        return True
    if d.startswith("you are inside") and len(d) < len(name_l) + 40:
        return True
    if "town square" in d and "square" not in name_l:
        return True
    tokens = [t for t in _tokenize(name_l) if len(t) > 3]
    return bool(tokens) and not any(t in d for t in tokens)


def _parent_area_names(db_path: str | None, state: dict[str, Any]) -> list[str]:
    """Outdoor/grid labels for the parent when standing inside a sublocation."""
    loc = state.get("location_state") or {}
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        label = str(name or "").strip()
        if not label:
            return
        key = label.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(label)
        tail = format_parent_area(label)
        if tail and tail.lower() not in seen:
            seen.add(tail.lower())
            names.append(tail)

    for key in ("parent_name", "parent_area"):
        _add(str(loc.get(key) or ""))
    parent_id = loc.get("parent_location_id") or loc.get("grid_location_id")
    if db_path and parent_id and os.path.isfile(db_path):
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT name FROM locations WHERE id = ?", (int(parent_id),)).fetchone()
            if row:
                _add(str(row["name"] or ""))
        finally:
            conn.close()
    return names


def _hint_matches_parent(hint: str, parent_names: list[str]) -> bool:
    for name in parent_names:
        if _score_match(hint, name) >= 0.55:
            return True
    return False


def _refresh_grid_location_after_leave(
    db_path: str | None,
    state: dict[str, Any],
    *,
    grid_location_id: int | None = None,
) -> None:
    if not db_path or not os.path.isfile(db_path):
        return
    from titan.fugassa.db import state_repository
    from titan.fugassa.location_population_engine import refresh_location_npcs_from_sql

    preserved_name = str((state.get("location_state") or {}).get("name") or "").strip()
    preserved_desc = str((state.get("location_state") or {}).get("description") or "").strip()
    loc_id = int(grid_location_id or 0) or state_repository.sync_location_only(db_path, state)
    if not loc_id:
        return
    state["_current_location_id"] = int(loc_id)
    loc = dict(state.get("location_state") or {})
    loc["location_id"] = int(loc_id)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, name, description_short, description_long, parent_location_id
            FROM locations WHERE id = ?
            """,
            (int(loc_id),),
        ).fetchone()
        if row:
            loc["name"] = str(row["name"] or preserved_name or loc.get("name") or "")
            raw_desc = (row["description_long"] or row["description_short"] or "").strip()
            loc["description"] = raw_desc or preserved_desc or loc.get("description") or ""
        conn.execute(
            "UPDATE player_characters SET current_location_id = ?, updated_at = ? WHERE code = 'pc_hero'",
            (int(loc_id), _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()
    if preserved_name and str(loc.get("name") or "").lower().startswith("cell "):
        loc["name"] = preserved_name
    if preserved_desc and not str(loc.get("description") or "").strip():
        loc["description"] = preserved_desc
    state["location_state"] = enrich_location_context(db_path, loc, location_id=int(loc_id))
    refresh_location_npcs_from_sql(db_path, state)


def _try_leave_sublocation_for_parent(
    db_path: str | None,
    state: dict[str, Any],
    hint: str,
) -> dict[str, Any] | None:
    player = state.get("player") or {}
    if not player.get("sublocation_id"):
        return None
    loc = enrich_location_context(
        db_path,
        dict(state.get("location_state") or {}),
        location_id=int(player["sublocation_id"]),
    )
    state["location_state"] = loc
    parent_names = _parent_area_names(db_path, state)
    if not _hint_matches_parent(hint, parent_names):
        return None
    from titan.fugassa import grid_engine

    result = grid_engine.leave_sublocation(db_path, state)
    if not result.get("success"):
        return None
    grid_id = loc.get("grid_location_id") or loc.get("parent_location_id")
    _refresh_grid_location_after_leave(
        db_path,
        state,
        grid_location_id=int(grid_id) if grid_id else None,
    )
    result["time_delta_minutes"] = 5
    result["intent"] = "narrative_travel"
    result["destination_hint"] = hint
    result["matched_name"] = parent_names[0] if parent_names else hint
    result["mode"] = "parent_exit"
    return result


def format_parent_area(parent_name: str) -> str:
    """Short outdoor label for HUD (e.g. 'City Town Square — Market District' → 'Market District')."""
    name = str(parent_name or "").strip()
    for sep in (" — ", " – ", " - "):
        if sep in name:
            tail = name.split(sep, 1)[1].strip()
            if tail:
                return tail
    return name


def enrich_location_context(
    db_path: str | None,
    location_state: dict[str, Any],
    *,
    location_id: int | None = None,
) -> dict[str, Any]:
    """Attach parent/grid metadata when `location_id` is an interior sublocation."""
    loc = dict(location_state or {})
    loc_id = int(location_id or loc.get("location_id") or 0)
    if not loc_id or not db_path or not os.path.isfile(db_path):
        loc["is_sublocation"] = False
        return loc
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, parent_location_id, region_name FROM locations WHERE id = ?",
            (loc_id,),
        ).fetchone()
        if not row:
            return loc
        parent_id = row["parent_location_id"]
        parent_region = None
        parent_name = None
        if parent_id:
            loc["is_sublocation"] = True
            loc["location_id"] = loc_id
            loc["parent_location_id"] = int(parent_id)
            loc["grid_location_id"] = int(parent_id)
            parent = conn.execute(
                "SELECT id, name, region_name FROM locations WHERE id = ?",
                (int(parent_id),),
            ).fetchone()
            if parent:
                loc["parent_name"] = parent["name"]
                loc["parent_area"] = format_parent_area(parent["name"])
                parent_region = parent["region_name"]
                parent_name = parent["name"]
        else:
            loc["is_sublocation"] = False
            loc["grid_location_id"] = loc_id
            for key in ("parent_location_id", "parent_name", "parent_area"):
                loc.pop(key, None)
        from titan.fugassa.location_name_registry import resolve_settlement_labels

        labels = resolve_settlement_labels(
            name=str(row["name"] or loc.get("name") or ""),
            region_name=row["region_name"],
            parent_location_id=int(parent_id) if parent_id else None,
            parent_region_name=parent_region,
            parent_name=parent_name,
        )
        loc.update(labels)
    finally:
        conn.close()
    return loc


def sync_player_sublocation_anchor(state: dict[str, Any]) -> None:
    """Keep grid anchor fields on `player` when standing inside a sublocation."""
    player = dict(state.get("player") or {})
    anchor = player.get("sublocation_anchor")
    if not player.get("sublocation_id") or not isinstance(anchor, dict):
        return
    if anchor.get("map_code") and not player.get("map_code"):
        player["map_code"] = anchor["map_code"]
    state["player"] = player


def _grid_parent_location_id(conn: sqlite3.Connection, parent_location_id: int) -> int:
    """Flatten nested parents so interiors always hang off the grid-level location."""
    loc_id = int(parent_location_id)
    seen = {loc_id}
    while True:
        row = conn.execute(
            "SELECT parent_location_id FROM locations WHERE id = ?",
            (loc_id,),
        ).fetchone()
        if not row or not row["parent_location_id"]:
            return loc_id
        parent_id = int(row["parent_location_id"])
        if parent_id == loc_id or parent_id in seen:
            return loc_id
        seen.add(parent_id)
        loc_id = parent_id


def ensure_sublocation(
    db_path: str,
    *,
    parent_location_id: int,
    name: str,
    description: str = "",
    parent_description: str = "",
) -> int:
    """Create or fetch a child location row + contains connection from parent."""
    place_desc = str(description or "").strip()
    if not place_desc or _is_stale_parent_description(place_desc, name):
        place_desc = _description_for_sublocation(parent_description, name)
    conn = _connect(db_path)
    now = _utc_now()
    parent_location_id = _grid_parent_location_id(conn, int(parent_location_id))
    code = _slug(f"{parent_location_id}_{name}")
    try:
        row = conn.execute("SELECT id, description_short FROM locations WHERE code = ?", (code,)).fetchone()
        if row:
            loc_id = int(row["id"])
            if _is_stale_parent_description(row["description_short"] or "", name):
                conn.execute(
                    "UPDATE locations SET description_short = ?, description_long = ?, updated_at = ? WHERE id = ?",
                    (place_desc[:500], place_desc[:2000], now, loc_id),
                )
        else:
            conn.execute(
                """
                INSERT INTO locations (
                    code, name, description_short, description_long,
                    parent_location_id, is_discovered, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (code, name, place_desc[:500], place_desc[:2000], parent_location_id, now, now),
            )
            loc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            """
            INSERT OR IGNORE INTO location_connections (from_location_id, to_location_id, connection_type, label, created_at)
            VALUES (?, ?, 'contains', ?, ?)
            """,
            (parent_location_id, loc_id, name, now),
        )
        conn.commit()
        return loc_id
    finally:
        conn.close()


def enter_sublocation(db_path: str | None, state: dict[str, Any], location_id: int, *, label: str = "") -> dict[str, Any]:
    """Move player into an off-grid sublocation while keeping grid anchor."""
    from titan.fugassa import grid_engine

    player = dict(state.get("player") or {})
    map_code = str(player.get("map_code") or grid_engine.DEFAULT_MAP_CODE)
    x, y, z = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    already_there = player.get("sublocation_id") == location_id

    name, desc = label, ""
    parent_desc = str((state.get("location_state") or {}).get("description") or "")
    if db_path and os.path.isfile(db_path):
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT name, description_short, description_long, parent_location_id FROM locations WHERE id = ?",
                (int(location_id),),
            ).fetchone()
            if row:
                name = row["name"] or name
                raw_long = (row["description_long"] or "").strip()
                raw_short = (row["description_short"] or "").strip()
                desc = raw_long if raw_long and not _is_stale_parent_description(raw_long, name or label) else raw_short
                if row["parent_location_id"]:
                    parent_row = conn.execute(
                        "SELECT description_short, description_long FROM locations WHERE id = ?",
                        (int(row["parent_location_id"]),),
                    ).fetchone()
                    if parent_row:
                        pl = (parent_row["description_long"] or "").strip()
                        ps = (parent_row["description_short"] or "").strip()
                        parent_desc = pl if pl and not _is_stale_parent_description(pl, name or label) else ps or parent_desc
        finally:
            conn.close()

    if _is_stale_parent_description(desc, name or label):
        desc = _description_for_sublocation(parent_desc, name or label)
        if db_path and os.path.isfile(db_path):
            conn = _connect(db_path)
            now = _utc_now()
            try:
                conn.execute(
                    "UPDATE locations SET description_short = ?, description_long = ?, updated_at = ? WHERE id = ?",
                    (desc[:500], desc[:2000], now, int(location_id)),
                )
                conn.commit()
            finally:
                conn.close()

    if not player.get("sublocation_anchor") and not already_there:
        player["sublocation_anchor"] = {"map_code": map_code, "x": x, "y": y, "z": z}
    if not already_there:
        player["sublocation_id"] = int(location_id)
    state["player"] = player
    sync_player_sublocation_anchor(state)
    state["can_undo"] = True
    loc_state = {
        "name": name or "Interior",
        "description": desc,
        "location_id": int(location_id),
        "npcs": [],
        "hidden_npcs": [],
        "enemies": [],
        "loot": [],
        "sublocations": list((state.get("location_state") or {}).get("sublocations") or []),
    }
    state["location_state"] = enrich_location_context(db_path, loc_state, location_id=int(location_id))
    if db_path and os.path.isfile(db_path):
        from titan.fugassa.location_population_engine import refresh_location_npcs_from_sql
        from titan.fugassa.property_repository import attach_property_context_to_location

        refresh_location_npcs_from_sql(db_path, state)
        conn = _connect(db_path)
        try:
            attach_property_context_to_location(conn, state, location_id=int(location_id))
        finally:
            conn.close()
    return {
        "success": True,
        "target_location_id": int(location_id),
        "summary": f"You {'are at' if already_there else 'go to'} {name or label or 'the destination'}.",
        "mode": "sublocation",
        "reason": "already_there" if already_there else None,
    }


def resolve_narrative_travel(db_path: str | None, state: dict[str, Any], player_text: str) -> dict[str, Any] | None:
    """Try to mechanically move the player based on natural-language travel intent."""
    home = resolve_go_home(db_path, state, player_text)
    if home and home.get("success") and home.get("reason") != "already_there":
        return home
    if home and home.get("success"):
        return home

    hint = extract_destination_hint(player_text)
    if not hint:
        return None

    player = state.get("player") or {}
    parent_exit: dict[str, Any] | None = None

    if player.get("sublocation_id"):
        from titan.fugassa import grid_engine

        result = grid_engine.move_sublocation(db_path, state, hint)
        if result.get("success"):
            return {**result, "time_delta_minutes": 3}
        if _score_match(hint, "outside") >= 0.8 or re.search(r"\b(exit|leave|back\s+out)\b", player_text, re.I):
            result = grid_engine.leave_sublocation(db_path, state)
            if result.get("success"):
                _refresh_grid_location_after_leave(db_path, state)
                return {**result, "time_delta_minutes": 2}
        parent_exit = _try_leave_sublocation_for_parent(db_path, state, hint)

    candidates = list_travel_candidates(db_path, state)
    if not candidates:
        return parent_exit

    scored = sorted(
        ((c, _score_match(hint, c["name"])) for c in candidates),
        key=lambda item: item[1],
        reverse=True,
    )
    best, score = scored[0]
    if score < 0.45:
        return parent_exit

    loc_id = best.get("location_id")
    parent_id = _grid_location_id(db_path, state)
    if parent_id and loc_id and int(loc_id) == int(parent_id):
        if parent_exit:
            return parent_exit
        exit_result = _try_leave_sublocation_for_parent(db_path, state, hint)
        if exit_result:
            return exit_result

    if not loc_id and db_path and parent_id:
        parent_desc = str((state.get("location_state") or {}).get("description") or "")
        loc_id = ensure_sublocation(
            db_path,
            parent_location_id=int(parent_id),
            name=best["name"],
            parent_description=parent_desc,
        )
        subs = list((state.get("location_state") or {}).get("sublocations") or [])
        subs.append({"name": best["name"], "location_id": loc_id})
        loc = dict(state.get("location_state") or {})
        loc["sublocations"] = subs
        state["location_state"] = loc

    if not loc_id or not db_path:
        return parent_exit

    result = enter_sublocation(db_path, state, int(loc_id), label=best["name"])
    if result.get("success") and result.get("reason") != "already_there":
        result["time_delta_minutes"] = 8
        result["intent"] = "narrative_travel"
        result["destination_hint"] = hint
        result["matched_name"] = best["name"]
        result["match_score"] = round(score, 3)
        return result
    if result.get("success"):
        return result
    return parent_exit


def apply_gm_location_hint(state: dict[str, Any], db_path: str | None, location_label: str | None) -> bool:
    """When GM timestamp names a new place, enter matching sublocation if resolvable."""
    label = str(location_label or "").strip()
    if not label or not db_path:
        return False
    current = (state.get("location_state") or {}).get("name") or ""
    if _score_match(label, current) >= 0.85:
        return False
    hint_result = resolve_narrative_travel(db_path, state, f"go to {label}")
    return bool(hint_result and hint_result.get("success"))


def sync_post_gm_movement(
    db_path: str | None,
    state: dict[str, Any],
    *,
    gm_prose: str,
    gm_location: str | None,
    player_text: str,
) -> dict[str, Any] | None:
    """Second pass after GM — player intent first, then timestamp/prose confirmation."""
    player_hint = extract_destination_hint(player_text)
    current = (state.get("location_state") or {}).get("name") or ""
    gm_stuck_on_current = bool(
        gm_location and _score_match(str(gm_location), current) >= 0.85
    )

    if player_hint:
        result = resolve_narrative_travel(db_path, state, f"go to {player_hint}")
        if result and result.get("success"):
            result["source"] = "player_intent"
            return result

    if gm_location and not (player_hint and gm_stuck_on_current):
        applied = apply_gm_location_hint(state, db_path, gm_location)
        if applied:
            return {"source": "gm_timestamp", "location": gm_location, "success": True}

    if player_hint:
        prose_hint = extract_destination_hint(gm_prose) or gm_location
        if prose_hint and (
            _score_match(prose_hint, player_hint) >= 0.4
            or (gm_stuck_on_current and _score_match(prose_hint, current) < 0.85)
        ):
            result = resolve_narrative_travel(db_path, state, f"visit {prose_hint}")
            if result and result.get("success"):
                result["source"] = "gm_prose"
                return result
    return None

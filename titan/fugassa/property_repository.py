"""Property holdings CRUD + game.json sync."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug_code(name: str, fallback: str = "property") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:48] or fallback)


def _hero_pc_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def create_holding_conn(
    conn: sqlite3.Connection,
    *,
    player_character_id: int,
    proposal: dict[str, Any],
    acquired_at_turn: int = 0,
) -> dict[str, Any] | None:
    """Create root location + property_holdings row from wizard/archivist proposal."""
    if not proposal.get("granted"):
        return None
    code = str(proposal.get("code") or "").strip() or _slug_code(str(proposal.get("name")))
    existing = conn.execute("SELECT id FROM property_holdings WHERE code = ?", (code,)).fetchone()
    if existing:
        row = conn.execute(
            "SELECT * FROM property_holdings WHERE id = ?", (int(existing["id"]),)
        ).fetchone()
        return dict(row) if row else None

    name = str(proposal.get("name") or "Residence").strip()
    family = _family_token(name, code)
    if family:
        canonical = find_canonical_holding_for_family_conn(conn, family=family, pc_id=player_character_id)
        if canonical and _room_count_at_root(conn, int(canonical["root_location_id"])) > 0:
            return canonical

    loc_name = str(proposal.get("root_location_name") or name).strip()
    loc_code = _slug_code(loc_name, code)
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, description_long, is_discovered, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (
            loc_code,
            loc_name,
            str(proposal.get("deed_summary") or "")[:240],
            str(proposal.get("deed_summary") or ""),
            now,
            now,
        ),
    )
    root_location_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    specs = proposal.get("specs") if isinstance(proposal.get("specs"), dict) else {}
    conn.execute(
        """
        INSERT INTO property_holdings (
            code, player_character_id, root_location_id, name, property_kind, title_status,
            acquired_at_turn, acquired_via, deed_summary, specs_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            player_character_id,
            root_location_id,
            name,
            str(proposal.get("property_kind") or "townhouse"),
            str(proposal.get("title_status") or "owned"),
            acquired_at_turn,
            str(proposal.get("acquired_via") or "bootstrap"),
            str(proposal.get("deed_summary") or ""),
            json.dumps(specs),
            now,
            now,
        ),
    )
    holding_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    row = conn.execute("SELECT * FROM property_holdings WHERE id = ?", (holding_id,)).fetchone()
    result = dict(row) if row else None
    if result:
        _after_holding_created(conn, result, acquired_at_turn=acquired_at_turn)
    return result


def _after_holding_created(
    conn: sqlite3.Connection,
    holding: dict[str, Any],
    *,
    acquired_at_turn: int = 0,
) -> None:
    """Pin fact + vec index for new holdings (engine-owned, not LLM-guessed)."""
    from titan.fugassa import campaign_facts

    name = str(holding.get("name") or "Property").strip()
    deed = str(holding.get("deed_summary") or "").strip()
    if deed:
        campaign_facts.pin_fact_conn(
            conn,
            f"Player holds {name}: {deed[:280]}",
            known_by="everyone",
        )


def seed_starting_property(
    db_path: str,
    *,
    proposal: dict[str, Any] | None,
    acquired_at_turn: int = 0,
) -> dict[str, Any] | None:
    if not db_path or not proposal or not proposal.get("granted"):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        pc_id = _hero_pc_id(conn)
        if not pc_id:
            return None
        result = create_holding_conn(
            conn,
            player_character_id=pc_id,
            proposal=proposal,
            acquired_at_turn=acquired_at_turn,
        )
        conn.commit()
        if result:
            from titan.fugassa import campaign_chronicle

            reconn = sqlite3.connect(db_path)
            reconn.row_factory = sqlite3.Row
            try:
                loc_row = reconn.execute(
                    "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
                ).fetchone()
                loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None
                event_id = campaign_chronicle.record_property_acquired_conn(
                    reconn,
                    db_path,
                    result,
                    turn_id=acquired_at_turn or 0,
                    location_id=loc_id,
                    source="bootstrap",
                )
                reconn.commit()
            finally:
                reconn.close()
            if event_id:
                campaign_chronicle.index_event_log_ids(db_path, [event_id])
        return result
    finally:
        conn.close()


def list_holdings_conn(conn: sqlite3.Connection, *, pc_id: int | None = None) -> list[dict[str, Any]]:
    pid = pc_id or _hero_pc_id(conn)
    if not pid:
        return []
    rows = conn.execute(
        """
        SELECT h.*, l.image_path AS thumbnail_asset
        FROM property_holdings h
        LEFT JOIN locations l ON l.id = h.root_location_id
        WHERE h.player_character_id = ?
        ORDER BY h.id
        """,
        (pid,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        specs: dict[str, Any] = {}
        try:
            specs = json.loads(row["specs_json"]) if row["specs_json"] else {}
        except (TypeError, ValueError):
            specs = {}
        out.append(
            {
                "code": row["code"],
                "name": row["name"],
                "property_kind": row["property_kind"],
                "title_status": row["title_status"],
                "root_location_id": row["root_location_id"],
                "acquired_at_turn": row["acquired_at_turn"],
                "deed_summary": row["deed_summary"] or "",
                "specs": specs,
                "thumbnail_asset": row["thumbnail_asset"],
                "room_count": _room_count_for_holding_conn(conn, int(row["root_location_id"])),
                "staff_names": _staff_names_for_holding_conn(conn, int(row["id"])),
            }
        )
    return out


def sync_property_portfolio(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    holdings = list_holdings_conn(conn)
    if not holdings:
        state.pop("property_portfolio", None)
        return
    portfolio = dict(state.get("property_portfolio") or {})
    active = str(portfolio.get("active_residence_code") or "").strip()
    codes = {h["code"] for h in holdings}
    if not active or active not in codes:
        active = holdings[0]["code"]
    state["property_portfolio"] = {
        "holdings": holdings,
        "active_residence_code": active,
    }


def get_holding_by_code_conn(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM property_holdings WHERE code = ?", (str(code).strip(),)).fetchone()
    return dict(row) if row else None


def find_holding_conn(conn: sqlite3.Connection, *, code: str = "", name: str = "") -> dict[str, Any] | None:
    code = str(code or "").strip()
    name = str(name or "").strip()
    if code:
        row = conn.execute("SELECT * FROM property_holdings WHERE code = ?", (code,)).fetchone()
        if row:
            return dict(row)
    if name:
        row = conn.execute(
            "SELECT * FROM property_holdings WHERE name = ? COLLATE NOCASE LIMIT 1", (name,)
        ).fetchone()
        if row:
            return dict(row)
    return None


def list_rooms_for_holding_conn(conn: sqlite3.Connection, root_location_id: int) -> list[dict[str, Any]]:
    """Rooms under a holding — prefers property_rooms rows, falls back to child locations."""
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.code, l.name, l.description_short, l.description_long,
                   pr.room_kind, pr.floor_label, pr.layout_notes
            FROM property_rooms pr
            JOIN locations l ON l.id = pr.location_id
            JOIN property_holdings h ON h.id = pr.property_id
            WHERE h.root_location_id = ?
            ORDER BY pr.id
            """,
            (int(root_location_id),),
        ).fetchall()
        if rows:
            return [
                {
                    "id": int(r["id"]),
                    "code": r["code"],
                    "name": r["name"],
                    "description": (r["layout_notes"] or r["description_short"] or "").strip(),
                    "room_kind": r["room_kind"] or "room",
                    "floor_label": r["floor_label"],
                }
                for r in rows
            ]
    except sqlite3.OperationalError:
        pass
    rows = conn.execute(
        """
        SELECT id, code, name, description_short, description_long
        FROM locations
        WHERE parent_location_id = ?
        ORDER BY id
        """,
        (int(root_location_id),),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "code": r["code"],
            "name": r["name"],
            "description": (r["description_short"] or "").strip(),
            "room_kind": "room",
            "floor_label": None,
        }
        for r in rows
    ]


def _room_count_for_holding_conn(conn: sqlite3.Connection, root_location_id: int) -> int:
    return len(list_rooms_for_holding_conn(conn, int(root_location_id)))


def _staff_names_for_holding_conn(conn: sqlite3.Connection, property_id: int) -> list[str]:
    try:
        rows = conn.execute(
            """
            SELECT name FROM npcs
            WHERE assigned_property_id = ? AND status = 'alive'
            ORDER BY name
            """,
            (int(property_id),),
        ).fetchall()
        return [str(r["name"]) for r in rows if r["name"]]
    except sqlite3.OperationalError:
        return []


def list_fixtures_for_holding_conn(conn: sqlite3.Connection, property_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.name, f.fixture_kind, f.description, f.condition_pct,
                   f.room_location_id, l.name AS room_name
            FROM property_fixtures f
            LEFT JOIN locations l ON l.id = f.room_location_id
            WHERE f.property_id = ?
            ORDER BY f.id
            """,
            (int(property_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "fixture_kind": r["fixture_kind"],
            "description": r["description"] or "",
            "condition_pct": int(r["condition_pct"] or 100),
            "room_location_id": r["room_location_id"],
            "room_name": r["room_name"],
        }
        for r in rows
    ]


def list_staff_for_holding_conn(conn: sqlite3.Connection, property_id: int) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT id, code, name, assigned_role, portrait_path
            FROM npcs
            WHERE assigned_property_id = ? AND status = 'alive'
            ORDER BY name
            """,
            (int(property_id),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "npc_id": int(r["id"]),
            "npc_code": r["code"],
            "name": r["name"],
            "role": r["assigned_role"] or "staff",
            "portrait_path": r["portrait_path"],
        }
        for r in rows
    ]


def holding_for_location_conn(conn: sqlite3.Connection, location_id: int) -> dict[str, Any] | None:
    """Resolve property holding for a location (root or room)."""
    loc_id = int(location_id)
    row = conn.execute(
        "SELECT * FROM property_holdings WHERE root_location_id = ? LIMIT 1", (loc_id,)
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        """
        SELECT h.* FROM property_holdings h
        JOIN locations l ON l.parent_location_id = h.root_location_id
        WHERE l.id = ?
        LIMIT 1
        """,
        (loc_id,),
    ).fetchone()
    return dict(row) if row else None


def attach_property_context_to_location(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    *,
    location_id: int | None = None,
) -> None:
    """Set location_state.property_code when player is at an owned place."""
    loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    loc_id = location_id or loc.get("location_id") or state.get("_current_location_id")
    if not loc_id:
        loc.pop("property_code", None)
        state["location_state"] = loc
        return
    holding = holding_for_location_conn(conn, int(loc_id))
    if holding:
        loc["property_code"] = holding["code"]
        loc["property_name"] = holding["name"]
    else:
        loc.pop("property_code", None)
        loc.pop("property_name", None)
    state["location_state"] = loc


def proposal_from_archivist_op(op: dict[str, Any]) -> dict[str, Any]:
    name = str(op.get("name") or "Residence").strip()
    root_name = str(op.get("root_location_name") or name).strip()
    specs = op.get("specs") if isinstance(op.get("specs"), dict) else {}
    code_hint = str(op.get("code") or "").strip()
    return {
        "granted": True,
        "code": code_hint or _slug_code(name),
        "name": name,
        "root_location_name": root_name,
        "property_kind": str(op.get("property_kind") or "townhouse").strip().lower(),
        "title_status": str(op.get("title_status") or "owned").strip().lower(),
        "acquired_via": str(op.get("acquired_via") or "narrative").strip(),
        "deed_summary": str(op.get("deed_summary") or "").strip(),
        "specs": specs,
    }


def update_holding_from_op_conn(conn: sqlite3.Connection, op: dict[str, Any]) -> bool:
    holding = find_holding_conn(
        conn,
        code=str(op.get("code") or op.get("property_code") or ""),
        name=str(op.get("name") or op.get("property_name") or ""),
    )
    if not holding:
        return False
    now = _utc_now()
    fields: list[str] = []
    values: list[Any] = []
    if op.get("name") or op.get("property_name"):
        fields.append("name = ?")
        values.append(str(op.get("name") or op.get("property_name")).strip())
    if op.get("property_kind"):
        fields.append("property_kind = ?")
        values.append(str(op.get("property_kind")).strip().lower())
    if op.get("title_status"):
        fields.append("title_status = ?")
        values.append(str(op.get("title_status")).strip().lower())
    deed_append = str(op.get("deed_append") or "").strip()
    if deed_append:
        merged = f"{(holding.get('deed_summary') or '').strip()} {deed_append}".strip()
        fields.append("deed_summary = ?")
        values.append(merged[:2000])
    if isinstance(op.get("specs"), dict):
        existing: dict[str, Any] = {}
        try:
            existing = json.loads(holding.get("specs_json") or "{}")
        except (TypeError, ValueError):
            existing = {}
        existing.update(op["specs"])
        fields.append("specs_json = ?")
        values.append(json.dumps(existing))
    if not fields:
        return False
    fields.append("updated_at = ?")
    values.append(now)
    values.append(int(holding["id"]))
    conn.execute(
        f"UPDATE property_holdings SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    return True


def create_property_room_conn(
    conn: sqlite3.Connection,
    *,
    property_code: str = "",
    property_name: str = "",
    room_name: str,
    description: str = "",
) -> dict[str, Any] | None:
    holding = find_holding_conn(conn, code=property_code, name=property_name)
    if not holding:
        return None
    root_id = int(holding["root_location_id"])
    existing = conn.execute(
        "SELECT id, name FROM locations WHERE parent_location_id = ? AND name = ? COLLATE NOCASE",
        (root_id, room_name),
    ).fetchone()
    if existing:
        return {"location_id": int(existing["id"]), "name": existing["name"], "created": False}
    now = _utc_now()
    loc_code = _slug_code(room_name, f"room_{holding['code']}")
    conn.execute(
        """
        INSERT INTO locations (code, name, description_short, description_long, parent_location_id, is_discovered, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (loc_code, room_name, description[:240], description[:2000], root_id, now, now),
    )
    room_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        """
        INSERT OR IGNORE INTO location_connections (from_location_id, to_location_id, connection_type, label, created_at)
        VALUES (?, ?, 'contains', ?, ?)
        """,
        (root_id, room_id, room_name, now),
    )
    try:
        conn.execute(
            """
            INSERT INTO property_rooms (property_id, location_id, room_kind, layout_notes, created_at)
            VALUES (?, ?, 'room', ?, ?)
            """,
            (int(holding["id"]), room_id, description[:500] if description else None, now),
        )
    except sqlite3.OperationalError:
        pass
    return {"location_id": room_id, "name": room_name, "created": True}


def create_fixture_conn(
    conn: sqlite3.Connection,
    *,
    property_code: str = "",
    property_name: str = "",
    room_name: str = "",
    room_location_id: int | None = None,
    name: str,
    fixture_kind: str = "furniture",
    description: str = "",
    condition_pct: int = 100,
    specs: dict[str, Any] | None = None,
    installed_at_turn: int = 0,
) -> dict[str, Any] | None:
    holding = find_holding_conn(conn, code=property_code, name=property_name)
    if not holding:
        return None
    room_loc_id = room_location_id
    if not room_loc_id and room_name.strip():
        row = conn.execute(
            """
            SELECT l.id FROM locations l
            WHERE l.parent_location_id = ? AND l.name = ? COLLATE NOCASE
            LIMIT 1
            """,
            (int(holding["root_location_id"]), room_name.strip()),
        ).fetchone()
        if row:
            room_loc_id = int(row["id"])
    fixture_name = str(name or "").strip()
    if not fixture_name:
        return None
    now = _utc_now()
    specs_json = json.dumps(specs or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO property_fixtures (
            property_id, room_location_id, fixture_kind, name, description,
            condition_pct, specs_json, installed_at_turn, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(holding["id"]),
            int(room_loc_id) if room_loc_id else None,
            str(fixture_kind or "furniture").strip().lower(),
            fixture_name,
            description[:2000] if description else None,
            max(0, min(100, int(condition_pct))),
            specs_json,
            int(installed_at_turn) if installed_at_turn else None,
            now,
        ),
    )
    fixture_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {
        "id": fixture_id,
        "name": fixture_name,
        "property_code": holding["code"],
        "room_location_id": room_loc_id,
    }


def assign_staff_conn(
    conn: sqlite3.Connection,
    *,
    property_code: str = "",
    property_name: str = "",
    npc_name: str = "",
    npc_code: str = "",
    role: str = "staff",
) -> dict[str, Any] | None:
    holding = find_holding_conn(conn, code=property_code, name=property_name)
    if not holding:
        return None
    npc_row = None
    if npc_code.strip():
        npc_row = conn.execute(
            "SELECT id, name, code FROM npcs WHERE code = ? AND status = 'alive' LIMIT 1",
            (npc_code.strip(),),
        ).fetchone()
    if not npc_row and npc_name.strip():
        npc_row = conn.execute(
            "SELECT id, name, code FROM npcs WHERE name = ? COLLATE NOCASE AND status = 'alive' LIMIT 1",
            (npc_name.strip(),),
        ).fetchone()
    if not npc_row:
        return None
    role_text = str(role or "staff").strip().lower()
    conn.execute(
        """
        UPDATE npcs SET assigned_property_id = ?, assigned_role = ?, updated_at = ?
        WHERE id = ?
        """,
        (int(holding["id"]), role_text, _utc_now(), int(npc_row["id"])),
    )
    return {
        "npc_id": int(npc_row["id"]),
        "npc_code": npc_row["code"],
        "name": npc_row["name"],
        "role": role_text,
        "property_code": holding["code"],
    }


def proposal_fixture_from_archivist_op(op: dict[str, Any]) -> dict[str, Any]:
    return {
        "property_code": str(op.get("property_code") or "").strip(),
        "property_name": str(op.get("property_name") or "").strip(),
        "room_name": str(op.get("room_name") or "").strip(),
        "name": str(op.get("name") or "").strip(),
        "fixture_kind": str(op.get("fixture_kind") or "furniture").strip().lower(),
        "description": str(op.get("description") or "").strip(),
        "condition_pct": int(op.get("condition_pct") or 100),
        "specs": op.get("specs") if isinstance(op.get("specs"), dict) else {},
    }


def _family_token(name: str, code: str = "") -> str | None:
    hay = f"{name} {code}".lower()
    if "driscoll" in hay:
        return "driscoll"
    return None


def _room_count_at_root(conn: sqlite3.Connection, root_location_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM locations WHERE parent_location_id = ?",
        (int(root_location_id),),
    ).fetchone()
    return int(row["c"] or 0)


def find_canonical_holding_for_family_conn(
    conn: sqlite3.Connection,
    *,
    family: str,
    pc_id: int | None = None,
) -> dict[str, Any] | None:
    """Best holding for a property family — most rooms, then lowest id."""
    pid = pc_id or _hero_pc_id(conn)
    if not pid:
        return None
    candidates: list[dict[str, Any]] = []
    for row in conn.execute(
        "SELECT * FROM property_holdings WHERE player_character_id = ?",
        (pid,),
    ).fetchall():
        h = dict(row)
        token = _family_token(str(h.get("name") or ""), str(h.get("code") or ""))
        if token != family:
            continue
        h["_room_count"] = _room_count_at_root(conn, int(h["root_location_id"]))
        candidates.append(h)
    if not candidates:
        return None
    candidates.sort(key=lambda h: (-int(h["_room_count"]), int(h["id"])))
    best = dict(candidates[0])
    best.pop("_room_count", None)
    return best


def dedupe_spurious_holdings_conn(
    conn: sqlite3.Connection,
    state: dict[str, Any],
    *,
    canonical_codes: dict[str, str] | None = None,
) -> list[str]:
    """Remove empty / mis-rooted duplicate holdings; keep canonical per family."""
    canonical_codes = canonical_codes or {"driscoll": "house_driscoll_city"}
    removed: list[str] = []
    pid = _hero_pc_id(conn)
    if not pid:
        return removed

    settlement_roots = frozenset({"crownstone", "market district", "town square"})

    for row in conn.execute(
        "SELECT * FROM property_holdings WHERE player_character_id = ? ORDER BY id",
        (pid,),
    ).fetchall():
        h = dict(row)
        code = str(h.get("code") or "")
        family = _family_token(str(h.get("name") or ""), code)
        room_count = _room_count_at_root(conn, int(h["root_location_id"]))
        root = conn.execute(
            "SELECT name FROM locations WHERE id = ?",
            (int(h["root_location_id"]),),
        ).fetchone()
        root_name = str(root["name"] or "").strip().lower() if root else ""

        canonical_code = canonical_codes.get(family or "")
        if canonical_code and code == canonical_code:
            continue

        spurious = False
        if family and canonical_code:
            canonical = find_holding_conn(conn, code=canonical_code)
            if canonical and code != canonical_code:
                if room_count == 0 or room_count < _room_count_at_root(conn, int(canonical["root_location_id"])):
                    spurious = True
        if room_count == 0 and root_name in settlement_roots:
            spurious = True

        if spurious:
            conn.execute("DELETE FROM property_holdings WHERE id = ?", (int(h["id"]),))
            removed.append(code)

    if removed:
        sync_property_portfolio(conn, state)
        portfolio = dict(state.get("property_portfolio") or {})
        active = str(portfolio.get("active_residence_code") or "").strip()
        if not active or active in removed:
            for family, canon in canonical_codes.items():
                holding = find_holding_conn(conn, code=canon)
                if holding:
                    portfolio["active_residence_code"] = canon
                    break
            state["property_portfolio"] = portfolio
    return removed


def set_active_residence(state: dict[str, Any], code: str) -> bool:
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    code = str(code or "").strip()
    if not any(isinstance(h, dict) and h.get("code") == code for h in holdings):
        return False
    state["property_portfolio"] = {**portfolio, "active_residence_code": code}
    return True


def backfill_holdings_from_state_conn(conn: sqlite3.Connection, state: dict[str, Any]) -> int:
    """Seed SQL holdings from JSON portfolio when legacy saves lack SQL rows."""
    count = conn.execute("SELECT COUNT(*) AS c FROM property_holdings").fetchone()["c"]
    if int(count or 0) > 0:
        return 0
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    pc_id = _hero_pc_id(conn)
    if not pc_id:
        return 0
    turn = int(state.get("turn") or 0)
    created = 0
    for entry in holdings:
        if not isinstance(entry, dict):
            continue
        proposal = {
            "granted": True,
            "code": entry.get("code"),
            "name": entry.get("name"),
            "property_kind": entry.get("property_kind") or "townhouse",
            "title_status": entry.get("title_status") or "owned",
            "acquired_via": entry.get("acquired_via") or "legacy_json",
            "deed_summary": entry.get("deed_summary") or "",
            "specs": entry.get("specs") if isinstance(entry.get("specs"), dict) else {},
        }
        if create_holding_conn(conn, player_character_id=pc_id, proposal=proposal, acquired_at_turn=turn):
            created += 1
    return created


def holdings_payload(conn: sqlite3.Connection, state: dict[str, Any]) -> dict[str, Any]:
    """Full holdings list for API — rooms, fixtures, staff."""
    sync_property_portfolio(conn, state)
    portfolio = state.get("property_portfolio") or {}
    enriched: list[dict[str, Any]] = []
    for h in portfolio.get("holdings") or []:
        if not isinstance(h, dict):
            continue
        row = get_holding_by_code_conn(conn, str(h.get("code") or ""))
        rooms: list[dict[str, Any]] = []
        fixtures: list[dict[str, Any]] = []
        staff: list[dict[str, Any]] = []
        if row:
            rooms = list_rooms_for_holding_conn(conn, int(row["root_location_id"]))
            fixtures = list_fixtures_for_holding_conn(conn, int(row["id"]))
            staff = list_staff_for_holding_conn(conn, int(row["id"]))
        enriched.append(
            {
                **h,
                "rooms": rooms,
                "fixtures": fixtures,
                "staff": staff,
                "is_active_residence": h.get("code") == portfolio.get("active_residence_code"),
            }
        )
    return {
        "holdings": enriched,
        "active_residence_code": portfolio.get("active_residence_code"),
    }

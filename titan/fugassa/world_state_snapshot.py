"""Deterministic campaign state snapshot — ADR §5.4 / C3.

Single canonical view of turn, party, quests, titles, property, combat —
consumed by GM prompt, summary API, and debug panel.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from titan.fugassa import world_time_engine
from titan.fugassa.title_engine import active_title_from_state


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _party_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for member in state.get("party") or []:
        if not isinstance(member, dict):
            continue
        role = str(member.get("role") or "companion").strip().lower()
        if role in ("player", "hero", ""):
            role = "hero"
        out.append(
            {
                "name": str(member.get("name") or "Unknown").strip(),
                "role": role,
                "npc_code": str(member.get("npc_code") or member.get("code") or "").strip() or None,
                "level": int(member.get("level") or 1),
                "hp": member.get("hp"),
                "max_hp": member.get("max_hp"),
            }
        )
    return out


def _format_party_line(party: list[dict[str, Any]]) -> str:
    if not party:
        return "none"
    parts: list[str] = []
    for m in party:
        name = m.get("name") or "Unknown"
        role = m.get("role") or "companion"
        level = m.get("level")
        level_note = f", L{level}" if role == "hero" and level else ""
        code = m.get("npc_code")
        code_note = f", npc:{code}" if code and role != "hero" else ""
        parts.append(f"{name} ({role}{level_note}{code_note})")
    return ", ".join(parts)


def _active_quests(state: dict[str, Any]) -> list[dict[str, Any]]:
    quests = state.get("quests") if isinstance(state.get("quests"), dict) else {}
    active = quests.get("active") if isinstance(quests.get("active"), list) else []
    out: list[dict[str, Any]] = []
    for q in active:
        if not isinstance(q, dict):
            continue
        out.append(
            {
                "name": str(q.get("name") or q.get("title") or "Quest").strip(),
                "scale": str(q.get("scale") or "standard"),
                "chain_code": q.get("chain_code"),
                "rewards_deferred": bool(q.get("rewards_deferred")),
            }
        )
    return out


def _recently_completed_quests(conn: sqlite3.Connection, *, limit: int = 5) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT code, title, updated_at
            FROM quests
            WHERE status = 'completed'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"code": str(r["code"]), "title": str(r["title"])} for r in rows]


def _titles_block(state: dict[str, Any]) -> dict[str, Any]:
    block = state.get("player_titles") if isinstance(state.get("player_titles"), dict) else {}
    titles = block.get("titles") if isinstance(block.get("titles"), list) else []
    active = active_title_from_state(state)
    return {
        "active_code": str(block.get("active_code") or (active or {}).get("code") or "").strip() or None,
        "active_display": str(block.get("active_display") or (active or {}).get("display") or "").strip() or None,
        "titles": [
            {
                "code": t.get("code"),
                "display": t.get("display"),
                "impact_tier": t.get("impact_tier"),
            }
            for t in titles
            if isinstance(t, dict)
        ],
    }


def _property_block(state: dict[str, Any], conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """ADR §7.1 — property holdings via holdings_payload when SQL conn available."""
    if conn is not None:
        from titan.fugassa.property_repository import holdings_payload, sync_property_portfolio

        sync_property_portfolio(conn, state)
        payload = holdings_payload(conn, state)
        holdings_out: list[dict[str, Any]] = []
        for h in payload.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            rooms = h.get("rooms") if isinstance(h.get("rooms"), list) else []
            staff = h.get("staff") if isinstance(h.get("staff"), list) else []
            staff_names = [
                str(s.get("name") or s.get("npc_name") or "").strip()
                for s in staff
                if isinstance(s, dict) and str(s.get("name") or s.get("npc_name") or "").strip()
            ]
            if not staff_names:
                staff_names = [
                    str(s).strip() for s in (h.get("staff_names") or []) if str(s).strip()
                ]
            holdings_out.append(
                {
                    "code": h.get("code"),
                    "name": h.get("name"),
                    "room_count": len(rooms) if rooms else h.get("room_count"),
                    "staff_names": staff_names,
                }
            )
        return {
            "active_residence_code": payload.get("active_residence_code"),
            "holdings": holdings_out,
        }

    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    active_code = str(portfolio.get("active_residence_code") or "").strip() or None
    holdings_out: list[dict[str, Any]] = []
    for h in portfolio.get("holdings") or []:
        if not isinstance(h, dict):
            continue
        staff = h.get("staff_names") if isinstance(h.get("staff_names"), list) else []
        holdings_out.append(
            {
                "code": h.get("code"),
                "name": h.get("name"),
                "room_count": h.get("room_count"),
                "staff_names": [str(s) for s in staff if str(s).strip()],
                "property_kind": h.get("property_kind"),
                "title_status": h.get("title_status"),
            }
        )
    return {
        "active_residence_code": active_code,
        "holdings": holdings_out,
    }


def _location_labels(state: dict[str, Any]) -> dict[str, str]:
    loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    settlement = str(loc.get("settlement_name") or "").strip()
    place = str(loc.get("place_label") or loc.get("name") or "").strip()
    return {
        "settlement": settlement,
        "place": place,
        "name": str(loc.get("name") or "").strip(),
    }


def build_snapshot_dict(
    db_path: str | None,
    state: dict[str, Any],
    *,
    recent_quest_limit: int = 5,
) -> dict[str, Any]:
    """Structured campaign state for API and UI (ADR §7.1 `campaign_state`)."""
    loc = _location_labels(state)
    wt = state.get("world_time") if isinstance(state.get("world_time"), dict) else {}
    time_label = world_time_engine.format_chat_header(wt, loc.get("place") or loc.get("name") or None)
    party = _party_entries(state)
    active_quests = _active_quests(state)
    recently_completed: list[dict[str, Any]] = []
    conn: sqlite3.Connection | None = None
    if db_path and os.path.isfile(db_path):
        conn = _connect(db_path)
        try:
            recently_completed = _recently_completed_quests(conn, limit=recent_quest_limit)
        finally:
            pass

    try:
        return {
            "turn": int(state.get("turn") or 0),
            "time_label": time_label,
            "location": loc,
            "party": party,
            "quests": {
                "active": active_quests,
                "recently_completed": recently_completed,
            },
            "titles": _titles_block(state),
            "property": _property_block(state, conn),
            "in_combat": bool(state.get("in_combat")),
        }
    finally:
        if conn is not None:
            conn.close()


def format_chronicle_for_api(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ADR §7.1 — summary API chronicle row shape."""
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        out.append(
            {
                "event_type": ev.get("event_type"),
                "title": ev.get("title"),
                "summary": ev.get("summary"),
                "turn_id": ev.get("turn_id"),
                "created_at": ev.get("created_at"),
            }
        )
    return out


def _geography_appendix(db_path: str | None, state: dict[str, Any]) -> str:
    if not db_path or not os.path.isfile(db_path):
        return ""
    from titan.fugassa.context_builder import build_settlement_context_block

    block = build_settlement_context_block(state, db_path=db_path)
    return block.strip()


def build_snapshot_text(
    db_path: str | None,
    state: dict[str, Any],
    *,
    truncate: int | None = None,
) -> str:
    """GM/debug prompt block — ADR §5.4 canonical header."""
    snap = build_snapshot_dict(db_path, state)
    loc = snap.get("location") if isinstance(snap.get("location"), dict) else {}
    place_bits = [b for b in (loc.get("settlement"), loc.get("place")) if b]
    place_line = " · ".join(place_bits) if place_bits else (loc.get("name") or "unknown location")

    active_quests = (snap.get("quests") or {}).get("active") or []
    if active_quests:
        quest_active_line = "; ".join(
            f"{q.get('name')} ({q.get('scale', 'standard')})" for q in active_quests if isinstance(q, dict)
        )
    else:
        quest_active_line = "none"

    completed = (snap.get("quests") or {}).get("recently_completed") or []
    if completed:
        quest_done_line = "; ".join(str(q.get("title") or q.get("code") or "") for q in completed if isinstance(q, dict))
    else:
        quest_done_line = "none"

    titles = snap.get("titles") if isinstance(snap.get("titles"), dict) else {}
    active_title = str(titles.get("active_display") or "").strip()
    titles_line = f"{active_title} (active)" if active_title else "none"

    prop = snap.get("property") if isinstance(snap.get("property"), dict) else {}
    holdings = prop.get("holdings") if isinstance(prop.get("holdings"), list) else []
    active_code = str(prop.get("active_residence_code") or "").strip()
    if holdings:
        prop_parts: list[str] = []
        for h in holdings:
            if not isinstance(h, dict):
                continue
            name = str(h.get("name") or h.get("code") or "Property")
            marker = " (active residence)" if active_code and h.get("code") == active_code else ""
            prop_parts.append(f"{name}{marker}")
        property_line = ", ".join(prop_parts)
    else:
        property_line = "none"

    lines = [
        "CAMPAIGN STATE SNAPSHOT (canonical — do not contradict):",
        f"Turn: {snap.get('turn', 0)} · {snap.get('time_label', '')} · {place_line}",
        f"Party: {_format_party_line(snap.get('party') or [])}",
        f"Quests active: {quest_active_line}",
        f"Quests recently completed: {quest_done_line}",
        f"Titles: {titles_line}",
        f"Property: {property_line}",
        f"In combat: {'yes' if snap.get('in_combat') else 'no'}",
    ]
    geo = _geography_appendix(db_path, state)
    if geo:
        lines.append(geo)
    text = "\n".join(lines)
    if truncate and len(text) > truncate:
        return text[: max(0, truncate - 3)] + "..."
    return text

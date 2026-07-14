"""Player titles (renown display) + mechanical bonuses."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

TITLE_BONUSES_BY_TIER: dict[int, dict[str, Any]] = {
    2: {"social_bonus": 0, "notes": "Local recognition"},
    3: {"social_bonus": 1, "notes": "Regional standing"},
    4: {"social_bonus": 2, "persuasion_bonus": 1, "notes": "Legendary epithet"},
}


def default_bonuses_for_tier(impact_tier: int) -> dict[str, Any]:
    tier = max(2, min(4, int(impact_tier or 2)))
    return dict(TITLE_BONUSES_BY_TIER.get(tier, TITLE_BONUSES_BY_TIER[2]))


def _hero_pc_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def _parse_bonuses(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def list_titles_conn(conn: sqlite3.Connection, *, pc_id: int | None = None) -> list[dict[str, Any]]:
    pid = pc_id or _hero_pc_id(conn)
    if not pid:
        return []
    rows = conn.execute(
        """
        SELECT renown_code, title_display, scope_type, scope_id, impact_tier, granted_at_turn, bonuses_json
        FROM player_renown
        WHERE player_character_id = ?
        ORDER BY granted_at_turn ASC, id ASC
        """,
        (pid,),
    ).fetchall()
    titles: list[dict[str, Any]] = []
    for row in rows:
        bonuses = _parse_bonuses(row["bonuses_json"])
        if not bonuses:
            bonuses = default_bonuses_for_tier(int(row["impact_tier"] or 2))
        titles.append(
            {
                "code": row["renown_code"],
                "display": row["title_display"] or row["renown_code"],
                "scope_type": row["scope_type"],
                "scope_id": row["scope_id"],
                "impact_tier": int(row["impact_tier"] or 2),
                "granted_at_turn": int(row["granted_at_turn"] or 0),
                "bonuses": bonuses,
            }
        )
    return titles


def active_title_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    block = state.get("player_titles") if isinstance(state.get("player_titles"), dict) else {}
    active_code = str(block.get("active_code") or "").strip()
    titles = block.get("titles") if isinstance(block.get("titles"), list) else []
    if not titles:
        return None
    if active_code:
        for t in titles:
            if isinstance(t, dict) and t.get("code") == active_code:
                return t
    return titles[-1] if titles else None


def aggregate_bonuses(state: dict[str, Any]) -> dict[str, Any]:
    title = active_title_from_state(state)
    if not title:
        return {}
    bonuses = title.get("bonuses") if isinstance(title.get("bonuses"), dict) else {}
    return dict(bonuses)


def sync_player_titles(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    titles = list_titles_conn(conn)
    if not titles:
        state.pop("player_titles", None)
        return
    row = conn.execute(
        "SELECT active_title_code FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
    ).fetchone()
    active_code = str(row["active_title_code"] or "").strip() if row else ""
    if not active_code:
        active_code = titles[-1]["code"]
    state["player_titles"] = {
        "titles": titles,
        "active_code": active_code,
        "active_display": next(
            (t["display"] for t in titles if t.get("code") == active_code),
            titles[-1]["display"],
        ),
        "bonuses": aggregate_bonuses(
            {"player_titles": {"titles": titles, "active_code": active_code}}
        ),
    }


def set_active_title(db_path: str, renown_code: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE player_characters SET active_title_code = ? WHERE code = 'pc_hero'",
            (str(renown_code).strip(),),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()

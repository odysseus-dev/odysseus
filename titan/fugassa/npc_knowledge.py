"""Knowledge boundaries — ADR §E: what an NPC knows about the player.

Two separate tracks, both engine-owned (never GM prose, never player declaration
— a claim like "everyone knows me" is a Reality Guard `declare_world_fact`
reject):

  A) Social knowledge (`npc_relationships.recognition_level` /
     `met_player` / `knowledge_sources`) — a ladder from `stranger` up to
     `personal`. Upgrades only ever move up the ladder; nothing ever demotes
     an NPC's knowledge of the player automatically.
  B) Sensory/rumor propagation (`propagate_rumor`) — location-radius based,
     using the `grid_overworld_{x}_{y}_{z}` location code convention, so a
     tier-3/4 event can update `knowledge_sources` for NPCs who were never at
     the scene without touching their personal relationship/hexagon at all
     (ADR §E2a: mass events never iterate hexagon for a crowd).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

LADDER = ("stranger", "rumor", "face_only", "acquainted", "personal")
_GRID_CODE_RE = re.compile(r"^grid_(\w+)_(-?\d+)_(-?\d+)_(-?\d+)$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _level_index(level: str | None) -> int:
    try:
        return LADDER.index(str(level or "stranger"))
    except ValueError:
        return 0


def _ensure_relationship_row(conn: sqlite3.Connection, npc_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    if row:
        return row
    conn.execute(
        """
        INSERT INTO npc_relationships (source_npc_id, target_type, target_id, attitude, trust, created_at, updated_at)
        VALUES (?, 'player', NULL, 'neutral', 0, ?, ?)
        """,
        (npc_id, _utc_now(), _utc_now()),
    )
    return conn.execute(
        "SELECT * FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()


def _sources(row: sqlite3.Row) -> list[str]:
    try:
        raw = row["knowledge_sources"]
    except (IndexError, KeyError):
        return []
    try:
        parsed = json.loads(raw) if raw else []
        return list(parsed) if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def upgrade_recognition_conn(
    conn: sqlite3.Connection,
    npc_id: int,
    min_level: str,
    *,
    source: str | None = None,
    met_player: bool | None = None,
) -> bool:
    """Move `recognition_level` up to at least `min_level` (never down). Returns
    whether anything actually changed."""
    row = _ensure_relationship_row(conn, npc_id)
    current = _level_index(row["recognition_level"])
    target = _level_index(min_level)
    new_level = LADDER[max(current, target)]
    sources = _sources(row)
    changed = new_level != row["recognition_level"]
    if source and source not in sources:
        sources.append(source)
        changed = True
    new_met = bool(row["met_player"]) or bool(met_player)
    if new_met != bool(row["met_player"]):
        changed = True
    if not changed:
        return False
    conn.execute(
        """
        UPDATE npc_relationships
        SET recognition_level = ?, knowledge_sources = ?, met_player = ?, updated_at = ?
        WHERE source_npc_id = ? AND target_type = 'player'
        """,
        (new_level, json.dumps(sources), 1 if new_met else 0, _utc_now(), npc_id),
    )
    return True


def mark_met_conn(conn: sqlite3.Connection, npc_id: int, *, level: str = "acquainted", source: str = "witness") -> bool:
    """A face-to-face interaction (social/combat) — ADR §E: `met_player` flips
    true on first personal interaction in a scene, not on world generation."""
    return upgrade_recognition_conn(conn, npc_id, level, source=source, met_player=True)


def upgrade_to_personal_if_trusted(conn: sqlite3.Connection, npc_id: int, *, trust_threshold: int = 7) -> bool:
    row = conn.execute(
        "SELECT trust FROM npc_relationships WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    if not row or int(row["trust"] or 0) < trust_threshold:
        return False
    return upgrade_recognition_conn(conn, npc_id, "personal")


def add_knowledge_source(db_path: str, npc_id: int, source: str, *, min_level: str = "rumor") -> bool:
    """External hook (no open transaction) for poster/faction/newspaper/told_by
    knowledge that doesn't come from a personal encounter."""
    if not db_path or not os.path.isfile(db_path):
        return False
    conn = _connect(db_path)
    try:
        changed = upgrade_recognition_conn(conn, npc_id, min_level, source=source)
        conn.commit()
        return changed
    finally:
        conn.close()


def _parse_grid_code(code: str | None) -> tuple[str, int, int, int] | None:
    if not code:
        return None
    m = _GRID_CODE_RE.match(code)
    if not m:
        return None
    map_name, x, y, z = m.groups()
    return map_name, int(x), int(y), int(z)


def propagate_rumor_conn(
    conn: sqlite3.Connection,
    *,
    origin_location_code: str,
    radius_cells: int,
    source: str = "told_by",
    level: str = "rumor",
    exclude_npc_ids: list[int] | None = None,
) -> list[int]:
    """ADR §E2e / part B — sensory+rumor propagation for tier 3–4 events, using
    an already-open connection (safe to call mid-transaction, e.g. from
    `quest_engine`'s reward grant — a fresh connection here could deadlock
    against that open write).

    Upgrades `knowledge_sources`/`recognition_level` (never `met_player`, never
    hexagon/trust) for every NPC whose current location is a grid cell within
    `radius_cells` of the origin — Chebyshev distance, matching the 8-directional
    cardinal/diagonal movement the grid engine already uses. Returns the list of
    NPC ids that were updated.
    """
    origin = _parse_grid_code(origin_location_code)
    if not origin:
        return []
    origin_map, ox, oy, oz = origin
    exclude = set(exclude_npc_ids or [])

    updated: list[int] = []
    rows = conn.execute(
        """
        SELECT n.id AS npc_id, l.code AS loc_code
        FROM npcs n JOIN locations l ON l.id = n.current_location_id
        WHERE n.status != 'dead'
        """
    ).fetchall()
    for row in rows:
        if row["npc_id"] in exclude:
            continue
        parsed = _parse_grid_code(row["loc_code"])
        if not parsed:
            continue
        map_name, x, y, z = parsed
        if map_name != origin_map or z != oz:
            continue
        distance = max(abs(x - ox), abs(y - oy))
        if distance > radius_cells:
            continue
        if upgrade_recognition_conn(conn, row["npc_id"], level, source=source):
            updated.append(row["npc_id"])
    return updated


def propagate_rumor(
    db_path: str,
    *,
    origin_location_code: str,
    radius_cells: int,
    source: str = "told_by",
    level: str = "rumor",
    exclude_npc_ids: list[int] | None = None,
) -> list[int]:
    """External hook (no open transaction) — wraps `propagate_rumor_conn`."""
    if not db_path or not os.path.isfile(db_path):
        return []
    conn = _connect(db_path)
    try:
        updated = propagate_rumor_conn(
            conn,
            origin_location_code=origin_location_code,
            radius_cells=radius_cells,
            source=source,
            level=level,
            exclude_npc_ids=exclude_npc_ids,
        )
        conn.commit()
        return updated
    finally:
        conn.close()


def recognition_summary(conn: sqlite3.Connection, npc_id: int) -> dict[str, Any]:
    """Context-builder helper — ADR §E part C: strangers get a knowledge summary,
    never the player's full backstory."""
    row = conn.execute(
        "SELECT recognition_level, met_player, knowledge_sources, trust, attitude FROM npc_relationships "
        "WHERE source_npc_id = ? AND target_type = 'player'",
        (npc_id,),
    ).fetchone()
    if not row:
        return {"recognition_level": "stranger", "met_player": False, "knowledge_sources": []}
    return {
        "recognition_level": row["recognition_level"] or "stranger",
        "met_player": bool(row["met_player"]),
        "knowledge_sources": _sources(row),
        "trust": int(row["trust"] or 0),
        "attitude": row["attitude"] or "neutral",
    }

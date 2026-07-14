"""ADR §4b — memory retrieval graph.

`memory_links(memory_id, entity_type, entity_id, link_type)` lets the context
builder score each NPC's memories by scene relevance (location / active quest
/ other-NPC-present match) instead of importance+recency alone:

    "Vyvolání podle relevance: scene graph -> memories linkované na hráče,
    quest, lokaci, NPC ve scéně."

Canon (`npc_memories`) is never pruned for the prompt — this module only
re-ranks the top-K a caller asks for. If no links exist yet (e.g. very early
game), scoring gracefully degrades to importance/recency, exactly like the
plain query it replaces.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_ENTITY_TYPES = {"subject", "location", "quest", "npc", "item", "event"}


def link_memory_conn(
    conn: sqlite3.Connection,
    memory_id: int,
    links: list[tuple[str, int | None, str]],
) -> int:
    """`links`: list of (entity_type, entity_id, link_type). Skips dupes/invalid types."""
    written = 0
    for entity_type, entity_id, link_type in links:
        if entity_type not in _ENTITY_TYPES or entity_id is None:
            continue
        existing = conn.execute(
            "SELECT id FROM memory_links WHERE memory_id = ? AND entity_type = ? AND entity_id = ?",
            (memory_id, entity_type, entity_id),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO memory_links (memory_id, entity_type, entity_id, link_type) VALUES (?, ?, ?, ?)",
            (memory_id, entity_type, entity_id, link_type or entity_type),
        )
        written += 1
    return written


def auto_link_memory_conn(
    conn: sqlite3.Connection,
    memory_id: int,
    *,
    npc_id: int | None = None,
    location_id: int | None = None,
    event_id: int | None = None,
    quest_ids: list[int] | None = None,
    other_npc_ids: list[int] | None = None,
    item_ids: list[int] | None = None,
) -> int:
    """Standard link set for a freshly-written `npc_memories` row (ADR §4b)."""
    links: list[tuple[str, int | None, str]] = []
    if npc_id:
        links.append(("subject", npc_id, "self"))
    if location_id:
        links.append(("location", location_id, "witnessed_at"))
    if event_id:
        links.append(("event", event_id, "origin"))
    for qid in quest_ids or []:
        links.append(("quest", qid, "relates_to"))
    for nid in other_npc_ids or []:
        links.append(("npc", nid, "present"))
    for iid in item_ids or []:
        links.append(("item", iid, "mentioned"))
    return link_memory_conn(conn, memory_id, links)


def scene_relevance_top_k(
    conn: sqlite3.Connection,
    npc_id: int,
    *,
    location_id: int | None = None,
    quest_ids: list[int] | None = None,
    npc_ids_in_scene: list[int] | None = None,
    top_k: int = 6,
) -> list[dict[str, Any]]:
    """ADR §4b relevance score: graph match + recency + importance, per NPC.

    Returns dicts (not sqlite3.Row) so callers can subscript exactly like the
    plain query they replace: `row["id"]`, `row["memory_text"]`, etc.
    """
    rows = conn.execute(
        """
        SELECT nm.id, nm.memory_text, nm.importance, nm.created_at,
               ml.entity_type, ml.entity_id
        FROM npc_memories nm
        LEFT JOIN memory_links ml ON ml.memory_id = nm.id
        WHERE nm.npc_id = ? AND nm.is_active = 1
        ORDER BY nm.created_at DESC, nm.id DESC
        """,
        (npc_id,),
    ).fetchall()
    if not rows:
        return []

    quest_set = set(quest_ids or [])
    npc_set = set(npc_ids_in_scene or [])
    by_id: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for r in rows:
        mid = int(r["id"])
        if mid not in by_id:
            by_id[mid] = {
                "id": mid,
                "memory_text": r["memory_text"],
                "importance": int(r["importance"] or 0),
                "created_at": r["created_at"],
                "link_score": 0,
            }
            order.append(mid)
        etype, eid = r["entity_type"], r["entity_id"]
        if etype == "location" and location_id and eid == location_id:
            by_id[mid]["link_score"] += 3
        elif etype == "quest" and eid in quest_set:
            by_id[mid]["link_score"] += 4
        elif etype == "npc" and eid in npc_set:
            by_id[mid]["link_score"] += 2

    for idx, mid in enumerate(order):
        recency_bonus = max(0, 3 - idx // 4)  # rows already DESC by created_at
        by_id[mid]["score"] = by_id[mid]["importance"] * 2 + by_id[mid]["link_score"] + recency_bonus

    ranked = sorted(by_id.values(), key=lambda m: (m["score"], m["id"]), reverse=True)
    return ranked[:top_k]

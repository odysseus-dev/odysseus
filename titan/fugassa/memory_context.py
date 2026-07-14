"""GM prompt memory context — ADR §4 layer table: structured top-K per NPC first,
optional sqlite-vec (M7) only as a supplementary fallback, never the canon.

Priority (ADR §7): SQLite + turn_resolution > pinned facts > campaign digest >
rolling chat > vec recall. This module only ever adds a read-only, clearly
labeled context block for the GM — it never mutates state.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from titan.fugassa import campaign_facts, campaign_chronicle, memory_graph, npc_generator, scene_summary_engine
from titan.fugassa.db import vec_index

_TOP_K_PER_NPC = 6  # ADR §4 "fair budget" (proposal: 5-7)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _npc_top_k_memories(
    conn: sqlite3.Connection,
    npc_id: int,
    top_k: int,
    *,
    location_id: int | None = None,
    quest_ids: list[int] | None = None,
    npc_ids_in_scene: list[int] | None = None,
) -> list[dict[str, Any]]:
    """ADR §4b: graph-scored top-K (location/quest/NPC-in-scene relevance),
    not just importance/recency — see `memory_graph.scene_relevance_top_k`."""
    return memory_graph.scene_relevance_top_k(
        conn,
        npc_id,
        location_id=location_id,
        quest_ids=quest_ids,
        npc_ids_in_scene=npc_ids_in_scene,
        top_k=top_k,
    )


def build_npc_scene_briefs_block(db_path: str | None, state: dict[str, Any]) -> str:
    """ADR §5 row 4 — compact hexagon/attitude/goals brief per NPC in the
    current scene. Top-K memories are a separate block (`build_scene_memory_block`);
    hidden agenda secrets stay gated in `npc_agenda`'s own GM-only banner."""
    if not db_path or not os.path.isfile(db_path):
        return ""
    loc = state.get("location_state") or {}
    npc_details = loc.get("npc_details") or []
    if not npc_details:
        return ""
    conn = _connect(db_path)
    lines: list[str] = []
    try:
        for d in npc_details:
            npc_id = d.get("npc_id")
            if not npc_id:
                continue
            brief = npc_generator.get_npc_scene_brief_conn(conn, int(npc_id))
            if not brief:
                continue
            parts = [f"{brief['name']}"]
            descriptor = " / ".join(x for x in (brief.get("race"), brief.get("class_role")) if x)
            if descriptor:
                parts.append(f"({descriptor})")
            line = " ".join(parts) + f" — attitude: {brief['attitude']} (trust {brief['trust']})"
            if brief.get("traits"):
                line += f"; traits: {', '.join(brief['traits'])}"
            if brief.get("goals"):
                line += f"; goals: {', '.join(brief['goals'])}"
            lines.append(f"  - {line}")
    finally:
        conn.close()
    if not lines:
        return ""
    return "NPCS IN SCENE (hexagon/goals — stay in character, do not invent contradicting traits):\n" + "\n".join(lines)


def build_pinned_facts_block(db_path: str | None, *, limit: int = 8) -> str:
    """ADR §5 row 7 — curated durable facts the GM must never contradict."""
    facts = campaign_facts.list_pinned_facts(db_path, limit=limit)
    if not facts:
        return ""
    lines = "\n".join(f"- {f}" for f in facts)
    return "PINNED CAMPAIGN FACTS (durable — never contradict):\n" + lines


def build_scene_summary_block(db_path: str | None, state: dict[str, Any], *, limit: int = 2) -> str:
    """ADR §5 row 8 — recap of this location's own recent history + per-turn deltas."""
    loc = state.get("location_state") or {}
    loc_id = loc.get("location_id")
    if not loc_id:
        return ""
    parts: list[str] = []
    entry_map = state.get("_location_entry_turn") or {}
    since_turn = entry_map.get(str(int(loc_id)))
    deltas = scene_summary_engine.latest_turn_deltas_for_location(
        db_path,
        int(loc_id),
        limit=4,
        since_turn=int(since_turn) if since_turn is not None else None,
    )
    if deltas:
        delta_lines = [
            f"- Turn {d['turn_number']}: {d['delta_text']}"
            for d in reversed(deltas)
        ]
        parts.append(
            "WHAT CHANGED THIS VISIT (mandatory — advance from here; do not repeat unchanged beats):\n"
            + "\n".join(delta_lines)
        )
    summaries = scene_summary_engine.latest_summaries_for_location(db_path, int(loc_id), limit=limit)
    if summaries:
        lines = "\n".join(f"- {s}" for s in summaries)
        parts.append("RECENT SCENE HISTORY at this location (from a previous visit):\n" + lines)
    if not parts:
        return ""
    return "\n\n".join(parts)


def build_scene_memory_block(
    db_path: str | None,
    state: dict[str, Any],
    player_text: str,
    *,
    top_k: int = _TOP_K_PER_NPC,
) -> str:
    """Structured memories for every NPC present in the current scene, plus an
    optional semantic-recall supplement (only if sqlite-vec is available and the
    structured set didn't already surface much). Returns "" if there's nothing
    to add — callers must treat that as normal, not an error.
    """
    if not db_path or not os.path.isfile(db_path):
        return ""
    loc = state.get("location_state") or {}
    npc_details = loc.get("npc_details") or []
    if not npc_details and not loc.get("npcs"):
        return ""

    conn = _connect(db_path)
    lines: list[str] = []
    seen_memory_ids: set[int] = set()
    try:
        names_at_scene = [str(n) for n in (loc.get("npcs") or []) if str(n).strip()]
        npc_ids: list[tuple[int, str]] = []
        if npc_details:
            npc_ids = [(int(d["npc_id"]), str(d.get("name") or "")) for d in npc_details if d.get("npc_id")]
        else:
            for name in names_at_scene:
                row = conn.execute("SELECT id FROM npcs WHERE name = ? LIMIT 1", (name,)).fetchone()
                if row:
                    npc_ids.append((int(row["id"]), name))

        loc_id_raw = loc.get("location_id")
        loc_id = int(loc_id_raw) if loc_id_raw else None
        quest_ids = [int(r["id"]) for r in conn.execute("SELECT id FROM quests WHERE status = 'active'").fetchall()]
        npc_ids_in_scene = [nid for nid, _ in npc_ids]

        for npc_id, name in npc_ids:
            rows = _npc_top_k_memories(
                conn, npc_id, top_k, location_id=loc_id, quest_ids=quest_ids, npc_ids_in_scene=npc_ids_in_scene
            )
            if not rows:
                continue
            lines.append(f"{name}:")
            for r in rows:
                seen_memory_ids.add(int(r["id"]))
                lines.append(f"  - (importance {r['importance']}) {r['memory_text']}")

        # Optional M7 supplement — only queried when the structured set above is
        # thin, per ADR §8 ("query only when structured SELECT/graph aren't enough").
        if len((player_text or "").strip()) >= 3 and (not lines or len(lines) < 3):
            hits = vec_index.semantic_recall(db_path, "npc_memory", player_text, top_k=top_k)
            extra: list[str] = []
            for hit in hits:
                mem_id = hit["row_id"]
                if mem_id in seen_memory_ids:
                    continue
                row = conn.execute(
                    "SELECT nm.memory_text, n.name FROM npc_memories nm JOIN npcs n ON n.id = nm.npc_id WHERE nm.id = ? AND nm.is_active = 1",
                    (mem_id,),
                ).fetchone()
                if row:
                    extra.append(f"  - [{row['name']}] {row['memory_text']} (semantic recall)")
            if extra:
                lines.append("Additional recollections (semantic recall — lower confidence):")
                lines.extend(extra)
    finally:
        conn.close()

    if not lines:
        return ""
    return "NPC MEMORY (structured top-K per NPC — do not invent beyond this):\n" + "\n".join(lines)


def build_chronicle_hint_block(
    db_path: str | None,
    state: dict[str, Any],
    player_text: str = "",
    *,
    limit: int = 5,
) -> str:
    """ADR §6.5 — recent typed chronicle events on location + optional vec supplement."""
    if not db_path or not os.path.isfile(db_path):
        campaign_chronicle.set_last_semantic_recall(None)
        return ""

    loc = state.get("location_state") or {}
    loc_id_raw = loc.get("location_id")
    loc_id = int(loc_id_raw) if loc_id_raw else None

    events = campaign_chronicle.query_recent(db_path, limit=limit * 3, exclude_turn_only=True)
    if loc_id is not None:
        filtered = [e for e in events if e.get("location_id") in (None, loc_id)]
        events = filtered[:limit]
    else:
        events = events[:limit]

    lines: list[str] = []
    for ev in reversed(events):
        summary = str(ev.get("summary") or ev.get("title") or "").strip()
        if not summary:
            continue
        lines.append(f"- [{ev.get('event_type', 'event')}] {summary}")

    recall_hits: list[dict[str, Any]] = []
    query = str(player_text or "").strip()
    if len(query) >= 3 and len(lines) < 3 and vec_index.is_available():
        hits = vec_index.semantic_recall(db_path, "event_log", query, top_k=5)
        seen_ids = {int(ev.get("event_id") or 0) for ev in events}
        extra: list[str] = []
        for hit in hits:
            row_id = int(hit.get("row_id") or 0)
            if row_id in seen_ids:
                continue
            preview = str(hit.get("text") or hit.get("text_preview") or "").strip()[:200]
            recall_hits.append(
                {
                    "kind": "event_log",
                    "row_id": row_id,
                    "score": hit.get("score"),
                    "text_preview": preview,
                }
            )
            if preview:
                extra.append(f"  - {preview} (semantic recall — lower confidence)")
        if extra:
            lines.append("Additional chronicle recall (semantic — lower confidence):")
            lines.extend(extra)

    campaign_chronicle.set_last_semantic_recall({"query": query[:200], "hits": recall_hits})

    if not lines:
        return ""
    return (
        "RECENT CAMPAIGN EVENTS (chronicle — authoritative recent history; do not contradict):\n"
        + "\n".join(lines)
    )

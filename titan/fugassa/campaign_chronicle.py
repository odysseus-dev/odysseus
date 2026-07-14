"""Campaign Chronicle bus — typed `event_log` writes, auto-pin, vec_index hook.

ADR: one write API for engine + archivist events. SQL + typed chronicle = kanon;
vec_index is supplement only.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_facts
from titan.fugassa.db import vec_index

LOG = logging.getLogger("titan.fugassa.campaign_chronicle")

# v1 typed events (ADR §5.3)
TYPED_EVENT_TYPES = frozenset(
    {
        "turn",
        "travel",
        "combat_start",
        "combat_end",
        "quest_progress",
        "quest_complete",
        "quest_failed",
        "companion_join",
        "companion_leave",
        "title_granted",
        "property_acquired",
        "agenda_reveal",
        "inventory_change",
        "currency_change",
        "discovery",
        "relationship_change",
        "npc_died",
        "level_up",
        "craft",
        "engine_only",
        "undo",
    }
)

_MAJOR_QUEST_SCALES = frozenset({"major", "epic", "finale"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ChronicleEvent:
    event_type: str
    title: str
    summary: str
    turn_id: int
    location_id: int | None = None
    ingame_occurred_at: str | None = None
    actor_type: str | None = None
    actor_id: int | None = None
    target_type: str | None = None
    target_id: int | None = None
    payload_json: dict[str, Any] = field(default_factory=dict)
    source: str = "engine"
    pin_fact: str | None = None
    code: str | None = None

    def index_text(self) -> str:
        return f"{self.title}. {self.summary}"[:500]


def hero_name_conn(conn: sqlite3.Connection) -> str:
    return _hero_name_conn(conn)


def _hero_name_conn(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT name FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    return str(row["name"] or "Hero") if row else "Hero"


def _holding_count_conn(conn: sqlite3.Connection, pc_id: int | None = None) -> int:
    if pc_id is None:
        row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
        if not row:
            return 0
        pc_id = int(row["id"])
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM property_holdings WHERE player_character_id = ?",
        (pc_id,),
    ).fetchone()
    return int(row["c"] or 0)


def should_pin_quest_complete(scale: str | None, *, chain_code: str | None = None, chain_position: int | None = None) -> bool:
    normalized = str(scale or "standard").strip().lower()
    if normalized in _MAJOR_QUEST_SCALES:
        return True
    if chain_code and chain_position is not None:
        try:
            return int(chain_position) >= 99
        except (TypeError, ValueError):
            pass
    return False


def make_quest_complete_event(
    *,
    quest_code: str,
    quest_title: str,
    hero_name: str,
    turn_id: int,
    location_id: int | None,
    scale: str | None,
    chain_code: str | None,
    chain_position: int | None,
    rewards_granted: list[str] | None = None,
) -> ChronicleEvent:
    summary = f"{hero_name} completed {quest_title}."
    pin = None
    if should_pin_quest_complete(scale, chain_code=chain_code, chain_position=chain_position):
        pin = f"{hero_name} completed {quest_title}."
    return ChronicleEvent(
        event_type="quest_complete",
        title=f"Quest complete: {quest_title}",
        summary=summary,
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source="engine",
        code=f"quest_complete_{quest_code}",
        pin_fact=pin,
        payload_json={
            "quest_code": quest_code,
            "quest_title": quest_title,
            "scale": scale or "standard",
            "chain_code": chain_code,
            "chain_position": chain_position,
            "rewards_granted": list(rewards_granted or []),
        },
    )


def make_quest_failed_event(
    *,
    quest_code: str,
    quest_title: str,
    hero_name: str,
    reason: str,
    turn_id: int,
    location_id: int | None,
) -> ChronicleEvent:
    summary = f"{hero_name}'s quest \"{quest_title}\" failed ({reason})."
    return ChronicleEvent(
        event_type="quest_failed",
        title=f"Quest failed: {quest_title}",
        summary=summary,
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source="engine",
        code=f"quest_failed_{quest_code}_t{turn_id}",
        pin_fact=f"{hero_name}'s quest \"{quest_title}\" failed: {reason}.",
        payload_json={"quest_code": quest_code, "quest_title": quest_title, "reason": reason},
    )


def make_quest_progress_event(
    *,
    quest_code: str,
    quest_title: str,
    objective: str,
    turn_id: int,
    location_id: int | None,
) -> ChronicleEvent:
    slug = _slug(objective)[:24] or "obj"
    return ChronicleEvent(
        event_type="quest_progress",
        title=f"Objective: {quest_title}",
        summary=f"{quest_title}: {objective}",
        turn_id=turn_id,
        location_id=location_id,
        source="engine",
        code=f"quest_progress_{quest_code}_{slug}_t{turn_id}",
        payload_json={"quest_code": quest_code, "quest_title": quest_title, "objective": objective},
    )


def make_companion_join_event(
    *,
    npc_code: str,
    npc_name: str,
    hero_name: str,
    turn_id: int,
    location_id: int | None,
) -> ChronicleEvent:
    return ChronicleEvent(
        event_type="companion_join",
        title=f"Companion: {npc_name}",
        summary=f"{npc_name} travels with {hero_name}.",
        turn_id=turn_id,
        location_id=location_id,
        target_type="npc",
        source="engine",
        code=f"companion_join_{npc_code}",
        pin_fact=f"{npc_name} travels with {hero_name}.",
        payload_json={"npc_code": npc_code, "npc_name": npc_name},
    )


def make_title_granted_event(
    *,
    renown_code: str,
    title_display: str,
    impact_tier: int,
    hero_name: str,
    turn_id: int,
    location_id: int | None,
) -> ChronicleEvent:
    label = title_display or renown_code
    return ChronicleEvent(
        event_type="title_granted",
        title=f"Title: {label}",
        summary=f"{hero_name} is known as {label} (tier {impact_tier}).",
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source="engine",
        code=f"title_granted_{renown_code}",
        pin_fact=None if impact_tier < 4 else f"{hero_name} is known as '{label}' — a tier-{impact_tier} deed.",
        payload_json={
            "renown_code": renown_code,
            "title_display": title_display,
            "impact_tier": impact_tier,
        },
    )


def make_travel_event(
    *,
    hero_name: str,
    from_label: str,
    to_label: str,
    turn_id: int,
    location_id: int | None,
    mode: str = "walk",
    source: str = "engine",
) -> ChronicleEvent:
    summary = f"{hero_name} traveled to {to_label}."
    if from_label and from_label != to_label:
        summary = f"{hero_name} traveled from {from_label} to {to_label}."
    return ChronicleEvent(
        event_type="travel",
        title=f"Travel: {to_label}",
        summary=summary,
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source=source,
        code=f"travel_{turn_id}_{_slug(to_label)}",
        payload_json={"from": from_label, "to": to_label, "mode": mode},
    )


def make_discovery_event(
    *,
    location_name: str,
    summary: str,
    turn_id: int,
    location_id: int | None,
    source: str = "engine",
) -> ChronicleEvent:
    text = str(summary or "").strip() or f"Something new was found at {location_name}."
    return ChronicleEvent(
        event_type="discovery",
        title=f"Discovery at {location_name}",
        summary=text[:500],
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source=source,
        code=f"discovery_{turn_id}_{_slug(location_name)}_{uuid.uuid4().hex[:6]}",
        payload_json={"location_name": location_name},
    )


def make_level_up_event(
    *,
    hero_name: str,
    from_level: int,
    to_level: int,
    turn_id: int,
    location_id: int | None,
    source: str = "engine",
) -> ChronicleEvent:
    return ChronicleEvent(
        event_type="level_up",
        title=f"Level {to_level}",
        summary=f"{hero_name} reached level {to_level}.",
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source=source,
        code=f"level_up_{hero_name}_{from_level}_to_{to_level}_{turn_id}",
        payload_json={"from_level": from_level, "to_level": to_level, "hero_name": hero_name},
    )


def make_inventory_change_event(
    *,
    hero_name: str,
    item_summary: str,
    turn_id: int,
    location_id: int | None,
    action: str = "pickup",
    source: str = "engine",
) -> ChronicleEvent:
    text = str(item_summary or "").strip()
    summary = f"{hero_name} {action}: {text}." if text else f"{hero_name} changed inventory."
    return ChronicleEvent(
        event_type="inventory_change",
        title="Inventory change",
        summary=summary[:500],
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source=source,
        code=f"inventory_{turn_id}_{_slug(action)}_{uuid.uuid4().hex[:6]}",
        payload_json={"action": action, "items": text},
    )


def make_property_acquired_event(
    *,
    holding_code: str,
    holding_name: str,
    hero_name: str,
    deed_summary: str,
    turn_id: int,
    location_id: int | None,
    is_first_holding: bool,
    source: str = "engine",
) -> ChronicleEvent:
    summary = f"{hero_name} acquired {holding_name}."
    return ChronicleEvent(
        event_type="property_acquired",
        title=f"Property: {holding_name}",
        summary=summary,
        turn_id=turn_id,
        location_id=location_id,
        actor_type="player",
        source=source,
        code=f"property_acquired_{holding_code}",
        pin_fact=None,
        payload_json={
            "property_code": holding_code,
            "property_name": holding_name,
            "deed_summary": deed_summary[:400],
        },
    )


def compose_turn_summary(
    *,
    turn_id: int,
    player_text: str,
    gm_excerpt: str,
    turn_resolution: dict[str, Any] | Any | None = None,
) -> str:
    """ADR §6.4 — GM-first turn summary for archivist chronicle row."""
    resolution: dict[str, Any] = {}
    if turn_resolution is not None:
        if hasattr(turn_resolution, "to_dict"):
            resolution = turn_resolution.to_dict()
        elif isinstance(turn_resolution, dict):
            resolution = turn_resolution

    quest = resolution.get("quest") if isinstance(resolution, dict) else None
    if isinstance(quest, dict):
        quest_summary = str(quest.get("summary") or "").strip()
        if quest_summary:
            return f"Turn {turn_id}: {quest_summary[:220]}"

    gm_bit = ""
    if gm_excerpt.strip():
        try:
            from titan.fugassa.gm_response_parser import extract_current_scene_narrative

            gm_bit = extract_current_scene_narrative(gm_excerpt).strip()[:180]
        except Exception:  # noqa: BLE001
            gm_bit = ""
        if not gm_bit:
            gm_bit = gm_excerpt.strip().replace("\n", " ")[:180]
    if gm_bit:
        return f"Turn {turn_id}: {gm_bit}"

    player_bit = str(player_text or "").strip()[:120]
    return f"Turn {turn_id}: {player_bit}"


def _resolution_dict(turn_resolution: dict[str, Any] | Any | None) -> dict[str, Any]:
    if turn_resolution is None:
        return {}
    if hasattr(turn_resolution, "to_dict"):
        return turn_resolution.to_dict()
    if isinstance(turn_resolution, dict):
        return turn_resolution
    return {}


def make_turn_event(
    *,
    turn_id: int,
    player_text: str,
    gm_excerpt: str,
    location_id: int | None,
    turn_resolution: dict[str, Any] | None = None,
    ingame_time: str | None = None,
    summary: str | None = None,
) -> ChronicleEvent:
    summary = summary or compose_turn_summary(
        turn_id=turn_id,
        player_text=player_text,
        gm_excerpt=gm_excerpt,
        turn_resolution=turn_resolution,
    )
    return ChronicleEvent(
        event_type="turn",
        title=f"Turn {turn_id}",
        summary=summary,
        turn_id=turn_id,
        location_id=location_id,
        ingame_occurred_at=ingame_time,
        source="archivist",
        code=f"turn_{turn_id}",
        payload_json={
            "gm_excerpt": gm_excerpt[:500],
            "resolution": _resolution_dict(turn_resolution),
        },
    )


def _slug(text: str) -> str:
    import re

    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    return base[:40] or "x"


def _insert_event_conn(conn: sqlite3.Connection, ev: ChronicleEvent) -> int | None:
    if ev.code:
        existing = conn.execute("SELECT id FROM event_log WHERE code = ?", (ev.code,)).fetchone()
        if existing:
            return int(existing[0] if isinstance(existing, tuple) else existing["id"])

    code = ev.code or f"{ev.event_type}_{ev.turn_id}_{uuid.uuid4().hex[:8]}"
    details = {"source": ev.source, "payload": ev.payload_json}
    try:
        conn.execute(
            """
            INSERT INTO event_log (
                code, event_type, title, summary, details_json,
                actor_type, actor_id, target_type, target_id,
                location_id, turn_id, ingame_occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                ev.event_type,
                ev.title[:200] if ev.title else None,
                ev.summary[:500],
                json.dumps(details, ensure_ascii=False),
                ev.actor_type,
                ev.actor_id,
                ev.target_type,
                ev.target_id,
                ev.location_id,
                ev.turn_id,
                ev.ingame_occurred_at,
                _utc_now(),
            ),
        )
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    except sqlite3.Error as exc:
        LOG.debug("chronicle insert skip (%s): %s", code, exc)
        return None


def record_events_conn(
    conn: sqlite3.Connection,
    db_path: str | None,
    events: list[ChronicleEvent],
    *,
    index_vectors: bool = False,
) -> list[int]:
    """Insert typed events and optional auto-pin. Caller commits before vec index."""
    ids: list[int] = []
    for ev in events:
        if not ev.summary.strip():
            continue
        event_id = _insert_event_conn(conn, ev)
        if not event_id:
            continue
        ids.append(event_id)
        if ev.pin_fact:
            campaign_facts.pin_fact_conn(conn, ev.pin_fact, source_event_id=event_id)
        if index_vectors and db_path:
            vec_index.index_text(db_path, "event_log", event_id, ev.index_text())
    return ids


def index_event_log_ids(db_path: str, event_ids: list[int]) -> dict[str, int]:
    """Embed existing event_log rows by id — safe after commit."""
    stats = {"rows": len(event_ids), "indexed": 0, "skipped": 0}
    if not db_path or not event_ids or not vec_index.is_available():
        stats["skipped"] = len(event_ids)
        return stats
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for event_id in event_ids:
            row = conn.execute(
                "SELECT id, title, summary, details_json FROM event_log WHERE id = ?",
                (int(event_id),),
            ).fetchone()
            if not row:
                stats["skipped"] += 1
                continue
            text = _event_index_text(row)
            if not text.strip():
                stats["skipped"] += 1
                continue
            if vec_index.index_text(db_path, "event_log", int(event_id), text):
                stats["indexed"] += 1
            else:
                stats["skipped"] += 1
    finally:
        conn.close()
    return stats


def record_events(db_path: str, events: list[ChronicleEvent]) -> list[int]:
    if not db_path or not events:
        return []
    conn = sqlite3.connect(db_path)
    try:
        ids = record_events_conn(conn, db_path, events, index_vectors=False)
        conn.commit()
    finally:
        conn.close()
    if ids:
        index_event_log_ids(db_path, ids)
    return ids


def record_from_resolution(
    db_path: str,
    events: list[ChronicleEvent],
    *,
    turn_resolution: dict[str, Any] | Any | None = None,
) -> list[int]:
    """ADR §5.5 — engine events (quest/combat/currency) after evaluate_quests."""
    _ = turn_resolution  # reserved for future resolution-scoped emits
    return record_events(db_path, events)


def purge_events_after_turn(db_path: str, turn_number: int) -> dict[str, int]:
    """ADR C9 — deactivate chronicle rows and derived per-turn data after undo."""
    stats = {"events_deactivated": 0, "deltas_removed": 0, "turn_history_removed": 0, "vec_removed": 0}
    if not db_path or turn_number < 0:
        return stats
    conn = sqlite3.connect(db_path)
    try:
        purge_ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM event_log WHERE turn_id > ? AND is_active = 1",
                (int(turn_number),),
            ).fetchall()
        ]
        cur = conn.execute(
            "UPDATE event_log SET is_active = 0 WHERE turn_id > ? AND is_active = 1",
            (int(turn_number),),
        )
        stats["events_deactivated"] = int(cur.rowcount or 0)
        cur = conn.execute("DELETE FROM scene_turn_deltas WHERE turn_number > ?", (int(turn_number),))
        stats["deltas_removed"] = int(cur.rowcount or 0)
        cur = conn.execute("DELETE FROM turn_history WHERE turn_number > ?", (int(turn_number),))
        stats["turn_history_removed"] = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    for event_id in purge_ids:
        vec_index.remove(db_path, "event_log", event_id)
        stats["vec_removed"] += 1
    return stats


def record_archivist_events(
    db_path: str,
    *,
    turn_id: int,
    player_text: str,
    gm_excerpt: str,
    location_id: int | None,
    turn_resolution: dict[str, Any] | Any | None = None,
    ingame_time: str | None = None,
) -> int | None:
    """ADR §5.5 — improved turn event after archivist narrative pass."""
    ev = make_turn_event(
        turn_id=turn_id,
        player_text=player_text,
        gm_excerpt=gm_excerpt,
        location_id=location_id,
        turn_resolution=turn_resolution,
        ingame_time=ingame_time,
    )
    ids = record_events(db_path, [ev])
    return ids[0] if ids else None


def record_turn_event(
    db_path: str,
    *,
    turn_id: int,
    player_text: str,
    gm_excerpt: str,
    location_id: int | None,
    turn_resolution: dict[str, Any] | None = None,
    ingame_time: str | None = None,
) -> int | None:
    """Legacy alias — prefer record_archivist_events in the turn pipeline."""
    return record_archivist_events(
        db_path,
        turn_id=turn_id,
        player_text=player_text,
        gm_excerpt=gm_excerpt,
        location_id=location_id,
        turn_resolution=turn_resolution,
        ingame_time=ingame_time,
    )


_pipeline_turn: list[dict[str, Any]] = []
_last_semantic_recall: dict[str, Any] | None = None


def clear_pipeline_turn() -> None:
    global _pipeline_turn
    _pipeline_turn = []


def record_pipeline_step(
    step: str,
    *,
    ok: bool = True,
    ms: float = 0,
    side_effects: list[str] | None = None,
) -> None:
    _pipeline_turn.append(
        {
            "step": step,
            "ok": bool(ok),
            "ms": round(float(ms), 2),
            "side_effects": list(side_effects or []),
        }
    )


def get_pipeline_turn() -> list[dict[str, Any]]:
    return list(_pipeline_turn)


def get_last_semantic_recall() -> dict[str, Any] | None:
    return dict(_last_semantic_recall) if _last_semantic_recall else None


def set_last_semantic_recall(payload: dict[str, Any] | None) -> None:
    global _last_semantic_recall
    _last_semantic_recall = dict(payload) if payload else None


def persist_pipeline_turn(db_path: str) -> None:
    if not db_path or not _pipeline_turn:
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO save_meta (key, value, updated_at)
            VALUES ('last_pipeline_turn', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (json.dumps(_pipeline_turn, ensure_ascii=False),),
        )
        conn.execute(
            """
            INSERT INTO save_meta (key, value, updated_at)
            VALUES ('last_semantic_recall', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (json.dumps(_last_semantic_recall or {}, ensure_ascii=False),),
        )
        conn.commit()
    finally:
        conn.close()


def load_pipeline_turn(db_path: str) -> list[dict[str, Any]]:
    if not db_path:
        return get_pipeline_turn()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT value FROM save_meta WHERE key = 'last_pipeline_turn'").fetchone()
        if not row or not row["value"]:
            return get_pipeline_turn()
        data = json.loads(row["value"])
        return data if isinstance(data, list) else get_pipeline_turn()
    except (TypeError, ValueError, sqlite3.Error):
        return get_pipeline_turn()
    finally:
        conn.close()


def load_semantic_recall_last(db_path: str) -> dict[str, Any] | None:
    recall = get_last_semantic_recall()
    if recall:
        return recall
    if not db_path:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT value FROM save_meta WHERE key = 'last_semantic_recall'").fetchone()
        if not row or not row["value"]:
            return None
        data = json.loads(row["value"])
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError, sqlite3.Error):
        return None
    finally:
        conn.close()


def record_property_acquired_conn(
    conn: sqlite3.Connection,
    db_path: str | None,
    holding: dict[str, Any],
    *,
    turn_id: int,
    location_id: int | None = None,
    source: str = "engine",
) -> int | None:
    hero = _hero_name_conn(conn)
    pc_row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    pc_id = int(pc_row["id"]) if pc_row else None
    count = _holding_count_conn(conn, pc_id)
    ev = make_property_acquired_event(
        holding_code=str(holding.get("code") or ""),
        holding_name=str(holding.get("name") or "Property"),
        hero_name=hero,
        deed_summary=str(holding.get("deed_summary") or ""),
        turn_id=turn_id,
        location_id=location_id,
        is_first_holding=count <= 1,
        source=source,
    )
    ids = record_events_conn(conn, db_path, [ev], index_vectors=False)
    return ids[0] if ids else None


def query_recent(db_path: str, *, limit: int = 10, exclude_turn_only: bool = False) -> list[dict[str, Any]]:
    if not db_path:
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if exclude_turn_only:
            rows = conn.execute(
                """
                SELECT id, code, event_type, title, summary, details_json, turn_id, location_id, created_at
                FROM event_log
                WHERE event_type != 'turn'
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, code, event_type, title, summary, details_json, turn_id, location_id, created_at
                FROM event_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            details: dict[str, Any] = {}
            try:
                details = json.loads(r["details_json"]) if r["details_json"] else {}
            except (TypeError, ValueError):
                details = {}
            out.append(
                {
                    "event_id": int(r["id"]),
                    "code": r["code"],
                    "event_type": r["event_type"],
                    "title": r["title"],
                    "summary": r["summary"],
                    "turn_id": r["turn_id"],
                    "location_id": r["location_id"],
                    "created_at": r["created_at"],
                    "source": details.get("source") or "unknown",
                    "details_json": details,
                }
            )
        return out
    finally:
        conn.close()


def query_by_turn_range(db_path: str, turn_min: int, turn_max: int) -> list[dict[str, Any]]:
    if not db_path:
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, event_type, title, summary, turn_id, details_json
            FROM event_log
            WHERE turn_id BETWEEN ? AND ? AND is_active = 1
            ORDER BY id ASC
            """,
            (int(turn_min), int(turn_max)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _dedupe_bullet_strings(bullets: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in bullets:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def rollup_bullets_for_turn_range(
    db_path: str | None,
    turn_start: int,
    turn_end: int,
    *,
    location_id: int | None = None,
) -> list[str]:
    """ADR C4 — scene exit rollup: typed engine events first, then turn rows."""
    if not db_path or not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if location_id is not None:
            rows = conn.execute(
                """
                SELECT event_type, summary FROM event_log
                WHERE turn_id BETWEEN ? AND ? AND is_active = 1
                  AND (location_id = ? OR location_id IS NULL)
                ORDER BY turn_id ASC, id ASC
                """,
                (int(turn_start), int(turn_end), int(location_id)),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT event_type, summary FROM event_log
                    WHERE turn_id BETWEEN ? AND ? AND is_active = 1
                    ORDER BY turn_id ASC, id ASC
                    """,
                    (int(turn_start), int(turn_end)),
                ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT event_type, summary FROM event_log
                WHERE turn_id BETWEEN ? AND ? AND is_active = 1
                ORDER BY turn_id ASC, id ASC
                """,
                (int(turn_start), int(turn_end)),
            ).fetchall()
        typed = [str(r["summary"] or "").strip() for r in rows if str(r["event_type"] or "") != "turn"]
        turn_rows = [str(r["summary"] or "").strip() for r in rows if str(r["event_type"] or "") == "turn"]
        return _dedupe_bullet_strings([b for b in typed if b] + [b for b in turn_rows if b])
    finally:
        conn.close()


def engine_summary_for_turn(
    db_path: str | None,
    turn_id: int,
    *,
    turn_resolution: dict[str, Any] | Any | None = None,
) -> str:
    """One-line engine fact for scene delta fallback (quest block or typed chronicle)."""
    resolution = _resolution_dict(turn_resolution)
    quest = resolution.get("quest") if isinstance(resolution, dict) else None
    if isinstance(quest, dict):
        quest_summary = str(quest.get("summary") or "").strip()
        if quest_summary:
            return quest_summary
    if not db_path or not os.path.isfile(db_path):
        return ""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT summary FROM event_log
            WHERE turn_id = ? AND is_active = 1 AND event_type != 'turn'
            ORDER BY id DESC LIMIT 1
            """,
            (int(turn_id),),
        ).fetchone()
        return str(row["summary"] or "").strip() if row else ""
    finally:
        conn.close()


ENGINE_APPENDIX_HEADER = (
    "ENGINE APPENDIX (authoritative — SQL/engine events; overrides narrative guesses):"
)


def build_engine_appendix(db_path: str | None, turn_min: int, turn_max: int) -> str:
    """ADR C4 — deterministic digest appendix from typed chronicle in turn batch."""
    events = query_by_turn_range(db_path, int(turn_min), int(turn_max)) if db_path else []
    lines: list[str] = []
    for ev in events:
        if str(ev.get("event_type") or "") == "turn":
            continue
        summary = str(ev.get("summary") or "").strip()
        if summary:
            lines.append(f"- {summary}")
    lines = _dedupe_bullet_strings(lines)
    if not lines:
        return ""
    return f"\n\n{ENGINE_APPENDIX_HEADER}\n" + "\n".join(lines)


def build_embedding_debug(db_path: str | None) -> dict[str, Any]:
    """Structured embedding status for debug API (AI-first)."""
    info: dict[str, Any] = {
        "available": False,
        "backend": None,
        "model": None,
        "dim": None,
        "sqlite_vec_loaded": vec_index.is_available(),
        "last_error": None,
    }
    try:
        from src.embeddings import get_embedding_client

        client = get_embedding_client()
        if client is None:
            info["last_error"] = "no embedding client"
            return info
        info["available"] = True
        url = getattr(client, "url", "") or ""
        info["backend"] = "fastembed" if "fastembed" in url.lower() or url.startswith("local://") else "http"
        info["model"] = getattr(client, "model", None) or getattr(client, "model_name", None)
        try:
            info["dim"] = int(client.get_sentence_embedding_dimension())
        except Exception as exc:  # noqa: BLE001
            info["last_error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        info["last_error"] = str(exc)
    if db_path:
        try:
            conn = sqlite3.connect(db_path)
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'vec_%'"
                ).fetchall()
            ]
            info["vec_tables"] = tables
            conn.close()
        except sqlite3.Error:
            info["vec_tables"] = []
    return info


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _event_index_text(row: sqlite3.Row) -> str:
    title = str(row["title"] or "").strip()
    summary = str(row["summary"] or "").strip()
    parts = [p for p in (title, summary) if p]
    try:
        details = json.loads(row["details_json"]) if row["details_json"] else {}
        if isinstance(details, dict):
            excerpt = str(details.get("gm_excerpt") or "").strip()
            if excerpt:
                parts.append(excerpt[:300])
            payload = details.get("payload")
            if isinstance(payload, dict):
                excerpt = str(payload.get("gm_excerpt") or "").strip()
                if excerpt:
                    parts.append(excerpt[:300])
    except (TypeError, ValueError):
        pass
    return ". ".join(parts)[:500]


def _max_turn_conn(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(turn_number) AS m FROM turn_history").fetchone()
    if row and row["m"] is not None:
        return int(row["m"])
    row = conn.execute("SELECT MAX(turn_id) AS m FROM event_log").fetchone()
    return int(row["m"] or 0) if row and row["m"] is not None else 0


def _hero_location_conn(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    if row and row["current_location_id"]:
        return int(row["current_location_id"])
    return None


def _quest_row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except (AttributeError, IndexError, TypeError):
        pass
    return default


def _infer_quest_complete_turn(conn: sqlite3.Connection, q: sqlite3.Row) -> int:
    title = str(q["title"] or "")
    code = str(q["code"] or "")
    for row in conn.execute(
        "SELECT turn_number, resolution_json FROM turn_history ORDER BY turn_number DESC"
    ).fetchall():
        try:
            res = json.loads(row["resolution_json"] or "{}")
        except (TypeError, ValueError):
            continue
        quest_block = res.get("quest") if isinstance(res, dict) else None
        if not isinstance(quest_block, dict):
            continue
        completed = quest_block.get("quests_completed") or []
        if title in completed or any(code in str(x) for x in completed):
            return int(row["turn_number"])
        rewards = quest_block.get("rewards_granted") or {}
        if isinstance(rewards, dict) and code in rewards:
            return int(row["turn_number"])

    chain_code = _quest_row_value(q, "chain_code")
    chain_pos = _quest_row_value(q, "chain_position")
    max_turn = _max_turn_conn(conn)
    if chain_code and chain_pos is not None and max_turn:
        try:
            pos = int(chain_pos)
            chain_len_row = conn.execute(
                "SELECT MAX(chain_position) AS m FROM quests WHERE chain_code = ?",
                (str(chain_code),),
            ).fetchone()
            chain_len = int(chain_len_row["m"] or pos) if chain_len_row else pos
            if chain_len > 1:
                return max(1, max_turn - (chain_len - pos))
        except (TypeError, ValueError):
            pass

    try:
        rewards = json.loads(q["rewards_json"] or "{}")
    except (TypeError, ValueError):
        rewards = {}
    renown = rewards.get("renown") if isinstance(rewards.get("renown"), dict) else {}
    renown_code = str(renown.get("renown_code") or "").strip()
    if renown_code:
        row = conn.execute(
            "SELECT granted_at_turn FROM player_renown WHERE renown_code = ? LIMIT 1",
            (renown_code,),
        ).fetchone()
        if row and row["granted_at_turn"] is not None:
            return int(row["granted_at_turn"])

    if max_turn:
        return max_turn
    return 1


def _collect_backfill_events(conn: sqlite3.Connection, state: dict[str, Any]) -> list[ChronicleEvent]:
    """Synthesize typed chronicle rows from SQL kanon + game.json party."""
    hero = _hero_name_conn(conn)
    loc_id = _hero_location_conn(conn)
    events: list[ChronicleEvent] = []

    quest_cols = {r[1] for r in conn.execute("PRAGMA table_info(quests)").fetchall()}
    for q in conn.execute("SELECT * FROM quests WHERE status = 'completed'").fetchall():
        turn_id = _infer_quest_complete_turn(conn, q)
        scale = _quest_row_value(q, "quest_scale") if "quest_scale" in quest_cols else None
        chain_code = _quest_row_value(q, "chain_code") if "chain_code" in quest_cols else None
        chain_pos = _quest_row_value(q, "chain_position") if "chain_position" in quest_cols else None
        ev = make_quest_complete_event(
            quest_code=str(q["code"]),
            quest_title=str(q["title"]),
            hero_name=hero,
            turn_id=turn_id,
            location_id=loc_id,
            scale=str(scale) if scale else None,
            chain_code=str(chain_code) if chain_code else None,
            chain_position=int(chain_pos) if chain_pos is not None else None,
            rewards_granted=[],
        )
        ev.source = "backfill"
        events.append(ev)

    for q in conn.execute("SELECT * FROM quests WHERE status = 'failed'").fetchall():
        turn_id = _max_turn_conn(conn) or int(state.get("turn") or 1)
        ev = make_quest_failed_event(
            quest_code=str(q["code"]),
            quest_title=str(q["title"]),
            hero_name=hero,
            reason=str(q["fail_reason"] or "unknown"),
            turn_id=turn_id,
            location_id=loc_id,
        )
        ev.source = "backfill"
        events.append(ev)

    for h in conn.execute("SELECT * FROM property_holdings ORDER BY id ASC").fetchall():
        holding = dict(h)
        turn_id = int(holding.get("acquired_at_turn") or 0) or 1
        ev = make_property_acquired_event(
            holding_code=str(holding.get("code") or ""),
            holding_name=str(holding.get("name") or "Property"),
            hero_name=hero,
            deed_summary=str(holding.get("deed_summary") or ""),
            turn_id=turn_id,
            location_id=loc_id,
            is_first_holding=False,
            source="backfill",
        )
        events.append(ev)

    for row in conn.execute(
        """
        SELECT renown_code, title_display, impact_tier, granted_at_turn
        FROM player_renown
        ORDER BY id ASC
        """
    ).fetchall():
        turn_id = int(row["granted_at_turn"] or 0) or 1
        ev = make_title_granted_event(
            renown_code=str(row["renown_code"]),
            title_display=str(row["title_display"] or row["renown_code"]),
            impact_tier=int(row["impact_tier"] or 2),
            hero_name=hero,
            turn_id=turn_id,
            location_id=loc_id,
        )
        ev.source = "backfill"
        events.append(ev)

    companion_turn = _max_turn_conn(conn) or int(state.get("turn") or 1)
    seen_companions: set[str] = set()
    for member in state.get("party") or []:
        if not isinstance(member, dict):
            continue
        role = str(member.get("role") or "").strip().lower()
        if role in ("player", "hero", ""):
            continue
        npc_code = str(member.get("npc_code") or member.get("code") or "").strip()
        npc_name = str(member.get("name") or npc_code).strip()
        if not npc_code or npc_code in seen_companions:
            continue
        seen_companions.add(npc_code)
        ev = make_companion_join_event(
            npc_code=npc_code,
            npc_name=npc_name,
            hero_name=hero,
            turn_id=companion_turn,
            location_id=loc_id,
        )
        ev.source = "backfill"
        events.append(ev)

    return events


def reindex_event_log_vectors(db_path: str, conn: sqlite3.Connection, *, dry_run: bool = False) -> dict[str, int]:
    stats = {"rows": 0, "indexed": 0, "skipped": 0}
    if not vec_index.is_available():
        stats["skipped"] = -1
        return stats
    rows = conn.execute(
        "SELECT id, title, summary, details_json FROM event_log ORDER BY id ASC"
    ).fetchall()
    stats["rows"] = len(rows)
    for row in rows:
        text = _event_index_text(row)
        if not text.strip():
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["indexed"] += 1
            continue
        if vec_index.index_text(db_path, "event_log", int(row["id"]), text):
            stats["indexed"] += 1
        else:
            stats["skipped"] += 1
    return stats


def reindex_npc_memory_vectors(db_path: str, conn: sqlite3.Connection, *, dry_run: bool = False) -> dict[str, int]:
    stats = {"rows": 0, "indexed": 0, "skipped": 0}
    if not vec_index.is_available():
        stats["skipped"] = -1
        return stats
    try:
        rows = conn.execute("SELECT id, memory_text FROM npc_memories ORDER BY id ASC").fetchall()
    except sqlite3.OperationalError:
        return stats
    stats["rows"] = len(rows)
    for row in rows:
        text = str(row["memory_text"] or "").strip()
        if not text:
            stats["skipped"] += 1
            continue
        if dry_run:
            stats["indexed"] += 1
            continue
        if vec_index.index_text(db_path, "npc_memory", int(row["id"]), text[:500]):
            stats["indexed"] += 1
        else:
            stats["skipped"] += 1
    return stats


def repair_chronicle(
    db_path: str,
    state: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
    reindex_only: bool = False,
    recondense: bool = False,
) -> dict[str, Any]:
    """Backfill typed chronicle events from SQL kanon and rebuild vec indexes."""
    import os

    summary: dict[str, Any] = {
        "ok": False,
        "dry_run": dry_run,
        "reindex_only": reindex_only,
        "events_before": {},
        "events_after": {},
        "synthesized": [],
        "event_ids_written": [],
        "reindex_event_log": {},
        "reindex_npc_memory": {},
        "recondense": None,
    }
    if not db_path or not os.path.isfile(db_path):
        summary["error"] = "no_db"
        return summary

    state = state if isinstance(state, dict) else {}
    conn = _connect(db_path)
    try:
        before = conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM event_log GROUP BY event_type ORDER BY event_type"
        ).fetchall()
        summary["events_before"] = {str(r["event_type"]): int(r["c"]) for r in before}

        backfill_events: list[ChronicleEvent] = []
        if not reindex_only:
            backfill_events = _collect_backfill_events(conn, state)
            summary["synthesized"] = [
                {"code": ev.code, "event_type": ev.event_type, "turn_id": ev.turn_id, "source": ev.source}
                for ev in backfill_events
            ]

        if dry_run:
            summary["reindex_event_log"] = reindex_event_log_vectors(db_path, conn, dry_run=True)
            summary["reindex_npc_memory"] = reindex_npc_memory_vectors(db_path, conn, dry_run=True)
            summary["events_after"] = dict(summary["events_before"])
            summary["ok"] = True
            if not reindex_only:
                from titan.fugassa import scene_summary_engine

                summary["repair_scene_turn_deltas"] = scene_summary_engine.repair_scene_turn_deltas(
                    db_path,
                    dry_run=True,
                )
            return summary

        if backfill_events:
            ids = record_events_conn(conn, db_path, backfill_events, index_vectors=False)
            summary["event_ids_written"] = ids
            conn.execute(
                """
                INSERT INTO save_meta (key, value, updated_at)
                VALUES ('chronicle_repair_v1', ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (_utc_now(),),
            )
            conn.commit()

        after = conn.execute(
            "SELECT event_type, COUNT(*) AS c FROM event_log GROUP BY event_type ORDER BY event_type"
        ).fetchall()
        summary["events_after"] = {str(r["event_type"]): int(r["c"]) for r in after}
        summary["ok"] = True
    finally:
        conn.close()

    if not dry_run:
        reconn = _connect(db_path)
        try:
            summary["reindex_event_log"] = reindex_event_log_vectors(db_path, reconn, dry_run=False)
            summary["reindex_npc_memory"] = reindex_npc_memory_vectors(db_path, reconn, dry_run=False)
            if reindex_only or not backfill_events:
                reconn.execute(
                    """
                    INSERT INTO save_meta (key, value, updated_at)
                    VALUES ('chronicle_repair_v1', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (_utc_now(),),
                )
                reconn.commit()
        finally:
            reconn.close()

    if recondense and not dry_run:
        summary["recondense"] = {
            "requested": True,
            "note": "Digest recondense not run automatically — use campaign_digest.maybe_condense when needed.",
        }

    from titan.fugassa import scene_summary_engine

    if not reindex_only:
        summary["repair_scene_turn_deltas"] = scene_summary_engine.repair_scene_turn_deltas(
            db_path,
            dry_run=dry_run,
        )

    return summary

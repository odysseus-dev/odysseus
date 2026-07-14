"""Post-GM archivist — ADR §D: turn_history, event_log, memories, LLM patch."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import campaign_chronicle, campaign_facts, gm_response_parser, memory_graph, npc_generator
from titan.fugassa.db import vec_index
from titan.fugassa.turn_resolution import TurnResolution

LOG = logging.getLogger("titan.fugassa.archivist")

# ADR §D scope table — fields the archivist may NEVER touch (engine/turn_resolution owns them)
_ENGINE_ONLY_ENTITIES = {"combat", "inventory", "quest_status", "position", "hp"}
_ALLOWED_OPS = {
    ("add", "npc_memory"),
    ("add", "campaign_fact"),
    ("update", "location"),
    ("create", "npc"),
    ("create", "item"),
    ("create", "quest"),
    ("create", "property"),
    ("update", "property"),
    ("create", "property_room"),
    ("create", "property_fixture"),
    ("assign", "property_staff"),
    ("noop", "quest"),
    ("noop", "npc"),
    ("noop", "location"),
    ("noop", "item"),
    ("noop", "property"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str, fallback: str = "x") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:40] or fallback)


async def run_archivist(
    db_path: str,
    *,
    turn_number: int,
    player_text: str,
    gm_prose: str,
    turn_resolution: TurnResolution,
    state: dict[str, Any],
    ingame_time: str | None = None,
    owner: str | None = None,
    llm_enabled: bool = True,
    scene_cast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist turn + extract lightweight memories from GM prose."""
    base = apply_archivist(
        db_path,
        turn_number=turn_number,
        player_text=player_text,
        gm_prose=gm_prose,
        turn_resolution=turn_resolution,
        ingame_time=ingame_time,
    )
    if not base.get("applied"):
        return base

    memories = _extract_scene_memories(state, gm_prose, turn_number)
    if memories:
        _persist_memories(db_path, memories, state)
        base["memories_written"] = len(memories)

    event_id = campaign_chronicle.record_archivist_events(
        db_path,
        turn_id=turn_number,
        player_text=player_text,
        gm_excerpt=gm_prose[:500],
        location_id=_event_location_id(state),
        turn_resolution=turn_resolution,
        ingame_time=ingame_time,
    )
    if event_id:
        base["chronicle_turn_event_id"] = event_id

    loc_id = _event_location_id(state)
    if loc_id and base.get("applied"):
        from titan.fugassa import scene_summary_engine

        scene_summary_engine.record_turn_delta(
            db_path,
            location_id=int(loc_id),
            turn_number=int(turn_number),
            player_text=player_text,
            gm_prose=gm_prose,
            turn_resolution=turn_resolution,
        )

    if llm_enabled and turn_resolution.requires_archivist() and gm_prose.strip():
        try:
            patch_result = await run_llm_patch(
                db_path,
                gm_prose=gm_prose,
                turn_resolution=turn_resolution,
                state=state,
                owner=owner,
                scene_cast=scene_cast,
            )
            base["patch"] = patch_result
        except Exception as exc:  # noqa: BLE001 — archivist patch must never break the turn
            LOG.warning("LLM archivist patch failed: %s", exc)
            base["patch"] = {"applied_ops": 0, "error": str(exc)}

    sync_location_description_to_state(db_path, state)
    return base


async def run_llm_patch(
    db_path: str,
    *,
    gm_prose: str,
    turn_resolution: TurnResolution,
    state: dict[str, Any],
    owner: str | None,
    scene_cast: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Samostatný LLM call po GM (ADR #2, variant A) — produces + validates a structured patch."""
    from titan.fugassa.llm_client import chat_completion

    messages = build_patch_messages(gm_prose, turn_resolution, state, scene_cast=scene_cast)
    raw = await chat_completion(messages, owner=owner, max_tokens=900, temperature=0.2)
    ops = parse_patch_ops(raw)
    valid_ops = validate_ops(db_path, ops, turn_resolution)
    op_result = apply_ops(db_path, valid_ops, state)
    applied = int(op_result.get("applied") or 0)
    from titan.fugassa.db import state_repository

    promoted = state_repository.promote_narrative_npcs_to_scene(
        db_path,
        state,
        gm_prose=gm_prose,
        npc_names=list(op_result.get("created_npc_names") or []),
    )
    sync_location_description_to_state(db_path, state)
    return {
        "proposed_ops": len(ops),
        "applied_ops": applied,
        "rejected_ops": len(ops) - len(valid_ops),
        "promoted_scene_npcs": promoted,
    }


def build_patch_messages(
    gm_prose: str,
    turn_resolution: TurnResolution,
    state: dict[str, Any],
    *,
    scene_cast: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    from titan.fugassa.scene_character_context import format_scene_cast_for_llm

    loc = state.get("location_state") or {}
    scene_npcs = ", ".join(str(n) for n in (loc.get("npcs") or [])) or "none"
    cast_line = format_scene_cast_for_llm(scene_cast)
    system = (
        "You are the Archivist for a text RPG. You NEVER change combat, HP, inventory quantities, "
        "position, or quest completion state — those are engine-owned. You ONLY extract narrative "
        "facts from the GM's prose (NPC memories, new items/NPCs mentioned, location description "
        "additions, new narrative quest hooks) into a structured JSON patch.\n\n"
        "Return ONLY a JSON object: {\"ops\": [ {...}, ... ]}. Allowed op shapes:\n"
        '{"op": "add", "entity": "npc_memory", "npc_name": "<name>", "text": "<short episodic memory>", "importance": 1-7}\n'
        '{"op": "update", "entity": "location", "description_append": "<short fact to append>"}\n'
        '{"op": "create", "entity": "npc", "name": "<name>", "race": "<race or null>", "role": "<role or null>", '
        '"is_hostile": true|false, "backstory": "<1 sentence or null>"}\n'
        "Only use create/npc for a character the GM actually introduced in the scene prose this turn "
        "(not background mentions or hypotheticals). Engine adds them to the visible scene cast when named in prose.\n"
        '{"op": "create", "entity": "item", "name": "<name>", "description": "<short>"}\n'
        '{"op": "create", "entity": "quest", "title": "<title>", "description": "<1-2 sentences>", '
        '"scale": "minor|standard|major|arc", '
        '"giver_npc_name": "<NPC name at scene or null>", '
        '"objectives": [{"text": "<player-facing step>", "type": "custom|visit_location|talk_npc|obtain_item"}], '
        '"rewards": {"gold": <int>, "xp": <int>, "items": [{"name": "<item>", "qty": 1}], '
        '"renown": {"renown_code": "<slug>", "title_display": "<honorific>", "impact_tier": 2-4, "scope_type": "region"}}, '
        '"rewards_deferred": true|false, "chain_code": "<arc slug or null>", "chain_position": <int or null>}\n'
        "QUEST RULES: minor/standard quests MUST include concrete rewards (gold/xp/items). "
        "major/arc quests MUST set rewards_deferred=true OR chain_code (multi-part storyline) — never vague open-ended hooks. "
        "Include at least one objective players can track. Do not create duplicate quests.\n"
        '{"op": "create", "entity": "property", "name": "<holding display name>", '
        '"property_kind": "townhouse|cottage|estate|apartment", "root_location_name": "<interior root name>", '
        '"acquired_via": "inheritance|purchase|gift|narrative", '
        '"deed_summary": "<1-2 sentences of canonical ownership fact>", '
        '"specs": {"prestige": 1-3, "bedrooms": 1-4}}\n'
        "PROPERTY RULES: use create/property only when GM clearly grants player ownership of a place. "
        "Visiting someone else's home is NOT a property create — use update/location or npc_memory. "
        '{"op": "update", "entity": "property", "property_code": "<code or omit>", "property_name": "<name>", '
        '"deed_append": "<new canonical fact>", "specs": {"has_study": true}}\n'
        '{"op": "create", "entity": "property_room", "property_name": "<holding name>", '
        '"room_name": "<room label>", "description": "<short room fact>"}\n'
        '{"op": "create", "entity": "property_fixture", "property_name": "<holding name>", '
        '"room_name": "<room label or omit>", "name": "<fixture name>", '
        '"fixture_kind": "furniture|storage|crafting_station", "description": "<short>"}\n'
        '{"op": "assign", "entity": "property_staff", "property_name": "<holding name>", '
        '"npc_name": "<NPC name>", "role": "steward|maid|concubine|guard|staff"}\n'
        '{"op": "add", "entity": "campaign_fact", "text": "<durable, campaign-defining fact>", '
        '"known_by": "<npc name, faction, \\"everyone\\", or null>"}\n'
        '{"op": "noop", "entity": "<entity>", "reason": "<why nothing to do>"}\n\n'
        "Use campaign_fact sparingly — only for facts that permanently redefine the world or "
        "story (a kingdom fell, a pact was sealed), never for routine scene detail (that's "
        "npc_memory).\n"
        "If nothing narratively new happened, return {\"ops\": [{\"op\": \"noop\", \"entity\": \"scene\", \"reason\": \"no change\"}]}. "
        "Never output prose outside the JSON object."
    )
    user = (
        f"Scene NPCs present: {scene_npcs}\n"
        + (f"{cast_line}\n" if cast_line else "")
        + f"Turn resolution (engine facts — do not restate as new): {turn_resolution.to_json()}\n\n"
        f"GM prose this turn:\n{gm_prose.strip()[:2000]}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_patch_ops(raw: str) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    ops = data.get("ops") if isinstance(data, dict) else None
    if not isinstance(ops, list):
        return []
    return [op for op in ops if isinstance(op, dict) and op.get("op") and op.get("entity")]


def validate_ops(
    db_path: str,
    ops: list[dict[str, Any]],
    turn_resolution: TurnResolution,
) -> list[dict[str, Any]]:
    """ADR §D validator: reject unknown shapes, engine-owned fields, and DB conflicts (DB wins)."""
    out: list[dict[str, Any]] = []
    for op in ops:
        kind = str(op.get("op") or "")
        entity = str(op.get("entity") or "")
        if (kind, entity) not in _ALLOWED_OPS:
            continue
        if kind == "update" and entity == "location" and not str(op.get("description_append") or "").strip():
            continue
        if kind == "add" and entity == "npc_memory" and not str(op.get("text") or "").strip():
            continue
        if kind == "add" and entity == "campaign_fact" and not str(op.get("text") or "").strip():
            continue
        if kind == "create" and entity == "npc" and not str(op.get("name") or "").strip():
            continue
        if kind == "create" and entity == "item" and not str(op.get("name") or "").strip():
            continue
        if kind == "create" and entity == "quest" and not str(op.get("title") or "").strip():
            continue
        if kind == "create" and entity == "property":
            from titan.fugassa import property_validator

            if property_validator.validate_archivist_property_op(op):
                continue
        if kind == "update" and entity == "property":
            from titan.fugassa import property_validator

            if property_validator.validate_archivist_property_update_op(op):
                continue
        if kind == "create" and entity == "property_room":
            from titan.fugassa import property_validator

            if property_validator.validate_archivist_property_room_op(op):
                continue
        if kind == "create" and entity == "property_fixture":
            from titan.fugassa import property_validator

            if property_validator.validate_archivist_property_fixture_op(op):
                continue
        if kind == "assign" and entity == "property_staff":
            from titan.fugassa import property_validator

            if property_validator.validate_archivist_property_staff_op(op):
                continue
        out.append(op)
    return out


def _scene_link_context(
    conn: sqlite3.Connection, loc_id: int | None, subject_npc_id: int | None
) -> tuple[list[int], list[int]]:
    """ADR §4b link set for a freshly-written memory: active quests tied to
    this location/NPC, and other NPCs sharing the scene."""
    if not loc_id:
        return [], []
    quest_rows = conn.execute(
        "SELECT id FROM quests WHERE status = 'active' AND (related_location_id = ? OR giver_npc_id = ?)",
        (loc_id, subject_npc_id),
    ).fetchall()
    npc_rows = conn.execute(
        "SELECT id FROM npcs WHERE current_location_id = ? AND id != ?",
        (loc_id, subject_npc_id or -1),
    ).fetchall()
    return [int(r["id"]) for r in quest_rows], [int(r["id"]) for r in npc_rows]


def apply_ops(db_path: str, ops: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    import os

    if not db_path or not os.path.isfile(db_path) or not ops:
        return {"applied": 0, "created_npc_names": []}
    applied = 0
    created_npc_names: list[str] = []
    pending_memory_index: list[tuple[int, str]] = []
    pending_property_chronicle: list[dict[str, Any]] = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        loc_row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None

        for op in ops:
            kind, entity = op.get("op"), op.get("entity")
            try:
                if kind == "add" and entity == "npc_memory":
                    row = conn.execute("SELECT id FROM npcs WHERE name = ?", (str(op.get("npc_name") or ""),)).fetchone()
                    if not row:
                        continue
                    importance = max(1, min(7, int(op.get("importance", 4) or 4)))
                    text = str(op.get("text") or "")[:500]
                    conn.execute(
                        """
                        INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, created_at)
                        VALUES (?, 'episodic', ?, ?, ?)
                        """,
                        (row["id"], text, importance, _utc_now()),
                    )
                    mem_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                    pending_memory_index.append((mem_id, text))
                    quest_ids, other_npc_ids = _scene_link_context(conn, loc_id, int(row["id"]))
                    memory_graph.auto_link_memory_conn(
                        conn, mem_id, npc_id=int(row["id"]), location_id=loc_id,
                        quest_ids=quest_ids, other_npc_ids=other_npc_ids,
                    )
                    applied += 1
                elif kind == "add" and entity == "campaign_fact":
                    text = str(op.get("text") or "").strip()
                    if text:
                        campaign_facts.pin_fact_conn(conn, text, known_by=op.get("known_by") or None)
                        applied += 1
                elif kind == "update" and entity == "location" and loc_id:
                    row = conn.execute("SELECT description_short FROM locations WHERE id = ?", (loc_id,)).fetchone()
                    appended = str(op.get("description_append") or "").strip()[:300]
                    new_desc = f"{(row['description_short'] or '').strip()} {appended}".strip() if row else appended
                    conn.execute(
                        "UPDATE locations SET description_short = ?, updated_at = ? WHERE id = ?",
                        (new_desc, _utc_now(), loc_id),
                    )
                    applied += 1
                elif kind == "create" and entity == "npc":
                    name = str(op.get("name") or "").strip()
                    if not name:
                        continue
                    from titan.fugassa import campaign_name_registry

                    spawn_name = campaign_name_registry.prepare_npc_name(
                        db_path, name, role=str(op.get("role") or "").strip() or None
                    )
                    result = npc_generator.spawn_npc(
                        conn,
                        name=spawn_name,
                        tier="T2",
                        location_id=loc_id,
                        race=op.get("race") or None,
                        class_role=op.get("role") or None,
                        is_hostile=bool(op.get("is_hostile")),
                        backstory_summary=op.get("backstory") or None,
                    )
                    if result.get("npc_id"):
                        campaign_name_registry.register_spawned_npc(
                            db_path, npc_id=int(result["npc_id"]), name=spawn_name
                        )
                        created_npc_names.append(spawn_name)
                        applied += 1
                elif kind == "create" and entity == "item":
                    code = _slug(str(op.get("name")), "item")
                    existing = conn.execute("SELECT id FROM items WHERE code = ?", (code,)).fetchone()
                    if not existing:
                        conn.execute(
                            """
                            INSERT INTO items (code, name, item_type, description, quantity, owner_type, owner_id, created_at, updated_at)
                            VALUES (?, ?, 'misc', ?, 1, 'location', ?, ?, ?)
                            """,
                            (code, str(op.get("name")), str(op.get("description") or ""), loc_id, _utc_now(), _utc_now()),
                        )
                        applied += 1
                elif kind == "create" and entity == "quest":
                    from titan.fugassa import quest_templates

                    if quest_templates.validate_archivist_quest_op(op):
                        continue
                    if quest_templates.create_quest_from_archivist_op(
                        db_path, op, state=state, loc_id=loc_id,
                    ):
                        applied += 1
                elif kind == "create" and entity == "property":
                    from titan.fugassa import property_repository

                    pc_row = conn.execute(
                        "SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
                    ).fetchone()
                    if not pc_row:
                        continue
                    proposal = property_repository.proposal_from_archivist_op(op)
                    turn = int(state.get("turn") or 0)
                    created = property_repository.create_holding_conn(
                        conn,
                        player_character_id=int(pc_row["id"]),
                        proposal=proposal,
                        acquired_at_turn=turn,
                    )
                    if created:
                        applied += 1
                        pending_property_chronicle.append(created)
                elif kind == "update" and entity == "property":
                    from titan.fugassa import property_repository

                    if property_repository.update_holding_from_op_conn(conn, op):
                        applied += 1
                elif kind == "create" and entity == "property_room":
                    from titan.fugassa import property_repository

                    if property_repository.create_property_room_conn(
                        conn,
                        property_code=str(op.get("property_code") or ""),
                        property_name=str(op.get("property_name") or ""),
                        room_name=str(op.get("room_name") or op.get("name") or ""),
                        description=str(op.get("description") or ""),
                    ):
                        applied += 1
                elif kind == "create" and entity == "property_fixture":
                    from titan.fugassa import property_repository

                    turn = int(state.get("turn") or 0)
                    payload = property_repository.proposal_fixture_from_archivist_op(op)
                    if property_repository.create_fixture_conn(
                        conn,
                        property_code=payload["property_code"],
                        property_name=payload["property_name"],
                        room_name=payload["room_name"],
                        name=payload["name"],
                        fixture_kind=payload["fixture_kind"],
                        description=payload["description"],
                        condition_pct=payload["condition_pct"],
                        specs=payload["specs"],
                        installed_at_turn=turn,
                    ):
                        applied += 1
                elif kind == "assign" and entity == "property_staff":
                    from titan.fugassa import property_repository

                    if property_repository.assign_staff_conn(
                        conn,
                        property_code=str(op.get("property_code") or ""),
                        property_name=str(op.get("property_name") or ""),
                        npc_name=str(op.get("npc_name") or ""),
                        npc_code=str(op.get("npc_code") or ""),
                        role=str(op.get("role") or "staff"),
                    ):
                        applied += 1
                # noop: intentionally skipped
            except sqlite3.Error as exc:
                LOG.warning("archivist op failed (%s/%s): %s", kind, entity, exc)
        from titan.fugassa.property_repository import attach_property_context_to_location, sync_property_portfolio

        sync_property_portfolio(conn, state)
        loc_row = conn.execute(
            "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        if loc_row and loc_row["current_location_id"]:
            attach_property_context_to_location(conn, state, location_id=int(loc_row["current_location_id"]))
        turn = int(state.get("turn") or 0)
        property_event_ids: list[int] = []
        for holding in pending_property_chronicle:
            event_id = campaign_chronicle.record_property_acquired_conn(
                conn,
                db_path,
                holding,
                turn_id=turn,
                location_id=loc_id,
                source="archivist",
            )
            if event_id:
                property_event_ids.append(event_id)
        conn.commit()
    finally:
        conn.close()
    if property_event_ids:
        campaign_chronicle.index_event_log_ids(db_path, property_event_ids)
    for memory_id, text in pending_memory_index:
        vec_index.index_text(db_path, "npc_memory", memory_id, text)
    return {"applied": applied, "created_npc_names": created_npc_names}


def sync_location_description_to_state(db_path: str, state: dict[str, Any]) -> None:
    """Push SQL location description into runtime state for HUD panels."""
    loc = dict(state.get("location_state") or {})
    loc_id = loc.get("location_id") or state.get("_current_location_id")
    if not db_path or not loc_id:
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT name, description_short, description_long, image_path FROM locations WHERE id = ?",
            (int(loc_id),),
        ).fetchone()
        if not row:
            return
        if row["name"]:
            loc["name"] = row["name"]
        desc = str(row["description_long"] or row["description_short"] or "").strip()
        if desc:
            loc["description"] = desc
        if row["image_path"]:
            loc["scene_asset"] = row["image_path"]
        state["location_state"] = loc
    finally:
        conn.close()


def apply_archivist(
    db_path: str,
    *,
    turn_number: int,
    player_text: str,
    gm_prose: str,
    turn_resolution: TurnResolution,
    ingame_time: str | None = None,
) -> dict[str, Any]:
    if not db_path or not __import__("os").path.isfile(db_path):
        return {"applied": False, "reason": "no_db"}

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO turn_history (
                turn_number, player_text, ai_text, resolution_json,
                ingame_time, is_active, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (turn_number, player_text, gm_prose, turn_resolution.to_json(), ingame_time, _utc_now()),
        )
        conn.commit()
        return {"applied": True, "turn_number": turn_number}
    except sqlite3.Error as exc:
        LOG.warning("archivist persist failed: %s", exc)
        return {"applied": False, "error": str(exc)}
    finally:
        conn.close()


def _extract_scene_memories(state: dict[str, Any], gm_prose: str, turn_number: int) -> list[dict[str, Any]]:
    """Heuristic memory extraction — NPC names mentioned in scene get episodic memory."""
    loc = state.get("location_state") or {}
    npc_names = [str(n) for n in (loc.get("npcs") or []) if str(n).strip()]
    prose_lower = gm_prose.lower()
    out: list[dict[str, Any]] = []
    for name in npc_names:
        if name.lower() in prose_lower:
            snippet = gm_prose[:400].strip()
            out.append({"npc_name": name, "memory_text": f"Scene turn {turn_number}: {snippet}", "turn_id": turn_number})
    return out[:5]


def _persist_memories(db_path: str, memories: list[dict[str, Any]], state: dict[str, Any]) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    written: list[tuple[int, str]] = []
    loc_id_raw = (state.get("location_state") or {}).get("location_id")
    loc_id = int(loc_id_raw) if loc_id_raw else None
    try:
        for mem in memories:
            name = str(mem.get("npc_name") or "")
            row = conn.execute("SELECT id FROM npcs WHERE name = ? LIMIT 1", (name,)).fetchone()
            if not row:
                continue
            text = str(mem.get("memory_text") or "")
            conn.execute(
                """
                INSERT INTO npc_memories (npc_id, memory_type, memory_text, importance, turn_id, created_at)
                VALUES (?, 'episodic', ?, 4, ?, ?)
                """,
                (row["id"], text, mem.get("turn_id"), _utc_now()),
            )
            mem_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            written.append((mem_id, text))
            quest_ids, other_npc_ids = _scene_link_context(conn, loc_id, int(row["id"]))
            memory_graph.auto_link_memory_conn(
                conn, mem_id, npc_id=int(row["id"]), location_id=loc_id,
                quest_ids=quest_ids, other_npc_ids=other_npc_ids,
            )
        conn.commit()
    finally:
        conn.close()
    for memory_id, text in written:
        vec_index.index_text(db_path, "npc_memory", memory_id, text)


def _event_location_id(state: dict[str, Any]) -> int | None:
    loc = state.get("_current_location_id")
    if loc is not None:
        try:
            return int(loc)
        except (TypeError, ValueError):
            pass
    loc_state = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    try:
        lid = int(loc_state.get("location_id") or 0)
        return lid if lid > 0 else None
    except (TypeError, ValueError):
        return None


def extract_ingame_time_label(state: dict[str, Any], ts: dict[str, Any]) -> str | None:
    from titan.fugassa import world_time_engine

    if ts.get("display"):
        return str(ts["display"])
    loc = str(ts.get("location") or (state.get("location_state") or {}).get("name") or "").strip() or None
    header = world_time_engine.format_chat_header(state.get("world_time") or {}, loc)
    return header or None


def parse_gm_response(raw: str) -> dict[str, Any]:
    parsed = gm_response_parser.parse(raw)
    return {
        "assistant_text": gm_response_parser.assistant_text_from_response(raw),
        "timestamp": parsed.get("timestamp") or {},
        "has_valid_timestamp": gm_response_parser.is_valid_timestamp(parsed.get("timestamp") or {}),
    }

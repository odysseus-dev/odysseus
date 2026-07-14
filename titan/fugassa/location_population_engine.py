"""LLM-driven location population — decide which NPCs belong at a place and spawn them.

ADR intent: not every grid cell gets NPCs (wilderness peaks stay empty). An LLM
reads campaign theme + location identity and returns a structured population plan.
Engine applies it idempotently (world flag + locations.notes manifest) and keeps
game.json `location_state` / `cell_location_cache` in sync for movement restore.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import grid_engine, npc_generator, world_flags
from titan.fugassa import wizard_json as wj

LOG = logging.getLogger("titan.fugassa.location_population_engine")

POPULATION_FLAG_PREFIX = "location_populated:"
MAX_PRESENT_NPCS = 4
MAX_HIDDEN_NPCS = 2


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str, fallback: str = "npc") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:40] or fallback)


def population_flag_key(location_code: str) -> str:
    return f"{POPULATION_FLAG_PREFIX}{location_code}"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fetch_location_row(db_path: str, location_id: int) -> dict[str, Any] | None:
    if not db_path or not os.path.isfile(db_path) or not location_id:
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, code, name, description_short, description_long, notes FROM locations WHERE id = ?",
            (int(location_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_manifest(notes: str | None) -> dict[str, Any]:
    if not notes:
        return {}
    try:
        data = json.loads(str(notes))
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def load_location_manifest(notes: str | None) -> dict[str, Any]:
    return _load_manifest(notes)


def _save_manifest_conn(conn: sqlite3.Connection, location_id: int, manifest: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE locations SET notes = ?, updated_at = ? WHERE id = ?",
        (json.dumps(manifest, ensure_ascii=False), _utc_now(), int(location_id)),
    )


def is_location_populated(db_path: str, location_code: str) -> bool:
    if world_flags.get_flag(db_path, population_flag_key(location_code)):
        return True
    return False


def is_procedural_wilderness_cell(state: dict[str, Any], loc_row: dict[str, Any]) -> bool:
    """True for auto-generated biome placeholders (LLM may still run but usually skips)."""
    player = state.get("player") or {}
    if player.get("sublocation_id"):
        return False
    x, y, z = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    name = str(loc_row.get("name") or "")
    desc = str(loc_row.get("description_short") or loc_row.get("description_long") or "")
    biome = grid_engine.biome_label(x, y)
    if name.strip().lower() == biome.lower() and desc.strip().lower() == f"a {biome} area.":
        return True
    return False


def should_enqueue_population(
    db_path: str,
    state: dict[str, Any],
    location_id: int,
    *,
    save_id: str,
) -> bool:
    """Whether to queue an LLM population job for this SQL location."""
    loc_row = fetch_location_row(db_path, location_id)
    if not loc_row:
        return False
    code = str(loc_row.get("code") or "")
    if not code or is_location_populated(db_path, code):
        return False
    manifest = _load_manifest(loc_row.get("notes"))
    if manifest.get("population_applied"):
        return False
    if _pending_population_job(db_path, save_id, location_id):
        return False
    # Always consider curated / named places; procedural cells still go through LLM
    # so it can explicitly return populate=false for empty wilderness.
    return True


def _pending_population_job(db_path: str, save_id: str, location_id: int) -> bool:
    from titan.fugassa.db import job_repository

    for status in ("pending", "running"):
        jobs = job_repository.list_jobs(
            db_path,
            save_id=save_id,
            job_type="location_population",
            status=status,
            limit=20,
        )
        for job in jobs:
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if int(payload.get("location_id") or 0) == int(location_id):
                return True
    return False


def build_population_context(
    state: dict[str, Any],
    *,
    loc_row: dict[str, Any],
    opening_excerpt: str = "",
) -> dict[str, str]:
    wp = state.get("world_profile") or {}
    wt = state.get("world_time") or {}
    player = state.get("player") or {}
    x, y, z = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    map_code = str(player.get("map_code") or grid_engine.DEFAULT_MAP_CODE)
    biome = grid_engine.biome_label(x, y)
    desc = str(loc_row.get("description_long") or loc_row.get("description_short") or "").strip()
    return {
        "theme": str(wp.get("theme") or "fantasy"),
        "world_information": str(wp.get("world_information") or "")[:2500],
        "opening_hook": str(wp.get("opening_hook") or "")[:1500],
        "location_code": str(loc_row.get("code") or ""),
        "location_name": str(loc_row.get("name") or "Unknown"),
        "location_description": desc[:2000],
        "biome": biome,
        "coordinates": f"({x}, {y}, {z}) on {map_code}",
        "time_of_day": str(wt.get("time_of_day") or wt.get("hhmm") or ""),
        "weather": str(wt.get("weather") or ""),
        "season": str(wt.get("season") or ""),
        "opening_scene_excerpt": opening_excerpt[:2000],
        "is_procedural_biome": "yes" if is_procedural_wilderness_cell(state, loc_row) else "no",
    }


def _population_system_message() -> str:
    return (
        "You design NPC presence for a tabletop RPG location.\n"
        'Return strict JSON only: {"populate":true|false,"reason":"...","location_kind":"...",'
        '"present_npcs":[...],"hidden_npcs":[...]}\n'
        "populate: false for empty wilderness (mountain peak, barren ruin, deep forest trail) "
        "with no social activity. true for settlements, markets, taverns, camps, dungeons with "
        "inhabitants, corporate districts, etc.\n"
        "present_npcs: 0-4 NPCs visibly present when the player arrives (merchants, guards, "
        "key story figures). Each entry: "
        '{"name":"...","role":"...","race":"...","is_important":true|false,'
        '"backstory_summary":"one sentence"}\n'
        "hidden_npcs: 0-2 optional NPCs not obvious at first glance (pickpocket, spy, hidden guard). "
        "Same fields; these require Investigation to notice in-game.\n"
        "Names must fit the campaign theme. Do NOT duplicate the player character. "
        "Honor campaign lore and tone. No prose outside JSON."
    )


def _population_user_message(ctx: dict[str, str], *, name_registry_block: str = "") -> str:
    parts = [
        f"Campaign theme: {ctx['theme']}\n",
        f"Location: {ctx['location_name']} ({ctx['location_code']})\n",
        f"Grid context: {ctx['coordinates']}, biome={ctx['biome']}, procedural_biome={ctx['is_procedural_biome']}\n",
    ]
    if name_registry_block.strip():
        parts.append(f"{name_registry_block.strip()}\n")
    if ctx.get("time_of_day") or ctx.get("weather"):
        parts.append(
            f"Time/weather: {ctx.get('time_of_day') or 'n/a'}, {ctx.get('weather') or 'n/a'}, "
            f"season={ctx.get('season') or 'n/a'}\n"
        )
    if ctx.get("location_description"):
        parts.append(f"Place description:\n{ctx['location_description']}\n")
    if ctx.get("world_information"):
        parts.append(f"World lore (use for faction/ tone consistency):\n{ctx['world_information']}\n")
    if ctx.get("opening_hook"):
        parts.append(f"Opening situation:\n{ctx['opening_hook']}\n")
    if ctx.get("opening_scene_excerpt"):
        parts.append(f"GM opening scene (extract recurring NPCs if appropriate):\n{ctx['opening_scene_excerpt']}\n")
    return "".join(parts)


def _normalize_npc_entry(raw: Any, *, default_visibility: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if len(name) < 2:
        return None
    return {
        "name": name[:80],
        "role": str(raw.get("role") or "").strip()[:80] or None,
        "race": str(raw.get("race") or "").strip()[:60] or None,
        "is_important": bool(raw.get("is_important")),
        "backstory_summary": str(raw.get("backstory_summary") or "").strip()[:300] or None,
        "visibility": default_visibility,
    }


def parse_population_plan(raw: str) -> dict[str, Any]:
    data = wj.parse_wizard_json_object(raw) or {}
    present = [
        e
        for e in (_normalize_npc_entry(x, default_visibility="present") for x in (data.get("present_npcs") or []))
        if e
    ][:MAX_PRESENT_NPCS]
    hidden = [
        e
        for e in (_normalize_npc_entry(x, default_visibility="hidden") for x in (data.get("hidden_npcs") or []))
        if e
    ][:MAX_HIDDEN_NPCS]
    populate = bool(data.get("populate")) and bool(present or hidden)
    if not data.get("populate"):
        populate = False
    return {
        "populate": populate,
        "reason": str(data.get("reason") or "").strip()[:300],
        "location_kind": str(data.get("location_kind") or "").strip()[:80],
        "present_npcs": present,
        "hidden_npcs": hidden,
        "source": "llm",
    }


def should_persist_population_plan(plan: dict[str, Any]) -> bool:
    """Whether to write manifest + world flag (skip transient LLM-off failures)."""
    if plan.get("source") == "llm":
        return True
    reason = str(plan.get("reason") or "")
    return plan.get("source") == "deterministic" and reason == "procedural wilderness cell"


def deterministic_population_plan(
    state: dict[str, Any],
    *,
    loc_row: dict[str, Any],
) -> dict[str, Any]:
    """Fallback when LLM is off/unavailable — skip procedural cells; no invented NPCs."""
    if is_procedural_wilderness_cell(state, loc_row):
        return {
            "populate": False,
            "reason": "procedural wilderness cell",
            "location_kind": "wilderness",
            "present_npcs": [],
            "hidden_npcs": [],
            "source": "deterministic",
        }
    return {
        "populate": False,
        "reason": "llm_disabled",
        "location_kind": "unknown",
        "present_npcs": [],
        "hidden_npcs": [],
        "source": "deterministic",
    }


async def generate_population_plan(
    state: dict[str, Any],
    *,
    loc_row: dict[str, Any],
    owner: str | None = None,
    llm_enabled: bool = True,
    opening_excerpt: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    ctx = build_population_context(state, loc_row=loc_row, opening_excerpt=opening_excerpt)
    if not llm_enabled:
        return deterministic_population_plan(state, loc_row=loc_row)

    name_block = ""
    if db_path:
        from titan.fugassa import campaign_name_registry

        registry = campaign_name_registry.seed_registry_from_npcs(db_path)
        name_block = campaign_name_registry.prompt_block(registry)

    messages = [
        {"role": "system", "content": _population_system_message()},
        {"role": "user", "content": _population_user_message(ctx, name_registry_block=name_block)},
    ]
    try:
        from titan.fugassa.llm_client import FugassaLlmDisabled, chat_completion

        raw = await chat_completion(messages, owner=owner, max_tokens=900, temperature=0.45)
        plan = parse_population_plan(raw)
        if db_path:
            from titan.fugassa import campaign_name_registry

            plan = campaign_name_registry.sanitize_population_plan(plan, db_path)
        if plan["populate"] or plan.get("reason"):
            return plan
    except FugassaLlmDisabled:
        pass
    except Exception as exc:  # noqa: BLE001
        LOG.warning("location population LLM failed: %s", exc)
    return deterministic_population_plan(state, loc_row=loc_row)


def apply_population_plan_conn(
    conn: sqlite3.Connection,
    *,
    location_id: int,
    location_code: str,
    plan: dict[str, Any],
    db_path: str | None = None,
) -> dict[str, Any]:
    """Spawn NPCs in SQL and persist manifest. Idempotent per location_code flag."""
    if world_flags.get_flag_conn(conn, population_flag_key(location_code)):
        return {"applied": False, "reason": "already_populated"}
    row = conn.execute("SELECT notes FROM locations WHERE id = ?", (int(location_id),)).fetchone()
    if row and _load_manifest(row[0] if isinstance(row, tuple) else row["notes"]).get("population_applied"):
        return {"applied": False, "reason": "already_populated"}

    spawned_present: list[str] = []
    spawned_hidden: list[str] = []
    spawned_npc_ids: list[int] = []

    if plan.get("populate"):
        from titan.fugassa import campaign_name_registry

        path = db_path or ""
        for entry in plan.get("present_npcs") or []:
            spawn_name = entry["name"]
            if path:
                spawn_name = campaign_name_registry.prepare_npc_name(
                    path, entry["name"], role=entry.get("role")
                )
            result = npc_generator.spawn_npc(
                conn,
                name=spawn_name,
                tier="T2",
                location_id=location_id,
                race=entry.get("race"),
                class_role=entry.get("role"),
                is_important=entry.get("is_important"),
                backstory_summary=entry.get("backstory_summary"),
                code=_slug(spawn_name, "npc"),
            )
            if result.get("npc_id"):
                if path:
                    campaign_name_registry.register_spawned_npc(
                        path, npc_id=int(result["npc_id"]), name=spawn_name
                    )
                spawned_present.append(spawn_name)
                spawned_npc_ids.append(int(result["npc_id"]))
        for entry in plan.get("hidden_npcs") or []:
            spawn_name = entry["name"]
            if path:
                spawn_name = campaign_name_registry.prepare_npc_name(
                    path, entry["name"], role=entry.get("role")
                )
            result = npc_generator.spawn_npc(
                conn,
                name=spawn_name,
                tier="T2",
                location_id=location_id,
                race=entry.get("race"),
                class_role=entry.get("role"),
                is_important=entry.get("is_important"),
                backstory_summary=entry.get("backstory_summary"),
                code=_slug(spawn_name, "npc"),
            )
            if result.get("npc_id"):
                if path:
                    campaign_name_registry.register_spawned_npc(
                        path, npc_id=int(result["npc_id"]), name=spawn_name
                    )
                spawned_hidden.append(spawn_name)
                spawned_npc_ids.append(int(result["npc_id"]))

    manifest = {
        "population_applied": True,
        "plan": plan,
        "spawned_present": spawned_present,
        "spawned_hidden": spawned_hidden,
        "applied_at": _utc_now(),
    }
    _save_manifest_conn(conn, location_id, manifest)
    world_flags.set_flag_conn(conn, population_flag_key(location_code), "1")
    return {
        "applied": True,
        "populate": bool(plan.get("populate")),
        "present": spawned_present,
        "hidden": spawned_hidden,
        "spawned_npc_ids": spawned_npc_ids,
        "reason": plan.get("reason"),
        "source": plan.get("source"),
    }


def present_npc_names_from_manifest(manifest: dict[str, Any]) -> set[str]:
    """Names the population manifest marks as visibly present at a location."""
    names: set[str] = set()
    for raw in manifest.get("spawned_present") or []:
        if str(raw).strip():
            names.add(str(raw).strip())
    plan = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
    for entry in plan.get("present_npcs") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            names.add(str(entry["name"]).strip())
    return names


def hidden_npc_names_from_manifest(manifest: dict[str, Any]) -> set[str]:
    """Names that should stay in hidden_npcs until Investigation reveals them."""
    names: set[str] = set()
    for raw in manifest.get("spawned_hidden") or []:
        if str(raw).strip():
            names.add(str(raw).strip())
    plan = manifest.get("plan") if isinstance(manifest.get("plan"), dict) else {}
    for entry in plan.get("hidden_npcs") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            names.add(str(entry["name"]).strip())
    return names


def manifest_npc_names(manifest: dict[str, Any]) -> set[str]:
    return present_npc_names_from_manifest(manifest) | hidden_npc_names_from_manifest(manifest)


def merge_population_into_state(
    state: dict[str, Any],
    *,
    present: list[str],
    hidden: list[str],
    population_done: bool = True,
) -> None:
    """Update runtime location_state + cell cache so HUD/movement see NPCs."""
    loc = dict(state.get("location_state") or {})
    if present:
        existing = [str(n).strip() for n in (loc.get("npcs") or []) if str(n).strip()]
        merged = existing[:]
        for name in present:
            if name not in merged:
                merged.append(name)
        loc["npcs"] = merged
    if hidden:
        existing_h = [str(n).strip() for n in (loc.get("hidden_npcs") or []) if str(n).strip()]
        merged_h = existing_h[:]
        for name in hidden:
            if name not in merged_h:
                merged_h.append(name)
        loc["hidden_npcs"] = merged_h
    state["location_state"] = loc

    player = state.get("player") or {}
    if player.get("sublocation_id"):
        return
    map_code = str(player.get("map_code") or grid_engine.DEFAULT_MAP_CODE)
    key = grid_engine.coord_key(
        int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0)), map_code
    )
    cache = dict(state.get("cell_location_cache") or {})
    cached = dict(cache.get(key) or {}) if isinstance(cache.get(key), dict) else {}
    if not cached.get("name"):
        cached["name"] = loc.get("name", "")
    if not cached.get("description"):
        cached["description"] = loc.get("description", "")
    if present:
        cached_n = [str(n) for n in (cached.get("npcs") or []) if str(n).strip()]
        for name in present:
            if name not in cached_n:
                cached_n.append(name)
        cached["npcs"] = cached_n
    if hidden:
        cached_h = [str(n) for n in (cached.get("hidden_npcs") or []) if str(n).strip()]
        for name in hidden:
            if name not in cached_h:
                cached_h.append(name)
        cached["hidden_npcs"] = cached_h
    if population_done:
        cached["population_done"] = True
    cache[key] = cached
    state["cell_location_cache"] = cache


def refresh_location_npcs_from_sql(db_path: str, state: dict[str, Any]) -> dict[str, Any]:
    """Reload npc lists + npc_details for the active scene location."""
    from titan.fugassa.db import state_repository

    loc_id = int((state.get("location_state") or {}).get("location_id") or state.get("_current_location_id") or 0)
    if loc_id:
        return state_repository.sync_location_state_npcs(db_path, state, loc_id)
    return state_repository.enrich_state_from_sql(db_path, state)


async def run_population_for_location(
    save_id: str,
    db_path: str,
    state: dict[str, Any],
    *,
    location_id: int,
    owner: str | None = None,
    llm_enabled: bool = True,
    opening_excerpt: str = "",
) -> dict[str, Any]:
    loc_row = fetch_location_row(db_path, location_id)
    if not loc_row:
        return {"applied": False, "reason": "location_not_found"}
    code = str(loc_row.get("code") or "")
    if is_location_populated(db_path, code):
        refresh_location_npcs_from_sql(db_path, state)
        return {"applied": False, "reason": "already_populated", "skipped": True}

    plan = await generate_population_plan(
        state,
        loc_row=loc_row,
        owner=owner,
        llm_enabled=llm_enabled,
        opening_excerpt=opening_excerpt,
        db_path=db_path,
    )
    if not should_persist_population_plan(plan):
        return {
            "applied": False,
            "reason": plan.get("reason") or "population_deferred",
            "skipped": True,
            "plan": plan,
        }
    present_names = [str(e["name"]) for e in (plan.get("present_npcs") or []) if e.get("name")]
    hidden_names = [str(e["name"]) for e in (plan.get("hidden_npcs") or []) if e.get("name")]
    if plan.get("populate"):
        merge_population_into_state(state, present=present_names, hidden=hidden_names, population_done=False)

    conn = _connect(db_path)
    result: dict[str, Any] = {"applied": False, "reason": "apply_failed"}
    try:
        result = apply_population_plan_conn(
            conn,
            location_id=location_id,
            location_code=code,
            plan=plan,
            db_path=db_path,
        )
        if result.get("applied") and plan.get("populate"):
            from titan.fugassa.db import state_repository

            state_repository._sync_npcs_at_location(
                conn, location_id, state.get("location_state") or {}, db_path=db_path
            )
        conn.commit()
    finally:
        conn.close()

    if result.get("applied"):
        if plan.get("populate"):
            merge_population_into_state(
                state,
                present=list(result.get("present") or present_names),
                hidden=list(result.get("hidden") or hidden_names),
                population_done=True,
            )
            spawned_ids = list(result.get("spawned_npc_ids") or [])
            if spawned_ids:
                from titan.fugassa import config_store, npc_portrait_prompts

                cfg = config_store.load()
                try:
                    await npc_portrait_prompts.assign_portrait_prompts_for_npc_ids(
                        db_path,
                        state,
                        spawned_ids,
                        owner=owner,
                        llm_enabled=bool(cfg.get("llm_enabled", True)),
                    )
                except Exception:  # noqa: BLE001 — prompts must not break population
                    LOG.warning("NPC portrait prompt assignment failed", exc_info=True)
        else:
            merge_population_into_state(state, present=[], hidden=[], population_done=True)
        refresh_location_npcs_from_sql(db_path, state)
    return {**result, "plan": plan}


def enqueue_population_job(
    db_path: str,
    *,
    save_id: str,
    location_id: int,
    state: dict[str, Any] | None = None,
    owner: str | None = None,
    opening_excerpt: str = "",
    turn_number: int | None = None,
) -> int | None:
    if not should_enqueue_population(db_path, state or {}, location_id, save_id=save_id):
        return None
    from titan.fugassa.db import job_repository

    batch_id = f"pop_{location_id}_{int(datetime.now().timestamp())}"
    return job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="location_population",
        batch_id=batch_id,
        payload={
            "location_id": int(location_id),
            "owner": owner,
            "opening_excerpt": opening_excerpt[:2000] if opening_excerpt else "",
        },
        priority=120,
        turn_number=turn_number,
    )

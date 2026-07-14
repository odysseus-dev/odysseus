"""Repair world time, movement desync, and chat metadata on existing saves."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

from datetime import datetime, timezone

from titan.fugassa import narrative_movement, world_time_engine
from titan.fugassa.db import state_repository
from titan.fugassa.game_bootstrap import GAME_JSON, read_game_json, write_game_json
from titan.fugassa.save_store import game_db_path
from titan.fugassa.turn_resolver import apply_time_delta, sync_location_and_track
from titan.fugassa.turn_resolution import TurnResolution


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_sublocation_connections(db_path: str) -> int:
    """Backfill parent→child graph edges required for travel/discovery."""
    conn = _connect(db_path)
    now = _utc_now()
    added = 0
    try:
        rows = conn.execute(
            "SELECT id, name, parent_location_id FROM locations WHERE parent_location_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM location_connections WHERE from_location_id = ? AND to_location_id = ?",
                (int(row["parent_location_id"]), int(row["id"])),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO location_connections (from_location_id, to_location_id, connection_type, label, created_at)
                VALUES (?, ?, 'contains', ?, ?)
                """,
                (int(row["parent_location_id"]), int(row["id"]), row["name"], now),
            )
            added += 1
        conn.commit()
        return added
    finally:
        conn.close()


def _relocate_location_references(conn: sqlite3.Connection, *, from_location_id: int, to_location_id: int) -> None:
    """Move NPCs/items/quests off a duplicate location row before it is deleted."""
    now = _utc_now()
    conn.execute(
        "UPDATE npcs SET current_location_id = ?, updated_at = ? WHERE current_location_id = ?",
        (to_location_id, now, from_location_id),
    )
    conn.execute(
        "UPDATE items SET owner_id = ? WHERE owner_type = 'location' AND owner_id = ?",
        (to_location_id, from_location_id),
    )
    conn.execute(
        "UPDATE quests SET related_location_id = ? WHERE related_location_id = ?",
        (to_location_id, from_location_id),
    )


def dedupe_sublocations(db_path: str) -> int:
    """Remove orphan duplicate child rows created by repeated repairs."""
    conn = _connect(db_path)
    removed = 0
    try:
        rows = conn.execute(
            "SELECT id, code, name, parent_location_id FROM locations WHERE parent_location_id IS NOT NULL ORDER BY id"
        ).fetchall()
        for row in rows:
            parent = conn.execute(
                "SELECT parent_location_id FROM locations WHERE id = ?",
                (int(row["parent_location_id"]),),
            ).fetchone()
            if parent and parent["parent_location_id"] is not None:
                parent_parent = int(parent["parent_location_id"])
                if parent_parent == int(row["parent_location_id"]):
                    continue
                target_id = int(row["parent_location_id"])
                _relocate_location_references(conn, from_location_id=int(row["id"]), to_location_id=target_id)
                conn.execute("DELETE FROM world_flags WHERE key = ?", (f"location_populated:{row['code']}",))
                conn.execute(
                    "DELETE FROM location_connections WHERE from_location_id = ? OR to_location_id = ?",
                    (row["id"], row["id"]),
                )
                conn.execute("DELETE FROM locations WHERE id = ?", (row["id"],))
                removed += 1
        conn.commit()
        return removed
    finally:
        conn.close()


def repair_orphan_npc_locations(db_path: str) -> int:
    """Move NPCs pointing at deleted location rows back to their manifest owner."""
    conn = _connect(db_path)
    moved = 0
    try:
        def _orphan_rows() -> list[sqlite3.Row]:
            return conn.execute(
                """
                SELECT n.id, n.name, n.current_location_id
                FROM npcs n
                WHERE n.current_location_id IS NOT NULL
                  AND n.current_location_id NOT IN (SELECT id FROM locations)
                """
            ).fetchall()

        orphans = _orphan_rows()
        for row in orphans:
            target_id: int | None = None
            for loc in conn.execute(
                "SELECT id, notes FROM locations WHERE notes IS NOT NULL AND TRIM(notes) != ''"
            ):
                try:
                    manifest = json.loads(str(loc["notes"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(manifest, dict):
                    continue
                spawned = set(manifest.get("spawned_present") or []) | set(manifest.get("spawned_hidden") or [])
                if row["name"] in spawned:
                    target_id = int(loc["id"])
                    break
            if target_id is None:
                continue
            conn.execute(
                "UPDATE npcs SET current_location_id = ?, updated_at = ? WHERE id = ?",
                (target_id, _utc_now(), int(row["id"])),
            )
            moved += 1

        # Second pass: NPCs stranded on the same deleted location id — move to the
        # manifest location that already owns the largest overlap (archivist extras).
        invalid_ids = {
            int(r["current_location_id"])
            for r in _orphan_rows()
            if r["current_location_id"] is not None
        }
        for invalid_id in invalid_ids:
            names = {
                str(r["name"])
                for r in conn.execute(
                    "SELECT name FROM npcs WHERE current_location_id = ?",
                    (invalid_id,),
                )
            }
            best_target: int | None = None
            best_overlap = 0
            for loc in conn.execute(
                "SELECT id, notes FROM locations WHERE notes IS NOT NULL AND TRIM(notes) != ''"
            ):
                try:
                    manifest = json.loads(str(loc["notes"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(manifest, dict) or not manifest.get("population_applied"):
                    continue
                spawned = set(manifest.get("spawned_present") or []) | set(manifest.get("spawned_hidden") or [])
                overlap = len(names & spawned)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_target = int(loc["id"])
            if best_target is None:
                grid_row = conn.execute(
                    "SELECT id FROM locations WHERE parent_location_id IS NULL ORDER BY id LIMIT 1"
                ).fetchone()
                if grid_row:
                    best_target = int(grid_row["id"])
            if best_target:
                conn.execute(
                    "UPDATE npcs SET current_location_id = ?, updated_at = ? WHERE current_location_id = ?",
                    (best_target, _utc_now(), invalid_id),
                )
                moved += conn.execute("SELECT changes()").fetchone()[0]

        conn.execute(
            """
            DELETE FROM world_flags
            WHERE key LIKE 'location_populated:%'
              AND substr(key, 21) NOT IN (SELECT code FROM locations)
            """
        )
        conn.commit()
        return moved
    finally:
        conn.close()


def relocate_stray_npcs_from_populated_locations(db_path: str) -> int:
    """Move archivist/noise NPC rows off population-managed interiors back to the parent area."""
    from titan.fugassa.location_population_engine import load_location_manifest, manifest_npc_names

    conn = _connect(db_path)
    moved = 0
    try:
        for loc in conn.execute(
            "SELECT id, parent_location_id, notes FROM locations WHERE notes IS NOT NULL AND TRIM(notes) != ''"
        ):
            manifest = load_location_manifest(loc["notes"])
            if not manifest.get("population_applied"):
                continue
            allowed = manifest_npc_names(manifest)
            if not allowed:
                continue
            target_id = int(loc["parent_location_id"]) if loc["parent_location_id"] else None
            if not target_id:
                grid_row = conn.execute(
                    "SELECT id FROM locations WHERE parent_location_id IS NULL ORDER BY id LIMIT 1"
                ).fetchone()
                target_id = int(grid_row["id"]) if grid_row else None
            if not target_id or int(target_id) == int(loc["id"]):
                continue
            for npc in conn.execute(
                "SELECT id, name FROM npcs WHERE current_location_id = ? AND status = 'alive'",
                (int(loc["id"]),),
            ):
                if str(npc["name"]) not in allowed:
                    conn.execute(
                        "UPDATE npcs SET current_location_id = ?, updated_at = ? WHERE id = ?",
                        (int(target_id), _utc_now(), int(npc["id"])),
                    )
                    moved += 1
        conn.commit()
        return moved
    finally:
        conn.close()


def restore_population_flags(db_path: str) -> int:
    """Rehydrate population flags from location manifests after orphan cleanup."""
    conn = _connect(db_path)
    restored = 0
    try:
        for row in conn.execute("SELECT code, notes FROM locations WHERE notes IS NOT NULL AND TRIM(notes) != ''"):
            try:
                manifest = json.loads(str(row["notes"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(manifest, dict) or not manifest.get("population_applied"):
                continue
            key = f"location_populated:{row['code']}"
            conn.execute(
                """
                INSERT INTO world_flags (key, value, updated_at)
                VALUES (?, '1', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, _utc_now()),
            )
            restored += 1
        conn.commit()
        return restored
    finally:
        conn.close()


def repair_cell_location_cache(state: dict[str, Any], db_path: str) -> int:
    """Keep outdoor cell cache aligned with the grid parent, not interior population."""
    from titan.fugassa import grid_engine
    from titan.fugassa.location_population_engine import hidden_npc_names_from_manifest, load_location_manifest

    player = state.get("player") or {}
    anchor = player.get("sublocation_anchor") if isinstance(player.get("sublocation_anchor"), dict) else {}
    map_code = str(anchor.get("map_code") or player.get("map_code") or grid_engine.DEFAULT_MAP_CODE)
    x = int(anchor.get("x", player.get("x", 0)))
    y = int(anchor.get("y", player.get("y", 0)))
    z = int(anchor.get("z", player.get("z", 0)))
    key = grid_engine.coord_key(x, y, z, map_code)
    cache = dict(state.get("cell_location_cache") or {})
    if key not in cache or not isinstance(cache.get(key), dict):
        return 0

    conn = _connect(db_path)
    try:
        parent_loc_id: int | None = None
        if anchor:
            row = conn.execute(
                """
                SELECT location_id FROM grid_cells
                WHERE map_code = ? AND x = ? AND y = ? AND z = ?
                """,
                (map_code, x, y, z),
            ).fetchone()
            if row and row["location_id"]:
                parent_loc_id = int(row["location_id"])
        if parent_loc_id is None:
            outdoor_state = dict(state)
            outdoor_player = dict(player)
            outdoor_player.pop("sublocation_id", None)
            outdoor_state["player"] = outdoor_player
            parent_loc_id = narrative_movement._grid_location_id(db_path, outdoor_state)
        if not parent_loc_id:
            return 0
        parent_row = conn.execute(
            "SELECT name, description_short, notes FROM locations WHERE id = ?",
            (int(parent_loc_id),),
        ).fetchone()
        if not parent_row:
            return 0
        npc_rows = conn.execute(
            "SELECT name FROM npcs WHERE current_location_id = ? AND status = 'alive'",
            (int(parent_loc_id),),
        ).fetchall()
        manifest = load_location_manifest(parent_row["notes"])
        hidden_names = hidden_npc_names_from_manifest(manifest)
        visible = [r["name"] for r in npc_rows if r["name"] not in hidden_names]
        hidden = [r["name"] for r in npc_rows if r["name"] in hidden_names]
        cached = dict(cache[key])
        cached["name"] = parent_row["name"] or cached.get("name", "")
        cached["description"] = parent_row["description_short"] or cached.get("description", "")
        cached["npcs"] = visible
        cached["hidden_npcs"] = hidden
        cache[key] = cached
        state["cell_location_cache"] = cache
        return 1
    finally:
        conn.close()


def backfill_chat_metadata(state: dict[str, Any], db_path: str) -> int:
    """Attach ingame_time/location and scene_cast to GM chat rows when missing."""
    from titan.fugassa.gm_response_parser import extract_current_scene_narrative
    from titan.fugassa.scene_character_context import scene_cast_metadata

    conn = _connect(db_path)
    try:
        rows = {
            int(r["turn_number"]): dict(r)
            for r in conn.execute(
                "SELECT turn_number, player_text, ai_text, ingame_time FROM turn_history ORDER BY turn_number"
            )
        }
    finally:
        conn.close()

    updated = 0
    loc_name = (state.get("location_state") or {}).get("name") or ""
    for msg in state.get("chat_history") or []:
        if msg.get("role") != "assistant":
            continue
        turn = int(msg.get("turn_number") or 0)
        row = rows.get(turn) or {}
        changed = False
        if not msg.get("ingame_time"):
            label = row.get("ingame_time") or world_time_engine.format_chat_header(
                state.get("world_time") or {}, loc_name
            )
            if label:
                msg["ingame_time"] = label
                msg["location"] = loc_name
                changed = True
        if not msg.get("scene_cast") and row.get("ai_text"):
            narrative = extract_current_scene_narrative(str(row.get("ai_text") or ""))
            msg["scene_cast"] = scene_cast_metadata(
                state=state,
                db_path=db_path,
                narrative=narrative,
                player_action=str(row.get("player_text") or ""),
            )
            changed = True
        if changed:
            updated += 1
    return updated


def fix_sublocation_descriptions(db_path: str, state: dict[str, Any]) -> int:
    """Rewrite child location rows that still carry the parent outdoor prose."""
    parent_id = narrative_movement._grid_location_id(db_path, state)
    if not parent_id:
        return 0
    conn = _connect(db_path)
    try:
        parent = conn.execute(
            "SELECT description_short, description_long FROM locations WHERE id = ?",
            (int(parent_id),),
        ).fetchone()
        parent_desc = (parent["description_long"] or parent["description_short"] or "") if parent else ""
        fixed = 0
        for row in conn.execute(
            "SELECT id, name, description_short FROM locations WHERE parent_location_id = ?",
            (int(parent_id),),
        ):
            if narrative_movement._is_stale_parent_description(row["description_short"] or "", row["name"]):
                desc = narrative_movement._description_for_sublocation(parent_desc, row["name"])
                conn.execute(
                    "UPDATE locations SET description_short = ?, description_long = ?, updated_at = ? WHERE id = ?",
                    (desc[:500], desc[:2000], _utc_now(), int(row["id"])),
                )
                fixed += 1
        conn.commit()
        return fixed
    finally:
        conn.close()


def repair_fugassa_movement(state: dict[str, Any], db_path: str) -> dict[str, Any]:
    """Place player in the matched sublocation with a correct interior description."""
    summary: dict[str, Any] = {"movement_applied": False}
    fix_sublocation_descriptions(db_path, state)

    history = state.get("chat_history") or []
    visit_text = ""
    for msg in history:
        if msg.get("role") == "user" and "visit" in str(msg.get("content") or "").lower():
            visit_text = str(msg.get("content") or "")

    parent_id = int(state.get("_current_location_id") or (state.get("location_state") or {}).get("location_id") or 1)
    if state.get("player", {}).get("sublocation_anchor"):
        parent_id = narrative_movement._grid_location_id(db_path, state) or parent_id

    conn = _connect(db_path)
    try:
        parent = conn.execute(
            "SELECT description_short, description_long FROM locations WHERE id = ?",
            (parent_id,),
        ).fetchone()
        parent_desc = ""
        if parent:
            pl = (parent["description_long"] or "").strip()
            ps = (parent["description_short"] or "").strip()
            parent_desc = ps if len(ps) > len(pl) else (pl or ps)
    finally:
        conn.close()

    sub_name = "Concubine's Residence"
    for place in narrative_movement.discover_embedded_places(parent_desc):
        if "residence" in place.lower() or "concubine" in place.lower():
            sub_name = place
            break

    sub_id = narrative_movement.ensure_sublocation(
        db_path,
        parent_location_id=parent_id,
        name=sub_name,
        parent_description=parent_desc,
    )

    if visit_text:
        result = narrative_movement.enter_sublocation(db_path, state, sub_id, label=sub_name)
    elif state.get("player", {}).get("sublocation_id"):
        sub_id = int(state["player"]["sublocation_id"])
        if sub_id != narrative_movement.ensure_sublocation(
            db_path, parent_location_id=parent_id, name=sub_name, parent_description=parent_desc
        ):
            sub_id = narrative_movement.ensure_sublocation(
                db_path, parent_location_id=parent_id, name=sub_name, parent_description=parent_desc
            )
            state["player"]["sublocation_id"] = sub_id
        result = narrative_movement.enter_sublocation(db_path, state, sub_id, label=sub_name)
    else:
        return summary

    if result.get("success"):
        from titan.fugassa.turn_resolver import sync_location_and_track
        from titan.fugassa.turn_resolution import TurnResolution

        resolution = TurnResolution(mode="action", intent="repair")
        sync_location_and_track(db_path, state, resolution)
        state["_current_location_id"] = sub_id
        summary.update({"movement_applied": True, "sublocation_id": sub_id, "sublocation_name": state["location_state"]["name"]})
    return summary


def backfill_property_holdings(db_path: str, state: dict[str, Any]) -> bool:
    """Promote legacy JSON portfolio entries into SQL property_holdings."""
    if not db_path or not os.path.isfile(db_path):
        return False
    from titan.fugassa.property_repository import backfill_holdings_from_state_conn

    conn = _connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        created = backfill_holdings_from_state_conn(conn, state)
        if created:
            conn.commit()
            from titan.fugassa.property_repository import sync_property_portfolio

            sync_property_portfolio(conn, state)
            return True
        return False
    finally:
        conn.close()


def backfill_portrait_prompts(db_path: str, state: dict[str, Any] | None = None) -> dict[str, int]:
    """Copy missing portrait_prompt values from wizard text, appearance rows, or assets."""
    if not db_path or not os.path.isfile(db_path):
        return {"player": 0, "npc": 0}
    from titan.fugassa.db import asset_repository
    from titan.fugassa.player_portrait_prompt import (
        is_generic_auto_portrait_prompt,
        prompt_from_wizard_state,
        resolve_portrait_prompts_from_sources,
    )

    conn = _connect(db_path)
    updated_player = 0
    updated_npc = 0
    now = _utc_now()
    try:
        pc = conn.execute(
            "SELECT id, portrait_prompt FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        if pc:
            stored = str(pc["portrait_prompt"] or "").strip()
            wizard_pos, wizard_neg, combined = resolve_portrait_prompts_from_sources(game_state=state)
            if combined and isinstance(state, dict):
                snap = state.setdefault("wizard_draft_snapshot", {})
                if isinstance(snap, dict) and not str(snap.get("portrait_sd_prompt_text") or "").strip():
                    snap["portrait_sd_prompt_text"] = combined
            if not wizard_pos:
                wizard_pos, wizard_neg = prompt_from_wizard_state(state)
            prompt = ""
            negative = str(wizard_neg or "").strip()
            if not stored:
                if wizard_pos:
                    prompt = wizard_pos
                if not prompt:
                    active = asset_repository.get_active_asset(
                        db_path,
                        entity_type="player_character",
                        entity_id=int(pc["id"]),
                        asset_type="portrait",
                    )
                    if active and str(active.get("prompt") or "").strip():
                        prompt = str(active["prompt"]).strip()
                    if not negative and active and str(active.get("negative_prompt") or "").strip():
                        negative = str(active["negative_prompt"]).strip()
            elif wizard_pos and is_generic_auto_portrait_prompt(stored):
                prompt = wizard_pos
            if prompt and prompt != stored:
                conn.execute(
                    "UPDATE player_characters SET portrait_prompt = ?, updated_at = ? WHERE id = ?",
                    (prompt, now, int(pc["id"])),
                )
                updated_player = 1
            if prompt or negative:
                active = asset_repository.get_active_asset(
                    db_path,
                    entity_type="player_character",
                    entity_id=int(pc["id"]),
                    asset_type="portrait",
                )
                if active:
                    asset_id = int(active["id"])
                    asset_pos = str(active.get("prompt") or "").strip()
                    asset_neg = str(active.get("negative_prompt") or "").strip()
                    new_pos = prompt or asset_pos
                    new_neg = negative or asset_neg
                    if new_pos != asset_pos or new_neg != asset_neg:
                        conn.execute(
                            """
                            UPDATE assets
                            SET prompt = ?, negative_prompt = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (new_pos or None, new_neg or None, now, asset_id),
                        )

        npc_rows = conn.execute(
            "SELECT id FROM npcs WHERE portrait_prompt IS NULL OR TRIM(portrait_prompt) = ''"
        ).fetchall()
        for row in npc_rows:
            active = asset_repository.get_active_asset(
                db_path,
                entity_type="npc",
                entity_id=int(row["id"]),
                asset_type="portrait",
            )
            if active and str(active.get("prompt") or "").strip():
                conn.execute(
                    "UPDATE npcs SET portrait_prompt = ?, updated_at = ? WHERE id = ?",
                    (str(active["prompt"]).strip(), now, int(row["id"])),
                )
                updated_npc += 1
        conn.commit()
    finally:
        conn.close()
    return {"player": updated_player, "npc": updated_npc}


def repair_save(save_id: str, *, fix_movement: bool = True) -> dict[str, Any]:
    save_dir = os.path.dirname(game_db_path(save_id))
    db_path = game_db_path(save_id)
    state = read_game_json(save_dir)
    summary: dict[str, Any] = {"save_id": save_id}

    time_fixed = world_time_engine.repair_world_time_from_wizard(state)
    summary["world_time_repaired"] = time_fixed
    wt = dict(state.get("world_time") or {})
    parsed = world_time_engine.parse_hhmm(str(wt.get("hhmm") or ""))
    if parsed:
        wt["hour"], wt["minute"] = parsed
        state["world_time"] = wt

    # Advance clock for player turns that never moved time (8 min each).
    user_turns = sum(1 for m in state.get("chat_history") or [] if m.get("role") == "user")
    if user_turns:
        apply_time_delta(state, int(user_turns) * 8)
        summary["time_advanced_minutes"] = int(user_turns) * 8

    chat_updates = backfill_chat_metadata(state, db_path)
    summary["chat_metadata_backfilled"] = chat_updates
    summary["portrait_prompts_backfilled"] = backfill_portrait_prompts(db_path, state)
    summary["sublocations_fixed"] = fix_sublocation_descriptions(db_path, state)
    summary["sublocations_deduped"] = dedupe_sublocations(db_path)
    summary["orphan_npcs_relocated"] = repair_orphan_npc_locations(db_path)
    summary["population_flags_restored"] = restore_population_flags(db_path)
    summary["sublocation_connections_added"] = ensure_sublocation_connections(db_path)
    summary["cell_cache_repaired"] = repair_cell_location_cache(state, db_path)

    from titan.fugassa import campaign_name_registry

    registry = campaign_name_registry.seed_registry_from_npcs(db_path)
    summary["name_registry_entries"] = len(registry.entries)

    if fix_movement:
        summary["movement"] = repair_fugassa_movement(state, db_path)

    state_repository.export_json_snapshot(db_path, state, save_dir)
    state = state_repository.enrich_state_from_sql(db_path, state)
    write_game_json(save_dir, state)
    summary["world_time"] = state.get("world_time")
    summary["player"] = state.get("player")
    summary["location"] = (state.get("location_state") or {}).get("name")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair world time / movement / chat metadata on a Fugassa save")
    parser.add_argument("save_id", help="Save folder name (e.g. Fugassa)")
    parser.add_argument("--no-movement", action="store_true", help="Skip movement/sublocation repair")
    args = parser.parse_args()
    result = repair_save(args.save_id, fix_movement=not args.no_movement)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

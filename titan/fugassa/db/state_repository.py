"""SQL ↔ game.json sync — ADR M2 dual-write."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import asset_repository, sqlite_store
from titan.fugassa.sheet_persistence import enrich_character_sheet_from_sql
from titan.fugassa.turn_resolution import TurnResolution
from titan.fugassa import npc_generator, world_flags

LOG = logging.getLogger("titan.fugassa.state_repository")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(name: str, fallback: str = "x") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:40] or fallback)


def _connect(db_path: str) -> sqlite3.Connection:
    sqlite_store.ensure_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def npc_mentioned_in_prose(name: str, prose: str) -> bool:
    """True when GM prose names the NPC in this turn (layer-3 scene gate)."""
    needle = str(name or "").strip().lower()
    return bool(needle) and needle in str(prose or "").lower()


def promote_narrative_npcs_to_scene(
    db_path: str,
    state: dict[str, Any],
    *,
    gm_prose: str,
    npc_names: list[str],
) -> list[str]:
    """Layer 3 — add archivist-created NPCs to visible scene cast when GM used them."""
    if not npc_names or not str(gm_prose or "").strip():
        return []
    loc = dict(state.get("location_state") or {})
    visible = [str(n).strip() for n in (loc.get("npcs") or []) if str(n).strip()]
    hidden = [str(n).strip() for n in (loc.get("hidden_npcs") or []) if str(n).strip()]
    narrative = [str(n).strip() for n in (loc.get("narrative_npcs") or []) if str(n).strip()]
    promoted: list[str] = []

    conn = _connect(db_path) if db_path and os.path.isfile(db_path) else None
    try:
        for raw_name in npc_names:
            name = str(raw_name or "").strip()
            if not name or not npc_mentioned_in_prose(name, gm_prose):
                continue
            if name in hidden:
                hidden = [h for h in hidden if h != name]
            if name not in visible:
                visible.append(name)
            if name not in narrative:
                narrative.append(name)
            promoted.append(name)
        if not promoted:
            return []
        loc["npcs"] = visible
        loc["hidden_npcs"] = hidden
        loc["narrative_npcs"] = narrative
        if conn:
            details = list(loc.get("npc_details") or [])
            known_ids = {int(d.get("npc_id") or 0) for d in details}
            for name in promoted:
                row = conn.execute(
                    "SELECT id, name, is_hostile FROM npcs WHERE name = ? AND status = 'alive' LIMIT 1",
                    (name,),
                ).fetchone()
                if not row or int(row["id"]) in known_ids:
                    continue
                details.append(
                    {
                        "npc_id": int(row["id"]),
                        "name": row["name"],
                        "is_hostile": bool(row["is_hostile"]),
                        "tags": [
                            t["tag"]
                            for t in conn.execute(
                                "SELECT tag FROM npc_tags WHERE npc_id = ?", (int(row["id"]),)
                            ).fetchall()
                        ],
                    }
                )
            loc["npc_details"] = details
        state["location_state"] = loc
        return promoted
    finally:
        if conn:
            conn.close()


def _apply_location_npcs_to_state(
    conn: sqlite3.Connection,
    loc: dict[str, Any],
    *,
    location_id: int,
    notes: str | None,
) -> None:
    from titan.fugassa.location_population_engine import (
        hidden_npc_names_from_manifest,
        load_location_manifest,
        manifest_npc_names,
        present_npc_names_from_manifest,
    )

    npc_rows = conn.execute(
        "SELECT id, name, is_hostile, portrait_path FROM npcs WHERE current_location_id = ? AND status = 'alive'",
        (int(location_id),),
    ).fetchall()
    narrative_names = {str(n).strip() for n in (loc.get("narrative_npcs") or []) if str(n).strip()}
    prior_visible = [str(n).strip() for n in (loc.get("npcs") or []) if str(n).strip()]
    scene_extra_names = set(narrative_names)
    loc["npcs"] = []
    loc["hidden_npcs"] = []
    loc["npc_details"] = []
    if not npc_rows:
        return
    manifest = load_location_manifest(notes)
    hidden_names = hidden_npc_names_from_manifest(manifest)
    present_names = present_npc_names_from_manifest(manifest)
    for name in prior_visible:
        if name not in present_names and name not in hidden_names:
            scene_extra_names.add(name)
    if manifest.get("population_applied") and manifest_npc_names(manifest):
        visible_rows = [
            r for r in npc_rows if r["name"] in present_names or r["name"] in scene_extra_names
        ]
        hidden_rows = [
            r for r in npc_rows if r["name"] in hidden_names and r["name"] not in scene_extra_names
        ]
    else:
        visible_rows = [r for r in npc_rows if r["name"] not in hidden_names]
        hidden_rows = [r for r in npc_rows if r["name"] in hidden_names]
    loc["npcs"] = [r["name"] for r in visible_rows]
    if hidden_rows:
        loc["hidden_npcs"] = [r["name"] for r in hidden_rows]
    detail_rows = visible_rows or hidden_rows or npc_rows
    loc["npc_details"] = [
        {
            "npc_id": r["id"],
            "name": r["name"],
            "is_hostile": bool(r["is_hostile"]),
            "portrait_path": str(r["portrait_path"] or "").strip() or None,
            "tags": [
                t["tag"]
                for t in conn.execute("SELECT tag FROM npc_tags WHERE npc_id = ?", (r["id"],)).fetchall()
            ],
        }
        for r in detail_rows
    ]


def sync_location_state_npcs(db_path: str, state: dict[str, Any], location_id: int) -> dict[str, Any]:
    """Reload visible/hidden NPC lists for a specific SQL location into location_state."""
    if not db_path or not os.path.isfile(db_path) or not location_id:
        return state
    conn = _connect(db_path)
    try:
        loc_row = conn.execute(
            "SELECT id, name, description_short, image_path, notes FROM locations WHERE id = ?",
            (int(location_id),),
        ).fetchone()
        if not loc_row:
            return state
        loc = dict(state.get("location_state") or {})
        loc["location_id"] = loc_row["id"]
        if not loc.get("name"):
            loc["name"] = loc_row["name"]
        if not loc.get("description"):
            loc["description"] = loc_row["description_short"] or ""
        if loc_row["image_path"]:
            loc["scene_asset"] = loc_row["image_path"]
        _apply_location_npcs_to_state(
            conn,
            loc,
            location_id=int(loc_row["id"]),
            notes=loc_row["notes"],
        )
        state["location_state"] = loc
        state["_current_location_id"] = int(loc_row["id"])
    except sqlite3.Error as exc:
        LOG.debug("sync_location_state_npcs: %s", exc)
    finally:
        conn.close()
    return state


def enrich_party_from_sql(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    """ADR C6 — portrait, backstory, relationship for companions from npcs SQL."""
    party = list(state.get("party") or [])
    if len(party) <= 1:
        return
    pc_row = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    pc_id = int(pc_row["id"]) if pc_row else None
    for idx, member in enumerate(party):
        if idx == 0 or not isinstance(member, dict):
            continue
        code = str(member.get("npc_code") or member.get("code") or "").strip()
        name = str(member.get("name") or "").strip()
        npc = None
        if code:
            npc = conn.execute(
                "SELECT * FROM npcs WHERE code = ? AND status = 'alive' LIMIT 1",
                (code,),
            ).fetchone()
        if not npc and name:
            npc = conn.execute(
                "SELECT * FROM npcs WHERE name = ? COLLATE NOCASE AND status = 'alive' LIMIT 1",
                (name,),
            ).fetchone()
        if not npc:
            continue
        enriched = dict(member)
        enriched["npc_id"] = int(npc["id"])
        enriched["npc_code"] = str(npc["code"])
        if npc["portrait_path"]:
            enriched["portrait_file"] = npc["portrait_path"]
        if npc["backstory_summary"]:
            enriched["backstory_summary"] = npc["backstory_summary"]
        if npc["race"]:
            enriched["race"] = npc["race"]
        if npc["class_role"]:
            enriched["character_class"] = npc["class_role"]
        if pc_id:
            rel = conn.execute(
                """
                SELECT attitude, trust, summary FROM npc_relationships
                WHERE source_npc_id = ? AND target_type = 'player_character' AND target_id = ?
                LIMIT 1
                """,
                (int(npc["id"]), pc_id),
            ).fetchone()
            if rel:
                enriched["relationship"] = {
                    "attitude": rel["attitude"],
                    "trust": int(rel["trust"] or 0),
                    "summary": rel["summary"],
                }
        party[idx] = enriched
    state["party"] = party


def enrich_state_from_sql(db_path: str, state: dict[str, Any]) -> dict[str, Any]:
    """Merge SQL kanon into runtime state (quests, location npcs)."""
    if not db_path or not os.path.isfile(db_path):
        return state
    conn = _connect(db_path)
    try:
        quests = conn.execute(
            """
            SELECT id, title, description, status, rewards_json, quest_scale,
                   chain_code, chain_position, rewards_deferred
            FROM quests WHERE status = 'active' ORDER BY id
            """
        ).fetchall()
        if quests:
            from titan.fugassa import quest_templates

            active = []
            for r in quests:
                obj_rows = conn.execute(
                    """
                    SELECT description_text, status, optional, objective_type, completion_mode
                    FROM quest_objectives WHERE quest_id = ? ORDER BY sort_order
                    """,
                    (r["id"],),
                ).fetchall()
                rewards: dict[str, Any] = {}
                try:
                    rewards = json.loads(r["rewards_json"]) if r["rewards_json"] else {}
                except (TypeError, ValueError):
                    rewards = {}
                deferred = bool(r["rewards_deferred"])
                active.append(
                    {
                        "name": r["title"],
                        "objective": r["description"] or "",
                        "description": r["description"] or "",
                        "status": r["status"],
                        "scale": r["quest_scale"] or "standard",
                        "chain_code": r["chain_code"],
                        "chain_position": r["chain_position"],
                        "rewards_deferred": deferred,
                        "rewards_preview": quest_templates.rewards_preview(rewards, deferred=deferred),
                        "objectives": [
                            {
                                "text": o["description_text"] or "",
                                "status": o["status"],
                                "optional": bool(o["optional"]),
                                "objective_type": o["objective_type"] or "custom",
                                "completion_mode": o["completion_mode"] or "auto",
                                "hidden": (o["objective_type"] or "") == "fail_on_event_flag",
                            }
                            for o in obj_rows
                        ],
                    }
                )
            state["quests"] = {
                "active": active,
                "closed": state.get("quests", {}).get("closed", []) if isinstance(state.get("quests"), dict) else [],
            }
        elif isinstance(state.get("quests"), dict):
            state["quests"]["active"] = []

        loc_row = conn.execute(
            """
            SELECT l.id, l.name, l.description_short, l.image_path, l.notes, l.region_name, l.parent_location_id
            FROM locations l
            JOIN player_characters pc ON pc.current_location_id = l.id
            WHERE pc.code = 'pc_hero' LIMIT 1
            """
        ).fetchone()
        if loc_row:
            state["_current_location_id"] = loc_row["id"]
            loc = dict(state.get("location_state") or {})
            loc["location_id"] = loc_row["id"]
            loc["name"] = loc_row["name"]
            loc["description"] = loc_row["description_short"] or loc.get("description", "")
            if loc_row["image_path"]:
                loc["scene_asset"] = loc_row["image_path"]
            from titan.fugassa.location_name_registry import resolve_settlement_labels

            parent_region = None
            if loc_row["parent_location_id"]:
                prow = conn.execute(
                    "SELECT region_name FROM locations WHERE id = ?",
                    (int(loc_row["parent_location_id"]),),
                ).fetchone()
                if prow:
                    parent_region = prow["region_name"]
            loc.update(
                resolve_settlement_labels(
                    name=str(loc_row["name"] or ""),
                    region_name=loc_row["region_name"],
                    parent_location_id=int(loc_row["parent_location_id"]) if loc_row["parent_location_id"] else None,
                    parent_region_name=parent_region,
                )
            )
            _apply_location_npcs_to_state(
                conn,
                loc,
                location_id=int(loc_row["id"]),
                notes=loc_row["notes"],
            )
            state["location_state"] = loc

        from titan.fugassa.narrative_movement import enrich_location_context, sync_player_sublocation_anchor

        sync_player_sublocation_anchor(state)
        loc_id = int((state.get("location_state") or {}).get("location_id") or state.get("_current_location_id") or 0)
        if loc_id:
            state["location_state"] = enrich_location_context(db_path, state.get("location_state") or {}, location_id=loc_id)

        pc = conn.execute(
            """
            SELECT id, portrait_path, portrait_prompt, experience_points, level
            FROM player_characters WHERE code = 'pc_hero' LIMIT 1
            """
        ).fetchone()
        if pc:
            state["player_character_id"] = pc["id"]
            from titan.fugassa.player_portrait_prompt import resolve_player_portrait_prompt

            pos, neg = resolve_player_portrait_prompt(db_path, int(pc["id"]), state)
            if pos:
                state["portrait_prompt"] = pos
            if neg:
                state["portrait_negative_prompt"] = neg
            party = list(state.get("party") or [])
            if party and isinstance(party[0], dict):
                hero = dict(party[0])
                if pc["portrait_path"]:
                    hero["portrait_file"] = pc["portrait_path"]
                from titan.fugassa.dnd5e_options import xp_to_next_for_level

                hero_level = int(hero.get("level") or pc["level"] or 1)
                hero["xp"] = int(pc["experience_points"] or hero.get("xp") or 0)
                hero["xp_to_next"] = xp_to_next_for_level(hero_level)
                party[0] = hero
                state["party"] = party
            try:
                state = enrich_character_sheet_from_sql(conn, int(pc["id"]), state)
            except sqlite3.Error:
                pass
        from titan.fugassa.property_repository import attach_property_context_to_location, sync_property_portfolio
        from titan.fugassa.title_engine import sync_player_titles

        enrich_party_from_sql(conn, state)
        sync_property_portfolio(conn, state)
        if loc_id:
            attach_property_context_to_location(conn, state, location_id=loc_id)
        sync_player_titles(conn, state)
    except sqlite3.Error as exc:
        LOG.debug("enrich_state_from_sql: %s", exc)
    finally:
        conn.close()
    return state


def sync_from_state(
    db_path: str,
    state: dict[str, Any],
    *,
    turn_resolution: TurnResolution | None = None,
    turn_number: int | None = None,
) -> None:
    """Push runtime game.json fields into SQLite."""
    if not db_path:
        return
    party = state.get("party") or []
    hero = party[0] if party and isinstance(party[0], dict) else {}
    hero_name = str(hero.get("name") or "Hero")
    loc = state.get("location_state") or {}
    turn = turn_number if turn_number is not None else int(state.get("turn") or 0)
    player = state.get("player") or {}

    cs = state.get("character_sheet")
    if isinstance(cs, dict):
        vol = dict(cs.get("volatile_state") or {})
        vol["hp_current"] = int(hero.get("hp", vol.get("hp_current", 100)))
        cs["volatile_state"] = vol
        state["character_sheet"] = cs

    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE campaign_settings SET turn_number = ?, updated_at = ? WHERE id = 1",
            (turn, _utc_now()),
        )
        reality_mode = state.get("reality_mode")
        if reality_mode in ("simulation", "sandbox"):
            conn.execute(
                "UPDATE campaign_settings SET reality_mode = ?, updated_at = ? WHERE id = 1",
                (reality_mode, _utc_now()),
            )
        conn.execute(
            """
            UPDATE player_characters
            SET name = ?, hit_points_current = ?, hit_points_max = ?, armor_class = ?, updated_at = ?
            WHERE code = 'pc_hero'
            """,
            (hero_name, int(hero.get("hp", 100)), int(hero.get("max_hp", 100)), int(hero.get("ac", 12)), _utc_now()),
        )
        loc_id = _resolve_current_location(conn, player, loc)
        if loc_id:
            conn.execute(
                """
                UPDATE locations SET name = ?, description_short = ?, description_long = ?, is_discovered = 1, updated_at = ?
                WHERE id = ?
                """,
                (str(loc.get("name") or "Unknown"), str(loc.get("description") or ""), str(loc.get("description") or ""), _utc_now(), loc_id),
            )
            _sync_npcs_at_location(conn, loc_id, loc, db_path=db_path)

        _sync_quests(conn, state)
        _sync_inventory(conn, state, hero_name)

        if turn_resolution and turn_resolution.asset_requests:
            _enqueue_asset_requests(conn, turn_resolution.asset_requests)
        conn.commit()
    except sqlite3.Error as exc:
        LOG.warning("state sync failed: %s", exc)
    finally:
        conn.close()


def sync_location_only(db_path: str, state: dict[str, Any]) -> int | None:
    """Resolve + commit the player's current SQL location, without a full sync.

    Movement resolvers (grid_engine.travel_to / move_cardinal) only mutate the
    JSON `state["player"]` position; the full `sync_from_state` pass that used
    to be the only place `_resolve_current_location` ran happens *after* the
    quest engine in the chat pipeline. That left `visit_location` / `explore`
    objectives checking last turn's SQL location — a full turn behind the
    player's actual move. Call this right after a move, before quest
    evaluation, so the location-of-record is already correct this turn.
    Idempotent — safe to call again later via the full `sync_from_state`.
    """
    if not db_path or not os.path.isfile(db_path):
        return None
    player = state.get("player") or {}
    loc = state.get("location_state") or {}
    conn = _connect(db_path)
    try:
        loc_id = _resolve_current_location(conn, player, loc)
        conn.commit()
        return loc_id
    except sqlite3.Error as exc:
        LOG.warning("sync_location_only failed: %s", exc)
        return None
    finally:
        conn.close()


def _sync_npcs_at_location(
    conn: sqlite3.Connection,
    location_id: int,
    loc: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Keep NPCs present in scene text synced to DB; new ones get a full T2 spawn package (ADR §B4)."""
    names = [str(n).strip() for n in (loc.get("npcs") or []) if str(n).strip()]
    for name in names:
        spawn_name = name
        if db_path:
            from titan.fugassa import campaign_name_registry

            spawn_name = campaign_name_registry.prepare_npc_name(db_path, name)
        code = _slug(spawn_name, "npc")
        existing = conn.execute("SELECT id FROM npcs WHERE code = ?", (code,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE npcs SET name = ?, current_location_id = ?, updated_at = ? WHERE id = ?",
                (spawn_name, location_id, _utc_now(), existing["id"]),
            )
            if db_path:
                from titan.fugassa import campaign_name_registry

                campaign_name_registry.register_spawned_npc(
                    db_path, npc_id=int(existing["id"]), name=spawn_name
                )
        else:
            result = npc_generator.spawn_npc(
                conn, name=spawn_name, tier="T2", location_id=location_id, code=code
            )
            if db_path and result.get("npc_id"):
                from titan.fugassa import campaign_name_registry

                campaign_name_registry.register_spawned_npc(
                    db_path, npc_id=int(result["npc_id"]), name=spawn_name
                )


def _resolve_current_location(conn: sqlite3.Connection, player: dict[str, Any], loc: dict[str, Any]) -> int | None:
    """Map the player's current grid cell to a stable SQL location row.

    Movement itself is JSON-side (grid_engine caches biome-generated scenes per
    cell in game.json); this makes SQL the location-of-record — one `locations`
    row per visited cell, with `player_characters.current_location_id` actually
    following the player — so `visit_location`/`explore` quest objectives and
    NPC location tracking work beyond the bootstrap cell.
    """
    # ADR §A: a player standing inside the off-grid sublocation graph (entered
    # via a `grid_cell_portals.target_location_id` portal) has their SQL
    # location-of-record set directly — no grid cell lookup applies there.
    sublocation_id = player.get("sublocation_id")
    if sublocation_id:
        loc_id = int(sublocation_id)
        conn.execute(
            "UPDATE player_characters SET current_location_id = ?, updated_at = ? WHERE code = 'pc_hero'",
            (loc_id, _utc_now()),
        )
        return loc_id

    map_code = str(player.get("map_code") or "overworld")
    x, y, z = int(player.get("x", 0)), int(player.get("y", 0)), int(player.get("z", 0))
    cell = conn.execute(
        "SELECT id, location_id FROM grid_cells WHERE map_code = ? AND x = ? AND y = ? AND z = ?",
        (map_code, x, y, z),
    ).fetchone()

    if cell and cell["location_id"]:
        loc_id = int(cell["location_id"])
        conn.execute(
            "UPDATE grid_cells SET is_discovered = 1, updated_at = ? WHERE id = ?",
            (_utc_now(), cell["id"]),
        )
    else:
        code = f"grid_{map_code}_{x}_{y}_{z}"
        existing = conn.execute("SELECT id FROM locations WHERE code = ?", (code,)).fetchone()
        is_new = not existing
        if existing:
            loc_id = int(existing["id"])
        else:
            name = str(loc.get("name") or f"Cell ({x},{y},{z})")
            desc = str(loc.get("description") or "")
            now = _utc_now()
            conn.execute(
                """
                INSERT INTO locations (code, name, description_short, description_long, is_discovered, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (code, name, desc, desc, now, now),
            )
            loc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        if cell:
            conn.execute(
                "UPDATE grid_cells SET location_id = ?, is_discovered = 1, updated_at = ? WHERE id = ?",
                (loc_id, _utc_now(), cell["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO grid_cells (map_code, x, y, z, location_id, is_discovered, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (map_code, x, y, z, loc_id, _utc_now(), _utc_now()),
            )
        if is_new:
            # ADR §H8.1 wait_event hook: first physical visit to a cell is a
            # first-class engine event ("explore" objectives already read
            # locations.is_discovered directly; this flag lets quests also
            # chain off discovery of a *specific* location by code).
            world_flags.set_flag_conn(conn, f"location_discovered:{code}")

    conn.execute(
        "UPDATE player_characters SET current_location_id = ?, updated_at = ? WHERE code = 'pc_hero'",
        (loc_id, _utc_now()),
    )
    return loc_id


def _sync_quests(conn: sqlite3.Connection, state: dict[str, Any]) -> None:
    quests = state.get("quests") if isinstance(state.get("quests"), dict) else {}
    for q in quests.get("active") or []:
        if not isinstance(q, dict):
            continue
        title = str(q.get("name") or q.get("title") or "").strip()
        if not title:
            continue
        code = _slug(title, "quest")
        desc = str(q.get("objective") or q.get("description") or "")
        row = conn.execute("SELECT id FROM quests WHERE code = ?", (code,)).fetchone()
        if row:
            conn.execute(
                "UPDATE quests SET title = ?, description = ?, status = 'active', updated_at = ? WHERE id = ?",
                (title, desc, _utc_now(), row["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO quests (code, title, description, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (code, title, desc, _utc_now(), _utc_now()),
            )


def _sync_inventory(conn: sqlite3.Connection, state: dict[str, Any], hero_name: str) -> None:
    inv = state.get("inventory") if isinstance(state.get("inventory"), dict) else {}
    pc = conn.execute("SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1").fetchone()
    if not pc:
        return
    pc_id = int(pc["id"])
    for it in inv.get("shared") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        code = _slug(name, "item")
        qty = int(it.get("qty", 1))
        row = conn.execute("SELECT id FROM items WHERE code = ?", (code,)).fetchone()
        if row:
            conn.execute(
                "UPDATE items SET name = ?, quantity = ?, owner_type = 'player_character', owner_id = ?, updated_at = ? WHERE id = ?",
                (name, qty, pc_id, _utc_now(), row["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO items (code, name, item_type, quantity, owner_type, owner_id, stackable, created_at, updated_at)
                VALUES (?, ?, 'misc', ?, 'player_character', ?, 1, ?, ?)
                """,
                (code, name, qty, pc_id, _utc_now(), _utc_now()),
            )


def _enqueue_asset_requests(conn: sqlite3.Connection, requests: list[dict[str, Any]]) -> None:
    for req in requests:
        entity_type = str(req.get("entity_type") or "location")
        entity_id = int(req.get("entity_id") or 1)
        asset_type = str(req.get("asset_type") or "scene")
        code = f"{entity_type}:{entity_id}:{asset_type}:queued_{int(datetime.now().timestamp())}"
        try:
            conn.execute(
                """
                INSERT INTO assets (
                    code, asset_type, entity_type, entity_id, status, prompt_source,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', 'auto', ?, ?, ?)
                """,
                (code, asset_type, entity_type, entity_id, json.dumps(req, ensure_ascii=False), _utc_now(), _utc_now()),
            )
        except sqlite3.IntegrityError:
            pass


def export_json_snapshot(db_path: str, state: dict[str, Any], save_dir: str) -> None:
    from titan.fugassa.game_bootstrap import write_game_json
    from titan.fugassa.paths import generated_dir

    write_game_json(save_dir, state)
    if db_path and os.path.isfile(db_path):
        asset_repository.rebuild_manifest(db_path, generated_dir(save_dir))

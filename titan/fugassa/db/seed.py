"""Hydrate SQLite kanon from wizard draft + game.json state (M1)."""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.dnd5e_options import (
    effective_class,
    effective_race,
    effective_subclass,
)
from titan.fugassa import crafting_engine
from titan.fugassa.db import asset_repository
from titan.fugassa.sheet_persistence import apply_player_sheet, build_sheet_from_draft


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug_code(name: str, fallback: str = "entity") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:48] or fallback)


_SUBLOCATION_NAME_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


def _split_sublocation_name(name: str) -> tuple[str, bool]:
    """
    Detect the wizard opening's "Parent (Sub)" convention, e.g.
    "Oakhaven Reach (Lucas's quarters)" (see
    `game_bootstrap.starting_location_from_opening`). Returns
    `(parent_name, has_sublocation)` — `parent_name` is the outdoor/settlement
    name a grid cell can sensibly represent; the full original `name` string
    is kept as the sublocation's own display name (unchanged from today's
    behavior) when `has_sublocation` is True.
    """
    m = _SUBLOCATION_NAME_RE.match(str(name or "").strip())
    if not m:
        return str(name or "").strip(), False
    parent = m.group(1).strip()
    sub = m.group(2).strip()
    if not parent or not sub:
        return str(name or "").strip(), False
    return parent, True


# Fresh characters start locked out of crafting entirely without at least
# one known recipe (see crafting_engine module docstring). One class-relevant
# profession + the universal "artisan" fallback gives every build a
# reachable tier-0 recipe regardless of theme, without hardcoding a whole
# recipe catalog (recipes discovered later are genuine campaign content).
_CLASS_PROFESSION: dict[str, str] = {
    "fighter": "weaponsmith", "barbarian": "weaponsmith", "paladin": "weaponsmith", "ranger": "weaponsmith",
    "rogue": "weaponsmith",
    "cleric": "armorsmith", "monk": "armorsmith",
    "wizard": "enchanter", "sorcerer": "enchanter", "warlock": "enchanter", "bard": "enchanter",
    "druid": "alchemist",
    "artificer": "engineer",
}

_STARTER_RECIPES_BY_PROFESSION: dict[str, dict[str, Any]] = {
    "weaponsmith": {
        "output_item_name": "Sharpened Blade",
        "description": "A quick edge-sharpening that keeps a weapon reliable.",
        "ingredients": [{"item_name": "Whetstone", "qty": 1}],
    },
    "armorsmith": {
        "output_item_name": "Patched Armor",
        "description": "A field repair that closes small breaches in worn armor.",
        "ingredients": [{"item_name": "Leather Scrap", "qty": 1}],
    },
    "alchemist": {
        "output_item_name": "Minor Healing Draught",
        "description": "A bitter tonic that closes shallow wounds.",
        "ingredients": [{"item_name": "Herbs", "qty": 2}],
        "recipe_kind": "potion",
        "heal_amount": 5,
    },
    "enchanter": {
        "output_item_name": "Minor Ward Scroll",
        "description": "A single-use scroll that wards off a minor threat.",
        "ingredients": [{"item_name": "Parchment", "qty": 1}, {"item_name": "Ink", "qty": 1}],
        "recipe_kind": "scroll",
    },
    "engineer": {
        "output_item_name": "Jury-Rigged Tool",
        "description": "A improvised tool cobbled together from scrap.",
        "ingredients": [{"item_name": "Scrap Metal", "qty": 1}],
    },
    "artisan": {
        "output_item_name": "Mended Cloth",
        "description": "A patched-up garment, good as new.",
        "ingredients": [{"item_name": "Spare Cloth", "qty": 1}],
    },
}


def _profession_for_class(class_name: str) -> str:
    return _CLASS_PROFESSION.get(str(class_name or "").strip().lower(), "artisan")


def _seed_starter_blueprints(conn: sqlite3.Connection, hero_name: str, class_name: str) -> None:
    professions = {_profession_for_class(class_name), "artisan"}
    for profession in professions:
        recipe = _STARTER_RECIPES_BY_PROFESSION.get(profession)
        if not recipe:
            continue
        crafting_engine.grant_starter_blueprint(
            conn,
            hero_name,
            output_item_name=recipe["output_item_name"],
            profession=profession,
            description=recipe.get("description"),
            ingredients=recipe["ingredients"],
            recipe_kind=recipe.get("recipe_kind", "item"),
            heal_amount=recipe.get("heal_amount"),
        )


def bootstrap_from_wizard(
    db_path: str,
    *,
    draft: dict[str, Any],
    state: dict[str, Any],
    portrait_relative_path: str | None = None,
    portrait_prompt: str | None = None,
    portrait_negative_prompt: str | None = None,
) -> dict[str, Any]:
    """
    Seed players, player_characters, starter location, optional portrait asset.
    Idempotent per save: skips if hero row already exists.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    created_holding: dict[str, Any] | None = None
    try:
        existing = conn.execute(
            "SELECT id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        if existing:
            pc_id = int(existing["id"])
            result = {"player_character_id": pc_id, "skipped": True}
            if portrait_relative_path:
                asset_repository.register_portrait_file(
                    db_path,
                    player_character_id=pc_id,
                    player_code="pc_hero",
                    relative_file_path=portrait_relative_path,
                    prompt=portrait_prompt,
                    negative_prompt=portrait_negative_prompt,
                )
            elif portrait_prompt:
                conn.execute(
                    """
                    UPDATE player_characters
                    SET portrait_prompt = COALESCE(NULLIF(TRIM(portrait_prompt), ''), ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (portrait_prompt, _utc_now(), pc_id),
                )
                conn.commit()
            return result

        hero_name = str(draft.get("player_name") or "Hero").strip() or "Hero"
        lvl = max(1, int(draft.get("level") or 1))
        prof = 2 + int(math.floor(max(lvl - 1, 0) / 4))
        abilities = draft.get("abilities") if isinstance(draft.get("abilities"), dict) else {}
        loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
        loc_name = str(loc.get("name") or "Starter Crossroads")
        loc_desc = str(loc.get("description") or "")

        now = _utc_now()
        conn.execute(
            """
            INSERT INTO players (code, display_name, created_at, updated_at)
            VALUES ('player_1', ?, ?, ?)
            """,
            (hero_name, now, now),
        )
        player_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Wizard opening "Parent (Sub)" names (e.g. "Oakhaven Reach (Lucas's
        # quarters)") describe a character starting *inside* a room, not
        # standing in the open on the overworld grid — without this split the
        # sublocation graph (`locations.parent_location_id`,
        # `location_connections`, `player.sublocation_id`) that
        # `use_portal`/`leave_sublocation`/`move_sublocation` operate on was
        # never populated for a fresh save, so a game that started "inside"
        # was mechanically indistinguishable from standing outside on that
        # grid cell (wrong for the minimap, NPC/quest location scoping, and
        # location-scoped anti-duplication tracking like Investigate).
        parent_name, has_sublocation = _split_sublocation_name(loc_name)
        sublocation_id: int | None = None
        if has_sublocation:
            parent_code = _slug_code(parent_name, "starter_location")
            conn.execute(
                """
                INSERT INTO locations (
                    code, name, description_short, description_long, is_discovered,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (parent_code, parent_name, "", "", now, now),
            )
            parent_location_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            sub_code = _slug_code(f"{parent_name}_{loc_name}", "starter_sublocation")
            conn.execute(
                """
                INSERT INTO locations (
                    code, name, description_short, description_long, parent_location_id,
                    is_discovered, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (sub_code, loc_name, loc_desc, loc_desc, parent_location_id, now, now),
            )
            sublocation_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO location_connections (from_location_id, to_location_id, connection_type, label, created_at)
                VALUES (?, ?, 'contains', ?, ?)
                """,
                (parent_location_id, sublocation_id, loc_name, now),
            )
            # Grid cell represents the outdoor/settlement place; the PC's
            # actual current location is the specific room inside it.
            grid_location_id = parent_location_id
            location_id = sublocation_id
        else:
            loc_code = _slug_code(loc_name, "starter_location")
            conn.execute(
                """
                INSERT INTO locations (
                    code, name, description_short, description_long, is_discovered,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (loc_code, loc_name, loc_desc, loc_desc, now, now),
            )
            location_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            grid_location_id = location_id

        party = state.get("party") or []
        hero_state = party[0] if party else {}
        conn.execute(
            """
            INSERT INTO player_characters (
                code, player_id, name, race, class_name, subclass_name, background_name,
                level, proficiency_bonus,
                str_score, dex_score, con_score, int_score, wis_score, cha_score,
                armor_class, hit_points_current, hit_points_max,
                current_location_id, backstory_summary, status,
                created_at, updated_at
            ) VALUES (
                'pc_hero', ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, 'active',
                ?, ?
            )
            """,
            (
                player_id,
                hero_name,
                effective_race(draft),
                effective_class(draft),
                effective_subclass(draft) or None,
                str(draft.get("character_background") or "").strip() or None,
                lvl,
                prof,
                int(abilities.get("str", 10)),
                int(abilities.get("dex", 10)),
                int(abilities.get("con", 10)),
                int(abilities.get("int", 10)),
                int(abilities.get("wis", 10)),
                int(abilities.get("cha", 10)),
                int(hero_state.get("ac", 12)),
                int(hero_state.get("hp", 100)),
                int(hero_state.get("max_hp", 100)),
                location_id,
                str(draft.get("character_background") or "").strip() or None,
                now,
                now,
            ),
        )
        pc_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

        try:
            sheet, build_input = build_sheet_from_draft(draft)
            apply_player_sheet(conn, pc_id, sheet, build_input)
            sc = sheet.get("spellcasting") or {}
            conn.execute(
                """
                UPDATE player_characters SET
                    hit_points_current = ?, hit_points_max = ?,
                    armor_class = COALESCE(?, armor_class),
                    speed_walk = ?, passive_perception = ?,
                    spell_save_dc = ?, spell_attack_bonus = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(sheet.get("hp") or hero_state.get("hp", 10)),
                    int(sheet.get("hp") or hero_state.get("max_hp", 10)),
                    int(hero_state.get("ac")) if hero_state.get("ac") is not None else int(sheet.get("ac_base") or 10),
                    int(sheet.get("speed") or 30),
                    int(sheet.get("passive_perception") or 10),
                    int(sc.get("spell_save_dc") or 0) or None,
                    int(sc.get("spell_attack_mod") or 0) or None,
                    now,
                    pc_id,
                ),
            )
        except Exception:
            pass

        px = int((state.get("player") or {}).get("x", 0))
        py = int((state.get("player") or {}).get("y", 0))
        pz = int((state.get("player") or {}).get("z", 0))
        conn.execute(
            """
            INSERT INTO grid_cells (map_code, x, y, z, location_id, is_discovered, created_at, updated_at)
            VALUES ('overworld', ?, ?, ?, ?, 1, ?, ?)
            """,
            (px, py, pz, grid_location_id, now, now),
        )
        for npc_name in loc.get("npcs") or []:
            n = str(npc_name).strip()
            if not n:
                continue
            ncode = _slug_code(n, "npc")
            conn.execute(
                """
                INSERT OR IGNORE INTO npcs (code, name, current_location_id, is_important, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (ncode, n, location_id, now, now),
            )
        quests = state.get("quests") if isinstance(state.get("quests"), dict) else {}
        for q in quests.get("active") or []:
            if not isinstance(q, dict):
                continue
            title = str(q.get("name") or "").strip()
            if not title:
                continue
            qcode = _slug_code(title, "quest")
            conn.execute(
                """
                INSERT OR IGNORE INTO quests (code, title, description, status, related_location_id, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?)
                """,
                (qcode, title, str(q.get("objective") or ""), location_id, now, now),
            )
        _seed_starter_blueprints(conn, hero_name, effective_class(draft))
        proposal = draft.get("starting_property")
        if isinstance(proposal, dict) and proposal.get("granted"):
            from titan.fugassa.property_repository import create_holding_conn

            created_holding = create_holding_conn(conn, player_character_id=pc_id, proposal=proposal, acquired_at_turn=0)
        conn.commit()
    finally:
        conn.close()

    if created_holding:
        from titan.fugassa import campaign_chronicle

        reconn = sqlite3.connect(db_path)
        reconn.row_factory = sqlite3.Row
        try:
            loc_row = reconn.execute(
                "SELECT current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
            ).fetchone()
            loc_id = int(loc_row["current_location_id"]) if loc_row and loc_row["current_location_id"] else None
            event_id = campaign_chronicle.record_property_acquired_conn(
                reconn,
                db_path,
                created_holding,
                turn_id=0,
                location_id=loc_id,
                source="bootstrap",
            )
            reconn.commit()
        finally:
            reconn.close()
        if event_id:
            campaign_chronicle.index_event_log_ids(db_path, [event_id])

    asset: dict[str, Any] | None = None
    if portrait_relative_path:
        asset = asset_repository.register_portrait_file(
            db_path,
            player_character_id=pc_id,
            player_code="pc_hero",
            relative_file_path=portrait_relative_path,
            prompt=portrait_prompt,
            negative_prompt=portrait_negative_prompt,
        )

    return {
        "player_id": player_id,
        "player_character_id": pc_id,
        "location_id": location_id,
        "sublocation_id": sublocation_id,
        "grid_location_id": grid_location_id,
        "portrait_asset": asset,
    }

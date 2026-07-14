"""Item-use engine — ADR §6: item consumption is engine-owned, not GM chat.

Mirrors the fuzzy name-matching pattern used by combat_engine/social_engine
for target NPCs: the player's free-text action is matched against the item
names actually in their inventory, never inferred from prose alone.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import equipment_slots
from titan.fugassa.dnd5e_options import ability_modifier

LOG = logging.getLogger("titan.fugassa.item_engine")


class EquipError(Exception):
    def __init__(self, message: str, code: str = "invalid_equip") -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _find_item_by_name(conn: sqlite3.Connection, pc_id: int, text: str) -> sqlite3.Row | None:
    rows = conn.execute(
        "SELECT id, code, name, quantity FROM items WHERE owner_type = 'player_character' AND owner_id = ? AND quantity > 0",
        (pc_id,),
    ).fetchall()
    if not rows:
        return None
    hint = (text or "").lower()
    best: tuple[int, sqlite3.Row] | None = None
    for r in rows:
        name = (r["name"] or "").lower()
        if not name:
            continue
        if name in hint and (best is None or len(name) > best[0]):
            best = (len(name), r)
            continue
        for token in name.split():
            if len(token) >= 3 and token in hint and (best is None or len(token) > best[0]):
                best = (len(token), r)
    return best[1] if best else None


def _mirror_quantity_to_json(state: dict[str, Any], item_name: str, new_qty: int) -> None:
    inv = dict(state.get("inventory") or {})
    shared = list(inv.get("shared") or [])
    name_low = (item_name or "").strip().lower()
    updated = False
    for entry in shared:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip().lower() == name_low:
            entry["qty"] = new_qty
            updated = True
    if updated:
        shared = [e for e in shared if not (isinstance(e, dict) and int(e.get("qty", 0)) <= 0)]
        inv["shared"] = shared
        state["inventory"] = inv


def resolve_use_item(db_path: str | None, state: dict[str, Any], player_text: str) -> dict[str, Any]:
    """Consume one unit of a matched inventory item at the player's current location.

    Returns a `turn_resolution.inventory` payload consumed by quest_engine's
    `use_item_at` objective type: {used, item_id, item_code, item_name, location_id, summary}.
    """
    if not db_path or not os.path.isfile(db_path):
        return {"used": False, "summary": "No item data available."}

    conn = _connect(db_path)
    try:
        pc = conn.execute(
            "SELECT id, current_location_id FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
        ).fetchone()
        if not pc:
            return {"used": False, "summary": "No character data available."}

        item = _find_item_by_name(conn, int(pc["id"]), player_text)
        if not item:
            return {"used": False, "summary": "You don't have that item."}

        new_qty = max(0, int(item["quantity"]) - 1)
        conn.execute(
            "UPDATE items SET quantity = ?, updated_at = ? WHERE id = ?",
            (new_qty, _utc_now(), item["id"]),
        )
        conn.commit()
        # `state_repository.sync_from_state` re-syncs `items` FROM the JSON
        # inventory every turn (JSON is authoritative there) — without mirroring
        # this decrement back into `state["inventory"]["shared"]`, that later
        # sync silently reverts the SQL update we just made above.
        _mirror_quantity_to_json(state, item["name"], new_qty)
        loc_id = int(pc["current_location_id"]) if pc["current_location_id"] else None
        return {
            "used": True,
            "item_id": item["id"],
            "item_code": item["code"],
            "item_name": item["name"],
            "location_id": loc_id,
            "remaining_qty": max(0, new_qty),
            "summary": f"Used {item['name']} (remaining: {max(0, new_qty)}).",
        }
    finally:
        conn.close()


def _find_hero(state: dict[str, Any], hero_name: str) -> tuple[int, dict[str, Any]]:
    party = list(state.get("party") or [])
    for idx, member in enumerate(party):
        if isinstance(member, dict) and str(member.get("name") or "") == hero_name:
            return idx, member
    raise EquipError(f"Unknown party member: {hero_name}", "hero_not_found")


def _recompute_hero_derived_stats(state: dict[str, Any], hero_name: str) -> None:
    """Body armor is the only slot that affects AC; weapon_main is the only
    slot that affects damage_dice. Everything else (clothes/head/feet/
    hands/backpack/belt/weapon_off) is flavor/utility only, per the
    character-screen spec. Also mirrors weapon/armor names into
    `character_sheet.stable_sheet.inventory` so the GM prompt context
    (`gm_runner._gear_loadout_summary`) stays in sync with what's equipped.
    """
    try:
        idx, hero = _find_hero(state, hero_name)
    except EquipError:
        return
    hero = dict(hero)
    equipped = (((state.get("inventory") or {}).get("equipped") or {}).get(hero_name)) or {}

    sheet_abilities = (((state.get("character_sheet") or {}).get("stable_sheet") or {}).get("abilities")) or {}
    dex_mod = ability_modifier(int(sheet_abilities.get("dexterity", 10)))
    body = equipped.get("body")
    ac_bonus = equipment_slots.extract_ac_bonus(body)
    hero["ac"] = ac_bonus if ac_bonus is not None else 10 + dex_mod

    weapon_main = equipped.get("weapon_main")
    dice = equipment_slots.extract_damage_dice(weapon_main)
    hero["damage_dice"] = dice or "1d8"

    party = list(state.get("party") or [])
    party[idx] = hero
    state["party"] = party

    cs = dict(state.get("character_sheet") or {})
    stable = dict(cs.get("stable_sheet") or {})
    inv_sheet = dict(stable.get("inventory") or {})
    inv_sheet["weapon"] = (weapon_main or {}).get("name") if isinstance(weapon_main, dict) else (weapon_main or inv_sheet.get("weapon", ""))
    inv_sheet["armor"] = (body or {}).get("name") if isinstance(body, dict) else (body or inv_sheet.get("armor", ""))
    stable["inventory"] = inv_sheet
    cs["stable_sheet"] = stable
    state["character_sheet"] = cs


def equip_item(state: dict[str, Any], hero_name: str, item_name: str, slot: str) -> dict[str, Any]:
    """Move one unit of `item_name` from `inventory.shared` into
    `inventory.equipped[hero_name][slot]`, rejecting the move if the item's
    inferred category doesn't fit that slot (e.g. a potion into `body`).
    Whatever previously occupied the slot goes back to `shared`.
    """
    if slot not in equipment_slots.SLOTS:
        raise EquipError(f"Unknown equipment slot: {slot}", "invalid_slot")
    _find_hero(state, hero_name)  # raises if hero doesn't exist

    inv = dict(state.get("inventory") or {})
    shared = list(inv.get("shared") or [])
    name_low = item_name.strip().lower()
    idx = next(
        (i for i, it in enumerate(shared) if isinstance(it, dict) and str(it.get("name") or "").strip().lower() == name_low),
        None,
    )
    if idx is None:
        raise EquipError(f"Item not found in inventory: {item_name}", "item_not_found")
    item = dict(shared[idx])
    if not equipment_slots.slot_accepts(slot, item):
        raise EquipError(f"{item.get('name')} cannot be equipped in the {slot} slot", "slot_mismatch")

    qty = int(item.get("qty", 1) or 1)
    if qty > 1:
        shared[idx] = {**item, "qty": qty - 1}
    else:
        shared.pop(idx)

    equipped_all = dict(inv.get("equipped") or {})
    hero_equipped = dict(equipped_all.get(hero_name) or {})
    previous = hero_equipped.get(slot)
    # Keep the full item snapshot (minus qty) — not just name/description —
    # so ac/damage/armor_type/weapon_type stay available for
    # _recompute_hero_derived_stats after the item leaves `shared`.
    equipped_snapshot = {k: v for k, v in item.items() if k != "qty"}
    hero_equipped[slot] = equipped_snapshot
    equipped_all[hero_name] = hero_equipped
    inv["equipped"] = equipped_all

    if previous:
        prev_entry = previous if isinstance(previous, dict) else {"name": str(previous), "qty": 1}
        prev_name_low = str(prev_entry.get("name") or "").strip().lower()
        existing = next(
            (e for e in shared if isinstance(e, dict) and str(e.get("name") or "").strip().lower() == prev_name_low),
            None,
        )
        if existing:
            existing["qty"] = int(existing.get("qty", 1) or 1) + 1
        else:
            shared.append({**prev_entry, "qty": 1})

    inv["shared"] = shared
    state["inventory"] = inv
    _recompute_hero_derived_stats(state, hero_name)
    return state


def unequip_item(state: dict[str, Any], hero_name: str, slot: str) -> dict[str, Any]:
    """Move whatever occupies `slot` back into `inventory.shared`."""
    if slot not in equipment_slots.SLOTS:
        raise EquipError(f"Unknown equipment slot: {slot}", "invalid_slot")
    _find_hero(state, hero_name)

    inv = dict(state.get("inventory") or {})
    equipped_all = dict(inv.get("equipped") or {})
    hero_equipped = dict(equipped_all.get(hero_name) or {})
    item = hero_equipped.pop(slot, None)
    if item is None:
        raise EquipError(f"Nothing equipped in the {slot} slot", "slot_empty")
    equipped_all[hero_name] = hero_equipped
    inv["equipped"] = equipped_all

    entry = item if isinstance(item, dict) else {"name": str(item), "qty": 1}
    entry.setdefault("qty", 1)
    shared = list(inv.get("shared") or [])
    name_low = str(entry.get("name") or "").strip().lower()
    existing = next(
        (e for e in shared if isinstance(e, dict) and str(e.get("name") or "").strip().lower() == name_low),
        None,
    )
    if existing:
        existing["qty"] = int(existing.get("qty", 1) or 1) + int(entry.get("qty", 1) or 1)
    else:
        shared.append(entry)
    inv["shared"] = shared
    state["inventory"] = inv
    _recompute_hero_derived_stats(state, hero_name)
    return state

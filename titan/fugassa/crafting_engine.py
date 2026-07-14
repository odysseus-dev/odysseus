"""Crafting — professions/rank ladder + hierarchical blueprint recipes.

Rank (Novice..Grandmaster) is a hard prerequisite gate on
`crafting_recipes.min_rank`, never just a DC/roll modifier — a Novice
cannot produce a Grandmaster item even on a natural 20. Recipes are
campaign content the player discovers at play time (invented from scratch,
reverse-engineered from an owned item, or handed to them as a starter/found
blueprint) — not a hardcoded catalog (see `schema.sql`'s `crafting_recipes`
table comment).

Hierarchical crafting (small parts -> spaceships/portals) needs no special
recursive-execution logic: a recipe's ingredients are just item names/qty
(matched against `state["inventory"]["shared"]`, mirroring `item_engine`'s
shared-list convention); some ingredients happen to be items with their own
recipe, so the player must craft/acquire those first like any ingredient.
"""

from __future__ import annotations

import random
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.dnd5e_options import ability_modifier

RANK_NAMES = ["Novice", "Apprentice", "Journeyman", "Expert", "Master", "Grandmaster"]
MAX_RANK = len(RANK_NAMES) - 1

PROFESSIONS = ("weaponsmith", "armorsmith", "alchemist", "enchanter", "engineer", "artisan")
RECIPE_KINDS = ("item", "scroll", "potion")

# Cumulative XP required to *reach* rank i (mirrors the shape of
# dnd5e_options.XP_THRESHOLDS_BY_LEVEL) — deliberately steep so grinding
# trivial tier-0 recipes can't fast-track a Grandmaster.
RANK_XP_THRESHOLDS = [0, 100, 300, 700, 1500, 3000]

# Suggested defaults by tier (0..5) — a recipe stores its own values, these
# are only used when creating a *new* recipe (invent/reverse-engineer) and
# nothing more specific was supplied.
CRAFT_DC_BY_TIER = [8, 12, 15, 18, 22, 27]
DURATION_MINUTES_BY_TIER = [30, 60, 120, 240, 480, 960]
INVENT_DC_PREMIUM = 5  # designing from scratch is harder than executing a known recipe
REVERSE_ENGINEER_DC_DISCOUNT = 3  # studying a real example is easier than inventing blind


class CraftingError(Exception):
    def __init__(self, message: str, code: str = "invalid_craft") -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _slug_code(name: str, fallback: str = "recipe") -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return (base[:48] or fallback)


def rank_name(rank: int) -> str:
    return RANK_NAMES[max(0, min(int(rank), MAX_RANK))]


def clamp_tier(tier: int) -> int:
    return max(0, min(int(tier), MAX_RANK))


# ---------------------------------------------------------------------------
# Profession rank/XP
# ---------------------------------------------------------------------------


def get_profession_progress(conn: sqlite3.Connection, hero_name: str, profession: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT rank, xp FROM crafting_professions WHERE hero_name = ? AND profession = ?",
        (hero_name, profession),
    ).fetchone()
    rank = int(row["rank"]) if row else 0
    xp = int(row["xp"]) if row else 0
    return {"profession": profession, "rank": rank, "rank_name": rank_name(rank), "xp": xp}


def list_profession_progress(conn: sqlite3.Connection, hero_name: str) -> list[dict[str, Any]]:
    return [get_profession_progress(conn, hero_name, p) for p in PROFESSIONS]


def _rank_for_xp(xp: int) -> int:
    rank = 0
    for r in range(1, len(RANK_XP_THRESHOLDS)):
        if xp >= RANK_XP_THRESHOLDS[r]:
            rank = r
    return rank


def _award_profession_xp(conn: sqlite3.Connection, hero_name: str, profession: str, *, tier: int, success: bool) -> dict[str, Any]:
    progress = get_profession_progress(conn, hero_name, profession)
    gained = max(1, clamp_tier(tier)) * (10 if success else 2)
    new_xp = progress["xp"] + gained
    new_rank = _rank_for_xp(new_xp)
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO crafting_professions (hero_name, profession, rank, xp, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(hero_name, profession) DO UPDATE SET
            rank = excluded.rank, xp = excluded.xp, updated_at = excluded.updated_at
        """,
        (hero_name, profession, new_rank, new_xp, now),
    )
    return {
        "profession": profession,
        "xp_gained": gained,
        "xp": new_xp,
        "old_rank": progress["rank"],
        "new_rank": new_rank,
        "rank_up": new_rank > progress["rank"],
    }


# ---------------------------------------------------------------------------
# Recipe lookup helpers
# ---------------------------------------------------------------------------


def get_recipe(conn: sqlite3.Connection, recipe_code: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM crafting_recipes WHERE code = ?", (recipe_code,)).fetchone()
    if not row:
        return None
    recipe = dict(row)
    ingredient_rows = conn.execute(
        "SELECT item_name, qty FROM recipe_ingredients WHERE recipe_id = ? ORDER BY id", (recipe["id"],)
    ).fetchall()
    recipe["ingredients"] = [{"item_name": r["item_name"], "qty": int(r["qty"])} for r in ingredient_rows]
    return recipe


def get_professions_for_hero(db_path: str, hero_name: str) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        return list_profession_progress(conn, hero_name)
    finally:
        conn.close()


def get_blueprints_for_hero(db_path: str, state: dict[str, Any], hero_name: str) -> list[dict[str, Any]]:
    """Known recipes with an ingredient have/need diff against the hero's
    current inventory, for the crafting UI's blueprint list."""
    conn = _connect(db_path)
    try:
        blueprints = list_known_blueprints(conn, hero_name)
    finally:
        conn.close()
    for recipe in blueprints:
        recipe["ingredients_status"] = check_ingredients(state, recipe["ingredients"])
        recipe["can_afford"] = all(i["have_enough"] for i in recipe["ingredients_status"])
    return blueprints


def list_known_blueprints(conn: sqlite3.Connection, hero_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT cr.*, pb.source AS blueprint_source
        FROM player_blueprints pb
        JOIN crafting_recipes cr ON cr.id = pb.recipe_id
        WHERE pb.hero_name = ?
        ORDER BY cr.profession, cr.tier, cr.output_item_name
        """,
        (hero_name,),
    ).fetchall()
    out = []
    for row in rows:
        recipe = dict(row)
        ingredient_rows = conn.execute(
            "SELECT item_name, qty FROM recipe_ingredients WHERE recipe_id = ? ORDER BY id", (recipe["id"],)
        ).fetchall()
        recipe["ingredients"] = [{"item_name": r["item_name"], "qty": int(r["qty"])} for r in ingredient_rows]
        out.append(recipe)
    return out


# ---------------------------------------------------------------------------
# JSON inventory helpers (state["inventory"]["shared"] — same list item_engine
# equip/unequip already read/write; SQL `items` is re-derived from this every
# turn by `state_repository.sync_from_state`, so crafting mutates JSON only).
# ---------------------------------------------------------------------------


def _shared_list(state: dict[str, Any]) -> list[dict[str, Any]]:
    inv = state.get("inventory") or {}
    return list(inv.get("shared") or [])


def _set_shared_list(state: dict[str, Any], shared: list[dict[str, Any]]) -> None:
    inv = dict(state.get("inventory") or {})
    inv["shared"] = shared
    state["inventory"] = inv


def _find_shared_idx(shared: list[dict[str, Any]], name: str) -> int | None:
    name_low = str(name or "").strip().lower()
    for i, it in enumerate(shared):
        if isinstance(it, dict) and str(it.get("name") or "").strip().lower() == name_low:
            return i
    return None


def check_ingredients(state: dict[str, Any], ingredients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Have/need diff for the crafting UI — does not mutate anything."""
    shared = _shared_list(state)
    out = []
    for ing in ingredients:
        idx = _find_shared_idx(shared, ing["item_name"])
        have = int(shared[idx].get("qty", 1)) if idx is not None else 0
        needed = int(ing["qty"])
        out.append({"item_name": ing["item_name"], "qty_needed": needed, "qty_have": have, "have_enough": have >= needed})
    return out


def _consume_ingredients(state: dict[str, Any], ingredients: list[dict[str, Any]]) -> None:
    diff = check_ingredients(state, ingredients)
    missing = [d for d in diff if not d["have_enough"]]
    if missing:
        names = ", ".join(f"{d['item_name']} ({d['qty_have']}/{d['qty_needed']})" for d in missing)
        raise CraftingError(f"Missing ingredients: {names}", "missing_ingredients")

    shared = _shared_list(state)
    for ing in ingredients:
        idx = _find_shared_idx(shared, ing["item_name"])
        item = dict(shared[idx])
        remaining = int(item.get("qty", 1)) - int(ing["qty"])
        if remaining > 0:
            item["qty"] = remaining
            shared[idx] = item
        else:
            shared.pop(idx)
    _set_shared_list(state, shared)


def _add_shared_item(
    state: dict[str, Any],
    name: str,
    qty: int,
    *,
    description: str | None = None,
    heal_amount: int | None = None,
) -> None:
    shared = _shared_list(state)
    idx = _find_shared_idx(shared, name)
    if idx is not None:
        item = dict(shared[idx])
        item["qty"] = int(item.get("qty", 1)) + int(qty)
        if description and not item.get("description"):
            item["description"] = description
        if heal_amount is not None:
            item["heal_amount"] = heal_amount
        shared[idx] = item
    else:
        entry: dict[str, Any] = {"name": name, "qty": int(qty)}
        if description:
            entry["description"] = description
        if heal_amount is not None:
            entry["heal_amount"] = heal_amount
        shared.append(entry)
    _set_shared_list(state, shared)


def _remove_one_shared_item(state: dict[str, Any], name: str) -> dict[str, Any]:
    shared = _shared_list(state)
    idx = _find_shared_idx(shared, name)
    if idx is None:
        raise CraftingError(f"Item not found in inventory: {name}", "item_not_found")
    item = dict(shared[idx])
    remaining = int(item.get("qty", 1)) - 1
    if remaining > 0:
        item["qty"] = remaining
        shared[idx] = item
    else:
        shared.pop(idx)
    _set_shared_list(state, shared)
    return item


def _player_int_and_proficiency(conn: sqlite3.Connection) -> tuple[int, int]:
    """INT modifier + proficiency bonus straight from the sheet — mirrors
    `combat_engine._player_attack_profile`'s STR/DEX pattern for craft checks.
    """
    row = conn.execute(
        "SELECT int_score, proficiency_bonus FROM player_characters WHERE code = 'pc_hero' LIMIT 1"
    ).fetchone()
    if not row:
        return 0, 2
    int_mod = ability_modifier(int(row["int_score"] or 10))
    prof = int(row["proficiency_bonus"] or 2)
    return int_mod, prof


# ---------------------------------------------------------------------------
# Craft an already-known recipe
# ---------------------------------------------------------------------------


def craft_item(db_path: str, state: dict[str, Any], hero_name: str, recipe_code: str) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        recipe = get_recipe(conn, recipe_code)
        if not recipe:
            raise CraftingError(f"Unknown recipe: {recipe_code}", "recipe_not_found")

        bp = conn.execute(
            "SELECT id FROM player_blueprints WHERE hero_name = ? AND recipe_id = ?",
            (hero_name, recipe["id"]),
        ).fetchone()
        if not bp:
            raise CraftingError(
                f"You don't know the blueprint for {recipe['output_item_name']}.", "blueprint_unknown"
            )

        progress = get_profession_progress(conn, hero_name, recipe["profession"])
        if progress["rank"] < recipe["min_rank"]:
            raise CraftingError(
                f"A {progress['rank_name']} {recipe['profession']} cannot craft a "
                f"{rank_name(recipe['min_rank'])}-tier item, even on a critical roll. "
                f"Craft lower-tier recipes first to advance.",
                "rank_too_low",
            )

        # Ingredients are spent on the attempt itself — crafting risk is real,
        # a failed attempt still wastes the materials (unlike simply "buying" a
        # guaranteed result with a DC roll bolted on for flavor).
        _consume_ingredients(state, recipe["ingredients"])

        int_mod, prof_bonus = _player_int_and_proficiency(conn)
        roll = random.randint(1, 20)
        total = roll + int_mod + prof_bonus
        dc = int(recipe["craft_dc"])
        success = total >= dc
        critical = roll == 20
        fumble = roll == 1

        output_qty = int(recipe["output_qty"])
        if success:
            if critical:
                output_qty += 1
            _add_shared_item(
                state,
                recipe["output_item_name"],
                output_qty,
                description=recipe.get("description"),
                heal_amount=recipe.get("heal_amount"),
            )

        award = _award_profession_xp(conn, hero_name, recipe["profession"], tier=recipe["tier"], success=success)
        conn.commit()

        outcome = "success" if success else "failure"
        crit_note = " (critical!)" if critical else " (fumble)" if fumble else ""
        summary = (
            f"Craft {recipe['output_item_name']}: d20={roll}+{int_mod}(INT)+{prof_bonus}(prof)={total} "
            f"vs DC {dc}: {outcome}{crit_note}."
        )
        if award["rank_up"]:
            summary += f" {hero_name}'s {recipe['profession']} rank rose to {rank_name(award['new_rank'])}!"

        return {
            "success": success,
            "roll": roll,
            "int_mod": int_mod,
            "proficiency_bonus": prof_bonus,
            "total": total,
            "dc": dc,
            "critical": critical,
            "fumble": fumble,
            "output_item_name": recipe["output_item_name"] if success else None,
            "output_qty": output_qty if success else 0,
            "profession": recipe["profession"],
            "recipe_code": recipe["code"],
            "rank_up": award["rank_up"],
            "new_rank": award["new_rank"],
            "new_rank_name": rank_name(award["new_rank"]),
            "xp_gained": award["xp_gained"],
            "summary": summary,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Discovering new recipes: invent from scratch / reverse-engineer an item
# ---------------------------------------------------------------------------


def _insert_recipe(
    conn: sqlite3.Connection,
    *,
    output_item_name: str,
    recipe_kind: str,
    profession: str,
    tier: int,
    craft_dc: int,
    duration_minutes: int,
    ingredients: list[dict[str, Any]],
    description: str | None,
    heal_amount: int | None,
    discovered_by: str,
) -> dict[str, Any]:
    tier = clamp_tier(tier)
    now = _utc_now()
    # A timestamp-only suffix collides whenever two recipes for the same
    # item name are inserted within the same wall-clock second (e.g. the
    # bootstrap starter blueprint and a test/GM re-seeding the same tier-0
    # recipe) — a fixed-width random suffix, appended *after* truncating the
    # name slug (not before), guarantees `code` stays unique regardless of
    # how long `output_item_name` is.
    name_slug = _slug_code(output_item_name, "recipe")[:30]
    code = f"{name_slug}_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO crafting_recipes (
            code, output_item_name, output_qty, recipe_kind, profession, tier, min_rank,
            craft_dc, duration_minutes, heal_amount, description, discovered_by, created_at, updated_at
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code, output_item_name, recipe_kind, profession, tier, tier,
            craft_dc, duration_minutes, heal_amount, description, discovered_by, now, now,
        ),
    )
    recipe_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    for ing in ingredients:
        conn.execute(
            "INSERT INTO recipe_ingredients (recipe_id, item_name, qty) VALUES (?, ?, ?)",
            (recipe_id, str(ing["item_name"]).strip(), max(1, int(ing.get("qty", 1)))),
        )
    return {"id": recipe_id, "code": code}


def _grant_blueprint(conn: sqlite3.Connection, hero_name: str, recipe_id: int, source: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO player_blueprints (hero_name, recipe_id, source, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (hero_name, recipe_id, source, _utc_now()),
    )


_RARITY_TIER = {
    "common": 0, "ordinary": 0,
    "uncommon": 1,
    "rare": 2,
    "very rare": 3, "very_rare": 3,
    "legendary": 4,
    "artifact": 5, "mythic": 5,
}


def infer_tier_from_item(item: dict[str, Any]) -> int:
    rarity = str(item.get("rarity") or "").strip().lower()
    if rarity in _RARITY_TIER:
        return _RARITY_TIER[rarity]
    return 1


def _default_draft_ingredients(output_item_name: str, tier: int) -> list[dict[str, Any]]:
    """Deterministic fallback ingredient list used when no LLM draft is
    available (e.g. LLM disabled) — a plain scaling material list, not
    flavorful, but keeps the feature usable without an LLM round-trip.
    """
    tier = clamp_tier(tier)
    qty = max(1, tier + 1)
    return [{"item_name": f"Crafting materials for {output_item_name}", "qty": qty}]


async def _draft_recipe_via_llm(
    *,
    theme: str,
    profession: str,
    tier: int,
    description: str,
    reference_item: dict[str, Any] | None,
    owner: str | None,
) -> dict[str, Any]:
    """Ask the LLM to flesh out a plausible output name/description/ingredient
    list grounded in the requested tier/profession/theme — flavor only. The
    engine (not the LLM) enforces the rank gate, DC roll, and persistence.
    Falls back to a plain deterministic draft if the LLM is unavailable.
    """
    from titan.fugassa import llm_client, wizard_json as wj

    kind_hint = "an existing item to reverse-engineer" if reference_item else "a new invention"
    ref_text = (
        f"\nReference item being studied: {reference_item.get('name')} — "
        f"{reference_item.get('description', '')}"
        if reference_item
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You design a crafting blueprint for a tabletop RPG. Return valid JSON only: "
                '{"output_item_name":"...","description":"...","ingredients":'
                '[{"item_name":"...","qty":1}]}. 2-5 ingredients, scaled to the requested tier '
                "(0=trivial, 5=legendary/spaceship/portal-grade). No prose outside the JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Theme: {theme}\nCraft profession: {profession}\nTarget tier: {tier}\n"
                f"This is {kind_hint}.{ref_text}\nWhat the crafter wants: {description}"
            ),
        },
    ]
    try:
        raw = await llm_client.chat_completion(messages, owner=owner, max_tokens=800, temperature=0.8)
        data = wj.parse_wizard_json_object(raw)
        if isinstance(data, dict) and data.get("output_item_name") and isinstance(data.get("ingredients"), list):
            return {
                "output_item_name": str(data["output_item_name"]).strip(),
                "description": str(data.get("description") or "").strip(),
                "ingredients": [
                    {"item_name": str(i.get("item_name") or "").strip(), "qty": max(1, int(i.get("qty", 1) or 1))}
                    for i in data["ingredients"]
                    if isinstance(i, dict) and str(i.get("item_name") or "").strip()
                ],
            }
    except Exception:
        pass

    fallback_name = str(description or reference_item and reference_item.get("name") or "Crafted Item").strip()
    return {
        "output_item_name": fallback_name,
        "description": "",
        "ingredients": _default_draft_ingredients(fallback_name, tier),
    }


async def invent_blueprint(
    db_path: str,
    state: dict[str, Any],
    hero_name: str,
    *,
    profession: str,
    tier: int,
    description: str,
    theme: str = "",
    owner: str | None = None,
) -> dict[str, Any]:
    if profession not in PROFESSIONS:
        raise CraftingError(f"Unknown profession: {profession}", "invalid_profession")
    tier = clamp_tier(tier)

    conn = _connect(db_path)
    try:
        progress = get_profession_progress(conn, hero_name, profession)
        if progress["rank"] < tier:
            raise CraftingError(
                f"A {progress['rank_name']} {profession} cannot invent a {rank_name(tier)}-tier blueprint, "
                "even on a critical roll. Advance your rank by crafting lower-tier recipes first.",
                "rank_too_low",
            )

        draft = await _draft_recipe_via_llm(
            theme=theme, profession=profession, tier=tier, description=description,
            reference_item=None, owner=owner,
        )

        int_mod, prof_bonus = _player_int_and_proficiency(conn)
        roll = random.randint(1, 20)
        total = roll + int_mod + prof_bonus
        dc = CRAFT_DC_BY_TIER[tier] + INVENT_DC_PREMIUM
        success = total >= dc

        result: dict[str, Any] = {
            "action": "invent",
            "success": success,
            "roll": roll,
            "total": total,
            "dc": dc,
            "profession": profession,
            "tier": tier,
            "output_item_name": draft["output_item_name"],
        }
        if not success:
            conn.commit()
            result["summary"] = (
                f"Invent {draft['output_item_name']}: d20={roll}+{int_mod}(INT)+{prof_bonus}(prof)={total} "
                f"vs DC {dc}: failure. No blueprint gained (no materials were spent)."
            )
            return result

        recipe = _insert_recipe(
            conn,
            output_item_name=draft["output_item_name"],
            recipe_kind="item",
            profession=profession,
            tier=tier,
            craft_dc=CRAFT_DC_BY_TIER[tier],
            duration_minutes=DURATION_MINUTES_BY_TIER[tier],
            ingredients=draft["ingredients"],
            description=draft.get("description"),
            heal_amount=None,
            discovered_by=hero_name,
        )
        _grant_blueprint(conn, hero_name, recipe["id"], "invented")
        conn.commit()

        result.update(
            {
                "recipe_code": recipe["code"],
                "ingredients": draft["ingredients"],
                "summary": (
                    f"Invent {draft['output_item_name']}: d20={roll}+{int_mod}(INT)+{prof_bonus}(prof)={total} "
                    f"vs DC {dc}: success! Blueprint learned."
                ),
            }
        )
        return result
    finally:
        conn.close()


async def reverse_engineer(
    db_path: str,
    state: dict[str, Any],
    hero_name: str,
    *,
    profession: str,
    item_name: str,
    theme: str = "",
    owner: str | None = None,
) -> dict[str, Any]:
    if profession not in PROFESSIONS:
        raise CraftingError(f"Unknown profession: {profession}", "invalid_profession")

    shared = _shared_list(state)
    idx = _find_shared_idx(shared, item_name)
    if idx is None:
        raise CraftingError(f"You don't have {item_name} to study.", "item_not_found")
    reference_item = dict(shared[idx])
    tier = infer_tier_from_item(reference_item)

    conn = _connect(db_path)
    try:
        progress = get_profession_progress(conn, hero_name, profession)
        if progress["rank"] < tier:
            raise CraftingError(
                f"A {progress['rank_name']} {profession} cannot reverse-engineer a {rank_name(tier)}-tier item, "
                "even on a critical roll. Advance your rank by crafting lower-tier recipes first.",
                "rank_too_low",
            )

        # Studying the item consumes it, win or lose — you took it apart either way.
        _remove_one_shared_item(state, item_name)

        draft = await _draft_recipe_via_llm(
            theme=theme, profession=profession, tier=tier, description=f"reverse-engineer {reference_item.get('name')}",
            reference_item=reference_item, owner=owner,
        )
        draft["output_item_name"] = reference_item.get("name") or draft["output_item_name"]

        int_mod, prof_bonus = _player_int_and_proficiency(conn)
        roll = random.randint(1, 20)
        total = roll + int_mod + prof_bonus
        dc = max(5, CRAFT_DC_BY_TIER[tier] - REVERSE_ENGINEER_DC_DISCOUNT)
        success = total >= dc

        result: dict[str, Any] = {
            "action": "reverse_engineer",
            "success": success,
            "roll": roll,
            "total": total,
            "dc": dc,
            "profession": profession,
            "tier": tier,
            "output_item_name": draft["output_item_name"],
            "item_consumed": reference_item.get("name"),
        }
        if not success:
            conn.commit()
            result["summary"] = (
                f"Reverse-engineer {draft['output_item_name']}: d20={roll}+{int_mod}(INT)+{prof_bonus}(prof)={total} "
                f"vs DC {dc}: failure. The item is destroyed and no blueprint was learned."
            )
            return result

        recipe = _insert_recipe(
            conn,
            output_item_name=draft["output_item_name"],
            recipe_kind="item",
            profession=profession,
            tier=tier,
            craft_dc=CRAFT_DC_BY_TIER[tier],
            duration_minutes=DURATION_MINUTES_BY_TIER[tier],
            ingredients=draft["ingredients"],
            description=draft.get("description"),
            heal_amount=None,
            discovered_by=hero_name,
        )
        _grant_blueprint(conn, hero_name, recipe["id"], "reverse_engineered")
        conn.commit()

        result.update(
            {
                "recipe_code": recipe["code"],
                "ingredients": draft["ingredients"],
                "summary": (
                    f"Reverse-engineer {draft['output_item_name']}: d20={roll}+{int_mod}(INT)+{prof_bonus}(prof)={total} "
                    f"vs DC {dc}: success! Blueprint learned."
                ),
            }
        )
        return result
    finally:
        conn.close()


def grant_starter_blueprint(
    conn: sqlite3.Connection,
    hero_name: str,
    *,
    output_item_name: str,
    profession: str,
    description: str | None,
    ingredients: list[dict[str, Any]],
    recipe_kind: str = "item",
    heal_amount: int | None = None,
) -> dict[str, Any]:
    """Seed one tier-0 recipe the hero already knows (see
    `game_bootstrap`) — without this, a fresh character has zero known
    blueprints and is locked out of crafting entirely.
    """
    recipe = _insert_recipe(
        conn,
        output_item_name=output_item_name,
        recipe_kind=recipe_kind,
        profession=profession,
        tier=0,
        craft_dc=CRAFT_DC_BY_TIER[0],
        duration_minutes=DURATION_MINUTES_BY_TIER[0],
        ingredients=ingredients,
        description=description,
        heal_amount=heal_amount,
        discovered_by=hero_name,
    )
    _grant_blueprint(conn, hero_name, recipe["id"], "starter")
    return recipe

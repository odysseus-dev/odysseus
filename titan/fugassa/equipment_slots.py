"""Lightweight, tag/name-based equipment slot classification.

There is no formal per-item stat schema yet — items are still mostly
free-text (name/description/tags/usage; see `wizard_engine`'s
INVENTORY_EXCLUDE_WEAPON_ARMOR and `game_bootstrap.apply_wizard_draft`) —
so slot compatibility is inferred from keywords/tags/explicit gear fields
rather than a strict item-type enum. This is good enough to stop obviously
wrong drops (a potion into the body-armor slot) without requiring a full
item-data model migration.

Character silhouette slots (per ADR-lite UI spec):
    head, body, clothes, feet, hands, backpack, weapon_main, weapon_off, belt
Only `body` contributes AC — everything else is flavor/utility.
"""

from __future__ import annotations

import re
from typing import Any

SLOTS: tuple[str, ...] = (
    "head", "body", "clothes", "feet", "hands", "backpack", "weapon_main", "weapon_off", "belt",
)

# Slot -> accepted item "category". weapon_main/weapon_off both accept the
# same "weapon" category (either hand can hold a weapon).
_SLOT_CATEGORY: dict[str, str] = {
    "head": "head",
    "body": "body",
    "clothes": "clothes",
    "feet": "feet",
    "hands": "hands",
    "backpack": "backpack",
    "belt": "belt",
    "weapon_main": "weapon",
    "weapon_off": "weapon",
}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "head": ("helmet", "helm", "hood", "cap", "hat", "mask", "circlet", "crown", "headgear"),
    "body": ("armor", "armour", "vest", "mail", "plate", "cuirass", "chestplate", "breastplate", "jack"),
    "clothes": ("shirt", "tunic", "robe", "cloak", "coat", "dress", "cape", "garment", "jacket"),
    "feet": ("boots", "boot", "shoes", "sandals", "greaves", "footwear"),
    "hands": ("gloves", "glove", "gauntlets", "gauntlet", "bracers", "mitts", "handwear"),
    "backpack": ("backpack", "satchel", "rucksack", "pack"),
    "belt": ("belt", "sash", "girdle", "bandolier"),
    "weapon": (
        "sword", "blade", "axe", "mace", "bow", "gun", "pistol", "rifle", "dagger",
        "staff", "wand", "spear", "hammer", "knife", "rapier", "crossbow", "cannon", "club",
    ),
}


def classify_item_category(item: dict[str, Any]) -> str | None:
    """Best-effort slot CATEGORY for a free-text inventory item, or None if
    it isn't equippable at all (consumables, quest items, generic tools…).
    Explicit hints (an item carrying its own `slot`, or gear-tab fields like
    `weapon_type`/`damage`/`armor_type`/`ac`) always win over name-sniffing.
    """
    if not isinstance(item, dict):
        return None
    explicit = str(item.get("slot") or "").strip().lower()
    if explicit in _SLOT_CATEGORY:
        return _SLOT_CATEGORY[explicit]
    if explicit in _KEYWORDS:
        return explicit
    if item.get("weapon_type") or item.get("damage") or item.get("attack_bonus"):
        return "weapon"
    if item.get("armor_type") or item.get("ac") or item.get("defense"):
        return "body"
    haystack = " ".join(str(item.get(k) or "") for k in ("name", "description", "usage"))
    haystack += " " + " ".join(str(t) for t in (item.get("tags") or []))
    haystack = haystack.lower()
    # Plain substring match (not \b-bounded): item names are frequently
    # compound words with no word boundary before the telling suffix
    # ("Longsword", "Warhammer", "Backpack"), so a strict \bsword\b would
    # miss them.
    for category, words in _KEYWORDS.items():
        if any(w in haystack for w in words):
            return category
    return None


def slot_accepts(slot: str, item: dict[str, Any]) -> bool:
    """Whether `item` is a valid fit for `slot` (e.g. a potion never fits
    the `body` slot, a sword fits either `weapon_main` or `weapon_off`)."""
    category = _SLOT_CATEGORY.get(slot)
    if not category:
        return False
    return classify_item_category(item) == category


def extract_ac_bonus(item: dict[str, Any] | None) -> int | None:
    """Numeric AC out of an item's explicit ac/defense/armor_class field
    (string or number), else scraped from its description text (e.g.
    "...AC 15..." / "defense: 12"). None if nothing usable is found.
    """
    if not isinstance(item, dict):
        return None
    for key in ("ac", "defense", "armor_class"):
        raw = item.get(key)
        if raw is not None:
            m = re.match(r"\s*(\d+)", str(raw))
            if m:
                return int(m.group(1))
    text = str(item.get("description") or "")
    m = re.search(r"\b(?:AC|armou?r(?:\s*class)?|defense)\s*[:\-]?\s*(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def extract_damage_dice(item: dict[str, Any] | None) -> str | None:
    """Leading dice notation (e.g. "1d6+2") out of an item's explicit
    `damage` field or its description text, tolerant of trailing prose
    like "1d6+2 piercing" (see game_bootstrap.apply_wizard_draft for the
    same leniency applied to the wizard's Gear tab output)."""
    if not isinstance(item, dict):
        return None
    raw = str(item.get("damage") or item.get("description") or "")
    m = re.search(r"\b(\d+\s*d\s*\d+(?:\s*[+-]\s*\d+)?)\b", raw, re.IGNORECASE)
    return m.group(1).replace(" ", "") if m else None

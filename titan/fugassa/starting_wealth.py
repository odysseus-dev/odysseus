"""Background-driven starting currency at chargen (not opening currency names)."""

from __future__ import annotations

from typing import Any

# Higher tier → more / higher-denomination currency. First matching tier wins
# when scanning upward (destitute keywords can coexist with noble in prose —
# take the highest signal).
_WEALTH_KEYWORDS: list[tuple[int, tuple[str, ...]]] = [
    (
        4,
        (
            "noble",
            "aristocrat",
            "lord",
            "lady",
            "baron",
            "baroness",
            "duke",
            "duchess",
            "count",
            "countess",
            "knight",
            "royal",
            "princess",
            "prince",
            "wealthy",
            "rich",
            "heir",
            "dynasty",
            "courtier",
            "gentry",
        ),
    ),
    (
        3,
        (
            "merchant",
            "trader",
            "shopkeeper",
            "guildmaster",
            "officer",
            "captain",
            "landowner",
            "proprietor",
        ),
    ),
    (
        2,
        (
            "soldier",
            "guard",
            "artisan",
            "apprentice",
            "acolyte",
            "hermit",
            "sailor",
            "farmer",
            "blacksmith",
            "scholar",
            "craftsman",
        ),
    ),
    (
        1,
        (
            "wanderer",
            "drifter",
            "vagabond",
            "urchin",
            "peasant",
            "laborer",
            "dockworker",
            "thief",
            "orphan",
            "commoner",
        ),
    ),
    (
        0,
        (
            "beggar",
            "destitute",
            "penniless",
            "broke",
            "outcast",
            "slave",
            "fugitive",
        ),
    ),
]


def wealth_tier_for_background(background: str) -> int:
    """0 = destitute … 4 = wealthy/noble. Empty background → modest wanderer (1)."""
    bg = str(background or "").lower()
    if not bg.strip():
        return 1
    best = 1
    for tier, words in _WEALTH_KEYWORDS:
        if any(word in bg for word in words):
            best = max(best, tier)
    return best


def starting_currency_grants(
    background: str,
    currency: list[str],
    *,
    level: int = 1,
) -> list[dict[str, Any]]:
    """Return [{name, qty}, …] using the campaign's three currency tiers."""
    tiers = [str(c).strip() for c in (currency or []) if str(c).strip()]
    if not tiers:
        tiers = ["bronze", "silver", "gold"]
    low = tiers[0]
    mid = tiers[1] if len(tiers) > 1 else tiers[0]
    high = tiers[-1]

    wealth = wealth_tier_for_background(background)
    lvl = max(1, int(level or 1))
    scale = 1.0 + min(lvl - 1, 9) * 0.05

    grants: list[dict[str, Any]] = []
    if wealth >= 4:
        grants.append({"name": high, "qty": max(5, int(round(15 * scale)))})
        grants.append({"name": mid, "qty": max(2, int(round(8 * scale)))})
    elif wealth == 3:
        grants.append({"name": mid, "qty": max(5, int(round(12 * scale)))})
        grants.append({"name": low, "qty": max(10, int(round(25 * scale)))})
    elif wealth == 2:
        grants.append({"name": low, "qty": max(15, int(round(30 * scale)))})
        grants.append({"name": mid, "qty": max(2, int(round(5 * scale)))})
    elif wealth == 1:
        grants.append({"name": low, "qty": max(8, int(round(10 * scale)))})
    else:
        grants.append({"name": low, "qty": max(2, int(round(5 * scale)))})
    return grants


def _shared_has_currency(shared: list[Any], currency_names: set[str]) -> bool:
    for item in shared:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip().lower() in currency_names:
            return True
    return False


def apply_starting_currency(
    inventory: dict[str, Any],
    *,
    background: str,
    currency: list[str],
    level: int = 1,
) -> dict[str, Any]:
    """Append tiered starting coins unless the wizard inventory already has any."""
    inv = dict(inventory or {})
    shared = list(inv.get("shared") or [])
    currency_names = {str(c).strip().lower() for c in (currency or []) if str(c).strip()}
    if not currency_names:
        currency_names = {"bronze", "silver", "gold"}
    if _shared_has_currency(shared, currency_names):
        return inv

    grants = starting_currency_grants(background, list(currency or []), level=level)
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in shared:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        by_key[key] = dict(item)
        order.append(key)

    for grant in grants:
        name = str(grant["name"])
        qty = int(grant["qty"])
        key = name.lower()
        if key in by_key:
            by_key[key]["qty"] = int(by_key[key].get("qty") or 0) + qty
        else:
            by_key[key] = {"name": name, "qty": qty}
            order.append(key)

    inv["shared"] = [by_key[k] for k in order]
    return inv

"""Engine-owned currency — ADR: inventory quantities are never archivist/GM prose.

Wizard chain (chargen):
  draft.currency → game_bootstrap.apply_wizard_draft → world_profile.currency
  → starting_wealth.apply_starting_currency → inventory.shared tier rows.
Runtime grants/spends use this module so JSON stays authoritative.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from titan.fugassa.game_bootstrap import default_currency_for_theme

LOG = logging.getLogger("titan.fugassa.currency_engine")

_GENERIC_CURRENCY_NAMES = frozenset(
    {
        "gold",
        "silver",
        "bronze",
        "copper",
        "coin",
        "coins",
        "credits",
        "scrip",
        "guilders",
        "sovereigns",
        "certificates",
        "bills",
        "data chips",
        "reactor cores",
    }
)

_BRIBE_RE = re.compile(
    r"\b(bribe|pay off|slip (?:him|her|them|the)?|offer|hand over|give)\b.{0,40}\b(\d+)\b",
    re.I,
)
_AMOUNT_TIER_RE = re.compile(
    r"\b(\d+)\s+(" + "|".join(re.escape(n) for n in sorted(_GENERIC_CURRENCY_NAMES, key=len, reverse=True)) + r")\b",
    re.I,
)


def normalize_tier_list(raw: Any) -> list[str]:
    """Coerce wizard/pause payloads into ≤3 non-empty tier names."""
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("→", ",").split(",") if p.strip()]
        return parts[:3]
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()][:3]
    return []


def tier_keys(tiers: list[str]) -> set[str]:
    return {t.lower() for t in tiers if t}


def is_currency_item_name(name: str, tiers: list[str]) -> bool:
    key = str(name or "").strip().lower()
    return bool(key and key in tier_keys(tiers))


def infer_tiers_from_holdings(state: dict[str, Any]) -> list[str]:
    """When `world_profile.currency` is missing, infer from shared inventory rows."""
    shared = (state.get("inventory") or {}).get("shared") or []
    found: list[str] = []
    seen: set[str] = set()
    for item in shared:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if not key or key in seen:
            continue
        hay = name.lower()
        if any(
            tok in hay
            for tok in (
                "sword",
                "armor",
                "potion",
                "dagger",
                "mail",
                "boot",
                "cloak",
                "ring",
                "wand",
                "scroll",
                "compass",
            )
        ):
            continue
        if key in _GENERIC_CURRENCY_NAMES:
            found.append(name)
            seen.add(key)
        if len(found) >= 3:
            break
    return found[:3]


def ensure_currency_profile(state: dict[str, Any], *, repair: bool = True) -> list[str]:
    """Guarantee `world_profile.currency` is a 3-tier list; optionally persist repair."""
    wp = dict(state.get("world_profile") or {})
    tiers = normalize_tier_list(wp.get("currency"))
    if not tiers and repair:
        tiers = infer_tiers_from_holdings(state)
    if not tiers and repair:
        theme = str(wp.get("theme") or state.get("theme") or "Fantasy")
        tiers = default_currency_for_theme(theme)
    if repair and tiers:
        wp["currency"] = tiers
        state["world_profile"] = wp
    return tiers


def _canonical_tier_name(tiers: list[str], name: str) -> str:
    key = str(name or "").strip().lower()
    for tier in tiers:
        if tier.lower() == key:
            return tier
    return str(name or "").strip()


def resolve_tier_name(tiers: list[str], hint: str | None = None, *, prefer_high: bool = False) -> str:
    return _resolve_tier_name(tiers, hint, prefer_high=prefer_high)


def _resolve_tier_name(tiers: list[str], hint: str | None = None, *, prefer_high: bool = False) -> str:
    if not tiers:
        return str(hint or "gold").strip() or "gold"
    if hint:
        canon = _canonical_tier_name(tiers, hint)
        if canon.lower() in tier_keys(tiers):
            return canon
    return tiers[-1] if prefer_high else tiers[0]


def adjust_currency(
    state: dict[str, Any],
    amount: int,
    *,
    tier_name: str | None = None,
    prefer_high_tier: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """Add (positive) or spend (negative) currency in `inventory.shared`. Returns delta record."""
    tiers = ensure_currency_profile(state)
    if not tiers:
        return {"applied": 0, "tier": tier_name or "gold", "reason": reason, "error": "no_tiers"}

    delta = int(amount)
    if delta == 0:
        return {"applied": 0, "tier": tier_name or tiers[-1], "reason": reason}

    name = _resolve_tier_name(tiers, tier_name, prefer_high=prefer_high_tier or delta < 0)
    inv = dict(state.get("inventory") or {})
    shared = list(inv.get("shared") or [])
    key = name.lower()
    applied = 0
    new_qty = 0

    if delta > 0:
        merged = False
        for entry in shared:
            if isinstance(entry, dict) and str(entry.get("name") or "").strip().lower() == key:
                new_qty = max(0, int(entry.get("qty") or 0)) + delta
                entry["qty"] = new_qty
                entry["name"] = _canonical_tier_name(tiers, entry.get("name") or name)
                applied = delta
                merged = True
                break
        if not merged:
            new_qty = delta
            shared.append({"name": _canonical_tier_name(tiers, name), "qty": new_qty})
            applied = delta
    else:
        spend = abs(delta)
        for i, entry in enumerate(shared):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "").strip().lower() != key:
                continue
            have = max(0, int(entry.get("qty") or 0))
            take = min(have, spend)
            new_qty = have - take
            applied = -take
            if new_qty > 0:
                entry["qty"] = new_qty
            else:
                shared.pop(i)
            break

    shared = [e for e in shared if not (isinstance(e, dict) and int(e.get("qty") or 0) <= 0)]
    inv["shared"] = shared
    state["inventory"] = inv

    record = {
        "applied": applied,
        "tier": _canonical_tier_name(tiers, name),
        "balance": new_qty,
        "reason": reason,
    }
    if applied:
        LOG.debug("currency delta %s", record)
    return record


def spend_currency(state: dict[str, Any], amount: int, *, tier_name: str | None = None, reason: str = "") -> int:
    """Spend up to `amount` from the high tier by default. Returns amount actually spent."""
    if amount <= 0:
        return 0
    record = adjust_currency(
        state,
        -amount,
        tier_name=tier_name,
        prefer_high_tier=True,
        reason=reason,
    )
    return abs(int(record.get("applied") or 0))


def grant_currency(
    state: dict[str, Any],
    amount: int,
    *,
    tier_name: str | None = None,
    prefer_high_tier: bool = True,
    reason: str = "",
) -> int:
    if amount <= 0:
        return 0
    record = adjust_currency(
        state,
        amount,
        tier_name=tier_name,
        prefer_high_tier=prefer_high_tier,
        reason=reason,
    )
    return int(record.get("applied") or 0)


def parse_bribe_from_text(player_text: str, state: dict[str, Any]) -> dict[str, Any] | None:
    """Detect explicit bribe amounts in player chat — engine applies spend before GM."""
    text = str(player_text or "")
    if not _BRIBE_RE.search(text):
        return None
    tiers = ensure_currency_profile(state)
    m = _AMOUNT_TIER_RE.search(text)
    amount = int(m.group(1)) if m else 0
    tier_hint = m.group(2) if m else None
    if amount <= 0:
        amt_m = re.search(r"\b(\d+)\b", text)
        amount = int(amt_m.group(1)) if amt_m else 0
    if amount <= 0:
        return None
    if tier_hint:
        tier_hint = _canonical_tier_name(tiers, tier_hint)
    record = adjust_currency(
        state,
        -amount,
        tier_name=tier_hint,
        prefer_high_tier=not tier_hint,
        reason="social_bribe",
    )
    spent = abs(int(record.get("applied") or 0))
    if not spent:
        return {
            "requested": amount,
            "spent": 0,
            "tier": record.get("tier"),
            "summary": f"Bribe of {amount} {record.get('tier')} failed — insufficient funds.",
        }
    return {
        "requested": amount,
        "spent": spent,
        "tier": record.get("tier"),
        "summary": f"Paid {spent} {record.get('tier')} as a bribe.",
    }


def resolution_currency_summary(deltas: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for d in deltas:
        applied = int(d.get("applied") or 0)
        if not applied:
            continue
        sign = "+" if applied > 0 else ""
        parts.append(f"{sign}{applied} {d.get('tier', 'currency')}")
    return ", ".join(parts) if parts else ""


def apply_resolution_currency(state: dict[str, Any], resolution: Any) -> list[dict[str, Any]]:
    """Collect currency deltas already applied this turn into `resolution.currency`."""
    deltas: list[dict[str, Any]] = list(getattr(resolution, "currency", None) or [])
    if isinstance(resolution, dict):
        deltas = list(resolution.get("currency") or [])
    summary = resolution_currency_summary(deltas)
    if summary:
        if hasattr(resolution, "currency"):
            resolution.currency = deltas  # type: ignore[attr-defined]
            if hasattr(resolution, "binding_summary") and not resolution.binding_summary:
                resolution.binding_summary = f"currency: {summary}"  # type: ignore[attr-defined]
        elif isinstance(resolution, dict):
            resolution["currency"] = deltas
    return deltas


def record_currency_delta(resolution: Any, record: dict[str, Any]) -> None:
    if not record or not int(record.get("applied") or 0):
        return
    if hasattr(resolution, "currency"):
        resolution.currency = list(getattr(resolution, "currency") or []) + [record]  # type: ignore[attr-defined]
    elif isinstance(resolution, dict):
        resolution.setdefault("currency", []).append(record)

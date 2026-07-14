"""Wallet + backpack display helpers — mirrors static/js/fugassa/gameplay/inventoryDisplay.js."""

from __future__ import annotations

from typing import Any

from titan.fugassa import currency_engine


def currency_tier_names(state: dict[str, Any], *, repair: bool = False) -> list[str]:
    if repair:
        return currency_engine.ensure_currency_profile(state, repair=True)
    raw = (state.get("world_profile") or {}).get("currency")
    tiers = currency_engine.normalize_tier_list(raw)
    if tiers:
        return tiers
    return currency_engine.infer_tiers_from_holdings(state)


def _tier_keys(tiers: list[str]) -> set[str]:
    return {t.lower() for t in tiers if t}


def wallet_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    tiers = currency_tier_names(state)
    if not tiers:
        return []

    qty_by_key: dict[str, dict[str, Any]] = {}
    for item in (state.get("inventory") or {}).get("shared") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if key not in _tier_keys(tiers):
            continue
        canonical = next((t for t in tiers if t.lower() == key), name)
        qty_by_key[key] = {"name": canonical, "qty": max(0, int(item.get("qty") or 0))}

    return [qty_by_key.get(t.lower(), {"name": t, "qty": 0}) for t in tiers]


def backpack_gear_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    tiers = currency_tier_names(state)
    tier_keys = _tier_keys(tiers)
    out: list[dict[str, Any]] = []
    for item in (state.get("inventory") or {}).get("shared") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or "").strip().lower()
        if key and key not in tier_keys:
            out.append(dict(item))
    return out

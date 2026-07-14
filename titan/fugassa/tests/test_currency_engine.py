"""Tests for currency_engine — grants, spends, tier repair, bribes."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import currency_engine


def _state(**overrides):
    base = {
        "world_profile": {"theme": "Fantasy", "currency": ["scrip", "guilders", "sovereigns"]},
        "inventory": {"shared": [{"name": "sovereigns", "qty": 14}, {"name": "guilders", "qty": 8}], "equipped": {}},
    }
    base.update(overrides)
    return base


def test_adjust_currency_merges_existing_tier():
    state = _state()
    record = currency_engine.adjust_currency(state, 6, tier_name="sovereigns", reason="test")
    assert record["applied"] == 6
    wallet = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert wallet["sovereigns"] == 20


def test_spend_currency_caps_at_balance():
    state = _state()
    spent = currency_engine.spend_currency(state, 50, tier_name="sovereigns", reason="test")
    assert spent == 14
    wallet = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert "sovereigns" not in wallet


def test_ensure_currency_profile_infers_from_holdings():
    state = {
        "world_profile": {"theme": "Fantasy"},
        "inventory": {"shared": [{"name": "guilders", "qty": 3}, {"name": "Longsword", "qty": 1}]},
    }
    tiers = currency_engine.ensure_currency_profile(state, repair=True)
    assert "guilders" in tiers
    assert state["world_profile"]["currency"]


def test_parse_bribe_spends_high_tier():
    state = _state()
    bribe = currency_engine.parse_bribe_from_text("I bribe the guard with 5 sovereigns", state)
    assert bribe
    assert bribe["spent"] == 5
    wallet = {i["name"]: i["qty"] for i in state["inventory"]["shared"]}
    assert wallet["sovereigns"] == 9


def test_normalize_tier_list_accepts_string():
    assert currency_engine.normalize_tier_list("scrip, guilders, sovereigns") == [
        "scrip",
        "guilders",
        "sovereigns",
    ]

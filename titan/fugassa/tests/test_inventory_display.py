"""Tests for inventory_display wallet resolution."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import inventory_display as inv_disp


def test_wallet_from_state_reads_quantities_from_shared():
    state = {
        "world_profile": {"currency": ["scrip", "guilders", "sovereigns"]},
        "inventory": {
            "shared": [
                {"name": "sovereigns", "qty": 15},
                {"name": "guilders", "qty": 8},
                {"name": "Arcane Compass", "qty": 1},
            ],
        },
    }
    wallet = inv_disp.wallet_from_state(state)
    assert wallet == [
        {"name": "scrip", "qty": 0},
        {"name": "guilders", "qty": 8},
        {"name": "sovereigns", "qty": 15},
    ]


def test_backpack_gear_excludes_currency_tiers():
    state = {
        "world_profile": {"currency": ["bronze", "silver", "gold"]},
        "inventory": {
            "shared": [
                {"name": "gold", "qty": 5},
                {"name": "Healing Potion", "qty": 2},
            ],
        },
    }
    gear = inv_disp.backpack_gear_from_state(state)
    assert len(gear) == 1
    assert gear[0]["name"] == "Healing Potion"

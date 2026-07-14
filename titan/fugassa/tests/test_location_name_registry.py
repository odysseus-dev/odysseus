"""Tests for settlement / place name registry."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.location_name_registry import (
    name_collision,
    register_settlement,
    resolve_settlement_labels,
    seed_registry_from_locations,
    settlement_from_location_name,
)
from titan.fugassa.db import sqlite_store


def test_settlement_from_dash_name():
    assert settlement_from_location_name("Crownstone — Market District") == "Crownstone"
    assert resolve_settlement_labels(name="Market District", region_name="Crownstone")["place_label"] == "Crownstone · Market District"


def test_place_registry_blocks_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "game.db")
        sqlite_store.init_game_db(db_path, "Places", theme="fantasy")
        register_settlement(db_path, name="Crownstone", location_id=1, kind="city")
        registry = seed_registry_from_locations(db_path, persist=False)
        assert name_collision(registry, "Crownstone")
        assert not name_collision(registry, "Ashford")

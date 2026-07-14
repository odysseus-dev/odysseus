"""Integration tests: selective loot pickup via game_session.pickup_loot
against a real save — leaves unselected entries behind, clamps requested
quantities to what's actually available, and preserves legacy "pick up
everything" behavior when no selection is supplied.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import game_session
from titan.fugassa import save_store


@pytest.fixture
def save_id():
    world_name = f"LootSessionTest_{os.getpid()}_{id(object())}"
    draft = {"world_name": world_name, "theme_mode": "Fantasy", "player_name": "Lucas", "level": 1}
    sid = save_store.normalize_save_name(world_name)
    save_store.create_save_from_wizard(draft)
    yield sid
    try:
        save_store.delete_save(sid)
    except Exception:
        pass


def _seed_loot(save_id_: str, loot: list) -> None:
    state = game_session.load_game_state(save_id_)
    loc = dict(state.get("location_state") or {})
    loc["loot"] = loot
    state["location_state"] = loc
    game_session.save_game_state(save_id_, state)


def test_selective_pickup_leaves_unselected_loot_behind(save_id):
    _seed_loot(
        save_id,
        [
            {"name": "Rusty Sword", "qty": 1},
            {"name": "Gold Coins", "qty": 10},
            {"name": "Healing Potion", "qty": 2},
        ],
    )

    result = game_session.pickup_loot(save_id, [{"name": "Gold Coins", "qty": 10}])

    picked_names = {p["name"] for p in result["picked"]}
    assert picked_names == {"Gold Coins"}

    remaining_names = {item["name"] for item in result["state"]["location_state"]["loot"]}
    assert remaining_names == {"Rusty Sword", "Healing Potion"}

    shared_names = {item["name"] for item in result["state"]["inventory"]["shared"]}
    assert "Gold Coins" in shared_names


def test_partial_quantity_pickup_leaves_remainder_at_location(save_id):
    _seed_loot(save_id, [{"name": "Gold Coins", "qty": 10}])

    result = game_session.pickup_loot(save_id, [{"name": "Gold Coins", "qty": 4}])

    remaining = next(item for item in result["state"]["location_state"]["loot"] if item["name"] == "Gold Coins")
    assert remaining["qty"] == 6
    picked_item = next(p for p in result["picked"] if p["name"] == "Gold Coins")
    assert picked_item["qty"] == 4
    shared_item = next(i for i in result["state"]["inventory"]["shared"] if i["name"] == "Gold Coins")
    assert shared_item["qty"] == 4


def test_requested_quantity_is_clamped_to_available_quantity(save_id):
    _seed_loot(save_id, [{"name": "Gold Coins", "qty": 3}])

    result = game_session.pickup_loot(save_id, [{"name": "Gold Coins", "qty": 999}])

    assert "Gold Coins" not in {item["name"] for item in result["state"]["location_state"]["loot"]}
    picked_item = next(p for p in result["picked"] if p["name"] == "Gold Coins")
    assert picked_item["qty"] == 3


def test_empty_selection_falls_back_to_legacy_pick_up_everything(save_id):
    _seed_loot(save_id, [{"name": "Rusty Sword", "qty": 1}, {"name": "Gold Coins", "qty": 5}])

    result = game_session.pickup_loot(save_id, [])

    assert result["state"]["location_state"]["loot"] == []
    picked_names = {p["name"] for p in result["picked"]}
    assert picked_names == {"Rusty Sword", "Gold Coins"}


def test_legacy_string_loot_entries_are_normalized(save_id):
    _seed_loot(save_id, ["Old Boots"])

    result = game_session.pickup_loot(save_id, [{"name": "Old Boots", "qty": 1}])

    picked_names = {p["name"] for p in result["picked"]}
    assert picked_names == {"Old Boots"}
    shared_names = {item["name"] for item in result["state"]["inventory"]["shared"]}
    assert "Old Boots" in shared_names


def test_no_loot_at_location_returns_nothing_to_pick_up_message(save_id):
    _seed_loot(save_id, [])

    result = game_session.pickup_loot(save_id, [{"name": "Anything", "qty": 1}])
    assert result["message"] == "Nothing to pick up."
    assert result["picked"] == []


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-v"]))

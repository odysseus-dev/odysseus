"""A fresh character must never be locked out of crafting entirely — the
wizard bootstrap seeds one class-relevant profession's tier-0 blueprint plus
the universal "artisan" fallback (see db/seed.py's `_seed_starter_blueprints`).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import crafting_engine as ce
from titan.fugassa.db import seed as db_seed
from titan.fugassa.db import sqlite_store


def make_db_path() -> str:
    d = tempfile.mkdtemp(prefix="fugassa_seedcraft_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Seed Craft Campaign", theme="fantasy")
    return db_path


def _base_state() -> dict:
    return {
        "player": {"x": 0, "y": 0, "z": 0},
        "location_state": {"name": "Starter Crossroads", "description": "", "npcs": []},
        "party": [{"hp": 20, "max_hp": 20, "ac": 12}],
        "quests": {"active": []},
    }


def test_fighter_gets_weaponsmith_and_artisan_blueprints():
    db_path = make_db_path()
    draft = {"player_name": "Lucas", "level": 1, "abilities": {}, "player_class_idx": 5}  # Fighter (see dnd5e_options.CLASS_CHOICES)
    db_seed.bootstrap_from_wizard(db_path, draft=draft, state=_base_state())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    blueprints = ce.list_known_blueprints(conn, "Lucas")
    conn.close()

    professions = {bp["profession"] for bp in blueprints}
    assert "weaponsmith" in professions
    assert "artisan" in professions
    weaponsmith_recipe = next(bp for bp in blueprints if bp["profession"] == "weaponsmith")
    assert weaponsmith_recipe["output_item_name"] == "Sharpened Blade"
    assert weaponsmith_recipe["tier"] == 0
    assert weaponsmith_recipe["min_rank"] == 0


def test_wizard_gets_enchanter_and_artisan_blueprints():
    db_path = make_db_path()
    from titan.fugassa import dnd5e_options as opt

    wizard_idx = opt.CLASS_CHOICES.index("Wizard")
    draft = {"player_name": "Elowen", "level": 1, "abilities": {}, "player_class_idx": wizard_idx}
    db_seed.bootstrap_from_wizard(db_path, draft=draft, state=_base_state())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    blueprints = ce.list_known_blueprints(conn, "Elowen")
    conn.close()

    professions = {bp["profession"] for bp in blueprints}
    assert "enchanter" in professions
    assert "artisan" in professions


def test_seeded_blueprint_is_immediately_craftable_with_ingredients():
    db_path = make_db_path()
    from titan.fugassa import dnd5e_options as opt

    custom_idx = opt.CLASS_CHOICES.index("Custom")
    draft = {
        "player_name": "Ren",
        "level": 1,
        "abilities": {},
        "player_class_idx": custom_idx,  # blank custom class -> falls back to artisan-only
    }
    db_seed.bootstrap_from_wizard(db_path, draft=draft, state=_base_state())

    state = {"inventory": {"shared": [{"name": "Spare Cloth", "qty": 1}], "equipped": {}}}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    blueprints = ce.list_known_blueprints(conn, "Ren")
    conn.close()
    artisan_recipe = next(bp for bp in blueprints if bp["profession"] == "artisan")

    result = ce.craft_item(db_path, state, "Ren", artisan_recipe["code"])
    assert result["profession"] == "artisan"  # rank 0, min_rank 0 -> gate always passes


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

"""Crafting: rank is a hard prerequisite gate (never just a DC modifier — a
Novice cannot produce a Grandmaster item even on a critical roll), recipes
are discovered at play time (invent from scratch / reverse-engineer an owned
item), and ingredients are always spent on the attempt, win or lose.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import crafting_engine as ce
from titan.fugassa.db import sqlite_store


def make_db(int_score: int = 14, proficiency_bonus: int = 2) -> str:
    d = tempfile.mkdtemp(prefix="fugassa_craft_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Craft Campaign", theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO players (code, display_name) VALUES ('p1', 'Hero')")
    conn.execute(
        "INSERT INTO player_characters (code, player_id, name, int_score, proficiency_bonus) "
        "VALUES ('pc_hero', 1, 'Lucas', ?, ?)",
        (int_score, proficiency_bonus),
    )
    conn.commit()
    conn.close()
    return db_path


def _state(**overrides):
    base = {"inventory": {"shared": [], "equipped": {}}}
    base.update(overrides)
    return base


def _seed_recipe(db_path, *, hero_name="Lucas", grant=True, tier=0, min_rank=None, craft_dc=10, ingredients=None, **kw):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    recipe = ce._insert_recipe(
        conn,
        output_item_name=kw.get("output_item_name", "Sharpened Blade"),
        recipe_kind=kw.get("recipe_kind", "item"),
        profession=kw.get("profession", "weaponsmith"),
        tier=tier,
        craft_dc=craft_dc,
        duration_minutes=kw.get("duration_minutes", 30),
        ingredients=ingredients or [{"item_name": "Whetstone", "qty": 1}],
        description=kw.get("description"),
        heal_amount=kw.get("heal_amount"),
        discovered_by=hero_name or "player_character",
    )
    if min_rank is not None:
        conn.execute("UPDATE crafting_recipes SET min_rank = ? WHERE id = ?", (min_rank, recipe["id"]))
    if grant and hero_name:
        ce._grant_blueprint(conn, hero_name, recipe["id"], "starter")
    conn.commit()
    conn.close()
    return recipe["code"]


class TestCraftItem:
    def test_craft_requires_known_blueprint(self):
        db_path = make_db()
        code = _seed_recipe(db_path, grant=False)
        state = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})
        with pytest.raises(ce.CraftingError) as exc:
            ce.craft_item(db_path, state, "Lucas", code)
        assert exc.value.code == "blueprint_unknown"

    def test_rank_gate_rejects_craft_above_hero_rank(self):
        db_path = make_db()
        code = _seed_recipe(db_path, min_rank=2)
        state = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})
        with pytest.raises(ce.CraftingError) as exc:
            ce.craft_item(db_path, state, "Lucas", code)
        assert exc.value.code == "rank_too_low"
        # Ingredients must be untouched — the gate is checked before spending anything.
        assert state["inventory"]["shared"][0]["qty"] == 1

    def test_missing_ingredients_raises_before_rolling(self):
        db_path = make_db()
        code = _seed_recipe(db_path)
        state = _state()  # no Whetstone at all
        with pytest.raises(ce.CraftingError) as exc:
            ce.craft_item(db_path, state, "Lucas", code)
        assert exc.value.code == "missing_ingredients"

    def test_successful_craft_consumes_ingredients_and_grants_output(self, monkeypatch):
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 15)  # guarantees success vs low DC
        db_path = make_db(int_score=10, proficiency_bonus=2)
        code = _seed_recipe(db_path, craft_dc=10, ingredients=[{"item_name": "Whetstone", "qty": 2}])
        state = _state(inventory={"shared": [{"name": "Whetstone", "qty": 3}], "equipped": {}})

        result = ce.craft_item(db_path, state, "Lucas", code)

        assert result["success"] is True
        remaining = next(i for i in state["inventory"]["shared"] if i["name"] == "Whetstone")
        assert remaining["qty"] == 1
        output = next(i for i in state["inventory"]["shared"] if i["name"] == "Sharpened Blade")
        assert output["qty"] == 1

    def test_failed_craft_still_consumes_ingredients_no_output(self, monkeypatch):
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 1)  # guarantees failure
        db_path = make_db(int_score=8, proficiency_bonus=2)
        code = _seed_recipe(db_path, craft_dc=25, ingredients=[{"item_name": "Whetstone", "qty": 1}])
        state = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})

        result = ce.craft_item(db_path, state, "Lucas", code)

        assert result["success"] is False
        assert not any(i["name"] == "Whetstone" for i in state["inventory"]["shared"])
        assert not any(i["name"] == "Sharpened Blade" for i in state["inventory"]["shared"])

    def test_critical_roll_grants_bonus_output_qty(self, monkeypatch):
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)
        db_path = make_db(int_score=10, proficiency_bonus=2)
        code = _seed_recipe(db_path, craft_dc=10, ingredients=[{"item_name": "Whetstone", "qty": 1}])
        state = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})

        result = ce.craft_item(db_path, state, "Lucas", code)

        assert result["critical"] is True
        assert result["output_qty"] == 2

    def test_rank_up_crosses_threshold_after_enough_successful_crafts(self, monkeypatch):
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)
        db_path = make_db()
        code = _seed_recipe(db_path, tier=5, craft_dc=1, ingredients=[{"item_name": "Whetstone", "qty": 1}])
        # tier=5 recipe grants max(1,5)*10 = 50 xp per success; threshold to
        # Apprentice(rank 1) is 100 xp -> rank-up should land on the 2nd craft.
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE crafting_recipes SET min_rank = 0 WHERE code = ?", (code,))
        conn.commit()
        conn.close()

        state1 = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})
        r1 = ce.craft_item(db_path, state1, "Lucas", code)
        assert r1["rank_up"] is False
        assert r1["xp_gained"] == 50

        state2 = _state(inventory={"shared": [{"name": "Whetstone", "qty": 1}], "equipped": {}})
        r2 = ce.craft_item(db_path, state2, "Lucas", code)
        assert r2["rank_up"] is True
        assert r2["new_rank"] == 1
        assert r2["new_rank_name"] == "Apprentice"


class TestInferTierFromItem:
    @pytest.mark.parametrize(
        "rarity,expected_tier",
        [
            ("common", 0),
            ("Uncommon", 1),
            ("RARE", 2),
            ("very rare", 3),
            ("legendary", 4),
            ("artifact", 5),
            ("", 1),
            ("made-up-rarity", 1),
        ],
    )
    def test_infers_expected_tier(self, rarity, expected_tier):
        assert ce.infer_tier_from_item({"rarity": rarity}) == expected_tier


class TestInventBlueprint:
    @pytest.mark.asyncio
    async def test_hard_gate_rejects_tier_above_rank_without_calling_llm(self, monkeypatch):
        async def _boom(**kwargs):
            raise AssertionError("LLM draft must not be called when the rank gate already fails")

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _boom)
        db_path = make_db()
        with pytest.raises(ce.CraftingError) as exc:
            await ce.invent_blueprint(
                db_path, _state(), "Lucas", profession="enchanter", tier=3, description="a portal"
            )
        assert exc.value.code == "rank_too_low"

    @pytest.mark.asyncio
    async def test_success_creates_recipe_and_blueprint_then_craftable(self, monkeypatch):
        async def _fake_draft(**kwargs):
            return {
                "output_item_name": "Rune Lantern",
                "description": "Glows near the undead.",
                "ingredients": [{"item_name": "Silver Wire", "qty": 2}],
            }

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _fake_draft)
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)
        db_path = make_db(int_score=16, proficiency_bonus=4)

        result = await ce.invent_blueprint(
            db_path, _state(), "Lucas", profession="enchanter", tier=0, description="a lantern that senses undead"
        )

        assert result["success"] is True
        assert result["output_item_name"] == "Rune Lantern"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        recipe = conn.execute(
            "SELECT * FROM crafting_recipes WHERE code = ?", (result["recipe_code"],)
        ).fetchone()
        assert recipe is not None
        assert recipe["output_item_name"] == "Rune Lantern"
        bp = conn.execute(
            "SELECT * FROM player_blueprints WHERE hero_name = 'Lucas' AND recipe_id = ?", (recipe["id"],)
        ).fetchone()
        assert bp is not None and bp["source"] == "invented"
        conn.close()

        # Now actually craft it with the freshly learned blueprint.
        state = _state(inventory={"shared": [{"name": "Silver Wire", "qty": 2}], "equipped": {}})
        craft_result = ce.craft_item(db_path, state, "Lucas", result["recipe_code"])
        assert craft_result["success"] is True
        assert any(i["name"] == "Rune Lantern" for i in state["inventory"]["shared"])

    @pytest.mark.asyncio
    async def test_failure_does_not_create_recipe_or_blueprint(self, monkeypatch):
        async def _fake_draft(**kwargs):
            return {"output_item_name": "Rune Lantern", "description": "", "ingredients": []}

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _fake_draft)
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 1)
        db_path = make_db(int_score=8, proficiency_bonus=2)

        result = await ce.invent_blueprint(
            db_path, _state(), "Lucas", profession="enchanter", tier=0, description="a lantern"
        )

        assert result["success"] is False
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM crafting_recipes").fetchone()[0]
        assert count == 0
        conn.close()


class TestReverseEngineer:
    @pytest.mark.asyncio
    async def test_consumes_item_regardless_of_success(self, monkeypatch):
        async def _fake_draft(**kwargs):
            return {"output_item_name": "x", "description": "", "ingredients": []}

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _fake_draft)
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 1)
        db_path = make_db(int_score=8, proficiency_bonus=2)
        state = _state(inventory={"shared": [{"name": "Salvage Pistol", "qty": 1, "rarity": "common"}], "equipped": {}})

        result = await ce.reverse_engineer(
            db_path, state, "Lucas", profession="engineer", item_name="Salvage Pistol"
        )

        assert result["success"] is False
        assert not any(i["name"] == "Salvage Pistol" for i in state["inventory"]["shared"])

    @pytest.mark.asyncio
    async def test_success_grants_blueprint_named_after_original_item(self, monkeypatch):
        async def _fake_draft(**kwargs):
            return {"output_item_name": "generic", "description": "", "ingredients": [{"item_name": "Scrap Metal", "qty": 1}]}

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _fake_draft)
        monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)
        db_path = make_db(int_score=16, proficiency_bonus=4)
        state = _state(inventory={"shared": [{"name": "Salvage Pistol", "qty": 1, "rarity": "common"}], "equipped": {}})

        result = await ce.reverse_engineer(
            db_path, state, "Lucas", profession="engineer", item_name="Salvage Pistol"
        )

        assert result["success"] is True
        assert result["output_item_name"] == "Salvage Pistol"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        recipe = conn.execute("SELECT * FROM crafting_recipes WHERE code = ?", (result["recipe_code"],)).fetchone()
        assert recipe["output_item_name"] == "Salvage Pistol"
        conn.close()

    @pytest.mark.asyncio
    async def test_rank_gate_applies_to_reverse_engineer_too(self, monkeypatch):
        async def _boom(**kwargs):
            raise AssertionError("LLM draft must not be called when the rank gate already fails")

        monkeypatch.setattr(ce, "_draft_recipe_via_llm", _boom)
        db_path = make_db()
        state = _state(inventory={"shared": [{"name": "Starship Core", "qty": 1, "rarity": "legendary"}], "equipped": {}})
        with pytest.raises(ce.CraftingError) as exc:
            await ce.reverse_engineer(db_path, state, "Lucas", profession="engineer", item_name="Starship Core")
        assert exc.value.code == "rank_too_low"
        # Rejected before consuming the item too.
        assert any(i["name"] == "Starship Core" for i in state["inventory"]["shared"])


class TestGrantStarterBlueprint:
    def test_starter_blueprint_is_immediately_craftable(self):
        db_path = make_db()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        recipe = ce.grant_starter_blueprint(
            conn,
            "Lucas",
            output_item_name="Mended Cloth",
            profession="artisan",
            description="A patched-up garment.",
            ingredients=[{"item_name": "Spare Cloth", "qty": 1}],
        )
        conn.commit()
        conn.close()

        state = _state(inventory={"shared": [{"name": "Spare Cloth", "qty": 1}], "equipped": {}})
        result = ce.craft_item(db_path, state, "Lucas", recipe["code"])
        assert result["profession"] == "artisan"
        # rank 0 recipe, min_rank 0 -> always passes the gate regardless of roll.


if __name__ == "__main__":
    import asyncio

    tests = []
    for cls_name, cls in list(globals().items()):
        if isinstance(cls, type) and cls_name.startswith("Test"):
            inst = cls()
            for method_name in dir(inst):
                if method_name.startswith("test_"):
                    tests.append((f"{cls_name}.{method_name}", getattr(inst, method_name)))

    failures = 0
    for name, fn in tests:
        try:
            if asyncio.iscoroutinefunction(fn):
                asyncio.run(fn())
            else:
                fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)

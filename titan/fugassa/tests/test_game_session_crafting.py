"""Integration test: game_session's crafting wrappers against a real save
(load_game_state -> engine -> save_game_state -> state_repository.sync),
not just the pure crafting_engine unit tests.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import crafting_engine as ce
from titan.fugassa import game_session
from titan.fugassa import save_store


@pytest.fixture
def save_id():
    world_name = f"CraftSessionTest_{os.getpid()}_{id(object())}"
    draft = {"world_name": world_name, "theme_mode": "Fantasy", "player_name": "Lucas", "level": 1}
    sid = save_store.normalize_save_name(world_name)
    save_store.create_save_from_wizard(draft)
    yield sid
    try:
        save_store.delete_save(sid)
    except Exception:
        pass


def _seed_starter_blueprint(save_id_: str, hero_name: str = "Lucas") -> str:
    db_path = game_session.game_db_path(save_id_)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    recipe = ce.grant_starter_blueprint(
        conn,
        hero_name,
        output_item_name="Mended Cloth",
        profession="artisan",
        description="A patched-up garment.",
        ingredients=[{"item_name": "Spare Cloth", "qty": 1}],
    )
    conn.commit()
    conn.close()
    return recipe["code"]


def test_get_crafting_professions_defaults_to_novice(save_id):
    result = game_session.get_crafting_professions(save_id, "Lucas")
    assert len(result["professions"]) == len(ce.PROFESSIONS)
    assert all(p["rank"] == 0 for p in result["professions"])


def test_get_crafting_blueprints_reports_have_need_diff(save_id):
    recipe_code = _seed_starter_blueprint(save_id)
    result = game_session.get_crafting_blueprints(save_id, "Lucas")
    matched = next(r for r in result["blueprints"] if r["code"] == recipe_code)
    assert matched["can_afford"] is False  # fresh save has no "Spare Cloth"


def test_craft_item_end_to_end_updates_state_and_persists(save_id, monkeypatch):
    monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)
    recipe_code = _seed_starter_blueprint(save_id)

    state = game_session.load_game_state(save_id)
    inv = dict(state.get("inventory") or {})
    inv["shared"] = [{"name": "Spare Cloth", "qty": 1}]
    state["inventory"] = inv
    game_session.save_game_state(save_id, state)

    result = game_session.craft_item(save_id, "Lucas", recipe_code)
    assert result["success"] is True
    assert result["output_item_name"] == "Mended Cloth"

    reloaded = game_session.load_game_state(save_id)
    shared_names = {i["name"] for i in reloaded["inventory"]["shared"]}
    assert "Mended Cloth" in shared_names
    assert "Spare Cloth" not in shared_names


def test_craft_item_unknown_blueprint_raises_game_session_error(save_id):
    with pytest.raises(game_session.GameSessionError) as exc:
        game_session.craft_item(save_id, "Lucas", "nonexistent_recipe_code")
    assert exc.value.code == "recipe_not_found"


@pytest.mark.asyncio
async def test_invent_blueprint_end_to_end(save_id, monkeypatch):
    async def _fake_draft(**kwargs):
        return {"output_item_name": "Rune Lantern", "description": "", "ingredients": [{"item_name": "Silver Wire", "qty": 1}]}

    monkeypatch.setattr(ce, "_draft_recipe_via_llm", _fake_draft)
    monkeypatch.setattr(ce.random, "randint", lambda a, b: 20)

    result = await game_session.invent_blueprint(save_id, "Lucas", "enchanter", 0, "a lantern that senses undead")
    assert result["success"] is True
    assert result["output_item_name"] == "Rune Lantern"

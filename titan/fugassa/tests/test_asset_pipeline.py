"""Tests for the SD asset pipeline additions:

- `asset_service.regenerate_for_entity` create-or-regenerate semantics
  (with optional `metadata`/`title`) — the backend behind the unified
  `/assets/generate` route (NPC portraits, per-message chat scenes,
  first-time "generate" from `AssetEditor.js`).
- `asset_worker.drain_once` NPC portrait backfill branch.
- `campaign_digest.get_min_active_turn` + the chat-message-scene cleanup
  wired into `condense_pending_conn`.
- `game_session.get_chat_scene_assets` / `get_summary` read wrappers.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa import asset_service, asset_worker, campaign_digest, game_session, save_store
from titan.fugassa.db import asset_repository, sqlite_store


def make_db() -> str:
    d = tempfile.mkdtemp(prefix="fugassa_assets_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Asset Pipeline Test", theme="fantasy")
    return db_path


def _insert_npc(db_path: str, *, name: str = "Old Man Willow", race: str = "Human", class_role: str = "Herbalist") -> int:
    conn = sqlite3.connect(db_path)
    try:
        now = "2024-01-01T00:00:00"
        cur = conn.execute(
            "INSERT INTO npcs (code, name, race, class_role, status, created_at, updated_at) "
            "VALUES ('npc_willow', ?, ?, ?, 'alive', ?, ?)",
            (name, race, class_role, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# asset_service.regenerate_for_entity
# ---------------------------------------------------------------------------

def test_regenerate_for_entity_creates_when_none_exists():
    db_path = make_db()
    result = asset_service.regenerate_for_entity(
        db_path,
        entity_type="npc",
        entity_id=1,
        asset_type="portrait",
        use_auto_prompt=True,
        metadata={"asset_type": "portrait", "prompt_seed": {"name": "Willow"}},
        title="Portrait Willow",
    )
    assert result["success"] is True
    asset = result["asset"]
    assert asset["status"] == "queued"
    assert asset["entity_type"] == "npc"
    assert asset["title"] == "Portrait Willow"

    fetched = asset_repository.list_assets(db_path, entity_type="npc", entity_id=1, asset_type="portrait")
    assert len(fetched) == 1


def test_regenerate_for_entity_archives_existing_and_creates_new():
    db_path = make_db()
    first = asset_service.regenerate_for_entity(db_path, entity_type="location", entity_id=5, asset_type="scene")
    first_id = first["asset"]["id"]
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE assets SET status = 'ready', file_path = 'scenes/x.png' WHERE id = ?", (first_id,))
    conn.commit()
    conn.close()

    second = asset_service.regenerate_for_entity(db_path, entity_type="location", entity_id=5, asset_type="scene")
    assert second["asset"]["id"] != first_id

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    old_row = conn.execute("SELECT status FROM assets WHERE id = ?", (first_id,)).fetchone()
    conn.close()
    assert old_row["status"] == "archived"


# ---------------------------------------------------------------------------
# asset_worker.drain_once — NPC portrait backfill
# ---------------------------------------------------------------------------

def test_drain_once_backfills_npc_portrait_columns(monkeypatch):
    db_path = make_db()
    npc_id = _insert_npc(db_path)
    d = os.path.dirname(db_path)

    asset_service.regenerate_for_entity(
        db_path,
        entity_type="npc",
        entity_id=npc_id,
        asset_type="portrait",
        use_auto_prompt=True,
        metadata={"asset_type": "portrait", "prompt_seed": {"name": "Old Man Willow"}},
    )

    async def _fake_generate_image(*, positive_prompt, negative_prompt, asset_type, theme, image_style_default, dest_path, **_kw):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"fake-png-bytes")
        return {"success": True, "path": dest_path}

    monkeypatch.setattr(asset_worker.asset_gen, "generate_image", _fake_generate_image)

    result = asyncio.run(asset_worker.drain_once("save_x", db_path, d, images_enabled=True, theme="fantasy", state={}))
    assert result["drained"] == 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    npc_row = conn.execute("SELECT portrait_asset_id, portrait_path, portrait_prompt FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    conn.close()
    assert npc_row["portrait_asset_id"] is not None
    assert npc_row["portrait_path"]
    assert os.path.isfile(os.path.join(d, "generated", npc_row["portrait_path"]))


# ---------------------------------------------------------------------------
# campaign_digest — min_active_turn + chat-message-scene cleanup
# ---------------------------------------------------------------------------

def _insert_turn_history(db_path: str, turn_number: int, *, active: bool = True) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO turn_history (turn_number, player_text, ai_text, is_active) VALUES (?, ?, ?, ?)",
        (turn_number, f"player {turn_number}", f"gm {turn_number}", 1 if active else 0),
    )
    conn.commit()
    conn.close()


def test_min_active_turn_defaults_to_zero_with_no_history():
    db_path = make_db()
    assert campaign_digest.get_min_active_turn(db_path) == 0


def test_min_active_turn_reflects_lowest_active_row():
    db_path = make_db()
    _insert_turn_history(db_path, 1, active=False)
    _insert_turn_history(db_path, 2, active=False)
    _insert_turn_history(db_path, 3, active=True)
    _insert_turn_history(db_path, 4, active=True)
    assert campaign_digest.get_min_active_turn(db_path) == 3


def test_condense_pending_conn_deletes_chat_scene_assets_for_batch_turns():
    db_path = make_db()
    d = os.path.dirname(db_path)
    generated_root = os.path.join(d, "generated")
    os.makedirs(os.path.join(generated_root, "scenes"), exist_ok=True)

    # turn_number=7 -> a "chat message scene" asset (entity_type='other'), plus
    # an unrelated location scene asset that must survive condensation.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = "2024-01-01T00:00:00"
    rel_path = "scenes/other_7_v1.png"
    with open(os.path.join(generated_root, rel_path), "wb") as f:
        f.write(b"fake")
    conn.execute(
        "INSERT INTO assets (code, asset_type, entity_type, entity_id, status, prompt_source, file_path, created_at, updated_at) "
        "VALUES ('other:7:scene:v1', 'scene', 'other', 7, 'ready', 'auto', ?, ?, ?)",
        (rel_path, now, now),
    )
    conn.execute(
        "INSERT INTO assets (code, asset_type, entity_type, entity_id, status, prompt_source, created_at, updated_at) "
        "VALUES ('location:9:scene:v1', 'scene', 'location', 9, 'ready', 'auto', ?, ?)",
        (now, now),
    )
    conn.commit()

    batch = [{"id": 1, "turn_number": 7, "player_text": "p", "ai_text": "a"}]

    result = campaign_digest.condense_pending_conn(conn, batch, condensed_text="digest text", generated_root=generated_root)
    conn.commit()
    conn.close()

    assert result["deleted_chat_assets"] == 1
    assert not os.path.isfile(os.path.join(generated_root, rel_path))

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT entity_type, entity_id FROM assets").fetchall()
    conn.close()
    assert remaining == [("location", 9)]


# ---------------------------------------------------------------------------
# game_session.get_chat_scene_assets / get_summary
# ---------------------------------------------------------------------------

@pytest.fixture
def save_id():
    world_name = f"AssetSessionTest_{os.getpid()}_{id(object())}"
    draft = {"world_name": world_name, "theme_mode": "Fantasy", "player_name": "Lucas", "level": 1}
    sid = save_store.normalize_save_name(world_name)
    save_store.create_save_from_wizard(draft)
    yield sid
    try:
        save_store.delete_save(sid)
    except Exception:
        pass


def test_get_chat_scene_assets_returns_map_and_min_active_turn(save_id):
    db_path = game_session.game_db_path(save_id)
    conn = sqlite3.connect(db_path)
    now = "2024-01-01T00:00:00"
    conn.execute(
        "INSERT INTO assets (code, asset_type, entity_type, entity_id, status, prompt_source, created_at, updated_at) "
        "VALUES ('other:3:scene:v1', 'scene', 'other', 3, 'ready', 'auto', ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    result = game_session.get_chat_scene_assets(save_id)
    assert result["min_active_turn"] == 0
    assert "3" in result["assets"] or 3 in result["assets"]


def test_get_summary_returns_digest_and_scene_summaries(save_id):
    db_path = game_session.game_db_path(save_id)
    conn = sqlite3.connect(db_path)
    now = "2024-01-01T00:00:00"
    conn.execute(
        "INSERT INTO locations (code, name, created_at, updated_at) VALUES ('loc_x', 'Old Mill', ?, ?)",
        (now, now),
    )
    loc_id = conn.execute("SELECT id FROM locations WHERE code = 'loc_x'").fetchone()[0]
    conn.execute(
        "INSERT INTO scene_summaries (location_id, summary_text, turn_start, turn_end, created_at) "
        "VALUES (?, 'Something happened here.', 1, 3, ?)",
        (loc_id, now),
    )
    conn.execute("UPDATE campaign_digest SET digest_text = 'Earlier events condensed.' WHERE id = 1")
    conn.commit()
    conn.close()

    result = game_session.get_summary(save_id)
    assert result["digest_text"] == "Earlier events condensed."
    assert len(result["scene_summaries"]) == 1
    assert result["scene_summaries"][0]["location_name"] == "Old Mill"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

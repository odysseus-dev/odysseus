"""Legacy save → campaign_jobs pipeline v2 migration."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.db import job_repository, save_pipeline_migration, sqlite_store
from titan.fugassa.game_bootstrap import GAME_JSON, apply_opening_time_hint_to_world_time, write_game_json


def _legacy_db(save_id: str = "LegacySave") -> tuple[str, str, str]:
    root = tempfile.mkdtemp(prefix="fugassa_legacy_")
    db_path = os.path.join(root, "game.db")
    sqlite_store.init_game_db(db_path, save_id, theme="fantasy")
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM save_meta WHERE key = 'pipeline_model'")
    conn.commit()
    conn.close()
    state = {
        "turn": 2,
        "world_profile": {
            "opening_time_hint": (
                "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | "
                "Current Location | Season | Weather |\n"
                "|---|---|---|---|---|---|---|\n"
                "| Morning | 08:00 AM | Third Age, 1024, Harvest, 15 | Full | "
                "Oakhaven | Spring | Clear |"
            ),
        },
        "world_time": {"day": 1, "hour": 8},
        "chat_history": [{"role": "assistant", "content": "Welcome."}],
        "party": [{"name": "Hero"}],
        "player": {"x": 0, "y": 0, "z": 0},
        "location_state": {"name": "Oakhaven"},
    }
    slot = write_game_json(root, state)
    return save_id, db_path, slot


def test_first_load_migrates_pipeline_model_and_world_time():
    save_id, db_path, slot = _legacy_db()
    summary = save_pipeline_migration.ensure_save_ready(save_id, db_path, save_path=slot)
    assert summary["migrated"] is True
    assert summary["world_time_patched"] is True

    conn = sqlite3.connect(db_path)
    model = conn.execute("SELECT value FROM save_meta WHERE key = 'pipeline_model'").fetchone()[0]
    conn.close()
    assert model == "v2"

    reloaded = json.loads(open(slot, encoding="utf-8").read())
    wt = reloaded["world_time"]
    assert wt.get("hhmm") == "08:00 AM"
    assert wt.get("moon_phase") == "Full"
    assert wt.get("season") == "Spring"
    assert wt.get("weather") == "Clear"


def test_queued_assets_get_sd_jobs_on_migration():
    save_id, db_path, slot = _legacy_db()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM save_meta WHERE key = 'pipeline_model'")
    conn.execute(
        """
        INSERT INTO assets (
            code, entity_type, entity_id, asset_type, status, title, prompt,
            created_at, updated_at
        ) VALUES ('sc1', 'location', 1, 'scene', 'queued', 'Scene', 'tavern', 't', 't')
        """
    )
    conn.commit()
    conn.close()

    summary = save_pipeline_migration.ensure_save_ready(save_id, db_path, save_path=slot)
    assert summary["enqueued_sd_jobs"] >= 1
    jobs = job_repository.list_jobs(db_path, save_id, limit=10)
    assert any(j["job_type"] == "sd_generate" for j in jobs)


def test_running_jobs_recovered_to_pending():
    save_id, db_path, slot = _legacy_db()
    batch = job_repository.new_batch_id(save_id)
    job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="sd_generate",
        batch_id=batch,
        payload={"asset_id": 1},
    )
    claimed = job_repository.claim_next_job(db_path, save_id)
    assert claimed["status"] == "running"

    with patch("titan.fugassa.campaign_job_runner.ensure_worker_scheduled"):
        summary = save_pipeline_migration.ensure_save_ready(save_id, db_path, save_path=slot)
    assert summary["recovered_jobs"] == 1
    running = job_repository.current_running_job(db_path, save_id)
    assert running is None
    pending = job_repository.list_jobs(db_path, save_id, status="pending", limit=5)
    assert any(int(j["id"]) == int(claimed["id"]) for j in pending)


def test_running_job_excluded_from_recovery_when_still_executing():
    save_id, db_path, slot = _legacy_db()
    batch = job_repository.new_batch_id(save_id)
    job_id = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="interactive_turn",
        batch_id=batch,
        payload={"player_text": "hello"},
    )
    claimed = job_repository.claim_next_job(db_path, save_id)
    assert int(claimed["id"]) == job_id
    assert claimed["status"] == "running"

    with patch("titan.fugassa.campaign_job_runner.ensure_worker_scheduled"):
        summary = save_pipeline_migration.ensure_save_ready(
            save_id,
            db_path,
            save_path=slot,
            exclude_running_job_id=job_id,
        )
    assert summary["recovered_jobs"] == 0
    running = job_repository.current_running_job(db_path, save_id)
    assert running is not None
    assert int(running["id"]) == job_id


def test_apply_opening_time_hint_preserves_gm_fields():
    state = {
        "world_profile": {"opening_time_hint": "| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |\n|---|---|---|---|---|---|---|\n| Night | 11:00 PM | Year 1 | New | Room | Winter | Snow |"},
        "world_time": {"day": 5, "hour": 14, "weather": "Rain"},
    }
    apply_opening_time_hint_to_world_time(state, overwrite=False)
    assert state["world_time"]["hour"] == 14
    assert state["world_time"]["weather"] == "Rain"
    assert state["world_time"].get("moon_phase") == "New"
    assert state["world_time"].get("season") == "Winter"

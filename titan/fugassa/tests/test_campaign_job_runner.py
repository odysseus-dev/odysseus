"""Campaign job queue — FIFO ordering, batch unlock, SD dedup."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from titan.fugassa.campaign_job_runner import enqueue_sd_jobs_for_queued_assets
from titan.fugassa.db import job_repository, sqlite_store


def _make_db() -> tuple[str, str]:
    d = tempfile.mkdtemp(prefix="fugassa_jobs_")
    db_path = os.path.join(d, "game.db")
    sqlite_store.init_game_db(db_path, "Job Runner Test", theme="fantasy")
    save_id = "save_job_test"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO save_meta (key, value, updated_at) VALUES ('save_id', ?, '2024-01-01T00:00:00')",
        (save_id,),
    )
    conn.commit()
    conn.close()
    return save_id, db_path


def _insert_queued_asset(db_path: str, *, entity_id: int = 1) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO assets (
                code, entity_type, entity_id, asset_type, status, title, prompt_source,
                created_at, updated_at
            ) VALUES (?, 'location', ?, 'scene', 'queued', 'Scene', 'auto', ?, ?)
            """,
            (f"asset_{entity_id}", entity_id, "2024-01-01T00:00:00", "2024-01-01T00:00:00"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_claim_next_job_respects_fifo_priority_and_id():
    save_id, db_path = _make_db()
    batch = job_repository.new_batch_id(save_id, 1)
    sd_id = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="sd_generate",
        batch_id=batch,
        payload={"asset_id": 99},
        priority=200,
    )
    interactive_id = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="interactive_turn",
        batch_id=batch,
        payload={"player_text": "hello"},
        priority=100,
    )
    first = job_repository.claim_next_job(db_path, save_id)
    assert first is not None
    assert int(first["id"]) == interactive_id
    job_repository.mark_job_completed(db_path, int(first["id"]))
    second = job_repository.claim_next_job(db_path, save_id)
    assert second is not None
    assert int(second["id"]) == sd_id


def test_batch_interactive_unlocked_after_turn_completes():
    save_id, db_path = _make_db()
    batch = job_repository.new_batch_id(save_id, 1)
    assert job_repository.batch_interactive_unlocked(db_path, save_id, batch) is False
    job_id = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="interactive_turn",
        batch_id=batch,
        payload={},
        priority=100,
    )
    assert job_repository.batch_interactive_unlocked(db_path, save_id, batch) is False
    claimed = job_repository.claim_next_job(db_path, save_id)
    assert int(claimed["id"]) == job_id
    job_repository.mark_job_completed(db_path, job_id)
    assert job_repository.batch_interactive_unlocked(db_path, save_id, batch) is True


def test_enqueue_sd_jobs_dedupes_pending_asset_jobs():
    save_id, db_path = _make_db()
    asset_id = _insert_queued_asset(db_path)
    batch = job_repository.new_batch_id(save_id)
    first = enqueue_sd_jobs_for_queued_assets(save_id, db_path, batch_id=batch)
    second = enqueue_sd_jobs_for_queued_assets(save_id, db_path, batch_id=batch)
    assert len(first) == 2  # scene_prompt_llm + sd_generate
    assert second == []
    assert job_repository.pipeline_job_exists_for_asset(db_path, save_id, asset_id)


def test_enqueue_scene_pipeline_creates_prompt_then_sd():
    save_id, db_path = _make_db()
    asset_id = _insert_queued_asset(db_path)
    batch = job_repository.new_batch_id(save_id)
    from titan.fugassa.campaign_job_runner import enqueue_scene_asset_pipeline

    ids = enqueue_scene_asset_pipeline(
        db_path, save_id, asset_id=asset_id, batch_id=batch, priority=200
    )
    assert len(ids) == 2
    jobs = job_repository.list_jobs(db_path, save_id, batch_id=batch, limit=10)
    types = {j["job_type"] for j in jobs}
    assert types == {"scene_prompt_llm", "sd_generate"}
    sd = next(j for j in jobs if j["job_type"] == "sd_generate")
    assert sd.get("depends_on_id") is not None


def test_pipeline_status_reports_interactive_unlocked():
    save_id, db_path = _make_db()
    batch = job_repository.new_batch_id(save_id, 2)
    job_id = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="interactive_turn",
        batch_id=batch,
        payload={"player_text": "look around"},
        priority=100,
        turn_number=2,
    )
    from titan.fugassa.campaign_job_runner import get_pipeline_status

    locked = get_pipeline_status(db_path, save_id, batch_id=batch)
    assert locked["interactive_unlocked"] is False
    assert locked["pipeline_locked"] is True

    job_repository.claim_next_job(db_path, save_id)
    job_repository.mark_job_completed(db_path, job_id)
    unlocked = get_pipeline_status(db_path, save_id, batch_id=batch)
    assert unlocked["interactive_unlocked"] is True


def test_preempt_is_noop():
    from titan.fugassa import asset_worker

    asset_worker.preempt("save_x")  # must not raise

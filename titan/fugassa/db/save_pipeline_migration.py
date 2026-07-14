"""One-time + crash-recovery migration from ad-hoc SD drain to campaign_jobs pipeline."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import asset_worker, campaign_job_runner
from titan.fugassa.db import job_repository, sqlite_store
from titan.fugassa.game_bootstrap import (
    GAME_JSON,
    apply_opening_time_hint_to_world_time,
    read_game_json,
    write_game_json,
)

LOG = logging.getLogger("titan.fugassa.save_pipeline_migration")

PIPELINE_MODEL_KEY = "pipeline_model"
PIPELINE_MODEL_V2 = "v2"
MIGRATED_AT_KEY = "pipeline_migrated_at"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM save_meta WHERE key = ?", (key,)).fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO save_meta (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, _utc_now()),
    )


def _recover_stale_jobs(
    conn: sqlite3.Connection,
    save_id: str,
    *,
    exclude_job_id: int | None = None,
) -> int:
    """Server restart mid-job: running → pending so worker can reclaim."""
    now = _utc_now()
    if exclude_job_id is not None:
        cur = conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'pending', started_at = NULL, updated_at = ?
            WHERE save_id = ? AND status = 'running' AND id != ?
            """,
            (now, save_id, exclude_job_id),
        )
    else:
        cur = conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'pending', started_at = NULL, updated_at = ?
            WHERE save_id = ? AND status = 'running'
            """,
            (now, save_id),
        )
    return int(cur.rowcount or 0)


def _recover_stale_assets(conn: sqlite3.Connection) -> int:
    """Orphan `generating` rows from old in-memory worker — re-queue."""
    now = _utc_now()
    cur = conn.execute(
        """
        UPDATE assets
        SET status = 'queued', updated_at = ?
        WHERE status = 'generating'
        """,
        (now,),
    )
    return int(cur.rowcount or 0)


def _sync_campaign_phase(db_path: str, save_id: str) -> str:
    running = job_repository.current_running_job(db_path, save_id)
    if not running and not job_repository.has_active_jobs(db_path, save_id):
        phase = "idle"
    elif running and str(running.get("job_type") or "") in job_repository.SD_JOB_TYPES:
        phase = "generating_assets"
    elif job_repository.has_active_jobs(db_path, save_id):
        phase = "processing"
    else:
        phase = "idle"
    job_repository.set_campaign_phase(db_path, phase)
    return phase


def _migrate_world_time_from_wizard(save_path: str) -> bool:
    state = read_game_json(save_path)
    if not state:
        return False
    if apply_opening_time_hint_to_world_time(state, overwrite=False):
        write_game_json(save_path, state)
        return True
    return False


def ensure_save_ready(
    save_id: str,
    db_path: str,
    *,
    save_path: str | None = None,
    exclude_running_job_id: int | None = None,
) -> dict[str, Any]:
    """
    Idempotent: schema v9, v1→v2 pipeline migration, crash recovery, worker schedule.

    Called on every `load_game_state` — first run migrates legacy saves; later runs
    only recover stale running/generating rows and resume pending jobs.
    """
    if not db_path or not os.path.isfile(db_path):
        return {"ready": False, "reason": "no_db"}

    sqlite_store.ensure_db(db_path)
    save_dir = save_path or os.path.dirname(db_path)
    if save_dir.endswith(os.sep + GAME_JSON) or save_dir.endswith("/" + GAME_JSON):
        save_dir = os.path.dirname(save_dir)
    elif save_dir.endswith(GAME_JSON):
        save_dir = os.path.dirname(save_dir)

    conn = sqlite_store.connect(db_path)
    summary: dict[str, Any] = {
        "ready": True,
        "migrated": False,
        "recovered_jobs": 0,
        "recovered_assets": 0,
        "enqueued_sd_jobs": 0,
        "world_time_patched": False,
    }
    try:
        model = _get_meta(conn, PIPELINE_MODEL_KEY)
        first_migration = model != PIPELINE_MODEL_V2

        summary["recovered_jobs"] = _recover_stale_jobs(
            conn, save_id, exclude_job_id=exclude_running_job_id
        )
        summary["recovered_assets"] = _recover_stale_assets(conn)
        conn.commit()

        if first_migration:
            LOG.info("migrating save %s to pipeline model v2", save_id)
            summary["world_time_patched"] = _migrate_world_time_from_wizard(save_dir)
            batch_id = job_repository.new_batch_id(save_id, turn_number=0)
            enqueued = campaign_job_runner.enqueue_sd_jobs_for_queued_assets(
                save_id,
                db_path,
                batch_id=batch_id,
                priority=250,
            )
            summary["enqueued_sd_jobs"] = len(enqueued)
            _set_meta(conn, PIPELINE_MODEL_KEY, PIPELINE_MODEL_V2)
            _set_meta(conn, MIGRATED_AT_KEY, _utc_now())
            conn.commit()
            summary["migrated"] = True
            LOG.info(
                "save %s pipeline v2: sd_jobs=%s world_time=%s",
                save_id,
                len(enqueued),
                summary["world_time_patched"],
            )
        elif summary["recovered_jobs"] or summary["recovered_assets"]:
            batch_id = job_repository.new_batch_id(save_id)
            enqueued = campaign_job_runner.enqueue_sd_jobs_for_queued_assets(
                save_id,
                db_path,
                batch_id=batch_id,
                priority=250,
            )
            summary["enqueued_sd_jobs"] = len(enqueued)
    finally:
        conn.close()

    phase = _sync_campaign_phase(db_path, save_id)
    summary["campaign_phase"] = phase
    asset_worker.set_turn_phase(save_id, "reading" if phase == "idle" else phase)

    reconciled = campaign_job_runner.reconcile_queued_asset_jobs(save_id, db_path)
    if reconciled:
        summary["enqueued_sd_jobs"] = int(summary.get("enqueued_sd_jobs") or 0) + len(reconciled)
        phase = _sync_campaign_phase(db_path, save_id)
        summary["campaign_phase"] = phase

    if job_repository.has_active_jobs(db_path, save_id):
        campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
        summary["worker_scheduled"] = True
    else:
        summary["worker_scheduled"] = False

    from titan.fugassa import campaign_name_registry

    registry = campaign_name_registry.seed_registry_from_npcs(db_path)
    summary["name_registry_entries"] = len(registry.entries)

    return summary


def mark_new_save_pipeline_v2(db_path: str) -> None:
    """New saves created after the pipeline rollout."""
    conn = sqlite_store.connect(db_path)
    try:
        _set_meta(conn, PIPELINE_MODEL_KEY, PIPELINE_MODEL_V2)
        conn.commit()
    finally:
        conn.close()

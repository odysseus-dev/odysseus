"""Campaign job pipeline — FIFO per save (GM → archivist → SD)."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import job_repository

LOG = logging.getLogger("titan.fugassa.campaign_job_runner")

_worker_threads: dict[str, threading.Thread] = {}
_worker_locks: dict[str, threading.Lock] = {}

SD_BACKOFF_SEC = (5, 15, 45)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _lock(save_id: str) -> threading.Lock:
    if save_id not in _worker_locks:
        _worker_locks[save_id] = threading.Lock()
    return _worker_locks[save_id]


def ensure_worker_scheduled(save_id: str, db_path: str) -> None:
    """Schedule background worker if not already running for this save."""
    thr = _worker_threads.get(save_id)
    if thr and thr.is_alive():
        return

    def _thread_main() -> None:
        try:
            asyncio.run(_worker_loop(save_id, db_path))
        finally:
            _worker_threads.pop(save_id, None)

    t = threading.Thread(
        target=_thread_main,
        daemon=True,
        name=f"fugassa-jobs-{save_id}",
    )
    _worker_threads[save_id] = t
    t.start()


async def _worker_loop(save_id: str, db_path: str) -> None:
    lock = _lock(save_id)
    if not lock.acquire(blocking=False):
        return
    try:
        while True:
            job = job_repository.claim_next_job(db_path, save_id)
            if not job:
                job_repository.set_campaign_phase(db_path, "idle")
                from titan.fugassa import asset_worker
                from titan.fugassa.game_session import persist_turn_phase

                asset_worker.set_turn_phase(save_id, "reading")
                persist_turn_phase(save_id, "reading", campaign_phase="idle")
                break
            job_type = str(job.get("job_type") or "")
            if job_type in job_repository.INTERACTIVE_JOB_TYPES:
                job_repository.set_campaign_phase(db_path, "processing")
                from titan.fugassa import asset_worker
                from titan.fugassa.game_session import persist_turn_phase

                asset_worker.set_turn_phase(save_id, "processing")
                persist_turn_phase(save_id, "processing", campaign_phase="processing")
            elif job_type in job_repository.SD_JOB_TYPES:
                job_repository.set_campaign_phase(db_path, "generating_assets")
            elif job_type in job_repository.LLM_SCENE_JOB_TYPES | job_repository.LLM_POPULATION_JOB_TYPES:
                job_repository.set_campaign_phase(db_path, "generating_assets")
            try:
                t0 = time.monotonic()
                await _execute_job(save_id, db_path, job)
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                LOG.info(
                    "job done save=%s id=%s type=%s ms=%s",
                    save_id,
                    job.get("id"),
                    job.get("job_type"),
                    elapsed_ms,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.exception("job %s failed: %s", job.get("id"), exc)
                job_repository.mark_job_failed(db_path, int(job["id"]), str(exc))
    finally:
        lock.release()


async def _execute_job(save_id: str, db_path: str, job: dict[str, Any]) -> None:
    job_id = int(job["id"])
    job_type = str(job.get("job_type") or "")
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}

    if job_type == "interactive_turn":
        from titan.fugassa import game_session

        result = await game_session.run_interactive_turn_job(
            save_id,
            db_path,
            owner=payload.get("owner"),
            player_text=str(payload.get("player_text") or ""),
            opening_bootstrap=bool(payload.get("opening_bootstrap")),
            job_id=job_id,
        )
        job_repository.mark_job_completed(db_path, job_id, result=result)
        batch_id = str(job.get("batch_id") or "")
        enqueue_sd_jobs_for_queued_assets(save_id, db_path, batch_id=batch_id)
        ensure_worker_scheduled(save_id, db_path)
        return

    if job_type == "scene_prompt_llm":
        from titan.fugassa import config_store, game_session, scene_prompt_engine

        asset_id = int(payload.get("asset_id") or 0)
        cfg = config_store.load()
        state = game_session.load_game_state(save_id, exclude_running_job_id=job_id)
        conn_asset = sqlite3.connect(db_path)
        conn_asset.row_factory = sqlite3.Row
        try:
            row = conn_asset.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            asset = dict(row) if row else {}
        finally:
            conn_asset.close()
        if not asset:
            job_repository.mark_job_failed(db_path, job_id, "asset_not_found")
            return
        prompts = await scene_prompt_engine.generate_scene_prompts_for_asset(
            asset,
            state=state,
            db_path=db_path,
            owner=payload.get("owner"),
            llm_enabled=bool(cfg.get("llm_enabled", True)),
        )
        scene_prompt_engine.apply_prompts_to_asset(
            db_path,
            asset_id,
            positive=str(prompts.get("positive_prompt") or ""),
            negative=str(prompts.get("negative_prompt") or ""),
            prompt_seed=prompts.get("prompt_seed") if isinstance(prompts.get("prompt_seed"), dict) else None,
        )
        if not str(prompts.get("positive_prompt") or "").strip():
            job_repository.mark_job_failed(db_path, job_id, "empty scene prompt after LLM/fallback")
            return
        job_repository.mark_job_completed(db_path, job_id, result=prompts)
        return

    if job_type == "location_population":
        from titan.fugassa import config_store, game_session, location_population_engine

        location_id = int(payload.get("location_id") or 0)
        if not location_id:
            job_repository.mark_job_failed(db_path, job_id, "missing location_id")
            return
        cfg = config_store.load()
        state = game_session.load_game_state(save_id, exclude_running_job_id=job_id)
        result = await location_population_engine.run_population_for_location(
            save_id,
            db_path,
            state,
            location_id=location_id,
            owner=payload.get("owner"),
            llm_enabled=bool(cfg.get("llm_enabled", True)),
            opening_excerpt=str(payload.get("opening_excerpt") or ""),
        )
        game_session.save_game_state(save_id, state)
        job_repository.mark_job_completed(db_path, job_id, result=result)
        ensure_worker_scheduled(save_id, db_path)
        return

    if job_type == "sd_generate":
        from titan.fugassa import asset_worker, config_store, game_session

        asset_id = int(payload.get("asset_id") or 0)
        cfg = config_store.load()
        state = game_session.load_game_state(save_id, exclude_running_job_id=job_id)
        theme = str((state.get("world_profile") or {}).get("theme") or "fantasy")
        result = await asset_worker.generate_asset_by_id(
            save_id,
            db_path,
            game_session.save_path_for(save_id),
            asset_id=asset_id,
            images_enabled=bool(cfg.get("images_enabled", True)),
            theme=theme,
            state=state,
            image_style_default=str(cfg.get("image_style_default") or "") or None,
        )
        if result.get("success"):
            job_repository.mark_job_completed(db_path, job_id, result=result)
            return
        err = str(result.get("error") or "sd_generate failed")
        attempt = int(job.get("attempts") or 1)
        if attempt < int(job.get("max_attempts") or 3):
            backoff = SD_BACKOFF_SEC[min(attempt - 1, len(SD_BACKOFF_SEC) - 1)]
            job_repository.requeue_job(db_path, job_id, error=err)
            await asyncio.sleep(backoff)
            ensure_worker_scheduled(save_id, db_path)
            return
        job_repository.mark_job_failed(db_path, job_id, err)
        return

    job_repository.mark_job_failed(db_path, job_id, f"unknown job_type: {job_type}")


def _load_asset_row(db_path: str, asset_id: int) -> dict[str, Any]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def enqueue_scene_asset_pipeline(
    db_path: str,
    save_id: str,
    *,
    asset_id: int,
    batch_id: str,
    priority: int = 200,
    owner: str | None = None,
) -> list[int]:
    """Enqueue scene_prompt_llm → sd_generate chain when auto prompt is needed."""
    from titan.fugassa import scene_prompt_engine

    if job_repository.pipeline_job_exists_for_asset(db_path, save_id, asset_id):
        return []
    asset = _load_asset_row(db_path, asset_id)
    if not asset:
        return []
    job_ids: list[int] = []
    depends_on: int | None = None
    if scene_prompt_engine.asset_needs_scene_prompt_llm(asset):
        prompt_job = job_repository.insert_job(
            db_path,
            save_id=save_id,
            job_type="scene_prompt_llm",
            batch_id=batch_id,
            payload={"asset_id": asset_id, "owner": owner},
            priority=priority,
        )
        job_ids.append(prompt_job)
        depends_on = prompt_job
        sd_priority = priority + 10
    else:
        sd_priority = priority
    sd_job = job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="sd_generate",
        batch_id=batch_id,
        payload={"asset_id": asset_id},
        priority=sd_priority,
        depends_on_id=depends_on,
    )
    job_ids.append(sd_job)
    return job_ids


def enqueue_sd_jobs_for_queued_assets(
    save_id: str,
    db_path: str,
    *,
    batch_id: str,
    priority: int = 200,
) -> list[int]:
    """Create sd_generate jobs for assets.status=queued without duplicate pending jobs."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    job_ids: list[int] = []
    try:
        rows = conn.execute(
            "SELECT id FROM assets WHERE status = 'queued' ORDER BY id ASC"
        ).fetchall()
        for row in rows:
            asset_id = int(row["id"])
            if job_repository.pipeline_job_exists_for_asset(db_path, save_id, asset_id):
                continue
            job_ids.extend(
                enqueue_scene_asset_pipeline(
                    db_path,
                    save_id,
                    asset_id=asset_id,
                    batch_id=batch_id,
                    priority=priority,
                )
            )
    finally:
        conn.close()
    return job_ids


def reconcile_queued_asset_jobs(save_id: str, db_path: str) -> list[int]:
    """Recover queued assets whose pipeline jobs failed or were blocked by a bad dependency."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'failed',
                error = COALESCE(error, 'blocked: dependency failed'),
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE status = 'pending'
              AND depends_on_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM campaign_jobs d
                WHERE d.id = campaign_jobs.depends_on_id AND d.status = 'failed'
              )
            """,
            (_utc_now(), _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()

    batch_id = job_repository.new_batch_id(save_id)
    return enqueue_sd_jobs_for_queued_assets(
        save_id,
        db_path,
        batch_id=batch_id,
        priority=250,
    )


def enqueue_interactive_turn(
    db_path: str,
    save_id: str,
    *,
    owner: str | None,
    player_text: str = "",
    opening_bootstrap: bool = False,
    turn_number: int | None = None,
) -> str:
    batch_id = job_repository.new_batch_id(save_id, turn_number)
    job_repository.insert_job(
        db_path,
        save_id=save_id,
        job_type="interactive_turn",
        batch_id=batch_id,
        payload={
            "owner": owner,
            "player_text": player_text,
            "opening_bootstrap": opening_bootstrap,
        },
        priority=100,
        turn_number=turn_number,
    )
    job_repository.set_campaign_phase(db_path, "processing")
    return batch_id


def enqueue_sd_asset(
    db_path: str,
    save_id: str,
    *,
    asset_id: int,
    batch_id: str | None = None,
    priority: int = 150,
    owner: str | None = None,
) -> int:
    bid = batch_id or job_repository.new_batch_id(save_id)
    ids = enqueue_scene_asset_pipeline(
        db_path,
        save_id,
        asset_id=asset_id,
        batch_id=bid,
        priority=priority,
        owner=owner,
    )
    return ids[-1] if ids else 0


async def wait_for_batch_interactive_unlock(
    db_path: str,
    save_id: str,
    batch_id: str,
    *,
    timeout_sec: float = 600.0,
    poll_sec: float = 0.25,
) -> dict[str, Any]:
    """Block until interactive_turn for batch completes (for HTTP handlers if needed)."""
    elapsed = 0.0
    while elapsed < timeout_sec:
        ensure_worker_scheduled(save_id, db_path)
        if job_repository.batch_interactive_unlocked(db_path, save_id, batch_id):
            jobs = job_repository.list_jobs(db_path, save_id, batch_id=batch_id, limit=20)
            failed = [j for j in jobs if j.get("job_type") == "interactive_turn" and j.get("status") == "failed"]
            if failed:
                return {"unlocked": True, "success": False, "error": failed[0].get("error")}
            return {"unlocked": True, "success": True}
        await asyncio.sleep(poll_sec)
        elapsed += poll_sec
    return {"unlocked": False, "success": False, "error": "timeout waiting for interactive turn"}


def get_pipeline_status(
    db_path: str,
    save_id: str,
    *,
    batch_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    turn_number: int | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    pipeline = job_repository.pipeline_status(db_path, save_id, batch_id=batch_id)
    pipeline["jobs"] = job_repository.list_jobs(
        db_path,
        save_id,
        batch_id=batch_id,
        status=status,
        job_type=job_type,
        turn_number=turn_number,
        limit=limit,
    )
    return pipeline

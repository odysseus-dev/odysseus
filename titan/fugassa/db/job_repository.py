"""Persistent campaign job queue — per-save FIFO pipeline."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import sqlite_store

INTERACTIVE_JOB_TYPES = frozenset({"interactive_turn"})
LLM_SCENE_JOB_TYPES = frozenset({"scene_prompt_llm"})
LLM_POPULATION_JOB_TYPES = frozenset({"location_population"})
SD_JOB_TYPES = frozenset({"sd_generate"})
BLOCKING_JOB_TYPES = INTERACTIVE_JOB_TYPES | LLM_SCENE_JOB_TYPES | SD_JOB_TYPES

JOB_UI_LABELS: dict[str, str] = {
    "interactive_turn": "GM prepares response…",
    "sd_generate": "Generating scene…",
    "scene_prompt_llm": "Building image prompt…",
    "location_population": "Populating location…",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    sqlite_store.ensure_db(db_path)
    return sqlite_store.connect(db_path)


def new_batch_id(save_id: str, turn_number: int | None = None) -> str:
    turn = turn_number if turn_number is not None else 0
    return f"{save_id}:t{turn}:{uuid.uuid4().hex[:10]}"


def get_campaign_phase(db_path: str) -> str:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM save_meta WHERE key = 'campaign_phase'"
        ).fetchone()
        return str(row["value"]) if row and row["value"] else "idle"
    finally:
        conn.close()


def set_campaign_phase(db_path: str, phase: str) -> None:
    conn = _connect(db_path)
    try:
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO save_meta (key, value, updated_at)
            VALUES ('campaign_phase', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (phase, now),
        )
        conn.commit()
    finally:
        conn.close()


def insert_job(
    db_path: str,
    *,
    save_id: str,
    job_type: str,
    batch_id: str,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    turn_number: int | None = None,
    depends_on_id: int | None = None,
    max_attempts: int = 3,
) -> int:
    code = f"{save_id}:{job_type}:{uuid.uuid4().hex[:12]}"
    now = _utc_now()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO campaign_jobs (
                save_id, code, job_type, status, priority, turn_number, batch_id,
                depends_on_id, payload_json, attempts, max_attempts,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                save_id,
                code,
                job_type,
                priority,
                turn_number,
                batch_id,
                depends_on_id,
                json.dumps(payload or {}, ensure_ascii=False),
                max_attempts,
                now,
                now,
            ),
        )
        job_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        return job_id
    finally:
        conn.close()


def claim_next_job(db_path: str, save_id: str) -> dict[str, Any] | None:
    """Claim oldest pending job whose dependency (if any) is completed."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT j.* FROM campaign_jobs j
            WHERE j.save_id = ? AND j.status = 'pending'
              AND (
                j.depends_on_id IS NULL
                OR EXISTS (
                  SELECT 1 FROM campaign_jobs d
                  WHERE d.id = j.depends_on_id AND d.status = 'completed'
                )
              )
            ORDER BY j.priority ASC, j.id ASC
            LIMIT 1
            """,
            (save_id,),
        ).fetchone()
        if not row:
            return None
        now = _utc_now()
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'running', started_at = ?, updated_at = ?,
                attempts = attempts + 1
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, row["id"]),
        )
        if conn.total_changes == 0:
            conn.commit()
            return None
        conn.commit()
        out = dict(row)
        out["status"] = "running"
        if out.get("payload_json"):
            try:
                out["payload"] = json.loads(out["payload_json"])
            except json.JSONDecodeError:
                out["payload"] = {}
        else:
            out["payload"] = {}
        return out
    finally:
        conn.close()


def mark_job_completed(
    db_path: str,
    job_id: int,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    now = _utc_now()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'completed', result_json = ?, finished_at = ?, updated_at = ?, error = NULL
            WHERE id = ?
            """,
            (json.dumps(result or {}, ensure_ascii=False), now, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_job_failed(db_path: str, job_id: int, error: str) -> None:
    now = _utc_now()
    err = str(error)[:2000]
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'failed', error = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (err, now, now, job_id),
        )
        blocked = f"dependency failed: {err[:500]}"
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'failed', error = ?, finished_at = ?, updated_at = ?
            WHERE depends_on_id = ? AND status = 'pending'
            """,
            (blocked, now, now, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def requeue_job(db_path: str, job_id: int, *, error: str | None = None) -> bool:
    """Return pending if attempts remain; otherwise mark failed."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT attempts, max_attempts FROM campaign_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return False
        if int(row["attempts"]) >= int(row["max_attempts"]):
            mark_job_failed(db_path, job_id, error or "max attempts exceeded")
            return False
        now = _utc_now()
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'pending', started_at = NULL, updated_at = ?, error = ?
            WHERE id = ?
            """,
            (now, (error or "")[:500] or None, job_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_jobs(
    db_path: str,
    save_id: str,
    *,
    batch_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    turn_number: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        clauses = ["save_id = ?"]
        params: list[Any] = [save_id]
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if job_type:
            clauses.append("job_type = ?")
            params.append(job_type)
        if turn_number is not None:
            clauses.append("turn_number = ?")
            params.append(int(turn_number))
        params.append(max(1, min(limit, 200)))
        rows = conn.execute(
            f"""
            SELECT * FROM campaign_jobs
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if item.get("payload_json"):
                try:
                    item["payload"] = json.loads(item["payload_json"])
                except json.JSONDecodeError:
                    item["payload"] = {}
            out.append(item)
        return out
    finally:
        conn.close()


def has_active_interactive_jobs(db_path: str, save_id: str) -> bool:
    """True while an interactive_turn job is pending or running (blocks reading/TTS)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM campaign_jobs
            WHERE save_id = ?
              AND job_type IN ('interactive_turn')
              AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (save_id,),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def has_active_jobs(db_path: str, save_id: str, *, batch_id: str | None = None) -> bool:
    conn = _connect(db_path)
    try:
        if batch_id:
            row = conn.execute(
                """
                SELECT 1 FROM campaign_jobs
                WHERE save_id = ? AND batch_id = ? AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (save_id, batch_id),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1 FROM campaign_jobs
                WHERE save_id = ? AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (save_id,),
            ).fetchone()
        return bool(row)
    finally:
        conn.close()


def batch_interactive_unlocked(db_path: str, save_id: str, batch_id: str) -> bool:
    """True when interactive_turn for batch is done (completed or failed) and none pending/running."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN job_type = 'interactive_turn' AND status IN ('pending','running') THEN 1 ELSE 0 END) AS open_interactive,
              SUM(CASE WHEN job_type = 'interactive_turn' AND status IN ('completed','failed') THEN 1 ELSE 0 END) AS done_interactive
            FROM campaign_jobs
            WHERE save_id = ? AND batch_id = ?
            """,
            (save_id, batch_id),
        ).fetchone()
        if not row or not row[1]:
            return False
        return int(row[0] or 0) == 0
    finally:
        conn.close()


def turn_has_incomplete_jobs(db_path: str, save_id: str, turn_number: int) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM campaign_jobs
            WHERE save_id = ? AND turn_number = ? AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (save_id, turn_number),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def current_running_job(db_path: str, save_id: str) -> dict[str, Any] | None:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT * FROM campaign_jobs
            WHERE save_id = ? AND status = 'running'
            ORDER BY id ASC
            LIMIT 1
            """,
            (save_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def sd_job_exists_for_asset(db_path: str, save_id: str, asset_id: int) -> bool:
    return pipeline_job_exists_for_asset(db_path, save_id, asset_id)


def pipeline_job_exists_for_asset(db_path: str, save_id: str, asset_id: int) -> bool:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT payload_json, job_type FROM campaign_jobs
            WHERE save_id = ? AND job_type IN ('sd_generate', 'scene_prompt_llm')
              AND status IN ('pending', 'running')
            """,
            (save_id,),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row[0] or "{}")
            except json.JSONDecodeError:
                continue
            if int(payload.get("asset_id") or 0) == asset_id:
                return True
        return False
    finally:
        conn.close()


def retry_failed_job(db_path: str, save_id: str, job_id: int) -> bool:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM campaign_jobs WHERE id = ? AND save_id = ?",
            (job_id, save_id),
        ).fetchone()
        if not row or str(row["status"]) != "failed":
            return False
        now = _utc_now()
        conn.execute(
            """
            UPDATE campaign_jobs
            SET status = 'pending', error = NULL, started_at = NULL, finished_at = NULL,
                updated_at = ?, attempts = 0
            WHERE id = ? AND save_id = ?
            """,
            (now, job_id, save_id),
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def get_job(db_path: str, save_id: str, job_id: int) -> dict[str, Any] | None:
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM campaign_jobs WHERE id = ? AND save_id = ?",
            (job_id, save_id),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        if item.get("payload_json"):
            try:
                item["payload"] = json.loads(item["payload_json"])
            except json.JSONDecodeError:
                item["payload"] = {}
        if item.get("result_json"):
            try:
                item["result"] = json.loads(item["result_json"])
            except json.JSONDecodeError:
                item["result"] = {}
        return item
    finally:
        conn.close()


def pipeline_status(db_path: str, save_id: str, *, batch_id: str | None = None) -> dict[str, Any]:
    running = current_running_job(db_path, save_id)
    phase = get_campaign_phase(db_path)
    current_type = str(running["job_type"]) if running else None
    label = JOB_UI_LABELS.get(current_type or "", "")
    unlocked = True
    if batch_id:
        unlocked = batch_interactive_unlocked(db_path, save_id, batch_id)
    unlock_when = "interactive_turn completed for batch" if batch_id else None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE status IN ('queued', 'generating')"
        ).fetchone()
        queued_assets = int(row["n"] if row else 0)
    finally:
        conn.close()
    return {
        "campaign_phase": phase,
        "current_job": running,
        "current_job_type": current_type,
        "current_job_label": label,
        "pipeline_locked": has_active_jobs(db_path, save_id, batch_id=batch_id) if batch_id else has_active_jobs(db_path, save_id),
        "interactive_unlocked": unlocked,
        "blocking_phase": current_type,
        "unlock_when": unlock_when if batch_id and not unlocked else None,
        "queued_assets": queued_assets,
    }

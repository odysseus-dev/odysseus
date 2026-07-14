"""Assets table CRUD — ADR §L kanon."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def next_asset_version_code(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_code: str,
    asset_type: str,
) -> tuple[str, int]:
    prefix = f"{entity_type}:{entity_code}:{asset_type}:v"
    row = conn.execute(
        """
        SELECT code FROM assets
        WHERE code LIKE ? || '%'
        ORDER BY id DESC LIMIT 1
        """,
        (prefix,),
    ).fetchone()
    version = 1
    if row:
        code = str(row["code"])
        try:
            version = int(code.rsplit(":v", 1)[-1]) + 1
        except (IndexError, ValueError):
            version = 2
    return f"{prefix}{version}", version


def archive_active_assets(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    asset_type: str,
) -> None:
    conn.execute(
        """
        UPDATE assets
        SET status = 'archived', updated_at = ?
        WHERE entity_type = ? AND entity_id = ? AND asset_type = ?
          AND status IN ('ready', 'generating', 'queued')
        """,
        (_utc_now(), entity_type, entity_id, asset_type),
    )


def insert_asset(
    conn: sqlite3.Connection,
    *,
    code: str,
    asset_type: str,
    entity_type: str,
    entity_id: int,
    status: str = "ready",
    prompt_source: str = "auto",
    prompt: str | None = None,
    negative_prompt: str | None = None,
    file_path: str | None = None,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_by_type: str = "system",
) -> int:
    now = _utc_now()
    cur = conn.execute(
        """
        INSERT INTO assets (
            code, asset_type, entity_type, entity_id, title, status, prompt_source,
            prompt, negative_prompt, file_path, mime_type, metadata_json,
            created_by_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'image/png', ?, ?, ?, ?)
        """,
        (
            code,
            asset_type,
            entity_type,
            entity_id,
            title,
            status,
            prompt_source,
            prompt,
            negative_prompt,
            file_path,
            json.dumps(metadata or {}, ensure_ascii=False) if metadata else None,
            created_by_type,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def register_portrait_file(
    db_path: str,
    *,
    player_character_id: int,
    player_code: str,
    relative_file_path: str,
    prompt: str | None = None,
    negative_prompt: str | None = None,
    prompt_source: str = "auto",
    archive_previous: bool = True,
) -> dict[str, Any]:
    """Register an on-disk portrait PNG as assets row and link player_character."""
    conn = _connect(db_path)
    try:
        if archive_previous:
            archive_active_assets(
                conn,
                entity_type="player_character",
                entity_id=player_character_id,
                asset_type="portrait",
            )
        code, _version = next_asset_version_code(
            conn,
            entity_type="player_character",
            entity_code=player_code,
            asset_type="portrait",
        )
        asset_id = insert_asset(
            conn,
            code=code,
            asset_type="portrait",
            entity_type="player_character",
            entity_id=player_character_id,
            status="ready",
            prompt_source=prompt_source,
            prompt=prompt,
            negative_prompt=negative_prompt,
            file_path=relative_file_path,
            title=f"Portrait {player_code}",
            metadata={"source": "wizard_create"},
            created_by_type="player",
        )
        conn.execute(
            """
            UPDATE player_characters
            SET portrait_asset_id = ?, portrait_path = ?, portrait_prompt = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (asset_id, relative_file_path, prompt, _utc_now(), player_character_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_dict(row) or {}
    finally:
        conn.close()


def get_active_asset(
    db_path: str,
    *,
    entity_type: str,
    entity_id: int,
    asset_type: str,
) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT * FROM assets
            WHERE entity_type = ? AND entity_id = ? AND asset_type = ?
              AND status = 'ready'
            ORDER BY id DESC LIMIT 1
            """,
            (entity_type, entity_id, asset_type),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def delete_assets_for_entities_conn(
    conn: sqlite3.Connection,
    generated_root: str,
    *,
    entity_type: str,
    asset_type: str,
    entity_ids: list[int],
) -> int:
    """Hard-delete asset rows (+ their PNG files on disk) for the given
    `(entity_type, asset_type, entity_id)` set, on an already-open
    connection/transaction. Used to clean up per-message chat scene images
    once their backing `turn_history` row condenses into the campaign digest
    — those images "disappear" for good at that point, unlike location/NPC
    portraits which stay around across regenerations. Caller commits."""
    if not entity_ids:
        return 0
    placeholders = ",".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"""
        SELECT id, file_path FROM assets
        WHERE entity_type = ? AND asset_type = ? AND entity_id IN ({placeholders})
        """,
        (entity_type, asset_type, *entity_ids),
    ).fetchall()
    for row in rows:
        file_path = row["file_path"]
        if file_path:
            full = os.path.join(generated_root, file_path)
            try:
                os.remove(full)
            except OSError:
                pass
    ids = [int(r["id"]) for r in rows]
    if ids:
        id_placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM assets WHERE id IN ({id_placeholders})", ids)
    return len(ids)


def delete_assets_for_entities(
    db_path: str,
    generated_root: str,
    *,
    entity_type: str,
    asset_type: str,
    entity_ids: list[int],
) -> int:
    conn = _connect(db_path)
    try:
        count = delete_assets_for_entities_conn(
            conn, generated_root, entity_type=entity_type, asset_type=asset_type, entity_ids=entity_ids
        )
        conn.commit()
        return count
    finally:
        conn.close()


def list_assets(
    db_path: str,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    asset_type: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if asset_type:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if not include_archived:
            clauses.append("status != 'archived'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM assets{where} ORDER BY id DESC",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        conn.close()


def rebuild_manifest(db_path: str, generated_dir: str) -> str:
    """Write generated/manifest.json from ready assets (FE cache, rebuildable)."""
    assets = list_assets(db_path, include_archived=False)
    ready = [a for a in assets if a.get("status") == "ready" and a.get("file_path")]
    manifest = {
        "version": 1,
        "updated_at": _utc_now(),
        "assets": [
            {
                "id": a["id"],
                "code": a["code"],
                "asset_type": a["asset_type"],
                "entity_type": a["entity_type"],
                "entity_id": a["entity_id"],
                "file_path": a["file_path"],
            }
            for a in ready
        ],
    }
    os.makedirs(generated_dir, exist_ok=True)
    path = os.path.join(generated_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path

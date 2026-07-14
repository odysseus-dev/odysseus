"""Asset regen + prompt edit — ADR §L12."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa.db import asset_repository


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def patch_prompt(
    db_path: str,
    asset_id: int,
    *,
    positive_prompt: str | None = None,
    negative_prompt: str | None = None,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            return {"success": False, "error": "not_found"}
        updates: list[str] = []
        params: list[Any] = []
        if positive_prompt is not None:
            updates.append("prompt = ?")
            params.append(positive_prompt)
            updates.append("prompt_source = 'manual_edited'")
        if negative_prompt is not None:
            updates.append("negative_prompt = ?")
            params.append(negative_prompt)
        if not updates:
            return {"success": True, "asset": dict(row)}
        updates.append("updated_at = ?")
        params.append(_utc_now())
        params.append(asset_id)
        conn.execute(f"UPDATE assets SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return {"success": True, "asset": dict(row) if row else None}
    finally:
        conn.close()


def request_regenerate(
    db_path: str,
    asset_id: int,
    *,
    positive_prompt: str | None = None,
    negative_prompt: str | None = None,
    use_auto_prompt: bool = False,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            return {"success": False, "error": "not_found"}
        asset = dict(row)
        asset_repository.archive_active_assets(
            conn,
            entity_type=str(asset["entity_type"]),
            entity_id=int(asset["entity_id"]),
            asset_type=str(asset["asset_type"]),
        )
        entity_code = f"{asset['entity_type']}_{asset['entity_id']}"
        code, _v = asset_repository.next_asset_version_code(
            conn,
            entity_type=str(asset["entity_type"]),
            entity_code=entity_code,
            asset_type=str(asset["asset_type"]),
        )
        prompt = None if use_auto_prompt else (positive_prompt or asset.get("prompt"))
        neg = negative_prompt if negative_prompt is not None else asset.get("negative_prompt")
        source = "auto" if use_auto_prompt else ("manual" if positive_prompt else str(asset.get("prompt_source") or "auto"))
        # Carry forward the previous row's `metadata_json` (prompt_seed etc.)
        # so a plain "regenerate with auto prompt" click still drafts from
        # the same context (e.g. an NPC's name/race, or a chat message's own
        # text) instead of silently degrading to the generic scene fallback.
        old_metadata: dict[str, Any] | None = None
        if asset.get("metadata_json"):
            try:
                old_metadata = json.loads(asset["metadata_json"])
            except (TypeError, ValueError):
                old_metadata = None
        new_id = asset_repository.insert_asset(
            conn,
            code=code,
            asset_type=str(asset["asset_type"]),
            entity_type=str(asset["entity_type"]),
            entity_id=int(asset["entity_id"]),
            status="queued",
            prompt_source=source,
            prompt=prompt,
            negative_prompt=neg,
            title=asset.get("title"),
            metadata=old_metadata,
            created_by_type="player",
        )
        conn.commit()
        new_row = conn.execute("SELECT * FROM assets WHERE id = ?", (new_id,)).fetchone()
        return {"success": True, "asset": dict(new_row) if new_row else None}
    finally:
        conn.close()


def regenerate_for_entity(
    db_path: str,
    *,
    entity_type: str,
    entity_id: int,
    asset_type: str,
    positive_prompt: str | None = None,
    negative_prompt: str | None = None,
    use_auto_prompt: bool = False,
    metadata: dict[str, Any] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Create-or-regenerate: works whether an active asset already exists for
    this entity/asset_type or not — the single backend for every "generate
    image" button (locations, NPC portraits, per-message chat scenes), not
    just the id-scoped `/assets/{id}/regenerate` route which requires an
    existing row."""
    active = asset_repository.get_active_asset(
        db_path,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_type=asset_type,
    )
    if active:
        return request_regenerate(
            db_path,
            int(active["id"]),
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            use_auto_prompt=use_auto_prompt,
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        code, _ = asset_repository.next_asset_version_code(
            conn,
            entity_type=entity_type,
            entity_code=f"{entity_type}_{entity_id}",
            asset_type=asset_type,
        )
        new_id = asset_repository.insert_asset(
            conn,
            code=code,
            asset_type=asset_type,
            entity_type=entity_type,
            entity_id=entity_id,
            status="queued",
            prompt_source="auto" if use_auto_prompt else "manual",
            prompt=positive_prompt,
            negative_prompt=negative_prompt,
            title=title,
            metadata=metadata,
            created_by_type="player",
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (new_id,)).fetchone()
        return {"success": True, "asset": dict(row) if row else None}
    finally:
        conn.close()

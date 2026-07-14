"""Background SD queue — ADR §L11 reading phase."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import asset_gen, asset_prompts
from titan.fugassa.db import asset_repository
from titan.fugassa.paths import generated_dir

LOG = logging.getLogger("titan.fugassa.asset_worker")

_turn_phases: dict[str, str] = {}


def _asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    raw = asset.get("metadata_json")
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
        return meta if isinstance(meta, dict) else {}
    except json.JSONDecodeError:
        return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def set_turn_phase(save_id: str, phase: str) -> None:
    _turn_phases[save_id] = phase


def get_turn_phase(save_id: str) -> str:
    return _turn_phases.get(save_id, "reading")


def preempt(save_id: str) -> None:
    """Deprecated — FIFO pipeline no longer kills running SD (Q6). Kept for call-site compat."""
    LOG.debug("preempt ignored for save %s (strict FIFO pipeline)", save_id)


async def generate_asset_by_id(
    save_id: str,
    db_path: str,
    save_path: str,
    *,
    asset_id: int,
    images_enabled: bool = True,
    theme: str = "fantasy",
    state: dict[str, Any] | None = None,
    image_style_default: str | None = None,
) -> dict[str, Any]:
    """Generate one asset row by id — used by campaign_job_runner sd_generate jobs."""
    if not images_enabled:
        return {"success": False, "error": "images_disabled", "asset_id": asset_id}
    if not db_path or not os.path.isfile(db_path):
        return {"success": False, "error": "no_db", "asset_id": asset_id}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if not row:
            return {"success": False, "error": "asset_not_found", "asset_id": asset_id}
        asset = dict(row)
        conn.execute(
            "UPDATE assets SET status = 'generating', updated_at = ? WHERE id = ?",
            (_utc_now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

    asset_type = str(asset.get("asset_type"))
    entity_type = str(asset.get("entity_type"))
    entity_id = int(asset.get("entity_id"))

    positive = str(asset.get("prompt") or "").strip()
    negative = str(asset.get("negative_prompt") or "").strip()
    if not positive:
        meta = {}
        if asset.get("metadata_json"):
            try:
                meta = json.loads(asset["metadata_json"])
            except json.JSONDecodeError:
                pass
        positive, negative = asset_prompts.prompt_for_asset_request(
            meta if isinstance(meta, dict) else {"asset_type": asset_type},
            state=state,
            theme=theme,
        )

    subdir = "portraits" if asset_type == "portrait" else "scenes"
    filename = f"{entity_type}_{entity_id}_v{asset_id}.png"
    rel_path = f"{subdir}/{filename}"
    dest = os.path.join(generated_dir(save_path), rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    cast_count = 1
    if asset_type == "scene":
        meta = _asset_metadata(asset)
        prompt_seed = meta.get("prompt_seed") if isinstance(meta.get("prompt_seed"), dict) else {}
        cast_block = str((prompt_seed or {}).get("scene_characters") or "")
        from titan.fugassa.scene_character_context import cast_prompt_stats

        has_hero, supporting_count, cast_count = cast_prompt_stats(cast_block)
        positive = asset_prompts.sanitize_scene_generation_prompt(
            positive,
            cast_count=cast_count,
            has_hero=has_hero,
            supporting_count=supporting_count,
        )
        if cast_count >= 2 or supporting_count:
            extra_neg = (
                "merged face, solo portrait, cropped, close-up, duplicate person, "
                "face swap, wrong protagonist, hero blended with npc"
            )
            if extra_neg.split(",")[0] not in negative.lower():
                negative = f"{negative}, {extra_neg}"

    gen_kwargs = dict(
        positive_prompt=positive,
        negative_prompt=negative or asset_prompts.default_negative(),
        theme=theme,
        campaign_style=asset_gen.image_style_from_state(state) or None,
        image_style_default=image_style_default,
        dest_path=dest,
    )
    wp = state.get("world_profile") or {}
    theme_label, theme_facets = asset_prompts.scene_theme_bundle(theme, wp)
    gen_kwargs["theme_label"] = theme_label
    gen_kwargs["theme_facets"] = theme_facets
    if asset_type == "scene" and asset_gen._scene_two_pass_enabled():
        result = await asset_gen.generate_scene_two_pass(**gen_kwargs)
    else:
        result = await asset_gen.generate_image(asset_type=asset_type, **gen_kwargs)

    if not result.get("success"):
        _fail(db_path, asset_id, str(result.get("error") or "unknown"))
        return {"success": False, "error": result.get("error"), "asset_id": asset_id}

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE assets SET status = 'ready', file_path = ?, prompt = ?,
                negative_prompt = ?, updated_at = ? WHERE id = ?
            """,
            (rel_path, positive, negative, _utc_now(), asset_id),
        )
        if entity_type == "player_character" and asset_type == "portrait":
            conn.execute(
                """
                UPDATE player_characters
                SET portrait_asset_id = ?, portrait_path = ?, portrait_prompt = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (asset_id, rel_path, positive, _utc_now(), entity_id),
            )
        elif entity_type == "location" and asset_type == "scene":
            conn.execute(
                """
                UPDATE locations SET image_path = ?, image_prompt = ?, updated_at = ?
                WHERE id = ?
                """,
                (rel_path, positive, _utc_now(), entity_id),
            )
        elif entity_type == "npc" and asset_type == "portrait":
            conn.execute(
                """
                UPDATE npcs SET portrait_asset_id = ?, portrait_path = ?, portrait_prompt = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (asset_id, rel_path, positive, _utc_now(), entity_id),
            )
        conn.commit()
    finally:
        conn.close()

    asset_repository.rebuild_manifest(db_path, generated_dir(save_path))
    return {"success": True, "asset_id": asset_id, "file_path": rel_path}


async def drain_once(
    save_id: str,
    db_path: str,
    save_path: str,
    *,
    images_enabled: bool = True,
    theme: str = "fantasy",
    state: dict[str, Any] | None = None,
    image_style_default: str | None = None,
) -> dict[str, Any]:
    if not images_enabled:
        return {"drained": 0, "reason": "images_disabled"}
    if not db_path or not os.path.isfile(db_path):
        return {"drained": 0, "reason": "no_db"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM assets WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return {"drained": 0, "reason": "empty_queue"}
        asset_id = int(row["id"])
    finally:
        conn.close()

    result = await generate_asset_by_id(
        save_id,
        db_path,
        save_path,
        asset_id=asset_id,
        images_enabled=images_enabled,
        theme=theme,
        state=state,
        image_style_default=image_style_default,
    )
    if result.get("success"):
        return {"drained": 1, **result}
    return {"drained": 0, "reason": "generate_failed", "error": result.get("error")}


def _requeue(db_path: str, asset_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE assets SET status = 'queued', updated_at = ? WHERE id = ?",
            (_utc_now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fail(db_path: str, asset_id: int, error: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            UPDATE assets SET status = 'failed', metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps({"error": error}), _utc_now(), asset_id),
        )
        conn.commit()
    finally:
        conn.close()

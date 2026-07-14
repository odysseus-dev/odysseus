"""NPC portrait SD prompts — LLM subject tags + wizard merge (ADR §L4)."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import asset_prompts, wizard_json as wj

LOG = logging.getLogger("titan.fugassa.npc_portrait_prompts")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _campaign_context(state: dict[str, Any]) -> tuple[str, str, str]:
    wp = state.get("world_profile") or {}
    theme = str(wp.get("theme") or "fantasy").strip() or "fantasy"
    style = str(wp.get("image_style") or wp.get("theme") or "fantasy").strip() or "fantasy"
    world_info = str(wp.get("world_information") or "")[:4000]
    return theme, style, world_info


def deterministic_npc_portrait_prompts(
    *,
    name: str,
    race: str = "",
    class_role: str = "",
    backstory_summary: str = "",
    theme: str = "fantasy",
    style_override: str = "",
) -> dict[str, str]:
    """Tag-based portrait prompt when LLM is off or fails."""
    appearance = asset_prompts.prose_to_tags(backstory_summary, max_chars=320)
    if not appearance:
        appearance = asset_prompts.prose_to_tags(f"{race} {class_role}".strip(), max_chars=120)
    if not appearance:
        appearance = asset_prompts.build_portrait_prompt(
            name=name,
            race=race,
            class_role=class_role,
            theme=theme,
        )
    return wj.merge_portrait_sd_prompts(theme, name, style_override, appearance, "")


async def generate_npc_portrait_prompts(
    *,
    name: str,
    race: str = "",
    class_role: str = "",
    backstory_summary: str = "",
    theme: str = "fantasy",
    style_override: str = "",
    world_information: str = "",
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, str]:
    """LLM visual tags for one NPC; falls back to deterministic tags."""
    profile = ", ".join(p for p in (race, class_role) if str(p).strip())
    backstory = str(backstory_summary or "").strip() or f"{name}, {profile}".strip(", ")

    if llm_enabled:
        try:
            from titan.fugassa import wizard_engine
            from titan.fugassa.llm_client import FugassaLlmDisabled

            data = await wizard_engine.generatePortraitSdPrompts(
                theme_label=theme,
                player_name=name,
                backstory=backstory,
                world_information=world_information,
                style_override=style_override,
                llm_config=None,
                character_profile=profile,
                appearance_visual="",
                owner=owner,
                llm_enabled=True,
            )
            if data.get("valid") and str(data.get("positive_prompt") or "").strip():
                return {
                    "positive_prompt": str(data["positive_prompt"]).strip(),
                    "negative_prompt": str(data.get("negative_prompt") or wj.PORTRAIT_SD_NEGATIVE_BASE),
                    "source": "llm",
                }
        except FugassaLlmDisabled:
            llm_enabled = False
        except Exception as exc:  # noqa: BLE001
            LOG.warning("NPC portrait prompt LLM failed for %s: %s", name, exc)

    merged = deterministic_npc_portrait_prompts(
        name=name,
        race=race,
        class_role=class_role,
        backstory_summary=backstory,
        theme=theme,
        style_override=style_override,
    )
    merged["source"] = "deterministic"
    return merged


def set_npc_portrait_prompt_conn(
    conn: sqlite3.Connection,
    npc_id: int,
    positive_prompt: str,
) -> None:
    conn.execute(
        "UPDATE npcs SET portrait_prompt = ?, updated_at = ? WHERE id = ?",
        (str(positive_prompt or "").strip() or None, _utc_now(), int(npc_id)),
    )


async def assign_portrait_prompts_for_npc_ids(
    db_path: str,
    state: dict[str, Any],
    npc_ids: list[int],
    *,
    owner: str | None = None,
    llm_enabled: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Generate and store portrait_prompt for NPC rows missing one."""
    if not npc_ids:
        return {"updated": 0, "npc_ids": []}

    theme, style, world_info = _campaign_context(state)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    updated: list[int] = []
    try:
        for npc_id in npc_ids:
            row = conn.execute(
                """
                SELECT id, name, race, class_role, backstory_summary, portrait_prompt
                FROM npcs WHERE id = ?
                """,
                (int(npc_id),),
            ).fetchone()
            if not row:
                continue
            if not force and str(row["portrait_prompt"] or "").strip():
                continue
            prompts = await generate_npc_portrait_prompts(
                name=str(row["name"] or "NPC"),
                race=str(row["race"] or ""),
                class_role=str(row["class_role"] or ""),
                backstory_summary=str(row["backstory_summary"] or ""),
                theme=theme,
                style_override=style,
                world_information=world_info,
                owner=owner,
                llm_enabled=llm_enabled,
            )
            pos = str(prompts.get("positive_prompt") or "").strip()
            if not pos:
                continue
            set_npc_portrait_prompt_conn(conn, int(row["id"]), pos)
            updated.append(int(row["id"]))
        conn.commit()
    finally:
        conn.close()
    return {"updated": len(updated), "npc_ids": updated}


async def backfill_save_portrait_prompts(
    save_id: str,
    *,
    owner: str | None = None,
    llm_enabled: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from titan.fugassa import config_store
    from titan.fugassa.game_session import load_game_state
    from titan.fugassa.save_store import game_db_path

    cfg = config_store.load()
    if llm_enabled is None:
        llm_enabled = bool(cfg.get("llm_enabled", True))

    db_path = game_db_path(save_id)
    state = load_game_state(save_id)
    conn = sqlite3.connect(db_path)
    try:
        if force:
            ids = [int(r[0]) for r in conn.execute("SELECT id FROM npcs ORDER BY id").fetchall()]
        else:
            ids = [
                int(r[0])
                for r in conn.execute(
                    "SELECT id FROM npcs WHERE portrait_prompt IS NULL OR TRIM(portrait_prompt) = ''"
                ).fetchall()
            ]
    finally:
        conn.close()
    result = await assign_portrait_prompts_for_npc_ids(
        db_path,
        state,
        ids,
        owner=owner,
        llm_enabled=llm_enabled,
        force=force,
    )
    return {"save_id": save_id, "candidates": len(ids), **result}


def main() -> None:
    import sys

    from titan.fugassa.paths import SAVES_DIR

    names = sys.argv[1:] or ["Fugassa"]
    force = False
    if "--force" in names:
        force = True
        names = [n for n in names if n != "--force"]
    for name in names:
        stats = asyncio.run(backfill_save_portrait_prompts(name, force=force))
        print(stats)


if __name__ == "__main__":
    main()

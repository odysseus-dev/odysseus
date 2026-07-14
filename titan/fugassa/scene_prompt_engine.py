"""LLM scene prompts (✨ location / 📷 chat) + opening location description distill."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from titan.fugassa import asset_prompts, wizard_json as wj
from titan.fugassa.scene_character_context import (
    cast_prompt_stats,
    collect_scene_characters,
    format_characters_for_scene_prompt,
    primary_portrait_reference,
    split_hero_and_supporting,
)

LOG = logging.getLogger("titan.fugassa.scene_prompt_engine")


def _scene_narrative_from_gm_text(text: str) -> str:
    """Current-scene beat only; tolerates stale gm_response_parser in long-running workers."""
    try:
        from titan.fugassa.gm_response_parser import extract_current_scene_narrative

        return extract_current_scene_narrative(text)
    except ImportError:
        from titan.fugassa.gm_response_parser import _extract_narrative

        return _extract_narrative(text)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_generic_location_desc(description: str, name: str) -> bool:
    desc = str(description or "").strip()
    if not desc:
        return True
    if desc == f"You find yourself in {name}.":
        return True
    return bool(re.match(r"^You find yourself in\b", desc, re.IGNORECASE))


def _asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    raw = asset.get("metadata_json")
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
        return meta if isinstance(meta, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _scene_context_for_asset(
    asset: dict[str, Any],
    *,
    state: dict[str, Any],
    db_path: str | None = None,
) -> dict[str, str]:
    entity_type = str(asset.get("entity_type") or "")
    entity_id = int(asset.get("entity_id") or 0)
    meta = _asset_metadata(asset)
    seed = meta.get("prompt_seed") if isinstance(meta.get("prompt_seed"), dict) else meta
    seed = seed if isinstance(seed, dict) else {}
    loc = state.get("location_state") or {}
    wt = state.get("world_time") or {}
    wp = state.get("world_profile") or {}

    ctx: dict[str, str] = {
        "theme": str(wp.get("theme") or "fantasy"),
        "world_information": str(wp.get("world_information") or "")[:2000],
        "time_of_day": str(seed.get("time") or wt.get("time_of_day") or "day"),
        "weather": str(seed.get("weather") or wt.get("weather") or "clear"),
        "season": str(wt.get("season") or ""),
        "location_name": str(seed.get("name") or loc.get("name") or "unknown place"),
        "location_description": "",
        "biome": str(seed.get("biome") or loc.get("biome") or wp.get("biome") or ""),
        "scene_narrative": "",
        "player_action": "",
        "scene_kind": "location",
    }
    if entity_type == "other":
        ctx["scene_kind"] = "chat_message"
        ctx["location_description"] = str(loc.get("description") or "")[:200]
    else:
        ctx["location_description"] = str(seed.get("description") or loc.get("description") or "")

    if entity_type == "other" and db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT player_text, ai_text FROM turn_history WHERE turn_number = ?",
                (entity_id,),
            ).fetchone()
            if row:
                if row["player_text"]:
                    ctx["player_action"] = str(row["player_text"])[:600]
                if row["ai_text"]:
                    ctx["scene_narrative"] = _scene_narrative_from_gm_text(str(row["ai_text"]))[:2000]
        finally:
            conn.close()
        characters = collect_scene_characters(
            state=state,
            db_path=db_path,
            narrative=ctx.get("scene_narrative") or "",
            player_action=ctx.get("player_action") or "",
            include_player=True,
        )
        if characters:
            narrative = ctx.get("scene_narrative") or ""
            ctx["scene_characters"] = format_characters_for_scene_prompt(
                characters,
                narrative=narrative,
                player_action=ctx.get("player_action") or "",
            )
            has_hero, supporting_count, cast_total = cast_prompt_stats(ctx["scene_characters"])
            ctx["cast_has_hero"] = "1" if has_hero else "0"
            ctx["cast_supporting_count"] = str(supporting_count)
            ctx["cast_total"] = str(cast_total)
            ref = primary_portrait_reference(characters)
            if ref and ref.get("portrait_path"):
                ctx["scene_portrait_ref"] = str(ref["portrait_path"])
                ctx["scene_portrait_entity"] = f"{ref.get('entity_type')}:{ref.get('entity_id')}"
    elif entity_type == "location" and db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT name, description_short, description_long FROM locations WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row:
                ctx["location_name"] = str(row["name"] or ctx["location_name"])
                ctx["location_description"] = str(
                    row["description_long"] or row["description_short"] or ctx["location_description"]
                )
        finally:
            conn.close()

    return ctx


def _scene_cast_counts(characters: list[dict[str, Any]] | None) -> tuple[bool, int, int]:
    hero, supporting = split_hero_and_supporting(characters or [])
    return bool(hero), len(supporting), (1 if hero else 0) + len(supporting)


def _scene_negative_extras(*, has_hero: bool, supporting_count: int) -> str:
    base = "merged face, solo portrait, cropped, close-up, duplicate person"
    if has_hero and supporting_count:
        return (
            f"{base}, face swap, identical twins, two heads one body, "
            "hero blended with npc, wrong protagonist, npc as main subject"
        )
    if supporting_count >= 2:
        return f"{base}, face swap, identical twins"
    return base


def _scene_prompt_system_message(scene_kind: str) -> str:
    if scene_kind == "chat_message":
        return (
            "You write prompts for Stable Diffusion scene images for ONE RPG turn/moment.\n"
            'Output strict JSON only: {"scene_positive":"...","negative_extra":"..."}\n'
            "scene_positive: comma-separated VISUAL tags, MAX ~50 words (CLIP truncates longer prompts).\n"
            "COMPOSITION PRIORITY (strict tag order):\n"
            "1. PRIMARY CAST (NPCs in the beat) — main focal subject in foreground. "
            "When the player observes someone, draw THAT person as hero, not the player.\n"
            "2. ACTION — what is happening this turn (pose, gaze, gesture).\n"
            "3. SUPPORTING CAST — player hero and others as separate smaller figures; "
            "player is often observer at frame edge, not the largest figure.\n"
            "4. BACKDROP — location, weather, mood as soft background only (≤25% of tags).\n"
            "Include indoor/outdoor, architecture, lighting (e.g. arched windows, daylight) when in the beat.\n"
            "Use medium-wide or wide shot when supporting cast exists; hero must dominate the frame.\n"
            "NEVER portrait/waist-up/close-up framing when multiple people appear.\n"
            "START scene_positive with the exact campaign Genre/theme string — it may combine "
            "MULTIPLE genres (e.g. dark fantasy AND dystopian future). Include visual tags from "
            "ALL stated facets; do NOT collapse a hybrid campaign to pure medieval OR pure sci-fi.\n"
            "FORBID only generic modern corporate look (tie, necktie, business suit, office worker) "
            "unless the beat explicitly calls for it.\n"
            "When Campaign world lore is provided, honor its visual tone and technology level.\n"
            "Match the campaign checkpoint style; prefer clean anime/cel shading when style hint says anime.\n"
            "Do NOT visualize recap, round summary, bullet suggestions, or meta text.\n"
            "Do NOT add generic quality tags (masterpiece, 8k) — the server appends those.\n"
            "negative_extra: short extra negatives; forbid merged faces, wrong protagonist, npc as main subject."
        )
    return (
        "You write comma-separated Stable Diffusion TAG prompts for RPG LOCATION environment images.\n"
        'Output strict JSON only: {"scene_positive":"...","negative_extra":"..."}\n'
        "scene_positive MUST be comma-separated English TAGS only — NOT sentences, NOT prose paragraphs.\n"
        "Lead with campaign genre/theme tags, then architecture, terrain, biome, materials, props, "
        "lighting, weather, palette, and mood of the PLACE.\n"
        "Do NOT describe player actions, combat, NPCs, dialogue, or plot events.\n"
        "No characters in foreground.\n"
        "Do NOT add generic quality tags (masterpiece, 8k) — the server appends those.\n"
        "negative_extra: short extra negatives to forbid mismatches; empty string if none."
    )


def _scene_prompt_user_message(ctx: dict[str, str], scene_kind: str, *, style_hint: str) -> str:
    if scene_kind == "chat_message":
        parts = [
            f"Scene kind: {scene_kind} (single-turn snapshot — action over backdrop)\n",
            f"Genre/theme (may be a hybrid — include ALL facets): {ctx['theme']}\n",
        ]
        world_info = str(ctx.get("world_information") or "").strip()
        if world_info:
            parts.append(
                "Campaign world lore (visual tone, technology, aesthetic — honor this blend):\n"
                f"{world_info[:1500]}\n"
            )
        if ctx.get("player_action"):
            parts.append(f"Player action this turn:\n{ctx['player_action']}\n")
        if ctx.get("scene_narrative"):
            parts.append(
                "GM current-scene beat to visualize (NOT recap/summary — only this moment):\n"
                f"{ctx['scene_narrative'][:1500]}\n"
            )
        cast_block = str(ctx.get("scene_characters") or "").strip()
        if cast_block:
            parts.append(
                "CAST IDENTITY (HERO = primary NPC focal subject; player often supporting/observer — "
                "do NOT swap roles or merge identities):\n"
                f"{cast_block}\n"
            )
        parts.append(
            "Backdrop (environment only — sparse background tags, not the subject): "
            f"{ctx['location_name']}"
        )
        if ctx.get("location_description"):
            parts[-1] += f" — {ctx['location_description'][:200]}"
        parts[-1] += "\n"
        parts.append(
            f"Time: {ctx['time_of_day']}, weather: {ctx['weather']}, "
            f"season: {ctx.get('season') or 'n/a'}\n"
        )
        if style_hint:
            parts.append(f"Art style hint: {style_hint}\n")
        return "".join(parts)

    parts = [
        f"Scene kind: {scene_kind} (environment/place image — tags only, no story)\n",
        f"Campaign genre/theme (start your tag list with this): {ctx['theme']}\n",
        f"Location name: {ctx['location_name']}\n",
    ]
    if ctx.get("biome"):
        parts.append(f"Biome: {ctx['biome']}\n")
    if ctx.get("location_description"):
        parts.append(
            "Place description (extract static visual tags only — ignore events/actions):\n"
            f"{ctx['location_description'][:800]}\n"
        )
    parts.append(
        f"Time: {ctx['time_of_day']}, weather: {ctx['weather']}, "
        f"season: {ctx.get('season') or 'n/a'}\n"
    )
    parts.append("Do NOT include turn narrative or what characters are doing.\n")
    if style_hint:
        parts.append(f"Art style hint (include as tags): {style_hint}\n")
    return "".join(parts)


async def generate_scene_prompts_for_asset(
    asset: dict[str, Any],
    *,
    state: dict[str, Any],
    db_path: str,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> dict[str, Any]:
    """LLM SD prompt for scene assets; deterministic fallback on disable/failure."""
    ctx = _scene_context_for_asset(asset, state=state, db_path=db_path)
    theme = ctx["theme"]
    style_hint = str((state.get("world_profile") or {}).get("image_style") or "").strip()
    wp = state.get("world_profile") or {}
    theme_label, theme_facets = asset_prompts.scene_theme_bundle(theme, wp)
    entity_type = str(asset.get("entity_type") or "")
    scene_kind = str(ctx.get("scene_kind") or ("chat_message" if entity_type == "other" else "location"))
    prompt_seed = {
        **ctx,
        "scene_kind": scene_kind,
    }
    if scene_kind == "chat_message":
        prompt_seed["scene_action"] = ctx.get("scene_narrative") or ""
        if ctx.get("scene_characters"):
            prompt_seed["scene_characters"] = ctx["scene_characters"]
        if ctx.get("scene_portrait_ref"):
            prompt_seed["scene_portrait_ref"] = ctx["scene_portrait_ref"]

    if not llm_enabled:
        pos, neg = asset_prompts.prompt_for_asset_request(
            {"asset_type": "scene", "prompt_seed": prompt_seed},
            state=state,
            theme=theme,
        )
        if scene_kind == "location":
            pos = asset_prompts.normalize_tag_prompt(pos)
        elif scene_kind == "chat_message":
            pos = asset_prompts.apply_theme_to_scene_prompt(
                pos, theme, style_hint=style_hint, facets=theme_facets, theme_label=theme_label,
            )
            neg = asset_prompts.merge_scene_theme_negative(neg, theme, facets=theme_facets)
        return {
            "valid": True,
            "positive_prompt": pos,
            "negative_prompt": neg,
            "source": "deterministic",
            "prompt_seed": prompt_seed,
        }

    messages = [
        {"role": "system", "content": _scene_prompt_system_message(scene_kind)},
        {
            "role": "user",
            "content": _scene_prompt_user_message(ctx, scene_kind, style_hint=style_hint),
        },
    ]

    try:
        from titan.fugassa.llm_client import FugassaLlmDisabled, chat_completion

        raw = await chat_completion(messages, owner=owner, max_tokens=768, temperature=0.5)
        data = wj.parse_wizard_json_object(raw) or {}
        scene_positive = asset_prompts.normalize_tag_prompt(str(data.get("scene_positive") or "").strip())
        if len(scene_positive) > 8:
            neg_extra = str(data.get("negative_extra") or "").strip()
            neg = asset_prompts.default_negative(neg_extra if neg_extra else None)
            has_hero = str(ctx.get("cast_has_hero") or "") == "1"
            supporting_count = int(str(ctx.get("cast_supporting_count") or "0") or 0)
            cast_count = int(str(ctx.get("cast_total") or "0") or 0) or max(
                1, (1 if has_hero else 0) + supporting_count
            )
            scene_positive = asset_prompts.sanitize_scene_generation_prompt(
                scene_positive,
                cast_count=cast_count,
                has_hero=has_hero,
                supporting_count=supporting_count,
            )
            scene_positive = asset_prompts.apply_theme_to_scene_prompt(
                scene_positive,
                theme,
                style_hint=style_hint,
                facets=theme_facets,
                theme_label=theme_label,
            )
            if cast_count >= 2 or supporting_count:
                neg = f"{neg}, {_scene_negative_extras(has_hero=has_hero, supporting_count=supporting_count)}"
            neg = asset_prompts.merge_scene_theme_negative(neg, theme, facets=theme_facets)
            return {
                "valid": True,
                "positive_prompt": scene_positive,
                "negative_prompt": neg,
                "source": "llm",
                "raw": raw,
                "prompt_seed": prompt_seed,
            }
    except FugassaLlmDisabled:
        pass
    except Exception as exc:  # noqa: BLE001
        LOG.warning("scene prompt LLM failed: %s", exc)

    pos, neg = asset_prompts.prompt_for_asset_request(
        {"asset_type": "scene", "prompt_seed": prompt_seed},
        state=state,
        theme=theme,
    )
    pos = asset_prompts.normalize_tag_prompt(pos) if scene_kind == "location" else pos
    if scene_kind == "chat_message":
        pos = asset_prompts.apply_theme_to_scene_prompt(
            pos, theme, style_hint=style_hint, facets=theme_facets, theme_label=theme_label,
        )
        neg = asset_prompts.merge_scene_theme_negative(neg, theme, facets=theme_facets)
    return {
        "valid": True,
        "positive_prompt": pos,
        "negative_prompt": neg,
        "source": "fallback",
        "prompt_seed": prompt_seed,
    }


def apply_prompts_to_asset(
    db_path: str,
    asset_id: int,
    *,
    positive: str,
    negative: str,
    prompt_seed: dict[str, Any] | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        if isinstance(prompt_seed, dict) and prompt_seed:
            row = conn.execute("SELECT metadata_json FROM assets WHERE id = ?", (asset_id,)).fetchone()
            meta: dict[str, Any] = {}
            if row and row[0]:
                try:
                    parsed = json.loads(row[0])
                    if isinstance(parsed, dict):
                        meta = parsed
                except json.JSONDecodeError:
                    meta = {}
            meta["prompt_seed"] = {**(meta.get("prompt_seed") if isinstance(meta.get("prompt_seed"), dict) else {}), **prompt_seed}
            conn.execute(
                """
                UPDATE assets
                SET prompt = ?, negative_prompt = ?, prompt_source = 'auto', metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (positive, negative, json.dumps(meta), _utc_now(), asset_id),
            )
        else:
            conn.execute(
                """
                UPDATE assets
                SET prompt = ?, negative_prompt = ?, prompt_source = 'auto', updated_at = ?
                WHERE id = ?
                """,
                (positive, negative, _utc_now(), asset_id),
            )
        conn.commit()
    finally:
        conn.close()


async def distill_opening_location_description(
    db_path: str,
    state: dict[str, Any],
    *,
    gm_prose: str,
    owner: str | None = None,
    llm_enabled: bool = True,
) -> bool:
    """Replace generic wizard location seed with LLM-distilled place description (Q9)."""
    loc = dict(state.get("location_state") or {})
    name = str(loc.get("name") or "Starting Location").strip()
    desc = str(loc.get("description") or "")
    if not _is_generic_location_desc(desc, name):
        return False
    if not llm_enabled:
        return False

    wp = state.get("world_profile") or {}
    opening_hook = str(wp.get("opening_hook") or "").strip()[:1500]
    gm_excerpt = str(gm_prose or "").strip()[:2500]

    messages = [
        {
            "role": "system",
            "content": (
                "You write short RPG location descriptions for a game HUD.\n"
                'Return strict JSON only: {"description":"..."}\n'
                "description: 2-4 sentences, second-person present, sensory details of the PLACE only.\n"
                "Do NOT repeat the character waking up or player actions — describe the environment."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Location name: {name}\n"
                + (f"Wizard hook:\n{opening_hook}\n\n" if opening_hook else "")
                + f"GM opening scene:\n{gm_excerpt}\n"
            ),
        },
    ]

    try:
        from titan.fugassa.llm_client import chat_completion

        raw = await chat_completion(messages, owner=owner, max_tokens=512, temperature=0.4)
        data = wj.parse_wizard_json_object(raw) or {}
        new_desc = str(data.get("description") or "").strip()
        if len(new_desc) < 40:
            return False
    except Exception as exc:  # noqa: BLE001
        LOG.warning("opening location distill failed: %s", exc)
        return False

    loc["description"] = new_desc
    state["location_state"] = loc
    loc_id = loc.get("location_id") or state.get("_current_location_id")
    if db_path and loc_id:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                UPDATE locations
                SET description_short = ?, description_long = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_desc, new_desc, _utc_now(), int(loc_id)),
            )
            conn.commit()
        finally:
            conn.close()
    return True


def asset_needs_scene_prompt_llm(asset: dict[str, Any]) -> bool:
    if str(asset.get("asset_type") or "") != "scene":
        return False
    source = str(asset.get("prompt_source") or "")
    if source in ("manual", "manual_edited"):
        return False
    if str(asset.get("prompt") or "").strip():
        return False
    return True

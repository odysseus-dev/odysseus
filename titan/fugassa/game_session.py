"""Gameplay session — ADR §K turn pipeline."""

from __future__ import annotations

import logging
import os
import asyncio
import sqlite3
import time
from difflib import SequenceMatcher
from typing import Any

from titan.fugassa import archivist, campaign_chronicle, campaign_digest, narrative_movement, world_time_engine, context_builder, gm_runner, memory_context
from titan.fugassa import asset_worker
from titan.fugassa import campaign_job_runner
from titan.fugassa.db import job_repository
from titan.fugassa.db import save_pipeline_migration
from titan.fugassa import crafting_engine
from titan.fugassa import investigate_engine
from titan.fugassa import item_engine
from titan.fugassa.db import snapshot, state_repository, sqlite_store
from titan.fugassa.game_bootstrap import GAME_JSON, read_game_json, write_gm_guides
from titan.fugassa import grid_engine
from titan.fugassa.grid_engine import (
    build_map_cells,
    build_minimap_cells,
    available_travel_modes,
    move_cardinal,
    travel_to,
)
from titan.fugassa import config_store
from titan.fugassa.llm_client import FugassaLlmDisabled, chat_completion
from titan.fugassa.paths import GM_TEMPLATES_DIR, generated_dir
from titan.fugassa.save_store import game_db_path, save_dir
from titan.fugassa.turn_resolution import TurnResolution
from titan.fugassa.turn_resolver import apply_time_delta, enqueue_opening_scene, resolve_turn, run_engine_only_checks, sync_location_and_track

_DISPLAY_TEXT_SIZES = frozenset({"small", "normal", "large", "xlarge"})
_TTS_LANGS = frozenset({"en", "cs", "uk"})
_TTS_MODES = frozenset({"off", "manual", "auto"})


def normalize_display_settings(raw: Any) -> dict[str, str]:
    out = {"ui_text_size": "normal", "chat_text_size": "normal"}
    if not isinstance(raw, dict):
        return out
    ui = str(raw.get("ui_text_size") or "normal").strip().lower()
    chat = str(raw.get("chat_text_size") or "normal").strip().lower()
    if ui in _DISPLAY_TEXT_SIZES:
        out["ui_text_size"] = ui
    if chat in _DISPLAY_TEXT_SIZES:
        out["chat_text_size"] = chat
    return out


def _default_tts_lang() -> str:
    lang = str(config_store.load().get("language") or "cs").strip().lower()
    return lang if lang in _TTS_LANGS else "cs"


def normalize_tts_prefs(raw: Any, *, default_lang: str | None = None) -> dict[str, Any]:
    base_lang = default_lang if default_lang in _TTS_LANGS else _default_tts_lang()
    out: dict[str, Any] = {
        "enabled": True,
        "mode": "manual",
        "lang": base_lang,
        "speaker_id": 0,
        "speed": 1.0,
    }
    if not isinstance(raw, dict):
        return out
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    mode = str(raw.get("mode") or out["mode"]).strip().lower()
    if mode in _TTS_MODES:
        out["mode"] = mode
    lang = str(raw.get("lang") or out["lang"]).strip().lower()
    if lang in _TTS_LANGS:
        out["lang"] = lang
    try:
        sid = int(raw.get("speaker_id", out["speaker_id"]))
        out["speaker_id"] = max(0, min(9, sid))
    except (TypeError, ValueError):
        pass
    try:
        speed = float(raw.get("speed", out["speed"]))
        out["speed"] = max(0.75, min(1.5, speed))
    except (TypeError, ValueError):
        pass
    return out


LOG = logging.getLogger("titan.fugassa.game_session")


class GameSessionError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def _game_json_path(save_id: str) -> str:
    return os.path.join(save_dir(save_id), GAME_JSON)


def _save_path(save_id: str) -> str:
    return save_dir(save_id)


def save_path_for(save_id: str) -> str:
    return _save_path(save_id)


def _can_undo_save(save_id: str, db_path: str) -> bool:
    """Q7: undo only when autosave exists and no pipeline jobs are pending/running."""
    if not snapshot.has_autosave_prev(_save_path(save_id)):
        return False
    if job_repository.has_active_jobs(db_path, save_id):
        return False
    return True


def resolve_turn_phase(db_path: str, save_id: str) -> str:
    """Reading window is independent of background SD/asset jobs."""
    if job_repository.has_active_interactive_jobs(db_path, save_id):
        return "processing"
    return "reading"


def persist_turn_phase(save_id: str, phase: str, *, campaign_phase: str | None = None) -> None:
    """Write turn_phase to game.json so restarts and polls stay consistent."""
    from titan.fugassa.game_bootstrap import write_game_json

    path = _save_path(save_id)
    raw = read_game_json(path) or {}
    patch: dict[str, Any] = {**raw, "turn_phase": phase}
    if campaign_phase is not None:
        patch["campaign_phase"] = campaign_phase
    if raw.get("turn_phase") != phase or (
        campaign_phase is not None and raw.get("campaign_phase") != campaign_phase
    ):
        write_game_json(path, patch)
    asset_worker.set_turn_phase(save_id, phase)


def load_game_state(save_id: str, *, exclude_running_job_id: int | None = None) -> dict[str, Any]:
    path = _save_path(save_id)
    state = read_game_json(path)
    if not state:
        raise GameSessionError("Game state not found — create campaign first", "not_found")
    db_path = game_db_path(save_id)
    save_pipeline_migration.ensure_save_ready(
        save_id,
        db_path,
        save_path=path,
        exclude_running_job_id=exclude_running_job_id,
    )
    state = read_game_json(path)
    from titan.fugassa.save_state_repair import backfill_chat_metadata, backfill_portrait_prompts, backfill_property_holdings

    backfill_portrait_prompts(db_path, state)

    if backfill_property_holdings(db_path, state):
        from titan.fugassa.game_bootstrap import write_game_json

        write_game_json(path, state)
    if backfill_chat_metadata(state, db_path):
        from titan.fugassa.game_bootstrap import write_game_json

        write_game_json(path, state)
    from titan.fugassa.scene_summary_engine import dedupe_scene_summaries

    dedupe_scene_summaries(db_path)
    from titan.fugassa.theme_facet_engine import ensure_theme_facets_in_state

    if ensure_theme_facets_in_state(state):
        from titan.fugassa.game_bootstrap import write_game_json

        write_game_json(path, state)
    state = state_repository.enrich_state_from_sql(db_path, state)
    from titan.fugassa.currency_engine import ensure_currency_profile, normalize_tier_list

    ensure_currency_profile(state, repair=True)
    raw = read_game_json(path) or {}
    wp_raw = (raw.get("world_profile") or {}).get("currency")
    if not normalize_tier_list(wp_raw):
        from titan.fugassa.game_bootstrap import write_game_json

        write_game_json(path, {**raw, "world_profile": state.get("world_profile") or raw.get("world_profile") or {}})
    phase = job_repository.get_campaign_phase(db_path) or "idle"
    state["campaign_phase"] = phase
    turn_phase = resolve_turn_phase(db_path, save_id)
    state["turn_phase"] = turn_phase
    asset_worker.set_turn_phase(save_id, turn_phase)
    raw = read_game_json(path) or {}
    if str(raw.get("turn_phase") or "").strip() != turn_phase:
        from titan.fugassa.game_bootstrap import write_game_json

        write_game_json(path, {**raw, "turn_phase": turn_phase, "campaign_phase": phase})
    state["can_undo"] = _can_undo_save(save_id, db_path)
    state["save_id"] = save_id
    return state


def save_game_state(save_id: str, state: dict[str, Any]) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    state_repository.export_json_snapshot(db_path, state, _save_path(save_id))
    if os.path.isfile(db_path):
        sqlite_store.update_turn_number(db_path, int(state.get("turn") or 0))
    return state


def _apply_world_time(state: dict[str, Any], ts: dict[str, Any]) -> None:
    world_time_engine.apply_gm_timestamp(state, ts)


async def _call_gm(
    messages: list[dict[str, str]],
    *,
    owner: str | None,
    max_tokens: int = 4096,
) -> str:
    return await chat_completion(messages, owner=owner, max_tokens=max_tokens, temperature=0.7)


def _with_session_meta(save_id: str, state: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    state = dict(state)
    db_path = game_db_path(save_id)
    campaign_phase = job_repository.get_campaign_phase(db_path) or "idle"
    turn_phase = resolve_turn_phase(db_path, save_id)
    state["turn_phase"] = turn_phase
    state["campaign_phase"] = campaign_phase
    state["can_undo"] = _can_undo_save(save_id, db_path)
    from titan.fugassa import inventory_display

    wallet = inventory_display.wallet_from_state(state)
    out = {"state": state, "turn_phase": turn_phase, "wallet": wallet}
    if extra:
        out.update(extra)
    return out


async def run_interactive_turn_job(
    save_id: str,
    db_path: str,
    *,
    owner: str | None,
    player_text: str = "",
    opening_bootstrap: bool = False,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Core GM + archivist turn — no SD (sd_generate jobs enqueued separately)."""
    state = load_game_state(save_id, exclude_running_job_id=job_id)
    result = await _complete_gm_turn(
        save_id,
        state,
        owner=owner,
        player_text=player_text,
        opening_bootstrap=opening_bootstrap,
    )
    return {
        "assistant_text": result.get("assistant_text"),
        "turn_number": int((load_game_state(save_id, exclude_running_job_id=job_id).get("turn") or 0)),
        "has_valid_timestamp": result.get("has_valid_timestamp"),
        "quest": (result.get("turn_resolution") or {}).get("quest"),
    }


async def _complete_gm_turn(
    save_id: str,
    state: dict[str, Any],
    *,
    owner: str | None,
    player_text: str = "",
    opening_bootstrap: bool = False,
) -> dict[str, Any]:
    gm_notes = str(state.get("gm_guides_notes") or "")
    db_path = game_db_path(save_id)
    if not opening_bootstrap:
        campaign_chronicle.clear_pipeline_turn()

    if opening_bootstrap:
        messages = gm_runner.build_messages_for_history(state, gm_notes=gm_notes, opening_bootstrap=True)
        from titan.fugassa.turn_resolution import TurnResolution

        resolution = TurnResolution(mode="narrative_only", intent="opening")
        enqueue_opening_scene(db_path, state, resolution, turn=int(state.get("turn") or 0))
    else:
        history = list(state.get("chat_history") or [])
        last = history[-1] if history else None
        if not (
            last
            and last.get("role") == "user"
            and str(last.get("content") or "").strip() == str(player_text or "").strip()
        ):
            history.append({"role": "user", "content": player_text})
            state["chat_history"] = history
        resolution = resolve_turn(state, player_text, db_path=db_path)
        campaign_chronicle.record_pipeline_step("resolve_turn", ms=0)
        apply_time_delta(state, resolution.time_delta_minutes)
        npc_brief_block = memory_context.build_npc_scene_briefs_block(db_path, state)
        memory_block = memory_context.build_scene_memory_block(db_path, state, player_text)
        pinned_facts_block = memory_context.build_pinned_facts_block(db_path)
        chronicle_hint_block = memory_context.build_chronicle_hint_block(db_path, state, player_text)
        scene_summary_block = memory_context.build_scene_summary_block(db_path, state)
        campaign_digest_block = campaign_digest.build_digest_block(db_path)
        messages = context_builder.build_gm_messages(
            state,
            gm_notes=gm_notes,
            turn_resolution=resolution,
            player_text=player_text,
            npc_brief_block=npc_brief_block,
            memory_block=memory_block,
            pinned_facts_block=pinned_facts_block,
            scene_summary_block=scene_summary_block,
            campaign_digest_block=campaign_digest_block,
            chronicle_hint_block=chronicle_hint_block,
        )

    prev_gm_excerpt = ""
    if not opening_bootstrap:
        for entry in reversed(state.get("chat_history") or []):
            if isinstance(entry, dict) and entry.get("role") == "assistant":
                prev_gm_excerpt = str(entry.get("content") or "")
                break

    raw = await _call_gm(messages, owner=owner, max_tokens=4096 if opening_bootstrap else 3072)
    campaign_chronicle.record_pipeline_step("gm_llm")
    parsed = archivist.parse_gm_response(raw)
    assistant_text = parsed["assistant_text"]
    ts = parsed["timestamp"]
    has_valid_ts = parsed["has_valid_timestamp"]
    is_placeholder = assistant_text in ("/", "...", "/...")
    has_safe = len(assistant_text.strip()) >= 20 and not is_placeholder

    if (
        not opening_bootstrap
        and prev_gm_excerpt.strip()
        and len(assistant_text.strip()) >= 80
        and SequenceMatcher(None, prev_gm_excerpt[:2000], assistant_text[:2000]).ratio() > 0.65
    ):
        retry_messages = list(messages) + [
            {
                "role": "system",
                "content": (
                    "REWRITE REQUIRED: Your draft repeated the previous GM reply. "
                    "Do not replay the same scene beats or dialogue. "
                    "If the player corrected a fact, confirm it in one sentence and advance with one new beat only."
                ),
            }
        ]
        raw = await _call_gm(retry_messages, owner=owner, max_tokens=2400)
        parsed = archivist.parse_gm_response(raw)
        assistant_text = parsed["assistant_text"]
        ts = parsed["timestamp"]
        has_valid_ts = parsed["has_valid_timestamp"]
        is_placeholder = assistant_text in ("/", "...", "/...")
        has_safe = len(assistant_text.strip()) >= 20 and not is_placeholder

    if opening_bootstrap and not has_safe:
        # A missing/malformed timestamp table is enrichment we can live
        # without (world_time simply keeps its wizard-derived starting
        # value — see game_bootstrap.starting_location_from_opening); but a
        # missing/placeholder narrative means the GM didn't actually write
        # an opening scene, which IS worth failing loudly on so the caller
        # can retry instead of silently leaving chat_history empty forever.
        raise GameSessionError("Opening scene generation failed — empty/invalid GM narrative", "gm_format")

    if has_valid_ts:
        _apply_world_time(state, ts)
    elif opening_bootstrap:
        from titan.fugassa.game_bootstrap import apply_opening_time_hint_to_world_time

        apply_opening_time_hint_to_world_time(state, overwrite=False)

    if not opening_bootstrap:
        post_move = narrative_movement.sync_post_gm_movement(
            db_path,
            state,
            gm_prose=assistant_text,
            gm_location=str(ts.get("location") or "").strip() or None,
            player_text=player_text,
        )
        if post_move and post_move.get("success"):
            from titan.fugassa.turn_resolver import sync_location_and_track

            sync_location_and_track(db_path, state, resolution)

    if not opening_bootstrap:
        state["turn"] = int(state.get("turn") or 0) + 1
    turn_number = int(state.get("turn") or 0)

    # `turn_number` tag lets the frontend request a scene image "for this
    # message" (POST /assets/generate entity_type=other) and know when the
    # backing `turn_history` row has condensed into the campaign digest
    # (ChatPanel hides the generate/view icon once `turn_number` rolls below
    # `min_active_turn` — see campaign_digest.get_min_active_turn).
    chat_snap = world_time_engine.snapshot_for_chat(state)
    if has_valid_ts and ts.get("location"):
        chat_snap["location"] = str(ts["location"]).strip()
        chat_snap["header"] = world_time_engine.format_chat_header(state.get("world_time") or {}, chat_snap["location"])
    from titan.fugassa.gm_response_parser import extract_current_scene_narrative
    from titan.fugassa.scene_character_context import scene_cast_metadata

    scene_narrative = extract_current_scene_narrative(assistant_text)
    scene_cast = scene_cast_metadata(
        state=state,
        db_path=db_path,
        narrative=scene_narrative,
        player_action=player_text,
    )
    history = list(state.get("chat_history") or [])
    history.append(
        {
            "role": "assistant",
            "content": assistant_text,
            "turn_number": turn_number,
            "ingame_time": chat_snap.get("header") or chat_snap.get("label"),
            "location": chat_snap.get("location") or "",
            "scene_cast": scene_cast,
        }
    )
    state["chat_history"] = history
    state_repository.sync_from_state(db_path, state, turn_resolution=resolution, turn_number=turn_number)
    cfg = config_store.load()
    quest_result: dict[str, Any] = {}
    if not opening_bootstrap:
        from titan.fugassa import quest_engine
        from titan.fugassa.db.state_repository import enrich_state_from_sql

        t0 = time.perf_counter()
        quest_result = quest_engine.evaluate_quests_after_gm(
            db_path,
            state,
            resolution,
            player_text=player_text,
            gm_prose=assistant_text,
            scene_cast=scene_cast,
        )
        campaign_chronicle.record_pipeline_step(
            "evaluate_quests_after_gm",
            ms=(time.perf_counter() - t0) * 1000,
            side_effects=[
                *(f"quest_complete:{q}" for q in quest_result.get("quests_completed") or []),
                *(f"quest_failed:{f.get('code')}" for f in quest_result.get("quests_failed") or []),
            ],
        )
        state = enrich_state_from_sql(db_path, state)
    t0 = time.perf_counter()
    archivist_result = await archivist.run_archivist(
        db_path,
        turn_number=turn_number,
        player_text=player_text,
        gm_prose=assistant_text,
        turn_resolution=resolution,
        state=state,
        ingame_time=world_time_engine.format_chat_header(
            state.get("world_time") or {},
            str(ts.get("location") or (state.get("location_state") or {}).get("name") or "").strip() or None,
        ),
        owner=owner,
        llm_enabled=bool(cfg.get("llm_enabled", True)),
        scene_cast=scene_cast,
    )
    campaign_chronicle.record_pipeline_step(
        "archivist",
        ms=(time.perf_counter() - t0) * 1000,
        side_effects=[f"memories:{archivist_result.get('memories_written', 0)}"],
    )
    archivist.sync_location_description_to_state(db_path, state)
    # ADR §7 workflow — after engine + GM + archivist have all written to the
    # DB, check whether the rolling chat window has overflowed and needs its
    # oldest 15 turns condensed into the campaign digest.
    try:
        t0 = time.perf_counter()
        await campaign_digest.maybe_condense(
            db_path,
            owner=owner,
            llm_enabled=bool(cfg.get("llm_enabled", True)),
            generated_root=generated_dir(_save_path(save_id)),
        )
        campaign_chronicle.record_pipeline_step("maybe_condense", ms=(time.perf_counter() - t0) * 1000)
    except Exception:  # noqa: BLE001 — digest maintenance must never break the turn
        LOG.warning("campaign digest condensation failed", exc_info=True)
        campaign_chronicle.record_pipeline_step("maybe_condense", ok=False)

    if not opening_bootstrap:
        campaign_chronicle.persist_pipeline_turn(db_path)

    if opening_bootstrap:
        try:
            from titan.fugassa import scene_prompt_engine

            await scene_prompt_engine.distill_opening_location_description(
                db_path,
                state,
                gm_prose=assistant_text,
                owner=owner,
                llm_enabled=bool(cfg.get("llm_enabled", True)),
            )
        except Exception:  # noqa: BLE001
            LOG.warning("opening location distill failed", exc_info=True)
        loc_id = int((state.get("location_state") or {}).get("location_id") or state.get("_current_location_id") or 0)
        if loc_id:
            from titan.fugassa import campaign_job_runner, location_population_engine

            job_id = location_population_engine.enqueue_population_job(
                db_path,
                save_id=save_id,
                location_id=loc_id,
                state=state,
                owner=owner,
                opening_excerpt=assistant_text[:2000],
                turn_number=turn_number,
            )
            if job_id:
                campaign_job_runner.ensure_worker_scheduled(save_id, db_path)

    save_game_state(save_id, state)
    state["turn_phase"] = "reading"
    persist_turn_phase(save_id, "reading")
    return {
        "state": state,
        "assistant_text": assistant_text,
        "raw": raw,
        "timestamp": ts,
        "has_valid_timestamp": has_valid_ts,
        "turn_resolution": resolution.to_dict(),
    }


async def bootstrap_opening(
    save_id: str,
    *,
    owner: str | None,
    llm_enabled: bool,
) -> dict[str, Any]:
    if not llm_enabled:
        raise FugassaLlmDisabled("LLM is disabled in Fugassa Settings")
    db_path = game_db_path(save_id)
    state = load_game_state(save_id)
    history = state.get("chat_history") or []
    if history:
        asset_worker.set_turn_phase(save_id, "reading")
        return _with_session_meta(save_id, state, {"skipped": True, "reason": "chat_history_not_empty"})

    batch_id = campaign_job_runner.enqueue_interactive_turn(
        db_path,
        save_id,
        owner=owner,
        opening_bootstrap=True,
        turn_number=int(state.get("turn") or 0),
    )
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
    wait = await campaign_job_runner.wait_for_batch_interactive_unlock(db_path, save_id, batch_id)
    if not wait.get("success"):
        err = wait.get("error") or "Opening bootstrap failed"
        raise GameSessionError(str(err), "gm_format")

    state = load_game_state(save_id)
    pipeline = campaign_job_runner.get_pipeline_status(db_path, save_id, batch_id=batch_id)
    return _with_session_meta(
        save_id,
        state,
        {
            "skipped": False,
            "batch_id": batch_id,
            "pipeline": pipeline,
            "interactive_unlocked": True,
        },
    )


async def submit_player_action(
    save_id: str,
    text: str,
    *,
    owner: str | None,
    llm_enabled: bool,
) -> dict[str, Any]:
    action = str(text or "").strip()
    if not action:
        raise GameSessionError("Action text is required", "invalid_action")
    if not llm_enabled:
        raise FugassaLlmDisabled("LLM is disabled in Fugassa Settings")

    db_path = game_db_path(save_id)
    snapshot.create_autosave_prev(_save_path(save_id))

    state = load_game_state(save_id)
    history = list(state.get("chat_history") or [])
    last = history[-1] if history else None
    if not (
        last
        and last.get("role") == "user"
        and str(last.get("content") or "").strip() == action
    ):
        history.append({"role": "user", "content": action})
        state["chat_history"] = history
    state["can_undo"] = True
    save_game_state(save_id, state)

    turn_number = int(state.get("turn") or 0) + 1
    batch_id = campaign_job_runner.enqueue_interactive_turn(
        db_path,
        save_id,
        owner=owner,
        player_text=action,
        opening_bootstrap=False,
        turn_number=turn_number,
    )
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
    persist_turn_phase(save_id, "processing", campaign_phase="processing")

    return _with_session_meta(
        save_id,
        load_game_state(save_id),
        {
            "player_action": action,
            "batch_id": batch_id,
            "pipeline_locked": True,
            "pipeline": campaign_job_runner.get_pipeline_status(db_path, save_id, batch_id=batch_id),
        },
    )


def undo_last_turn(save_id: str) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    state = load_game_state(save_id)
    if job_repository.has_active_jobs(db_path, save_id):
        raise GameSessionError("Cannot undo while pipeline jobs are still running", "jobs_pending")
    path = _save_path(save_id)
    if not snapshot.restore_autosave_prev(path):
        raise GameSessionError("Nothing to undo", "no_undo")
    snapshot.clear_autosave_prev(path)
    state = load_game_state(save_id)
    turn = int(state.get("turn") or 0)
    campaign_chronicle.purge_events_after_turn(db_path, turn)
    state["can_undo"] = False
    save_game_state(save_id, state)
    asset_worker.set_turn_phase(save_id, "reading")
    return _with_session_meta(save_id, state)


def get_pipeline_jobs(
    save_id: str,
    *,
    batch_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    turn_number: int | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
    return campaign_job_runner.get_pipeline_status(
        db_path,
        save_id,
        batch_id=batch_id,
        status=status,
        job_type=job_type,
        turn_number=turn_number,
        limit=limit,
    )


def get_pipeline_job_detail(save_id: str, job_id: int) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    job = job_repository.get_job(db_path, save_id, job_id)
    if not job:
        raise GameSessionError("Job not found", "not_found")
    return {"job": job}


def retry_pipeline_job(save_id: str, job_id: int) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    ok = job_repository.retry_failed_job(db_path, save_id, job_id)
    if not ok:
        raise GameSessionError("Job cannot be retried", "retry_failed")
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)
    return get_pipeline_jobs(save_id, limit=30)


def _schedule_sd_after_world_action(save_id: str, db_path: str) -> None:
    batch_id = job_repository.new_batch_id(save_id)
    campaign_job_runner.enqueue_sd_jobs_for_queued_assets(save_id, db_path, batch_id=batch_id)
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)


def _location_label(state: dict[str, Any]) -> str:
    loc = state.get("location_state") if isinstance(state.get("location_state"), dict) else {}
    return str(loc.get("place_label") or loc.get("name") or "unknown location").strip()


def _current_location_id(state: dict[str, Any]) -> int | None:
    raw = state.get("_current_location_id") or (state.get("location_state") or {}).get("location_id")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _hero_name(state: dict[str, Any]) -> str:
    party = state.get("party") or []
    if party and isinstance(party[0], dict):
        name = str(party[0].get("name") or "").strip()
        if name:
            return name
    return "Hero"


def _emit_engine_chronicle(db_path: str, events: list[campaign_chronicle.ChronicleEvent]) -> None:
    if db_path and events:
        campaign_chronicle.record_events(db_path, events)


def _snapshot_before_world_action(save_id: str) -> None:
    snapshot.create_autosave_prev(_save_path(save_id))


def get_map_data(save_id: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    return {
        "cells": build_map_cells(state),
        "minimap": build_minimap_cells(state),
        "travel_modes": available_travel_modes(state, db_path),
        "transport_options": grid_engine.list_transport_options(db_path),
        "active_transport": grid_engine.current_transport(db_path),
        "player": state.get("player") or {},
    }


def get_transport_options(save_id: str) -> dict[str, Any]:
    db_path = game_db_path(save_id)
    return {
        "options": grid_engine.list_transport_options(db_path),
        "active": grid_engine.current_transport(db_path),
    }


def set_transport(save_id: str, mode: str, item_id: int | None = None) -> dict[str, Any]:
    """ADR §J5c Map-screen picker — persists `active_transport_item_id`/
    `active_transport_mode` so the next `travel`/`move` calls price
    correctly, independent of any single travel request's `mode` param.
    """
    db_path = game_db_path(save_id)
    result = grid_engine.set_active_transport(db_path, item_id=item_id, mode=mode)
    if not result.get("success"):
        raise GameSessionError(f"Cannot select transport: {result.get('reason')}", result.get("reason", "error"))
    return result


def travel(save_id: str, x: int, y: int, z: int, mode: str = "walk", transport_item_id: int | None = None) -> dict[str, Any]:
    asset_worker.set_turn_phase(save_id, "processing")
    _snapshot_before_world_action(save_id)
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    # Map screen: the explicit `mode`/`transport_item_id` picked here becomes
    # the party's active transport (ADR §J5c "výběr na mapě -> active_transport
    # -> propisuje se do map UI"), not just a one-shot validation string.
    if str(mode or "walk").strip().lower() != "walk":
        grid_engine.set_active_transport(db_path, item_id=transport_item_id, mode=mode)
    player = state.get("player") or {}
    ox, oy = int(player.get("x", 0)), int(player.get("y", 0))
    from_label = _location_label(state)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    message = travel_to(state, x, y, z, mode, db_path=db_path)
    state["can_undo"] = True
    if message.startswith("Traveled"):
        cost = grid_engine.travel_cost(db_path, (ox, oy), (x, y))
        apply_time_delta(state, cost["time_delta_minutes"])
    save_game_state(save_id, state)
    engine_resolution = TurnResolution(mode="engine_only", intent="engine_only")
    sync_location_and_track(db_path, state, engine_resolution)
    state_repository.sync_from_state(
        db_path, state, turn_resolution=engine_resolution, turn_number=int(state.get("turn") or 0)
    )
    engine_resolution = run_engine_only_checks(db_path, state, resolution=engine_resolution)
    if message.startswith("Traveled"):
        _emit_engine_chronicle(
            db_path,
            [
                campaign_chronicle.make_travel_event(
                    hero_name=_hero_name(state),
                    from_label=from_label,
                    to_label=_location_label(state),
                    turn_id=turn_id,
                    location_id=_current_location_id(state) or loc_id,
                    mode=str(mode or "walk"),
                )
            ],
        )
    save_game_state(save_id, state)
    asset_worker.set_turn_phase(save_id, "reading")
    _schedule_sd_after_world_action(save_id, db_path)
    state = load_game_state(save_id)
    return _with_session_meta(
        save_id, state, {"message": message, "quest": engine_resolution.quest, "combat": engine_resolution.combat}
    )


def move_direction(save_id: str, direction: str) -> dict[str, Any]:
    deltas = {
        "north": (0, -1, 0),
        "south": (0, 1, 0),
        "east": (1, 0, 0),
        "west": (-1, 0, 0),
        "up": (0, 0, 1),
        "down": (0, 0, -1),
    }
    d = deltas.get(str(direction or "").strip().lower())
    if not d:
        raise GameSessionError("Invalid direction", "invalid_direction")
    asset_worker.set_turn_phase(save_id, "processing")
    _snapshot_before_world_action(save_id)
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    player = state.get("player") or {}
    ox, oy = int(player.get("x", 0)), int(player.get("y", 0))
    from_label = _location_label(state)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    message = move_cardinal(state, d[0], d[1], d[2], db_path=db_path)
    new_player = state.get("player") or {}
    nx, ny = int(new_player.get("x", ox)), int(new_player.get("y", oy))
    state["can_undo"] = True
    if message.startswith("Traveled"):
        cost = grid_engine.travel_cost(db_path, (ox, oy), (nx, ny))
        apply_time_delta(state, cost["time_delta_minutes"])
    save_game_state(save_id, state)
    engine_resolution = TurnResolution(mode="engine_only", intent="engine_only")
    sync_location_and_track(db_path, state, engine_resolution)
    state_repository.sync_from_state(
        db_path, state, turn_resolution=engine_resolution, turn_number=int(state.get("turn") or 0)
    )
    engine_resolution = run_engine_only_checks(db_path, state, resolution=engine_resolution)
    if message.startswith("Traveled"):
        _emit_engine_chronicle(
            db_path,
            [
                campaign_chronicle.make_travel_event(
                    hero_name=_hero_name(state),
                    from_label=from_label,
                    to_label=_location_label(state),
                    turn_id=turn_id,
                    location_id=_current_location_id(state) or loc_id,
                    mode="walk",
                )
            ],
        )
    save_game_state(save_id, state)
    asset_worker.set_turn_phase(save_id, "reading")
    _schedule_sd_after_world_action(save_id, db_path)
    state = load_game_state(save_id)
    return _with_session_meta(
        save_id, state, {"message": message, "quest": engine_resolution.quest, "combat": engine_resolution.combat}
    )


def enter_combat(save_id: str) -> dict[str, Any]:
    from titan.fugassa import combat_engine

    state = load_game_state(save_id)
    combat_engine.start_combat(game_db_path(save_id), state)
    save_game_state(save_id, state)
    return _with_session_meta(save_id, state)


def end_combat(save_id: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    state["in_combat"] = False
    state["initiative_order"] = []
    save_game_state(save_id, state)
    return _with_session_meta(save_id, state)


def investigate(
    save_id: str, search_types: list[str] | None = None, duration_minutes: int | None = None
) -> dict[str, Any]:
    """Dedicated Investigate popup entry point — unlike the old version of
    this function, this actually persists the resulting state (search
    history exhaustion, revealed hidden_* content, elapsed time); the
    previous implementation called `resolve_turn` and threw the mutated
    state away, making every Investigate click a silent no-op.
    """
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    loc_name = _location_label(state)
    result = investigate_engine.resolve_investigate(db_path, state, search_types or [], duration_minutes)
    apply_time_delta(state, result["time_delta_minutes"])
    save_game_state(save_id, state)
    state_repository.sync_from_state(db_path, state, turn_number=int(state.get("turn") or 0))
    engine_resolution = run_engine_only_checks(db_path, state)
    if result.get("revealed_any"):
        _emit_engine_chronicle(
            db_path,
            [
                campaign_chronicle.make_discovery_event(
                    location_name=loc_name,
                    summary=str(result.get("summary") or ""),
                    turn_id=turn_id,
                    location_id=loc_id,
                )
            ],
        )
    save_game_state(save_id, state)
    return _with_session_meta(
        save_id,
        state,
        {
            "message": result["summary"],
            "results": result["results"],
            "revealed_any": result["revealed_any"],
            "location_key": result["location_key"],
            "quest": engine_resolution.quest,
        },
    )


def get_properties(save_id: str) -> dict[str, Any]:
    import sqlite3

    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    if not os.path.isfile(db_path):
        return {"holdings": [], "active_residence_code": None}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from titan.fugassa.property_repository import holdings_payload

        return holdings_payload(conn, state)
    finally:
        conn.close()


def set_active_residence(save_id: str, property_code: str) -> dict[str, Any]:
    from titan.fugassa.property_repository import set_active_residence as _set_active

    state = load_game_state(save_id)
    if not _set_active(state, property_code):
        raise GameSessionError("Unknown property code", "not_found")
    save_game_state(save_id, state)
    return _with_session_meta(
        save_id,
        load_game_state(save_id),
        {"message": f"Active residence set to {property_code}."},
    )


def visit_property(save_id: str, property_code: str) -> dict[str, Any]:
    asset_worker.set_turn_phase(save_id, "processing")
    _snapshot_before_world_action(save_id)
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    holding = next((h for h in holdings if isinstance(h, dict) and h.get("code") == property_code), None)
    if not holding:
        raise GameSessionError("Property not found", "not_found")
    root_id = int(holding.get("root_location_id") or 0)
    if not root_id:
        raise GameSessionError("Property has no visitable location", "no_location")
    from_label = _location_label(state)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    to_label = str(holding.get("name") or "Residence")
    result = narrative_movement.enter_sublocation(
        db_path, state, root_id, label=to_label
    )
    if not result.get("success"):
        raise GameSessionError(result.get("summary") or "Cannot visit property", "visit_failed")
    apply_time_delta(state, 5)
    save_game_state(save_id, state)
    engine_resolution = TurnResolution(mode="engine_only", intent="engine_only")
    sync_location_and_track(db_path, state, engine_resolution)
    state_repository.sync_from_state(
        db_path, state, turn_resolution=engine_resolution, turn_number=int(state.get("turn") or 0)
    )
    engine_resolution = run_engine_only_checks(db_path, state, resolution=engine_resolution)
    _emit_engine_chronicle(
        db_path,
        [
            campaign_chronicle.make_travel_event(
                hero_name=_hero_name(state),
                from_label=from_label,
                to_label=to_label,
                turn_id=turn_id,
                location_id=_current_location_id(state) or loc_id,
                mode="visit",
            )
        ],
    )
    save_game_state(save_id, state)
    asset_worker.set_turn_phase(save_id, "reading")
    _schedule_sd_after_world_action(save_id, db_path)
    state = load_game_state(save_id)
    return _with_session_meta(
        save_id,
        state,
        {"message": result.get("summary") or "You arrive at your property.", "visit": result},
    )


def visit_property_room(save_id: str, property_code: str, room_location_id: int) -> dict[str, Any]:
    """Narrative travel into a specific room under a owned holding."""
    asset_worker.set_turn_phase(save_id, "processing")
    _snapshot_before_world_action(save_id)
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    portfolio = state.get("property_portfolio") if isinstance(state.get("property_portfolio"), dict) else {}
    holdings = portfolio.get("holdings") if isinstance(portfolio.get("holdings"), list) else []
    holding = next((h for h in holdings if isinstance(h, dict) and h.get("code") == property_code), None)
    if not holding:
        raise GameSessionError("Property not found", "not_found")
    root_id = int(holding.get("root_location_id") or 0)
    room_id = int(room_location_id or 0)
    if not root_id or not room_id:
        raise GameSessionError("Invalid room", "no_location")
    if not os.path.isfile(db_path):
        raise GameSessionError("Save database missing", "no_db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        from titan.fugassa.property_repository import list_rooms_for_holding_conn

        allowed = {int(r["id"]) for r in list_rooms_for_holding_conn(conn, root_id)}
        allowed.add(root_id)
        if room_id not in allowed:
            raise GameSessionError("Room does not belong to this property", "invalid_room")
        row = conn.execute("SELECT name FROM locations WHERE id = ?", (room_id,)).fetchone()
        label = str(row["name"] if row else "Room")
    finally:
        conn.close()
    from_label = _location_label(state)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    result = narrative_movement.enter_sublocation(db_path, state, room_id, label=label)
    if not result.get("success"):
        raise GameSessionError(result.get("summary") or "Cannot visit room", "visit_failed")
    apply_time_delta(state, 3)
    save_game_state(save_id, state)
    engine_resolution = TurnResolution(mode="engine_only", intent="engine_only")
    sync_location_and_track(db_path, state, engine_resolution)
    state_repository.sync_from_state(
        db_path, state, turn_resolution=engine_resolution, turn_number=int(state.get("turn") or 0)
    )
    engine_resolution = run_engine_only_checks(db_path, state, resolution=engine_resolution)
    _emit_engine_chronicle(
        db_path,
        [
            campaign_chronicle.make_travel_event(
                hero_name=_hero_name(state),
                from_label=from_label,
                to_label=label,
                turn_id=turn_id,
                location_id=_current_location_id(state) or loc_id,
                mode="visit_room",
            )
        ],
    )
    save_game_state(save_id, state)
    asset_worker.set_turn_phase(save_id, "reading")
    _schedule_sd_after_world_action(save_id, db_path)
    state = load_game_state(save_id)
    return _with_session_meta(
        save_id,
        state,
        {"message": result.get("summary") or "You enter the room.", "visit": result},
    )


def get_investigate_options(save_id: str) -> dict[str, Any]:
    """Which search types are still available at the current location — lets
    the popup grey out/label types already exhausted here."""
    state = load_game_state(save_id)
    return investigate_engine.options_for_location(state)


def get_chat_scene_assets(save_id: str) -> dict[str, Any]:
    """Per-message scene images (entity_type='other', asset_type='scene',
    entity_id=turn_number) plus `min_active_turn` — the lowest turn_number
    still verbatim in the rolling window. ChatPanel.js only offers the
    generate/view icon for messages whose `turn_number >= min_active_turn`;
    anything older has already had its image (if any) hard-deleted by
    `campaign_digest.condense_pending_conn`."""
    from titan.fugassa.db import asset_repository

    db_path = game_db_path(save_id)
    assets = asset_repository.list_assets(db_path, entity_type="other", asset_type="scene") if os.path.isfile(db_path) else []
    by_turn: dict[int, Any] = {}
    for a in assets:
        item = dict(a)
        if item.get("status") == "failed" and item.get("metadata_json"):
            try:
                import json

                meta = json.loads(item["metadata_json"])
                if isinstance(meta, dict) and meta.get("error"):
                    item["error"] = meta["error"]
            except (TypeError, ValueError):
                pass
        by_turn[int(a["entity_id"])] = item
    return {"assets": by_turn, "min_active_turn": campaign_digest.get_min_active_turn(db_path)}


def get_summary(save_id: str) -> dict[str, Any]:
    """Campaign digest + scene summaries + C3 snapshot/chronicle/pinned facts."""
    import sqlite3

    from titan.fugassa import campaign_chronicle, campaign_facts, world_state_snapshot
    from titan.fugassa.db.state_repository import enrich_state_from_sql

    db_path = game_db_path(save_id)
    state = load_game_state(save_id)
    if db_path and os.path.isfile(db_path):
        state = enrich_state_from_sql(db_path, state)

    digest = campaign_digest.get_digest(db_path)
    summaries: list[dict[str, Any]] = []
    if db_path and os.path.isfile(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.summary_text, s.delta_text, s.turn_start, s.turn_end, s.created_at, l.name AS location_name
                FROM scene_summaries s
                LEFT JOIN locations l ON l.id = s.location_id
                ORDER BY s.turn_end DESC, s.id DESC
                LIMIT 50
                """
            ).fetchall()
            summaries = [dict(r) for r in rows]
        finally:
            conn.close()

    chronicle = world_state_snapshot.format_chronicle_for_api(
        campaign_chronicle.query_recent(db_path, limit=30) if db_path else []
    )
    pinned_facts = campaign_facts.list_pinned_facts(db_path, limit=50) if db_path else []

    return {
        "campaign_state": world_state_snapshot.build_snapshot_dict(db_path, state),
        "chronicle": chronicle,
        "pinned_facts": pinned_facts,
        "digest_text": digest.get("digest_text") or "",
        "last_condensed_turn": digest.get("last_condensed_turn") or 0,
        "scene_summaries": summaries,
    }


def _normalize_loot_entry(item: Any) -> dict[str, Any]:
    """Loot entries may still be bare strings on older saves (the format
    before structured loot objects) — normalize to the `{name, qty, ...}`
    shape everywhere so selective pickup has one consistent shape to work
    against."""
    if isinstance(item, dict):
        out = dict(item)
        out["name"] = str(out.get("name") or "").strip()
        out["qty"] = max(1, int(out.get("qty", 1) or 1))
        return out
    return {"name": str(item).strip(), "qty": 1}


def pickup_loot(save_id: str, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pick up loot from the current location. `items` is a list of
    `{name, qty}` selections from the pickup popup; quantities are clamped
    to what's actually available and unselected entries are left behind.
    Omitting/empty `items` preserves the legacy "pick up everything"
    behavior for any caller that hasn't been updated to the selective flow.
    """
    state = load_game_state(save_id)
    loc = dict(state.get("location_state") or {})
    loot = [_normalize_loot_entry(entry) for entry in (loc.get("loot") or [])]
    if not loot:
        return _with_session_meta(save_id, state, {"message": "Nothing to pick up.", "picked": []})

    if items:
        requested = {
            str(sel.get("name") or "").strip().lower(): max(1, int(sel.get("qty", 1) or 1)) for sel in items
        }
    else:
        requested = {entry["name"].strip().lower(): entry["qty"] for entry in loot}

    inv = dict(state.get("inventory") or {})
    shared = list(inv.get("shared") or [])
    picked: list[dict[str, Any]] = []
    remaining_loot: list[dict[str, Any]] = []
    for entry in loot:
        key = entry["name"].strip().lower()
        want = requested.get(key)
        if not want:
            remaining_loot.append(entry)
            continue
        take = min(want, entry["qty"])
        taken_entry = dict(entry)
        taken_entry["qty"] = take
        picked.append(taken_entry)
        shared_item: dict[str, Any] = {"name": entry["name"], "qty": take}
        if entry.get("description"):
            shared_item["description"] = entry["description"]
        if entry.get("rarity"):
            shared_item["rarity"] = entry["rarity"]
        if entry.get("tags"):
            shared_item["tags"] = entry["tags"]
        shared.append(shared_item)
        leftover_qty = entry["qty"] - take
        if leftover_qty > 0:
            leftover = dict(entry)
            leftover["qty"] = leftover_qty
            remaining_loot.append(leftover)

    inv["shared"] = shared
    loc["loot"] = remaining_loot
    state["inventory"] = inv
    state["location_state"] = loc
    save_game_state(save_id, state)
    db_path = game_db_path(save_id)
    state_repository.sync_from_state(db_path, state, turn_number=int(state.get("turn") or 0))
    engine_resolution = run_engine_only_checks(db_path, state)
    if picked:
        names = ", ".join(f"{p.get('name')}×{p.get('qty', 1)}" for p in picked if p.get("name"))
        _emit_engine_chronicle(
            db_path,
            [
                campaign_chronicle.make_inventory_change_event(
                    hero_name=_hero_name(state),
                    item_summary=names,
                    turn_id=int(state.get("turn") or 0),
                    location_id=_current_location_id(state),
                    action="picked up",
                )
            ],
        )
    save_game_state(save_id, state)
    return _with_session_meta(
        save_id,
        state,
        {"message": f"Picked up {len(picked)} item(s).", "picked": picked, "quest": engine_resolution.quest},
    )


def patch_inventory(save_id: str, inventory: dict[str, Any]) -> dict[str, Any]:
    state = load_game_state(save_id)
    state["inventory"] = inventory
    save_game_state(save_id, state)
    state_repository.sync_from_state(game_db_path(save_id), state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state)


def equip_item(save_id: str, hero_name: str, item_name: str, slot: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    try:
        item_engine.equip_item(state, hero_name, item_name, slot)
    except item_engine.EquipError as exc:
        raise GameSessionError(str(exc), exc.code) from exc
    save_game_state(save_id, state)
    state_repository.sync_from_state(game_db_path(save_id), state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state, {"message": f"Equipped {item_name}."})


def unequip_item(save_id: str, hero_name: str, slot: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    try:
        item_engine.unequip_item(state, hero_name, slot)
    except item_engine.EquipError as exc:
        raise GameSessionError(str(exc), exc.code) from exc
    save_game_state(save_id, state)
    state_repository.sync_from_state(game_db_path(save_id), state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state, {"message": f"Unequipped {slot}."})


def get_crafting_professions(save_id: str, hero_name: str) -> dict[str, Any]:
    return {"professions": crafting_engine.get_professions_for_hero(game_db_path(save_id), hero_name)}


def get_crafting_blueprints(save_id: str, hero_name: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    return {"blueprints": crafting_engine.get_blueprints_for_hero(game_db_path(save_id), state, hero_name)}


def craft_item(save_id: str, hero_name: str, recipe_code: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)
    try:
        result = crafting_engine.craft_item(db_path, state, hero_name, recipe_code)
    except crafting_engine.CraftingError as exc:
        raise GameSessionError(str(exc), exc.code) from exc
    if result.get("success") and result.get("output_item_name"):
        qty = int(result.get("output_qty") or 1)
        item_label = f"{result['output_item_name']}×{qty}" if qty > 1 else str(result["output_item_name"])
        _emit_engine_chronicle(
            db_path,
            [
                campaign_chronicle.make_inventory_change_event(
                    hero_name=hero_name,
                    item_summary=item_label,
                    turn_id=turn_id,
                    location_id=loc_id,
                    action="crafted",
                )
            ],
        )
    save_game_state(save_id, state)
    state_repository.sync_from_state(db_path, state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state, result)


def apply_level_up(
    save_id: str,
    *,
    target_level: int,
    class_mechanic_choices: dict[str, Any] | None = None,
    asi_choices: dict[str, Any] | None = None,
    selected_cantrips: list[str] | None = None,
    selected_spells_by_level: dict[str, Any] | None = None,
    hp_current: int | None = None,
) -> dict[str, Any]:
    from titan.fugassa.level_progression import level_up_apply

    state = load_game_state(save_id)
    db_path = game_db_path(save_id)
    party = state.get("party") or []
    hero = party[0] if party else {}
    sheet = state.get("character_sheet") or {}
    identity = (sheet.get("stable_sheet") or {}).get("identity") or {}
    from_level = int(hero.get("level") or identity.get("level") or 1)
    turn_id = int(state.get("turn") or 0)
    loc_id = _current_location_id(state)

    result = level_up_apply(
        state,
        target_level=target_level,
        class_mechanic_choices=class_mechanic_choices,
        asi_choices=asi_choices,
        selected_cantrips=selected_cantrips,
        selected_spells_by_level=selected_spells_by_level,
        hp_current=hp_current,
    )
    if not result.get("ok"):
        return result
    save_game_state(save_id, result["state"])
    state_repository.sync_from_state(db_path, result["state"], turn_number=turn_id)
    _emit_engine_chronicle(
        db_path,
        [
            campaign_chronicle.make_level_up_event(
                hero_name=_hero_name(result["state"]),
                from_level=from_level,
                to_level=target_level,
                turn_id=turn_id,
                location_id=loc_id,
            )
        ],
    )
    return _with_session_meta(save_id, load_game_state(save_id), result)


async def invent_blueprint(
    save_id: str, hero_name: str, profession: str, tier: int, description: str
) -> dict[str, Any]:
    state = load_game_state(save_id)
    theme = str((state.get("world_profile") or {}).get("theme") or "")
    try:
        result = await crafting_engine.invent_blueprint(
            game_db_path(save_id), state, hero_name,
            profession=profession, tier=tier, description=description, theme=theme, owner=save_id,
        )
    except crafting_engine.CraftingError as exc:
        raise GameSessionError(str(exc), exc.code) from exc
    save_game_state(save_id, state)
    state_repository.sync_from_state(game_db_path(save_id), state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state, result)


async def reverse_engineer_item(save_id: str, hero_name: str, profession: str, item_name: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    theme = str((state.get("world_profile") or {}).get("theme") or "")
    try:
        result = await crafting_engine.reverse_engineer(
            game_db_path(save_id), state, hero_name,
            profession=profession, item_name=item_name, theme=theme, owner=save_id,
        )
    except crafting_engine.CraftingError as exc:
        raise GameSessionError(str(exc), exc.code) from exc
    save_game_state(save_id, state)
    state_repository.sync_from_state(game_db_path(save_id), state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state, result)


def default_gm_guide_names() -> list[str]:
    if not os.path.isdir(GM_TEMPLATES_DIR):
        return []
    return sorted(f for f in os.listdir(GM_TEMPLATES_DIR) if f.endswith(".txt"))


def combine_gm_guides(gm_map: dict[str, Any]) -> str:
    parts: list[str] = []
    for name in sorted(gm_map.keys()):
        text = str(gm_map.get(name) or "").strip()
        if text:
            parts.append(f"--- {name} ---\n{text}")
    return "\n\n".join(parts)


def save_pause_settings(save_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_game_state(save_id)
    world_patch = payload.get("world_profile")
    if isinstance(world_patch, dict):
        wp = dict(state.get("world_profile") or {})
        wp.update(world_patch)
        state["world_profile"] = wp

    rules_mode = payload.get("rules_mode")
    if rules_mode is not None:
        new_rules = str(rules_mode).strip().lower()
        old_rules = str(state.get("rules_mode") or "5e-style").strip().lower()
        if old_rules == "homebrew" and new_rules == "5e-style":
            raise GameSessionError("Cannot switch from homebrew back to strict 5e-style on this save", "rules_locked")
        state["rules_mode"] = new_rules

    if payload.get("resolution_mode") is not None:
        state["resolution_mode"] = str(payload.get("resolution_mode") or "dice")

    if payload.get("playstyle") is not None:
        state["playstyle"] = str(payload.get("playstyle") or "adventure")

    reality_mode = payload.get("reality_mode")
    if reality_mode is not None:
        new_mode = str(reality_mode).strip().lower()
        if new_mode not in ("simulation", "sandbox"):
            raise GameSessionError("reality_mode must be 'simulation' or 'sandbox'", "invalid_reality_mode")
        state["reality_mode"] = new_mode

    gm_map = payload.get("gm_guides_map")
    if isinstance(gm_map, dict):
        state["gm_guides_map"] = dict(gm_map)
        state["gm_guides_notes"] = combine_gm_guides(gm_map)
        write_gm_guides(_save_path(save_id), gm_map)

    display_patch = payload.get("display_settings")
    if isinstance(display_patch, dict):
        state["display_settings"] = normalize_display_settings(
            {**(state.get("display_settings") or {}), **display_patch}
        )

    tts_patch = payload.get("tts_prefs")
    if isinstance(tts_patch, dict):
        state["tts_prefs"] = normalize_tts_prefs(
            {**(state.get("tts_prefs") or {}), **tts_patch}
        )

    save_game_state(save_id, state)
    db_path = game_db_path(save_id)
    if db_path:
        state_repository.sync_from_state(db_path, state, turn_number=int(state.get("turn") or 0))
    return _with_session_meta(save_id, state)


def get_pause_defaults(save_id: str) -> dict[str, Any]:
    state = load_game_state(save_id)
    gm_map = dict(state.get("gm_guides_map") or {})
    if not gm_map:
        for name in default_gm_guide_names():
            path = os.path.join(GM_TEMPLATES_DIR, name)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    gm_map[name] = f.read()
    return {
        "world_profile": state.get("world_profile") or {},
        "rules_mode": state.get("rules_mode") or "5e-style",
        "resolution_mode": state.get("resolution_mode") or "dice",
        "playstyle": state.get("playstyle") or "adventure",
        "reality_mode": state.get("reality_mode") or "simulation",
        "gm_guides_map": gm_map,
        "gm_guide_names": sorted(gm_map.keys()) or default_gm_guide_names(),
        "display_settings": normalize_display_settings(state.get("display_settings")),
        "tts_prefs": normalize_tts_prefs(state.get("tts_prefs")),
    }

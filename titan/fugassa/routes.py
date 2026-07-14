"""Fugassa HTTP API."""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth_helpers import get_current_user, effective_user
from titan.fugassa import config_store, save_store, wizard_draft_store, session_manifest_store
from titan.fugassa import game_session
from titan.fugassa.dnd5e_data import load_resource, ALLOWED as DND5E_ALLOWED
from titan.fugassa.dnd5e_character_builder import build, draft_to_build_input, validate_sheet_input
from titan.fugassa.dnd5e_database import get_dnd5e_database
from titan.fugassa.llm_client import FugassaLlmDisabled
from titan.fugassa import wizard_engine
from titan.fugassa.save_store import SaveStoreError
from titan.fugassa import portrait_gen
from titan.fugassa.game_bootstrap import read_game_json, wizard_portrait_staging_path
from titan.fugassa import npc_generator, debug_snapshot
from titan.fugassa.save_store import game_db_path

router = APIRouter(prefix="/api/fugassa", tags=["fugassa"])

LOG = logging.getLogger("titan.fugassa.routes")


def _kick_asset_pipeline(
    db_path: str,
    save_id: str,
    asset_id: int,
    *,
    owner: str | None = None,
) -> None:
    """Enqueue SD jobs and recover queued assets if enqueue failed (e.g. stale imports)."""
    from titan.fugassa import campaign_job_runner

    try:
        campaign_job_runner.enqueue_sd_asset(
            db_path,
            save_id,
            asset_id=asset_id,
            owner=owner,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("enqueue_sd_asset failed for asset %s: %s", asset_id, exc)
    campaign_job_runner.reconcile_queued_asset_jobs(save_id, db_path)
    campaign_job_runner.ensure_worker_scheduled(save_id, db_path)


class ConfigUpdate(BaseModel):
    llm_enabled: bool | None = None
    images_enabled: bool | None = None
    image_style_default: str | None = None
    debug_ai_logging: bool | None = None
    language: str | None = None
    hud_theme: str | None = None


class SaveCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    theme: str | None = "fantasy"


class SaveFromWizardBody(BaseModel):
    """Full Fugassa II wizard draft payload for Create."""

    model_config = {"extra": "allow"}


class SaveRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class WizardDraftPatch(BaseModel):
    """Shallow merge into wizard draft (Fugassa II flat keys)."""

    model_config = {"extra": "allow"}


class WizardChatMessage(BaseModel):
    role: str
    content: str


class RulesContext(BaseModel):
    playstyle_framework: str = "rules_based"
    playstyle: str = "adventure"
    rules_mode: str = "5e-style"
    resolution_mode: str = "dice"
    level: int = 1
    character_class: str = ""
    race: str = ""
    background: str = ""


class WizardWorldOptionsBody(BaseModel):
    theme: str = "Fantasy"
    campaignLength: str = "medium"
    playerRequest: str = ""
    optionStart: int = 1
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardWorldSummaryBody(BaseModel):
    theme: str = "Fantasy"
    campaignLength: str = "medium"
    currentDraft: str = ""
    playerRequest: str = ""
    dialogTranscript: str = ""
    dialog: list[WizardChatMessage] = Field(default_factory=list)
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardBackstoryOptionsBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    playerRequest: str = ""
    characterProfile: str = ""
    optionStart: int = 1
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardBackstorySummaryBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    currentDraft: str = ""
    playerRequest: str = ""
    worldInformation: str = ""
    characterProfile: str = ""
    dialogTranscript: str = ""
    dialog: list[WizardChatMessage] = Field(default_factory=list)
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardInventoryOptionsBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    optionStart: int = 1
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardInventorySummaryBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    currentDraft: str = ""
    playerRequest: str = ""
    dialogTranscript: str = ""
    dialog: list[WizardChatMessage] = Field(default_factory=list)
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardGearOptionsBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    optionStart: int = 1
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardGearSummaryBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    currentDraft: str = ""
    playerRequest: str = ""
    dialogTranscript: str = ""
    dialog: list[WizardChatMessage] = Field(default_factory=list)
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardOpeningOptionsBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    optionStart: int = 1
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardOpeningSummaryBody(BaseModel):
    theme: str = "Fantasy"
    playerName: str = ""
    worldInformation: str = ""
    currentDraft: str = ""
    playerRequest: str = ""
    dialogTranscript: str = ""
    dialog: list[WizardChatMessage] = Field(default_factory=list)
    rulesContext: RulesContext = Field(default_factory=RulesContext)


class WizardPortraitPromptsBody(BaseModel):
    theme: str = ""
    playerName: str = ""
    backstory: str = ""
    worldInformation: str = ""
    styleOverride: str = ""
    characterProfile: str = ""
    appearanceVisual: str = ""


class WizardPortraitGenerateBody(BaseModel):
    positive_prompt: str = ""
    negative_prompt: str = ""
    theme: str = "Fantasy"
    style_override: str = ""


class GameSubmitBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class GameTravelBody(BaseModel):
    x: int = 0
    y: int = 0
    z: int = 0
    mode: str = "walk"
    transport_item_id: int | None = None


class GameMoveBody(BaseModel):
    direction: str = Field(..., min_length=1, max_length=16)


class GameTransportBody(BaseModel):
    mode: str = Field(..., min_length=1, max_length=32)
    item_id: int | None = None


class GameInventoryBody(BaseModel):
    inventory: dict[str, Any]


class GameEquipBody(BaseModel):
    hero_name: str = Field(..., min_length=1, max_length=120)
    item_name: str = Field(..., min_length=1, max_length=200)
    slot: str = Field(..., min_length=1, max_length=32)


class GameUnequipBody(BaseModel):
    hero_name: str = Field(..., min_length=1, max_length=120)
    slot: str = Field(..., min_length=1, max_length=32)


class GameCraftBody(BaseModel):
    hero_name: str = Field(..., min_length=1, max_length=120)
    recipe_code: str = Field(..., min_length=1, max_length=64)


class GameInventBlueprintBody(BaseModel):
    hero_name: str = Field(..., min_length=1, max_length=120)
    profession: str = Field(..., min_length=1, max_length=32)
    tier: int = Field(0, ge=0, le=5)
    description: str = Field(..., min_length=1, max_length=500)


class GameReverseEngineerBody(BaseModel):
    hero_name: str = Field(..., min_length=1, max_length=120)
    profession: str = Field(..., min_length=1, max_length=32)
    item_name: str = Field(..., min_length=1, max_length=200)


class GameInvestigateBody(BaseModel):
    search_types: list[str] = Field(default_factory=list)
    duration_minutes: int = Field(30, ge=5, le=240)


class LootPickupSelection(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    qty: int = Field(1, ge=1, le=999)


class GameLootPickupBody(BaseModel):
    items: list[LootPickupSelection] = Field(default_factory=list)


class GamePauseBody(BaseModel):
    model_config = {"extra": "allow"}


class GamePropertyCodeBody(BaseModel):
    property_code: str = Field(..., min_length=1, max_length=120)


class GamePropertyRoomVisitBody(BaseModel):
    property_code: str = Field(..., min_length=1, max_length=120)
    room_location_id: int = Field(..., ge=1)


class SessionManifestUpdate(BaseModel):
    mode: str | None = None
    menuScreen: str | None = None
    wizardTab: int | None = None
    activeSaveId: str | None = None
    lastTool: str | None = None
    play: dict[str, Any] | None = None


def _save_error(exc: SaveStoreError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 400
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _game_error(exc: game_session.GameSessionError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 400
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _rules_dict(ctx: RulesContext | dict[str, Any] | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    if isinstance(ctx, RulesContext):
        return ctx.model_dump()
    return dict(ctx)


def _dialog_text(body_dialog: list[WizardChatMessage], transcript: str) -> str:
    if transcript.strip():
        return transcript
    if not body_dialog:
        return ""
    from titan.fugassa import wizard_json as wj

    return wj.dialog_transcript([m.model_dump() for m in body_dialog])


@router.get("/health")
def fugassa_health() -> dict[str, str]:
    return {"status": "ok", "module": "fugassa"}


@router.get("/saves")
def get_saves(request: Request) -> dict[str, Any]:
    get_current_user(request)
    return {"saves": save_store.list_saves()}


@router.get("/saves/check")
def check_save_name(request: Request, name: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        available = save_store.is_name_available(name)
        return {"available": available, "name": name.strip()}
    except SaveStoreError as exc:
        return {"available": False, "name": name.strip(), "message": str(exc)}


@router.post("/saves")
def post_save(request: Request, body: SaveCreate) -> dict[str, Any]:
    get_current_user(request)
    try:
        save = save_store.create_save(body.name, theme=body.theme or "fantasy")
    except SaveStoreError as exc:
        raise _save_error(exc) from exc
    return {"save": save}


@router.post("/saves/from-wizard")
async def post_save_from_wizard(request: Request, body: SaveFromWizardBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    draft = body.model_dump()
    from titan.fugassa.game_bootstrap import resolve_theme
    from titan.fugassa.theme_facet_engine import (
        apply_normalized_theme_to_draft,
        normalize_theme_facets_for_wizard,
    )

    theme = resolve_theme(draft)
    normalized = await normalize_theme_facets_for_wizard(
        draft,
        theme=theme,
        owner=owner,
        llm_enabled=llm_on,
    )
    apply_normalized_theme_to_draft(
        draft,
        theme_facets=normalized.get("theme_facets") or [],
        theme_label_en=str(normalized.get("theme_label_en") or theme),
    )
    try:
        save = save_store.create_save_from_wizard(draft)
    except SaveStoreError as exc:
        raise _save_error(exc) from exc
    return {"save": save, "theme_facets": draft.get("theme_facets"), "theme_label_en": draft.get("theme_label_en")}


@router.get("/saves/{save_id}")
def get_save_detail(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        return {"save": save_store.get_save(save_id)}
    except SaveStoreError as exc:
        raise _save_error(exc) from exc


@router.patch("/saves/{save_id}")
def patch_save(request: Request, save_id: str, body: SaveRename) -> dict[str, Any]:
    get_current_user(request)
    try:
        save = save_store.rename_save(save_id, body.name)
    except SaveStoreError as exc:
        raise _save_error(exc) from exc
    return {"save": save}


@router.delete("/saves/{save_id}")
def delete_save_route(request: Request, save_id: str) -> dict[str, str]:
    get_current_user(request)
    try:
        save_store.delete_save(save_id)
    except SaveStoreError as exc:
        raise _save_error(exc) from exc
    return {"status": "deleted", "id": save_id}


@router.get("/saves/{save_id}/game")
def get_game_state(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        state = game_session.load_game_state(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"state": state}


@router.get("/saves/{save_id}/game/jobs")
def get_game_jobs(
    request: Request,
    save_id: str,
    batch_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    turn_number: int | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    get_current_user(request)
    try:
        pipeline = game_session.get_pipeline_jobs(
            save_id,
            batch_id=batch_id,
            status=status,
            job_type=job_type,
            turn_number=turn_number,
            limit=min(max(limit, 1), 100),
        )
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    state = game_session.load_game_state(save_id)
    return {"success": True, "state": state, **pipeline}


@router.get("/saves/{save_id}/game/jobs/{job_id}")
def get_game_job_detail(request: Request, save_id: str, job_id: int) -> dict[str, Any]:
    get_current_user(request)
    try:
        return {"success": True, **game_session.get_pipeline_job_detail(save_id, job_id)}
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc


@router.post("/saves/{save_id}/game/jobs/{job_id}/retry")
def post_game_job_retry(request: Request, save_id: str, job_id: int) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.retry_pipeline_job(save_id, job_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/bootstrap")
async def post_game_bootstrap(request: Request, save_id: str) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    get_current_user(request)
    try:
        result = await game_session.bootstrap_opening(save_id, owner=owner, llm_enabled=llm_on)
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/submit")
async def post_game_submit(request: Request, save_id: str, body: GameSubmitBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    get_current_user(request)
    try:
        result = await game_session.submit_player_action(
            save_id,
            body.text,
            owner=owner,
            llm_enabled=llm_on,
        )
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/undo")
def post_game_undo(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.undo_last_turn(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/map")
def get_game_map(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        return game_session.get_map_data(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc


@router.post("/saves/{save_id}/game/travel")
def post_game_travel(request: Request, save_id: str, body: GameTravelBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.travel(save_id, body.x, body.y, body.z, body.mode, body.transport_item_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/transport")
def get_game_transport(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        return {"success": True, **game_session.get_transport_options(save_id)}
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc


@router.post("/saves/{save_id}/game/transport")
def post_game_transport(request: Request, save_id: str, body: GameTransportBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.set_transport(save_id, body.mode, body.item_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/move")
def post_game_move(request: Request, save_id: str, body: GameMoveBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.move_direction(save_id, body.direction)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/combat/start")
def post_combat_start(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.enter_combat(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/combat/end")
def post_combat_end(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.end_combat(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/npcs/{npc_id}")
def get_npc_detail_route(request: Request, save_id: str, npc_id: int) -> dict[str, Any]:
    get_current_user(request)
    db_path = game_db_path(save_id)
    if not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Save DB not found")
    detail = npc_generator.get_npc_detail(db_path, npc_id)
    if not detail:
        raise HTTPException(status_code=404, detail="NPC not found")
    return detail


class NpcPortraitPromptBody(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None


@router.patch("/saves/{save_id}/npcs/{npc_id}/portrait-prompt")
def patch_npc_portrait_prompt(
    request: Request,
    save_id: str,
    npc_id: int,
    body: NpcPortraitPromptBody,
) -> dict[str, Any]:
    get_current_user(request)
    db_path = game_db_path(save_id)
    if not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Save DB not found")
    pos = str(body.positive_prompt or "").strip()
    if not pos:
        raise HTTPException(status_code=400, detail="positive_prompt is required")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE npcs SET portrait_prompt = ?, updated_at = datetime('now') WHERE id = ?",
            (pos, int(npc_id)),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="NPC not found")
    finally:
        conn.close()
    return {"success": True, "portrait_prompt": pos}


@router.patch("/saves/{save_id}/player-character/portrait-prompt")
def patch_player_portrait_prompt(
    request: Request,
    save_id: str,
    body: NpcPortraitPromptBody,
) -> dict[str, Any]:
    get_current_user(request)
    db_path = game_db_path(save_id)
    if not os.path.isfile(db_path):
        raise HTTPException(status_code=404, detail="Save DB not found")
    pos = str(body.positive_prompt or "").strip()
    if not pos:
        raise HTTPException(status_code=400, detail="positive_prompt is required")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            UPDATE player_characters
            SET portrait_prompt = ?, updated_at = datetime('now')
            WHERE code = 'pc_hero'
            """,
            (pos,),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Player character not found")
    finally:
        conn.close()
    return {"success": True, "portrait_prompt": pos}


@router.get("/saves/{save_id}/debug")
def get_debug_snapshot_route(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    db_path = game_db_path(save_id)
    include_secrets = request.query_params.get("include_secrets") in ("1", "true", "True")
    return debug_snapshot.build_debug_snapshot(db_path, save_id, include_secrets=include_secrets)


@router.get("/saves/{save_id}/game/investigate/options")
def get_game_investigate_options(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.get_investigate_options(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/investigate")
def post_game_investigate(request: Request, save_id: str, body: GameInvestigateBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.investigate(save_id, body.search_types, body.duration_minutes)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/loot/pickup")
def post_game_loot_pickup(request: Request, save_id: str, body: GameLootPickupBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        items = [{"name": sel.name, "qty": sel.qty} for sel in body.items]
        result = game_session.pickup_loot(save_id, items)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/chat-scene-assets")
def get_game_chat_scene_assets(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.get_chat_scene_assets(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/summary")
def get_game_summary(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.get_summary(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.patch("/saves/{save_id}/game/inventory")
def patch_game_inventory(request: Request, save_id: str, body: GameInventoryBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.patch_inventory(save_id, body.inventory)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/equipment-slots")
def get_game_equipment_slots(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    from titan.fugassa import equipment_slots

    return {"success": True, "slots": list(equipment_slots.SLOTS)}


@router.post("/saves/{save_id}/game/equip")
def post_game_equip(request: Request, save_id: str, body: GameEquipBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.equip_item(save_id, body.hero_name, body.item_name, body.slot)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/unequip")
def post_game_unequip(request: Request, save_id: str, body: GameUnequipBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.unequip_item(save_id, body.hero_name, body.slot)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/crafting/professions")
def get_crafting_professions(request: Request, save_id: str, hero_name: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.get_crafting_professions(save_id, hero_name)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/crafting/blueprints")
def get_crafting_blueprints(request: Request, save_id: str, hero_name: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.get_crafting_blueprints(save_id, hero_name)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/crafting/craft")
def post_crafting_craft(request: Request, save_id: str, body: GameCraftBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.craft_item(save_id, body.hero_name, body.recipe_code)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    # Nested under "craft" (not spread) — the engine's own roll-outcome
    # "success" field would otherwise silently clobber the HTTP-level
    # "success" flag every time a craft attempt's dice roll failed.
    return {"success": True, "state": result.get("state"), "turn_phase": result.get("turn_phase"), "craft": result}


@router.post("/saves/{save_id}/game/crafting/invent")
async def post_crafting_invent(request: Request, save_id: str, body: GameInventBlueprintBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = await game_session.invent_blueprint(
            save_id, body.hero_name, body.profession, body.tier, body.description
        )
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, "state": result.get("state"), "turn_phase": result.get("turn_phase"), "invent": result}


@router.post("/saves/{save_id}/game/crafting/reverse-engineer")
async def post_crafting_reverse_engineer(
    request: Request, save_id: str, body: GameReverseEngineerBody
) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = await game_session.reverse_engineer_item(save_id, body.hero_name, body.profession, body.item_name)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {
        "success": True,
        "state": result.get("state"),
        "turn_phase": result.get("turn_phase"),
        "reverse_engineer": result,
    }


@router.get("/saves/{save_id}/game/pause")
def get_game_pause(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        return game_session.get_pause_defaults(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc


@router.patch("/saves/{save_id}/game/pause")
def patch_game_pause(request: Request, save_id: str, body: GamePauseBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.save_pause_settings(save_id, body.model_dump())
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/game/properties")
def get_game_properties(request: Request, save_id: str) -> dict[str, Any]:
    get_current_user(request)
    try:
        payload = game_session.get_properties(save_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **payload}


@router.post("/saves/{save_id}/game/properties/visit")
def post_game_property_visit(request: Request, save_id: str, body: GamePropertyCodeBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.visit_property(save_id, body.property_code)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/properties/visit-room")
def post_game_property_visit_room(request: Request, save_id: str, body: GamePropertyRoomVisitBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.visit_property_room(save_id, body.property_code, body.room_location_id)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.post("/saves/{save_id}/game/properties/active-residence")
def post_game_property_active_residence(request: Request, save_id: str, body: GamePropertyCodeBody) -> dict[str, Any]:
    get_current_user(request)
    try:
        result = game_session.set_active_residence(save_id, body.property_code)
    except game_session.GameSessionError as exc:
        raise _game_error(exc) from exc
    return {"success": True, **result}


@router.get("/saves/{save_id}/assets/{asset_path:path}")
def get_save_asset(request: Request, save_id: str, asset_path: str):
    from fastapi.responses import FileResponse

    get_current_user(request)
    if ".." in asset_path or asset_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid asset path")
    base = os.path.join(save_store.save_dir(save_id), "generated")
    full = os.path.normpath(os.path.join(base, asset_path))
    if not full.startswith(os.path.normpath(base + os.sep)) and full != os.path.normpath(base):
        raise HTTPException(status_code=400, detail="Invalid asset path")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(full)


@router.get("/saves/{save_id}/assets-meta")
def get_save_assets_meta(
    request: Request,
    save_id: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    asset_type: str | None = None,
) -> dict[str, Any]:
    get_current_user(request)
    if not os.path.isdir(save_store.save_dir(save_id)):
        raise HTTPException(status_code=404, detail="Save not found")
    from titan.fugassa.db import asset_repository

    db_path = save_store.game_db_path(save_id)
    assets = asset_repository.list_assets(
        db_path,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_type=asset_type,
    )
    pending_prompt: str | None = None
    pending_negative: str | None = None
    game_state: dict[str, Any] | None = None
    if entity_type == "player_character" and entity_id:
        try:
            game_state = read_game_json(save_store.save_dir(save_id))
        except Exception:
            game_state = None
    if entity_type == "npc" and entity_id:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT portrait_prompt FROM npcs WHERE id = ?",
                (int(entity_id),),
            ).fetchone()
            if row and str(row["portrait_prompt"] or "").strip():
                pending_prompt = str(row["portrait_prompt"]).strip()
            if not pending_prompt:
                active = asset_repository.get_active_asset(
                    db_path,
                    entity_type="npc",
                    entity_id=int(entity_id),
                    asset_type=asset_type or "portrait",
                )
                if active and str(active.get("prompt") or "").strip():
                    pending_prompt = str(active["prompt"]).strip()
        finally:
            conn.close()
    elif entity_type == "player_character" and entity_id:
        from titan.fugassa.player_portrait_prompt import resolve_player_portrait_prompt

        pos, neg = resolve_player_portrait_prompt(db_path, int(entity_id), game_state)
        if pos:
            pending_prompt = pos
        if neg:
            pending_negative = neg
        active = asset_repository.get_active_asset(
            db_path,
            entity_type="player_character",
            entity_id=int(entity_id),
            asset_type=asset_type or "portrait",
        )
        if not pending_negative and active and str(active.get("negative_prompt") or "").strip():
            pending_negative = str(active["negative_prompt"]).strip()
    return {
        "success": True,
        "assets": assets,
        "pending_prompt": pending_prompt,
        "pending_negative_prompt": pending_negative,
    }


@router.get("/config")
def get_config(request: Request) -> dict[str, Any]:
    get_current_user(request)
    from titan.hub_sd_config import image_style_catalog

    return {**config_store.load(), "image_styles": image_style_catalog()}


@router.get("/image-styles")
def get_image_styles(request: Request) -> dict[str, Any]:
    get_current_user(request)
    from titan.hub_sd_config import image_style_catalog

    return {"styles": image_style_catalog()}


@router.patch("/config")
def patch_config(request: Request, body: ConfigUpdate) -> dict[str, Any]:
    get_current_user(request)
    current = config_store.load()
    patch = body.model_dump(exclude_none=True)
    return config_store.save({**current, **patch})


@router.get("/wizard-draft")
def get_wizard_draft(request: Request) -> dict[str, Any]:
    get_current_user(request)
    from titan.hub_sd_config import image_style_catalog

    return {**wizard_draft_store.load(), "image_styles": image_style_catalog()}


@router.patch("/wizard-draft")
def patch_wizard_draft(request: Request, body: WizardDraftPatch) -> dict[str, Any]:
    get_current_user(request)
    patch = body.model_dump(exclude_none=True)
    return wizard_draft_store.save(patch)


@router.delete("/wizard-draft")
def delete_wizard_draft(request: Request) -> dict[str, Any]:
    get_current_user(request)
    return wizard_draft_store.clear()


@router.get("/session-manifest")
def get_session_manifest(request: Request) -> dict[str, Any]:
    get_current_user(request)
    return session_manifest_store.load()


@router.put("/session-manifest")
def put_session_manifest(request: Request, body: SessionManifestUpdate) -> dict[str, Any]:
    get_current_user(request)
    current = session_manifest_store.load()
    # `exclude_unset` (not `exclude_none`) — a client resetting the session
    # must be able to explicitly send `activeSaveId: null` to clear a stale
    # save reference. `exclude_none` would silently drop that null and the
    # old save would keep winning on every future `tryRestoreFugassa()`.
    patch = body.model_dump(exclude_unset=True)
    return session_manifest_store.save({**current, **patch})


@router.get("/memory/status")
def memory_status(request: Request, save_id: str | None = None) -> dict[str, Any]:
    get_current_user(request)
    from titan.fugassa.db import sqlite_store

    if not save_id:
        return {"backend": "sqlite", "schema_version": None}
    db_path = save_store.game_db_path(save_id)
    meta = sqlite_store.read_campaign_meta(db_path) if os.path.isfile(db_path) else None
    return {
        "backend": "sqlite",
        "save_id": save_id,
        "schema_version": (meta or {}).get("schema_version"),
        "turn_number": (meta or {}).get("turn_number"),
    }


class AssetPromptPatch(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None


class AssetRegenerateBody(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    use_auto_prompt: bool = False


@router.patch("/saves/{save_id}/assets/{asset_id}/prompt")
def patch_asset_prompt(request: Request, save_id: str, asset_id: int, body: AssetPromptPatch) -> dict[str, Any]:
    get_current_user(request)
    from titan.fugassa import asset_service

    db_path = save_store.game_db_path(save_id)
    return asset_service.patch_prompt(
        db_path,
        asset_id,
        positive_prompt=body.positive_prompt,
        negative_prompt=body.negative_prompt,
    )


@router.post("/saves/{save_id}/assets/{asset_id}/regenerate")
async def post_asset_regenerate(
    request: Request, save_id: str, asset_id: int, body: AssetRegenerateBody
) -> dict[str, Any]:
    get_current_user(request)
    from titan.fugassa import asset_service, campaign_job_runner

    cfg = config_store.load()
    if not bool(cfg.get("images_enabled", True)):
        raise HTTPException(status_code=400, detail="Image generation is disabled in Fugassa settings")

    db_path = save_store.game_db_path(save_id)
    result = asset_service.request_regenerate(
        db_path,
        asset_id,
        positive_prompt=body.positive_prompt,
        negative_prompt=body.negative_prompt,
        use_auto_prompt=body.use_auto_prompt,
    )
    if result.get("success"):
        asset = result.get("asset") or {}
        new_id = int(asset.get("id") or asset_id)
        owner, _ = _wizard_cfg(request)
        _kick_asset_pipeline(db_path, save_id, new_id, owner=owner)
        result["state"] = game_session.load_game_state(save_id)
        result["pipeline"] = game_session.get_pipeline_jobs(save_id)
    return result


class AssetGenerateBody(BaseModel):
    entity_type: str
    entity_id: int
    asset_type: str = "scene"
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    use_auto_prompt: bool = True


_ASSET_GENERATE_ALLOWED_ENTITY_TYPES = {"npc", "player_character", "location", "other"}


@router.post("/saves/{save_id}/assets/generate")
async def post_asset_generate(request: Request, save_id: str, body: AssetGenerateBody) -> dict[str, Any]:
    """Create-or-regenerate an asset for any entity — unlike the id-scoped
    `/assets/{id}/regenerate` above, this works even when no asset row exists
    yet (NPC portraits, per-message chat scenes, first-time manual generate
    from `AssetEditor.js`)."""
    get_current_user(request)
    from titan.fugassa import asset_service, campaign_job_runner

    if body.entity_type not in _ASSET_GENERATE_ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported entity_type")

    cfg = config_store.load()
    if not bool(cfg.get("images_enabled", True)):
        raise HTTPException(status_code=400, detail="Image generation is disabled in Fugassa settings")

    db_path = save_store.game_db_path(save_id)
    metadata: dict[str, Any] | None = None
    title: str | None = None
    positive_prompt = body.positive_prompt
    negative_prompt = body.negative_prompt
    use_auto_prompt = body.use_auto_prompt

    if body.entity_type == "npc" and not positive_prompt:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            npc_row = conn.execute(
                "SELECT name, race, class_role, portrait_prompt, backstory_summary FROM npcs WHERE id = ?",
                (body.entity_id,),
            ).fetchone()
        finally:
            conn.close()
        if npc_row:
            stored_prompt = str(npc_row["portrait_prompt"] or "").strip()
            title = f"Portrait {npc_row['name']}"
            if stored_prompt:
                positive_prompt = stored_prompt
                use_auto_prompt = False
            else:
                metadata = {
                    "asset_type": body.asset_type,
                    "prompt_seed": {
                        "name": npc_row["name"],
                        "race": npc_row["race"] or "",
                        "class": npc_row["class_role"] or "",
                    },
                }
    elif body.entity_type == "player_character" and body.asset_type == "portrait" and not positive_prompt:
        from titan.fugassa.player_portrait_prompt import resolve_player_portrait_prompt

        game_state = read_game_json(save_store.save_dir(save_id))
        stored_pos, stored_neg = resolve_player_portrait_prompt(
            db_path,
            int(body.entity_id),
            game_state,
        )
        if stored_pos:
            positive_prompt = stored_pos
            if not negative_prompt and stored_neg:
                negative_prompt = stored_neg
            use_auto_prompt = False
        else:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                pc_row = conn.execute(
                    "SELECT name, race, class_name FROM player_characters WHERE id = ?",
                    (body.entity_id,),
                ).fetchone()
            finally:
                conn.close()
            if pc_row:
                title = f"Portrait {pc_row['name']}"
                metadata = {
                    "asset_type": body.asset_type,
                    "prompt_seed": {
                        "name": pc_row["name"],
                        "race": pc_row["race"] or "",
                        "class": pc_row["class_name"] or "",
                    },
                }
    elif body.entity_type == "other" and body.asset_type == "scene" and not body.positive_prompt:
        from titan.fugassa.scene_character_context import scene_cast_for_turn
        from titan.fugassa import scene_prompt_engine

        game_state = game_session.load_game_state(save_id)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            turn_row = conn.execute(
                "SELECT player_text, ai_text FROM turn_history WHERE turn_number = ?", (body.entity_id,)
            ).fetchone()
        finally:
            conn.close()
        raw_text = str(turn_row["ai_text"] or "") if turn_row else ""
        player_text = str(turn_row["player_text"] or "") if turn_row else ""
        description = scene_prompt_engine._scene_narrative_from_gm_text(raw_text)[:600]
        title = f"Scene — turn {body.entity_id}"
        cast_ctx = scene_cast_for_turn(
            state=game_state,
            db_path=db_path,
            turn_number=int(body.entity_id),
            narrative=description,
            player_action=player_text[:600],
        )
        prompt_seed = {
            "scene_kind": "chat_message",
            "scene_action": description,
            "description": description,
            **cast_ctx,
        }
        metadata = {
            "asset_type": "scene",
            "prompt_seed": prompt_seed,
        }
    elif body.entity_type == "location" and body.asset_type == "scene" and not body.positive_prompt:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            loc_row = conn.execute(
                "SELECT name, description_short, description_long FROM locations WHERE id = ?",
                (body.entity_id,),
            ).fetchone()
        finally:
            conn.close()
        if loc_row:
            title = f"Scene — {loc_row['name']}"
            metadata = {
                "asset_type": "scene",
                "prompt_seed": {
                    "name": loc_row["name"] or "",
                    "description": str(loc_row["description_long"] or loc_row["description_short"] or ""),
                },
            }

    result = asset_service.regenerate_for_entity(
        db_path,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        asset_type=body.asset_type,
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        use_auto_prompt=use_auto_prompt or not positive_prompt,
        metadata=metadata,
        title=title,
    )
    if result.get("success"):
        asset = result.get("asset") or {}
        new_id = int(asset.get("id") or 0)
        if new_id:
            owner, _ = _wizard_cfg(request)
            _kick_asset_pipeline(db_path, save_id, new_id, owner=owner)
        result["state"] = game_session.load_game_state(save_id)
        result["pipeline"] = game_session.get_pipeline_jobs(save_id)
    return result


class CharacterSheetBody(BaseModel):
    """Wizard draft fragment or full draft for sheet compute/validate."""

    model_config = {"extra": "allow"}


@router.post("/character-sheet/compute")
def compute_character_sheet(request: Request, body: CharacterSheetBody) -> dict[str, Any]:
    get_current_user(request)
    draft = body.model_dump()
    db = get_dnd5e_database()
    sheet = build(db, draft_to_build_input(draft))
    return {"success": True, "sheet": sheet}


@router.post("/character-sheet/validate")
def validate_character_sheet(request: Request, body: CharacterSheetBody) -> dict[str, Any]:
    get_current_user(request)
    draft = body.model_dump()
    result = validate_sheet_input(draft)
    return {
        "success": True,
        "ok": result["ok"],
        "errors": result["errors"],
        "sheet": result["sheet"],
    }


class LevelUpApplyBody(BaseModel):
    target_level: int = Field(..., ge=2, le=20)
    class_mechanic_choices: dict[str, Any] | None = None
    asi_choices: dict[str, Any] | None = None
    selected_cantrips: list[str] | None = None
    selected_spells_by_level: dict[str, Any] | None = None
    hp_current: int | None = None


@router.get("/saves/{save_id}/game/level-up/preview")
def game_level_up_preview(request: Request, save_id: str, level: int) -> dict[str, Any]:
    get_current_user(request)
    state = game_session.load_game_state(save_id)
    from titan.fugassa.level_progression import level_up_preview

    preview = level_up_preview(state, level)
    return {"success": True, **preview}


@router.post("/saves/{save_id}/game/level-up/apply")
def game_level_up_apply(request: Request, save_id: str, body: LevelUpApplyBody) -> dict[str, Any]:
    get_current_user(request)
    result = game_session.apply_level_up(
        save_id,
        target_level=body.target_level,
        class_mechanic_choices=body.class_mechanic_choices,
        asi_choices=body.asi_choices,
        selected_cantrips=body.selected_cantrips,
        selected_spells_by_level=body.selected_spells_by_level,
        hp_current=body.hp_current,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result.get("errors") or "Level-up failed")
    return {"success": True, "state": result.get("state"), "sheet": result.get("sheet")}


@router.post("/wizard/character/homebrew")
async def wizard_character_homebrew(request: Request, body: CharacterSheetBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_homebrew_sheet(body.model_dump(), None, owner=owner, llm_enabled=llm_on)
        if not data.get("valid"):
            raise HTTPException(status_code=422, detail=data.get("error") or "Homebrew generation failed")
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dnd5e/{resource}")
def get_dnd5e_resource(request: Request, resource: str) -> Any:
    get_current_user(request)
    if resource not in DND5E_ALLOWED:
        raise HTTPException(status_code=404, detail="Unknown dnd5e resource")
    try:
        return load_resource(resource)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _wizard_cfg(request: Request) -> tuple[str | None, bool]:
    get_current_user(request)
    owner = effective_user(request)
    cfg = config_store.load()
    return owner, bool(cfg.get("llm_enabled", True))


@router.post("/wizard/world/options")
async def wizard_world_options(request: Request, body: WizardWorldOptionsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_world_options(
            body.theme,
            body.campaignLength,
            None,
            _rules_dict(body.rulesContext),
            body.playerRequest,
            body.optionStart,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/world/summary")
async def wizard_world_summary(request: Request, body: WizardWorldSummaryBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    dialog = _dialog_text(body.dialog, body.dialogTranscript)
    try:
        data = await wizard_engine.generate_world_summary(
            body.theme,
            body.campaignLength,
            body.currentDraft,
            body.playerRequest,
            None,
            _rules_dict(body.rulesContext),
            dialog,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/backstory/options")
async def wizard_backstory_options(request: Request, body: WizardBackstoryOptionsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_backstory_options(
            body.theme,
            body.playerName,
            body.worldInformation,
            None,
            _rules_dict(body.rulesContext),
            body.playerRequest,
            body.characterProfile,
            body.optionStart,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/backstory/summary")
async def wizard_backstory_summary(request: Request, body: WizardBackstorySummaryBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    dialog = _dialog_text(body.dialog, body.dialogTranscript)
    try:
        data = await wizard_engine.generate_backstory_summary(
            body.theme,
            body.playerName,
            body.currentDraft,
            body.playerRequest,
            None,
            _rules_dict(body.rulesContext),
            body.worldInformation,
            body.characterProfile,
            dialog,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/inventory/options")
async def wizard_inventory_options(request: Request, body: WizardInventoryOptionsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_inventory_options(
            body.theme,
            body.playerName,
            body.worldInformation,
            None,
            _rules_dict(body.rulesContext),
            body.optionStart,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/inventory/summary")
async def wizard_inventory_summary(request: Request, body: WizardInventorySummaryBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    dialog = _dialog_text(body.dialog, body.dialogTranscript)
    try:
        data = await wizard_engine.generate_inventory_summary(
            body.theme,
            body.playerName,
            body.worldInformation,
            body.currentDraft,
            body.playerRequest,
            None,
            _rules_dict(body.rulesContext),
            dialog,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/gear/options")
async def wizard_gear_options(request: Request, body: WizardGearOptionsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_gear_options(
            body.theme,
            body.playerName,
            body.worldInformation,
            None,
            _rules_dict(body.rulesContext),
            body.optionStart,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/gear/summary")
async def wizard_gear_summary(request: Request, body: WizardGearSummaryBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    dialog = _dialog_text(body.dialog, body.dialogTranscript)
    try:
        data = await wizard_engine.generate_gear_summary(
            body.theme,
            body.playerName,
            body.worldInformation,
            body.currentDraft,
            body.playerRequest,
            None,
            _rules_dict(body.rulesContext),
            dialog,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/opening/options")
async def wizard_opening_options(request: Request, body: WizardOpeningOptionsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_opening_options(
            body.theme,
            body.playerName,
            body.worldInformation,
            None,
            _rules_dict(body.rulesContext),
            body.optionStart,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/opening/summary")
async def wizard_opening_summary(request: Request, body: WizardOpeningSummaryBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    dialog = _dialog_text(body.dialog, body.dialogTranscript)
    try:
        data = await wizard_engine.generate_opening_summary(
            body.theme,
            body.playerName,
            body.worldInformation,
            body.currentDraft,
            body.playerRequest,
            None,
            _rules_dict(body.rulesContext),
            dialog,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/portrait/prompts")
async def wizard_portrait_prompts(request: Request, body: WizardPortraitPromptsBody) -> dict[str, Any]:
    owner, llm_on = _wizard_cfg(request)
    try:
        data = await wizard_engine.generate_portrait_sd_prompts(
            body.theme,
            body.playerName,
            body.backstory,
            body.worldInformation,
            body.styleOverride,
            None,
            body.characterProfile,
            body.appearanceVisual,
            owner=owner,
            llm_enabled=llm_on,
        )
        return {"success": True, "data": data}
    except FugassaLlmDisabled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/wizard/portrait/generate")
async def wizard_portrait_generate(request: Request, body: WizardPortraitGenerateBody) -> dict[str, Any]:
    get_current_user(request)
    cfg = config_store.load()
    if not bool(cfg.get("images_enabled", True)):
        raise HTTPException(status_code=400, detail="Image generation is disabled in Fugassa settings")
    result = await portrait_gen.generate_portrait(
        positive_prompt=body.positive_prompt,
        negative_prompt=body.negative_prompt,
        theme=body.theme,
        style_override=body.style_override or None,
        campaign_style=body.style_override or None,
        image_style_default=str(cfg.get("image_style_default") or "") or None,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Portrait generation failed")
    return {"success": True, "data": result}


@router.get("/wizard/portrait/staging")
def get_wizard_portrait_staging(request: Request, v: str | None = None):
    """Serves the wizard's staged portrait PNG before a save exists (§Picture tab preview)."""
    from fastapi.responses import FileResponse

    get_current_user(request)
    path = wizard_portrait_staging_path()
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No staged portrait yet")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

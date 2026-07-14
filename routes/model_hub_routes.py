"""Titan Model Hub — proxy to host scheduler + endpoint sync."""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import ModelEndpoint, SessionLocal
from core.middleware import require_admin
from src.settings import load_settings, save_settings

logger = logging.getLogger(__name__)

HUB_CONFIG_PATH = Path(os.environ.get("TITAN_MODELS_CONFIG", "/app/data/titan-models.yaml"))
SCHEDULER_URL = os.environ.get("TITAN_SCHEDULER_URL", "http://host.docker.internal:8150").rstrip("/")

TITAN_LLM_ENDPOINT_ID = "titan-llm-host"
TITAN_SD_ENDPOINT_ID = "titan-sd-scheduler"
TITAN_VISION_ENDPOINT_ID = "titan-vision-host"


class HubProxyBody(BaseModel):
    payload: Optional[dict] = None


async def _scheduler_request(method: str, path: str, json_body: dict | None = None) -> Any:
    url = f"{SCHEDULER_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=30.0)) as client:
            r = await client.request(method, url, json=json_body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Scheduler unreachable at {SCHEDULER_URL}: {exc}") from exc
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text[:500]
        raise HTTPException(status_code=r.status_code, detail=detail)
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return r.text


def _load_hub_file() -> dict:
    if not HUB_CONFIG_PATH.is_file():
        return {"version": 0, "error": "missing config"}
    with HUB_CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_hub_file(cfg: dict) -> None:
    HUB_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HUB_CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    try:
        from titan.hub_sd_config import invalidate_sd_config_cache

        invalidate_sd_config_cache()
    except Exception:
        pass


def _upsert_endpoint(
    ep_id: str,
    name: str,
    base_url: str,
    model_type: str,
    pinned_models: list[str],
    supports_tools: bool | None = None,
    enabled: bool = True,
) -> ModelEndpoint:
    db = SessionLocal()
    try:
        row = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        if not row:
            row = ModelEndpoint(id=ep_id, name=name, base_url=base_url)
            db.add(row)
        row.name = name
        row.base_url = base_url
        row.model_type = model_type
        row.is_enabled = enabled
        row.endpoint_kind = "local"
        row.owner = None
        row.pinned_models = json.dumps(pinned_models)
        if supports_tools is not None:
            row.supports_tools = supports_tools
        row.cached_models = json.dumps(pinned_models)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _sd_pinned_models(cfg: dict) -> list[str]:
    """SD profile pins must match scheduler /v1/models IDs (checkpoint filenames)."""
    pins: list[str] = []
    sd_models = (cfg.get("models") or {}).get("sd") or []
    for prof in (cfg.get("launch_profiles") or {}).get("sd") or []:
        mid = prof.get("model_id")
        model = next((m for m in sd_models if m.get("id") == mid), None)
        if model and model.get("path"):
            pins.append(Path(str(model["path"])).name)
        elif mid:
            pins.append(str(mid))
    return pins or [
        "thisisrealSDXL_v30.safetensors",
        "novaAnimeXL_ilV190.safetensors",
    ]


def sync_hub_endpoints() -> dict[str, Any]:
    """Register Titan local LLM + SD scheduler in ModelEndpoint and settings."""
    cfg = _load_hub_file()
    general = (cfg.get("roles") or {}).get("general") or {}
    port = general.get("endpoint_port", 8000)
    llm_url = f"http://host.docker.internal:{port}/v1"
    sd_url = f"{SCHEDULER_URL}/v1"

    # Deterministic model name from the Hub config — the GGUF basename matches
    # the id llama-server reports. Used as the fallback so a transient LLM
    # down-state at startup (e.g. while vision/Gemma is loaded) can't clobber
    # the endpoint with a generic name that no longer matches the served model.
    general_model = next(
        (
            m for m in (cfg.get("models") or {}).get("llm") or []
            if m.get("id") == general.get("model_id")
        ),
        {},
    )
    llm_model = (
        general_model.get("gguf_file")
        or general_model.get("display_name")
        or "Huihui-Opus-35B"
    )[:80]
    try:
        cached = httpx.get(f"{llm_url}/models", timeout=5.0)
        if cached.status_code == 200:
            data = cached.json().get("data") or cached.json().get("models") or []
            if data:
                mid = data[0].get("id") or data[0].get("name")
                if mid:
                    llm_model = mid.split("/")[-1][:80]
    except Exception as exc:
        logger.warning("Hub sync: LLM probe failed, using config name %s: %s", llm_model, exc)

    _upsert_endpoint(
        TITAN_LLM_ENDPOINT_ID,
        "Titan LLM (host)",
        llm_url,
        "llm",
        [llm_model],
        supports_tools=True,
    )
    # Vision role (Gemma 4 12B + mmproj) on its own host port. The service is
    # mutually exclusive with the general LLM on a single 16 GB GPU, so it's
    # only live after POST /api/titan/hub/load {"role":"vision"}; we still
    # register the endpoint so the multimodal model is selectable in Odysseus.
    vision = (cfg.get("roles") or {}).get("vision") or {}
    vision_port = vision.get("endpoint_port")
    vision_model_id = vision.get("model_id")
    if vision_port and vision_model_id:
        vision_models = next(
            (
                m for m in (cfg.get("models") or {}).get("llm") or []
                if m.get("id") == vision_model_id
            ),
            {},
        )
        vision_name = vision_models.get("display_name", "Gemma 4 12B (vision)")
        # Registered DISABLED: vision (Gemma) is mutually exclusive with the
        # general LLM on a single GPU, so :8001 is down whenever Qwen runs.
        # Leaving it enabled put a tool-incapable model into the active picker
        # and broke tool routing when a chat landed on it. The vision-load flow
        # (POST /api/titan/hub/load {"role":"vision"}) enables it on demand.
        _upsert_endpoint(
            TITAN_VISION_ENDPOINT_ID,
            "Titan Vision (host)",
            f"http://host.docker.internal:{vision_port}/v1",
            "llm",
            [vision_name],
            supports_tools=False,
            enabled=False,
        )

    sd_pins = _sd_pinned_models(cfg)
    _upsert_endpoint(
        TITAN_SD_ENDPOINT_ID,
        "Titan SD (scheduler)",
        sd_url,
        "image",
        sd_pins,
        supports_tools=None,
    )

    settings = load_settings()
    settings["default_endpoint_id"] = TITAN_LLM_ENDPOINT_ID
    settings["default_model"] = llm_model
    settings["image_model"] = settings.get("image_model") or sd_pins[0]
    save_settings(settings)

    return {
        "llm_endpoint_id": TITAN_LLM_ENDPOINT_ID,
        "llm_url": llm_url,
        "llm_model": llm_model,
        "sd_endpoint_id": TITAN_SD_ENDPOINT_ID,
        "sd_url": sd_url,
        "vision_endpoint_id": TITAN_VISION_ENDPOINT_ID if vision_port and vision_model_id else None,
    }


def _general_role_model(cfg: dict) -> tuple[dict, dict]:
    """Return (general role spec, llm model dict) for the general chat model."""
    general = (cfg.get("roles") or {}).get("general") or {}
    model_id = general.get("model_id")
    model = next(
        (
            m for m in (cfg.get("models") or {}).get("llm") or []
            if m.get("id") == model_id
        ),
        {},
    )
    return general, model


def _profile_vram_mb(model: dict, profile_id: str) -> int:
    vram = model.get("vram_mb") or {}
    if isinstance(vram, dict):
        return int(vram.get(profile_id) or vram.get("fast") or 12000)
    return int(vram or 12000)


def _general_required_mb(cfg: dict) -> int:
    """VRAM (MB) needed to load the general LLM at its configured profile."""
    general, model = _general_role_model(cfg)
    profile_id = general.get("profile_id") or "fast"
    return _profile_vram_mb(model, profile_id)


def _general_llm_profiles(cfg: dict, free_mb: int) -> tuple[list[dict[str, Any]], str]:
    """Launch profiles for the general model, annotated with VRAM fit."""
    general, model = _general_role_model(cfg)
    model_id = general.get("model_id")
    configured = str(general.get("profile_id") or "fast")
    profiles: list[dict[str, Any]] = []
    for prof in (cfg.get("launch_profiles") or {}).get("llm") or []:
        if prof.get("model_id") != model_id:
            continue
        pid = prof.get("id")
        if not pid:
            continue
        required_mb = _profile_vram_mb(model, str(pid))
        profiles.append(
            {
                "id": pid,
                "display_name": prof.get("display_name") or pid,
                "required_mb": required_mb,
                "fits": free_mb >= required_mb,
            }
        )
    return profiles, configured


async def _compute_llm_health() -> dict[str, Any]:
    """Report whether the general LLM is up, and if not, whether it can be
    safely (re)started given current VRAM — without evicting an active SD or
    vision model. Used by the chat guard to ask before starting vs. warn."""
    cfg = _load_hub_file()
    general = (cfg.get("roles") or {}).get("general") or {}
    port = general.get("endpoint_port", 8000)
    llm_url = f"http://host.docker.internal:{port}/v1/models"

    # Deterministic configured model name (gguf basename) as fallback.
    general_model = next(
        (
            m for m in (cfg.get("models") or {}).get("llm") or []
            if m.get("id") == general.get("model_id")
        ),
        {},
    )
    model_name = (
        general_model.get("gguf_file")
        or general_model.get("display_name")
        or "general"
    )

    # 1) Is the general LLM already serving?
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(4.0)) as client:
            r = await client.get(llm_url)
        if r.status_code == 200:
            try:
                data = r.json().get("data") or r.json().get("models") or []
                if data:
                    mid = data[0].get("id") or data[0].get("name")
                    if mid:
                        model_name = mid.split("/")[-1]
            except Exception:
                pass
            return {"up": True, "role": "general", "model": model_name}
    except httpx.RequestError:
        pass

    # 2) Down — decide whether it can be safely started right now.
    required_mb = _general_required_mb(cfg)
    vram = (cfg.get("vram") or {})
    reserve_mb = int(vram.get("reserve_mb") or 0)
    gpu_total_mb = int(vram.get("gpu_total_mb") or 0)
    used_mb = None
    sd_active = False
    try:
        status = await _scheduler_request("GET", "/v1/hub/status")
        if isinstance(status, dict):
            used_mb = status.get("vram_used_mb")
            gpu_total_mb = int(status.get("gpu_total_mb") or gpu_total_mb)
            sd_active = bool((status.get("sd") or {}).get("active"))
    except HTTPException:
        used_mb = None

    if used_mb is None:
        return {
            "up": False,
            "role": "general",
            "model": model_name,
            "can_load": False,
            "reason": "Scheduler unreachable — cannot check VRAM safely.",
            "required_mb": required_mb,
        }

    free_mb = max(0, gpu_total_mb - reserve_mb - int(used_mb))
    profiles, configured_profile_id = _general_llm_profiles(cfg, free_mb)
    loadable = [p for p in profiles if p.get("fits")]
    min_required = min((p["required_mb"] for p in profiles), default=required_mb)
    can_load = bool(loadable)
    configured_fits = any(
        p.get("id") == configured_profile_id and p.get("fits") for p in profiles
    )

    if can_load:
        if configured_fits:
            reason = (
                f"Volno {free_mb} MB VRAM — naposledy používaný profil "
                f"„{configured_profile_id}“ se vejde."
            )
        else:
            reason = (
                f"Volno {free_mb} MB VRAM — uložený profil „{configured_profile_id}“ "
                f"potřebuje {required_mb} MB, ale menší profily se vejdou."
            )
    elif sd_active:
        reason = (
            f"Stable Diffusion drží VRAM — volno jen {free_mb} MB, "
            f"nejmenší profil Qwen potřebuje {min_required} MB. "
            f"Nejdřív ukončete SD v Model Hubu."
        )
    else:
        reason = (
            f"Nedostatek VRAM — volno {free_mb} MB, nejmenší profil Qwen potřebuje "
            f"{min_required} MB. Zavřete aplikace na GPU (Chrome, LM Studio…)."
        )

    return {
        "up": False,
        "role": "general",
        "model": model_name,
        "can_load": can_load,
        "configured_profile_id": configured_profile_id,
        "configured_profile_fits": configured_fits,
        "profiles": profiles,
        "loadable_profiles": loadable,
        "free_mb": free_mb,
        "required_mb": required_mb,
        "min_required_mb": min_required,
        "used_mb": int(used_mb),
        "gpu_total_mb": gpu_total_mb,
        "sd_active": sd_active,
        "reason": reason,
    }


def setup_model_hub_routes() -> APIRouter:
    router = APIRouter(prefix="/api/titan/hub", tags=["titan-hub"])

    @router.get("/llm-health")
    async def llm_health(request: Request):
        # Read-only; no admin gate so the chat guard can always preflight.
        from fastapi.responses import JSONResponse

        body = await _compute_llm_health()
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @router.get("/image-status")
    async def image_status(request: Request):
        """Read-only SD generation phase for chat probe / progress UI."""
        try:
            status = await _scheduler_request("GET", "/v1/status")
        except HTTPException:
            return {"phase": "idle", "progress_step": 0, "progress_total": 0}
        state = (status or {}).get("state") or {}
        return {
            "phase": state.get("phase", "idle"),
            "progress_step": state.get("progress_step", 0),
            "progress_total": state.get("progress_total", 0),
            "last_error": state.get("last_error"),
        }

    @router.post("/image-stop")
    async def image_stop(request: Request):
        """Abort SD work and restore LLM (Stop during generation)."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await _scheduler_request("POST", "/v1/release/sd", {"restart_llm": True})

    @router.post("/image-resolve")
    async def image_resolve(request: Request, body: dict):
        """Resolve proposal presets without generating (for UI card)."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        from titan.image_kernel import resolve_proposal
        owner = effective_user(request)
        return await resolve_proposal(body or {}, owner=owner)

    @router.post("/image-execute")
    async def image_execute(request: Request, body: dict):
        """Run ImageProposal from UI card (deterministic, no LLM)."""
        from src.auth_helpers import effective_user
        owner = effective_user(request)
        if not owner:
            raise HTTPException(401, "Authentication required")
        session_id = (body or {}).get("session_id")
        from titan.image_kernel import execute_proposal
        try:
            result = await execute_proposal(body or {}, owner=owner, session_id=session_id)
        except Exception as exc:
            logger.exception("image-execute failed")
            raise HTTPException(500, detail=str(exc)) from exc
        if result.get("error"):
            raise HTTPException(400, detail=result["error"])
        return result

    @router.get("/image-pipeline-config")
    async def image_pipeline_config(request: Request):
        """Read-only routing flags (wizard vs card per op)."""
        from fastapi.responses import JSONResponse
        from titan.image_guidance import guidance_as_dict
        from titan.image_pipeline_config import config_as_dict

        body = config_as_dict()
        body["guidance"] = guidance_as_dict()
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @router.get("/sd-loras")
    async def sd_loras(request: Request):
        """List LoRA files from scheduler (for proposal card UI)."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        try:
            data = await _scheduler_request("GET", "/v1/loras")
        except HTTPException:
            return {"loras": []}
        return data if isinstance(data, dict) else {"loras": []}

    @router.get("/image-studio/config")
    async def image_studio_config(request: Request):
        """Profiles, defaults, and VRAM state for the manual Image Studio tool."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        from titan.hub_sd_config import load_models_config
        from titan.style_labels import STYLE_LABELS, get_active_styles
        from src.settings import get_setting

        cfg = _load_hub_file()
        active = get_active_styles()
        profiles: list[dict[str, Any]] = []
        for prof in (cfg.get("launch_profiles") or {}).get("sd") or []:
            pid = (prof.get("id") or "").strip()
            if not pid or pid not in active:
                continue
            defaults = prof.get("chat_defaults") or {}
            style = (defaults.get("style") or pid).strip().lower()
            w = defaults.get("width") or 1024
            h = defaults.get("height") or 1024
            profiles.append(
                {
                    "id": pid,
                    "style": style,
                    "display_name": prof.get("display_name") or pid,
                    "model_label": STYLE_LABELS.get(style, style),
                    "defaults": {
                        "size": f"{w}x{h}",
                        "width": w,
                        "height": h,
                        "steps": defaults.get("steps"),
                        "cfg_scale": defaults.get("cfg_scale"),
                        "sampler_name": defaults.get("sampler_name"),
                        "scheduler": defaults.get("scheduler"),
                        "negative_prompt": defaults.get("negative_prompt") or "",
                    },
                }
            )
        try:
            status = await _scheduler_request("GET", "/v1/status")
        except HTTPException:
            status = {}
        sd = (status or {}).get("sd") or {}
        llm = (status or {}).get("llm") or {}
        state = (status or {}).get("state") or {}
        return {
            "profiles": profiles,
            "control_net_default": bool(get_setting("image_control_net", False)),
            "sd_active": bool(sd.get("active")),
            "sd_profile": sd.get("profile"),
            "llm_active": bool(llm.get("active")),
            "phase": state.get("phase", "idle"),
            "vram_used_mb": status.get("vram_used_mb"),
            "gpu_total_mb": status.get("gpu_total_mb"),
        }

    @router.post("/image-studio/warm")
    async def image_studio_warm(request: Request, body: dict):
        """Stop LLM and load SD profile (warm model for manual generation)."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        profile_id = (body or {}).get("profile_id") or (body or {}).get("profile")
        if not profile_id:
            raise HTTPException(400, "profile_id required")
        return await _scheduler_request(
            "POST",
            "/v1/allocate/sd",
            {"profile": str(profile_id), "stop_llm": True},
        )

    @router.post("/image-studio/release")
    async def image_studio_release(request: Request):
        """Stop SD and restore LLM when leaving Image Studio."""
        from src.auth_helpers import effective_user
        if not effective_user(request):
            raise HTTPException(401, "Authentication required")
        return await _scheduler_request("POST", "/v1/release/sd", {"restart_llm": True})

    @router.get("/config")
    async def get_config(request: Request):
        require_admin(request)
        return _load_hub_file()

    @router.put("/config")
    async def put_config(request: Request, body: dict):
        require_admin(request)
        if not body or "version" not in body:
            raise HTTPException(400, "invalid config")
        _save_hub_file(body)
        return {"status": "saved"}

    @router.get("/status")
    async def hub_status(request: Request):
        require_admin(request)
        return await _scheduler_request("GET", "/v1/hub/status")

    @router.get("/cached")
    async def hub_cached(request: Request):
        require_admin(request)
        return await _scheduler_request("GET", "/v1/hub/cached")

    @router.post("/load")
    async def hub_load(request: Request, body: dict):
        require_admin(request)
        result = await _scheduler_request("POST", "/v1/hub/load", body)
        sync_hub_endpoints()
        return result

    @router.post("/unload")
    async def hub_unload(request: Request, body: dict):
        require_admin(request)
        return await _scheduler_request("POST", "/v1/hub/unload", body)

    @router.post("/sync-endpoints")
    async def hub_sync(request: Request):
        require_admin(request)
        return sync_hub_endpoints()

    @router.post("/download")
    async def hub_download(request: Request, body: dict):
        require_admin(request)
        return await _scheduler_request("POST", "/v1/hub/download", body)

    @router.get("/downloads")
    async def hub_downloads(request: Request):
        require_admin(request)
        return await _scheduler_request("GET", "/v1/hub/downloads")

    @router.get("/downloads/{job_id}")
    async def hub_download_job(request: Request, job_id: str):
        require_admin(request)
        return await _scheduler_request("GET", f"/v1/hub/downloads/{job_id}")

    @router.get("/scheduler-url")
    async def scheduler_url(request: Request):
        require_admin(request)
        return {"url": SCHEDULER_URL}

    return router

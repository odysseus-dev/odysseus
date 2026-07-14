"""Unified SD generation via Titan scheduler — ADR §L."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from titan.fugassa.game_bootstrap import resolve_theme, wizard_portrait_staging_path
from titan.fugassa import asset_prompts
from titan.image_proposal import build_proposal, proposal_to_scheduler_body
from titan.control_net import resolve_control_for_scheduler

LOG = logging.getLogger("titan.fugassa.asset_gen")

_SCHEDULER_URL = os.environ.get("TITAN_SCHEDULER_URL", "http://host.docker.internal:8150").rstrip("/")
_USE_SCHEDULER = os.environ.get("TITAN_LLM_VIA_SCHEDULER", "true").lower() not in ("0", "false", "no")
_ENSURE_SD_TIMEOUT_SEC = float(os.environ.get("TITAN_ENSURE_SD_TIMEOUT_SEC", "200"))

_VALID_SD_STYLES = frozenset({"realistic", "anime", "pixelart", "krea"})

_THEME_TO_STYLE = {
    "fantasy": "realistic",
    "sci-fi": "krea",
    "sci fi": "krea",
    "modern": "realistic",
    "present": "realistic",
    "custom": "realistic",
    "anime": "anime",
}

_ASPECT_MAP = {
    "portrait": "portrait",
    "scene": "landscape",
    "token": "portrait",
    "map": "landscape",
    "image": "landscape",
}


def style_for_theme(theme: str, override: str | None = None) -> str:
    if override and str(override).strip().lower() in _VALID_SD_STYLES:
        return str(override).strip().lower()
    key = str(theme or "fantasy").strip().lower()
    return _THEME_TO_STYLE.get(key, "realistic")


def resolve_image_style(
    *,
    theme: str,
    campaign_style: str | None = None,
    global_default: str | None = None,
) -> str:
    """Campaign Genre-tab pick beats global Fugassa settings; both beat theme auto."""
    key = str(campaign_style or "").strip().lower()
    if key and key != "auto":
        if key in _VALID_SD_STYLES:
            return key
    for candidate in (global_default,):
        gkey = str(candidate or "").strip().lower()
        if gkey and gkey not in {"auto", "fantasy"} and gkey in _VALID_SD_STYLES:
            return gkey
    return style_for_theme(theme)


def image_style_from_state(state: dict[str, Any] | None) -> str:
    wp = (state or {}).get("world_profile") or {}
    return str(wp.get("image_style") or "").strip()


async def _ensure_sd_ready(profile: str | None = None) -> None:
    """Best-effort VRAM scheduler preflight before SD generation."""
    if not _USE_SCHEDULER:
        return
    payload: dict[str, Any] = {}
    if profile:
        payload["profile"] = str(profile).strip().lower()
    try:
        async with httpx.AsyncClient(timeout=_ENSURE_SD_TIMEOUT_SEC) as client:
            resp = await client.post(f"{_SCHEDULER_URL}/v1/external/ensure-sd", json=payload)
            if resp.status_code >= 400:
                LOG.warning("VRAM scheduler ensure-sd returned %s: %s", resp.status_code, resp.text[:300])
    except Exception as exc:  # noqa: BLE001
        LOG.warning("VRAM scheduler ensure-sd unreachable, proceeding: %s", exc)


async def generate_image(
    *,
    positive_prompt: str,
    negative_prompt: str = "",
    asset_type: str = "portrait",
    theme: str = "Fantasy",
    style_override: str | None = None,
    campaign_style: str | None = None,
    image_style_default: str | None = None,
    dest_path: str | None = None,
    init_image_path: str | None = None,
    init_strength: float | None = None,
    control: dict[str, Any] | None = None,
    shutdown_after: bool | None = None,
    steps: int | None = None,
    cfg_scale: float | None = None,
    sampler: str | None = None,
    scheduler: str | None = None,
    quality: str | None = None,
    theme_facets: frozenset[str] | list[str] | None = None,
    theme_label: str | None = None,
) -> dict[str, Any]:
    positive = str(positive_prompt or "").strip()
    if not positive:
        return {"success": False, "error": "Prompt is empty"}

    style = resolve_image_style(
        theme=theme,
        campaign_style=style_override or campaign_style,
        global_default=image_style_default,
    )
    style_hint = str(campaign_style or image_style_default or "").strip()
    negative = str(negative_prompt or "").strip()
    is_scene = asset_type in ("scene", "map", "image")
    facets = frozenset(theme_facets) if theme_facets else None
    if is_scene:
        positive = asset_prompts.apply_theme_to_scene_prompt(
            positive,
            theme,
            style_hint=style_hint,
            facets=facets,
            theme_label=theme_label,
        )
        negative = asset_prompts.merge_scene_theme_negative(negative, theme, facets=facets)
    await _ensure_sd_ready(profile=style)

    aspect = _ASPECT_MAP.get(asset_type, "portrait")
    proposal_kwargs: dict[str, Any] = {
        "op": "generate",
        "prompt": positive,
        "negative_prompt": negative,
        "style": style,
        "aspect": aspect,
        "quality": str(quality or "high").strip().lower(),
    }
    if is_scene:
        proposal_kwargs["scene"] = True
    if shutdown_after is not None:
        proposal_kwargs["shutdown_after"] = bool(shutdown_after)
    if steps is not None:
        proposal_kwargs["steps"] = int(steps)
    if cfg_scale is not None:
        proposal_kwargs["cfg_scale"] = float(cfg_scale)
    if sampler:
        proposal_kwargs["sampler"] = str(sampler).strip()
    if scheduler:
        proposal_kwargs["scheduler"] = str(scheduler).strip()
    if init_image_path and os.path.isfile(init_image_path):
        with open(init_image_path, "rb") as imgf:
            proposal_kwargs["image"] = base64.b64encode(imgf.read()).decode("ascii")
        proposal_kwargs["strength"] = float(init_strength if init_strength is not None else 0.38)
    if control:
        resolved = resolve_control_for_scheduler(control)
        if resolved:
            proposal_kwargs["control"] = resolved
    proposal = build_proposal(proposal_kwargs)
    body = proposal_to_scheduler_body(proposal)

    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=20.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{_SCHEDULER_URL}/v1/images/generations", json=body)
    except Exception as exc:
        LOG.warning("Scheduler unreachable: %s", exc)
        return {"success": False, "error": f"Scheduler unreachable: {exc}"}

    if resp.status_code != 200:
        detail = resp.text[:400]
        try:
            payload = resp.json()
            detail = payload.get("error", detail)
            if isinstance(detail, dict):
                detail = detail.get("message", str(detail))
        except Exception:
            pass
        return {"success": False, "error": f"Image generation failed ({resp.status_code}): {detail}"}

    try:
        data = resp.json()
    except Exception:
        return {"success": False, "error": "Scheduler returned non-JSON"}

    images = (data or {}).get("data") or []
    if not images or not images[0].get("b64_json"):
        return {"success": False, "error": "No image returned from scheduler"}

    raw = base64.b64decode(images[0]["b64_json"])
    if not dest_path:
        dest_path = wizard_portrait_staging_path()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(raw)

    return {
        "success": True,
        "path": dest_path,
        "style": style,
        "theme": resolve_theme({"theme_mode": theme}) if theme else theme,
    }


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _scene_control_weight() -> float:
    """Pass-2 CN strength — lower than typical chat CN; composition comes from pass1 edges."""
    weight = _env_float("FUGASSA_SCENE_CONTROLNET_WEIGHT", 0.35)
    return max(0.1, min(0.9, weight))


def _scene_pass2_img2img_strength() -> float | None:
    """img2img denoise on pass2; 0 disables (CN-only refinement)."""
    raw = os.environ.get("FUGASSA_SCENE_PASS2_STRENGTH", "0.35").strip()
    if raw.lower() in ("0", "false", "no", "off", "none"):
        return None
    return max(0.1, min(0.85, _env_float("FUGASSA_SCENE_PASS2_STRENGTH", 0.35)))


def _scene_pass2_gen_params(style: str) -> dict[str, Any]:
    """Pass-2 uses milder CFG / fewer steps than pass1 (refinement, not re-compose)."""
    from titan.hub_sd_config import chat_defaults_for_style

    defaults = chat_defaults_for_style(style)
    base_steps = int(defaults.get("steps") or 28)
    base_cfg = float(defaults.get("cfg_scale") or 6.0)
    steps = _env_int("FUGASSA_SCENE_PASS2_STEPS", max(16, base_steps - 6))
    cfg = _env_float("FUGASSA_SCENE_PASS2_CFG", round(max(4.0, base_cfg - 1.5), 1))
    return {"steps": steps, "cfg_scale": cfg}


def _scene_two_pass_enabled() -> bool:
    """Scene two-pass ControlNet (pass1 txt2img → pass2 canny). Disable via FUGASSA_SCENE_SINGLE_PASS=1."""
    return os.environ.get("FUGASSA_SCENE_SINGLE_PASS", "").lower() not in ("1", "true", "yes")


async def generate_scene_two_pass(
    *,
    positive_prompt: str,
    negative_prompt: str = "",
    theme: str = "Fantasy",
    style_override: str | None = None,
    campaign_style: str | None = None,
    image_style_default: str | None = None,
    dest_path: str,
    theme_facets: frozenset[str] | list[str] | None = None,
    theme_label: str | None = None,
) -> dict[str, Any]:
    """Scene pipeline: pass1 txt2img (composition) → pass2 img2img + CN canny refine."""
    if not dest_path:
        return {"success": False, "error": "dest_path required for scene two-pass"}

    style = resolve_image_style(
        theme=theme,
        campaign_style=style_override or campaign_style,
        global_default=image_style_default,
    )
    pass1_path = f"{dest_path}.pass1.png"
    pass1_common = dict(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        asset_type="scene",
        theme=theme,
        style_override=style_override,
        campaign_style=campaign_style,
        image_style_default=image_style_default,
        theme_facets=theme_facets,
        theme_label=theme_label,
    )

    LOG.info("Scene pass 1/2 txt2img (composition) → %s", pass1_path)
    pass1 = await generate_image(**pass1_common, dest_path=pass1_path, shutdown_after=False)
    if not pass1.get("success"):
        return pass1

    facets = frozenset(theme_facets) if theme_facets else None
    refinement_prompt = asset_prompts.build_scene_refinement_prompt(
        positive_prompt,
        style=style,
        theme=theme,
        style_hint=str(style_override or campaign_style or image_style_default or "").strip(),
        facets=facets,
        theme_label=theme_label,
    )
    pass2_gen = _scene_pass2_gen_params(style)
    weight = _scene_control_weight()
    img2img_strength = _scene_pass2_img2img_strength()
    pass2_kwargs: dict[str, Any] = dict(
        pass1_common,
        positive_prompt=refinement_prompt,
        dest_path=dest_path,
        control={"type": "canny", "path": pass1_path, "weight": weight, "preprocess": True},
        shutdown_after=True,
        **pass2_gen,
    )
    if img2img_strength is not None:
        pass2_kwargs["init_image_path"] = pass1_path
        pass2_kwargs["init_strength"] = img2img_strength
        LOG.info(
            "Scene pass 2/2 img2img+%.2f + CN canny %.2f steps=%s cfg=%.1f",
            img2img_strength,
            weight,
            pass2_gen.get("steps"),
            pass2_gen.get("cfg_scale"),
        )
    else:
        LOG.info(
            "Scene pass 2/2 CN canny weight=%.2f steps=%s cfg=%.1f ← %s",
            weight,
            pass2_gen.get("steps"),
            pass2_gen.get("cfg_scale"),
            pass1_path,
        )
    pass2 = await generate_image(**pass2_kwargs)

    if pass2.get("success"):
        try:
            os.remove(pass1_path)
        except OSError:
            pass
        pass2["two_pass"] = True
        return pass2

    LOG.warning("Scene pass 2 failed (%s), keeping pass 1 output", pass2.get("error"))
    try:
        os.replace(pass1_path, dest_path)
    except OSError:
        return pass2
    return {
        **pass1,
        "path": dest_path,
        "two_pass": False,
        "pass2_error": pass2.get("error"),
    }


async def generate_portrait(
    *,
    positive_prompt: str,
    negative_prompt: str = "",
    theme: str = "Fantasy",
    style_override: str | None = None,
    campaign_style: str | None = None,
    image_style_default: str | None = None,
) -> dict[str, Any]:
    """Wizard-compat wrapper."""
    dest = wizard_portrait_staging_path()
    result = await generate_image(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        asset_type="portrait",
        theme=theme,
        style_override=style_override,
        campaign_style=campaign_style,
        image_style_default=image_style_default,
        dest_path=dest,
    )
    if result.get("success"):
        result["relative_path"] = "wizard_portrait_staging.png"
    return result

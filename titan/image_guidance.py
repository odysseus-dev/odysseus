"""Opt-in image workflow guidance (pipeline step 9) — all flags OFF by default."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Set

from titan.image_pipeline_config import (
    _coerce_bool,
    _load_settings_dict,
    _load_yaml,
)

LOG = logging.getLogger("titan.image_guidance")

_GUIDANCE_ENV = {
    "TITAN_IMAGE_GUIDANCE_PIN_RAG": "pin_generate_image_rag",
    "TITAN_IMAGE_GUIDANCE_AUTO_RESUME": "auto_resume_pending_workflow",
    "TITAN_IMAGE_GUIDANCE_AUTO_REGEN": "auto_regenerate_bypass_llm",
    "TITAN_IMAGE_GUIDANCE_NUDGE": "nudge_announced_without_tool",
}


@dataclass
class ImageGuidanceConfig:
    pin_generate_image_rag: bool = False
    auto_resume_pending_workflow: bool = False
    auto_regenerate_bypass_llm: bool = False
    nudge_announced_without_tool: bool = False


_GUIDANCE_DEFAULT = ImageGuidanceConfig()
_GUIDANCE_CACHE: Optional[ImageGuidanceConfig] = None


def _apply_guidance_dict(cfg: ImageGuidanceConfig, data: Dict[str, Any]) -> ImageGuidanceConfig:
    out = ImageGuidanceConfig(**asdict(cfg))
    block = data.get("guidance") if isinstance(data.get("guidance"), dict) else data
    for field in asdict(out):
        if field in block:
            b = _coerce_bool(block[field])
            if b is not None:
                setattr(out, field, b)
    return out


def _load_guidance_env() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for env_key, field_name in _GUIDANCE_ENV.items():
        if env_key not in __import__("os").environ:
            continue
        b = _coerce_bool(__import__("os").environ[env_key])
        if b is not None:
            out[field_name] = b
    return out


def load_image_guidance_config(*, force: bool = False) -> ImageGuidanceConfig:
    global _GUIDANCE_CACHE
    if not force and _GUIDANCE_CACHE is not None:
        return _GUIDANCE_CACHE

    cfg = ImageGuidanceConfig()
    cfg = _apply_guidance_dict(cfg, _load_yaml())
    cfg = _apply_guidance_dict(cfg, _load_settings_dict())
    cfg = _apply_guidance_dict(cfg, _load_guidance_env())
    _GUIDANCE_CACHE = cfg
    return cfg


def guidance_enabled() -> bool:
    g = load_image_guidance_config()
    return any(
        (
            g.pin_generate_image_rag,
            g.auto_resume_pending_workflow,
            g.auto_regenerate_bypass_llm,
            g.nudge_announced_without_tool,
        )
    )


def guidance_as_dict() -> Dict[str, Any]:
    return asdict(load_image_guidance_config())


def apply_image_tool_guidance(
    relevant_tools: Optional[Set[str]],
    messages: List[Dict[str, Any]],
    *,
    user_text: str = "",
) -> None:
    """Mutate relevant_tools when guidance flags are ON. No-op when all OFF."""
    if relevant_tools is None:
        return

    cfg = load_image_guidance_config()
    if not any(
        (
            cfg.pin_generate_image_rag,
            cfg.auto_resume_pending_workflow,
            cfg.auto_regenerate_bypass_llm,
            cfg.nudge_announced_without_tool,
        )
    ):
        return

    try:
        from src.pending_tool_workflow import (
            PENDING_TOOL_COMPANIONS,
            find_pending_tool_workflow,
            should_auto_regenerate_image,
        )
    except Exception as exc:
        LOG.debug("pending_tool_workflow unavailable: %s", exc)
        return

    pending = find_pending_tool_workflow(messages)

    if cfg.pin_generate_image_rag and pending:
        relevant_tools.add(pending.tool_name)
        relevant_tools.update(PENDING_TOOL_COMPANIONS.get(pending.tool_name, ()))

    if cfg.auto_regenerate_bypass_llm and should_auto_regenerate_image(messages, user_text):
        relevant_tools.add("generate_image")

    # auto_resume / nudge hooks reserved — intentionally inactive until toggled
    # and covered by dedicated E2E tests (default path = UI card / wizard).

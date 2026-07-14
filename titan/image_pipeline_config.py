"""Configurable routing: wizard (chat confirm) vs UI card per image op.

Override order (later wins):
  1. Built-in defaults (hybrid: generate=wizard, regen=card, fallback on)
  2. data/titan-image-pipeline.yaml (if present)
  3. settings.json key ``titan_image_pipeline`` (dict)
  4. Env ``TITAN_IMAGE_PIPELINE_*`` per field

Example YAML::

    generate: wizard
    regenerate: card
    upscale: card
    inpaint: card
    fallback_card: true
    wizard_fingerprint_auto_confirm: true
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Literal, Optional

LOG = logging.getLogger("titan.image_pipeline")

TriggerMode = Literal["wizard", "card"]
WizardConfirmMode = Literal["missing_only", "always", "never"]
_OPS = ("generate", "regenerate", "upscale", "inpaint")


@dataclass
class ImagePipelineConfig:
  # wizard = LLM tool call; card = UI Generovat button
    generate: TriggerMode = "wizard"
    regenerate: TriggerMode = "card"
    upscale: TriggerMode = "card"
    inpaint: TriggerMode = "card"
    # wizard only: missing_only = run when prompt+style set; always = confirm step;
    # never = always run when params sufficient (no confirm prose).
    wizard_confirm: WizardConfirmMode = "missing_only"
    # When wizard stalls (LLM never calls confirm=true), offer UI card at end of turn.
    fallback_card: bool = False
    # Same-params retry after user said go auto-sets confirm (old _CONFIRM_WAITING).
    wizard_fingerprint_auto_confirm: bool = True
    # Step 10: LoRA multi-select on proposal card (requires sd-loras API).
    lora_ui_enabled: bool = False


_DEFAULT = ImagePipelineConfig()
_CACHE: Optional[ImagePipelineConfig] = None
_CACHE_MTIME: float = 0.0

_ENV_MAP = {
    "TITAN_IMAGE_TRIGGER_GENERATE": "generate",
    "TITAN_IMAGE_TRIGGER_REGENERATE": "regenerate",
    "TITAN_IMAGE_TRIGGER_UPSCALE": "upscale",
    "TITAN_IMAGE_TRIGGER_INPAINT": "inpaint",
    "TITAN_IMAGE_PIPELINE_FALLBACK_CARD": "fallback_card",
    "TITAN_IMAGE_PIPELINE_WIZARD_FINGERPRINT": "wizard_fingerprint_auto_confirm",
    "TITAN_IMAGE_PIPELINE_WIZARD_CONFIRM": "wizard_confirm",
    "TITAN_IMAGE_PIPELINE_LORA_UI": "lora_ui_enabled",
}


def _coerce_trigger(val: Any) -> Optional[TriggerMode]:
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("wizard", "card"):
        return s  # type: ignore[return-value]
    return None


def _coerce_wizard_confirm(val: Any) -> Optional[WizardConfirmMode]:
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("missing_only", "missing", "auto", "incomplete"):
        return "missing_only"
    if s in ("always", "confirm"):
        return "always"
    if s in ("never", "none", "off"):
        return "never"
    return None


def _coerce_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def _apply_dict(cfg: ImagePipelineConfig, data: Dict[str, Any]) -> ImagePipelineConfig:
    out = ImagePipelineConfig(**asdict(cfg))
    for op in _OPS:
        if op in data:
            t = _coerce_trigger(data[op])
            if t:
                setattr(out, op, t)
    if "fallback_card" in data:
        b = _coerce_bool(data["fallback_card"])
        if b is not None:
            out.fallback_card = b
    if "wizard_fingerprint_auto_confirm" in data:
        b = _coerce_bool(data["wizard_fingerprint_auto_confirm"])
        if b is not None:
            out.wizard_fingerprint_auto_confirm = b
    if "wizard_confirm" in data:
        wc = _coerce_wizard_confirm(data["wizard_confirm"])
        if wc:
            out.wizard_confirm = wc
    if "lora_ui_enabled" in data:
        b = _coerce_bool(data["lora_ui_enabled"])
        if b is not None:
            out.lora_ui_enabled = b
    return out


def _yaml_path() -> Path:
    explicit = os.environ.get("TITAN_IMAGE_PIPELINE_CONFIG", "").strip()
    if explicit:
        return Path(explicit)
    for candidate in (
        Path(os.environ.get("TITAN_MODELS_CONFIG", "/app/data/titan-models.yaml")).parent
        / "titan-image-pipeline.yaml",
        Path("/app/data/titan-image-pipeline.yaml"),
        Path("data/titan-image-pipeline.yaml"),
    ):
        if candidate.is_file():
            return candidate
    return Path("/app/data/titan-image-pipeline.yaml")


def _load_yaml() -> Dict[str, Any]:
    path = _yaml_path()
    if not path.is_file():
        return {}
    try:
        import yaml

        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        LOG.warning("Could not read %s: %s", path, exc)
        return {}


def _load_settings_dict() -> Dict[str, Any]:
    try:
        from src.settings import get_setting

        raw = get_setting("titan_image_pipeline", None)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _load_env_overrides() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for env_key, field_name in _ENV_MAP.items():
        if env_key not in os.environ:
            continue
        val = os.environ[env_key]
        if field_name in _OPS:
            t = _coerce_trigger(val)
            if t:
                out[field_name] = t
        elif field_name == "wizard_confirm":
            wc = _coerce_wizard_confirm(val)
            if wc:
                out["wizard_confirm"] = wc
        elif field_name in ("fallback_card", "wizard_fingerprint_auto_confirm", "lora_ui_enabled"):
            b = _coerce_bool(val)
            if b is not None:
                out[field_name] = b
        else:
            b = _coerce_bool(val)
            if b is not None:
                out[field_name] = b
    return out


def load_image_pipeline_config(*, force: bool = False) -> ImagePipelineConfig:
    """Return cached pipeline routing config."""
    global _CACHE, _CACHE_MTIME
    path = _yaml_path()
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if not force and _CACHE is not None and mtime == _CACHE_MTIME:
        return _CACHE

    cfg = ImagePipelineConfig()
    cfg = _apply_dict(cfg, _load_yaml())
    cfg = _apply_dict(cfg, _load_settings_dict())
    cfg = _apply_dict(cfg, _load_env_overrides())
    _CACHE = cfg
    _CACHE_MTIME = mtime
    return cfg


def invalidate_image_pipeline_config_cache() -> None:
    global _CACHE, _CACHE_MTIME
    _CACHE = None
    _CACHE_MTIME = 0.0


def trigger_for_op(op: Optional[str]) -> TriggerMode:
    cfg = load_image_pipeline_config()
    key = (op or "generate").strip().lower()
    if key not in _OPS:
        key = "generate"
    return getattr(cfg, key)


def should_use_card(op: Optional[str]) -> bool:
    return trigger_for_op(op) == "card"


def should_use_wizard(op: Optional[str]) -> bool:
    return trigger_for_op(op) == "wizard"


def fallback_card_enabled() -> bool:
    return load_image_pipeline_config().fallback_card


def wizard_fingerprint_auto_confirm() -> bool:
    return load_image_pipeline_config().wizard_fingerprint_auto_confirm


def wizard_confirm_mode() -> WizardConfirmMode:
    return load_image_pipeline_config().wizard_confirm


def lora_ui_enabled() -> bool:
    return load_image_pipeline_config().lora_ui_enabled


def should_auto_execute_wizard(*, prompt: str, style: str, op: str) -> bool:
    """True when wizard path should run immediately (no confirm step)."""
    from titan.style_labels import get_active_styles

    styles = get_active_styles()
    _ = op
    mode = wizard_confirm_mode()
    if mode == "never":
        return bool(prompt and style in styles)
    if mode == "always":
        return False
    if not prompt or style not in styles:
        return False
    return True


def config_as_dict() -> Dict[str, Any]:
    return asdict(load_image_pipeline_config())

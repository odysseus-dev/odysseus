"""Read SD launch profiles and chat_defaults from titan-models.yaml (Odysseus side)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG = Path(
    os.environ.get("TITAN_MODELS_CONFIG") or "/app/data/titan-models.yaml"
)


def load_models_config(path: Path | None = None) -> dict[str, Any]:
    p = path or _DEFAULT_CONFIG
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _sd_dir(cfg: dict[str, Any]) -> Path:
    """Resolve SD model directory (host path in yaml vs container bind-mount)."""
    for candidate in (
        os.environ.get("TITAN_SD_DIR", "").strip(),
        (cfg.get("downloads") or {}).get("sd_dir", ""),
    ):
        if candidate:
            p = Path(str(candidate))
            if p.is_dir():
                return p
    host_data = (
        os.environ.get("TITAN_HOST_DATA_DIR")
        or os.environ.get("ODYSSEUS_HOST_DATA_DIR", "")
    ).strip()
    if host_data:
        p = Path(host_data) / "sd-models"
        if p.is_dir():
            return p
    app = Path("/app/data/sd-models")
    if app.is_dir():
        return app
    return Path(str((cfg.get("downloads") or {}).get("sd_dir", "")))


def _sd_model_path(model: dict[str, Any], cfg: dict[str, Any]) -> Path:
    sd_dir = _sd_dir(cfg)
    rel = model.get("path", "")
    if rel and Path(rel).is_absolute():
        return Path(rel)
    if rel and sd_dir:
        return sd_dir / Path(rel).name
    return Path("")


def _krea_aux_ready(model: dict[str, Any], cfg: dict[str, Any]) -> bool:
    sd_dir = _sd_dir(cfg)
    if not sd_dir.is_dir():
        return False
    for key in ("llm_path", "vae_path"):
        rel = model.get(key)
        if not rel or not (sd_dir / str(rel)).is_file():
            return False
    return True


_STYLE_LABELS: dict[str, str] = {
    "realistic": "Realistic",
    "anime": "Anime",
    "pixelart": "Pixel art",
    "krea": "Krea",
}


def image_style_catalog(cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Installed SD launch profiles available for campaign image generation."""
    styles = sorted(active_sd_styles(cfg))
    return [
        {
            "id": style_id,
            "label": _STYLE_LABELS.get(style_id, style_id.replace("_", " ").title()),
        }
        for style_id in styles
    ]


def active_sd_styles(cfg: dict[str, Any] | None = None) -> frozenset[str]:
    """Styles whose checkpoint file exists and have a launch profile."""
    cfg = cfg or load_models_config()
    by_id = {m.get("id"): m for m in (cfg.get("models") or {}).get("sd") or [] if m.get("id")}
    styles: set[str] = set()
    for prof in (cfg.get("launch_profiles") or {}).get("sd") or []:
        pid = (prof.get("id") or "").strip()
        mid = prof.get("model_id")
        if not pid or not mid:
            continue
        model = by_id.get(mid)
        if not model:
            continue
        full = _sd_model_path(model, cfg)
        if not full.is_file():
            continue
        if (model.get("arch") or "").strip().lower() == "krea2" and not _krea_aux_ready(model, cfg):
            continue
        styles.add(pid)
    return frozenset(styles)


def chat_defaults_for_style(style: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_models_config()
    key = (style or "").strip().lower()
    for prof in (cfg.get("launch_profiles") or {}).get("sd") or []:
        if (prof.get("id") or "").strip().lower() == key:
            d = prof.get("chat_defaults")
            return dict(d) if isinstance(d, dict) else {}
    return {}


@lru_cache(maxsize=1)
def cached_active_sd_styles() -> frozenset[str]:
    return active_sd_styles()


def invalidate_sd_config_cache() -> None:
    cached_active_sd_styles.cache_clear()

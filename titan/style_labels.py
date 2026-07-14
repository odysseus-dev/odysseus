"""Canonical Titan SD style → display labels (single source of truth)."""

from __future__ import annotations

import re

STYLE_LABELS: dict[str, str] = {
    "realistic": "ThisIsReal SDXL v3.0",
    "anime": "Nova Anime XL IL v19",
    "pixelart": "Pixel Storm XL v1.0",
    "krea": "KREA (Dark Beast)",
}

STYLE_LABELS_LONG: dict[str, str] = {
    "realistic": "ThisIsReal SDXL v3.0 (photoreal)",
    "anime": "Nova Anime XL IL v19 (anime/illustration)",
    "pixelart": "Pixel Storm XL v1.0 (pixel art)",
    "krea": "KREA / Dark Beast KREA 2 (KREA2 — prose prompts, photoreal or anime)",
}

# Legacy alias → canonical style id
STYLE_ALIASES: dict[str, str] = {
    "hyperrealistic": "krea",
    "hyper-realistic": "krea",
    "dark beast": "krea",
    "dark beast krea": "krea",
}


def canonical_style(style: str | None) -> str:
    key = (style or "").strip().lower()
    return STYLE_ALIASES.get(key, key)

# Styles with checkpoints present on disk (from titan-models.yaml launch_profiles.sd)
def get_active_styles() -> frozenset[str]:
    try:
        from titan.hub_sd_config import cached_active_sd_styles

        styles = cached_active_sd_styles()
        if styles:
            return styles
    except Exception:
        pass
    return frozenset({"realistic", "anime", "pixelart"})


# Back-compat for imports; refreshed when Odysseus restarts or hub saves yaml.
ACTIVE_STYLES = get_active_styles()

_LEGACY_REALVIS = re.compile(r"realvis\s*xl?", re.I)


def style_display_name(style: str | None, *, long: bool = False) -> str:
    key = (style or "").strip().lower()
    table = STYLE_LABELS_LONG if long else STYLE_LABELS
    return table.get(key, style or "")


def normalize_model_label(model: str | None, style: str | None = None) -> str:
    """Map tool/chat image_model strings to current display names."""
    raw = (model or "").strip()
    if _LEGACY_REALVIS.search(raw):
        return STYLE_LABELS["realistic"]
    key = canonical_style(style or raw)
    if key in STYLE_LABELS:
        return STYLE_LABELS[key]
    m = re.match(r"^(realistic|anime|pixelart|krea)\b", raw, re.I)
    if m:
        return STYLE_LABELS[m.group(1).lower()]
    m = re.search(r"style:\s*(realistic|anime|pixelart|krea)\b", raw, re.I)
    if m:
        return STYLE_LABELS[m.group(1).lower()]
    if raw.lower().startswith("style:"):
        rest = raw[6:].strip()
        m = re.match(r"^(realistic|anime|pixelart|krea)\b", rest, re.I)
        if m:
            return STYLE_LABELS[m.group(1).lower()]
    return raw.split("/")[-1] if raw else "image"

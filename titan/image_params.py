"""Normalize and infer Stable Diffusion / generate_image parameters."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_STYLES = frozenset({"realistic", "anime", "pixelart", "krea"})
_QUALITIES = frozenset({"low", "medium", "high", "auto"})
_ASPECTS = frozenset({"square", "portrait", "landscape"})

_STYLE_HINTS = (
    (("photoreal", "photo-real", "photo real"), "realistic"),
    (("realistic", "realism"), "realistic"),
    (("pixel art", "pixelart", "8-bit", "8bit", "16-bit", "16bit", "retro game"), "pixelart"),
    (("hyper-realistic", "hyperrealistic", "hyper realistic", "dark beast", "dark beast krea", "krea 2", "krea"), "krea"),
    (("anime", "manga"), "anime"),
)
_QUALITY_HINTS = (
    (("high quality", "high qual"), "high"),
    (("medium quality",), "medium"),
    (("low quality", "draft quality"), "low"),
)
_SIZE_RE = re.compile(r"\b(\d{3,4})x(\d{3,4})\b", re.I)
_CN_OFF_RE = re.compile(
    r"\b(?:bez|without|no|not|disable|vypni|ne)\s+(?:control[\s-]?net(?:u|em)?|controlnet)\b",
    re.I,
)
_CN_ON_RE = re.compile(
    r"(?:\b(?:with|use|using|použij|zapni|s)\s+(?:control[\s-]?net(?:em|u)?|controlnet)\b"
    r"|\bcontrol[\s-]?net(?:em|u)?\b|\bcontrolnet\b)",
    re.I,
)


def infer_control_net_from_text(text: str) -> Optional[bool]:
    """Detect explicit ControlNet on/off in the user's chat message."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if _CN_OFF_RE.search(raw):
        return False
    if _CN_ON_RE.search(raw):
        return True
    return None


def normalize_style(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _STYLES:
        return text
    from titan.style_labels import STYLE_ALIASES

    if text in STYLE_ALIASES:
        return STYLE_ALIASES[text]
    for hints, canonical in _STYLE_HINTS:
        if any(h in text for h in hints):
            return canonical
    return None


def normalize_quality(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in _QUALITIES:
        return text
    for hints, canonical in _QUALITY_HINTS:
        if any(h in text for h in hints):
            return canonical
    return None


def normalize_aspect(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in _ASPECTS:
        return text
    if text in ("vertical",):
        return "portrait"
    if text in ("horizontal",):
        return "landscape"
    return None


def infer_from_text(text: str) -> Dict[str, str]:
    """Pull size/style/quality hints out of free-form text (English tool args)."""
    found: Dict[str, str] = {}
    if not text:
        return found
    m = _SIZE_RE.search(text)
    if m:
        found["size"] = f"{m.group(1)}x{m.group(2)}"
    style = normalize_style(text)
    if style:
        found["style"] = style
    quality = normalize_quality(text)
    if quality:
        found["quality"] = quality
    aspect = normalize_aspect(text)
    if aspect:
        found["aspect"] = aspect
    return found


def normalize_image_args(arguments: Dict[str, Any], *, source_text: str = "") -> Dict[str, Any]:
    """Canonicalize generate_image arguments before calling the MCP server."""
    args = dict(arguments or {})
    merged_source = " ".join(
        str(args.get(k) or "") for k in ("prompt", "style", "quality", "size")
    )
    if source_text:
        merged_source = f"{source_text} {merged_source}"

    inferred = infer_from_text(merged_source)
    for key in ("size", "quality", "aspect"):
        if not args.get(key) and inferred.get(key):
            args[key] = inferred[key]

    if args.get("control_net") is None:
        cn = infer_control_net_from_text(merged_source)
        if cn is not None:
            args["control_net"] = cn

    style = normalize_style(args.get("style"))
    if not style:
        style = inferred.get("style")
    if style:
        args["style"] = style
    elif args.get("style"):
        args.pop("style", None)

    quality = normalize_quality(args.get("quality"))
    if quality:
        args["quality"] = quality
    elif args.get("quality"):
        args.pop("quality", None)

    aspect = normalize_aspect(args.get("aspect"))
    if aspect:
        args["aspect"] = aspect

    if "confirm" in args:
        from titan.image_parse import coerce_confirm
        coerced = coerce_confirm(args.get("confirm"))
        if coerced is not None:
            args["confirm"] = coerced
        else:
            args.pop("confirm", None)

    # Internal routing keys — never forward to the scheduler / SD API.
    for _internal in ("_odysseus_owner", "_odysseus_session_id", "_odysseus_user_text"):
        args.pop(_internal, None)

    return args

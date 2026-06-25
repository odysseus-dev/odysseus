"""ComfyUI/RunComfy media generation helpers.

Odysseus routes simple image fallback through free local ComfyUI when it is
available. Paid RunComfy Cloud remains available as an explicit integration
option. Both paths save generated artifacts into the local generated-media
folder and return fields the chat renderer can display inline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

import httpx

from src.constants import BASE_DIR, DATA_DIR, GENERATED_IMAGES_DIR


IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "m4v"}
AUDIO_EXTS = {"mp3", "wav", "ogg", "m4a", "flac", "aac", "webm"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS

DEFAULT_MODELS = {
    "image": "blackforestlabs/flux-2-klein/9b/text-to-image",
    "video": "kling/kling-3.0/standard/text-to-video",
    "music": "acestep-ai/ace-step-1.5/text-to-audio",
}

LOCAL_COMFY_PRESETS = {"comfyui", "comfy", "comfyui_local", "comfyui-local", "local_comfyui", "local-comfyui"}
RUNCOMFY_PRESETS = {"runcomfy_cloud", "runcomfy-cloud", "runcomfy", "run_comfy"}
LOCAL_COMFY_PROVIDER_ALIASES = {
    "comfy",
    "comfyui",
    "comfy_ui",
    "comfyui_local",
    "comfyui-local",
    "local",
    "local_comfy",
    "local_comfyui",
    "local-comfyui",
    "free",
}
RUNCOMFY_PROVIDER_ALIASES = {
    "runcomfy",
    "run_comfy",
    "runcomfy_cloud",
    "runcomfy-cloud",
    "comfyui_cloud",
    "comfyui-cloud",
    "cloud",
    "paid",
}
GEMINI_VIDEO_PROVIDER_ALIASES = {
    "gemini",
    "google",
    "google_gemini",
    "google-gemini",
    "veo",
}
GEMINI_VIDEO_DEFAULT_MODEL = "veo-3.1-generate-preview"
GEMINI_VIDEO_MODELS = (
    "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-preview",
    "veo-3.1-lite-generate-preview",
    "veo-2.0-generate-001",
)
GEMINI_VIDEO_MODEL_ALIASES = {
    "veo": GEMINI_VIDEO_DEFAULT_MODEL,
    "veo-3": GEMINI_VIDEO_DEFAULT_MODEL,
    "veo-3.1": GEMINI_VIDEO_DEFAULT_MODEL,
    "veo-3.1-pro": GEMINI_VIDEO_DEFAULT_MODEL,
    "veo-fast": "veo-3.1-fast-generate-preview",
    "veo-3.1-fast": "veo-3.1-fast-generate-preview",
    "veo-lite": "veo-3.1-lite-generate-preview",
    "veo-3.1-lite": "veo-3.1-lite-generate-preview",
    "veo-2": "veo-2.0-generate-001",
    "veo-2.0": "veo-2.0-generate-001",
}
RUNCOMFY_MODEL_PREFIXES = (
    "blackforestlabs/",
    "kling/",
    "acestep-ai/",
    "elevenlabs/",
    "wan-ai/",
    "happyhorse/",
    "openai/gpt-image",
)

LOCAL_COMFY_HOSTS = {"127.0.0.1", "localhost", "::1"}
COMFYUI_REPO_URL = "https://github.com/comfyanonymous/ComfyUI.git"
COMFYUI_ZIP_URL = "https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.zip"
COMFYUI_DEFAULT_MODEL_URL = "https://huggingface.co/Lykon/DreamShaper/resolve/main/DreamShaper_8_pruned.safetensors"
COMFYUI_DEFAULT_MODEL_NAME = "DreamShaper_8_pruned.safetensors"

_COMFYUI_AUTOSTART_PROCESS: Optional[subprocess.Popen] = None
_COMFYUI_AUTOSTART_LAST_ATTEMPT = 0.0
_COMFYUI_AUTOSTART_LAST_MESSAGE = ""
_COMFYUI_ACCELERATOR_CACHE: Optional[str] = None
_COMFYUI_BOOTSTRAP_LAST_MESSAGE = ""

MODEL_ALIASES = {
    "happyhorse/happyhorse-1-0/text-to-video": "happyhorse/happyhorse-1.0/text-to-video",
    "happyhorse/happyhorse-1-0/image-to-video": "happyhorse/happyhorse-1.0/image-to-video",
    "wan-ai/wan-2-7/text-to-video": "wan-ai/wan-2.7/text-to-video",
}

PROFESSIONAL_IMAGE_SUFFIX = (
    "professional art direction, strong composition, controlled lighting, "
    "premium color grade, high-detail materials, production-ready finish"
)
PROFESSIONAL_VIDEO_SUFFIX = (
    "Clear subject motion, intentional camera direction, stable framing, "
    "cinematic lighting, polished edit rhythm, professional color grade."
)
PROFESSIONAL_AUDIO_TAGS = (
    "professionally arranged, professionally mixed and mastered, clear stereo image, "
    "balanced dynamics, polished production"
)

PREMIUM_WORDS = {
    "premium", "commercial", "campaign", "brand", "hero", "broadcast",
    "final", "polished", "professional", "studio", "advertising",
}
TYPOGRAPHY_WORDS = {"poster", "headline", "logo", "typography", "text", "sign", "ad", "label", "banner"}
PHOTO_WORDS = {"photo", "photoreal", "portrait", "product", "fashion", "lifestyle", "food", "interior", "realistic"}
LOOP_WORDS = {"loop", "seamless", "game loop", "background loop"}


def _parse_args(content: str, kind: str = "") -> Dict[str, Any]:
    raw = (content or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {"prompt": raw}
    lines = raw.splitlines()
    args: Dict[str, Any] = {"prompt": lines[0].strip() if lines else ""}
    if len(lines) > 1 and lines[1].strip():
        args["model"] = lines[1].strip()
    if kind == "image":
        if len(lines) > 2 and lines[2].strip():
            args["size"] = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            args["quality"] = lines[3].strip()
    elif kind == "music":
        if len(lines) > 2 and lines[2].strip():
            args["duration"] = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            args["lyrics"] = "\n".join(lines[3:]).strip()
    elif len(lines) > 2 and lines[2].strip():
        args["duration"] = lines[2].strip()
    if kind not in {"image", "music"} and len(lines) > 3 and lines[3].strip():
        args["aspect_ratio"] = lines[3].strip()
    return args


def wants_runcomfy_media(content: str) -> bool:
    args = _parse_args(content)
    provider = str(args.get("provider") or args.get("backend") or args.get("service") or "").lower()
    model = str(args.get("model_id") or args.get("model") or "").lower()
    text = str(content or "").lower()
    if _normalize_provider_name(provider) in (LOCAL_COMFY_PROVIDER_ALIASES | RUNCOMFY_PROVIDER_ALIASES):
        return True
    if any(phrase in text for phrase in (
        "runcomfy",
        "run comfy",
        "comfyui",
        "comfy ui",
        "comfy cloud",
        "local comfy",
    )):
        return True
    return any(model.startswith(prefix) for prefix in RUNCOMFY_MODEL_PREFIXES)


def _normalize_provider_name(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_")
    return re.sub(r"[^a-z0-9_/-]+", "", raw)


def _compact_provider_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _integration_kind(integration: Dict[str, Any]) -> str:
    fields = [
        integration.get("preset"),
        integration.get("provider"),
        integration.get("service"),
        integration.get("name"),
        integration.get("id"),
    ]
    normalized = {_normalize_provider_name(item) for item in fields if item}
    compacted = {_compact_provider_name(item) for item in fields if item}
    if normalized & LOCAL_COMFY_PRESETS or compacted & {"comfyuilocal", "localcomfyui"}:
        return "comfyui_local"
    if normalized & RUNCOMFY_PRESETS or compacted & {"runcomfycloud", "runcomfy"}:
        return "runcomfy"
    return ""


def _load_media_integrations() -> list[Dict[str, Any]]:
    try:
        from src.integrations import load_integrations

        return [
            item for item in load_integrations()
            if isinstance(item, dict) and item.get("enabled", True)
        ]
    except Exception:
        return []


def _find_media_integration(args: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    identifier = str(
        args.get("integration")
        or args.get("integration_id")
        or args.get("provider_id")
        or ""
    ).strip()
    integrations = _load_media_integrations()
    if identifier:
        ident_lower = identifier.lower()
        for item in integrations:
            if str(item.get("id", "")).lower() == ident_lower or str(item.get("name", "")).lower() == ident_lower:
                return item if _integration_kind(item) == kind else None
        return None

    for item in integrations:
        if _integration_kind(item) == kind:
            return item
    return None


def _comfyui_integration(args: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    return _find_media_integration(args or {}, "comfyui_local")


def _runcomfy_integration(args: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    return _find_media_integration(args or {}, "runcomfy")


def _requested_provider(args: Dict[str, Any], content: str = "") -> str:
    raw_provider = _normalize_provider_name(
        args.get("provider") or args.get("backend") or args.get("service") or ""
    )
    if raw_provider in RUNCOMFY_PROVIDER_ALIASES:
        return "runcomfy"
    if raw_provider in LOCAL_COMFY_PROVIDER_ALIASES:
        return "comfyui_local"
    if raw_provider in GEMINI_VIDEO_PROVIDER_ALIASES:
        return "gemini_video"

    if args.get("workflow") or args.get("comfyui_workflow"):
        return "comfyui_local"

    model = str(args.get("model_id") or args.get("model") or "").strip().lower()
    if any(model.startswith(prefix) for prefix in RUNCOMFY_MODEL_PREFIXES):
        return "runcomfy"

    text = str(content or "").lower()
    if "runcomfy" in text or "run comfy" in text or "comfy cloud" in text:
        return "runcomfy"
    if "local comfy" in text or "comfyui" in text or "comfy ui" in text:
        return "comfyui_local"
    return ""


def _requested_provider_from_integration(args: Dict[str, Any]) -> str:
    if not (args.get("integration") or args.get("integration_id") or args.get("provider_id")):
        return ""
    if _runcomfy_integration(args):
        return "runcomfy"
    if _comfyui_integration(args):
        return "comfyui_local"
    return ""


def runcomfy_fallback_content(kind: str, content: str) -> str:
    """Drop local/provider-specific model hints before falling back to RunComfy."""
    if wants_runcomfy_media(content):
        return content
    args = _parse_args(content, kind=kind)
    if not args:
        return content
    keep_by_kind = {
        "image": {
            "prompt", "description", "size", "aspect_ratio", "quality",
            "style", "enhance_prompt", "raw_prompt", "seed", "steps",
            "width", "height",
        },
        "video": {
            "prompt", "description", "duration", "aspect_ratio", "quality",
            "camera", "camera_motion", "image_url", "image", "audio_url",
            "seed", "negative_prompt", "enhance_prompt", "raw_prompt", "timeout",
        },
        "music": {
            "prompt", "description", "tags", "lyrics", "duration", "seconds",
            "quality", "bpm", "mood", "loop", "force_instrumental", "audio",
            "start_time", "end_time", "extend_before_duration",
            "extend_after_duration", "enhance_prompt", "raw_prompt", "timeout",
        },
    }
    allowed = keep_by_kind.get(kind, {"prompt", "description", "quality"})
    cleaned = {key: value for key, value in args.items() if key in allowed and value not in (None, "")}
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else content


def _model_ids_from_endpoint_fields(ep: object, *field_names: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for field_name in field_names:
        raw = getattr(ep, field_name, None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = raw
        if isinstance(parsed, str):
            values = [part.strip() for part in parsed.replace("\n", ",").split(",") if part.strip()]
        elif isinstance(parsed, (list, tuple, set)):
            values = [str(item).strip() for item in parsed if str(item or "").strip()]
        else:
            values = []
        for value in values:
            key = value.lower()
            if key not in seen:
                seen.add(key)
                out.append(value)
    return out


def _is_gemini_media_endpoint(base_or_url: str, ep: object | None = None) -> bool:
    text = f"{base_or_url or ''} {getattr(ep, 'name', '') if ep else ''}".lower()
    try:
        host = (urlparse(base_or_url or "").hostname or "").lower()
    except Exception:
        host = ""
    return "generativelanguage.googleapis.com" in host or "gemini" in text or "google" in text


def _is_gemini_video_model(model_id: str) -> bool:
    model = str(model_id or "").strip().lower()
    return model in GEMINI_VIDEO_MODEL_ALIASES or model.startswith("veo-") or model.startswith("models/veo-")


def _canonical_gemini_video_model(model_id: str) -> str:
    raw = str(model_id or "").strip()
    lowered = raw.lower()
    if lowered.startswith("models/"):
        lowered = lowered.split("/", 1)[1]
        raw = raw.split("/", 1)[1]
    return GEMINI_VIDEO_MODEL_ALIASES.get(lowered, raw)


def _gemini_openai_base_from_url(base_or_url: str) -> str:
    parsed = urlparse(base_or_url or "")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "generativelanguage.googleapis.com"
    version = "v1beta"
    for part in [part for part in (parsed.path or "").split("/") if part]:
        if re.fullmatch(r"v\d+(?:beta)?", part):
            version = part
            break
    return f"{scheme}://{netloc}/{version}/openai"


def _gemini_native_base_from_url(base_or_url: str) -> str:
    parsed = urlparse(base_or_url or "")
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "generativelanguage.googleapis.com"
    version = "v1beta"
    for part in [part for part in (parsed.path or "").split("/") if part]:
        if re.fullmatch(r"v\d+(?:beta)?", part):
            version = part
            break
    return f"{scheme}://{netloc}/{version}"


def _extract_api_key_from_headers(headers: Dict[str, str]) -> str:
    auth = str((headers or {}).get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(
        (headers or {}).get("x-goog-api-key")
        or (headers or {}).get("X-Goog-Api-Key")
        or ""
    ).strip()


def _gemini_video_endpoint_config(owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        from src.auth_helpers import owner_filter
        from src.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import build_headers, resolve_endpoint_runtime
    except Exception:
        return None

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        for ep in query.all():
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            if not _is_gemini_media_endpoint(base, ep):
                continue
            headers = build_headers(api_key, base)
            if not _extract_api_key_from_headers(headers):
                continue
            return {
                "base_url": _gemini_openai_base_from_url(base),
                "native_base_url": _gemini_native_base_from_url(base),
                "headers": headers,
                "models": _model_ids_from_endpoint_fields(ep, "cached_models", "pinned_models"),
                "endpoint_name": getattr(ep, "name", "Gemini"),
            }
    finally:
        db.close()
    return None


def _select_gemini_video_model(args: Dict[str, Any], models: Optional[list[str]] = None) -> str:
    requested = str(args.get("model") or args.get("model_id") or "").strip()
    if requested and _is_gemini_video_model(requested):
        return _canonical_gemini_video_model(requested)
    available = [_canonical_gemini_video_model(model) for model in (models or []) if _is_gemini_video_model(model)]
    available_lower = {model.lower(): model for model in available}
    for preferred in GEMINI_VIDEO_MODELS:
        if preferred.lower() in available_lower:
            return available_lower[preferred.lower()]
    for model in available:
        if _is_gemini_video_model(model):
            return model
    return GEMINI_VIDEO_DEFAULT_MODEL


def _gemini_video_aspect_ratio(args: Dict[str, Any]) -> str:
    ratio = _normalize_aspect_ratio(args.get("aspect_ratio"))
    return "9:16" if ratio == "9:16" else "16:9"


def _gemini_video_duration_seconds(args: Dict[str, Any]) -> Optional[int]:
    raw = args.get("duration") or args.get("duration_seconds") or args.get("seconds")
    if raw is None or str(raw).strip() == "":
        return None
    duration = _coerce_int(raw, 8, 4, 8)
    if duration <= 4:
        return 4
    if duration <= 6:
        return 6
    return 8


def _gemini_video_extra_body(args: Dict[str, Any]) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    ratio = _gemini_video_aspect_ratio(args)
    if args.get("aspect_ratio"):
        extra["aspect_ratio"] = ratio
    duration = _gemini_video_duration_seconds(args)
    if duration:
        extra["duration_seconds"] = duration
    resolution = str(args.get("resolution") or "").strip().lower()
    if resolution in {"720p", "1080p", "4k"}:
        extra["resolution"] = resolution
        if resolution in {"1080p", "4k"}:
            extra["duration_seconds"] = 8
    for key in ("negative_prompt", "seed", "style", "person_generation"):
        if args.get(key) not in (None, ""):
            extra[key] = args[key]
    return extra


def _gemini_video_parameters(args: Dict[str, Any]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if args.get("aspect_ratio"):
        params["aspectRatio"] = _gemini_video_aspect_ratio(args)
    duration = _gemini_video_duration_seconds(args)
    if duration:
        params["durationSeconds"] = duration
    resolution = str(args.get("resolution") or "").strip().lower()
    if resolution in {"720p", "1080p", "4k"}:
        params["resolution"] = resolution
        if resolution in {"1080p", "4k"}:
            params["durationSeconds"] = 8
    if args.get("negative_prompt") not in (None, ""):
        params["negativePrompt"] = str(args["negative_prompt"])
    if args.get("person_generation") not in (None, ""):
        params["personGeneration"] = args["person_generation"]
    if args.get("seed") not in (None, ""):
        params["seed"] = _coerce_int(args.get("seed"), 0, 0, 2**31 - 1)
    return params


def _normalize_gemini_video_download_url(video_url: str, native_base_url: str) -> str:
    parsed = urlparse(video_url or "")
    if (parsed.hostname or "").lower() != "generativelanguage.googleapis.com":
        return video_url
    parts = [part for part in (parsed.path or "").split("/") if part]
    if len(parts) < 2 or parts[1] != "files":
        return video_url

    native_parts = [part for part in (urlparse(native_base_url or "").path or "").split("/") if part]
    native_version = next((part for part in native_parts if re.fullmatch(r"v\d+(?:beta)?", part)), "v1beta")
    if parts[0] == native_version:
        return video_url
    parts[0] = native_version
    return urlunparse(parsed._replace(path="/" + "/".join(parts)))


def _find_video_url(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("url", "download_url", "video_url", "uri"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_video_url(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_video_url(item)
            if found:
                return found
    return ""


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        iv = default
    return max(minimum, min(maximum, iv))


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        fv = float(value)
    except (TypeError, ValueError):
        fv = default
    return max(minimum, min(maximum, fv))


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _quality(args: Dict[str, Any]) -> str:
    return str(args.get("quality") or args.get("tier") or "professional").strip().lower()


def _contains_any(text: str, words: set[str]) -> bool:
    haystack = str(text or "").lower()
    for word in words:
        needle = str(word or "").lower()
        if not needle:
            continue
        if len(needle) <= 3:
            if re.search(rf"\b{re.escape(needle)}\b", haystack):
                return True
        elif needle in haystack:
            return True
    return False


def _append_unique(text: str, additions: list[str], *, max_chars: int = 1800) -> str:
    base = str(text or "").strip()
    lower = base.lower()
    clean_additions = []
    for item in additions:
        item = str(item or "").strip().strip(".")
        if item and item.lower() not in lower:
            clean_additions.append(item)
    if clean_additions:
        base = base.rstrip(" .") + ". " + ", ".join(clean_additions) + "."
    return base[:max_chars].rstrip()


def _enhance_prompt_enabled(args: Dict[str, Any]) -> bool:
    return _coerce_bool(args.get("enhance_prompt"), True) and not _coerce_bool(args.get("raw_prompt"), False)


def _split_size(size: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    raw = str(size or "").lower().replace("_", "x")
    if "x" not in raw:
        return default
    left, right = raw.split("x", 1)
    return (
        _coerce_int(left, default[0], 256, 2048),
        _coerce_int(right, default[1], 256, 2048),
    )


def _size_from_aspect(aspect_ratio: str, default: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
    ratio = str(aspect_ratio or "").strip()
    if ratio in {"16:9", "1.78", "1.777"}:
        return 1536, 864
    if ratio in {"9:16", "0.56", "0.5625"}:
        return 864, 1536
    if ratio in {"3:2", "1.5"}:
        return 1536, 1024
    if ratio in {"2:3", "0.67", "0.666"}:
        return 1024, 1536
    if ratio in {"21:9", "2.33", "2.333"}:
        return 1536, 658
    return default


def _gpt_image_size(size: str) -> str:
    raw = str(size or "").lower().replace("x", "_")
    if raw in {"1024_1024", "1024_1536", "1536_1024"}:
        return raw
    if raw in {"1024_1792", "1792_1024"}:
        return "1024_1536" if raw.startswith("1024") else "1536_1024"
    return "1024_1024"


def _normalize_aspect_ratio(value: Any) -> str:
    raw = str(value or "16:9").strip().lower()
    aliases = {
        "landscape": "16:9",
        "wide": "16:9",
        "horizontal": "16:9",
        "portrait": "9:16",
        "vertical": "9:16",
        "square": "1:1",
        "cinema": "21:9",
        "cinematic": "21:9",
        "anamorphic": "21:9",
    }
    return aliases.get(raw, raw if raw in {"16:9", "9:16", "1:1", "21:9", "4:5", "3:2", "2:3"} else "16:9")


def _normalize_resolution(value: Any, quality: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"720p", "1080p", "4k"}:
        return raw
    if quality in {"draft", "low", "fast", "cheap"}:
        return "720p"
    return "1080p"


def _normalize_model_id(model_id: str) -> str:
    raw = str(model_id or "").strip()
    return MODEL_ALIASES.get(raw, raw)


def _is_kling_3_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("kling/kling-3.0/")


def _is_happyhorse_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("happyhorse/happyhorse-1.0/")


def _is_wan_27_model(model_id: str) -> bool:
    return _normalize_model_id(model_id).startswith("wan-ai/wan-2.7/")


def _kling_aspect_ratio(value: Any) -> str:
    ratio = _normalize_aspect_ratio(value)
    if ratio in {"16:9", "9:16", "1:1"}:
        return ratio
    if ratio in {"4:5", "3:4", "2:3"}:
        return "9:16"
    return "16:9"


def _wan_or_happyhorse_aspect_ratio(value: Any) -> str:
    raw = str(value or "16:9").strip().lower()
    aliases = {
        "landscape": "16:9",
        "wide": "16:9",
        "horizontal": "16:9",
        "portrait": "9:16",
        "vertical": "9:16",
        "square": "1:1",
    }
    ratio = aliases.get(raw, raw)
    return ratio if ratio in {"16:9", "9:16", "1:1", "4:3", "3:4"} else "16:9"


def _wants_video_sound(prompt: str, args: Dict[str, Any]) -> bool:
    for key in ("sound", "generate_audio", "with_audio"):
        if args.get(key) is not None:
            return _coerce_bool(args.get(key), False)
    lower = str(prompt or "").lower()
    if any(phrase in lower for phrase in ("no sound", "no audio", "silent", "without audio", "without sound")):
        return False
    return any(
        phrase in lower
        for phrase in (
            "with sound",
            "with audio",
            "dialogue",
            "voiceover",
            "voice over",
            "speaking",
            "talking",
            "ambient sound",
            "foley",
            "lip sync",
            "lipsync",
        )
    )


def _runcomfy_executable() -> Optional[str]:
    return shutil.which("runcomfy") or shutil.which("runcomfy.cmd")


def _subprocess_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    launcher = str(cmd[0]).lower()
    if launcher.endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", subprocess.list2cmdline(cmd)]
    return cmd


def _runcomfy_env(integration: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    env = dict(os.environ)
    token = str((integration or {}).get("api_key") or "").strip()
    if token and "****" not in token:
        env["RUNCOMFY_TOKEN"] = token
    return env


def _run_checked(
    cmd: list[str],
    timeout: int,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        _subprocess_command(cmd),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _auth_error_message(output: str = "") -> str:
    extra = (output or "").strip()
    if extra:
        extra = "\n\nRunComfy said:\n" + extra[:800]
    return (
        "RunComfy is installed, but it is not signed in. Create/sign in to a "
        "RunComfy account, then run `runcomfy login` once, or set "
        "`RUNCOMFY_TOKEN` from your RunComfy profile. Restart Odysseus after "
        "setting a token so the app process can see it."
        + extra
    )


def _check_runcomfy_ready(
    exe: str,
    integration: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        proc = _run_checked([exe, "whoami"], timeout=20, env=_runcomfy_env(integration))
    except FileNotFoundError:
        return {"error": "RunComfy CLI is not installed or not on PATH.", "exit_code": 1}
    except subprocess.TimeoutExpired:
        return {"error": "RunComfy CLI did not answer `whoami` within 20s.", "exit_code": 1}
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
    if proc.returncode == 0:
        return None
    return {"error": _auth_error_message(output), "exit_code": proc.returncode or 1}


def _select_model(kind: str, args: Dict[str, Any], default_model: Optional[str]) -> str:
    explicit = str(args.get("model_id") or args.get("model") or "").strip()
    if explicit:
        return _normalize_model_id(explicit)
    if default_model:
        return _normalize_model_id(default_model)

    promptish = " ".join(str(args.get(key) or "") for key in ("prompt", "tags", "lyrics", "description"))
    quality = _quality(args)

    if kind == "image":
        if _contains_any(promptish, TYPOGRAPHY_WORDS):
            return "openai/gpt-image-2/text-to-image"
        return DEFAULT_MODELS["image"]

    if kind == "video":
        has_image = bool(args.get("image_url") or args.get("image"))
        has_audio = bool(args.get("audio_url"))
        if has_audio and not has_image:
            return "wan-ai/wan-2.7/text-to-video"
        if has_image:
            if quality in {"4k"} or "4k" in promptish.lower():
                return "kling/kling-3.0/4k/image-to-video"
            if quality in {"premium", "hero"} or "physics" in promptish.lower() or "product spin" in promptish.lower():
                return "kling/kling-3.0/pro/image-to-video"
            return "kling/kling-3.0/standard/image-to-video"
        if quality in {"4k"} or "4k" in promptish.lower():
            return "kling/kling-3.0/4k/text-to-video"
        if quality in {"premium", "hero"}:
            return "kling/kling-3.0/pro/text-to-video"
        return DEFAULT_MODELS["video"]

    if kind == "music":
        if args.get("audio"):
            if args.get("extend_before_duration") is not None or args.get("extend_after_duration") is not None:
                return "acestep-ai/ace-step/audio-outpaint"
            return "acestep-ai/ace-step/audio-inpaint"
        if quality in {"premium", "commercial", "hero", "broadcast"}:
            return "elevenlabs/elevenlabs/music-generation"
        if quality in {"draft", "low", "cheap", "cost", "cost-sensitive", "batch"}:
            return "acestep-ai/ace-step/text-to-audio"
        return DEFAULT_MODELS["music"]

    return DEFAULT_MODELS.get(kind, "")


def _professional_image_prompt(prompt: str, args: Dict[str, Any], model_id: str) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions: list[str] = []
    style = str(args.get("style") or "").strip()
    if style:
        additions.append(style)
    if _contains_any(prompt, TYPOGRAPHY_WORDS) or "gpt-image" in model_id:
        additions.extend([
            "clean professional layout",
            "precise readable typography when text is requested",
            "balanced whitespace",
            "brand-ready composition",
            "print-quality finish",
        ])
    elif _contains_any(prompt, PHOTO_WORDS):
        additions.extend([
            "editorial photography",
            "controlled studio lighting",
            "natural textures",
            "sharp subject separation",
            "premium color grade",
        ])
    else:
        additions.append(PROFESSIONAL_IMAGE_SUFFIX)
    return _append_unique(prompt, additions)


def _professional_video_prompt(prompt: str, args: Dict[str, Any], model_id: str) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions = []
    camera = str(args.get("camera") or args.get("camera_motion") or "").strip()
    if camera:
        additions.append(f"Camera: {camera}")
    if "Audio:" not in prompt and "audio:" not in prompt and "happyhorse" in model_id:
        additions.append("Audio: subtle, professionally mixed ambience appropriate to the scene")
    additions.append(PROFESSIONAL_VIDEO_SUFFIX)
    return _append_unique(prompt, additions, max_chars=2200)


def _professional_audio_tags(tags: str, args: Dict[str, Any]) -> str:
    if not _enhance_prompt_enabled(args):
        return tags
    additions = [PROFESSIONAL_AUDIO_TAGS]
    if _contains_any(tags, LOOP_WORDS) or _coerce_bool(args.get("loop"), False):
        additions.append("seamless loop, consistent groove, clean loop point")
    if args.get("bpm"):
        additions.append(f"{args.get('bpm')} BPM")
    if args.get("mood"):
        additions.append(str(args["mood"]))
    return _append_unique(tags, additions, max_chars=1200)


def _professional_elevenlabs_prompt(prompt: str, args: Dict[str, Any]) -> str:
    if not _enhance_prompt_enabled(args):
        return prompt
    additions = [
        "premium commercial-ready production",
        "44.1 kHz stereo feel",
        "professional arrangement",
        "polished mix and master",
        "clear section structure",
    ]
    if _coerce_bool(args.get("force_instrumental"), False):
        additions.append("instrumental only, no vocals")
    if _contains_any(prompt, LOOP_WORDS) or _coerce_bool(args.get("loop"), False):
        additions.append("seamless loopable ending")
    return _append_unique(prompt, additions, max_chars=2200)


def _input_for_kind(kind: str, args: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    explicit = args.get("input") or args.get("body")
    if isinstance(explicit, dict):
        return dict(explicit)

    prompt = str(args.get("prompt") or args.get("description") or "").strip()
    if kind == "image":
        if not prompt:
            return {}
        prompt = _professional_image_prompt(prompt, args, model_id)
        if "gpt-image-2" in model_id:
            return {"prompt": prompt, "size": _gpt_image_size(str(args.get("size") or "1024_1024"))}
        if args.get("size"):
            width, height = _split_size(str(args.get("size") or "1024x1024"))
        else:
            width, height = _size_from_aspect(str(args.get("aspect_ratio") or ""), (1024, 1024))
        quality = _quality(args)
        body = {
            "prompt": prompt,
            "steps": _coerce_int(args.get("steps"), 8 if quality in {"draft", "low", "fast"} else 25, 1, 50),
            "width": _coerce_int(args.get("width"), width, 256, 2048),
            "height": _coerce_int(args.get("height"), height, 256, 2048),
        }
        for key in ("seed", "image", "image_url", "images", "reference_images"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if kind == "video":
        if not prompt:
            return {}
        quality = _quality(args)
        prompt = _professional_video_prompt(prompt, args, model_id)
        duration = _coerce_int(args.get("duration"), 5, 3, 15)
        if _is_kling_3_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _kling_aspect_ratio(args.get("aspect_ratio")),
                "sound": _wants_video_sound(prompt, args),
            }
            if args.get("negative_prompt"):
                body["negative_prompt"] = str(args["negative_prompt"])
            if args.get("cfg_scale") is not None:
                body["cfg_scale"] = _coerce_float(args.get("cfg_scale"), 0.5, 0.0, 1.0)
            if args.get("multi_prompt") is not None:
                body["multi_prompt"] = args["multi_prompt"]
            if args.get("shot_type") in {"intelligent", "customize"}:
                body["shot_type"] = args["shot_type"]
            source_image = args.get("start_image_url") or args.get("image_url") or args.get("image")
            if source_image is not None:
                body["start_image_url"] = source_image
            if args.get("tail_image_url") is not None:
                body["tail_image_url"] = args["tail_image_url"]
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            return body
        if _is_happyhorse_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _wan_or_happyhorse_aspect_ratio(args.get("aspect_ratio")),
                "resolution": _normalize_resolution(args.get("resolution"), quality).upper(),
                "watermark": _coerce_bool(args.get("watermark"), False),
            }
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            source_image = args.get("image_url") or args.get("image")
            if source_image is not None:
                body["image_url"] = source_image
            return body
        if _is_wan_27_model(model_id):
            body = {
                "prompt": prompt,
                "duration": duration,
                "aspect_ratio": _wan_or_happyhorse_aspect_ratio(args.get("aspect_ratio")),
                "resolution": _normalize_resolution(args.get("resolution"), quality),
                "enable_prompt_expansion": _coerce_bool(args.get("enable_prompt_expansion"), True),
            }
            if args.get("negative_prompt"):
                body["negative_prompt"] = str(args["negative_prompt"])
            if args.get("audio_url") is not None:
                body["audio_url"] = args["audio_url"]
            if args.get("seed") is not None:
                body["seed"] = args["seed"]
            return body
        body = {
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": _normalize_aspect_ratio(args.get("aspect_ratio")),
        }
        for key in ("image", "image_url", "audio_url", "video_url", "seed", "negative_prompt"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if kind == "music":
        duration = _coerce_int(args.get("duration") or args.get("seconds"), 60, 5, 300)
        if "elevenlabs/elevenlabs/music-generation" in model_id:
            eleven_prompt = str(args.get("prompt") or args.get("tags") or "").strip()
            lyrics = str(args.get("lyrics") or "").strip()
            if lyrics and lyrics not in eleven_prompt:
                eleven_prompt = (eleven_prompt + "\n" + lyrics).strip()
            if not eleven_prompt:
                return {}
            eleven_prompt = _professional_elevenlabs_prompt(eleven_prompt, args)
            return {
                "prompt": eleven_prompt,
                "music_length_ms": _coerce_int(args.get("music_length_ms"), duration * 1000, 5000, 300000),
            }
        tags = str(args.get("tags") or prompt).strip()
        if not tags:
            return {}
        tags = _professional_audio_tags(tags, args)
        lyrics = args.get("lyrics")
        if lyrics is None:
            lyrics = "[inst]" if args.get("force_instrumental", True) is not False else ""
        body = {
            "tags": tags,
            "duration": duration,
        }
        if lyrics:
            body["lyrics"] = str(lyrics)
        for key in ("audio", "start_time", "end_time", "extend_before_duration", "extend_after_duration"):
            if args.get(key) is not None:
                body[key] = args[key]
        return body

    if prompt:
        return {"prompt": prompt}
    return {}


def _media_type_for_path(path: Path, preferred_type: str = "") -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "webm" and preferred_type == "audio":
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "file"


def _image_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return None, None


def _save_gallery_row(
    *,
    path: Path,
    filename: str,
    media_type: str,
    prompt: str,
    model_id: str,
    owner: Optional[str],
    session_id: Optional[str],
    args: Dict[str, Any],
) -> str:
    if media_type not in {"image", "video"}:
        return ""
    try:
        from src.database import GalleryImage, SessionLocal

        raw = path.read_bytes()
        width, height = _image_dimensions(path) if media_type == "image" else (None, None)
        size_label = args.get("size")
        if not size_label and width and height:
            size_label = f"{width}x{height}"
        elif not size_label and args.get("duration"):
            size_label = f"{args.get('duration')}s"
        row_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.add(GalleryImage(
                id=row_id,
                filename=filename,
                prompt=(prompt or f"Generated {media_type}")[:500],
                model=model_id,
                size=str(size_label or ""),
                quality=str(args.get("quality") or args.get("resolution") or ""),
                session_id=session_id,
                owner=owner,
                file_hash=hashlib.sha256(raw).hexdigest(),
                file_size=len(raw),
                width=width,
                height=height,
                tags=f"generated,{media_type}",
            ))
            db.commit()
            return row_id
        finally:
            db.close()
    except Exception:
        return ""


def _collect_outputs(
    out_dir: Path,
    *,
    kind: str,
    prompt: str,
    model_id: str,
    owner: Optional[str],
    session_id: Optional[str],
    args: Dict[str, Any],
) -> list[Dict[str, Any]]:
    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    files: list[Dict[str, Any]] = []
    candidates: list[tuple[int, float, Path]] = []
    preferred_type = "audio" if kind == "music" else kind
    for candidate in out_dir.rglob("*"):
        if not candidate.is_file():
            continue
        ext = candidate.suffix.lower().lstrip(".")
        if ext not in MEDIA_EXTS:
            continue
        media_type = _media_type_for_path(candidate, preferred_type)
        priority = 0 if media_type == preferred_type else 1
        try:
            mtime = -candidate.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((priority, mtime, candidate))

    for _, _, candidate in sorted(candidates):
        ext = candidate.suffix.lower().lstrip(".")
        media_type = _media_type_for_path(candidate, preferred_type)
        final_name = f"{uuid.uuid4().hex[:12]}.{ext}"
        final_path = generated_root / final_name
        shutil.copyfile(candidate, final_path)
        media_id = _save_gallery_row(
            path=final_path,
            filename=final_name,
            media_type=media_type,
            prompt=prompt,
            model_id=model_id,
            owner=owner,
            session_id=session_id,
            args=args,
        )
        files.append({
            "url": f"/api/generated-image/{final_name}",
            "id": media_id,
            "filename": final_name,
            "type": media_type,
            "kind": kind,
            "size_bytes": final_path.stat().st_size,
        })
    return files


def _failure_message(kind: str, model_id: str, code: int, output: str, body: Dict[str, Any]) -> str:
    base = f"RunComfy {kind} generation failed with exit code {code}."
    hint = ""
    if code == 64:
        hint = "The CLI rejected the command arguments. Verify the RunComfy CLI version and model id."
    elif code == 65:
        hint = "The model rejected the input schema. Use `runcomfy_media` with the exact JSON fields from the skill/model page, or remove unsupported optional fields."
    elif code == 69:
        hint = "The upstream model service returned an error. Retry or switch to a nearby model tier."
    elif code == 75:
        hint = "This is retryable: timeout, rate limit, or queue pressure. Try again with a shorter duration/lower resolution."
    elif code == 77:
        hint = "RunComfy auth failed. Run `runcomfy login` or set `RUNCOMFY_TOKEN`."
    request_preview = json.dumps({"model_id": model_id, "input": body}, ensure_ascii=False)[:1200]
    parts = [base]
    if hint:
        parts.append(hint)
    if output:
        parts.append(output[:2000])
    parts.append("Request preview:\n" + request_preview)
    return "\n\n".join(parts)


def _default_comfyui_base_url() -> str:
    return (os.environ.get("COMFYUI_URL") or "http://127.0.0.1:8188").rstrip("/")


def _comfyui_base_url(integration: Optional[Dict[str, Any]]) -> str:
    return str((integration or {}).get("base_url") or _default_comfyui_base_url()).strip().rstrip("/")


def _comfyui_headers(integration: Optional[Dict[str, Any]]) -> Dict[str, str]:
    integration = integration or {}
    api_key = str(integration.get("api_key") or "").strip()
    auth_type = str(integration.get("auth_type") or "none").lower()
    if not api_key or "****" in api_key:
        return {}
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {api_key}"}
    if auth_type == "header":
        return {integration.get("auth_header") or "Authorization": api_key}
    return {}


def _comfyui_params(
    integration: Optional[Dict[str, Any]],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    final = dict(params or {})
    integration = integration or {}
    api_key = str(integration.get("api_key") or "").strip()
    if api_key and "****" not in api_key and str(integration.get("auth_type") or "").lower() == "query":
        final[integration.get("auth_param") or "api_key"] = api_key
    return final


def _comfyui_auth(integration: Optional[Dict[str, Any]]) -> Optional[httpx.BasicAuth]:
    integration = integration or {}
    api_key = str(integration.get("api_key") or "").strip()
    if not api_key or "****" in api_key or str(integration.get("auth_type") or "").lower() != "basic":
        return None
    user, sep, password = api_key.partition(":")
    return httpx.BasicAuth(user, password if sep else "")


async def _comfyui_server_available(integration: Optional[Dict[str, Any]] = None) -> bool:
    base_url = _comfyui_base_url(integration)
    if not base_url:
        return False
    try:
        async with httpx.AsyncClient(
            timeout=2.5,
            headers=_comfyui_headers(integration),
            auth=_comfyui_auth(integration),
        ) as client:
            response = await client.get(
                f"{base_url}/system_stats",
                params=_comfyui_params(integration),
            )
        return response.is_success
    except Exception:
        return False


def _parsed_comfyui_url(integration: Optional[Dict[str, Any]]) -> Any:
    base_url = _comfyui_base_url(integration)
    url = base_url if "://" in base_url else f"http://{base_url}"
    return urlparse(url)


def _is_local_comfyui_url(integration: Optional[Dict[str, Any]]) -> bool:
    parsed = _parsed_comfyui_url(integration)
    return (parsed.hostname or "").lower() in LOCAL_COMFY_HOSTS


def _comfyui_launch_port(integration: Optional[Dict[str, Any]]) -> int:
    parsed = _parsed_comfyui_url(integration)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _comfyui_launch_host(integration: Optional[Dict[str, Any]]) -> str:
    host = (_parsed_comfyui_url(integration).hostname or "127.0.0.1").lower()
    return "127.0.0.1" if host == "localhost" else host


def _comfyui_auto_launch_enabled(integration: Optional[Dict[str, Any]]) -> bool:
    integration = integration or {}
    value = integration.get("auto_launch")
    if value is None:
        value = os.environ.get("COMFYUI_AUTO_LAUNCH")
    return _coerce_bool(value, True)


def _comfyui_bootstrap_enabled(integration: Optional[Dict[str, Any]]) -> bool:
    integration = integration or {}
    value = integration.get("auto_install")
    if value is None:
        value = integration.get("bootstrap")
    if value is None:
        value = os.environ.get("COMFYUI_AUTO_INSTALL")
    if value is None:
        value = os.environ.get("COMFYUI_BOOTSTRAP")
    return _coerce_bool(value, True)


def _comfyui_model_download_enabled(integration: Optional[Dict[str, Any]]) -> bool:
    integration = integration or {}
    value = integration.get("auto_download_model")
    if value is None:
        value = os.environ.get("COMFYUI_AUTO_DOWNLOAD_MODEL")
    return _coerce_bool(value, True)


def _normalize_comfyui_accelerator(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-").replace("/", "-")
    if raw in {"amd", "amd-directml", "radeon", "directml", "dml", "dx12"}:
        return "directml"
    if raw in {"nvidia", "cuda", "gpu"}:
        return "nvidia"
    if raw in {"cpu", "none"}:
        return "cpu"
    if raw in {"auto", ""}:
        return "auto"
    return raw


def _detect_windows_comfyui_accelerator() -> str:
    global _COMFYUI_ACCELERATOR_CACHE
    if _COMFYUI_ACCELERATOR_CACHE is not None:
        return _COMFYUI_ACCELERATOR_CACHE
    if os.name != "nt":
        _COMFYUI_ACCELERATOR_CACHE = "auto"
        return _COMFYUI_ACCELERATOR_CACHE
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join ';'",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        names = f"{completed.stdout} {completed.stderr}".lower()
        if any(token in names for token in ("amd", "radeon", "advanced micro devices")):
            _COMFYUI_ACCELERATOR_CACHE = "directml"
        elif "nvidia" in names:
            _COMFYUI_ACCELERATOR_CACHE = "nvidia"
        else:
            _COMFYUI_ACCELERATOR_CACHE = "auto"
    except Exception:
        _COMFYUI_ACCELERATOR_CACHE = "auto"
    return _COMFYUI_ACCELERATOR_CACHE


def _comfyui_accelerator(integration: Optional[Dict[str, Any]]) -> str:
    integration = integration or {}
    raw = (
        integration.get("accelerator")
        or integration.get("gpu")
        or integration.get("device")
        or os.environ.get("COMFYUI_ACCELERATOR")
        or os.environ.get("COMFYUI_GPU")
    )
    normalized = _normalize_comfyui_accelerator(raw)
    return _detect_windows_comfyui_accelerator() if normalized == "auto" else normalized


def _comfyui_configured_dirs(integration: Optional[Dict[str, Any]]) -> list[Path]:
    integration = integration or {}
    raw_values = [
        integration.get("launch_cwd"),
        integration.get("working_dir"),
        integration.get("comfyui_dir"),
        integration.get("install_dir"),
        os.environ.get("COMFYUI_LAUNCH_CWD"),
        os.environ.get("COMFYUI_DIR"),
        os.environ.get("COMFYUI_PATH"),
    ]
    dirs: list[Path] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        dirs.append(Path(os.path.expandvars(os.path.expanduser(value))))
    return dirs


def _default_comfyui_dirs() -> list[Path]:
    base = Path(BASE_DIR).resolve()
    data = Path(DATA_DIR).resolve()
    home = Path.home()
    dirs = [
        data / "comfyui" / "ComfyUI",
        data / "ComfyUI",
        base / "ComfyUI",
        base.parent / "ComfyUI",
        Path.cwd() / "ComfyUI",
        home / "ComfyUI",
        home / "Documents" / "ComfyUI",
        Path("C:/ComfyUI"),
        Path("D:/ComfyUI"),
        Path("E:/ComfyUI"),
    ]
    for root in (base.parent, Path("D:/GitHub"), home):
        try:
            dirs.extend(path for path in root.glob("ComfyUI*") if path.is_dir())
        except Exception:
            pass
    return dirs


def _bootstrap_comfyui_dir(integration: Optional[Dict[str, Any]]) -> Path:
    configured = _comfyui_configured_dirs(integration)
    if configured:
        return configured[0]
    return Path(DATA_DIR).resolve() / "comfyui" / "ComfyUI"


def _comfyui_install_markers(path: Path) -> list[Path]:
    return [
        path / "main.py",
        path / "ComfyUI" / "main.py",
        path / "run_nvidia_gpu.bat",
        path / "run_amd_gpu.bat",
        path / "run_directml.bat",
        path / "run_cpu.bat",
    ]


def _candidate_comfyui_dirs(integration: Optional[Dict[str, Any]]) -> list[Path]:
    seen: set[str] = set()
    candidates: list[Path] = []
    for path in [*_comfyui_configured_dirs(integration), *_default_comfyui_dirs()]:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if any(marker.exists() for marker in _comfyui_install_markers(resolved)):
            candidates.append(resolved)
    return candidates


def _comfyui_script_order(accelerator: str) -> list[str]:
    if accelerator == "directml":
        return ["run_amd_gpu.bat", "run_directml.bat", "run_amd.bat", "run_gpu.bat"]
    if accelerator == "nvidia":
        return ["run_nvidia_gpu.bat", "run_gpu.bat", "run_cpu.bat"]
    if accelerator == "cpu":
        return ["run_cpu.bat"]
    return ["run_nvidia_gpu.bat", "run_amd_gpu.bat", "run_directml.bat", "run_gpu.bat", "run_cpu.bat"]


def _comfyui_main_script(path: Path) -> Optional[str]:
    if (path / "main.py").exists():
        return "main.py"
    nested = path / "ComfyUI" / "main.py"
    if nested.exists():
        return str(Path("ComfyUI") / "main.py")
    return None


def _comfyui_python_executable(path: Path) -> str:
    for rel in (
        Path("python_embeded") / "python.exe",
        Path("python_embedded") / "python.exe",
        Path(".venv") / "Scripts" / "python.exe",
        Path("venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
        Path("venv") / "bin" / "python",
    ):
        candidate = path / rel
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def _comfyui_main_extra_args(accelerator: str) -> list[str]:
    if accelerator == "directml":
        return ["--directml"]
    if accelerator == "cpu":
        return ["--cpu"]
    return []


def _comfyui_command_shell(command: str, cwd: Path) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("ComSpec") or "cmd.exe", "/c", command]
    return ["sh", "-lc", command]


def _comfyui_launch_spec(integration: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not _comfyui_auto_launch_enabled(integration) or not _is_local_comfyui_url(integration):
        return None

    integration = integration or {}
    base_url = _comfyui_base_url(integration)
    host = _comfyui_launch_host(integration)
    port = _comfyui_launch_port(integration)
    accelerator = _comfyui_accelerator(integration)
    raw_command = str(
        integration.get("launch_command")
        or integration.get("command")
        or os.environ.get("COMFYUI_LAUNCH_COMMAND")
        or ""
    ).strip()
    if raw_command:
        command = (
            raw_command
            .replace("{base_url}", base_url)
            .replace("{host}", host)
            .replace("{port}", str(port))
            .replace("{accelerator}", accelerator)
        )
        cwd = (_comfyui_configured_dirs(integration) or [Path(BASE_DIR)])[0]
        return {
            "argv": _comfyui_command_shell(command, cwd),
            "cwd": str(cwd),
            "accelerator": accelerator,
            "source": "COMFYUI_LAUNCH_COMMAND",
        }

    for cwd in _candidate_comfyui_dirs(integration):
        for script_name in _comfyui_script_order(accelerator):
            script = cwd / script_name
            if not script.exists():
                continue
            if os.name == "nt" and script.suffix.lower() in {".bat", ".cmd"}:
                argv = [os.environ.get("ComSpec") or "cmd.exe", "/c", str(script)]
            else:
                argv = [str(script)]
            return {
                "argv": argv,
                "cwd": str(cwd),
                "accelerator": accelerator,
                "source": script_name,
            }

        main_script = _comfyui_main_script(cwd)
        if main_script:
            argv = [
                _comfyui_python_executable(cwd),
                main_script,
                "--listen",
                host,
                "--port",
                str(port),
                *_comfyui_main_extra_args(accelerator),
            ]
            return {
                "argv": argv,
                "cwd": str(cwd),
                "accelerator": accelerator,
                "source": main_script,
            }

    return None


def _comfyui_can_auto_launch(integration: Optional[Dict[str, Any]] = None) -> bool:
    return _comfyui_launch_spec(integration) is not None


def _comfyui_can_auto_bootstrap(integration: Optional[Dict[str, Any]] = None) -> bool:
    return _comfyui_bootstrap_enabled(integration) and _is_local_comfyui_url(integration)


def _comfyui_log_paths() -> tuple[Path, Path]:
    log_dir = Path(DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "comfyui-local.out.log", log_dir / "comfyui-local.err.log"


def _comfyui_launch_timeout(integration: Optional[Dict[str, Any]]) -> int:
    integration = integration or {}
    return _coerce_int(
        integration.get("launch_timeout") or os.environ.get("COMFYUI_START_TIMEOUT"),
        120,
        5,
        600,
    )


def _comfyui_launch_retry_seconds(integration: Optional[Dict[str, Any]]) -> int:
    integration = integration or {}
    return _coerce_int(
        integration.get("launch_retry_seconds") or os.environ.get("COMFYUI_LAUNCH_RETRY_SECONDS"),
        15,
        0,
        300,
    )


def _comfyui_bootstrap_timeout(integration: Optional[Dict[str, Any]]) -> int:
    integration = integration or {}
    return _coerce_int(
        integration.get("bootstrap_timeout") or os.environ.get("COMFYUI_BOOTSTRAP_TIMEOUT"),
        3600,
        60,
        14400,
    )


def _comfyui_bootstrap_log_path() -> Path:
    log_dir = Path(DATA_DIR) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "comfyui-bootstrap.log"


def _display_command(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    import shlex

    return shlex.join(cmd)


def _run_comfyui_bootstrap_step(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
) -> None:
    run_env = dict(os.environ)
    run_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    run_env.setdefault("PYTHONUTF8", "1")
    if env:
        run_env.update(env)
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] $ {_display_command(cmd)}\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=run_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        log.write(f"[exit {proc.returncode}]\n")
        if proc.returncode != 0:
            raise RuntimeError(f"Command failed with exit code {proc.returncode}: {_display_command(cmd)}")


def _download_file(url: str, destination: Path, log_path: Path, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".download")
    if temp_path.exists():
        temp_path.unlink()
    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] download {url} -> {destination}\n")
        log.flush()
    previous_timeout = None
    try:
        import socket

        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        urllib.request.urlretrieve(url, temp_path)
        temp_path.replace(destination)
    finally:
        try:
            import socket

            socket.setdefaulttimeout(previous_timeout)
        except Exception:
            pass
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def _clone_or_download_comfyui(target_dir: Path, log_path: Path, timeout: int) -> None:
    if (target_dir / "main.py").exists():
        return
    if target_dir.exists():
        try:
            has_files = any(target_dir.iterdir())
        except Exception:
            has_files = True
        if has_files:
            raise RuntimeError(f"{target_dir} exists but does not look like a ComfyUI checkout.")

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    git_exe = shutil.which("git")
    repo_url = os.environ.get("COMFYUI_REPO_URL") or COMFYUI_REPO_URL
    if git_exe:
        _run_comfyui_bootstrap_step(
            [git_exe, "clone", "--depth", "1", repo_url, str(target_dir)],
            cwd=target_dir.parent,
            log_path=log_path,
            timeout=timeout,
        )
        return

    zip_url = os.environ.get("COMFYUI_ZIP_URL") or COMFYUI_ZIP_URL
    with tempfile.TemporaryDirectory(prefix="odysseus-comfyui-") as tmp:
        tmp_dir = Path(tmp)
        archive = tmp_dir / "comfyui.zip"
        _download_file(zip_url, archive, log_path, timeout)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp_dir)
        roots = [path for path in tmp_dir.iterdir() if path.is_dir() and (path / "main.py").exists()]
        if not roots:
            raise RuntimeError("Downloaded ComfyUI archive did not contain main.py.")
        shutil.move(str(roots[0]), str(target_dir))


def _comfyui_venv_python(target_dir: Path) -> Path:
    venv_dir = target_dir / ".venv"
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _install_comfyui_requirements(
    target_dir: Path,
    integration: Optional[Dict[str, Any]],
    log_path: Path,
    timeout: int,
) -> None:
    python_exe = _comfyui_venv_python(target_dir)
    if not python_exe.exists():
        _run_comfyui_bootstrap_step(
            [sys.executable, "-m", "venv", str(target_dir / ".venv")],
            cwd=target_dir,
            log_path=log_path,
            timeout=timeout,
        )
    _run_comfyui_bootstrap_step(
        [str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        cwd=target_dir,
        log_path=log_path,
        timeout=timeout,
    )
    requirements = target_dir / "requirements.txt"
    if requirements.exists() and _coerce_bool(os.environ.get("COMFYUI_INSTALL_REQUIREMENTS"), True):
        _run_comfyui_bootstrap_step(
            [str(python_exe), "-m", "pip", "install", "-r", str(requirements)],
            cwd=target_dir,
            log_path=log_path,
            timeout=timeout,
        )
    if _comfyui_accelerator(integration) == "directml":
        _run_comfyui_bootstrap_step(
            [str(python_exe), "-m", "pip", "install", "torch-directml", "torchaudio==2.4.1"],
            cwd=target_dir,
            log_path=log_path,
            timeout=timeout,
        )


def _comfyui_checkpoint_dir(target_dir: Path) -> Path:
    return target_dir / "models" / "checkpoints"


def _comfyui_has_checkpoint(target_dir: Path) -> bool:
    checkpoint_dir = _comfyui_checkpoint_dir(target_dir)
    if not checkpoint_dir.exists():
        return False
    return any(checkpoint_dir.glob("*.safetensors")) or any(checkpoint_dir.glob("*.ckpt"))


def _bootstrap_comfyui_model(
    target_dir: Path,
    integration: Optional[Dict[str, Any]],
    log_path: Path,
    timeout: int,
) -> None:
    if _comfyui_has_checkpoint(target_dir) or not _comfyui_model_download_enabled(integration):
        return
    integration = integration or {}
    model_url = str(
        integration.get("model_url")
        or os.environ.get("COMFYUI_BOOTSTRAP_MODEL_URL")
        or COMFYUI_DEFAULT_MODEL_URL
    ).strip()
    if not model_url:
        return
    model_name = str(
        integration.get("model_filename")
        or os.environ.get("COMFYUI_BOOTSTRAP_MODEL_NAME")
        or Path(urlparse(model_url).path).name
        or COMFYUI_DEFAULT_MODEL_NAME
    ).strip()
    if not model_name.lower().endswith((".safetensors", ".ckpt")):
        model_name = COMFYUI_DEFAULT_MODEL_NAME
    destination = _comfyui_checkpoint_dir(target_dir) / model_name
    if destination.exists():
        return
    _download_file(model_url, destination, log_path, timeout)


def _bootstrap_comfyui_install(integration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _COMFYUI_BOOTSTRAP_LAST_MESSAGE

    base_url = _comfyui_base_url(integration)
    if not _comfyui_bootstrap_enabled(integration):
        return {
            "ok": False,
            "message": f"ComfyUI Local is not reachable at {base_url}, and auto-install is disabled.",
        }
    if not _is_local_comfyui_url(integration):
        return {
            "ok": False,
            "message": f"ComfyUI Local is not reachable at {base_url}. Auto-install only supports localhost URLs.",
        }

    target_dir = _bootstrap_comfyui_dir(integration).resolve()
    log_path = _comfyui_bootstrap_log_path()
    timeout = _comfyui_bootstrap_timeout(integration)
    try:
        with log_path.open("a", encoding="utf-8", errors="replace") as log:
            log.write(
                "\n[{ts}] Bootstrapping ComfyUI for Odysseus\n"
                "target: {target}\n"
                "base_url: {base_url}\n"
                "accelerator: {accelerator}\n".format(
                    ts=time.strftime("%Y-%m-%d %H:%M:%S"),
                    target=target_dir,
                    base_url=base_url,
                    accelerator=_comfyui_accelerator(integration),
                )
            )
        _clone_or_download_comfyui(target_dir, log_path, timeout)
        _install_comfyui_requirements(target_dir, integration, log_path, timeout)
        _bootstrap_comfyui_model(target_dir, integration, log_path, timeout)
    except Exception as exc:
        message = f"ComfyUI auto-install failed: {exc}. Log: {log_path}"
        _COMFYUI_BOOTSTRAP_LAST_MESSAGE = message
        return {"ok": False, "message": message}

    message = f"ComfyUI auto-install finished at {target_dir}. Log: {log_path}"
    _COMFYUI_BOOTSTRAP_LAST_MESSAGE = message
    return {"ok": True, "message": message, "path": str(target_dir)}


def _launch_comfyui_server(integration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    global _COMFYUI_AUTOSTART_LAST_ATTEMPT, _COMFYUI_AUTOSTART_LAST_MESSAGE, _COMFYUI_AUTOSTART_PROCESS

    base_url = _comfyui_base_url(integration)
    if not _comfyui_auto_launch_enabled(integration):
        return {"ok": False, "message": f"ComfyUI Local is not reachable at {base_url}, and auto-launch is disabled."}
    if not _is_local_comfyui_url(integration):
        return {
            "ok": False,
            "message": (
                f"ComfyUI Local is not reachable at {base_url}. Auto-launch only starts localhost ComfyUI servers."
            ),
        }
    if _COMFYUI_AUTOSTART_PROCESS and _COMFYUI_AUTOSTART_PROCESS.poll() is None:
        out_log, err_log = _comfyui_log_paths()
        return {
            "ok": True,
            "message": f"ComfyUI auto-launch is already running. Logs: {out_log}, {err_log}",
        }

    now = time.monotonic()
    cooldown = _comfyui_launch_retry_seconds(integration)
    if cooldown and _COMFYUI_AUTOSTART_LAST_ATTEMPT and now - _COMFYUI_AUTOSTART_LAST_ATTEMPT < cooldown:
        return {
            "ok": False,
            "message": _COMFYUI_AUTOSTART_LAST_MESSAGE
            or f"ComfyUI Local is not reachable at {base_url}, and the previous auto-launch just failed.",
        }

    _COMFYUI_AUTOSTART_LAST_ATTEMPT = now
    spec = _comfyui_launch_spec(integration)
    if not spec:
        bootstrap = _bootstrap_comfyui_install(integration)
        if not bootstrap.get("ok"):
            message = str(bootstrap.get("message") or (
                f"ComfyUI Local is not reachable at {base_url}, and Odysseus could not install ComfyUI."
            ))
            _COMFYUI_AUTOSTART_LAST_MESSAGE = message
            return {"ok": False, "message": message}
        spec = _comfyui_launch_spec(integration)
        if not spec:
            message = (
                f"{bootstrap.get('message')} Odysseus still could not find a launchable ComfyUI entry point. "
                "Set COMFYUI_DIR to your ComfyUI folder or set COMFYUI_LAUNCH_COMMAND."
            )
            _COMFYUI_AUTOSTART_LAST_MESSAGE = message
            return {"ok": False, "message": message}

    out_log, err_log = _comfyui_log_paths()
    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        with out_log.open("ab", buffering=0) as stdout, err_log.open("ab", buffering=0) as stderr:
            _COMFYUI_AUTOSTART_PROCESS = subprocess.Popen(
                spec["argv"],
                cwd=spec["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
    except Exception as exc:
        message = f"ComfyUI auto-launch failed: {exc}"
        _COMFYUI_AUTOSTART_LAST_MESSAGE = message
        return {"ok": False, "message": message}

    message = (
        f"Started ComfyUI Local from {spec['source']} using {spec['accelerator']} mode. "
        f"Waiting for {base_url}. Logs: {out_log}, {err_log}"
    )
    _COMFYUI_AUTOSTART_LAST_MESSAGE = message
    return {"ok": True, "message": message}


async def _ensure_comfyui_server_available(integration: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    base_url = _comfyui_base_url(integration)
    if await _comfyui_server_available(integration):
        return True, f"ComfyUI Local is reachable at {base_url}."

    launch = await asyncio.to_thread(_launch_comfyui_server, integration)
    if not launch.get("ok"):
        return False, str(launch.get("message") or f"ComfyUI Local is not reachable at {base_url}.")

    timeout = _comfyui_launch_timeout(integration)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        if await _comfyui_server_available(integration):
            return True, str(launch.get("message") or f"ComfyUI Local started at {base_url}.")

    return (
        False,
        (
            f"{launch.get('message') or 'ComfyUI auto-launch started.'} "
            f"ComfyUI did not become ready at {base_url} within {timeout}s."
        ),
    )


def _object_options(object_info: Dict[str, Any], class_type: str, input_name: str) -> list[str]:
    class_info = object_info.get(class_type) if isinstance(object_info, dict) else None
    if not isinstance(class_info, dict):
        return []
    input_info = class_info.get("input") or {}
    for group in ("required", "optional"):
        spec = (input_info.get(group) or {}).get(input_name)
        if isinstance(spec, list) and spec:
            options = spec[0] if isinstance(spec[0], list) else spec
            return [str(item) for item in options if item is not None]
    return []


def _pick_comfy_option(
    options: list[str],
    requested: Any,
    preferred: list[str],
    fallback: str,
) -> str:
    raw = str(requested or "").strip()
    if raw and (not options or raw in options):
        return raw
    lowered = {item.lower(): item for item in options}
    for candidate in preferred:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return options[0] if options else fallback


def _local_checkpoint_name(args: Dict[str, Any], object_info: Dict[str, Any]) -> str:
    for key in ("ckpt_name", "checkpoint", "local_model"):
        value = str(args.get(key) or "").strip()
        if value:
            return value
    model = str(args.get("model") or args.get("model_id") or "").strip()
    if model and not any(model.lower().startswith(prefix) for prefix in RUNCOMFY_MODEL_PREFIXES):
        return model
    options = _object_options(object_info, "CheckpointLoaderSimple", "ckpt_name")
    return options[0] if options else ""


def _build_default_comfyui_image_workflow(
    args: Dict[str, Any],
    object_info: Dict[str, Any],
) -> tuple[Dict[str, Any], str, str]:
    prompt = str(args.get("prompt") or args.get("description") or "").strip()
    if not prompt:
        return {}, "", ""

    ckpt_name = _local_checkpoint_name(args, object_info)
    if not ckpt_name:
        return {}, prompt, ""

    if args.get("size"):
        width, height = _split_size(str(args.get("size") or "1024x1024"))
    else:
        width, height = _size_from_aspect(str(args.get("aspect_ratio") or ""), (1024, 1024))

    quality = _quality(args)
    steps_default = 8 if quality in {"draft", "low", "fast"} else 25
    sampler = _pick_comfy_option(
        _object_options(object_info, "KSampler", "sampler_name"),
        args.get("sampler") or args.get("sampler_name"),
        ["dpmpp_2m", "euler", "euler_ancestral"],
        "euler",
    )
    scheduler = _pick_comfy_option(
        _object_options(object_info, "KSampler", "scheduler"),
        args.get("scheduler"),
        ["karras", "normal", "simple"],
        "normal",
    )
    seed_default = int(uuid.uuid4().int % 2147483647)
    prompt = _professional_image_prompt(prompt, args, "comfyui-local")
    negative = str(
        args.get("negative_prompt")
        or "low quality, blurry, distorted, deformed, text artifacts, watermark"
    ).strip()
    workflow = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": _coerce_int(args.get("width"), width, 256, 2048),
                "height": _coerce_int(args.get("height"), height, 256, 2048),
                "batch_size": _coerce_int(args.get("batch_size"), 1, 1, 4),
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["4", 1]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": _coerce_int(args.get("seed"), seed_default, 0, 2147483647),
                "steps": _coerce_int(args.get("steps"), steps_default, 1, 80),
                "cfg": _coerce_float(args.get("cfg") or args.get("cfg_scale"), 7.0, 1.0, 20.0),
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": _coerce_float(args.get("denoise"), 1.0, 0.0, 1.0),
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "odysseus", "images": ["8", 0]},
        },
    }
    return workflow, prompt, f"comfyui-local:{ckpt_name}"


def _workflow_from_args(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    workflow = args.get("workflow") or args.get("comfyui_workflow")
    if isinstance(workflow, dict):
        return workflow
    if isinstance(workflow, str) and workflow.strip():
        try:
            parsed = json.loads(workflow)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    explicit = args.get("input") or args.get("body")
    if isinstance(explicit, dict) and any(
        isinstance(value, dict) and value.get("class_type")
        for value in explicit.values()
    ):
        return explicit
    return None


def _comfyui_history_outputs(history_entry: Dict[str, Any]) -> list[Dict[str, Any]]:
    outputs = history_entry.get("outputs") if isinstance(history_entry, dict) else None
    if not isinstance(outputs, dict):
        return []
    collected: list[Dict[str, Any]] = []
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        for key in ("images", "gifs", "videos", "audio", "audios"):
            values = node_output.get(key)
            if isinstance(values, list):
                collected.extend(item for item in values if isinstance(item, dict) and item.get("filename"))
    return collected


def _ext_from_response(filename: str, content_type: str) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext:
        return ext
    content_type = str(content_type or "").lower()
    if "png" in content_type:
        return "png"
    if "jpeg" in content_type or "jpg" in content_type:
        return "jpg"
    if "webp" in content_type:
        return "webp"
    if "mp4" in content_type:
        return "mp4"
    if "mpeg" in content_type:
        return "mp3"
    if "wav" in content_type:
        return "wav"
    return "bin"


async def _download_comfyui_outputs(
    client: httpx.AsyncClient,
    base_url: str,
    integration: Optional[Dict[str, Any]],
    outputs: list[Dict[str, Any]],
    *,
    kind: str,
    prompt: str,
    model_id: str,
    owner: Optional[str],
    session_id: Optional[str],
    args: Dict[str, Any],
) -> list[Dict[str, Any]]:
    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    preferred_type = "audio" if kind == "music" else kind
    files: list[Dict[str, Any]] = []
    for output in outputs:
        view_params = {
            "filename": output.get("filename"),
            "subfolder": output.get("subfolder") or "",
            "type": output.get("type") or "output",
        }
        response = await client.get(
            f"{base_url}/view",
            params=_comfyui_params(integration, view_params),
        )
        response.raise_for_status()
        ext = _ext_from_response(str(output.get("filename") or ""), response.headers.get("content-type", ""))
        if ext not in MEDIA_EXTS:
            ext = "png" if kind == "image" else ext
        final_name = f"{uuid.uuid4().hex[:12]}.{ext}"
        final_path = generated_root / final_name
        final_path.write_bytes(response.content)
        media_type = _media_type_for_path(final_path, preferred_type)
        media_id = _save_gallery_row(
            path=final_path,
            filename=final_name,
            media_type=media_type,
            prompt=prompt,
            model_id=model_id,
            owner=owner,
            session_id=session_id,
            args=args,
        )
        files.append({
            "url": f"/api/generated-image/{final_name}",
            "id": media_id,
            "filename": final_name,
            "type": media_type,
            "kind": kind,
            "size_bytes": final_path.stat().st_size,
        })
    return files


def _media_success_result(
    *,
    kind: str,
    files: list[Dict[str, Any]],
    prompt: str,
    model_id: str,
    args: Dict[str, Any],
    provider: str,
) -> Dict[str, Any]:
    first = files[0]
    label = first["type"]
    result: Dict[str, Any] = {
        "output": f"Generated {label} saved: {first['url']}",
        "media_url": first["url"],
        "media_id": first.get("id") or "",
        "media_type": first["type"],
        "media_prompt": prompt,
        "media_model": model_id,
        "media_provider": provider,
        "media_size": str(args.get("size") or args.get("duration") or ""),
        "media_quality": _quality(args),
        "media_files": files,
        "exit_code": 0,
    }
    if first["type"] == "image":
        result.update({
            "image_url": first["url"],
            "image_id": first.get("id") or "",
            "image_prompt": prompt,
            "image_model": model_id,
            "image_provider": provider,
            "image_size": str(args.get("size") or ""),
            "image_quality": _quality(args),
        })
    return result


async def _generate_local_comfyui_media(
    kind: str,
    args: Dict[str, Any],
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
    integration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if kind != "image" and not _workflow_from_args(args):
        return {
            "error": (
                "Local ComfyUI currently needs an exact workflow JSON for video or audio. "
                "For simple video/music requests, enable the RunComfy Cloud integration, or pass "
                "`workflow`/`comfyui_workflow` for your local ComfyUI setup."
            ),
            "exit_code": 1,
        }

    integration = integration or _comfyui_integration(args)
    base_url = _comfyui_base_url(integration)
    if not base_url:
        return {"error": "ComfyUI Local has no Base URL configured.", "exit_code": 1}

    ready, ready_message = await _ensure_comfyui_server_available(integration)
    if not ready:
        return {"error": ready_message, "exit_code": 1}

    timeout = _coerce_int(args.get("timeout"), 900, 30, 7200)
    client_id = uuid.uuid4().hex
    prompt = str(args.get("prompt") or args.get("description") or f"Generated {kind}").strip()

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            headers=_comfyui_headers(integration),
            auth=_comfyui_auth(integration),
        ) as client:
            object_info: Dict[str, Any] = {}
            workflow = _workflow_from_args(args)
            if workflow is None:
                try:
                    object_response = await client.get(
                        f"{base_url}/object_info",
                        params=_comfyui_params(integration),
                    )
                    object_response.raise_for_status()
                    object_info = object_response.json()
                except Exception as exc:
                    return {
                        "error": (
                            f"ComfyUI Local is reachable at {base_url}, but Odysseus could not read /object_info: {exc}. "
                            "Start ComfyUI normally and make sure its API is enabled."
                        ),
                        "exit_code": 1,
                    }
                workflow, prompt, model_id = _build_default_comfyui_image_workflow(args, object_info)
                if not workflow:
                    if not model_id:
                        return {
                            "error": (
                                "ComfyUI Local is reachable, but no checkpoint was reported by CheckpointLoaderSimple. "
                                "Put a .safetensors/.ckpt file under ComfyUI/models/checkpoints or pass `ckpt_name`."
                            ),
                            "exit_code": 1,
                        }
                    return {"error": f"A prompt is required for local ComfyUI {kind} generation.", "exit_code": 1}
            else:
                model_id = str(args.get("model") or args.get("model_id") or "comfyui-local:workflow").strip()

            submit_response = await client.post(
                f"{base_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
                params=_comfyui_params(integration),
            )
            if submit_response.status_code >= 400:
                return {
                    "error": f"ComfyUI Local rejected the workflow: HTTP {submit_response.status_code}\n{submit_response.text[:1200]}",
                    "exit_code": 1,
                }
            submit_payload = submit_response.json()
            prompt_id = str(submit_payload.get("prompt_id") or "").strip()
            if not prompt_id:
                return {"error": f"ComfyUI Local did not return a prompt_id: {submit_payload}", "exit_code": 1}

            history_entry: Dict[str, Any] = {}
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                history_response = await client.get(
                    f"{base_url}/history/{prompt_id}",
                    params=_comfyui_params(integration),
                )
                if history_response.is_success:
                    history_payload = history_response.json()
                    entry = history_payload.get(prompt_id) if isinstance(history_payload, dict) else None
                    if isinstance(entry, dict):
                        status = entry.get("status") or {}
                        status_str = str(status.get("status_str") or "").lower()
                        if status_str == "error":
                            return {
                                "error": f"ComfyUI Local workflow failed: {json.dumps(status, ensure_ascii=False)[:1600]}",
                                "exit_code": 1,
                            }
                        outputs = _comfyui_history_outputs(entry)
                        if outputs:
                            history_entry = entry
                            break
                await asyncio.sleep(1.0)

            if not history_entry:
                return {"error": f"ComfyUI Local generation timed out after {timeout}s.", "exit_code": 1}

            outputs = _comfyui_history_outputs(history_entry)
            files = await _download_comfyui_outputs(
                client,
                base_url,
                integration,
                outputs,
                kind=kind,
                prompt=prompt,
                model_id=model_id,
                owner=owner,
                session_id=session_id,
                args=args,
            )
    except httpx.ConnectError:
        return {
            "error": (
                f"ComfyUI Local is not reachable at {base_url}. Start ComfyUI locally, or add/edit the "
                "ComfyUI Local integration with the right Base URL."
            ),
            "exit_code": 1,
        }
    except httpx.RequestError as exc:
        return {"error": f"ComfyUI Local request failed: {exc}", "exit_code": 1}
    except Exception as exc:
        return {"error": f"ComfyUI Local generation failed: {exc}", "exit_code": 1}

    if not files:
        return {
            "error": "ComfyUI Local completed, but no media outputs were returned in prompt history.",
            "exit_code": 1,
        }

    return _media_success_result(
        kind=kind,
        files=files,
        prompt=prompt,
        model_id=model_id,
        args=args,
        provider="comfyui_local",
    )


async def _generate_runcomfy_cli_media(
    kind: str,
    args: Dict[str, Any],
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
    default_model: Optional[str] = None,
    integration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    integration = integration or _runcomfy_integration(args)
    if not integration:
        return {
            "error": (
                "RunComfy Cloud is disabled. Add and enable the `RunComfy Cloud` integration to use the paid route. "
                "For the free route, start local ComfyUI and add/use the `ComfyUI Local` integration."
            ),
            "exit_code": 1,
        }

    model_id = _select_model(kind, args, default_model).strip()
    if not model_id:
        return {"error": "RunComfy model_id is required.", "exit_code": 1}

    body = _input_for_kind(kind, args, model_id)
    if not body:
        return {"error": f"A prompt or input JSON body is required for {kind} generation.", "exit_code": 1}

    exe = _runcomfy_executable()
    if not exe:
        return {
            "error": (
                "RunComfy Cloud integration is enabled, but the RunComfy CLI is not installed or not on PATH. "
                "Install it with `npm i -g @runcomfy/cli`, then run `runcomfy login` once."
            ),
            "exit_code": 1,
        }

    ready_error = await asyncio.to_thread(_check_runcomfy_ready, exe, integration)
    if ready_error:
        return ready_error

    prompt = str(body.get("prompt") or body.get("tags") or args.get("prompt") or f"Generated {kind}").strip()
    timeout = _coerce_int(args.get("timeout"), 1800 if kind in {"video", "music"} else 900, 60, 7200)

    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runcomfy_", dir=str(generated_root)) as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / "input.json"
        input_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [
            exe,
            "run",
            model_id,
            "--input-file",
            input_path.name,
            "--output-dir",
            ".",
        ]
        if args.get("output") == "json":
            cmd.extend(["--output", "json"])

        try:
            proc = await asyncio.to_thread(_run_checked, cmd, timeout, str(tmp_dir), _runcomfy_env(integration))
        except subprocess.TimeoutExpired:
            return {"error": f"RunComfy {kind} generation timed out after {timeout}s.", "exit_code": 1}
        except Exception as exc:
            return {"error": f"RunComfy execution failed: {exc}", "exit_code": 1}

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        if proc.returncode != 0:
            if proc.returncode == 77 or "not signed in" in combined.lower() or "login" in combined.lower():
                return {"error": _auth_error_message(combined), "exit_code": proc.returncode or 1}
            return {
                "error": _failure_message(kind, model_id, proc.returncode, combined, body),
                "exit_code": proc.returncode or 1,
            }

        files = _collect_outputs(
            tmp_dir,
            kind=kind,
            prompt=prompt,
            model_id=model_id,
            owner=owner,
            session_id=session_id,
            args=args,
        )

    if not files:
        return {
            "error": (
                f"RunComfy completed, but no downloaded media file was found in the output directory.\n"
                f"{combined[:2000]}"
            ),
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": 1,
        }

    result = _media_success_result(
        kind=kind,
        files=files,
        prompt=prompt,
        model_id=model_id,
        args=args,
        provider="runcomfy",
    )
    if result.get("image_url"):
        result["image_size"] = str(args.get("size") or body.get("size") or (
            f"{body.get('width')}x{body.get('height')}" if body.get("width") and body.get("height") else ""
        ))
    return result


def _builtin_music_seed(text: str) -> int:
    digest = hashlib.sha256((text or "odysseus").encode("utf-8", errors="ignore")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _deterministic_noise(index: int, seed: int) -> float:
    raw = math.sin((index + (seed % 100_000)) * 12.9898) * 43758.5453
    return (raw - math.floor(raw)) * 2.0 - 1.0


def _write_builtin_synth_wav(path: Path, prompt: str, args: Dict[str, Any]) -> None:
    sample_rate = 22050
    duration = _coerce_int(args.get("duration") or args.get("seconds"), 30, 5, 120)
    seed = _builtin_music_seed(prompt)
    bpm = _coerce_int(args.get("bpm"), 88 + (seed % 41), 60, 180)
    base_notes = [220.0, 246.94, 261.63, 293.66, 329.63, 349.23, 392.0]
    base = base_notes[seed % len(base_notes)]
    scales = (
        [0, 2, 4, 7, 9, 12],
        [0, 3, 5, 7, 10, 12],
        [0, 2, 3, 7, 9, 12],
        [0, 5, 7, 10, 12, 14],
    )
    scale = scales[(seed >> 4) % len(scales)]
    progression = [0, 3, 4, 1, 5, 4, 0, 0]
    total = sample_rate * duration
    frames = bytearray()
    two_pi = math.pi * 2.0
    beat_len = 60.0 / bpm

    for i in range(total):
        t = i / sample_rate
        beat = t / beat_len
        bar = int(beat // 4)
        beat_in_bar = beat % 4.0
        eighth = int(beat * 2)
        step_phase = (beat * 2.0) % 1.0
        chord_root = progression[bar % len(progression)]
        melody_pick = (eighth + (seed >> (eighth % 16))) % len(scale)
        melody_freq = base * (2 ** ((scale[melody_pick] + chord_root) / 12.0))
        bass_freq = (base / 2.0) * (2 ** (chord_root / 12.0))

        gate = min(1.0, step_phase * 10.0) * max(0.0, 1.0 - step_phase * 0.75)
        melody = (
            math.sin(two_pi * melody_freq * t)
            + 0.35 * math.sin(two_pi * melody_freq * 2.0 * t)
        ) * 0.12 * gate
        harmony = math.sin(two_pi * melody_freq * 0.5 * t) * 0.07 * gate
        bass = math.sin(two_pi * bass_freq * t) * 0.16 * max(0.0, 1.0 - (beat % 1.0) * 0.45)

        kick_phase = beat % 1.0
        kick = 0.0
        if kick_phase < 0.16:
            kick_freq = 78.0 - 42.0 * (kick_phase / 0.16)
            kick = math.sin(two_pi * kick_freq * t) * math.exp(-28.0 * kick_phase) * 0.45

        snare = 0.0
        if (1.0 <= beat_in_bar < 1.12) or (3.0 <= beat_in_bar < 3.12):
            local = min(beat_in_bar - 1.0 if beat_in_bar < 2.0 else beat_in_bar - 3.0, 0.12)
            snare = _deterministic_noise(i, seed) * math.exp(-18.0 * local) * 0.18

        hat_phase = (beat * 2.0) % 1.0
        hat = 0.0
        if hat_phase < 0.08:
            hat = _deterministic_noise(i * 3, seed) * math.exp(-45.0 * hat_phase) * 0.07

        left = bass + kick + snare + hat + melody * 0.85 + harmony * 0.55
        right = bass + kick + snare + hat + melody * 0.55 + harmony * 0.85
        left = max(-0.95, min(0.95, left))
        right = max(-0.95, min(0.95, right))
        frames.extend(struct.pack("<hh", int(left * 32767), int(right * 32767)))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(frames))


async def _generate_builtin_music_media(
    args: Dict[str, Any],
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    prompt = str(
        args.get("prompt")
        or args.get("description")
        or args.get("tags")
        or "an original instrumental song"
    ).strip()
    if not prompt:
        return {"error": "A prompt is required for local music generation.", "exit_code": 1}

    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    final_name = f"{uuid.uuid4().hex[:12]}.wav"
    final_path = generated_root / final_name
    await asyncio.to_thread(_write_builtin_synth_wav, final_path, prompt, args)
    media_id = _save_gallery_row(
        path=final_path,
        filename=final_name,
        media_type="audio",
        prompt=prompt,
        model_id="odysseus-local-synth",
        owner=owner,
        session_id=session_id,
        args=args,
    )
    result = _media_success_result(
        kind="music",
        files=[{
            "url": f"/api/generated-image/{final_name}",
            "id": media_id,
            "filename": final_name,
            "type": "audio",
            "kind": "music",
            "size_bytes": final_path.stat().st_size,
        }],
        prompt=prompt,
        model_id="odysseus-local-synth",
        args=args,
        provider="builtin_audio",
    )
    result["output"] = (
        f"Generated local synth audio saved: {result['media_url']}\n"
        "Provider: built-in local WAV fallback. For AI music/vocals, enable RunComfy Cloud or pass a local ComfyUI audio workflow."
    )
    return result


async def _generate_gemini_video_media(
    kind: str,
    args: Dict[str, Any],
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if kind != "video":
        return None

    config = _gemini_video_endpoint_config(owner=owner)
    if not config:
        return None

    prompt = str(args.get("prompt") or args.get("description") or "").strip()
    if not prompt:
        return {"error": "A prompt is required for Gemini/Veo video generation.", "exit_code": 1}

    model_id = _select_gemini_video_model(args, config.get("models") or [])
    prompt = _professional_video_prompt(prompt, args, model_id)
    timeout = _coerce_int(args.get("timeout"), 1800, 60, 7200)
    poll_interval = _coerce_int(args.get("poll_interval"), 10, 2, 60)
    configured_base = str(config.get("base_url") or "").rstrip("/")
    native_base_url = str(config.get("native_base_url") or _gemini_native_base_from_url(configured_base)).rstrip("/")
    api_key = _extract_api_key_from_headers(dict(config["headers"] or {}))
    if not api_key:
        return {"error": "Gemini/Veo video generation needs a Gemini API key.", "exit_code": 1}

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    download_headers = {"x-goog-api-key": api_key}
    body: Dict[str, Any] = {"instances": [{"prompt": prompt}]}
    parameters = _gemini_video_parameters(args)
    if parameters:
        body["parameters"] = parameters

    create_url = f"{native_base_url}/models/{model_id}:predictLongRunning"

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0),
            follow_redirects=True,
        ) as client:
            create_response = await client.post(create_url, headers=headers, json=body)
            if create_response.status_code >= 400:
                return {
                    "error": f"Gemini/Veo video generation failed ({create_response.status_code}): {create_response.text[:1200]}",
                    "exit_code": 1,
                }
            operation = create_response.json()
            operation_name = str(operation.get("name") or operation.get("id") or "").strip().lstrip("/")
            if not operation_name:
                return {"error": f"Gemini/Veo returned no operation id: {operation}", "exit_code": 1}

            status_payload = operation
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = str(status_payload.get("status") or "").strip().lower()
                if status in {"completed", "succeeded", "done"} or status_payload.get("done") is True:
                    break
                if status in {"failed", "cancelled", "canceled", "error"}:
                    return {
                        "error": f"Gemini/Veo video generation failed: {json.dumps(status_payload.get('error') or status_payload, ensure_ascii=False)[:1200]}",
                        "exit_code": 1,
                    }
                if status_payload.get("error"):
                    return {
                        "error": f"Gemini/Veo video generation failed: {json.dumps(status_payload.get('error'), ensure_ascii=False)[:1200]}",
                        "exit_code": 1,
                    }
                await asyncio.sleep(poll_interval)
                status_response = await client.get(f"{native_base_url}/{operation_name}", headers=download_headers)
                if status_response.status_code >= 400:
                    return {
                        "error": f"Gemini/Veo video status check failed ({status_response.status_code}): {status_response.text[:1200]}",
                        "exit_code": 1,
                    }
                status_payload = status_response.json()
            else:
                return {"error": f"Gemini/Veo video generation timed out after {timeout}s.", "exit_code": 1}

            video_url = _find_video_url(status_payload)
            if not video_url:
                return {"error": f"Gemini/Veo completed but returned no video URL: {json.dumps(status_payload, ensure_ascii=False)[:1200]}", "exit_code": 1}

            video_url = _normalize_gemini_video_download_url(video_url, native_base_url)
            download_response = await client.get(video_url, headers=download_headers, timeout=300.0)
            if download_response.status_code >= 400:
                return {
                    "error": f"Gemini/Veo video download failed ({download_response.status_code}): {download_response.text[:1200]}",
                    "exit_code": 1,
                }
    except httpx.RequestError as exc:
        return {"error": f"Gemini/Veo video request failed: {exc}", "exit_code": 1}
    except Exception as exc:
        return {"error": f"Gemini/Veo video generation failed: {exc}", "exit_code": 1}

    generated_root = Path(GENERATED_IMAGES_DIR)
    generated_root.mkdir(parents=True, exist_ok=True)
    parsed_url = urlparse(video_url)
    ext = _ext_from_response(Path(parsed_url.path).name or "video.mp4", download_response.headers.get("content-type", ""))
    if ext not in VIDEO_EXTS:
        ext = "mp4"
    final_name = f"{uuid.uuid4().hex[:12]}.{ext}"
    final_path = generated_root / final_name
    final_path.write_bytes(download_response.content)
    result_args = dict(args)
    duration = _gemini_video_duration_seconds(args)
    if duration and not result_args.get("duration"):
        result_args["duration"] = duration
    media_id = _save_gallery_row(
        path=final_path,
        filename=final_name,
        media_type="video",
        prompt=prompt,
        model_id=model_id,
        owner=owner,
        session_id=session_id,
        args=result_args,
    )
    return _media_success_result(
        kind="video",
        files=[{
            "url": f"/api/generated-image/{final_name}",
            "id": media_id,
            "filename": final_name,
            "type": "video",
            "kind": "video",
            "size_bytes": final_path.stat().st_size,
        }],
        prompt=prompt,
        model_id=model_id,
        args=result_args,
        provider="gemini_veo",
    )


async def generate_runcomfy_media(
    kind: str,
    content: str,
    *,
    owner: Optional[str] = None,
    session_id: Optional[str] = None,
    default_model: Optional[str] = None,
) -> Dict[str, Any]:
    args = _parse_args(content, kind=kind)
    requested = _requested_provider(args, content) or _requested_provider_from_integration(args)

    if requested == "comfyui_local":
        return await _generate_local_comfyui_media(
            kind,
            args,
            owner=owner,
            session_id=session_id,
            integration=_comfyui_integration(args),
        )

    if requested == "runcomfy":
        return await _generate_runcomfy_cli_media(
            kind,
            args,
            owner=owner,
            session_id=session_id,
            default_model=default_model,
            integration=_runcomfy_integration(args),
        )

    if requested == "gemini_video" or (not requested and kind == "video"):
        gemini_result = await _generate_gemini_video_media(
            kind,
            args,
            owner=owner,
            session_id=session_id,
        )
        if gemini_result is not None:
            return gemini_result

    if kind == "image":
        local_integration = _comfyui_integration(args)
        if (
            local_integration
            or await _comfyui_server_available(None)
            or _comfyui_can_auto_launch(None)
            or _comfyui_can_auto_bootstrap(None)
        ):
            return await _generate_local_comfyui_media(
                kind,
                args,
                owner=owner,
                session_id=session_id,
                integration=local_integration,
            )

    runcomfy_integration = _runcomfy_integration(args)
    if runcomfy_integration:
        return await _generate_runcomfy_cli_media(
            kind,
            args,
            owner=owner,
            session_id=session_id,
            default_model=default_model,
            integration=runcomfy_integration,
        )

    if kind == "image":
        return {
            "error": (
                "No media backend is ready. For the free path, Odysseus can auto-install and launch ComfyUI at "
                f"{_default_comfyui_base_url()}. If you disabled auto-install, set COMFYUI_DIR or COMFYUI_LAUNCH_COMMAND. "
                "For AMD GPUs, set COMFYUI_ACCELERATOR=amd or use a DirectML ComfyUI launch script. "
                "For the paid path, add and enable the `RunComfy Cloud` integration."
            ),
            "exit_code": 1,
        }

    if kind == "music":
        return await _generate_builtin_music_media(
            args,
            owner=owner,
            session_id=session_id,
        )

    return {
        "error": (
            f"No {kind} media backend is configured. Enable `RunComfy Cloud` for simple {kind} generation, "
            "or pass an exact local ComfyUI workflow with provider `comfyui`."
        ),
        "exit_code": 1,
    }


async def test_media_integration(integration: Dict[str, Any]) -> Dict[str, Any]:
    kind = _integration_kind(integration)
    if kind == "comfyui_local":
        base_url = _comfyui_base_url(integration)
        try:
            async with httpx.AsyncClient(
                timeout=5.0,
                headers=_comfyui_headers(integration),
                auth=_comfyui_auth(integration),
            ) as client:
                response = await client.get(
                    f"{base_url}/system_stats",
                    params=_comfyui_params(integration),
                )
            if response.is_success:
                return {"ok": True, "message": f"ComfyUI Local is reachable at {base_url}."}
            return {"ok": False, "message": f"ComfyUI Local returned HTTP {response.status_code} from {base_url}."}
        except Exception as exc:
            return {"ok": False, "message": f"ComfyUI Local is not reachable at {base_url}: {exc}"[:500]}

    if kind == "runcomfy":
        exe = _runcomfy_executable()
        if not exe:
            return {
                "ok": False,
                "message": "RunComfy Cloud integration is enabled, but the RunComfy CLI is not installed or not on PATH.",
            }
        ready_error = await asyncio.to_thread(_check_runcomfy_ready, exe, integration)
        if ready_error:
            return {"ok": False, "message": str(ready_error.get("error") or "RunComfy CLI is not ready")[:500]}
        return {"ok": True, "message": "RunComfy CLI is installed and signed in."}

    return {"ok": False, "message": "This integration is not a ComfyUI/RunComfy media integration."}

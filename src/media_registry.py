# src/media_registry.py
"""Media generation model registry (Slice 2).

A lightweight, settings-backed registry that sits in front of the media
provider layer so the agent uses *configured model IDs* instead of guessing
unavailable model names (e.g. "Stable Diffusion", "FLUX").

Source of truth (data/settings.json, see src/settings.py DEFAULT_SETTINGS):
  - ``media_models``               list of MediaModel dicts (shape below)
  - ``default_image_media_model``  preferred image model id (string)
  - ``comfyui_endpoint_url``       default ComfyUI endpoint, used when a model
                                   entry omits its own ``endpointUrl``

MediaModel shape (from the build brief; superfluous keys are ignored):
    {
      "id": str,                 # required, unique within the registry
      "label": str,
      "provider": str,           # one of MEDIA_PROVIDER_TYPES (else "custom")
      "kind": str,               # one of MEDIA_KINDS (else "image")
      "capabilities": [str],     # subset of MEDIA_CAPABILITIES
      "endpointUrl": str,        # optional; falls back to comfyui_endpoint_url
      "workflowPath": str,       # optional
      "enabled": bool,           # default True when omitted
      "isDefault": bool,         # default False
      "generationTimeoutSeconds": number,  # optional ComfyUI poll budget override
      "defaultSize": str,        # optional WxH default for ComfyUI generation
      "notes": str               # optional
    }

This module performs **no network calls**. Provider probing and image
generation are added in later slices (S3+). The hybrid design (per OQ-1)
keeps ComfyUI out of the OpenAI-compatible ModelEndpoint assumptions while
reusing the existing settings persistence layer (per OQ-2).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Vocabulary (mirrors the build brief's proposed types) ──

MEDIA_KINDS = ("image", "video")

MEDIA_PROVIDER_TYPES = ("comfyui", "diffusers", "wan", "ltx", "custom")

MEDIA_CAPABILITIES = (
    "text-to-image",
    "image-to-image",
    "image-edit",
    "text-to-video",
    "image-to-video",
)

# Suggested (not stored) default endpoint surfaced in degraded-state guidance.
SUGGESTED_COMFYUI_ENDPOINT = "http://localhost:8188"

# ComfyUI / local media generation sizes (WxH). Conservative default for
# first-run smoke tests on Apple Silicon / 16 GB class machines.
ALLOWED_IMAGE_SIZES = frozenset({"512x512", "768x768", "1024x1024"})
DEFAULT_COMFYUI_IMAGE_SIZE = "512x512"

# Maps a media kind to the settings key naming its preferred default model.
# Only image is wired for the MVP; video is intentionally left out.
_DEFAULT_MODEL_SETTING_BY_KIND = {
    "image": "default_image_media_model",
}

# Fields safe to expose to the agent / end user. Deliberately omits
# ``endpointUrl`` and ``workflowPath`` so local paths / internal URLs are not
# leaked into tool output (see brief Safety and Security Notes).
_PUBLIC_FIELDS = (
    "id",
    "label",
    "provider",
    "kind",
    "capabilities",
    "enabled",
    "isDefault",
    "notes",
)


# ── Settings access ──

def _load_settings(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the merged settings dict.

    Tests (and later slices) may pass an explicit ``settings`` dict to keep the
    registry pure / offline; otherwise it reads through ``src.settings``.
    """
    if settings is not None:
        return settings
    try:
        from src.settings import load_settings
        return load_settings()
    except Exception:  # pragma: no cover - defensive: settings unavailable at boot
        logger.warning("media_registry: settings unavailable, using empty config")
        return {}


# ── Normalization ──

def normalize_model(
    raw: Any, settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Validate and normalize a single registry entry.

    Returns a clean MediaModel dict, or ``None`` if the entry is unusable
    (not a dict, or missing a non-empty ``id``). Unknown ``provider`` /
    ``kind`` values are coerced to safe defaults rather than rejected so a
    single bad field does not drop the whole entry.
    """
    if not isinstance(raw, dict):
        return None

    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    model_id = model_id.strip()

    label = raw.get("label")
    label = label.strip() if isinstance(label, str) and label.strip() else model_id

    provider = raw.get("provider")
    if not isinstance(provider, str) or provider not in MEDIA_PROVIDER_TYPES:
        provider = "custom"

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in MEDIA_KINDS:
        kind = "image"

    caps_in = raw.get("capabilities")
    capabilities: List[str] = []
    if isinstance(caps_in, list):
        for cap in caps_in:
            if isinstance(cap, str) and cap in MEDIA_CAPABILITIES and cap not in capabilities:
                capabilities.append(cap)

    endpoint_url = raw.get("endpointUrl")
    endpoint_url = endpoint_url.strip() if isinstance(endpoint_url, str) else ""
    # ComfyUI entries inherit the global endpoint when they don't carry one.
    if not endpoint_url and provider == "comfyui":
        cfg = _load_settings(settings)
        fallback = cfg.get("comfyui_endpoint_url")
        if isinstance(fallback, str):
            endpoint_url = fallback.strip()

    workflow_path = raw.get("workflowPath")
    workflow_path = workflow_path.strip() if isinstance(workflow_path, str) and workflow_path.strip() else None

    # Checkpoint name for ComfyUI workflows. Accept either ``checkpoint`` or the
    # ``checkpointName`` alias. This is a ComfyUI-side model identifier (a name
    # inside the provider's models dir), NOT a path on the Odysseus host. It is
    # internal config and is intentionally NOT in _PUBLIC_FIELDS, so it never
    # leaks through list_media_models.
    checkpoint = raw.get("checkpoint")
    if not (isinstance(checkpoint, str) and checkpoint.strip()):
        checkpoint = raw.get("checkpointName")
    checkpoint = checkpoint.strip() if isinstance(checkpoint, str) and checkpoint.strip() else None

    notes = raw.get("notes")
    notes = notes.strip() if isinstance(notes, str) and notes.strip() else None

    model: Dict[str, Any] = {
        "id": model_id,
        "label": label,
        "provider": provider,
        "kind": kind,
        "capabilities": capabilities,
        "endpointUrl": endpoint_url,
        "enabled": bool(raw.get("enabled", True)),
        "isDefault": bool(raw.get("isDefault", False)),
    }
    if workflow_path is not None:
        model["workflowPath"] = workflow_path
    if checkpoint is not None:
        model["checkpoint"] = checkpoint
    if notes is not None:
        model["notes"] = notes

    gen_timeout = raw.get("generationTimeoutSeconds")
    if gen_timeout is None:
        gen_timeout = raw.get("generation_timeout_seconds")
    if gen_timeout is not None and gen_timeout != "":
        try:
            model["generationTimeoutSeconds"] = float(gen_timeout)
        except (TypeError, ValueError):
            pass

    default_size = raw.get("defaultSize")
    if default_size is None:
        default_size = raw.get("default_size")
    if isinstance(default_size, str) and default_size.strip():
        normalized_size = normalize_image_size(default_size)
        if normalized_size:
            model["defaultSize"] = normalized_size
    return model


def normalize_image_size(raw: object) -> Optional[str]:
    """Return a supported WxH size string, or None when invalid."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    size = raw.strip().lower()
    return size if size in ALLOWED_IMAGE_SIZES else None


def resolve_image_size(
    *,
    explicit_size: Optional[str] = None,
    media_model: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve ComfyUI generation size (explicit → model → global → 512x512)."""
    explicit = normalize_image_size(explicit_size or "")
    if explicit:
        return explicit
    if media_model is not None:
        model_size = normalize_image_size(media_model.get("defaultSize") or "")
        if model_size:
            return model_size
    cfg = _load_settings(settings)
    global_size = normalize_image_size(cfg.get("comfyui_default_image_size") or "")
    if global_size:
        return global_size
    return DEFAULT_COMFYUI_IMAGE_SIZE


def resolve_generation_timeout(
    model: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> float:
    """Resolve the ComfyUI generation poll budget (model-specific wins over global).

    Order: ``media_models[].generationTimeoutSeconds`` →
    ``comfyui_generation_timeout_seconds`` → provider default. Values are clamped
    to 30–900 seconds in ``services.media.comfyui.coerce_generation_timeout``.
    """
    from services.media.comfyui import DEFAULT_GENERATE_TIMEOUT, coerce_generation_timeout

    if model is not None:
        model_raw = model.get("generationTimeoutSeconds")
        if model_raw is not None and model_raw != "":
            return coerce_generation_timeout(model_raw)
    cfg = _load_settings(settings)
    global_raw = cfg.get("comfyui_generation_timeout_seconds")
    if global_raw is not None and global_raw != "":
        return coerce_generation_timeout(global_raw)
    return float(DEFAULT_GENERATE_TIMEOUT)


def load_media_models(
    owner: str = "", settings: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Load and normalize all registry entries.

    Duplicate ids are dropped (first occurrence wins). ``owner`` is accepted
    for forward-compatibility with per-user scoping but is not used yet (the
    registry is global for the MVP).
    """
    cfg = _load_settings(settings)
    raw_list = cfg.get("media_models")
    if not isinstance(raw_list, list):
        return []

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_list:
        model = normalize_model(raw, settings=cfg)
        if model is None:
            continue
        if model["id"] in seen:
            logger.warning("media_registry: duplicate media model id %r ignored", model["id"])
            continue
        seen.add(model["id"])
        out.append(model)
    return out


# ── Listing & resolution ──

def list_enabled_models(
    kind: str = "image", owner: str = "", settings: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Return enabled registry models, optionally filtered by ``kind``.

    Pass ``kind=None`` (or "") to list enabled models of every kind.
    """
    models = load_media_models(owner=owner, settings=settings)
    return [m for m in models if m["enabled"] and (not kind or m["kind"] == kind)]


def get_model(
    model_id: str, owner: str = "", settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Return a single registry model by id (enabled or not), or None."""
    if not model_id:
        return None
    for m in load_media_models(owner=owner, settings=settings):
        if m["id"] == model_id:
            return m
    return None


def resolve_default_model(
    kind: str = "image", owner: str = "", settings: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Resolve the default *enabled* model for ``kind``.

    Resolution order (first match wins):
      1. The id named by the kind's default setting (e.g.
         ``default_image_media_model``), if that model is enabled.
      2. The first enabled model flagged ``isDefault``.
      3. The single enabled model, when exactly one exists.
    Returns ``None`` when no default can be resolved (zero, or ambiguous).
    """
    cfg = _load_settings(settings)
    enabled = list_enabled_models(kind=kind, owner=owner, settings=cfg)
    if not enabled:
        return None

    setting_key = _DEFAULT_MODEL_SETTING_BY_KIND.get(kind)
    if setting_key:
        preferred_id = cfg.get(setting_key)
        if isinstance(preferred_id, str) and preferred_id.strip():
            preferred_id = preferred_id.strip()
            for m in enabled:
                if m["id"] == preferred_id:
                    return m

    for m in enabled:
        if m["isDefault"]:
            return m

    if len(enabled) == 1:
        return enabled[0]

    return None


# ── Degraded-state contract (OQ-7) ──
#
# A single small shape, reused by list_media_models and generate_image (S4).
#   {
#     "ok": False,
#     "available": False,
#     "status": "<reason>",      # e.g. "no_models", "no_default"
#     "kind": "image",
#     "message": "<one-line summary>",
#     "checked": [ {"provider": str, "status": str}, ... ],
#     "next_steps": [ "<step>", ... ],
#     "detail": <optional extra context or None>
#   }

def degraded_state(
    status: str,
    *,
    kind: str = "image",
    message: str = "",
    checked: Optional[List[Dict[str, str]]] = None,
    next_steps: Optional[List[str]] = None,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a degraded-state response in the shared shape."""
    return {
        "ok": False,
        "available": False,
        "status": status,
        "kind": kind,
        "message": message,
        "checked": checked or [],
        "next_steps": next_steps or [],
        "detail": detail,
    }


def _checked_image_providers(settings: Dict[str, Any]) -> List[Dict[str, str]]:
    """Summarize which image providers appear configured (no network calls)."""
    comfy_url = settings.get("comfyui_endpoint_url")
    if isinstance(comfy_url, str) and comfy_url.strip():
        comfy_status = "configured (no enabled image model)"
    else:
        comfy_status = "not configured or unavailable"
    return [
        {"provider": "comfyui", "status": comfy_status},
        {"provider": "diffusers", "status": "not configured"},
        {"provider": "other image providers", "status": "not configured"},
    ]


def default_image_model_or_degraded(
    owner: str = "", settings: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Resolve the default image model, or build a degraded-state response.

    Returns ``(model, None)`` on success, or ``(None, degraded)`` when no
    image model can be used. Two degraded reasons:
      - ``no_models``  : no enabled image models are registered at all.
      - ``no_default`` : enabled image models exist but none could be chosen
                          as the default (ambiguous / none flagged).
    """
    cfg = _load_settings(settings)
    enabled = list_enabled_models(kind="image", owner=owner, settings=cfg)

    if not enabled:
        return None, degraded_state(
            "no_models",
            kind="image",
            message=(
                "Image generation is available as a tool, but no image model "
                "is currently configured."
            ),
            checked=_checked_image_providers(cfg),
            next_steps=[
                "Configure a local ComfyUI endpoint in settings.",
                "Register or select an image workflow/model.",
                "Run the provider probe again.",
            ],
        )

    model = resolve_default_model(kind="image", owner=owner, settings=cfg)
    if model is not None:
        return model, None

    checked = [
        {"provider": m["provider"], "status": f"enabled model '{m['id']}'"}
        for m in enabled
    ]
    return None, degraded_state(
        "no_default",
        kind="image",
        message=(
            "Image generation is available, but no default image model is set "
            "and the choice is ambiguous."
        ),
        checked=checked,
        next_steps=[
            "Mark one media model as default (\"isDefault\": true), or",
            "set \"default_image_media_model\" to one of the enabled model ids.",
        ],
    )


def image_generation_routable(
    owner: str = "",
    settings: Optional[Dict[str, Any]] = None,
    *,
    include_legacy: bool = True,
    include_db: bool = True,
) -> bool:
    """True when ``generate_image`` has a configured path (no network probing).

    Matches the pre-route gate for concrete creation prompts:
      1. resolvable default media-registry image model, or
      2. legacy ``image_model`` setting (when ``include_legacy``), or
      3. at least one enabled image-type ``ModelEndpoint`` in the DB
         (when ``include_db``).

    ``include_legacy`` / ``include_db`` default to True for runtime callers.
    Tests can disable them to assert the settings-only path without depending
    on the developer's local DB or legacy config.
    """
    cfg = _load_settings(settings)
    if resolve_default_model(kind="image", owner=owner, settings=cfg) is not None:
        return True
    if include_legacy:
        legacy = cfg.get("image_model")
        if isinstance(legacy, str) and legacy.strip():
            return True
    if include_db:
        try:
            from src.database import SessionLocal, ModelEndpoint
            from src.auth_helpers import owner_filter

            db = SessionLocal()
            try:
                q = db.query(ModelEndpoint).filter(
                    ModelEndpoint.is_enabled == True,
                    ModelEndpoint.model_type == "image",
                )
                if owner:
                    q = owner_filter(q, ModelEndpoint, owner)
                if q.first() is not None:
                    return True
            finally:
                db.close()
        except Exception:
            pass
    return False


# ── Presentation helpers ──

def to_public_dict(model: Dict[str, Any]) -> Dict[str, Any]:
    """Project a registry model to the fields safe to return to the agent/user.

    Omits ``endpointUrl`` and ``workflowPath`` to avoid leaking internal URLs
    or local filesystem paths.
    """
    return {k: model[k] for k in _PUBLIC_FIELDS if k in model}


def format_degraded_message(state: Dict[str, Any]) -> str:
    """Render a degraded-state response as the user-facing text block.

    Mirrors the format described in the build brief (summary + checked list +
    numbered next steps). Reused by list_media_models / generate_image (S4).
    """
    lines: List[str] = []
    if state.get("message"):
        lines.append(state["message"])

    checked = state.get("checked") or []
    if checked:
        lines.append("")
        lines.append("Checked:")
        for item in checked:
            provider = item.get("provider", "unknown")
            status = item.get("status", "")
            lines.append(f"- {provider}: {status}")

    steps = state.get("next_steps") or []
    if steps:
        lines.append("")
        lines.append("Next steps:")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")

    detail = state.get("detail")
    if detail:
        lines.append("")
        lines.append(str(detail))

    return "\n".join(lines)

"""Per-model generation settings for added (LLM) models.

An admin stores a Customization Entry per (endpoint, model) pair in
``MODEL_CUSTOMIZATIONS_FILE``. At call time this module classifies the chat URL
into a Provider Format, picks the fields that format supports, and supplies the
Effective Settings to the payload builders in ``src.llm_core``, the agent and
editor request paths, context discovery and session creation.

See ``openspec/changes/add-model-customization/design.md`` and ADR-0001/0002.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from src.constants import MODEL_CUSTOMIZATIONS_FILE
from src.model_profiles import supports_user_thinking_toggle

SETTING_NAMES = (
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "repeat_penalty",
    "num_ctx",
    "thinking",
)

STORE_PATH = MODEL_CUSTOMIZATIONS_FILE
STORE_VERSION = 1

logger = logging.getLogger(__name__)


def atomic_write_json(path, data, **kwargs):
    # Imported lazily: ``core/__init__`` imports ``src.llm_core``, which imports this module.
    from core.atomic_io import atomic_write_json as write
    return write(path, data, **kwargs)

EXCLUDED_PROVIDERS = frozenset({"anthropic", "chatgpt-subscription"})


def provider_format(url: str) -> str:
    """Classify a chat URL into its Provider Format (first match wins)."""
    # Lazy import: src.llm_core calls into this module, so a top-level import
    # would be circular.
    from src.llm_core import (
        _detect_provider,
        _is_ollama_native_url,
        _is_ollama_openai_compat_url,
        _is_self_hosted_openai_compatible,
    )

    if _detect_provider(url) in EXCLUDED_PROVIDERS:
        return "excluded"
    if _is_ollama_native_url(url):
        return "ollama-native"
    if _is_ollama_openai_compat_url(url):
        return "ollama-openai"
    if _is_self_hosted_openai_compatible(url):
        return "local-openai"
    return "hosted-openai"


_OPENAI_COMMON = frozenset({"temperature", "top_p", "presence_penalty", "thinking"})

SUPPORTED_FIELDS = {
    "excluded": frozenset(),
    "ollama-native": frozenset(SETTING_NAMES),
    "ollama-openai": _OPENAI_COMMON,
    "local-openai": _OPENAI_COMMON | {"top_k", "min_p", "repeat_penalty"},
    "hosted-openai": _OPENAI_COMMON,
}


def supported_fields(fmt: str) -> frozenset:
    """Settings a Provider Format accepts (empty for unknown formats)."""
    return SUPPORTED_FIELDS.get(fmt, frozenset())


def supported_fields_for(url: str, model: str) -> frozenset:
    """Settings this model accepts on this URL; thinking needs the user toggle."""
    fields = supported_fields(provider_format(url))
    if not supports_user_thinking_toggle(model):
        fields = fields - {"thinking"}
    return fields


# (kind, low, high, low_exclusive) per numeric setting, from the spec's table.
_RANGES = {
    "temperature": ("number", 0, 2, False),
    "top_p": ("number", 0, 1, False),
    "top_k": ("integer", 0, 1000, False),
    "min_p": ("number", 0, 1, False),
    "presence_penalty": ("number", -2, 2, False),
    "repeat_penalty": ("number", 0, 2, True),
    "num_ctx": ("integer", 256, 1048576, False),
}
THINKING_VALUES = ("on", "off")


def _check_value(name: str, value):
    if name == "thinking":
        if value not in THINKING_VALUES:
            raise ValueError(f"{name} must be 'on' or 'off'")
        return value
    kind, low, high, low_exclusive = _RANGES[name]
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not is_number or (kind == "integer" and value != int(value)):
        raise ValueError(f"{name} must be {'an integer' if kind == 'integer' else 'a number'}")
    too_low = value <= low if low_exclusive else value < low
    if too_low or value > high:
        bound = f"above {low}" if low_exclusive else f"from {low}"
        raise ValueError(f"{name} must be {bound} to {high}")
    return int(value) if kind == "integer" else value


def validate_settings(settings: dict, supported) -> dict:
    """Return the non-blank settings, or raise ValueError naming the bad field.

    ``supported`` is the set of settings the target model accepts.
    """
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    cleaned = {}
    for name, value in settings.items():
        if value is None or value == "":
            continue
        if name not in SETTING_NAMES:
            raise ValueError(f"{name} is not a known setting")
        if name not in supported:
            raise ValueError(f"{name} is not supported for this model")
        cleaned[name] = _check_value(name, value)
    return cleaned


def _read_store() -> list:
    """Parse the store; a missing, unreadable or malformed file means no entries."""
    try:
        with open(STORE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable model customizations file %s: %s", STORE_PATH, exc)
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        logger.warning("Ignoring malformed model customizations file %s", STORE_PATH)
        return []
    return [
        e for e in entries
        if isinstance(e, dict)
        and isinstance(e.get("model"), str)
        and isinstance(e.get("endpoint_id"), str)
        and isinstance(e.get("settings"), dict)
    ]


_cache = {"stamp": None, "entries": []}
_save_lock = threading.Lock()


def _reset_cache() -> None:
    _cache["stamp"] = None
    _cache["entries"] = []


def load_entries() -> list:
    """All stored Customization Entries. Never creates or modifies the file.

    Re-parsed only when the file's (mtime, size) changes, so the per-call cost
    on the LLM hot path is one ``os.stat``.
    """
    try:
        st = os.stat(STORE_PATH)
        stamp = (STORE_PATH, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (STORE_PATH, None, None)
    if stamp != _cache["stamp"]:
        _cache["entries"] = _read_store() if stamp[1] is not None else []
        _cache["stamp"] = stamp
    return _cache["entries"]


def save_entry(endpoint_id: str, base_url: str, model: str, settings: dict):
    """Upsert the entry for (endpoint_id, model) with already-validated settings.

    Empty ``settings`` removes the entry. Returns the saved entry, or None
    when it was removed.
    """
    entry = None
    if settings:
        entry = {
            "model": model,
            "endpoint_id": endpoint_id,
            "base_url": base_url,
            "settings": dict(settings),
            "edited_at": datetime.now(timezone.utc).isoformat(),
        }
    with _save_lock:
        entries = [
            e for e in _read_store()
            if not (e["endpoint_id"] == endpoint_id and e["model"] == model)
        ]
        if entry:
            entries.append(entry)
        atomic_write_json(STORE_PATH, {"version": STORE_VERSION, "entries": entries}, indent=2)
        _reset_cache()
    return entry


# ── Matching a call to an entry through live registered endpoints ──────────

ENDPOINT_CACHE_TTL = 5.0
_monotonic = time.monotonic
_endpoint_cache = {"at": None, "views": []}
_endpoint_lock = threading.Lock()


def _session_factory():
    from core.database import SessionLocal
    return SessionLocal


def _reset_endpoint_cache() -> None:
    _endpoint_cache["at"] = None
    _endpoint_cache["views"] = []


def _endpoint_bases(base_url: str) -> set:
    """Every base a chat URL for this endpoint can normalise to."""
    from src import endpoint_resolver as er

    base = er.normalize_base(base_url)
    bases = {base}
    for derive in (er._rewrite_docker_host_for_native_runtime, er.build_chat_url):
        try:
            bases.add(er.normalize_base(derive(base)))
        except Exception:
            continue
    return bases


def _endpoint_views() -> list:
    """Enabled endpoints as (id, bases, enabled models), cached for a short TTL."""
    now = _monotonic()
    with _endpoint_lock:
        at = _endpoint_cache["at"]
        if at is not None and now - at < ENDPOINT_CACHE_TTL:
            return _endpoint_cache["views"]
        from core.database import ModelEndpoint
        from src.endpoint_resolver import _endpoint_enabled_models

        views = []
        try:
            with _session_factory()() as db:
                rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True)).all()
                for ep in rows:
                    views.append({
                        "id": ep.id,
                        "bases": _endpoint_bases(ep.base_url or ""),
                        "models": set(_endpoint_enabled_models(ep)),
                    })
        except Exception as exc:
            logger.warning("Model customization could not read endpoints: %s", exc)
            views = []
        _endpoint_cache["views"] = views
        _endpoint_cache["at"] = now
        return views


def _url_matches(url: str, bases: set) -> bool:
    from src.endpoint_resolver import same_endpoint_base
    return any(same_endpoint_base(url, base) for base in bases)


def _candidates(url: str, model: str) -> list:
    """Entries for ``model`` whose live endpoint serves ``url``."""
    entries = [e for e in load_entries() if e["model"] == model]
    if not entries:
        return []
    views = {v["id"]: v for v in _endpoint_views()}
    found = []
    for e in entries:
        view = views.get(e["endpoint_id"])
        if view and model in view["models"] and _url_matches(url, view["bases"]):
            found.append(e)
    return found


def lookup(url: str, model: str):
    """The stored settings that apply to a call, or None.

    None when the URL's Provider Format is excluded, no live endpoint serving
    the URL holds an entry for the model, or more than one does (ambiguous).
    """
    if not url or not model or not load_entries():
        return None
    if provider_format(url) == "excluded":
        return None
    found = _candidates(url, model)
    if len(found) > 1:
        logger.debug("Model customization for %s at %s is ambiguous across endpoints %s",
                     model, url, [e["endpoint_id"] for e in found])
        return None
    return dict(found[0]["settings"]) if found else None


def is_ambiguous(endpoint_id: str, model: str) -> bool:
    """True when another live endpoint sharing this endpoint's base holds an entry for ``model``."""
    view = next((v for v in _endpoint_views() if v["id"] == endpoint_id), None)
    if view is None:
        return False
    others = 0
    for e in load_entries():
        if e["model"] != model or e["endpoint_id"] == endpoint_id:
            continue
        other = next((v for v in _endpoint_views() if v["id"] == e["endpoint_id"]), None)
        if other and other["bases"] & view["bases"]:
            others += 1
    return others > 0


# ── Pure call-time helpers ──────────────────────────────────────────────────

# Payload key(s) per sampling setting. temperature, num_ctx and thinking are
# applied elsewhere (temperature resolution, context budgeting, session seed).
_PAYLOAD_KEYS = {
    "top_p": ("top_p",),
    "top_k": ("top_k",),
    "min_p": ("min_p",),
    "presence_penalty": ("presence_penalty",),
    "repeat_penalty": ("repeat_penalty", "repetition_penalty"),
}
_OLLAMA_NATIVE_KEYS = dict(_PAYLOAD_KEYS, repeat_penalty=("repeat_penalty",))


def pick_temperature(explicit, settings, fmt: str) -> float:
    """Explicit Override > per-model setting (when supported) > built-in default."""
    from src.llm_core import LLMConfig

    if explicit is not None:
        return explicit
    if settings and "temperature" in supported_fields(fmt) and "temperature" in settings:
        return settings["temperature"]
    return LLMConfig.DEFAULT_TEMPERATURE


def inject(payload: dict, fmt: str, settings) -> dict:
    """Write supported sampling settings into ``payload`` (in place) and return it."""
    if not settings or fmt == "excluded":
        return payload
    supported = supported_fields(fmt)
    keys = _OLLAMA_NATIVE_KEYS if fmt == "ollama-native" else _PAYLOAD_KEYS
    target = payload.setdefault("options", {}) if fmt == "ollama-native" else payload
    for name, payload_keys in keys.items():
        if name in supported and name in settings:
            for key in payload_keys:
                target[key] = settings[name]
    return payload


# ── I/O wrappers used by llm_core, the agent/editor paths, context and sessions

def resolve_temperature(url: str, model: str, explicit):
    return pick_temperature(explicit, lookup(url, model), provider_format(url))


def apply_sampling(payload: dict, url: str, model: str) -> dict:
    return inject(payload, provider_format(url), lookup(url, model))


def cache_key_settings(url: str, model: str) -> dict:
    settings = lookup(url, model) or {}
    return dict(sorted(settings.items()))


def context_window_override(url: str, model: str):
    if provider_format(url) != "ollama-native":
        return None
    return (lookup(url, model) or {}).get("num_ctx")


def seed_thinking_mode(url: str, model: str):
    if "thinking" not in supported_fields_for(url, model):
        return None
    return (lookup(url, model) or {}).get("thinking")


# ── Admin read/write operations (routes/model_customization) ────────────────

def _endpoint_chat_url(base_url: str) -> str:
    from src import endpoint_resolver as er
    try:
        return er.build_chat_url(base_url)
    except Exception:
        return er.normalize_base(base_url)


def _ordered(fields) -> list:
    return [name for name in SETTING_NAMES if name in fields]


def cascade() -> list:
    """Endpoint → model listing for the settings panel. Never includes API keys.

    Only enabled, non-excluded LLM endpoints and their enabled models; entries
    whose endpoint or model is gone are left in the file but not listed.
    """
    from core.database import ModelEndpoint
    from src.endpoint_resolver import _endpoint_enabled_models

    stored = {(e["endpoint_id"], e["model"]): e["settings"] for e in load_entries()}
    listing = []
    with _session_factory()() as db:
        rows = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled.is_(True)).all()
        for ep in rows:
            if (ep.model_type or "llm") != "llm":
                continue
            chat_url = _endpoint_chat_url(ep.base_url or "")
            fmt = provider_format(chat_url)
            if fmt == "excluded":
                continue
            models = [
                {
                    "model": model,
                    "format": fmt,
                    "supported": _ordered(supported_fields_for(chat_url, model)),
                    "settings": dict(stored.get((ep.id, model), {})),
                }
                for model in _endpoint_enabled_models(ep)
            ]
            listing.append({"id": ep.id, "name": ep.name, "models": models})
    return listing


def save_for_endpoint(endpoint_id: str, model: str, settings: dict) -> dict:
    """Validate and upsert one entry. Raises ValueError with a field-specific message.

    Returns ``{"entry": entry_or_None, "warning": str_or_None}``; the warning is
    set when another endpoint on the same server holds an entry for this model,
    so neither entry will apply.
    """
    from core.database import ModelEndpoint

    if not isinstance(model, str) or not model.strip():
        raise ValueError("model is required")
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    with _session_factory()() as db:
        ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        base_url = ep.base_url if ep else None
    if not base_url:
        raise ValueError("endpoint_id does not name a registered endpoint")
    chat_url = _endpoint_chat_url(base_url)
    if provider_format(chat_url) == "excluded":
        raise ValueError("this endpoint's provider does not accept model customizations")
    cleaned = validate_settings(settings, supported_fields_for(chat_url, model))
    entry = save_entry(endpoint_id, base_url, model, cleaned)
    _reset_endpoint_cache()
    warning = None
    if entry and is_ambiguous(endpoint_id, model):
        warning = ("This setting is ambiguous: another endpoint on the same server also "
                   "customizes this model, so neither setting will apply.")
    return {"entry": entry, "warning": warning}

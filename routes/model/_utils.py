# routes/model_routes.py
"""Routes for model and provider management."""
import os
import re
import uuid
import json
import hashlib
import ipaddress
import socket
import time as _time
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlunparse
from fastapi import APIRouter, HTTPException, Form, Query, Body, Request, Response
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from core.database import SessionLocal, ModelEndpoint, Session as DbSession
from core.log_safety import redact_url as _redact_url_for_log
from core.middleware import require_admin
from src.constants import COOKBOOK_STATE_FILE
from src.llm_core import _detect_provider, _host_match, ANTHROPIC_MODELS
from src.tls_overrides import llm_verify
from src.settings import load_settings as _load_settings, save_settings as _save_settings
from src.endpoint_resolver import (
    normalize_base as _normalize_base,
    build_chat_url,
    build_models_url,
    build_headers,
)
from src.auth_helpers import _auth_disabled, effective_user, owner_filter

logger = logging.getLogger(__name__)

_SPEECH_ENDPOINT_SETTINGS = (
    ("tts_provider", "tts_model", "tts-1", "Text to Speech"),
    ("stt_provider", "stt_model", "base", "Speech to Text"),
)

_ENDPOINT_SETTING_FIELDS = {
    "default_endpoint_id":  ("default_model",  "Default Model"),
    "utility_endpoint_id":  ("utility_model",   "Utility Model"),
    "research_endpoint_id": ("research_model",  "Deep Research"),
    "task_endpoint_id":     ("task_model",       "Background Tasks"),
}

_ENDPOINT_FALLBACK_FIELDS = {
    "default_model_fallbacks": "Default Model Fallbacks",
    "utility_model_fallbacks": "Utility Model Fallbacks",
    "vision_model_fallbacks":  "Vision Model Fallbacks",
}


def _speech_settings_using_endpoint(settings: dict, ep_id: str) -> list:
    """Return speech settings that reference a model endpoint."""
    endpoint_ref = f"endpoint:{ep_id}"
    return [
        label
        for provider_key, _, _, label in _SPEECH_ENDPOINT_SETTINGS
        if (settings.get(provider_key) or "") == endpoint_ref
    ]


def _clear_speech_settings_for_endpoint(settings: dict, ep_id: str) -> list:
    """Reset speech settings that reference a model endpoint."""
    endpoint_ref = f"endpoint:{ep_id}"
    cleared = []
    for provider_key, model_key, default_model, label in _SPEECH_ENDPOINT_SETTINGS:
        if (settings.get(provider_key) or "") == endpoint_ref:
            settings[provider_key] = "disabled"
            settings[model_key] = default_model
            cleared.append(label)
    return cleared


def _endpoint_settings_using_endpoint(settings: dict, ep_id: str, *, include_speech: bool = False) -> list:
    """Return labels for settings and fallback chains that reference an endpoint."""
    affected = []
    for ep_key, (_, label) in _ENDPOINT_SETTING_FIELDS.items():
        if (settings.get(ep_key) or "") == ep_id:
            affected.append(label)
    for fallback_key, label in _ENDPOINT_FALLBACK_FIELDS.items():
        chain = settings.get(fallback_key) or []
        if any(isinstance(entry, dict) and (entry.get("endpoint_id") or "") == ep_id for entry in chain):
            affected.append(label)
    if include_speech:
        affected.extend(_speech_settings_using_endpoint(settings, ep_id))
    return affected


def _clear_endpoint_settings_for_endpoint(settings: dict, ep_id: str, *, include_speech: bool = False) -> list:
    """Remove an endpoint from direct settings and model fallback chains."""
    cleared = []
    for ep_key, (model_key, label) in _ENDPOINT_SETTING_FIELDS.items():
        if (settings.get(ep_key) or "") == ep_id:
            settings[ep_key] = ""
            settings[model_key] = ""
            cleared.append(label)
    for fallback_key, label in _ENDPOINT_FALLBACK_FIELDS.items():
        chain = settings.get(fallback_key)
        if not isinstance(chain, list):
            continue
        kept = [
            entry for entry in chain
            if not (isinstance(entry, dict) and (entry.get("endpoint_id") or "") == ep_id)
        ]
        if len(kept) != len(chain):
            settings[fallback_key] = kept
            cleared.append(label)
    if include_speech:
        cleared.extend(_clear_speech_settings_for_endpoint(settings, ep_id))
    return cleared


_COOKBOOK_ACTIVE_SERVE_STATUSES = {
    "starting", "loading", "ready", "running", "restarting",
}


def _active_cookbook_endpoint_ids() -> set[str]:
    """Endpoint IDs owned by active Cookbook serve tasks.

    Cookbook auto-registers endpoints with ids like ``local-*``. Those rows are
    managed lifecycle state, not durable user configuration. If a tmux stream is
    stopped or an old task lingers, the row must stop participating in model
    selection and defaults.
    """
    try:
        if not os.path.exists(COOKBOOK_STATE_FILE):
            return set()
        with open(COOKBOOK_STATE_FILE, "r", encoding="utf-8") as fh:
            raw = fh.read()
        state = json.loads(raw)
    except Exception:
        return set()
    out: set[str] = set()
    for task in state.get("tasks") or []:
        if not isinstance(task, dict) or task.get("type") != "serve":
            continue
        if str(task.get("status") or "").lower() not in _COOKBOOK_ACTIVE_SERVE_STATUSES:
            continue
        ep_id = task.get("_endpointId") or task.get("endpointId") or task.get("endpoint_id")
        if ep_id:
            out.add(str(ep_id))
    return out


def _disable_stale_cookbook_local_endpoints(db) -> int:
    """Disable enabled cookbook endpoints whose serve task is no longer active."""
    active_ids = _active_cookbook_endpoint_ids()
    if not active_ids:
        return 0
    stale = (
        db.query(ModelEndpoint)
        .filter(ModelEndpoint.is_enabled == True)  # noqa: E712
        .filter(ModelEndpoint.id.like("local-%"))
        .filter(~ModelEndpoint.id.in_(active_ids))
        .all()
    )
    if not stale:
        return 0
    settings = _load_settings()
    touched_settings = False
    for ep in stale:
        ep.is_enabled = False
        ep.model_refresh_mode = "disabled"
        if _clear_endpoint_settings_for_endpoint(settings, ep.id):
            touched_settings = True
        logger.info("Disabled stale Cookbook endpoint %s (%s @ %s)", ep.id, ep.name, ep.base_url)
    if touched_settings:
        _save_settings(settings)
    db.commit()
    return len(stale)


def _clear_user_pref_endpoint_refs(all_prefs: dict, ep_id: str) -> int:
    """Remove endpoint references from scoped or legacy-flat user preferences."""
    if not isinstance(all_prefs, dict):
        return 0
    users = all_prefs.get("_users")
    pref_sets = users.values() if isinstance(users, dict) else [all_prefs]
    cleared_users = 0
    for prefs in pref_sets:
        if isinstance(prefs, dict) and _clear_endpoint_settings_for_endpoint(prefs, ep_id):
            cleared_users += 1
    return cleared_users


def _endpoint_visible_model_ids(ep: Any) -> List[str]:
    """Known visible model ids for an endpoint, including pinned/manual ids."""
    if ep is None:
        return []
    return _visible_models(
        getattr(ep, "cached_models", None),
        getattr(ep, "hidden_models", None),
        getattr(ep, "pinned_models", None),
    )


def _default_endpoint_needs_assignment(
    current_default_id: str,
    enabled_endpoint_ids,
    *,
    current_default_endpoint: Any = None,
    current_default_model: str = "",
) -> bool:
    """Whether the global default chat endpoint should be (re)assigned.

    True when nothing is configured yet, or the configured default no longer
    resolves to an enabled endpoint (e.g. the user disabled it). Without the
    second case, adding a new endpoint after disabling the previous default
    leaves `default_endpoint_id` pointing at the disabled endpoint, so features
    that read the raw setting (Memory → Tidy) fail with "No default model
    configured" even though an enabled endpoint exists. See #3586.
    """
    if not current_default_id:
        return True
    if current_default_id not in enabled_endpoint_ids:
        return True
    if current_default_endpoint is None:
        return False
    if not (current_default_model or "").strip():
        return True
    visible = _endpoint_visible_model_ids(current_default_endpoint)
    return bool(visible and current_default_model not in visible)


# Loopback hosts a user might type for a local model server (LM Studio,
# llama.cpp, vLLM, …). Inside Docker these point at the *container*, not the
# host the server actually runs on.
_ANY_BIND_HOSTS = {"0.0.0.0", "::"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", *_ANY_BIND_HOSTS}


def _docker_host_gateway_reachable() -> bool:
    """True when we run inside a container whose host is reachable via
    ``host.docker.internal`` (compose maps it to ``host-gateway``). Returns
    False on native installs and on container setups without the mapping, so
    the loopback rewrite below stays a no-op there."""
    in_container = os.path.exists("/.dockerenv")
    if not in_container:
        try:
            with open("/proc/1/cgroup", encoding="utf-8") as fh:
                in_container = any(t in fh.read() for t in ("docker", "containerd", "kubepods"))
        except OSError:
            in_container = False
    if not in_container:
        return False
    try:
        socket.getaddrinfo("host.docker.internal", None)
        return True
    except OSError:
        return False

def _container_loopback_reachable(base_url: str, timeout: float = 0.2) -> bool:
    """True when the requested loopback host:port is already reachable from
    inside the current container.

    This distinguishes "a model server running alongside Odysseus in the same
    container" from "a model server running on the Docker host". Only the
    latter should be rewritten to host.docker.internal.
    """
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if host not in _LOOPBACK_HOSTS or not port:
        return False
    probe_host = "::1" if host == "::1" else "127.0.0.1"
    family = socket.AF_INET6 if probe_host == "::1" else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((probe_host, port))
        return True
    except OSError:
        return False


def _rewrite_loopback_for_docker(base_url: str, *, container_local: bool = False) -> str:
    """Rewrite a loopback model-endpoint URL to ``host.docker.internal`` when
    running in Docker. A URL like ``http://localhost:1234/v1`` (the LM Studio
    default) otherwise targets the Odysseus container itself, so the probe gets
    a connection error and the endpoint is rejected with a misleading "No
    models found for that provider/key".

    Cookbook local serves are the opposite case: Odysseus started the model
    server inside the same container/process environment, so the saved endpoint
    must remain container-local. In that mode, normalize a bind address such as
    0.0.0.0 to a connectable loopback host, but do not jump to the Docker host.
    """
    try:
        parsed = urlparse(base_url)
    except Exception:
        return base_url
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return base_url
    if container_local:
        if host in _ANY_BIND_HOSTS:
            netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
            return urlunparse(parsed._replace(netloc=netloc))
        return base_url
    if host in _ANY_BIND_HOSTS and not _docker_host_gateway_reachable():
        netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
        return urlunparse(parsed._replace(netloc=netloc))
    if _container_loopback_reachable(base_url):
        return base_url
    if not _docker_host_gateway_reachable():
        return base_url
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc))


# ── Curated model lists per provider ──
# For cloud providers that return 100+ models, only show these by default.
# A model ID matches if it starts with or equals a curated entry.
_PROVIDER_CURATED = {
    "openai": [
        "gpt-5.2", "gpt-5.2-pro", "gpt-5", "gpt-5-pro", "gpt-5-mini", "gpt-5-nano",
        "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-image-1.5", "gpt-image-1", "dall-e-3", "tts-1", "whisper-1",
    ],
    "anthropic": [
        "claude-sonnet-4", "claude-opus-4", "claude-haiku-4",
        "claude-sonnet-4-5", "claude-haiku-3-5",
    ],
    "zai": [
        "glm-5", "glm-5.1", "glm-5v-turbo", "glm-4.7", "glm-4.7-flash",
        "glm-4.6", "glm-4.6v",
        "glm-4.5", "glm-4.5v", "glm-4.5-air", "glm-4.5-flash",
    ],
    "zai-coding": [
        "glm-5.1", "glm-5v-turbo", "glm-5-turbo", "glm-4.7", "glm-4.5-air",
    ],
    "kimi-code": [
        "kimi-for-coding",
    ],
    "deepseek": [
        "deepseek-v4-flash", "deepseek-v4-pro",
        "deepseek-chat", "deepseek-reasoner",
    ],
    "groq": [
        "openai/gpt-oss-120b", "openai/gpt-oss-20b",
        "groq/compound", "groq/compound-mini",
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "llama-4-scout-17b-16e-instruct",
        "llama-4-maverick-17b-128e-instruct",
    ],
    "mistral": [
        "mistral-large-latest", "mistral-medium-latest", "mistral-small-latest",
    ],
    "together": [
        "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ],
    "fireworks": [
        "accounts/fireworks/models/llama4-scout-instruct-basic",
        "accounts/fireworks/models/llama4-maverick-instruct-basic",
        "accounts/fireworks/models/deepseek-r1",
    ],
    "google": [
        "gemini-3.5", "gemini-3.1", "gemini-3",
        "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash",
    ],
    "xai": [
        "grok-4.3", "grok-4", "grok-4-fast", "grok-3", "grok-3-fast",
    ],
}

# Map hostnames → curated-list keys for providers whose _detect_provider()
# returns a generic value (e.g. "openai") but deserve their own curated list.
# "openrouter" is a sentinel meaning "no curation — show all models as curated".
# Entries are matched by hostname equality or subdomain suffix (via _host_match),
# so e.g. "deepseek.com" covers api.deepseek.com without matching the substring
# inside an unrelated URL.
_HOST_TO_CURATED = (
    ("z.ai", "zai"),
    ("deepseek.com", "deepseek"),
    ("groq.com", "groq"),
    ("mistral.ai", "mistral"),
    ("together.xyz", "together"),
    ("together.ai", "together"),
    ("fireworks.ai", "fireworks"),
    ("googleapis.com", "google"),
    ("x.ai", "xai"),
    ("nvidia.com", "nvidia"),
    ("openrouter.ai", "openrouter"),
    ("ollama.com", "ollama"),
)


def _match_provider_curated(base_url: str, provider: str) -> str:
    """Return the curated-list key for a given endpoint.

    Checks path-based overrides first (for hosts serving multiple plans),
    then matches the base URL's hostname against known providers, and
    finally falls back to the raw provider string from _detect_provider().
    """
    # Path-based overrides for hosts that serve multiple curated lists.
    parsed = urlparse(base_url)
    if _host_match(base_url, "z.ai") and "/api/coding" in (parsed.path or ""):
        return "zai-coding"
    if _host_match(base_url, "kimi.com") and "/coding" in (parsed.path or ""):
        return "kimi-code"
    for domain, key in _HOST_TO_CURATED:
        if _host_match(base_url, domain):
            return key
    return provider


def _curate_models(model_ids, provider):
    """Partition model_ids into (curated, extra) based on provider's curated list.
    If no curated list exists for the provider, returns (model_ids, [])."""
    if provider == "openrouter":
        return model_ids, []
    curated_list = _PROVIDER_CURATED.get(provider)
    if not curated_list:
        return model_ids, []
    curated = []
    extra = []
    def _best_match_idx(mid):
        """Return index of the longest matching curated entry, or -1."""
        best_i, best_len = -1, 0
        for i, entry in enumerate(curated_list):
            if (mid == entry or mid.startswith(entry)) and len(entry) > best_len:
                best_i, best_len = i, len(entry)
        return best_i

    for mid in model_ids:
        if _best_match_idx(mid) >= 0:
            curated.append(mid)
        else:
            extra.append(mid)
    # Sort curated models by their priority order in the curated list
    curated.sort(key=lambda mid: (_best_match_idx(mid), mid))
    return curated, extra


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


_ENDPOINT_KINDS = {"auto", "local", "api", "proxy"}
_REFRESH_MODES = {"auto", "manual", "disabled"}


def _normalize_endpoint_kind(value: Any) -> str:
    kind = str(value or "auto").strip().lower()
    return kind if kind in _ENDPOINT_KINDS else "auto"


def _normalize_refresh_mode(value: Any, endpoint_kind: str = "auto") -> str:
    mode = str(value or "").strip().lower()
    kind = _normalize_endpoint_kind(endpoint_kind)
    if mode in ("manual", "disabled"):
        return mode
    if mode == "auto" and kind != "proxy":
        return "auto"
    # Proxies default to manual cached-first behavior. Normal local/API
    # endpoints keep automatic bounded refreshes.
    return "manual" if kind == "proxy" else "auto"


def _endpoint_kind(ep: Any) -> str:
    return _normalize_endpoint_kind(getattr(ep, "endpoint_kind", None))


def _endpoint_refresh_mode(ep: Any, endpoint_kind: str | None = None) -> str:
    return _normalize_refresh_mode(getattr(ep, "model_refresh_mode", None), endpoint_kind or _endpoint_kind(ep))


def _endpoint_refresh_interval(ep: Any, category: str) -> float:
    raw = getattr(ep, "model_refresh_interval", None)
    try:
        val = int(raw) if raw is not None else 0
    except Exception:
        val = 0
    if val > 0:
        return float(max(30, val))
    return 60.0 if category == "local" else 3600.0


def _endpoint_refresh_timeout(ep: Any, category: str) -> float:
    raw = getattr(ep, "model_refresh_timeout", None)
    try:
        val = int(raw) if raw is not None else 0
    except Exception:
        val = 0
    if val > 0:
        return float(max(1, min(60, val)))
    # llama.cpp and other local OpenAI-compatible servers can block briefly
    # while warming/loading. A 2s local timeout makes working endpoints flicker
    # offline before /v1/models is ready.
    return 10.0 if category == "local" else 2.0


def _manual_refresh_timeout(ep: Any, category: str, requested: Any = None) -> float:
    """Timeout for explicit user-triggered model-list refreshes.

    Background refreshes stay short. A manual refresh is the one path where a
    large proxy may legitimately need 15-30s to aggregate its catalog.
    """
    requested_val = _parse_positive_int(requested, minimum=1, maximum=60)
    if requested_val is not None:
        return float(requested_val)
    stored = _parse_positive_int(getattr(ep, "model_refresh_timeout", None), minimum=1, maximum=60)
    if category == "local":
        return float(stored) if stored is not None else _endpoint_refresh_timeout(ep, category)
    return float(max(stored or 30, 30))


def _parse_model_list(raw: Any) -> List[str]:
    """Return a sanitized list of model ids from JSON/list/comma text."""
    if raw is None:
        return []
    value = raw
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                value = parsed
            else:
                value = re.split(r"[\n,]+", text)
        except Exception:
            value = re.split(r"[\n,]+", text)
    if not isinstance(value, list):
        return []
    out = []
    seen = set()
    for item in value:
        mid = str(item or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


def _parse_positive_int(raw: Any, *, minimum: int = 1, maximum: int = 86400) -> Optional[int]:
    try:
        val = int(str(raw).strip())
    except Exception:
        return None
    if val < minimum:
        return None
    return min(val, maximum)


def _explicit_model_list_timeout(base_url: str, endpoint_kind: str = "auto", requested: Any = None) -> float:
    """Timeout for explicit user-triggered model-list fetches during setup."""
    requested_val = _parse_positive_int(requested, minimum=1, maximum=60)
    if requested_val is not None:
        return float(requested_val)
    kind = _normalize_endpoint_kind(endpoint_kind)
    category = _classify_endpoint(base_url, kind)
    if kind in ("api", "proxy") or category == "api":
        return 30.0
    return 15.0 if category == "local" else (3.0 if _is_ollama_base(base_url) else 2.0)


def _cached_model_ids(ep: Any) -> List[str]:
    return _parse_model_list(getattr(ep, "cached_models", None))


def _hidden_model_ids(ep: Any) -> set:
    return set(_parse_model_list(getattr(ep, "hidden_models", None)))


def _is_ollama_base(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        return parsed.port == 11434 or "ollama" in host
    except Exception:
        return "ollama" in (base_url or "").lower()


# Prefixes/substrings for models that are NOT chat-completions-capable
_NON_CHAT_PREFIXES = (
    "dall-e", "tts-", "whisper", "text-embedding", "embedding",
    "davinci", "babbage", "moderation", "omni-moderation",
    "sora", "gpt-image", "chatgpt-image",
    # embedding / retrieval / non-chat models (common across providers)
    "snowflake/arctic-embed", "nvidia/nv-embed", "embed",
)
_NON_CHAT_CONTAINS = (
    "-realtime", "-transcribe", "-tts", "-codex",
    "codex-", "content-safety", "-safety", "-reward", "nvclip",
    "kosmos", "fuyu", "deplot", "vila", "neva",
    "gliner", "riva", "-parse", "-embedqa", "-nemoretriever",
    "topic-control", "calibration",
    "ai-synthetic-video", "cosmos-reason2",
    "bge", "llama-guard",
)
_NON_CHAT_EXACT_PREFIXES = (
    "gpt-audio",  # gpt-audio, gpt-audio-mini etc. (not gpt-4o-audio-preview which is chat)
    "gpt-3.5-turbo-instruct",  # legacy OpenAI completions model
)


def _is_chat_model(model_id: str) -> bool:
    """Return True if the model ID looks like a chat/completions-capable model."""
    if not isinstance(model_id, str):
        # Non-compliant upstreams can return non-string IDs (e.g. int/None);
        # treat them as chat-capable rather than crashing on .lower().
        return True
    mid = model_id.lower()
    for prefix in _NON_CHAT_PREFIXES:
        if mid.startswith(prefix):
            return False
    for prefix in _NON_CHAT_EXACT_PREFIXES:
        if mid.startswith(prefix):
            return False
    for substr in _NON_CHAT_CONTAINS:
        if substr in mid:
            return False
    return True


def _delete_orphaned_provider_auth(db, auth_id: Optional[str], exclude_ep_id: Optional[str] = None) -> bool:
    """Delete a ProviderAuthSession once no endpoint still references it."""
    if not auth_id:
        return False
    from core.database import ProviderAuthSession
    still_referenced = db.query(ModelEndpoint.id).filter(
        ModelEndpoint.provider_auth_id == auth_id,
        ModelEndpoint.id != exclude_ep_id,
    ).first()
    if still_referenced is not None:
        return False
    auth_row = db.query(ProviderAuthSession).filter(ProviderAuthSession.id == auth_id).first()
    if auth_row is None:
        return False
    db.delete(auth_row)
    return True


def _safe_detect_provider(base_url: str) -> str:
    """Best-effort provider detection that must not break endpoint probing."""
    try:
        return _detect_provider(base_url)
    except Exception as exc:
        logger.debug("Provider detection failed for %s: %s", base_url, exc)
        return ""


def _safe_build_models_url(base_url: str) -> str:
    """Build a /models URL without letting optional provider imports break probes."""
    try:
        return build_models_url(base_url)
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Model URL detection failed for %s: %s", base_url, exc)
        return f"{(base_url or '').rstrip('/')}/models"


def _safe_build_headers(api_key: Optional[str], base_url: str) -> dict:
    """Build auth headers without letting optional provider imports break probes."""
    try:
        return build_headers(api_key, base_url)
    except Exception as exc:
        logger.debug("Header detection failed for %s: %s", base_url, exc)
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _supports_ollama_unload(base_url: str) -> bool:
    """Return True for local/native Ollama endpoints that support keep_alive=0."""
    try:
        parsed = urlparse(_normalize_base(base_url))
        host = (parsed.hostname or "").lower()
        if host == "ollama.com" or host.endswith(".ollama.com"):
            return False
        return parsed.port == 11434 or "ollama" in host
    except Exception:
        return False


def _ollama_generate_url_for_unload(base_url: str) -> str:
    """Map an Ollama /v1 or /api base to native /api/generate."""
    from src.endpoint_resolver import resolve_url

    base = resolve_url(_normalize_base(base_url)).rstrip("/")
    if base.endswith("/api/generate"):
        return base
    if base.endswith("/generate") and base.rsplit("/", 1)[0].endswith("/api"):
        return base
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    if base.endswith("/api"):
        return base + "/generate"
    return base + "/api/generate"


def _ollama_root_url_for_unload(base_url: str) -> str:
    """Map an Ollama /v1 or /api base to the native Ollama server root."""
    from src.endpoint_resolver import resolve_url

    base = resolve_url(_normalize_base(base_url)).rstrip("/")
    for suffix in ("/api/generate", "/api/chat", "/api/tags", "/api/ps", "/v1", "/api"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base


def _ollama_loaded_models(base_url: str, api_key: Optional[str]) -> List[str]:
    """Return models currently resident in an Ollama runtime.

    Bulk unload must target loaded models, not every installed model, because
    unloading via /api/generate needs a model name and should not wake cold
    models just to unload them again.
    """
    target_url = _ollama_root_url_for_unload(base_url) + "/api/ps"
    headers = _safe_build_headers(api_key, base_url)
    try:
        response = httpx.get(target_url, headers=headers, timeout=8, verify=llm_verify())
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Timed out asking Ollama which models are loaded")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Could not list loaded Ollama models: {str(exc)[:180]}")

    models = []
    for item in data.get("models") or []:
        if not isinstance(item, dict):
            continue
        model = item.get("model") or item.get("name")
        if isinstance(model, str) and model.strip():
            models.append(model.strip())
    return _normalize_model_ids(models)


def _ollama_unload_model(base_url: str, api_key: Optional[str], model: str) -> Dict[str, Any]:
    target_url = _ollama_generate_url_for_unload(base_url)
    headers = _safe_build_headers(api_key, base_url)
    headers["Content-Type"] = "application/json"
    payload = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
    try:
        response = httpx.post(
            target_url,
            headers=headers,
            json=payload,
            timeout=15,
            verify=llm_verify(),
        )
    except httpx.TimeoutException:
        raise HTTPException(504, f"Timed out asking Ollama to unload {model}")
    except Exception as exc:
        raise HTTPException(502, f"Unload request failed: {str(exc)[:180]}")

    if response.is_success:
        return {
            "ok": True,
            "supported": True,
            "provider": "ollama",
            "model": model,
            "message": f"Unload requested for {model}",
        }

    detail = f"HTTP {response.status_code}"
    try:
        data = response.json()
        err = data.get("error")
        if isinstance(err, dict):
            detail = err.get("message") or detail
        elif err:
            detail = str(err)
    except Exception:
        text = (response.text or "").strip()
        if text:
            detail = text[:220]
    raise HTTPException(response.status_code, f"Ollama unload failed: {detail}")


def _is_discovery_only_provider(provider: str) -> bool:
    return provider == "chatgpt-subscription"


def _resolve_probe_key(ep) -> Optional[str]:
    """API key/bearer to probe an endpoint with."""
    try:
        from src.endpoint_resolver import resolve_endpoint_runtime
        _base, key = resolve_endpoint_runtime(ep, owner=getattr(ep, "owner", None))
        return key
    except Exception as exc:
        logger.warning("Probe key resolution failed for %s: %s", getattr(ep, "id", "?"), exc)
        return None


def _probe_single_model(base: str, api_key: str, model_id: str, timeout: int = 10, with_tools: bool = False) -> dict:
    """Send a realistic completion request to a single model. Returns {status, latency_ms, error?}."""
    provider = _safe_detect_provider(base)
    if _is_discovery_only_provider(provider):
        return {"status": "ok", "latency_ms": 0, "skipped": True}
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say OK"},
    ]
    # Simple tool definition to test tool support
    _test_tools = [{"type": "function", "function": {"name": "test", "description": "Test tool", "parameters": {"type": "object", "properties": {}}}}] if with_tools else None

    if provider == "anthropic":
        from src.llm_core import _normalize_anthropic_url, _build_anthropic_headers, _build_anthropic_payload
        target_url = _normalize_anthropic_url(base)
        auth_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        h = _build_anthropic_headers(auth_headers)
        payload = _build_anthropic_payload(model_id, messages, 0.0, 5)
        if _test_tools:
            payload["tools"] = [{"name": "test", "description": "Test tool", "input_schema": {"type": "object", "properties": {}}}]
    elif provider == "ollama":
        from src.llm_core import _build_ollama_payload
        target_url = build_chat_url(base)
        h = _safe_build_headers(api_key, base)
        h["Content-Type"] = "application/json"
        payload = _build_ollama_payload(model_id, messages, 0.0, 5, stream=False, tools=_test_tools)
    else:
        target_url = build_chat_url(base)
        h = _safe_build_headers(api_key, base)
        h["Content-Type"] = "application/json"
        from src.llm_core import _uses_max_completion_tokens, _restricts_temperature
        _max_key = "max_completion_tokens" if _uses_max_completion_tokens(model_id) else "max_tokens"
        payload = {"model": model_id, "messages": messages, _max_key: 5}
        # Reasoning models (o1/o3/o4/gpt-5) reject an explicit temperature, so a
        # probe that hardcodes one falsely reports a working endpoint as failing.
        if not _restricts_temperature(model_id):
            payload["temperature"] = 0.0
        if _test_tools:
            payload["tools"] = _test_tools

    try:
        t0 = _time.time()
        r = httpx.post(target_url, headers=h, json=payload, timeout=timeout, verify=llm_verify())
        latency = round((_time.time() - t0) * 1000)
        if r.is_success:
            return {"status": "ok", "latency_ms": latency}
        else:
            # Extract error detail from response body
            error_msg = f"HTTP {r.status_code}"
            try:
                body = r.json()
                if "error" in body:
                    err = body["error"]
                    if isinstance(err, dict):
                        error_msg = err.get("message", error_msg)[:120]
                    elif isinstance(err, str):
                        error_msg = err[:120]
            except Exception:
                pass
            return {"status": "fail", "latency_ms": latency, "error": error_msg}
    except httpx.TimeoutException:
        return {"status": "timeout", "latency_ms": timeout * 1000, "error": f"Timed out ({timeout}s)"}
    except Exception as e:
        return {"status": "fail", "error": str(e)[:80]}


# Hostnames / IP prefixes that indicate a local endpoint
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_ip_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS) or ip in _TAILSCALE_CGNAT


def _classify_endpoint(base_url: str, endpoint_kind: str = "auto") -> str:
    """Return 'local' if the endpoint URL points to a private/local address, else 'api'.
    Includes the Tailscale CGNAT range (100.64.0.0/10) so tailnet-hosted
    servers (e.g. Cookbook serve endpoints) get reachability-probed too."""
    kind = _normalize_endpoint_kind(endpoint_kind)
    if kind == "local":
        return "local"
    if kind in ("api", "proxy"):
        return "api"
    try:
        host = urlparse(base_url).hostname or ""
        if host in _LOCAL_HOSTS or _local_ip_literal(host):
            return "local"
    except Exception:
        pass
    return "api"


def _effective_endpoint_kind(ep: Any, base_url: str) -> str:
    """Return explicit kind, with a legacy proxy heuristic for keyed /v1 URLs."""
    kind = _endpoint_kind(ep)
    if kind != "auto":
        return kind
    if getattr(ep, "api_key", None) and not _is_ollama_base(base_url):
        try:
            path = (urlparse(base_url).path or "").rstrip("/")
            if path.endswith("/v1") or "/openai" in path:
                return "proxy"
        except Exception:
            pass
    return "auto"


def _is_loading_model_response(resp: Any) -> bool:
    if getattr(resp, "status_code", None) != 503:
        return False
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    return "loading model" in body.lower()



def _openai_model_ids(data: Any) -> List[str]:
    """Extract OpenAI-style model IDs.

    Accepts both standard ``{"data": [{"id": ...}]}`` responses and bare
    ``[{"id": ...}]`` lists returned by some OpenAI-compatible providers.
    Tolerates non-dict/non-list bodies and non-string IDs, returning only
    non-empty string IDs.
    """
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data")
    else:
        items = None
    return [m["id"] for m in (items or [])
            if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]


def _ollama_model_names(data: Any) -> List[str]:
    """Extract native-Ollama model names (``{"models": [{"name"|"model": ...}]}``).

    Same tolerance as :func:`_openai_model_ids`: a non-dict body or non-string
    value is skipped rather than crashing, preserving name-then-model precedence.
    """
    items = data.get("models") if isinstance(data, dict) else None
    out: List[str] = []
    for m in (items or []):
        if not isinstance(m, dict):
            continue
        v = m.get("name") or m.get("model")
        if isinstance(v, str) and v:
            out.append(v)
    return out


def _filter_probed_models(models: List[str], include_non_chat: bool = False) -> List[str]:
    return list(models or []) if include_non_chat else [m for m in (models or []) if _is_chat_model(m)]


def _probe_endpoint(
    base_url: str,
    api_key: str = None,
    timeout: int = 5,
    include_non_chat: bool = False,
) -> List[str]:
    """Probe a base URL's /models endpoint and return list of model IDs.
    For Anthropic, queries their /v1/models API, falling back to hardcoded list."""
    from src.endpoint_resolver import resolve_url
    from src.llm_core import httpx_get_kimi_aware
    base = resolve_url(_normalize_base(base_url))
    provider = _safe_detect_provider(base)
    if provider == "chatgpt-subscription":
        from src.chatgpt_subscription import fetch_available_models
        if api_key:
            return fetch_available_models(api_key, timeout=timeout)
        return []
    if provider == "anthropic":
        # Try Anthropic's /v1/models endpoint first
        url = _safe_build_models_url(base)
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        try:
            r = httpx.get(url, headers=headers, timeout=timeout, verify=llm_verify())
            r.raise_for_status()
            data = r.json()
            models = _openai_model_ids(data)
            if models:
                return models
        except httpx.HTTPStatusError as e:
            if api_key:
                status = e.response.status_code if e.response is not None else "unknown"
                logger.warning(f"Anthropic /v1/models failed with API key: HTTP {status}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {e}")
        except Exception as e:
            if api_key:
                logger.warning(f"Anthropic /v1/models failed with API key: {e}")
                return []
            logger.warning(f"Anthropic /v1/models failed, using hardcoded list: {e}")
        return list(ANTHROPIC_MODELS)
    url = _safe_build_models_url(base)
    headers = _safe_build_headers(api_key, base)
    try:
        r = httpx_get_kimi_aware(url, headers, timeout=timeout, verify=llm_verify())
        r.raise_for_status()
        data = r.json()
        # OpenAI format: {"data": [{"id": "model-name"}]}
        models = _openai_model_ids(data)
        # Ollama format: {"models": [{"name": "model-name"}]}
        if not models:
            models = _ollama_model_names(data)
        if models:
            # Z.AI coding plan omits some working models from /models;
            # append curated-only entries for that endpoint only.
            if _host_match(base, "z.ai") and "/api/coding" in (urlparse(base).path or ""):
                _ck = _match_provider_curated(base, None)
                for _e in _PROVIDER_CURATED.get(_ck, []):
                    if _e not in set(models) and not any(m.startswith(_e) for m in models):
                        models.append(_e)
            return _filter_probed_models(models, include_non_chat)
    except httpx.HTTPStatusError as e:
        if e.response is not None and _is_loading_model_response(e.response):
            logger.info("Endpoint still loading model at %s", _redact_url_for_log(url))
            return []
        if api_key:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.warning("Failed to probe %s with API key: HTTP %s", _redact_url_for_log(url), status)
            return []
        logger.warning("Failed to probe %s: %s", _redact_url_for_log(url), e)
    except Exception as e:
        if api_key:
            logger.warning("Failed to probe %s with API key: %s", _redact_url_for_log(url), e)
            return []
        logger.warning("Failed to probe %s: %s", _redact_url_for_log(url), e)

    # Older Ollama builds and some proxies expose native /api/tags even when
    # the OpenAI-compatible /v1/models path is unavailable.
    try:
        parsed = urlparse(base)
        if parsed.port == 11434 or "ollama" in (parsed.hostname or "").lower():
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base
            r = httpx.get(root + "/api/tags", timeout=timeout, verify=llm_verify())
            r.raise_for_status()
            data = r.json()
            models = _ollama_model_names(data)
            if models:
                return _filter_probed_models(models, include_non_chat)
    except Exception as e:
        logger.debug(f"Ollama /api/tags probe failed for {base}: {e}")
    # Fall back to curated list if the provider has a URL-based match (e.g. z.ai has no /models endpoint)
    curated_key = _match_provider_curated(base, None)
    fallback = _PROVIDER_CURATED.get(curated_key) if curated_key else None
    if fallback:
        logger.info(f"Using curated fallback for {curated_key}: {fallback}")
        return list(fallback)
    return []


def _probe_endpoint_for_model_type(
    base_url: str,
    api_key: str = None,
    timeout: int = 5,
    model_type: str = "llm",
) -> List[str]:
    return _probe_endpoint(base_url, api_key, timeout=timeout, include_non_chat=True)


def _local_health_probe_urls(base: str, models_url: Optional[str]) -> List[str]:
    """Extra cheap probes for local image/diffusion servers.

    Some local OpenAI-compatible image sidecars expose `/health` at the server
    root and `/v1/models`, while a user may register the root URL. The generic
    chat probe checks `<base>/models`; this fills the common diffusion gap
    without changing cloud/API probe behaviour.
    """
    if _classify_endpoint(base, "auto") != "local":
        return []
    root = (base or "").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    candidates = [f"{root}/health", f"{root}/v1/models"]
    seen = {(base or "").rstrip("/"), (models_url or "").rstrip("/")}
    out = []
    for url in candidates:
        key = url.rstrip("/")
        if key and key not in seen and key not in out:
            out.append(url)
    return out


def _ping_endpoint(base_url: str, api_key: str = None, timeout: float = 1.5) -> Dict[str, Any]:
    """Reachability probe that does not require installed/listed models."""
    from src.endpoint_resolver import resolve_url
    base = resolve_url(_normalize_base(base_url))
    headers = _safe_build_headers(api_key, base)

    # Ollama exposes /v1/models (OpenAI-compatible) AND native /api/version,
    # /api/tags. Probe native paths for Ollama-style endpoints, but avoid using
    # /models as a generic health check because large proxy catalogs can be slow.
    parsed_base = urlparse(base)
    looks_like_ollama = (
        parsed_base.port == 11434
        or "ollama" in (parsed_base.hostname or "").lower()
    )

    def _is_loading_model_response(r) -> bool:
        if getattr(r, "status_code", None) != 503:
            return False
        try:
            body = r.text or ""
        except Exception:
            body = ""
        return "loading model" in body.lower()

    def _result_from_response(r) -> Dict[str, Any]:
        if 300 <= r.status_code < 400:
            loc = r.headers.get("location", "")
            if loc.startswith("/login") or "/login" in loc:
                return {
                    "reachable": False,
                    "status_code": r.status_code,
                    "error": "That is Odysseus, not a model server. Use the Ollama URL, usually http://host.docker.internal:11434/v1 in Docker.",
                }
            return {"reachable": False, "status_code": r.status_code, "error": f"HTTP {r.status_code} redirect"}
        if 200 <= r.status_code < 300:
            return {
                "reachable": True,
                "status_code": r.status_code,
                "error": None,
            }
        if _is_loading_model_response(r):
            return {
                "reachable": True,
                "loading": True,
                "status_code": r.status_code,
                "error": "Loading model",
            }
        return {"reachable": False, "status_code": r.status_code, "error": f"HTTP {r.status_code}"}

    last_error: Optional[str] = None

    try:
        if looks_like_ollama:
            root = base
            for suffix in ("/v1", "/api"):
                if root.endswith(suffix):
                    root = root[: -len(suffix)].rstrip("/")
                    break
            for path in ("/api/version", "/api/tags"):
                try:
                    r = httpx.get(root + path, timeout=timeout, verify=llm_verify())
                    result = _result_from_response(r)
                    if result["reachable"]:
                        return result
                    last_error = result.get("error")
                except Exception as e:
                    last_error = str(e)[:120]
    except Exception:
        pass

    try:
        r = httpx.get(base, headers=headers, timeout=timeout, verify=llm_verify())
        result = _result_from_response(r)
        if result["reachable"]:
            return result
        sc = result.get("status_code") or 0
        if 400 <= sc < 500 and sc not in (401, 403):
            models_url = _safe_build_models_url(base)
            try:
                r2 = httpx.get(models_url, headers=headers,timeout=timeout, verify=llm_verify())
                result2 = _result_from_response(r2)
                if result2["reachable"]:
                    return result2
            except Exception:
                pass
            for probe_url in _local_health_probe_urls(base, models_url):
                try:
                    r3 = httpx.get(probe_url, headers=headers, timeout=timeout, verify=llm_verify())
                    result3 = _result_from_response(r3)
                    if result3["reachable"]:
                        return result3
                    last_error = result3.get("error") or last_error
                except Exception as e:
                    last_error = str(e)[:120]
        if sc:
            return result
        last_error = result.get("error") or last_error
    except Exception as e:
        last_error = str(e)[:120]

    models_url = _safe_build_models_url(base)
    for probe_url in _local_health_probe_urls(base, models_url):
        try:
            r = httpx.get(probe_url, headers=headers, timeout=timeout, verify=llm_verify())
            result = _result_from_response(r)
            if result["reachable"]:
                return result
            last_error = result.get("error") or last_error
        except Exception as e:
            last_error = str(e)[:120]

    return {"reachable": False, "status_code": None, "error": last_error}



def _model_endpoint_error_message(base_url: str, ping: Dict[str, Any] = None) -> str:
    """Return a provider-aware error message for failed endpoint probes.

    Surfaces the URL we actually probed and, when the endpoint looks like
    LM Studio (port 1234 or hostname match), adds a hint about loading a
    model and confirming the Developer Server is running. The user previously
    saw a generic "No models found for that provider/key" with no way to
    tell whether the URL was wrong, the server was down, or the server was
    reachable but had no model loaded (issue #25).
    """
    ping = ping or {}
    error = ping.get("error")
    from src.endpoint_resolver import build_models_url
    try:
        probed = build_models_url(base_url) or base_url
    except Exception:
        probed = base_url
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    is_ollama = parsed.port == 11434 or "ollama" in host or "ollama" in base_url.lower()
    is_lmstudio = (
        parsed.port == 1234
        or "lmstudio" in host
        or "lm-studio" in host
        or "lm_studio" in host
    )

    if is_lmstudio:
        parts = [
            "LM Studio is reachable, but no models were reported.",
            f"Probed {probed}.",
        ]
        if error:
            parts.append(f"Last probe error: {error}.")
        parts.append(
            "Open LM Studio, load at least one model, and confirm the "
            "Developer Server is running on port 1234."
        )
        parts.append(
            "Base URL should be http://localhost:1234/v1 (native) or "
            "http://host.docker.internal:1234/v1 (Docker)."
        )
        return " ".join(parts)

    if is_ollama:
        parts = ["No Ollama models found for that endpoint."]
        parts.append(f"Probed {probed}.")
        if error:
            parts.append(f"Last probe error: {error}.")
        parts.append("Check that Ollama is running and that the base URL is correct.")
        parts.append("For native/local installs, use http://localhost:11434/v1.")
        parts.append("For Docker, use http://host.docker.internal:11434/v1 when Ollama runs on the host.")
        parts.append("Run `ollama list` to confirm at least one model is installed.")
        return " ".join(parts)

    if error:
        return f"No models found for that provider/key. Probed {probed}. Last probe error: {error}."

    return f"No models found for that provider/key. Probed {probed}."


def _normalize_model_ids(value):
    """Coerce a model-ID input into a clean, ordered list of strings.

    Accepts a list, a JSON-encoded list string, or a comma/newline separated
    string (handy for form or backend API input). Trims whitespace, drops
    empty and non-string values, and de-duplicates preserving first-seen order.
    """
    if value is None:
        return []
    items = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        items = parsed if isinstance(parsed, list) else re.split(r"[,\n]", text)
    if not isinstance(items, list):
        return []
    out, seen = [], set()
    for item in items:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_model_ids(*lists):
    """Concatenate model-ID lists, de-duplicating and preserving order."""
    out, seen = [], set()
    for ids in lists:
        for m in (ids or []):
            if not isinstance(m, str) or m in seen:
                continue
            seen.add(m)
            out.append(m)
    return out


def _is_mlx_deepseek_v4_repo_id(model_id: str) -> bool:
    m = str(model_id or "").lower()
    return "mlx-community/deepseek-v4" in m


def _is_mlx_deepseek_v4_shim_id(model_id: str) -> bool:
    m = str(model_id or "").lower()
    return "/.cache/odysseus/mlx-shims/deepseek-v4" in m


def _filter_mlx_deepseek_v4_repo_when_shimmed(model_ids):
    """Hide the broken MLX repo id when a launch-specific shim id is available.

    mlx_lm.server may advertise the original HF repo id even though generation
    only works through Odysseus' sanitized local shim. Keep the shim as the
    submitted model id and remove the raw repo id from the picker/default list.
    """
    ids = list(model_ids or [])
    has_shim = any(_is_mlx_deepseek_v4_shim_id(m) for m in ids)
    if not has_shim:
        return ids
    return [m for m in ids if not _is_mlx_deepseek_v4_repo_id(m)]


def _model_display_name(model_id: str) -> str:
    if _is_mlx_deepseek_v4_shim_id(model_id):
        return str(model_id or "").rstrip("/").split("/")[-1] or "DeepSeek-V4-Flash-4bit"
    return str(model_id or "").split("/")[-1]


def _visible_models(cached_models, hidden_models, pinned_models=None):
    """Merge cached + pinned model IDs, then filter out hidden ones.

    Pinned IDs are admin-entered and may not appear in cached_models (e.g.
    cloud deployment IDs the provider does not list in /v1/models). Returns an
    ordered, de-duplicated list of visible IDs.
    """
    # Normalize each input so JSON strings, lists, comma/newline strings, and
    # malformed strings are all handled without raising.
    merged = _merge_model_ids(
        _normalize_model_ids(cached_models),
        _normalize_model_ids(pinned_models),
    )
    merged = _filter_mlx_deepseek_v4_repo_when_shimmed(merged)
    if not hidden_models:
        return merged
    hidden = set(_normalize_model_ids(hidden_models))
    return [m for m in merged if m not in hidden]


def _api_key_fingerprint(api_key: Optional[str]) -> str:
    """Stable, non-secret label for distinguishing same-URL credentials."""
    key = (api_key or "").strip()
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]

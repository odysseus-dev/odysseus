# routes/model_routes.py
"""Routes for model and provider management."""
import os
import re
import uuid
import json
import hashlib
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
from core.middleware import require_admin
from src.llm_core import _detect_provider, _host_match, ANTHROPIC_MODELS
from src.tls_overrides import llm_verify
from src.settings import load_settings as _load_settings, save_settings as _save_settings
from src.endpoint_resolver import (
    normalize_base as _normalize_base,
    build_chat_url,
    build_models_url,
    build_headers,
)
from src.auth_helpers import _auth_disabled, owner_filter

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


def _default_endpoint_needs_assignment(current_default_id: str, enabled_endpoint_ids) -> bool:
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
    return current_default_id not in enabled_endpoint_ids


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
        return float(max(1, min(30, val)))
    return 2.5 if category == "local" else 2.0


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
    return 3.0 if _is_ollama_base(base_url) else 2.0


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
        r = httpx.post(target_url, headers=h, json=payload, timeout=timeout)
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
_PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.")


_TAILSCALE_RE = re.compile(r"^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.")


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
        if host in _LOCAL_HOSTS or host.startswith(_PRIVATE_PREFIXES):
            return "local"
        if _TAILSCALE_RE.match(host):
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



def _probe_endpoint(base_url: str, api_key: str = None, timeout: int = 5) -> List[str]:
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
            models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
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
        models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        # Ollama format: {"models": [{"name": "model-name"}]}
        if not models:
            models = [m.get("name") or m.get("model") for m in (data.get("models") or []) if m.get("name") or m.get("model")]
        if models:
            # Z.AI coding plan omits some working models from /models;
            # append curated-only entries for that endpoint only.
            if _host_match(base, "z.ai") and "/api/coding" in (urlparse(base).path or ""):
                _ck = _match_provider_curated(base, None)
                for _e in _PROVIDER_CURATED.get(_ck, []):
                    if _e not in set(models) and not any(m.startswith(_e) for m in models):
                        models.append(_e)
            if _host_match(base, "kimi.com") and "/coding" in (urlparse(base).path or ""):
                _ck = _match_provider_curated(base, None)
                for _e in _PROVIDER_CURATED.get(_ck, []):
                    if _e not in set(models) and not any(m.startswith(_e) for m in models):
                        models.append(_e)
            return [m for m in models if _is_chat_model(m)]
    except httpx.HTTPStatusError as e:
        if api_key:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.warning(f"Failed to probe {url} with API key: HTTP {status}")
            return []
        logger.warning(f"Failed to probe {url}: {e}")
    except Exception as e:
        if api_key:
            logger.warning(f"Failed to probe {url} with API key: {e}")
            return []
        logger.warning(f"Failed to probe {url}: {e}")

    # Older Ollama builds and some proxies expose native /api/tags even when
    # the OpenAI-compatible /v1/models path is unavailable.
    try:
        parsed = urlparse(base)
        if parsed.port == 11434 or "ollama" in (parsed.hostname or "").lower():
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base
            r = httpx.get(root + "/api/tags", timeout=timeout, verify=llm_verify())
            r.raise_for_status()
            data = r.json()
            models = [m.get("name") or m.get("model") for m in (data.get("models") or []) if m.get("name") or m.get("model")]
            if models:
                return [m for m in models if _is_chat_model(m)]
    except Exception as e:
        logger.debug(f"Ollama /api/tags probe failed for {base}: {e}")
    # Fall back to curated list if the provider has a URL-based match (e.g. z.ai has no /models endpoint)
    curated_key = _match_provider_curated(base, None)
    fallback = _PROVIDER_CURATED.get(curated_key) if curated_key else None
    if fallback:
        logger.info(f"Using curated fallback for {curated_key}: {fallback}")
        return list(fallback)
    return []


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
                r2 = httpx.get(models_url, headers=headers, timeout=timeout, verify=llm_verify())
                result2 = _result_from_response(r2)
                if result2["reachable"]:
                    return result2
            except Exception:
                pass
        if sc:
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


def setup_model_routes(model_discovery):
    router = APIRouter(prefix="/api")

    # ---- Model list cache ----
    import time as _time
    # Per-user cache: { owner_key: {"data": ..., "time": ...} }. owner_key is
    # the username (or "" for the unconfigured / single-user case). Without
    # this every user shared the same cached result and the picker showed
    # whichever admin's endpoint list happened to populate it first.
    _models_cache: dict = {}
    _MODELS_CACHE_TTL = 30  # seconds

    def _invalidate_models_cache() -> None:
        """Clear the per-user /api/models cache. Call after any change that
        affects the visible endpoint list (CRUD on ModelEndpoint, prefs
        flip)."""
        _models_cache.clear()

    # Track model-list refreshes by URL+key. This prevents repeated picker/API
    # opens from starting duplicate /models probes, and gives slow/offline
    # providers a cooldown after failures.
    _refresh_state: Dict[str, Dict[str, Any]] = {}
    _refresh_inflight = {"v": False}  # coarse single-flight guard
    _REFRESH_FAILURE_BASE = 300.0
    _REFRESH_FAILURE_MAX = 3600.0

    def _refresh_key(base: str, api_key: Optional[str]) -> str:
        return f"{base.rstrip('/')}\x00{api_key or ''}"

    def _ts(value: Any) -> float:
        try:
            return float(value.timestamp()) if value else 0.0
        except Exception:
            return 0.0

    def _failure_delay(fails: int) -> float:
        if fails <= 0:
            return 0.0
        return min(_REFRESH_FAILURE_BASE * (2 ** max(0, fails - 1)), _REFRESH_FAILURE_MAX)

    def _should_refresh_endpoint(ep: Any, now: float, force: bool = False) -> tuple[bool, Dict[str, Any]]:
        base = _normalize_base(getattr(ep, "base_url", "") or "")
        kind = _effective_endpoint_kind(ep, base)
        category = _classify_endpoint(base, kind)
        mode = _endpoint_refresh_mode(ep, kind)
        cached = _cached_model_ids(ep)
        key = _refresh_key(base, getattr(ep, "api_key", None))
        state = _refresh_state.get(key, {})

        info = {
            "id": getattr(ep, "id", ""),
            "base": base,
            "api_key": getattr(ep, "api_key", None),
            "kind": kind,
            "category": category,
            "mode": mode,
            "key": key,
            "timeout": _endpoint_refresh_timeout(ep, category),
        }
        if not base:
            return False, info
        if state.get("inflight"):
            return False, info
        if mode in ("manual", "disabled") and not force:
            return False, info
        fails = int(state.get("fail_count") or 0)
        if fails and not force:
            last_failure = float(state.get("last_failure") or 0.0)
            if now - last_failure < _failure_delay(fails):
                return False, info
        if cached and not force:
            interval = _endpoint_refresh_interval(ep, category)
            last_good = float(state.get("last_success") or 0.0) or _ts(getattr(ep, "updated_at", None)) or _ts(getattr(ep, "created_at", None))
            if last_good and now - last_good < interval:
                return False, info
        return True, info

    def _refresh_caches_bg(force: bool = False):
        """Background thread: safely refresh model caches with per-base single-flight.

        The public /api/models path stays cached-first. This refresh never clears
        a non-empty cached model list on timeout/failure, and proxy/manual
        endpoints are skipped unless explicitly forced."""
        import threading
        if _refresh_inflight["v"]:
            return  # already running
        _refresh_inflight["v"] = True

        def _do():
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                db = SessionLocal()
                changed = False
                try:
                    endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
                    now = _time.time()
                    groups: Dict[str, Dict[str, Any]] = {}
                    for ep in endpoints:
                        ok, info = _should_refresh_endpoint(ep, now, force=force)
                        if not ok:
                            continue
                        groups.setdefault(info["key"], {
                            "base": info["base"],
                            "api_key": info["api_key"],
                            "timeout": info["timeout"],
                            "endpoint_ids": [],
                        })["endpoint_ids"].append(info["id"])

                    for key in groups:
                        st = _refresh_state.setdefault(key, {})
                        st["inflight"] = True
                        st["last_attempt"] = now

                    def _probe_one(key: str, data: Dict[str, Any]):
                        try:
                            ids = _probe_endpoint(data["base"], data.get("api_key"), timeout=data.get("timeout") or 2)
                            return key, data["endpoint_ids"], ids, None
                        except Exception as e:
                            return key, data["endpoint_ids"], None, e

                    if groups:
                        with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
                            futures = [pool.submit(_probe_one, key, data) for key, data in groups.items()]
                            for fut in as_completed(futures):
                                key, endpoint_ids, ids, err = fut.result()
                                st = _refresh_state.setdefault(key, {})
                                if ids:
                                    for ep_id in endpoint_ids:
                                        ep_obj = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
                                        if ep_obj:
                                            ep_obj.cached_models = json.dumps(ids)
                                            changed = True
                                    st["last_success"] = _time.time()
                                    st["fail_count"] = 0
                                    st.pop("last_failure", None)
                                else:
                                    st["last_failure"] = _time.time()
                                    st["fail_count"] = int(st.get("fail_count") or 0) + 1
                                st["inflight"] = False
                        db.commit()
                finally:
                    db.close()
                if changed:
                    _invalidate_models_cache()
            except Exception as e:
                logger.warning('Background endpoint refresh failed: %s', e)
            finally:
                for st in _refresh_state.values():
                    st["inflight"] = False
                _refresh_inflight["v"] = False
        threading.Thread(target=_do, daemon=True).start()

    def _fetch_models(owner: str = "", is_admin: bool = False):
        """Return model list from cached data (instant). Background refresh keeps caches fresh.

        SECURITY: filters endpoints by `owner` — without this the picker
        leaked every admin-added endpoint (and the model list behind each
        one) to every authenticated user. NULL-owner rows are treated as
        legacy/shared so existing configs still appear after migration.

        Admins see EVERY endpoint (they manage the global pool, and the
        scoped filter was making the picker disappear for them).
        """
        items = []

        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if owner and not is_admin:
                # Regular users see: their own endpoints + null-owner
                # (legacy / shared). Admins see everything.
                q = owner_filter(q, ModelEndpoint, owner)
            endpoints = q.all()
        finally:
            db.close()

        for ep in endpoints:
            base = _normalize_base(ep.base_url)
            provider = _safe_detect_provider(base)
            # Merge cached + pinned models, then filter out hidden ones
            ep_model_type = getattr(ep, "model_type", None) or "llm"
            model_ids = _visible_models(
                _cached_model_ids(ep),
                ep.hidden_models,
                getattr(ep, "pinned_models", None),
            )
            # Build correct URL based on provider
            chat_url = build_chat_url(base)
            kind = _effective_endpoint_kind(ep, base)
            category = _classify_endpoint(base, kind)

            if model_ids:
                curated_key = _match_provider_curated(base, None)
                curated, extra = _curate_models(model_ids, curated_key)
                # Pinned models are admin-selected — they always belong in the
                # primary curated list, not buried in extras.
                pinned = _normalize_model_ids(getattr(ep, "pinned_models", None))
                for m in pinned:
                    if m not in curated:
                        curated.append(m)
                extra = [m for m in extra if m not in pinned]
                items.append({
                    "host": "custom",
                    "port": 0,
                    "url": chat_url,
                    "models": curated,
                    "models_display": [mid.split("/")[-1] for mid in curated],
                    "models_extra": extra,
                    "models_extra_display": [mid.split("/")[-1] for mid in extra],
                    "endpoint_id": ep.id,
                    "endpoint_name": ep.name,
                    "category": category,
                    "endpoint_kind": kind,
                    "model_type": ep_model_type,
                })
            else:
                # Endpoint unreachable but still show it greyed out
                items.append({
                    "host": "custom",
                    "port": 0,
                    "url": chat_url,
                    "models": [],
                    "models_display": [],
                    "models_extra": [],
                    "models_extra_display": [],
                    "endpoint_id": ep.id,
                    "endpoint_name": ep.name,
                    "category": category,
                    "endpoint_kind": kind,
                    "model_type": ep_model_type,
                    "offline": True,
                })

        return {"hosts": [], "items": items}

    @router.get("/models")
    def api_models(request: Request, refresh: bool = False):
        """Get available models — per-user (caller sees only their endpoints +
        legacy/shared null-owner rows). Cached per-user for 30s."""
        # Require auth; "" is the unconfigured single-user mode, treated as
        # "see everything" by _fetch_models.
        try:
            from src.auth_helpers import get_current_user as _gcu
            owner = _gcu(request) or ""
        except Exception:
            owner = ""
        # Reject anonymous in configured deployments — no leaking the model
        # list to unauthenticated callers.
        try:
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if not owner and not _auth_disabled() and auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
                raise HTTPException(401, "Not authenticated")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Auth gate error in GET /api/models, failing closed: %s", e)
            raise HTTPException(status_code=500, detail="Internal error")
        # Admins see every endpoint (they manage the global pool); regular
        # users get the owner-scoped view.
        _is_admin = False
        try:
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if owner and auth_mgr is not None and getattr(auth_mgr, "is_admin", None):
                _is_admin = bool(auth_mgr.is_admin(owner))
        except Exception:
            _is_admin = False
        now = _time.time()
        # Cache key includes the admin flag so a demotion / promotion doesn't
        # serve the wrong scoped view from cache.
        _cache_key = (owner, _is_admin)
        cache_entry = _models_cache.get(_cache_key)
        if not refresh and cache_entry is not None and (now - cache_entry["time"]) < _MODELS_CACHE_TTL:
            return cache_entry["data"]
        result = _fetch_models(owner=owner, is_admin=_is_admin)
        _models_cache[_cache_key] = {"data": result, "time": now}
        # Kick off background refresh to update caches from live endpoints
        _refresh_caches_bg(force=refresh)
        return result

    # Brief cache for local-probe results so picker-open doesn't hammer
    # endpoint health checks every time. 8s TTL — long enough to amortize cost,
    # short enough that a freshly-killed local server shows as offline
    # within ~8s of the user noticing.
    _LOCAL_PROBE_TTL = 8.0
    _local_probe_cache: Dict[str, Any] = {"data": None, "time": 0.0}

    @router.get("/model-endpoints/probe-local")
    async def probe_local_endpoints(request: Request):
        """Fast parallel reachability check for LOCAL endpoints only.
        Cloud endpoints (api.openai.com, api.anthropic.com, etc.) are
        assumed up. Local endpoints get a 1.5s cheap reachability probe so the UI
        can dim stale entries pointing at dead vLLM servers. Returns
        {ep_id: {alive, latency_ms, error}}."""
        require_admin(request)
        now = _time.time()
        if (_local_probe_cache["data"] is not None and
                (now - _local_probe_cache["time"]) < _LOCAL_PROBE_TTL):
            return _local_probe_cache["data"]

        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
            local_eps = []
            for ep in endpoints:
                base = _normalize_base(ep.base_url)
                kind = _effective_endpoint_kind(ep, base)
                if _classify_endpoint(base, kind) == "local":
                    local_eps.append((ep.id, base, ep.api_key))
        finally:
            db.close()

        grouped: Dict[str, Dict[str, Any]] = {}
        for ep_id, base, api_key in local_eps:
            key = _refresh_key(base, api_key)
            grouped.setdefault(key, {"base": base, "api_key": api_key, "endpoint_ids": []})["endpoint_ids"].append(ep_id)

        async def _probe_one(data: Dict[str, Any]) -> Dict[str, Any]:
            t0 = _time.time()
            try:
                import asyncio as _asyncio
                # Bumped 1.5s → 3.5s. The previous 1.5s budget was clipping
                # local vLLM endpoints on Tailscale links where the model
                # server is still loading (Qwen3.5-122B takes 2–3 min to
                # warm); /v1/models can take 500–2500 ms on a busy box,
                # which pushed _ping_endpoint's full path-discovery sweep
                # past the cap and marked the row offline despite the
                # user actively chatting with it.
                ping = await _asyncio.to_thread(_ping_endpoint, data["base"], data.get("api_key"), 3.5)
                lat = round((_time.time() - t0) * 1000)
                return {
                    "alive": bool(ping.get("reachable")),
                    "latency_ms": lat,
                    "status_code": ping.get("status_code"),
                    "error": ping.get("error"),
                }
            except Exception as e:
                return {"alive": False, "latency_ms": None, "status_code": None, "error": str(e)[:120]}

        import asyncio as _asyncio
        results_list = await _asyncio.gather(
            *[_probe_one(data) for data in grouped.values()],
            return_exceptions=False,
        )
        results: Dict[str, Any] = {}
        for data, r in zip(grouped.values(), results_list):
            for eid in data["endpoint_ids"]:
                results[eid] = r

        _local_probe_cache["data"] = results
        _local_probe_cache["time"] = now
        return results

    @router.get("/ping")
    def ping_endpoints(request: Request):
        """Probe all enabled endpoints and return status + latency."""
        require_admin(request)
        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        finally:
            db.close()

        results = []
        for ep in endpoints:
            base = _normalize_base(ep.base_url)
            provider = _safe_detect_provider(base)
            kind = _effective_endpoint_kind(ep, base)
            cached_count = len(_cached_model_ids(ep))
            entry = {
                "id": ep.id,
                "name": ep.name,
                "base_url": base,
                "provider": provider,
                "category": _classify_endpoint(base, kind),
                "endpoint_kind": kind,
            }
            try:
                t0 = _time.time()
                ping = _ping_endpoint(base, ep.api_key, timeout=1.5)
                entry["latency_ms"] = round((_time.time() - t0) * 1000)
                entry["status"] = "online" if ping.get("reachable") or cached_count else "offline"
                entry["error"] = ping.get("error")
                entry["model_count"] = cached_count or (len(ANTHROPIC_MODELS) if provider == "anthropic" else 0)
            except Exception as e:
                entry["latency_ms"] = None
                entry["status"] = "online" if cached_count else "offline"
                entry["error"] = str(e)
                entry["model_count"] = cached_count
            results.append(entry)

        return {"endpoints": results}

    @router.post("/probe-selected")
    def probe_selected(request: Request, request_body: dict = Body(...)):
        """Probe specific models for compare pre-check. Body: {models: [{endpoint_id, model}]}."""
        require_admin(request)
        models_to_probe = request_body.get("models", [])
        if not models_to_probe:
            return {"results": []}

        db = SessionLocal()
        try:
            endpoints_cache = {}
            results = []
            for item in models_to_probe:
                ep_id = item.get("endpoint_id", "")
                model_id = item.get("model", "")
                if not model_id:
                    results.append({"model": model_id, "status": "fail", "error": "No model specified"})
                    continue

                # Cache endpoint lookups
                if ep_id and ep_id not in endpoints_cache:
                    ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
                    if ep:
                        endpoints_cache[ep_id] = {"base_url": ep.base_url, "api_key": ep.api_key}
                ep_data = endpoints_cache.get(ep_id)
                if not ep_data:
                    # Try to find by base_url from the model's endpoint field
                    endpoint_url = item.get("endpoint", "")
                    if endpoint_url:
                        ep_data = {"base_url": endpoint_url, "api_key": item.get("api_key", "")}
                    else:
                        results.append({"model": model_id, "status": "fail", "error": "Endpoint not found"})
                        continue

                base = _normalize_base(ep_data["base_url"])
                _with_tools = item.get("with_tools", False)
                result = _probe_single_model(base, ep_data.get("api_key"), model_id, timeout=8, with_tools=_with_tools)
                result["model"] = model_id
                result["endpoint_id"] = ep_id
                results.append(result)

            return {"results": results}
        finally:
            db.close()

    @router.get("/probe")
    def probe_models(request: Request, endpoint_id: Optional[str] = Query(None)):
        """Probe individual models with a tiny completion request. Streams SSE results."""
        require_admin(request)
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
            if endpoint_id:
                q = q.filter(ModelEndpoint.id == endpoint_id)
            endpoints = q.all()
            # Detach from session
            ep_data = []
            for ep in endpoints:
                ep_data.append({
                    "id": ep.id,
                    "name": ep.name,
                    "base_url": ep.base_url,
                    "api_key": ep.api_key,
                })
        finally:
            db.close()

        if not ep_data:
            def _empty():
                yield f"data: {json.dumps({'type': 'probe_done', 'total': 0, 'ok': 0})}\n\n"
            return StreamingResponse(_empty(), media_type="text/event-stream")

        def _stream():
            total = 0
            ok_count = 0
            for ep in ep_data:
                base = _normalize_base(ep["base_url"])
                all_models = _probe_endpoint(base, ep.get("api_key"))
                # Update cached_models in DB
                if all_models:
                    db2 = SessionLocal()
                    try:
                        ep_obj = db2.query(ModelEndpoint).filter(ModelEndpoint.id == ep["id"]).first()
                        if ep_obj:
                            ep_obj.cached_models = json.dumps(all_models)
                            db2.commit()
                    finally:
                        db2.close()
                if not all_models:
                    yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep['name'], 'model_count': 0, 'error': 'No models found or endpoint offline'})}\n\n"
                    continue

                models = [m for m in all_models if _is_chat_model(m)]
                skipped = len(all_models) - len(models)
                yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep['name'], 'model_count': len(models), 'skipped': skipped})}\n\n"

                for model_id in models:
                    total += 1
                    result = _probe_single_model(base, ep.get("api_key"), model_id, timeout=8)
                    result["type"] = "probe_result"
                    result["endpoint"] = ep["name"]
                    result["model"] = model_id
                    if result["status"] == "ok":
                        ok_count += 1
                    yield f"data: {json.dumps(result)}\n\n"

            yield f"data: {json.dumps({'type': 'probe_done', 'total': total, 'ok': ok_count})}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # /api/providers runs a full host port-scan (discover_models) which can take
    # seconds when a configured LLM host is unreachable. It's fetched on every
    # page load, so cache it briefly like _models_cache to keep page load snappy.
    _providers_cache = {"data": None, "time": 0}
    _PROVIDERS_CACHE_TTL = 30  # seconds

    @router.get("/providers")
    def providers(request: Request, refresh: bool = False):
        """Get all available providers (cached for 30s)."""
        require_admin(request)
        now = _time.time()
        if not refresh and _providers_cache["data"] is not None and (now - _providers_cache["time"]) < _PROVIDERS_CACHE_TTL:
            return _providers_cache["data"]
        result = model_discovery.get_providers()
        _providers_cache["data"] = result
        _providers_cache["time"] = now
        return result

    @router.get("/discover")
    def discover_local(request: Request):
        """Scan local network for model servers on common ports."""
        require_admin(request)
        return model_discovery.discover_models()

    # ---- Admin: model endpoints CRUD ----

    @router.get("/model-endpoints")
    def list_model_endpoints(request: Request) -> List[Dict[str, Any]]:
        require_admin(request)
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).order_by(ModelEndpoint.created_at).all()
            results = []
            for r in rows:
                all_models = _cached_model_ids(r)
                hidden = _hidden_model_ids(r)
                pinned = _normalize_model_ids(getattr(r, "pinned_models", None))
                visible = _visible_models(all_models, r.hidden_models, pinned)
                # Endpoint counts as reachable if it has any model — including
                # admin-pinned IDs that a probe would never surface.
                status = "online" if (all_models or pinned) else "offline"
                ping = None
                # When cached_models is empty, do a quick reachability probe.
                # Bumped 1.0s → 3.5s because the user reported endpoints they
                # were ACTIVELY chatting with showed "offline" — the previous
                # 1s timeout was clipping live cloud endpoints (DeepSeek can
                # take 1.5–2.5s on /v1/models when their region is under load,
                # vLLM on a remote GPU box behind SSH can also push past 1s).
                # 3.5s still keeps the picker render snappy in the common
                # "everything's already cached" path because this branch only
                # runs for endpoints with an empty cached_models.
                if not all_models and not pinned and r.is_enabled:
                    ping = _ping_endpoint(r.base_url, r.api_key, timeout=3.5)
                    if ping.get("reachable"):
                        status = "empty"
                        # Best-effort: if the probe came back reachable, try
                        # to populate cached_models in the background so the
                        # NEXT picker load shows "online" instead of "empty".
                        # Failure here is silent — we already returned the
                        # "empty" status, and the existing background refresh
                        # path will eventually fill it in too.
                        try:
                            probed = _probe_endpoint(r.base_url, r.api_key, timeout=5)
                            if probed:
                                r.cached_models = json.dumps(probed)
                                db.commit()
                                all_models = probed
                                visible = _visible_models(all_models, r.hidden_models, pinned)
                                status = "online"
                        except Exception as _refill_err:
                            logger.debug(f"opportunistic cached_models refill failed for {r.id}: {_refill_err!r}")
                base = _normalize_base(r.base_url)
                kind = _effective_endpoint_kind(r, base)
                results.append({
                    "id": r.id,
                    "name": r.name,
                    "base_url": r.base_url,
                    "has_key": bool(r.api_key),
                    "api_key_fingerprint": _api_key_fingerprint(r.api_key),
                    "is_enabled": r.is_enabled,
                    "models": visible,
                    "pinned_models": pinned,
                    "hidden_count": len(hidden),
                    "online": status != "offline",
                    "status": status,
                    "ping_error": (ping or {}).get("error") if ping else None,
                    "model_type": getattr(r, "model_type", None) or "llm",
                    "supports_tools": getattr(r, "supports_tools", None),
                    "endpoint_kind": kind,
                    "category": _classify_endpoint(base, kind),
                    "model_refresh_mode": _endpoint_refresh_mode(r, kind),
                    "model_refresh_interval": getattr(r, "model_refresh_interval", None),
                    "model_refresh_timeout": getattr(r, "model_refresh_timeout", None),
                })
            return results
        finally:
            db.close()

    @router.post("/model-endpoints")
    def create_model_endpoint(
        request: Request,
        name: str = Form(""),
        base_url: str = Form(...),
        api_key: str = Form(""),
        skip_probe: str = Form("false"),
        require_models: str = Form("false"),
        model_type: str = Form("llm"),
        endpoint_kind: str = Form("auto"),
        model_refresh_mode: str = Form(""),
        model_refresh_interval: str = Form(""),
        model_refresh_timeout: str = Form(""),
        supports_tools: str = Form(""),  # "true"/"false"/"" (unknown)
        pinned_models: str = Form(""),  # admin-pinned IDs: list/JSON/comma/newline
        container_local: str = Form("false"),
        # Default `shared=true` → endpoints are visible to all users (the
        # app's historical behaviour). Admins can pass `shared=false` to
        # scope a new endpoint to their own account only.
        shared: str = Form("true"),
    ):
        require_admin(request)
        base_url = _normalize_base(base_url)
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        # Resolve hostname via Tailscale if DNS fails
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)
        # In Docker, manually added loopback URLs usually point at a host-local
        # server. Cookbook local serves are launched inside Odysseus itself, so
        # keep those container-local when the frontend marks them as such.
        base_url = _rewrite_loopback_for_docker(base_url, container_local=_truthy(container_local))

        # Auto-generate name from URL if not provided
        if not name.strip():
            name = base_url.replace("http://", "").replace("https://", "").split("/")[0]

        requested_kind = _normalize_endpoint_kind(endpoint_kind)
        refresh_mode = _normalize_refresh_mode(model_refresh_mode, requested_kind)
        refresh_interval = _parse_positive_int(model_refresh_interval, minimum=30, maximum=86400)
        refresh_timeout = _parse_positive_int(model_refresh_timeout, minimum=1, maximum=60)
        require_model_list = _truthy(require_models)
        should_probe = (
            require_model_list or requested_kind in ("api", "proxy") or not _truthy(skip_probe)
        )
        explicit_timeout = _explicit_model_list_timeout(base_url, requested_kind, refresh_timeout)

        # Dedupe: if an endpoint with the same base_url already exists and
        # is reachable by the caller (shared or owned by them), return it
        # instead of creating a duplicate row. Fixes "Scan for Servers"
        # re-adding manually-added endpoints under their host:port name.
        from src.auth_helpers import get_current_user as _gcu_dedup
        _caller = _gcu_dedup(request) or None
        _incoming_api_key = api_key.strip()
        _db_dedup = SessionLocal()
        try:
            _same_url_rows = (
                _db_dedup.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == base_url)
                .filter((ModelEndpoint.owner.is_(None)) | (ModelEndpoint.owner == _caller))
                .order_by(ModelEndpoint.owner.desc())  # prefer owned over shared
                .all()
            )
            existing = None
            _empty_key_existing = None
            for _candidate in _same_url_rows:
                _candidate_key = (getattr(_candidate, "api_key", None) or "").strip()
                if _candidate_key == _incoming_api_key:
                    existing = _candidate
                    break
                if _incoming_api_key and not _candidate_key and _empty_key_existing is None:
                    _empty_key_existing = _candidate
            if existing is None and _incoming_api_key and _empty_key_existing is not None:
                existing = _empty_key_existing
            if existing:
                changed = False
                # Persist any incoming pinned IDs onto the existing row. An
                # empty/omitted form field must not wipe previously pinned IDs.
                _incoming_pinned = _normalize_model_ids(pinned_models)
                if _incoming_pinned:
                    _merged_pinned = _merge_model_ids(
                        _normalize_model_ids(getattr(existing, "pinned_models", None)),
                        _incoming_pinned,
                    )
                    existing.pinned_models = json.dumps(_merged_pinned) if _merged_pinned else None
                    changed = True
                existing_kind_for_probe = requested_kind if requested_kind != "auto" else _effective_endpoint_kind(existing, base_url)
                if requested_kind != "auto" and _endpoint_kind(existing) == "auto":
                    existing.endpoint_kind = requested_kind
                    changed = True
                if model_refresh_mode or (requested_kind == "proxy" and _endpoint_refresh_mode(existing, requested_kind) != refresh_mode):
                    existing.model_refresh_mode = refresh_mode
                    changed = True
                if refresh_interval is not None:
                    existing.model_refresh_interval = refresh_interval
                    changed = True
                if refresh_timeout is not None:
                    existing.model_refresh_timeout = refresh_timeout
                    changed = True
                if api_key.strip() and not existing.api_key:
                    existing.api_key = api_key.strip()
                    changed = True
                if should_probe:
                    probed_models = _probe_endpoint(
                        base_url,
                        (api_key.strip() or existing.api_key or None),
                        timeout=_explicit_model_list_timeout(base_url, existing_kind_for_probe, refresh_timeout),
                    )
                    if probed_models:
                        existing.cached_models = json.dumps(probed_models)
                        changed = True
                if changed:
                    _db_dedup.commit()
                    _invalidate_models_cache()
                    _local_probe_cache["data"] = None
                existing_models = _cached_model_ids(existing)
                _existing_pinned = _normalize_model_ids(getattr(existing, "pinned_models", None))
                existing_kind = _effective_endpoint_kind(existing, existing.base_url)
                return {
                    "id": existing.id,
                    "name": existing.name,
                    "base_url": existing.base_url,
                    "has_key": bool(existing.api_key),
                    "api_key_fingerprint": _api_key_fingerprint(existing.api_key),
                    "models": _visible_models(
                        existing_models,
                        getattr(existing, "hidden_models", None),
                        existing.pinned_models,
                    ),
                    "pinned_models": _existing_pinned,
                    "online": True,
                    "status": "online",
                    "existing": True,
                    "endpoint_kind": existing_kind,
                    "category": _classify_endpoint(existing.base_url, existing_kind),
                }
        finally:
            _db_dedup.close()

        model_ids = _probe_endpoint(base_url, api_key.strip() or None, timeout=explicit_timeout) if should_probe else []
        ping = {"reachable": False, "error": None}
        if (should_probe or requested_kind in ("api", "proxy")) and not model_ids:
            ping = _ping_endpoint(base_url, api_key.strip() or None, timeout=min(explicit_timeout, 2.0))
        if require_model_list and not model_ids:
            raise HTTPException(400, _model_endpoint_error_message(base_url, ping))

        ep_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            _st_raw = (supports_tools or "").strip().lower()
            _st = True if _st_raw in ("true", "1", "yes") else (False if _st_raw in ("false", "0", "no") else None)
            _pinned = _normalize_model_ids(pinned_models)
            # Stamp owner so the picker only shows this endpoint to the admin
            # who added it. Pass `shared=true` to mark it null-owner (visible
            # to all users), preserving the pre-fix "everyone sees everything"
            # behaviour for endpoints the admin explicitly intends to share.
            from src.auth_helpers import get_current_user as _gcu
            _shared_flag = (shared or "").strip().lower() in ("true", "1", "yes")
            _owner_val = None if _shared_flag else (_gcu(request) or None)
            ep = ModelEndpoint(
                id=ep_id,
                name=name.strip(),
                base_url=base_url,
                api_key=api_key.strip() or None,
                is_enabled=True,
                model_type=model_type.strip() if model_type else "llm",
                endpoint_kind=requested_kind,
                model_refresh_mode=refresh_mode,
                model_refresh_interval=refresh_interval,
                model_refresh_timeout=refresh_timeout,
                cached_models=json.dumps(model_ids) if model_ids else None,
                pinned_models=json.dumps(_pinned) if _pinned else None,
                supports_tools=_st,
                owner=_owner_val,
            )
            db.add(ep)
            db.commit()
            # Auto-set as default chat endpoint when none is usable yet — either
            # nothing is configured, or the configured default points at an
            # endpoint that is now missing/disabled (#3586). Seed the first CHAT
            # model (not raw model_ids[0]) so we don't pin the global default to
            # an embedding/tts/etc. entry a provider happens to list first.
            settings = _load_settings()
            enabled_ids = {
                e.id
                for e in db.query(ModelEndpoint).filter(
                    ModelEndpoint.is_enabled == True  # noqa: E712
                ).all()
            }
            if _default_endpoint_needs_assignment(settings.get("default_endpoint_id") or "", enabled_ids):
                from src.endpoint_resolver import _first_chat_model
                settings["default_endpoint_id"] = ep.id
                settings["default_model"] = _first_chat_model(model_ids) or ""
                _save_settings(settings)
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
        finally:
            db.close()

        # Return immediately — probing happens via the separate /probe SSE endpoint
        return {
            "id": ep_id,
            "name": name.strip(),
            "base_url": base_url,
            "has_key": bool(api_key.strip()),
            "api_key_fingerprint": _api_key_fingerprint(api_key),
            "models": _merge_model_ids(model_ids, _pinned),
            "pinned_models": _pinned,
            "online": bool(model_ids) or bool(_pinned) or bool(ping.get("reachable")),
            "status": "online" if (model_ids or _pinned) else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
            "endpoint_kind": requested_kind,
            "category": _classify_endpoint(base_url, requested_kind),
        }

    @router.post("/model-endpoints/test")
    def test_model_endpoint(
        request: Request,
        base_url: str = Form(...),
        api_key: str = Form(""),
        endpoint_kind: str = Form("auto"),
        model_refresh_timeout: str = Form(""),
    ):
        require_admin(request)
        base_url = _normalize_base(base_url)
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)
        base_url = _rewrite_loopback_for_docker(base_url)
        requested_kind = _normalize_endpoint_kind(endpoint_kind)
        configured_timeout = _parse_positive_int(model_refresh_timeout, minimum=1, maximum=60)
        probe_timeout = _explicit_model_list_timeout(base_url, requested_kind, configured_timeout)
        models = _probe_endpoint(base_url, api_key.strip() or None, timeout=probe_timeout)
        ping = {"reachable": True, "error": None} if models else _ping_endpoint(base_url, api_key.strip() or None, timeout=min(probe_timeout, 2.0))
        return {
            "base_url": base_url,
            "online": bool(models) or bool(ping.get("reachable")),
            "status": "online" if models else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
            "models": models,
            "count": len(models),
            "endpoint_kind": requested_kind,
            "category": _classify_endpoint(base_url, requested_kind),
        }

    @router.get("/model-endpoints/{ep_id}/probe")
    def probe_endpoint_models(ep_id: str, request: Request):
        """Re-probe all models on an endpoint. Updates hidden_models and streams SSE results."""
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            ep_data = {"id": ep.id, "name": ep.name, "base_url": ep.base_url, "api_key": ep.api_key}
        finally:
            db.close()

        base = _normalize_base(ep_data["base_url"])
        all_models = _probe_endpoint(base, ep_data["api_key"])
        chat_models = [m for m in all_models if _is_chat_model(m)]
        skipped = len(all_models) - len(chat_models)

        def _stream():
            yield f"data: {json.dumps({'type': 'probe_start', 'endpoint': ep_data['name'], 'model_count': len(chat_models), 'skipped': skipped})}\n\n"
            failed = []
            ok_count = 0
            for mid in chat_models:
                result = _probe_single_model(base, ep_data["api_key"], mid, timeout=8)
                result["model"] = mid
                result["type"] = "probe_result"
                result["endpoint"] = ep_data["name"]
                if result["status"] == "ok":
                    ok_count += 1
                else:
                    failed.append(mid)
                yield f"data: {json.dumps(result)}\n\n"

            # Update hidden_models and cached_models in DB
            db2 = SessionLocal()
            try:
                ep_obj = db2.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
                if ep_obj:
                    ep_obj.hidden_models = json.dumps(failed) if failed else None
                    if all_models:
                        ep_obj.cached_models = json.dumps(all_models)
                    db2.commit()
            finally:
                db2.close()
            _invalidate_models_cache()

            yield f"data: {json.dumps({'type': 'probe_done', 'total': len(all_models), 'ok': ok_count, 'hidden': len(failed)})}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @router.get("/model-endpoints/{ep_id}/models")
    def list_endpoint_models(
        ep_id: str,
        request: Request,
        response: Response,
        refresh: bool = False,
        refresh_timeout: Optional[int] = Query(None, ge=1, le=60),
    ):
        """List all discovered models for an endpoint with hidden/visible state."""
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            hidden = _hidden_model_ids(ep)
            all_models = _cached_model_ids(ep)
            if refresh:
                base = _normalize_base(ep.base_url)
                kind = _effective_endpoint_kind(ep, base)
                category = _classify_endpoint(base, kind)
                timeout = _manual_refresh_timeout(ep, category, refresh_timeout)
                try:
                    probed = _probe_endpoint(base, ep.api_key, timeout=timeout)
                except Exception as exc:
                    logger.warning("Manual model refresh failed for endpoint %s at %s: %s", ep_id, base, exc)
                    probed = []
                if probed:
                    all_models = probed
                    ep.cached_models = json.dumps(all_models)
                    db.commit()
                    _invalidate_models_cache()
                    response.headers["X-Model-Refresh-Status"] = "refreshed"
                    response.headers["X-Model-Refresh-Count"] = str(len(probed))
                else:
                    response.headers["X-Model-Refresh-Status"] = "failed"
                    response.headers["X-Model-Refresh-Warning"] = "Model refresh failed or returned no models; kept cached models."
            pinned = _normalize_model_ids(getattr(ep, "pinned_models", None))
            pinned_set = set(pinned)
            return [
                {
                    "id": m,
                    "display": m.split("/")[-1],
                    "is_hidden": m in hidden,
                    "is_pinned": m in pinned_set,
                }
                for m in _merge_model_ids(all_models, pinned)
            ]
        finally:
            db.close()

    @router.patch("/model-endpoints/{ep_id}/models")
    async def update_hidden_models(ep_id: str, request: Request):
        """Bulk update hidden and/or pinned model lists for an endpoint.

        Expects JSON body with optional keys:
          {"hidden": ["model-id-1", ...], "pinned_models": ["deploy-id", ...]}
        Each key is updated only when present, so callers can patch one list
        without clobbering the other.
        """
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            body = await request.json()
            if not isinstance(body, dict):
                raise HTTPException(400, "Body must be a JSON object")
            if "hidden" in body:
                hidden = body.get("hidden")
                if not isinstance(hidden, list):
                    raise HTTPException(400, "hidden must be a list of model IDs")
                ep.hidden_models = json.dumps(hidden) if hidden else None
            # Accept either "pinned" or "pinned_models" for the manual IDs list.
            if "pinned_models" in body or "pinned" in body:
                pinned = _normalize_model_ids(body.get("pinned_models", body.get("pinned")))
                ep.pinned_models = json.dumps(pinned) if pinned else None
            db.commit()
            _invalidate_models_cache()
            hidden_count = len(json.loads(ep.hidden_models)) if ep.hidden_models else 0
            pinned_count = len(json.loads(ep.pinned_models)) if ep.pinned_models else 0
            return {"id": ep_id, "hidden_count": hidden_count, "pinned_count": pinned_count}
        finally:
            db.close()

    @router.get("/default-chat")
    def get_default_chat(request: Request):
        # SECURITY: resolve the default endpoint + model from the CALLER's
        # per-user prefs ONLY. We deliberately do NOT fall back to the
        # global `default_model` / `default_endpoint_id` in settings.json
        # for authenticated users — that's what was leaking the previous
        # admin's pick into every new account's composer. If the user has
        # no per-user default yet, we resolve via the owner-scoped endpoint
        # lookup below (last-resort: first enabled endpoint THIS user owns).
        # Unauthenticated single-user mode keeps the old behavior.
        from src.auth_helpers import get_current_user as _gcu
        try:
            _user = _gcu(request) or ""
        except Exception:
            _user = ""
        # Admins resolve via the global defaults (they own them, and the
        # scoped resolution was making the picker disappear for them).
        # Regular users get per-user prefs with NO global fallback for the
        # model/endpoint values — that's what was leaking the previous
        # admin's pick into every new account's composer.
        settings = _load_settings()
        _is_admin = False
        try:
            auth_mgr = getattr(request.app.state, "auth_manager", None)
            if _user and auth_mgr is not None and getattr(auth_mgr, "is_admin", None):
                _is_admin = bool(auth_mgr.is_admin(_user))
        except Exception:
            _is_admin = False
        if _user and not _is_admin:
            from routes.prefs_routes import _load_for_user
            _user_prefs = _load_for_user(_user) or {}
            ep_id = (_user_prefs.get("default_endpoint_id") or "").strip()
            model = (_user_prefs.get("default_model") or "").strip()
            _fallbacks = _user_prefs.get("default_model_fallbacks") or []
        else:
            ep_id = settings.get("default_endpoint_id", "")
            model = settings.get("default_model", "")
            _fallbacks = settings.get("default_model_fallbacks") or []
        db = SessionLocal()
        try:
            ep = None
            if ep_id:
                ep_q = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == ep_id, ModelEndpoint.is_enabled == True
                )
                # Honor the same owner-scope rule as /api/models — a per-user
                # default that points at an endpoint owned by a different user
                # mustn't silently resolve. Admins are exempt (they manage the
                # global pool).
                if _user and not _is_admin:
                    ep_q = owner_filter(ep_q, ModelEndpoint, _user)
                ep = ep_q.first()
            # Configured fallback chain — when the chosen default endpoint is
            # gone/disabled, honor the user's configured `default_model_fallbacks`
            # in order BEFORE arbitrarily grabbing the first enabled endpoint.
            # (Previously this jumped straight to "first enabled", which is why
            # deleting/changing the main endpoint silently reassigned the default
            # chat to some unrelated endpoint instead of the fallback.)
            if not ep:
                for entry in _fallbacks:
                    if not isinstance(entry, dict):
                        continue
                    fid = (entry.get("endpoint_id") or "").strip()
                    if not fid:
                        continue
                    cand_q = db.query(ModelEndpoint).filter(
                        ModelEndpoint.id == fid, ModelEndpoint.is_enabled == True
                    )
                    if _user and not _is_admin:
                        cand_q = owner_filter(cand_q, ModelEndpoint, _user)
                    cand = cand_q.first()
                    if cand:
                        ep = cand
                        # Use the fallback entry's model. Reset even when empty
                        # so we don't carry the prior endpoint's stale model onto
                        # this fallback — the cached-models lookup below then
                        # fills it from the fallback endpoint.
                        model = (entry.get("model") or "").strip()
                        break
            # Last resort: first enabled endpoint owned by THIS user. Do not
            # include null-owner/shared endpoints here: a brand-new user with
            # no explicit default should not auto-open a pending chat using an
            # existing shared/admin endpoint. Shared endpoints remain visible
            # in the picker and still work when explicitly selected/saved.
            if not ep:
                _last_q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
                if _user and not _is_admin:
                    _last_q = owner_filter(_last_q, ModelEndpoint, _user, include_shared=False)
                ep = _last_q.first()
            if not ep:
                return {"endpoint_id": "", "endpoint_url": "", "model": ""}
            base = _normalize_base(ep.base_url)
            chat_url = build_chat_url(base)
            if not model and (getattr(ep, "cached_models", None) or getattr(ep, "pinned_models", None)):
                try:
                    visible = _visible_models(ep.cached_models, getattr(ep, "hidden_models", None), getattr(ep, "pinned_models", None))
                    if visible:
                        model = visible[0]
                except Exception:
                    pass
            return {"endpoint_id": ep.id, "endpoint_url": chat_url, "model": model}
        finally:
            db.close()

    @router.patch("/model-endpoints/{ep_id}")
    async def toggle_model_endpoint(ep_id: str, request: Request):
        require_admin(request)
        # Optional JSON body for field-targeted updates. No body → toggle is_enabled (legacy behaviour).
        body: Dict[str, Any] = {}
        try:
            if int(request.headers.get("content-length") or 0) > 0:
                body = await request.json()
                if not isinstance(body, dict):
                    body = {}
        except Exception:
            body = {}
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            if body:
                if "supports_tools" in body:
                    v = body["supports_tools"]
                    ep.supports_tools = {True: True, False: False, 'true': True, 'false': False, 1: True, 0: False}.get(v)
                if "is_enabled" in body:
                    v_ie = body['is_enabled']
                    ep.is_enabled = v_ie.lower() in ('true', '1', 'yes') if isinstance(v_ie, str) else bool(v_ie)
                if "name" in body and isinstance(body["name"], str):
                    ep.name = body["name"].strip() or ep.name
                if "model_type" in body and isinstance(body["model_type"], str):
                    ep.model_type = body["model_type"].strip() or ep.model_type
                if "pinned_models" in body:
                    _pinned = _normalize_model_ids(body["pinned_models"])
                    ep.pinned_models = json.dumps(_pinned) if _pinned else None
                if "endpoint_kind" in body:
                    ep.endpoint_kind = _normalize_endpoint_kind(body.get("endpoint_kind"))
                if "model_refresh_mode" in body:
                    ep.model_refresh_mode = _normalize_refresh_mode(body.get("model_refresh_mode"), _endpoint_kind(ep))
                if "model_refresh_interval" in body:
                    interval = _parse_positive_int(body.get("model_refresh_interval"), minimum=30, maximum=86400)
                    ep.model_refresh_interval = interval
                if "model_refresh_timeout" in body:
                    timeout = _parse_positive_int(body.get("model_refresh_timeout"), minimum=1, maximum=60)
                    ep.model_refresh_timeout = timeout
                # Rotating an API key used to require DELETE+POST, which wiped
                # endpoint_url/model from every session referencing the old base
                # URL. Allow in-place updates so the admin can change the key
                # (or correct a typo'd base URL) without nuking session state.
                if "api_key" in body and isinstance(body["api_key"], str):
                    _new_key = body["api_key"].strip()
                    # Empty string means "clear it" (e.g. local Ollama no longer needs a key).
                    ep.api_key = _new_key or None
                if "base_url" in body and isinstance(body["base_url"], str):
                    _new_base = body["base_url"].strip().rstrip("/")
                    for _suffix in ("/models", "/chat/completions", "/completions", "/v1/messages"):
                        if _new_base.endswith(_suffix):
                            _new_base = _new_base[: -len(_suffix)].rstrip("/")
                    _new_base = _normalize_base(_new_base)
                    if _new_base:
                        ep.base_url = _new_base
            else:
                ep.is_enabled = not ep.is_enabled
            db.commit()
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
            return {
                "id": ep.id,
                "is_enabled": ep.is_enabled,
                "supports_tools": ep.supports_tools,
                "name": ep.name,
                "model_type": ep.model_type,
                "base_url": ep.base_url,
                "pinned_models": _normalize_model_ids(getattr(ep, "pinned_models", None)),
                "endpoint_kind": getattr(ep, "endpoint_kind", None) or "auto",
                "model_refresh_mode": getattr(ep, "model_refresh_mode", None) or "auto",
                "model_refresh_interval": getattr(ep, "model_refresh_interval", None),
                "model_refresh_timeout": getattr(ep, "model_refresh_timeout", None),
            }
        finally:
            db.close()

    def _settings_using_endpoint(ep_id: str) -> list:
        """Return human-readable labels for settings that reference this endpoint."""
        return _endpoint_settings_using_endpoint(_load_settings(), ep_id, include_speech=True)

    def _clear_settings_for_endpoint(ep_id: str) -> list:
        """Clear all settings that reference this endpoint. Returns list of cleared labels."""
        settings = _load_settings()
        cleared = _clear_endpoint_settings_for_endpoint(settings, ep_id, include_speech=True)
        if cleared:
            _save_settings(settings)
        return cleared

    def _clear_user_prefs_for_endpoint(ep_id: str) -> int:
        """Clear per-user endpoint selections and fallback chains."""
        try:
            from routes.prefs_routes import _load as _load_prefs, _save as _save_prefs
            all_prefs = _load_prefs()
            cleared_users = _clear_user_pref_endpoint_refs(all_prefs, ep_id)
            if cleared_users:
                _save_prefs(all_prefs)
            return cleared_users
        except Exception as e:
            logger.warning("Failed to clear user prefs for endpoint %s: %s", ep_id, e)
            return 0

    def _session_uses_endpoint_url(session_url: str, base_url: str) -> bool:
        if not session_url or not base_url:
            return False
        sess = session_url.rstrip("/")
        base = _normalize_base(base_url).rstrip("/")
        variants = {
            base,
            base + "/chat/completions",
            build_chat_url(base).rstrip("/"),
        }
        return sess in variants or sess.startswith(base + "/")

    def _clear_sessions_for_endpoint(db, base_url: str) -> int:
        """Drop stored auth for sessions using an endpoint being deleted.

        Keep the session's endpoint URL and model intact. If the admin is
        replacing an endpoint with the same URL, clearing those fields leaves
        the UI looking selected while chat requests arrive with an empty model.
        The chat-time orphan guard still clears truly dead endpoints when no
        matching enabled endpoint exists.
        """
        cleared = 0
        rows = db.query(DbSession).filter(DbSession.endpoint_url.isnot(None)).all()
        for row in rows:
            if _session_uses_endpoint_url(row.endpoint_url or "", base_url):
                row.headers = {}
                row.updated_at = datetime.utcnow()
                cleared += 1
        return cleared

    def _clear_loaded_sessions_for_endpoint(base_url: str) -> int:
        try:
            from src.ai_interaction import get_session_manager
            manager = get_session_manager()
        except Exception:
            manager = None
        if not manager:
            return 0
        cleared = 0
        try:
            for sess in list(getattr(manager, "sessions", {}).values()):
                if _session_uses_endpoint_url(getattr(sess, "endpoint_url", "") or "", base_url):
                    sess.headers = {}
                    cleared += 1
        except Exception:
            return cleared
        return cleared

    @router.get("/model-endpoints/{ep_id}/dependents")
    def get_endpoint_dependents(ep_id: str, request: Request):
        """Check which settings depend on this endpoint."""
        require_admin(request)
        return {"dependents": _settings_using_endpoint(ep_id)}

    @router.delete("/model-endpoints/{ep_id}")
    def delete_model_endpoint(ep_id: str, request: Request):
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            # Clean up any settings that reference this endpoint
            cleared = _clear_settings_for_endpoint(ep_id)
            cleared_user_preferences = _clear_user_prefs_for_endpoint(ep_id)
            cleared_sessions = _clear_sessions_for_endpoint(db, ep.base_url)
            cleared_loaded_sessions = _clear_loaded_sessions_for_endpoint(ep.base_url)
            auth_id = getattr(ep, "provider_auth_id", None)
            db.delete(ep)
            cleared_provider_auth = _delete_orphaned_provider_auth(db, auth_id, exclude_ep_id=ep_id)
            db.commit()
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
            return {
                "deleted": True,
                "cleared_settings": cleared,
                "cleared_user_preferences": cleared_user_preferences,
                "cleared_sessions": cleared_sessions,
                "cleared_loaded_sessions": cleared_loaded_sessions,
                "cleared_provider_auth": cleared_provider_auth,
            }
        finally:
            db.close()

    # ── Tool management ──

    @router.get("/tools")
    def list_tools():
        """List all available tools with their enabled/disabled status."""
        from src.agent_tools import TOOL_TAGS
        settings = _load_settings()
        disabled = set(settings.get("disabled_tools", []))
        tools = []
        for tag in sorted(TOOL_TAGS):
            tools.append({"id": tag, "enabled": tag not in disabled})
        return {"tools": tools}

    class ToolsUpdate(BaseModel):
        disabled: list = []

    @router.post("/tools")
    def update_tools(body: ToolsUpdate, request: Request):
        """Update which tools are disabled."""
        require_admin(request)
        settings = _load_settings()
        settings["disabled_tools"] = body.disabled
        _save_settings(settings)
        return {"ok": True, "disabled": body.disabled}

    return router

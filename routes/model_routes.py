# routes/model_routes.py
"""Routes for model and provider management."""
import re
import uuid
import json
import time as _time
import logging
import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Form, Query, Body, Request
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from core.database import SessionLocal, ModelEndpoint, Session as DbSession
from core.middleware import require_admin
from src.llm_core import _detect_provider, ANTHROPIC_MODELS
from src.settings import load_settings as _load_settings, save_settings as _save_settings
from src.endpoint_resolver import normalize_base as _normalize_base, build_chat_url
from src.auth_helpers import owner_filter

logger = logging.getLogger(__name__)


def _anthropic_api_root(base: str) -> str:
    """Return Anthropic's API root without duplicating /v1."""
    base = (base or "").strip().rstrip("/")
    host = urlparse(base).hostname or ""
    if host.endswith("anthropic.com") and base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


def _ollama_api_root(base: str) -> str:
    """Return Ollama's native API root without depending on deferred imports."""
    base = (base or "").strip().rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api"):
        return base
    if host.endswith("ollama.com"):
        root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://ollama.com"
        return root.rstrip("/") + "/api"
    return base


def _models_url(base: str) -> str:
    """Return provider-specific model-list URL for route-local probing."""
    provider = _detect_provider(base)
    host = urlparse(base).hostname or ""
    if provider == "anthropic" or host.endswith("anthropic.com"):
        return _anthropic_api_root(base) + "/v1/models"
    if provider == "ollama" or host.endswith("ollama.com"):
        return _ollama_api_root(base) + "/tags"
    return base.rstrip("/") + "/models"


def _provider_headers(api_key: Optional[str], base: str) -> Dict[str, str]:
    """Build provider auth headers without depending on import-time stubs."""
    if not api_key:
        return {}
    provider = _detect_provider(base)
    host = urlparse(base).hostname or ""
    if provider == "anthropic" or host.endswith("anthropic.com"):
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {"Authorization": f"Bearer {api_key}"}


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
        "glm-5", "glm-4.7", "glm-4.7-flash",
        "glm-4.6", "glm-4.6v",
        "glm-4.5", "glm-4.5v", "glm-4.5-air", "glm-4.5-flash",
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

# Map URL substrings → curated-list keys for providers whose _detect_provider()
# returns a generic value (e.g. "openai") but deserve their own curated list.
# "openrouter" is a sentinel meaning "no curation — show all models as curated".
_URL_TO_CURATED = {
    "z.ai": "zai",
    "api.deepseek.com": "deepseek",
    "api.groq.com": "groq",
    "api.mistral.ai": "mistral",
    "api.together.xyz": "together",
    "api.fireworks.ai": "fireworks",
    "generativelanguage.googleapis.com": "google",
    "api.x.ai": "xai",
    "openrouter.ai": "openrouter",
    "ollama.com": "ollama",
}


def _match_provider_curated(base_url: str, provider: str) -> str:
    """Return the curated-list key for a given endpoint.

    Checks the base URL against _URL_TO_CURATED first, then falls back
    to the raw provider string from _detect_provider().
    """
    for substring, key in _URL_TO_CURATED.items():
        if substring in (base_url or ""):
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


# Prefixes/substrings for models that are NOT chat-completions-capable
_NON_CHAT_PREFIXES = (
    "dall-e", "tts-", "whisper", "text-embedding", "embedding",
    "davinci", "babbage", "moderation", "omni-moderation",
    "sora", "gpt-image", "chatgpt-image",
)
_NON_CHAT_CONTAINS = (
    "-realtime", "-transcribe", "-tts", "-codex",
    "codex-",
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


def _probe_single_model(base: str, api_key: str, model_id: str, timeout: int = 10, with_tools: bool = False) -> dict:
    """Send a realistic completion request to a single model. Returns {status, latency_ms, error?}."""
    provider = _detect_provider(base)
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
        h = _provider_headers(api_key, base)
        h["Content-Type"] = "application/json"
        payload = _build_ollama_payload(model_id, messages, 0.0, 5, stream=False, tools=_test_tools)
    else:
        target_url = build_chat_url(base)
        h = _provider_headers(api_key, base)
        h["Content-Type"] = "application/json"
        from src.llm_core import _uses_max_completion_tokens
        _max_key = "max_completion_tokens" if _uses_max_completion_tokens(model_id) else "max_tokens"
        payload = {"model": model_id, "messages": messages, _max_key: 5, "temperature": 0.0}
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


def _classify_endpoint(base_url: str) -> str:
    """Return 'local' if the endpoint URL points to a private/local address, else 'api'.
    Includes the Tailscale CGNAT range (100.64.0.0/10) so tailnet-hosted
    servers (e.g. Cookbook serve endpoints) get reachability-probed too."""
    try:
        host = urlparse(base_url).hostname or ""
        if host in _LOCAL_HOSTS or host.startswith(_PRIVATE_PREFIXES):
            return "local"
        if _TAILSCALE_RE.match(host):
            return "local"
    except Exception:
        pass
    return "api"



def _probe_endpoint(base_url: str, api_key: str = None, timeout: int = 5) -> List[str]:
    """Probe a base URL's /models endpoint and return list of model IDs.
    For Anthropic, queries their /v1/models API, falling back to hardcoded list."""
    from src.endpoint_resolver import resolve_url
    base = resolve_url(_normalize_base(base_url))
    if _detect_provider(base) == "anthropic":
        # Try Anthropic's /v1/models endpoint first
        url = _anthropic_api_root(base) + "/v1/models"
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        try:
            r = httpx.get(url, headers=headers, timeout=timeout)
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
    url = _models_url(base)
    headers = _provider_headers(api_key, base)
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        # OpenAI format: {"data": [{"id": "model-name"}]}
        models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        # Ollama format: {"models": [{"name": "model-name"}]}
        if not models:
            models = [m.get("name") or m.get("model") for m in (data.get("models") or []) if m.get("name") or m.get("model")]
        if models:
            return models
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
            r = httpx.get(root + "/api/tags", timeout=timeout)
            r.raise_for_status()
            data = r.json()
            models = [m.get("name") or m.get("model") for m in (data.get("models") or []) if m.get("name") or m.get("model")]
            if models:
                return models
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
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    url = base + "/models"
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
        if 300 <= r.status_code < 400:
            loc = r.headers.get("location", "")
            if loc.startswith("/login") or "/login" in loc:
                return {
                    "reachable": False,
                    "status_code": r.status_code,
                    "error": "That is Odysseus, not a model server. Use the Ollama URL, usually http://host.docker.internal:11434/v1 in Docker.",
                }
            return {"reachable": False, "status_code": r.status_code, "error": f"HTTP {r.status_code} redirect"}
        if r.status_code < 500:
            return {"reachable": r.status_code < 400, "status_code": r.status_code, "error": None if r.status_code < 400 else f"HTTP {r.status_code}"}
    except Exception as e:
        last_error = str(e)[:120]
    else:
        last_error = f"HTTP {r.status_code}"

    try:
        parsed = urlparse(base)
        if parsed.port == 11434 or "ollama" in (parsed.hostname or "").lower():
            root = base[:-3].rstrip("/") if base.endswith("/v1") else base
            for path in ("/api/version", "/api/tags"):
                try:
                    r = httpx.get(root + path, timeout=timeout)
                    if r.status_code < 400:
                        return {"reachable": True, "status_code": r.status_code, "error": None}
                    last_error = f"HTTP {r.status_code}"
                except Exception as e:
                    last_error = str(e)[:120]
    except Exception:
        pass

    return {"reachable": False, "status_code": None, "error": last_error}


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

    # Track endpoints that have failed recently so we back off probing dead ones.
    _probe_failures = {}  # ep_id → (last_fail_ts, consecutive_fails)
    _refresh_inflight = {"v": False}  # coarse single-flight guard

    def _refresh_caches_bg():
        """Background thread: re-probe all endpoints in PARALLEL with a tight
        timeout, skipping endpoints that have been failing repeatedly.

        Was the cause of gradual server degradation: sequential 3s-timeout
        probes against many endpoints (some offline) tied up the threadpool
        for 15-30s every cache cycle, eventually exhausting it."""
        import threading
        if _refresh_inflight["v"]:
            return  # already running
        _refresh_inflight["v"] = True

        def _do():
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                db = SessionLocal()
                try:
                    endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
                    # Skip endpoints that have failed 3+ times in a row in the last 5 min
                    now = _time.time()
                    to_probe = []
                    for ep in endpoints:
                        ts, fails = _probe_failures.get(ep.id, (0, 0))
                        if fails >= 3 and (now - ts) < 300:
                            continue
                        to_probe.append(ep)

                    def _probe_one(ep):
                        base = _normalize_base(ep.base_url)
                        try:
                            ids = _probe_endpoint(base, ep.api_key, timeout=2)
                            return ep, ids, None
                        except Exception as e:
                            return ep, None, e

                    if to_probe:
                        # Bounded parallelism — 8 concurrent probes is plenty
                        with ThreadPoolExecutor(max_workers=min(8, len(to_probe))) as pool:
                            futures = [pool.submit(_probe_one, ep) for ep in to_probe]
                            for fut in as_completed(futures):
                                ep, ids, err = fut.result()
                                if ids:
                                    ep.cached_models = json.dumps(ids)
                                    _probe_failures.pop(ep.id, None)
                                else:
                                    prev = _probe_failures.get(ep.id, (0, 0))
                                    _probe_failures[ep.id] = (_time.time(), prev[1] + 1)
                        db.commit()
                finally:
                    db.close()
                _invalidate_models_cache()
            except Exception:
                pass
            finally:
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
            provider = _detect_provider(base)
            # Use cached models — background refresh keeps them updated
            model_ids = []
            if ep.cached_models:
                try:
                    model_ids = json.loads(ep.cached_models)
                except Exception:
                    pass
            ep_model_type = getattr(ep, "model_type", None) or "llm"
            # Filter out hidden (probe-failed) models
            hidden = set()
            if ep.hidden_models:
                try:
                    hidden = set(json.loads(ep.hidden_models))
                except Exception:
                    pass
            model_ids = [m for m in model_ids if m not in hidden]
            # Build correct URL based on provider
            chat_url = build_chat_url(base)
            category = _classify_endpoint(base)

            if model_ids:
                curated_key = _match_provider_curated(base, None)
                curated, extra = _curate_models(model_ids, curated_key)
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
            if not owner and auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
                raise HTTPException(401, "Not authenticated")
        except HTTPException:
            raise
        except Exception:
            pass
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
        _refresh_caches_bg()
        return result

    # Brief cache for local-probe results so picker-open doesn't hammer
    # /v1/models every time. 8s TTL — long enough to amortize cost,
    # short enough that a freshly-killed local server shows as offline
    # within ~8s of the user noticing.
    _LOCAL_PROBE_TTL = 8.0
    _local_probe_cache: Dict[str, Any] = {"data": None, "time": 0.0}

    @router.get("/model-endpoints/probe-local")
    async def probe_local_endpoints(request: Request):
        """Fast parallel reachability check for LOCAL endpoints only.
        Cloud endpoints (api.openai.com, api.anthropic.com, etc.) are
        assumed up. Local endpoints get a 1.5s /models probe so the UI
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
            local_eps = [
                (ep.id, _normalize_base(ep.base_url), ep.api_key)
                for ep in endpoints
                if _classify_endpoint(_normalize_base(ep.base_url)) == "local"
            ]
        finally:
            db.close()

        async def _probe_one(ep_id: str, base: str, api_key: Optional[str]) -> Dict[str, Any]:
            t0 = _time.time()
            try:
                models = _probe_endpoint(base, api_key, timeout=2.5)
                lat = round((_time.time() - t0) * 1000)
                return {
                    "alive": bool(models),
                    "latency_ms": lat,
                    "status_code": 200 if models else None,
                    "error": None if models else "No models found",
                }
            except Exception as e:
                return {"alive": False, "latency_ms": None, "status_code": None, "error": str(e)[:120]}

        import asyncio as _asyncio
        results_list = await _asyncio.gather(
            *[_probe_one(eid, base, key) for eid, base, key in local_eps],
            return_exceptions=False,
        )
        results: Dict[str, Any] = {}
        for (eid, _, _), r in zip(local_eps, results_list):
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
            provider = _detect_provider(base)
            entry = {
                "id": ep.id,
                "name": ep.name,
                "base_url": base,
                "provider": provider,
                "category": _classify_endpoint(base),
            }
            if provider == "anthropic":
                # Anthropic has no /models endpoint; just check connectivity
                try:
                    t0 = _time.time()
                    r = httpx.get(base.rstrip("/"), timeout=5)
                    entry["latency_ms"] = round((_time.time() - t0) * 1000)
                    entry["status"] = "online"
                    entry["model_count"] = len(ANTHROPIC_MODELS)
                except Exception as e:
                    entry["latency_ms"] = None
                    entry["status"] = "offline"
                    entry["error"] = str(e)
                    entry["model_count"] = 0
            else:
                url = _models_url(base)
                headers = _provider_headers(ep.api_key, base)
                try:
                    t0 = _time.time()
                    r = httpx.get(url, headers=headers, timeout=5)
                    entry["latency_ms"] = round((_time.time() - t0) * 1000)
                    r.raise_for_status()
                    data = r.json()
                    models = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                    if not models:
                        models = [
                            m.get("name") or m.get("model")
                            for m in (data.get("models") or [])
                            if m.get("name") or m.get("model")
                        ]
                    entry["status"] = "online"
                    entry["model_count"] = len(models)
                except Exception as e:
                    if "latency_ms" not in entry:
                        entry["latency_ms"] = None
                    entry["status"] = "offline"
                    entry["error"] = str(e)
                    entry["model_count"] = 0
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
    def providers(refresh: bool = False):
        """Get all available providers (cached for 30s)."""
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
                # Use cached model list to avoid slow probe on every load
                all_models = []
                if r.cached_models:
                    try:
                        all_models = json.loads(r.cached_models)
                    except Exception:
                        pass
                hidden = set()
                if r.hidden_models:
                    try:
                        hidden = set(json.loads(r.hidden_models))
                    except Exception:
                        pass
                visible = [m for m in all_models if m not in hidden]
                status = "online" if all_models else "offline"
                ping = None
                if not all_models and r.is_enabled:
                    ping = _ping_endpoint(r.base_url, r.api_key, timeout=1.0)
                    if ping.get("reachable"):
                        status = "empty"
                results.append({
                    "id": r.id,
                    "name": r.name,
                    "base_url": r.base_url,
                    "has_key": bool(r.api_key),
                    "is_enabled": r.is_enabled,
                    "models": visible,
                    "hidden_count": len(hidden),
                    "online": status != "offline",
                    "status": status,
                    "ping_error": (ping or {}).get("error") if ping else None,
                    "model_type": getattr(r, "model_type", None) or "llm",
                    "supports_tools": getattr(r, "supports_tools", None),
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
        supports_tools: str = Form(""),  # "true"/"false"/"" (unknown)
        # Default `shared=true` → endpoints are visible to all users (the
        # app's historical behaviour). Admins can pass `shared=false` to
        # scope a new endpoint to their own account only.
        shared: str = Form("true"),
    ):
        require_admin(request)
        base_url = base_url.strip().rstrip("/")
        # Normalize: strip trailing /models, /chat/completions, /v1/messages etc to get clean base
        for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"]:
            if base_url.endswith(suffix):
                base_url = base_url[:-len(suffix)].rstrip("/")
        base_url = _normalize_base(base_url)
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        # Resolve hostname via Tailscale if DNS fails
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)

        # Auto-generate name from URL if not provided
        if not name.strip():
            name = base_url.replace("http://", "").replace("https://", "").split("/")[0]

        require_model_list = _truthy(require_models)
        should_probe = require_model_list or not _truthy(skip_probe)

        # Dedupe: if an endpoint with the same base_url already exists and
        # is reachable by the caller (shared or owned by them), return it
        # instead of creating a duplicate row. Fixes "Scan for Servers"
        # re-adding manually-added endpoints under their host:port name.
        from src.auth_helpers import get_current_user as _gcu_dedup
        _caller = _gcu_dedup(request) or None
        _db_dedup = SessionLocal()
        try:
            existing = (
                _db_dedup.query(ModelEndpoint)
                .filter(ModelEndpoint.base_url == base_url)
                .filter((ModelEndpoint.owner.is_(None)) | (ModelEndpoint.owner == _caller))
                .order_by(ModelEndpoint.owner.desc())  # prefer owned over shared
                .first()
            )
            if existing:
                return {
                    "id": existing.id,
                    "name": existing.name,
                    "base_url": existing.base_url,
                    "models": json.loads(existing.cached_models) if existing.cached_models else [],
                    "online": True,
                    "status": "online",
                    "existing": True,
                }
        finally:
            _db_dedup.close()

        # Quick model list fetch (1s timeout — if endpoint is slow, it'll update on next refresh)
        _probe_timeout = 3 if (":11434" in base_url or "ollama" in base_url.lower()) else 1
        model_ids = _probe_endpoint(base_url, api_key.strip() or None, timeout=_probe_timeout) if should_probe else []
        ping = {"reachable": False, "error": None}
        if should_probe and not model_ids:
            ping = _ping_endpoint(base_url, api_key.strip() or None, timeout=_probe_timeout)
        if require_model_list and not model_ids:
            raise HTTPException(400, "No models found for that provider/key")

        ep_id = str(uuid.uuid4())[:8]
        db = SessionLocal()
        try:
            _st_raw = (supports_tools or "").strip().lower()
            _st = True if _st_raw in ("true", "1", "yes") else (False if _st_raw in ("false", "0", "no") else None)
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
                cached_models=json.dumps(model_ids) if model_ids else None,
                supports_tools=_st,
                owner=_owner_val,
            )
            db.add(ep)
            db.commit()
            # Auto-set as default chat endpoint if none configured yet
            settings = _load_settings()
            if not settings.get("default_endpoint_id"):
                settings["default_endpoint_id"] = ep.id
                settings["default_model"] = model_ids[0] if model_ids else ""
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
            "models": model_ids,
            "online": bool(model_ids) or bool(ping.get("reachable")),
            "status": "online" if model_ids else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
        }

    @router.post("/model-endpoints/test")
    def test_model_endpoint(
        request: Request,
        base_url: str = Form(...),
        api_key: str = Form(""),
    ):
        require_admin(request)
        base_url = base_url.strip().rstrip("/")
        for suffix in ["/models", "/chat/completions", "/completions", "/v1/messages"]:
            if base_url.endswith(suffix):
                base_url = base_url[:-len(suffix)].rstrip("/")
        if not base_url:
            raise HTTPException(400, "Base URL is required")
        from src.endpoint_resolver import resolve_url
        base_url = resolve_url(base_url)
        probe_timeout = 3 if (":11434" in base_url or "ollama" in base_url.lower()) else 2
        models = _probe_endpoint(base_url, api_key.strip() or None, timeout=probe_timeout)
        ping = {"reachable": True, "error": None} if models else _ping_endpoint(base_url, api_key.strip() or None, timeout=probe_timeout)
        return {
            "base_url": base_url,
            "online": bool(models) or bool(ping.get("reachable")),
            "status": "online" if models else ("empty" if ping.get("reachable") else "offline"),
            "ping_error": ping.get("error") if ping else None,
            "models": models,
            "count": len(models),
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
                    ep_obj.cached_models = json.dumps(all_models) if all_models else None
                    db2.commit()
            finally:
                db2.close()
            _invalidate_models_cache()

            yield f"data: {json.dumps({'type': 'probe_done', 'total': len(all_models), 'ok': ok_count, 'hidden': len(failed)})}\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    @router.get("/model-endpoints/{ep_id}/models")
    def list_endpoint_models(ep_id: str, request: Request):
        """List all discovered models for an endpoint with hidden/visible state."""
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            hidden = set()
            if ep.hidden_models:
                try:
                    hidden = set(json.loads(ep.hidden_models))
                except Exception:
                    pass
            # Try live probe, fall back to cached
            all_models = _probe_endpoint(ep.base_url, ep.api_key, timeout=3)
            if all_models:
                ep.cached_models = json.dumps(all_models)
                db.commit()
            elif ep.cached_models:
                try:
                    all_models = json.loads(ep.cached_models)
                except Exception:
                    pass
            return [
                {"id": m, "display": m.split("/")[-1], "is_hidden": m in hidden}
                for m in all_models
            ]
        finally:
            db.close()

    @router.patch("/model-endpoints/{ep_id}/models")
    async def update_hidden_models(ep_id: str, request: Request):
        """Bulk update hidden models list for an endpoint.

        Expects JSON body: {"hidden": ["model-id-1", "model-id-2"]}
        """
        require_admin(request)
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            body = await request.json()
            hidden = body.get("hidden", [])
            if not isinstance(hidden, list):
                raise HTTPException(400, "hidden must be a list of model IDs")
            ep.hidden_models = json.dumps(hidden) if hidden else None
            db.commit()
            _invalidate_models_cache()
            return {"id": ep_id, "hidden_count": len(hidden)}
        finally:
            db.close()

    @router.get("/default-chat")
    def get_default_chat(request: Request):
        import json as _json
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
            if not model and getattr(ep, "cached_models", None):
                try:
                    models = _json.loads(ep.cached_models) if isinstance(ep.cached_models, str) else ep.cached_models
                    if models:
                        model = models[0]
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
                    ep.supports_tools = bool(v) if v in (True, False, "true", "false", 1, 0) else None
                if "is_enabled" in body:
                    ep.is_enabled = bool(body["is_enabled"])
                if "name" in body and isinstance(body["name"], str):
                    ep.name = body["name"].strip() or ep.name
                if "model_type" in body and isinstance(body["model_type"], str):
                    ep.model_type = body["model_type"].strip() or ep.model_type
            else:
                ep.is_enabled = not ep.is_enabled
            db.commit()
            _invalidate_models_cache()
            return {
                "id": ep.id,
                "is_enabled": ep.is_enabled,
                "supports_tools": ep.supports_tools,
                "name": ep.name,
                "model_type": ep.model_type,
            }
        finally:
            db.close()

    # ── Settings fields that store an endpoint ID ──
    _EP_SETTING_FIELDS = {
        "default_endpoint_id":  ("default_model",  "Default Model"),
        "utility_endpoint_id":  ("utility_model",   "Utility Model"),
        "research_endpoint_id": ("research_model",  "Deep Research"),
        "task_endpoint_id":     ("task_model",       "Background Tasks"),
    }

    def _settings_using_endpoint(ep_id: str) -> list:
        """Return human-readable labels for settings that reference this endpoint."""
        settings = _load_settings()
        affected = []
        for ep_key, (_, label) in _EP_SETTING_FIELDS.items():
            if (settings.get(ep_key) or "") == ep_id:
                affected.append(label)
        tts_prov = settings.get("tts_provider") or ""
        if tts_prov == f"endpoint:{ep_id}":
            affected.append("Text to Speech")
        return affected

    def _clear_settings_for_endpoint(ep_id: str) -> list:
        """Clear all settings that reference this endpoint. Returns list of cleared labels."""
        settings = _load_settings()
        cleared = []
        for ep_key, (model_key, label) in _EP_SETTING_FIELDS.items():
            if (settings.get(ep_key) or "") == ep_id:
                settings[ep_key] = ""
                settings[model_key] = ""
                cleared.append(label)
        tts_prov = settings.get("tts_provider") or ""
        if tts_prov == f"endpoint:{ep_id}":
            settings["tts_provider"] = "disabled"
            settings["tts_model"] = "tts-1"
            cleared.append("Text to Speech")
        if cleared:
            _save_settings(settings)
        return cleared

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
        cleared = 0
        rows = db.query(DbSession).filter(DbSession.endpoint_url.isnot(None)).all()
        for row in rows:
            if _session_uses_endpoint_url(row.endpoint_url or "", base_url):
                row.endpoint_url = ""
                row.model = ""
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
                    sess.endpoint_url = ""
                    sess.model = ""
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
            cleared_sessions = _clear_sessions_for_endpoint(db, ep.base_url)
            cleared_loaded_sessions = _clear_loaded_sessions_for_endpoint(ep.base_url)
            db.delete(ep)
            db.commit()
            _invalidate_models_cache()
            _local_probe_cache["data"] = None
            return {
                "deleted": True,
                "cleared_settings": cleared,
                "cleared_sessions": cleared_sessions,
                "cleared_loaded_sessions": cleared_loaded_sessions,
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

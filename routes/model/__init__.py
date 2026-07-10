"""Model routes package — split from model_routes.py.

Usage (in app.py):
    from routes.model import setup_model_routes
"""

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

# setup_model_routes owns the live cache in a closure. Keep a stable module
# wrapper for provisioning routes that import this helper before or after setup.
_model_cache_invalidator = None


def _invalidate_models_cache() -> None:
    callback = _model_cache_invalidator
    if callback is not None:
        callback()

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



from routes.model._utils import (
    _PROVIDER_CURATED,
    _speech_settings_using_endpoint,
    _clear_speech_settings_for_endpoint,
    _endpoint_settings_using_endpoint,
    _clear_endpoint_settings_for_endpoint,
    _active_cookbook_endpoint_ids,
    _disable_stale_cookbook_local_endpoints,
    _clear_user_pref_endpoint_refs,
    _endpoint_visible_model_ids,
    _default_endpoint_needs_assignment,
    _docker_host_gateway_reachable,
    _container_loopback_reachable,
    _rewrite_loopback_for_docker,
    _match_provider_curated,
    _curate_models,
    _truthy,
    _normalize_endpoint_kind,
    _normalize_refresh_mode,
    _endpoint_kind,
    _endpoint_refresh_mode,
    _endpoint_refresh_interval,
    _endpoint_refresh_timeout,
    _manual_refresh_timeout,
    _parse_model_list,
    _parse_positive_int,
    _explicit_model_list_timeout,
    _cached_model_ids,
    _hidden_model_ids,
    _is_ollama_base,
    _is_chat_model,
    _delete_orphaned_provider_auth,
    _safe_detect_provider,
    _safe_build_models_url,
    _safe_build_headers,
    _supports_ollama_unload,
    _ollama_generate_url_for_unload,
    _ollama_root_url_for_unload,
    _ollama_loaded_models,
    _ollama_unload_model,
    _is_discovery_only_provider,
    _resolve_probe_key,
    _probe_single_model,
    _local_ip_literal,
    _classify_endpoint,
    _effective_endpoint_kind,
    _is_loading_model_response,
    _openai_model_ids,
    _ollama_model_names,
    _filter_probed_models,
    _probe_endpoint,
    _probe_endpoint_for_model_type,
    _local_health_probe_urls,
    _ping_endpoint,
    _model_endpoint_error_message,
    _normalize_model_ids,
    _merge_model_ids,
    _is_mlx_deepseek_v4_repo_id,
    _is_mlx_deepseek_v4_shim_id,
    _filter_mlx_deepseek_v4_repo_when_shimmed,
    _model_display_name,
    _visible_models,
    _api_key_fingerprint,
)


def _probe_endpoint_for_model_type(
    base_url: str,
    api_key: str = None,
    timeout: int = 5,
    model_type: str = "llm",
) -> List[str]:
    """Package-level bridge that preserves legacy monkeypatch boundaries."""
    return _probe_endpoint(
        base_url, api_key, timeout=timeout, include_non_chat=True
    )


def _rewrite_loopback_for_docker(
    base_url: str, *, container_local: bool = False
) -> str:
    """Package-level bridge using the package's patchable probe helpers."""
    from routes.model import _utils as _model_utils

    try:
        parsed = urlparse(base_url)
    except Exception:
        return base_url
    host = (parsed.hostname or "").lower()
    if host not in _model_utils._LOOPBACK_HOSTS:
        return base_url
    if container_local:
        if host in _model_utils._ANY_BIND_HOSTS:
            netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
            return urlunparse(parsed._replace(netloc=netloc))
        return base_url
    if host in _model_utils._ANY_BIND_HOSTS and not _docker_host_gateway_reachable():
        netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
        return urlunparse(parsed._replace(netloc=netloc))
    if _container_loopback_reachable(base_url):
        return base_url
    if not _docker_host_gateway_reachable():
        return base_url
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc))


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

    global _model_cache_invalidator
    _model_cache_invalidator = _invalidate_models_cache

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

    def _failure_delay(fails: int, *, empty_local: bool = False) -> float:
        if fails <= 0:
            return 0.0
        if empty_local:
            return min(5.0 * (2 ** max(0, fails - 1)), 30.0)
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
            "model_type": getattr(ep, "model_type", None) or "llm",
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
            empty_local = (
                not cached
                and category == "local"
                and str(getattr(ep, "id", "") or "").startswith("local-")
            )
            if now - last_failure < _failure_delay(fails, empty_local=empty_local):
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
                    if _disable_stale_cookbook_local_endpoints(db):
                        changed = True
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
                            "model_type": info["model_type"],
                            "timeout": info["timeout"],
                            "endpoint_ids": [],
                        })["endpoint_ids"].append(info["id"])

                    for key in groups:
                        st = _refresh_state.setdefault(key, {})
                        st["inflight"] = True
                        st["last_attempt"] = now

                    def _probe_one(key: str, data: Dict[str, Any]):
                        try:
                            ids = _probe_endpoint_for_model_type(
                                data["base"],
                                data.get("api_key"),
                                timeout=data.get("timeout") or 2,
                                model_type=data.get("model_type") or "llm",
                            )
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
            if _disable_stale_cookbook_local_endpoints(db):
                _invalidate_models_cache()
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
                    "models_display": [_model_display_name(mid) for mid in curated],
                    "models_extra": extra,
                    "models_extra_display": [_model_display_name(mid) for mid in extra],
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
    def api_models(request: Request, refresh: bool = False, background: bool = False):
        """Get available models — per-user (caller sees only their endpoints +
        legacy/shared null-owner rows). Cached per-user for 30s."""
        # Require auth; "" is the unconfigured single-user mode, treated as
        # "see everything" by _fetch_models.
        try:
            if getattr(request.state, "api_token", False):
                scopes = set(getattr(request.state, "api_token_scopes", []) or [])
                if "chat" not in scopes:
                    raise HTTPException(403, "API token is not scoped for chat")
                if not getattr(request.state, "api_token_owner", None):
                    raise HTTPException(403, "API token has no owner")
            owner = effective_user(request) or ""

            # Reject anonymous in configured deployments — no leaking the model
            # list to unauthenticated callers.
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
        # Kick off background refresh to update caches from live endpoints.
        # Page boot can opt out with background=false so opening Odysseus does
        # not start endpoint probes against slow/offline model servers.
        if background or refresh:
            _refresh_caches_bg(force=refresh)
        return result

    # Brief cache for local-probe results so picker-open doesn't hammer
    # endpoint health checks every time. 8s TTL — long enough to amortize cost,
    # short enough that a freshly-killed local server shows as offline
    # within ~8s of the user noticing.
    _LOCAL_PROBE_TTL = 8.0
    _local_probe_cache: Dict[str, Any] = {"data": None, "time": 0.0}
    _local_probe_inflight: Dict[str, Any] = {"task": None}

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

        import asyncio as _asyncio
        task = _local_probe_inflight.get("task")
        if task is not None and not task.done():
            return await task

        async def _compute_local_probe() -> Dict[str, Any]:
            db = SessionLocal()
            try:
                if _disable_stale_cookbook_local_endpoints(db):
                    _invalidate_models_cache()
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

            results_list = await _asyncio.gather(
                *[_probe_one(data) for data in grouped.values()],
                return_exceptions=False,
            )
            results: Dict[str, Any] = {}
            for data, r in zip(grouped.values(), results_list):
                for eid in data["endpoint_ids"]:
                    results[eid] = r

            _local_probe_cache["data"] = results
            _local_probe_cache["time"] = _time.time()
            return results

        task = _asyncio.create_task(_compute_local_probe())
        _local_probe_inflight["task"] = task
        try:
            return await task
        finally:
            if _local_probe_inflight.get("task") is task:
                _local_probe_inflight["task"] = None

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
                entry["status"] = "loading" if ping.get("loading") else ("online" if ping.get("reachable") or cached_count else "offline")
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
            if _disable_stale_cookbook_local_endpoints(db):
                _invalidate_models_cache()
            rows = db.query(ModelEndpoint).order_by(ModelEndpoint.created_at).all()
            results = []
            for r in rows:
                all_models = _cached_model_ids(r)
                hidden = _hidden_model_ids(r)
                pinned = _normalize_model_ids(getattr(r, "pinned_models", None))
                visible = _visible_models(all_models, r.hidden_models, pinned)
                # Keep the list route cache-only. It feeds Settings →
                # Added Models and must render immediately; explicit
                # Refresh/Probe endpoints do the network work.
                status = "online" if (all_models or pinned) else ("empty" if r.is_enabled else "offline")
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
                            probed = _probe_endpoint_for_model_type(
                                r.base_url,
                                r.api_key,
                                timeout=5,
                                model_type=getattr(r, "model_type", None) or "llm",
                            )
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
                incoming_model_type = (model_type or "").strip()
                existing_model_type = incoming_model_type or getattr(existing, "model_type", None) or "llm"
                if incoming_model_type and getattr(existing, "model_type", None) != incoming_model_type:
                    existing.model_type = incoming_model_type
                    changed = True
                if should_probe:
                    probed_models = _probe_endpoint_for_model_type(
                        base_url,
                        (api_key.strip() or existing.api_key or None),
                        timeout=_explicit_model_list_timeout(base_url, existing_kind_for_probe, refresh_timeout),
                        model_type=existing_model_type,
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

        requested_model_type = model_type.strip() if model_type else "llm"
        model_ids = (
            _probe_endpoint_for_model_type(
                base_url,
                api_key.strip() or None,
                timeout=explicit_timeout,
                model_type=requested_model_type,
            )
            if should_probe else []
        )
        ping = {"reachable": False, "error": None}
        if (should_probe or requested_kind in ("api", "proxy")) and not model_ids:
            ping = _ping_endpoint(base_url, api_key.strip() or None, timeout=min(explicit_timeout, 10.0))
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
                model_type=requested_model_type,
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
            current_default_id = settings.get("default_endpoint_id") or ""
            current_default_ep = None
            if current_default_id:
                current_default_ep = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == current_default_id
                ).first()
            if _default_endpoint_needs_assignment(
                current_default_id,
                enabled_ids,
                current_default_endpoint=current_default_ep,
                current_default_model=settings.get("default_model") or "",
            ):
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
            "status": "online" if (model_ids or _pinned) else ("loading" if ping.get("loading") else ("empty" if ping.get("reachable") else "offline")),
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
        model_type: str = Form("llm"),
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
        models = _probe_endpoint_for_model_type(
            base_url,
            api_key.strip() or None,
            timeout=probe_timeout,
            model_type=model_type,
        )
        ping = {"reachable": True, "error": None} if models else _ping_endpoint(base_url, api_key.strip() or None, timeout=min(probe_timeout, 2.0))
        return {
            "base_url": base_url,
            "online": bool(models) or bool(ping.get("reachable")),
            "status": "online" if models else ("loading" if ping.get("loading") else ("empty" if ping.get("reachable") else "offline")),
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
            ep_data = {
                "id": ep.id,
                "name": ep.name,
                "base_url": ep.base_url,
                "api_key": ep.api_key,
                "model_type": getattr(ep, "model_type", None) or "llm",
            }
        finally:
            db.close()

        base = _normalize_base(ep_data["base_url"])
        all_models = _probe_endpoint_for_model_type(
            base,
            ep_data["api_key"],
            model_type=ep_data.get("model_type") or "llm",
        )
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
                    probed = _probe_endpoint_for_model_type(
                        base,
                        ep.api_key,
                        timeout=timeout,
                        model_type=getattr(ep, "model_type", None) or "llm",
                    )
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
            # If user has no personal default, fall back to global default
            # But only based on the "share_defaults_with_users" flag
            # (only if share_defaults_with_users is enabled)
            if settings.get("share_defaults_with_users", False):
                if not ep_id:
                    ep_id = settings.get("default_endpoint_id", "")
                if not model:
                    model = settings.get("default_model", "")
                if not _fallbacks:
                    _fallbacks = settings.get("default_model_fallbacks") or []
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

    @router.post("/model-endpoints/{ep_id}/unload")
    async def unload_endpoint_model(ep_id: str, request: Request, response: Response):
        """Ask a supported local model runtime to unload one model."""
        require_admin(request)
        try:
            body = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}

        requested_model = str(body.get("model") or "").strip()
        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
            if not ep:
                raise HTTPException(404, "Endpoint not found")
            try:
                from src.endpoint_resolver import resolve_endpoint_runtime
                base_url, api_key = resolve_endpoint_runtime(ep, owner=getattr(ep, "owner", None))
            except Exception:
                base_url = _normalize_base(ep.base_url)
                api_key = ep.api_key
            visible_models = _visible_models(
                _cached_model_ids(ep),
                getattr(ep, "hidden_models", None),
                getattr(ep, "pinned_models", None),
            )
        finally:
            db.close()

        model = requested_model
        if not model:
            if len(visible_models) == 1:
                model = visible_models[0]
            else:
                response.status_code = 400
                return {
                    "ok": False,
                    "supported": False,
                    "detail": "Pick a model to unload from this endpoint.",
                }

        if not _supports_ollama_unload(base_url):
            response.status_code = 400
            return {
                "ok": False,
                "supported": False,
                "model": model,
                "detail": "Unload is currently supported for local Ollama endpoints only.",
            }

        return _ollama_unload_model(base_url, api_key, model)

    @router.post("/model-endpoints/unload-all")
    async def unload_all_endpoint_models(request: Request, response: Response):
        """Ask every supported local runtime to unload all currently loaded models."""
        require_admin(request)
        db = SessionLocal()
        try:
            endpoints = db.query(ModelEndpoint).all()
        finally:
            db.close()

        supported_endpoints = 0
        skipped_endpoints = 0
        requested = 0
        unloaded = 0
        results = []
        errors = []

        for ep in endpoints:
            try:
                from src.endpoint_resolver import resolve_endpoint_runtime
                base_url, api_key = resolve_endpoint_runtime(ep, owner=getattr(ep, "owner", None))
            except Exception:
                base_url = _normalize_base(getattr(ep, "base_url", "") or "")
                api_key = getattr(ep, "api_key", None)

            if not _supports_ollama_unload(base_url):
                skipped_endpoints += 1
                continue

            supported_endpoints += 1
            ep_label = getattr(ep, "name", None) or getattr(ep, "id", None) or base_url
            try:
                loaded_models = _ollama_loaded_models(base_url, api_key)
            except HTTPException as exc:
                errors.append({
                    "endpoint_id": getattr(ep, "id", None),
                    "endpoint": ep_label,
                    "detail": exc.detail,
                })
                continue

            for model in loaded_models:
                requested += 1
                try:
                    data = _ollama_unload_model(base_url, api_key, model)
                    unloaded += 1
                    results.append({
                        "endpoint_id": getattr(ep, "id", None),
                        "endpoint": ep_label,
                        "model": model,
                        "provider": data.get("provider", "ollama"),
                    })
                except HTTPException as exc:
                    errors.append({
                        "endpoint_id": getattr(ep, "id", None),
                        "endpoint": ep_label,
                        "model": model,
                        "detail": exc.detail,
                    })

        failed = len(errors)
        if failed:
            response.status_code = 207 if unloaded else 502

        if unloaded and failed:
            message = f"Unloaded {unloaded} loaded model{'' if unloaded == 1 else 's'}; {failed} failed."
        elif unloaded:
            message = f"Unloaded {unloaded} loaded model{'' if unloaded == 1 else 's'}."
        elif failed:
            message = f"Unload failed for {failed} runtime/model request{'' if failed == 1 else 's'}."
        elif supported_endpoints:
            message = "No loaded Ollama models found."
        else:
            message = "No supported local model runtimes found."

        return {
            "ok": failed == 0,
            "supported": supported_endpoints > 0,
            "provider": "ollama",
            "requested": requested,
            "unloaded": unloaded,
            "failed": failed,
            "supported_endpoints": supported_endpoints,
            "skipped_endpoints": skipped_endpoints,
            "results": results,
            "errors": errors[:10],
            "message": message,
        }

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

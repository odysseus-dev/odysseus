"""Operator core — capability health, result envelope, audit logging.

The Operator service is the single entry point the agent loop uses for
perception (Screenpipe), recall (PixelRAG / unified memory), spec traces,
desktop actions (Clicky), browser actions (CDP), and research fan-out.

Every capability is optional. Sidecar probes are cached (30 s) and failures
downgrade to structured envelopes with a remediation hint — a missing sidecar
must never raise into the agent loop or block the other capabilities.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 2.0
STATUS_CACHE_TTL = 30.0

CAP_SCREEN_PERCEPTION = "screen_perception"
CAP_PIXEL_RETRIEVAL = "pixel_retrieval"
CAP_SPEC_TRACER = "spec_tracer"
CAP_DESKTOP_ACTION = "desktop_action"
CAP_BROWSER_ACTION = "browser_action"
CAP_RESEARCH = "research"

HINTS = {
    CAP_SCREEN_PERCEPTION: "Start Screenpipe: deploy/scripts/start-screenpipe.ps1",
    CAP_PIXEL_RETRIEVAL: "Start the memory stack: deploy/scripts/start_pixelrag_local.ps1",
    CAP_SPEC_TRACER: "Send a capture from the SpecTracer extension first",
    CAP_DESKTOP_ACTION: "Start Clicky: POST /api/clicky/start (or deploy/scripts/start-clicky.ps1)",
    CAP_BROWSER_ACTION: "Start Chrome with --remote-debugging-port=9222",
    CAP_RESEARCH: "Configure a TinyFish, Perplexity, or Firecrawl API key in Admin settings",
}


def envelope(
    capability: str,
    ok: bool,
    data: Any = None,
    *,
    degraded: bool = False,
    reason: Optional[str] = None,
    hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Uniform result shape returned by every operator tool."""
    out: Dict[str, Any] = {"ok": ok, "capability": capability, "data": data, "degraded": degraded}
    if reason:
        out["reason"] = reason
    if hint:
        out["hint"] = hint
    return out


def degraded_envelope(capability: str, reason: str, hint: Optional[str] = None) -> Dict[str, Any]:
    return envelope(
        capability, False, degraded=True, reason=reason,
        hint=hint or HINTS.get(capability),
    )


# ── Environment / endpoints ──

def _stack_env() -> Dict[str, str]:
    """memory_stack.env values merged over os.environ (best effort)."""
    merged: Dict[str, str] = dict(os.environ)
    try:
        from tools.memory_stack_env import load_memory_stack_env
        merged.update(load_memory_stack_env() or {})
    except Exception:
        pass
    return merged


def _port(env: Dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key) or default)
    except (TypeError, ValueError):
        return default


def screenpipe_url() -> str:
    env = _stack_env()
    return f"http://127.0.0.1:{_port(env, 'SCREENPIPE_PORT', 3030)}"


def unified_memory_url() -> str:
    env = _stack_env()
    explicit = (env.get("UNIFIED_MEMORY_API_URL") or "").strip()
    if explicit:
        # May include a /query path; strip to the origin for health checks.
        return explicit.rstrip("/").rsplit("/query", 1)[0]
    return f"http://127.0.0.1:{_port(env, 'UNIFIED_MEMORY_API_PORT', 40001)}"


def pixelrag_url() -> str:
    env = _stack_env()
    return f"http://127.0.0.1:{_port(env, 'PIXELRAG_SERVE_PORT', 30001)}"


def clicky_worker_url() -> str:
    env = _stack_env()
    return f"http://127.0.0.1:{_port(env, 'CLICKY_WORKER_PORT', 40002)}"


def cdp_url() -> str:
    env = _stack_env()
    explicit = (env.get("OPERATOR_CDP_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"http://127.0.0.1:{_port(env, 'OPERATOR_CDP_PORT', 9222)}"


def _fetch_json(url: str, timeout: float = PROBE_TIMEOUT) -> Optional[Dict[str, Any]]:
    req = request.Request(url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


# ── Capability probes ──
# Each probe returns (available, detail). Probes must be cheap and never raise.

def _probe_screen_perception() -> Tuple[bool, Dict[str, Any]]:
    url = screenpipe_url()
    health = _fetch_json(f"{url}/health")
    return bool(health), {"endpoint": url}


def _probe_pixel_retrieval() -> Tuple[bool, Dict[str, Any]]:
    memory_url = unified_memory_url()
    health = _fetch_json(f"{memory_url}/health")
    if health and health.get("status") == "ok":
        return True, {"endpoint": memory_url, "via": "unified_memory"}
    # Fallback: direct PixelRAG serve.
    direct = pixelrag_url()
    direct_health = _fetch_json(f"{direct}/health")
    if direct_health:
        return True, {"endpoint": direct, "via": "pixelrag_direct"}
    return False, {"endpoint": memory_url}


def _probe_spec_tracer() -> Tuple[bool, Dict[str, Any]]:
    # Local SQLite-backed store — always available once the app is up.
    return True, {"endpoint": "/api/operator/spec-trace"}


def _probe_desktop_action() -> Tuple[bool, Dict[str, Any]]:
    url = clicky_worker_url()
    health = _fetch_json(f"{url}/health")
    return bool(health), {"endpoint": url}


def _probe_browser_action() -> Tuple[bool, Dict[str, Any]]:
    url = cdp_url()
    version = _fetch_json(f"{url}/json/version")
    detail: Dict[str, Any] = {"endpoint": url}
    if version and version.get("Browser"):
        detail["browser"] = version.get("Browser")
    return bool(version), detail


def _research_providers_configured() -> list:
    """Names of fan-out providers with a configured key. Never raises."""
    configured = []
    try:
        from services.search.providers import _get_provider_key
        for name in ("tinyfish", "perplexity", "firecrawl"):
            try:
                if _get_provider_key(name):
                    configured.append(name)
            except Exception:
                continue
    except Exception:
        pass
    return configured


def _probe_research() -> Tuple[bool, Dict[str, Any]]:
    providers = _research_providers_configured()
    return bool(providers), {"providers": providers}


_PROBES = {
    CAP_SCREEN_PERCEPTION: _probe_screen_perception,
    CAP_PIXEL_RETRIEVAL: _probe_pixel_retrieval,
    CAP_SPEC_TRACER: _probe_spec_tracer,
    CAP_DESKTOP_ACTION: _probe_desktop_action,
    CAP_BROWSER_ACTION: _probe_browser_action,
    CAP_RESEARCH: _probe_research,
}

# capability -> (probed_at_monotonic, entry)
_status_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _probe_one(capability: str) -> Dict[str, Any]:
    probe = _PROBES[capability]
    try:
        available, detail = probe()
    except Exception as exc:  # probes shouldn't raise, but never let one bubble
        logger.warning("operator probe %s raised: %s", capability, exc)
        available, detail = False, {"error": str(exc)}
    entry = {
        "available": bool(available),
        "probed_at": time.time(),
        **detail,
    }
    if not available:
        entry["hint"] = HINTS.get(capability)
    return entry


def capability_status(capability: str, *, force: bool = False) -> Dict[str, Any]:
    """Cached availability for one capability."""
    cached = _status_cache.get(capability)
    now = time.monotonic()
    if not force and cached and (now - cached[0]) < STATUS_CACHE_TTL:
        return cached[1]
    entry = _probe_one(capability)
    _status_cache[capability] = (time.monotonic(), entry)
    return entry


def get_operator_status(*, force: bool = False) -> Dict[str, Any]:
    """Health snapshot for every capability (probes run in parallel)."""
    names = list(_PROBES.keys())
    to_probe = [
        n for n in names
        if force
        or n not in _status_cache
        or (time.monotonic() - _status_cache[n][0]) >= STATUS_CACHE_TTL
    ]
    if to_probe:
        with ThreadPoolExecutor(max_workers=len(to_probe)) as pool:
            for name, entry in zip(to_probe, pool.map(_probe_one, to_probe)):
                _status_cache[name] = (time.monotonic(), entry)
    return {
        "capabilities": {name: _status_cache[name][1] for name in names},
        "generated_at": time.time(),
    }


def require_capability(capability: str) -> Optional[Dict[str, Any]]:
    """Return None when available, else a degraded envelope for the tool result."""
    entry = capability_status(capability)
    if entry.get("available"):
        return None
    reason = f"{capability}_offline"
    return degraded_envelope(capability, reason)


def reset_status_cache() -> None:
    """Test hook — drop all cached probe results."""
    _status_cache.clear()


# ── Audit log ──

def record_audit(
    capability: str,
    action: str,
    *,
    target: Optional[str] = None,
    session_id: Optional[str] = None,
    result: str = "ok",
) -> Optional[str]:
    """Append an executed/denied action to the operator audit log.

    Best effort: audit failures are logged, never raised, so a DB hiccup
    can't turn a successful action into a tool error after the fact.
    """
    try:
        from core.database import OperatorAudit, SessionLocal
        db = SessionLocal()
        try:
            row = OperatorAudit(
                id=uuid.uuid4().hex,
                capability=capability,
                action=action,
                target=target,
                session_id=session_id,
                result=result,
            )
            db.add(row)
            db.commit()
            return row.id
        finally:
            db.close()
    except Exception as exc:
        logger.warning("operator audit write failed: %s", exc)
        return None

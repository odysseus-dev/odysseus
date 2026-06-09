"""Consolidated service health / degraded-state reporting.

ROADMAP: "Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy,
and provider probes." There was no single readout of which subsystems are
actually working — `/api/health` is only a liveness ping and each subsystem's
signal lives in a different module. This collects them into one uniform,
*non-intrusive* report (no test push is sent, no real search is run), so the
admin endpoint built on top of it is safe to poll.

Each probe returns:

    {"name": str, "status": "ok"|"degraded"|"down"|"disabled",
     "detail": str, "meta": dict}

- ok        — reachable / working
- degraded  — partially working (one of several components down)
- down      — configured & enabled but unreachable / erroring
- disabled  — not configured or turned off (not counted as a failure)

Design notes (driven by review feedback):

- **Bounded wall-clock.** Per-item probes (providers, email accounts) fan out
  across a bounded thread pool with a hard total budget (`_FANOUT_BUDGET`);
  stragglers are reported as a controlled `timeout` rather than blocking. The
  aggregate adds a per-subsystem deadline (`_SUBSYSTEM_DEADLINE`) and an overall
  ceiling (`_AGGREGATE_DEADLINE`), so the endpoint cannot hang regardless of how
  many endpoints/accounts are configured or how slowly they respond.
- **No secret leakage.** Even though the endpoint is admin-only, the response
  never returns credential-bearing URLs or raw exception text: URLs are passed
  through `_safe_url` (userinfo / query / fragment stripped) and failures are
  mapped to controlled categories via `_classify_error`.

The probe functions take their inputs as parameters (settings dict, account
list, endpoint list, manager objects) and isolate the network call to
``_http_get`` / injected callables, so they unit-test without touching the
network.
"""

import asyncio
import concurrent.futures
import logging
import socket
import ssl
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Status ordering for rolling up an overall verdict. "disabled" is excluded —
# a turned-off feature must never drag the overall status down.
_SEVERITY = {"ok": 0, "degraded": 1, "down": 2}

OK = "ok"
DEGRADED = "degraded"
DOWN = "down"
DISABLED = "disabled"

# Timing budgets (seconds). _PROBE_TIMEOUT bounds a single network op;
# _FANOUT_BUDGET bounds a whole fan-out (providers/email) regardless of count;
# the aggregate layer adds a per-subsystem deadline and an overall ceiling.
_PROBE_TIMEOUT = 4
_PROBE_CONCURRENCY = 8
_FANOUT_BUDGET = 8
_SUBSYSTEM_DEADLINE = 10
_AGGREGATE_DEADLINE = 14

# Controlled, secret-free phrasing for each failure category.
_ERROR_DETAIL = {
    "timeout": "probe timed out",
    "connection_refused": "connection refused",
    "dns_error": "host could not be resolved",
    "tls_error": "TLS handshake failed",
    "network_error": "network error",
    "http_error": "server returned an error response",
    "auth_or_protocol_error": "authentication or protocol error",
    "no_models": "endpoint returned no models",
    "no_host": "no host configured",
    "error": "probe failed",
}


def _svc(name: str, status: str, detail: str, **meta: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "meta": dict(meta)}


def _safe_url(url: Optional[str]) -> str:
    """Strip credentials (userinfo), query, and fragment from a URL.

    Keeps scheme / host / port / path so the report is still useful, but never
    echoes `user:pass@`, `?api_key=…`, or `#…` back to the caller. Returns
    "<redacted>" if the URL can't be parsed into at least a host.
    """
    if not url:
        return ""
    raw = url.strip()
    try:
        p = urlparse(raw if "://" in raw else "//" + raw)
        host = p.hostname or ""
        if not host:
            return "<redacted>"
        netloc = f"{host}:{p.port}" if p.port else host
        path = (p.path or "").rstrip("/")
        scheme = f"{p.scheme}://" if p.scheme else ""
        return f"{scheme}{netloc}{path}"
    except Exception:
        return "<redacted>"


def _classify_error(exc: BaseException) -> str:
    """Map an exception to a controlled, secret-free category token.

    Never returns `str(exc)` — httpx/imaplib exception text can embed the target
    URL (which may carry credentials) or server-supplied detail.
    """
    if isinstance(exc, (asyncio.TimeoutError, concurrent.futures.TimeoutError,
                        TimeoutError, socket.timeout)):
        return "timeout"
    name = type(exc).__name__
    mod = (type(exc).__module__ or "")
    if isinstance(exc, ssl.SSLError) or "SSL" in name or "Certificate" in name:
        return "tls_error"
    if isinstance(exc, socket.gaierror) or name in ("gaierror", "herror"):
        return "dns_error"
    if isinstance(exc, ConnectionRefusedError) or "ConnectionRefused" in name \
            or name in ("ConnectError",):
        return "connection_refused"
    if "Timeout" in name:
        return "timeout"
    if mod.startswith("imaplib") or name in ("error", "abort", "readonly"):
        return "auth_or_protocol_error"
    if name == "HTTPStatusError":
        return "http_error"
    if name in ("ConnectTimeout", "ReadTimeout", "ReadError", "WriteError",
                "PoolTimeout", "RemoteProtocolError", "NetworkError",
                "ProxyError", "ProtocolError"):
        return "network_error"
    if isinstance(exc, OSError):
        return "network_error"
    return "error"


def _detail_for(category: str) -> str:
    return _ERROR_DETAIL.get(category, _ERROR_DETAIL["error"])


def _http_get(url: str, timeout: float = _PROBE_TIMEOUT):
    """Single network entry point for the HTTP probes (monkeypatched in tests)."""
    import httpx
    return httpx.get(url, timeout=timeout)


def _bounded_map(items: List[Any], worker: Callable[[int, Any], Dict[str, Any]],
                 *, budget: float = _FANOUT_BUDGET,
                 concurrency: int = _PROBE_CONCURRENCY) -> List[Optional[Dict[str, Any]]]:
    """Run ``worker(index, item)`` across a bounded thread pool, in order.

    `worker` must catch its own exceptions and return a per-item dict. Any item
    not finished within `budget` seconds *in total* is left as ``None`` (the
    caller substitutes a controlled `timeout` entry). The pool is shut down with
    ``wait=False`` so stragglers never block the response — their own per-op
    timeout reaps them shortly after.
    """
    n = len(items)
    out: List[Optional[Dict[str, Any]]] = [None] * n
    if n == 0:
        return out
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(concurrency, n)))
    futures = {ex.submit(worker, i, items[i]): i for i in range(n)}
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=budget):
            i = futures[fut]
            try:
                out[i] = fut.result()
            except Exception as e:  # worker is expected to handle its own errors
                out[i] = {"ok": False, "error": _classify_error(e)}
    except concurrent.futures.TimeoutError:
        pass  # unfinished items stay None → marked timeout by the caller
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return out


# ── ChromaDB (vector RAG + vector memory) ──

def chromadb_health(rag_manager: Any, memory_vector: Any) -> Dict[str, Any]:
    """Report on the two ChromaDB-backed stores via their `.healthy` flags.

    Both absent  → disabled (Chroma/embeddings not installed or off).
    Both healthy → ok. One down → degraded. Both present but unhealthy → down.
    """
    rag_present = rag_manager is not None
    mem_present = memory_vector is not None
    if not rag_present and not mem_present:
        return _svc("chromadb", DISABLED,
                    "Vector RAG and vector memory are not initialized.",
                    rag=None, memory=None)

    rag_ok = bool(rag_present and getattr(rag_manager, "healthy", False))
    mem_ok = bool(mem_present and getattr(memory_vector, "healthy", False))
    meta = {"rag": rag_ok if rag_present else None,
            "memory": mem_ok if mem_present else None}

    healthy = [ok for ok in (rag_ok if rag_present else None,
                             mem_ok if mem_present else None) if ok is not None]
    if healthy and all(healthy):
        return _svc("chromadb", OK, "Vector stores healthy.", **meta)
    if any(healthy):
        return _svc("chromadb", DEGRADED,
                    "One vector store is unavailable.", **meta)
    return _svc("chromadb", DOWN, "Vector stores are unavailable.", **meta)


# ── SearXNG ──

def _searxng_instance(settings: Dict[str, Any]) -> str:
    """Mirror src/search/providers.py:_get_search_instance precedence."""
    url = (settings.get("search_url") or "").strip()
    if url:
        return url.rstrip("/")
    from src.constants import SEARXNG_INSTANCE
    return SEARXNG_INSTANCE.rstrip("/")


def searxng_health(settings: Dict[str, Any],
                   *, http_get: Callable = _http_get) -> Dict[str, Any]:
    """Non-intrusive reachability probe for the configured SearXNG instance.

    Tries `/healthz` (2xx), falling back to the instance root (any non-5xx means
    the host answered). No search query is run. The configured instance is
    probed in full, but only its sanitized form is returned in `meta`.
    """
    provider = (settings.get("search_provider") or "searxng")
    if provider != "searxng":
        return _svc("searxng", DISABLED,
                    f"Search provider is '{provider}', not SearXNG.",
                    provider=provider)
    instance = _searxng_instance(settings)
    if not instance:
        return _svc("searxng", DISABLED, "No SearXNG instance configured.")
    safe_instance = _safe_url(instance)
    last_category = "error"
    for path, accept in (("/healthz", lambda c: 200 <= c < 300),
                         ("/", lambda c: 0 < c < 500)):
        try:
            r = http_get(instance + path, timeout=_PROBE_TIMEOUT)
            code = getattr(r, "status_code", 0)
            if accept(code):
                return _svc("searxng", OK, f"Reachable (HTTP {code}).",
                            instance=safe_instance, probed=path, http_status=code)
            last_category = "http_error"
        except Exception as e:  # connection refused, DNS, timeout, …
            last_category = _classify_error(e)
    return _svc("searxng", DOWN, f"Unreachable ({_detail_for(last_category)}).",
                instance=safe_instance, error=last_category)


# ── ntfy ──

def _ntfy_integration(integrations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """First enabled ntfy integration with a base_url (matches note_routes)."""
    for i in integrations or []:
        if (i.get("preset") == "ntfy" and i.get("enabled", True)
                and i.get("base_url")):
            return i
    return None


def ntfy_health(integrations: List[Dict[str, Any]], settings: Dict[str, Any],
                *, http_get: Callable = _http_get) -> Dict[str, Any]:
    """Non-intrusive ntfy probe via the server's built-in `/v1/health` route.

    No test notification is POSTed — `/v1/health` returns `{"healthy":true}`
    without publishing to a topic. The request keeps whatever credentials the
    configured base_url carries, but `meta.base` is sanitized.
    """
    channel = settings.get("reminder_channel") or "browser"
    intg = _ntfy_integration(integrations)
    if not intg:
        return _svc("ntfy", DISABLED, "No ntfy integration configured.",
                    reminder_channel=channel)
    raw = (intg.get("base_url") or "").strip()
    parsed = urlparse(raw)
    probe_base = (f"{parsed.scheme}://{parsed.netloc}"
                  if parsed.scheme and parsed.netloc else raw.rstrip("/"))
    safe_base = _safe_url(raw)
    try:
        r = http_get(probe_base + "/v1/health", timeout=_PROBE_TIMEOUT)
        code = getattr(r, "status_code", 0)
        if code and code < 500:
            return _svc("ntfy", OK, f"Reachable (HTTP {code}).",
                        base=safe_base, reminder_channel=channel, http_status=code)
        return _svc("ntfy", DOWN, "Server returned an error response.",
                    base=safe_base, reminder_channel=channel, error="http_error")
    except Exception as e:
        category = _classify_error(e)
        return _svc("ntfy", DOWN, f"Unreachable ({_detail_for(category)}).",
                    base=safe_base, reminder_channel=channel, error=category)


# ── Email (IMAP) ──

def email_health(accounts: List[Dict[str, Any]],
                 *, connect: Optional[Callable] = None) -> Dict[str, Any]:
    """Try a short IMAP connect+logout per configured account, concurrently.

    All connect → ok. Some fail → degraded. All fail → down. No account
    configured → disabled. Bounded by `_FANOUT_BUDGET` regardless of count.
    `meta` carries only the account label and a controlled error category —
    never credentials or raw exception text.
    """
    if not accounts:
        return _svc("email", DISABLED, "No email accounts configured.")
    if connect is None:
        from routes.email_helpers import _imap_connect
        # Impose the service-health budget on the IMAP connect itself.
        connect = lambda aid: _imap_connect(aid, timeout=_PROBE_TIMEOUT)  # noqa: E731

    def _label(acc: Dict[str, Any]) -> str:
        return acc.get("account_name") or acc.get("account_id") or "account"

    def _check(_i: int, acc: Dict[str, Any]) -> Dict[str, Any]:
        name = _label(acc)
        if not (acc.get("imap_host") or ""):
            return {"name": name, "ok": False, "error": "no_host"}
        try:
            conn = connect(acc.get("account_id"))
            try:
                conn.logout()
            except Exception:
                pass
            return {"name": name, "ok": True, "error": None}
        except Exception as e:
            return {"name": name, "ok": False, "error": _classify_error(e)}

    raw = _bounded_map(accounts, _check, budget=_FANOUT_BUDGET,
                       concurrency=_PROBE_CONCURRENCY)
    per_account = [r if r is not None
                   else {"name": _label(accounts[i]), "ok": False, "error": "timeout"}
                   for i, r in enumerate(raw)]
    return _rollup_items("email", "mailbox(es)", per_account)


# ── Provider endpoints ──

def providers_health(endpoints: List[Dict[str, Any]],
                     *, probe: Optional[Callable] = None) -> Dict[str, Any]:
    """Probe each enabled model endpoint's model list, concurrently.

    `endpoints` is a list of plain dicts ({name, base_url, api_key}) so this
    stays decoupled from the ORM and trivially testable. Non-empty model list
    → reachable. Bounded by `_FANOUT_BUDGET` regardless of count. `meta` never
    contains api_key or raw URLs — only a display name (or a sanitized URL when
    no name is set) and a controlled error category.
    """
    if not endpoints:
        return _svc("providers", DISABLED, "No model endpoints configured.")
    if probe is None:
        from routes.model_routes import _probe_endpoint as probe

    def _label(ep: Dict[str, Any]) -> str:
        return ep.get("name") or _safe_url(ep.get("base_url")) or "endpoint"

    def _check(_i: int, ep: Dict[str, Any]) -> Dict[str, Any]:
        name = _label(ep)
        try:
            models = probe(ep.get("base_url"), ep.get("api_key"),
                           timeout=_PROBE_TIMEOUT) or []
        except Exception as e:
            return {"name": name, "ok": False, "model_count": 0,
                    "error": _classify_error(e)}
        count = len(models)
        return {"name": name, "ok": bool(count), "model_count": count,
                "error": None if count else "no_models"}

    raw = _bounded_map(endpoints, _check, budget=_FANOUT_BUDGET,
                       concurrency=_PROBE_CONCURRENCY)
    per_endpoint = [r if r is not None
                    else {"name": _label(endpoints[i]), "ok": False,
                          "model_count": 0, "error": "timeout"}
                    for i, r in enumerate(raw)]
    return _rollup_items("providers", "endpoint(s)", per_endpoint, key="endpoints")


def _rollup_items(name: str, noun: str, items: List[Dict[str, Any]],
                  key: str = "accounts") -> Dict[str, Any]:
    """Shared ok/degraded/down rollup for a list of per-item probe results."""
    total = len(items)
    ok_count = sum(1 for it in items if it.get("ok"))
    if ok_count == total:
        status, detail = OK, f"{ok_count}/{total} {noun} reachable."
    elif ok_count == 0:
        status, detail = DOWN, f"No {noun} reachable."
    else:
        status, detail = DEGRADED, f"{ok_count}/{total} {noun} reachable."
    return _svc(name, status, detail, **{key: items})


# ── Aggregate ──

def _rollup(services: List[Dict[str, Any]]) -> str:
    worst = OK
    for s in services:
        sev = _SEVERITY.get(s.get("status"))
        if sev is not None and sev > _SEVERITY[worst]:
            worst = s["status"]
    return worst


def _gather_inputs() -> Dict[str, Any]:
    """Pull live config/account/endpoint lists from the app's data sources.

    Each lookup fails soft: a broken source yields an empty/neutral value so a
    single failure can't take down the whole health report.
    """
    settings: Dict[str, Any] = {}
    integrations: List[Dict[str, Any]] = []
    accounts: List[Dict[str, Any]] = []
    endpoints: List[Dict[str, Any]] = []
    try:
        from src.settings import load_settings
        settings = load_settings() or {}
    except Exception as e:
        logger.debug(f"service_health: settings load failed: {e}")
    try:
        from src.integrations import load_integrations
        integrations = load_integrations() or []
    except Exception as e:
        logger.debug(f"service_health: integrations load failed: {e}")
    try:
        from routes.email_helpers import _list_email_accounts
        accounts = _list_email_accounts() or []
    except Exception as e:
        logger.debug(f"service_health: email accounts load failed: {e}")
    try:
        from core.database import SessionLocal, ModelEndpoint
        db = SessionLocal()
        try:
            rows = db.query(ModelEndpoint).filter(
                ModelEndpoint.is_enabled == True).all()  # noqa: E712
            endpoints = [{"name": r.name, "base_url": r.base_url,
                          "api_key": r.api_key} for r in rows]
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"service_health: endpoint load failed: {e}")
    return {"settings": settings, "integrations": integrations,
            "accounts": accounts, "endpoints": endpoints}


async def _run_subsystem(name: str, fn: Callable, *args: Any) -> Dict[str, Any]:
    """Run one (sync) subsystem probe in a thread under a hard deadline.

    A subsystem that overruns `_SUBSYSTEM_DEADLINE` (or raises) becomes a
    controlled `down`/`timeout` entry instead of hanging or leaking the error.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args),
                                      timeout=_SUBSYSTEM_DEADLINE)
    except asyncio.TimeoutError:
        return _svc(name, DOWN, _detail_for("timeout"), error="timeout")
    except Exception as e:
        category = _classify_error(e)
        return _svc(name, DOWN, _detail_for(category), error=category)


async def collect_service_health(rag_manager: Any = None,
                                 memory_vector: Any = None) -> Dict[str, Any]:
    """Run every probe and return {overall, services, timestamp}.

    Bounded end-to-end: in-process ChromaDB flags are read synchronously; the
    four network subsystems run concurrently, each under `_SUBSYSTEM_DEADLINE`,
    with an overall `_AGGREGATE_DEADLINE` backstop. Per-item probes inside
    providers/email are themselves bounded by `_FANOUT_BUDGET`.
    """
    from datetime import datetime, timezone

    inputs = _gather_inputs()
    settings = inputs["settings"]

    # ChromaDB is in-process and synchronous (just reads flags).
    chroma = chromadb_health(rag_manager, memory_vector)

    names = ["searxng", "ntfy", "email", "providers"]
    coros = [
        _run_subsystem("searxng", searxng_health, settings),
        _run_subsystem("ntfy", ntfy_health, inputs["integrations"], settings),
        _run_subsystem("email", email_health, inputs["accounts"]),
        _run_subsystem("providers", providers_health, inputs["endpoints"]),
    ]
    try:
        results = await asyncio.wait_for(asyncio.gather(*coros),
                                         timeout=_AGGREGATE_DEADLINE)
    except asyncio.TimeoutError:
        # Hard backstop — should not normally fire given per-subsystem deadlines.
        results = [_svc(n, DOWN, _detail_for("timeout"), error="timeout")
                   for n in names]

    services = [chroma, *results]
    return {
        "overall": _rollup(services),
        "services": services,
        # Timezone-aware UTC (…+00:00). Avoids the deprecated naive
        # datetime.utcnow() flagged in review (overlaps with #1116).
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

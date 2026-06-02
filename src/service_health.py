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

The probe functions take their inputs as parameters (settings dict, account
list, endpoint list, manager objects) and isolate the actual network call to
``_http_get`` / injected callables, so they unit-test without touching the
network.
"""

import asyncio
import logging
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

# Per-probe network budget. Kept short so the aggregate endpoint can't hang.
_PROBE_TIMEOUT = 4


def _svc(name: str, status: str, detail: str, **meta: Any) -> Dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "meta": dict(meta)}


def _http_get(url: str, timeout: float = _PROBE_TIMEOUT):
    """Single network entry point for the HTTP probes (monkeypatched in tests)."""
    import httpx
    return httpx.get(url, timeout=timeout)


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

    Tries `/healthz`, falling back to the instance root; any status < 500 means
    the instance answered. No search query is run.
    """
    provider = (settings.get("search_provider") or "searxng")
    if provider != "searxng":
        return _svc("searxng", DISABLED,
                    f"Search provider is '{provider}', not SearXNG.",
                    provider=provider)
    instance = _searxng_instance(settings)
    if not instance:
        return _svc("searxng", DISABLED, "No SearXNG instance configured.")
    # /healthz is the preferred signal (must answer 2xx). If it's missing or
    # erroring, fall back to the instance root and accept any non-5xx as
    # "the host answered" — a search-engine UI returning 200/3xx/4xx is up.
    last = "no response"
    for path, accept in (("/healthz", lambda c: 200 <= c < 300),
                         ("/", lambda c: 0 < c < 500)):
        try:
            r = http_get(instance + path, timeout=_PROBE_TIMEOUT)
            code = getattr(r, "status_code", 0)
            if accept(code):
                return _svc("searxng", OK, f"Reachable (HTTP {code}).",
                            instance=instance, probed=path)
            last = f"HTTP {code}"
        except Exception as e:  # connection refused, DNS, timeout, …
            last = str(e)[:160]
    return _svc("searxng", DOWN, f"Unreachable: {last}", instance=instance)


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
    without publishing to a topic.
    """
    channel = settings.get("reminder_channel") or "browser"
    intg = _ntfy_integration(integrations)
    if not intg:
        return _svc("ntfy", DISABLED, "No ntfy integration configured.",
                    reminder_channel=channel)
    raw = (intg.get("base_url") or "").strip()
    parsed = urlparse(raw)
    base = (f"{parsed.scheme}://{parsed.netloc}"
            if parsed.scheme and parsed.netloc else raw.rstrip("/"))
    headers_url = base + "/v1/health"
    try:
        r = http_get(headers_url, timeout=_PROBE_TIMEOUT)
        code = getattr(r, "status_code", 0)
        if code and code < 500:
            return _svc("ntfy", OK, f"Reachable (HTTP {code}).",
                        base=base, reminder_channel=channel)
        return _svc("ntfy", DOWN, f"ntfy returned HTTP {code}.",
                    base=base, reminder_channel=channel)
    except Exception as e:
        return _svc("ntfy", DOWN, f"Unreachable: {str(e)[:160]}",
                    base=base, reminder_channel=channel)


# ── Email (IMAP) ──

def email_health(accounts: List[Dict[str, Any]],
                 *, connect: Optional[Callable] = None) -> Dict[str, Any]:
    """Try a short IMAP connect+logout per configured account.

    All connect → ok. Some fail → degraded. All fail → down. No account
    configured → disabled. `meta` never contains credentials.
    """
    if not accounts:
        return _svc("email", DISABLED, "No email accounts configured.")
    if connect is None:
        from routes.email_helpers import _imap_connect as connect

    per_account = []
    ok_count = 0
    for acc in accounts:
        name = acc.get("account_name") or acc.get("account_id") or "account"
        host = acc.get("imap_host") or ""
        if not host:
            per_account.append({"name": name, "ok": False,
                                "error": "no IMAP host configured"})
            continue
        try:
            conn = connect(acc.get("account_id"))
            try:
                conn.logout()
            except Exception:
                pass
            ok_count += 1
            per_account.append({"name": name, "ok": True, "error": None})
        except Exception as e:
            per_account.append({"name": name, "ok": False,
                                "error": str(e)[:160]})

    total = len(per_account)
    if ok_count == total:
        return _svc("email", OK, f"{ok_count}/{total} mailbox(es) reachable.",
                    accounts=per_account)
    if ok_count == 0:
        return _svc("email", DOWN, "No mailboxes reachable.",
                    accounts=per_account)
    return _svc("email", DEGRADED,
                f"{ok_count}/{total} mailbox(es) reachable.",
                accounts=per_account)


# ── Provider endpoints ──

def providers_health(endpoints: List[Dict[str, Any]],
                     *, probe: Optional[Callable] = None) -> Dict[str, Any]:
    """Probe each enabled model endpoint's model list.

    `endpoints` is a list of plain dicts ({name, base_url, api_key}) so this
    stays decoupled from the ORM and trivially testable. Non-empty model list
    → reachable. All reachable → ok; some fail → degraded; all fail → down.
    `meta` never contains api_key.
    """
    if not endpoints:
        return _svc("providers", DISABLED, "No model endpoints configured.")
    if probe is None:
        from routes.model_routes import _probe_endpoint as probe

    per_endpoint = []
    ok_count = 0
    for ep in endpoints:
        name = ep.get("name") or ep.get("base_url") or "endpoint"
        try:
            models = probe(ep.get("base_url"), ep.get("api_key"),
                           timeout=_PROBE_TIMEOUT) or []
        except Exception as e:
            per_endpoint.append({"name": name, "ok": False,
                                 "model_count": 0, "error": str(e)[:160]})
            continue
        count = len(models)
        if count:
            ok_count += 1
        per_endpoint.append({"name": name, "ok": bool(count),
                             "model_count": count,
                             "error": None if count else "no models returned"})

    total = len(per_endpoint)
    if ok_count == total:
        return _svc("providers", OK, f"{ok_count}/{total} endpoint(s) reachable.",
                    endpoints=per_endpoint)
    if ok_count == 0:
        return _svc("providers", DOWN, "No endpoints reachable.",
                    endpoints=per_endpoint)
    return _svc("providers", DEGRADED,
                f"{ok_count}/{total} endpoint(s) reachable.",
                endpoints=per_endpoint)


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


async def collect_service_health(rag_manager: Any = None,
                                 memory_vector: Any = None) -> Dict[str, Any]:
    """Run every probe and return {overall, services, timestamp}.

    Blocking probes (IMAP, sync HTTP) run in worker threads so they don't block
    the event loop, and they run concurrently with a bounded wall-clock.
    """
    from datetime import datetime, timezone

    inputs = _gather_inputs()
    settings = inputs["settings"]

    # ChromaDB is in-process and synchronous (just reads flags).
    chroma = chromadb_health(rag_manager, memory_vector)

    # The rest touch the network — fan out across threads.
    results = await asyncio.gather(
        asyncio.to_thread(searxng_health, settings),
        asyncio.to_thread(ntfy_health, inputs["integrations"], settings),
        asyncio.to_thread(email_health, inputs["accounts"]),
        asyncio.to_thread(providers_health, inputs["endpoints"]),
        return_exceptions=True,
    )
    names = ["searxng", "ntfy", "email", "providers"]
    services = [chroma]
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            logger.warning(f"service_health: {name} probe errored: {res}")
            services.append(_svc(name, DOWN, f"Probe error: {str(res)[:160]}"))
        else:
            services.append(res)

    return {
        "overall": _rollup(services),
        "services": services,
        # Timezone-aware UTC (…+00:00). Avoids the deprecated naive
        # datetime.utcnow() flagged in review (overlaps with #1116).
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

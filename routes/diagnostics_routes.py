"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research,
/api/diagnostics/services (degraded-state health summary for optional services)."""

import asyncio
import logging
import socket
import time
from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException, Form

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from core.constants import DEFAULT_HOST

logger = logging.getLogger(__name__)

# Status vocabulary for the degraded-state summary. Kept as plain strings so the
# UI can branch on them without importing anything from the backend.
#   ok           — probe succeeded, service is reachable
#   down         — service is configured/expected but the probe failed
#   unconfigured — nothing is set up for this service yet (not an error)
#   disabled     — the user explicitly turned this service off
#   unknown      — we could not even attempt a probe (missing dependency, etc.)
STATUS_OK = "ok"
STATUS_DOWN = "down"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_DISABLED = "disabled"
STATUS_UNKNOWN = "unknown"

# Each probe is wrapped in this timeout so the summary endpoint stays snappy even
# when an optional service is hung. Probes run concurrently, so the whole
# endpoint is bounded by roughly this value, not the sum of all probes.
_PROBE_TIMEOUT_SECONDS = 4.0


def _status(name: str, status: str, detail: str = "", **extra: Any) -> Dict[str, Any]:
    """Build one service entry for the summary."""
    out: Dict[str, Any] = {"service": name, "status": status, "detail": detail}
    out.update(extra)
    return out


def classify_searxng_url(search_url: str, fallback_instance: str) -> str:
    """Pick the effective SearXNG base URL: admin override, then env/default.

    Pure helper (no I/O) so URL-resolution precedence is unit-testable.
    """
    url = (search_url or "").strip().rstrip("/")
    if url:
        return url
    return (fallback_instance or "").strip().rstrip("/")


async def _http_probe(name: str, url: str, *, ok_path: str = "") -> Dict[str, Any]:
    """GET a service URL and report reachability. Any HTTP response (even 4xx)
    means the service is *reachable*; only transport failures are 'down'."""
    target = url.rstrip("/") + ok_path
    try:
        import httpx
    except ImportError:
        return _status(name, STATUS_UNKNOWN, "httpx not installed", url=url)
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(target)
        return _status(
            name, STATUS_OK, f"HTTP {resp.status_code}", url=url, http_status=resp.status_code
        )
    except Exception as e:  # noqa: BLE001 — any failure to connect is "down"
        return _status(name, STATUS_DOWN, str(e) or e.__class__.__name__, url=url)


async def _tcp_probe(name: str, host: str, port: int, **extra: Any) -> Dict[str, Any]:
    """Open a TCP connection to host:port. Used for IMAP/SMTP where we only want
    to confirm the server is listening, without sending credentials."""
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=_PROBE_TIMEOUT_SECONDS)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — close errors don't change reachability
            pass
        return _status(name, STATUS_OK, f"{host}:{port} reachable", host=host, port=port, **extra)
    except (asyncio.TimeoutError, OSError, socket.gaierror) as e:
        return _status(name, STATUS_DOWN, str(e) or e.__class__.__name__, host=host, port=port, **extra)


async def _probe_chromadb(rag_manager, rag_available: bool) -> Dict[str, Any]:
    if not rag_available or not rag_manager:
        return _status("chromadb", STATUS_DISABLED, "RAG system not available")
    try:
        # get_stats() is synchronous and touches the on-disk Chroma collection;
        # run it off the event loop so a slow disk can't block the endpoint.
        stats = await asyncio.wait_for(
            asyncio.to_thread(rag_manager.get_stats), timeout=_PROBE_TIMEOUT_SECONDS
        )
        if isinstance(stats, dict) and stats.get("error"):
            return _status("chromadb", STATUS_DOWN, str(stats["error"]))
        return _status("chromadb", STATUS_OK, "collection reachable", stats=stats)
    except Exception as e:  # noqa: BLE001
        return _status("chromadb", STATUS_DOWN, str(e) or e.__class__.__name__)


async def _probe_searxng() -> Dict[str, Any]:
    try:
        from src.settings import get_setting
        from core.constants import SEARXNG_INSTANCE
    except Exception as e:  # noqa: BLE001
        return _status("searxng", STATUS_UNKNOWN, str(e) or e.__class__.__name__)

    provider = get_setting("search_provider", "searxng")
    url = classify_searxng_url(get_setting("search_url", ""), SEARXNG_INSTANCE)
    if not url:
        return _status("searxng", STATUS_UNCONFIGURED, "No SearXNG URL configured")
    if provider != "searxng":
        # Still reachable-check it, but flag that it isn't the active provider so
        # the user understands why search may use a different backend.
        result = await _http_probe("searxng", url)
        result["detail"] = f"{result['detail']} (active provider: {provider})"
        return result
    return await _http_probe("searxng", url)


async def _probe_ntfy() -> Dict[str, Any]:
    try:
        from src.integrations import load_integrations
    except Exception as e:  # noqa: BLE001
        return _status("ntfy", STATUS_UNKNOWN, str(e) or e.__class__.__name__)

    intg = next(
        (
            i
            for i in load_integrations()
            if i.get("preset") == "ntfy" and i.get("enabled", True) and i.get("base_url")
        ),
        None,
    )
    if not intg:
        return _status("ntfy", STATUS_UNCONFIGURED, "No enabled ntfy integration")
    return await _http_probe("ntfy", intg["base_url"], ok_path="/v1/health")


async def _probe_email() -> Dict[str, Any]:
    try:
        from routes.email_helpers import _get_email_config
    except Exception as e:  # noqa: BLE001
        return _status("email", STATUS_UNKNOWN, str(e) or e.__class__.__name__)

    try:
        cfg = _get_email_config()
    except Exception as e:  # noqa: BLE001
        return _status("email", STATUS_UNKNOWN, str(e) or e.__class__.__name__)

    host = (cfg.get("imap_host") or "").strip()
    if not host:
        return _status("email", STATUS_UNCONFIGURED, "No IMAP host configured")
    port = int(cfg.get("imap_port") or 993)
    return await _tcp_probe("email", host, port)


async def _run_probe(coro) -> Dict[str, Any]:
    """Never let one probe blow up the whole summary."""
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        return _status("unknown", STATUS_DOWN, str(e) or e.__class__.__name__)


def summarize(services: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll up per-service statuses into an at-a-glance verdict.

    Pure helper (no I/O) so the overall-status logic is unit-testable.
    'down' counts toward degraded; unconfigured/disabled do not (those are
    deliberate, not failures).
    """
    counts: Dict[str, int] = {}
    for s in services:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    down = counts.get(STATUS_DOWN, 0)
    unknown = counts.get(STATUS_UNKNOWN, 0)
    if down:
        overall = "degraded"
    elif unknown:
        overall = "partial"
    else:
        overall = "healthy"
    return {"overall": overall, "counts": counts}


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/db/stats")
    async def get_database_stats() -> Dict[str, Any]:
        try:
            from core.database import get_detailed_stats
            return get_detailed_stats()
        except Exception as e:
            logger.error(f"DB stats error: {e}")
            raise HTTPException(500, "Failed to retrieve database statistics")

    @router.get("/api/rag/stats")
    async def get_rag_stats() -> Dict[str, Any]:
        if rag_available and rag_manager:
            return rag_manager.get_stats()
        return {"error": "RAG system not available"}

    @router.get("/api/diagnostics/services")
    async def diagnostics_services() -> Dict[str, Any]:
        """Degraded-state summary for optional services.

        Probes ChromaDB, SearXNG, email (IMAP), and ntfy concurrently with
        bounded timeouts so a self-hoster can see at a glance which optional
        services are reachable vs down vs simply not set up. Non-blocking and
        dependency-light: HTTP probes reuse httpx, the IMAP probe is a bare TCP
        connect (no credentials sent), and every probe is timeout-guarded.
        """
        started = time.monotonic()
        results = await asyncio.gather(
            _run_probe(_probe_chromadb(rag_manager, rag_available)),
            _run_probe(_probe_searxng()),
            _run_probe(_probe_email()),
            _run_probe(_probe_ntfy()),
        )
        services = list(results)
        summary = summarize(services)
        return {
            **summary,
            "services": services,
            "probe_ms": int((time.monotonic() - started) * 1000),
        }

    @router.get("/api/test/youtube")
    async def test_youtube(url: str) -> Dict[str, Any]:
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return {"error": "Invalid YouTube URL"}

            data = await extract_transcript_async(url, video_id)
            return {
                "video_id": video_id,
                "transcript_success": data.get("success", False),
                "transcript_length": len(data.get("transcript", "")) if data.get("success") else 0,
                "transcript_preview": (data.get("transcript", "")[:500] + "...")
                    if data.get("success") and len(data.get("transcript", "")) > 500
                    else data.get("transcript", ""),
                "error": data.get("error") if not data.get("success") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/test-research")
    async def test_research(query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        try:
            endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            return {
                "status": "success",
                "query": query,
                "result_preview": result[:200] + "..." if len(result) > 200 else result,
                "result_length": len(result),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "query": query}

    return router

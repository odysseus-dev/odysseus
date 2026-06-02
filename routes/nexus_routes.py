"""Nexus sidecar service proxy routes — /api/nexus/*.

Proxies requests to nexus-cost, nexus-metrics, and nexus-news sidecar
containers so the Odysseus frontend can query them without CORS or
authentication issues.
"""

import os
import logging
from typing import Dict, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

# Sidecar service URLs (configurable via env)
NEXUS_COST_URL = os.getenv("NEXUS_COST_URL", "http://nexus-cost:8199")
NEXUS_METRICS_URL = os.getenv("NEXUS_METRICS_URL", "http://localhost:9100")
NEXUS_NEWS_URL = os.getenv("NEXUS_NEWS_URL", "http://nexus-news:8100")


def setup_nexus_routes() -> APIRouter:
    router = APIRouter(tags=["nexus"])

    # ── Nexus Cost ────────────────────────────────────────────────────────

    @router.get("/api/nexus/cost/health")
    async def nexus_cost_health() -> Dict[str, Any]:
        """Proxy: nexus-cost health check."""
        return await _proxy_get(f"{NEXUS_COST_URL}/health")

    @router.get("/api/nexus/cost/summary")
    async def nexus_cost_summary(period: str = "month") -> Dict[str, Any]:
        """Proxy: nexus-cost period summary."""
        return await _proxy_get(f"{NEXUS_COST_URL}/costs", params={"period": period})

    @router.get("/api/nexus/cost/history")
    async def nexus_cost_history(days: int = 30) -> Dict[str, Any]:
        """Proxy: nexus-cost daily history."""
        return await _proxy_get(f"{NEXUS_COST_URL}/costs/history", params={"days": days})

    @router.get("/api/nexus/cost/by-model")
    async def nexus_cost_by_model(period: str = "month") -> Any:
        """Proxy: nexus-cost breakdown by model."""
        return await _proxy_get(f"{NEXUS_COST_URL}/costs/by-model", params={"period": period})

    @router.get("/api/nexus/cost/by-service")
    async def nexus_cost_by_service(period: str = "month") -> Any:
        """Proxy: nexus-cost breakdown by service."""
        return await _proxy_get(f"{NEXUS_COST_URL}/costs/by-service", params={"period": period})

    @router.get("/api/nexus/cost/trend")
    async def nexus_cost_trend(days: int = 14) -> Dict[str, Any]:
        """Proxy: nexus-cost trend analysis."""
        return await _proxy_get(f"{NEXUS_COST_URL}/costs/trend", params={"days": days})

    @router.get("/api/nexus/cost/budget")
    async def nexus_cost_budget() -> Any:
        """Proxy: nexus-cost budget list."""
        return await _proxy_get(f"{NEXUS_COST_URL}/budget")

    @router.get("/api/nexus/cost/budget/{service}")
    async def nexus_cost_budget_service(service: str) -> Dict[str, Any]:
        """Proxy: nexus-cost budget for a specific service."""
        return await _proxy_get(f"{NEXUS_COST_URL}/budget/{service}")

    @router.get("/api/nexus/cost/alerts")
    async def nexus_cost_alerts(limit: int = 20) -> Any:
        """Proxy: nexus-cost recent alerts."""
        return await _proxy_get(f"{NEXUS_COST_URL}/alerts", params={"limit": limit})

    @router.get("/api/nexus/cost/models")
    async def nexus_cost_models() -> Any:
        """Proxy: nexus-cost known model pricing."""
        return await _proxy_get(f"{NEXUS_COST_URL}/models")

    # ── Nexus Metrics ─────────────────────────────────────────────────────

    @router.get("/api/nexus/metrics/health")
    async def nexus_metrics_health() -> Dict[str, Any]:
        """Proxy: nexus-metrics health check."""
        return await _proxy_get(f"{NEXUS_METRICS_URL}/health")

    @router.get("/api/nexus/metrics/all")
    async def nexus_metrics_all() -> Dict[str, Any]:
        """Proxy: all system metrics."""
        return await _proxy_get(f"{NEXUS_METRICS_URL}/metrics")

    @router.get("/api/nexus/metrics/{metric_type}")
    async def nexus_metrics_type(metric_type: str) -> Dict[str, Any]:
        """Proxy: metrics for one type (cpu, memory, disk, network, gpu, process, temperature)."""
        return await _proxy_get(f"{NEXUS_METRICS_URL}/metrics/{metric_type}")

    # ── Nexus News ────────────────────────────────────────────────────────

    @router.get("/api/nexus/news/health")
    async def nexus_news_health() -> Dict[str, Any]:
        """Proxy: nexus-news health check."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/health")

    @router.get("/api/nexus/news")
    async def nexus_news_list(
        limit: int = Query(50, ge=1, le=200),
        min_relevance: float = Query(0.0, ge=0.0, le=1.0),
    ) -> Any:
        """Proxy: recent news articles."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/news", params={"limit": limit, "min_relevance": min_relevance})

    @router.get("/api/nexus/news/search")
    async def nexus_news_search(
        q: str = Query(..., min_length=2),
        limit: int = Query(50, ge=1, le=200),
    ) -> Any:
        """Proxy: search news articles."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/news/search", params={"q": q, "limit": limit})

    @router.get("/api/nexus/news/category/{category}")
    async def nexus_news_category(
        category: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> Any:
        """Proxy: news by category."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/news/{category}", params={"limit": limit})

    @router.get("/api/nexus/news/digest")
    async def nexus_news_digest(
        hours: int = Query(24, ge=1, le=168),
        format: str = Query("markdown"),
        min_relevance: float = Query(0.0, ge=0.0, le=1.0),
    ) -> Dict[str, Any]:
        """Proxy: generate news digest."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/digest", params={"hours": hours, "format": format, "min_relevance": min_relevance})

    @router.get("/api/nexus/news/feeds")
    async def nexus_news_feeds() -> Any:
        """Proxy: list registered feeds."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/feeds")

    @router.get("/api/nexus/news/stats")
    async def nexus_news_stats() -> Dict[str, Any]:
        """Proxy: aggregator statistics."""
        return await _proxy_get(f"{NEXUS_NEWS_URL}/stats")

    return router


async def _proxy_get(url: str, params: Optional[Dict] = None) -> Any:
    """Proxy a GET request to a sidecar service."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
            if r.status_code == 404:
                raise HTTPException(404, "Not found")
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "Service unavailable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Service timeout")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Proxy error for {url}: {e}")
        raise HTTPException(502, f"Proxy error: {e}")

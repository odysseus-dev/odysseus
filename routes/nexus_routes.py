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
NEXUS_GATEWAY_URL = os.getenv("NEXUS_GATEWAY_URL", "http://nexus-gateway:8080")
NEXUS_MEMORY_URL = os.getenv("NEXUS_MEMORY_URL", "http://nexus-memory:8010")
NEXUS_WORKFLOWS_URL = os.getenv("NEXUS_WORKFLOWS_URL", "http://nexus-workflows:7800")
NEXUS_WEATHER_URL = os.getenv("NEXUS_WEATHER_URL", "http://nexus-weather:8080")
NEXUS_AUTOMATION_URL = os.getenv("NEXUS_AUTOMATION_URL", "http://nexus-automation:8090")
NEXUS_SECURITY_URL = os.getenv("NEXUS_SECURITY_URL", "http://nexus-security:8443")
NEXUS_MONITOR_URL = os.getenv("NEXUS_MONITOR_URL", "http://nexus-monitor:8099")
NEXUS_SECRETS_URL = os.getenv("NEXUS_SECRETS_URL", "http://nexus-secrets:7438")
DUINN_MEDIA_URL = os.getenv("DUINN_MEDIA_URL", "http://duinn-media:8080")


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

    # ── Nexus Gateway ─────────────────────────────────────────────────────

    @router.get("/api/nexus/gateway/health")
    async def nexus_gateway_health() -> Dict[str, Any]:
        """Proxy: nexus-gateway health check."""
        return await _proxy_get(f"{NEXUS_GATEWAY_URL}/health")

    @router.get("/api/nexus/gateway/metrics")
    async def nexus_gateway_metrics() -> Dict[str, Any]:
        """Proxy: gateway Prometheus metrics."""
        return await _proxy_get(f"{NEXUS_GATEWAY_URL}/metrics")

    @router.get("/api/nexus/gateway/routes")
    async def nexus_gateway_routes() -> Dict[str, Any]:
        """Proxy: gateway active routes."""
        return await _proxy_get(f"{NEXUS_GATEWAY_URL}/routes")

    # ── Nexus Memory ──────────────────────────────────────────────────────

    @router.get("/api/nexus/memory/health")
    async def nexus_memory_health() -> Dict[str, Any]:
        """Proxy: nexus-memory health check."""
        return await _proxy_get(f"{NEXUS_MEMORY_URL}/health")

    @router.get("/api/nexus/memory/memories")
    async def nexus_memory_list() -> Dict[str, Any]:
        """Proxy: list all memories."""
        return await _proxy_get(f"{NEXUS_MEMORY_URL}/memories")

    @router.get("/api/nexus/memory/search")
    async def nexus_memory_search(q: str = Query(...)) -> Dict[str, Any]:
        """Proxy: search memories."""
        return await _proxy_get(f"{NEXUS_MEMORY_URL}/search", {"q": q})

    @router.get("/api/nexus/memory/stats")
    async def nexus_memory_stats() -> Dict[str, Any]:
        """Proxy: memory statistics."""
        return await _proxy_get(f"{NEXUS_MEMORY_URL}/stats")

    # ── Nexus Workflows ───────────────────────────────────────────────────

    @router.get("/api/nexus/workflows/health")
    async def nexus_workflows_health() -> Dict[str, Any]:
        """Proxy: nexus-workflows health check."""
        return await _proxy_get(f"{NEXUS_WORKFLOWS_URL}/health")

    @router.get("/api/nexus/workflows/list")
    async def nexus_workflows_list() -> Dict[str, Any]:
        """Proxy: list all workflows."""
        return await _proxy_get(f"{NEXUS_WORKFLOWS_URL}/workflows")

    @router.get("/api/nexus/workflows/status")
    async def nexus_workflows_status() -> Dict[str, Any]:
        """Proxy: workflow run status."""
        return await _proxy_get(f"{NEXUS_WORKFLOWS_URL}/status")

    # ── Nexus Weather ─────────────────────────────────────────────────────

    @router.get("/api/nexus/weather/health")
    async def nexus_weather_health() -> Dict[str, Any]:
        """Proxy: nexus-weather health check."""
        return await _proxy_get(f"{NEXUS_WEATHER_URL}/health")

    @router.get("/api/nexus/weather/current")
    async def nexus_weather_current() -> Dict[str, Any]:
        """Proxy: current weather."""
        return await _proxy_get(f"{NEXUS_WEATHER_URL}/current")

    @router.get("/api/nexus/weather/forecast")
    async def nexus_weather_forecast() -> Dict[str, Any]:
        """Proxy: weather forecast."""
        return await _proxy_get(f"{NEXUS_WEATHER_URL}/forecast")

    @router.get("/api/nexus/weather/air-quality")
    async def nexus_weather_air_quality() -> Dict[str, Any]:
        """Proxy: air quality data."""
        return await _proxy_get(f"{NEXUS_WEATHER_URL}/air-quality")

    @router.get("/api/nexus/weather/sun")
    async def nexus_weather_sun() -> Dict[str, Any]:
        """Proxy: sun times."""
        return await _proxy_get(f"{NEXUS_WEATHER_URL}/sun")

    # ── Nexus Automation ──────────────────────────────────────────────────

    @router.get("/api/nexus/automation/health")
    async def nexus_automation_health() -> Dict[str, Any]:
        """Proxy: nexus-automation health check."""
        return await _proxy_get(f"{NEXUS_AUTOMATION_URL}/health")

    @router.get("/api/nexus/automation/rules")
    async def nexus_automation_rules() -> Dict[str, Any]:
        """Proxy: list automation rules."""
        return await _proxy_get(f"{NEXUS_AUTOMATION_URL}/rules")

    @router.get("/api/nexus/automation/sensors")
    async def nexus_automation_sensors() -> Dict[str, Any]:
        """Proxy: list sensor states."""
        return await _proxy_get(f"{NEXUS_AUTOMATION_URL}/sensors")

    @router.get("/api/nexus/automation/history")
    async def nexus_automation_history() -> Dict[str, Any]:
        """Proxy: action history."""
        return await _proxy_get(f"{NEXUS_AUTOMATION_URL}/history")

    # ── Nexus Security ────────────────────────────────────────────────────

    @router.get("/api/nexus/security/health")
    async def nexus_security_health() -> Dict[str, Any]:
        """Proxy: nexus-security health check."""
        return await _proxy_get(f"{NEXUS_SECURITY_URL}/health")

    @router.get("/api/nexus/security/threats")
    async def nexus_security_threats() -> Dict[str, Any]:
        """Proxy: current threats."""
        return await _proxy_get(f"{NEXUS_SECURITY_URL}/threats")

    @router.get("/api/nexus/security/blocked")
    async def nexus_security_blocked() -> Dict[str, Any]:
        """Proxy: blocked IPs."""
        return await _proxy_get(f"{NEXUS_SECURITY_URL}/blocked")

    @router.get("/api/nexus/security/audit")
    async def nexus_security_audit() -> Dict[str, Any]:
        """Proxy: audit log."""
        return await _proxy_get(f"{NEXUS_SECURITY_URL}/audit")

    @router.get("/api/nexus/security/whitelist")
    async def nexus_security_whitelist() -> Dict[str, Any]:
        """Proxy: IP whitelist."""
        return await _proxy_get(f"{NEXUS_SECURITY_URL}/whitelist")

    # ── Nexus Monitor ─────────────────────────────────────────────────────

    @router.get("/api/nexus/monitor/health")
    async def nexus_monitor_health() -> Dict[str, Any]:
        """Proxy: nexus-monitor health check."""
        return await _proxy_get(f"{NEXUS_MONITOR_URL}/health")

    @router.get("/api/nexus/monitor/nodes")
    async def nexus_monitor_nodes() -> Dict[str, Any]:
        """Proxy: cluster nodes."""
        return await _proxy_get(f"{NEXUS_MONITOR_URL}/nodes")

    @router.get("/api/nexus/monitor/services")
    async def nexus_monitor_services() -> Dict[str, Any]:
        """Proxy: monitored services."""
        return await _proxy_get(f"{NEXUS_MONITOR_URL}/services")

    @router.get("/api/nexus/monitor/alerts")
    async def nexus_monitor_alerts() -> Dict[str, Any]:
        """Proxy: active alerts."""
        return await _proxy_get(f"{NEXUS_MONITOR_URL}/alerts")

    @router.get("/api/nexus/monitor/stats")
    async def nexus_monitor_stats() -> Dict[str, Any]:
        """Proxy: system stats."""
        return await _proxy_get(f"{NEXUS_MONITOR_URL}/stats")

    # ── Nexus Secrets ─────────────────────────────────────────────────────

    @router.get("/api/nexus/secrets/health")
    async def nexus_secrets_health() -> Dict[str, Any]:
        """Proxy: nexus-secrets health check."""
        return await _proxy_get(f"{NEXUS_SECRETS_URL}/health")

    @router.get("/api/nexus/secrets/list")
    async def nexus_secrets_list() -> Dict[str, Any]:
        """Proxy: list secret names (not values)."""
        return await _proxy_get(f"{NEXUS_SECRETS_URL}/secrets")

    # ── Duinn Media ───────────────────────────────────────────────────────

    @router.get("/api/nexus/media/health")
    async def duinn_media_health() -> Dict[str, Any]:
        """Proxy: duinn-media health check."""
        return await _proxy_get(f"{DUINN_MEDIA_URL}/health")

    @router.get("/api/nexus/media/library")
    async def duinn_media_library() -> Dict[str, Any]:
        """Proxy: media library."""
        return await _proxy_get(f"{DUINN_MEDIA_URL}/api/library")

    @router.get("/api/nexus/media/scan")
    async def duinn_media_scan() -> Dict[str, Any]:
        """Proxy: trigger media scan."""
        return await _proxy_get(f"{DUINN_MEDIA_URL}/api/scan")

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

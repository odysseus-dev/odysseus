"""Prometheus gauges/counters for overlay stability.

Agents: Startup purge refreshes ``odysseus_model_endpoints_cloud_rows``.
The stability probe POSTs ``/api/overlay/native-probe`` so
``odysseus_overlay_native_probe_success`` is set on the uvicorn process that
Prometheus scrapes (not the ``compose exec`` PID). ``refresh_dependency_up``
runs from ``GET /metrics`` and probes overlay health/metrics URLs.
``record_client_http_error`` is called from the OpenHands client on 401/5xx.
Scraped at ``GET /metrics`` via prometheus_client ``generate_latest``.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from prometheus_client import Counter, Gauge

# Remaining public-cloud ModelEndpoint rows (should be 0 after startup purge).
_CLOUD_ROWS = Gauge(
    "odysseus_model_endpoints_cloud_rows",
    "Count of ModelEndpoint rows whose base_url is a public cloud inference URL",
)

# Last overlay native chat pipe probe outcome (1 ok, 0 failed); unset reads 0.
_NATIVE_PROBE = Gauge(
    "odysseus_overlay_native_probe_success",
    "Whether the last overlay native chat stability probe succeeded (1/0)",
)

# OpenHands / 9router client HTTP errors observed by Odysseus (not Agent Server).
_CLIENT_HTTP_ERRORS = Counter(
    "odysseus_overlay_client_http_errors_total",
    "Odysseus client HTTP errors toward overlay OpenHands or 9router",
    ["target", "code"],
)

# 1 when Odysseus can reach the dependency health/metrics URL.
_DEPENDENCY_UP = Gauge(
    "odysseus_overlay_dependency_up",
    "1 if overlay dependency health or metrics URL is reachable from Odysseus",
    ["service"],
)

# Overlay DNS names only. Host publishes are not used from inside the network.
_DEPENDENCY_URLS: dict[str, str] = {
    "9router": "http://9router:20128/api/health",
    "openhands-agent-server": "http://openhands-agent-server:8000/health",
    "tempo": "http://tempo:3200/metrics",
    "langfuse": "http://langfuse-web:3000/api/public/health",
    "otel-collector": "http://otel-collector:8889/metrics",
    "prometheus": "http://prometheus:9090/-/ready",
}


def set_cloud_endpoint_rows(n: int) -> None:
    """Set ``odysseus_model_endpoints_cloud_rows`` to ``n``."""
    _CLOUD_ROWS.set(int(n))


def set_native_probe_success(ok: bool) -> None:
    """Set ``odysseus_overlay_native_probe_success`` to 1 or 0."""
    _NATIVE_PROBE.set(1 if ok else 0)


def record_client_http_error(target: str, status: int) -> None:
    """Increment 401 or 5xx counters. Other statuses are ignored.

    Agents: ``target`` is ``openhands`` or ``9router``. Call from the client
    that saw the status; never pass response bodies.
    """
    code = int(status or 0)
    if code == 401:
        _CLIENT_HTTP_ERRORS.labels(target=str(target), code="401").inc()
    elif code >= 500:
        _CLIENT_HTTP_ERRORS.labels(target=str(target), code="5xx").inc()


def set_dependency_up(service: str, ok: bool) -> None:
    """Set ``odysseus_overlay_dependency_up{service=...}`` to 1 or 0."""
    _DEPENDENCY_UP.labels(service=str(service)).set(1 if ok else 0)


def _probe_url(url: str, timeout: float = 0.5) -> bool:
    """True when ``url`` returns HTTP < 500. Used by ``refresh_dependency_up``."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status) < 500
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False
    except Exception:
        return False


def refresh_dependency_up() -> None:
    """Probe each overlay dependency and refresh labeled ``dependency_up`` gauges.

    Agents: called from ``GET /metrics`` so Prometheus scrapes a fresh view.
    Timeouts stay short so a down peer cannot stall the scrape.
    """
    for service, url in _DEPENDENCY_URLS.items():
        set_dependency_up(service, _probe_url(url))

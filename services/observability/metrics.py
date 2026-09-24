"""Prometheus gauges for overlay stability (cloud endpoint rows, native probe).

Agents: Task 8 stability probe calls ``set_native_probe_success``. Startup
purge and ``purge_leftover_cloud_model_endpoints`` refresh
``odysseus_model_endpoints_cloud_rows`` to the live public-cloud ModelEndpoint
count. Scraped at ``GET /metrics`` via prometheus_client ``generate_latest``.
"""

from prometheus_client import Gauge

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


def set_cloud_endpoint_rows(n: int) -> None:
    """Set ``odysseus_model_endpoints_cloud_rows`` to ``n``."""
    _CLOUD_ROWS.set(int(n))


def set_native_probe_success(ok: bool) -> None:
    """Set ``odysseus_overlay_native_probe_success`` to 1 or 0."""
    _NATIVE_PROBE.set(1 if ok else 0)

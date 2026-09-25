"""Prometheus gauges for overlay cloud ModelEndpoint rows and native probe.

Agents: ``services.observability.metrics`` registers gauges/counters on the
process REGISTRY. Purge refreshes ``odysseus_model_endpoints_cloud_rows``.
The stability probe POSTs uvicorn so ``odysseus_overlay_native_probe_success``
is scraped from the server process, not ``compose exec``.
"""

from types import SimpleNamespace

from prometheus_client import REGISTRY, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from tests.helpers.import_state import preserve_import_state

with preserve_import_state("core.database", "src.database", "routes.model_routes"):
    import routes.model_routes as model_routes

from services.observability import metrics as overlay_metrics


def _sample_value(name: str, labels: dict | None = None) -> float | None:
    """Read a gauge/counter sample from the process REGISTRY."""
    return REGISTRY.get_sample_value(name, labels or {})


def test_set_cloud_endpoint_rows_updates_registry():
    overlay_metrics.set_cloud_endpoint_rows(4)
    assert _sample_value("odysseus_model_endpoints_cloud_rows") == 4.0
    overlay_metrics.set_cloud_endpoint_rows(0)
    assert _sample_value("odysseus_model_endpoints_cloud_rows") == 0.0


def test_set_native_probe_success_updates_registry():
    overlay_metrics.set_native_probe_success(False)
    assert _sample_value("odysseus_overlay_native_probe_success") == 0.0
    overlay_metrics.set_native_probe_success(True)
    assert _sample_value("odysseus_overlay_native_probe_success") == 1.0


def test_record_client_http_error_counts_401_and_5xx():
    before_401 = _sample_value(
        "odysseus_overlay_client_http_errors_total",
        {"target": "openhands", "code": "401"},
    ) or 0.0
    before_5xx = _sample_value(
        "odysseus_overlay_client_http_errors_total",
        {"target": "openhands", "code": "5xx"},
    ) or 0.0
    overlay_metrics.record_client_http_error("openhands", 401)
    overlay_metrics.record_client_http_error("openhands", 503)
    overlay_metrics.record_client_http_error("openhands", 200)
    overlay_metrics.record_client_http_error("9router", 401)
    assert _sample_value(
        "odysseus_overlay_client_http_errors_total",
        {"target": "openhands", "code": "401"},
    ) == before_401 + 1.0
    assert _sample_value(
        "odysseus_overlay_client_http_errors_total",
        {"target": "openhands", "code": "5xx"},
    ) == before_5xx + 1.0
    assert _sample_value(
        "odysseus_overlay_client_http_errors_total",
        {"target": "9router", "code": "401"},
    ) >= 1.0


def test_set_dependency_up_updates_labeled_gauge():
    overlay_metrics.set_dependency_up("9router", True)
    assert _sample_value(
        "odysseus_overlay_dependency_up", {"service": "9router"}
    ) == 1.0
    overlay_metrics.set_dependency_up("9router", False)
    assert _sample_value(
        "odysseus_overlay_dependency_up", {"service": "9router"}
    ) == 0.0


def test_refresh_dependency_up_probes_configured_urls(monkeypatch):
    seen = []

    def fake_probe(url: str, timeout: float = 0.5) -> bool:
        seen.append(url)
        return "9router" in url or "tempo" in url

    monkeypatch.setattr(overlay_metrics, "_probe_url", fake_probe)
    overlay_metrics.refresh_dependency_up()
    assert any("9router" in u for u in seen)
    assert any("openhands-agent-server" in u for u in seen)
    assert any("tempo" in u for u in seen)
    assert any("langfuse" in u for u in seen)
    assert any("otel-collector" in u for u in seen)
    assert any("prometheus" in u for u in seen)
    assert _sample_value(
        "odysseus_overlay_dependency_up", {"service": "9router"}
    ) == 1.0
    assert _sample_value(
        "odysseus_overlay_dependency_up", {"service": "openhands-agent-server"}
    ) == 0.0


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, endpoints, sessions):
        self.endpoints = list(endpoints)
        self.sessions = list(sessions)
        self.deleted = []
        self.committed = False

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "ModelEndpoint":
            return _FakeQuery(self.endpoints)
        return _FakeQuery(self.sessions)

    def delete(self, row):
        self.deleted.append(row)
        if row in self.endpoints:
            self.endpoints.remove(row)

    def commit(self):
        self.committed = True


def test_purge_sets_cloud_rows_gauge_to_zero(monkeypatch):
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "_save_settings", lambda _s: None)
    monkeypatch.setattr(
        model_routes,
        "_clear_user_pref_endpoint_refs",
        lambda prefs, ep_id: 0,
    )
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")
    cloud = SimpleNamespace(
        id="ep-cloud",
        base_url="https://api.openai.com/v1",
        api_key="",
        provider_auth_id=None,
    )
    local = SimpleNamespace(
        id="ep-local",
        base_url="http://ollama:11434/v1",
        api_key="",
        provider_auth_id=None,
    )
    db = _FakeDb([cloud, local], [])
    overlay_metrics.set_cloud_endpoint_rows(99)
    model_routes.purge_leftover_cloud_model_endpoints(db)
    assert _sample_value("odysseus_model_endpoints_cloud_rows") == 0.0


def test_purge_noop_when_only_local_sets_cloud_rows_gauge(monkeypatch):
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "_save_settings", lambda _s: None)
    local = SimpleNamespace(
        id="ep-lan",
        base_url="http://192.168.1.10:8080/v1",
        api_key="",
        provider_auth_id=None,
    )
    db = _FakeDb([local], [])
    overlay_metrics.set_cloud_endpoint_rows(5)
    model_routes.purge_leftover_cloud_model_endpoints(db)
    assert _sample_value("odysseus_model_endpoints_cloud_rows") == 0.0


def test_metrics_route_exposes_gauges():
    from fastapi.testclient import TestClient

    from app import app

    overlay_metrics.set_cloud_endpoint_rows(2)
    overlay_metrics.set_native_probe_success(True)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    families = {
        family.name: family
        for family in text_string_to_metric_families(response.text)
    }
    assert "odysseus_model_endpoints_cloud_rows" in families
    assert "odysseus_overlay_native_probe_success" in families
    assert "odysseus_overlay_dependency_up" in families
    assert "odysseus_overlay_client_http_errors" in families
    payload = generate_latest(REGISTRY).decode("utf-8")
    assert "odysseus_model_endpoints_cloud_rows" in payload


def test_fastapi_instrumentor_excludes_metrics_and_health():
    text = open("app.py", encoding="utf-8").read()
    assert "FastAPIInstrumentor.instrument_app" in text
    idx = text.index("FastAPIInstrumentor.instrument_app")
    window = text[idx : idx + 220]
    assert "excluded_urls" in window
    assert "/metrics" in window
    assert "/api/health" in window


def test_prometheus_scrapes_tempo_and_self():
    text = open("deploy/observability/prometheus.yml", encoding="utf-8").read()
    assert "otel-collector:8889" in text
    assert "odysseus:7000" in text
    assert "tempo:3200" in text
    assert "prometheus:9090" in text


def test_stability_probe_posts_probe_gauge_to_uvicorn():
    text = open("scripts/overlay_stability_probe.py", encoding="utf-8").read()
    assert "/api/overlay/native-probe" in text
    assert "native-probe" in text


def test_native_probe_route_exists_in_app():
    text = open("app.py", encoding="utf-8").read()
    assert "/api/overlay/native-probe" in text

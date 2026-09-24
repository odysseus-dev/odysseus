# tests/test_otel_configure.py
# Fail closed: Odysseus exports traces only when the overlay Collector endpoint is set.
import pytest


def test_configure_tracer_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from services.observability.otel import configure_tracer
    configure_tracer("odysseus")  # must not raise


def test_configure_tracer_rejects_tempo_url(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318")
    from services.observability.otel import configure_tracer
    with pytest.raises(ValueError, match="otel-collector"):
        configure_tracer("odysseus")


def test_configure_tracer_rejects_collector_in_query_string(monkeypatch):
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4318?x=otel-collector"
    )
    from services.observability.otel import configure_tracer
    with pytest.raises(ValueError, match="otel-collector"):
        configure_tracer("odysseus")

# tests/test_otel_configure.py
# Fail closed: Odysseus exports traces only when the overlay Collector endpoint is set.
import os

import pytest

_COLLECTOR_ENDPOINT = "http://otel-collector:4318"


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


def test_configure_sets_traceloop_trace_content_false(monkeypatch):
    monkeypatch.delenv("TRACELOOP_TRACE_CONTENT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _COLLECTOR_ENDPOINT)
    from services.observability.otel import configure_tracer

    configure_tracer("odysseus")
    assert os.environ.get("TRACELOOP_TRACE_CONTENT") == "false"


def test_configure_traceloop_content_false_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("TRACELOOP_TRACE_CONTENT", raising=False)
    from services.observability.otel import configure_tracer

    configure_tracer("odysseus")
    assert os.environ.get("TRACELOOP_TRACE_CONTENT") == "false"


def test_configure_overwrites_traceloop_trace_content_true(monkeypatch):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from services.observability.otel import configure_tracer

    configure_tracer("odysseus")
    assert os.environ.get("TRACELOOP_TRACE_CONTENT") == "false"
    monkeypatch.delenv("TRACELOOP_API_KEY", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _COLLECTOR_ENDPOINT)
    from services.observability.otel import configure_tracer

    configure_tracer("odysseus")


def test_configure_preserves_collector_otel_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _COLLECTOR_ENDPOINT)
    from services.observability.otel import configure_tracer

    configure_tracer("odysseus")
    assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == _COLLECTOR_ENDPOINT

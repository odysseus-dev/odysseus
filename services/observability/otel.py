"""Process tracer that exports only to the overlay Collector.

Agents: Odysseus and the model-job worker must not be given Tempo or Langfuse
URLs. ``configure_tracer`` reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` and refuses any
set value that does not name ``otel-collector``. Unset means no exporter (local
runs and unit tests stay quiet). After a provider is installed, outbound ``httpx``
calls are auto-instrumented (9router and other HTTP clients). OpenLLMetry-style
libraries run without Traceloop cloud: ``TRACELOOP_TRACE_CONTENT`` defaults to
``false`` and ``Traceloop.init`` is never called. Callers start spans with
``get_tracer`` and must set attributes through ``apply_span_attributes`` so
secrets and prompt bodies are dropped first. ``ODYSSEUS_SYNTHETIC=1`` adds
``odysseus.synthetic``. Spec §6 GenAI chat spans use ``chat_completion_span``
(name ``chat {model}``, no prompt bodies).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

_COLLECTOR_HOST_MARK = "otel-collector"


def configure_tracer(service_name: str) -> None:
    """Install a batch OTLP/HTTP exporter, or no-op when no endpoint is set.

    The exporter URL is ``{endpoint}/v1/traces``. A set endpoint that does not
    contain ``otel-collector`` raises ``ValueError`` so traces cannot leak to
    Tempo or any other backend the app was pointed at by mistake.

    When the provider is installed, ``HTTPXClientInstrumentor`` patches ``httpx``
    so outbound HTTP (including 9router) emits client spans on the same OTLP
    exporter. No ``TRACELOOP_API_KEY`` or Traceloop SaaS export is used.
    """
    os.environ["TRACELOOP_TRACE_CONTENT"] = "false"
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return
    host = (urlparse(endpoint).hostname or "").lower()
    if host != _COLLECTOR_HOST_MARK:
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT must contain otel-collector"
        )
    base = endpoint.rstrip("/")
    exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces")
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # httpx auto-instrumentation is best-effort. Model-jobs uses urllib and may
    # fail to import httpx when ``/app/calendar`` shadows the stdlib module.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass


def get_tracer(name: str):
    """Return the global tracer for ``name``. No-op until ``configure_tracer``."""
    return trace.get_tracer(name)


def apply_span_attributes(span, attrs: dict[str, object]) -> None:
    """Set attributes after redaction. Never pass prompt bodies or API keys.

    Agents: this is the only supported way to attach attributes to an Odysseus
    span. Keys that match the collector deny-list are dropped. When
    ``ODYSSEUS_SYNTHETIC=1``, ``odysseus.synthetic`` is forced true.
    """
    from services.observability.redact import redact_span_attributes

    cleaned = redact_span_attributes(dict(attrs))
    if os.environ.get("ODYSSEUS_SYNTHETIC") == "1":
        cleaned["odysseus.synthetic"] = True
    for key, value in cleaned.items():
        if value is None:
            continue
        span.set_attribute(key, value)


def record_span_error(span, exc: BaseException) -> None:
    """Mark ``span`` ERROR and store the exception type. Re-raise at the call site."""
    span.record_exception(exc)
    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


def chat_span_name(model: str) -> str:
    """Return the spec §6 span name ``chat {model}`` (or ``chat`` when empty)."""
    cleaned = str(model or "").strip()
    return f"chat {cleaned}" if cleaned else "chat"


@contextmanager
def chat_completion_span(
    model: str, *, provider: str = "9router"
) -> Iterator:
    """Start a GenAI ``chat`` span for a 9router (or probe) completion.

    Agents: sets ``gen_ai.operation.name=chat`` so Langfuse maps a GENERATION.
    Never attach ``gen_ai.input.messages`` or completion bodies. Callers set
    ``http.status_code`` before exiting the context.
    """
    tracer = get_tracer("odysseus")
    with tracer.start_as_current_span(chat_span_name(model)) as span:
        apply_span_attributes(
            span,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": provider,
                "gen_ai.request.model": str(model or ""),
            },
        )
        yield span

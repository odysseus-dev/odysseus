"""Process tracer that exports only to the overlay Collector.

Agents: Odysseus and the model-job worker must not be given Tempo or Langfuse
URLs. ``configure_tracer`` reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` and refuses any
set value that does not name ``otel-collector``. Unset means no exporter (local
runs and unit tests stay quiet). Callers start spans with ``get_tracer`` and
must set attributes through ``apply_span_attributes`` so secrets and prompt
bodies are dropped first. ``ODYSSEUS_SYNTHETIC=1`` adds ``odysseus.synthetic``.
"""

import os
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
    """
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

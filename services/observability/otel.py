"""Process tracer that exports only to the overlay Collector.

Agents: Odysseus and the model-job worker must not be given Tempo or Langfuse
URLs. ``configure_tracer`` reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` and refuses any
set value that does not name ``otel-collector``. Unset means no exporter (local
runs and unit tests stay quiet). Spans are a later task; this module only
installs the provider and returns tracers.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

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
    if _COLLECTOR_HOST_MARK not in endpoint:
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

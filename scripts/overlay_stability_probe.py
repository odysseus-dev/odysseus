#!/usr/bin/env python3
"""Black-box overlay stability probe with a synthetic trace id.

Runs inside the ``odysseus`` container (compose network), not on the Mac.
From the Mac repo:

    ./scripts/run_overlay_stability_probe.sh

Stdout is one JSON object ``{ok, trace_id, session_id, steps}``. Exit 0 iff
``ok``. The sidecar virtual key and internal token are never printed.

Steps, in order: compose files, health, settings model, sidecar boolean,
catalog pick (skip ``gpt-6-astra`` and ``-review``), cloud_rows, native pipe
(Hello/Hi via Odysseus ``/api/session`` + ``/api/chat_stream``), probe gauge.

The root span is ``overlay.stability`` with ``odysseus.synthetic=true``.
``trace_id`` is the hex id of ``otel.trace.get_current_span()`` inside that span;
W3C ``traceparent`` continues into uvicorn so Tempo shows ``overlay.bind``.
``session_id`` is the Odysseus session id.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# Import ``services`` and the sibling native-chat probe when exec'd as a file.
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for entry in (str(ROOT), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# /app/calendar shadows stdlib; OTLP exporter needs timegm before otel import.
from services.observability.stdlib_calendar import prefer_stdlib_calendar

prefer_stdlib_calendar()

from overlay_native_chat_probe import (  # noqa: E402
    AGENT,
    NINE,
    SKIP,
    _http,
    _pick_catalog_id,
    _read_sidecar_key,
    run_odysseus_native_pipe,
)

# Overlay project files. Relay is forbidden. Observability must be present.
COMPOSE_REQUIRED = (
    "docker-compose.yml",
    "docker-compose.openhands.yml",
    "docker-compose.observability.yml",
)
COMPOSE_FORBIDDEN = "docker-compose.relay.yml"
# Pinned Native route. openai/auto 404s (no OpenAI API key) and is not the pin.
PINNED_MODEL_PREFIX = "openai/cx/"
REJECT_MODEL = "openai/auto"
NINE_BASE = "http://9router:20128/v1"
HEALTH_URLS = {
    "odysseus": "http://127.0.0.1:7000/api/health",
    "9router": f"{NINE}/api/health",
    "agent-server": f"{AGENT}/health",
}
CLOUD_ROWS_METRIC = "odysseus_model_endpoints_cloud_rows"


def _scrub(value):
    """Drop key-shaped strings before they can reach stdout."""
    if isinstance(value, str):
        if value.startswith("sk-") or "Bearer " in value:
            return "[redacted]"
        return value
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            low = str(key).lower()
            if "api_key" in low or "authorization" in low or "virtual_key" in low:
                continue
            cleaned[key] = _scrub(item)
        return cleaned
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _compose_files_step() -> dict:
    """Require the three overlay files and reject relay.

    The wrapper sets ``OVERLAY_COMPOSE_CONFIG_FILES`` from ``docker compose ls``
    on the guest host. Basenames are compared. The raw env is not echoed.
    """
    raw = os.environ.get("OVERLAY_COMPOSE_CONFIG_FILES", "")
    names = [Path(part.strip()).name for part in raw.split(",") if part.strip()]
    forbidden = COMPOSE_FORBIDDEN in names or any("relay" in name for name in names)
    ok = all(name in names for name in COMPOSE_REQUIRED) and not forbidden
    return {"name": "compose_files", "ok": ok, "files": names}


def _health_step() -> dict:
    """GET Odysseus, 9router, and Agent Server health. All three must be 200."""
    checks = []
    for name, url in HEALTH_URLS.items():
        status, _body = _http(url, timeout=10)
        checks.append({"name": name, "http": status})
    ok = all(item["http"] == 200 for item in checks)
    return {"name": "health", "ok": ok, "checks": checks}


def _settings_model_step() -> dict:
    """Native settings model must be openai/cx/…, never openai/auto.

    Only model and base_url are recorded. ``llm.api_key`` from Agent Server
    is redacted upstream and is not copied here.
    """
    status, payload = _http(f"{AGENT}/api/settings", timeout=15)
    llm = {}
    if isinstance(payload, dict):
        agent = payload.get("agent_settings") or {}
        if isinstance(agent, dict) and isinstance(agent.get("llm"), dict):
            llm = agent["llm"]
    model = str(llm.get("model") or "")
    base_url = str(llm.get("base_url") or "")
    rejected = model == REJECT_MODEL or model.endswith("/auto")
    skipped = any(token in model for token in SKIP)
    ok = (
        status == 200
        and model.startswith(PINNED_MODEL_PREFIX)
        and not rejected
        and not skipped
        and base_url.rstrip("/") == NINE_BASE
    )
    return {
        "name": "settings_model",
        "ok": ok,
        "http": status,
        "model": model,
        "base_url": base_url,
    }


def _sidecar_step() -> dict:
    """Boolean only: sidecar file exists and is non-empty. Never the key."""
    non_empty = bool(_read_sidecar_key())
    return {"name": "sidecar", "ok": non_empty, "non_empty": non_empty}


def _catalog_step() -> dict:
    """GET 9router /v1/models and pick a catalog id that skips astra and review."""
    key = _read_sidecar_key()
    if not key:
        return {"name": "catalog", "ok": False, "http": 0, "catalog_id": ""}
    status, models = _http(
        f"{NINE}/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20,
    )
    ids = []
    if isinstance(models, dict):
        ids = [str(item.get("id") or "") for item in (models.get("data") or [])]
        ids = [item for item in ids if item]
    catalog_id = _pick_catalog_id(ids)
    skipped = any(token in catalog_id for token in SKIP)
    ok = status == 200 and bool(catalog_id) and not skipped and catalog_id != REJECT_MODEL
    return {
        "name": "catalog",
        "ok": ok,
        "http": status,
        "count": len(ids),
        "catalog_id": catalog_id,
    }


def _cloud_rows_value(body: str) -> float | None:
    """Parse ``odysseus_model_endpoints_cloud_rows`` from Prometheus text."""
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        head, _, value = line.partition(" ")
        metric = head.split("{", 1)[0]
        if metric != CLOUD_ROWS_METRIC:
            continue
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _cloud_rows_step() -> dict:
    """Read the cloud-row gauge from Odysseus ``/metrics``. Must be 0."""
    status, body = _http("http://127.0.0.1:7000/metrics", timeout=10)
    text = body if isinstance(body, str) else ""
    value = _cloud_rows_value(text)
    ok = status == 200 and value == 0.0
    return {"name": "cloud_rows", "ok": ok, "http": status, "cloud_rows": value}


def _native_pipe_step() -> dict:
    """Hello/Hi through Odysseus HTTP (phone path). Nested steps stay; secrets do not.

    Agents: must call ``run_odysseus_native_pipe`` so uvicorn emits
    ``overlay.bind`` / ``invoke_agent Native`` / ``openhands.*`` on the same
    ``trace_id`` as ``overlay.stability`` (W3C traceparent).
    """
    pipe = _scrub(run_odysseus_native_pipe())
    return {
        "name": "native_pipe",
        "ok": bool(pipe.get("ok")),
        "session_id": pipe.get("session_id"),
        "openhands_conversation_id": pipe.get("openhands_conversation_id"),
        "steps": pipe.get("steps") or [],
        "error": pipe.get("error"),
    }


def _set_probe_gauge(ok: bool) -> dict:
    """Record the last probe outcome on uvicorn via ``/api/overlay/native-probe``.

    Agents: this process is ``compose exec``, not the scraped worker. POST the
    result with the loopback internal token so Prometheus sees the gauge on
    ``odysseus:7000/metrics``.
    """
    from overlay_native_chat_probe import _http, _odysseus_auth_headers

    status, body = _http(
        "http://127.0.0.1:7000/api/overlay/native-probe",
        method="POST",
        body={"ok": bool(ok)},
        headers=_odysseus_auth_headers(),
        timeout=10,
    )
    recorded = bool(isinstance(body, dict) and body.get("ok"))
    return {
        "name": "probe_gauge",
        "ok": status == 200 and recorded,
        "http": status,
        "value": 1 if ok else 0,
    }


def _current_trace_id() -> str:
    """Hex trace id from the current span, or empty when the context is invalid.

    Agents: call this inside the ``overlay.stability`` span so
    ``otel.trace.get_current_span()`` is that root span.
    """
    from opentelemetry import trace as otel_trace

    current = otel_trace.get_current_span()
    context = current.get_span_context()
    if not context.is_valid:
        return ""
    return format(context.trace_id, "032x")


def _flush_tracer() -> None:
    """Push the synthetic span before the process exits."""
    from opentelemetry import trace as otel_trace

    provider = otel_trace.get_tracer_provider()
    force_flush = getattr(provider, "force_flush", None)
    if callable(force_flush):
        force_flush()


def main() -> int:
    """Run the stability steps and print one JSON report."""
    from services.observability.otel import (
        apply_span_attributes,
        configure_tracer,
        get_tracer,
    )

    os.environ["ODYSSEUS_SYNTHETIC"] = "1"
    try:
        configure_tracer("odysseus")
    except ValueError:
        # Endpoint pointed somewhere other than the Collector. Still probe.
        pass

    report: dict = {"ok": False, "trace_id": "", "session_id": None, "steps": []}
    tracer = get_tracer("odysseus.overlay")
    try:
        with tracer.start_as_current_span("overlay.stability") as span:
            apply_span_attributes(span, {"odysseus.synthetic": True})
            span.set_attribute("odysseus.synthetic", True)
            # trace_id comes from the current span, not a stored SpanContext copy.
            report["trace_id"] = _current_trace_id()

            report["steps"].append(_compose_files_step())
            report["steps"].append(_health_step())
            report["steps"].append(_settings_model_step())
            report["steps"].append(_sidecar_step())
            report["steps"].append(_catalog_step())
            report["steps"].append(_cloud_rows_step())

            prior_ok = all(bool(step.get("ok")) for step in report["steps"])
            if prior_ok:
                pipe = _native_pipe_step()
            else:
                pipe = {
                    "name": "native_pipe",
                    "ok": False,
                    "session_id": None,
                    "steps": [],
                    "error": "skipped",
                }
            report["steps"].append(pipe)
            report["session_id"] = pipe.get("session_id")

            probe_ok = prior_ok and bool(pipe.get("ok")) and bool(report["trace_id"])
            try:
                report["steps"].append(_set_probe_gauge(probe_ok))
            except Exception as exc:  # noqa: BLE001 — gauge failure is a step, not a traceback
                report["steps"].append({
                    "name": "probe_gauge",
                    "ok": False,
                    "error": type(exc).__name__,
                })
            gauge_ok = bool(report["steps"][-1].get("ok"))
            report["ok"] = probe_ok and gauge_ok
    finally:
        print(json.dumps(_scrub(report), indent=2))
        try:
            _flush_tracer()
        except Exception:
            # Export failure must not replace the JSON report or the exit code.
            pass
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

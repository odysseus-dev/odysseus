"""Native session lifecycle spans: names from spec §6, no secrets on attributes.

Agents: these tests install an in-memory exporter on the global tracer. They
call overlay bind and a fake OpenHands create through ``stream_governed_agent``.
``api_key`` and ``gen_ai.input.messages`` must not appear on any span.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from tests.agents._import_agents import ensure_agents_package
from tests.helpers.import_state import preserve_import_state

with preserve_import_state(
    "core.database", "src.database", "routes.model_routes", "routes.prefs_routes"
):
    import routes.model_routes as model_routes

ensure_agents_package()

from services.agents.dispatcher import AgentDispatcher  # noqa: E402
from services.agents.legacy_bridge import stream_governed_agent  # noqa: E402
from services.agents.openhands_client import (  # noqa: E402
    OpenHandsClient,
    OpenHandsFailure,
)

_SECRET = "sk-native-lifecycle-secret"


def _install_memory_exporter() -> InMemorySpanExporter:
    """Record finished spans without exporting to a collector."""
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        current.add_span_processor(processor)
        return exporter
    provider = TracerProvider()
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def memory_spans():
    exporter = _install_memory_exporter()
    exporter.clear()
    yield exporter
    exporter.clear()


def _walk_attributes(span):
    """Yield span and event attributes so a secret cannot hide on an event."""
    for key, value in (span.attributes or {}).items():
        yield key, value
    for event in span.events:
        for key, value in (event.attributes or {}).items():
            yield key, value


def _assert_no_secrets(spans) -> None:
    for span in spans:
        for key, value in _walk_attributes(span):
            assert "api_key" not in str(key).lower()
            assert key != "gen_ai.input.messages"
            assert _SECRET not in str(value)
            assert "**********" not in str(value)


class _CreateTransport:
    """Settings GET returns a redacted key; POST create echoes a new conv id."""

    def __init__(self, *, create_status: int | None = None) -> None:
        self.create_status = create_status
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        self.calls.append((method, path))
        if method == "GET" and path == "/api/settings":
            return {
                "agent_settings": {
                    "llm": {
                        "model": "openai/cx/gpt-5.5",
                        "base_url": "http://9router:20128/v1",
                        "api_key": "**********",
                    }
                }
            }
        if method == "POST" and path == "/api/conversations":
            if self.create_status:
                raise OpenHandsFailure("agent-server", f"http {self.create_status}")
            assert (body or {}).get("agent_settings", {}).get("llm", {}).get(
                "api_key"
            ) == _SECRET
            return {"id": "conv-created"}
        if method == "GET" and path.endswith("/events/search"):
            return {
                "items": [
                    {
                        "id": "s1",
                        "kind": "ConversationStateUpdate",
                        "status": "finished",
                    }
                ]
            }
        if method == "GET" and "/conversations/" in path:
            return {"id": "conv-created", "execution_status": "finished"}
        raise AssertionError(f"unexpected {method} {path}")


def _run_native_turn(transport: _CreateTransport) -> None:
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent")
    dispatcher = AgentDispatcher(client=client)

    async def _collect():
        return [
            chunk
            async for chunk in stream_governed_agent(
                dispatcher=dispatcher,
                messages=[{"role": "user", "content": "Hello"}],
                turn_id="turn-otel",
                poll_timeout_s=1,
            )
        ]

    asyncio.run(_collect())


def test_lifecycle_span_names_and_api_key_absent(memory_spans, monkeypatch, tmp_path):
    """Fake OpenHands create emits the five lifecycle spans and no key material."""
    key_file = Path(tmp_path) / "native-llm-api-key"
    key_file.write_text(_SECRET, encoding="utf-8")
    monkeypatch.setenv("OPENHANDS_NATIVE_LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("ODYSSEUS_SYNTHETIC", "1")
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")

    bound = model_routes.overlay_session_bind("automatic", "", "")
    assert bound == ("http://9router:20128/v1", "automatic")
    _run_native_turn(_CreateTransport())

    finished = list(memory_spans.get_finished_spans())
    names = {span.name for span in finished}
    assert {
        "overlay.bind",
        "openhands.settings",
        "openhands.create",
        "openhands.idle",
        "invoke_agent Native",
    } <= names
    _assert_no_secrets(finished)

    by_name = {span.name: span for span in finished}
    bind = by_name["overlay.bind"].attributes
    assert bind["odysseus.overlay"] is True
    assert bind["url.full"] == "http://9router:20128/v1"
    assert bind["gen_ai.conversation.id"] == ""
    assert bind["odysseus.synthetic"] is True

    settings = by_name["openhands.settings"].attributes
    assert settings["http.status_code"] == 200
    assert settings["odysseus.sidecar_restored"] is True
    assert settings["odysseus.synthetic"] is True

    created = by_name["openhands.create"].attributes
    assert created["http.status_code"] == 200
    assert created["gen_ai.request.model"] == "openai/cx/gpt-5.5"
    assert created["odysseus.synthetic"] is True

    invoked = by_name["invoke_agent Native"].attributes
    assert invoked["gen_ai.operation.name"] == "invoke_agent"
    assert invoked["gen_ai.agent.name"] == "Native"
    assert invoked["gen_ai.conversation.id"] == "conv-created"
    assert invoked["odysseus.synthetic"] is True

    idle = by_name["openhands.idle"]
    assert idle.attributes["odysseus.execution_status"] == "finished"
    assert idle.attributes["odysseus.synthetic"] is True
    assert any(event.name == "odysseus.assistant.empty" for event in idle.events)


def test_overlay_bind_url_full_is_host_and_path(memory_spans, monkeypatch):
    """Query and userinfo never land on url.full when bind declines leftover."""
    monkeypatch.delenv("ODYSSEUS_SYNTHETIC", raising=False)
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")
    bound = model_routes.overlay_session_bind(
        "llama3",
        "",
        "http://user:pw@10.0.0.5:11434/v1?api_key=sk-live",
    )
    assert bound is None
    spans = [
        span
        for span in memory_spans.get_finished_spans()
        if span.name == "overlay.bind"
    ]
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs["odysseus.overlay"] is False
    assert attrs["url.full"] == "http://10.0.0.5:11434/v1"
    assert "api_key" not in attrs["url.full"]
    assert "pw" not in attrs["url.full"]
    assert "odysseus.synthetic" not in attrs


def test_openhands_create_non_2xx_records_error(memory_spans, monkeypatch, tmp_path):
    """POST /api/conversations 401 sets span status ERROR and the exception type."""
    key_file = Path(tmp_path) / "native-llm-api-key"
    key_file.write_text(_SECRET, encoding="utf-8")
    monkeypatch.setenv("OPENHANDS_NATIVE_LLM_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("ODYSSEUS_SYNTHETIC", raising=False)

    with pytest.raises(OpenHandsFailure):
        _run_native_turn(_CreateTransport(create_status=401))

    finished = list(memory_spans.get_finished_spans())
    _assert_no_secrets(finished)
    created = next(span for span in finished if span.name == "openhands.create")
    assert created.attributes["http.status_code"] == 401
    assert created.status.status_code == StatusCode.ERROR
    assert any(
        event.name == "exception"
        and str(event.attributes.get("exception.type", "")).endswith(
            "OpenHandsFailure"
        )
        for event in created.events
    )
    invoked = next(span for span in finished if span.name == "invoke_agent Native")
    assert invoked.status.status_code == StatusCode.ERROR

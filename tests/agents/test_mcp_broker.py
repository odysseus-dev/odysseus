import importlib.util
import logging
import sys
from pathlib import Path

import pytest


_BROKER_PATH = Path(__file__).resolve().parents[2] / "services" / "agents" / "mcp_broker.py"
_SPEC = importlib.util.spec_from_file_location("_mcp_broker_under_test", _BROKER_PATH)
assert _SPEC and _SPEC.loader
mcp_broker = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mcp_broker
_SPEC.loader.exec_module(mcp_broker)
McpBroker = mcp_broker.McpBroker
McpRequest = mcp_broker.McpRequest
McpResponse = mcp_broker.McpResponse
WorkloadContext = mcp_broker.WorkloadContext


class MemoryResolver:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}

    def set(self, execution_id: str, workload_id: str, token: str) -> None:
        self._tokens[(execution_id, workload_id)] = token

    def current_token(self, *, execution_id: str, workload_id: str) -> str:
        return self._tokens[(execution_id, workload_id)]


class RecordingTransport:
    def __init__(self, accepted: str) -> None:
        self.accepted = accepted
        self.calls: list[tuple[str, str]] = []

    def send(self, request: McpRequest, token: str) -> McpResponse:
        self.calls.append((request.tool, token))
        if token != self.accepted:
            return McpResponse(status=401, body={"error": "invalid_token"})
        return McpResponse(status=200, body={"ok": True})


class MemoryLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


@pytest.fixture
def surfaces():
    return {
        "agent_environment": {},
        "events": [],
        "profile_snapshots": [],
    }


def test_forward_attaches_current_token_and_rejects_old(surfaces):
    resolver = MemoryResolver()
    transport = RecordingTransport(accepted="T1")
    broker = McpBroker(resolver, transport, surfaces=surfaces)
    context = WorkloadContext(execution_id="exec-1", workload_id="wl-1")
    request = McpRequest(method="tools/call", tool="notes.read")

    resolver.set("exec-1", "wl-1", "T1")
    first = broker.forward(request, context)
    assert first.status == 200

    resolver.set("exec-1", "wl-1", "T2")
    transport.accepted = "T2"
    second = broker.forward(request, context)
    assert second.status == 200
    assert [token for _, token in transport.calls] == ["T1", "T2"]
    assert transport.send(request, "T1").status == 401


def test_token_never_appears_in_agent_env_logs_events_or_profiles(surfaces):
    resolver = MemoryResolver()
    resolver.set("exec-1", "wl-1", "T2-secret")
    transport = RecordingTransport(accepted="T2-secret")
    handler = MemoryLogHandler()
    logger = logging.getLogger("services.agents.mcp_broker.test")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    broker = McpBroker(resolver, transport, surfaces=surfaces, logger=logger)

    broker.forward(
        McpRequest(method="tools/call", tool="notes.read"),
        WorkloadContext(execution_id="exec-1", workload_id="wl-1", profile="opencode"),
    )

    env_blob = str(surfaces["agent_environment"])
    event_blob = str(surfaces["events"])
    profile_blob = str(surfaces["profile_snapshots"])
    log_blob = "\n".join(handler.records)
    assert "T2-secret" not in env_blob
    assert "T2-secret" not in event_blob
    assert "T2-secret" not in profile_blob
    assert "T2-secret" not in log_blob
    assert "ODYSSEUS_DELEGATION" not in surfaces["agent_environment"]

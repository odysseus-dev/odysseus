import importlib.util
import sys
from pathlib import Path


_PROXY_PATH = Path(__file__).resolve().parents[2] / "services" / "agents" / "acp_mcp_proxy.py"
_SPEC = importlib.util.spec_from_file_location("_acp_mcp_proxy_under_test", _PROXY_PATH)
assert _SPEC and _SPEC.loader
proxy_mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = proxy_mod
_SPEC.loader.exec_module(proxy_mod)
AcpMcpProxy = proxy_mod.AcpMcpProxy
AcpSession = proxy_mod.AcpSession


_BROKER_PATH = Path(__file__).resolve().parents[2] / "services" / "agents" / "mcp_broker.py"
_BROKER_SPEC = importlib.util.spec_from_file_location("_mcp_broker_for_proxy", _BROKER_PATH)
assert _BROKER_SPEC and _BROKER_SPEC.loader
broker_mod = importlib.util.module_from_spec(_BROKER_SPEC)
sys.modules[_BROKER_SPEC.name] = broker_mod
_BROKER_SPEC.loader.exec_module(broker_mod)
McpBroker = broker_mod.McpBroker
McpRequest = broker_mod.McpRequest
McpResponse = broker_mod.McpResponse
WorkloadContext = broker_mod.WorkloadContext


class MemoryResolver:
    def __init__(self, token: str = "T-current") -> None:
        self.token = token

    def current_token(self, *, execution_id: str, workload_id: str) -> str:
        return self.token


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send(self, request: McpRequest, token: str) -> McpResponse:
        self.calls.append((request.tool, token))
        if request.tool == "notes.read":
            return McpResponse(status=200, body={"allowed": True})
        return McpResponse(status=403, body={"allowed": False})


def test_proxy_attaches_broker_and_enforces_scope():
    transport = RecordingTransport()
    broker = McpBroker(MemoryResolver(), transport)
    proxy = AcpMcpProxy(broker, allowed_scopes={"notes.read"})
    session = proxy.connect(
        AcpSession(profile="opencode", execution_id="e1", workload_id="w1"),
        mcp_config={"url": "stdio://agent"},
    )
    allowed = proxy.call(session, "notes.read")
    denied = proxy.call(session, "mail.send")
    assert allowed.allowed is True
    assert denied.allowed is False
    assert transport.calls == [("notes.read", "T-current")]


def test_proxy_reconnect_and_cancel_keep_broker_context():
    transport = RecordingTransport()
    broker = McpBroker(MemoryResolver(), transport)
    proxy = AcpMcpProxy(broker, allowed_scopes={"notes.read"})
    session = proxy.connect(
        AcpSession(profile="hermes", execution_id="e2", workload_id="w2"),
        mcp_config={"url": "stdio://agent"},
    )
    proxy.disconnect(session)
    resumed = proxy.reconnect(session)
    assert resumed.profile == "hermes"
    assert proxy.call(resumed, "notes.read").allowed is True
    proxy.cancel(resumed)
    cancelled = proxy.call(resumed, "notes.read")
    assert cancelled.allowed is False


def test_proxy_does_not_persist_or_log_token():
    transport = RecordingTransport()
    surfaces = {"agent_environment": {}, "events": [], "profile_snapshots": []}
    broker = McpBroker(MemoryResolver("secret-token"), transport, surfaces=surfaces)
    proxy = AcpMcpProxy(broker, allowed_scopes={"notes.read"}, surfaces=surfaces)
    session = proxy.connect(
        AcpSession(profile="opencode", execution_id="e3", workload_id="w3"),
        mcp_config={"headers": {"Authorization": "should-not-store"}},
    )
    proxy.call(session, "notes.read")
    blob = str(surfaces) + str(proxy.snapshot(session))
    assert "secret-token" not in blob
    assert "should-not-store" not in blob
    assert session.mcp_url == "local-broker"

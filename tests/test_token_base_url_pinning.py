"""POST /api/v1/chat must connect only to the address its SSRF check approved.

A chat-scope API token may supply its own ``base_url``. The URL is checked
once, by resolving its hostname; if anything afterwards connects *by hostname*
(the context-window probe to ``<base>/models``, the chat POST itself), DNS is
resolved again and a rebinding resolver can answer with a private address the
check never saw. These tests let the hostname "resolve" to a public IP for the
check and record the host every TCP connect is opened to: it must always be
the vetted IP. A resumed session's stored URL must be re-checked the same way,
while server-chosen endpoints (known providers, admin-configured
ModelEndpoints, which may be on the LAN) keep working.
"""

import ipaddress
import socket
import types

import httpcore
import pytest

PUBLIC_IP = "93.184.216.34"
DNS = {
    "llm.example.test": PUBLIC_IP,
    "rebind.example.test": "127.0.0.1",
}


class _Session:
    def __init__(self, endpoint_url, model, owner="alice"):
        self.endpoint_url = endpoint_url
        self.model = model
        self.owner = owner
        self.headers = {"Authorization": "Bearer stored-key"}
        self.history = []

    def add_message(self, message):
        self.history.append(message)


class _SessionManager:
    def __init__(self, sessions=None):
        self.sessions = dict(sessions or {})

    def get_session(self, session_id):
        return self.sessions[session_id]

    def create_session(self, *, session_id, name, endpoint_url, model, owner):
        sess = _Session(endpoint_url, model, owner)
        self.sessions[session_id] = sess
        return sess

    def save_sessions(self):
        pass


class _WebhookManager:
    def fire_and_forget(self, event, payload):
        pass


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)

    def close(self):
        pass


@pytest.fixture
def connects(monkeypatch):
    """Hosts every TCP connect is opened to; each connect then fails."""
    from src import llm_core, model_context, url_security

    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    def _resolve(host):
        if host not in DNS:
            raise socket.gaierror(f"unknown test host {host}")
        return [ipaddress.ip_address(DNS[host])]

    monkeypatch.setattr(url_security, "_resolve_hostname_ips", _resolve)

    hosts = []

    async def _async_connect(self, host, port, *args, **kwargs):
        hosts.append(host)
        raise httpcore.ConnectError("network disabled in tests")

    def _sync_connect(self, host, port, *args, **kwargs):
        hosts.append(host)
        raise httpcore.ConnectError("network disabled in tests")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", _async_connect)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", _sync_connect)

    monkeypatch.setattr(llm_core.LLMConfig, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_core, "_dead_hosts", {})
    monkeypatch.setattr(llm_core, "_host_fails", {})
    monkeypatch.setattr(llm_core, "_http_client", None)
    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(model_context, "_context_cache", {})
    monkeypatch.setattr(model_context, "_catalog_ctx_cache", {})
    return hosts


def _client(monkeypatch, session_manager, endpoint_rows=()):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.webhook import webhook_routes

    monkeypatch.setattr(webhook_routes, "SessionLocal", lambda: _DB(list(endpoint_rows)))

    app = FastAPI()

    @app.middleware("http")
    async def _chat_scope_token(request, call_next):
        # What AuthMiddleware sets for a valid chat-scope bearer token.
        request.state.api_token = True
        request.state.api_token_scopes = ["chat"]
        request.state.api_token_owner = "alice"
        return await call_next(request)

    # The module-level router accumulates one route per setup call, so mount
    # the endpoint just registered rather than the whole router.
    router = webhook_routes.setup_webhook_routes(
        _WebhookManager(), auth_manager=None, session_manager=session_manager,
    )
    sync_chat = [r for r in router.routes if r.path == "/api/v1/chat"][-1]
    app.add_api_route("/api/v1/chat", sync_chat.endpoint, methods=["POST"])
    return TestClient(app)


def test_token_base_url_connects_only_to_the_validated_ip(monkeypatch, connects):
    client = _client(monkeypatch, _SessionManager())

    r = client.post("/api/v1/chat", json={
        "message": "hello",
        "api_key": "token-supplied-key",
        "base_url": "https://llm.example.test/v1",
        "model": "pin-test-unlisted-model",
    })

    assert r.status_code == 503, r.text
    assert connects, "the chat request never tried to connect"
    assert set(connects) == {PUBLIC_IP}, connects


def test_resumed_session_whose_url_now_resolves_private_is_rejected(monkeypatch, connects):
    sess = _Session("https://rebind.example.test/v1/chat/completions", "pin-test-unlisted-model")
    client = _client(monkeypatch, _SessionManager({"s1": sess}))

    r = client.post("/api/v1/chat", json={"message": "hello", "session": "s1"})

    assert r.status_code == 400, r.text
    assert connects == []
    assert sess.history == []


@pytest.mark.parametrize("endpoint_url, rows, expected_host", [
    # Admin-configured LAN endpoint the owner can see (Case 3 session).
    (
        "http://10.0.0.7:8000/v1/chat/completions",
        [types.SimpleNamespace(base_url="http://10.0.0.7:8000/v1", owner="alice")],
        "10.0.0.7",
    ),
    # Auto-resolved known provider.
    ("https://api.deepseek.com/v1/chat/completions", [], "api.deepseek.com"),
])
def test_resumed_server_chosen_endpoint_is_not_blocked(
    monkeypatch, connects, endpoint_url, rows, expected_host,
):
    sess = _Session(endpoint_url, "pin-test-unlisted-model")
    client = _client(monkeypatch, _SessionManager({"s1": sess}), rows)

    r = client.post("/api/v1/chat", json={"message": "hello", "session": "s1"})

    assert r.status_code == 503, r.text
    assert expected_host in connects

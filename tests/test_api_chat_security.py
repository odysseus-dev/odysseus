import ipaddress
import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1",
    "http://localhost:8000/v1",
    "http://10.0.0.5/v1",
    "http://172.16.0.1/v1",
    "http://192.168.1.2/v1",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/",
    "http://[::1]:8000/v1",
    "http://[fc00::1]/v1",
    "http://224.0.0.1/v1",
    "http://0.0.0.0/v1",
    "file:///etc/passwd",
])
def test_public_url_validator_blocks_internal_targets(url):
    from src.url_security import is_public_http_url

    assert is_public_http_url(url) is False


def test_public_url_validator_allows_public_endpoint(monkeypatch):
    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )

    assert url_security.validate_public_http_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_public_url_validator_blocks_dns_to_private(monkeypatch):
    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("10.0.0.5")],
    )

    with pytest.raises(ValueError):
        url_security.validate_public_http_url("https://api.example.com/v1")


def _load_webhook_routes_for_test(monkeypatch):
    # Load under a unique module name so each test gets a fresh module object
    # rather than a cached one from a previous monkeypatch run.
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = []
    core_db = types.ModuleType("core.database")
    core_db.SessionLocal = object
    core_db.Webhook = object
    core_db.ModelEndpoint = object
    core_middleware = types.ModuleType("core.middleware")
    core_middleware.require_admin = lambda request: None
    webhook_manager = types.ModuleType("src.webhook_manager")
    webhook_manager.WebhookManager = object
    webhook_manager.validate_webhook_url = lambda url: url
    webhook_manager.validate_events = lambda events: events

    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.database", core_db)
    monkeypatch.setitem(sys.modules, "core.middleware", core_middleware)
    monkeypatch.setitem(sys.modules, "src.webhook_manager", webhook_manager)

    module_name = "routes.webhook_routes_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parent.parent / "routes" / "webhook_routes.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Expr:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, row):
        return self.fn(row)

    def __or__(self, other):
        return _Expr(lambda row: self(row) or other(row))


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return _Expr(lambda row: getattr(row, self.name) == other)

    def desc(self):
        return ("desc", self.name)


class _ModelEndpoint:
    is_enabled = _Column("is_enabled")
    owner = _Column("owner")
    created_at = _Column("created_at")


class _Endpoint:
    def __init__(
        self,
        *,
        owner,
        is_enabled=True,
        created_at=1,
        base_url="https://api.example.com/v1",
        api_key=None,
    ):
        self.owner = owner
        self.is_enabled = is_enabled
        self.created_at = created_at
        self.base_url = base_url
        self.api_key = api_key


class _EndpointQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.orders = []

    def filter(self, *exprs):
        self.filters.extend(exprs)
        return self

    def order_by(self, *exprs):
        self.orders.extend(exprs)
        return self

    def first(self):
        rows = self.rows
        for expr in self.filters:
            rows = [row for row in rows if expr(row)]
        # Apply sort keys right-to-left so the leftmost key ends up as the
        # primary sort (stable-sort reversal idiom mirrors SQLAlchemy's
        # multi-column ORDER BY behaviour).
        for order in reversed(self.orders):
            reverse = False
            name = getattr(order, "name", None)
            if isinstance(order, tuple) and order[0] == "desc":
                reverse = True
                name = order[1]
            rows = sorted(rows, key=lambda row: getattr(row, name) is not None, reverse=reverse)
            if name != "owner":
                rows = sorted(rows, key=lambda row: getattr(row, name), reverse=reverse)
        return rows[0] if rows else None


class _DB:
    def __init__(self, rows):
        self.query_obj = _EndpointQuery(rows)
        self.closed = False

    def query(self, model):
        assert model is _ModelEndpoint
        return self.query_obj

    def close(self):
        self.closed = True


class _ChatSession:
    def __init__(self, endpoint_url, model):
        self.endpoint_url = endpoint_url
        self.model = model
        self.headers = {}
        self.history = []

    def add_message(self, message):
        self.history.append(message)


class _SessionManager:
    def __init__(self):
        self.created = []
        self.save_calls = 0

    def create_session(self, *, session_id, name, endpoint_url, model, owner):
        session = _ChatSession(endpoint_url, model)
        self.created.append({
            "session_id": session_id,
            "name": name,
            "endpoint_url": endpoint_url,
            "model": model,
            "owner": owner,
            "session": session,
        })
        return session

    def save_sessions(self):
        self.save_calls += 1


class _Request:
    def __init__(self, *, owner="alice"):
        self.state = types.SimpleNamespace(
            api_token=True,
            api_token_scopes=["chat"],
            api_token_owner=owner,
        )


class _WebhookManager:
    async def fire(self, event, payload):
        return None

    def fire_and_forget(self, event, payload):
        return None


def _install_sync_chat_stubs(monkeypatch):
    # FastAPI checks for python_multipart at import time when Form is used;
    # stub it so the optional dependency is not required in the test environment.
    python_multipart = types.ModuleType("python_multipart")
    python_multipart.__version__ = "0.0.13"
    core_models = types.ModuleType("core.models")

    class _ChatMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    async def _llm_call_async(endpoint_url, model, messages, headers=None, timeout=None):
        return "mocked response"

    endpoint_resolver = types.ModuleType("src.endpoint_resolver")
    endpoint_resolver.normalize_base = lambda url: (url or "").strip().rstrip("/")
    endpoint_resolver.build_chat_url = lambda base_url: f"{base_url}/chat/completions"
    endpoint_resolver.build_models_url = lambda base_url: f"{base_url}/models"
    endpoint_resolver.build_headers = lambda api_key, base_url: {"Authorization": f"Bearer {api_key}"}

    llm_core = types.ModuleType("src.llm_core")
    llm_core.llm_call_async = _llm_call_async
    core_models.ChatMessage = _ChatMessage

    monkeypatch.setitem(sys.modules, "python_multipart", python_multipart)
    monkeypatch.setitem(sys.modules, "core.models", core_models)
    monkeypatch.setitem(sys.modules, "src.llm_core", llm_core)
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", endpoint_resolver)


def _sync_chat_endpoint(webhook_routes, session_manager):
    router = webhook_routes.setup_webhook_routes(
        _WebhookManager(),
        auth_manager=None,
        session_manager=session_manager,
    )
    for route in router.routes:
        if route.path == "/api/v1/chat":
            return route.endpoint
    raise AssertionError("sync chat route not found")


@pytest.mark.parametrize("base_url", [
    "http://127.0.0.1:11434/v1",
    "http://localhost:11434/v1",
    "http://10.0.0.5/v1",
    "http://169.254.169.254/latest/meta-data/",
])
@pytest.mark.asyncio
async def test_api_chat_direct_base_url_rejects_local_private_targets(monkeypatch, base_url):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)

    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url=base_url,
        model="test-model",
        provider=None,
        session=None,
    )

    with pytest.raises(webhook_routes.HTTPException) as exc:
        await sync_chat(_Request(), body)

    assert exc.value.status_code == 400
    assert exc.value.detail == "base_url must point to a public HTTP(S) endpoint"
    assert session_manager.created == []


@pytest.mark.asyncio
async def test_api_chat_direct_base_url_allows_mocked_public_endpoint(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)

    from src import url_security

    monkeypatch.setattr(
        url_security,
        "_resolve_hostname_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(), body)

    assert response["response"] == "mocked response"
    assert response["model"] == "test-model"
    assert session_manager.created[0]["endpoint_url"] == "https://api.example.com/v1/chat/completions"


def test_api_chat_fallback_endpoint_selection_for_owned_token(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    rows = [
        _Endpoint(owner="alice", is_enabled=False, created_at=0),
        _Endpoint(owner="bob", created_at=0),
        _Endpoint(owner=None, created_at=1),
        _Endpoint(owner="alice", created_at=2),
    ]

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)

    selected = webhook_routes._select_api_chat_fallback_endpoint(_DB(rows), "alice")

    assert selected.owner == "alice"
    assert selected.is_enabled is True
    assert selected.created_at == 2


def test_api_chat_fallback_without_owner_uses_shared_only(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    rows = [
        _Endpoint(owner="alice", created_at=0),
        _Endpoint(owner=None, is_enabled=False, created_at=1),
        _Endpoint(owner=None, created_at=2),
    ]

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)

    selected = webhook_routes._select_api_chat_fallback_endpoint(_DB(rows), None)

    assert selected.owner is None
    assert selected.is_enabled is True
    assert selected.created_at == 2


@pytest.mark.asyncio
async def test_api_chat_fallback_trusts_configured_local_endpoint(monkeypatch):
    webhook_routes = _load_webhook_routes_for_test(monkeypatch)
    _install_sync_chat_stubs(monkeypatch)
    local_endpoint = _Endpoint(
        owner=None,
        base_url="http://localhost:11434/v1",
        api_key="configured-key",
    )
    db = _DB([local_endpoint])
    calls = []

    def _session_local():
        return db

    def _validate_public_http_url(url, *, max_length=2048):
        calls.append(url)
        raise AssertionError("configured fallback endpoint should not be publicly validated")

    monkeypatch.setattr(webhook_routes, "ModelEndpoint", _ModelEndpoint)
    monkeypatch.setattr(webhook_routes, "SessionLocal", _session_local)
    monkeypatch.setattr(webhook_routes, "validate_public_http_url", _validate_public_http_url)

    session_manager = _SessionManager()
    sync_chat = _sync_chat_endpoint(webhook_routes, session_manager)
    body = types.SimpleNamespace(
        message="hello",
        model="local-model",
        api_key=None,
        base_url=None,
        provider=None,
        session=None,
    )

    response = await sync_chat(_Request(owner=None), body)

    assert response["response"] == "mocked response"
    assert response["model"] == "local-model"
    assert session_manager.created[0]["endpoint_url"] == "http://localhost:11434/v1/chat/completions"
    assert calls == []

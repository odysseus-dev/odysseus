import json
import pytest
from unittest.mock import MagicMock, patch
from starlette.requests import Request
from starlette.datastructures import Headers

from routes.editor_agent_routes import setup_editor_agent_routes
from routes.gallery.gallery_routes import _visible_image_endpoint_for_base


def create_fake_request(
    user: str | None = None,
    is_admin: bool = False,
    api_token: bool = False,
    api_token_scopes: list | None = None,
    api_token_owner: str | None = None,
    auth_disabled: bool = False,
    client_host: str = "127.0.0.1",
):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/editor/image-models",
        "headers": [],
        "app": MagicMock(),
        "client": (client_host, 12345),
    }
    req = Request(scope)
    # Mock auth_manager on app state
    auth_mgr = MagicMock()
    auth_mgr.is_admin.side_effect = lambda u: is_admin if (u == user or u == "adminuser") else False
    auth_mgr.is_configured = not auth_disabled
    req.app.state.auth_manager = auth_mgr

    if user is not None:
        req.state.current_user = user
    if api_token:
        req.state.api_token = True
        req.state.api_token_owner = api_token_owner or (user if user != "api" else None)
        req.state.api_token_scopes = api_token_scopes or []
    else:
        req.state.api_token = False
    return req


class FakeEndpoint:
    def __init__(self, id, name, base_url, model_type="image", is_enabled=True, owner=None, cached_models=None):
        self.id = id
        self.name = name
        self.base_url = base_url
        self.model_type = model_type
        self.is_enabled = is_enabled
        self.owner = owner
        self.cached_models = cached_models or json.dumps(["qwen-image-edit", "qwen-generate"])
        self.pinned_models = None


@pytest.mark.asyncio
async def test_editor_image_models_non_admin_redacts_urls():
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    fake_ep = FakeEndpoint(
        id=42,
        name="private-gpu-cluster",
        base_url="http://10.0.0.50:8000/v1",
        owner="testuser",
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    req = create_fake_request(user="testuser", is_admin=False)

    with patch("routes.editor_agent_routes._user", return_value="testuser"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=False), \
         patch("core.database.SessionLocal", return_value=fake_db), \
         patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q), \
         patch("src.endpoint_resolver._endpoint_enabled_models", return_value=["qwen-image-edit"]):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert "models" in data
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["id"] == "qwen-image-edit"
    assert m["endpoint_id"] == 42
    # Non-admin must NOT see internal URL!
    assert "base_url" not in m
    assert "10.0.0.50" not in json.dumps(data)


@pytest.mark.asyncio
async def test_editor_image_models_admin_includes_urls():
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    fake_ep = FakeEndpoint(
        id=42,
        name="private-gpu-cluster",
        base_url="http://10.0.0.50:8000/v1",
        owner="adminuser",
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    req = create_fake_request(user="adminuser", is_admin=True)

    with patch("routes.editor_agent_routes._user", return_value="adminuser"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=True), \
         patch("core.database.SessionLocal", return_value=fake_db), \
         patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q), \
         patch("src.endpoint_resolver._endpoint_enabled_models", return_value=["qwen-image-edit"]):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["endpoint_id"] == 42
    # Admin is allowed to see the endpoint base URL
    assert m.get("base_url") == "http://10.0.0.50:8000/v1"


@pytest.mark.asyncio
async def test_editor_agent_models_non_admin_redacts_urls_and_probe():
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/agent-models")

    raw_models = [
        {"id": "Qwen/Qwen3.5-9B", "label": "Qwen3.5-9B · local", "source": "local", "vision": True, "base_url": "http://100.69.120.65:8097/v1"},
        {"id": "hosted-default", "label": "hosted-default", "source": "hosted", "vision": None, "base_url": "https://api.hosted.ai/v1"},
    ]

    req = create_fake_request(user="regularuser", is_admin=False)

    with patch("routes.editor_agent_routes._user", return_value="regularuser"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=False), \
         patch("src.editor_agent_model.list_editor_agent_models", return_value=raw_models):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert data.get("probe") == {}
    assert len(data["models"]) == 2
    for m in data["models"]:
        assert "base_url" not in m
    assert "100.69.120.65" not in json.dumps(data)


def test_visible_image_endpoint_for_base_supports_id_and_url():
    fake_ep = FakeEndpoint(
        id=7,
        name="my-diffusion",
        base_url="http://internal-host:8080/v1",
        owner="alice",
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    with patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q):
        # 1. Lookup by numeric string ID
        res_id = _visible_image_endpoint_for_base(fake_db, "7", "alice", "qwen-image-edit")
        assert res_id == fake_ep

        # 2. Lookup by URL
        res_url = _visible_image_endpoint_for_base(fake_db, "http://internal-host:8080", "alice", "qwen-image-edit")
        assert res_url == fake_ep

        # 3. Lookup non-existent
        res_none = _visible_image_endpoint_for_base(fake_db, "999", "alice", "qwen-image-edit")
        assert res_none is None


@pytest.mark.asyncio
async def test_editor_image_models_admin_delegated_bearer_token_redacts_urls():
    """Administrator-owned delegated bearer tokens must NOT inherit administrator metadata access."""
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    fake_ep = FakeEndpoint(
        id=42,
        name="private-gpu-cluster",
        base_url="http://10.0.0.50:8000/v1",
        owner="adminuser",
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    # Delegated bearer token minted by adminuser with chat scope
    req = create_fake_request(
        user="api",
        is_admin=False,
        api_token=True,
        api_token_owner="adminuser",
        api_token_scopes=["chat"],
    )

    with patch("core.database.SessionLocal", return_value=fake_db), \
         patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q), \
         patch("src.endpoint_resolver._endpoint_enabled_models", return_value=["qwen-image-edit"]):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["id"] == "qwen-image-edit"
    # Even though owner is adminuser, delegated bearer token must NOT see base_url!
    assert "base_url" not in m
    assert "10.0.0.50" not in json.dumps(data)


@pytest.mark.asyncio
async def test_editor_image_models_bearer_token_missing_chat_scope_rejected():
    """Bearer token missing required 'chat' scope must fail closed with 403."""
    from fastapi import HTTPException
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    req = create_fake_request(
        user="api",
        is_admin=False,
        api_token=True,
        api_token_owner="adminuser",
        api_token_scopes=["documents:read"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint(req)
    assert exc_info.value.status_code == 403
    assert "API token missing required scope: chat" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_editor_image_models_bearer_token_valid_chat_scope_success():
    """Bearer token with required 'chat' scope succeeds with safe endpoint discovery."""
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    fake_ep = FakeEndpoint(
        id=10,
        name="bob-server",
        base_url="http://192.168.1.100:8000/v1",
        owner="bob",
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    req = create_fake_request(
        user="api",
        is_admin=False,
        api_token=True,
        api_token_owner="bob",
        api_token_scopes=["chat"],
    )

    with patch("core.database.SessionLocal", return_value=fake_db), \
         patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q), \
         patch("src.endpoint_resolver._endpoint_enabled_models", return_value=["qwen-image-edit"]):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["id"] == "qwen-image-edit"
    assert "base_url" not in m


@pytest.mark.asyncio
async def test_editor_image_models_unauthenticated_session_rejected():
    """Unauthenticated request in configured deployment must raise 401."""
    from fastapi import HTTPException
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    req = create_fake_request(
        user=None,
        is_admin=False,
        client_host="203.0.113.5",  # non-loopback
    )

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint(req)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_editor_image_models_auth_disabled_mode_includes_urls():
    """Explicitly auth-disabled single-user mode allows operator full access to endpoint URLs."""
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    fake_ep = FakeEndpoint(
        id=99,
        name="local-qwen",
        base_url="http://127.0.0.1:8000/v1",
        owner=None,
    )

    fake_db = MagicMock()
    fake_query = MagicMock()
    fake_db.query.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = [fake_ep]

    req = create_fake_request(
        user=None,
        is_admin=False,
        auth_disabled=True,
    )

    with patch("src.auth_helpers._auth_disabled", return_value=True), \
         patch("core.database.SessionLocal", return_value=fake_db), \
         patch("src.auth_helpers.owner_filter", side_effect=lambda q, *a, **k: q), \
         patch("src.endpoint_resolver._endpoint_enabled_models", return_value=["qwen-image-edit"]):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert len(data["models"]) == 1
    m = data["models"][0]
    assert m["base_url"] == "http://127.0.0.1:8000/v1"


@pytest.mark.asyncio
async def test_editor_image_models_bearer_token_missing_owner_rejected():
    """Malformed bearer token without an owner principal must fail closed with 403."""
    from fastapi import HTTPException
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/image-models")

    req = create_fake_request(
        user="api",
        is_admin=False,
        api_token=True,
        api_token_owner=None,
        api_token_scopes=["chat"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint(req)
    assert exc_info.value.status_code == 403
    assert "API token has no owner" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_editor_agent_models_admin_includes_urls_and_probe():
    """Admin browser session sees agent model base_urls and probe status."""
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/agent-models")

    raw_models = [
        {"id": "Qwen/Qwen3.5-9B", "label": "Qwen3.5-9B · local", "source": "local", "vision": True, "base_url": "http://100.69.120.65:8097/v1"},
        {"id": "hosted-default", "label": "hosted-default", "source": "hosted", "vision": None, "base_url": "https://api.hosted.ai/v1"},
    ]

    req = create_fake_request(user="adminuser", is_admin=True)

    with patch("src.editor_agent_model.list_editor_agent_models", return_value=raw_models), \
         patch("src.editor_agent_model.probe_status", return_value={"status": "online", "model": "Qwen3.5-9B"}):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert data.get("probe") == {"status": "online", "model": "Qwen3.5-9B"}
    assert len(data["models"]) == 2
    assert data["models"][0].get("base_url") == "http://100.69.120.65:8097/v1"


@pytest.mark.asyncio
async def test_editor_agent_models_admin_delegated_bearer_token_redacts_urls_and_probe():
    """Administrator-owned delegated bearer token must NOT see agent model base_urls or probe status."""
    router = setup_editor_agent_routes()
    route = next(r for r in router.routes if r.path == "/api/editor/agent-models")

    raw_models = [
        {"id": "Qwen/Qwen3.5-9B", "label": "Qwen3.5-9B · local", "source": "local", "vision": True, "base_url": "http://100.69.120.65:8097/v1"},
    ]

    req = create_fake_request(
        user="api",
        is_admin=False,
        api_token=True,
        api_token_owner="adminuser",
        api_token_scopes=["chat"],
    )

    with patch("src.editor_agent_model.list_editor_agent_models", return_value=raw_models):
        resp = await route.endpoint(req)

    data = json.loads(resp.body.decode())
    assert data.get("probe") == {}
    assert len(data["models"]) == 1
    assert "base_url" not in data["models"][0]

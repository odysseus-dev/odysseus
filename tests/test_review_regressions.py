"""Regression tests for issues found during code review."""

import importlib
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.preset_manager import PresetManager


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _FakeModelEndpoint:
    id = _FakeColumn("id")
    is_enabled = _FakeColumn("is_enabled")
    owner = _FakeColumn("owner")


class _FakeDbSession:
    id = _FakeColumn("id")
    endpoint_url = _FakeColumn("endpoint_url")


class _FakeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                _, field, value = condition
                self.rows = [row for row in self.rows if getattr(row, field) == value]
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _FakeQuery(self.rows)

    def close(self):
        pass


def _default_chat_endpoint():
    from routes.model_routes import setup_model_routes

    router = setup_model_routes(model_discovery=None)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/default-chat":
            return route.endpoint
    raise AssertionError("/api/default-chat route not found")


def _install_model_route_import_stubs(monkeypatch):
    core_mod = types.ModuleType("core")
    core_mod.__path__ = []
    db_mod = types.ModuleType("core.database")
    db_mod.SessionLocal = lambda: _FakeDb([])
    db_mod.ModelEndpoint = _FakeModelEndpoint
    db_mod.Session = _FakeDbSession
    db_mod.Document = MagicMock()
    db_mod.DocumentVersion = MagicMock()
    db_mod.GalleryImage = MagicMock()
    middleware_mod = types.ModuleType("core.middleware")
    middleware_mod.require_admin = lambda request: None
    multipart_mod = types.ModuleType("python_multipart")
    multipart_mod.__version__ = "0.0.13"
    models_mod = types.ModuleType("core.models")
    models_mod.ChatMessage = MagicMock()
    exceptions_mod = types.ModuleType("core.exceptions")
    exceptions_mod.SessionNotFoundError = type("SessionNotFoundError", (Exception,), {})
    session_mgr_mod = types.ModuleType("core.session_manager")
    session_mgr_mod.SessionManager = MagicMock()

    monkeypatch.delitem(sys.modules, "routes.model_routes", raising=False)
    monkeypatch.delitem(sys.modules, "routes.chat_routes", raising=False)
    monkeypatch.delitem(sys.modules, "routes.session_routes", raising=False)
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    monkeypatch.setitem(sys.modules, "core.middleware", middleware_mod)
    monkeypatch.setitem(sys.modules, "python_multipart", multipart_mod)
    monkeypatch.setitem(sys.modules, "core.models", models_mod)
    monkeypatch.setitem(sys.modules, "core.exceptions", exceptions_mod)
    monkeypatch.setitem(sys.modules, "core.session_manager", session_mgr_mod)


def _install_core_auth_stub(monkeypatch):
    """Install the narrow auth surface needed by tool-policy tests."""
    core_mod = types.ModuleType("core")
    core_mod.__path__ = []
    auth_mod = types.ModuleType("core.auth")
    auth_mod.AuthManager = MagicMock()
    core_mod.auth = auth_mod
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.auth", auth_mod)
    return auth_mod


def _install_core_middleware_stub(monkeypatch):
    """Install the narrow middleware surface needed by loopback tool tests."""
    core_mod = types.ModuleType("core")
    core_mod.__path__ = []
    middleware_mod = types.ModuleType("core.middleware")
    middleware_mod.INTERNAL_TOOL_HEADER = "X-Internal-Tool"
    middleware_mod.INTERNAL_TOOL_TOKEN = "test-token"
    core_mod.middleware = middleware_mod
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.middleware", middleware_mod)
    return middleware_mod


def test_providers_requires_admin_before_discovery_and_cache(monkeypatch):
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes

    class _Discovery:
        def __init__(self):
            self.calls = 0

        def get_providers(self):
            self.calls += 1
            return {"providers": [{"host": "internal.example"}]}

    discovery = _Discovery()
    router = model_routes.setup_model_routes(discovery)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "") == "/api/providers"
    )
    request = SimpleNamespace()

    assert endpoint(request, refresh=True) == {"providers": [{"host": "internal.example"}]}
    assert discovery.calls == 1

    def deny_admin(_request):
        raise PermissionError("admin required")

    monkeypatch.setattr(model_routes, "require_admin", deny_admin)

    with pytest.raises(PermissionError):
        endpoint(request, refresh=True)
    with pytest.raises(PermissionError):
        endpoint(request, refresh=False)
    assert discovery.calls == 1


def test_default_chat_does_not_auto_pick_shared_endpoint_for_fresh_user(monkeypatch):
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes

    shared_ep = SimpleNamespace(
        id="shared",
        base_url="http://localhost:11434",
        is_enabled=True,
        owner=None,
        cached_models='["shared-model"]',
    )

    def scoped_owner_filter(query, model_cls, user, *, include_shared=True):
        query.rows = [
            row for row in query.rows
            if row.owner == user or (include_shared and row.owner is None)
        ]
        return query

    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([shared_ep]))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "owner_filter", scoped_owner_filter)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat/completions")
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user: {})

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="fresh"),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_admin=lambda user: False)
        )),
    )

    assert _default_chat_endpoint()(request) == {
        "endpoint_id": "",
        "endpoint_url": "",
        "model": "",
    }


def test_default_chat_uses_owned_endpoint_as_regular_user_last_resort(monkeypatch):
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes

    owned_ep = SimpleNamespace(
        id="owned",
        base_url="http://localhost:11434",
        is_enabled=True,
        owner="fresh",
        cached_models='["owned-model"]',
    )

    def scoped_owner_filter(query, model_cls, user, *, include_shared=True):
        query.rows = [
            row for row in query.rows
            if row.owner == user or (include_shared and row.owner is None)
        ]
        return query

    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([owned_ep]))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "owner_filter", scoped_owner_filter)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat/completions")
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user: {})

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="fresh"),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_admin=lambda user: False)
        )),
    )

    assert _default_chat_endpoint()(request) == {
        "endpoint_id": "owned",
        "endpoint_url": "http://localhost:11434/chat/completions",
        "model": "owned-model",
    }


def test_preset_manager_persists_inject_fields(tmp_path):
    manager = PresetManager(str(tmp_path))

    ok = manager.update_custom(
        temperature=0.7,
        max_tokens=2048,
        system_prompt="Be useful.",
        name="Custom",
        enabled=True,
        inject_prefix="PREFIX",
        inject_suffix="SUFFIX",
    )

    assert ok is True
    assert manager.presets["custom"]["inject_prefix"] == "PREFIX"
    assert manager.presets["custom"]["inject_suffix"] == "SUFFIX"

    reloaded = PresetManager(str(tmp_path))
    assert reloaded.presets["custom"]["inject_prefix"] == "PREFIX"
    assert reloaded.presets["custom"]["inject_suffix"] == "SUFFIX"


def test_preset_manager_default_custom_preset_starts_disabled(tmp_path):
    manager = PresetManager(str(tmp_path))

    custom = manager.presets["custom"]

    assert custom["enabled"] is False
    assert custom["system_prompt"] == ""
    assert custom["temperature"] == 1.0
    assert custom["max_tokens"] == 0


def test_preset_manager_migrates_legacy_default_custom_preset_disabled(tmp_path):
    presets_file = tmp_path / "presets.json"
    presets_file.write_text(
        json.dumps({
            "custom": {
                "name": "Custom",
                "temperature": 0.7,
                "max_tokens": 4096,
                "system_prompt": "You are a helpful, balanced assistant. Match your response style to the user's needs.",
            }
        }),
        encoding="utf-8",
    )

    manager = PresetManager(str(tmp_path))
    custom = manager.presets["custom"]

    assert custom["enabled"] is False
    assert custom["system_prompt"] == ""
    assert custom["temperature"] == 1.0
    assert custom["max_tokens"] == 0


def test_normalize_thinking_handles_lowercase_thinking_process(monkeypatch):
    for mod_name in [
        "starlette.middleware",
        "starlette.middleware.base",
        "core.models",
        "core.database",
        "routes.prefs_routes",
        "routes.research_routes",
        "src.llm_core",
        "src.context_compactor",
        "src.model_context",
        "src.auth_helpers",
    ]:
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, MagicMock())

    chat_helpers = importlib.import_module("routes.chat_helpers")

    text = (
        "Thinking process:\n"
        "Analyze the Request: The user is explicitly instructing me to use the tag.\n\n"
        "hi"
    )

    normalized = chat_helpers._normalize_thinking(text)

    assert normalized == (
        "<think>Analyze the Request: The user is explicitly instructing me to use the tag.</think>\n\n"
        "hi"
    )


@pytest.mark.asyncio
async def test_build_chat_context_incognito_does_not_duplicate_current_user_message(monkeypatch):
    for mod_name in [
        "starlette.middleware",
        "starlette.middleware.base",
        "core.models",
        "core.database",
        "routes.prefs_routes",
        "routes.research_routes",
        "src.llm_core",
        "src.context_compactor",
        "src.model_context",
        "src.auth_helpers",
    ]:
        if mod_name not in sys.modules:
            monkeypatch.setitem(sys.modules, mod_name, MagicMock())

    chat_helpers = importlib.import_module("routes.chat_helpers")

    async def fake_preprocess(chat_handler, message, att_ids, sess, **kwargs):
        # **kwargs absorbs auto_opened_docs (added when PDF imports auto-create
        # docs) and any other future preprocess kwargs without the test fixture
        # having to be updated each time.
        return chat_helpers.PreprocessedMessage(
            enhanced_message=message,
            user_content=message,
            text_for_context=message,
            youtube_transcripts=[],
            attachment_meta=[],
        )

    def fake_extract_preset(chat_handler, preset_id):
        return chat_helpers.PresetInfo(
            temperature=0.7,
            max_tokens=1024,
            system_prompt=None,
            character_name=None,
        )

    def fake_add_user_message(sess, chat_handler, preprocessed, incognito=False):
        sess.messages.append({"role": "user", "content": preprocessed.user_content})

    async def fake_maybe_compact(sess, endpoint_url, model, messages, headers, owner=None):
        return messages, 123, False

    monkeypatch.setattr(chat_helpers, "preprocess", fake_preprocess)
    monkeypatch.setattr(chat_helpers, "extract_preset", fake_extract_preset)
    monkeypatch.setattr(chat_helpers, "add_user_message", fake_add_user_message)
    monkeypatch.setattr(chat_helpers, "load_prefs_for_user", lambda user: {})
    monkeypatch.setattr(chat_helpers, "get_current_user", lambda request: "tester")
    monkeypatch.setattr(chat_helpers, "normalize_model_id", lambda endpoint_url, model: None)
    monkeypatch.setattr(chat_helpers, "maybe_compact", fake_maybe_compact)
    monkeypatch.setattr(chat_helpers, "trim_for_context", lambda messages, context_length: messages)

    sess = SimpleNamespace(
        endpoint_url="http://localhost:8000/v1",
        model="test-model",
        headers={},
        messages=[],
        get_context_messages=lambda: list(sess.messages),
    )
    request = SimpleNamespace()
    chat_handler = SimpleNamespace()
    chat_processor = SimpleNamespace(
        build_context_preface=lambda **kwargs: ([], [], []),
    )

    ctx = await chat_helpers.build_chat_context(
        sess=sess,
        request=request,
        chat_handler=chat_handler,
        chat_processor=chat_processor,
        message="hello",
        session_id="s1",
        incognito=True,
    )

    user_messages = [m for m in ctx.messages if m.get("role") == "user" and m.get("content") == "hello"]
    assert len(user_messages) == 1


@pytest.mark.asyncio
async def test_admin_agent_tools_require_admin(monkeypatch):
    auth_mod = _install_core_auth_stub(monkeypatch)
    from src.tool_execution import execute_tool_block

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: FakeAuth())

    for tool_name in ("manage_tokens", "app_api", "serve_preset"):
        desc, result = await execute_tool_block(
            SimpleNamespace(tool_type=tool_name, content='{"action":"create","name":"bad"}'),
            owner="regular-user",
        )

        assert desc == f"{tool_name}: BLOCKED"
        assert result["exit_code"] == 1
        assert "requires an admin" in result["error"]


@pytest.mark.asyncio
async def test_app_api_blocks_shell_routes_before_loopback(monkeypatch):
    import httpx
    from src.tool_implementations import do_app_api

    class UnexpectedAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("app_api should block shell routes before loopback")

    monkeypatch.setattr(httpx, "AsyncClient", UnexpectedAsyncClient)

    for path in ("/api/shell/exec", "api/shell/stream"):
        result = await do_app_api(
            json.dumps(
                {
                    "action": "call",
                    "method": "POST",
                    "path": path,
                    "body": {"command": "echo should-not-run"},
                }
            ),
            owner="admin",
        )

        assert result["exit_code"] == 1
        assert "Path blocked for safety" in result["error"]
        assert "Sensitive endpoints" in result["error"]


@pytest.mark.asyncio
async def test_app_api_endpoint_discovery_hides_shell_routes(monkeypatch):
    _install_core_middleware_stub(monkeypatch)
    import httpx
    from src.tool_implementations import do_app_api

    class FakeResponse:
        def json(self):
            return {
                "paths": {
                    "/api/shell/exec": {"post": {"summary": "Execute Shell Command"}},
                    "/api/shell/stream": {"post": {"summary": "Stream Shell Command"}},
                    "/api/auth/settings": {"get": {"summary": "Auth Settings"}},
                    "/api/cookbook/gpus": {"get": {"summary": "List GPUs"}},
                }
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await do_app_api(json.dumps({"action": "endpoints"}), owner="admin")

    assert result["exit_code"] == 0
    paths = {(endpoint["method"], endpoint["path"]) for endpoint in result["endpoints"]}
    assert ("GET", "/api/cookbook/gpus") in paths
    assert ("POST", "/api/shell/exec") not in paths
    assert ("POST", "/api/shell/stream") not in paths
    assert ("GET", "/api/auth/settings") not in paths
    assert all(not endpoint["path"].startswith("/api/shell") for endpoint in result["endpoints"])


@pytest.mark.asyncio
async def test_public_agent_policy_blocks_sensitive_tools(monkeypatch):
    auth_mod = _install_core_auth_stub(monkeypatch)
    from src.tool_execution import execute_tool_block

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: FakeAuth())

    for tool_name in ("send_email", "read_file", "mcp__email__send_email"):
        desc, result = await execute_tool_block(
            SimpleNamespace(tool_type=tool_name, content="{}"),
            owner="regular-user",
        )
        assert desc == f"{tool_name}: BLOCKED"
        assert result["exit_code"] == 1
        assert "restricted to admin users" in result["error"]


def test_public_agent_policy_hides_sensitive_tools(monkeypatch):
    auth_mod = _install_core_auth_stub(monkeypatch)
    from src.tool_security import blocked_tools_for_owner

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: FakeAuth())

    blocked = blocked_tools_for_owner("regular-user")

    assert "send_email" in blocked
    assert "read_file" in blocked
    assert "app_api" in blocked
    assert "serve_preset" in blocked
    assert "manage_tasks" in blocked


@pytest.mark.asyncio
async def test_webhook_tool_reuses_private_url_validation():
    class FakeDb:
        def close(self):
            pass

    fake_core_db = types.ModuleType("core.database")
    fake_core_db.SessionLocal = lambda: FakeDb()
    fake_core_db.Webhook = object
    fake_src_db = types.ModuleType("src.database")
    fake_src_db.SessionLocal = fake_core_db.SessionLocal
    fake_src_db.Webhook = object
    # Importing do_manage_webhooks below re-executes src.webhook_manager bound to
    # the faked src.database, whose Webhook is plain `object`. Save BOTH the
    # sys.modules entry AND the parent-package attribute (src.webhook_manager) so
    # the real module can be restored afterwards. Without this the polluted
    # module leaks into the cache and breaks sibling tests that call
    # WebhookManager._deliver (which evaluates `Webhook.id == webhook_id`).
    _ABSENT = object()
    _wm_saved_module = sys.modules.get("src.webhook_manager", _ABSENT)
    _src_pkg = sys.modules.get("src")
    _wm_saved_attr = (
        getattr(_src_pkg, "webhook_manager", _ABSENT) if _src_pkg is not None else _ABSENT
    )

    # Drop both bindings so the import re-executes against the fake src.database,
    # still exercising the intended import path.
    sys.modules.pop("src.webhook_manager", None)
    if _src_pkg is not None and hasattr(_src_pkg, "webhook_manager"):
        delattr(_src_pkg, "webhook_manager")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "core.database", fake_core_db)
    monkeypatch.setitem(sys.modules, "src.database", fake_src_db)

    from src.tool_implementations import do_manage_webhooks

    try:
        result = await do_manage_webhooks(
            '{"action":"add","url":"http://127.0.0.1:8000/hook","events":"chat.completed"}',
            owner="admin",
        )
    finally:
        monkeypatch.undo()
        # Restore src.webhook_manager to its exact pre-test state at BOTH the
        # sys.modules and parent-package attribute level.
        if _wm_saved_module is _ABSENT:
            sys.modules.pop("src.webhook_manager", None)
        else:
            sys.modules["src.webhook_manager"] = _wm_saved_module
        if _src_pkg is not None:
            if _wm_saved_attr is _ABSENT:
                if hasattr(_src_pkg, "webhook_manager"):
                    delattr(_src_pkg, "webhook_manager")
            else:
                setattr(_src_pkg, "webhook_manager", _wm_saved_attr)

    assert result["exit_code"] == 1
    assert "private/internal" in result["error"]


def test_default_chat_skips_hidden_first_model(monkeypatch):
    """get_default_chat picks first visible model when default_model is empty
    and the first cached model is hidden."""
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes

    ep = SimpleNamespace(
        id="ep1",
        base_url="http://localhost:11434",
        is_enabled=True,
        owner="fresh",
        cached_models='["hidden-model", "visible-model"]',
        hidden_models='["hidden-model"]',
    )

    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([ep]))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "owner_filter", lambda q, m, u, **kw: q)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat/completions")
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda user: {})

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="fresh"),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_admin=lambda user: False)
        )),
    )

    result = _default_chat_endpoint()(request)
    assert result["model"] == "visible-model", f"Expected visible-model, got {result['model']!r}"


def test_default_chat_admin_skips_hidden_first_model(monkeypatch):
    """Admin user with global defaults also skips hidden models in fallback."""
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes

    ep = SimpleNamespace(
        id="ep1",
        base_url="http://localhost:11434",
        is_enabled=True,
        owner=None,
        cached_models='["hidden-model", "visible-model"]',
        hidden_models='["hidden-model"]',
    )

    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([ep]))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "owner_filter", lambda q, m, u, **kw: q)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat/completions")

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="admin"),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_admin=lambda user: True)
        )),
    )

    result = _default_chat_endpoint()(request)
    assert result["model"] == "visible-model"


def test_default_chat_all_models_hidden_returns_empty_model(monkeypatch):
    """When all cached models are hidden, get_default_chat returns model: ''."""
    _install_model_route_import_stubs(monkeypatch)
    import routes.model_routes as model_routes

    ep = SimpleNamespace(
        id="ep1",
        base_url="http://localhost:11434",
        is_enabled=True,
        owner=None,
        cached_models='["hidden-a", "hidden-b"]',
        hidden_models='["hidden-a", "hidden-b"]',
    )

    monkeypatch.setattr(model_routes, "ModelEndpoint", _FakeModelEndpoint)
    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDb([ep]))
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "owner_filter", lambda q, m, u, **kw: q)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda base: base.rstrip("/"))
    monkeypatch.setattr(model_routes, "build_chat_url", lambda base: f"{base}/chat/completions")

    request = SimpleNamespace(
        state=SimpleNamespace(current_user="admin"),
        app=SimpleNamespace(state=SimpleNamespace(
            auth_manager=SimpleNamespace(is_admin=lambda user: True)
        )),
    )

    result = _default_chat_endpoint()(request)
    assert result["model"] == "", f"Expected empty model, got {result['model']!r}"


def test_visible_models_filters_hidden_first(monkeypatch):
    """_visible_models removes hidden models from the list."""
    from routes.model_routes import _visible_models

    result = _visible_models(
        '["hidden-model", "visible-model"]',
        '["hidden-model"]',
    )
    assert result == ["visible-model"]


def test_visible_models_all_hidden_returns_empty(monkeypatch):
    """_visible_models returns [] when all models are hidden."""
    from routes.model_routes import _visible_models

    result = _visible_models(
        '["hidden-a", "hidden-b"]',
        '["hidden-a", "hidden-b"]',
    )
    assert result == []


def test_visible_models_no_hidden_returns_all(monkeypatch):
    """_visible_models returns full list when no hidden_models."""
    from routes.model_routes import _visible_models

    result = _visible_models(
        '["model-a", "model-b"]',
        None,
    )
    assert result == ["model-a", "model-b"]


def test_visible_models_empty_cached_returns_empty(monkeypatch):
    """_visible_models returns [] for empty cached list."""
    from routes.model_routes import _visible_models

    result = _visible_models([], None)
    assert result == []

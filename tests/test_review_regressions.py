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
    db_mod.Session = MagicMock()
    middleware_mod = types.ModuleType("core.middleware")
    middleware_mod.require_admin = lambda request: None
    multipart_mod = types.ModuleType("python_multipart")
    multipart_mod.__version__ = "0.0.13"

    monkeypatch.delitem(sys.modules, "routes.model_routes", raising=False)
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)
    monkeypatch.setitem(sys.modules, "core.middleware", middleware_mod)
    monkeypatch.setitem(sys.modules, "python_multipart", multipart_mod)


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

    async def fake_maybe_compact(sess, endpoint_url, model, messages, headers):
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
    from src.tool_execution import execute_tool_block
    import core.auth

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(core.auth, "AuthManager", lambda: FakeAuth())

    desc, result = await execute_tool_block(
        SimpleNamespace(tool_type="manage_tokens", content='{"action":"create","name":"bad"}'),
        owner="regular-user",
    )

    assert desc == "manage_tokens: BLOCKED"
    assert result["exit_code"] == 1
    assert "requires an admin" in result["error"]


@pytest.mark.asyncio
async def test_public_agent_policy_blocks_sensitive_tools(monkeypatch):
    from src.tool_execution import execute_tool_block
    import core.auth

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(core.auth, "AuthManager", lambda: FakeAuth())

    for tool_name in ("send_email", "read_file", "app_api", "mcp__email__send_email"):
        desc, result = await execute_tool_block(
            SimpleNamespace(tool_type=tool_name, content="{}"),
            owner="regular-user",
        )
        assert desc == f"{tool_name}: BLOCKED"
        assert result["exit_code"] == 1
        assert "restricted to admin users" in result["error"]


def test_public_agent_policy_hides_sensitive_tools(monkeypatch):
    import core.auth
    from src.tool_security import blocked_tools_for_owner

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return False

    monkeypatch.setattr(core.auth, "AuthManager", lambda: FakeAuth())

    blocked = blocked_tools_for_owner("regular-user")

    assert "send_email" in blocked
    assert "read_file" in blocked
    assert "app_api" in blocked
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
    sys.modules.pop("src.webhook_manager", None)
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

    assert result["exit_code"] == 1
    assert "private/internal" in result["error"]

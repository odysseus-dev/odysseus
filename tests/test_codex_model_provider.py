import asyncio
import os
import sys
import types
from types import SimpleNamespace

from src.codex_model_provider import (
    CODEX_EXPERIMENTAL_MODEL_ID,
    CODEX_MODEL_PROVIDER_FLAG,
    CODEX_EXPERIMENTAL_MODEL_DISPLAY,
    CodexCliChatAdapter,
    CodexModelProvider,
    codex_available_models,
    normalize_codex_model_id,
)
import src.codex_model_provider as codex_provider_module
from routes.codex_model_provider_routes import setup_codex_model_provider_routes


if "core" not in sys.modules:
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")]
    sys.modules["core"] = core_pkg


def run(coro):
    return asyncio.run(coro)


class _FakeService:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def status(self):
        self.calls += 1
        return dict(self.payload)

    def _bin_path(self):
        return "/usr/bin/codex"

    def _env(self):
        return {"PATH": "/usr/bin"}


class _FakeAdapter:
    def __init__(self):
        self.calls = []

    async def available(self):
        return {
            "ok": True,
            "status": "available",
            "chat_supported": True,
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
            "limitations": [],
        }

    async def complete(self, messages, model=None, timeout_seconds=120, **kwargs):
        self.calls.append({"messages": messages, "model": model, "timeout_seconds": timeout_seconds, **kwargs})
        return {
            "ok": True,
            "status": "ok",
            "message": "mock response",
            "duration_ms": 1,
            "model": model or CODEX_EXPERIMENTAL_MODEL_ID,
            "limitations": [],
            "streaming_supported": False,
            "session_resume_supported": False,
            "tool_execution_allowed": False,
        }


def _provider(payload):
    svc = _FakeService(payload)
    adapter = _FakeAdapter()
    return CodexModelProvider(lambda: svc, chat_adapter=adapter), svc


def test_codex_model_provider_hidden_when_flag_disabled(monkeypatch):
    monkeypatch.delenv(CODEX_MODEL_PROVIDER_FLAG, raising=False)
    provider, svc = _provider({"codex_cli_available": True, "authenticated": True})

    out = run(provider.status())

    assert out["feature_enabled"] is False
    assert out["status"] == "disabled"
    assert out["models"] == []
    assert svc.calls == 0


def test_codex_model_provider_requires_sign_in_when_unauthenticated(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({
        "codex_cli_available": True,
        "authenticated": False,
        "status": "not_authenticated",
    })

    out = run(provider.status())

    assert out["status"] == "sign_in_required"
    assert out["requires_sign_in"] is True
    assert out["models"] == []
    assert out["chat_supported"] is False
    assert out["streaming_supported"] is False


def test_codex_model_provider_reports_experimental_model_when_authenticated(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    provider, _ = _provider({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
        "auth_mode": "ChatGPT",
        "access_token": "secret",
        "refresh_token": "secret",
    })

    out = run(provider.status())

    assert out["status"] == "available"
    assert out["authenticated"] is True
    assert out["models"][0]["id"] == CODEX_EXPERIMENTAL_MODEL_ID
    assert out["models"][0]["display"] == CODEX_EXPERIMENTAL_MODEL_DISPLAY
    assert out["models"][0]["experimental"] is True
    assert out["chat_supported"] is True
    assert out["streaming_supported"] is False
    assert out["session_resume_supported"] is False
    assert out["tool_execution_allowed"] is False
    assert "secret" not in str(out)
    assert "access_token" not in str(out)
    assert "refresh_token" not in str(out)


def test_codex_model_provider_reports_cli_unavailable(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({
        "codex_cli_available": False,
        "cli_found": False,
        "cli_executable": False,
        "status": "cli_missing",
    })

    out = run(provider.status())

    assert out["status"] == "cli_unavailable"
    assert out["cli_available"] is False
    assert out["models"] == []


class _AuthManager:
    is_configured = True

    def is_admin(self, user):
        return user == "admin"


def _request(user="admin"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=_AuthManager())),
    )


def _endpoint(router, path, method):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def test_codex_model_provider_route_is_admin_gated(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({"codex_cli_available": True, "authenticated": True})
    router = setup_codex_model_provider_routes(provider)
    status = _endpoint(router, "/api/codex-model-provider/status", "GET")

    out = run(status(_request(user="admin")))
    assert out["status"] == "available"

    try:
        run(status(_request(user="bob")))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("non-admin request should fail")


def test_codex_model_provider_test_chat_route_is_admin_gated(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({"codex_cli_available": True, "authenticated": True})
    router = setup_codex_model_provider_routes(provider)
    test_chat = _endpoint(router, "/api/codex-model-provider/test-chat", "POST")

    body = SimpleNamespace(prompt="hello", messages=None, model=None, timeout_seconds=None)
    out = run(test_chat(_request(user="admin"), body))
    assert out["ok"] is True
    assert out["message"] == "mock response"

    try:
        run(test_chat(_request(user="bob"), body))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("non-admin request should fail")


def test_codex_model_provider_test_chat_requires_body(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({"codex_cli_available": True, "authenticated": True})
    router = setup_codex_model_provider_routes(provider)
    test_chat = _endpoint(router, "/api/codex-model-provider/test-chat", "POST")

    body = SimpleNamespace(prompt="", messages=None, model=None, timeout_seconds=None)
    out = run(test_chat(_request(user="admin"), body))
    assert out["ok"] is False
    assert out["status"] == "invalid_request"


def test_codex_model_picker_item_when_authenticated(monkeypatch, tmp_path):
    from src.codex_model_provider import CODEX_PROVIDER_ENDPOINT_URL, codex_model_picker_item

    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    provider, _ = _provider({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })

    item = run(codex_model_picker_item(provider))

    assert item["url"] == CODEX_PROVIDER_ENDPOINT_URL
    assert item["endpoint_id"] == "codex-cli"
    assert item["models"] == [CODEX_EXPERIMENTAL_MODEL_ID]
    assert item["endpoint_name"] == "Codex / ChatGPT"
    assert item["experimental"] is True
    assert item["streaming_supported"] is False


def test_codex_selection_detection():
    from src.codex_model_provider import CODEX_PROVIDER_ENDPOINT_URL, is_codex_provider_selection

    assert is_codex_provider_selection(CODEX_PROVIDER_ENDPOINT_URL, CODEX_EXPERIMENTAL_MODEL_ID)
    assert is_codex_provider_selection(CODEX_PROVIDER_ENDPOINT_URL + "/chat", "anything")
    assert is_codex_provider_selection("", CODEX_EXPERIMENTAL_MODEL_ID)
    assert not is_codex_provider_selection("http://localhost:8000/v1", "llama")


def test_stream_codex_chat_emits_sse_delta_and_done(monkeypatch):
    from src.codex_model_provider import stream_codex_chat

    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })

    async def collect():
        return [chunk async for chunk in stream_codex_chat([{"role": "user", "content": "hi"}], provider=provider)]

    chunks = run(collect())

    assert any('"delta": "mock response"' in chunk for chunk in chunks)
    assert any('"type": "metrics"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_codex_chat_can_use_odysseus_agent_prompt(monkeypatch):
    from src.codex_model_provider import stream_codex_chat

    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    provider, _ = _provider({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })

    async def collect():
        return [
            chunk async for chunk in stream_codex_chat(
                [
                    {"role": "system", "content": "Use ```read_file blocks for files."},
                    {"role": "user", "content": "read ./README.md"},
                ],
                provider=provider,
                allow_odysseus_tools=True,
            )
        ]

    chunks = run(collect())

    assert any('"delta": "mock response"' in chunk for chunk in chunks)
    adapter_call = provider._chat_adapter.calls[-1]
    assert adapter_call["allow_odysseus_tools"] is True


def test_codex_agent_prompt_says_writes_are_allowed_through_odysseus_tools():
    from src.codex_model_provider import CodexCliChatAdapter

    prompt = CodexCliChatAdapter._build_prompt(
        [{"role": "user", "content": "utwórz plik notes.txt"}],
        allow_odysseus_tools=True,
    )

    assert "You are NOT a read-only agent" in prompt
    assert "You may create, edit, overwrite, and delete files" in prompt
    assert "write_file" in prompt
    assert "bash" in prompt
    assert "Codex CLI sandbox" not in prompt
    assert "Do NOT run shell commands, edit files" not in prompt
    assert "direct tools" not in prompt.lower()


def test_llm_core_routes_codex_virtual_endpoint_to_codex_stream(monkeypatch):
    from src.codex_model_provider import CODEX_PROVIDER_ENDPOINT_URL
    import src.llm_core as llm_core

    calls = []

    async def fake_stream(messages, model=None, timeout_seconds=None, allow_odysseus_tools=False, **kwargs):
        calls.append({
            "messages": messages,
            "model": model,
            "timeout_seconds": timeout_seconds,
            "allow_odysseus_tools": allow_odysseus_tools,
        })
        yield 'data: {"delta": "codex agent"}\n\n'
        yield 'data: [DONE]\n\n'

    monkeypatch.setattr("src.codex_model_provider.stream_codex_chat", fake_stream)

    async def collect():
        return [
            chunk async for chunk in llm_core.stream_llm(
                CODEX_PROVIDER_ENDPOINT_URL,
                "gpt-5.5",
                [{"role": "user", "content": "hi"}],
            )
        ]

    chunks = run(collect())

    assert chunks == ['data: {"delta": "codex agent"}\n\n', 'data: [DONE]\n\n']
    assert calls[0]["allow_odysseus_tools"] is True


def test_chat_route_only_bypasses_codex_for_plain_chat_mode():
    route_path = os.path.join(os.path.dirname(__file__), "..", "routes", "chat_routes.py")
    route_text = open(route_path, encoding="utf-8").read()

    assert "if is_codex_session and _effective_mode == \"chat\":" in route_text
    assert "stream_agent_loop(" in route_text


def test_model_routes_only_expose_codex_when_endpoint_is_visible_to_user():
    route_path = os.path.join(os.path.dirname(__file__), "..", "routes", "model_routes.py")
    route_text = open(route_path, encoding="utf-8").read()

    assert "def _append_codex_model_item(result: Dict[str, Any], owner: str = \"\", is_admin: bool = False)" in route_text
    assert "codex_already_visible" in route_text
    assert "if not codex_already_visible:" in route_text
    assert "return out" in route_text
    assert "if ep_id == CODEX_PROVIDER_ENDPOINT_ID and asyncio.run(codex_model_picker_item())" not in route_text


def test_session_routes_require_visible_codex_endpoint_before_skip_validation():
    route_path = os.path.join(os.path.dirname(__file__), "..", "routes", "session_routes.py")
    route_text = open(route_path, encoding="utf-8").read()

    assert "ModelEndpoint" in route_text
    assert "CODEX_PROVIDER_ENDPOINT_ID" in route_text
    assert "Codex model endpoint is not enabled for this user" in route_text


def test_codex_add_model_route_scopes_endpoint_to_current_admin_and_supports_tools():
    route_path = os.path.join(os.path.dirname(__file__), "..", "routes", "codex_model_provider_routes.py")
    route_text = open(route_path, encoding="utf-8").read()

    assert "owner = get_current_user(request)" in route_text
    assert "supports_tools=True" in route_text
    assert "ep.supports_tools = True" in route_text
    assert "ep.owner = owner" in route_text
    assert "owner=None" not in route_text
    assert "supports_tools=False" not in route_text


def test_adapter_success_from_mocked_subprocess(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    svc = _FakeService({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })
    calls = []

    async def runner(args, timeout, cwd=None, env=None):
        calls.append((args, cwd, env))
        if args[-1] == "--help":
            return 0, "Usage: codex exec --sandbox <MODE> --ask-for-approval <POLICY> --json", ""
        return 0, "codex provider test ok", ""

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.complete([{"role": "user", "content": "Say ok"}]))

    assert out["ok"] is True
    assert out["message"] == "codex provider test ok"
    assert out["streaming_supported"] is False
    assert out["session_resume_supported"] is False
    assert out["tool_execution_allowed"] is False
    assert calls[1][1]
    assert "--sandbox" in calls[1][0]
    assert "read-only" in calls[1][0]
    assert "-c" in calls[1][0]
    assert 'approval_policy="never"' in calls[1][0]
    assert "--ask-for-approval" not in calls[1][0]
    assert "--model" not in calls[1][0]


def test_adapter_uses_workspace_write_sandbox_for_odysseus_agent_mode(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    svc = _FakeService({
        "codex_cli_available": True,
        "authenticated": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })
    calls = []

    async def runner(args, timeout, cwd=None, env=None):
        calls.append((args, cwd, env))
        if args[-1] == "--help":
            return 0, "Usage: codex exec --sandbox <MODE>", ""
        return 0, "ok", ""

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.complete(
        [{"role": "user", "content": "create a file"}],
        allow_odysseus_tools=True,
    ))

    assert out["ok"] is True
    assert out["tool_execution_allowed"] is True
    exec_args = calls[1][0]
    assert "--sandbox" in exec_args
    assert exec_args[exec_args.index("--sandbox") + 1] == "workspace-write"
    assert "read-only" not in exec_args

def test_codex_available_models_reads_codex_cli_cache(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(
        '{"models": ['
        '{"slug":"hidden","display_name":"Hidden","visibility":"hidden","supported_in_api":true,"priority":1},'
        '{"slug":"gpt-5.4-mini","display_name":"GPT-5.4-Mini","visibility":"list","supported_in_api":true,"priority":20},'
        '{"slug":"gpt-5.5","display_name":"GPT-5.5","description":"Frontier","visibility":"list","supported_in_api":true,"priority":10}'
        ']}',
        encoding="utf-8",
    )

    models = codex_available_models(cache)

    assert [m["id"] for m in models] == ["gpt-5.5", "gpt-5.4-mini"]
    assert models[0]["display"] == "GPT-5.5"
    assert "base_instructions" not in models[0]


def test_adapter_passes_selected_codex_model_to_cli(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    svc = _FakeService({"codex_cli_available": True, "authenticated": True})
    calls = []

    async def runner(args, timeout, cwd=None, env=None):
        calls.append(args)
        if args[-1] == "--help":
            return 0, "Usage: codex exec --sandbox --model <MODEL>", ""
        return 0, "ok", ""

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.complete([{"role": "user", "content": "hi"}], model="gpt-5.5"))

    assert out["ok"] is True
    assert "--model" in calls[1]
    assert calls[1][calls[1].index("--model") + 1] == "gpt-5.5"
    assert normalize_codex_model_id("codex-cli/gpt-5.4") == "gpt-5.4"


def test_adapter_handles_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    svc = _FakeService({"codex_cli_available": True, "authenticated": True})

    async def runner(args, timeout, cwd=None, env=None):
        if args[-1] == "--help":
            return 0, "Usage: codex exec --sandbox --ask-for-approval", ""
        return 124, "", "access_token=secret"

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.complete([{"role": "user", "content": "hello"}], timeout_seconds=1))

    assert out["ok"] is False
    assert out["status"] == "timeout"
    assert "secret" not in str(out)


def test_adapter_handles_cli_nonzero_and_redacts(monkeypatch, tmp_path):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    monkeypatch.setattr(codex_provider_module, "CODEX_MODELS_CACHE_PATH", tmp_path / "missing-models-cache.json")
    svc = _FakeService({"codex_cli_available": True, "authenticated": True})

    async def runner(args, timeout, cwd=None, env=None):
        if args[-1] == "--help":
            return 0, "Usage: codex exec --sandbox --ask-for-approval", ""
        return 2, "", "refresh_token=secret failed"

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.complete([{"role": "user", "content": "hello"}]))

    assert out["ok"] is False
    assert out["status"] == "cli_failed"
    assert "secret" not in str(out)
    assert "refresh_token" not in str(out)


def test_adapter_refuses_unsafe_cli_help(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    svc = _FakeService({"codex_cli_available": True, "authenticated": True})

    async def runner(args, timeout, cwd=None, env=None):
        return 0, "Usage: codex exec --json", ""

    adapter = CodexCliChatAdapter(lambda: svc, runner=runner)
    out = run(adapter.available())

    assert out["ok"] is False
    assert out["status"] == "unsupported_unsafe_cli_mode"
    assert "--sandbox" in out["missing_flags"]
    assert "--ask-for-approval" not in out["missing_flags"]


def test_status_does_not_expose_model_when_adapter_is_unsafe(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")

    class UnsafeAdapter:
        async def available(self):
            return {"ok": False, "status": "unsupported_unsafe_cli_mode"}

    svc = _FakeService({"codex_cli_available": True, "authenticated": True})
    provider = CodexModelProvider(lambda: svc, chat_adapter=UnsafeAdapter())

    out = run(provider.status())

    assert out["status"] == "unsupported_unsafe_cli_mode"
    assert out["models"] == []
    assert out["chat_supported"] is False

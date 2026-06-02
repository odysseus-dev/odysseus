import asyncio
import json

from src.codex_model_provider import (
    CODEX_MODEL_PROVIDER_FLAG,
    CODEX_PROVIDER_ENDPOINT_URL,
    CODEX_DEFAULT_MODEL_ID,
    CodexCliChatAdapter,
    CodexModelProvider,
    codex_available_models,
    codex_endpoint_id_for_owner,
    stream_codex_chat,
)


def run(coro):
    return asyncio.run(coro)


class _AuthService:
    async def status(self):
        return {"codex_cli_available": True, "codex_authenticated": True}

    def _bin_path(self):
        return "/usr/bin/codex"

    def _env(self):
        return {"PATH": "/usr/bin"}


def test_endpoint_id_is_owner_scoped():
    assert codex_endpoint_id_for_owner("sean") == codex_endpoint_id_for_owner("Sean")
    assert codex_endpoint_id_for_owner("sean") != codex_endpoint_id_for_owner("admin")


def test_available_models_reads_codex_cache(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({
        "models": [
            {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hidden"},
            {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list", "priority": 1},
            {"slug": "gpt-5.5", "display_name": "Duplicate", "visibility": "list", "priority": 2},
        ]
    }))
    models = codex_available_models(cache)
    assert models == [{
        "id": "gpt-5.5",
        "display": "GPT-5.5",
        "description": "",
        "priority": 1,
        "experimental": False,
    }]


def test_available_models_falls_back_to_gpt_55(tmp_path):
    assert codex_available_models(tmp_path / "missing.json")[0]["id"] == CODEX_DEFAULT_MODEL_ID


def test_provider_disabled_by_default(monkeypatch):
    monkeypatch.delenv(CODEX_MODEL_PROVIDER_FLAG, raising=False)
    provider = CodexModelProvider(CodexCliChatAdapter(auth_service_getter=_AuthService))
    out = run(provider.status())
    assert out["status"] == "disabled"
    assert out["feature_enabled"] is False
    assert out["billing_mode"] == "openai_subscription"
    assert out["api_key_required"] is False
    assert out["usage_meter_supported"] is False


def test_complete_uses_readonly_sandbox_and_selected_model(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    calls = []

    async def runner(args, timeout, cwd=None, env=None):
        calls.append({"args": args, "timeout": timeout, "cwd": cwd, "env": env})
        if args == ["/usr/bin/codex", "exec", "--help"]:
            return 0, "--sandbox --skip-git-repo-check --ephemeral --model", ""
        return 0, "hello from codex", ""

    adapter = CodexCliChatAdapter(auth_service_getter=_AuthService, runner=runner)
    out = run(adapter.complete([{"role": "user", "content": "Hi"}], model="codex-cli/gpt-5.5"))

    assert out["ok"] is True
    assert out["message"] == "hello from codex"
    exec_args = calls[-1]["args"]
    assert exec_args[:2] == ["/usr/bin/codex", "exec"]
    assert "--sandbox" in exec_args
    assert "read-only" in exec_args
    assert "--skip-git-repo-check" in exec_args
    assert "--ephemeral" in exec_args
    assert exec_args[exec_args.index("--model") + 1] == "gpt-5.5"
    assert "Do not run tools" in exec_args[-1]


def test_stream_codex_chat_emits_error_and_done_without_fallback(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")

    class _Provider:
        async def test_chat(self, messages, model=None, timeout_seconds=180):
            return {"ok": False, "status": "sign_in_required", "error": "Sign in with Codex first"}

    async def collect():
        return [chunk async for chunk in stream_codex_chat([{"role": "user", "content": "Hi"}], provider=_Provider())]

    chunks = run(collect())
    assert chunks[0].startswith("event: error")
    assert chunks[-1] == "data: [DONE]\n\n"


def test_codex_virtual_endpoint_does_not_use_api_headers():
    from src.endpoint_resolver import build_chat_url, build_headers, build_models_url

    assert build_chat_url(CODEX_PROVIDER_ENDPOINT_URL) == CODEX_PROVIDER_ENDPOINT_URL
    assert build_models_url(CODEX_PROVIDER_ENDPOINT_URL) == CODEX_PROVIDER_ENDPOINT_URL
    assert build_headers("sk-should-not-be-used", CODEX_PROVIDER_ENDPOINT_URL) == {}


class _Endpoint:
    id = "codex-cli-test"
    name = "OpenAI subscription (Codex)"
    base_url = CODEX_PROVIDER_ENDPOINT_URL
    api_key = "sk-should-not-be-used"
    is_enabled = True
    cached_models = json.dumps(["gpt-5.5"])
    models = None


class _Query:
    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return _Endpoint()


class _Db:
    def query(self, *args, **kwargs):
        return _Query()

    def close(self):
        pass


def _install_codex_resolver_fakes(monkeypatch, endpoint_id="codex-cli-test"):
    from src import endpoint_resolver
    import src.settings as settings

    monkeypatch.setattr(endpoint_resolver, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(settings, "load_settings", lambda: {
        "default_endpoint_id": endpoint_id,
        "default_model": "",
        "utility_endpoint_id": "",
        "utility_model": "",
    })
    monkeypatch.setattr(
        settings,
        "get_user_setting",
        lambda key, owner="", default=None: default,
    )
    return endpoint_resolver


def test_codex_endpoint_resolves_through_shared_default_and_utility(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "true")
    endpoint_resolver = _install_codex_resolver_fakes(monkeypatch)

    assert endpoint_resolver.resolve_endpoint("default") == (
        CODEX_PROVIDER_ENDPOINT_URL,
        "gpt-5.5",
        {},
    )
    assert endpoint_resolver.resolve_endpoint("utility") == (
        CODEX_PROVIDER_ENDPOINT_URL,
        "gpt-5.5",
        {},
    )


def test_codex_endpoint_does_not_resolve_when_feature_flag_is_off(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_PROVIDER_FLAG, "false")
    endpoint_resolver = _install_codex_resolver_fakes(monkeypatch)

    assert endpoint_resolver.resolve_endpoint("default", "fallback-url", "fallback-model", {}) == (
        "fallback-url",
        "fallback-model",
        {},
    )
    assert endpoint_resolver.resolve_endpoint_by_id("codex-cli-test") is None

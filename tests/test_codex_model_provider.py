import asyncio
from types import SimpleNamespace

import pytest

from src import codex_model_provider
from src.codex_auth import set_codex_auth_service


def run(coro):
    return asyncio.run(coro)


class _FakeCodexAuthService:
    def __init__(self, status):
        self._status = status

    async def status(self):
        return dict(self._status)


@pytest.fixture(autouse=True)
def _reset_codex_auth_service():
    yield
    set_codex_auth_service(None)


def _set_auth(status):
    set_codex_auth_service(_FakeCodexAuthService(status))


def test_provider_status_disabled_by_default(monkeypatch):
    monkeypatch.delenv(codex_model_provider.FEATURE_FLAG, raising=False)
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": True,
        "status": "authenticated",
    })

    out = run(codex_model_provider.provider_status())

    assert out["status"] == "disabled"
    assert out["enabled"] is False
    assert out["models"] == []
    assert out["capabilities"]["chat_supported"] is False


def test_provider_status_requires_sign_in_when_enabled(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": False,
        "status": "not_authenticated",
        "message": "Codex CLI ready. Not signed in.",
    })

    out = run(codex_model_provider.provider_status())

    assert out["status"] == "sign_in_required"
    assert out["requires_sign_in"] is True
    assert out["models"] == []


def test_provider_status_does_not_require_sign_in_when_cli_unavailable(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": False,
        "codex_authenticated": False,
        "status": "cli_unavailable",
    })

    out = run(codex_model_provider.provider_status())

    assert out["status"] == "cli_unavailable"
    assert out["requires_sign_in"] is False
    assert out["models"] == []


def test_provider_status_available_but_chat_deferred(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": True,
        "authenticated": True,
        "status": "authenticated",
        "auth_mode": "ChatGPT",
    })

    out = run(codex_model_provider.provider_status())

    assert out["status"] == "available"
    assert out["provider_type"] == "codex_cli"
    assert out["auth_type"] == "codex_cli"
    assert out["endpoint_url"] == "odysseus://codex-cli/codex-cli"
    assert out["models"] == ["codex-cli/chatgpt-experimental"]
    assert out["capabilities"] == {
        "chat_supported": False,
        "streaming_supported": False,
        "agent_tools_supported": False,
        "session_resume_supported": False,
    }


def test_provider_status_redacts_token_like_auth_fields(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": True,
        "access_token": "secret-token",
        "message": "access_token=secret-token",
    })

    out = run(codex_model_provider.provider_status())

    assert "access_token" not in out["auth"]
    assert "secret-token" not in str(out)
    assert out["auth"]["message"] == "[redacted]"


def test_model_list_item_is_absent_until_available(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": False,
        "status": "not_authenticated",
    })

    assert codex_model_provider.codex_model_list_item_if_available() is None


def test_model_list_item_does_not_probe_auth_when_feature_disabled(monkeypatch):
    class _FailingCodexAuthService:
        async def status(self):
            raise AssertionError("auth status should not be probed")

    monkeypatch.delenv(codex_model_provider.FEATURE_FLAG, raising=False)
    set_codex_auth_service(_FailingCodexAuthService())

    assert codex_model_provider.codex_model_list_item_if_available() is None


def test_model_list_item_is_offline_until_chat_slice(monkeypatch):
    monkeypatch.setenv(codex_model_provider.FEATURE_FLAG, "true")
    _set_auth({
        "codex_cli_available": True,
        "codex_authenticated": True,
        "authenticated": True,
        "status": "authenticated",
    })

    item = codex_model_provider.codex_model_list_item_if_available()

    assert item["url"] == "odysseus://codex-cli/codex-cli"
    assert item["models"] == ["codex-cli/chatgpt-experimental"]
    assert item["offline"] is True
    assert item["provider_type"] == "codex_cli"


@pytest.mark.asyncio
async def test_status_route_is_admin_gated(monkeypatch):
    import routes.codex_model_provider_routes as provider_routes

    async def fake_provider_status():
        return {"status": "disabled"}

    monkeypatch.setattr(provider_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(
        provider_routes.codex_model_provider,
        "provider_status",
        fake_provider_status,
    )

    router = provider_routes.setup_codex_model_provider_routes()
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "") == "/api/codex-model-provider/status"
    )

    assert await endpoint(SimpleNamespace()) == {"status": "disabled"}

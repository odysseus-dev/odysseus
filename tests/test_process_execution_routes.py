"""Admin-only transient Full Access confirmation routes."""

import asyncio
from types import SimpleNamespace

import pytest

from src import process_execution


class _AuthManager:
    def get_username_for_token(self, token):
        return "admin" if token == "session-token" else None

    def is_admin(self, user):
        return user == "admin"


class _Request(SimpleNamespace):
    def __init__(self, *, token="session-token"):
        super().__init__(cookies={"odysseus_session": token})


def _endpoint(router, path, method):
    for route in router.routes:
        if (
            getattr(route, "path", "") == path
            and method in getattr(route, "methods", set())
        ):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


@pytest.fixture
def process_routes(monkeypatch):
    from routes import auth_routes

    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    process_execution.reset_process_execution_mode()
    capability = process_execution.ProcessCapability(
        process_execution.ProfileCapability(True, "", True, ""),
        process_execution.ProfileCapability(True, "", True, ""),
        1.0,
    )
    monkeypatch.setattr(
        process_execution,
        "process_capability",
        lambda **_kwargs: capability,
    )
    router = auth_routes.setup_auth_routes(_AuthManager())
    yield router, auth_routes
    process_execution.reset_process_execution_mode()


def test_full_access_requires_exact_typed_confirmation(process_routes):
    router, auth_routes = process_routes
    handler = _endpoint(router, "/api/auth/process-execution", "POST")

    with pytest.raises(auth_routes.HTTPException) as exc:
        asyncio.run(
            handler(
                auth_routes.SetProcessExecutionModeRequest(
                    mode="full_access",
                    confirmation="yes",
                ),
                _Request(),
            )
        )

    assert exc.value.status_code == 400
    assert (
        process_execution.configured_process_execution_mode()
        is process_execution.ProcessExecutionMode.SANDBOX
    )


def test_explicit_confirmation_enables_transient_full_access(process_routes):
    router, auth_routes = process_routes
    handler = _endpoint(router, "/api/auth/process-execution", "POST")

    result = asyncio.run(
        handler(
            auth_routes.SetProcessExecutionModeRequest(
                mode="full_access",
                confirmation=process_execution.FULL_ACCESS_CONFIRMATION,
            ),
            _Request(),
        )
    )

    assert result["mode"] == "full_access"
    assert result["transient"] is True
    assert result["mode_available"] is True
    assert (
        process_execution.configured_process_execution_mode()
        is process_execution.ProcessExecutionMode.FULL_ACCESS
    )


def test_sandbox_switch_does_not_require_confirmation(process_routes):
    router, auth_routes = process_routes
    handler = _endpoint(router, "/api/auth/process-execution", "POST")
    process_execution.set_process_execution_mode(
        process_execution.ProcessExecutionMode.FULL_ACCESS,
        confirmation=process_execution.FULL_ACCESS_CONFIRMATION,
    )

    result = asyncio.run(
        handler(
            auth_routes.SetProcessExecutionModeRequest(mode="sandbox"),
            _Request(),
        )
    )

    assert result["mode"] == "sandbox"
    assert (
        process_execution.configured_process_execution_mode()
        is process_execution.ProcessExecutionMode.SANDBOX
    )


def test_process_execution_routes_are_admin_only(process_routes):
    router, auth_routes = process_routes
    handler = _endpoint(router, "/api/auth/process-execution", "GET")

    with pytest.raises(auth_routes.HTTPException) as exc:
        asyncio.run(handler(_Request(token="invalid")))

    assert exc.value.status_code == 403

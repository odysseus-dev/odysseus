"""Regression coverage for app.py BASE_PATH routing and URL prefix wiring.

``BASE_PATH`` is resolved once at ``app`` import time, so each scenario clears
and re-imports ``app`` under the desired env (see ``tests/helpers/import_state``).
``get_base_path()`` reads ``os.environ`` on every call and is tested in-process
against a single import.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from tests.helpers.import_state import clear_module, preserve_import_state

pytest.importorskip("fastapi")


def _configure_app_env(monkeypatch, *, base_path: str | None, auth_enabled: str) -> None:
    monkeypatch.setenv("AUTH_ENABLED", auth_enabled)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ODYSSEUS_INPROCESS_POLLERS", "0")
    if base_path is None:
        monkeypatch.delenv("BASE_PATH", raising=False)
    else:
        monkeypatch.setenv("BASE_PATH", base_path)


def _ensure_real_src_database() -> None:
    """Conftest may stub ``src.database`` without ORM symbols full ``app`` needs."""
    clear_module("src.database")
    import src.database


@contextmanager
def _fresh_app_module(monkeypatch, *, base_path: str | None, auth_enabled: str = "false"):
    """Import ``app`` after applying env; restore prior import state on exit."""
    _configure_app_env(monkeypatch, base_path=base_path, auth_enabled=auth_enabled)
    _ensure_real_src_database()
    with preserve_import_state("app"):
        clear_module("app")
        import app as app_mod

        yield app_mod


def _client(app_mod, *, raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(app_mod.app, raise_server_exceptions=raise_server_exceptions)


def _route_paths(app_mod) -> list[str]:
    return [
        getattr(route, "path", None)
        for route in app_mod.app.routes
        if getattr(route, "path", None)
    ]


def _has_mount(app_mod, path: str) -> bool:
    return any(
        type(route).__name__ == "Mount" and getattr(route, "path", None) == path
        for route in app_mod.app.routes
    )


def _has_named_route(app_mod, *, name: str, path: str | None = None) -> bool:
    for route in app_mod.app.routes:
        if getattr(route, "name", None) != name:
            continue
        if path is None or getattr(route, "path", None) == path:
            return True
    return False


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("/", ""),
        ("/odysseus", "/odysseus"),
        ("/odysseus/", "/odysseus"),
    ],
)
def test_get_base_path_normalizes_env(monkeypatch, raw, expected):
    with _fresh_app_module(monkeypatch, base_path=None) as app_mod:
        if raw is None:
            monkeypatch.delenv("BASE_PATH", raising=False)
        else:
            monkeypatch.setenv("BASE_PATH", raw)
        assert app_mod.get_base_path() == expected


@pytest.mark.parametrize(
    "configured_base_path,expected_base_path",
    [
        (None, ""),
        ("", ""),
        ("/odysseus", "/odysseus"),
    ],
)
def test_app_routes_and_http_respect_configured_base_path(
    monkeypatch, configured_base_path, expected_base_path
):
    with _fresh_app_module(
        monkeypatch, base_path=configured_base_path, auth_enabled="false"
    ) as app_mod:
        client = _client(app_mod)
        paths = _route_paths(app_mod)

        assert app_mod.BASE_PATH == expected_base_path
        assert _has_mount(app_mod, f"{expected_base_path}/static")
        assert f"{expected_base_path}/static" in paths
        assert f"{expected_base_path}/api/health" in paths
        assert f"{expected_base_path}/login" in paths

        index_path = f"{expected_base_path}/" if expected_base_path else "/"
        index = client.get(index_path)
        static = client.get(f"{expected_base_path}/static/js/axios/api.js")
        health = client.get(f"{expected_base_path}/api/health")
        unprefixed_health = client.get("/api/health")

        assert health.status_code == 200
        assert static.status_code == 200
        assert index.status_code == 200
        assert "{{BASE_PATH}}" not in index.text
        assert f"window.__ODYSSEUS_BASE_PATH = '{expected_base_path}';" in index.text

        if expected_base_path:
            root = client.get("/", follow_redirects=False)
            assert _has_named_route(app_mod, name="root")
            assert root.status_code == 302
            assert root.headers.get("location", "").endswith(f"{expected_base_path}/")
            assert unprefixed_health.status_code == 404
        else:
            assert not _has_named_route(app_mod, name="root")
            assert _has_named_route(app_mod, name="index", path="/")
            assert unprefixed_health.status_code == 200


def test_auth_webhook_exemption_honors_base_path_prefix(monkeypatch):
    with _fresh_app_module(
        monkeypatch, base_path="/odysseus", auth_enabled="true"
    ) as app_mod:
        client = _client(app_mod, raise_server_exceptions=False)

        webhook = client.post("/odysseus/api/tasks/task-1/webhook/secret-token")
        protected = client.post("/odysseus/api/chat", json={})
        unprefixed_webhook = client.post(
            "/api/tasks/task-1/webhook/secret-token",
            follow_redirects=False,
        )

        # Auth middleware must let prefixed webhook paths through; the handler may
        # still 404/500 once it runs (e.g. missing task row in a fresh DB).
        assert webhook.status_code != 401
        assert protected.status_code == 401
        assert protected.json().get("error") == "Not authenticated"
        # Unprefixed paths are not exempt and must be rejected before the handler.
        assert unprefixed_webhook.status_code in {401, 302}

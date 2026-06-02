"""Tests for admin app log listing/tailing (issue #981)."""

import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from core import app_logs
from routes.admin_logs_routes import setup_admin_logs_routes


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    log_root.mkdir()
    monkeypatch.setattr(app_logs, "LOGS_DIR", str(log_root))
    monkeypatch.setattr(
        "core.constants.LOGS_DIR",
        str(log_root),
        raising=False,
    )
    return log_root


def test_enumerate_logs_empty(logs_dir):
    assert app_logs.enumerate_logs() == []


def test_enumerate_and_tail(logs_dir):
    p = logs_dir / "test.log"
    lines = [f"line {i}\n" for i in range(5)]
    p.write_text("".join(lines), encoding="utf-8")
    listed = app_logs.enumerate_logs()
    assert len(listed) == 1
    assert listed[0]["name"] == "test.log"
    result = app_logs.tail_log("test", lines=3)
    assert result is not None
    assert result["lines"] == ["line 2", "line 3", "line 4"]
    assert result["name"] == "test.log"


def test_resolve_rejects_traversal(logs_dir):
    (logs_dir / "ok.log").write_text("ok\n", encoding="utf-8")
    assert app_logs.resolve_log("../etc/passwd") is None
    assert app_logs.resolve_log("..") is None
    assert app_logs.resolve_log("sub/x.log") is None


def test_tail_caps_lines(logs_dir):
    p = logs_dir / "big.log"
    p.write_text("\n".join(f"L{i}" for i in range(3000)), encoding="utf-8")
    result = app_logs.tail_log("big", lines=5000)
    assert result is not None
    assert len(result["lines"]) == app_logs.MAX_TAIL_LINES


def test_scrub_line_masks_secrets():
    line = "api_key=secret123 password=foo Authorization: Bearer tok"
    scrubbed = app_logs.scrub_line(line)
    assert "secret123" not in scrubbed
    assert "foo" not in scrubbed
    assert "tok" not in scrubbed
    assert "***" in scrubbed


@pytest.mark.asyncio
async def test_list_route_requires_admin(monkeypatch):
    from core.middleware import require_admin

    def deny(_request):
        raise HTTPException(403, "Admin only")

    monkeypatch.setattr("routes.admin_logs_routes.require_admin", deny)
    router = setup_admin_logs_routes()
    route = next(r for r in router.routes if getattr(r, "path", None) == "/api/admin/logs")
    with pytest.raises(HTTPException) as exc:
        await route.endpoint(type("R", (), {})())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_route_returns_logs(monkeypatch, logs_dir):
    monkeypatch.setattr("routes.admin_logs_routes.require_admin", lambda _r: None)
    monkeypatch.setattr(
        "routes.admin_logs_routes.enumerate_logs",
        lambda: [{"name": "test.log", "bytes": 4, "modified": "2026-01-01T00:00:00"}],
    )
    router = setup_admin_logs_routes()
    route = next(r for r in router.routes if getattr(r, "path", None) == "/api/admin/logs")
    out = await route.endpoint(type("R", (), {})())
    assert out["logs"][0]["name"] == "test.log"


@pytest.mark.asyncio
async def test_tail_route_404(monkeypatch, logs_dir):
    monkeypatch.setattr("routes.admin_logs_routes.require_admin", lambda _r: None)
    router = setup_admin_logs_routes()
    route = next(
        r for r in router.routes if "{name}" in getattr(r, "path", "")
    )
    with pytest.raises(HTTPException) as exc:
        await route.endpoint(type("R", (), {})(), name="missing.log", tail=50)
    assert exc.value.status_code == 404

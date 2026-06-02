"""Tests for check-only update-status backend route."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fastapi import HTTPException

# `routes/update_status_routes.py` depends on `core.middleware`. Importing
# `core.*` directly triggers package-level initialization that can touch
# filesystem-backed SQLAlchemy setup when DB paths aren't available in the test
# environment. Keep the tests isolated by pre-seeding lightweight module stubs
# before import.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if "core" not in sys.modules:
    _core_pkg = types.ModuleType("core")
    _core_pkg.__path__ = [os.path.join(_ROOT, "core")]
    sys.modules["core"] = _core_pkg
else:
    _core_pkg = sys.modules["core"]
    if not hasattr(_core_pkg, "__path__"):
        _core_pkg.__path__ = [os.path.join(_ROOT, "core")]

_core_db = types.ModuleType("core.database")
for _name in (
    "SessionLocal",
    "Session",
    "ChatMessage",
    "Memory",
    "ScheduledTask",
    "TaskRun",
    "Document",
    "DocumentVersion",
    "GalleryImage",
    "CalendarEvent",
    "CalendarCal",
    "Note",
):
    setattr(_core_db, _name, MagicMock())

if "core.database" not in sys.modules:
    sys.modules["core.database"] = _core_db
if not hasattr(_core_pkg, "database"):
    setattr(_core_pkg, "database", _core_db)

import routes.update_status_routes as U


def _admin_request(current_user: str, is_admin: bool = True):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=current_user),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(
                    is_configured=True,
                    is_admin=lambda u: is_admin,
                )
            )
        ),
    )


def _route_endpoint():
    router = U.setup_update_status_routes()
    for route in router.routes:
        if route.path == "/api/admin/update/status":
            return route.endpoint
    raise AssertionError("update-status route missing")


def _run_response_for(commands):
    def _fake_run_git(cwd, args, timeout=1.8):
        command = " ".join(args)
        result = commands.get(command)
        if result is None:
            return {"ok": False, "error": "missing_stub", "details": f"no stub for {command}"}
        return result

    return _fake_run_git


def test_update_status_requires_admin():
    """A non-admin caller cannot reach the route."""
    endpoint = _route_endpoint()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(request=_admin_request("alice", is_admin=False)))
    assert exc.value.status_code == 403


def test_update_status_no_git_shows_unavailable(monkeypatch):
    """If git is absent, the endpoint returns a soft failure and still includes mode hints."""
    monkeypatch.setattr(
        U,
        "_run_git",
        lambda *args, **kwargs: {"ok": False, "error": "git_missing", "details": "git missing"},
    )
    status = U._collect_update_status("/tmp")
    assert status["git"]["available"] is False
    assert status["git"]["errors"]
    assert status["install"]["mode_guess"] in {"source_native", "prebuilt_docker"}


def test_update_status_timeout_is_reported(monkeypatch):
    """A git timeout should be recorded and should not throw."""
    repo_dir = os.path.join("/", "tmp", "update-status-timeout-test")
    os.makedirs(repo_dir, exist_ok=True)

    monkeypatch.setattr(
        U,
        "_run_git",
        lambda *args, **kwargs: {"ok": False, "error": "timeout", "details": "timed out"},
    )
    status = U._collect_update_status(repo_dir)

    assert status["git"]["available"] is False
    assert status["git"]["timeouts"] == ["rev-parse --show-toplevel"]
    assert status["git"]["errors"] == ["Repository is not a git checkout."]


def test_update_status_clean_repo(monkeypatch, tmp_path):
    """A clean repo returns commit/branch/upstream/remote SHA without dirty flag."""
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}", encoding="utf-8")

    stubs = {
        "rev-parse --show-toplevel": {
            "ok": True,
            "returncode": 0,
            "stdout": str(tmp_path),
            "stderr": "",
        },
        "rev-parse --short HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "cafebabe",
            "stderr": "",
        },
        "rev-parse --abbrev-ref HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "main",
            "stderr": "",
        },
        "status --porcelain": {"ok": True, "returncode": 0, "stdout": "", "stderr": ""},
        "config --get branch.main.remote": {
            "ok": True,
            "returncode": 0,
            "stdout": "origin",
            "stderr": "",
        },
        "config --get branch.main.merge": {
            "ok": True,
            "returncode": 0,
            "stdout": "refs/heads/main",
            "stderr": "",
        },
        "config --get remote.origin.url": {
            "ok": True,
            "returncode": 0,
            "stdout": "https://user:secret@example.com/odysseus.git",
            "stderr": "",
        },
        "for-each-ref --format=%(refname:short) %(upstream:short) refs/heads": {
            "ok": True,
            "returncode": 0,
            "stdout": "main origin/main",
            "stderr": "",
        },
        "ls-remote --heads origin main": {
            "ok": True,
            "returncode": 0,
            "stdout": "feedface1234567890\trefs/heads/main",
            "stderr": "",
        },
        "rev-list --left-right --count HEAD...@{u}": {
            "ok": True,
            "returncode": 0,
            "stdout": "1\t2",
            "stderr": "",
        },
    }
    monkeypatch.setattr(U, "_run_git", _run_response_for(stubs))
    status = U._collect_update_status(str(tmp_path))

    assert "repo_path" not in status
    assert "top_level" not in status["git"]
    assert status["git"]["available"] is True
    assert status["git"]["commit"] == "cafebabe"
    assert status["git"]["branch"] == "main"
    assert status["git"]["detached_head"] is False
    assert status["git"]["dirty"] is False
    assert status["git"]["upstream"]["tracking_configured"] is True
    assert status["git"]["upstream"]["remote"] == "origin"
    assert status["git"]["upstream"]["remote_url_scrubbed"] == "https://example.com/odysseus.git"
    assert status["git"]["remote"]["sha"] == "feedface1234567890"
    assert status["git"]["ahead"] == 1
    assert status["git"]["behind"] == 2
    assert status["install"]["mode_guess"] == "source_docker"
    assert status["install"]["manual_update_commands"] == U._MODE_COMMANDS["source_docker"]


def test_update_status_detects_dirty_and_detached(monkeypatch, tmp_path):
    """Detached HEAD and dirty working tree are surfaced as warnings with no upstream checks."""
    stubs = {
        "rev-parse --show-toplevel": {
            "ok": True,
            "returncode": 0,
            "stdout": str(tmp_path),
            "stderr": "",
        },
        "rev-parse --short HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "deadbeef",
            "stderr": "",
        },
        "rev-parse --abbrev-ref HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "HEAD",
            "stderr": "",
        },
        "status --porcelain": {
            "ok": True,
            "returncode": 0,
            "stdout": " M dirty.txt",
            "stderr": "",
        },
    }
    monkeypatch.setattr(U, "_run_git", _run_response_for(stubs))
    status = U._collect_update_status(str(tmp_path))

    assert status["git"]["detached_head"] is True
    assert status["git"]["branch"] == "HEAD"
    assert status["git"]["dirty"] is True
    assert status["git"]["upstream"]["tracking_configured"] is False
    assert any("Detached HEAD" in warn for warn in status["git"]["warnings"])


def test_update_status_reports_missing_upstream(monkeypatch, tmp_path):
    """If branch config does not point to an upstream branch, callers get a warning."""
    stubs = {
        "rev-parse --show-toplevel": {
            "ok": True,
            "returncode": 0,
            "stdout": str(tmp_path),
            "stderr": "",
        },
        "rev-parse --short HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "beefcafe",
            "stderr": "",
        },
        "rev-parse --abbrev-ref HEAD": {
            "ok": True,
            "returncode": 0,
            "stdout": "feature",
            "stderr": "",
        },
        "status --porcelain": {"ok": True, "returncode": 0, "stdout": "", "stderr": ""},
        "config --get branch.feature.remote": {
            "ok": True,
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        },
        "config --get branch.feature.merge": {"ok": True, "returncode": 0, "stdout": "", "stderr": ""},
        "for-each-ref --format=%(refname:short) %(upstream:short) refs/heads": {"ok": True, "returncode": 0, "stdout": "feature ", "stderr": ""},
    }
    monkeypatch.setattr(U, "_run_git", _run_response_for(stubs))
    status = U._collect_update_status(str(tmp_path))

    assert status["git"]["upstream"]["tracking_configured"] is False
    assert any("No upstream tracking branch configured." in warn for warn in status["git"]["warnings"])


def test_scrub_remote_url_removes_credentials():
    assert (
        U._scrub_remote_url("https://alice:tok_xyz@example.com/odysseus.git")
        == "https://example.com/odysseus.git"
    )
    assert U._scrub_remote_url("git@github.com:odysseus/odysseus.git") == "git@github.com:odysseus/odysseus.git"


def test_remote_url_allowed_rejects_local_paths_and_unknown_schemes():
    assert U._remote_url_allowed("https://github.com/example/odysseus.git") is True
    assert U._remote_url_allowed("git@github.com:example/odysseus.git") is True
    assert U._remote_url_allowed("file:///tmp/odysseus.git") is False
    assert U._remote_url_allowed("/tmp/odysseus.git") is False

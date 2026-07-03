"""Path-confinement regression tests for research routes.

Covers the CodeQL py/path-injection alert cluster (#552-#567) in
routes/research/research_routes.py:
  - _owns_in_memory disk fallback (alerts #552, #553)
  - _assert_owns_research (alerts #554, #555)
  - research_detail (alerts #556, #557)
  - research_archive (alerts #558, #559, #560)
  - research_delete (alerts #561, #562, #563)
  - research_result_peek (alerts #564, #565)
  - research_spinoff (alerts #566, #567)
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from routes.research_routes import setup_research_routes
from routes.research.research_routes import _confine_research_path


@pytest.fixture(autouse=True)
def _redirect_research_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "routes.research_routes.DEEP_RESEARCH_DIR",
        str(tmp_path / "deep_research"),
    )


def _request(user: str):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def _write_research(data_dir, session_id: str, **data):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{session_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _research_handler():
    handler = MagicMock()
    handler._active_tasks = {}
    return handler


# ---------------------------------------------------------------------------
# Helper-level tests — _confine_research_path
# ---------------------------------------------------------------------------

def test_confine_allows_valid_session_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    path = _confine_research_path("rp-abc123de4567")
    assert path == (data_dir / "rp-abc123de4567.json").resolve()


@pytest.mark.parametrize("bad_id", [
    "../escape",
    "../../etc/passwd",
    "/etc/passwd",
    "safe/../../x",
    "",
    "rp_bad",          # underscore not in allowed charset
    "rp-bad.json",     # dot not in allowed charset
    "a" * 129,         # exceeds length limit
])
def test_confine_rejects_bad_session_ids(tmp_path, monkeypatch, bad_id):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    with pytest.raises(HTTPException) as exc:
        _confine_research_path(bad_id)
    assert exc.value.status_code == 400


def test_confine_rejects_symlink_escape(tmp_path, monkeypatch):
    """A symlink inside DEEP_RESEARCH_DIR that resolves outside is rejected."""
    data_dir = tmp_path / "deep_research"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    target = outside / "rp-linktest1234.json"
    target.write_text("{}", encoding="utf-8")
    link = data_dir / "rp-linktest1234.json"
    try:
        link.symlink_to(target)
    except (AttributeError, NotImplementedError, OSError) as e:
        pytest.skip(f"symlinks unavailable: {e}")
    with pytest.raises(HTTPException) as exc:
        _confine_research_path("rp-linktest1234")
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Route-level tests — valid paths work
# ---------------------------------------------------------------------------

def test_detail_returns_data_for_owner(tmp_path):
    data_dir = tmp_path / "deep_research"
    _write_research(data_dir, "rp-validid12345", owner="alice", query="valid query")
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    out = asyncio.run(target(session_id="rp-validid12345", request=_request("alice")))
    assert out["query"] == "valid query"


# ---------------------------------------------------------------------------
# Route-level tests — traversal and injection rejected
# ---------------------------------------------------------------------------

_TRAVERSAL_IDS = [
    "../escape",
    "../../etc/passwd",
    "/etc/passwd",
    "safe/../../x",
    "rp_under",
    "a" * 129,
]


@pytest.mark.parametrize("bad_id", _TRAVERSAL_IDS)
def test_detail_rejects_traversal(bad_id):
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id=bad_id, request=_request("alice")))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad_id", _TRAVERSAL_IDS)
def test_archive_rejects_traversal(bad_id):
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}/archive", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id=bad_id, request=_request("alice"), archived=True))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad_id", _TRAVERSAL_IDS)
def test_delete_rejects_traversal(bad_id):
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id=bad_id, request=_request("alice")))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Route-level tests — traversal does not touch files outside DEEP_RESEARCH_DIR
# ---------------------------------------------------------------------------

def test_delete_traversal_does_not_delete_outside_file(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "sensitive.json"
    outside.write_text('{"secret": true}', encoding="utf-8")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="../sensitive", request=_request("alice")))
    assert exc.value.status_code == 400
    assert outside.exists(), "file outside DEEP_RESEARCH_DIR must not be deleted"


def test_archive_traversal_does_not_mutate_outside_file(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "sensitive.json"
    outside.write_text('{"owner": "alice", "archived": false}', encoding="utf-8")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}/archive", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="../sensitive", request=_request("alice"), archived=True))
    assert exc.value.status_code == 400
    data = json.loads(outside.read_text(encoding="utf-8"))
    assert data["archived"] is False, "file outside DEEP_RESEARCH_DIR must not be mutated"


def test_detail_traversal_does_not_read_outside_file(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir(parents=True)
    outside = tmp_path / "sensitive.json"
    outside.write_text('{"owner": "alice", "result": "secret data"}', encoding="utf-8")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="../sensitive", request=_request("alice")))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Route-level symlink escape test
# ---------------------------------------------------------------------------

def test_detail_rejects_symlink_escape(tmp_path, monkeypatch):
    """research_detail rejects a confined-format ID whose JSON is a symlink to outside."""
    data_dir = tmp_path / "deep_research"
    outside_dir = tmp_path / "outside"
    data_dir.mkdir(parents=True)
    outside_dir.mkdir()
    outside_file = outside_dir / "rp-linktest5678.json"
    outside_file.write_text(
        json.dumps({"owner": "alice", "result": "secret"}), encoding="utf-8"
    )
    link = data_dir / "rp-linktest5678.json"
    try:
        link.symlink_to(outside_file)
    except (AttributeError, NotImplementedError, OSError) as e:
        pytest.skip(f"symlinks unavailable: {e}")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="rp-linktest5678", request=_request("alice")))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Owner/session scoping cannot escape root
# ---------------------------------------------------------------------------

def test_owner_scoped_paths_stay_within_research_root(tmp_path, monkeypatch):
    """Owner-scoped session IDs never produce paths outside DEEP_RESEARCH_DIR."""
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    root = data_dir.resolve()
    for session_id in ("rp-abc123456789", "rp-000000000001", "abc-xyz-123"):
        path = _confine_research_path(session_id)
        assert path.resolve().is_relative_to(root), (
            f"{session_id!r} produced path outside research root: {path}"
        )

@pytest.mark.parametrize("bad_id", _TRAVERSAL_IDS)
def test_result_peek_rejects_traversal(bad_id):
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/result-peek/{session_id}", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id=bad_id, request=_request("alice")))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("bad_id", _TRAVERSAL_IDS)
def test_spinoff_rejects_traversal(bad_id):
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/spinoff/{session_id}", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id=bad_id, request=_request("alice")))
    assert exc.value.status_code == 400

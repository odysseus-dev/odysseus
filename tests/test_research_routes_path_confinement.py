"""Path-confinement regression tests for research routes.

Covers the CodeQL py/path-injection alert cluster (#552-#567 and #594) in
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
from routes.research.research_routes import (
    _find_owned_research_path,
    _find_research_path,
    _require_research_path,
)


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
# Helper-level tests
# ---------------------------------------------------------------------------

def test_find_returns_existing_trusted_research_path(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    expected = _write_research(data_dir, "rp-abc123de4567", owner="alice")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    assert _find_research_path("rp-abc123de4567") == expected.resolve()


def test_find_returns_none_for_missing_valid_session_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    assert _find_research_path("rp-missing12345") is None


def test_require_returns_404_for_missing_valid_session_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))
    with pytest.raises(HTTPException) as exc:
        _require_research_path("rp-missing12345")
    assert exc.value.status_code == 404


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
def test_find_rejects_bad_session_ids_before_enumeration(monkeypatch, bad_id):
    storage_root = MagicMock()
    monkeypatch.setattr(
        "routes.research.research_routes._research_storage_root",
        MagicMock(return_value=storage_root),
    )
    with pytest.raises(HTTPException) as exc:
        _find_research_path(bad_id)
    assert exc.value.status_code == 400
    storage_root.glob.assert_not_called()


def test_find_matches_names_from_trusted_enumeration_without_joining_input(
    tmp_path, monkeypatch
):
    """Pin the CodeQL-friendly lookup: match a glob result, never root / input."""
    data_dir = tmp_path / "deep_research"
    expected = _write_research(data_dir, "rp-abc123de4567", owner="alice").resolve()

    class EnumeratedRoot:
        def glob(self, pattern):
            assert pattern == "*.json"
            return [expected]

        def __fspath__(self):
            return str(data_dir.resolve())

        def __truediv__(self, _other):
            raise AssertionError("user-derived path segment was joined to root")

    monkeypatch.setattr(
        "routes.research.research_routes._research_storage_root",
        lambda: EnumeratedRoot(),
    )
    assert _find_research_path("rp-abc123de4567") == expected


def test_find_ignores_symlink_escape(tmp_path, monkeypatch):
    """A matching symlink that resolves outside is not a trusted file."""
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
    assert _find_research_path("rp-linktest1234") is None



def test_find_owned_returns_path_for_matching_owner(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    expected = _write_research(data_dir, "rp-ownedalice1", owner="alice")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    assert _find_owned_research_path("rp-ownedalice1", "alice") == expected.resolve()


def test_find_owned_returns_none_for_other_owner(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    _write_research(data_dir, "rp-ownedbybob12", owner="bob")
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    assert _find_owned_research_path("rp-ownedbybob12", "alice") is None


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


def test_detail_returns_404_for_missing_valid_id():
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="rp-missing12345", request=_request("alice")))
    assert exc.value.status_code == 404


def test_detail_hides_other_owners_research_with_404(tmp_path):
    data_dir = tmp_path / "deep_research"
    _write_research(data_dir, "rp-ownedbybob12", owner="bob")
    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="rp-ownedbybob12", request=_request("alice")))
    assert exc.value.status_code == 404


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

def _write_outside_symlink(tmp_path, session_id: str, data: dict):
    data_dir = tmp_path / "deep_research"
    outside_dir = tmp_path / "outside"
    data_dir.mkdir(parents=True)
    outside_dir.mkdir()
    outside_file = outside_dir / f"{session_id}.json"
    outside_file.write_text(json.dumps(data), encoding="utf-8")
    link = data_dir / f"{session_id}.json"
    try:
        link.symlink_to(outside_file)
    except (AttributeError, NotImplementedError, OSError) as e:
        pytest.skip(f"symlinks unavailable: {e}")
    return data_dir, outside_file


def test_detail_rejects_symlink_escape(tmp_path, monkeypatch):
    """research_detail never reads a matching symlink outside the root."""
    data_dir, _ = _write_outside_symlink(
        tmp_path,
        "rp-linktest5678",
        {"owner": "alice", "result": "secret"},
    )
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="rp-linktest5678", request=_request("alice")))
    assert exc.value.status_code == 404


def test_archive_does_not_write_through_symlink_escape(tmp_path, monkeypatch):
    data_dir, outside_file = _write_outside_symlink(
        tmp_path,
        "rp-linkarchive1",
        {"owner": "alice", "archived": False},
    )
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}/archive", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            target(
                session_id="rp-linkarchive1",
                request=_request("alice"),
                archived=True,
            )
        )
    assert exc.value.status_code == 404
    assert json.loads(outside_file.read_text(encoding="utf-8"))["archived"] is False


def test_delete_does_not_unlink_symlink_escape(tmp_path, monkeypatch):
    data_dir, outside_file = _write_outside_symlink(
        tmp_path,
        "rp-linkdelete12",
        {"owner": "alice"},
    )
    link = data_dir / "rp-linkdelete12.json"
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")
    out = asyncio.run(
        target(session_id="rp-linkdelete12", request=_request("alice"))
    )
    assert out == {"deleted": False}
    assert link.is_symlink()
    assert outside_file.exists()


# ---------------------------------------------------------------------------
# Owner/session scoping cannot escape root
# ---------------------------------------------------------------------------

def test_owner_scoped_paths_stay_within_research_root(tmp_path, monkeypatch):
    """Owner-scoped persisted files resolve within DEEP_RESEARCH_DIR."""
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("routes.research_routes.DEEP_RESEARCH_DIR", str(data_dir))

    root = data_dir.resolve()
    for session_id in ("rp-abc123456789", "rp-000000000001", "abc-xyz-123"):
        _write_research(data_dir, session_id, owner="alice")
        path = _require_research_path(session_id)
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

def test_result_peek_uses_single_disk_lookup_for_completed_result(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    path = _write_research(
        data_dir,
        "rp-peeksingle1",
        owner="alice",
        result="saved result",
        sources=["s1"],
        raw_findings=["f1"],
        category="security",
    ).resolve()

    calls = []

    def fake_find_owned(session_id, user):
        calls.append((session_id, user))
        return path

    monkeypatch.setattr(
        "routes.research.research_routes._find_owned_research_path",
        fake_find_owned,
    )

    handler = _research_handler()
    handler.get_result.return_value = None
    router = setup_research_routes(handler)
    target = _route(router, "/api/research/result-peek/{session_id}", "POST")

    out = asyncio.run(target(session_id="rp-peeksingle1", request=_request("alice")))

    assert out["result"] == "saved result"
    assert out["sources"] == ["s1"]
    assert out["raw_findings"] == ["f1"]
    assert out["category"] == "security"
    assert calls == [("rp-peeksingle1", "alice")]


def test_spinoff_uses_single_disk_lookup_for_completed_result(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    path = _write_research(
        data_dir,
        "rp-spinsingle1",
        owner="alice",
        result="saved report",
        sources=["s1", "s2"],
        query="original query",
    ).resolve()

    calls = []

    def fake_find_owned(session_id, user):
        calls.append((session_id, user))
        return path

    class FakeSession:
        endpoint_url = ""
        model = ""
        headers = {}

        def __init__(self):
            self.messages = []

        def add_message(self, message):
            self.messages.append(message)

    class FakeSessionManager:
        def __init__(self):
            self.created = None

        def get_session(self, session_id):
            raise KeyError(session_id)

        def create_session(self, **kwargs):
            self.created = FakeSession()
            return self.created

        def save_sessions(self):
            pass

    monkeypatch.setattr(
        "routes.research.research_routes._find_owned_research_path",
        fake_find_owned,
    )
    monkeypatch.setattr(
        "routes.research.research_routes.resolve_endpoint",
        lambda *_args, **_kwargs: ("http://endpoint/v1", "model", {}),
    )

    handler = _research_handler()
    handler.get_result.return_value = None
    handler.get_sources.return_value = []
    session_manager = FakeSessionManager()
    router = setup_research_routes(handler, session_manager=session_manager)
    target = _route(router, "/api/research/spinoff/{session_id}", "POST")

    out = asyncio.run(target(session_id="rp-spinsingle1", request=_request("alice")))

    assert out["name"] == "Follow-up: original query"
    assert out["source_count"] == 2
    assert calls == [("rp-spinsingle1", "alice")]
    assert session_manager.created is not None
    assert session_manager.created.messages

def test_spinoff_reads_saved_query_for_done_active_task(tmp_path, monkeypatch):
    session_id = "rp-activedone1"
    data_dir = tmp_path / "deep_research"
    _write_research(
        data_dir,
        session_id,
        owner="alice",
        result="saved report",
        sources=["s1"],
        query="completed query",
    )

    class FakeSession:
        endpoint_url = ""
        model = ""
        headers = {}

        def __init__(self):
            self.messages = []

        def add_message(self, message):
            self.messages.append(message)

    class FakeSessionManager:
        def __init__(self):
            self.created = None

        def get_session(self, session_id):
            raise KeyError(session_id)

        def create_session(self, **kwargs):
            self.created = FakeSession()
            return self.created

        def save_sessions(self):
            pass

    monkeypatch.setattr(
        "routes.research.research_routes.resolve_endpoint",
        lambda *_args, **_kwargs: ("http://endpoint/v1", "model", {}),
    )

    handler = _research_handler()
    handler._active_tasks[session_id] = {"owner": "alice", "status": "done"}
    handler.get_result.return_value = None
    handler.get_sources.return_value = []

    session_manager = FakeSessionManager()
    router = setup_research_routes(handler, session_manager=session_manager)
    target = _route(router, "/api/research/spinoff/{session_id}", "POST")

    out = asyncio.run(target(session_id=session_id, request=_request("alice")))

    assert out["name"] == "Follow-up: completed query"
    assert out["source_count"] == 1
    assert session_manager.created is not None
    primer = session_manager.created.messages[0].content
    assert "completed query" in primer
    assert "(not recorded)" not in primer

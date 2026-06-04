"""Route-level owner-scope tests for persisted research reports."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import routes.research_routes as research_routes
from routes.research_routes import _resolve_research_endpoint, setup_research_routes


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


class _Predicate:
    def __init__(self, check):
        self._check = check

    def __call__(self, row):
        return self._check(row)

    def __or__(self, other):
        return _Predicate(lambda row: self(row) or other(row))


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return _Predicate(lambda row: getattr(row, self.name) == value)


class _ModelEndpoint:
    id = _Column("id")
    is_enabled = _Column("is_enabled")
    owner = _Column("owner")


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *predicates):
        self._rows = [
            row for row in self._rows
            if all(predicate(row) for predicate in predicates)
        ]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        assert model is _ModelEndpoint
        return _Query(self._rows)

    def close(self):
        pass


def _endpoint(eid, owner, *, base_url=None, model=None, api_key=None, is_enabled=True):
    return SimpleNamespace(
        id=eid,
        owner=owner,
        is_enabled=is_enabled,
        base_url=base_url or f"https://{eid}.example/v1",
        api_key=api_key or f"key-{eid}",
        cached_models=json.dumps([model or f"{eid}-chat"]),
    )


def test_library_returns_only_caller_owned_unarchived_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data" / "deep_research"
    _write_research(data_dir, "alice-live", owner="alice", query="Alice", completed_at=30)
    _write_research(data_dir, "alice-archived", owner="alice", query="Archived", archived=True)
    _write_research(data_dir, "bob-live", owner="bob", query="Bob", completed_at=40)
    _write_research(data_dir, "legacy-null", query="Legacy", completed_at=50)

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/library", "GET")

    out = asyncio.run(target(
        request=_request("alice"),
        search=None,
        sort="recent",
        limit=50,
        archived=False,
    ))

    assert [item["id"] for item in out["research"]] == ["alice-live"]
    assert out["total"] == 1


def test_detail_rejects_cross_owner_and_null_owner_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data" / "deep_research"
    _write_research(data_dir, "bob-report", owner="bob", result="bob secret")
    _write_research(data_dir, "legacy-report", result="legacy secret")

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/detail/{session_id}", "GET")

    for session_id in ("bob-report", "legacy-report"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(target(session_id=session_id, request=_request("alice")))
        assert exc.value.status_code == 404


def test_report_rejects_null_owner_before_generating_html(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data" / "deep_research"
    _write_research(data_dir, "legacy-report", result="legacy secret")

    handler = _research_handler()
    router = setup_research_routes(handler)
    target = _route(router, "/api/research/report/{session_id}", "GET")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="legacy-report", request=_request("alice")))

    assert exc.value.status_code == 404
    handler.get_report_html.assert_not_called()


def test_archive_rejects_cross_owner_without_mutating_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data" / "deep_research"
    path = _write_research(data_dir, "bob-report", owner="bob", archived=False)

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}/archive", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="bob-report", request=_request("alice"), archived=True))

    assert exc.value.status_code == 404
    assert json.loads(path.read_text(encoding="utf-8"))["archived"] is False


def test_delete_rejects_cross_owner_without_unlinking_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data" / "deep_research"
    path = _write_research(data_dir, "bob-report", owner="bob", result="bob secret")

    router = setup_research_routes(_research_handler())
    target = _route(router, "/api/research/{session_id}", "DELETE")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(target(session_id="bob-report", request=_request("alice")))

    assert exc.value.status_code == 404
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["result"] == "bob secret"


def test_resolve_research_endpoint_uses_session_owner(monkeypatch):
    calls = []

    def fake_resolve(setting_prefix, **kwargs):
        calls.append((setting_prefix, kwargs))
        return (
            "https://research.example/v1/chat/completions",
            "research-chat",
            {"Authorization": "Bearer key"},
        )

    monkeypatch.setattr(research_routes, "resolve_endpoint", fake_resolve)

    sess = SimpleNamespace(
        endpoint_url="https://fallback.example/v1/chat/completions",
        model="fallback-chat",
        headers={"X-Fallback": "1"},
        owner="alice",
    )

    assert _resolve_research_endpoint(sess) == (
        "https://research.example/v1/chat/completions",
        "research-chat",
        {"Authorization": "Bearer key"},
    )
    assert calls == [(
        "research",
        {
            "fallback_url": "https://fallback.example/v1/chat/completions",
            "fallback_model": "fallback-chat",
            "fallback_headers": {"X-Fallback": "1"},
            "owner": "alice",
        },
    )]


def test_spinoff_endpoint_fallback_is_owner_scoped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_id = "alice-report"
    _write_research(
        tmp_path / "data" / "deep_research",
        session_id,
        owner="alice",
        query="Alice research",
        result="Alice report body",
        sources=[],
    )

    resolve_calls = []

    def fake_resolve(setting_prefix, **kwargs):
        resolve_calls.append((setting_prefix, kwargs.get("owner")))
        return "", "", {}

    endpoints = [
        _endpoint(
            "bob", "bob",
            base_url="https://bob.example/v1",
            model="bob-chat",
            api_key="bob-key",
        ),
        _endpoint(
            "alice", "alice",
            base_url="https://alice.example/v1",
            model="alice-chat",
            api_key="alice-key",
        ),
    ]

    db_mod = sys.modules["src.database"]
    monkeypatch.setattr(db_mod, "ModelEndpoint", _ModelEndpoint, raising=False)
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _DB(endpoints), raising=False)
    monkeypatch.setattr(research_routes, "resolve_endpoint", fake_resolve)

    handler = _research_handler()
    handler.get_result.return_value = None
    handler.get_sources.return_value = []
    handler.get_raw_findings.return_value = []

    session_manager = MagicMock()
    session_manager.get_session.side_effect = KeyError(session_id)
    session_manager.create_session.return_value = SimpleNamespace(
        headers={},
        add_message=MagicMock(),
    )

    router = setup_research_routes(handler, session_manager=session_manager)
    target = _route(router, "/api/research/spinoff/{session_id}", "POST")

    out = asyncio.run(target(session_id=session_id, request=_request("alice")))

    assert out["session_id"]
    assert resolve_calls == [
        ("chat", "alice"),
        ("research", "alice"),
        ("utility", "alice"),
    ]
    kwargs = session_manager.create_session.call_args.kwargs
    assert kwargs["owner"] == "alice"
    assert kwargs["endpoint_url"] == "https://alice.example/v1/chat/completions"
    assert kwargs["model"] == "alice-chat"
    assert session_manager.create_session.return_value.headers == {"Authorization": "Bearer alice-key"}

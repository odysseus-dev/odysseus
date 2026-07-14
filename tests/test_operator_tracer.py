"""Tests for SpecTracer ingest: store, retrieval, retention, route, tool."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("sqlalchemy")


@pytest.fixture(autouse=True)
def _clean_traces():
    from core.database import OperatorTrace, SessionLocal

    def wipe():
        db = SessionLocal()
        try:
            db.query(OperatorTrace).delete()
            db.commit()
        finally:
            db.close()

    wipe()
    yield
    wipe()


def _bundle(label="Join Beta", url="https://app.test/landing"):
    return {
        "element": {"tag": "button"},
        "label": label,
        "classes": ["btn", "btn-primary"],
        "hierarchy": "body > main > .hero > button",
        "selector": ".hero > button.btn-primary",
        "position": {"x": 245, "y": 180},
        "page": {"url": url, "title": "Landing"},
        "console": [{"level": "error", "text": "TypeError: x is undefined"}],
        "bundle_version": "1",
    }


def test_store_and_get_latest_roundtrip():
    from services.operator.tracer import get_trace, store_trace

    stored = store_trace(_bundle())
    assert stored["trace_id"]

    result = get_trace()
    assert result["ok"] is True
    data = result["data"]
    assert data["trace_id"] == stored["trace_id"]
    assert data["page_url"] == "https://app.test/landing"
    assert "button.btn" in data["element"]
    assert data["bundle"]["selector"] == ".hero > button.btn-primary"
    assert data["bundle"]["console"][0]["text"].startswith("TypeError")


def test_get_by_id_and_missing_id():
    from services.operator.tracer import get_trace, store_trace

    stored = store_trace(_bundle())
    assert get_trace(stored["trace_id"])["ok"] is True

    missing = get_trace("nope")
    assert missing["ok"] is False
    assert missing["reason"] == "trace_not_found"


def test_no_traces_yet_is_structured():
    from services.operator.tracer import get_trace

    result = get_trace()
    assert result["ok"] is False
    assert result["reason"] == "no_traces"
    assert "SpecTracer" in result["hint"]


def test_oversized_bundle_rejected():
    from services.operator.tracer import store_trace

    big = _bundle()
    big["dom"] = "x" * (300 * 1024)
    with pytest.raises(ValueError, match="too_large"):
        store_trace(big)


def test_count_retention_keeps_newest():
    from services.operator.tracer import list_traces, store_trace

    with patch.dict("os.environ", {"OPERATOR_TRACE_MAX_COUNT": "2"}):
        store_trace(_bundle(label="first"))
        store_trace(_bundle(label="second"))
        store_trace(_bundle(label="third"))
        result = list_traces(limit=10)

    labels = [t["element"] for t in result["data"]["traces"]]
    assert result["data"]["count"] == 2
    assert any("third" in l for l in labels)
    assert not any("first" in l for l in labels)


def test_age_retention_purges_old_rows():
    from core.database import OperatorTrace, SessionLocal, utcnow_naive
    from services.operator.tracer import list_traces, store_trace

    stored = store_trace(_bundle(label="old"))
    db = SessionLocal()
    try:
        row = db.query(OperatorTrace).filter(OperatorTrace.id == stored["trace_id"]).one()
        row.created_at = utcnow_naive() - timedelta(hours=48)
        db.commit()
    finally:
        db.close()

    result = list_traces()
    assert result["data"]["count"] == 0


# ── ingest route ──

def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.operator_routes as operator_routes

    monkeypatch.setattr(operator_routes, "require_authenticated_request", lambda _r: "tester")
    app = FastAPI()
    app.include_router(operator_routes.setup_operator_routes())
    return TestClient(app)


def test_ingest_route_stores_and_returns_id(monkeypatch):
    # NOTE: store_trace is mocked here because the TestClient portal thread
    # would get its own empty :memory: SQLite DB (SingletonThreadPool).
    # Real-storage behavior is covered by the same-thread service tests above.
    pytest.importorskip("fastapi")
    import routes.operator_routes as operator_routes

    seen = {}

    def fake_store(bundle):
        seen["bundle"] = bundle
        return {"trace_id": "abc123"}

    monkeypatch.setattr(operator_routes, "store_trace", fake_store)
    client = _client(monkeypatch)

    response = client.post("/api/operator/spec-trace", json=_bundle())
    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "trace_id": "abc123"}
    assert seen["bundle"]["label"] == "Join Beta"


def test_ingest_route_rejects_oversized(monkeypatch):
    pytest.importorskip("fastapi")
    client = _client(monkeypatch)

    big = _bundle()
    big["dom"] = "x" * (300 * 1024)
    response = client.post("/api/operator/spec-trace", json=big)
    assert response.status_code == 413
    assert "reduce capture depth" in response.json()["detail"]


def test_ingest_route_rejects_non_object(monkeypatch):
    pytest.importorskip("fastapi")
    client = _client(monkeypatch)

    response = client.post(
        "/api/operator/spec-trace",
        content="not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


# ── agent tool ──

def test_do_spec_trace_latest_and_list():
    # Tracer functions are mocked: do_spec_trace runs them via asyncio.to_thread,
    # and a worker thread would see its own empty :memory: DB. Real storage is
    # covered by the same-thread service tests above.
    from src.tool_implementations import do_spec_trace

    calls = []

    def fake_get(trace_id=None):
        calls.append(("get", trace_id))
        return {"ok": True, "capability": "spec_tracer", "data": {"element": "pick-me"}, "degraded": False}

    def fake_list(limit=10):
        calls.append(("list", limit))
        return {"ok": True, "capability": "spec_tracer", "data": {"count": 1}, "degraded": False}

    with patch("services.operator.tracer.get_trace", fake_get):
        with patch("services.operator.tracer.list_traces", fake_list):
            latest = asyncio.run(do_spec_trace("{}"))
            listing = asyncio.run(do_spec_trace(json.dumps({"action": "list", "limit": 5})))

    assert latest["data"]["element"] == "pick-me"
    assert listing["data"]["count"] == 1
    assert calls == [("get", None), ("list", 5)]


def test_do_spec_trace_unknown_action():
    from src.tool_implementations import do_spec_trace

    result = asyncio.run(do_spec_trace(json.dumps({"action": "delete"})))
    assert result["exit_code"] == 1
    assert "Unknown action" in result["error"]


def test_spec_trace_registered():
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS}
    assert "spec_trace" in names
    assert "spec_trace" in TOOL_TAGS

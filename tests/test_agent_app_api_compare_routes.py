"""The agent's app_api guide must describe routes its JSON bridge can call."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_app_api_compare_guide_matches_live_router():
    from routes.compare.compare_routes import setup_compare_routes
    from src.agent_loop import TOOL_SECTIONS

    class _Sessions:
        pass

    live = {
        (method, route.path)
        for route in setup_compare_routes(_Sessions()).routes
        for method in getattr(route, "methods", set())
    }
    guide = TOOL_SECTIONS["app_api"]

    assert "/api/compare/sessions" not in guide
    assert ("GET", "/api/compare/history") in live
    assert ("POST", "/api/compare/record") in live
    assert ("POST", "/api/compare/{comp_id}/vote") in live
    assert ("DELETE", "/api/compare/{comp_id}") in live
    for path in (
        "/api/compare/history",
        "/api/compare/record",
        "/api/compare/{comp_id}/vote",
        "/api/compare/{comp_id}",
    ):
        assert path in guide


def test_app_api_guide_does_not_claim_json_bridge_can_start_compare():
    from src.agent_loop import TOOL_SECTIONS

    guide = TOOL_SECTIONS["app_api"]

    assert "`/api/compare/start` is a browser multipart/session-creation flow" in guide
    assert "use the Compare UI" in guide


def test_app_api_shaped_json_vote_reaches_compare_handler(monkeypatch):
    import routes.compare.compare_routes as compare_routes

    comparison = SimpleNamespace(
        id="cmp-1",
        owner=None,
        winner=None,
        blind_mapping=json.dumps({"left": "b", "right": "a"}),
        model_a="model-a",
        model_b="model-b",
        voted_at=None,
    )

    class _Query:
        def filter(self, *_args):
            return self

        def first(self):
            return comparison

    class _DB:
        committed = False
        closed = False

        def query(self, _model):
            return _Query()

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    db = _DB()
    monkeypatch.setattr(compare_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(compare_routes, "get_current_user", lambda _request: None)

    app = FastAPI()
    app.include_router(compare_routes.setup_compare_routes(SimpleNamespace()))
    bridge_call = {
        "action": "call",
        "method": "POST",
        "path": "/api/compare/cmp-1/vote",
        "body": {"winner": "left"},
    }
    response = TestClient(app).request(
        bridge_call["method"], bridge_call["path"], json=bridge_call["body"]
    )

    assert response.status_code == 200
    assert response.json() == {
        "winner": "b",
        "model_a": "model-a",
        "model_b": "model-b",
        "revealed": {"left": "model-b", "right": "model-a"},
    }
    assert comparison.winner == "b"
    assert db.committed is True
    assert db.closed is True

    comparison.winner = None
    db.committed = False
    db.closed = False
    form_response = TestClient(app).post(
        "/api/compare/cmp-1/vote", data={"winner": "right"}
    )
    assert form_response.status_code == 200
    assert form_response.json()["winner"] == "a"
    assert db.committed is True
    assert db.closed is True

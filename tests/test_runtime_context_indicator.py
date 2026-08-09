"""Regression coverage for the visible runtime-context indicator contract."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import APIRouter

import routes.session_routes as session_routes
import src.model_context as model_context


ROOT = Path(__file__).resolve().parents[1]


def _context_info_endpoint(monkeypatch, session):
    monkeypatch.setattr(
        session_routes,
        "router",
        APIRouter(prefix="/api", tags=["sessions"]),
    )
    monkeypatch.setattr(
        session_routes,
        "_verify_session_owner",
        lambda request, session_id: None,
    )
    manager = MagicMock()
    manager.get_session.return_value = session
    router = session_routes.setup_session_routes(manager, {})
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", "") == "/api/session/{session_id}/context_info"
    )


def test_context_info_reports_proven_ollama_runtime_budget(monkeypatch):
    endpoint = _context_info_endpoint(
        monkeypatch,
        SimpleNamespace(
            endpoint_url="http://host.docker.internal:11434/v1",
            model="qwen3-coder:30b",
        ),
    )
    monkeypatch.setattr(
        model_context,
        "get_context_length_known",
        lambda endpoint_url, model: (4096, True),
    )
    monkeypatch.setattr(
        model_context,
        "_local_ollama_ps_url",
        lambda endpoint_url: "http://host.docker.internal:11434/api/ps",
    )

    assert asyncio.run(endpoint(request=MagicMock(), session_id="session-1")) == {
        "context_length": 4096,
        "model": "qwen3-coder:30b",
        "budget_context": 4096,
        "context_source": "ollama_api_ps",
    }


def test_context_info_hides_unproven_context_from_the_ui(monkeypatch):
    endpoint = _context_info_endpoint(
        monkeypatch,
        SimpleNamespace(
            endpoint_url="http://host.docker.internal:11434/v1",
            model="qwen3-coder:30b",
        ),
    )
    monkeypatch.setattr(
        model_context,
        "get_context_length_known",
        lambda endpoint_url, model: (model_context.DEFAULT_CONTEXT, False),
    )

    assert asyncio.run(endpoint(request=MagicMock(), session_id="session-1")) == {
        "context_length": None,
        "model": "qwen3-coder:30b",
        "budget_context": 0,
        "context_source": None,
    }


def test_runtime_context_indicator_has_race_safe_session_wiring():
    sessions_js = (ROOT / "static" / "js" / "sessions.js").read_text(encoding="utf-8")
    style_css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "function _getRuntimeContextIndicator" in sessions_js
    assert "document.createElement('span')" in sessions_js
    assert "runtime-context-indicator" in sessions_js
    assert "function _refreshRuntimeContextIndicator" in sessions_js
    assert "/context_info" in sessions_js
    assert "navToken !== _sessionNavToken" in sessions_js
    assert "function _clearRuntimeContextIndicator" in sessions_js
    assert "context_source" in sessions_js
    assert ".runtime-context-indicator" in style_css
    assert ".runtime-context-indicator[hidden]" in style_css

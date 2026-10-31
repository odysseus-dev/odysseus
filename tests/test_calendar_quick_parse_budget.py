"""Behavioral regression tests for the quick_parse LLM budget (#6432 bug 2).

``quick_parse`` hard-coded ``max_tokens=512`` / ``timeout=20``. With a
reasoning model as the utility endpoint, thinking tokens exhausted the 512
budget and the JSON answer truncated mid-object, so Quick Add failed with
"Could not extract JSON" while the endpoint still returned HTTP 200. This
pins the reporter-verified budget (6144 tokens / 60s) that lets reasoning
models answer, per the focused-PR split in #6432 (bug 1 stays with #6332).

Drives the real calendar router through ASGI with the LLM call stubbed and
asserts on the kwargs the route actually sends - behavior, not source text.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import httpx
from fastapi import FastAPI

from routes.calendar_routes import setup_calendar_routes

EVENT_JSON = (
    '{"summary": "Lunch with Sara", "dtstart": "2026-10-09T13:00:00", '
    '"dtend": "2026-10-09T14:00:00", "all_day": false, '
    '"location": "Downtown", "description": "", "confidence": 0.9}'
)


def _make_app():
    app = FastAPI()
    app.include_router(setup_calendar_routes())
    return app


def _stub_llm(monkeypatch, sent, response):
    async def fake_llm_call_async(**kwargs):
        sent.update(kwargs)
        return response

    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_endpoint",
        lambda purpose, owner=None: ("http://stub/v1", "stub-model", {}),
    )
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)


async def test_quick_parse_sends_reasoning_model_budget(monkeypatch):
    """The route must budget 6144 tokens / 60s for the utility model."""
    sent = {}
    _stub_llm(monkeypatch, sent, EVENT_JSON)

    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/api/calendar/quick-parse",
            json={"text": "lunch with Sara friday 1pm downtown", "tz": "UTC"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True, body
    assert body["event"]["summary"] == "Lunch with Sara"
    # The regression pin: 512 was consumed by reasoning tokens and the
    # JSON answer truncated; 20s was tight for observed 4-19s parses.
    assert sent.get("max_tokens") == 6144
    assert sent.get("timeout") == 60


async def test_quick_parse_reports_unparseable_llm_output(monkeypatch):
    """A truncated answer still surfaces the explicit failure shape."""
    sent = {}
    _stub_llm(monkeypatch, sent, '{"summary": "truncated mid obj')

    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/api/calendar/quick-parse",
            json={"text": "lunch with Sara", "tz": "UTC"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] == "Could not extract JSON"

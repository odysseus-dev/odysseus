"""Regression tests for GET /api/test/youtube diagnostics behavior."""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("starlette.testclient")

from fastapi import FastAPI, Request
from starlette.testclient import TestClient

diag = pytest.importorskip("routes.diagnostics_routes")


def _client(monkeypatch, transcript):
    def gate(_request: Request):
        return None

    async def fake_extract_transcript(_url, _video_id):
        return {"success": True, "transcript": transcript}

    monkeypatch.setattr(diag, "require_admin", gate)
    monkeypatch.setattr(diag, "extract_youtube_id", lambda _url: "video123")
    monkeypatch.setattr(diag, "extract_transcript_async", fake_extract_transcript)

    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(
        rag_manager=None,
        rag_available=False,
        research_handler=None,
        memory_vector=None,
    ))
    return TestClient(app, raise_server_exceptions=False)


def test_youtube_diagnostics_normalizes_nonstring_transcript(monkeypatch):
    client = _client(monkeypatch, ["chunk"] * 501)

    response = client.get("/api/test/youtube?url=https://youtu.be/video123")

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == "video123"
    assert body["transcript_success"] is True
    assert body["error"] is None
    assert isinstance(body["transcript_preview"], str)
    assert body["transcript_preview"].endswith("...")
    assert body["transcript_length"] > 500

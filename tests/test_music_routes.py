"""Music routes request body binding and scope regression tests."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.music_routes import setup_music_routes


@pytest.fixture(autouse=True)
def isolated_music_state(tmp_path, monkeypatch):
    """Do not read the operator's data/music_state.json or library in tests."""
    from services.music import music_service as ms

    root = tmp_path / "Music"
    root.mkdir()
    monkeypatch.setattr(ms, "MUSIC_ROOT", root)
    monkeypatch.setattr(ms, "_MUSIC_STATE_FILE", tmp_path / "music_state.json")
    monkeypatch.setattr(ms, "_MUSIC_FAVORITES_FILE", tmp_path / "music_favorites.json")
    monkeypatch.setattr(ms, "_DOWNLOAD_TASKS_FILE", tmp_path / "music_download_tasks.json")
    monkeypatch.setattr(ms, "_MUSIC_QUALITY_CACHE_FILE", tmp_path / "music_quality_cache.json")
    yield root


def test_music_scope_accepts_json_body():
    """Nested Pydantic models inside setup_music_routes broke JSON body binding (422 query/body)."""
    app = FastAPI()
    app.include_router(setup_music_routes())
    client = TestClient(app)

    # Clearing scope via null
    res = client.post("/api/music/scope", json={"scope": None})
    assert res.status_code == 200
    data = res.json()
    assert data.get("scope") is None
    assert "status" in data


def test_music_reveal_and_equalizer_accept_json_body():
    app = FastAPI()
    app.include_router(setup_music_routes())
    client = TestClient(app)

    res_rev = client.post("/api/music/reveal", json={"path": ""})
    assert res_rev.status_code in (200, 400, 404)

    res_eq = client.post("/api/music/equalizer", json={"preset": "flat"})
    assert res_eq.status_code in (200, 400)


def test_music_stream_endpoint(tmp_path, monkeypatch):
    from services.music import music_service as ms

    app = FastAPI()
    app.include_router(setup_music_routes())
    client = TestClient(app)

    dummy_flac = tmp_path / "test.flac"
    dummy_flac.write_bytes(b"FLAC" + b"\x00" * 100)
    monkeypatch.setattr(ms, "_current_track_path", lambda: dummy_flac)

    res = client.get("/api/music/stream")
    assert res.status_code == 200
    assert res.headers.get("accept-ranges") == "bytes"
    assert "audio/" in res.headers.get("content-type", "")

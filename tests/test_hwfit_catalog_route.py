"""Route tests for the DB-backed hwfit model catalog."""

from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _cpu_system():
    return {
        "has_gpu": False,
        "backend": "cpu_x86",
        "gpu_name": None,
        "gpu_vram_gb": 0,
        "gpu_count": 0,
        "available_ram_gb": 32.0,
        "total_ram_gb": 32.0,
    }


def _client():
    from routes.hwfit_routes import setup_hwfit_routes

    app = FastAPI()
    app.include_router(setup_hwfit_routes())
    return TestClient(app)


def test_hwfit_models_returns_db_refreshed_catalog_row(monkeypatch):
    """A model persisted by catalog refresh is visible through /api/hwfit/models."""
    catalog = [{
        "name": "hf/example-refresh-7b",
        "provider": "hf",
        "parameter_count": "7B",
        "pipeline_tag": "text-generation",
        "gguf_sources": [{"repo": "hf/example-refresh-7b-GGUF", "kind": "GGUF"}],
        "_source": "hf_trending",
        "source": "hf_trending",
    }]

    monkeypatch.setattr("services.hwfit.hardware.detect_system", lambda **kw: _cpu_system())
    monkeypatch.setattr("services.hwfit.models.get_models", lambda: [{"name": "stub"}])
    monkeypatch.setattr("services.hwfit.catalog_sync.get_catalog_or_static", lambda seed_if_empty=True: catalog)
    monkeypatch.setattr("services.hwfit.local_scanner.scan_local_gguf", lambda: [])
    monkeypatch.setattr("services.hwfit.lmstudio_catalog.fetch_lmstudio_models", lambda host="": [])
    monkeypatch.setattr("services.hwfit.ollama_catalog.fetch_ollama_models", lambda host="": [])

    response = _client().get("/api/hwfit/models?search=example-refresh&limit=5")

    assert response.status_code == 200
    names = [model["name"] for model in response.json()["models"]]
    assert "hf/example-refresh-7b" in names


def test_hwfit_models_persists_live_local_scan(monkeypatch):
    """Live local scan rows are upserted while serving the hwfit route."""
    local_entry = {
        "name": "local/test-route-4B-Q4_K_M",
        "provider": "Local GGUF",
        "parameter_count": "4B",
        "quantization": "Q4_K_M",
        "quant": "Q4_K_M",
        "is_gguf": True,
        "gguf_sources": [{"path": "D:/Models/test-route-4B-Q4_K_M.gguf", "kind": "GGUF"}],
        "backend": "llamacpp",
        "context_length": 4096,
        "local_path": "D:/Models/test-route-4B-Q4_K_M.gguf",
        "_source": "local_gguf",
        "source": "local_gguf",
    }
    captured = {}

    @contextmanager
    def _fake_db_session():
        yield object()

    def _capture_upsert(db, entries, source=None):
        captured["entries"] = list(entries)
        captured["source"] = source
        return []

    monkeypatch.setattr("services.hwfit.hardware.detect_system", lambda **kw: _cpu_system())
    monkeypatch.setattr("services.hwfit.models.get_models", lambda: [{"name": "stub"}])
    monkeypatch.setattr("services.hwfit.catalog_sync.get_catalog_or_static", lambda seed_if_empty=True: [])
    monkeypatch.setattr("services.hwfit.catalog_sync.upsert_discovered_models", _capture_upsert)
    monkeypatch.setattr("core.database.get_db_session", _fake_db_session)
    monkeypatch.setattr("services.hwfit.local_scanner.scan_local_gguf", lambda: [local_entry])
    monkeypatch.setattr("services.hwfit.lmstudio_catalog.fetch_lmstudio_models", lambda host="": [])
    monkeypatch.setattr("services.hwfit.ollama_catalog.fetch_ollama_models", lambda host="": [])

    response = _client().get("/api/hwfit/models?search=test-route&limit=5")

    assert response.status_code == 200
    assert captured["entries"] == [local_entry]
    assert captured["source"] is None

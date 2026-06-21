"""Route tests for the inline RAM-budget override (the editable RAM chip).

The override raises/lowers the available-RAM budget the ranker uses WITHOUT
faking the detected GPU, so speed estimates keep the real card's bandwidth.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _gpu_system():
    """A small-VRAM GPU box with little free RAM at scan time — the case the
    override exists for (probe sees live free RAM, not the real ceiling)."""
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA Test GPU 4GB",
        "gpu_vram_gb": 4.0,
        "gpu_count": 1,
        "gpus": [{"index": 0, "name": "NVIDIA Test GPU 4GB", "vram_gb": 4.0}],
        "gpu_groups": [{"name": "NVIDIA Test GPU 4GB", "vram_each": 4.0, "count": 1, "indices": [0], "vram_total": 4.0}],
        "available_ram_gb": 16.0,
        "total_ram_gb": 40.0,
    }


def _client():
    from routes.hwfit_routes import setup_hwfit_routes

    app = FastAPI()
    app.include_router(setup_hwfit_routes())
    return TestClient(app)


def _stub_catalog(monkeypatch, catalog):
    monkeypatch.setattr("services.hwfit.hardware.detect_system", lambda **kw: _gpu_system())
    monkeypatch.setattr("services.hwfit.models.get_models", lambda: [{"name": "stub"}])
    monkeypatch.setattr("services.hwfit.catalog_sync.get_catalog_or_static", lambda seed_if_empty=True: catalog)
    monkeypatch.setattr("services.hwfit.local_scanner.scan_local_gguf", lambda: [])
    monkeypatch.setattr("services.hwfit.lmstudio_catalog.fetch_lmstudio_models", lambda host="": [])
    monkeypatch.setattr("services.hwfit.ollama_catalog.fetch_ollama_models", lambda host="": [])


# A ~40B dense GGUF: ~23 GB at Q4_K_M — too tight for 16 GB free, fits in 32 GB.
_BIG = [{
    "name": "vendor/big-40b",
    "provider": "vendor",
    "parameter_count": "40B",
    "parameters_raw": 40000000000,
    "quantization": "Q4_K_M",
    "context_length": 4096,
    "pipeline_tag": "text-generation",
    "gguf_sources": [{"repo": "vendor/big-40b-GGUF", "kind": "GGUF"}],
}]


def test_override_raises_budget_and_keeps_gpu(monkeypatch):
    _stub_catalog(monkeypatch, _BIG)
    data = _client().get("/api/hwfit/models?override_ram_gb=32&ctx=4096&limit=5").json()
    sys = data["system"]
    # Budget replaced with the override; detected GPU left intact.
    assert sys["available_ram_gb"] == 32.0
    assert sys["ram_override_gb"] == 32.0
    assert sys["has_gpu"] is True
    assert sys["gpu_vram_gb"] == 4.0
    assert "Test GPU" in sys["gpu_name"]
    # True installed RAM exposed for the slider cap.
    assert sys["detected_ram_total_gb"] == 40.0


def test_override_changes_fit_outcome(monkeypatch):
    _stub_catalog(monkeypatch, _BIG)
    base = _client().get("/api/hwfit/models?ctx=4096&limit=5").json()["models"][0]
    bumped = _client().get("/api/hwfit/models?override_ram_gb=32&ctx=4096&limit=5").json()["models"][0]
    # Too tight against 16 GB free, but runnable once the budget is raised to 32.
    assert base["fit_level"] == "too_tight"
    assert bumped["fit_level"] != "too_tight"
    assert bumped["run_mode"] == "cpu_offload"


def test_no_override_leaves_detected_budget(monkeypatch):
    _stub_catalog(monkeypatch, _BIG)
    sys = _client().get("/api/hwfit/models?ctx=4096&limit=5").json()["system"]
    assert sys["available_ram_gb"] == 16.0
    assert "ram_override_gb" not in sys
    assert sys["detected_ram_total_gb"] == 40.0


def test_override_ignored_when_blank_or_invalid(monkeypatch):
    _stub_catalog(monkeypatch, _BIG)
    for q in ("override_ram_gb=", "override_ram_gb=abc", "override_ram_gb=0", "override_ram_gb=-5"):
        sys = _client().get(f"/api/hwfit/models?{q}&ctx=4096&limit=5").json()["system"]
        assert sys["available_ram_gb"] == 16.0
        assert "ram_override_gb" not in sys

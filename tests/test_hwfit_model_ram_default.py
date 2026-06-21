"""Per-model default RAM budget.

A heavy model can declare (or be hardcoded with) the RAM budget it should be
ranked against by default, so it doesn't read as too_tight against whatever
memory is momentarily free. The explicit global RAM override still wins; models
without a default keep tracking live free RAM dynamically.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.hwfit import fit


def _system(free_ram, total_ram=40.0):
    """Small-VRAM laptop with little momentary free RAM."""
    return {
        "has_gpu": True, "backend": "cuda",
        "gpu_name": "NVIDIA Test GPU 4GB",
        "gpu_vram_gb": 4.0, "gpu_count": 1,
        "available_ram_gb": free_ram, "total_ram_gb": total_ram,
        "detected_ram_total_gb": total_ram,
    }


# ~26 B dense GGUF needing ~16 GB at Q4_K_M: too tight at 12 GB free, fine at 20.
def _model(name, **extra):
    m = {
        "name": name, "provider": "vendor", "parameter_count": "26B",
        "parameters_raw": 26000000000, "quantization": "Q4_K_M",
        "context_length": 16384, "gguf_sources": [{"repo": name + "-GGUF"}],
    }
    m.update(extra)
    return m


def test_hardcoded_default_applies_with_low_free_ram():
    """A name in DEFAULT_RAM_BUDGET_GB ranks against its budget, not live free."""
    name = next(iter(fit.DEFAULT_RAM_BUDGET_GB))
    budget = fit.DEFAULT_RAM_BUDGET_GB[name]
    r = fit.analyze_model(_model(name), _system(free_ram=12.0), target_context=4096)
    assert r["ram_budget_gb"] == budget        # ranked at the default, not 12
    assert r["fit_level"] != "too_tight"
    assert r["run_mode"] == "cpu_offload"


def test_declared_field_takes_precedence():
    """An explicit default_ram_budget_gb field beats the hardcoded map / live RAM."""
    r = fit.analyze_model(
        _model("vendor/declared-26b", default_ram_budget_gb=24.0),
        _system(free_ram=10.0), target_context=4096,
    )
    assert r["ram_budget_gb"] == 24.0


def test_model_without_default_tracks_live_free_ram():
    """No default → budget is exactly the (dynamic) live free RAM."""
    r = fit.analyze_model(_model("vendor/plain-26b"), _system(free_ram=22.0), target_context=4096)
    assert r["ram_budget_gb"] == 22.0


def test_global_override_beats_per_model_default():
    """An explicit RAM override (the chip) wins over the model default."""
    name = next(iter(fit.DEFAULT_RAM_BUDGET_GB))
    sysd = _system(free_ram=12.0)
    sysd["ram_override_gb"] = 30.0
    sysd["available_ram_gb"] = 30.0
    r = fit.analyze_model(_model(name), sysd, target_context=4096)
    assert r["ram_budget_gb"] == 30.0


def test_default_capped_at_installed_ram():
    """The default can't claim more RAM than is physically installed."""
    r = fit.analyze_model(
        _model("vendor/huge", default_ram_budget_gb=64.0),
        _system(free_ram=8.0, total_ram=16.0), target_context=4096,
    )
    assert r["ram_budget_gb"] == 16.0


def test_serve_profiles_use_model_default_budget(monkeypatch):
    """/api/hwfit/profiles ranks the recommended -ngl against the model's default
    RAM budget (20 GB for the 26B), so the serve panel agrees with What Fits."""
    from routes.hwfit_routes import setup_hwfit_routes

    name = next(iter(fit.DEFAULT_RAM_BUDGET_GB))
    model = _model(name, is_moe=True, active_parameters=3800000000)
    monkeypatch.setattr(
        "services.hwfit.hardware.detect_system",
        lambda **kw: _system(free_ram=10.0),  # little free RAM at scan time
    )
    monkeypatch.setattr("services.hwfit.models.get_models", lambda: [model])

    app = FastAPI(); app.include_router(setup_hwfit_routes())
    data = TestClient(app).get(f"/api/hwfit/profiles?model={name}").json()

    assert data["system"]["ram_budget_gb"] == fit.DEFAULT_RAM_BUDGET_GB[name]
    assert data["system"]["available_ram_gb"] == fit.DEFAULT_RAM_BUDGET_GB[name]
    assert "recommended_ngl" in data

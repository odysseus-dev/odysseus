"""Manual hardware simulator backend handling (Cookbook "what if I had…").

`_apply_manual_hardware` replaces detected hardware with a user-described box so
the Cookbook can rank models against hardware you don't have yet. These pin that
the accepted backends stay in lock-step with what services.hwfit.fit can rank —
notably that "metal" is honoured (Apple Silicon is GGUF-only via llama.cpp /
Ollama) instead of being silently coerced to CUDA.
"""

from routes.hwfit_routes import _apply_manual_hardware, _MANUAL_BACKENDS
from services.hwfit.fit import rank_models
from services.hwfit.models import get_models


def test_no_manual_mode_leaves_system_untouched():
    base = {"backend": "cuda", "gpu_vram_gb": 24.0, "has_gpu": True}
    assert _apply_manual_hardware(dict(base), manual_mode="") == base
    assert _apply_manual_hardware(dict(base), manual_mode="bogus") == base


def test_manual_metal_backend_is_accepted():
    """The whole point of this change: 'metal' must survive instead of being
    rewritten to 'cuda', so the simulated Mac ranks through the Apple path."""
    s = _apply_manual_hardware({}, manual_mode="gpu", manual_vram_gb="24", manual_backend="metal")
    assert s["backend"] == "metal"
    assert s["unified_memory"] is True
    assert s["has_gpu"] is True
    assert "METAL" in s["gpu_name"]


def test_manual_metal_vram_and_count_math():
    s = _apply_manual_hardware({}, manual_mode="gpu", manual_gpu_count="2", manual_vram_gb="24", manual_backend="metal")
    assert s["gpu_count"] == 2
    assert s["gpu_vram_gb"] == 48.0
    assert len(s["gpus"]) == 2
    grp = s["gpu_groups"][0]
    assert grp["vram_each"] == 24.0
    assert grp["count"] == 2
    assert grp["vram_total"] == 48.0


def test_manual_backend_whitelist_matches_fit_backends():
    """Guard against drift: every manual backend must be one fit.py understands."""
    assert _MANUAL_BACKENDS == {"cuda", "rocm", "metal", "cpu_x86", "cpu_arm"}


def test_unknown_manual_backend_falls_back_to_cuda():
    s = _apply_manual_hardware({}, manual_mode="gpu", manual_backend="tpu")
    assert s["backend"] == "cuda"
    assert "unified_memory" not in s


def test_manual_rocm_and_cuda_are_not_unified_memory():
    for backend in ("cuda", "rocm"):
        s = _apply_manual_hardware({"unified_memory": True}, manual_mode="gpu", manual_backend=backend)
        assert s["backend"] == backend
        # Discrete GPUs are not unified memory — a stale flag must be cleared.
        assert "unified_memory" not in s


def test_manual_ram_mode_wipes_gpu_and_unified_flag():
    s = _apply_manual_hardware({"unified_memory": True}, manual_mode="ram", manual_ram_gb="64")
    assert s["has_gpu"] is False
    assert s["backend"] == "cpu_x86"
    assert s["gpu_vram_gb"] == 0
    assert s["total_ram_gb"] == 64.0
    assert "unified_memory" not in s


def test_simulated_metal_box_only_recommends_gguf_or_mlx():
    """End-to-end: a simulated Metal box must rank exactly like a real Mac —
    only locally servable GGUF or MLX models survive."""
    system = _apply_manual_hardware(
        {"backend": "cuda", "available_ram_gb": 32.0, "total_ram_gb": 64.0},
        manual_mode="gpu", manual_vram_gb="48", manual_backend="metal",
    )
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(system, limit=900)
        if not (str(r.get("quant", "")).startswith("mlx-")
                or str(r.get("name", "")).startswith(("mlx-community/", "lmstudio-community/"))
                or catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-servable models on simulated Metal, e.g. {unservable[:3]}"

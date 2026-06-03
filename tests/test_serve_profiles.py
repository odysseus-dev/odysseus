"""Intelligent llama.cpp serve profiles computed from hardware.

Locks in that compute_serve_profiles() turns detected VRAM + model size into
sane Quality/Balanced/Speed flag sets: a too-big MoE offloads experts to CPU
(n_cpu_moe > 0) instead of failing, a model that fits stays fully on GPU
(n_cpu_moe == 0), context shrinks before giving up, and quant choice tracks the
profile intent.
"""

from services.hwfit.profiles import compute_serve_profiles

_QWEN_35B_MOE = {
    "name": "Qwen3.6-35B-A3B",
    "parameter_count": "35B",
    "is_moe": True,
    "active_parameters": 3_000_000_000,
    "num_hidden_layers": 48,
}
_DENSE_8B = {
    "name": "Qwen3-8B",
    "parameter_count": "8B",
    "is_moe": False,
    "num_hidden_layers": 36,
}


def _sys(vram, family="rdna"):
    return {"backend": "rocm", "gpu_vram_gb": vram, "gpu_family": family}


def test_big_moe_on_small_card_offloads_not_fails():
    """A 35B MoE can't hold its weights on 16 GB, so the Quality profile must
    offload experts to CPU (n_cpu_moe > 0) rather than be dropped."""
    profs = compute_serve_profiles(_sys(15.9), _QWEN_35B_MOE)
    assert profs, "expected at least one profile"
    q = next(p for p in profs if p["key"] == "quality")
    assert q["n_cpu_moe"] > 0
    assert q["offloads"] is True
    assert q["cache_type"] == "q8_0"          # quality uses the sharp KV cache
    assert q["est_vram_gb"] <= 16.0           # never exceeds the card


def test_profiles_never_exceed_vram():
    """Every profile's VRAM estimate must fit the detected card."""
    for vram in (8.0, 12.0, 16.0, 24.0):
        for p in compute_serve_profiles(_sys(vram), _QWEN_35B_MOE):
            assert p["est_vram_gb"] <= vram + 0.05, (vram, p)


def test_small_model_stays_fully_on_gpu():
    """A model whose weights fit must NOT offload — n_cpu_moe == 0 everywhere."""
    for p in compute_serve_profiles(_sys(15.9), _DENSE_8B):
        assert p["n_cpu_moe"] == 0
        assert p["offloads"] is False


def test_speed_profile_is_lighter_than_quality():
    """Speed trades quant/context for less offload than Quality."""
    profs = {p["key"]: p for p in compute_serve_profiles(_sys(15.9), _QWEN_35B_MOE)}
    if "speed" in profs and "quality" in profs:
        assert profs["speed"]["n_cpu_moe"] <= profs["quality"]["n_cpu_moe"]
        assert profs["speed"]["ctx"] <= profs["quality"]["ctx"]


def test_flags_are_launchable():
    """Each profile must carry the concrete llama.cpp flags the cmd builder needs."""
    for p in compute_serve_profiles(_sys(15.9), _QWEN_35B_MOE):
        assert p["n_gpu_layers"] == 999
        assert isinstance(p["n_cpu_moe"], int) and p["n_cpu_moe"] >= 0
        assert p["cache_type"] in ("q4_0", "q8_0", "f16")
        assert p["ctx"] >= 8192
        assert p["quant"]


def test_context_capped_at_model_limit():
    """Profiles must never propose more context than the model was trained for
    — over-asking triggers a training-context overflow and, with a quantized KV
    cache, a GPU OOM/device-lost crash."""
    small_ctx_model = dict(_QWEN_35B_MOE, name="X", context_length=32768)
    for p in compute_serve_profiles(_sys(15.9), small_ctx_model):
        assert p["ctx"] <= 32768, p


def test_no_gpu_returns_empty():
    """No VRAM detected → no GPU profiles (caller falls back to manual flags)."""
    assert compute_serve_profiles({"backend": "cpu_x86", "gpu_vram_gb": 0}, _QWEN_35B_MOE) == []


def test_vision_model_leaves_encoder_headroom():
    """A vision model must budget extra VRAM for the image encoder, so its
    estimate leaves more slack below the card than a text model would."""
    vis = dict(_QWEN_35B_MOE, name="Qwen3-VL-35B", is_multimodal=True)
    for p in compute_serve_profiles(_sys(15.9), vis):
        assert p["est_vram_gb"] <= 15.9 - 1.0 + 0.05  # ~1.1 GB encoder headroom


def test_serve_mode_keeps_fixed_quant():
    """Serving a specific GGUF file: the quant is fixed (the file's), so every
    profile must keep it and vary only the serving knobs (KV/ctx/offload) — not
    propose a different quant (which makes no sense for an on-disk file)."""
    profs = compute_serve_profiles(_sys(15.9), _QWEN_35B_MOE,
                                   serve_weights_gb=20.6, serve_quant="Q4_K_M")
    assert profs
    assert all(p["quant"] == "Q4_K_M" for p in profs), [p["quant"] for p in profs]
    # The knobs should still differ across profiles (KV type and/or context).
    kvs = {p["cache_type"] for p in profs}
    ctxs = {p["ctx"] for p in profs}
    assert len(kvs) > 1 or len(ctxs) > 1, "serve profiles are identical"
    # All must fit the card.
    assert all(p["est_vram_gb"] <= 16.0 for p in profs)

"""AMD ROCm support for Cookbook hardware-fit.

Consumer AMD Radeon (RDNA: gfx10/11/12) can realistically only serve GGUF via
llama.cpp — vLLM/SGLang on ROCm are validated for datacenter Instinct (CDNA,
gfx9xx), not consumer cards, where AWQ kernels are largely unsupported and FP8
needs out-of-tree patches. These tests lock in that consumer RDNA is treated
like Apple Silicon (GGUF-only recommendations) while datacenter CDNA and
unknown-family AMD are left untouched, and that CUDA is unchanged.
"""

from services.hwfit import hardware
from services.hwfit.fit import rank_models
from services.hwfit.models import get_models


def _rocm_system(family="rdna", ram_gb=32.0, vram_gb=16.0):
    return {
        "has_gpu": True,
        "backend": "rocm",
        "gpu_name": "AMD Radeon RX 9060 XT" if family == "rdna" else "AMD Instinct MI300X",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.7,
        "total_ram_gb": ram_gb,
        "gpu_arch": "gfx1200" if family == "rdna" else "gfx942",
        "gpu_family": family,
    }


def _cuda_system():
    return {
        "has_gpu": True, "backend": "cuda", "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0, "gpu_count": 1, "available_ram_gb": 32.0, "total_ram_gb": 64.0,
    }


def test_only_gguf_models_recommended_on_consumer_rdna():
    """llama.cpp (GGUF) is the servable path on consumer Radeon, so every model
    recommended on RDNA must ship a real GGUF — no vLLM-only AWQ/GPTQ/FP8."""
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(_rocm_system(family="rdna"), limit=900)
        if not (catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-GGUF models on RDNA, e.g. {unservable[:3]}"


def test_safetensors_models_still_recommended_on_cdna():
    """Datacenter Instinct (CDNA) runs vLLM/SGLang on ROCm fine, so non-GGUF
    repos must NOT be filtered there — the GGUF-only rule is consumer-RDNA only."""
    names = {r["name"] for r in rank_models(_rocm_system(family="cdna"), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_unknown_amd_family_not_filtered():
    """When rocminfo is unavailable (family 'unknown'), don't hide non-GGUF
    models — a possibly-capable Instinct box shouldn't lose models on misdetect."""
    names = {r["name"] for r in rank_models(_rocm_system(family="unknown"), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_safetensors_models_still_recommended_on_cuda():
    """Regression guard: the GGUF-only rule must not leak onto CUDA."""
    names = {r["name"] for r in rank_models(_cuda_system(), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_classify_amd_gfx_rdna_vs_cdna():
    """classify_amd_gfx maps gfx targets to the right family: consumer RDNA
    (gfx10/11/12) vs datacenter CDNA (gfx9xx Instinct) vs older GCN."""
    cases = {
        "gfx1200": "rdna",   # RX 9060 XT (RDNA4)
        "gfx1201": "rdna",   # RX 9070 (RDNA4)
        "gfx1100": "rdna",   # RX 7900 (RDNA3)
        "gfx1030": "rdna",   # RX 6800 (RDNA2)
        "gfx942": "cdna",    # MI300 (CDNA3)
        "gfx950": "cdna",    # MI350 (CDNA4)
        "gfx90a": "cdna",    # MI200 (CDNA2)
        "gfx908": "cdna",    # MI100 (CDNA1)
        "gfx906": "gcn",     # Radeon VII / MI50 (GCN5/Vega)
        "": "unknown",
        "gfx": "unknown",
    }
    for gfx, expected_family in cases.items():
        out_gfx, family = hardware.classify_amd_gfx(gfx)
        assert family == expected_family, f"{gfx} -> {family}, expected {expected_family}"
        if expected_family != "unknown":
            assert out_gfx == gfx


def test_detect_amd_reports_family(monkeypatch):
    """_detect_amd surfaces gpu_family from rocminfo so fit/serve can branch on
    consumer-RDNA vs datacenter-CDNA. rocminfo lists the CPU agent first, then
    the GPU's gfx target. Drive it through the remote-read path (no real sysfs)."""
    rocminfo_out = "  Name:  AMD Ryzen 7 3700X\n  Name:  gfx1200\n  Marketing Name: AMD Radeon RX 9060 XT\n"

    def fake_run(cmd):
        if not cmd:
            return None
        if "rocminfo" in cmd[0]:
            return rocminfo_out
        if cmd[0] == "ls":
            return "card1\ncard1-DP-1\nrenderD128"
        if cmd[0] == "cat":
            path = cmd[1]
            if path.endswith("/vendor"):
                return "0x1002"
            if path.endswith("/mem_info_vram_total"):
                return str(16 * 1024**3)
            if path.endswith("/product_name"):
                return "AMD Radeon RX 9060 XT"
            return None
        return None

    # _remote_host truthy routes _read/_list_drm_cards through _run (no real sysfs).
    monkeypatch.setattr(hardware, "_remote_host", "fake-host")
    monkeypatch.setattr(hardware, "_run", fake_run)

    info = hardware._detect_amd()
    assert info is not None
    assert info["backend"] == "rocm"
    assert info["gpu_family"] == "rdna"
    assert info["gpu_arch"] == "gfx1200"


def test_consumer_amd_cards_have_real_bandwidth():
    """Consumer AMD cards must be in the bandwidth table so speed estimates use
    real VRAM bandwidth, not the crude rocm FALLBACK_K constant. The RX 9060 XT
    was missing entirely, so its estimates fell back to the constant and were off."""
    from services.hwfit.fit import _lookup_bandwidth
    for name, expected_min in [
        ("AMD Radeon RX 9060 XT", 300),
        ("AMD Radeon RX 9070 XT", 600),
        ("AMD Radeon RX 7900 XTX", 900),
    ]:
        bw = _lookup_bandwidth(name)
        assert bw and bw >= expected_min, f"{name}: {bw} GB/s (expected >= {expected_min})"


def test_9060xt_speed_estimate_is_realistic():
    """Calibration guard: a small MoE fully on a 9060 XT at Q4 should estimate in
    a believable range, not the absurd numbers the missing-bandwidth fallback gave.
    Measured reference: DeepSeek-Coder-V2-Lite Q4 ~60-86 t/s on this card."""
    from services.hwfit.fit import _estimate_speed
    model = {"name": "DeepSeek-Coder-V2-Lite-Instruct", "parameter_count": "16B",
             "is_moe": True, "active_parameters": 2_400_000_000}
    sys = {"backend": "rocm", "gpu_name": "AMD Radeon RX 9060 XT", "gpu_vram_gb": 15.9}
    tps = _estimate_speed(model, "Q4_K_M", "gpu", sys)
    assert 40 <= tps <= 130, f"unrealistic estimate: {tps} t/s"


def test_offload_is_slower_than_full_gpu():
    """Partial CPU offload must estimate slower than the same model fully on GPU,
    and heavier offload slower than lighter — the blend model, not a flat halving."""
    from services.hwfit.fit import _estimate_speed
    model = {"name": "X", "parameter_count": "35B", "is_moe": True,
             "active_parameters": 3_000_000_000}
    sys = {"backend": "rocm", "gpu_name": "AMD Radeon RX 9060 XT", "gpu_vram_gb": 15.9}
    full = _estimate_speed(model, "Q4_K_M", "gpu", sys)
    light = _estimate_speed(model, "Q4_K_M", "cpu_offload", sys, offload_frac=0.2)
    heavy = _estimate_speed(model, "Q4_K_M", "cpu_offload", sys, offload_frac=0.6)
    assert full > light > heavy, (full, light, heavy)


def test_sort_by_newest_orders_by_release_date():
    """sort='newest' orders results by release_date descending (newest first),
    with undated models sorted last."""
    sys = {"backend": "rocm", "gpu_name": "AMD Radeon RX 9060 XT", "gpu_vram_gb": 15.9,
           "gpu_family": "rdna", "gpu_count": 1, "available_ram_gb": 22.0, "total_ram_gb": 31.0}
    res = rank_models(sys, sort="newest", limit=50)
    dated = [r.get("release_date") for r in res if r.get("release_date")]
    # dates present must be in descending order
    assert dated == sorted(dated, reverse=True), "release dates not descending"
    # any undated entries must come after all dated ones
    seen_blank = False
    for r in res:
        if not r.get("release_date"):
            seen_blank = True
        elif seen_blank:
            assert False, "a dated model appeared after an undated one"


def test_no_vendor_specific_formats_on_consumer_rdna():
    """Consumer Radeon can't run NVIDIA NVFP4, Apple MLX, or vLLM-only FP8/AWQ/
    GPTQ builds — none should be recommended on RDNA even though such repos DO
    exist in the catalog. Guards the format filter directly (not just is_gguf)."""
    import re
    bad = re.compile(r"NVFP4|FP8|FP4|-MLX-|\bMLX\b|AWQ|GPTQ", re.IGNORECASE)
    names = [r["name"] for r in rank_models(_rocm_system(family="rdna"), limit=900)]
    offenders = [n for n in names if bad.search(n)]
    assert offenders == [], f"non-runnable formats recommended on RDNA: {offenders[:5]}"
    # Guard against a vacuous test: such formats must actually be in the catalog.
    assert any(bad.search(m["name"]) for m in get_models()), \
        "catalog has no NVFP4/MLX/FP8 repos — test would be vacuous"

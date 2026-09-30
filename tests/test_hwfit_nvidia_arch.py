"""NVIDIA compute-capability detection and its effect on servability.

HW Fit tells datacenter AMD (CDNA) from consumer AMD (RDNA) via
classify_amd_gfx and steers RDNA to GGUF. NVIDIA had no equivalent: the
nvidia-smi probe only asked for memory.total and name, so a pair of Tesla
P100s looked like any other 32 GB CUDA box and the Cookbook rated
AWQ/GPTQ/FP8 repos as good fits there. Current vLLM and SGLang builds ship no
kernels below compute capability 7.5 (vLLM dropped sm_70 in v0.20.0; Pascal
was never supported), so on Pascal and Volta the servable path is GGUF.

These lock the classifier, the newest-card rule for mixed boxes, the separate
compute_cap query (and its fallback for an nvidia-smi that rejects the field),
and the effect on rank_models.
"""
import pytest

import services.hwfit.hardware as hardware
from services.hwfit.fit import rank_models


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setattr(hardware, "_remote_host", None)


def test_compute_caps_classify_by_family():
    assert hardware.classify_nvidia_cc("6.0", "Tesla P100-PCIE-16GB") == ("6.0", "pascal")
    assert hardware.classify_nvidia_cc("6.1", "NVIDIA GeForce GTX 1080 Ti") == ("6.1", "pascal")
    assert hardware.classify_nvidia_cc("7.0", "Tesla V100-PCIE-16GB") == ("7.0", "volta")
    assert hardware.classify_nvidia_cc("7.5", "Tesla T4")[1] == "turing"
    assert hardware.classify_nvidia_cc("8.6", "NVIDIA GeForce RTX 3090")[1] == "ampere"
    assert hardware.classify_nvidia_cc("8.9", "NVIDIA GeForce RTX 4090")[1] == "ada"
    assert hardware.classify_nvidia_cc("9.0", "NVIDIA H100 PCIe")[1] == "hopper"
    assert hardware.classify_nvidia_cc("12.0", "NVIDIA GeForce RTX 5090")[1] == "blackwell"
    assert hardware.classify_nvidia_cc("5.2", "NVIDIA GeForce GTX TITAN X")[1] == "maxwell"


def test_name_fallback_when_compute_cap_is_missing():
    """An nvidia-smi that predates the compute_cap field leaves it empty; the
    card name is the only signal left."""
    assert hardware.classify_nvidia_cc("", "Tesla P100-PCIE-16GB")[1] == "pascal"
    assert hardware.classify_nvidia_cc("", "Tesla P40")[1] == "pascal"
    assert hardware.classify_nvidia_cc("", "NVIDIA GeForce GTX 1080 Ti")[1] == "pascal"
    assert hardware.classify_nvidia_cc("", "Tesla V100-SXM2-32GB")[1] == "volta"
    assert hardware.classify_nvidia_cc("", "Tesla V100S-PCIE-32GB")[1] == "volta"
    # Unrecognized names stay "unknown": treating an unknown card as old would
    # hide models it can serve.
    assert hardware.classify_nvidia_cc("", "NVIDIA Some Future Card")[1] == "unknown"
    assert hardware.classify_nvidia_cc("", "NVIDIA GeForce RTX 4090")[1] == "unknown"


def test_pool_reports_the_newest_card():
    p100 = {"index": 0, "name": "Tesla P100-PCIE-16GB", "compute_cap": "6.0"}
    v100 = {"index": 1, "name": "Tesla V100-PCIE-16GB", "compute_cap": "7.0"}
    rtx4090 = {"index": 2, "name": "NVIDIA GeForce RTX 4090", "compute_cap": "8.9"}

    assert hardware.classify_nvidia_pool([p100, dict(p100, index=1)]) == ("6.0", "pascal")
    # P100 + V100: neither card has current vLLM/SGLang kernels.
    assert hardware.classify_nvidia_pool([p100, v100]) == ("7.0", "volta")
    # P100 + 4090: vLLM still runs on the 4090, so the host is not restricted.
    assert hardware.classify_nvidia_pool([p100, rtx4090]) == ("8.9", "ada")


def test_pool_with_an_unclassifiable_card_is_unknown():
    gpus = [
        {"index": 0, "name": "Tesla P100-PCIE-16GB", "compute_cap": "6.0"},
        {"index": 1, "name": "NVIDIA Some Future Card", "compute_cap": ""},
    ]
    assert hardware.classify_nvidia_pool(gpus) == ("", "unknown")
    assert hardware.classify_nvidia_pool([]) == ("", "unknown")


def _fake_smi(compute_cap_out):
    """nvidia-smi stand-in: answers the memory/name query, and answers the
    compute_cap query with `compute_cap_out` (None = rejected / non-zero exit,
    which is what _run returns for an nvidia-smi that predates the field)."""
    rows = "16384, Tesla P100-PCIE-16GB\n16384, Tesla P100-PCIE-16GB\n16384, Tesla V100-PCIE-16GB"

    def fake_run(cmd):
        joined = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "--query-gpu=memory.total,name" in joined:
            return rows
        if "--query-gpu=compute_cap" in joined:
            return compute_cap_out
        return None

    return fake_run


def test_detect_nvidia_reports_compute_cap_and_family(monkeypatch):
    monkeypatch.setattr(hardware, "_run", _fake_smi("6.0\n6.0\n7.0"))
    info = hardware._detect_nvidia()

    assert info["backend"] == "cuda"
    assert [g["compute_cap"] for g in info["gpus"]] == ["6.0", "6.0", "7.0"]
    assert info["gpu_arch"] == "7.0"
    assert info["gpu_family"] == "volta"
    assert info["gpu_count"] == 3


def test_detect_nvidia_survives_an_nvidia_smi_without_compute_cap(monkeypatch):
    """An old nvidia-smi rejects the compute_cap query. The GPUs must still be
    detected, and the family comes from the card names."""
    monkeypatch.setattr(hardware, "_run", _fake_smi(None))
    info = hardware._detect_nvidia()

    assert info is not None, "an nvidia-smi without compute_cap must not read as 'No GPU'"
    assert info["gpu_count"] == 3
    assert [g["compute_cap"] for g in info["gpus"]] == ["", "", ""]
    assert info["gpu_family"] == "volta"


_AWQ_ONLY = {
    "name": "some-org/Model-AWQ",
    "provider": "some-org",
    "parameter_count": "7B",
    "quantization": "AWQ-4bit",
    "is_gguf": False,
    "gguf_sources": [],
    "context_length": 32768,
}

_GGUF_ONLY = {
    "name": "some-org/Model-GGUF",
    "provider": "some-org",
    "parameter_count": "7B",
    "quantization": "Q4_K_M",
    "is_gguf": True,
    "gguf_sources": ["some-org/Model-GGUF"],
    "context_length": 32768,
}


def _catalog(monkeypatch, *models):
    """rank_models reads the bundled HF catalog; swap in a small fixture so the
    assertion is about the servability filter, not catalog drift."""
    monkeypatch.setattr("services.hwfit.fit.get_models", lambda: [dict(m) for m in models])


def _cuda_system(family, arch, name):
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_family": family,
        "gpu_arch": arch,
        "gpu_name": name,
        "gpu_vram_gb": 32.0,
        "gpu_count": 2,
        "available_ram_gb": 128.0,
        "total_ram_gb": 128.0,
        "platform": "linux",
    }


def _ranked_names(system, **kwargs):
    return {r["name"] for r in rank_models(system, limit=50, **kwargs)}


@pytest.mark.parametrize("family,arch,name", [
    ("pascal", "6.0", "Tesla P100-PCIE-16GB"),
    ("volta", "7.0", "Tesla V100-PCIE-16GB"),
])
def test_pre_turing_keeps_gguf_and_hides_vllm_only_rows(monkeypatch, family, arch, name):
    _catalog(monkeypatch, _AWQ_ONLY, _GGUF_ONLY)
    names = _ranked_names(_cuda_system(family, arch, name))

    assert "some-org/Model-GGUF" in names, "GGUF is the servable path here; it must stay."
    assert "some-org/Model-AWQ" not in names, (
        f"No current vLLM/SGLang build serves {family}, so an AWQ-only repo is "
        "not servable even though it fits VRAM."
    )


def test_pre_turing_q4_filter_does_not_swap_in_awq(monkeypatch):
    """On multi-GPU CUDA boxes the Q4 filter treats native AWQ rows as the 4-bit
    fit. That swap must not bring AWQ back on Pascal/Volta."""
    _catalog(monkeypatch, _AWQ_ONLY, _GGUF_ONLY)
    names = _ranked_names(_cuda_system("pascal", "6.0", "Tesla P100-PCIE-16GB"), quant="Q4_K_M")

    assert "some-org/Model-AWQ" not in names


@pytest.mark.parametrize("family", ["turing", "ampere", "ada", "unknown", ""])
def test_turing_and_newer_or_unknown_still_see_vllm_only_rows(monkeypatch, family):
    _catalog(monkeypatch, _AWQ_ONLY, _GGUF_ONLY)
    names = _ranked_names(_cuda_system(family, "", "NVIDIA GeForce RTX 3090"))

    assert "some-org/Model-AWQ" in names


def test_detect_system_carries_family_through_to_the_ranker(monkeypatch):
    """gpu_family must survive from the vendor probe into the detected system,
    otherwise fit.py never sees it and the pre-Turing rule cannot fire."""
    monkeypatch.setattr(hardware, "_run", _fake_smi("6.0\n6.0\n7.0"))
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    system = hardware.detect_system(fresh=True)

    assert system["backend"] == "cuda"
    assert system["gpu_arch"] == "7.0"
    assert system["gpu_family"] == "volta"

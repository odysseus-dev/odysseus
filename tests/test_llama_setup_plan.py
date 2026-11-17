"""One-click runtime setup planning: GPU/driver matching, CPU alternative, cache/offline."""
import hashlib

import pytest

from src import llama_runtime as lr

H = lambda c: "sha256:" + c * 64  # noqa: E731


def release(extra=()):
    return {"tag_name": "b9", "assets": [
        {"name": "llama-b9-bin-win-cpu-x64.zip", "size": 25, "digest": H("1"), "browser_download_url": "u-cpu"},
        {"name": "llama-b9-bin-win-cpu-arm64.zip", "size": 13, "digest": H("2"), "browser_download_url": "x"},
        {"name": "llama-b9-bin-win-cuda-12.4-x64.zip", "size": 300, "digest": H("3"), "browser_download_url": "u12"},
        {"name": "llama-b9-bin-win-cuda-13.5-x64.zip", "size": 200, "digest": H("4"), "browser_download_url": "u13"},
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "size": 390, "digest": H("5"), "browser_download_url": "c12"},
        {"name": "cudart-llama-bin-win-cuda-13.5-x64.zip", "size": 420, "digest": H("6"), "browser_download_url": "c13"},
        *extra]}


def gpu(major=13, cc="8.6", kind="nvidia"):
    return {"kind": kind, "name": "RTX 3070", "driver": "621.01", "cuda_driver_major": major,
            "compute_capability": cc, "total_bytes": 8 << 30}


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "llama_runtime_root", lambda: tmp_path / "rt")
    return tmp_path / "rt"


def test_cuda13_driver_gets_cuda13_with_matching_cudart_and_total_size():
    plan = lr.plan_setup(release(), gpu(13))
    assert plan["chosen"] == "cuda" and plan["cuda"]["install_key"] == "b9-cuda-13.5"
    assert [a["name"] for a in plan["cuda"]["archives"]] == [
        "llama-b9-bin-win-cuda-13.5-x64.zip", "cudart-llama-bin-win-cuda-13.5-x64.zip"]
    assert plan["cuda"]["total_bytes"] == 620 and plan["cuda"]["download_bytes"] == 620
    assert plan["cuda"]["verifiable"] and not plan["cuda"]["already_installed"]


def test_cuda12_driver_never_gets_cuda13():
    plan = lr.plan_setup(release(), gpu(12))
    assert plan["cuda"]["install_key"] == "b9-cuda-12.4"


def test_pre_turing_gpu_is_limited_to_cuda12_even_with_a_cuda13_driver():
    assert lr.plan_setup(release(), gpu(13, cc="6.1"))["cuda"]["install_key"] == "b9-cuda-12.4"


def test_old_driver_falls_back_to_cpu_with_reason_and_never_offers_a_driver_install():
    plan = lr.plan_setup(release(), gpu(11))
    assert plan["chosen"] == "cpu" and plan["cuda"] is None and "CUDA 11" in plan["cuda_unavailable_reason"]
    assert plan["cpu"]["install_key"] == "b9-cpu"
    assert all("arm64" not in a["name"] for a in plan["cpu"]["archives"])


def test_nvidia_without_cuda_support_explains_driver_update():
    plan = lr.plan_setup(release(), gpu(0))
    assert plan["chosen"] == "cpu" and "Update the NVIDIA driver" in plan["cuda_unavailable_reason"]


def test_no_gpu_picks_cpu_and_cpu_is_always_available_as_alternative():
    assert lr.plan_setup(release(), {"kind": "unknown"})["chosen"] == "cpu"
    assert lr.plan_setup(release(), gpu(13))["cpu"] is not None
    assert lr.plan_setup(release(), gpu(13), "cpu")["chosen"] == "cpu"


def test_missing_digest_marks_option_unverifiable():
    rel = release()
    rel["assets"][3]["digest"] = ""
    assert not lr.plan_setup(rel, gpu(13))["cuda"]["verifiable"]


def test_cached_packages_are_not_counted_as_download(root):
    cache = root / "_packages"
    cache.mkdir(parents=True)
    (cache / "llama-b9-bin-win-cuda-13.5-x64.zip").write_bytes(b"x" * 200)
    plan = lr.plan_setup(release(), gpu(13))
    assert plan["cuda"]["download_bytes"] == 420
    assert [a["cached"] for a in plan["cuda"]["archives"]] == [True, False]


def test_release_snapshot_roundtrip_enables_offline_planning(root):
    assert lr.load_release_snapshot() is None
    lr.save_release_snapshot(release())
    snap = lr.load_release_snapshot()
    assert snap["tag_name"] == "b9"
    assert lr.plan_setup(snap, gpu(13))["cuda"]["install_key"] == "b9-cuda-13.5"
    assert not list((root / "_packages").glob("*.tmp"))


def test_select_asset_uses_driver_cuda_major_when_known():
    rel = release()
    assert "13.5" in lr.select_llama_asset(rel, "cuda", "621.01", 13)["name"]
    assert "12.4" in lr.select_llama_asset(rel, "cuda", "550.00", 12)["name"]
    assert lr.select_llama_asset(rel, "cuda", "400.00", 11) is None

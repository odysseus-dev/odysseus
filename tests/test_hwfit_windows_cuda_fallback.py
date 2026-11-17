"""WMI-only Windows detection (nvidia-smi/NVML broken) must not hide a working NVIDIA GPU."""
import json

from services.hwfit import hardware
from src import gpu_runtime

WMI_ONLY = json.dumps({"ram_gb": 63.8, "avail_gb": 40.0, "cpu_name": "cpu", "cpu_cores": 16, "cpu_arch": "AMD64",
                       "gpu_name": "NVIDIA GeForce RTX 3070", "gpu_vram_gb": 4.0, "gpu_count": 1,
                       "gpu_backend": "cpu_x86"})


def _detect(monkeypatch, cuda):
    monkeypatch.setattr(hardware, "_run", lambda cmd: WMI_ONLY)
    monkeypatch.setattr(hardware, "_remote_host", "", raising=False)
    monkeypatch.setattr(gpu_runtime, "probe_cuda_driver", lambda *a, **k: cuda)
    return hardware._detect_windows()


def test_cuda_driver_api_promotes_wmi_gpu_to_cuda_with_real_vram(monkeypatch):
    result = _detect(monkeypatch, {"available": True, "name": "NVIDIA GeForce RTX 3070", "device_count": 1,
                                   "total_bytes": 8589410304})
    assert result["backend"] == "cuda" and result["gpu_vram_gb"] == 8.0
    assert result["gpus"][0]["vram_gb"] == 8.0


def test_without_a_working_driver_api_wmi_result_is_unchanged(monkeypatch):
    result = _detect(monkeypatch, {"available": False, "error": "no driver"})
    assert result["backend"] == "cpu_x86" and result["gpu_vram_gb"] == 4.0


def test_remote_hosts_are_never_probed_locally(monkeypatch):
    monkeypatch.setattr(hardware, "_run", lambda cmd: WMI_ONLY)
    monkeypatch.setattr(hardware, "_remote_host", "gpu-box", raising=False)
    monkeypatch.setattr(gpu_runtime, "probe_cuda_driver",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("local probe for remote host")))
    assert hardware._detect_windows()["backend"] == "cpu_x86"

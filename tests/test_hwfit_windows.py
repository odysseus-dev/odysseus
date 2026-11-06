"""Windows support for Cookbook hardware-fit.

Odysseus only supports llama.cpp on Windows (vLLM/SGLang are explicitly
blocked). llama.cpp requires GGUF, so non-GGUF models — including AWQ/GPTQ/
FP8 safetensors repos — must be filtered out on Windows so the Cookbook does
not recommend models the user cannot actually serve.
"""

import os

import pytest

from services.hwfit.fit import rank_models
from services.hwfit.models import get_models
from tests.hwfit_publication_fixtures import publication_catalog  # noqa: F401

# Rank authored inputs rather than publication catalog snapshots.
pytestmark = pytest.mark.usefixtures("publication_catalog")


def _windows_system(ram_gb=32.0, vram_gb=16.0):
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4060",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.7,
        "total_ram_gb": ram_gb,
        "platform": "windows",
    }


def _cuda_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "total_ram_gb": 64.0,
    }


def test_only_gguf_models_recommended_on_windows():
    """llama.cpp (GGUF) is the only servable path on Windows, so every model
    recommended there must ship a real GGUF — no vLLM-only AWQ/GPTQ/FP8."""
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(_windows_system(), limit=900)
        if not (catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-GGUF models on Windows, e.g. {unservable[:3]}"


def test_safetensors_models_still_recommended_on_cuda():
    """Regression guard: the GGUF-only rule must not leak onto CUDA."""
    names = {r["name"] for r in rank_models(_cuda_system(), search="microsoft/Phi-mini-MoE-instruct", limit=10)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_awq_model_hidden_on_windows():
    """The user's reported issue: Qwen2.5-3B-Instruct-AWQ is AWQ-only and must
    not be recommended on Windows where it cannot be served."""
    names = {r["name"] for r in rank_models(_windows_system(), limit=900)}
    assert "Qwen/Qwen2.5-3B-Instruct-AWQ" not in names


def test_awq_model_visible_on_cuda():
    """The same AWQ model should still be visible on CUDA where vLLM can
    serve it."""
    names = {r["name"] for r in rank_models(_cuda_system(), search="Qwen/Qwen2.5-3B-Instruct-AWQ", limit=10)}
    assert "Qwen/Qwen2.5-3B-Instruct-AWQ" in names


def test_gguf_alternate_still_recommended_on_windows():
    """Qwen2.5-3B-Instruct (the base model) has a GGUF source, so it should
    still appear on Windows even though the AWQ variant is hidden."""
    names = {r["name"] for r in rank_models(_windows_system(), limit=900)}
    assert "Qwen/Qwen2.5-3B-Instruct" in names


def test_remote_windows_probe_uses_encoded_command(monkeypatch):
    """Remote Windows hwfit must not use nested -Command quoting over SSH."""
    from services.hwfit import hardware

    calls = []
    monkeypatch.setattr(hardware, "_remote_host", "user@winpc")
    monkeypatch.setattr(hardware, "_remote_port", None)

    def fake_run(cmd):
        calls.append(cmd)
        if isinstance(cmd, str) and "EncodedCommand" in cmd:
            return (
                '{"ram_gb":64,"avail_gb":32,"cpu_name":"Test CPU",'
                '"cpu_cores":8,"arch":64}'
            )
        return None

    monkeypatch.setattr(hardware, "_run", fake_run)
    result = hardware._detect_windows()
    assert result is not None
    assert result["total_ram_gb"] == 64
    assert len(calls) == 1
    assert "EncodedCommand" in calls[0]
    assert '-Command "' not in calls[0]


def test_probe_remote_platform_detects_windows(monkeypatch):
    from services.hwfit import hardware

    monkeypatch.setattr(hardware, "_run", lambda cmd: "Windows_NT\n")
    assert hardware._probe_remote_platform() == "windows"


def test_probe_remote_platform_detects_darwin(monkeypatch):
    from services.hwfit import hardware

    def fake_run(cmd):
        if cmd == "echo %OS%":
            return "%OS%"
        if cmd == ["uname", "-s"]:
            return "Darwin"
        raise AssertionError(f"unexpected probe cmd: {cmd!r}")

    monkeypatch.setattr(hardware, "_run", fake_run)
    assert hardware._probe_remote_platform() == "linux"


def _ps_objects(items):
    def lit(v):
        return f"'{v}'" if isinstance(v, str) else str(v)
    return ", ".join(
        "[pscustomobject]@{" + "; ".join(f"'{k}' = {lit(v)}" for k, v in item.items()) + "}"
        for item in items
    )


def _run_local_windows_probe(monkeypatch, adapters, registry):
    """Run the real local Windows probe script against faked WMI/registry data.

    PowerShell resolves functions before cmdlets, so defining Get-CimInstance,
    Get-ItemProperty and nvidia-smi ahead of the script swaps in fixtures while
    the GPU-selection logic itself runs unmodified.
    """
    import json
    import shutil
    import subprocess

    from services.hwfit import hardware

    ps_exe = shutil.which("pwsh") or shutil.which("powershell")
    if not ps_exe:
        pytest.skip("PowerShell not available")

    captured = []
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", lambda cmd: captured.append(cmd))
    hardware._detect_windows()
    script = captured[0][-1]

    fakes = f"""
        function nvidia-smi {{ $global:LASTEXITCODE = 1 }}
        function Get-CimInstance {{
            param([string]$ClassName)
            switch ($ClassName) {{
                'Win32_OperatingSystem' {{ [pscustomobject]@{{ TotalVisibleMemorySize = 33554432; FreePhysicalMemory = 16777216 }} }}
                'Win32_Processor' {{ [pscustomobject]@{{ Name = 'Test CPU'; NumberOfLogicalProcessors = 12; AddressWidth = 64 }} }}
                'Win32_VideoController' {{ @({_ps_objects(adapters)}) }}
            }}
        }}
        function Get-ItemProperty {{ param([string]$Path) @({_ps_objects(registry)}) }}
    """
    out = subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-Command", fakes + script],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_VIRTUAL_DISPLAY = {"Name": "Virtual Desktop Monitor", "AdapterRAM": 0,
                    "PNPDeviceID": r"ROOT\DISPLAY\0000"}
_AMD_IGPU = {"Name": "AMD Radeon(TM) Graphics", "AdapterRAM": 536870912,
             "PNPDeviceID": r"PCI\VEN_1002&DEV_164E&SUBSYS_00000000&REV_C1\4&1"}
# AdapterRAM is a uint32, so a 16 GB card reports just under 4 GB there.
_RX_9070 = {"Name": "AMD Radeon RX 9070", "AdapterRAM": 4293918720,
            "PNPDeviceID": r"PCI\VEN_1002&DEV_7550&SUBSYS_00000000&REV_C0\4&2"}
_AMD_REGISTRY = [
    {"MatchingDeviceId": r"pci\ven_1002&dev_164e", "HardwareInformation.qwMemorySize": 536870912},
    {"MatchingDeviceId": r"pci\ven_1002&dev_7550", "HardwareInformation.qwMemorySize": 17095983104},
]


@pytest.mark.skipif(os.name != "nt", reason="runs the local PowerShell probe")
def test_windows_probe_picks_discrete_gpu_over_igpu_listed_first(monkeypatch):
    """Desktops with an iGPU + discrete card often list the iGPU first; the
    probe must report the adapter with the most VRAM."""
    d = _run_local_windows_probe(monkeypatch, [_VIRTUAL_DISPLAY, _AMD_IGPU, _RX_9070], _AMD_REGISTRY)
    assert d["gpu_name"] == "AMD Radeon RX 9070"
    assert d["gpu_vram_gb"] == 15.9
    assert d["gpu_backend"] == "vulkan"


@pytest.mark.skipif(os.name != "nt", reason="runs the local PowerShell probe")
def test_windows_probe_non_amd_gpu_keeps_cpu_backend(monkeypatch):
    intel = {"Name": "Intel(R) UHD Graphics", "AdapterRAM": 1073741824,
             "PNPDeviceID": r"PCI\VEN_8086&DEV_A780&SUBSYS_00000000&REV_04\3&1"}
    d = _run_local_windows_probe(monkeypatch, [intel], [])
    assert d["gpu_name"] == "Intel(R) UHD Graphics"
    assert d["gpu_vram_gb"] == 1.0
    assert d["gpu_backend"] == "cpu_x86"

"""Windows GPU detection for AMD cards (#483, #2918).

_detect_windows() must pick the discrete card over an iGPU that WMI lists
first (AM5 boards enumerate the Radeon iGPU before the PCIe slot), read VRAM
from the driver's 64-bit qwMemorySize rather than the 4 GB-capped AdapterRAM,
and report AMD as a GPU backend instead of cpu_x86.

The PowerShell tests run the real probe script with Get-CimInstance /
Get-ItemProperty / nvidia-smi shadowed by functions, so they need a
PowerShell on PATH (pwsh, or powershell.exe via WSL interop) and skip otherwise.
"""

import json
import shutil
import subprocess

import pytest

from services.hwfit import hardware

_PS = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")

_GB = 1024 ** 3


def _probe_script(monkeypatch):
    """Capture the PowerShell probe exactly as _detect_windows() runs it locally."""
    captured = []

    def fake_run(cmd):
        captured.append(cmd)
        return None

    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", fake_run)
    hardware._detect_windows()
    assert len(captured) == 1 and isinstance(captured[0], list)
    return captured[0][-1]


def _ps_adapter(name, dev, adapter_ram):
    return (
        f"[pscustomobject]@{{ Name = '{name}'; AdapterRAM = [uint32]{adapter_ram}; "
        f"PNPDeviceID = '{dev}&SUBSYS_00000000&REV_C0\\4&1&0&0008' }}"
    )


def _ps_driver(dev, qw_bytes):
    return (
        f"[pscustomobject]@{{ MatchingDeviceId = '{dev.lower()}'; "
        f"'HardwareInformation.qwMemorySize' = [int64]{qw_bytes} }}"
    )


def _run_probe(monkeypatch, adapters, drivers, hip_sdk=False):
    """Run the probe in real PowerShell against mocked WMI + registry data."""
    hip = "$env:HIP_PATH = 'C:\\HIP'" if hip_sdk else "Remove-Item Env:HIP_PATH -ErrorAction SilentlyContinue"
    prelude = f"""
        function nvidia-smi {{ $global:LASTEXITCODE = 1 }}
        {hip}
        function Get-CimInstance {{
            param([string]$ClassName)
            switch ($ClassName) {{
                'Win32_OperatingSystem' {{ [pscustomobject]@{{ TotalVisibleMemorySize = 33554432; FreePhysicalMemory = 16777216 }} }}
                'Win32_Processor' {{ [pscustomobject]@{{ Name = 'AMD Ryzen 7 9800X3D'; NumberOfLogicalProcessors = 16; AddressWidth = 64 }} }}
                'Win32_VideoController' {{ {"; ".join(adapters)} }}
            }}
        }}
        function Get-ItemProperty {{ {"; ".join(drivers)} }}
    """
    script = prelude + _probe_script(monkeypatch)
    out = subprocess.run(
        [_PS, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


_IGPU = ("AMD Radeon(TM) Graphics", "PCI\\VEN_1002&DEV_13C0")
_RX9070XT = ("AMD Radeon RX 9070 XT", "PCI\\VEN_1002&DEV_7550")


@pytest.mark.skipif(not _PS, reason="PowerShell not available")
def test_discrete_amd_card_wins_over_igpu_listed_first(monkeypatch):
    """The AM5 report: the iGPU enumerates first and was picked with ~0.5 GB."""
    d = _run_probe(
        monkeypatch,
        adapters=[
            _ps_adapter(*_IGPU, adapter_ram=512 * 1024 ** 2),
            # AdapterRAM is uint32, so a 16 GB card reports just under 4 GB.
            _ps_adapter(*_RX9070XT, adapter_ram=4293918720),
        ],
        drivers=[
            _ps_driver(_IGPU[1], 512 * 1024 ** 2),
            _ps_driver(_RX9070XT[1], 16 * _GB),
        ],
    )
    assert d["gpu_name"] == "AMD Radeon RX 9070 XT"
    assert d["gpu_vram_gb"] == 16.0
    assert d["gpu_count"] == 1


@pytest.mark.skipif(not _PS, reason="PowerShell not available")
def test_amd_without_hip_sdk_reports_vulkan(monkeypatch):
    d = _run_probe(
        monkeypatch,
        adapters=[_ps_adapter(*_RX9070XT, adapter_ram=4293918720)],
        drivers=[_ps_driver(_RX9070XT[1], 16 * _GB)],
    )
    assert d["gpu_backend"] == "vulkan"


@pytest.mark.skipif(not _PS, reason="PowerShell not available")
def test_amd_with_hip_sdk_reports_rocm(monkeypatch):
    d = _run_probe(
        monkeypatch,
        adapters=[_ps_adapter(*_RX9070XT, adapter_ram=4293918720)],
        drivers=[_ps_driver(_RX9070XT[1], 16 * _GB)],
        hip_sdk=True,
    )
    assert d["gpu_backend"] == "rocm"


@pytest.mark.skipif(not _PS, reason="PowerShell not available")
def test_non_amd_wmi_fallback_stays_cpu(monkeypatch):
    """Intel/unknown adapters keep the old cpu_x86 label; only AMD changed."""
    arc = ("Intel(R) Arc(TM) A770 Graphics", "PCI\\VEN_8086&DEV_56A0")
    d = _run_probe(
        monkeypatch,
        adapters=[_ps_adapter(*arc, adapter_ram=4293918720)],
        drivers=[_ps_driver(arc[1], 16 * _GB)],
    )
    assert d["gpu_name"] == arc[0]
    assert d["gpu_backend"] == "cpu_x86"


def test_vulkan_backend_flows_into_result(monkeypatch):
    """The probe's gpu_backend reaches the hwfit system dict unchanged."""
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", lambda cmd: json.dumps({
        "ram_gb": 32, "avail_gb": 16, "cpu_name": "AMD Ryzen 7 9800X3D",
        "cpu_cores": 16, "cpu_arch": "AMD64",
        "gpu_name": "AMD Radeon RX 9070 XT", "gpu_vram_gb": 16.0,
        "gpu_count": 1, "gpu_backend": "vulkan",
    }))
    result = hardware._detect_windows()
    assert result["has_gpu"] is True
    assert result["backend"] == "vulkan"
    assert result["gpus"] == [{"index": 0, "name": "AMD Radeon RX 9070 XT", "vram_gb": 16.0}]

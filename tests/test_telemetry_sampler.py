"""Unit tests for services.telemetry.sampler.

Tests:
- sampler starts and stops cleanly
- snapshot dict contains the required keys
- pynvml import failure → zeroes in GPU fields, no exception raised
- throttle flag is True at threshold and False below it
"""

import sys
import time
import types
import importlib
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fake_psutil(cpu=23.5, ram_used_gb=4.0, ram_pct=42.0):
    """Build a minimal psutil stub that the sampler accepts."""
    psutil = types.ModuleType("psutil")

    class _Mem:
        used = int(ram_used_gb * 1024 ** 3)
        percent = ram_pct

    psutil.cpu_percent = lambda interval=None: cpu
    psutil.virtual_memory = lambda: _Mem()
    return psutil


def _load_sampler_with(monkeypatch, psutil_stub=None, pynvml_stub=None):
    """(Re)load sampler with stubbed optional deps and return the module."""
    # Remove cached module so importlib.import_module re-executes module top-level.
    for key in list(sys.modules):
        if "telemetry" in key:
            del sys.modules[key]

    if psutil_stub is not None:
        monkeypatch.setitem(sys.modules, "psutil", psutil_stub)
    else:
        monkeypatch.setitem(sys.modules, "psutil", None)  # simulate ImportError

    if pynvml_stub is not None:
        monkeypatch.setitem(sys.modules, "pynvml", pynvml_stub)
    else:
        monkeypatch.setitem(sys.modules, "pynvml", None)  # simulate ImportError

    mod = importlib.import_module("services.telemetry.sampler")
    return mod


# ── tests ─────────────────────────────────────────────────────────────────────

def test_snapshot_keys_present(monkeypatch):
    """Snapshot must always contain the documented public fields."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil())
    sampler = mod.TelemetrySampler()
    snap = sampler._sample()
    for key in ("timestamp", "cpu_pct", "ram_gb", "ram_pct", "vram_gb", "gpu_pct", "gpu_temp_c", "throttle"):
        assert key in snap, f"Missing key in snapshot: {key}"


def test_cpu_ram_values_from_psutil(monkeypatch):
    """When psutil is available, cpu_pct and ram_gb match the stub."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil(cpu=50.0, ram_used_gb=8.0, ram_pct=75.0))
    sampler = mod.TelemetrySampler()
    snap = sampler._sample()
    assert snap["cpu_pct"] == pytest.approx(50.0)
    assert snap["ram_gb"] == pytest.approx(8.0, abs=0.1)
    assert snap["ram_pct"] == pytest.approx(75.0)


def test_no_psutil_returns_zeroes(monkeypatch):
    """When psutil is absent, CPU/RAM fields are 0 with no exception raised."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=None)
    sampler = mod.TelemetrySampler()
    snap = sampler._sample()
    assert snap["cpu_pct"] == 0.0
    assert snap["ram_gb"] == 0.0


def test_no_pynvml_gpu_fields_are_zeroes(monkeypatch):
    """When pynvml is absent, GPU fields are 0 and throttle is False."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil(), pynvml_stub=None)
    sampler = mod.TelemetrySampler()
    snap = sampler._sample()
    assert snap["vram_gb"] == 0.0
    assert snap["gpu_pct"] == 0
    assert snap["gpu_temp_c"] == 0
    assert snap["throttle"] is False


def test_throttle_flag_at_threshold(monkeypatch):
    """throttle is True when GPU temp is >= the threshold."""
    pynvml = types.ModuleType("pynvml")

    class _Mem:
        used = int(6 * 1024 ** 3)

    class _Util:
        gpu = 88

    pynvml.nvmlInit = lambda: None
    pynvml.nvmlDeviceGetHandleByIndex = lambda idx: object()
    pynvml.nvmlDeviceGetMemoryInfo = lambda h: _Mem()
    pynvml.nvmlDeviceGetUtilizationRates = lambda h: _Util()
    pynvml.NVML_TEMPERATURE_GPU = 0
    # Temperature exactly at threshold → throttle = True
    threshold = 87
    pynvml.nvmlDeviceGetTemperature = lambda h, t: threshold

    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil(), pynvml_stub=pynvml)
    monkeypatch.setenv("ODYSSEUS_THROTTLE_TEMP", str(threshold))
    sampler = mod.TelemetrySampler()
    # Force nvml_ok so the GPU branch runs.
    sampler._nvml_ok = True
    sampler._gpu_handle = object()
    snap = sampler._sample()
    assert snap["throttle"] is True


def test_throttle_flag_below_threshold(monkeypatch):
    """throttle is False when GPU temp is below the threshold."""
    pynvml = types.ModuleType("pynvml")

    class _Mem:
        used = int(4 * 1024 ** 3)

    class _Util:
        gpu = 60

    pynvml.nvmlInit = lambda: None
    pynvml.nvmlDeviceGetHandleByIndex = lambda idx: object()
    pynvml.nvmlDeviceGetMemoryInfo = lambda h: _Mem()
    pynvml.nvmlDeviceGetUtilizationRates = lambda h: _Util()
    pynvml.NVML_TEMPERATURE_GPU = 0
    pynvml.nvmlDeviceGetTemperature = lambda h, t: 86  # one below default 87

    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil(), pynvml_stub=pynvml)
    sampler = mod.TelemetrySampler()
    sampler._nvml_ok = True
    sampler._gpu_handle = object()
    snap = sampler._sample()
    assert snap["throttle"] is False


def test_sampler_start_stop(monkeypatch):
    """Sampler thread starts and stops without hanging."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil())
    sampler = mod.TelemetrySampler()
    sampler.start()
    assert sampler._running is True
    assert sampler._thread is not None and sampler._thread.is_alive()
    time.sleep(0.1)
    sampler.stop()
    assert sampler._running is False


def test_get_latest_empty_before_start(monkeypatch):
    """get_latest() returns an empty dict before the sampler has run."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil())
    sampler = mod.TelemetrySampler()
    assert sampler.get_latest() == {}


def test_get_latest_after_sample(monkeypatch):
    """get_latest() returns the snapshot after at least one poll cycle."""
    mod = _load_sampler_with(monkeypatch, psutil_stub=_make_fake_psutil())
    sampler = mod.TelemetrySampler()
    sampler.start()
    time.sleep(1.2)  # wait for at least one 1-second poll
    sampler.stop()
    snap = sampler.get_latest()
    assert snap.get("timestamp", 0) > 0

"""NVIDIA driver-error surfacing for Cookbook hardware-fit.

nvidia-smi reports an actionable failure (e.g. "Driver/library version
mismatch" after a GPU driver update without a reboot) on *stderr* with a
*non-zero* exit code. Before the fix, `_run` only ever returned stdout on a
zero exit, so that error was swallowed and the box was misclassified as
GPU-less ("No GPU detected") instead of surfacing the real reason via
`gpu_error` (which the Cookbook UI renders as a "GPU driver error" chip).

These tests lock in that nvidia-smi's stderr reaches `_detect_nvidia`, and that
a genuinely GPU-less machine (nvidia-smi absent / clean failure) still reports
no error.
"""

import subprocess

import pytest

from services.hwfit import hardware


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    # Pin to local detection so SSH-specific branches don't engage.
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_remote_port", None)


def test_driver_mismatch_on_stderr_sets_gpu_error(monkeypatch):
    """nvidia-smi: non-zero exit + NVML error on stderr -> gpu_error is set,
    not a misleading 'no GPU'."""
    msg = "Failed to initialize NVML: Driver/library version mismatch"

    def fake_run(cmd, *a, **k):
        return _FakeProc(returncode=255, stdout="", stderr=msg + "\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert hardware._detect_nvidia() is None
    assert hardware._last_gpu_error is not None
    assert "mismatch" in hardware._last_gpu_error.lower()
    # Trimmed to a single line and capped in length for the UI chip tooltip.
    assert "\n" not in hardware._last_gpu_error
    assert len(hardware._last_gpu_error) <= 140


def test_run_want_stderr_on_fail_returns_stderr():
    """The opt-in path returns stderr on a non-zero exit; default callers are
    unaffected (still stdout-or-None)."""
    import unittest.mock as mock

    proc = _FakeProc(returncode=1, stdout="", stderr="boom")
    with mock.patch.object(subprocess, "run", return_value=proc):
        assert hardware._run(["x"]) is None  # default: failure -> None
        assert hardware._run(["x"], want_stderr_on_fail=True) == "boom"


def test_no_gpu_has_no_error(monkeypatch):
    """A box with no NVIDIA GPU (nvidia-smi missing) must NOT report a driver
    error — gpu_error stays None so the UI shows the normal CPU path."""

    def fake_run(cmd, *a, **k):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert hardware._detect_nvidia() is None
    assert hardware._last_gpu_error is None


def test_healthy_gpu_parses_and_clears_error(monkeypatch):
    """A working nvidia-smi (zero exit, CSV rows) detects GPUs and leaves no
    error set."""

    def fake_run(cmd, *a, **k):
        return _FakeProc(returncode=0, stdout="24576, NVIDIA RTX 4090\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    info = hardware._detect_nvidia()
    assert info is not None
    assert info["gpu_count"] == 1
    assert info["backend"] == "cuda"
    assert round(info["gpu_vram_gb"]) == 24
    assert hardware._last_gpu_error is None

"""Hardware telemetry sampler for live inference monitoring.

Adapted from llm_bench_suite/src/monitoring/hardware.py. Runs a daemon
thread that polls CPU, RAM, and (optionally) GPU metrics once per second
while the ``_active`` flag is set. Keeps a 300-entry rolling deque so the
last 5 minutes of data are available for the history endpoint.

pynvml (NVIDIA Management Library) is optional — when absent or when no
NVIDIA GPU is present, GPU fields are zeroed and no exception propagates.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    logger.warning("psutil not installed — CPU/RAM telemetry will report zeroes")

try:
    import pynvml
    _HAS_NVML = True
except ImportError:
    _HAS_NVML = False

# Thermal throttle threshold in °C. Many consumer GPUs begin to throttle
# around 87 °C; override via ODYSSEUS_THROTTLE_TEMP env var for other hardware.
_THROTTLE_TEMP_C: int = int(os.environ.get("ODYSSEUS_THROTTLE_TEMP", "87"))

_HISTORY_LEN = 300  # 5-minute rolling window at 1 s/sample


class TelemetrySampler:
    """Daemon-thread hardware poller.

    Start by calling :meth:`start`; the background thread polls once per
    second until :meth:`stop` is called. The sampler is designed as a
    module-level singleton — instantiate once and reuse across requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: deque[Dict[str, Any]] = deque(maxlen=_HISTORY_LEN)
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._nvml_ok = False
        self._gpu_handle = None
        self._init_nvml()

    # ── NVML init ──────────────────────────────────────────────────────────

    def _init_nvml(self) -> None:
        """Attempt to initialise pynvml. Silently degrades if unavailable."""
        if not _HAS_NVML:
            return
        try:
            pynvml.nvmlInit()
            self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_ok = True
        except Exception:
            self._nvml_ok = False

    # ── Polling loop ────────────────────────────────────────────────────────

    def _poll(self) -> None:
        while self._running:
            snapshot = self._sample()
            with self._lock:
                self._data.append(snapshot)
            time.sleep(1)

    def _sample(self) -> Dict[str, Any]:
        """Collect one hardware snapshot. Never raises — bad reads → zeroes."""
        snapshot: Dict[str, Any] = {
            "timestamp": time.time(),
            "cpu_pct": 0.0,
            "ram_gb": 0.0,
            "ram_pct": 0.0,
            "vram_gb": 0.0,
            "gpu_pct": 0,
            "gpu_temp_c": 0,
            "throttle": False,
        }

        if _HAS_PSUTIL:
            try:
                vm = psutil.virtual_memory()
                snapshot["cpu_pct"] = psutil.cpu_percent(interval=None)
                snapshot["ram_gb"] = round(vm.used / (1024 ** 3), 2)
                snapshot["ram_pct"] = round(vm.percent, 1)
            except Exception:
                pass

        if self._nvml_ok and self._gpu_handle is not None:
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(self._gpu_handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(self._gpu_handle)
                temp = pynvml.nvmlDeviceGetTemperature(
                    self._gpu_handle, pynvml.NVML_TEMPERATURE_GPU
                )
                snapshot["vram_gb"] = round(mem.used / (1024 ** 3), 2)
                snapshot["gpu_pct"] = util.gpu
                snapshot["gpu_temp_c"] = temp
                snapshot["throttle"] = temp >= _THROTTLE_TEMP_C
            except Exception:
                pass

        return snapshot

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background polling thread if not already running."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name="telemetry-sampler")
        self._thread.start()
        logger.info("Telemetry sampler started (throttle threshold: %d °C)", _THROTTLE_TEMP_C)

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def get_latest(self) -> Dict[str, Any]:
        """Return the most recent snapshot, or an empty dict if not started."""
        with self._lock:
            return dict(self._data[-1]) if self._data else {}

    def get_history(self) -> list[Dict[str, Any]]:
        """Return a copy of the full rolling history (oldest first)."""
        with self._lock:
            return list(self._data)


# Module-level singleton — created once, shared across all routes.
_sampler: Optional[TelemetrySampler] = None


def get_sampler() -> TelemetrySampler:
    """Return (and lazily create) the module-level sampler singleton."""
    global _sampler
    if _sampler is None:
        _sampler = TelemetrySampler()
    return _sampler

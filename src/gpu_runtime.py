"""NVIDIA/CUDA detection and llama.cpp runtime classification for native Windows.

Nothing here needs NVML (which fails on some Windows hosts with "Unknown Error"):
the CUDA driver API in nvcuda.dll is queried directly. No model is loaded.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
from pathlib import Path

_CUDA_LIB_RE = re.compile(rb"(cudart64_\d+|cublas64_\d+|cublasLt64_\d+)\.dll", re.IGNORECASE)
_DEVICE_RE = re.compile(r"^\s*([A-Za-z]+\d+):\s*(.+?)\s*\((\d+) MiB(?:, (\d+) MiB free)?\)", re.M)
_GIB = 1024 ** 3


def probe_cuda_driver(loader=None) -> dict:
    """Query the CUDA driver API: version, device name, VRAM, compute capability.

    ``loader`` returns the driver library (injectable for tests). A context is
    created on device 0 only to read free memory, then destroyed.
    """
    try:
        lib = loader() if loader else ctypes.WinDLL("nvcuda.dll")
    except (OSError, AttributeError) as exc:
        return {"available": False, "error": f"CUDA driver library not found: {exc}"}
    try:
        if lib.cuInit(0) != 0:
            return {"available": False, "error": "cuInit failed (no usable NVIDIA driver)"}
        version, count = ctypes.c_int(), ctypes.c_int()
        lib.cuDriverGetVersion(ctypes.byref(version))
        if lib.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
            return {"available": False, "error": "No CUDA device reported by the driver"}
        device = ctypes.c_int()
        lib.cuDeviceGet(ctypes.byref(device), 0)
        name = ctypes.create_string_buffer(128)
        lib.cuDeviceGetName(name, 128, device)
        total = ctypes.c_size_t()
        lib.cuDeviceTotalMem_v2(ctypes.byref(total), device)
        major, minor = ctypes.c_int(), ctypes.c_int()
        lib.cuDeviceGetAttribute(ctypes.byref(major), 75, device)
        lib.cuDeviceGetAttribute(ctypes.byref(minor), 76, device)
        free_bytes = None
        context = ctypes.c_void_p()
        if lib.cuCtxCreate_v2(ctypes.byref(context), 0, device) == 0:
            try:
                free, tot = ctypes.c_size_t(), ctypes.c_size_t()
                if lib.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(tot)) == 0:
                    free_bytes = int(free.value)
            finally:
                lib.cuCtxDestroy_v2(context)
        return {
            "available": True,
            "name": name.value.decode("ascii", "replace"),
            "device_count": count.value,
            "cuda_driver_version": version.value,
            "cuda_driver_major": version.value // 1000,
            "compute_capability": f"{major.value}.{minor.value}",
            "total_bytes": int(total.value),
            "free_bytes": free_bytes,
            "source": "cuda-driver-api",
        }
    except (OSError, AttributeError, ValueError) as exc:
        return {"available": False, "error": f"CUDA driver query failed: {exc}"}


def nvidia_driver_from_wmi(version: str) -> str:
    """Windows reports NVIDIA 621.01 as 32.0.16.2101: last digit of part 3 + part 4."""
    parts = str(version or "").strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return ""
    digits = parts[2][-1:] + parts[3].zfill(4)
    return f"{int(digits[:-2])}.{digits[-2:]}"


def required_cuda_libs(install_dir: Path) -> list[str]:
    """CUDA runtime/cuBLAS libraries ggml-cuda.dll imports (read from its image)."""
    backend = Path(install_dir) / "ggml-cuda.dll"
    if not backend.is_file():
        return []
    try:
        data = backend.read_bytes()
    except OSError:
        return []
    return sorted({m.group(0).decode("ascii").lower() for m in _CUDA_LIB_RE.finditer(data)})


def _lib_major(name: str) -> int:
    match = re.search(r"64_(\d+)\.dll$", name)
    return int(match.group(1)) if match else 0


def inspect_runtime(install_dir: Path, *, cuda_driver_major: int = 0) -> dict:
    """Classify an installed llama.cpp directory without running it."""
    directory = Path(install_dir)
    if not (directory / "ggml-cuda.dll").is_file():
        return {"build": "cpu", "cuda_ready": False, "required_libs": [], "missing_libs": [],
                "detail": "No CUDA backend (ggml-cuda.dll) present: CPU-only build."}
    required = required_cuda_libs(directory)
    present = {p.name.lower() for p in directory.iterdir() if p.is_file()}
    missing = [name for name in required if name not in present]
    majors = {_lib_major(name) for name in required if _lib_major(name)}
    cuda_major = max(majors) if majors else 0
    detail = "CUDA build with all required libraries present."
    ready = True
    if missing:
        ready = False
        detail = ("CUDA backend is present but its runtime libraries are missing "
                  f"({', '.join(missing)}); it would silently fall back to CPU.")
    elif cuda_driver_major and cuda_major and cuda_major > cuda_driver_major:
        ready = False
        detail = f"Build needs CUDA {cuda_major}; installed driver supports CUDA {cuda_driver_major}."
    return {"build": "cuda", "cuda_ready": ready, "cuda_major": cuda_major or None,
            "required_libs": required, "missing_libs": missing, "detail": detail}


def complete_cuda_runtime(install_dir: Path, source_dir: Path) -> dict:
    """Copy missing CUDA libraries from an already-downloaded runtime directory.

    Additive only: never overwrites an existing file. Sizes are verified after
    the copy and a partial copy is removed.
    """
    target, source = Path(install_dir), Path(source_dir)
    info = inspect_runtime(target)
    if info["build"] != "cuda":
        return {"ok": False, "copied": [], "error": "Not a CUDA build."}
    copied: list[str] = []
    for name in info["missing_libs"]:
        src = source / name
        if not src.is_file():
            return {"ok": False, "copied": copied, "error": f"{name} not found in {source}."}
        part = target / (name + ".part")
        try:
            shutil.copyfile(src, part)
            if part.stat().st_size != src.stat().st_size:
                raise OSError("size mismatch after copy")
            os.replace(part, target / name)
            copied.append(name)
        except OSError as exc:
            part.unlink(missing_ok=True)
            return {"ok": False, "copied": copied, "error": f"Could not copy {name}: {exc}"}
    return {"ok": True, "copied": copied, "error": None}


def verify_runtime_devices(exe: Path, *, timeout: float = 60) -> dict:
    """Run ``llama-server --list-devices``: loads backends, never a model."""
    exe = Path(exe)
    try:
        proc = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True,
                              timeout=timeout, cwd=str(exe.parent), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "devices": [], "gpu_devices": [], "detail": f"could not run: {exc}"}
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    devices = [{"id": m.group(1), "name": m.group(2), "total_mib": int(m.group(3)),
                "free_mib": int(m.group(4)) if m.group(4) else None}
               for m in _DEVICE_RE.finditer(text)]
    return {"ok": proc.returncode == 0, "devices": devices,
            "gpu_devices": [d for d in devices if not d["id"].upper().startswith("CPU")],
            "detail": "" if proc.returncode == 0 else (text.strip().splitlines() or ["failed"])[0]}


def plan_gpu_offload(*, requested_layers: int, model_bytes: int, layers: int,
                     kv_bytes_per_token: int, context: int, free_vram: int,
                     total_vram: int) -> dict:
    """Largest layer count that fits VRAM with headroom for the desktop and scratch.

    Reserve max(1.25 GiB, 15% of total) for the display driver and compute
    buffers. Output/embedding tensors are folded into the per-layer average.
    """
    reserve = max(int(1.25 * _GIB), total_vram * 15 // 100)
    budget = max(0, free_vram - reserve)
    per_layer = (model_bytes + kv_bytes_per_token * context) / layers
    fit = min(layers, int(budget // per_layer)) if per_layer > 0 else 0
    want = layers if requested_layers >= layers else requested_layers
    chosen = min(want, fit)
    return {"gpu_layers": chosen, "model_layers": layers, "max_fitting_layers": fit,
            "reserve_bytes": reserve, "budget_bytes": budget, "free_vram_bytes": free_vram,
            "per_layer_bytes": int(per_layer), "clamped": chosen < want,
            "fully_offloaded": chosen >= layers}


def gpu_status(runtime_root: Path, *, driver_loader=None) -> dict:
    """Detection snapshot plus a CPU/CUDA verdict for each installed runtime."""
    gpu = probe_cuda_driver(driver_loader)
    major = gpu.get("cuda_driver_major", 0) if gpu.get("available") else 0
    runtimes = []
    root = Path(runtime_root)
    try:
        children = sorted(c for c in root.iterdir() if c.is_dir()) if root.is_dir() else []
    except OSError:
        children = []
    for child in children:
        if child.name.endswith(".previous"):
            continue
        names = ("llama-server.exe", "llama-server")
        exe = next((p for n in names for p in [child / n] if p.is_file()), None)
        if exe is None:
            exe = next((m for n in names for m in sorted(child.rglob(n))[:1]), None)
        if exe:
            runtimes.append({"tag": child.name, "executable": str(exe),
                             "managed": (exe.parent / "odysseus-install.json").is_file(),
                             **inspect_runtime(exe.parent, cuda_driver_major=major)})
    usable = [r for r in runtimes if r["build"] == "cuda" and r["cuda_ready"]]
    usable.sort(key=lambda r: (r.get("cuda_major") or 0, r["managed"]))
    return {"gpu": gpu, "runtimes": runtimes,
            "selected_runtime": usable[-1]["executable"] if gpu.get("available") and usable else None}

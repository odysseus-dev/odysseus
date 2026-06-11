import os
import platform
import re
import shutil
import subprocess
import time
import shlex

from core.platform_compat import (
    NVIDIA_PATH_CANDIDATES,
    SSH_PATH_OVERRIDE,
    run_ssh_command,
)

CACHE_TTL = 24 * 3600  # 24 h — hardware probes are user-initiated via the Rescan button; bumped
                       # from 30 min so changing filters doesn't keep re-probing the rig every
                       # half-hour during a long session.


_remote_host = None  # set by detect_system(host=...)
_remote_port = None  # set by detect_system(ssh_port=...)
_remote_platform = None  # set by detect_system(platform=...): "windows", "linux", "termux"
_last_gpu_error = None  # set by _detect_nvidia() when nvidia-smi errors (driver mismatch, etc.)


def _run(cmd):
    try:
        if _remote_host:
            # Run command on remote host via SSH
            if isinstance(cmd, list):
                cmd_str = shlex.join(str(c) for c in cmd)
            else:
                cmd_str = cmd
            r = run_ssh_command(
                _remote_host,
                _remote_port,
                cmd_str,
                timeout=15,
                connect_timeout=5,
                strict_host_key_checking=False,
                text=True,
            )
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _group_gpus(gpus):
    """Group identical GPUs by (name, rounded VRAM).

    vLLM tensor-parallel only works across IDENTICAL GPUs, so a mixed box must
    be split into homogeneous pools. Each group carries the device indices so a
    serve command can pin CUDA_VISIBLE_DEVICES to exactly one pool. Biggest pool
    (by total VRAM) first — that's the sensible auto-default serving target.
    """
    groups = {}
    order = []
    for g in gpus:
        key = (g["name"], round(g["vram_gb"]))
        if key not in groups:
            groups[key] = {
                "name": g["name"],
                "vram_each": round(g["vram_gb"], 1),
                "count": 0,
                "indices": [],
            }
            order.append(key)
        groups[key]["count"] += 1
        groups[key]["indices"].append(g.get("index"))
    out = []
    for key in order:
        grp = groups[key]
        grp["vram_total"] = round(grp["vram_each"] * grp["count"], 1)
        out.append(grp)
    out.sort(key=lambda x: x["vram_total"], reverse=True)
    return out


def _detect_nvidia():
    global _last_gpu_error
    _last_gpu_error = None
    out = _run(["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"])
    # Fallback: a non-interactive shell (or WSL) often has a minimal PATH
    # that omits where nvidia-smi lives (/usr/bin, /usr/local/cuda/bin,
    # /usr/lib/wsl/lib), so the first call silently returns nothing →
    # "No GPU" on machines that DO have GPUs.
    # Retry through a login shell with the common CUDA bin dirs on PATH.
    if not out and _remote_host:
        out = _run(
            f"bash -lc '{SSH_PATH_OVERRIDE}"
            "nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits'"
        )
    # Last resort: call nvidia-smi by absolute path. Some hosts have a login
    # shell that isn't bash (or a profile that errors), so the bash -lc retry
    # above still comes back empty even though the binary is right there.
    # Also handles WSL where nvidia-smi lives at /usr/lib/wsl/lib/ — a path
    # that may not be in the server process's PATH.
    if not out:
        for _p in NVIDIA_PATH_CANDIDATES:
            # Use list form so subprocess.run (local) resolves the absolute path
            # correctly instead of treating the whole string as an executable name.
            if _remote_host:
                out = _run(f"{_p} --query-gpu=memory.total,name --format=csv,noheader,nounits")
            else:
                out = _run([_p, "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"])
            if out:
                break
    if not out:
        return None

    # nvidia-smi present but unable to talk to the driver (e.g. it was updated
    # without a reboot). It prints an error and no GPU rows — surface that as a
    # driver error rather than the misleading "No GPU".
    _low = out.lower()
    if ("nvml" in _low or "driver/library version mismatch" in _low
            or "couldn't communicate" in _low or "no devices were found" in _low
            or "failed to initialize" in _low):
        _last_gpu_error = out.strip().split("\n")[0][:140] or "NVIDIA driver error"
        return None

    gpus = []
    # Devices nvidia-smi lists with a real name but a non-numeric memory.total.
    unified = []
    # nvidia-smi lists GPUs in index order (0,1,2,...), so the row position is
    # the CUDA device index we'd pass to CUDA_VISIBLE_DEVICES.
    for idx, line in enumerate(out.strip().split("\n")):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vram_mb = float(parts[0])
                gpus.append({"index": idx, "name": parts[1], "vram_gb": vram_mb / 1024.0})
            except ValueError:
                # Grace Blackwell GB10 / DGX Spark and other unified-memory
                # NVIDIA parts report memory.total as "[N/A]"/"Not Supported"
                # because the GPU shares the system LPDDR pool instead of
                # carrying discrete VRAM. Don't drop the device — remember it so
                # we report a unified-memory GPU below rather than "No GPU" (#1340).
                if parts[1]:
                    unified.append({"index": idx, "name": parts[1]})
                continue

    if not gpus:
        if unified:
            # Unified-memory CUDA box: report the GPU backed by system RAM so the
            # Cookbook recommends models and serving works. The pool is shared
            # (not per-GPU discrete VRAM), so report the RAM total once.
            ram_gb = round(_get_ram_gb(), 1)
            gpus = [{"index": g["index"], "name": g["name"], "vram_gb": ram_gb} for g in unified]
            return {
                "gpu_name": gpus[0]["name"],
                "gpu_vram_gb": ram_gb,
                "gpu_count": len(gpus),
                "gpus": gpus,
                "gpu_groups": _group_gpus(gpus),
                "homogeneous": True,
                "backend": "cuda",
                "unified_memory": True,
            }
        return None
    total_vram = sum(g["vram_gb"] for g in gpus)
    groups = _group_gpus(gpus)
    return {
        "gpu_name": gpus[0]["name"],
        "gpu_vram_gb": round(total_vram, 1),
        "gpu_count": len(gpus),
        "gpus": gpus,
        "gpu_groups": groups,
        "homogeneous": len(groups) <= 1,
        "backend": "cuda",
    }


def classify_amd_gfx(gfx):
    """Map an AMD ISA target (e.g. "gfx1200") to (gfx, family).

    family is one of:
      "rdna"    — consumer Radeon RX (gfx10xx RDNA1/2, gfx11xx RDNA3, gfx12xx RDNA4)
      "cdna"    — datacenter Instinct (gfx908 MI100, gfx90a MI200, gfx94x/95x MI300+)
      "gcn"     — older GCN/Vega (gfx900/906)
      "unknown" — empty/unrecognized; callers must treat conservatively

    This drives the serving decision: vLLM/SGLang on ROCm are validated on CDNA
    but fragile on consumer RDNA (AWQ kernels largely unsupported, FP8 needs
    out-of-tree patches), so RDNA is steered to GGUF/llama.cpp.
    """
    gfx = (gfx or "").lower().strip()
    m = re.fullmatch(r"gfx(\d+[a-f]?)", gfx)
    if not m:
        return "", "unknown"
    digits = m.group(1)
    if digits[:2] in ("10", "11", "12"):
        return gfx, "rdna"
    if digits in ("908", "90a") or digits[:2] in ("94", "95"):
        return gfx, "cdna"
    if digits[:1] == "9":
        return gfx, "gcn"
    return gfx, "unknown"


def _detect_amd():
    """Detect AMD GPUs. Handles both discrete cards (with mem_info_vram_total)
    and APUs / unified-memory SoCs like Strix Halo (which expose
    mem_info_vis_vram_total instead, or only mem_info_gtt_total)."""
    def _read(path):
        if _remote_host:
            val = _run(["cat", path])
            return val.strip() if val else None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except Exception:
            return None

    def _list_drm_cards():
        if _remote_host:
            out = _run(["ls", "/sys/class/drm"])
            if not out:
                return []
            return [e for e in out.split() if e.startswith("card") and "-" not in e]
        try:
            return [e for e in os.listdir("/sys/class/drm") if e.startswith("card") and "-" not in e]
        except Exception:
            return []

    def _amd_arch():
        """Best-effort AMD GPU ISA + family from rocminfo.

        rocminfo is the source of truth; its GPU agents report a `Name: gfxNNNN`
        line (CPU agents report a brand string, not a gfx target), so the first
        gfx match is the GPU ISA. Returns (gfx, family) — see classify_amd_gfx.
        """
        info = _run(["rocminfo"]) or _run(["/opt/rocm/bin/rocminfo"]) or ""
        m = re.search(r"gfx\d+[a-f]?", info)
        return classify_amd_gfx(m.group(0) if m else "")

    try:
        cards = []
        is_apu = False
        for _cidx, entry in enumerate(_list_drm_cards()):
            base = f"/sys/class/drm/{entry}/device"
            vendor = _read(f"{base}/vendor")
            if vendor != "0x1002":
                continue
            # Discrete cards usually report real VRAM in mem_info_vram_total,
            # while some AMD APUs / Docker views expose a tiny vram_total and
            # the usable pool in vis_vram_total. Use the larger of those two;
            # only fall back to GTT if neither VRAM field is available.
            vram_raw = _read(f"{base}/mem_info_vram_total")
            vis_raw = _read(f"{base}/mem_info_vis_vram_total")
            gtt_raw = _read(f"{base}/mem_info_gtt_total")
            vram_val = int(vram_raw) if vram_raw and vram_raw.isdigit() else 0
            vis_val = int(vis_raw) if vis_raw and vis_raw.isdigit() else 0
            gtt_val = int(gtt_raw) if gtt_raw and gtt_raw.isdigit() else 0
            vram_bytes = max(vram_val, vis_val)
            if vram_bytes <= 0:
                vram_bytes = gtt_val
            if vis_val and vis_val >= vram_val:
                is_apu = True
            if vram_bytes <= 0:
                continue
            name = _read(f"{base}/product_name") or f"AMD GPU ({entry})"
            cards.append({"index": _cidx, "name": name, "vram_gb": vram_bytes / (1024**3)})

        if not cards:
            return None
        total_vram = sum(c["vram_gb"] for c in cards)
        groups = _group_gpus(cards)
        gfx, family = _amd_arch()
        # NOTE: for APUs with BIOS UMA carveout (e.g. Strix Halo), vis_vram_total
        # is the real usable GPU memory — it's physically backed but reserved
        # by BIOS so it doesn't appear in /proc/meminfo. Don't cap it at system
        # RAM: the two pools are separate from the OS's perspective.
        return {
            "gpu_name": cards[0]["name"],
            "gpu_vram_gb": round(total_vram, 1),
            "gpu_count": len(cards),
            "gpus": cards,
            "gpu_groups": groups,
            "homogeneous": len(groups) <= 1,
            "backend": "rocm",
            "unified_memory": is_apu,
            # AMD ISA/family so downstream can tell datacenter Instinct (CDNA,
            # where vLLM/SGLang run AWQ/GPTQ reliably) from consumer Radeon
            # (RDNA, where the practical path is GGUF via llama.cpp). Empty/
            # "unknown" when rocminfo isn't available — callers must treat
            # unknown conservatively, not assume vLLM works.
            "gpu_arch": gfx,
            "gpu_family": family,
        }
    except Exception:
        return None


def _detect_apple_silicon():
    """Detect Apple Silicon (M-series) GPUs.

    Macs have no discrete VRAM — the GPU shares the system's unified memory.
    We report a fraction of total RAM as the usable GPU budget (matching macOS's
    default Metal working-set limit) so the Cookbook recommends models that
    actually run on the GPU instead of classifying the machine as CPU-only.

    backend="metal" is what services.hwfit.fit and the serve-command generation
    key off of (they already understand MLX / llama.cpp-Metal). Works locally
    (platform.system()=="Darwin") and over SSH (uname -s == Darwin).
    """
    # Gate to macOS — locally via platform, remotely via uname.
    if _remote_host:
        if "darwin" not in (_run(["uname", "-s"]) or "").lower():
            return None
        arch = (_run(["uname", "-m"]) or "").lower()
    else:
        if platform.system() != "Darwin":
            return None
        arch = platform.machine().lower()

    # Only Apple Silicon (arm64) has a Metal GPU worth serving LLMs on; Intel
    # Macs fall through to the CPU path.
    if "arm" not in arch and "aarch64" not in arch:
        return None

    # Chip name, e.g. "Apple M4 Max" — carries the Pro/Max/Ultra variant that
    # the fit bandwidth table keys off of.
    brand = (_run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "Apple Silicon").strip()

    # Total unified memory in bytes.
    memsize = _run(["sysctl", "-n", "hw.memsize"])
    try:
        total_gb = int(memsize) / (1024**3) if memsize else 0.0
    except ValueError:
        total_gb = 0.0
    if total_gb <= 0:
        return None

    # Usable GPU budget. macOS lets Metal use most of unified memory, but the
    # default working-set limit scales with RAM: small machines have to keep
    # more back for the OS + app. These fractions track Apple's
    # recommendedMaxWorkingSetSize defaults across the lineup. Honour an
    # explicit override if the user raised it with
    # `sudo sysctl iogpu.wired_limit_mb=…`.
    if total_gb <= 16:
        frac = 0.67
    elif total_gb <= 64:
        frac = 0.75
    else:
        frac = 0.80
    vram_gb = round(total_gb * frac, 1)
    wired = _run(["sysctl", "-n", "iogpu.wired_limit_mb"])
    try:
        wired_mb = int(wired) if wired else 0
        if wired_mb > 0:
            vram_gb = round(wired_mb / 1024.0, 1)
    except ValueError:
        pass

    gpu = {"index": 0, "name": brand, "vram_gb": vram_gb}
    return {
        "gpu_name": brand,
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "gpus": [gpu],
        "gpu_groups": _group_gpus([gpu]),
        "homogeneous": True,
        "backend": "metal",
        # Unified memory: the "VRAM" above is carved out of system RAM, not a
        # separate pool — downstream fit logic uses this to avoid double-budgeting.
        "unified_memory": True,
    }


def _read_file(path):
    """Read a file, locally or via SSH."""
    if _remote_host:
        return _run(["cat", path])
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _parse_meminfo():
    """Parse /proc/meminfo into a dict of key -> KB values."""
    text = _read_file("/proc/meminfo")
    if not text:
        return {}
    result = {}
    for line in text.split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            parts = val.strip().split()
            if parts:
                try:
                    result[key.strip()] = int(parts[0])
                except ValueError:
                    pass
    return result


def _get_ram_gb():
    meminfo = _parse_meminfo()
    if "MemTotal" in meminfo:
        return meminfo["MemTotal"] / (1024**2)

    # os.sysconf only exists on Unix; on Windows it's absent (AttributeError)
    # and these constants aren't defined — guard so this never raises there.
    if not _remote_host and hasattr(os, "sysconf") and "SC_PHYS_PAGES" in getattr(os, "sysconf_names", {}):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages and page_size:
                return (pages * page_size) / (1024**3)
        except Exception:
            pass

    # macOS has no /proc/meminfo — fall back to sysctl (works locally and over
    # SSH to a remote Mac, where the sysconf path above isn't taken).
    memsize = _run(["sysctl", "-n", "hw.memsize"])
    if memsize:
        try:
            return int(memsize.strip()) / (1024**3)
        except ValueError:
            pass
    return 0.0


def _get_available_ram_gb():
    meminfo = _parse_meminfo()
    if "MemAvailable" in meminfo:
        return meminfo["MemAvailable"] / (1024**2)
    return _get_ram_gb() * 0.7


def _get_cpu_name():
    text = _read_file("/proc/cpuinfo")
    if text:
        for line in text.split("\n"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()

    # macOS has no /proc/cpuinfo — sysctl gives the chip name (e.g. "Apple M4").
    # Harmlessly returns nothing on Linux, so it's safe to try unconditionally.
    brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if brand and brand.strip():
        return brand.strip()

    if not _remote_host:
        return platform.processor() or "unknown"
    return "unknown"


def _get_cpu_count():
    if _remote_host:
        # nproc on Linux; hw.ncpu via sysctl on a remote Mac (no nproc there).
        out = _run(["nproc"]) or _run(["sysctl", "-n", "hw.ncpu"])
        if out:
            try:
                return int(out.strip())
            except ValueError:
                pass
        # fallback: count "processor" lines in /proc/cpuinfo
        text = _read_file("/proc/cpuinfo")
        if text:
            return sum(1 for line in text.split("\n") if line.startswith("processor"))
    return os.cpu_count() or 1


def _powershell_exe():
    """Pick the best PowerShell executable for LOCAL execution: prefer pwsh
    (PowerShell 7+), fall back to Windows PowerShell 5.1. Returns an absolute
    path so we don't depend on a particular PATH ordering."""
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell"


def _detect_windows():
    """Detect Windows hardware via PowerShell/WMI.

    Works for BOTH local (host="") and remote (SSH) detection:
      * remote  -> `_run` ships the string to the host over SSH.
      * local   -> `_run` executes a list argv directly (no shell quoting hell).
    """
    # Single PowerShell command that gathers all hardware info at once
    ps_cmd = (
        """
        $r = @{}
        $os = Get-CimInstance Win32_OperatingSystem
        $r.ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1048576, 1)
        $r.avail_gb = [math]::Round($os.FreePhysicalMemory / 1048576, 1)
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $r.cpu_name = $cpu.Name
        $r.cpu_cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
        $r.arch = $cpu.AddressWidth
        # GPU detection via nvidia-smi (fastest) or WMI fallback
        try { 
            $nv = nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null
            if ($LASTEXITCODE -eq 0 -and $nv) { 
                $gpus = @()
                foreach ($line in $nv -split "`n") { 
                    $p = $line -split ','
                    if ($p.Count -ge 2) { $gpus += [pscustomobject]@{name = $p[1].Trim(); vram_mb = [double]$p[0].Trim() } } 
                }
                $r.gpu_name = $gpus[0].name
                $r.gpu_vram_gb = [math]::Round(($gpus | Measure-Object -Property vram_mb -Sum).Sum / 1024, 1)
                $r.gpu_count = $gpus.Count
                $r.gpu_backend = 'cuda'
            } 
        }
        catch {}
        if (-not $r.gpu_name) { 
            $wmiGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1
            $GPUDriverKey = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*"
            $GPUDeviceID = $wmiGpu.PNPDeviceID.Split('&')[0..1] -join '&'
            $VRAMfromRegistry = Get-ItemProperty -Path $GPUDriverKey |
            Where-Object { $_.MatchingDeviceId -like "${GPUDeviceID}*" } |
            # Sometimes there happen to be multiple driver classes for the same gpu.
            Select-Object -ExpandProperty HardwareInformation.qwMemorySize -ErrorAction SilentlyContinue -First 1
            if ($wmiGpu) { 
                $r.gpu_name = $wmiGpu.Name
                # Edge case: driver is broken, otherwise $wmiGpu.AdapterRAM is redundant
                if ($VRAMfromRegistry -ge $wmiGpu.AdapterRAM) {
                    $r.gpu_vram_gb = [math]::Round($VRAMfromRegistry / 1073741824, 1)
                }
                else {
                    $r.gpu_vram_gb = [math]::Round($wmiGpu.AdapterRAM / 1073741824, 1)
                }
                $r.gpu_count = 1
                # WMI doesn't tell us CUDA/ROCm
                $r.gpu_backend = 'cpu_x86';
            } 
        }
        $r | ConvertTo-Json -Compress
    """
    )
    if _remote_host:
        # Remote: ship a single command string over SSH. The remote shell parses
        # the quoting; PowerShell on the far side runs the -Command payload.
        out = _run(f'powershell -Command "{ps_cmd}"')
    else:
        # Local: pass a LIST argv straight to subprocess so the OS hands ps_cmd
        # to PowerShell verbatim — no fragile string-level quote escaping. Prefer
        # pwsh (PS7), else Windows PowerShell 5.1.
        out = _run([_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", ps_cmd])
    if not out:
        return None
    import json as _json
    try:
        d = _json.loads(out)
        # PowerShell's Measure-Object .Sum / .Count come back as JSON numbers and
        # decode to float; the Linux path returns plain ints for these — coerce
        # so the dict shape (and downstream int math) matches across platforms.
        def _as_int(v, default):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        _cpu_name = (d.get("cpu_name") or "unknown")
        if isinstance(_cpu_name, str):
            _cpu_name = _cpu_name.strip() or "unknown"
        result = {
            "total_ram_gb": d.get("ram_gb", 0),
            "available_ram_gb": d.get("avail_gb", 0),
            "cpu_cores": _as_int(d.get("cpu_cores"), 1),
            "cpu_name": _cpu_name,
            "has_gpu": bool(d.get("gpu_name")),
            "gpu_name": d.get("gpu_name"),
            "gpu_vram_gb": d.get("gpu_vram_gb"),
            "gpu_count": _as_int(d.get("gpu_count"), 0),
            "backend": d.get("gpu_backend", "cpu_x86"),
            "homogeneous": True,
            "gpu_error": None,
            "platform": "windows",
        }
        # PowerShell only reports aggregate GPU info, not per-card detail, so we
        # can't tell a mixed box from a uniform one here — assume one homogeneous
        # pool spanning all reported GPUs (the common Windows case).
        _n = result["gpu_count"] or 0
        if result["has_gpu"] and _n > 0:
            _each = round((result["gpu_vram_gb"] or 0) / _n, 1)
            result["gpus"] = [
                {"index": i, "name": result["gpu_name"], "vram_gb": _each} for i in range(_n)
            ]
            result["gpu_groups"] = [{
                "name": result["gpu_name"],
                "vram_each": _each,
                "count": _n,
                "indices": list(range(_n)),
                "vram_total": result["gpu_vram_gb"],
            }]
            result["homogeneous"] = True
        return result
    except Exception:
        return None


_cache_by_host = {}  # host -> (timestamp, result)


def _cache_key(host: str, ssh_port: str, platform_name: str):
    """Build a stable cache key that isolates remote SSH context.

    Same host aliases can have different hardware due to visibility, forwarding etc.
    To avoid using the wrong cached hardware info, include the SSH port and platform in the cache key.
    """
    return (
        host or "_local",
        str(ssh_port or ""),
        str(platform_name or "").lower(),
    )


def detect_system(host="", ssh_port="", platform="", fresh=False):
    """Detect system hardware: RAM, CPU, GPU. Cached per host (hardware rarely
    changes, and probing a remote host over SSH is slow). Pass fresh=True to
    bypass the cache and re-probe (the "Rescan" button).
    If host is set (e.g. 'user@server'), runs detection commands over SSH.
    platform: "windows", "linux", "termux", or "" (auto-detect).
    """
    global _remote_host, _remote_port, _remote_platform

    cache_key = _cache_key(host, ssh_port, platform)
    now = time.time()
    if not fresh and cache_key in _cache_by_host:
        ts, cached = _cache_by_host[cache_key]
        if (now - ts) < CACHE_TTL:
            return cached

    _remote_host = host or None
    _remote_port = ssh_port or None
    _remote_platform = platform or None

    # Windows: single PowerShell command for all hardware info
    if _remote_platform == "windows" and _remote_host:
        result = _detect_windows()
        if result:
            _remote_host = None
            _remote_platform = None
            _cache_by_host[cache_key] = (now, result)
            return result
        # If Windows detection failed, return error
        result = {"error": f"Cannot connect to {host}", "host": host}
        _remote_host = None
        _remote_platform = None
        _cache_by_host[cache_key] = (now, result)
        return result

    # Local Windows: the Linux /proc + /sys + os.sysconf path returns 0 GB RAM,
    # "unknown" CPU and no GPU on Windows (and os.sysconf doesn't even exist),
    # so detect locally via PowerShell/WMI instead. _detect_windows() runs the
    # same probe used for remote Windows, but _run() executes it locally.
    if not _remote_host and os.name == "nt":
        result = _detect_windows()
        if result:
            _cache_by_host[cache_key] = (now, result)
            return result
        # PowerShell probe failed entirely — fall through to the generic path
        # below so we at least return a well-shaped dict rather than crashing.

    # Linux/Termux: existing multi-command detection
    total_ram = round(_get_ram_gb(), 1)
    # If remote host returns 0 RAM, connection likely failed
    if _remote_host and total_ram <= 0:
        result = {"error": f"Cannot connect to {host}", "host": host}
        _cache_by_host[cache_key] = (now, result)
        _remote_host = None
        _remote_platform = None
        return result
    available_ram = round(_get_available_ram_gb(), 1)
    cpu_cores = _get_cpu_count()
    cpu_name = _get_cpu_name()

    gpu_info = _detect_apple_silicon() or _detect_nvidia() or _detect_amd()

    if gpu_info:
        result = {
            "total_ram_gb": total_ram,
            "available_ram_gb": available_ram,
            "cpu_cores": cpu_cores,
            "cpu_name": cpu_name,
            "has_gpu": True,
            "gpu_name": gpu_info["gpu_name"],
            "gpu_vram_gb": gpu_info["gpu_vram_gb"],
            "gpu_count": gpu_info["gpu_count"],
            "gpus": gpu_info.get("gpus", []),
            "gpu_groups": gpu_info.get("gpu_groups", []),
            "homogeneous": gpu_info.get("homogeneous", True),
            "backend": gpu_info["backend"],
            # Apple Silicon / AMD APUs share system RAM with the GPU — carry the
            # flag through so callers can tell unified from discrete VRAM.
            "unified_memory": gpu_info.get("unified_memory", False),
        }
    else:
        if _remote_host:
            arch_out = _run(["uname", "-m"]) or ""
        else:
            import platform as _platform
            arch_out = _platform.machine().lower()
        backend = "cpu_arm" if "aarch64" in arch_out or "arm" in arch_out else "cpu_x86"
        result = {
            "total_ram_gb": total_ram,
            "available_ram_gb": available_ram,
            "cpu_cores": cpu_cores,
            "cpu_name": cpu_name,
            "has_gpu": False,
            "gpu_name": None,
            "gpu_vram_gb": None,
            "gpu_count": 0,
            "backend": backend,
            # Set when nvidia-smi exists but failed (e.g. driver/library
            # version mismatch) — lets the UI say "GPU driver error" instead
            # of the misleading "No GPU".
            "gpu_error": _last_gpu_error,
        }

    _remote_host = None
    _remote_platform = None
    _cache_by_host[cache_key] = (now, result)
    return result

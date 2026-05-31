import os
import platform
import subprocess
import time

CACHE_TTL = 1800  # 30 min — hardware rarely changes; use the Rescan button to force a re-probe


_remote_host = None  # set by detect_system(host=...)
_remote_port = None  # set by detect_system(ssh_port=...)
_remote_platform = None  # set by detect_system(platform=...): "windows", "linux", "termux"
_last_gpu_error = None  # set by _detect_nvidia() when nvidia-smi errors (driver mismatch, etc.)


def _run(cmd):
    try:
        if _remote_host:
            # Run command on remote host via SSH
            if isinstance(cmd, list):
                cmd_str = " ".join(cmd)
            else:
                cmd_str = cmd
            ssh_cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no"]
            if _remote_port and _remote_port != "22":
                ssh_cmd += ["-p", _remote_port]
            ssh_cmd += [_remote_host, cmd_str]
            r = subprocess.run(
                ssh_cmd,
                capture_output=True, text=True, timeout=15,
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
    # Remote fallback: a non-interactive SSH shell often has a minimal PATH
    # that omits where nvidia-smi lives (/usr/bin, /usr/local/cuda/bin), so the
    # first call silently returns nothing → "No GPU" on hosts that DO have GPUs.
    # Retry through a login shell with the common CUDA bin dirs on PATH.
    if not out and _remote_host:
        out = _run(
            "bash -lc 'export PATH=\"$PATH:/usr/bin:/usr/local/bin:/usr/local/cuda/bin\"; "
            "nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits'"
        )
    # Last resort: call nvidia-smi by absolute path. Some hosts have a login
    # shell that isn't bash (or a profile that errors), so the bash -lc retry
    # above still comes back empty even though the binary is right there.
    if not out and _remote_host:
        for _p in ("/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi", "/usr/local/cuda/bin/nvidia-smi"):
            out = _run(f"{_p} --query-gpu=memory.total,name --format=csv,noheader,nounits")
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
    # nvidia-smi lists GPUs in index order (0,1,2,...), so the row position is
    # the CUDA device index we'd pass to CUDA_VISIBLE_DEVICES.
    for idx, line in enumerate(out.strip().split("\n")):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vram_mb = float(parts[0])
                gpus.append({"index": idx, "name": parts[1], "vram_gb": vram_mb / 1024.0})
            except ValueError:
                continue

    if not gpus:
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


def _detect_amd():
    """Detect AMD GPUs. Handles both discrete cards (with mem_info_vram_total)
    and APUs / unified-memory SoCs like Strix Halo (which expose
    mem_info_vis_vram_total instead, or only mem_info_gtt_total)."""
    def _read(path):
        if _remote_host:
            val = _run(["cat", path])
            return val.strip() if val else None
        try:
            with open(path) as f:
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
        }
    except Exception:
        return None


def _read_file(path):
    """Read a file, locally or via SSH."""
    if _remote_host:
        return _run(["cat", path])
    try:
        with open(path) as f:
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

    if not _remote_host:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages and page_size:
                return (pages * page_size) / (1024**3)
        except Exception:
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

    if not _remote_host:
        return platform.processor() or "unknown"
    return "unknown"


def _get_cpu_count():
    if _remote_host:
        out = _run(["nproc"])
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


def _detect_windows():
    """Detect Windows hardware using PowerShell.

    Works both locally (when Odysseus runs on Windows) and remotely
    (over SSH). When _remote_host is set, the command is piped through
    SSH by the existing _run() helper.
    """
    # Single PowerShell command that gathers all hardware info at once
    ps_cmd = (
        "$r = @{}; "
        "$os = Get-CimInstance Win32_OperatingSystem; "
        "$r.ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1048576, 1); "
        "$r.avail_gb = [math]::Round($os.FreePhysicalMemory / 1048576, 1); "
        "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "$r.cpu_name = $cpu.Name; "
        "$r.cpu_cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum; "
        "$r.arch = $cpu.AddressWidth; "
        # GPU detection via nvidia-smi (fastest) or WMI fallback
        "try { "
        "  $nv = nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null; "
        "  if ($LASTEXITCODE -eq 0 -and $nv) { "
        "    $gpus = @(); "
        "    foreach ($line in $nv -split \"`n\") { "
        "      $p = $line -split ','; "
        "      if ($p.Count -ge 2) { $gpus += @{name=$p[1].Trim(); vram_mb=[double]$p[0].Trim()} } "
        "    }; "
        "    $r.gpu_name = $gpus[0].name; "
        "    $r.gpu_vram_gb = [math]::Round(($gpus | Measure-Object -Property vram_mb -Sum).Sum / 1024, 1); "
        "    $r.gpu_count = $gpus.Count; "
        "    $r.gpu_backend = 'cuda'; "
        "  } "
        "} catch {}; "
        "if (-not $r.gpu_name) { "
        "  $wmiGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1; "
        "  if ($wmiGpu) { "
        "    $r.gpu_name = $wmiGpu.Name; "
        "    $r.gpu_vram_gb = [math]::Round($wmiGpu.AdapterRAM / 1073741824, 1); "
        "    $r.gpu_count = 1; "
        "    $r.gpu_backend = 'cpu_x86'; "  # WMI doesn't tell us CUDA/ROCm
        "  } "
        "}; "
        "$r | ConvertTo-Json -Compress"
    )
    # _run routes through SSH when _remote_host is set; locally it runs
    # the command directly — so this works in both contexts.
    out = _run(f'powershell -Command "{ps_cmd}"')
    if not out:
        return None
    import json as _json
    try:
        d = _json.loads(out)
        result = {
            "total_ram_gb": d.get("ram_gb", 0),
            "available_ram_gb": d.get("avail_gb", 0),
            "cpu_cores": d.get("cpu_cores", 1),
            "cpu_name": d.get("cpu_name", "unknown"),
            "has_gpu": bool(d.get("gpu_name")),
            "gpu_name": d.get("gpu_name"),
            "gpu_vram_gb": d.get("gpu_vram_gb"),
            "gpu_count": d.get("gpu_count", 0),
            "backend": d.get("gpu_backend", "cpu_x86"),
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


def detect_system(host="", ssh_port="", platform="", fresh=False):
    """Detect system hardware: RAM, CPU, GPU. Cached per host (hardware rarely
    changes, and probing a remote host over SSH is slow). Pass fresh=True to
    bypass the cache and re-probe (the "Rescan" button).
    If host is set (e.g. 'user@server'), runs detection commands over SSH.
    platform: "windows", "linux", "termux", or "" (auto-detect).
    """
    global _remote_host, _remote_port, _remote_platform

    cache_key = host or "_local"
    now = time.time()
    if not fresh and cache_key in _cache_by_host:
        ts, cached = _cache_by_host[cache_key]
        if (now - ts) < CACHE_TTL:
            return cached

    _remote_host = host or None
    _remote_port = ssh_port or None
    _remote_platform = platform or None

    # Windows detection — works for remote Windows hosts (via SSH) AND
    # for local detection when Odysseus itself runs on Windows.
    _use_windows = (
        (_remote_platform == "windows" and _remote_host)
        or (not _remote_host and os.name == "nt")
    )
    if _use_windows:
        result = _detect_windows()
        if result:
            _remote_host = None
            _remote_platform = None
            _cache_by_host[cache_key] = (now, result)
            return result
        if _remote_host:
            # Remote Windows detection failed — connection error
            result = {"error": f"Cannot connect to {host}", "host": host}
            _remote_host = None
            _remote_platform = None
            _cache_by_host[cache_key] = (now, result)
            return result
        # Local Windows detection failed — fall through to generic path

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

    gpu_info = _detect_nvidia() or _detect_amd()

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

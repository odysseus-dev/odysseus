// services/hwfit/hardware.rs  <- services/hwfit/hardware.py
//! System hardware detection: RAM, CPU, GPU.
//!
//! Detects local or (over SSH) remote host hardware by **shelling out**
//! (`nvidia-smi`, `nproc`, `uname`, PowerShell) and reading `/proc` + `/sys`
//! directly. `psutil` is **not** used by the Python source, so there is nothing
//! to map: the Rust port uses `std::process::Command` + `std::fs` faithfully.
//!
//! ## Honest substitutions (documented drift)
//!
//!  * **Subprocess timeout** — Python passes `timeout=10` (local) / `timeout=15`
//!    (SSH) to `subprocess.run`. `std::process` has no wait-with-timeout, so the
//!    port spawns the child and polls until a deadline, then `kill()`s it (no
//!    `wait-timeout` crate, per the Cargo.toml note). A killed/timed-out command
//!    is treated exactly like the Python `except Exception` branch: it yields
//!    `None`.
//!  * **Local RAM fallback** — Python's `_get_ram_gb` falls back to
//!    `os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")` when
//!    `/proc/meminfo` is absent (i.e. the non-Linux dev/runner host). `nix`
//!    v0.31 in this tree lacks the `fs` feature that exposes `sysconf`, so the
//!    `sysinfo` crate is the honest portable substitute. The `/proc` + `/sys` +
//!    ssh-remote Linux paths stay faithful `std::fs` / `std::process`.
//!  * **`platform.processor()` / `platform.machine()`** — Python reads these
//!    from the OS; the port maps to `std::env::consts::ARCH` (a static target
//!    string), which feeds the `"aarch64"/"arm" -> cpu_arm` backend test
//!    faithfully and slots into the `or "unknown"` fallback for the CPU name.
//!
//! ## State model
//!
//! Python uses mutable module globals (`_remote_host` / `_remote_port` /
//! `_remote_platform` / `_last_gpu_error`) that `detect_system` sets and clears.
//! The Rust port threads a `Detector` context struct through `_run` / `_read` /
//! the `_detect_*` helpers instead — cleaner than `Lazy<Mutex>` globals and
//! immune to cross-thread interleaving. The per-host result cache stays a
//! process-global `Lazy<Mutex<HashMap>>` to match Python's `_cache_by_host`.

use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// `CACHE_TTL = 24 * 3600` — 24 h. Hardware probes are user-initiated via the
/// Rescan button; bumped from 30 min so changing filters doesn't keep re-probing
/// the rig every half-hour during a long session.
pub const CACHE_TTL: u64 = 24 * 3600;

/// Per-host result cache: `host -> (timestamp, result)`.
///
/// Mirrors the Python module global `_cache_by_host = {}`. The timestamp is an
/// `Instant` (monotonic) rather than `time.time()` — TTL comparison is purely
/// relative, so this is parity-safe and immune to wall-clock jumps.
static CACHE_BY_HOST: Lazy<Mutex<HashMap<String, (Instant, Value)>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Detection context — replaces the Python mutable module globals.
///
/// Python sets `_remote_host` / `_remote_port` / `_remote_platform` (and the
/// out-param `_last_gpu_error`) on the module at the top of `detect_system`,
/// clears them at the end, and the `_run` / `_read` / `_detect_*` helpers read
/// them implicitly. Here they are explicit fields threaded through.
struct Detector {
    /// Set from `detect_system(host=...)`; `None` for local detection.
    remote_host: Option<String>,
    /// Set from `detect_system(ssh_port=...)`.
    remote_port: Option<String>,
    /// Set from `detect_system(platform=...)`: "windows" / "linux" / "termux".
    remote_platform: Option<String>,
    /// Set by `_detect_nvidia()` when `nvidia-smi` errors (driver mismatch, etc).
    last_gpu_error: Option<String>,
}

impl Detector {
    fn new(host: Option<String>, port: Option<String>, platform: Option<String>) -> Self {
        Detector {
            remote_host: host,
            remote_port: port,
            remote_platform: platform,
            last_gpu_error: None,
        }
    }

    /// `_run(cmd)` — run a command (locally, or on the remote host via SSH),
    /// returning trimmed stdout on a zero exit, else `None`.
    ///
    /// ```python
    /// def _run(cmd):
    ///     try:
    ///         if _remote_host:
    ///             ... ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no ...
    ///             r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
    ///         else:
    ///             r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    ///         if r.returncode == 0:
    ///             return r.stdout.strip()
    ///     except Exception:
    ///         pass
    ///     return None
    /// ```
    ///
    /// `cmd` may be either an argv list (`Cmd::Args`) or a single command
    /// string (`Cmd::Str`), matching the two Python call styles. For a local
    /// single string we run it through `sh -c` (the Python list-vs-str
    /// distinction is only observable for the SSH branch, where a `list` is
    /// `" ".join`-ed into one remote command string anyway).
    fn run(&self, cmd: Cmd) -> Option<String> {
        if let Some(host) = self.remote_host.as_deref() {
            // Run command on remote host via SSH.
            // `cmd_str = " ".join(cmd)` if list else `cmd`.
            let cmd_str = match &cmd {
                Cmd::Args(parts) => parts.join(" "),
                Cmd::Str(s) => s.clone(),
            };
            let mut ssh_cmd: Vec<String> = vec![
                "ssh".to_string(),
                "-o".to_string(),
                "ConnectTimeout=5".to_string(),
                "-o".to_string(),
                "StrictHostKeyChecking=no".to_string(),
            ];
            // if _remote_port and _remote_port != "22": ssh_cmd += ["-p", port]
            if let Some(port) = self.remote_port.as_deref() {
                if !port.is_empty() && port != "22" {
                    ssh_cmd.push("-p".to_string());
                    ssh_cmd.push(port.to_string());
                }
            }
            ssh_cmd.push(host.to_string());
            ssh_cmd.push(cmd_str);
            run_argv(&ssh_cmd, Duration::from_secs(15))
        } else {
            // Local: `subprocess.run(cmd, ..., timeout=10)`.
            match cmd {
                Cmd::Args(parts) => run_argv(&parts, Duration::from_secs(10)),
                Cmd::Str(s) => {
                    let argv = vec!["sh".to_string(), "-c".to_string(), s];
                    run_argv(&argv, Duration::from_secs(10))
                }
            }
        }
    }

    /// `_read_file(path)` — read a file locally or via SSH (returns the full
    /// file contents for the local branch; the SSH branch is trimmed by `_run`).
    ///
    /// ```python
    /// def _read_file(path):
    ///     if _remote_host:
    ///         return _run(["cat", path])
    ///     try:
    ///         with open(path) as f:
    ///             return f.read()
    ///     except Exception:
    ///         return None
    /// ```
    fn read_file(&self, path: &str) -> Option<String> {
        if self.remote_host.is_some() {
            self.run(Cmd::Args(vec!["cat".to_string(), path.to_string()]))
        } else {
            std::fs::read_to_string(path).ok()
        }
    }

    /// `_parse_meminfo()` — parse `/proc/meminfo` into a `key -> KB` integer map.
    ///
    /// ```python
    /// def _parse_meminfo():
    ///     text = _read_file("/proc/meminfo")
    ///     if not text: return {}
    ///     result = {}
    ///     for line in text.split("\n"):
    ///         if ":" in line:
    ///             key, val = line.split(":", 1)
    ///             parts = val.strip().split()
    ///             if parts:
    ///                 try: result[key.strip()] = int(parts[0])
    ///                 except ValueError: pass
    ///     return result
    /// ```
    fn parse_meminfo(&self) -> HashMap<String, i64> {
        let mut result: HashMap<String, i64> = HashMap::new();
        let text = match self.read_file("/proc/meminfo") {
            Some(t) if !t.is_empty() => t,
            _ => return result,
        };
        for line in text.split('\n') {
            if let Some(colon) = line.find(':') {
                let key = &line[..colon];
                let val = &line[colon + 1..];
                if let Some(first) = val.split_whitespace().next() {
                    if let Ok(n) = first.parse::<i64>() {
                        result.insert(key.trim().to_string(), n);
                    }
                }
            }
        }
        result
    }

    /// `_get_ram_gb()` — total RAM in GB.
    ///
    /// ```python
    /// def _get_ram_gb():
    ///     meminfo = _parse_meminfo()
    ///     if "MemTotal" in meminfo:
    ///         return meminfo["MemTotal"] / (1024**2)
    ///     if not _remote_host:
    ///         try:
    ///             pages = os.sysconf("SC_PHYS_PAGES"); page_size = os.sysconf("SC_PAGE_SIZE")
    ///             if pages and page_size:
    ///                 return (pages * page_size) / (1024**3)
    ///         except Exception: pass
    ///     return 0.0
    /// ```
    ///
    /// `MemTotal` is in KB so `/ 1024**2` -> GB. The `sysconf` fallback (local
    /// host with no `/proc/meminfo`) is the honest `sysinfo` substitute: it
    /// returns total physical RAM in bytes, so `/ 1024**3` -> GB.
    ///
    /// macOS has no `/proc/meminfo` — fall back to `sysctl -n hw.memsize` (works
    /// locally and over SSH to a remote Mac, where the `sysconf`/`sysinfo` path
    /// above isn't taken). Faithful to the Python tail:
    ///
    /// ```python
    /// memsize = _run(["sysctl", "-n", "hw.memsize"])
    /// if memsize:
    ///     try: return int(memsize.strip()) / (1024**3)
    ///     except ValueError: pass
    /// return 0.0
    /// ```
    fn get_ram_gb(&self) -> f64 {
        let meminfo = self.parse_meminfo();
        if let Some(kb) = meminfo.get("MemTotal") {
            return (*kb as f64) / (1024f64 * 1024f64);
        }
        if self.remote_host.is_none() {
            if let Some(bytes) = sysinfo_total_ram_bytes() {
                if bytes > 0 {
                    return (bytes as f64) / (1024f64 * 1024f64 * 1024f64);
                }
            }
        }
        // macOS sysctl fallback (also reached over SSH to a remote Mac).
        if let Some(memsize) = self.run(Cmd::Args(vec![
            "sysctl".to_string(),
            "-n".to_string(),
            "hw.memsize".to_string(),
        ])) {
            if !memsize.is_empty() {
                if let Ok(n) = memsize.trim().parse::<i128>() {
                    return (n as f64) / (1024f64 * 1024f64 * 1024f64);
                }
            }
        }
        0.0
    }

    /// `_get_available_ram_gb()` — available RAM in GB.
    ///
    /// ```python
    /// def _get_available_ram_gb():
    ///     meminfo = _parse_meminfo()
    ///     if "MemAvailable" in meminfo:
    ///         return meminfo["MemAvailable"] / (1024**2)
    ///     return _get_ram_gb() * 0.7
    /// ```
    ///
    /// Drift note: when `/proc/meminfo` is absent (local non-Linux host) Python
    /// falls back to `_get_ram_gb() * 0.7`. The port mirrors that exactly — it
    /// does NOT consult `sysinfo.available_memory()` here, to preserve the
    /// Python value precisely. (`sysinfo` is reached only via `_get_ram_gb`'s
    /// own `sysconf` substitute.)
    fn get_available_ram_gb(&self) -> f64 {
        let meminfo = self.parse_meminfo();
        if let Some(kb) = meminfo.get("MemAvailable") {
            return (*kb as f64) / (1024f64 * 1024f64);
        }
        self.get_ram_gb() * 0.7
    }

    /// `_get_cpu_name()`.
    ///
    /// ```python
    /// def _get_cpu_name():
    ///     text = _read_file("/proc/cpuinfo")
    ///     if text:
    ///         for line in text.split("\n"):
    ///             if line.startswith("model name"):
    ///                 return line.split(":", 1)[1].strip()
    ///     # macOS has no /proc/cpuinfo — sysctl gives the chip name (e.g. "Apple
    ///     # M4"). Harmlessly returns nothing on Linux, so safe to try always.
    ///     brand = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    ///     if brand and brand.strip():
    ///         return brand.strip()
    ///     if not _remote_host:
    ///         return platform.processor() or "unknown"
    ///     return "unknown"
    /// ```
    fn get_cpu_name(&self) -> String {
        if let Some(text) = self.read_file("/proc/cpuinfo") {
            for line in text.split('\n') {
                if line.starts_with("model name") {
                    if let Some(colon) = line.find(':') {
                        return line[colon + 1..].trim().to_string();
                    }
                }
            }
        }
        // macOS has no /proc/cpuinfo — sysctl gives the chip name (e.g. "Apple
        // M4"). Harmlessly returns nothing on Linux, so it's safe to try
        // unconditionally.
        if let Some(brand) = self.run(Cmd::Args(vec![
            "sysctl".to_string(),
            "-n".to_string(),
            "machdep.cpu.brand_string".to_string(),
        ])) {
            let brand = brand.trim();
            if !brand.is_empty() {
                return brand.to_string();
            }
        }
        if self.remote_host.is_none() {
            let proc = platform_processor();
            if !proc.is_empty() {
                return proc;
            }
            return "unknown".to_string();
        }
        "unknown".to_string()
    }

    /// `_get_cpu_count()`.
    ///
    /// ```python
    /// def _get_cpu_count():
    ///     if _remote_host:
    ///         # nproc on Linux; hw.ncpu via sysctl on a remote Mac (no nproc there).
    ///         out = _run(["nproc"]) or _run(["sysctl", "-n", "hw.ncpu"])
    ///         if out:
    ///             try: return int(out.strip())
    ///             except ValueError: pass
    ///         text = _read_file("/proc/cpuinfo")
    ///         if text:
    ///             return sum(1 for line in text.split("\n") if line.startswith("processor"))
    ///     return os.cpu_count() or 1
    /// ```
    fn get_cpu_count(&self) -> i64 {
        if self.remote_host.is_some() {
            // nproc on Linux; hw.ncpu via sysctl on a remote Mac (no nproc there).
            let out = self
                .run(Cmd::Args(vec!["nproc".to_string()]))
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    self.run(Cmd::Args(vec![
                        "sysctl".to_string(),
                        "-n".to_string(),
                        "hw.ncpu".to_string(),
                    ]))
                });
            if let Some(out) = out {
                if let Ok(n) = out.trim().parse::<i64>() {
                    return n;
                }
            }
            // fallback: count "processor" lines in /proc/cpuinfo
            if let Some(text) = self.read_file("/proc/cpuinfo") {
                return text
                    .split('\n')
                    .filter(|line| line.starts_with("processor"))
                    .count() as i64;
            }
        }
        // `os.cpu_count() or 1`
        std::thread::available_parallelism()
            .map(|n| n.get() as i64)
            .unwrap_or(1)
    }

    /// `_detect_nvidia()`.
    ///
    /// Returns the GPU-info `Value` map, or `None` when no NVIDIA GPU is found.
    /// Sets `self.last_gpu_error` (Python's `_last_gpu_error` global) when
    /// `nvidia-smi` is present but cannot talk to the driver.
    fn detect_nvidia(&mut self) -> Option<Value> {
        // global _last_gpu_error; _last_gpu_error = None
        self.last_gpu_error = None;
        let mut out = self.run(Cmd::Args(vec![
            "nvidia-smi".to_string(),
            "--query-gpu=memory.total,name".to_string(),
            "--format=csv,noheader,nounits".to_string(),
        ]));

        // Remote fallback: a non-interactive SSH shell often has a minimal PATH
        // that omits where nvidia-smi lives (/usr/bin, /usr/local/cuda/bin), so
        // the first call silently returns nothing -> "No GPU" on hosts that DO
        // have GPUs. Retry through a login shell with the common CUDA bin dirs.
        if out.as_deref().is_none_or(str::is_empty) && self.remote_host.is_some() {
            out = self.run(Cmd::Str(
                "bash -lc 'export PATH=\"$PATH:/usr/bin:/usr/local/bin:/usr/local/cuda/bin\"; \
                 nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits'"
                    .to_string(),
            ));
        }
        // Last resort: call nvidia-smi by absolute path. Some hosts have a login
        // shell that isn't bash (or a profile that errors), so the bash -lc retry
        // above still comes back empty even though the binary is right there.
        if out.as_deref().is_none_or(str::is_empty) && self.remote_host.is_some() {
            for p in [
                "/usr/bin/nvidia-smi",
                "/usr/local/bin/nvidia-smi",
                "/usr/local/cuda/bin/nvidia-smi",
            ] {
                out = self.run(Cmd::Str(format!(
                    "{p} --query-gpu=memory.total,name --format=csv,noheader,nounits"
                )));
                if out.as_deref().is_some_and(|s| !s.is_empty()) {
                    break;
                }
            }
        }
        let out = match out {
            Some(s) if !s.is_empty() => s,
            _ => return None,
        };

        // nvidia-smi present but unable to talk to the driver (e.g. it was
        // updated without a reboot). It prints an error and no GPU rows —
        // surface that as a driver error rather than the misleading "No GPU".
        let low = out.to_lowercase();
        if low.contains("nvml")
            || low.contains("driver/library version mismatch")
            || low.contains("couldn't communicate")
            || low.contains("no devices were found")
            || low.contains("failed to initialize")
        {
            // _last_gpu_error = out.strip().split("\n")[0][:140] or "NVIDIA driver error"
            let first_line = out.trim().split('\n').next().unwrap_or("");
            let truncated: String = first_line.chars().take(140).collect();
            self.last_gpu_error = Some(if truncated.is_empty() {
                "NVIDIA driver error".to_string()
            } else {
                truncated
            });
            return None;
        }

        let mut gpus: Vec<Value> = Vec::new();
        // Devices nvidia-smi lists with a real name but a non-numeric memory.total.
        let mut unified: Vec<Value> = Vec::new();
        // nvidia-smi lists GPUs in index order (0,1,2,...), so the row position
        // is the CUDA device index we'd pass to CUDA_VISIBLE_DEVICES.
        for (idx, line) in out.trim().split('\n').enumerate() {
            let parts: Vec<String> = line.split(',').map(|p| p.trim().to_string()).collect();
            if parts.len() >= 2 {
                // try: vram_mb = float(parts[0]) ... except ValueError:
                if let Ok(vram_mb) = parts[0].parse::<f64>() {
                    gpus.push(json!({
                        "index": idx,
                        "name": parts[1],
                        "vram_gb": vram_mb / 1024.0,
                    }));
                } else {
                    // Grace Blackwell GB10 / DGX Spark and other unified-memory
                    // NVIDIA parts report memory.total as "[N/A]"/"Not Supported"
                    // because the GPU shares the system LPDDR pool instead of
                    // carrying discrete VRAM. Don't drop the device — remember it
                    // so we report a unified-memory GPU below rather than "No GPU"
                    // (#1340).
                    if !parts[1].is_empty() {
                        unified.push(json!({"index": idx, "name": parts[1]}));
                    }
                }
            }
        }

        if gpus.is_empty() {
            if !unified.is_empty() {
                // Unified-memory CUDA box: report the GPU backed by system RAM so
                // the Cookbook recommends models and serving works. The pool is
                // shared (not per-GPU discrete VRAM), so report the RAM total once.
                let ram_gb = round1(self.get_ram_gb());
                let gpus: Vec<Value> = unified
                    .iter()
                    .map(|g| {
                        json!({
                            "index": g["index"],
                            "name": g["name"],
                            "vram_gb": ram_gb,
                        })
                    })
                    .collect();
                let gpu_name = gpus[0]["name"].clone();
                let gpu_count = gpus.len();
                let gpu_groups = group_gpus(&gpus);
                return Some(json!({
                    "gpu_name": gpu_name,
                    "gpu_vram_gb": ram_gb,
                    "gpu_count": gpu_count,
                    "gpus": gpus,
                    "gpu_groups": gpu_groups,
                    "homogeneous": true,
                    "backend": "cuda",
                    "unified_memory": true,
                }));
            }
            return None;
        }
        let total_vram: f64 = gpus.iter().map(gpu_vram).sum();
        let groups = group_gpus(&gpus);
        Some(json!({
            "gpu_name": gpus[0]["name"],
            "gpu_vram_gb": round1(total_vram),
            "gpu_count": gpus.len(),
            "gpus": gpus,
            "gpu_groups": groups,
            "homogeneous": groups_len(&groups) <= 1,
            "backend": "cuda",
        }))
    }

    /// `_detect_apple_silicon()` — detect Apple Silicon (M-series) GPUs.
    ///
    /// Macs have no discrete VRAM — the GPU shares the system's unified memory.
    /// We report a fraction of total RAM as the usable GPU budget (matching
    /// macOS's default Metal working-set limit) so the Cookbook recommends models
    /// that actually run on the GPU instead of classifying the machine as
    /// CPU-only.
    ///
    /// `backend="metal"` is what services.hwfit.fit and the serve-command
    /// generation key off of (they already understand MLX / llama.cpp-Metal).
    /// Works locally (`platform.system()=="Darwin"`) and over SSH (`uname -s ==
    /// Darwin`).
    fn detect_apple_silicon(&self) -> Option<Value> {
        // Gate to macOS — locally via platform, remotely via uname.
        let arch: String = if self.remote_host.is_some() {
            // if "darwin" not in (_run(["uname", "-s"]) or "").lower(): return None
            let sysname = self
                .run(Cmd::Args(vec!["uname".to_string(), "-s".to_string()]))
                .unwrap_or_default()
                .to_lowercase();
            if !sysname.contains("darwin") {
                return None;
            }
            self.run(Cmd::Args(vec!["uname".to_string(), "-m".to_string()]))
                .unwrap_or_default()
                .to_lowercase()
        } else {
            // if platform.system() != "Darwin": return None
            if std::env::consts::OS != "macos" {
                return None;
            }
            platform_machine().to_lowercase()
        };

        // Only Apple Silicon (arm64) has a Metal GPU worth serving LLMs on; Intel
        // Macs fall through to the CPU path.
        if !arch.contains("arm") && !arch.contains("aarch64") {
            return None;
        }

        // Chip name, e.g. "Apple M4 Max" — carries the Pro/Max/Ultra variant that
        // the fit bandwidth table keys off of.
        // brand = (_run(["sysctl","-n","machdep.cpu.brand_string"]) or "Apple Silicon").strip()
        // `or` treats an empty result as falsy, so an empty sysctl -> "Apple Silicon".
        let brand = self
            .run(Cmd::Args(vec![
                "sysctl".to_string(),
                "-n".to_string(),
                "machdep.cpu.brand_string".to_string(),
            ]))
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "Apple Silicon".to_string());
        let brand = brand.trim().to_string();

        // Total unified memory in bytes.
        let memsize = self.run(Cmd::Args(vec![
            "sysctl".to_string(),
            "-n".to_string(),
            "hw.memsize".to_string(),
        ]));
        // total_gb = int(memsize) / (1024**3) if memsize else 0.0  (except ValueError: 0.0)
        let total_gb = match memsize.as_deref() {
            Some(s) if !s.is_empty() => match s.trim().parse::<i128>() {
                Ok(n) => (n as f64) / (1024f64 * 1024f64 * 1024f64),
                Err(_) => 0.0,
            },
            _ => 0.0,
        };
        if total_gb <= 0.0 {
            return None;
        }

        // Usable GPU budget. macOS lets Metal use most of unified memory, but the
        // default working-set limit scales with RAM: small machines have to keep
        // more back for the OS + app. These fractions track Apple's
        // recommendedMaxWorkingSetSize defaults across the lineup. Honour an
        // explicit override if the user raised it with
        // `sudo sysctl iogpu.wired_limit_mb=…`.
        let frac = if total_gb <= 16.0 {
            0.67
        } else if total_gb <= 64.0 {
            0.75
        } else {
            0.80
        };
        let mut vram_gb = round1(total_gb * frac);
        let wired = self.run(Cmd::Args(vec![
            "sysctl".to_string(),
            "-n".to_string(),
            "iogpu.wired_limit_mb".to_string(),
        ]));
        // try: wired_mb = int(wired) if wired else 0; if wired_mb > 0: vram_gb = round(wired_mb/1024,1)
        if let Some(s) = wired.as_deref() {
            if !s.is_empty() {
                if let Ok(wired_mb) = s.trim().parse::<i64>() {
                    if wired_mb > 0 {
                        vram_gb = round1((wired_mb as f64) / 1024.0);
                    }
                }
            }
        }

        let gpu = json!({"index": 0, "name": brand.clone(), "vram_gb": vram_gb});
        let gpus = vec![gpu];
        let gpu_groups = group_gpus(&gpus);
        Some(json!({
            "gpu_name": brand,
            "gpu_vram_gb": vram_gb,
            "gpu_count": 1,
            "gpus": gpus,
            "gpu_groups": gpu_groups,
            "homogeneous": true,
            "backend": "metal",
            // Unified memory: the "VRAM" above is carved out of system RAM, not a
            // separate pool — downstream fit logic uses this to avoid
            // double-budgeting.
            "unified_memory": true,
        }))
    }

    /// `_detect_amd()` — detect AMD GPUs (discrete cards and APUs / unified-memory
    /// SoCs like Strix Halo, which expose `mem_info_vis_vram_total` instead, or
    /// only `mem_info_gtt_total`).
    ///
    /// The whole body is wrapped in Python's `try/except Exception: return None`;
    /// the Rust helpers each return `Option`, and the iteration cannot panic, so
    /// the function is total.
    fn detect_amd(&self) -> Option<Value> {
        // def _read(path): SSH `cat` or local open, .strip()
        let read = |path: &str| -> Option<String> {
            if self.remote_host.is_some() {
                // `val.strip() if val else None`
                self.run(Cmd::Args(vec!["cat".to_string(), path.to_string()]))
                    .map(|v| v.trim().to_string())
                    .filter(|v| !v.is_empty())
            } else {
                std::fs::read_to_string(path)
                    .ok()
                    .map(|s| s.trim().to_string())
            }
        };

        // def _list_drm_cards(): cardN entries without "-"
        let list_drm_cards = || -> Vec<String> {
            if self.remote_host.is_some() {
                match self.run(Cmd::Args(vec![
                    "ls".to_string(),
                    "/sys/class/drm".to_string(),
                ])) {
                    Some(out) if !out.is_empty() => out
                        .split_whitespace()
                        .filter(|e| e.starts_with("card") && !e.contains('-'))
                        .map(|e| e.to_string())
                        .collect(),
                    _ => Vec::new(),
                }
            } else {
                match std::fs::read_dir("/sys/class/drm") {
                    Ok(rd) => rd
                        .filter_map(|e| e.ok())
                        .filter_map(|e| e.file_name().into_string().ok())
                        .filter(|e| e.starts_with("card") && !e.contains('-'))
                        .collect(),
                    Err(_) => Vec::new(),
                }
            }
        };

        // def _amd_arch(): best-effort AMD GPU ISA + family from rocminfo.
        //
        // rocminfo is the source of truth; its GPU agents report a `Name: gfxNNNN`
        // line (CPU agents report a brand string, not a gfx target), so the first
        // gfx match is the GPU ISA. Returns (gfx, family) — see classify_amd_gfx.
        //
        //   info = _run(["rocminfo"]) or _run(["/opt/rocm/bin/rocminfo"]) or ""
        //   m = re.search(r"gfx\d+[a-f]?", info)
        //   return classify_amd_gfx(m.group(0) if m else "")
        let amd_arch = || -> (String, String) {
            // `_run(...) or _run(...) or ""` — `or` skips a falsy (empty) result,
            // so a zero-exit-but-empty rocminfo falls through to the next probe.
            let info = self
                .run(Cmd::Args(vec!["rocminfo".to_string()]))
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    self.run(Cmd::Args(vec!["/opt/rocm/bin/rocminfo".to_string()]))
                        .filter(|s| !s.is_empty())
                })
                .unwrap_or_default();
            match search_gfx(&info) {
                Some(g) => classify_amd_gfx(&g),
                None => classify_amd_gfx(""),
            }
        };

        let mut cards: Vec<Value> = Vec::new();
        let mut is_apu = false;
        for (cidx, entry) in list_drm_cards().into_iter().enumerate() {
            let base = format!("/sys/class/drm/{entry}/device");
            let vendor = read(&format!("{base}/vendor"));
            // if vendor != "0x1002": continue
            if vendor.as_deref() != Some("0x1002") {
                continue;
            }
            // Discrete cards usually report real VRAM in mem_info_vram_total,
            // while some AMD APUs / Docker views expose a tiny vram_total and the
            // usable pool in vis_vram_total. Use the larger of those two; only
            // fall back to GTT if neither VRAM field is available.
            let vram_raw = read(&format!("{base}/mem_info_vram_total"));
            let vis_raw = read(&format!("{base}/mem_info_vis_vram_total"));
            let gtt_raw = read(&format!("{base}/mem_info_gtt_total"));
            // int(x) if x and x.isdigit() else 0
            let vram_val = parse_digits_i128(vram_raw.as_deref());
            let vis_val = parse_digits_i128(vis_raw.as_deref());
            let gtt_val = parse_digits_i128(gtt_raw.as_deref());
            let mut vram_bytes = vram_val.max(vis_val);
            if vram_bytes <= 0 {
                vram_bytes = gtt_val;
            }
            // if vis_val and vis_val >= vram_val: is_apu = True
            if vis_val != 0 && vis_val >= vram_val {
                is_apu = true;
            }
            if vram_bytes <= 0 {
                continue;
            }
            // name = _read(product_name) or f"AMD GPU ({entry})"
            let name = read(&format!("{base}/product_name"))
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| format!("AMD GPU ({entry})"));
            cards.push(json!({
                "index": cidx,
                "name": name,
                "vram_gb": (vram_bytes as f64) / (1024f64 * 1024f64 * 1024f64),
            }));
        }

        if cards.is_empty() {
            return None;
        }
        let total_vram: f64 = cards.iter().map(gpu_vram).sum();
        let groups = group_gpus(&cards);
        let (gfx, family) = amd_arch();
        // NOTE: for APUs with BIOS UMA carveout (e.g. Strix Halo),
        // vis_vram_total is the real usable GPU memory — it's physically backed
        // but reserved by BIOS so it doesn't appear in /proc/meminfo. Don't cap
        // it at system RAM: the two pools are separate from the OS's view.
        Some(json!({
            "gpu_name": cards[0]["name"],
            "gpu_vram_gb": round1(total_vram),
            "gpu_count": cards.len(),
            "gpus": cards,
            "gpu_groups": groups,
            "homogeneous": groups_len(&groups) <= 1,
            "backend": "rocm",
            "unified_memory": is_apu,
            // AMD ISA/family so downstream can tell datacenter Instinct (CDNA,
            // where vLLM/SGLang run AWQ/GPTQ reliably) from consumer Radeon
            // (RDNA, where the practical path is GGUF via llama.cpp). Empty/
            // "unknown" when rocminfo isn't available — callers must treat
            // unknown conservatively, not assume vLLM works.
            "gpu_arch": gfx,
            "gpu_family": family,
        }))
    }

    /// `_detect_windows()` — detect Windows hardware in a single SSH call using
    /// PowerShell. Returns the result `Value` map or `None`.
    fn detect_windows(&self) -> Option<Value> {
        // Single PowerShell command that gathers all hardware info at once.
        let ps_cmd = concat!(
            "$r = @{}; ",
            "$os = Get-CimInstance Win32_OperatingSystem; ",
            "$r.ram_gb = [math]::Round($os.TotalVisibleMemorySize / 1048576, 1); ",
            "$r.avail_gb = [math]::Round($os.FreePhysicalMemory / 1048576, 1); ",
            "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1; ",
            "$r.cpu_name = $cpu.Name; ",
            "$r.cpu_cores = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum; ",
            "$r.arch = $cpu.AddressWidth; ",
            // GPU detection via nvidia-smi (fastest) or WMI fallback
            "try { ",
            "  $nv = nvidia-smi --query-gpu=memory.total,name --format=csv,noheader,nounits 2>$null; ",
            "  if ($LASTEXITCODE -eq 0 -and $nv) { ",
            "    $gpus = @(); ",
            "    foreach ($line in $nv -split \"`n\") { ",
            "      $p = $line -split ','; ",
            "      if ($p.Count -ge 2) { $gpus += [pscustomobject]@{name=$p[1].Trim(); vram_mb=[double]$p[0].Trim()} } ",
            "    }; ",
            "    $r.gpu_name = $gpus[0].name; ",
            "    $r.gpu_vram_gb = [math]::Round(($gpus | Measure-Object -Property vram_mb -Sum).Sum / 1024, 1); ",
            "    $r.gpu_count = $gpus.Count; ",
            "    $r.gpu_backend = 'cuda'; ",
            "  } ",
            "} catch {}; ",
            "if (-not $r.gpu_name) { ",
            "  $wmiGpu = Get-CimInstance Win32_VideoController | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1; ",
            "  if ($wmiGpu) { ",
            "    $r.gpu_name = $wmiGpu.Name; ",
            "    $r.gpu_vram_gb = [math]::Round($wmiGpu.AdapterRAM / 1073741824, 1); ",
            "    $r.gpu_count = 1; ",
            "    $r.gpu_backend = 'cpu_x86'; ",
            "  } ",
            "}; ",
            "$r | ConvertTo-Json -Compress"
        );
        let out = if self.remote_host.is_some() {
            // Remote: ship a single command string over SSH. The remote shell
            // parses the quoting; PowerShell on the far side runs the -Command
            // payload.
            // out = _run(f'powershell -Command "{ps_cmd}"')
            self.run(Cmd::Str(format!("powershell -Command \"{ps_cmd}\"")))
        } else {
            // Local: pass a LIST argv straight to subprocess so the OS hands
            // ps_cmd to PowerShell verbatim — no fragile string-level quote
            // escaping. Prefer pwsh (PS7), else Windows PowerShell 5.1.
            self.run(Cmd::Args(vec![
                powershell_exe(),
                "-NoProfile".to_string(),
                "-NonInteractive".to_string(),
                "-Command".to_string(),
                ps_cmd.to_string(),
            ]))
        };
        let out = match out {
            Some(s) if !s.is_empty() => s,
            _ => return None,
        };
        // d = json.loads(out)  (try/except Exception: return None)
        let d: Value = match serde_json::from_str(&out) {
            Ok(v) => v,
            Err(_) => return None,
        };

        // result = { ... d.get(...) defaults ... }
        // has_gpu = bool(d.get("gpu_name")) -> truthy: non-null and non-empty str
        let gpu_name = d.get("gpu_name").cloned().unwrap_or(Value::Null);
        let has_gpu = is_truthy(&gpu_name);
        // _cpu_name = (d.get("cpu_name") or "unknown"); if isinstance str:
        //     _cpu_name = _cpu_name.strip() or "unknown"
        let cpu_name = match d.get("cpu_name") {
            Some(Value::String(s)) => {
                let t = s.trim();
                if t.is_empty() {
                    json!("unknown")
                } else {
                    json!(t)
                }
            }
            // `(d.get("cpu_name") or "unknown")`: null/absent/falsy -> "unknown",
            // a non-string truthy value passes through unchanged.
            Some(v) if is_truthy(v) => v.clone(),
            _ => json!("unknown"),
        };
        let mut result = Map::new();
        result.insert(
            "total_ram_gb".to_string(),
            d.get("ram_gb").cloned().unwrap_or(json!(0)),
        );
        result.insert(
            "available_ram_gb".to_string(),
            d.get("avail_gb").cloned().unwrap_or(json!(0)),
        );
        // PowerShell's Measure-Object .Sum / .Count come back as JSON numbers and
        // decode to float; the Linux path returns plain ints for these — coerce
        // so the dict shape (and downstream int math) matches across platforms.
        // _as_int(d.get("cpu_cores"), 1)
        result.insert(
            "cpu_cores".to_string(),
            json!(as_int(d.get("cpu_cores"), 1)),
        );
        result.insert("cpu_name".to_string(), cpu_name);
        result.insert("has_gpu".to_string(), Value::Bool(has_gpu));
        result.insert("gpu_name".to_string(), gpu_name.clone());
        result.insert(
            "gpu_vram_gb".to_string(),
            d.get("gpu_vram_gb").cloned().unwrap_or(Value::Null),
        );
        // _as_int(d.get("gpu_count"), 0)
        result.insert("gpu_count".to_string(), json!(as_int(d.get("gpu_count"), 0)));
        result.insert(
            "backend".to_string(),
            d.get("gpu_backend").cloned().unwrap_or(json!("cpu_x86")),
        );
        result.insert("homogeneous".to_string(), Value::Bool(true));
        result.insert("gpu_error".to_string(), Value::Null);

        // PowerShell only reports aggregate GPU info, not per-card detail, so we
        // can't tell a mixed box from a uniform one here — assume one homogeneous
        // pool spanning all reported GPUs (the common Windows case).
        // _n = result["gpu_count"] or 0
        let n = result.get("gpu_count").and_then(value_as_i64).unwrap_or(0);
        if has_gpu && n > 0 {
            // _each = round((result["gpu_vram_gb"] or 0) / _n, 1)
            let total = result
                .get("gpu_vram_gb")
                .and_then(value_as_f64)
                .unwrap_or(0.0);
            let each = round1(total / (n as f64));
            let gpus: Vec<Value> = (0..n)
                .map(|i| {
                    json!({
                        "index": i,
                        "name": result.get("gpu_name").cloned().unwrap_or(Value::Null),
                        "vram_gb": each,
                    })
                })
                .collect();
            result.insert("gpus".to_string(), Value::Array(gpus));
            // gpu_groups: one homogeneous pool (insertion-ordered keys).
            let mut group = Map::new();
            group.insert(
                "name".to_string(),
                result.get("gpu_name").cloned().unwrap_or(Value::Null),
            );
            group.insert("vram_each".to_string(), json!(each));
            group.insert("count".to_string(), json!(n));
            group.insert(
                "indices".to_string(),
                Value::Array((0..n).map(|i| json!(i)).collect()),
            );
            group.insert(
                "vram_total".to_string(),
                result.get("gpu_vram_gb").cloned().unwrap_or(Value::Null),
            );
            result.insert(
                "gpu_groups".to_string(),
                Value::Array(vec![Value::Object(group)]),
            );
            result.insert("homogeneous".to_string(), Value::Bool(true));
        }
        Some(Value::Object(result))
    }
}

/// A command to run: either an argv list or a single command string.
///
/// Mirrors the Python `cmd` parameter which is sometimes a `list` and sometimes
/// a single string (`isinstance(cmd, list)` check in `_run`).
enum Cmd {
    Args(Vec<String>),
    Str(String),
}

/// `_group_gpus(gpus)` — group identical GPUs by `(name, round(vram_gb))`.
///
/// vLLM tensor-parallel only works across IDENTICAL GPUs, so a mixed box must
/// be split into homogeneous pools. Each group carries the device indices so a
/// serve command can pin CUDA_VISIBLE_DEVICES to exactly one pool. Biggest pool
/// (by total VRAM) first — that's the sensible auto-default serving target.
///
/// ```python
/// def _group_gpus(gpus):
///     groups = {}; order = []
///     for g in gpus:
///         key = (g["name"], round(g["vram_gb"]))
///         if key not in groups:
///             groups[key] = {"name":..., "vram_each": round(...,1), "count":0, "indices":[]}
///             order.append(key)
///         groups[key]["count"] += 1
///         groups[key]["indices"].append(g.get("index"))
///     out = []
///     for key in order:
///         grp = groups[key]
///         grp["vram_total"] = round(grp["vram_each"] * grp["count"], 1)
///         out.append(grp)
///     out.sort(key=lambda x: x["vram_total"], reverse=True)
///     return out
/// ```
///
/// Returns an `Array` of group `Value` maps with the Python key insertion order
/// preserved: name / vram_each / count / indices / vram_total.
fn group_gpus(gpus: &[Value]) -> Value {
    // The dict key is `(name, round(vram_gb))`. Python's `round` uses
    // banker's rounding (round-half-to-even); `round_py` mirrors it. Insertion
    // order is preserved with a parallel `order` Vec of keys.
    let mut groups: HashMap<(String, i64), Map<String, Value>> = HashMap::new();
    let mut order: Vec<(String, i64)> = Vec::new();
    for g in gpus {
        let name = g
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let vram_gb = gpu_vram(g);
        let key = (name.clone(), round_py(vram_gb, 0) as i64);
        if !groups.contains_key(&key) {
            let mut m = Map::new();
            m.insert("name".to_string(), json!(name));
            m.insert("vram_each".to_string(), json!(round1(vram_gb)));
            m.insert("count".to_string(), json!(0));
            m.insert("indices".to_string(), Value::Array(Vec::new()));
            groups.insert(key.clone(), m);
            order.push(key.clone());
        }
        let m = groups.get_mut(&key).unwrap();
        let count = m.get("count").and_then(|v| v.as_i64()).unwrap_or(0) + 1;
        m.insert("count".to_string(), json!(count));
        if let Some(Value::Array(arr)) = m.get_mut("indices") {
            // g.get("index") -> Null if absent
            arr.push(g.get("index").cloned().unwrap_or(Value::Null));
        }
    }
    let mut out: Vec<Value> = Vec::new();
    for key in &order {
        let mut grp = groups.remove(key).unwrap();
        let vram_each = grp.get("vram_each").and_then(value_as_f64).unwrap_or(0.0);
        let count = grp.get("count").and_then(|v| v.as_i64()).unwrap_or(0);
        grp.insert(
            "vram_total".to_string(),
            json!(round1(vram_each * (count as f64))),
        );
        out.push(Value::Object(grp));
    }
    // out.sort(key=lambda x: x["vram_total"], reverse=True)
    // Python's sort is stable; sort_by with a stable comparator preserves the
    // relative order of equal-vram_total groups (matching `order`).
    out.sort_by(|a, b| {
        let av = a.get("vram_total").and_then(value_as_f64).unwrap_or(0.0);
        let bv = b.get("vram_total").and_then(value_as_f64).unwrap_or(0.0);
        bv.partial_cmp(&av).unwrap_or(std::cmp::Ordering::Equal)
    });
    Value::Array(out)
}

/// `classify_amd_gfx(gfx)` — map an AMD ISA target (e.g. `"gfx1200"`) to
/// `(gfx, family)`.
///
/// family is one of:
///   "rdna"    — consumer Radeon RX (gfx10xx RDNA1/2, gfx11xx RDNA3, gfx12xx RDNA4)
///   "cdna"    — datacenter Instinct (gfx908 MI100, gfx90a MI200, gfx94x/95x MI300+)
///   "gcn"     — older GCN/Vega (gfx900/906)
///   "unknown" — empty/unrecognized; callers must treat conservatively
///
/// This drives the serving decision: vLLM/SGLang on ROCm are validated on CDNA
/// but fragile on consumer RDNA (AWQ kernels largely unsupported, FP8 needs
/// out-of-tree patches), so RDNA is steered to GGUF/llama.cpp.
///
/// ```python
/// def classify_amd_gfx(gfx):
///     gfx = (gfx or "").lower().strip()
///     m = re.fullmatch(r"gfx(\d+[a-f]?)", gfx)
///     if not m:
///         return "", "unknown"
///     digits = m.group(1)
///     if digits[:2] in ("10", "11", "12"): return gfx, "rdna"
///     if digits in ("908", "90a") or digits[:2] in ("94", "95"): return gfx, "cdna"
///     if digits[:1] == "9": return gfx, "gcn"
///     return gfx, "unknown"
/// ```
///
/// Returns `(gfx, family)` where `gfx` is the lowercased/stripped target (empty
/// on no match). The regex `gfx(\d+[a-f]?)` is reproduced by hand to avoid a
/// regex dependency: `gfx`, then one-or-more ASCII digits, then an optional
/// single `a`-`f` char, and nothing else (`fullmatch`).
pub fn classify_amd_gfx(gfx: &str) -> (String, String) {
    let gfx = gfx.to_lowercase();
    let gfx = gfx.trim();
    // m = re.fullmatch(r"gfx(\d+[a-f]?)", gfx)
    let digits = match gfx_fullmatch_digits(gfx) {
        Some(d) => d,
        None => return (String::new(), "unknown".to_string()),
    };
    let gfx = gfx.to_string();
    // digits[:2] — first two chars (may be shorter if `digits` is short).
    let first2: String = digits.chars().take(2).collect();
    if first2 == "10" || first2 == "11" || first2 == "12" {
        return (gfx, "rdna".to_string());
    }
    // digits in ("908", "90a") or digits[:2] in ("94", "95")
    if digits == "908" || digits == "90a" || first2 == "94" || first2 == "95" {
        return (gfx, "cdna".to_string());
    }
    // digits[:1] == "9"
    if digits.starts_with('9') {
        return (gfx, "gcn".to_string());
    }
    (gfx, "unknown".to_string())
}

/// Reproduce `re.search(r"gfx\d+[a-f]?", info)`: find the FIRST substring that
/// is `gfx` + one-or-more ASCII digits + an optional single `a`-`f`. Returns the
/// matched substring (e.g. `"gfx90a"`), else `None`. Case-sensitive, matching
/// the Python regex (rocminfo emits lowercase `gfx` targets).
fn search_gfx(info: &str) -> Option<String> {
    let bytes = info.as_bytes();
    let n = bytes.len();
    let mut start = 0;
    while start + 3 <= n {
        if &bytes[start..start + 3] == b"gfx" {
            let mut i = start + 3;
            let digit_start = i;
            while i < n && bytes[i].is_ascii_digit() {
                i += 1;
            }
            // \d+ requires at least one digit.
            if i > digit_start {
                // Optional single [a-f] (greedy: the regex consumes it if present).
                if i < n && (b'a'..=b'f').contains(&bytes[i]) {
                    i += 1;
                }
                return Some(info[start..i].to_string());
            }
        }
        start += 1;
    }
    None
}

/// Reproduce `re.fullmatch(r"gfx(\d+[a-f]?)", gfx)`: the whole string must be
/// `gfx` + one-or-more ASCII digits + an optional single `a`-`f`. Returns the
/// captured group (`\d+[a-f]?`) on a full match, else `None`. Expects an
/// already-lowercased/trimmed input (the `[a-f]` class is lowercase-only).
fn gfx_fullmatch_digits(gfx: &str) -> Option<String> {
    let rest = gfx.strip_prefix("gfx")?;
    let bytes = rest.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    // One or more digits.
    let mut i = 0;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i == 0 {
        // \d+ requires at least one digit.
        return None;
    }
    // Optional single [a-f].
    if i < bytes.len() {
        let c = bytes[i];
        if (b'a'..=b'f').contains(&c) {
            i += 1;
        }
    }
    // fullmatch: nothing may remain.
    if i != bytes.len() {
        return None;
    }
    Some(rest.to_string())
}

/// `detect_system(host="", ssh_port="", platform="", fresh=False)`.
///
/// Detect system hardware: RAM, CPU, GPU. Cached per host (hardware rarely
/// changes, and probing a remote host over SSH is slow). Pass `fresh=true` to
/// bypass the cache and re-probe (the "Rescan" button). If `host` is set (e.g.
/// `user@server`), runs detection commands over SSH. `platform`: "windows",
/// "linux", "termux", or "" (auto-detect).
///
/// Returns the result `Value` map (preserving Python dict key order).
pub fn detect_system(host: &str, ssh_port: &str, platform: &str, fresh: bool) -> Value {
    // cache_key = host or "_local"
    let cache_key = if host.is_empty() {
        "_local".to_string()
    } else {
        host.to_string()
    };
    // now = time.time() — used in the Python only for relative TTL math, so the
    // port keeps a monotonic `Instant` (parity-safe, immune to clock jumps).
    let now = Instant::now();
    if !fresh {
        if let Ok(cache) = CACHE_BY_HOST.lock() {
            if let Some((ts, cached)) = cache.get(&cache_key) {
                // if (now - ts) < CACHE_TTL: return cached
                if now.duration_since(*ts) < Duration::from_secs(CACHE_TTL) {
                    return cached.clone();
                }
            }
        }
    }

    // _remote_host = host or None; _remote_port = ssh_port or None;
    // _remote_platform = platform or None
    let mut det = Detector::new(
        if host.is_empty() {
            None
        } else {
            Some(host.to_string())
        },
        if ssh_port.is_empty() {
            None
        } else {
            Some(ssh_port.to_string())
        },
        if platform.is_empty() {
            None
        } else {
            Some(platform.to_string())
        },
    );

    let store = |key: &str, result: &Value| {
        if let Ok(mut cache) = CACHE_BY_HOST.lock() {
            cache.insert(key.to_string(), (now, result.clone()));
        }
    };

    // Windows: single PowerShell command for all hardware info.
    if det.remote_platform.as_deref() == Some("windows") && det.remote_host.is_some() {
        if let Some(result) = det.detect_windows() {
            // _remote_host = None; _remote_platform = None (det dropped at fn end)
            store(&cache_key, &result);
            return result;
        }
        // If Windows detection failed, return error.
        let result = json!({
            "error": format!("Cannot connect to {host}"),
            "host": host,
        });
        store(&cache_key, &result);
        return result;
    }

    // Local Windows: the Linux /proc + /sys + os.sysconf path returns 0 GB RAM,
    // "unknown" CPU and no GPU on Windows (and os.sysconf doesn't even exist), so
    // detect locally via PowerShell/WMI instead. _detect_windows() runs the same
    // probe used for remote Windows, but _run() executes it locally.
    if det.remote_host.is_none() && std::env::consts::OS == "windows" {
        if let Some(result) = det.detect_windows() {
            store(&cache_key, &result);
            return result;
        }
        // PowerShell probe failed entirely — fall through to the generic path
        // below so we at least return a well-shaped dict rather than crashing.
    }

    // Linux/Termux: existing multi-command detection.
    let total_ram = round1(det.get_ram_gb());
    // If remote host returns 0 RAM, connection likely failed.
    if det.remote_host.is_some() && total_ram <= 0.0 {
        let result = json!({
            "error": format!("Cannot connect to {host}"),
            "host": host,
        });
        store(&cache_key, &result);
        return result;
    }
    let available_ram = round1(det.get_available_ram_gb());
    let cpu_cores = det.get_cpu_count();
    let cpu_name = det.get_cpu_name();

    // gpu_info = _detect_apple_silicon() or _detect_nvidia() or _detect_amd()
    let gpu_info = det
        .detect_apple_silicon()
        .or_else(|| det.detect_nvidia())
        .or_else(|| det.detect_amd());

    let result = if let Some(gpu_info) = gpu_info {
        json!({
            "total_ram_gb": total_ram,
            "available_ram_gb": available_ram,
            "cpu_cores": cpu_cores,
            "cpu_name": cpu_name,
            "has_gpu": true,
            "gpu_name": gpu_info["gpu_name"],
            "gpu_vram_gb": gpu_info["gpu_vram_gb"],
            "gpu_count": gpu_info["gpu_count"],
            "gpus": gpu_info.get("gpus").cloned().unwrap_or_else(|| json!([])),
            "gpu_groups": gpu_info.get("gpu_groups").cloned().unwrap_or_else(|| json!([])),
            "homogeneous": gpu_info.get("homogeneous").cloned().unwrap_or(json!(true)),
            "backend": gpu_info["backend"],
            // Apple Silicon / AMD APUs share system RAM with the GPU — carry the
            // flag through so callers can tell unified from discrete VRAM.
            "unified_memory": gpu_info.get("unified_memory").cloned().unwrap_or(json!(false)),
        })
    } else {
        // arch detection
        let arch_out = if det.remote_host.is_some() {
            det.run(Cmd::Args(vec!["uname".to_string(), "-m".to_string()]))
                .unwrap_or_default()
        } else {
            platform_machine().to_lowercase()
        };
        // backend = "cpu_arm" if "aarch64" in arch_out or "arm" in arch_out else "cpu_x86"
        let backend = if arch_out.contains("aarch64") || arch_out.contains("arm") {
            "cpu_arm"
        } else {
            "cpu_x86"
        };
        json!({
            "total_ram_gb": total_ram,
            "available_ram_gb": available_ram,
            "cpu_cores": cpu_cores,
            "cpu_name": cpu_name,
            "has_gpu": false,
            "gpu_name": Value::Null,
            "gpu_vram_gb": Value::Null,
            "gpu_count": 0,
            "backend": backend,
            // Set when nvidia-smi exists but failed (e.g. driver/library version
            // mismatch) — lets the UI say "GPU driver error" instead of the
            // misleading "No GPU".
            "gpu_error": det.last_gpu_error.clone(),
        })
    };

    // _remote_host = None; _remote_platform = None (det dropped at fn end)
    store(&cache_key, &result);
    result
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// Run an argv with a wall-clock timeout, returning trimmed stdout on a zero
/// exit code, else `None`.
///
/// `std::process` has no wait-with-timeout, so we spawn the child, poll until a
/// deadline, then `kill()` it (no `wait-timeout` crate, per the Cargo.toml
/// note). A killed / errored / non-zero process yields `None` — exactly like the
/// Python `except Exception` branch and the `if r.returncode == 0` guard. This
/// is the honest substitute documented at the top of the module.
fn run_argv(argv: &[String], timeout: Duration) -> Option<String> {
    use std::process::{Command, Stdio};

    if argv.is_empty() {
        return None;
    }
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    let mut child = cmd.spawn().ok()?;

    // Watchdog: poll for completion until the deadline, then kill.
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_status)) => break,
            Ok(None) => {
                if start.elapsed() >= timeout {
                    // subprocess.TimeoutExpired -> caught by `except Exception` -> None
                    let _ = child.kill();
                    let _ = child.wait();
                    return None;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(_) => return None,
        }
    }

    // Re-collect the completed output via the captured pipes.
    let output = child.wait_with_output().ok()?;
    if output.status.success() {
        // r.stdout.strip() — text=True decodes UTF-8; lossy here for safety.
        Some(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        None
    }
}

/// `_powershell_exe()` — pick the best PowerShell executable for LOCAL
/// execution: prefer `pwsh` (PowerShell 7+), fall back to Windows PowerShell
/// 5.1. Returns an absolute path so we don't depend on PATH ordering.
///
/// ```python
/// def _powershell_exe():
///     return shutil.which("pwsh") or shutil.which("powershell") or "powershell"
/// ```
fn powershell_exe() -> String {
    which_in_path("pwsh")
        .or_else(|| which_in_path("powershell"))
        .unwrap_or_else(|| "powershell".to_string())
}

/// `shutil.which(name)` analogue: scan `PATH` for an executable `name`, returning
/// the first absolute match. On Windows, also tries the `PATHEXT` extensions
/// (`.EXE`, `.CMD`, …) that `shutil.which` appends. Returns `None` if not found.
fn which_in_path(name: &str) -> Option<String> {
    let path_var = std::env::var_os("PATH")?;
    // On Windows, shutil.which honours PATHEXT; elsewhere the bare name is run.
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_string())
            .split(';')
            .filter(|e| !e.is_empty())
            .map(|e| e.to_string())
            .collect()
    } else {
        Vec::new()
    };
    for dir in std::env::split_paths(&path_var) {
        // Bare name (Unix executables and an already-suffixed Windows name).
        let direct = dir.join(name);
        if is_executable_file(&direct) {
            return Some(direct.to_string_lossy().into_owned());
        }
        // Windows: try each PATHEXT suffix.
        for ext in &exts {
            let candidate = dir.join(format!("{name}{ext}"));
            if is_executable_file(&candidate) {
                return Some(candidate.to_string_lossy().into_owned());
            }
        }
    }
    None
}

/// Whether a path points at an existing regular file (the executable-bit check
/// `shutil.which` does via `os.access(..., X_OK)` is best-effort here; on Unix
/// we check the mode, on other platforms existence as a file suffices).
fn is_executable_file(path: &std::path::Path) -> bool {
    match std::fs::metadata(path) {
        Ok(md) if md.is_file() => {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                md.permissions().mode() & 0o111 != 0
            }
            #[cfg(not(unix))]
            {
                true
            }
        }
        _ => false,
    }
}

/// `_as_int(v, default)` from the Windows JSON decode path:
///
/// ```python
/// def _as_int(v, default):
///     try: return int(v)
///     except (TypeError, ValueError): return default
/// ```
///
/// `int(v)` succeeds for JSON numbers (truncating a float toward zero) and for
/// numeric strings; `None`/missing and non-numeric strings raise and yield the
/// default.
fn as_int(v: Option<&Value>, default: i64) -> i64 {
    match v {
        Some(Value::Number(_)) => value_as_i64(v.unwrap()).unwrap_or(default),
        // int("12") works; int("1.5") raises ValueError in Python, but JSON
        // strings here are PowerShell-produced integers, so a plain decimal
        // parse mirrors int(str) for the values that actually occur.
        Some(Value::String(s)) => s.trim().parse::<i64>().unwrap_or(default),
        Some(Value::Bool(b)) => {
            // int(True) == 1, int(False) == 0 in Python.
            if *b {
                1
            } else {
                0
            }
        }
        _ => default,
    }
}

/// Total physical RAM in bytes via `sysinfo` — the honest portable substitute
/// for `os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")`.
fn sysinfo_total_ram_bytes() -> Option<u64> {
    use sysinfo::{MemoryRefreshKind, RefreshKind, System};
    let mut sys = System::new_with_specifics(
        RefreshKind::new().with_memory(MemoryRefreshKind::new().with_ram()),
    );
    sys.refresh_memory();
    let total = sys.total_memory();
    if total > 0 {
        Some(total)
    } else {
        None
    }
}

/// `platform.processor()` analogue (used by `_get_cpu_name` local fallback).
///
/// Python's `platform.processor()` is "an empty string if the value cannot be
/// determined"; the caller falls back to `"unknown"` in that case. We surface
/// the target architecture (`std::env::consts::ARCH`) as a non-empty hint,
/// which slots into the same `or "unknown"` chain.
fn platform_processor() -> String {
    std::env::consts::ARCH.to_string()
}

/// `platform.machine().lower()` analogue (used for arch -> backend selection on
/// the local host). Maps the Rust target arch to the uname `-m` style string the
/// Python `"aarch64" in x or "arm" in x` test keys on (e.g. ARCH is "aarch64"
/// for Apple Silicon, "x86_64" for Intel/AMD).
fn platform_machine() -> &'static str {
    std::env::consts::ARCH
}

/// `g["vram_gb"]` as f64 (0.0 if absent / non-numeric).
fn gpu_vram(g: &Value) -> f64 {
    g.get("vram_gb").and_then(value_as_f64).unwrap_or(0.0)
}

/// Number of groups in a `_group_gpus` result `Array`.
fn groups_len(groups: &Value) -> usize {
    groups.as_array().map(|a| a.len()).unwrap_or(0)
}

/// `int(x) if x and x.isdigit() else 0` — parse a non-negative decimal string of
/// ASCII digits, else 0. Uses `i128` since AMD `mem_info_*` byte counts can
/// exceed `i64` headroom in the multiplication paths; values are well within
/// i128.
fn parse_digits_i128(s: Option<&str>) -> i128 {
    match s {
        Some(s) if !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()) => {
            s.parse::<i128>().unwrap_or(0)
        }
        _ => 0,
    }
}

/// Python truthiness for a JSON value as used by `bool(d.get("gpu_name"))` and
/// the `result["gpu_name"]`/`or 0` idioms: `null` is falsy, empty string is
/// falsy, `0`/`0.0` is falsy, `false` is falsy; everything else is truthy.
fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().is_none_or(|f| f != 0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Read an i64 out of a JSON value (accepts int or float, truncating toward
/// zero like Python `int()` does on a float). Used for `gpu_count`.
fn value_as_i64(v: &Value) -> Option<i64> {
    if let Some(n) = v.as_i64() {
        Some(n)
    } else if let Some(u) = v.as_u64() {
        Some(u as i64)
    } else {
        v.as_f64().map(|f| f as i64)
    }
}

/// Read an f64 out of a JSON value (int or float).
fn value_as_f64(v: &Value) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_i64().map(|n| n as f64))
        .or_else(|| v.as_u64().map(|n| n as f64))
}

/// `round(x, 1)` — Python's banker's rounding (round-half-to-even) to 1 decimal.
fn round1(x: f64) -> f64 {
    round_py(x, 1)
}

/// `round(x, ndigits)` faithful to CPython's `round` (round-half-to-even, aka
/// banker's rounding), for `ndigits >= 0`.
///
/// `f64::round` rounds half away from zero, which diverges from Python on exact
/// `.5` ties (e.g. `round(2.5) == 2` in Python, `2.5_f64.round() == 3.0`). The
/// GPU VRAM grouping key (`round(vram_gb)`) and the `round(..., 1)` values can
/// land on such ties, so this reproduces the half-even rule.
fn round_py(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if (diff - 0.5).abs() < f64::EPSILON {
        // Exact half: round to even.
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        scaled.round()
    };
    rounded / factor
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_py_half_even() {
        // Python: round(2.5) == 2, round(3.5) == 4, round(0.5) == 0
        assert_eq!(round_py(2.5, 0), 2.0);
        assert_eq!(round_py(3.5, 0), 4.0);
        assert_eq!(round_py(0.5, 0), 0.0);
        assert_eq!(round_py(1.5, 0), 2.0);
        // Non-tie cases round normally.
        assert_eq!(round1(80.04), 80.0);
        assert_eq!(round1(159.96), 160.0);
    }

    #[test]
    fn group_gpus_groups_and_orders() {
        let gpus = json!([
            {"index": 0, "name": "A100", "vram_gb": 80.0},
            {"index": 1, "name": "A100", "vram_gb": 80.0},
            {"index": 2, "name": "T4", "vram_gb": 16.0},
        ]);
        let out = group_gpus(gpus.as_array().unwrap());
        let arr = out.as_array().unwrap();
        assert_eq!(arr.len(), 2);
        // Biggest pool (by total VRAM) first: 2x A100 = 160 > 16.
        assert_eq!(arr[0]["name"], json!("A100"));
        assert_eq!(arr[0]["count"], json!(2));
        assert_eq!(arr[0]["vram_total"], json!(160.0));
        assert_eq!(arr[0]["indices"], json!([0, 1]));
        assert_eq!(arr[1]["name"], json!("T4"));
        assert_eq!(arr[1]["count"], json!(1));
        // Key order preserved: name, vram_each, count, indices, vram_total.
        let keys: Vec<&String> = arr[0].as_object().unwrap().keys().collect();
        assert_eq!(
            keys,
            vec!["name", "vram_each", "count", "indices", "vram_total"]
        );
    }

    #[test]
    fn parse_digits_matches_python_isdigit() {
        assert_eq!(parse_digits_i128(Some("17171480576")), 17171480576);
        assert_eq!(parse_digits_i128(Some("0x1002")), 0); // not all digits
        assert_eq!(parse_digits_i128(Some("")), 0);
        assert_eq!(parse_digits_i128(None), 0);
        assert_eq!(parse_digits_i128(Some("-5")), 0); // '-' not a digit
    }

    #[test]
    fn truthiness_matches_python() {
        assert!(!is_truthy(&Value::Null));
        assert!(!is_truthy(&json!("")));
        assert!(is_truthy(&json!("NVIDIA")));
        assert!(!is_truthy(&json!(0)));
        assert!(is_truthy(&json!(1)));
        assert!(!is_truthy(&json!(0.0)));
    }

    #[test]
    fn cache_ttl_is_24h() {
        // Bumped from 30 min (1800) to 24 h.
        assert_eq!(CACHE_TTL, 86400);
    }

    #[test]
    fn classify_amd_gfx_families() {
        // RDNA: gfx10xx / gfx11xx / gfx12xx (consumer Radeon RX).
        assert_eq!(
            classify_amd_gfx("gfx1100"),
            ("gfx1100".to_string(), "rdna".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx1200"),
            ("gfx1200".to_string(), "rdna".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx1030"),
            ("gfx1030".to_string(), "rdna".to_string())
        );
        // CDNA: MI100 (gfx908), MI200 (gfx90a), MI300+ (gfx94x/95x).
        assert_eq!(
            classify_amd_gfx("gfx908"),
            ("gfx908".to_string(), "cdna".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx90a"),
            ("gfx90a".to_string(), "cdna".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx942"),
            ("gfx942".to_string(), "cdna".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx950"),
            ("gfx950".to_string(), "cdna".to_string())
        );
        // GCN/Vega: other gfx9xx.
        assert_eq!(
            classify_amd_gfx("gfx900"),
            ("gfx900".to_string(), "gcn".to_string())
        );
        assert_eq!(
            classify_amd_gfx("gfx906"),
            ("gfx906".to_string(), "gcn".to_string())
        );
        // Case/whitespace normalisation: lower().strip().
        assert_eq!(
            classify_amd_gfx("  GFX1100  "),
            ("gfx1100".to_string(), "rdna".to_string())
        );
        // Empty / unrecognised -> ("", "unknown").
        assert_eq!(
            classify_amd_gfx(""),
            (String::new(), "unknown".to_string())
        );
        assert_eq!(
            classify_amd_gfx("not-a-gfx"),
            (String::new(), "unknown".to_string())
        );
        // gfx with trailing garbage fails fullmatch.
        assert_eq!(
            classify_amd_gfx("gfx1100x"),
            (String::new(), "unknown".to_string())
        );
        // gfx with no digits fails \d+.
        assert_eq!(
            classify_amd_gfx("gfx"),
            (String::new(), "unknown".to_string())
        );
    }

    #[test]
    fn search_gfx_finds_first_target() {
        // rocminfo emits "Name: gfx90a" lines; search returns the first hit.
        let info = "Agent 1\n  Name: AMD EPYC\nAgent 2\n  Name: gfx90a\n  Marketing Name: ...";
        assert_eq!(search_gfx(info), Some("gfx90a".to_string()));
        // The optional [a-f] is consumed greedily.
        assert_eq!(search_gfx("...gfx1100..."), Some("gfx1100".to_string()));
        // No gfx target present.
        assert_eq!(search_gfx("no targets here"), None);
        // "gfx" with no following digit does not match.
        assert_eq!(search_gfx("gfx and stuff"), None);
        // Round-trips through classify: a CPU brand line then a GPU gfx line.
        let (gfx, family) = classify_amd_gfx(&search_gfx(info).unwrap());
        assert_eq!((gfx, family), ("gfx90a".to_string(), "cdna".to_string()));
    }

    #[test]
    fn as_int_coerces_like_python() {
        // JSON numbers (PowerShell Measure-Object .Sum/.Count decode to float).
        assert_eq!(as_int(Some(&json!(8)), 1), 8);
        assert_eq!(as_int(Some(&json!(8.0)), 1), 8);
        // Float truncates toward zero (int(2.9) == 2).
        assert_eq!(as_int(Some(&json!(2.9)), 0), 2);
        // Numeric string.
        assert_eq!(as_int(Some(&json!("12")), 0), 12);
        // Missing / null / non-numeric string -> default.
        assert_eq!(as_int(None, 1), 1);
        assert_eq!(as_int(Some(&Value::Null), 0), 0);
        assert_eq!(as_int(Some(&json!("nope")), 5), 5);
        // Bools: int(True)==1, int(False)==0.
        assert_eq!(as_int(Some(&json!(true)), 9), 1);
        assert_eq!(as_int(Some(&json!(false)), 9), 0);
    }

    #[test]
    fn detect_system_caches_and_returns_local_shape() {
        // Local detection on the test host: should produce a result map with the
        // canonical keys (no remote/SSH involved). We can't assert specific GPU
        // values, but the RAM/CPU/backend keys must be present.
        let v = detect_system("", "", "", true);
        let obj = v.as_object().expect("result is an object");
        assert!(obj.contains_key("total_ram_gb"));
        assert!(obj.contains_key("available_ram_gb"));
        assert!(obj.contains_key("cpu_cores"));
        assert!(obj.contains_key("cpu_name"));
        assert!(obj.contains_key("has_gpu"));
        assert!(obj.contains_key("backend"));
        let ram = obj["total_ram_gb"].as_f64().unwrap_or(0.0);
        assert!(ram >= 0.0);
        // Second call (not fresh) should be served from the per-host cache.
        let v2 = detect_system("", "", "", false);
        assert_eq!(v, v2);
    }
}

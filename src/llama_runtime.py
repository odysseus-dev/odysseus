"""llama.cpp runtime discovery, selection and prebuilt installation.

Cookbook's Dependencies tab used to treat ``llama-cpp-python`` (a Python wheel)
as "llama.cpp installed". Those are not the same thing: the wheel is an
in-process binding, while Cookbook launches the standalone ``llama-server``
executable. On native Windows the wheel also does not provide that executable at
all, so the old row showed a Linux build toolchain (cmake/g++/git) as the way
forward.

This module owns the native-Windows path:

* which prebuilt variants are appropriate for the detected platform;
* which official release asset matches a variant;
* a versioned, application-managed install directory (never a global PATH edit);
* archive extraction and ``llama-server.exe --version`` validation;
* atomic replacement with the previous runtime kept for rollback.

Nothing here downloads or installs on import; callers require explicit user
approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

LLAMA_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
INSTALL_MANIFEST = "odysseus-install.json"
LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20"

# Asset name fragments, most specific first. llama.cpp publishes Windows builds
# as `llama-<bld>-bin-win-<backend>-<arch>.zip`.
VARIANT_ASSETS: dict[str, list[str]] = {
    "cuda": ["win-cuda-12", "win-cuda-", "win-cu12", "win-cu13"],
    "vulkan": ["win-vulkan-"],
    "cpu": ["win-cpu-", "win-avx2-", "win-avx-", "win-x64"],
    "sycl": ["win-sycl-"],
}

# Assets that are not a runnable server and must never be selected.
ASSET_EXCLUDE = ("cudart-", "rocm-", "openvino-", "-arm64", "-aarch64")

# Human-facing requirements. Kept factual: a CUDA build needs a driver new
# enough for the bundled CUDA runtime, and Cookbook never assumes a newer CUDA
# is supported just because an asset exists.
VARIANT_INFO: dict[str, dict] = {
    "cuda": {
        "label": "NVIDIA CUDA",
        "requires": "A recent NVIDIA driver (the build bundles its own CUDA runtime).",
        "recommended_for": "nvidia",
    },
    "vulkan": {
        "label": "Vulkan",
        "requires": "A Vulkan-capable GPU driver.",
        "recommended_for": "gpu",
    },
    "cpu": {
        "label": "CPU only",
        "requires": "No GPU required.",
        "recommended_for": "cpu",
    },
    "sycl": {
        "label": "Intel SYCL",
        "requires": "An Intel GPU with SYCL support.",
        "recommended_for": "intel",
    },
}


def llama_runtime_root() -> Path:
    """Application-managed runtime directory (never on the global PATH)."""
    from src.runtime_paths import get_default_data_dir

    data_dir = (os.environ.get("ODYSSEUS_DATA_DIR") or "").strip() or get_default_data_dir()
    return Path(data_dir) / "runtimes" / "llama.cpp"


def llama_install_dir(tag: str) -> Path:
    """Versioned install directory for a release tag or build id."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag or "")).strip("-")
    return llama_runtime_root() / (safe or "current")


def llama_previous_dir(tag: str) -> Path:
    return llama_runtime_root() / (re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag or "")).strip("-") + ".previous")


def llama_runtime_summary() -> dict:
    """What is installed, and what a fresh install would look like."""
    root = llama_runtime_root()
    installed: list[dict] = []
    try:
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir() or child.name.endswith(".previous"):
                    continue
                exe = _find_server_exe(child)
                entry = {
                    "tag": child.name,
                    "path": str(child),
                    "executable": str(exe) if exe else None,
                    "previous": llama_previous_dir(child.name).is_dir(),
                }
                if exe:
                    from src.gpu_runtime import inspect_runtime
                    entry.update({k: v for k, v in inspect_runtime(exe.parent).items()
                                  if k in ("build", "cuda_ready", "cuda_major", "missing_libs", "detail")})
                installed.append(entry)
    except OSError:
        pass
    return {"root": str(root), "installed": installed}


def _find_server_exe(directory: Path) -> Path | None:
    for name in ("llama-server.exe", "llama-server"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    # Prebuilt zips nest everything under a single top-level folder.
    for name in ("llama-server.exe", "llama-server"):
        matches = sorted(directory.rglob(name))
        if matches:
            return matches[0]
    return None


def platform_llama_variants(*, is_windows: bool, is_wsl: bool, is_macos: bool,
                            is_apple_silicon: bool, has_nvidia: bool) -> list[dict]:
    """Variants appropriate for this platform, recommended first.

    Source builds are intentionally not offered as a prebuilt variant: they need
    a toolchain the user may not have, so they stay an explicit advanced option
    in the UI rather than a default recommendation.
    """
    if is_macos or is_apple_silicon:
        return [{"id": "source", "label": "Build from source (CMake)", "requires": "Xcode command line tools.",
                 "recommended": True, "prebuilt": False}]
    if not is_windows:
        # Linux and WSL keep the existing supported paths (pip wheel or distro
        # package); prebuilt Windows assets are not applicable.
        return [{"id": "distro", "label": "Distro package or pip wheel", "requires": "See Dependencies tab.",
                 "recommended": True, "prebuilt": False}]

    variants: list[dict] = []
    order = ["cuda", "vulkan", "cpu", "sycl"]
    for vid in order:
        info = VARIANT_INFO[vid]
        recommended = (vid == "cuda" and has_nvidia) or (vid == "cpu" and not has_nvidia)
        variants.append({
            "id": vid,
            "label": info["label"],
            "requires": info["requires"],
            "recommended": recommended,
            "prebuilt": True,
        })
    if is_wsl:
        variants = [v for v in variants if v["id"] in ("cpu", "vulkan")]
    return variants


def select_llama_asset(release: dict, variant_id: str, driver_version: str = "",
                       cuda_driver_major: int = 0) -> dict | None:
    """Pick the Windows x64 asset for ``variant_id`` from a GitHub release.

    Returns the asset dict (with a ``driver_warning`` when the chosen CUDA build
    is newer than the detected driver can be shown to support), or None when the
    release has no matching Windows build. Runtime-only archives (``cudart-``),
    other-vendor builds (rocm/openvino) and ARM64 are rejected rather than
    guessed at.
    """
    if variant_id == "source":
        return None
    fragments = VARIANT_ASSETS.get(variant_id)
    if not fragments:
        return None
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        return None
    windows = [
        a for a in assets
        if isinstance(a, dict)
        and isinstance(a.get("name"), str)
        and "win" in a["name"].lower()
        and a["name"].lower().endswith(".zip")
        and not any(x in a["name"].lower() for x in ASSET_EXCLUDE)
    ]
    if not windows:
        return None
    if variant_id == "cuda" and cuda_driver_major:
        best = pick_cuda_asset(windows, cuda_driver_major)
        if best is None:
            return None
        fragments = [f"cuda-{cuda_version_of(best['name'])}"]
    for frag in fragments:
        for asset in windows:
            name = asset["name"].lower()
            if frag in name:
                selected = {
                    "name": asset["name"],
                    "url": asset.get("browser_download_url") or asset.get("url"),
                    "size": asset.get("size"),
                    "id": asset.get("id"),
                    "sha256": asset_sha256(asset),
                }
                warning = cuda_driver_warning(asset["name"], driver_version, cuda_driver_major)
                if warning:
                    selected["driver_warning"] = warning
                return selected
    return None


def cuda_driver_warning(asset_name: str, driver_version: str, cuda_driver_major: int = 0) -> str:
    """Flag a CUDA build that needs a newer driver than the one detected.

    CUDA 13.x needs an NVIDIA driver newer than 580.x; CUDA 12.4 needs 550.96+.
    The WMI driver string is reported as-is rather than guessed into a
    user-facing version, so this stays a warning the user can act on instead of
    a silent assumption.
    """
    low = asset_name.lower()
    if "cuda-13" not in low and "cu13" not in low:
        return ""
    if cuda_driver_major >= 13:
        return ""  # the driver itself reports CUDA 13+ support
    if not driver_version:
        return ("This is a CUDA 13.x build. No NVIDIA driver version could be read, so "
                "Cookbook cannot confirm it will run — update the driver first or choose CUDA 12.4.")
    return ("This is a CUDA 13.x build, which needs a newer NVIDIA driver than CUDA 12.4. "
            f"Detected driver: {driver_version}. If it fails to start, update the driver "
            "or choose the CUDA 12.4 build.")



def _validate_via_bash(exe: Path) -> tuple[bool, str]:
    """Verify a Windows shell wrapper the way the runner launches it.

    The path is passed as ``$1`` to a fixed script string — never concatenated
    into the shell command, so spaces or quotes in the path cannot change what
    runs.
    """
    try:
        from core.platform_compat import find_bash
        bash = find_bash() or "bash"
    except Exception:
        bash = "bash"
    try:
        proc = subprocess.run(
            [bash, "-c", 'exec "$1" --version', "_", str(exe)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not be run through bash: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[0] if detail else f"exit {proc.returncode}")
    text = (proc.stdout or "").strip()
    return True, text.splitlines()[0] if text else ""


def validate_runtime_exe(exe: Path) -> tuple[bool, str]:
    """Run ``exe --version``. Direct argument array, never a shell string."""
    try:
        proc = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60)
    except OSError as exc:
        # A shell wrapper is not a valid Win32 image, so Windows refuses to
        # execute it directly. The runner launches through Git Bash, so verify
        # it the same way instead of rejecting a wrapper that actually works.
        if os.name == "nt" and getattr(exc, "winerror", None) in (193, 216):
            return _validate_via_bash(exe)
        return False, f"could not be run: {exc}"
    except subprocess.SubprocessError as exc:
        return False, f"could not be run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (detail[0] if detail else f"exit {proc.returncode}")
    text = (proc.stdout or "").strip()
    return True, text.splitlines()[0] if text else ""


def extract_llama_archive(archive: Path, dest: Path) -> tuple[bool, str, Path | None]:
    """Extract a prebuilt ZIP and return (ok, message, executable)."""
    if not archive.is_file():
        return False, "The download is missing.", None
    try:
        with zipfile.ZipFile(archive) as zf:
            # Refuse path traversal, absolute members, links and special files
            # before writing anything.
            for member in zf.infolist():
                name = member.filename
                if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                    return False, f"Refusing unsafe archive member: {name}", None
                mode = member.external_attr >> 16
                # Only judge the file-type bits when they are present: many zip
                # writers set just permissions (0o600 << 16) with no type bits,
                # and that must not be treated as a special file.
                ftype = mode & 0o170000
                if ftype and ftype not in (stat.S_IFREG, stat.S_IFDIR):
                    return False, f"Refusing non-regular archive member: {name}", None
            zf.extractall(dest)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"The archive could not be extracted: {exc}", None
    exe = _find_server_exe(dest)
    if exe is None:
        return False, "The archive does not contain a llama-server executable.", None
    ok, detail = validate_runtime_exe(exe)
    if not ok:
        return False, f"The extracted executable is not usable: {detail}", None
    return True, detail, exe


def install_llama_archive(archive: Path, tag: str, *, dependencies: tuple = (),
                          manifest: dict | None = None) -> tuple[bool, str, dict | None]:
    """Install a downloaded archive atomically, keeping the previous runtime.

    The archive is extracted to a staging directory first; only a fully
    validated extract replaces the current install, and the old one is moved
    aside rather than deleted so a bad update can be rolled back.
    """
    root = llama_runtime_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create {root}: {exc}", None

    target = llama_install_dir(tag)
    staging = root / (target.name + ".staging")
    previous = llama_previous_dir(tag)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create staging dir: {exc}", None

    ok, message, exe = extract_llama_archive(archive, staging)
    if not ok:
        shutil.rmtree(staging, ignore_errors=True)
        return False, message, None

    from src.gpu_runtime import inspect_runtime
    for dep in dependencies:
        dep_ok, dep_msg = extract_runtime_libraries(Path(dep), exe.parent)
        if not dep_ok:
            shutil.rmtree(staging, ignore_errors=True)
            return False, dep_msg, None
    check = inspect_runtime(exe.parent)
    if check["build"] == "cuda" and check["missing_libs"]:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"Incomplete CUDA runtime: {check['detail']}", None
    if manifest:
        try:
            (staging / INSTALL_MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return False, f"Could not record the install manifest: {exc}", None

    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        try:
            target.rename(previous)
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return False, f"Could not set aside the previous runtime: {exc}", None
    try:
        staging.rename(target)
    except OSError as exc:
        # Put the old runtime back before reporting failure.
        if previous.exists() and not target.exists():
            previous.rename(target)
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"Could not activate the new runtime: {exc}", None

    # The executable path must be reported under the ACTIVATED directory —
    # before the rename it still points into .staging, which the caller would
    # then store as the selected runtime.
    exe = _find_server_exe(target)
    ok, detail = validate_runtime_exe(exe) if exe else (False, "missing executable")
    info = {
        "tag": target.name,
        "path": str(target),
        "executable": str(exe) if exe else None,
        "version": detail if ok else "",
        "previous": str(previous) if previous.is_dir() else None,
    }
    return True, detail, info


def roll_back_llama_runtime(tag: str) -> tuple[bool, str]:
    """Restore the runtime that was set aside by the last install."""
    target = llama_install_dir(tag)
    previous = llama_previous_dir(tag)
    if not previous.is_dir():
        return False, "No previous runtime is stored for this version."
    shutil.rmtree(target, ignore_errors=True)
    try:
        previous.rename(target)
    except OSError as exc:
        return False, f"Rollback failed: {exc}"
    exe = _find_server_exe(target)
    if exe is None:
        return False, "Rollback moved the folder back but no executable was found."
    return True, str(exe)


def _has_windows_assets(release: dict) -> bool:
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        return False
    return any(
        isinstance(a, dict) and isinstance(a.get("name"), str)
        and "win" in a["name"].lower() and a["name"].lower().endswith(".zip")
        for a in assets
    )


def fetch_release_manifest(timeout: int = 30) -> dict:
    """Find the most recent release that actually ships Windows binaries.

    `/releases/latest` resolves to the stable tag, which currently carries only
    a `nightly-tag.txt` — the prebuilt zips are attached to nightly
    prereleases. Returning the stable release would make Cookbook report "no
    Windows build" on every host, so scan the recent list instead.
    """
    import urllib.request

    req = urllib.request.Request(LLAMA_RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Odysseus-Cookbook",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        releases = json.loads(resp.read().decode("utf-8"))
    if not isinstance(releases, list) or not releases:
        raise RuntimeError("the releases API returned no releases")
    for release in releases:
        if isinstance(release, dict) and _has_windows_assets(release):
            return release
    return releases[0]


def nvidia_driver_version() -> str:
    """Best-effort NVIDIA driver version, used to pick a compatible build."""
    gpu = detect_gpu()
    return gpu.get("driver", "") if gpu.get("kind") == "nvidia" else ""


def _detect_gpu_basic() -> dict:
    """Detect the GPU and driver without depending on a working NVML.

    On this host `nvidia-smi` fails with "Failed to initialize NVML: Unknown
    Error", which would otherwise make Cookbook conclude there is no NVIDIA
    GPU and recommend a CPU build. Fall back to the WMI video-controller view,
    which reports the driver even when NVML will not initialise.
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            parts = [p.strip() for p in (proc.stdout or "").strip().splitlines()[0].split(",")]
            name = parts[0] if parts else ""
            driver = parts[1] if len(parts) > 1 else ""
            if name:
                return {"kind": "nvidia", "name": name, "driver": driver, "source": "nvidia-smi"}
    except (OSError, subprocess.SubprocessError):
        pass

    ps = (
        "Get-CimInstance Win32_VideoController | "
        "ForEach-Object { \"$($_.Name)|$($_.DriverVersion)\" }"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            controllers = []
            for line in (proc.stdout or "").strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                name, _sep, driver = line.partition("|")
                controllers.append({"name": name.strip(), "driver": driver.strip()})
            nvidia = next(
                (c for c in controllers
                 if "nvidia" in c["name"].lower() or "geforce" in c["name"].lower()
                 or "rtx" in c["name"].lower() or "quadro" in c["name"].lower()),
                None,
            )
            if nvidia:
                return {"kind": "nvidia", "name": nvidia["name"],
                        "driver": nvidia["driver"], "source": "wmi"}
            if controllers:
                return {"kind": "other", "name": controllers[0]["name"],
                        "driver": controllers[0]["driver"], "source": "wmi"}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"kind": "unknown", "name": "", "driver": "", "source": "none"}


def detect_gpu() -> dict:
    """GPU identity (nvidia-smi or WMI) enriched with the CUDA driver API.

    The CUDA driver API works when NVML does not, and reports VRAM and the
    supported CUDA version. WMI's 32.x driver string is translated to NVIDIA's
    public number (32.0.16.2101 -> 621.01).
    """
    info = _detect_gpu_basic()
    if info.get("kind") != "nvidia":
        return info
    from src import gpu_runtime

    if info.get("source") == "wmi":
        info["wmi_driver"] = info.get("driver", "")
        info["driver"] = gpu_runtime.nvidia_driver_from_wmi(info["driver"]) or info["driver"]
    cuda = gpu_runtime.probe_cuda_driver()
    if cuda.get("available"):
        info.update({k: cuda[k] for k in ("cuda_driver_version", "cuda_driver_major", "compute_capability",
                                           "total_bytes", "free_bytes")})
        info["name"] = cuda["name"] or info["name"]
    else:
        info["cuda_error"] = cuda.get("error", "")
    return info


def is_wsl_host() -> bool:
    try:
        from core.platform_compat import is_wsl

        return bool(is_wsl())
    except Exception:
        return False



# --- release integrity, companion libraries, resumable verified download -------

_DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
_LIB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.dll$")


class DownloadError(RuntimeError):
    """A download could not be completed or failed verification."""


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def asset_sha256(asset: dict) -> str:
    """Published SHA-256 from the GitHub release API (``sha256:<hex>``), else ''."""
    match = _DIGEST_RE.match(str(asset.get("digest") or "").strip().lower()) if isinstance(asset, dict) else None
    return match.group(1) if match else ""


def verify_archive(path: Path, sha256: str, size: int | None = None) -> tuple[bool, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256 or "")):
        return False, "No published SHA-256 digest is available for this archive; refusing to install it."
    path = Path(path)
    if not path.is_file():
        return False, "The archive is missing."
    if size and path.stat().st_size != size:
        return False, f"Archive size {path.stat().st_size} does not match the published size {size}."
    actual = sha256_file(path)
    if actual != sha256:
        return False, f"SHA-256 mismatch: expected {sha256}, got {actual}."
    return True, actual


def cuda_version_of(asset_name: str) -> str:
    match = re.search(r"cuda-(\d+(?:\.\d+)?)", str(asset_name).lower())
    return match.group(1) if match else ""


def install_key(tag: str, asset_name: str) -> str:
    """Install directory name. CUDA builds are keyed by CUDA version so a CUDA 13
    install never displaces a CUDA 12 install of the same release."""
    cuda = cuda_version_of(asset_name)
    if cuda:
        return f"{tag}-cuda-{cuda}"
    low = str(asset_name).lower()
    for backend in ("cpu", "vulkan", "sycl"):
        if f"win-{backend}-" in low:
            return f"{tag}-{backend}"  # never the bare tag: that name may hold a legacy install
    return str(tag)


def select_runtime_dependencies(release: dict, asset_name: str) -> list[dict]:
    """Companion archives a build needs: ``cudart-llama-bin-win-cuda-<same ver>-<arch>.zip``."""
    cuda = cuda_version_of(asset_name)
    if not cuda:
        return []
    arch = "arm64" if "arm64" in asset_name.lower() else "x64"
    wanted = f"cudart-llama-bin-win-cuda-{cuda}-{arch}.zip"
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and str(asset.get("name", "")).lower() == wanted:
            return [{"name": asset["name"], "url": asset.get("browser_download_url") or asset.get("url"),
                     "size": asset.get("size"), "sha256": asset_sha256(asset)}]
    return []


def extract_runtime_libraries(archive: Path, dest: Path) -> tuple[bool, str]:
    """Extract flat ``*.dll`` members next to llama-server. Never overwrites a file."""
    try:
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            bad = [m.filename for m in members if not _LIB_NAME_RE.match(m.filename)]
            if bad or not members:
                return False, f"Unexpected runtime-library archive contents: {bad[:3] or 'empty'}"
            for member in members:
                target = Path(dest) / member.filename
                if target.exists():
                    continue
                part = target.with_name(target.name + ".part")
                with zf.open(member) as src, part.open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                if part.stat().st_size != member.file_size:
                    part.unlink(missing_ok=True)
                    return False, f"Size mismatch extracting {member.filename}"
                os.replace(part, target)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"Runtime libraries could not be extracted: {exc}"
    return True, ""


def fetch_release_by_tag(tag: str, timeout: int = 30) -> dict:
    import urllib.request

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", str(tag or "")):
        raise ValueError("Invalid release tag")
    url = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "Odysseus-Cookbook"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        release = json.loads(resp.read().decode("utf-8"))
    if not isinstance(release, dict):
        raise RuntimeError("unexpected release response")
    return release


def download_verified(url: str, dest: Path, *, sha256: str, size: int | None = None,
                      timeout: int = 60, chunk: int = 1 << 20):
    """Generator: download ``url`` to ``dest`` with resume, yielding ``(received, total)``.

    The partial file lives at ``dest.part`` with a ``.part.json`` sidecar naming the
    URL and digest it belongs to; it is resumed only when both match. The finished
    file is size- and SHA-256-checked before it is moved to ``dest``. A mismatch
    deletes the partial and raises; cancelling (closing the generator) keeps a
    resumable partial and never produces ``dest``.
    """
    import urllib.error
    import urllib.request

    if not re.fullmatch(r"[0-9a-f]{64}", str(sha256 or "")):
        raise DownloadError("No published SHA-256 digest is available; refusing to download.")
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    sidecar = dest.with_name(dest.name + ".part.json")
    if dest.is_file() and verify_archive(dest, sha256, size)[0]:
        yield dest.stat().st_size, dest.stat().st_size
        return
    dest.unlink(missing_ok=True)
    have = 0
    if part.is_file():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if meta.get("url") == url and meta.get("sha256") == sha256:
            have = part.stat().st_size
        else:
            part.unlink(missing_ok=True)
    if size and have > size:
        part.unlink(missing_ok=True)
        have = 0
    sidecar.write_text(json.dumps({"url": url, "sha256": sha256, "size": size}), encoding="utf-8")
    last_error = ""
    for attempt in range(6):
        have = part.stat().st_size if part.is_file() else 0
        if size and have >= size:
            break
        headers = {"User-Agent": "Odysseus-Cookbook"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and have:
                break  # the server considers what we hold complete; verification decides
            if exc.code < 500 and exc.code != 429:
                raise DownloadError(f"Download failed: HTTP {exc.code}") from None
            last_error = f"HTTP {exc.code}"
            time.sleep(min(2 ** attempt, 10))
            continue
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
            time.sleep(min(2 ** attempt, 10))
            continue
        with resp:
            resumed = bool(have) and getattr(resp, "status", 200) == 206
            if have and not resumed:
                have = 0  # server ignored Range: restart cleanly
            length = int(resp.headers.get("Content-Length") or 0)
            total = size or ((have + length) if length else 0)
            received = have
            try:
                with part.open("ab" if resumed else "wb") as handle:
                    while True:
                        block = resp.read(chunk)
                        if not block:
                            break
                        handle.write(block)
                        received += len(block)
                        yield received, total
            except OSError as exc:
                last_error = f"connection interrupted: {exc}"
        if not total or received >= total:
            break  # complete (or size unknown: verification decides)
        last_error = last_error or f"connection closed early at {received} of {total} bytes"
        time.sleep(min(2 ** attempt, 10))
    else:
        raise DownloadError(f"Download interrupted and not completed after retries ({last_error}); "
                            "the partial file is kept and will resume next time.")
    ok, detail = verify_archive(part, sha256, size)
    if not ok:
        part.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise DownloadError(detail)
    os.replace(part, dest)
    sidecar.unlink(missing_ok=True)
    yield dest.stat().st_size, dest.stat().st_size


def existing_install_matches(key: str, archives: list[dict]) -> dict | None:
    """Return install info when ``key`` already holds exactly these verified archives."""
    target = llama_install_dir(key)
    try:
        recorded = json.loads((target / INSTALL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    want = sorted((a["name"], a["sha256"]) for a in archives)
    have = sorted((a.get("name"), a.get("sha256")) for a in recorded.get("archives", []))
    exe = _find_server_exe(target)
    if want != have or exe is None:
        return None
    from src.gpu_runtime import inspect_runtime
    if inspect_runtime(exe.parent)["missing_libs"]:
        return None
    return {"tag": target.name, "path": str(target), "executable": str(exe), "already_installed": True}


# --- one-click setup planning ---------------------------------------------------

PACKAGE_CACHE = "_packages"


def package_cache_dir() -> Path:
    return llama_runtime_root() / PACKAGE_CACHE


def pick_cuda_asset(assets: list[dict], cuda_driver_major: int, compute_capability: str = "") -> dict | None:
    """Newest x64 CUDA build whose CUDA major the driver supports.

    CUDA 13 builds drop pre-Turing GPUs (compute capability < 7.5), so those
    cards are limited to CUDA 12.
    """
    ceiling = cuda_driver_major
    try:
        if compute_capability and float(compute_capability) < 7.5:
            ceiling = min(ceiling, 12)
    except ValueError:
        pass
    best, best_key = None, None
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        version = cuda_version_of(name)
        if not version or "arm64" in name or name.startswith("cudart-"):
            continue
        key = tuple(int(p) for p in version.split("."))
        if key[0] <= ceiling and (best_key is None or key > best_key):
            best, best_key = asset, key
    return best


def _describe_option(release: dict, asset: dict, tag: str) -> dict:
    archives = [{"name": asset["name"], "url": asset.get("browser_download_url") or asset.get("url"),
                 "size": asset.get("size") or 0, "sha256": asset_sha256(asset)}]
    archives += select_runtime_dependencies(release, asset["name"])
    key = install_key(tag, asset["name"])
    cache = package_cache_dir()
    for item in archives:
        path = cache / re.sub(r"[^A-Za-z0-9._-]+", "-", item["name"])
        item["cached"] = bool(item["size"]) and path.is_file() and path.stat().st_size == item["size"]
    return {"install_key": key, "archives": archives,
            "total_bytes": sum(a["size"] for a in archives),
            "download_bytes": sum(a["size"] for a in archives if not a["cached"]),
            "verifiable": all(a["sha256"] for a in archives),
            "already_installed": existing_install_matches(key, archives) is not None}


def plan_setup(release: dict, gpu: dict, variant: str = "auto") -> dict:
    """What a one-click setup would install here, and what it would download.

    Never installs a driver: CUDA is offered only when the installed NVIDIA driver
    already supports the build's CUDA major. CPU is always offered as the alternative.
    """
    tag = str(release.get("tag_name") or "")
    windows = [a for a in release.get("assets") or []
               if isinstance(a, dict) and isinstance(a.get("name"), str) and "win" in a["name"].lower()
               and a["name"].lower().endswith(".zip") and not any(x in a["name"].lower() for x in ASSET_EXCLUDE)]
    cpu_asset = next((a for a in windows if "win-cpu-" in a["name"].lower()), None)
    cuda_asset, unavailable = None, ""
    if gpu.get("kind") != "nvidia":
        unavailable = "No NVIDIA GPU was detected."
    elif not gpu.get("cuda_driver_major"):
        unavailable = ("An NVIDIA GPU was found but the driver reports no CUDA support. "
                       "Update the NVIDIA driver (Odysseus never installs drivers).")
    else:
        cuda_asset = pick_cuda_asset(windows, gpu["cuda_driver_major"], gpu.get("compute_capability", ""))
        if cuda_asset is None:
            unavailable = (f"This release has no CUDA build compatible with CUDA {gpu['cuda_driver_major']} "
                           "(driver) and this GPU generation.")
    plan = {"tag": tag, "gpu": {k: gpu.get(k) for k in ("kind", "name", "driver", "cuda_driver_major",
                                                         "compute_capability", "total_bytes")},
            "cuda": _describe_option(release, cuda_asset, tag) if cuda_asset else None,
            "cuda_unavailable_reason": unavailable,
            "cpu": _describe_option(release, cpu_asset, tag) if cpu_asset else None}
    chosen = "cuda" if (variant in ("auto", "cuda") and plan["cuda"]) else ("cpu" if plan["cpu"] else None)
    plan["chosen"] = chosen
    plan["reason"] = (f"{gpu.get('name')} with CUDA {gpu.get('cuda_driver_major')} driver support"
                      if chosen == "cuda" else (unavailable or "CPU runtime requested"))
    return plan


def save_release_snapshot(release: dict) -> None:
    """Keep the verified asset list so a later setup can reinstall from cached packages offline."""
    tag = str(release.get("tag_name") or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", tag):
        return
    slim = {"tag_name": tag, "assets": [
        {k: a.get(k) for k in ("name", "size", "digest", "browser_download_url")}
        for a in release.get("assets") or [] if isinstance(a, dict)]}
    try:
        cache = package_cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        tmp = cache / f"{tag}.release.json.tmp"
        tmp.write_text(json.dumps(slim), encoding="utf-8")
        os.replace(tmp, cache / f"{tag}.release.json")
    except OSError:
        pass


def load_release_snapshot() -> dict | None:
    """Newest saved release whose packages are actually cached (offline reinstall)."""
    cache = package_cache_dir()
    try:
        snapshots = sorted(cache.glob("*.release.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in snapshots:
        try:
            release = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(release, dict) and release.get("assets"):
            return release
    return None

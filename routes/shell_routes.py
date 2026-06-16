"""Shell routes — user-facing command execution endpoint."""

import asyncio
import importlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
import tempfile
from collections import namedtuple
from pathlib import Path
from typing import Dict, Any
from core.platform_compat import IS_APPLE_SILICON, which_tool
from core.middleware import INTERNAL_TOOL_USER
from src.optional_deps import prepare_optional_dependency_import

# POSIX-only: `pty`/`fcntl` transitively import `termios`, which does NOT exist
# on Windows, so importing them unconditionally crashed app startup there
# (ModuleNotFoundError: termios — issues #140/#92/#63/#149/#150). The PTY code
# path is only reachable on POSIX; Windows uses pipe streaming + a detached-job
# fallback for the tmux feature (see _generate_win_detached).
try:
    import fcntl
    import pty
except ImportError as exc:
    fcntl = None
    pty = None
    _PTY_IMPORT_ERROR = exc
else:
    _PTY_IMPORT_ERROR = None

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.platform_compat import (
    IS_WINDOWS,
    detached_popen_kwargs,
    find_bash,
    git_bash_path,
)
from routes.cookbook_helpers import resolve_llama_cpp_wheel_suffix


def _require_admin(request: Request):
    """Reject non-admin callers. Shell exec is admin-only — never expose to
    regular users; that's RCE-after-signup."""
    auth_manager = getattr(request.app.state, "auth_manager", None)
    if not auth_manager:
        # No auth at all — only safe in fully-trusted localhost dev mode
        return
    user = getattr(request.state, "current_user", None)
    # In-process tool loopback. The AuthMiddleware already validated the
    # internal token + loopback client before setting this marker, so
    # honour it here as admin-equivalent.
    if user == INTERNAL_TOOL_USER:
        return
    if not user or user == "api":
        raise HTTPException(403, "Admin only")
    if not auth_manager.is_admin(user):
        raise HTTPException(403, "Admin only")


def _reject_cross_site(request: Request):
    """Reject browser cross-site navigations to shell-touching endpoints."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


_SSH_PORT_RE = re.compile(r"^\d{1,5}$")
_SAFE_VENV_RE = re.compile(r"^[A-Za-z0-9_./~-]+$")
_VS_BUILD_TOOLS_URL = "https://visualstudio.microsoft.com/visual-cpp-build-tools/"
_CMAKE_DOWNLOAD_URL = "https://cmake.org/download/"
_W64DEVKIT_URL = "https://github.com/skeeto/w64devkit"


def _win_path_to_bash(path: str) -> str:
    """Convert a Windows absolute path to a Git Bash (MSYS2) style path.

    ``C:\\Foo\\Bar`` → ``/c/Foo/Bar``
    Works for any drive letter; backslashes and forward slashes are both handled.
    """
    p = path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        rest = p[2:].lstrip("/")
        return f"/{drive}/{rest}" if rest else f"/{drive}"
    return p


def _normalize_platform_label(platform_hint: str | None) -> str:
    plat = (platform_hint or "").strip().lower()
    if plat in ("windows", "win", "win32"):
        return "windows"
    if plat in ("darwin", "mac", "macos", "osx"):
        return "macos"
    if plat in ("termux",):
        return "termux"
    if plat:
        return plat
    if IS_WINDOWS:
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _extract_cuda_release(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"release\s+([0-9]+\.[0-9]+)", text, flags=re.I)
    return m.group(1) if m else None


async def _probe_local_llama_wheel_context(platform_hint: str | None) -> dict:
    platform = _normalize_platform_label(platform_hint)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    backend = "cpu"
    cuda_version = None
    python_arch = ""
    host_arch = ""
    python_arch_mismatch = False

    if shutil.which("nvidia-smi"):
        backend = "cuda"
        # 1. nvcc --version  (most precise — reports toolkit release)
        if shutil.which("nvcc"):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nvcc",
                    "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=6)
                cuda_version = _extract_cuda_release(
                    out.decode("utf-8", errors="replace")
                )
            except Exception:
                cuda_version = None
        # 2. nvidia-smi header (driver present, nvcc absent — runtime-only install)
        if not cuda_version:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=6)
                m = re.search(
                    r"CUDA\s+Version[:\s]+([0-9]+\.[0-9]+)",
                    out.decode("utf-8", errors="replace"),
                    re.I,
                )
                cuda_version = m.group(1) if m else None
            except Exception:
                cuda_version = None
    elif platform == "macos":
        backend = "metal"
    elif shutil.which("rocminfo") or shutil.which("rocm-smi") or shutil.which("hipconfig"):
        backend = "rocm"
    elif shutil.which("vulkaninfo"):
        backend = "vulkan"

    # Read local ROCm version for suffix accuracy
    rocm_version = None
    if backend == "rocm":
        for _rpath in ("/opt/rocm/.info/version", "/opt/rocm/lib/rocm_version.txt"):
            try:
                with open(_rpath, encoding="utf-8") as _f:
                    _line = _f.readline().strip()
                    if re.search(r"\d+\.\d+", _line):
                        rocm_version = re.search(r"\d+\.\d+", _line).group(0)
                        break
            except Exception:
                pass

    if platform == "macos":
        python_arch = (os.uname().machine if hasattr(os, "uname") else "") or ""
        host_arch = python_arch
        try:
            proc = await asyncio.create_subprocess_exec(
                "sysctl",
                "-in",
                "hw.optional.arm64",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=4)
            arm_hw = out.decode("utf-8", errors="replace").strip() == "1"
        except Exception:
            arm_hw = False
        if arm_hw:
            host_arch = "arm64"
        python_arch_mismatch = bool(host_arch == "arm64" and python_arch in ("x86_64", "amd64"))

    return {
        "platform": platform,
        "python_version": python_version,
        "backend": backend,
        "cuda_version": cuda_version,
        "rocm_version": rocm_version,
        "python_arch": python_arch,
        "host_arch": host_arch,
        "python_arch_mismatch": python_arch_mismatch,
    }


async def _probe_remote_llama_wheel_context(
    host: str,
    ssh_port: str | None,
    venv: str | None,
    platform_hint: str | None,
) -> dict:
    platform = _normalize_platform_label(platform_hint)
    python_version = ""
    backend = "cpu"
    cuda_version = None
    python_arch = ""
    host_arch = ""
    python_arch_mismatch = False

    if platform == "windows":
        ps = (
            "$r=@{}; "
            "$r.python=(python -c \"import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))\" 2>$null); "
            "if(-not $r.python){$r.python='';}; "
            "$r.cuda=[bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue); "
            "$r.rocm=[bool](Get-Command hipconfig -ErrorAction SilentlyContinue) -or [bool](Get-Command rocminfo -ErrorAction SilentlyContinue); "
            "$r.vulkan=[bool](Get-Command vulkaninfo -ErrorAction SilentlyContinue); "
            "$r.cuda_version=''; "
            # 1. nvcc (most precise — toolkit release string)
            "if(Get-Command nvcc -ErrorAction SilentlyContinue){"
            "  $nv=(nvcc --version | Out-String); "
            "  if($nv -match 'release\\s+([0-9]+\\.[0-9]+)'){ $r.cuda_version=$Matches[1]; }"
            "}; "
            # 2. nvidia-smi header (driver present, nvcc absent — runtime-only install)
            "if(-not $r.cuda_version -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)){"
            "  $ns=(nvidia-smi | Out-String); "
            "  if($ns -match 'CUDA Version[:\\s]+([0-9]+\\.[0-9]+)'){ $r.cuda_version=$Matches[1]; }"
            "}; "
            # 3. Windows registry (last resort — older drivers may not print version in header)
            "if(-not $r.cuda_version){"
            "  $regPath='HKLM:\\SOFTWARE\\NVIDIA Corporation\\GPU Computing Toolkit\\CUDA'; "
            "  if(Test-Path $regPath){"
            "    $v=(Get-ItemProperty $regPath -ErrorAction SilentlyContinue | "
            "       Select-Object -ExpandProperty Version -ErrorAction SilentlyContinue | "
            "       Sort-Object -Descending | Select-Object -First 1); "
            "    if($v){ $r.cuda_version=$v; }"
            "  }"
            "}; "
            "$r | ConvertTo-Json -Compress"
        )
        argv = _ssh_base_argv(host, ssh_port) + [
            f"powershell -NoProfile -Command {shlex.quote(ps)}"
        ]
    else:
        src = _venv_activate_prefix(venv)
        script = (
            "PYV=$(python3 -c \"import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))\" 2>/dev/null || "
            "python -c \"import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))\" 2>/dev/null || echo ''); "
            "PYARCH=$(python3 -c \"import platform;print(platform.machine())\" 2>/dev/null || python -c \"import platform;print(platform.machine())\" 2>/dev/null || echo ''); "
            "HOSTARCH=$(uname -m 2>/dev/null || echo ''); "
            "ARMHW=$(sysctl -in hw.optional.arm64 2>/dev/null || echo 0); "
            "CUDA=false; ROCM=false; VULKAN=false; CUDAV=''; ROCMV=''; "
            "command -v nvidia-smi >/dev/null 2>&1 && CUDA=true; "
            "if command -v nvcc >/dev/null 2>&1; then CUDAV=$(nvcc --version 2>/dev/null | sed -n 's/.*release \\([0-9]\\+\\.[0-9]\\+\\).*/\\1/p' | head -n1); fi; "
            # Also try the CUDA version file as a runtime-only fallback
            "if [ -z \"$CUDAV\" ] && [ -f /usr/local/cuda/version.json ]; then "
            "  CUDAV=$(python3 -c \"import json,sys; d=json.load(open('/usr/local/cuda/version.json')); print(d.get('cuda',{}).get('version',''))\" 2>/dev/null || true); fi; "
            "if [ -z \"$CUDAV\" ] && [ -f /usr/local/cuda/version.txt ]; then "
            "  CUDAV=$(head -n1 /usr/local/cuda/version.txt 2>/dev/null | grep -oP '[0-9]+\\.[0-9]+' | head -n1 || true); fi; "
            "(command -v rocminfo >/dev/null 2>&1 || command -v rocm-smi >/dev/null 2>&1 || command -v hipconfig >/dev/null 2>&1) && ROCM=true; "
            # Read rocm version for suffix selection
            "if $ROCM; then ROCMV=$(cat /opt/rocm/.info/version 2>/dev/null || rocminfo 2>/dev/null | grep -i 'ROCm Runtime Version' | grep -oP '[0-9]+\\.[0-9]+' | head -n1 || true); fi; "
            "command -v vulkaninfo >/dev/null 2>&1 && VULKAN=true; "
            "printf '{\"python\":\"%s\",\"python_arch\":\"%s\",\"host_arch\":\"%s\",\"arm_hw\":\"%s\",\"cuda\":%s,\"rocm\":%s,\"vulkan\":%s,\"cuda_version\":\"%s\",\"rocm_version\":\"%s\"}' \"$PYV\" \"$PYARCH\" \"$HOSTARCH\" \"$ARMHW\" \"$CUDA\" \"$ROCM\" \"$VULKAN\" \"$CUDAV\" \"$ROCMV\""
        )
        argv = _ssh_base_argv(host, ssh_port) + [f"{src}{script}"]

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await asyncio.wait_for(proc.communicate(), timeout=12)
    txt = out.decode("utf-8", errors="replace").strip()
    payload = None
    for line in reversed(txt.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except Exception:
                payload = None
            break
    if not isinstance(payload, dict):
        return {
            "platform": platform,
            "python_version": "",
            "backend": "cpu",
            "cuda_version": None,
        }

    python_version = str(payload.get("python") or "")
    python_arch = str(payload.get("python_arch") or "")
    host_arch = str(payload.get("host_arch") or "")
    if platform == "macos" and str(payload.get("arm_hw") or "") == "1":
        host_arch = "arm64"
    if bool(payload.get("cuda")):
        backend = "cuda"
    elif platform == "macos":
        backend = "metal"
    elif bool(payload.get("rocm")):
        backend = "rocm"
    elif bool(payload.get("vulkan")):
        backend = "vulkan"
    else:
        backend = "cpu"
    cuda_version = str(payload.get("cuda_version") or "") or None
    rocm_version = str(payload.get("rocm_version") or "") or None
    python_arch_mismatch = bool(platform == "macos" and host_arch == "arm64" and python_arch in ("x86_64", "amd64"))

    return {
        "platform": platform,
        "python_version": python_version,
        "backend": backend,
        "cuda_version": cuda_version,
        "rocm_version": rocm_version,
        "python_arch": python_arch,
        "host_arch": host_arch,
        "python_arch_mismatch": python_arch_mismatch,
    }


def _ssh_base_argv(host: str, ssh_port: str | None) -> list[str]:
    """Build an ssh argv prefix for remote probes without local-shell parsing."""
    if not host or not str(host).strip() or str(host).lstrip().startswith("-"):
        raise ValueError("invalid ssh host")
    argv = ["ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no"]
    if ssh_port and str(ssh_port).strip() not in ("", "22"):
        port = str(ssh_port).strip()
        if not _SSH_PORT_RE.match(port) or not (1 <= int(port) <= 65535):
            raise ValueError("invalid ssh port")
        argv += ["-p", port]
    argv.append(str(host).strip())
    return argv


def _venv_activate_prefix(venv: str | None) -> str:
    """Return a remote activation prefix while preserving shell expansion of ~."""
    if not venv:
        return ""
    if not _SAFE_VENV_RE.match(venv):
        raise ValueError("invalid venv path")
    act = venv if venv.endswith("/bin/activate") else venv.rstrip("/") + "/bin/activate"
    return f". {act} && "


logger = logging.getLogger(__name__)

PTY_SUPPORTED = pty is not None and fcntl is not None and hasattr(os, "setsid")


DOCKER_IN_CONTAINER_HINT = (
    "Not available inside the Odysseus container by design. The image ships no "
    "docker CLI and no host socket is mounted. Run Docker-backed launches on a "
    "remote server, where docker is checked over SSH. Mounting /var/run/docker.sock "
    "into the container would grant it host-root access, so only do that if you "
    "accept that risk."
)


def _running_in_container(dockerenv_path="/.dockerenv", cgroup_path="/proc/1/cgroup"):
    if os.path.exists(dockerenv_path):
        return True
    try:
        with open(cgroup_path, "r", encoding="utf-8") as fh:
            contents = fh.read()
    except OSError:
        return False
    return any(token in contents for token in ("docker", "containerd", "kubepods"))


DockerRowStatus = namedtuple("DockerRowStatus", ["applicable", "install_hint"])
PackageUpdateStatus = namedtuple("PackageUpdateStatus", ["available", "note"])


def _docker_row_status(*, on_remote, in_container, installed, default_hint):
    local_docker_unavailable = not on_remote and in_container and not installed
    if local_docker_unavailable:
        return DockerRowStatus(applicable=False, install_hint=DOCKER_IN_CONTAINER_HINT)
    return DockerRowStatus(applicable=True, install_hint=default_hint)


def _pip_dist_name(pkg: dict) -> str:
    """Distribution name for importlib.metadata lookups.

    The Cookbook package catalog carries both the import name (``name``, e.g.
    ``llama_cpp``) and the pip spec (``pip``, e.g. ``llama-cpp-python[server]``).
    The distribution is NOT always the import name with underscores swapped for
    dashes — ``llama_cpp`` ships in the ``llama-cpp-python`` distribution — so
    derive it from the pip spec (stripping any ``[extras]`` and version markers)
    and fall back to the munged import name only when no pip spec is declared.
    """
    pip = (pkg.get("pip") or "").strip()
    if pip:
        base = re.split(r"[\[<>=!~;\s]", pip, maxsplit=1)[0].strip()
        if base:
            return base
    return (pkg.get("name") or "").replace("_", "-")


def _import_optional_dependency_for_status(name: str):
    prepare_optional_dependency_import(name)
    return importlib.import_module(name)


def _package_installed_from_probe(name: str, probe: dict) -> bool:
    """Return whether an optional dependency is usable by Cookbook.

    A Python import alone is not enough: namespace packages can be created by a
    same-named directory, and vLLM serving needs the CLI on PATH. Keep this
    aligned with the actual serve command each backend launches.
    """
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}

    if name == "vllm":
        return bool(binaries.get("vllm"))
    if name == "llama_cpp":
        return bool(binaries.get("llama-server") or dists.get("llama-cpp-python"))
    if name == "sglang":
        return bool(dists.get("sglang") or modules.get("sglang", {}).get("real_module"))
    if name == "diffusers":
        return bool(
            (dists.get("diffusers") or modules.get("diffusers", {}).get("real_module"))
            and (dists.get("torch") or modules.get("torch", {}).get("real_module"))
        )
    if name == "hf_transfer":
        return bool(
            dists.get("hf-transfer")
            or modules.get("hf_transfer", {}).get("real_module")
        )
    return bool(dists.get(name) or modules.get(name, {}).get("real_module"))


def _package_status_note(name: str, probe: dict) -> str:
    binaries = probe.get("binaries") if isinstance(probe.get("binaries"), dict) else {}
    modules = probe.get("modules") if isinstance(probe.get("modules"), dict) else {}
    dists = probe.get("dists") if isinstance(probe.get("dists"), dict) else {}
    module = modules.get(name) if isinstance(modules.get(name), dict) else {}
    locations = module.get("locations") or []
    if name == "vllm":
        if binaries.get("vllm"):
            parts = [f"vLLM CLI: {binaries['vllm']}"]
            if dists.get("vllm"):
                parts.append(f"python package: vllm {dists['vllm']}")
            return "; ".join(parts)
        if module.get("found") and not dists.get("vllm"):
            loc = locations[0] if locations else module.get("origin") or "unknown path"
            return f"Python sees a vllm namespace at {loc}, but no vLLM CLI is on PATH."
        return "vLLM CLI not found on PATH."
    if name == "llama_cpp":
        parts = []
        if binaries.get("llama-server"):
            parts.append(f"native llama-server: {binaries['llama-server']}")
        if dists.get("llama-cpp-python"):
            parts.append(
                f"python package: llama-cpp-python {dists['llama-cpp-python']}"
            )
        return (
            "; ".join(parts)
            if parts
            else "No native llama-server or llama-cpp-python server package found."
        )
    if name == "diffusers":
        if _package_installed_from_probe(name, probe):
            return f"diffusers {dists.get('diffusers', 'available')} with torch {dists.get('torch', 'available')}"
        return "Diffusers serving needs both diffusers and torch."
    if name in dists:
        return f"{name} {dists[name]}"
    return ""


def _package_pip_update_status(
    pkg: dict, probe: dict | None = None
) -> PackageUpdateStatus:
    """Return whether the Dependencies UI should offer a generic pip update.

    "Installed" means Cookbook can use the dependency. It does not always mean
    the dependency is a Python package that Cookbook should update with pip:
    native llama-server can come from a package manager/source build, and a CLI
    may be on PATH without matching Python package metadata.
    """
    if pkg.get("name") == "APFEL":
        return PackageUpdateStatus(
            False,
            "",  # Note is empty because IT DOES allow for updates outside of PIP.
        )

    if pkg.get("kind") == "system" or not pkg.get("pip"):
        return PackageUpdateStatus(
            False, "Update this system dependency outside Odysseus."
        )

    name = pkg.get("name")
    binaries = (
        probe.get("binaries")
        if isinstance(probe, dict) and isinstance(probe.get("binaries"), dict)
        else {}
    )
    dists = (
        probe.get("dists")
        if isinstance(probe, dict) and isinstance(probe.get("dists"), dict)
        else {}
    )

    if name == "llama_cpp" and binaries.get("llama-server"):
        return PackageUpdateStatus(
            False,
            "Using native llama-server on PATH; update it with its package manager or source checkout.",
        )
    if name == "vllm" and binaries.get("vllm") and not dists.get("vllm"):
        return PackageUpdateStatus(
            False,
            "Using a vLLM CLI on PATH without Python package metadata; update it outside Odysseus.",
        )

    return PackageUpdateStatus(
        True, "Update uses pip in the selected Python environment."
    )


def _prepend_user_install_bins_to_path() -> None:
    """Make pip --user console scripts visible to dependency probes.

    Docker Cookbook installs vLLM with `python -m pip install --user`, which
    drops the `vllm` CLI in /app/.local/bin. The running app process does not
    inherit that PATH update, so `shutil.which("vllm")` can report missing even
    after a successful install.
    """
    try:
        import site

        candidates = [os.path.join(site.USER_BASE, "bin")]
    except Exception:
        candidates = []
    candidates.append(os.path.expanduser("~/.local/bin"))

    parts = (
        os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    )
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def _package_probe_script(names: list[str]) -> str:
    names_lit = ",".join(repr(n) for n in names)
    return f"""
import importlib.util
import importlib.metadata as md
import json
import os
import shutil
import site

names=[{names_lit}]
dist_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-cpp-python'],
    'sglang':['sglang'],
    'diffusers':['diffusers','torch'],
    'hf_transfer':['hf-transfer','hf_transfer'],
}}
bin_names={{
    'vllm':['vllm'],
    'llama_cpp':['llama-server'],
}}

def add_user_install_bins_to_path():
    candidates = []
    try:
        candidates.append(os.path.join(site.USER_BASE, 'bin'))
    except Exception:
        pass
    candidates.append(os.path.expanduser('~/.local/bin'))
    parts = os.environ.get('PATH', '').split(os.pathsep) if os.environ.get('PATH') else []
    changed = False
    for path in reversed([p for p in candidates if p]):
        if path not in parts:
            parts.insert(0, path)
            changed = True
    if changed:
        os.environ['PATH'] = os.pathsep.join(parts)

add_user_install_bins_to_path()

def mod_status(n):
    spec = importlib.util.find_spec(n)
    loader = getattr(spec, 'loader', None) if spec else None
    return {{
        'found': bool(spec),
        'origin': getattr(spec, 'origin', None) if spec else None,
        'loader': type(loader).__name__ if loader else None,
        'locations': list(getattr(spec, 'submodule_search_locations', []) or []),
        'real_module': bool(spec and loader),
    }}

def dist_status(ds):
    out = {{}}
    for d in ds:
        try:
            out[d] = md.version(d)
        except Exception:
            pass
    return out

def probe(n):
    mods = {{n: mod_status(n)}}
    if n == 'diffusers':
        mods['torch'] = mod_status('torch')
    dists = dist_status(dist_names.get(n, [n]))
    bins = {{b: shutil.which(b) for b in bin_names.get(n, [])}}
    out = {{'modules': mods, 'dists': dists, 'binaries': bins}}
    if n == 'llama_cpp':
        info = {{
            'install_type': 'unknown',
            'installed_backend': 'unknown',
            'has_gpu_offload': None,
            'direct_url': None,
        }}
        if bins.get('llama-server') and not dists.get('llama-cpp-python'):
            info['install_type'] = 'native'
        elif dists.get('llama-cpp-python'):
            info['install_type'] = 'wheel'
            try:
                dist = md.distribution('llama-cpp-python')
                direct_url_txt = dist.read_text('direct_url.json')
                if direct_url_txt:
                    du = json.loads(direct_url_txt)
                    info['direct_url'] = du
                    if isinstance(du, dict) and (du.get('dir_info') or du.get('vcs_info')):
                        info['install_type'] = 'source'
            except Exception:
                pass

        try:
            m = mods.get('llama_cpp') or {{}}
            locs = m.get('locations') or []
            if locs:
                root = locs[0]
                names = []
                for base, _, files in os.walk(root):
                    if '/.git/' in base.replace('\\\\', '/'):
                        continue
                    for fn in files:
                        lf = fn.lower()
                        if ('ggml' in lf) or ('llama' in lf):
                            names.append(lf)
                    if len(names) > 200:
                        break
                joined = ' '.join(names)
                if ('cuda' in joined) or ('cublas' in joined):
                    info['installed_backend'] = 'cuda'
                elif ('hip' in joined) or ('rocm' in joined) or ('hipblas' in joined):
                    info['installed_backend'] = 'rocm'
                elif 'vulkan' in joined:
                    info['installed_backend'] = 'vulkan'
                elif 'metal' in joined:
                    info['installed_backend'] = 'metal'
        except Exception:
            pass

        try:
            from llama_cpp import llama_cpp as _llama_c
            if hasattr(_llama_c, 'llama_supports_gpu_offload'):
                gpu_offload = bool(_llama_c.llama_supports_gpu_offload())
                info['has_gpu_offload'] = gpu_offload
                if info['installed_backend'] == 'unknown':
                    info['installed_backend'] = 'gpu' if gpu_offload else 'cpu'
            elif info['installed_backend'] == 'unknown':
                info['installed_backend'] = 'cpu'
        except Exception:
            if info['installed_backend'] == 'unknown':
                info['installed_backend'] = 'cpu'

        out['llama'] = info
    return out

print(json.dumps({{n: probe(n) for n in names}}))
"""


def _find_line_break(buf):
    """Find next line terminator in buffer. Returns (index, separator_length) or (-1, 0)."""
    ni = buf.find(b"\n")
    ri = buf.find(b"\r")
    if ni == -1 and ri == -1:
        return -1, 0
    if ni == -1:
        return ri, 1
    if ri == -1:
        return ni, 1
    if ri < ni:
        return ri, (2 if ri + 1 == ni else 1)
    return ni, 1


EXEC_TIMEOUT = 30  # seconds — shorter than agent's 60s
STREAM_TIMEOUT = 120  # default for short commands
MAX_OUTPUT = 200_000  # truncate limit
TMUX_LOG_DIR = Path(tempfile.gettempdir()) / "odysseus-tmux"
PTY_UNSUPPORTED_ERROR = "pty_unsupported"


class ShellExecRequest(BaseModel):
    command: str
    timeout: int | None = (
        None  # optional override; 0 = no timeout (run until client disconnects)
    )
    use_pty: bool = False  # use pseudo-TTY (for progress bars)
    use_tmux: bool = False  # run in tmux session (survives browser disconnect)


async def _create_shell(command: str, **kwargs):
    """Spawn a shell subprocess for `command`.

    POSIX: /bin/sh via create_subprocess_shell (unchanged behaviour).
    Windows: prefer a real bash (Git Bash/WSL) so bash-syntax commands behave
    the same as on Linux; fall back to cmd.exe when no bash is installed.
    Powershell commands are executed directly via cmd.exe /c to avoid quoting
    and env variable expansion errors under Git Bash.
    """
    if IS_WINDOWS:
        # PowerShell commands (used by the frontend for Windows log-file polling
        # and session management) must run directly — passing them through
        # bash -c mangles $env:VAR syntax and breaks the command.
        cmd_trim = command.strip()
        if cmd_trim.startswith("powershell") or cmd_trim.startswith("cmd "):
            return await asyncio.create_subprocess_shell(command, **kwargs)
        bash = find_bash()
        if bash:
            return await asyncio.create_subprocess_exec(bash, "-c", command, **kwargs)
    return await asyncio.create_subprocess_shell(command, **kwargs)


async def _exec_shell(command: str, timeout: int = EXEC_TIMEOUT) -> Dict[str, Any]:
    """Run a shell command and return stdout/stderr/exit_code."""
    proc = None
    try:
        proc = await _create_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path.home()),
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_b.decode(errors="replace")[:MAX_OUTPUT]
        stderr = stderr_b.decode(errors="replace")[:MAX_OUTPUT]
        return {"stdout": stdout, "stderr": stderr, "exit_code": proc.returncode}
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "exit_code": -1,
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


async def _generate_pty(cmd: str, timeout: int, request: Request):
    """Run command in a pseudo-TTY so tqdm/progress bars work natively."""
    if not PTY_SUPPORTED:
        msg = "PTY streaming is not supported on this platform"
        if _PTY_IMPORT_ERROR:
            msg += f": {_PTY_IMPORT_ERROR}"
        yield f"data: {json.dumps({'stream': 'stderr', 'data': msg, 'error': PTY_UNSUPPORTED_ERROR})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1, 'error': PTY_UNSUPPORTED_ERROR})}\n\n"
        return

    loop = asyncio.get_running_loop()
    master_fd, slave_fd = pty.openpty()

    # Set master to non-blocking
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(Path.home()),
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)  # parent doesn't need the slave side

    deadline = (loop.time() + timeout) if timeout else None
    buf = b""
    process_done = asyncio.Event()

    async def _wait_proc():
        await proc.wait()
        process_done.set()

    wait_task = asyncio.create_task(_wait_proc())

    try:
        while not process_done.is_set():
            if deadline and loop.time() > deadline:
                proc.kill()
                await proc.wait()
                yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Command timed out after {timeout}s'})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
                return

            # Check client disconnect
            if await request.is_disconnected():
                proc.kill()
                await proc.wait()
                return

            # Read available data from PTY
            try:
                chunk = await asyncio.wait_for(
                    loop.run_in_executor(None, _pty_read, master_fd),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                continue
            except OSError:
                break

            if chunk is None:
                # No data yet, keep waiting
                continue
            if chunk == b"":
                # EOF — process closed the PTY
                break

            buf += chunk
            # Split on \r or \n
            while True:
                idx, sep_len = _find_line_break(buf)
                if idx == -1:
                    break
                line = buf[:idx].decode(errors="replace")
                buf = buf[idx + sep_len :]
                if line:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"

        # Drain any remaining PTY output after process exits
        try:
            while True:
                rest = _pty_read(master_fd)
                if rest is None or rest == b"":
                    break
                buf += rest
        except OSError:
            pass

        # Flush remaining buffer
        if buf:
            # Split remaining buffer same as above
            while True:
                idx, sep_len = _find_line_break(buf)
                if idx == -1:
                    break
                line = buf[:idx].decode(errors="replace")
                buf = buf[idx + sep_len :]
                if line:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
            if buf:
                text = buf.decode(errors="replace").strip()
                if text:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': text})}\n\n"

        await wait_task
        yield f"data: {json.dumps({'exit_code': proc.returncode})}\n\n"

    except Exception as e:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        yield f"data: {json.dumps({'stream': 'stderr', 'data': str(e)})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
    finally:
        wait_task.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass


def _pty_read(fd: int) -> bytes | None:
    """Blocking read from PTY fd. Called via run_in_executor.
    Returns bytes on data, None on timeout (no data yet)."""
    import select

    r, _, _ = select.select([fd], [], [], 1.0)
    if r:
        try:
            data = os.read(fd, 4096)
            return data if data else b""  # empty = EOF
        except OSError:
            return b""  # fd closed = EOF
    return None  # timeout, no data yet


async def _generate_tmux(cmd: str, request: Request):
    """Run command in a tmux session. Streams output via a log file.
    The tmux session survives browser disconnect — user can reconnect or
    `tmux attach -t <name>` to see it live."""
    TMUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
    session_id = f"cookbook-{uuid.uuid4().hex[:8]}"
    log_path = TMUX_LOG_DIR / f"{session_id}.log"

    # Write a wrapper script that runs the command, tees output, and records exit code.
    # Using a script avoids shell quoting issues with the tmux command.
    script_path = TMUX_LOG_DIR / f"{session_id}.sh"
    script_path.write_text(
        f"#!/bin/bash\n"
        f'ODYSSEUS_USER_SHELL="${{SHELL:-}}"\n'
        f'if [ -n "$ODYSSEUS_USER_SHELL" ] && [ -x "$ODYSSEUS_USER_SHELL" ]; then\n'
        f'  ODYSSEUS_USER_PATH="$("$ODYSSEUS_USER_SHELL" -ic \'printf "__ODYSSEUS_PATH__%s\\n" "$PATH"\' 2>/dev/null | sed -n \'s/^__ODYSSEUS_PATH__//p\' | tail -n 1 || true)"\n'
        f'  if [ -n "$ODYSSEUS_USER_PATH" ]; then export PATH="$ODYSSEUS_USER_PATH:$PATH"; fi\n'
        f"fi\n"
        f"{cmd} 2>&1 | tee '{log_path}'\n"
        f"EC=${{PIPESTATUS[0]}}\n"
        f"echo ':::EXIT_CODE:::'$EC >> '{log_path}'\n"
        f"rm -f '{script_path}'\n"
        f"exit $EC\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    logger.info(
        "tmux wrapper script created: session=%s path=%s", session_id, script_path
    )

    tmux_cmd = f"tmux new-session -d -s {session_id} {shlex.quote(str(script_path))}"

    proc = await asyncio.create_subprocess_shell(
        tmux_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode(errors="replace")
        yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Failed to start tmux: {stderr}'})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
        return

    yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Started tmux session: {session_id}'})}\n\n"

    # Tail the log file, streaming new lines as SSE
    lines_sent = 0
    exit_code = None

    while True:
        # Check client disconnect
        if await request.is_disconnected():
            # tmux keeps running — that's the whole point
            yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Disconnected. tmux session {session_id} continues in background.'})}\n\n"
            return

        # Read new lines from log
        try:
            if log_path.exists():
                lines = log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                new_lines = lines[lines_sent:]
                for line in new_lines:
                    if line.startswith(":::EXIT_CODE:::"):
                        try:
                            exit_code = int(line.split(":::")[-1])
                        except ValueError:
                            exit_code = -1
                    else:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                lines_sent = len(lines)
        except Exception as e:
            logger.debug(f"tmux log read error: {e}")

        if exit_code is not None:
            break

        # Check if tmux session is still alive
        check = await asyncio.create_subprocess_shell(
            f"tmux has-session -t {session_id} 2>/dev/null",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await check.wait()
        if check.returncode != 0:
            # Session ended — do one final read
            await asyncio.sleep(0.5)
            if log_path.exists():
                lines = log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                for line in lines[lines_sent:]:
                    if line.startswith(":::EXIT_CODE:::"):
                        try:
                            exit_code = int(line.split(":::")[-1])
                        except ValueError:
                            exit_code = -1
                    else:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
            if exit_code is None:
                exit_code = 0
            break

        await asyncio.sleep(1.0)

    yield f"data: {json.dumps({'exit_code': exit_code})}\n\n"

    # Clean up log file
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass


async def _generate_win_detached(cmd: str, request: Request):
    """Windows stand-in for the tmux path (issues #84/#162).

    tmux doesn't exist on Windows, so we run the command in a *detached* child
    (DETACHED_PROCESS — survives browser disconnect, same as the tmux session)
    that writes output to a log file, and tail that log over SSE. Prefers bash
    (Git Bash) for command-syntax parity; falls back to cmd.exe. There's no
    `tmux attach` equivalent, but the "keeps running if you disconnect" contract
    holds, which is the point of the feature for long Cookbook downloads."""
    TMUX_LOG_DIR.mkdir(parents=True, exist_ok=True)
    session_id = f"cookbook-{uuid.uuid4().hex[:8]}"
    log_path = TMUX_LOG_DIR / f"{session_id}.log"
    exit_path = TMUX_LOG_DIR / f"{session_id}.exit"

    bash = find_bash()
    if bash:
        script_path = TMUX_LOG_DIR / f"{session_id}.sh"
        script_path.write_text(
            f"{cmd} > {shlex.quote(git_bash_path(log_path))} 2>&1\n"
            f"echo $? > {shlex.quote(git_bash_path(exit_path))}\n",
            encoding="utf-8",
        )
        argv = [bash, str(script_path)]
    else:
        script_path = TMUX_LOG_DIR / f"{session_id}.cmd"
        # cmd.exe wrapper: run, redirect all output to the log, record exit code.
        script_path.write_text(
            "@echo off\r\n"
            f'call {cmd} > "{log_path}" 2>&1\r\n'
            f'echo %ERRORLEVEL%> "{exit_path}"\r\n',
            encoding="utf-8",
        )
        argv = [os.environ.get("ComSpec", "cmd.exe"), "/c", str(script_path)]

    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            **detached_popen_kwargs(),
        )
    except Exception as e:
        yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Failed to launch background job: {e}'})}\n\n"
        yield f"data: {json.dumps({'exit_code': -1})}\n\n"
        return

    yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Started background job: {session_id}'})}\n\n"

    lines_sent = 0
    exit_code = None
    while True:
        if await request.is_disconnected():
            yield f"data: {json.dumps({'stream': 'stdout', 'data': f'Disconnected. Background job {session_id} continues running.'})}\n\n"
            return
        try:
            if log_path.exists():
                lines = log_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                for line in lines[lines_sent:]:
                    yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                lines_sent = len(lines)
        except Exception as e:
            logger.debug("win detached log read error: %s", e)

        if exit_path.exists():
            # Drain any final lines, then read the recorded exit code.
            await asyncio.sleep(0.3)
            try:
                if log_path.exists():
                    lines = log_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    for line in lines[lines_sent:]:
                        yield f"data: {json.dumps({'stream': 'stdout', 'data': line})}\n\n"
                    lines_sent = len(lines)
                exit_code = int(
                    (
                        exit_path.read_text(encoding="utf-8", errors="replace").strip()
                        or "0"
                    )
                )
            except Exception:
                exit_code = 0
            break
        await asyncio.sleep(1.0)

    yield f"data: {json.dumps({'exit_code': exit_code})}\n\n"
    for p in (log_path, exit_path, script_path):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def setup_shell_routes() -> APIRouter:
    router = APIRouter(tags=["shell"])

    @router.post("/api/shell/exec")
    async def shell_exec(request: Request, req: ShellExecRequest) -> Dict[str, Any]:
        """Execute a shell command and return output. Admin only."""
        _require_admin(request)
        cmd = req.command.strip()
        if not cmd:
            return {"stdout": "", "stderr": "No command provided", "exit_code": 1}

        logger.info("User shell exec requested: length=%d", len(cmd))
        result = await _exec_shell(
            cmd, timeout=req.timeout if req.timeout is not None else EXEC_TIMEOUT
        )
        return result

    @router.post("/api/shell/stream")
    async def shell_stream(request: Request, req: ShellExecRequest):
        """Execute a shell command and stream output line-by-line via SSE. Admin only."""
        _require_admin(request)
        cmd = req.command.strip()
        if not cmd:

            async def empty():
                yield f"data: {json.dumps({'stream': 'stderr', 'data': 'No command provided'})}\n\n"
                yield f"data: {json.dumps({'exit_code': 1})}\n\n"

            return StreamingResponse(empty(), media_type="text/event-stream")

        timeout = req.timeout if req.timeout is not None else STREAM_TIMEOUT
        use_pty = req.use_pty
        use_tmux = req.use_tmux
        logger.info(
            "User shell stream requested: timeout=%s pty=%s tmux=%s length=%d",
            "none" if timeout == 0 else f"{timeout}s",
            use_pty,
            use_tmux,
            len(cmd),
        )

        if use_tmux:
            # tmux is POSIX-only; Windows uses a detached-process + logfile tail
            # that preserves the "survives disconnect" behaviour.
            gen = (
                _generate_win_detached(cmd, request)
                if IS_WINDOWS
                else _generate_tmux(cmd, request)
            )
            return StreamingResponse(gen, media_type="text/event-stream")

        if use_pty and not IS_WINDOWS:
            return StreamingResponse(
                _generate_pty(cmd, timeout, request),
                media_type="text/event-stream",
            )
        # Windows has no PTY; fall through to pipe streaming below (output still
        # streams line-by-line, just without live in-place progress-bar redraws).

        async def generate():
            proc = None
            reader_tasks = []
            try:
                proc = await _create_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(Path.home()),
                )

                q: asyncio.Queue = asyncio.Queue()

                async def _reader(stream, name):
                    """Read chunks, split on \\n or \\r for progress bar support."""
                    try:
                        buf = b""
                        while True:
                            chunk = await stream.read(4096)
                            if not chunk:
                                if buf:
                                    await q.put(
                                        (
                                            name,
                                            buf.decode(errors="replace").rstrip("\r\n"),
                                        )
                                    )
                                break
                            buf += chunk
                            while True:
                                idx, sep_len = _find_line_break(buf)
                                if idx == -1:
                                    break
                                line = buf[:idx].decode(errors="replace")
                                buf = buf[idx + sep_len :]
                                if line:
                                    await q.put((name, line))
                    finally:
                        await q.put((name, None))

                reader_tasks = [
                    asyncio.create_task(_reader(proc.stdout, "stdout")),
                    asyncio.create_task(_reader(proc.stderr, "stderr")),
                ]

                finished = 0
                loop = asyncio.get_running_loop()
                deadline = (loop.time() + timeout) if timeout else None
                while finished < 2:
                    if deadline:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise asyncio.TimeoutError()
                        wait = min(remaining, 2.0)
                    else:
                        wait = 2.0

                    try:
                        name, text = await asyncio.wait_for(q.get(), timeout=wait)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            if proc:
                                proc.kill()
                            return
                        continue

                    if text is None:
                        finished += 1
                        continue
                    yield f"data: {json.dumps({'stream': name, 'data': text})}\n\n"

                await proc.wait()
                yield f"data: {json.dumps({'exit_code': proc.returncode})}\n\n"

            except asyncio.TimeoutError:
                if proc:
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                yield f"data: {json.dumps({'stream': 'stderr', 'data': f'Command timed out after {timeout}s'})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'stream': 'stderr', 'data': str(e)})}\n\n"
                yield f"data: {json.dumps({'exit_code': -1})}\n\n"
            finally:
                for t in reader_tasks:
                    t.cancel()

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/api/cookbook/packages")
    async def list_packages(
        request: Request,
        host: str | None = None,
        ssh_port: str | None = None,
        venv: str | None = None,
        platform: str | None = None,
    ):
        """Check which optional packages are installed.

        Local-target packages are checked in-process. Remote-target packages
        (vllm, sglang, llama_cpp, diffusers, hf_transfer) are checked on the SELECTED
        server over SSH, inside its venv — otherwise installing on a remote box
        never reflected because the check only ever looked at the local host.
        """
        _require_admin(request)
        _reject_cross_site(request)
        import importlib.metadata as importlib_metadata
        import shlex
        import json as _json
        import site
        import sys

        _prepend_user_install_bins_to_path()
        importlib.invalidate_caches()
        try:
            user_site = site.getusersitepackages()
            if user_site and os.path.isdir(user_site) and user_site not in sys.path:
                sys.path.append(user_site)
        except Exception:
            pass
        if ssh_port and str(ssh_port).strip() not in ("", "22"):
            _port = str(ssh_port).strip()
            if not _SSH_PORT_RE.match(_port) or not (1 <= int(_port) <= 65535):
                raise HTTPException(400, "Invalid ssh_port")
        packages = [
            # ── System ── OS binaries, not pip packages
            {
                "name": "tmux",
                "pip": "",
                "desc": "Required for Linux/Termux Cookbook background downloads and serves",
                "category": "System",
                "target": "remote",
                "kind": "system",
                "install_hint": "Run Cookbook server setup, or install tmux with apt/pacman/dnf/apk/zypper.",
            },
            {
                "name": "docker",
                "pip": "",
                "desc": "Required only for Docker-backed launch commands",
                "category": "System",
                "target": "remote",
                "kind": "system",
                "install_hint": "Install Docker on the selected server and allow this user to run docker.",
            },
            # ── LLM ── installs on GPU servers for model serving/downloading
            {
                "name": "hf_transfer",
                "pip": "hf_transfer",
                "desc": "Fast model downloads from HuggingFace",
                "category": "LLM",
                "target": "remote",
            },
            {
                "name": "llama_cpp",
                "pip": "llama-cpp-python[server]",
                "desc": "Serve GGUF models via llama.cpp",
                "category": "LLM",
                "target": "remote",
            },
            {
                "name": "sglang",
                "pip": "sglang[all]",
                "desc": "Serve HF safetensors models via SGLang",
                "category": "LLM",
                "target": "remote",
            },
            {
                "name": "vllm",
                "pip": "vllm",
                "desc": "High-throughput LLM serving engine",
                "category": "LLM",
                "target": "remote",
            },
            {
                "name": "APFEL",
                "pip": "",
                "desc": "OpenAI-compatible API for Apple Foundational Models on Apple Silicon",
                "category": "LLM",
                "target": "local",
                "kind": "system",
                "install_cmd": "brew install apfel",
                "update_cmd": "brew upgrade apfel",
                "install_hint": "Requires a native Apple Silicon Mac with Apple Foundational Models support. Installable via Homebrew on supported Macs.",
            },
            # ── Image ── editor + diffusion model serving
            {
                "name": "diffusers",
                "pip": "diffusers[torch]",
                "desc": "Image generation pipelines (SD, Flux) with PyTorch",
                "category": "Image",
                "target": "remote",
            },
            {
                "name": "transformers",
                "pip": "transformers",
                "desc": "Hugging Face model components used by SD/Flux pipelines and image tools",
                "category": "Image",
                "target": "remote",
            },
            {
                "name": "rembg",
                "pip": "rembg[gpu]",
                "desc": "AI background removal for image editor",
                "category": "Image",
                "target": "local",
            },
            {
                "name": "realesrgan",
                "pip": "realesrgan",
                "desc": "AI denoise + upscale (Real-ESRGAN). Used by editor's Denoise and Upscale tools.",
                "category": "Image",
                "target": "local",
            },
            # ── Tools ──
            {
                "name": "playwright",
                "pip": "playwright",
                "desc": "Browser automation for web tools",
                "category": "Tools",
                "target": "local",
            },
        ]

        # Most packages should not be installed through external means. Hence, set the default of the
        # install_cmd and update_cmd to None, which indicates that the recommended way to install/update is through the Cookbook # server setup or pip. Only system packages, should have explicit install/update commands provided.
        for pkg in packages:
            pkg.setdefault("install_cmd", None)
            pkg.setdefault("update_cmd", None)
        # Remote check: for remote-target packages, probe the selected server's
        # venv over SSH so a remote `pip install` actually reflects here.
        remote_status: dict = {}
        remote_details: dict = {}
        remote_names = [
            p["name"]
            for p in packages
            if p.get("target") == "remote" and p.get("kind") != "system"
        ]
        remote_system_names = [
            p["name"]
            for p in packages
            if p.get("target") == "remote" and p.get("kind") == "system"
        ]
        if host and remote_names:
            try:
                py = _package_probe_script(remote_names)
                # `venv` is validated but left unquoted so leading ~ expands on
                # the remote; quoting it breaks ~/venv activation.
                src = _venv_activate_prefix(venv)
                inner = f"{src}python3 -c {shlex.quote(py)}"
                argv = _ssh_base_argv(host, ssh_port) + [inner]
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=12)
                txt = out.decode("utf-8", errors="replace").strip()
                # The activate script can emit noise — take the last JSON line.
                for line in reversed(txt.splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        remote_details = _json.loads(line)
                        remote_status = {
                            name: _package_installed_from_probe(name, probe)
                            for name, probe in remote_details.items()
                            if isinstance(probe, dict)
                        }
                        break
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception:
                remote_status = {}
        if host and remote_system_names:
            try:
                checks = []
                for name in remote_system_names:
                    qn = shlex.quote(name)
                    checks.append(
                        f"if command -v {qn} >/dev/null 2>&1; then echo {qn}=1; else echo {qn}=0; fi"
                    )
                inner = " ; ".join(checks)
                argv = _ssh_base_argv(host, ssh_port) + [inner]
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _err = await asyncio.wait_for(proc.communicate(), timeout=12)
                txt = out.decode("utf-8", errors="replace").strip()
                for line in txt.splitlines():
                    name, sep, value = line.strip().partition("=")
                    if sep and name in remote_system_names:
                        remote_status[name] = value == "1"
            except ValueError as e:
                raise HTTPException(400, str(e))
            except Exception:
                pass

        def _detected_backend_from_suffix(suffix: str | None) -> str:
            s = str(suffix or "").lower()
            if s.startswith("cu"):
                return "cuda"
            if "rocm" in s or "hip" in s:
                return "rocm"
            if "vulkan" in s:
                return "vulkan"
            if "metal" in s:
                return "metal"
            return "cpu"

        def _backend_label(backend: str | None, suffix: str | None = None) -> str:
            b = str(backend or "").lower()
            if b == "cuda":
                s = str(suffix or "").lower()
                if s.startswith("cu") and len(s) > 2:
                    return f"CUDA{s[2:]}".upper()
                return "CUDA"
            if b == "rocm":
                return "ROCM"
            if b == "vulkan":
                return "VULKAN"
            if b == "metal":
                return "METAL"
            if b == "gpu":
                return "GPU"
            if b == "native":
                return "NATIVE"
            return "CPU"

        for pkg in packages:
            on_remote = bool(host and pkg.get("target") == "remote")
            probe = None
            if pkg["name"] == "llama_cpp":
                pkg["source_build_hint"] = (
                    "Source build avoids prebuilt wheel constraints and compiles for this host. "
                    "Requires cmake and a C/C++ toolchain."
                )
                pkg["source_build_docs_url"] = (
                    "https://github.com/abetlen/llama-cpp-python#installation-configuration"
                )
                pkg["source_build_actions"] = {
                    "prebuilt": True,
                    "force_cpu_prebuilt": True,
                    "source": True,
                    "rebuild": True,
                }
                try:
                    if host:
                        ctx = await _probe_remote_llama_wheel_context(
                            host=host,
                            ssh_port=ssh_port,
                            venv=venv,
                            platform_hint=platform,
                        )
                    else:
                        ctx = await _probe_local_llama_wheel_context(platform)
                    wheel = resolve_llama_cpp_wheel_suffix(
                        platform=ctx.get("platform"),
                        python_version=ctx.get("python_version"),
                        backend=ctx.get("backend"),
                        cuda_version=ctx.get("cuda_version"),
                        rocm_version=ctx.get("rocm_version"),
                        force_cpu_prebuilt=False,
                    )
                    pkg["selected_wheel_suffix"] = wheel.get("suffix")
                    pkg["wheel_reason"] = wheel.get("reason")
                    pkg["detected_backend"] = _detected_backend_from_suffix(
                        pkg.get("selected_wheel_suffix")
                    )
                    pkg["compatibility_flags"] = {
                        "python_supported": bool(wheel.get("python_supported")),
                        "backend": wheel.get("backend"),
                        "platform": wheel.get("platform"),
                        "cuda_version": ctx.get("cuda_version"),
                        "rocm_version": ctx.get("rocm_version"),
                        "python_arch": ctx.get("python_arch"),
                        "host_arch": ctx.get("host_arch"),
                        "python_arch_mismatch": bool(ctx.get("python_arch_mismatch")),
                    }
                    if bool(ctx.get("python_arch_mismatch")):
                        pkg["wheel_reason"] = (
                            f"{pkg['wheel_reason']} Warning: arm64 macOS host with x86_64 Python detected. "
                            "Use a native arm64 Python for best llama-cpp compatibility/performance."
                        )
                    pkg["forced_cpu_note"] = "Use CPU Prebuilt to bypass failing accelerator wheel resolution without source build."
                    pkg["can_force_cpu_prebuilt"] = True
                except Exception:
                    pkg["selected_wheel_suffix"] = "cpu"
                    pkg["wheel_reason"] = "Could not probe accelerator capabilities; falling back to CPU prebuilt."
                    pkg["detected_backend"] = "cpu"
                    pkg["compatibility_flags"] = {
                        "python_supported": False,
                        "backend": "unknown",
                        "platform": _normalize_platform_label(platform),
                        "cuda_version": None,
                        "python_arch": None,
                        "host_arch": None,
                        "python_arch_mismatch": False,
                    }
                    pkg["forced_cpu_note"] = "CPU Prebuilt remains available as a stable fallback."
                    pkg["can_force_cpu_prebuilt"] = True
            if on_remote:
                pkg["installed"] = bool(remote_status.get(pkg["name"], False))
                probe = remote_details.get(pkg["name"])
                if isinstance(probe, dict):
                    pkg["details"] = probe
                    note = _package_status_note(pkg["name"], probe)
                    if note:
                        pkg["status_note"] = note
                    if pkg["name"] == "llama_cpp":
                        llama_info = (
                            probe.get("llama")
                            if isinstance(probe.get("llama"), dict)
                            else {}
                        )
                        installed_backend = str(
                            llama_info.get("installed_backend") or ""
                        ).lower()
                        if (
                            llama_info.get("install_type") == "native"
                            and installed_backend in ("", "unknown")
                        ):
                            installed_backend = "native"
                        pkg["llama_install_type"] = llama_info.get("install_type")
                        pkg["installed_backend"] = installed_backend
            elif pkg.get("kind") == "system":
                if pkg["name"] == "APFEL":
                    pkg["applicable"] = IS_APPLE_SILICON
                    pkg["installed"] = which_tool("apfel") is not None
                    pkg["status_note"] = (
                        "Available on Apple Silicon (arm64) devices; exposed through a local OpenAI-compatible API."
                        if IS_APPLE_SILICON
                        else "Requires a native Apple Silicon Mac with Apple Foundational Models support."
                    )
                else:
                    pkg["installed"] = shutil.which(pkg["name"]) is not None
            elif pkg["name"] == "llama_cpp" and shutil.which("llama-server"):
                pkg["installed"] = True
                pkg["llama_install_type"] = "native"
                pkg["installed_backend"] = "native"
                pkg["status_note"] = (
                    f"native llama-server: {shutil.which('llama-server')}"
                )
                probe = {
                    "binaries": {"llama-server": shutil.which("llama-server")},
                    "dists": {},
                }
            elif pkg["name"] == "vllm":
                _vllm_cli = shutil.which("vllm")
                pkg["installed"] = _vllm_cli is not None
                if pkg["installed"]:
                    try:
                        _vllm_version = importlib_metadata.version(_pip_dist_name(pkg))
                    except importlib_metadata.PackageNotFoundError:
                        _vllm_version = None
                    probe = {
                        "binaries": {"vllm": _vllm_cli},
                        "dists": {"vllm": _vllm_version} if _vllm_version else {},
                    }
                    pkg["status_note"] = _package_status_note("vllm", probe)
            else:
                try:
                    _import_optional_dependency_for_status(pkg["name"])
                    importlib_metadata.version(_pip_dist_name(pkg))
                    pkg["installed"] = True
                except ImportError:
                    pkg["installed"] = False
                except importlib_metadata.PackageNotFoundError:
                    pkg["installed"] = False
                except Exception as e:
                    # Installed but crashes on import — e.g. a CUDA build of
                    # llama-cpp-python raising FileNotFoundError when the CUDA
                    # toolkit dir is absent. One broken optional package must not
                    # 500 the entire packages panel; report it as not usable.
                    pkg["installed"] = False

            if pkg["name"] == "llama_cpp":
                detected_backend = str(pkg.get("detected_backend") or "cpu").lower()
                installed_backend = str(pkg.get("installed_backend") or "").lower()
                if (
                    installed_backend
                    and installed_backend != "unknown"
                    and detected_backend
                    and installed_backend != detected_backend
                ):
                    pkg["backend_mismatch_note"] = (
                        f"Installed: {_backend_label(installed_backend)}, "
                        f"Detected: {_backend_label(detected_backend, pkg.get('selected_wheel_suffix'))}"
                    )
                    pkg["backend_mismatch_tooltip"] = (
                        "GPU prebuilt wheels are usually faster than CPU wheels on capable hosts. "
                        "Custom source builds can be faster than prebuilt GPU wheels, but require a full build toolchain."
                    )

            if pkg.get("installed"):
                update_status = _package_pip_update_status(pkg, probe)
                pkg["pip_update_available"] = update_status.available
                if update_status.note:
                    pkg["update_note"] = update_status.note

            if pkg["name"] == "docker":
                status = _docker_row_status(
                    on_remote=on_remote,
                    in_container=_running_in_container() if not on_remote else False,
                    installed=pkg["installed"],
                    default_hint=pkg.get("install_hint"),
                )
                pkg["applicable"] = status.applicable
                pkg["install_hint"] = status.install_hint
        return {"packages": packages}

    @router.post("/api/cookbook/llama-cpp/prereq-check")
    async def llama_cpp_prereq_check(request: Request):
        """Check whether the selected host has tools needed for llama-cpp-python builds.

        ``mode`` controls which tools are required:
        - ``"prebuilt"`` (default): cl.exe / g++ only — needed for any pip install on Windows.
        - ``"source"``: cl.exe / g++ AND cmake — source builds also need cmake.

        Response fields:
        - ``ok``: True when all required tools are directly on PATH.
        - ``needs_path_add``: True when tools exist (found via vswhere) but are NOT on PATH.
          ``missing`` will be empty in this case; the caller should offer to add them.
        - ``paths_to_add``: server-computed list of dirs to add (no client paths accepted).
        - ``missing``: populated only when tools are genuinely absent from the system.
        """
        _require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        host = str(body.get("remote_host") or "").strip()
        ssh_port = body.get("ssh_port")
        platform = str(body.get("platform") or "").strip().lower()
        mode = str(body.get("mode") or "prebuilt").strip().lower()
        source_wheel_hint = str(body.get("source_wheel_hint") or "").strip().lower()
        if mode not in ("prebuilt", "source"):
            mode = "prebuilt"

        def _backend_from_suffix(suffix: str) -> str:
            s = (suffix or "").strip().lower()
            if s.startswith("cu"):
                return "cuda"
            if s == "hip-radeon" or s.startswith("rocm"):
                return "hip"
            if s == "vulkan":
                return "vulkan"
            if s == "metal":
                return "metal"
            return "cpu"

        source_backend_hint = _backend_from_suffix(source_wheel_hint) if mode == "source" else "cpu"
        is_windows = platform == "windows" or (IS_WINDOWS and not host)

        if is_windows:
            # The PowerShell script distinguishes three states per tool:
            #   1. on PATH directly (cl_on_path / cmake_on_path)
            #   2. found via vswhere but not on PATH (cl_dir / cmake_dir non-empty)
            #   3. not installed at all (both false / empty)
            # This lets Python decide whether to offer "add to PATH" vs "install tools".
            ps_check = (
                """
                $r=@{}; 
                # vswhere is at a fixed, well-known location on all VS/BuildTools installs
                $vsw=\"${env:ProgramFiles(x86)}\\Microsoft Visual Studio\\Installer\\vswhere.exe\"; 
                $vsInst=if(Test-Path $vsw){
                    & $vsw -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
                }else{$null}; 
                $r.vs_inst=if($vsInst){$vsInst}else{''}; 
                # cl.exe — check PATH via Get-Command and where.exe fallback, then vswhere instance
                $clCmd=Get-Command cl.exe -ErrorAction SilentlyContinue; 
                $clWhere=''; 
                $clOnPath=[bool]$clCmd; 
                if(-not $clOnPath){$w=(where.exe cl.exe 2>$null | Select-Object -First 1); if($LASTEXITCODE -eq 0 -and $w){$clWhere=[string]$w; $clOnPath=$true}}; 
                $r.vsw=$vsw; 
                $r.cl_on_path=$clOnPath; 
                $r.cl_path=if($clCmd){[string]$clCmd.Source}elseif($clWhere){$clWhere}else{''}; 
                $r.cl_dir=if(-not $clOnPath -and $vsInst){
                  $clBin=Get-ChildItem (Join-Path $vsInst 'VC\\Tools\\MSVC') -Recurse -Filter cl.exe -ErrorAction SilentlyContinue | 
                  Where-Object{$_.FullName -match 'Hostx64\\\\x64'} | Select-Object -First 1; 
                  if($clBin){[string]($clBin.DirectoryName)}else{''}
                }else{''}; 
                # cmake — check PATH first, then vswhere instance CMake component
                $cmOnPath=[bool](Get-Command cmake -ErrorAction SilentlyContinue); 
                $r.cmake_on_path=$cmOnPath; 
                $r.cmake_dir=if(-not $cmOnPath -and $vsInst){
                  $cmBin=Join-Path $vsInst 'Common7\\IDE\\CommonExtensions\\Microsoft\\CMake\\CMake\\bin\\cmake.exe'; 
                  if(Test-Path $cmBin){[string](Split-Path $cmBin)}else{''}
                }else{''}; 
                # g++ on PATH (w64devkit / MinGW alternative to MSVC)
                $r.gxx=[bool](Get-Command g++ -ErrorAction SilentlyContinue); 
                # Composite booleans for backwards-compat with callers that only check these
                $r.cl=[bool]($clOnPath -or $r.cl_dir -ne '' -or $r.gxx); 
                $r.cmake=[bool]($cmOnPath -or $r.cmake_dir -ne ''); 
                # Accelerator/toolkit signals for backend-specific source builds
                $r.nvcc_on_path=[bool](Get-Command nvcc -ErrorAction SilentlyContinue); 
                $r.cuda_root=if($env:CUDAToolkit_ROOT){
                    $env:CUDAToolkit_ROOT
                } elseif ($env:CUDA_PATH) {
                    $env:CUDA_PATH
                }else{
                    Get-ChildItem (\"${env:ProgramFiles}\\NVIDIA GPU Computing Toolkit\\CUDA\\\") -Directory -ErrorAction SilentlyContinue | 
                        Sort-Object Name -Descending | 
                        Select-Object -ExpandProperty FullName -First 1
                }; 
                $r.hipcc_on_path=[bool](Get-Command hipcc -ErrorAction SilentlyContinue); 
                $r.hip_path=if($env:HIP_PATH){$env:HIP_PATH}elseif($env:ROCM_PATH){$env:ROCM_PATH}else{''}; 
                $r.vulkaninfo_on_path=[bool](Get-Command vulkaninfo -ErrorAction SilentlyContinue); 
                $r.vulkan_sdk=if($env:VULKAN_SDK){$env:VULKAN_SDK}else{''}; 
                $r | ConvertTo-Json -Compress
                """
            )
            if host:
                try:
                    argv = _ssh_base_argv(host, ssh_port) + [
                        f"powershell -NoProfile -Command {shlex.quote(ps_check)}"
                    ]
                except ValueError as e:
                    raise HTTPException(400, str(e))
            else:
                argv = ["powershell", "-NoProfile", "-Command", ps_check]
        else:
            sh_check = (
                "CM=$(command -v cmake >/dev/null 2>&1; echo $?); "
                "CC=$(command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || command -v clang >/dev/null 2>&1 || command -v c++ >/dev/null 2>&1; echo $?); "
                "NV=$(command -v nvcc >/dev/null 2>&1; echo $?); "
                "HP=$(command -v hipcc >/dev/null 2>&1 || command -v hipconfig >/dev/null 2>&1; echo $?); "
                "VK=$(command -v vulkaninfo >/dev/null 2>&1; echo $?); "
                "XR=$(command -v xcrun >/dev/null 2>&1; echo $?); "
                "printf '{\"cmake\":%s,\"compiler\":%s,\"nvcc\":%s,\"hip\":%s,\"vulkan\":%s,\"xcrun\":%s}' \"$([ \"$CM\" = 0 ] && echo true || echo false)\" \"$([ \"$CC\" = 0 ] && echo true || echo false)\" \"$([ \"$NV\" = 0 ] && echo true || echo false)\" \"$([ \"$HP\" = 0 ] && echo true || echo false)\" \"$([ \"$VK\" = 0 ] && echo true || echo false)\" \"$([ \"$XR\" = 0 ] && echo true || echo false)\""
            )
            if host:
                try:
                    argv = _ssh_base_argv(host, ssh_port) + [sh_check]
                except ValueError as e:
                    raise HTTPException(400, str(e))
            else:
                argv = ["bash", "-lc", sh_check]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "error": "Prerequisite check timed out.",
                "missing": [],
            }

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": err.decode("utf-8", errors="replace")[-500:] or "Prerequisite check failed.",
                "missing": [],
            }

        txt = out.decode("utf-8", errors="replace").strip()
        payload = None
        for line in reversed(txt.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except Exception:
                    payload = None
                break
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "Could not parse prerequisite check output.",
                "missing": [],
                "output": txt[-500:],
            }

        missing = []
        needs_path_add = False
        paths_to_add: list[str] = []

        if is_windows:
            cl_on_path = bool(payload.get("cl_on_path") or payload.get("gxx"))
            cl_dir = str(payload.get("cl_dir") or "").strip()
            cl_found_anywhere = cl_on_path or bool(cl_dir)

            cmake_on_path = bool(payload.get("cmake_on_path"))
            cmake_dir = str(payload.get("cmake_dir") or "").strip()
            cmake_found_anywhere = cmake_on_path or bool(cmake_dir)

            # Determine which tools are required for this mode
            need_cmake = (mode == "source")

            if not cl_found_anywhere:
                missing.append(
                    {
                        "tool": "c/c++ compiler (MSVC cl.exe or g++)",
                        "hint": (
                            "No C++ compiler was found. "
                            "Install VS Build Tools and enable 'Desktop development with C++' "
                            "(or the 'MSVC v143' individual component). "
                            "Alternatively, install w64devkit/MinGW and ensure g++ is on PATH."
                        ),
                        "url": _VS_BUILD_TOOLS_URL,
                    }
                )
            elif not cl_on_path and cl_dir:
                # Found via vswhere but not on PATH — offer to add
                paths_to_add.append(cl_dir)

            if need_cmake and not cmake_found_anywhere:
                missing.append(
                    {
                        "tool": "cmake",
                        "hint": (
                            "CMake was not found via Visual Studio or on PATH. "
                            "In the VS Installer, ensure 'C++ CMake tools for Windows' is checked "
                            "under Individual Components, or install cmake standalone and add it to PATH."
                        ),
                        "url": _CMAKE_DOWNLOAD_URL,
                    }
                )
            elif need_cmake and not cmake_on_path and cmake_dir:
                if cmake_dir not in paths_to_add:
                    paths_to_add.append(cmake_dir)

            if mode == "source":
                if source_backend_hint == "cuda":
                    nvcc_on_path = bool(payload.get("nvcc_on_path"))
                    cuda_root = str(payload.get("cuda_root") or "").strip()
                    cuda_ready = nvcc_on_path or bool(cuda_root)
                    if not nvcc_on_path and cuda_root:
                        cuda_bin_dir = os.path.join(cuda_root, "bin")
                        # Local Windows only: if CUDA is installed but nvcc is not on PATH,
                        # offer a PATH-add flow instead of failing immediately.
                        if os.path.isdir(cuda_bin_dir) and cuda_bin_dir not in paths_to_add:
                            paths_to_add.append(cuda_bin_dir)
                    if not cuda_ready:
                        missing.append(
                            {
                                "tool": "CUDA Toolkit (nvcc)",
                                "hint": (
                                    "CUDA source builds require NVIDIA CUDA Toolkit. "
                                    "Install CUDA Toolkit and ensure nvcc is on PATH or set CUDAToolkit_ROOT/CUDA_PATH."
                                ),
                                "url": "https://developer.nvidia.com/cuda-downloads",
                            }
                        )
                elif source_backend_hint == "hip":
                    hip_ready = bool(payload.get("hipcc_on_path")) or bool(str(payload.get("hip_path") or "").strip())
                    if not hip_ready:
                        missing.append(
                            {
                                "tool": "AMD HIP/ROCm toolchain",
                                "hint": (
                                    "HIP source builds require AMD HIP SDK/ROCm. "
                                    "Install HIP SDK and ensure hipcc is on PATH or set HIP_PATH/ROCM_PATH."
                                ),
                                "url": "https://rocm.docs.amd.com/",
                            }
                        )
                elif source_backend_hint == "vulkan":
                    vk_ready = bool(payload.get("vulkaninfo_on_path")) or bool(str(payload.get("vulkan_sdk") or "").strip())
                    if not vk_ready:
                        missing.append(
                            {
                                "tool": "Vulkan SDK",
                                "hint": (
                                    "Vulkan source builds require Vulkan SDK headers/libs. "
                                    "Install Vulkan SDK and set VULKAN_SDK (or make vulkaninfo available)."
                                ),
                                "url": "https://vulkan.lunarg.com/sdk/home",
                            }
                        )
                elif source_backend_hint == "metal":
                    missing.append(
                        {
                            "tool": "macOS Metal toolchain",
                            "hint": "Metal source builds are only supported on macOS (Xcode Command Line Tools).",
                            "url": "https://developer.apple.com/metal/",
                        }
                    )

            # needs_path_add: tools exist but aren't on PATH, and nothing is truly missing
            needs_path_add = bool(paths_to_add) and not missing
        else:
            if not bool(payload.get("cmake")):
                missing.append(
                    {
                        "tool": "cmake",
                        "hint": "Install cmake on the selected server.",
                        "url": _CMAKE_DOWNLOAD_URL,
                    }
                )
            if not bool(payload.get("compiler")):
                missing.append(
                    {
                        "tool": "c/c++ compiler",
                        "hint": "Install gcc/clang build tools on the selected server.",
                        "url": "https://github.com/abetlen/llama-cpp-python#installation",
                    }
                )
            if mode == "source":
                if source_backend_hint == "cuda" and not bool(payload.get("nvcc")):
                    missing.append(
                        {
                            "tool": "CUDA Toolkit (nvcc)",
                            "hint": "CUDA source builds require CUDA toolkit (nvcc) on the selected server.",
                            "url": "https://developer.nvidia.com/cuda-downloads",
                        }
                    )
                elif source_backend_hint == "hip" and not bool(payload.get("hip")):
                    missing.append(
                        {
                            "tool": "HIP/ROCm toolchain",
                            "hint": "HIP source builds require hipcc/hipconfig on the selected server.",
                            "url": "https://rocm.docs.amd.com/",
                        }
                    )
                elif source_backend_hint == "vulkan" and not bool(payload.get("vulkan")):
                    missing.append(
                        {
                            "tool": "Vulkan SDK/runtime tools",
                            "hint": "Vulkan source builds require Vulkan SDK/runtime tools (vulkaninfo) on the selected server.",
                            "url": "https://vulkan.lunarg.com/sdk/home",
                        }
                    )
                elif source_backend_hint == "metal":
                    if platform and platform not in ("mac", "macos", "darwin"):
                        missing.append(
                            {
                                "tool": "macOS Metal toolchain",
                                "hint": "Metal source builds are only supported on macOS.",
                                "url": "https://developer.apple.com/metal/",
                            }
                        )
                    elif not bool(payload.get("xcrun")):
                        missing.append(
                            {
                                "tool": "Xcode Command Line Tools",
                                "hint": "Install Xcode Command Line Tools (`xcode-select --install`) for Metal source builds.",
                                "url": "https://developer.apple.com/xcode/resources/",
                            }
                        )

        all_ok = not missing and not needs_path_add
        return {
            "ok": all_ok,
            "needs_path_add": needs_path_add,
            "paths_to_add": paths_to_add,
            "checks": payload,
            "source_backend_hint": source_backend_hint,
            "missing": missing,
            "output": txt[-500:],
        }

    @router.post("/api/cookbook/llama-cpp/resolve-wheel")
    async def llama_cpp_resolve_wheel(request: Request):
        """Resolve best llama-cpp prebuilt wheel suffix for selected host."""
        _require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        host = str(body.get("remote_host") or "").strip()
        ssh_port = body.get("ssh_port")
        venv = body.get("venv")
        platform = str(body.get("platform") or "").strip().lower()
        force_cpu_prebuilt = bool(body.get("force_cpu_prebuilt"))

        try:
            if host:
                ctx = await _probe_remote_llama_wheel_context(
                    host=host,
                    ssh_port=ssh_port,
                    venv=venv,
                    platform_hint=platform,
                )
            else:
                ctx = await _probe_local_llama_wheel_context(platform)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            return {
                "ok": False,
                "error": f"Wheel capability probe failed: {e}",
                "resolution": {
                    "suffix": "cpu",
                    "reason": "Probe failed; defaulting to CPU prebuilt wheel.",
                },
            }

        wheel = resolve_llama_cpp_wheel_suffix(
            platform=ctx.get("platform"),
            python_version=ctx.get("python_version"),
            backend=ctx.get("backend"),
            cuda_version=ctx.get("cuda_version"),
            rocm_version=ctx.get("rocm_version"),
            force_cpu_prebuilt=force_cpu_prebuilt,
        )
        warnings = []
        if bool(ctx.get("python_arch_mismatch")):
            warnings.append(
                "Detected arm64 macOS host with x86_64 Python. Prefer native arm64 Python for llama-cpp Metal wheels."
            )
        return {
            "ok": True,
            "context": ctx,
            "resolution": wheel,
            "warnings": warnings,
            "can_force_cpu_prebuilt": True,
            "recommended_next_actions": [
                "Use Prebuilt for the resolved accelerator wheel.",
                "Use CPU Prebuilt when accelerator wheel import/install fails.",
                "Use Build Src if you need host-specific compilation.",
            ],
        }

    @router.post("/api/cookbook/llama-cpp/add-tools-to-path")
    async def llama_cpp_add_to_path(request: Request):
        """Permanently add known tool directories to the current user's PATH.

        Local Windows only. The client may request one or more known tools, and
        all candidate directories are discovered server-side and validated before
        PATH is updated.

        Supported tools:
        - ``cl``: MSVC cl.exe (via vswhere)
        - ``cmake``: CMake (via PATH, VS CMake component, common install root)
        - ``nvcc``: NVIDIA CUDA Toolkit nvcc.exe (via env + common install roots)
        """
        _require_admin(request)
        _reject_cross_site(request)
        body = await request.json()
        host = str(body.get("remote_host") or "").strip()
        if host:
            raise HTTPException(
                400,
                "add-tools-to-path is only supported for local Windows. "
                "On a remote server, add tool directories to PATH manually via System Properties > Environment Variables.",
            )
        if not IS_WINDOWS:
            raise HTTPException(400, "add-tools-to-path is only supported on Windows.")

        requested = body.get("tools")
        if isinstance(requested, str):
            requested_tools = [requested]
        elif isinstance(requested, list):
            requested_tools = [str(t) for t in requested]
        else:
            requested_tools = ["cl"]

        requested_tools = [t.strip().lower() for t in requested_tools if str(t).strip()]
        requested_tools = list(dict.fromkeys(requested_tools))
        known_tools = {"cl", "cmake", "nvcc"}
        invalid = [t for t in requested_tools if t not in known_tools]
        if invalid:
            raise HTTPException(400, f"Unsupported tools: {', '.join(invalid)}. Allowed: cl, cmake, nvcc")

        # Compute trusted candidate directories server-side only.
        ps_find = f"""
$req=@({", ".join(f"'{t}'" for t in requested_tools)});
$r=@{{
    cl=@();
    cmake=@();
    nvcc=@()
}};

$vsw="${{env:ProgramFiles(x86)}}\\Microsoft Visual Studio\\Installer\\vswhere.exe";
$vsInst = if (Test-Path $vsw) {{
    & $vsw -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
}} else {{
    $null
}};

if ($req -contains 'cl') {{
    if ($vsInst) {{
        $clBin = Get-ChildItem (Join-Path $vsInst 'VC\\Tools\\MSVC') -Recurse -Filter cl.exe -ErrorAction SilentlyContinue |
                 Where-Object {{ $_.FullName -match 'Hostx64\\\\x64' }} |
                 Select-Object -First 1;
        if ($clBin) {{ $r.cl += [string]$clBin.DirectoryName }}
    }}
}}

if ($req -contains 'cmake') {{
    $cmCmd = Get-Command cmake.exe -ErrorAction SilentlyContinue;
    if ($cmCmd) {{ $r.cmake += [string](Split-Path $cmCmd.Source) }}

    if ($vsInst) {{
        $cmBin = Join-Path $vsInst 'Common7\\IDE\\CommonExtensions\\Microsoft\\CMake\\CMake\\bin\\cmake.exe';
        if (Test-Path $cmBin) {{ $r.cmake += [string](Split-Path $cmBin) }}
    }}

    $pf = $env:ProgramFiles;
    if ($pf) {{
        $cmBase = Join-Path $pf 'CMake\\bin';
        if (Test-Path (Join-Path $cmBase 'cmake.exe')) {{ $r.cmake += [string]$cmBase }}
    }}
}}

if ($req -contains 'nvcc') {{
    $nvccCmd = Get-Command nvcc.exe -ErrorAction SilentlyContinue;
    if ($nvccCmd) {{ $r.nvcc += [string](Split-Path $nvccCmd.Source) }}

    $cudaRoots = @($env:CUDAToolkit_ROOT, $env:CUDA_PATH) | Where-Object {{ $_ }};
    foreach ($root in $cudaRoots) {{
        $b = Join-Path $root 'bin';
        if (Test-Path (Join-Path $b 'nvcc.exe')) {{ $r.nvcc += [string]$b }}
    }}

    $pf = $env:ProgramFiles;
    if ($pf) {{
        $cudaBase = Join-Path $pf 'NVIDIA GPU Computing Toolkit\\CUDA';
        if (Test-Path $cudaBase) {{
            Get-ChildItem $cudaBase -Directory -ErrorAction SilentlyContinue | ForEach-Object {{
                $b = Join-Path $_.FullName 'bin';
                if (Test-Path (Join-Path $b 'nvcc.exe')) {{ $r.nvcc += [string]$b }}
            }}
        }}
    }}
}}

$r.cl    = @($r.cl    | Where-Object {{ $_ }} | Select-Object -Unique);
$r.cmake = @($r.cmake | Where-Object {{ $_ }} | Select-Object -Unique);
$r.nvcc  = @($r.nvcc  | Where-Object {{ $_ }} | Select-Object -Unique);

$r | ConvertTo-Json -Compress
"""

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", ps_find,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            raise HTTPException(500, "tool lookup timed out.")

        if proc.returncode != 0:
            err_txt = err.decode("utf-8", errors="replace")[-400:]
            return {"ok": False, "error": f"tool lookup failed: {err_txt}", "paths_added": [], "bash_exports": []}

        raw = out.decode("utf-8", errors="replace").strip()
        found_by_tool: dict[str, list[str]] = {"cl": [], "cmake": [], "nvcc": []}
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        found_by_tool = {
                            "cl": [str(p) for p in (parsed.get("cl") or []) if p],
                            "cmake": [str(p) for p in (parsed.get("cmake") or []) if p],
                            "nvcc": [str(p) for p in (parsed.get("nvcc") or []) if p],
                        }
                except Exception:
                    pass
                break

        # Keep requested tool order when building the final add list.
        computed_dirs: list[str] = []
        for t in requested_tools:
            for p in found_by_tool.get(t, []):
                if p not in computed_dirs:
                    computed_dirs.append(p)

        valid_dirs = [p for p in computed_dirs if p and os.path.isdir(p)]
        if not valid_dirs:
            return {
                "ok": False,
                "error": "No matching tool directories were found for the requested tools, or nothing needed adding.",
                "paths_added": [],
                "bash_exports": [],
                "requested_tools": requested_tools,
                "found_by_tool": found_by_tool,
            }

        # Build the new User PATH: prepend new dirs, dedupe, keep existing entries.
        ps_add = f"""
$dirs=@({", ".join(f"'{d}'" for d in valid_dirs)});
$cur=[Environment]::GetEnvironmentVariable('PATH','User') -split ';' | Where-Object {{ $_ }};
$new=@($dirs | Where-Object {{ $_ -notin $cur }}) + @($cur);
[Environment]::SetEnvironmentVariable('PATH', ($new -join ';'), 'User');
Write-Output 'ok'
"""

        try:
            proc2 = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", ps_add,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _out2, err2 = await asyncio.wait_for(proc2.communicate(), timeout=15)
        except asyncio.TimeoutError:
            raise HTTPException(500, "PATH update timed out.")

        if proc2.returncode != 0:
            err_txt = err2.decode("utf-8", errors="replace")[-400:]
            return {"ok": False, "error": f"PATH update failed: {err_txt}", "paths_added": [], "bash_exports": []}

        bash_exports = [_win_path_to_bash(p) for p in valid_dirs]
        return {
            "ok": True,
            "paths_added": valid_dirs,
            "bash_exports": bash_exports,
            "requested_tools": requested_tools,
            "found_by_tool": found_by_tool,
        }

    @router.post("/api/cookbook/packages/install")
    async def install_package(request: Request):
        """Install a package via pip. Admin only — pip install is effectively code exec."""
        _require_admin(request)
        import sys as _sys

        body = await request.json()
        pip_name = body.get("pip")
        if not pip_name:
            return {"ok": False, "error": "No package specified"}
        # Validate against known packages to prevent arbitrary pip install
        known = {
            "rembg[gpu]",
            "hf_transfer",
            "llama-cpp-python[server]",
            "sglang[all]",
            "diffusers",
            "diffusers[torch]",
            "transformers",
            "TTS",
            "bark",
            "faster-whisper",
            "playwright",
            "realesrgan",
            "gfpgan",
            "insightface",
            "onnxruntime-gpu",
            "onnxruntime",
            "hdbscan",
            "vllm",
        }
        if pip_name not in known:
            return {"ok": False, "error": f"Unknown package: {pip_name}"}
        cmd = [_sys.executable, "-m", "pip", "install", pip_name]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return {"ok": True, "output": stdout.decode()[-200:]}
        return {"ok": False, "error": stderr.decode()[-300:]}

    @router.post("/api/cookbook/rebuild-engine")
    async def rebuild_engine(request: Request):
        """Clear the cached llama.cpp build so the next serve recompiles.

        Admin only — this removes the Cookbook-managed ``~/bin/llama-server``
        symlink and ``~/llama.cpp/build`` directory, locally or on the selected
        remote server. It installs and downloads nothing; the next llama.cpp
        serve rebuilds from source and picks up CUDA/HIP if a toolchain is now
        present. This is the missing "force a fresh GPU build" lever for hosts
        stuck on a CPU-only llama-server.
        """
        _require_admin(request)
        from routes.cookbook_helpers import _llama_cpp_rebuild_cmd

        body = await request.json()
        engine = str(body.get("engine") or "llamacpp").strip()
        if engine != "llamacpp":
            return {"ok": False, "error": f"Unsupported engine: {engine}"}
        host = str(body.get("remote_host") or "").strip()
        ssh_port = body.get("ssh_port")
        cmd = _llama_cpp_rebuild_cmd()
        try:
            argv = (
                (_ssh_base_argv(host, ssh_port) + [cmd])
                if host
                else ["bash", "-lc", cmd]
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "Rebuild-engine command timed out."}
        if proc.returncode == 0:
            return {"ok": True, "output": out.decode("utf-8", errors="replace")[-400:]}
        return {"ok": False, "error": err.decode("utf-8", errors="replace")[-400:]}

    return router

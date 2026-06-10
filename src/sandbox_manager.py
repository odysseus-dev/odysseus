"""
sandbox_manager.py

Run untrusted code snippets in an ephemeral, isolated Docker container
(Ubuntu 22.04 + Python 3 + Node.js, built from ``docker/sandbox.Dockerfile``).

The container is started with no network, a read-only root filesystem, dropped
Linux capabilities, a non-root user, and memory/CPU/PID limits, so a snippet
cannot touch the host or the rest of the network. Each run uses a fresh
container (``docker run --rm``) and a throwaway host directory that holds only
the snippet, mounted read-only at ``/sandbox``.

This module shells out to the ``docker`` CLI rather than the Docker SDK to avoid
adding a dependency; availability is detected at runtime and degrades to a clear
error when Docker is not installed.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from typing import Dict, List, Tuple

from src.constants import BASE_DIR, DATA_DIR

logger = logging.getLogger(__name__)

# Image tag and the Dockerfile that builds it. Build context is BASE_DIR; the
# Dockerfile copies nothing, so the context contents do not matter.
IMAGE_NAME = "odysseus-sandbox:latest"
DOCKERFILE = os.path.join(BASE_DIR, "docker", "sandbox.Dockerfile")

# Where snippet files are staged before being mounted into the container. Kept
# under DATA_DIR (not the OS temp dir) so it lives on persistent storage and is
# easy to inspect/clean.
_RUNS_DIR = os.path.join(DATA_DIR, "sandbox_runs")

# language alias -> (snippet filename, in-container argv)
LANG_RUNNERS: Dict[str, Tuple[str, List[str]]] = {
    "python": ("snippet.py", ["python3", "/sandbox/snippet.py"]),
    "python3": ("snippet.py", ["python3", "/sandbox/snippet.py"]),
    "py": ("snippet.py", ["python3", "/sandbox/snippet.py"]),
    "node": ("snippet.js", ["node", "/sandbox/snippet.js"]),
    "javascript": ("snippet.js", ["node", "/sandbox/snippet.js"]),
    "js": ("snippet.js", ["node", "/sandbox/snippet.js"]),
    "bash": ("snippet.sh", ["bash", "/sandbox/snippet.sh"]),
    "sh": ("snippet.sh", ["bash", "/sandbox/snippet.sh"]),
}

# Defaults for a single run; conservative so a snippet cannot exhaust the host.
DEFAULT_TIMEOUT = 30
DEFAULT_MEMORY = "512m"
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS = 256


def docker_available() -> bool:
    """True when the ``docker`` CLI is present and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def image_exists() -> bool:
    """True when the sandbox image has already been built locally."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE_NAME],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def build_image(timeout: int = 1800) -> Tuple[str, bool]:
    """Build the sandbox image from ``docker/sandbox.Dockerfile``.

    Returns ``(log_tail, success)``. Requires network access (apt + NodeSource).
    """
    try:
        result = subprocess.run(
            ["docker", "build", "-f", DOCKERFILE, "-t", IMAGE_NAME, BASE_DIR],
            capture_output=True, text=True, timeout=timeout,
        )
        tail = (result.stdout or "")[-2000:] + (result.stderr or "")[-2000:]
        return tail.strip(), result.returncode == 0
    except subprocess.TimeoutExpired:
        return f"image build timed out ({timeout}s)", False
    except Exception as e:  # pragma: no cover - defensive
        return str(e), False


def ensure_image() -> Tuple[str, bool]:
    """Build the sandbox image on first use; no-op when it already exists."""
    if image_exists():
        return "image present", True
    logger.info("Building sandbox image %s (first use)", IMAGE_NAME)
    return build_image()


def _docker_run_argv(
    container_name: str,
    runner_argv: List[str],
    host_dir: str,
    *,
    network: bool,
    memory: str,
    cpus: str,
    pids: int,
) -> List[str]:
    """Assemble the hardened ``docker run`` command for one snippet."""
    argv = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network", "bridge" if network else "none",
        "--memory", memory,
        "--memory-swap", memory,  # equal to --memory => no swap
        "--cpus", cpus,
        "--pids-limit", str(pids),
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "-e", "HOME=/tmp",
        "-v", f"{host_dir}:/sandbox:ro",
        "-w", "/sandbox",
        IMAGE_NAME,
    ]
    argv.extend(runner_argv)
    return argv


def run_sync(
    code: str,
    language: str = "python",
    *,
    timeout: int = DEFAULT_TIMEOUT,
    network: bool = False,
    memory: str = DEFAULT_MEMORY,
    cpus: str = DEFAULT_CPUS,
    pids: int = DEFAULT_PIDS,
    auto_build: bool = True,
) -> Tuple[str, bool]:
    """Run ``code`` in an ephemeral container and return ``(output, success)``.

    ``language`` is one of the keys in :data:`LANG_RUNNERS`. The snippet runs
    with no network by default; pass ``network=True`` to allow outbound access.
    """
    lang = (language or "").strip().lower()
    runner = LANG_RUNNERS.get(lang)
    if runner is None:
        return (
            f"Unsupported language '{language}'. Supported: "
            + ", ".join(sorted(LANG_RUNNERS)),
            False,
        )
    if not code or not code.strip():
        return "No code provided", False
    if not docker_available():
        return "Docker is not available on this host", False
    if auto_build:
        log_tail, ok = ensure_image()
        if not ok:
            return f"Failed to build sandbox image: {log_tail}", False
    elif not image_exists():
        return f"Sandbox image {IMAGE_NAME} not built", False

    filename, runner_argv = runner
    os.makedirs(_RUNS_DIR, exist_ok=True)
    host_dir = os.path.join(_RUNS_DIR, uuid.uuid4().hex)
    os.makedirs(host_dir, exist_ok=True)
    container_name = "odysseus-sandbox-" + uuid.uuid4().hex[:12]
    try:
        snippet_path = os.path.join(host_dir, filename)
        with open(snippet_path, "w", encoding="utf-8") as fh:
            fh.write(code)

        argv = _docker_run_argv(
            container_name, runner_argv, host_dir,
            network=network, memory=memory, cpus=cpus, pids=pids,
        )
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            _force_remove(container_name)
            return f"Execution timed out ({timeout}s)", False

        output = (result.stdout or "").strip()
        if result.returncode != 0 and result.stderr:
            output += ("\nSTDERR: " + result.stderr.strip())
        return output or "(no output)", result.returncode == 0
    except Exception as e:  # pragma: no cover - defensive
        return str(e), False
    finally:
        shutil.rmtree(host_dir, ignore_errors=True)


def _force_remove(container_name: str) -> None:
    """Best-effort kill+remove of a container left behind by a timeout."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:  # pragma: no cover - defensive
        pass


async def run(
    code: str,
    language: str = "python",
    **kwargs,
) -> Tuple[str, bool]:
    """Async wrapper around :func:`run_sync` (keeps the event loop responsive)."""
    return await asyncio.to_thread(run_sync, code, language, **kwargs)

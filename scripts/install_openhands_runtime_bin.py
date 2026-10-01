#!/usr/bin/env python3
"""Install pinned OpenCode and Hermes ACP binaries into openhands-runtime-bin.

Must not use latest OpenCode or Hermes ``main`` installer. Agent Server is
Linux; this writes Linux artifacts into the overlay ``/opt/oh-bin`` mount.

Hermes installer pin:
https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.7/scripts/install.sh
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSIONS_PATH = ROOT / "deploy" / "openhands" / "versions.env"
DEFAULT_DEST = ROOT / "data" / "openhands-runtime-bin"
OPENCODE_RELEASE = "https://github.com/anomalyco/opencode/releases/download/"
HERMES_TAG_INSTALLER = (
    "https://raw.githubusercontent.com/NousResearch/hermes-agent/"
)
AGENT_SERVER_IMAGE_KEY = "OPENHANDS_AGENT_SERVER_IMAGE"


def read_versions(path: Path = VERSIONS_PATH) -> dict[str, str]:
    """Parse ``versions.env`` key=value pins.

    Parameters
    ----------
    path
        Env file to read.

    Returns
    -------
    dict of str
        Pin map.

    Example
    -------
    ``read_versions()["OPENCODE_VERSION"]``
    """

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip()
    return values


def _require_pin(values: dict[str, str], key: str) -> str:
    value = (values.get(key) or "").strip()
    if not value or value.lower() in {"latest", "main"}:
        raise SystemExit(f"{key} must be a concrete pin, not {value!r}")
    return value


def detect_agent_server_linux_arch(values: dict[str, str]) -> str:
    """Return ``x64`` or ``arm64`` for the Agent Server image.

    Parameters
    ----------
    values
        Pin map including ``OPENHANDS_AGENT_SERVER_IMAGE``.

    Returns
    -------
    str
        OpenCode linux archive arch token.

    Example
    -------
    ``detect_agent_server_linux_arch(read_versions())``
    """

    image = values.get(AGENT_SERVER_IMAGE_KEY, "")
    if shutil.which("docker") and image:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Architecture}}"],
            check=False,
            capture_output=True,
            text=True,
        )
        arch = inspect.stdout.strip()
        if arch in {"arm64", "aarch64"}:
            return "arm64"
        if arch in {"amd64", "x86_64"}:
            return "x64"
    return "x64"


def opencode_asset(version: str, arch: str, values: dict[str, str]) -> tuple[str, str]:
    """Return the official OpenCode linux tarball URL and sha256.

    Parameters
    ----------
    version
        Tag such as ``v1.18.29``.
    arch
        ``x64`` or ``arm64``.
    values
        Pin map with archive digests.

    Returns
    -------
    tuple of str
        ``(url, sha256)``.

    Example
    -------
    ``opencode_asset("v1.18.29", "x64", read_versions())``
    """

    tag = version if version.startswith("v") else f"v{version}"
    key = "OPENCODE_LINUX_X64_SHA256" if arch == "x64" else "OPENCODE_LINUX_ARM64_SHA256"
    digest = _require_pin(values, key)
    url = f"{OPENCODE_RELEASE}{tag}/opencode-linux-{arch}.tar.gz"
    return url, digest


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _curl(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` with curl so host cert stores work.

    Parameters
    ----------
    url
        Official HTTPS URL.
    dest
        Destination file.
    """

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["curl", "-fsSL", url, "-o", str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr or f"curl failed for {url}")


def _live_opencode_version() -> str:
    if not shutil.which("docker"):
        return ""
    probe = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "-f",
            str(ROOT / "docker-compose.openhands.yml"),
            "exec",
            "-T",
            "openhands-agent-server",
            "/opt/oh-bin/opencode",
            "--version",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return (probe.stdout or probe.stderr or "").strip()


def install_opencode(dest: Path, values: dict[str, str], arch: str) -> Path:
    """Download the pinned OpenCode linux archive into ``dest/opencode``.

    Parameters
    ----------
    dest
        Host runtime-bin directory.
    values
        Pin map.
    arch
        ``x64`` or ``arm64``.

    Returns
    -------
    Path
        Installed ``opencode`` path.

    Example
    -------
    ``install_opencode(Path("data/openhands-runtime-bin"), read_versions(), "x64")``
    """

    version = _require_pin(values, "OPENCODE_VERSION")
    url, digest = opencode_asset(version, arch, values)
    target = dest / "opencode"
    pin_path = dest / ".opencode-pin.json"
    if target.is_file() and pin_path.is_file():
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        if pin.get("version") == version and pin.get("sha256") == digest:
            return target
    dest.mkdir(parents=True, exist_ok=True)
    live = _live_opencode_version()
    if target.is_file() and version.lstrip("v") in live:
        pin_path.write_text(
            json.dumps({"version": version, "sha256": digest, "arch": arch, "verified": "live"}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        return target
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "opencode.tar.gz"
        _curl(url, archive_path)
        archive = archive_path.read_bytes()
    actual = _sha256_bytes(archive)
    if actual != digest:
        raise SystemExit(f"OpenCode archive sha256 mismatch: {actual} != {digest}")
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "opencode.tar.gz"
        archive_path.write_bytes(archive)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        found = next(Path(tmp).rglob("opencode"), None)
        if found is None or not found.is_file():
            raise SystemExit("OpenCode archive did not contain an opencode binary")
        shutil.copy2(found, target)
    target.chmod(target.stat().st_mode | 0o111)
    pin_path.write_text(
        json.dumps({"version": version, "sha256": digest, "arch": arch}, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def hermes_installer_url(version: str) -> str:
    """Return the tagged official Hermes install.sh URL.

    Parameters
    ----------
    version
        Tag such as ``v2026.9.7``. Must not be ``main``.

    Returns
    -------
    str
        Raw GitHub URL for that tag.

    Example
    -------
    ``hermes_installer_url("v2026.9.7")``
    """

    tag = _require_pin({"HERMES_VERSION": version}, "HERMES_VERSION")
    if tag == "main":
        raise SystemExit("Hermes installer must not use main")
    return f"{HERMES_TAG_INSTALLER}{tag}/scripts/install.sh"


def install_hermes(dest: Path, values: dict[str, str], platform: str = "linux/amd64") -> Path:
    """Run the tagged Hermes installer into ``dest`` using a Linux container.

    Parameters
    ----------
    dest
        Host runtime-bin directory.
    values
        Pin map.
    platform
        Docker platform matching Agent Server.

    Returns
    -------
    Path
        Installed ``hermes`` wrapper path.

    Example
    -------
    ``install_hermes(Path("data/openhands-runtime-bin"), read_versions())``
    """

    if not shutil.which("docker"):
        raise SystemExit("docker is required to install the Linux Hermes distribution")
    version = _require_pin(values, "HERMES_VERSION")
    commit = _require_pin(values, "HERMES_GIT_COMMIT")
    url = hermes_installer_url(version)
    wrapper = dest / "hermes"
    tree = dest / "hermes-agent"
    pin_path = dest / ".hermes-pin.json"
    if wrapper.is_file() and (tree / "hermes").is_file() and (tree / ".venv" / "bin" / "python").is_file() and pin_path.is_file():
        pin = json.loads(pin_path.read_text(encoding="utf-8"))
        if pin.get("version") == version and pin.get("commit") == commit:
            return wrapper
    dest.mkdir(parents=True, exist_ok=True)
    script = f"""
set -euo pipefail
apt-get update
apt-get install -y --no-install-recommends git curl ca-certificates xz-utils
curl -fsSL {url} -o /tmp/hermes-install.sh
bash /tmp/hermes-install.sh --branch {version} --commit {commit} --force-commit \\
  --dir /opt/oh-bin/hermes-agent --hermes-home /tmp/hermes-installer-home \\
  --skip-setup --skip-browser --skip-computer-use --no-skills --non-interactive
test -x /opt/oh-bin/hermes-agent/hermes
export UV_PYTHON_INSTALL_DIR=/opt/oh-bin/python
UV_BIN=/tmp/hermes-installer-home/bin/uv
if [ ! -x "$UV_BIN" ]; then
  UV_BIN=/root/.local/bin/uv
fi
if [ ! -x "$UV_BIN" ]; then
  curl -fsSL https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL=/opt/oh-bin/uv sh
  UV_BIN=/opt/oh-bin/uv/uv
fi
"$UV_BIN" python install 3.11
PY="$("$UV_BIN" python find 3.11)"
test -x "$PY"
case "$PY" in
  /opt/oh-bin/*) ;;
  *) echo "Hermes python must live on the runtime-bin mount: $PY" >&2; exit 1 ;;
esac
cd /opt/oh-bin/hermes-agent
rm -rf venv
export UV_PROJECT_ENVIRONMENT=/opt/oh-bin/hermes-agent/.venv
"$UV_BIN" venv --python "$PY" .venv
"$UV_BIN" sync --python "$PY"
test -x /opt/oh-bin/hermes-agent/.venv/bin/python
RESOLVED="$(readlink -f /opt/oh-bin/hermes-agent/.venv/bin/python)"
case "$RESOLVED" in
  /opt/oh-bin/*) ;;
  *) echo "venv python escaped the mount: $RESOLVED" >&2; exit 1 ;;
esac
".venv/bin/python" -c "import yaml, openai"
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            platform,
            "-v",
            f"{dest.resolve()}:/opt/oh-bin",
            "python:3.11-bookworm",
            "bash",
            "-lc",
            script,
        ],
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"Hermes installer failed with {result.returncode}")
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'ROOT="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"\n'
        'export HERMES_HOME="${HERMES_HOME:-/home/hermes/.hermes}"\n'
        'exec "$ROOT/hermes-agent/.venv/bin/python" "$ROOT/hermes-agent/hermes" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | 0o111)
    pin_path.write_text(
        json.dumps({"version": version, "commit": commit, "installer": url}, indent=2) + "\n",
        encoding="utf-8",
    )
    return wrapper


def main(argv: list[str] | None = None) -> int:
    """Install pinned ACP binaries.

    Parameters
    ----------
    argv
        Optional CLI arguments. Unused.

    Returns
    -------
    int
        Process exit code.

    Example
    -------
    ``python3 scripts/install_openhands_runtime_bin.py``
    """

    del argv
    values = read_versions()
    dest = Path(os.environ.get("OPENHANDS_RUNTIME_BIN", DEFAULT_DEST))
    arch = detect_agent_server_linux_arch(values)
    platform = "linux/arm64" if arch == "arm64" else "linux/amd64"
    install_opencode(dest, values, arch)
    install_hermes(dest, values, platform)
    return 0


if __name__ == "__main__":
    sys.exit(main())

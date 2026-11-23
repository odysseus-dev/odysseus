"""Run the python-tests apt step from ci.yml against a fake apt toolchain.

The step decides between the restored .deb cache and a bounded download.
These tests execute its real script with stub ``sudo``/``apt-get``/
``dpkg-query`` binaries, so the cache-acceptance logic is exercised without
root, network or a runner image.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="the CI step is a bash script",
)

# Real tools the step needs besides the stubs below. PATH is restricted to
# these so a host that really has ffmpeg or bwrap cannot leak into the run.
_REAL_TOOLS = ("bash", "cat", "chmod", "chown", "head", "id", "mkdir", "rm", "touch")

_SUDO = """#!/bin/bash
# Swallow writes to /etc (apt config, mirror list); run everything else.
if [ "$1" = tee ]; then cat > /dev/null; exit 0; fi
exec "$@"
"""

_TIMEOUT = """#!/bin/bash
shift
exec "$@"
"""

# Packages install by dropping a binary (or a marker) into the fake system.
_APT_GET = """#!/bin/bash
echo "apt-get $*" >> "$FAKE/apt.log"
archives=""
debs=()
pkgs=()
download=false
while [ $# -gt 0 ]; do
  case "$1" in
    -o) archives="${2#Dir::Cache::archives=}"; shift ;;
    --download-only) download=true ;;
    update) exit 0 ;;
    install|-y|--no-download|--no-install-recommends) ;;
    *.deb) debs+=("$1") ;;
    *) pkgs+=("$1") ;;
  esac
  shift
done
if [ -e "$FAKE/fail-local-install" ] && [ ${#debs[@]} -gt 0 ]; then
  echo "E: Unable to correct problems, you have held broken packages." >&2
  exit 100
fi
if $download; then
  for p in $(cat "$FAKE/mirror"); do touch "$archives/${p}_1.0_amd64.deb"; done
  exit 0
fi
for p in "${pkgs[@]}"; do
  [ -e "$archives/${p}_1.0_amd64.deb" ] && debs+=("$archives/${p}_1.0_amd64.deb")
done
for d in "${debs[@]}"; do
  name="${d##*/}"
  case "${name%%_*}" in
    ffmpeg) printf '#!/bin/bash\\necho "ffmpeg version fake"\\n' > "$FAKE/bin/ffmpeg" ;;
    bubblewrap) printf '#!/bin/bash\\necho "bubblewrap fake"\\n' > "$FAKE/bin/bwrap" ;;
    ca-certificates) touch "$FAKE/ca-installed" ;;
  esac
done
chmod +x "$FAKE"/bin/* 2>/dev/null
exit 0
"""

_DPKG_QUERY = """#!/bin/bash
[ -e "$FAKE/ca-installed" ] && printf installed
exit 0
"""

# check_ca_trust.py is covered elsewhere; here it only has to succeed.
_PYTHON = """#!/bin/bash
exit 0
"""


def _step_script() -> str:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    for step in workflow["jobs"]["python-tests"]["steps"]:
        if step.get("id") == "apt-install":
            return step["run"]
    raise AssertionError("apt-install step not found in ci.yml")


def _run_step(tmp_path, *, cache_hit, cached, mirror, local_install_fails=False):
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    bin_dir.mkdir(parents=True)
    for tool in _REAL_TOOLS:
        real = shutil.which(tool)
        assert real, f"{tool} is required to run the CI step"
        (bin_dir / tool).symlink_to(real)
    for name, body in {
        "sudo": _SUDO,
        "timeout": _TIMEOUT,
        "apt-get": _APT_GET,
        "dpkg-query": _DPKG_QUERY,
        "python": _PYTHON,
    }.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    # The runner image already has ca-certificates; ffmpeg and bwrap are not.
    (fake / "ca-installed").touch()
    (fake / "mirror").write_text(" ".join(mirror))
    if local_install_fails:
        (fake / "fail-local-install").touch()

    home = tmp_path / "home"
    debs = home / ".cache" / "ci-apt-debs"
    debs.mkdir(parents=True)
    for pkg in cached:
        (debs / f"{pkg}_1.0_amd64.deb").touch()

    script = tmp_path / "step.sh"
    script.write_text(_step_script())
    output = tmp_path / "github_output"
    output.touch()
    env = {
        "PATH": str(bin_dir),
        "HOME": str(home),
        "FAKE": str(fake),
        "CACHE_HIT": "true" if cache_hit else "",
        "APT_PACKAGES": "ffmpeg bubblewrap ca-certificates",
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [shutil.which("bash"), "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    apt_log = fake / "apt.log"
    calls = apt_log.read_text().splitlines() if apt_log.exists() else []
    return result, calls, output.read_text()


def _downloaded(calls):
    return any("--download-only" in call for call in calls)


def test_complete_cache_installs_offline_without_saving(tmp_path):
    result, calls, outputs = _run_step(
        tmp_path,
        cache_hit=True,
        cached=["ffmpeg", "bubblewrap"],
        mirror=["ffmpeg", "bubblewrap"],
    )

    assert result.returncode == 0, result.stderr
    assert not any(call.startswith("apt-get update") for call in calls)
    assert not _downloaded(calls)
    assert "save=true" not in outputs


def test_cache_missing_bubblewrap_falls_back_to_download(tmp_path):
    # apt installs the partial set without complaint: only the result shows
    # that bwrap is missing.
    result, calls, outputs = _run_step(
        tmp_path,
        cache_hit=True,
        cached=["ffmpeg"],
        mirror=["ffmpeg", "bubblewrap"],
    )

    assert result.returncode == 0, result.stderr
    assert "is incomplete; downloading instead" in result.stdout
    assert _downloaded(calls)
    assert "save=true" in outputs
    assert (tmp_path / "fake" / "bin" / "bwrap").exists()


def test_cache_apt_rejects_falls_back_to_download(tmp_path):
    # A missing shared library: apt refuses the whole local set up front.
    result, calls, outputs = _run_step(
        tmp_path,
        cache_hit=True,
        cached=["ffmpeg", "bubblewrap"],
        mirror=["ffmpeg", "bubblewrap"],
        local_install_fails=True,
    )

    assert result.returncode == 0, result.stderr
    assert "downloading instead" in result.stdout
    assert _downloaded(calls)
    assert "save=true" in outputs


def test_incomplete_download_fails_without_saving(tmp_path):
    result, calls, outputs = _run_step(
        tmp_path,
        cache_hit=False,
        cached=[],
        mirror=["ffmpeg"],
    )

    assert result.returncode != 0
    assert "still missing after the apt install" in result.stdout
    assert _downloaded(calls)
    assert "save=true" not in outputs

"""Deployment-only checks for the Docker sandbox runtime contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.gpu-nvidia.yml",
    ROOT / "docker-compose.gpu-amd.yml",
)
EXPECTED_CAPS = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}


def _compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", COMPOSE_FILES)
def test_every_shipped_compose_variant_requires_the_same_outer_boundary(path):
    document = _compose(path)
    service = document["services"]["odysseus"]

    assert service["security_opt"] == [
        "no-new-privileges:true",
        "seccomp=./docker/seccomp/odysseus-bubblewrap.json",
        "apparmor=odysseus-sandbox",
    ]
    assert service["cap_drop"] == ["ALL"]
    assert set(service["cap_add"]) == EXPECTED_CAPS
    assert "SYS_ADMIN" not in service["cap_add"]
    assert "privileged" not in service
    assert service.get("network_mode") != "host"
    assert service.get("pid") != "host"


def test_non_app_services_do_not_receive_the_odysseus_boundary():
    for path in COMPOSE_FILES:
        document = _compose(path)
        for name, service in document["services"].items():
            if name == "odysseus":
                continue
            assert "security_opt" not in service, (path, name)


def test_dockerfile_installs_pinned_helpers_and_runs_the_boot_check():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for required in (
        "bubblewrap",
        "libseccomp2",
        "libseccomp-dev",
        "BWRAP_POLICY_VERSION=0.11.0",
        "BWRAP_POLICY_PACKAGE=0.11.0-2+deb13u1",
        "make -C security/seccomp install",
        "make -C security/egress install",
        "COPY docker/sandbox-self-test.sh /usr/local/bin/odysseus-sandbox-self-test",
        "chown root:root /usr/local/bin/entrypoint.sh /usr/local/bin/odysseus-sandbox-self-test",
    ):
        assert required in dockerfile

    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert '"$GOSU_BIN" "$ODY_USER" /usr/local/bin/odysseus-sandbox-self-test' in entrypoint


def test_apparmor_profile_is_loaded_by_name_and_has_no_unconfined_mode():
    profile = (ROOT / "docker" / "apparmor" / "odysseus-sandbox").read_text(
        encoding="utf-8"
    )

    assert "profile odysseus-sandbox" in profile
    for rule in ("userns create", "mount,", "umount,", "pivot_root,"):
        assert rule in profile
    assert "profile odysseus-sandbox-unconfined" not in profile
    assert "capability sys_admin" in profile
    assert "namespaced bootstrap" in profile


def test_boot_self_test_is_fail_closed_and_checks_proc_namespace_contract():
    path = ROOT / "docker" / "sandbox-self-test.sh"
    source = path.read_text(encoding="utf-8")
    syntax = subprocess.run(
        ["sh", "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert path.stat().st_mode & 0o111
    for required in (
        "exit 1",
        "--unshare-pid",
        "--unshare-net",
        "--proc /proc",
        "--cap-drop ALL",
        "mountinfo",
        "socket.if_nameindex",
        "odysseus-egress-broker",
        "127.0.0.1",
    ):
        assert required in source
    assert "--share-net" not in source
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "exit 78" in entrypoint


def test_generated_outer_profile_is_checked_when_foundation_files_are_present():
    generator = ROOT / "security" / "seccomp" / "generate.py"
    profile = ROOT / "docker" / "seccomp" / "odysseus-bubblewrap.json"
    if not generator.is_file() or not profile.is_file():
        pytest.skip("seccomp foundation is supplied by the prerequisite slice")

    completed = subprocess.run(
        ["python3", str(generator), "--check", "--verify-arches"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    document = json.loads(profile.read_text(encoding="utf-8"))
    assert document["defaultAction"] == "SCMP_ACT_ERRNO"
    assert document["defaultErrnoRet"] == 1
    assert document["syscalls"][-2:] == [
        {
            "names": ["clone"],
            "action": "SCMP_ACT_ALLOW",
            "args": [
                {"index": 0, "value": 2114060305, "op": "SCMP_CMP_EQ"}
            ],
            "comment": "Odysseus trusted Bubblewrap namespace bootstrap only",
        },
        {
            "names": ["mount", "pivot_root", "umount2"],
            "action": "SCMP_ACT_ALLOW",
            "comment": "Odysseus trusted Bubblewrap mount bootstrap; inner filter denies payload use",
        },
    ]


def test_compose_text_has_no_privileged_or_unconfined_fallback():
    compose = "\n".join(path.read_text(encoding="utf-8") for path in COMPOSE_FILES)
    assert "privileged:" not in compose
    assert "SYS_ADMIN" not in compose
    assert "seccomp=unconfined" not in compose
    assert "apparmor=unconfined" not in compose
    assert "network_mode: host" not in compose
    assert "pid: host" not in compose


def test_process_core_and_boot_check_share_the_fresh_proc_contract():
    core = ROOT / "src" / "execution_sandbox.py"
    if not core.is_file():
        pytest.skip("process core is supplied by the prerequisite slice")
    source = core.read_text(encoding="utf-8")
    boot = (ROOT / "docker" / "sandbox-self-test.sh").read_text(encoding="utf-8")

    assert '"--unshare-pid"' in source
    assert '"--proc"' in source
    assert '"/proc"' in source
    assert '_MOUNTINFO_PATH = "/proc/self/mountinfo"' in source
    assert "--unshare-pid" in boot
    assert "--proc /proc" in boot
    assert "--share-net" not in source
    assert "--share-net" not in boot

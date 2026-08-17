"""Determinism and deployment invariants for the two seccomp layers."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
SECCOMP_DIR = ROOT / "security" / "seccomp"
POLICY = json.loads((SECCOMP_DIR / "policy.json").read_text(encoding="utf-8"))
MOBY = json.loads((SECCOMP_DIR / "moby-default.json").read_text(encoding="utf-8"))
OUTER = json.loads(
    (ROOT / "docker" / "seccomp" / "odysseus-bubblewrap.json").read_text(
        encoding="utf-8"
    )
)


def test_generated_policy_matches_pinned_source_for_both_architectures():
    completed = subprocess.run(
        ["python", "generate.py", "--check", "--verify-arches"],
        cwd=SECCOMP_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert POLICY["moby"] == {
        "repository": "https://github.com/moby/moby",
        "commit": "35797366d7cdae8d1d84eac06fbb314ccaf3ccaf",
        "path": "vendor/github.com/moby/profiles/seccomp/default.json",
        "upstream_sha256": "536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74",
    }
    assert POLICY["target_arches"] == {"x86_64": "amd64", "aarch64": "arm64"}


def test_outer_profile_is_exact_moby_profile_plus_bwrap_bootstrap_rules():
    expected = copy.deepcopy(MOBY)
    expected["syscalls"].extend(OUTER["syscalls"][-2:])

    assert OUTER == expected
    clone_rule, mount_rule = OUTER["syscalls"][-2:]
    assert clone_rule == {
        "names": ["clone"],
        "action": "SCMP_ACT_ALLOW",
        "args": [
            {
                "index": 0,
                "value": 2114060305,
                "op": "SCMP_CMP_EQ",
            }
        ],
        "comment": "Odysseus trusted Bubblewrap namespace bootstrap only",
    }
    assert mount_rule["names"] == ["mount", "pivot_root", "umount2"]
    assert mount_rule["action"] == "SCMP_ACT_ALLOW"


def test_inner_policy_records_required_default_deny_and_conditional_rules():
    assert POLICY["default_errno"] == "EPERM"
    assert POLICY["clone3_errno"] == "ENOSYS"
    assert POLICY["tiocsti_errno"] == "EACCES"
    assert POLICY["clone_namespace_mask"] == 2114060288
    assert POLICY["socket_families"] == ["AF_UNIX", "AF_INET", "AF_INET6"]
    for denied in (
        "bpf",
        "perf_event_open",
        "mount",
        "umount2",
        "pivot_root",
        "unshare",
        "setns",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "keyctl",
        "open_by_handle_at",
        "userfaultfd",
        "io_uring_setup",
    ):
        assert denied in POLICY["denied_syscalls"]


def test_tiocsti_allow_rule_rejects_truncation_bypass_values():
    launcher = (SECCOMP_DIR / "odysseus-seccomp-launcher.c").read_text(
        encoding="utf-8"
    )

    assert "SCMP_CMP_MASKED_EQ" in launcher
    assert ".datum_a = UINT32_MAX" in launcher
    assert ".datum_b = TIOCSTI" in launcher
    assert "SCMP_ACT_ERRNO(EACCES)" in launcher


@pytest.mark.parametrize(
    "compose_path",
    [
        "docker-compose.yml",
        "docker-compose.gpu-nvidia.yml",
        "docker-compose.gpu-amd.yml",
    ],
)
def test_compose_applies_outer_profile_only_to_odysseus(compose_path):
    compose = yaml.safe_load((ROOT / compose_path).read_text(encoding="utf-8"))
    expected = ["seccomp=./docker/seccomp/odysseus-bubblewrap.json"]

    assert compose["services"]["odysseus"]["security_opt"] == expected
    for name, service in compose["services"].items():
        if name != "odysseus":
            assert "security_opt" not in service


def test_deployment_never_uses_privileged_sys_admin_or_unconfined_seccomp():
    compose_text = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "docker-compose.yml",
            "docker-compose.gpu-nvidia.yml",
            "docker-compose.gpu-amd.yml",
        )
    )
    sandbox_source = (ROOT / "src" / "execution_sandbox.py").read_text(
        encoding="utf-8"
    )

    assert "privileged:" not in compose_text
    assert "SYS_ADMIN" not in compose_text
    assert "seccomp=unconfined" not in compose_text
    assert '"--share-net"' not in sandbox_source


def test_docker_image_installs_root_owned_launcher_and_libseccomp():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "libseccomp2" in dockerfile
    assert "make -C security/seccomp install" in dockerfile
    assert "/usr/local/libexec" in (SECCOMP_DIR / "Makefile").read_text(
        encoding="utf-8"
    )
    launcher = (SECCOMP_DIR / "odysseus-seccomp-launcher.c").read_text(
        encoding="utf-8"
    )
    assert "S_ISUID | S_ISGID" in launcher


def test_docker_image_pins_the_traced_bubblewrap_version():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    version = re.search(
        r"^\s*&& BWRAP_POLICY_VERSION=(.+) \\$",
        dockerfile,
        re.MULTILINE,
    )

    assert version is not None
    expected_version = POLICY["outer_bubblewrap"][
        "bubblewrap_version_basis"
    ].removeprefix("v")
    assert version.group(1) == expected_version
    package = re.search(
        r"^\s*&& BWRAP_POLICY_PACKAGE=(.+) \\$",
        dockerfile,
        re.MULTILINE,
    )
    assert package is not None
    assert package.group(1) == POLICY["outer_bubblewrap"]["shipped_package_basis"]
    assert "ARG BUBBLEWRAP_VERSION" not in dockerfile
    assert 'BWRAP_ACTUAL="$(bwrap --version)"' in dockerfile
    assert '"bubblewrap ${BWRAP_POLICY_VERSION}"' in dockerfile
    assert "dpkg-query -W -f='${Version}' bubblewrap" in dockerfile


def test_bubblewrap_provenance_records_the_release_commit_not_only_the_tag():
    assert POLICY["outer_bubblewrap"] == {
        "clone_flags": 2114060305,
        "bootstrap_syscalls": ["mount", "pivot_root", "umount2"],
        "bubblewrap_version_basis": "v0.11.0",
        "bubblewrap_tag_object": "a871b148b7bc0571f50b917cd5fd03b427f54ed1",
        "bubblewrap_commit": "9ca3b05ec787acfb4b17bed37db5719fa777834f",
        "bubblewrap_release_sha256": (
            "988fd6b232dafa04b8b8198723efeaccdb3c6aa9c1c7936219d5791a8b7a8646"
        ),
        "shipped_package_basis": "0.11.0-2+deb13u1",
    }

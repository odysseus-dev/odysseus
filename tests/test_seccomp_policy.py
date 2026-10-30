"""Determinism and policy invariants for the two seccomp layers."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

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
    generated = (SECCOMP_DIR / "generated_inner_policy.h").read_text(
        encoding="utf-8"
    )

    assert "SCMP_CMP_MASKED_EQ" in launcher
    assert ".datum_a = UINT32_MAX" in launcher
    assert ".datum_b = TIOCSTI" in launcher
    assert "SCMP_ACT_ERRNO(ODYSSEUS_TIOCSTI_ERRNO)" in launcher
    assert "#define ODYSSEUS_TIOCSTI_ERRNO EACCES" in generated


def test_generated_header_owns_every_conditional_policy_value():
    launcher = (SECCOMP_DIR / "odysseus-seccomp-launcher.c").read_text(
        encoding="utf-8"
    )
    generated = (SECCOMP_DIR / "generated_inner_policy.h").read_text(
        encoding="utf-8"
    )

    for expected in (
        "#define ODYSSEUS_DEFAULT_ERRNO EPERM",
        "#define ODYSSEUS_CLONE3_ERRNO ENOSYS",
        "#define ODYSSEUS_TIOCSTI_ERRNO EACCES",
        "#define ODYSSEUS_CLONE_NAMESPACE_MASK 2114060288ULL",
        "static const uint64_t ODYSSEUS_SOCKET_FAMILIES[] = {",
        "static const uint64_t ODYSSEUS_SOCKETPAIR_FAMILIES[] = {",
        "static const uint64_t ODYSSEUS_PERSONALITY_VALUES[] = {",
    ):
        assert expected in generated
    for generated_name in (
        "ODYSSEUS_DEFAULT_ERRNO",
        "ODYSSEUS_CLONE3_ERRNO",
        "ODYSSEUS_TIOCSTI_ERRNO",
        "ODYSSEUS_SOCKET_FAMILIES",
        "ODYSSEUS_SOCKETPAIR_FAMILIES",
        "ODYSSEUS_PERSONALITY_VALUES",
    ):
        assert generated_name in launcher


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

"""Structural + posture checks for the tenant seccomp profile.

Phase 0 of the tenant tool-runtime sandbox roadmap
(docs/sandbox-hardening-roadmap.md). This validates the *shape* and *security
posture* of docker/sandbox/seccomp-tenant.json — it does NOT (and cannot, on a
non-Linux box) prove that a real workload runs under the profile. Live behaviour
is a documented cloud-host step.

Deliberately free of app imports so it needs no DB/app fixtures and runs fast.
"""

import json
from pathlib import Path

import pytest

PROFILE_PATH = (
    Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "seccomp-tenant.json"
)

# Syscalls a one-shot `python -I -c` / `sh -c` tool-runtime must be able to make.
# If any of these regress out of the allow-list the runtime won't start.
REQUIRED_SYSCALLS = {
    "read", "write", "openat", "close", "lseek", "stat", "fstat", "newfstatat",
    "mmap", "mprotect", "munmap", "brk", "rt_sigaction", "rt_sigprocmask",
    "clone", "clone3", "execve", "exit_group", "wait4", "futex",
    "set_robust_list", "set_tid_address", "getrandom", "epoll_create1",
    "epoll_wait", "eventfd2", "pipe2", "dup2", "fcntl", "getdents64",
    "kill", "nanosleep", "clock_gettime",
}

# Privileged / exploit-primitive syscalls that MUST stay denied (absent from the
# allow-list, since defaultAction is ERRNO). Mirrors the spec's "keep denied" set.
FORBIDDEN_SYSCALLS = {
    "mount", "umount", "umount2", "pivot_root", "chroot",
    "ptrace", "process_vm_readv", "process_vm_writev", "kcmp",
    "bpf", "perf_event_open",
    "kexec_load", "kexec_file_load", "reboot",
    "swapon", "swapoff",
    "init_module", "finit_module", "delete_module", "create_module",
    "query_module", "get_kernel_syms",
    "settimeofday", "clock_settime", "clock_adjtime", "adjtimex",
    "sethostname", "setdomainname",
    "acct", "quotactl", "nfsservctl", "lookup_dcookie",
    "open_by_handle_at", "iopl", "ioperm", "vm86", "vm86old",
    "userfaultfd", "modify_ldt",
    "io_uring_setup", "io_uring_enter", "io_uring_register",
    "fsopen", "fsconfig", "move_mount", "open_tree",
}


@pytest.fixture(scope="module")
def profile():
    with PROFILE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def allowed_syscalls(profile):
    names: set[str] = set()
    for rule in profile.get("syscalls", []):
        if rule.get("action") == "SCMP_ACT_ALLOW":
            names.update(rule.get("names", []))
    return names


def test_profile_file_exists():
    assert PROFILE_PATH.is_file(), f"missing seccomp profile at {PROFILE_PATH}"


def test_default_action_is_errno(profile):
    # Deny-by-default: anything not explicitly allowed returns an error.
    assert profile.get("defaultAction") == "SCMP_ACT_ERRNO"


def test_arch_map_covers_x86_64_and_aarch64(profile):
    arch_map = profile.get("archMap")
    assert isinstance(arch_map, list) and arch_map, "archMap must be a non-empty list"
    arches = {entry.get("architecture") for entry in arch_map}
    assert "SCMP_ARCH_X86_64" in arches
    assert "SCMP_ARCH_AARCH64" in arches


def test_has_an_allow_rule(allowed_syscalls):
    assert allowed_syscalls, "profile must allow at least one syscall"


def test_required_syscalls_present(allowed_syscalls):
    missing = sorted(REQUIRED_SYSCALLS - allowed_syscalls)
    assert not missing, f"required syscalls missing from allow-list: {missing}"


def test_forbidden_syscalls_absent(allowed_syscalls):
    leaked = sorted(FORBIDDEN_SYSCALLS & allowed_syscalls)
    assert not leaked, f"dangerous syscalls must not be allowed: {leaked}"


def test_no_unconfined_or_allow_default(profile):
    # Guard against someone flipping the profile open.
    assert profile.get("defaultAction") not in {"SCMP_ACT_ALLOW", "SCMP_ACT_LOG"}

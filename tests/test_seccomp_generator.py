"""Unit tests for seccomp policy architecture verification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "security" / "seccomp" / "generate.py"
NEWER_ALLOWLIST_SYSCALLS = {
    "getxattrat",
    "listmount",
    "listxattrat",
    "mseal",
    "removexattrat",
    "riscv_hwprobe",
    "setxattrat",
    "statmount",
    "uretprobe",
}


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seccomp_generator", GENERATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeLibseccomp:
    def __init__(self, unsupported: set[str]) -> None:
        self.unsupported = unsupported

    def seccomp_arch_resolve_name(self, name: bytes) -> int:
        return 1 if name in {b"x86_64", b"aarch64"} else 0

    def seccomp_syscall_resolve_name_arch(self, token: int, name: bytes) -> int:
        assert token == 1
        return -1 if name.decode("ascii") in self.unsupported else 1


def _policy(generator: ModuleType) -> dict:
    return generator._load_json(generator.POLICY)


def test_arch_verification_allows_newer_pinned_allowlist_names(monkeypatch):
    generator = _load_generator()
    policy = _policy(generator)
    source = generator._load_json(generator.SOURCE)
    for moby_arch in policy["target_arches"].values():
        assert NEWER_ALLOWLIST_SYSCALLS <= set(
            generator._allowlist_for_arch(source, policy, moby_arch)
        )
    monkeypatch.setattr(
        generator,
        "_load_libseccomp",
        lambda: _FakeLibseccomp(NEWER_ALLOWLIST_SYSCALLS),
    )

    generator._verify_arches(policy)


def test_arch_verification_rejects_missing_required_syscalls(monkeypatch):
    generator = _load_generator()
    policy = _policy(generator)
    monkeypatch.setattr(
        generator,
        "_load_libseccomp",
        lambda: _FakeLibseccomp({"clone3", "socketpair"}),
    )

    with pytest.raises(
        RuntimeError,
        match=r"libseccomp cannot resolve x86_64 syscalls: clone3, socketpair",
    ):
        generator._verify_arches(policy)


def test_conditional_values_deterministically_change_the_generated_header():
    generator = _load_generator()
    policy = _policy(generator)
    source = generator._load_json(generator.SOURCE)
    policy.update(
        {
            "default_errno": "EACCES",
            "clone3_errno": "EPERM",
            "tiocsti_errno": "ENOSYS",
            "socket_families": ["AF_UNIX"],
            "socketpair_families": ["AF_INET"],
            "personality_values": [7, 9],
        }
    )

    generated = generator._render_header(source, policy)

    assert "#define ODYSSEUS_DEFAULT_ERRNO EACCES" in generated
    assert "#define ODYSSEUS_CLONE3_ERRNO EPERM" in generated
    assert "#define ODYSSEUS_TIOCSTI_ERRNO ENOSYS" in generated
    assert "static const uint64_t ODYSSEUS_SOCKET_FAMILIES[] = {\n    AF_UNIX," in generated
    assert "static const uint64_t ODYSSEUS_SOCKETPAIR_FAMILIES[] = {\n    AF_INET," in generated
    assert "static const uint64_t ODYSSEUS_PERSONALITY_VALUES[] = {\n    7,\n    9," in generated

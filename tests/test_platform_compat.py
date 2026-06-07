"""Regression tests for cross-platform helper behavior."""

import importlib.util
import io
import sys
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "platform_compat.py"
_SPEC = importlib.util.spec_from_file_location("platform_compat_under_test", _MODULE_PATH)
platform_compat = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(platform_compat)


def _reset_bash_cache(monkeypatch):
    monkeypatch.setattr(platform_compat, "_BASH_CACHE", None)
    monkeypatch.setattr(platform_compat, "_BASH_PROBED", False)


def test_find_bash_tries_windows_exe_suffix(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)

    expected = r"C:\Program Files\Git\bin\bash.exe"

    def fake_which(name):
        return expected if name == "bash.exe" else None

    monkeypatch.setattr(platform_compat.shutil, "which", fake_which)
    monkeypatch.setattr(platform_compat.os.path, "exists", lambda _path: False)

    assert platform_compat.find_bash() == expected


def test_find_bash_checks_local_app_data_git_install(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    monkeypatch.setattr(platform_compat.shutil, "which", lambda _name: None)
    for env_name in platform_compat._WINDOWS_BASH_ROOT_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("LocalAppData", r"C:\Users\alice\AppData\Local")

    expected = r"C:\Users\alice\AppData\Local\Git\bin\bash.exe"
    monkeypatch.setattr(platform_compat.os.path, "exists", lambda path: path == expected)

    assert platform_compat.find_bash() == expected


def test_find_bash_skips_windows_wsl_stub(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)

    stub = r"C:\WINDOWS\system32\bash.exe"
    expected = r"C:\Program Files\Git\bin\bash.exe"
    monkeypatch.setattr(
        platform_compat.shutil,
        "which",
        lambda name: stub if name == "bash" else None,
    )
    monkeypatch.setattr(platform_compat.os.path, "exists", lambda path: path == expected)

    assert platform_compat.find_bash() == expected


def test_is_wsl_true_when_proc_version_mentions_microsoft(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux", raising=False)

    def fake_open(path, mode="r", *args, **kwargs):
        assert path == "/proc/version"
        assert mode == "r"
        return io.StringIO("Linux version 6.6.0 microsoft standard")

    monkeypatch.setattr("builtins.open", fake_open)

    assert platform_compat.is_wsl() is True


def test_is_wsl_false_when_proc_version_is_not_microsoft(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux", raising=False)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: io.StringIO("Linux version 6.6.0 generic"))

    assert platform_compat.is_wsl() is False


def test_is_wsl_false_on_non_posix_without_proc_probe(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setattr(platform_compat.os, "name", "nt", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("open should not be called when platform is not Linux/POSIX")

    monkeypatch.setattr("builtins.open", fail_open)

    assert platform_compat.is_wsl() is False


def test_translate_path_converts_windows_drive_path_on_wsl(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: True)

    out = platform_compat.translate_path(r"C:\Users\alice\models\qwen.gguf")

    assert out == "/mnt/c/Users/alice/models/qwen.gguf"


def test_translate_path_resolves_paths_when_not_wsl(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: False)

    assert platform_compat.translate_path(".") == str(Path(".").resolve())


def test_translate_path_returns_input_when_resolve_fails(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: False)

    class _BrokenPath:
        def __init__(self, _value):
            pass

        def resolve(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(platform_compat, "Path", _BrokenPath)

    assert platform_compat.translate_path("weird::path") == "weird::path"


def test_get_wsl_windows_user_profile_prefers_powershell(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: True)

    class _Result:
        returncode = 0
        stdout = "C:\\Users\\alice\\n"

    monkeypatch.setattr(platform_compat.subprocess, "run", lambda *_a, **_k: _Result())
    monkeypatch.setattr(platform_compat, "translate_path", lambda _v: "/mnt/c/Users/alice")

    assert platform_compat.get_wsl_windows_user_profile() == "/mnt/c/Users/alice"


def test_get_wsl_windows_user_profile_falls_back_to_users_dir(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: True)

    def raise_run(*_a, **_k):
        raise OSError("powershell unavailable")

    monkeypatch.setattr(platform_compat.subprocess, "run", raise_run)
    monkeypatch.setattr(
        platform_compat.os,
        "listdir",
        lambda _path: ["All Users", "Default", "Public", "alice"],
    )

    def fake_isdir(path):
        return path in {"/mnt/c/Users", "/mnt/c/Users/alice"}

    monkeypatch.setattr(platform_compat.os.path, "isdir", fake_isdir)

    assert platform_compat.get_wsl_windows_user_profile() == "/mnt/c/Users/alice"


def test_get_wsl_windows_user_profile_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.setattr(platform_compat, "is_wsl", lambda: True)
    monkeypatch.setattr(
        platform_compat.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("powershell unavailable")),
    )
    monkeypatch.setattr(platform_compat.os.path, "isdir", lambda _path: False)

    assert platform_compat.get_wsl_windows_user_profile() is None

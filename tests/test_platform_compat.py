"""Regression tests for cross-platform helper behavior."""

from core import platform_compat


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

"""Regression tests for cross-platform helper behavior."""

from core import platform_compat


def _reset_bash_cache(monkeypatch):
    monkeypatch.setattr(platform_compat, "_BASH_CACHE", None)
    monkeypatch.setattr(platform_compat, "_BASH_FLAVOUR", None)
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
    assert platform_compat.bash_flavour() == "git"


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
    assert platform_compat.bash_flavour() == "git"


def test_find_bash_prefers_git_bash_over_wsl(monkeypatch):
    """When shutil.which returns WSL bash but Git Bash is installed, prefer Git Bash."""
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)

    wsl_bash = r"C:\Windows\system32\bash.exe"
    git_bash = r"C:\Program Files\Git\bin\bash.exe"

    monkeypatch.setattr(platform_compat.shutil, "which", lambda name: wsl_bash if name == "bash" else None)
    monkeypatch.setattr(platform_compat.os.path, "exists", lambda path: path == git_bash)

    assert platform_compat.find_bash() == git_bash
    assert platform_compat.bash_flavour() == "git"


def test_find_bash_falls_back_to_wsl_when_no_git_bash(monkeypatch):
    """When only WSL bash is available (no Git Bash), use it as a fallback."""
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)

    wsl_bash = r"C:\Windows\system32\bash.exe"

    monkeypatch.setattr(platform_compat.shutil, "which", lambda name: wsl_bash if name == "bash" else None)
    monkeypatch.setattr(platform_compat.os.path, "exists", lambda _path: False)

    assert platform_compat.find_bash() == wsl_bash
    assert platform_compat.bash_flavour() == "wsl"


def test_win_to_bash_path_git_bash(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "_BASH_FLAVOUR", "git")
    monkeypatch.setattr(platform_compat, "_BASH_PROBED", True)

    assert platform_compat.win_to_bash_path(r"C:\Users\alice\venv\Scripts") == "/c/Users/alice/venv/Scripts"
    assert platform_compat.win_to_bash_path("D:\\models\\llama") == "/d/models/llama"


def test_win_to_bash_path_wsl(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "_BASH_FLAVOUR", "wsl")
    monkeypatch.setattr(platform_compat, "_BASH_PROBED", True)

    assert platform_compat.win_to_bash_path(r"C:\Users\alice\venv\Scripts") == "/mnt/c/Users/alice/venv/Scripts"
    assert platform_compat.win_to_bash_path("D:\\models\\llama") == "/mnt/d/models/llama"


def test_win_to_bash_path_posix_passthrough(monkeypatch):
    _reset_bash_cache(monkeypatch)
    monkeypatch.setattr(platform_compat, "_BASH_FLAVOUR", "posix")
    monkeypatch.setattr(platform_compat, "_BASH_PROBED", True)

    assert platform_compat.win_to_bash_path("/usr/local/bin") == "/usr/local/bin"
    assert platform_compat.win_to_bash_path("relative/path") == "relative/path"

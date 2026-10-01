"""Tests for ``_atomic_write`` in ``src.agent_tools.filesystem_tools``.

``_atomic_write`` writes to a temporary file in the same directory, fsyncs,
preserves existing permissions, then renames over the target.  On NFS this
avoids the case where a plain write-then-close reports success before data
reaches stable storage.
"""
import importlib.util
import os
import stat
from pathlib import Path

import pytest

# Load filesystem_tools directly so we avoid pulling in the full src package
# graph (database modules, MCP servers, etc.).
ROOT = Path(__file__).resolve().parents[1]
FS_TOOLS_PATH = ROOT / "src" / "agent_tools" / "filesystem_tools.py"
_spec = importlib.util.spec_from_file_location("_fs_tools_under_test", FS_TOOLS_PATH)
fs_tools = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs_tools)

_atomic_write = fs_tools._atomic_write


def _tmp_siblings(directory: Path) -> list:
    """Return any ``.odysseus-tmp`` files left behind."""
    return list(directory.glob("*.odysseus-tmp"))


# ---------------------------------------------------------------------------
# Happy path — basic write.
# ---------------------------------------------------------------------------
def test_write_creates_file_with_content(tmp_path):
    target = tmp_path / "hello.txt"
    _atomic_write(str(target), "hello world")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello world"
    assert target.stat().st_size == len("hello world")


def test_write_no_temp_file_residue(tmp_path):
    target = tmp_path / "clean.txt"
    _atomic_write(str(target), "data")

    assert len(_tmp_siblings(tmp_path)) == 0


def test_write_empty_content(tmp_path):
    target = tmp_path / "empty.txt"
    _atomic_write(str(target), "")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == ""
    assert target.stat().st_size == 0


# ---------------------------------------------------------------------------
# Replacement — overwrite existing file.
# ---------------------------------------------------------------------------
def test_write_replaces_existing_content(tmp_path):
    target = tmp_path / "replace.txt"
    _atomic_write(str(target), "first")
    _atomic_write(str(target), "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert target.stat().st_size == len("second")


def test_write_replacement_no_temp_residue(tmp_path):
    target = tmp_path / "replace_clean.txt"
    _atomic_write(str(target), "first")
    _atomic_write(str(target), "second")

    assert len(_tmp_siblings(tmp_path)) == 0


# ---------------------------------------------------------------------------
# Permission preservation.
# ---------------------------------------------------------------------------
def test_preserves_permissions_on_new_file(tmp_path):
    target = tmp_path / "perms.txt"
    target.write_text("old", encoding="utf-8")
    os.chmod(str(target), 0o644)

    _atomic_write(str(target), "new")

    if os.name != "nt":  # Unix permission bits not meaningful on Windows
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o644, f"expected 0o644, got {oct(mode)}"
    else:
        assert target.read_text(encoding="utf-8") == "new"


def test_preserves_permissions_across_rewrites(tmp_path):
    target = tmp_path / "perms_rw.txt"
    target.write_text("v1", encoding="utf-8")
    os.chmod(str(target), 0o640)

    _atomic_write(str(target), "v2")
    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o640, f"after first write: expected 0o640, got {oct(mode)}"

    _atomic_write(str(target), "v3")
    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o640, f"after second write: expected 0o640, got {oct(mode)}"


def test_new_file_gets_default_permissions(tmp_path):
    target = tmp_path / "new_perms.txt"
    assert not target.exists()

    _atomic_write(str(target), "content")

    # Should match 0o666 & ~umask, not mkstemp's 0o600.
    import threading
    _lock = threading.Lock()
    with _lock:
        saved = os.umask(0)
        os.umask(saved)
    expected = 0o666 & ~saved

    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == expected, f"expected {oct(expected)}, got {oct(mode)}"
    else:
        assert target.exists()
        assert os.access(str(target), os.R_OK)


# ---------------------------------------------------------------------------
# Failure cleanup — temp file removed on error.
# ---------------------------------------------------------------------------
def test_cleanup_on_write_failure(tmp_path):
    """If the content is not a string, os.fdopen should raise TypeError and
    the temp file should be cleaned up."""
    target = tmp_path / "fail.txt"

    with pytest.raises(TypeError):
        _atomic_write(str(target), 12345)

    assert not target.exists()
    assert len(_tmp_siblings(tmp_path)) == 0


def test_cleanup_on_replace_failure(tmp_path, monkeypatch):
    """If os.replace fails, the temp file should be cleaned up."""
    target = tmp_path / "replace_fail.txt"

    def bad_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", bad_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        _atomic_write(str(target), "data")

    assert not target.exists()
    assert len(_tmp_siblings(tmp_path)) == 0


# ---------------------------------------------------------------------------
# Unicode / encoding.
# ---------------------------------------------------------------------------
def test_unicode_content(tmp_path):
    target = tmp_path / "unicode.txt"
    content = "héllo wörld — 你好世界 🎉"

    _atomic_write(str(target), content)

    assert target.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# Large content.
# ---------------------------------------------------------------------------
def test_large_content(tmp_path):
    target = tmp_path / "large.bin"
    content = "x" * (1024 * 1024)  # 1 MB

    _atomic_write(str(target), content)

    assert target.read_text(encoding="utf-8") == content
    assert target.stat().st_size == 1024 * 1024


# ---------------------------------------------------------------------------
# Parent directory creation not needed — mkstemp uses existing dir.
# ---------------------------------------------------------------------------
def test_target_in_existing_directory(tmp_path):
    target = tmp_path / "sub" / "file.txt"
    tmp_path.joinpath("sub").mkdir()

    _atomic_write(str(target), "nested")

    assert target.read_text(encoding="utf-8") == "nested"


# ---------------------------------------------------------------------------
# fd leak check — no file descriptors left open after write.
# ---------------------------------------------------------------------------
def test_no_fd_leak(tmp_path):
    target = tmp_path / "fd_check.txt"
    before = len(os.listdir("/proc/self/fd")) if os.path.exists("/proc/self/fd") else 0

    _atomic_write(str(target), "data")

    # On Linux, verify no fd leak.  On other OSes this is a no-op.
    if os.path.exists("/proc/self/fd"):
        after = len(os.listdir("/proc/self/fd"))
        assert after <= before + 1, f"fd leak: {before} -> {after}"

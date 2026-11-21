"""Tests for ``core.atomic_io`` durability and crash-safety behavior.

``core.atomic_io`` provides ``atomic_write_json`` and ``atomic_write_text``.
Both write to a sibling ``.tmp.<random>`` file, ``fsync`` it, then
``os.replace`` into place so a crash mid-write leaves the previous good copy
untouched rather than a truncated/empty file.

These tests cover the happy path (round-trip, indent, parent-dir creation,
full overwrite, no leftover tmp), the two failure paths the implementation
guarantees (the target file is preserved when serialization fails before the
replace, and when ``os.replace`` itself fails), and that two concurrent
writers to the same path don't collide on the same temp file.
"""
import importlib.util
import json
import os
import threading
from pathlib import Path

import pytest

# Load core/atomic_io.py directly by file path so this stays a pure unit test:
# importing the ``core`` package would pull in core/__init__.py and the
# database/session modules, making the test depend on data/app.db existing.
ROOT = Path(__file__).resolve().parents[1]
ATOMIC_IO_PATH = ROOT / "core" / "atomic_io.py"
_spec = importlib.util.spec_from_file_location("_atomic_io_under_test", ATOMIC_IO_PATH)
atomic_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atomic_io)

atomic_write_json = atomic_io.atomic_write_json
atomic_write_text = atomic_io.atomic_write_text


def _tmp_siblings(directory: Path, name: str) -> list:
    """Return any ``<name>.tmp.*`` files the helpers may have left behind."""
    return list(directory.glob(f"{name}.tmp.*"))


# ---------------------------------------------------------------------------
# atomic_write_json — happy path.
# ---------------------------------------------------------------------------
def test_atomic_write_json_round_trips_object(tmp_path):
    target = tmp_path / "data.json"
    original = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}, "s": "héllo"}

    atomic_write_json(str(target), original)

    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_atomic_write_json_honors_indent(tmp_path):
    target = tmp_path / "indented.json"

    atomic_write_json(str(target), {"a": 1}, indent=2)

    text = target.read_text(encoding="utf-8")
    assert "\n" in text
    assert text == json.dumps({"a": 1}, indent=2)


def test_atomic_write_json_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "data.json"

    atomic_write_json(str(target), {"ok": True})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_atomic_write_json_fully_overwrites_longer_content(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(str(target), {"k": "x" * 500})

    atomic_write_json(str(target), {"k": "short"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"k": "short"}
    # No trailing bytes from the previous, longer write.
    assert target.read_text(encoding="utf-8") == json.dumps({"k": "short"})


def test_atomic_write_json_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "data.json"

    atomic_write_json(str(target), {"a": 1})

    assert _tmp_siblings(tmp_path, "data.json") == []


def test_atomic_write_json_concurrent_writers_do_not_collide(tmp_path):
    # Both writers run in this same process, so a PID-based tmp suffix is
    # identical for both: whichever writer finishes first unlinks the tmp
    # file (via os.replace) out from under the other, which then raises
    # FileNotFoundError on its own os.replace instead of landing its write.
    target = tmp_path / "settings.json"
    orig_dump = json.dump
    barrier = threading.Barrier(2)
    errors = []

    def slow_dump(obj, fp, **kwargs):
        orig_dump(obj, fp, **kwargs)
        fp.flush()
        barrier.wait()

    def write(payload):
        try:
            atomic_write_json(str(target), payload)
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    json.dump = slow_dump
    try:
        t1 = threading.Thread(target=write, args=({"writer": "A"},))
        t2 = threading.Thread(target=write, args=({"writer": "B"},))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    finally:
        json.dump = orig_dump

    assert errors == []
    assert json.loads(target.read_text(encoding="utf-8"))["writer"] in ("A", "B")


# ---------------------------------------------------------------------------
# atomic_write_json — failure paths
# ---------------------------------------------------------------------------
def test_atomic_write_json_preserves_target_when_serialization_fails(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(str(target), {"existing": "value"})
    before = target.read_text(encoding="utf-8")

    # A set is not JSON-serializable, so json.dump raises after the tmp file
    # is opened but before os.replace runs.
    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": {1, 2, 3}})

    assert target.read_text(encoding="utf-8") == before
    # Temp file should be cleaned up
    assert _tmp_siblings(tmp_path, "data.json") == []


def test_atomic_write_json_preserves_target_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "data.json"
    atomic_write_json(str(target), {"existing": "value"})
    before = target.read_text(encoding="utf-8")

    def boom(src, dst):
        raise PermissionError("replace failed")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(PermissionError, match="replace failed"):
        atomic_write_json(str(target), {"new": "content"})

    assert target.read_text(encoding="utf-8") == before
    # Temp file should be cleaned up
    assert _tmp_siblings(tmp_path, "data.json") == []


# ---------------------------------------------------------------------------
# file_mode — restrictive temp permissions before content is written.
#
# Review of #6263: under umask 022 a plain temp file is born 0644, so a
# secret written into it is readable by other local accounts until a later
# chmod runs. file_mode=0o600 must make the temp file (and, via os.replace,
# the published file) 0o600 from the very first byte.
# ---------------------------------------------------------------------------
def test_file_mode_restricts_temp_before_any_content_is_written(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not observable on Windows")

    target = tmp_path / "vault.json"
    seen = {}
    real_dump = json.dump

    def spying_dump(obj, fp, **kwargs):
        seen["mode_at_dump"] = os.fstat(fp.fileno()).st_mode & 0o777
        real_dump(obj, fp, **kwargs)

    monkeypatch.setattr(json, "dump", spying_dump)
    old_umask = os.umask(0o022)
    try:
        atomic_write_json(str(target), {"session": "s3cret"}, file_mode=0o600)
    finally:
        os.umask(old_umask)

    assert seen["mode_at_dump"] == 0o600
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_file_mode_temp_is_restrictive_at_replace_time(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not observable on Windows")

    target = tmp_path / "vault.json"
    seen = {}
    real_replace = os.replace

    def spying_replace(src, dst):
        seen["tmp_mode"] = os.stat(src).st_mode & 0o777
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.os, "replace", spying_replace)
    old_umask = os.umask(0o022)
    try:
        atomic_write_json(str(target), {"session": "s3cret"}, file_mode=0o600)
    finally:
        os.umask(old_umask)

    assert seen["tmp_mode"] == 0o600
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_default_permissions_without_file_mode_are_unchanged(tmp_path):
    # All existing callers omit file_mode and must keep 0o666 & ~umask.
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not observable on Windows")

    target = tmp_path / "data.json"
    old_umask = os.umask(0o022)
    try:
        atomic_write_json(str(target), {"a": 1})
    finally:
        os.umask(old_umask)

    assert os.stat(target).st_mode & 0o777 == 0o644


def test_file_mode_restricts_temp_when_replacing_existing_vault(tmp_path, monkeypatch):
    """Reviewer's exact scenario: an existing 0600 vault.json holding a live
    secret is replaced under umask 022. The temp file must stay 0600 at both
    moments the secret could leak (serialization and rename), and the
    published file must remain 0600 with valid new content."""
    if os.name != "posix":
        pytest.skip("POSIX permission bits are not observable on Windows")

    target = tmp_path / "vault.json"
    atomic_write_json(str(target), {"session": "old-secret"}, file_mode=0o600)
    assert os.stat(target).st_mode & 0o777 == 0o600

    seen = {}
    real_dump = json.dump
    real_replace = os.replace

    def spying_dump(obj, fp, **kwargs):
        seen["mode_at_dump"] = os.fstat(fp.fileno()).st_mode & 0o777
        real_dump(obj, fp, **kwargs)

    def spying_replace(src, dst):
        seen["tmp_mode"] = os.stat(src).st_mode & 0o777
        real_replace(src, dst)

    monkeypatch.setattr(json, "dump", spying_dump)
    monkeypatch.setattr(atomic_io.os, "replace", spying_replace)
    old_umask = os.umask(0o022)
    try:
        atomic_write_json(str(target), {"session": "new-secret"}, file_mode=0o600)
    finally:
        os.umask(old_umask)

    assert seen["mode_at_dump"] == 0o600
    assert seen["tmp_mode"] == 0o600
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == {"session": "new-secret"}


def test_fchmod_failure_cleans_tmp_and_preserves_original_exception(tmp_path, monkeypatch):
    """If the permission-setting step itself fails, _open_tmp closes the
    descriptor, the helper's finally block removes the temp file, and the
    original exception is what surfaces — nothing is masked, nothing leaks."""
    if os.name != "posix":
        pytest.skip("requires POSIX fchmod")

    target = tmp_path / "vault.json"
    atomic_write_json(str(target), {"existing": "value"}, file_mode=0o600)
    before = target.read_text(encoding="utf-8")

    def exploding_fchmod(fd, mode):
        raise OSError("fchmod failed")

    monkeypatch.setattr(atomic_io.os, "fchmod", exploding_fchmod)

    with pytest.raises(OSError, match="fchmod failed"):
        atomic_write_json(str(target), {"session": "new"}, file_mode=0o600)

    assert target.read_text(encoding="utf-8") == before
    assert _tmp_siblings(tmp_path, "vault.json") == []


def test_file_mode_round_trips_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "vault.json"

    atomic_write_json(str(target), {"session": "abc"}, file_mode=0o600)

    assert json.loads(target.read_text(encoding="utf-8")) == {"session": "abc"}
    assert _tmp_siblings(tmp_path, "vault.json") == []


def test_file_mode_preserves_target_when_serialization_fails(tmp_path):
    target = tmp_path / "vault.json"
    atomic_write_json(str(target), {"existing": "value"}, file_mode=0o600)
    before = target.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": {1, 2, 3}}, file_mode=0o600)

    assert target.read_text(encoding="utf-8") == before
    assert _tmp_siblings(tmp_path, "vault.json") == []


# ---------------------------------------------------------------------------
# atomic_write_text — happy path.
# ---------------------------------------------------------------------------
def test_atomic_write_text_round_trips(tmp_path):
    target = tmp_path / "note.txt"
    text = "line one\nline two\nunicode: héllo\n"

    atomic_write_text(str(target), text)

    assert target.read_text(encoding="utf-8") == text


def test_atomic_write_text_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "note.txt"

    atomic_write_text(str(target), "content")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_atomic_write_text_fully_overwrites_longer_content(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "x" * 500)

    atomic_write_text(str(target), "short")

    assert target.read_text(encoding="utf-8") == "short"


def test_atomic_write_text_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "note.txt"

    atomic_write_text(str(target), "content")

    assert _tmp_siblings(tmp_path, "note.txt") == []


def test_atomic_write_text_rejects_non_string_before_tmp_file(tmp_path):
    target = tmp_path / "note.txt"

    with pytest.raises(TypeError):
        atomic_write_text(str(target), 123)

    assert not target.exists()
    assert _tmp_siblings(tmp_path, "note.txt") == []


# ---------------------------------------------------------------------------
# atomic_write_text — failure paths
# ---------------------------------------------------------------------------
def test_atomic_write_text_preserves_target_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "original content")
    before = target.read_text(encoding="utf-8")

    def boom(src, dst):
        raise PermissionError("replace failed")

    monkeypatch.setattr(atomic_io.os, "replace", boom)

    with pytest.raises(PermissionError, match="replace failed"):
        atomic_write_text(str(target), "new content that never lands")

    assert target.read_text(encoding="utf-8") == before
    # Temp file should be cleaned up
    assert _tmp_siblings(tmp_path, "note.txt") == []


def test_cleanup_error_swallows_and_preserves_original_exception(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "original content")

    def replace_boom(src, dst):
        raise PermissionError("replace failed")

    def unlink_boom(path):
        raise OSError("unlink failed")

    monkeypatch.setattr(atomic_io.os, "replace", replace_boom)
    monkeypatch.setattr(atomic_io.os, "unlink", unlink_boom)

    # If BOTH the replace fails AND the cleanup unlink fails,
    # the original replace error should surface, completely swallowing the unlink error.
    with pytest.raises(PermissionError, match="replace failed"):
        atomic_write_text(str(target), "new content")
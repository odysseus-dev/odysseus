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


def _umask() -> int:
    """Read the process umask without changing it."""
    old = os.umask(0)
    os.umask(old)
    return old


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
# atomic_write_json — mode= keyword (secret-file permissions).
# ---------------------------------------------------------------------------
def test_atomic_write_json_applies_mode_atomically(tmp_path):
    target = tmp_path / "secrets.json"
    target.write_text('{"old": true}', encoding="utf-8")
    target.chmod(0o644)  # pre-existing world-readable target

    atomic_write_json(str(target), {"token": "s3cr3t"}, mode=0o600)

    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_text(encoding="utf-8")) == {"token": "s3cr3t"}
    # The restricted temp file was renamed in, not left behind.
    assert _tmp_siblings(tmp_path, "secrets.json") == []


def test_atomic_write_json_without_mode_uses_default_permissions(tmp_path):
    target = tmp_path / "data.json"

    atomic_write_json(str(target), {"a": 1})

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert _tmp_siblings(tmp_path, "data.json") == []
    # No explicit chmod on this path: the target keeps the usual create-time
    # permissions (0o666 minus the process umask), never a stricter mode.
    assert target.stat().st_mode & 0o777 == 0o666 & ~_umask()


def test_atomic_write_json_mode_set_on_temp_before_content_and_rename(tmp_path, monkeypatch):
    # The whole point of mode= is that the restricted permissions are in
    # place BEFORE any content lands and BEFORE the rename: a secret file
    # must never exist, even momentarily, at default permissions. Spy on
    # json.dump and os.replace (delegating to the real calls) and stat the
    # temp file at each moment.
    target = tmp_path / "secrets.json"
    requested = 0o600
    moments = {}

    real_dump = json.dump
    real_replace = os.replace

    def spy_dump(obj, fp, **kwargs):
        st = os.fstat(fp.fileno())
        moments["at_dump_mode"] = st.st_mode & 0o777
        moments["at_dump_size"] = st.st_size  # 0 = no content written yet
        real_dump(obj, fp, **kwargs)

    def spy_replace(src, dst):
        moments["at_replace_mode"] = os.stat(src).st_mode & 0o777
        real_replace(src, dst)

    monkeypatch.setattr(atomic_io.json, "dump", spy_dump)
    monkeypatch.setattr(atomic_io.os, "replace", spy_replace)

    # Run under a umask whose default file mode differs from 0o600 so the
    # assertions below cannot pass just because the create-time default
    # already matches the requested mode.
    saved_umask = os.umask(0o022)
    try:
        assert _umask() == 0o022
        assert (0o666 & ~_umask()) != requested
        atomic_write_json(str(target), {"token": "s3cr3t"}, mode=requested)
    finally:
        os.umask(saved_umask)

    # The spies must actually have run.
    assert moments, "spies never ran"
    assert moments["at_dump_mode"] == requested, "temp file lacked the mode when json.dump started"
    assert moments["at_dump_size"] == 0, "temp file already had content when json.dump started"
    assert moments["at_replace_mode"] == requested, "temp file lacked the mode when os.replace ran"
    # And still after the fact, of course.
    assert target.stat().st_mode & 0o777 == requested
    assert _tmp_siblings(tmp_path, "secrets.json") == []


def test_atomic_write_json_fallback_chmod_when_fchmod_missing(tmp_path, monkeypatch):
    # Windows has no os.fchmod; the code must fall back to os.chmod on the
    # temp file. Make fchmod raise AttributeError at the call site and
    # prove the fallback both runs and applies the mode.
    target = tmp_path / "fallback.json"
    calls = []
    real_chmod = os.chmod

    def spy_chmod(path, mode):
        calls.append((str(path), mode))
        real_chmod(path, mode)

    def _missing_fchmod(*args, **kwargs):
        # Not a deletion but an AttributeError at the call site: same
        # except branch, and the real stdlib fchmod stays intact for
        # anything pytest itself does while this test runs.
        raise AttributeError("os.fchmod is not available on this platform")

    monkeypatch.setattr(atomic_io.os, "fchmod", _missing_fchmod)
    monkeypatch.setattr(atomic_io.os, "chmod", spy_chmod)

    saved_umask = os.umask(0o022)
    try:
        assert (0o666 & ~_umask()) != 0o600  # default creation mode differs
        atomic_write_json(str(target), {"token": "s3cr3t"}, mode=0o600)
    finally:
        os.umask(saved_umask)

    assert len(calls) == 1
    chmod_path, chmod_mode = calls[0]
    assert chmod_mode == 0o600
    assert chmod_path != str(target) and chmod_path.startswith(str(target)), (
        "fallback chmod must target the temp sibling, not the final path"
    )
    assert target.stat().st_mode & 0o777 == 0o600
    assert _tmp_siblings(tmp_path, "fallback.json") == []


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
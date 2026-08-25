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
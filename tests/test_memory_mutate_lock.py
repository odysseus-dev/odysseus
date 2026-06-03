"""Concurrency + integrity tests for MemoryManager.mutate() and the unified class.

mutate() wraps load -> modify -> save in a cross-process + in-process lock so two
read-modify-write cycles can't drop one writer's change (the lost-update bug).
These tests exercise it under real thread contention and across two manager
instances pointed at the same file, plus the supporting invariants: the ``uses``
backfill on load, the single unified class behind every import path, and the
pid-tagged atomic save (no torn JSON, no leftover tmp files).
"""
import json
import threading

import pytest


def _data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "memory.json").write_text("[]", encoding="utf-8")
    return str(d)


def test_mutate_serializes_concurrent_increment_uses(tmp_path):
    """N threads each increment_uses([id]) for one id -> uses == N (no lost bumps)."""
    from src.memory import MemoryManager

    data_dir = _data_dir(tmp_path)
    mgr = MemoryManager(data_dir)
    mgr.mutate(lambda e: (e + [{"id": "m1", "text": "x", "uses": 0}], None))

    N = 24
    barrier = threading.Barrier(N)

    def worker():
        barrier.wait()  # maximize contention
        MemoryManager(data_dir).increment_uses(["m1"])

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = MemoryManager(data_dir).load_all()
    by_id = {e["id"]: e for e in entries}
    assert by_id["m1"]["uses"] == N


def test_mutate_atomic_under_two_instances(tmp_path):
    """Two MemoryManager instances, interleaved mutate() appends -> all survive."""
    from src.memory import MemoryManager

    data_dir = _data_dir(tmp_path)
    a = MemoryManager(data_dir)
    b = MemoryManager(data_dir)

    PER = 40
    barrier = threading.Barrier(2)

    def append_many(mgr, prefix):
        barrier.wait()
        for i in range(PER):
            mid = f"{prefix}-{i}"
            mgr.mutate(lambda e, mid=mid: (e + [{"id": mid, "text": mid}], None))

    t1 = threading.Thread(target=append_many, args=(a, "a"))
    t2 = threading.Thread(target=append_many, args=(b, "b"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    ids = {e["id"] for e in MemoryManager(data_dir).load_all()}
    expected = {f"a-{i}" for i in range(PER)} | {f"b-{i}" for i in range(PER)}
    assert ids == expected


def test_uses_field_backfilled_on_load(tmp_path):
    """A memory.json lacking 'uses' is backfilled to uses==0 on load_all()."""
    from src.memory import MemoryManager

    data_dir = _data_dir(tmp_path)
    raw = [
        {"id": "old-1", "text": "no uses field here"},
        {"id": "old-2", "text": "also missing", "category": "fact"},
    ]
    (tmp_path / "data" / "memory.json").write_text(json.dumps(raw), encoding="utf-8")

    entries = MemoryManager(data_dir).load_all()
    assert entries  # not dropped
    for e in entries:
        assert e["uses"] == 0


def test_both_import_paths_are_same_class():
    """The unification: every import path resolves to ONE MemoryManager class."""
    from src.memory import MemoryManager as A
    from services.memory import MemoryManager as B
    from services.memory.memory import MemoryManager as C

    assert A is B
    assert B is C


def test_save_pid_tmp_no_corruption(tmp_path):
    """Many parallel mutate() appends -> memory.json is always valid JSON, never
    truncated, and no leftover '<file>.tmp.*' files remain."""
    import glob
    import os

    from src.memory import MemoryManager

    data_dir = _data_dir(tmp_path)
    memory_file = os.path.join(data_dir, "memory.json")

    N = 16
    PER = 12
    barrier = threading.Barrier(N)
    errors = []

    def worker(w):
        try:
            barrier.wait()
            mgr = MemoryManager(data_dir)
            for i in range(PER):
                mid = f"w{w}-{i}"
                mgr.mutate(lambda e, mid=mid: (e + [{"id": mid, "text": mid}], None))
                # Read concurrently while others write — must never see torn JSON.
                with open(memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                assert isinstance(data, list)
        except Exception as exc:  # capture per-thread failures for the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent read/write errors: {errors[:3]}"

    # Final file is valid and complete.
    with open(memory_file, "r", encoding="utf-8") as f:
        final = json.load(f)
    ids = {e["id"] for e in final}
    expected = {f"w{w}-{i}" for w in range(N) for i in range(PER)}
    assert ids == expected

    # No leftover pid-tagged temp files.
    leftovers = glob.glob(memory_file + ".tmp.*")
    assert leftovers == [], f"leftover temp files: {leftovers}"

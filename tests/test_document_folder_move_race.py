"""Regression: concurrent opposing folder moves must not create a cycle.

Two reparents of the same owner racing against the same initial state — A->B and
B->A — must NOT both commit; that would leave A and B pointing at each other,
an orphaned subtree with no root (skeptic poc4.py: CYCLE True, 0 roots). The
per-owner move lock serializes read-validate-commit so the second move reads the
first's COMMITTED state and its anti-cycle check fires: at most one move
succeeds, the other is 400, and the graph stays acyclic. Deterministic — the
invariant holds regardless of which move wins the lock.
"""
import threading

import pytest
from fastapi import HTTPException

from core.database import DocumentFolder
from tests.helpers.doc_folders_harness import DocFoldersHarness


@pytest.fixture
def h(monkeypatch):
    return DocFoldersHarness(monkeypatch)


def test_concurrent_opposing_moves_stay_acyclic(h):
    a = h.create_folder("alice", "A")["id"]
    b = h.create_folder("alice", "B")["id"]

    barrier = threading.Barrier(2)
    results = {}

    def worker(name, fid, parent):
        # Both threads are live before either takes the move lock; they then race
        # for it. Whichever loses reads the winner's committed graph and 400s.
        barrier.wait(timeout=10)
        try:
            h.move_folder("alice", fid, parent)
            results[name] = "ok"
        except HTTPException as e:
            results[name] = e.status_code
        except Exception as e:  # surface an unexpected failure instead of hiding it
            results[name] = f"{type(e).__name__}: {e}"

    t1 = threading.Thread(target=worker, args=("A->B", a, b))
    t2 = threading.Thread(target=worker, args=("B->A", b, a))
    t1.start(); t2.start()
    t1.join(timeout=15); t2.join(timeout=15)

    # Exactly one move committed; the other was rejected with 400.
    assert sorted(results.values(), key=str) == [400, "ok"], results

    # The graph is acyclic: A and B don't point at each other, and a root remains.
    fa = h.folder_parent_id(a)
    fb = h.folder_parent_id(b)
    assert not (fa == b and fb == a), f"cycle A<->B: A.parent={fa} B.parent={fb}"
    db = h.SessionLocal()
    try:
        roots = db.query(DocumentFolder).filter(DocumentFolder.parent_id.is_(None)).count()
    finally:
        db.close()
    assert roots >= 1, "no root folder remains -> subtree orphaned by a cycle"


def test_sequential_anti_cycle_unchanged(h):
    # Sanity: the ordinary (non-concurrent) anti-cycle path still 400s and does
    # not regress into a cycle.
    a = h.create_folder("alice", "A")["id"]
    b = h.create_folder("alice", "B", parent_id=a)["id"]
    with pytest.raises(HTTPException) as ei:
        h.move_folder("alice", a, b)   # A under its own child
    assert ei.value.status_code == 400
    assert h.folder_parent_id(a) is None
    assert h.folder_parent_id(b) == a

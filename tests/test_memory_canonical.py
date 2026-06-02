"""Issue #49 — the MemoryManager has a single source of truth (no drift).

`services/memory/memory.py` used to be a second, drifting copy. It is now a thin
re-export of `src/memory.py`. These tests pin that, and verify the canonical
class carries the union of both copies' behaviour (the `uses` tracking that lived
only in src, plus `claim_ownerless` that lived only in services).
"""

from src.memory import MemoryManager as SrcMM
from services.memory.memory import MemoryManager as ModuleMM
from services.memory import MemoryManager as PackageMM


def test_single_source_of_truth():
    # Every import path must resolve to the exact same class object.
    assert ModuleMM is SrcMM
    assert PackageMM is SrcMM


def test_canonical_class_has_union_of_methods():
    # increment_uses lived only in src; claim_ownerless lived only in services.
    # The canonical class must have both, or the drift bug recurs.
    assert hasattr(SrcMM, "increment_uses")
    assert hasattr(SrcMM, "claim_ownerless")


def test_uses_tracking_round_trips(tmp_path):
    # add_entry builds the entry; the caller persists via save() (the real API).
    mm = SrcMM(data_dir=str(tmp_path))
    entry = mm.add_entry("the sky is blue", source="user", category="fact")
    assert entry.get("uses") == 0
    mm.save([entry])
    mm.increment_uses([entry["id"]])
    after = next(e for e in mm.load_all() if e["id"] == entry["id"])
    assert after["uses"] == 1


def test_claim_ownerless_assigns_owner(tmp_path):
    mm = SrcMM(data_dir=str(tmp_path))
    entry = mm.add_entry("ownerless note", source="user", category="fact")  # owner omitted
    mm.save([entry])
    mm.claim_ownerless("alice")
    entries = mm.load_all()
    assert entries, "expected at least one entry"
    assert all(e.get("owner") == "alice" for e in entries)

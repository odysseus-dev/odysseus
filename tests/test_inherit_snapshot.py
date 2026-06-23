# tests/test_inherit_snapshot.py
import json
import os

from core.atomic_io import atomic_write_json
from services.project.snapshot import run_inherit_snapshot


def _seed_main_brain(monkeypatch, tmp_path):
    """Pre-populate the global memory.json + tidy state, point DATA_DIR there."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    entries = [
        {"id": "m1", "text": "User prefers dark mode", "timestamp": 1, "source": "user", "category": "fact"},
        {"id": "m2", "text": "Lives in Portland", "timestamp": 2, "source": "user", "category": "fact"},
    ]
    atomic_write_json(os.path.join(str(tmp_path), "memory.json"), entries)
    atomic_write_json(os.path.join(str(tmp_path), "memory_tidy_state.json"), {"hashes": ["m1", "m2"]})


def test_inherit_snapshot_copies_and_writes_tidy_state(monkeypatch, tmp_path):
    _seed_main_brain(monkeypatch, tmp_path)
    proj_dir = tmp_path / "proj1"

    snap = run_inherit_snapshot(str(proj_dir))

    copied = json.load(open(os.path.join(str(proj_dir), "memory.json")))
    assert {e["id"] for e in copied} == {"m1", "m2"}
    assert os.path.exists(os.path.join(str(proj_dir), "memory_tidy_state.json"))
    assert snap.source_count == 2


def test_inherit_snapshot_rolls_back_on_failure(monkeypatch, tmp_path):
    _seed_main_brain(monkeypatch, tmp_path)
    proj_dir = tmp_path / "proj2"
    # Force the rebuild step to fail by monkeypatching MemoryVectorStore.rebuild.
    from services.project import snapshot
    def boom(_self, _entries):
        raise RuntimeError("simulated vector failure")
    monkeypatch.setattr(snapshot.MemoryVectorStore, "rebuild", boom)

    try:
        run_inherit_snapshot(str(proj_dir))
    except RuntimeError:
        pass
    # Directory should not exist (rolled back).
    assert not proj_dir.exists()

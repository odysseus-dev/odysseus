# tests/test_memory_atomic_save.py
"""MemoryManager.save must use core.atomic_io (PID-suffixed tmp), not memory.json.tmp."""
import json
from pathlib import Path

from src.memory import MemoryManager


def test_memory_save_uses_pid_suffixed_tmp_not_bare_tmp(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manager = MemoryManager(str(data_dir))
    entries = [{"id": "a1", "text": "hello", "timestamp": 1, "source": "user", "category": "fact"}]

    seen = {}

    def fake_atomic_write_json(path, data, *, indent=None):
        seen["path"] = path
        seen["indent"] = indent
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr("src.memory.atomic_write_json", fake_atomic_write_json)

    manager.save(entries)

    assert seen["path"] == str(data_dir / "memory.json")
    assert seen["indent"] == 2
    assert not list(data_dir.glob("memory.json.tmp"))
    assert not list(data_dir.glob("memory.json.tmp.*"))


def test_memory_save_round_trips(tmp_path):
    manager = MemoryManager(str(tmp_path))
    entries = [{"id": "x", "text": "persist me", "timestamp": 1, "source": "user", "category": "fact"}]
    manager.save(entries)
    loaded = manager.load_all()
    assert loaded[0]["text"] == "persist me"

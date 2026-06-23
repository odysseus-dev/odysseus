"""Inherit-mode snapshot: copy main-brain memory into the project.

Steps (spec §3a):
  1. Read main `memory.json` via `MemoryManager.load_all()` (atomic).
  2. Rebuild the project's own `MemoryVectorStore` from the snapshot.
  3. Atomic-copy `memory_tidy_state.json` next to the new memory file.

Any failure rolls back: remove the partially-created directory.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from typing import Optional

from core.atomic_io import atomic_write_json
from src.memory import MemoryManager
from src.memory_vector import MemoryVectorStore


@dataclass
class SnapshotMeta:
    taken_at: int
    count: int
    source_count: int

    def to_json(self) -> str:
        return json.dumps(
            {"taken_at": self.taken_at, "count": self.count, "source_count": self.source_count}
        )

    @classmethod
    def from_json(cls, s: str) -> "SnapshotMeta":
        d = json.loads(s)
        return cls(taken_at=d["taken_at"], count=d["count"], source_count=d["source_count"])


def _read_main_tidy_state(src_dir: str) -> Optional[dict]:
    p = os.path.join(src_dir, "memory_tidy_state.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def run_inherit_snapshot(project_data_dir: str) -> SnapshotMeta:
    """Copy main-brain memory into the project. Roll back on any failure.

    Caller has already created ``project_data_dir`` (Task 8); this function
    writes the project-specific memory files inside it.
    """
    from src.constants import DATA_DIR

    os.makedirs(project_data_dir, exist_ok=True)
    src_dir = DATA_DIR

    # Step 1 — atomic read of main brain memory.
    main = MemoryManager(src_dir)
    entries = main.load_all()
    source_count = len(entries)

    # Step 2 — re-embed into the project's own vector store.
    proj_collection = f"project_memory_{os.path.basename(project_data_dir)}"
    try:
        # Embeds first so a vector-store failure rolls back before we touch
        # any project file.
        mv = MemoryVectorStore(
            data_dir=project_data_dir, collection_name=proj_collection,
        )
        if not mv.healthy:
            raise RuntimeError("vector store unhealthy for inherit snapshot")
        mv.rebuild(entries)

        # Step 3 — copy the JSON + tidy state atomically. If this fails,
        # remove the rebuilt vectors so they don't leak into another project.
        atomic_write_json(os.path.join(project_data_dir, "memory.json"), entries)
        tidy = _read_main_tidy_state(src_dir)
        if tidy is not None:
            atomic_write_json(os.path.join(project_data_dir, "memory_tidy_state.json"), tidy)
    except Exception:
        shutil.rmtree(project_data_dir, ignore_errors=True)
        raise

    return SnapshotMeta(
        taken_at=int(time.time()),
        count=len(entries),
        source_count=source_count,
    )

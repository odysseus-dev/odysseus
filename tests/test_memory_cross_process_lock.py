"""Cross-process stress test for the memory.json write lock.

The lost-update race is not only in-process: the memory MCP server runs as a
SEPARATE OS process that writes the same data/memory.json as the main app. An
in-process threading.Lock cannot serialize those; the cross-process advisory
flock in core/file_lock.py is what does. This test exercises that layer with
real OS processes (subprocess, not threads), so a regression in the flock would
show up as lost writes here.

It spawns N independent python processes that each append `PER` entries through
the locked MemoryManager.mutate(); with correct cross-process locking every one
of the N*PER appends must survive.
"""
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _worker_code(data_dir: str, prefix: str, count: int) -> str:
    return f"""
import sys
sys.path.insert(0, {REPO_ROOT!r})
from src.memory import MemoryManager
m = MemoryManager({data_dir!r})
for j in range({count}):
    t = {prefix!r} + "-" + str(j)
    def fn(entries, t=t):
        entries.append({{"id": t, "text": t}})
        return entries, None
    m.mutate(fn)
"""


def test_cross_process_appends_lose_no_updates(tmp_path):
    data_dir = str(tmp_path)
    # Seed an empty store.
    from src.memory import MemoryManager
    MemoryManager(data_dir).save([])

    n_procs = 6
    per = 40
    procs = [
        subprocess.Popen([sys.executable, "-c", _worker_code(data_dir, f"p{i}", per)])
        for i in range(n_procs)
    ]
    for p in procs:
        assert p.wait(timeout=90) == 0, "worker process failed"

    entries = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    ids = [e["id"] for e in entries]
    # Every append from every process survived (no cross-process lost update),
    # and no entry was written twice (no torn read-modify-write).
    assert len(entries) == n_procs * per, f"expected {n_procs * per}, got {len(entries)}"
    assert len(set(ids)) == n_procs * per, "duplicate/torn ids found"
    # No leftover temp files from the pid-suffixed atomic writes.
    assert not [f for f in os.listdir(data_dir) if ".tmp." in f]

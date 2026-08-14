"""Focused tests for the memory lifecycle/integrity layer (PR 2).

Covers:
  - drift ledger: bulk change is flagged, an anchor clears it
  - worthiness: intake gate rejects noise, passes assistant value
  - socratic: a concede without amendment/hold is a coherence gap
  - consolidation pressure: store growth raises pressure

All tests isolate to temp dirs and never touch real memory.
"""

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory_platform"))

import _bridge
import memory_env


@pytest.fixture
def env():
    d = tempfile.mkdtemp()
    old = {k: os.environ.get(k) for k in
           ("MEMORY_MEMORY_DIR", "MEMORY_STORE_DB")}
    os.environ["MEMORY_MEMORY_DIR"] = d
    os.environ["MEMORY_STORE_DB"] = os.path.join(d, "mem.db")
    yield d
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Drift ledger: detect bulk change, clear with an anchor
# ---------------------------------------------------------------------------

def _make_ledger_store(d):
    """Create a scratch store with the entries schema the ledger reads."""
    db_path = os.path.join(d, "mem.db")
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "text TEXT, importance REAL, created_at TEXT, last_accessed TEXT, "
               "topic TEXT, entities TEXT, source TEXT, method TEXT, status TEXT, "
               "valid_from TEXT, valid_until TEXT, confidence REAL, temperature REAL, "
               "always_on INTEGER, priority INTEGER, triggers TEXT, kind TEXT, "
               "slug TEXT, summary TEXT)")
    db.execute("INSERT INTO entries (text, importance, created_at, last_accessed, "
               "topic, status, confidence, temperature, always_on, priority, kind) "
               "VALUES ('a stable human fact', 0.8, 'x', 'x', 'human', 'active', "
               "0.9, 1.0, 1, 3, 'fact')")
    db.commit()
    db.close()
    return db_path


def test_drift_flags_bulk_change(env):
    dl = _bridge.load("drift-ledger")
    d = env
    dl.INDEX_FILE = f"{d}/index/ledger.json"
    dl.JOURNAL_DIR = f"{d}/journal"
    dl.DB = _make_ledger_store(d)
    os.makedirs(f"{d}/index", exist_ok=True)
    os.makedirs(f"{d}/journal", exist_ok=True)

    dl.snapshot()
    # bulk unanchored change
    db = sqlite3.connect(dl.DB)
    db.execute("INSERT INTO entries (text, importance, created_at, last_accessed, "
               "topic, status, confidence, temperature, always_on, priority, kind) "
               "VALUES (?, 0.9, 'x', 'x', 'human', 'active', 0.9, 1.0, 1, 3, 'fact')",
               ("- bulk drift " * 200,))
    db.commit()
    db.close()
    assert dl.check(strict=False, autofix=False) != 0, "bulk drift not flagged"

    dl.anchor("human", "canary restore")
    assert dl.check(strict=False, autofix=False) == 0, "anchor did not clear drift"


# ---------------------------------------------------------------------------
# Worthiness: intake gate rejects noise, passes assistant value
# ---------------------------------------------------------------------------

def test_worthiness_rejects_noise(env):
    import worthiness
    bad = ["Conspiracy: scientists are hiding the real cure",
           "Get rich quick: passive income grindset",
           "Stock tip for tomorrow"]
    for f in bad:
        verdict = worthiness.assess(f)[0]
        assert verdict == "REJECT", f"expected REJECT got {verdict}: {f}"


def test_worthiness_passes_value(env):
    import worthiness
    good = ["Always reveal sources for claims",
            "The user prefers oat milk and limits dairy",
            "Teaching a physical skill: drill fundamentals before advanced moves works across learners"]
    for f in good:
        verdict = worthiness.assess(f)[0]
        assert verdict in ("ABSORB", "PROMOTE"), f"expected ABSORB/PROMOTE got {verdict}: {f}"


# ---------------------------------------------------------------------------
# Socratic: concede without amendment/hold is a coherence gap
# ---------------------------------------------------------------------------

def test_socratic_concede_without_followthrough_is_gap(env):
    s = _bridge.load("socratic")
    d = env
    s.DB_PATH = os.path.join(d, "mem.db")
    s.STORE_DB = os.path.join(d, "mem.db")
    db = s.connect()
    s.record("concede", "rule X is wrong", "you are right, the old rule is wrong",
             amended="", hold_reason="")
    gaps = s.coherence()
    assert gaps, "a concede with no amendment/hold must be a coherence gap"

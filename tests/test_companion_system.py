"""Unit tests for companion.system — the pure helpers behind the
/api/companion/system/* endpoints and the odysseus-update CLI.

These exercise version comparison, SQLite path resolution, and the online
`.backup` snapshot directly, with no network and no running app, so the update
and DB-extraction logic can't silently regress.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion import system as s


# ── version comparison ────────────────────────────────────────────────────────

def test_parse_version_strips_prefix_and_suffix():
    assert s.parse_version("v1.2.3") == (1, 2, 3)
    assert s.parse_version("1.2.3-rc1") == (1, 2, 3)
    assert s.parse_version("1.2.3+build.5") == (1, 2, 3)
    assert s.parse_version("  V0.9.1  ") == (0, 9, 1)


def test_parse_version_trailing_zeros_normalized():
    # 1.2 and 1.2.0 must compare equal.
    assert s.parse_version("1.2.0") == s.parse_version("1.2")


def test_parse_version_garbage_is_empty():
    assert s.parse_version("garbage") == ()
    assert s.parse_version("") == ()
    assert s.parse_version(None) == ()


def test_update_available_basic():
    assert s.update_available("0.9.1", "0.9.2") is True
    assert s.update_available("0.9.1", "0.10.0") is True
    assert s.update_available("0.9.1", "1.0.0") is True


def test_update_available_not_when_equal_or_older():
    assert s.update_available("0.9.1", "0.9.1") is False
    assert s.update_available("0.9.1", "v0.9.1") is False
    assert s.update_available("1.0.0", "0.9.9") is False


def test_update_available_unparseable_latest_is_conservative():
    # If we can't read `latest`, never claim an update exists.
    assert s.update_available("0.9.1", None) is False
    assert s.update_available("0.9.1", "garbage") is False


# ── SQLite path resolution ────────────────────────────────────────────────────

def test_resolve_sqlite_relative_path_against_base():
    p = s.resolve_sqlite_path("sqlite:///./data/app.db", "/repo")
    assert p == Path("/repo/data/app.db")


def test_resolve_sqlite_absolute_path():
    p = s.resolve_sqlite_path("sqlite:////srv/app.db", "/repo")
    # .resolve() may canonicalize symlinks; compare the tail to stay portable.
    assert p is not None and p.name == "app.db" and p.is_absolute()


def test_resolve_sqlite_rejects_non_sqlite_and_memory():
    assert s.resolve_sqlite_path("postgresql://u:p@h/db", "/repo") is None
    assert s.resolve_sqlite_path("sqlite://", "/repo") is None
    assert s.resolve_sqlite_path("", "/repo") is None


# ── safe snapshot ─────────────────────────────────────────────────────────────

def test_safe_sqlite_snapshot_copies_data():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "app.db"
        conn = sqlite3.connect(str(src))
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t VALUES ('hello')")
        conn.commit()
        conn.close()

        dst = Path(d) / "snap.sqlite"
        s.safe_sqlite_snapshot(src, dst)

        assert dst.is_file()
        out = sqlite3.connect(str(dst))
        rows = out.execute("SELECT x FROM t").fetchall()
        out.close()
        assert rows == [("hello",)]

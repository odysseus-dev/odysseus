"""Tests for memory platform degraded mode (sqlite-vec unavailable).

Verifies that the store works correctly when sqlite-vec is not installed:
- BM25-only recall returns results
- No crashes on vec0 operations
- Graceful degradation throughout.
"""

import json
import os
import sqlite3
import sys
import tempfile

import pytest

# Ensure memory_platform is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database path."""
    return str(tmp_path / "test_memory.db")


@pytest.fixture
def bm25_db(tmp_path, monkeypatch):
    """Create a store with sqlite-vec unavailable (BM25-only mode)."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("MEMORY_STORE_DB", db_path)

    # Import after setting env var.
    import memory_platform.memory_store as store

    # Patch connect to skip sqlite-vec loading.
    original_connect = store.connect

    def connect_no_vec():
        store._VEC_AVAILABLE = False  # Force BM25-only mode.
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                text, content='', tokenize='porter unicode61'
            );
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                created_at TEXT NOT NULL,
                last_accessed TEXT,
                topic TEXT DEFAULT '',
                entities TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                method TEXT DEFAULT 'curator',
                status TEXT DEFAULT 'active',
                valid_from TEXT,
                valid_until TEXT
            );
            CREATE TABLE IF NOT EXISTS working (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                claim TEXT,
                verdict TEXT,
                evidence TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS associations (
                src_id INTEGER NOT NULL,
                dst_id INTEGER NOT NULL,
                strength REAL DEFAULT 0.1,
                updated_at TEXT,
                PRIMARY KEY (src_id, dst_id)
            );
            CREATE TABLE IF NOT EXISTS scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                summary TEXT DEFAULT '',
                member_ids TEXT DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        # Add schema migrations.
        cols = {r[1] for r in db.execute("PRAGMA table_info(entries)")}
        for col, ddl in (("confidence", "REAL DEFAULT 0.7"),
                         ("temperature", "REAL DEFAULT 1.0"),
                         ("always_on", "INTEGER DEFAULT 0"),
                         ("priority", "INTEGER DEFAULT 5"),
                         ("triggers", "TEXT DEFAULT ''"),
                         ("kind", "TEXT DEFAULT 'fact'"),
                         ("slug", "TEXT DEFAULT ''"),
                         ("summary", "TEXT DEFAULT ''")):
            if col not in cols:
                db.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                wing TEXT DEFAULT '',
                room TEXT DEFAULT '',
                source_path TEXT,
                start_line INTEGER,
                end_line INTEGER,
                page INTEGER,
                text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                ingested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                path TEXT PRIMARY KEY,
                mtime REAL,
                content_hash TEXT,
                status TEXT DEFAULT 'pending',
                error TEXT DEFAULT '',
                wing TEXT DEFAULT '',
                ingested_at TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text, content='', tokenize='porter unicode61'
            );
        """)
        db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                   ("lexicon_version", "2"))
        return db

    store.connect = connect_no_vec
    yield store
    store.connect = original_connect


class TestDegradedMode:
    """Tests for BM25-only degraded mode."""

    def test_add_entry_without_vec(self, bm25_db):
        """Adding an entry works without sqlite-vec."""
        store = bm25_db
        db = store.connect()
        result = store.add_entry(db, "User prefers coffee over tea",
                                 importance=0.7, topic="preference")
        assert result is True
        # Verify entry exists.
        row = db.execute("SELECT text FROM entries WHERE id=1").fetchone()
        assert row["text"] == "User prefers coffee over tea"

    def test_recall_bm25_only(self, bm25_db):
        """BM25-only recall returns relevant results."""
        store = bm25_db
        db = store.connect()
        store.add_entry(db, "User drinks coffee every morning", topic="habit")
        store.add_entry(db, "User prefers oat milk in coffee", topic="preference")
        store.add_entry(db, "The weather is sunny today", topic="observation")

        results = store.recall(db, "coffee morning")
        assert len(results) > 0
        # The coffee entries should rank higher.
        texts = [r["text"] for r in results]
        assert any("coffee" in t.lower() for t in texts)

    def test_recall_empty_store(self, bm25_db):
        """Recall on empty store returns empty list, no crash."""
        store = bm25_db
        db = store.connect()
        results = store.recall(db, "anything")
        assert results == []

    def test_delete_entry_without_vec(self, bm25_db):
        """Deleting an entry works without sqlite-vec."""
        store = bm25_db
        db = store.connect()
        store.add_entry(db, "Temporary fact", topic="test")
        result = store.delete_entry(db, 1)
        assert result is True
        row = db.execute("SELECT * FROM entries WHERE id=1").fetchone()
        assert row is None

    def test_update_entry_without_vec(self, bm25_db):
        """Updating an entry works without sqlite-vec."""
        store = bm25_db
        db = store.connect()
        store.add_entry(db, "Original text", topic="test")
        store.update_entry(db, 1, text="Updated text")
        row = db.execute("SELECT text FROM entries WHERE id=1").fetchone()
        assert row["text"] == "Updated text"

    def test_has_vec_flag(self, bm25_db):
        """The has_vec flag is False in degraded mode."""
        store = bm25_db
        db = store.connect()
        assert store.has_vec() is False

    def test_stats_work_without_vec(self, bm25_db):
        """Stats function works in degraded mode."""
        store = bm25_db
        db = store.connect()
        store.add_entry(db, "Test entry", topic="test")
        stats = store.stats(db)
        assert stats["active_entries"] >= 1

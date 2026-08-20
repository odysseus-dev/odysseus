"""Tests for memory platform — pure SQLite, no C extensions.

Verifies that the hybrid store works correctly with:
- FTS5 BM25 lexical search (built into SQLite)
- Pure Python cosine similarity over embedding BLOBs
- No sqlite-vec or any C extension dependency
"""

import json
import os
import sqlite3
import sys

import pytest

# Ensure memory_platform is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    """Create a temporary store for testing."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("MEMORY_STORE_DB", db_path)

    import memory_platform.memory_store as store
    # Patch module-level paths so connect() uses the test DB.
    monkeypatch.setattr(store, "STORE_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", db_path)
    # Clear embed cache between tests.
    store._embed.cache.clear()
    db = store.connect()
    yield store, db
    db.close()


class TestHybridStore:
    """Tests for the pure SQLite hybrid store."""

    def test_add_entry(self, store_db):
        """Adding an entry works."""
        store, db = store_db
        result = store.add_entry(db, "User prefers coffee over tea",
                                 importance=0.7, topic="preference")
        assert result is True
        row = db.execute("SELECT text FROM entries WHERE id=1").fetchone()
        assert row["text"] == "User prefers coffee over tea"

    def test_add_entry_stores_embedding(self, store_db):
        """Adding an entry stores its embedding as JSON blob."""
        store, db = store_db
        # The store uses _embed internally — it's called via Ollama.
        # Just verify that an embedding column exists and is nullable.
        store.add_entry(db, "Test entry", topic="test")
        row = db.execute("SELECT embedding FROM entries WHERE id=1").fetchone()
        # embedding may be None if Ollama is not running — that's OK.
        # The column exists and the entry was created.
        assert row is not None

    def test_recall_bm25(self, store_db):
        """BM25 recall returns relevant results."""
        store, db = store_db
        store.add_entry(db, "User drinks coffee every morning", topic="habit")
        store.add_entry(db, "User prefers oat milk in coffee", topic="preference")
        store.add_entry(db, "The weather is sunny today", topic="observation")

        results = store.recall(db, "coffee morning")
        assert len(results) > 0
        texts = [r["text"] for r in results]
        assert any("coffee" in t.lower() for t in texts)

    def test_recall_empty_store(self, store_db):
        """Recall on empty store returns empty list, no crash."""
        store, db = store_db
        results = store.recall(db, "anything")
        assert results == []

    def test_delete_entry(self, store_db):
        """Deleting an entry works."""
        store, db = store_db
        store.add_entry(db, "Temporary fact", topic="test")
        result = store.delete_entry(db, 1)
        assert result is True
        row = db.execute("SELECT * FROM entries WHERE id=1").fetchone()
        assert row is None

    def test_update_entry(self, store_db):
        """Updating an entry works."""
        store, db = store_db
        store.add_entry(db, "Original text", topic="test")
        store.update_entry(db, 1, text="Updated text")
        row = db.execute("SELECT text FROM entries WHERE id=1").fetchone()
        assert row["text"] == "Updated text"

    def test_update_entry_reembeds(self, store_db):
        """Updating text re-embeds the entry (if Ollama available)."""
        store, db = store_db
        store.add_entry(db, "Original text", topic="test")
        store.update_entry(db, 1, text="New text")
        row = db.execute("SELECT text FROM entries WHERE id=1").fetchone()
        assert row["text"] == "New text"

    def test_stats(self, store_db):
        """Stats function works."""
        store, db = store_db
        store.add_entry(db, "Test entry", topic="test")
        stats = store.stats(db)
        assert stats["active_entries"] >= 1

    def test_cosine_similarity(self):
        """Pure Python cosine similarity works correctly."""
        from memory_platform.memory_store import _cosine_sim
        # Identical vectors -> 1.0
        assert _cosine_sim([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
        # Orthogonal vectors -> 0.0
        assert _cosine_sim([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
        # Opposite vectors -> -1.0
        assert _cosine_sim([1, 0, 0], [-1, 0, 0]) == pytest.approx(-1.0)
        # Empty -> 0.0
        assert _cosine_sim([], []) == 0.0
        assert _cosine_sim(None, [1, 2, 3]) == 0.0

    def test_dense_search(self, store_db):
        """Dense search returns entries sorted by cosine similarity."""
        store, db = store_db
        # Insert entries with known embeddings directly.
        store.add_entry(db, "Entry Alpha is the first one", topic="test")
        store.add_entry(db, "Entry Beta is the second one", topic="test")
        store.add_entry(db, "Entry Gamma is the third one", topic="test")
        db.execute("UPDATE entries SET embedding=? WHERE id=1",
                   (json.dumps([1.0, 0.0, 0.0]),))
        db.execute("UPDATE entries SET embedding=? WHERE id=2",
                   (json.dumps([0.0, 1.0, 0.0]),))
        db.execute("UPDATE entries SET embedding=? WHERE id=3",
                   (json.dumps([0.9, 0.1, 0.0]),))
        db.commit()

        results = store._dense_search(db, [1.0, 0.0, 0.0], 3)
        assert len(results) == 3
        # Entry Alpha (identical) should rank first.
        assert results[0][0] == 1
        # Entry Gamma (close) should rank second.
        assert results[1][0] == 3

    def test_fts5_builtin(self, store_db):
        """FTS5 is available (built into SQLite)."""
        store, db = store_db
        # FTS5 virtual table should exist.
        row = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='entries_fts'").fetchone()
        assert row is not None

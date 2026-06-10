"""Tests for the mtime-cached O(1) upload index in UploadHandler.

Covers:
1. get_upload_info returns correct record in O(1) (no re-read when mtime unchanged)
2. Cache invalidation when mtime changes (e.g. after save_upload)
3. _find_upload_path returns stored path from cache without os.walk
"""
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch, call

import pytest

from src.upload_handler import UploadHandler


@pytest.fixture
def handler(tmp_path):
    """Create an UploadHandler with a temporary upload directory."""
    base_dir = str(tmp_path)
    upload_dir = str(tmp_path / "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    h = UploadHandler(base_dir, upload_dir)
    return h


# ── Test 1: O(1) lookup — no re-read when mtime is unchanged ──────────────────

def test_get_upload_info_o1_hit(handler, tmp_path):
    """get_upload_info should not call _load_upload_index again when mtime is
    unchanged and the cache is populated."""
    file_id = "abcdef1234567890abcdef1234567890.txt"
    record = {"id": file_id, "name": "test.txt", "path": "/fake/path"}
    index = {"owner:hash": record}

    # Seed the cache directly
    handler._index_cache = {file_id: record}
    handler._index_mtime = 999.0

    uploads_db = os.path.join(handler.upload_dir, "uploads.json")

    with patch.object(handler, "_load_upload_index", return_value=index) as mock_load, \
         patch("os.path.getmtime", return_value=999.0):
        result1 = handler.get_upload_info(file_id)
        result2 = handler.get_upload_info(file_id)

    # _load_upload_index must NOT have been called (cache hit both times)
    mock_load.assert_not_called()
    assert result1 is not None
    assert result1["id"] == file_id
    assert result2 is not None
    assert result2["id"] == file_id


def test_get_upload_info_o1_lookup_by_id(handler):
    """get_upload_info should use dict lookup (O(1)) keyed by id, not linear scan."""
    file_id = "aabbccddeeff00112233445566778899"
    record = {"id": file_id, "name": "foo.bin", "path": "/some/path"}
    # Two entries: only one matches our file_id
    index_dict = {
        "user1:hash1": {"id": "00000000000000000000000000000001", "name": "other.bin", "path": "/x"},
        "user1:hash2": record,
    }

    handler._index_cache = {}  # force a reload
    handler._index_mtime = 0.0

    uploads_db = os.path.join(handler.upload_dir, "uploads.json")

    with patch.object(handler, "_load_upload_index", return_value=index_dict), \
         patch("os.path.getmtime", return_value=1.0):
        result = handler.get_upload_info(file_id)

    assert result is not None
    assert result["id"] == file_id
    assert result["name"] == "foo.bin"


def test_get_upload_info_returns_copy(handler):
    """get_upload_info must return a copy, not the cached reference."""
    file_id = "ffffffffffffffffffffffffffffffff"
    record = {"id": file_id, "name": "orig.txt", "path": "/p"}
    handler._index_cache = {file_id: record}
    handler._index_mtime = 42.0

    with patch("os.path.getmtime", return_value=42.0):
        result = handler.get_upload_info(file_id)

    # Mutating the returned dict must not affect the cache
    result["name"] = "mutated"
    assert handler._index_cache[file_id]["name"] == "orig.txt"


def test_get_upload_info_missing_id(handler):
    """get_upload_info returns None when the id is not in the index."""
    handler._index_cache = {}
    handler._index_mtime = 0.0

    with patch.object(handler, "_load_upload_index", return_value={}), \
         patch("os.path.getmtime", side_effect=OSError("no file")):
        result = handler.get_upload_info("abcdef1234567890abcdef1234567890")

    assert result is None


# ── Test 2: Cache invalidation when mtime changes ────────────────────────────

def test_cache_invalidated_on_mtime_change(handler):
    """When mtime changes, _get_cached_index must reload from _load_upload_index."""
    file_id = "11111111111111111111111111111111"
    old_record = {"id": file_id, "name": "old.txt", "path": "/old"}
    new_record = {"id": file_id, "name": "new.txt", "path": "/new"}

    handler._index_cache = {file_id: old_record}
    handler._index_mtime = 1.0

    # Second call: mtime is now 2.0 (file was written)
    new_index = {"user:hash": new_record}

    with patch.object(handler, "_load_upload_index", return_value=new_index) as mock_load, \
         patch("os.path.getmtime", return_value=2.0):
        result = handler.get_upload_info(file_id)

    mock_load.assert_called_once()
    assert result is not None
    assert result["name"] == "new.txt"
    assert handler._index_mtime == 2.0


def test_atomic_write_json_resets_mtime(handler, tmp_path):
    """_atomic_write_json must reset _index_mtime to 0.0 so the next read reloads."""
    handler._index_cache = {"x": {"id": "x"}}
    handler._index_mtime = 99.0

    target = str(tmp_path / "test.json")
    handler._atomic_write_json(target, {"key": "value"})

    assert handler._index_mtime == 0.0


def test_cache_repopulated_after_invalidation(handler):
    """After _atomic_write_json resets mtime, next get_upload_info call should
    rebuild the cache from the fresh index."""
    file_id = "22222222222222222222222222222222"
    fresh_record = {"id": file_id, "name": "fresh.txt", "path": "/fresh"}

    # Simulate post-write state: mtime was reset
    handler._index_cache = {}
    handler._index_mtime = 0.0

    with patch.object(handler, "_load_upload_index", return_value={"k": fresh_record}), \
         patch("os.path.getmtime", return_value=5.0):
        result = handler.get_upload_info(file_id)

    assert result is not None
    assert result["name"] == "fresh.txt"
    assert handler._index_mtime == 5.0


# ── Test 3: _find_upload_path uses cache; no os.walk when path is present ─────

def test_find_upload_path_from_cache(handler, tmp_path):
    """_find_upload_path should return the stored path from the cache without
    triggering os.walk when the path is present and valid."""
    file_id = "33333333333333333333333333333333"

    # Create a real file so os.path.exists returns True
    real_path = str(tmp_path / "uploads" / file_id)
    os.makedirs(os.path.dirname(real_path), exist_ok=True)
    with open(real_path, "w") as f:
        f.write("data")

    record = {"id": file_id, "name": "cached.bin", "path": real_path}
    handler._index_cache = {file_id: record}
    handler._index_mtime = 10.0

    with patch("os.path.getmtime", return_value=10.0), \
         patch("os.walk") as mock_walk:
        result = handler._find_upload_path(file_id)

    mock_walk.assert_not_called()
    assert result == real_path


def test_find_upload_path_falls_back_to_walk_when_path_missing(handler, tmp_path):
    """_find_upload_path should fall back to os.walk when the cached path
    does not exist on disk."""
    file_id = "44444444444444444444444444444444"
    nonexistent = str(tmp_path / "uploads" / "gone" / file_id)

    record = {"id": file_id, "name": "missing.bin", "path": nonexistent}
    handler._index_cache = {file_id: record}
    handler._index_mtime = 10.0

    # Create the real file in a different location (to be found by walk)
    real_dir = str(tmp_path / "uploads" / "2026" / "01" / "01")
    os.makedirs(real_dir, exist_ok=True)
    real_path = os.path.join(real_dir, file_id)
    with open(real_path, "w") as f:
        f.write("data")

    # Make _inside_upload_dir return True for the found path
    with patch("os.path.getmtime", return_value=10.0):
        result = handler._find_upload_path(file_id)

    # Should have found it via walk
    assert result == real_path


def test_find_upload_path_returns_none_if_not_found(handler):
    """_find_upload_path returns None when neither cache nor walk finds the file."""
    file_id = "55555555555555555555555555555555"
    handler._index_cache = {}
    handler._index_mtime = 0.0

    with patch.object(handler, "_load_upload_index", return_value={}), \
         patch("os.path.getmtime", side_effect=OSError("no uploads.json")):
        result = handler._find_upload_path(file_id)

    assert result is None


# ── Test 4: async helper ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_upload_info_async(handler):
    """get_upload_info_async must return the same result as get_upload_info."""
    file_id = "66666666666666666666666666666666"
    record = {"id": file_id, "name": "async.txt", "path": "/async/path"}
    handler._index_cache = {file_id: record}
    handler._index_mtime = 7.0

    with patch("os.path.getmtime", return_value=7.0):
        result = await handler.get_upload_info_async(file_id)

    assert result is not None
    assert result["id"] == file_id


# ── Test 5: thread-safety basics ──────────────────────────────────────────────

def test_get_upload_info_thread_safe(handler):
    """Concurrent calls to get_upload_info from multiple threads should not
    raise or return corrupt data."""
    file_id = "77777777777777777777777777777777"
    record = {"id": file_id, "name": "thread.bin", "path": "/thread"}
    handler._index_cache = {file_id: record}
    handler._index_mtime = 99.0

    results = []
    errors = []

    def worker():
        try:
            with patch("os.path.getmtime", return_value=99.0):
                r = handler.get_upload_info(file_id)
            results.append(r)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert all(r is not None and r["id"] == file_id for r in results)

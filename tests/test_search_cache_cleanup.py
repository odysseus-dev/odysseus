import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from services.search import cache as cache_module


def _write_cache_file(cache_dir, key, age_seconds):
    path = cache_dir / f"{key}.cache"
    path.write_text("{}", encoding="utf-8")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


def test_cleanup_cache_removes_expired_disk_files_missing_from_index(tmp_path):
    expired = _write_cache_file(tmp_path, "expired", age_seconds=7200)

    cache_module.cleanup_cache(tmp_path, {}, timedelta(hours=1))

    assert not expired.exists()


def test_cleanup_cache_keeps_fresh_disk_files_missing_from_index(tmp_path):
    fresh = _write_cache_file(tmp_path, "fresh", age_seconds=60)

    cache_module.cleanup_cache(tmp_path, {}, timedelta(hours=1))

    assert fresh.exists()


def test_cleanup_cache_enforces_max_entries_against_disk_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "CACHE_MAX_ENTRIES", 2)
    oldest = _write_cache_file(tmp_path, "oldest", age_seconds=30)
    newer = _write_cache_file(tmp_path, "newer", age_seconds=20)
    newest = _write_cache_file(tmp_path, "newest", age_seconds=10)

    cache_module.cleanup_cache(tmp_path, {}, timedelta(hours=1))

    assert not oldest.exists()
    assert newer.exists()
    assert newest.exists()


def test_cleanup_cache_removes_index_entries_for_missing_files(tmp_path):
    cache_index = {"missing": datetime.now()}

    cache_module.cleanup_cache(tmp_path, cache_index, timedelta(hours=1))

    assert cache_index == {}


def test_cleanup_cache_keeps_index_when_delete_fails(tmp_path, monkeypatch):
    expired = _write_cache_file(tmp_path, "expired", age_seconds=7200)
    cache_index = {"expired": datetime.fromtimestamp(expired.stat().st_mtime)}
    original_unlink = Path.unlink

    def fail_expired_unlink(self, missing_ok=False):
        if self == expired:
            raise OSError("delete failed")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_expired_unlink)

    cache_module.cleanup_cache(tmp_path, cache_index, timedelta(hours=1))

    assert expired.exists()
    assert "expired" in cache_index

"""Regression: the TTS on-disk cache must stay bounded (LRU by mtime).

Before the fix, `TTSService._put_cache()` wrote a file for every distinct
synthesis and nothing but the manual `clear_cache()` ever removed them, so a
long-running instance with TTS enabled could fill the data disk. `_evict_cache()`
now prunes oldest-first once the file count exceeds `TTS_CACHE_MAX_ENTRIES`.
"""
import os

import services.tts.tts_service as tts_service
from services.tts.tts_service import TTSService


def test_evict_cache_removes_oldest_beyond_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_service, "TTS_CACHE_MAX_ENTRIES", 3)
    svc = TTSService(cache_dir=str(tmp_path))

    # Five cache files with strictly increasing mtimes (key0 oldest, key4 newest).
    for i in range(5):
        p = tmp_path / f"key{i}.mp3"
        p.write_bytes(b"x")
        os.utime(p, (1000 + i, 1000 + i))

    svc._evict_cache()

    survivors = sorted(p.name for p in tmp_path.glob("*.*"))
    # Only the 3 newest survive; the 2 oldest are evicted.
    assert survivors == ["key2.mp3", "key3.mp3", "key4.mp3"]


def test_put_cache_keeps_cache_bounded(tmp_path, monkeypatch):
    # Without the fix, _put_cache never evicts, so 4 writes leave 4 files.
    monkeypatch.setattr(tts_service, "TTS_CACHE_MAX_ENTRIES", 2)
    svc = TTSService(cache_dir=str(tmp_path))

    for i in range(4):
        svc._put_cache(f"k{i}", b"RIFF....WAVE")  # non-MP3 bytes -> .wav

    files = list(tmp_path.glob("*.*"))
    assert len(files) <= 2


def test_evict_cache_noop_under_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_service, "TTS_CACHE_MAX_ENTRIES", 10)
    svc = TTSService(cache_dir=str(tmp_path))
    for i in range(3):
        (tmp_path / f"k{i}.wav").write_bytes(b"x")

    svc._evict_cache()

    assert len(list(tmp_path.glob("*.*"))) == 3  # nothing removed under the cap

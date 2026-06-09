from services.tts.tts_service import TTSService


def test_tts_cache_stats_counts_mp3(tmp_path):
    service = TTSService(cache_dir=str(tmp_path))

    # Put an MP3-headed blob (starts with b'ID3') into cache, with size > 1MB so cache_size_mb > 0
    service._put_cache("k", b"ID3" + b"x" * (1024 * 1024))

    stats = service.get_stats()
    assert stats["cache_entries"] == 1
    assert stats["cache_size_mb"] > 0

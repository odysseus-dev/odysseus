import os
import time
from pathlib import Path
import pytest

# Adjust the import path if your file is directly in ./services instead of ./services/tts
from services.tts.tts_service import TTSService

def test_cache_under_limit(tmp_path, monkeypatch):
    """Test that writing a file under the size limit does not trigger eviction."""
    # Set a tiny limit: 100 bytes
    monkeypatch.setenv("ODYSSEUS_TTS_CACHE_MAX_BYTES", "100")
    
    # Initialize service with pytest's temporary directory
    service = TTSService(cache_dir=str(tmp_path))
    
    # Write a 40-byte file (under the 100-byte limit)
    service._put_cache("test_key", b"x" * 40)
    
    # Verify the file was written and nothing was deleted
    files = list(tmp_path.glob("*.*"))
    assert len(files) == 1
    assert sum(f.stat().st_size for f in files) == 40

def test_cache_exceeds_limit_triggers_eviction(tmp_path, monkeypatch):
    """Test that exceeding the limit evicts the oldest files down to 80% capacity."""
    # Set limit to 100 bytes. 80% target capacity will be 80 bytes.
    monkeypatch.setenv("ODYSSEUS_TTS_CACHE_MAX_BYTES", "100")
    service = TTSService(cache_dir=str(tmp_path))
    
    # 1. Setup: Manually create two older files (40 bytes each)
    file1 = tmp_path / "oldest.wav"
    file2 = tmp_path / "middle.wav"
    
    file1.write_bytes(b"a" * 40)
    file2.write_bytes(b"b" * 40)
    
    # Spoof timestamps so file1 is explicitly older than file2
    now = time.time()
    os.utime(file1, (now - 100, now - 100)) # 100 seconds ago
    os.utime(file2, (now - 50, now - 50))   # 50 seconds ago
    
    # 2. Action: Write a 3rd file using the service method (40 bytes)
    # Total cache is now 120 bytes, which exceeds 100.
    # It should delete oldest (file1) to drop to 80 bytes (which matches the 80% target).
    service._put_cache("newest", b"c" * 40)
    
    # 3. Assertions
    # The newest file should exist (saved as .wav because it lacks MP3 magic bytes)
    newest_file = tmp_path / "newest.wav"
    
    assert not file1.exists(), "The oldest file should have been evicted."
    assert file2.exists(), "The middle file should still exist."
    assert newest_file.exists(), "The newest file should have been saved."
    
    # Verify the final directory size is <= 80 bytes
    total_size = sum(f.stat().st_size for f in tmp_path.glob("*.*"))
    assert total_size <= 80

def test_cache_limit_disabled(tmp_path, monkeypatch):
    """Test that setting max bytes to 0 disables eviction."""
    monkeypatch.setenv("ODYSSEUS_TTS_CACHE_MAX_BYTES", "0")
    service = TTSService(cache_dir=str(tmp_path))
    
    # Write 3 large files that would normally trigger eviction
    service._put_cache("file1", b"x" * 1000)
    service._put_cache("file2", b"x" * 1000)
    service._put_cache("file3", b"x" * 1000)
    
    # Ensure nothing was deleted
    files = list(tmp_path.glob("*.*"))
    assert len(files) == 3
    assert sum(f.stat().st_size for f in files) == 3000

def test_cache_eviction_handles_unlink_error_gracefully(tmp_path, monkeypatch):
    """Test that if unlinking a file fails, _put_cache still succeeds without raising."""
    service = TTSService(cache_dir=str(tmp_path))
    service.max_cache_bytes = 50

    # Create a file to evict
    old_file = tmp_path / "old.wav"
    old_file.write_bytes(b"x" * 40)

    # Monkeypatch unlink on Path objects to simulate a PermissionError / file-lock failure
    def mock_unlink(self_path):
        raise OSError("Permission denied / file locked")

    monkeypatch.setattr(Path, "unlink", mock_unlink)

    # Writing a new file triggers eviction which encounters the mocked unlink error
    try:
        service._put_cache("new_key", b"y" * 40)
    except Exception as e:
        pytest.fail(f"_put_cache raised an exception during failed eviction: {e}")

    # The new file should still be written successfully
    assert (tmp_path / "new_key.wav").exists()
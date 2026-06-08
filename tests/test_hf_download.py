from scripts.hf_download import _cleanup_zero_byte_incomplete

def test_cleanup_zero_byte_incomplete_removes_zero_byte_files(tmp_path):
    """Test that zero-byte .incomplete files are removed."""
    # Create a zero-byte .incomplete file
    zero_byte_file = tmp_path / "test.incomplete"
    zero_byte_file.touch()

    # Run the cleanup function
    removed_count = _cleanup_zero_byte_incomplete(repo_id="test/repo", download_path=str(tmp_path))

    # Check that the zero-byte file was removed and the non-zero-byte file was preserved
    assert not zero_byte_file.exists(), "Zero-byte .incomplete file should be removed"
    assert removed_count == 1, "Exactly one zero-byte .incomplete file should be removed"

def test_cleanup_zero_byte_incomplete_preserves_non_zero_byte_files(tmp_path):
    """Test that non-zero-byte .incomplete files are preserved."""
    # Create a non-zero-byte .incomplete file
    non_zero_byte_file = tmp_path / "test_non_zero.incomplete"
    non_zero_byte_file.write_text("data")

    # Run the cleanup function
    removed_count = _cleanup_zero_byte_incomplete(repo_id="test/repo", download_path=str(tmp_path))

    # Check that the non-zero-byte file was preserved
    assert non_zero_byte_file.exists(), "Non-zero-byte .incomplete file should be preserved"
    assert removed_count == 0, "No zero-byte .incomplete files should be removed"

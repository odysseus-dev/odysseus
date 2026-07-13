from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import core.atomic_io as atomic_io


def test_concurrent_writes_use_unique_temporary_files(tmp_path):
    target = tmp_path / "state.txt"
    payloads = [f"payload-{index}-" * 1000 for index in range(32)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(atomic_io.atomic_write_text, str(target), payload)
            for payload in payloads
        ]
        for future in futures:
            future.result()

    assert target.read_text(encoding="utf-8") in payloads
    assert list(tmp_path.glob(".*.tmp")) == []


def test_failed_replace_preserves_target_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    target = tmp_path / "state.txt"
    target.write_text("original", encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        atomic_io.atomic_write_text(str(target), "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_existing_mode_is_preserved(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o640)

    atomic_io.atomic_write_text(str(target), "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o777 == 0o640


def test_explicit_mode_is_applied_to_new_file(tmp_path):
    target = tmp_path / "state.txt"

    atomic_io.atomic_write_text(str(target), "new", mode=0o600)

    assert target.stat().st_mode & 0o777 == 0o600


def test_parent_directory_is_fsynced_after_replace(tmp_path, monkeypatch):
    target = tmp_path / "state.txt"
    calls = []

    monkeypatch.setattr(
        atomic_io,
        "_fsync_parent_directory",
        lambda path: calls.append(path),
    )

    atomic_io.atomic_write_text(str(target), "new")

    assert calls == [str(target)]

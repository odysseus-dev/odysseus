import asyncio
import json
import os
import stat

import pytest

import core.atomic_io as atomic_io
import src.agent_tools.filesystem_tools as filesystem_tools
from src.agent_tools.filesystem_tools import EditFileTool, WriteFileTool


def _write_payload(path, content):
    return json.dumps({"path": str(path), "content": content})


def _edit_payload(path, old, new, **extra):
    payload = {"path": str(path), "old_string": old, "new_string": new}
    payload.update(extra)
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_write_file_successful_replacement_preserves_result_and_diff(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("old line\nsame\n", encoding="utf-8")

    result = await WriteFileTool().execute(_write_payload(target, "new line\nsame\n"), {})

    assert result["exit_code"] == 0
    assert result["output"] == f"Wrote 14 bytes to {target}"
    assert target.read_text(encoding="utf-8") == "new line\nsame\n"
    assert result["diff"]["added"] == 1
    assert result["diff"]["removed"] == 1
    assert result["diff"]["file"] == "note.txt"
    assert result["diff"]["new_file"] is False


@pytest.mark.asyncio
async def test_edit_file_successful_replacement_preserves_result_and_diff(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    result = await EditFileTool().execute(
        _edit_payload(target, "return 1", "return 2"),
        {},
    )

    assert result["exit_code"] == 0
    assert result["output"] == f"Edited {target} (1 replacement)"
    assert target.read_text(encoding="utf-8") == "def f():\n    return 2\n"
    assert result["diff"]["added"] == 1
    assert result["diff"]["removed"] == 1
    assert result["diff"]["file"] == "code.py"
    assert result["diff"]["new_file"] is False


@pytest.mark.asyncio
async def test_write_file_partial_final_write_failure_preserves_existing_target(
    tmp_path, monkeypatch
):
    target = tmp_path / "partial.txt"
    target.write_text("original-content", encoding="utf-8")
    target.chmod(0o640)

    real_fdopen = atomic_io.os.fdopen

    class FailingStream:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            self._stream.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self._stream.__exit__(exc_type, exc_value, traceback)

        def write(self, content):
            self._stream.write(content[: max(1, len(content) // 2)])
            self._stream.flush()
            raise OSError("simulated partial write")

        def __getattr__(self, name):
            return getattr(self._stream, name)

    def flaky_fdopen(*args, **kwargs):
        return FailingStream(real_fdopen(*args, **kwargs))

    monkeypatch.setattr(atomic_io.os, "fdopen", flaky_fdopen)

    result = await WriteFileTool().execute(
        _write_payload(target, "replacement-content"),
        {},
    )

    assert result["exit_code"] == 1
    assert "simulated partial write" in result["error"]
    assert target.read_text(encoding="utf-8") == "original-content"
    assert target.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".*.tmp")) == []


async def test_write_file_cancellation_before_replace_preserves_existing_target(tmp_path, monkeypatch):
    target = tmp_path / "cancelled.txt"
    original = "stable content\n"
    target.write_text(original, encoding="utf-8")

    def cancelled(path, text, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(filesystem_tools, "atomic_write_text", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await WriteFileTool().execute(_write_payload(target, "new content\n"), {})

    assert target.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_write_file_failure_before_replace_preserves_existing_target(tmp_path, monkeypatch):
    target = tmp_path / "failed.txt"
    original = b"original bytes\n"
    target.write_bytes(original)

    def failed(path, text, **kwargs):
        tmp = f"{path}.tmp.test"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text[:4])
        raise OSError("staged write failed")

    monkeypatch.setattr(filesystem_tools, "atomic_write_text", failed)

    result = await WriteFileTool().execute(_write_payload(target, "replacement\n"), {})

    assert result["exit_code"] == 1
    assert "staged write failed" in result["error"]
    assert target.read_bytes() == original


@pytest.mark.asyncio
async def test_write_file_new_file_creation_still_supported(tmp_path):
    target = tmp_path / "new" / "created.txt"

    result = await WriteFileTool().execute(_write_payload(target, "created\n"), {})

    assert result["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "created\n"
    assert result["diff"]["new_file"] is True


@pytest.mark.asyncio
async def test_write_file_preserves_existing_regular_file_permissions(tmp_path):
    target = tmp_path / "mode.txt"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(0o640)

    result = await WriteFileTool().execute(_write_payload(target, "new\n"), {})

    assert result["exit_code"] == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_edit_file_preserves_existing_regular_file_permissions(tmp_path):
    target = tmp_path / "edit-mode.txt"
    target.write_text("old value\n", encoding="utf-8")
    target.chmod(0o640)

    result = await EditFileTool().execute(_edit_payload(target, "old", "new"), {})

    assert result["exit_code"] == 0
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert target.read_text(encoding="utf-8") == "new value\n"


@pytest.mark.asyncio
async def test_edit_file_failure_before_replace_preserves_existing_target(tmp_path, monkeypatch):
    target = tmp_path / "edit-failed.txt"
    original = "alpha beta gamma\n"
    target.write_text(original, encoding="utf-8")

    def failed(path, text, **kwargs):
        raise OSError("replace never reached")

    monkeypatch.setattr(filesystem_tools, "atomic_write_text", failed)

    result = await EditFileTool().execute(_edit_payload(target, "beta", "BETA"), {})

    assert result["exit_code"] == 1
    assert "replace never reached" in result["error"]
    assert target.read_text(encoding="utf-8") == original

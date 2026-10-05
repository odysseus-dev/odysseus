"""write_file keeps line endings instead of translating them per platform (#6491).

Text-mode open() turned every "\n" into "\r\n" on Windows and rewrote a CRLF
file as LF on Linux whenever write_file replaced it. These tests read and write
bytes so the check holds on both platforms.
"""
import json
import os
import tempfile

import pytest

from src.agent_tools.filesystem_tools import WriteFileTool


@pytest.fixture
def target():
    """A fresh directory under /tmp, which the tool path roots allow on every platform."""
    os.makedirs("/tmp", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="odysseus-6491-", dir="/tmp") as directory:
        yield os.path.join(directory, "eol.txt")


def _write(path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _write_file(path, content):
    return await WriteFileTool().execute(json.dumps({"path": path, "content": content}), {})


@pytest.mark.asyncio
async def test_new_file_written_as_sent(target):
    res = await _write_file(target, "a = 1\nb = 2\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\nb = 2\n"


@pytest.mark.asyncio
async def test_new_whitespace_only_file_written_as_sent(target):
    # Goes through the staged no-overwrite publish path.
    res = await _write_file(target, "\n\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"\n\n"


@pytest.mark.asyncio
async def test_overwriting_crlf_file_keeps_crlf(target):
    _write(target, b"a = 1\r\nb = 2\r\n")
    res = await _write_file(target, "a = 1\nb = 3\nc = 4\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\r\nb = 3\r\nc = 4\r\n"
    assert res["diff"]["added"] == 2 and res["diff"]["removed"] == 1


@pytest.mark.asyncio
async def test_overwriting_lf_file_keeps_lf(target):
    _write(target, b"a = 1\nb = 2\n")
    res = await _write_file(target, "a = 1\r\nb = 3\r\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\nb = 3\n"


@pytest.mark.asyncio
async def test_rewriting_same_text_leaves_crlf_file_unchanged(target):
    _write(target, b"a = 1\r\nb = 2\r\n")
    res = await _write_file(target, "a = 1\nb = 2\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\r\nb = 2\r\n"
    assert "diff" not in res


@pytest.mark.asyncio
async def test_overwriting_file_without_line_endings_writes_as_sent(target):
    # No existing ending to keep, so the body's own endings are used.
    _write(target, b"single line")
    res = await _write_file(target, "a = 1\r\nb = 2\r\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\r\nb = 2\r\n"

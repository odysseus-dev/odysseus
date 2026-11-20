"""write_file keeps line endings instead of translating them per platform (#6491).

Text-mode open() turned every "\n" into "\r\n" on Windows and rewrote a CRLF
file as LF on Linux whenever write_file replaced it. These tests read and write
bytes so the check holds on both platforms.
"""
import json

import pytest

from src.agent_tools.filesystem_tools import WriteFileTool


@pytest.fixture
def target(tmp_path, monkeypatch):
    from src import tool_execution

    monkeypatch.setattr(tool_execution, "get_active_workspace", lambda: str(tmp_path))
    return str(tmp_path / "eol.txt")


def _write(path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _write_file(path, content):
    return await WriteFileTool().execute(json.dumps({"path": path, "content": content}), {})


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
async def test_new_file_written_as_sent(target, eol):
    body = eol.join(["a = 1", "b = 2", ""])
    res = await _write_file(target, body)
    assert res["exit_code"] == 0, res
    assert _read(target) == body.encode("utf-8")


@pytest.mark.asyncio
async def test_new_whitespace_only_file_written_as_sent(target):
    # Goes through the staged no-overwrite publish path.
    res = await _write_file(target, "\n\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"], ids=["lf", "crlf", "cr"])
async def test_overwriting_file_keeps_existing_line_endings(target, eol):
    _write(target, eol.join([b"a = 1", b"b = 2", b""]))
    res = await _write_file(target, "a = 1\nb = 3\nc = 4\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == eol.join([b"a = 1", b"b = 3", b"c = 4", b""])
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


@pytest.mark.asyncio
async def test_overwriting_mixed_line_endings_uses_majority(target):
    _write(target, b"a = 1\r\nb = 2\r\nc = 3\nd = 4\r\n")
    res = await _write_file(target, "a = 1\nb = 3\nc = 3\nd = 4\n")
    assert res["exit_code"] == 0, res
    assert _read(target) == b"a = 1\r\nb = 3\r\nc = 3\r\nd = 4\r\n"

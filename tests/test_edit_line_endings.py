"""edit_file / apply_patch keep the file's line endings (#6491).

Text-mode open() translates newlines on read and write, so an edit used to
rewrite every line ending in the file: LF -> CRLF on Windows, CRLF -> LF on
Linux. These tests read and write bytes so the check holds on both platforms.
"""
import json

import pytest

from src.agent_tools.filesystem_tools import ApplyPatchTool, EditFileTool


def _write(path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def tmp_file(tmp_path, monkeypatch):
    from src import tool_execution

    monkeypatch.setattr(tool_execution, "get_active_workspace", lambda: str(tmp_path))
    return str(tmp_path / "eol.txt")


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
async def test_edit_file_keeps_line_endings(tmp_file, eol):
    _write(tmp_file, eol.join([b"a = 1", b"b = 2", b"c = 3", b""]))
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "b = 2", "new_string": "b = 3"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == eol.join([b"a = 1", b"b = 3", b"c = 3", b""])
    assert res["diff"]["added"] == 1 and res["diff"]["removed"] == 1


@pytest.mark.asyncio
async def test_edit_file_multiline_old_string_matches_crlf_file(tmp_file):
    # Models send "\n" in old_string even when the file on disk uses CRLF.
    _write(tmp_file, b"a = 1\r\nb = 2\r\nc = 3\r\n")
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "a = 1\nb = 2", "new_string": "a = 1\nb = 3\nb2 = 4"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\r\nb = 3\r\nb2 = 4\r\nc = 3\r\n"


@pytest.mark.asyncio
async def test_edit_file_mixed_line_endings_use_majority(tmp_file):
    # A stray LF line in a CRLF file must not stop multi-line matches. The
    # write goes back with the ending most lines already use.
    _write(tmp_file, b"a = 1\r\nb = 2\r\nc = 3\nd = 4\r\n")
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "a = 1\nb = 2", "new_string": "a = 1\nb = 3"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\r\nb = 3\r\nc = 3\r\nd = 4\r\n"


@pytest.mark.asyncio
async def test_edit_file_crlf_in_new_string_not_doubled(tmp_file):
    _write(tmp_file, b"a = 1\r\nb = 2\r\n")
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "b = 2", "new_string": "b = 3\r\nb2 = 4"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\r\nb = 3\r\nb2 = 4\r\n"


@pytest.mark.asyncio
async def test_edit_file_crlf_old_string_matches_lf_file(tmp_file):
    _write(tmp_file, b"a = 1\nb = 2\nc = 3\n")
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "a = 1\r\nb = 2", "new_string": "a = 1\r\nb = 3"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\nb = 3\nc = 3\n"


@pytest.mark.asyncio
async def test_edit_file_cr_only_file(tmp_file):
    _write(tmp_file, b"a = 1\rb = 2\rc = 3\r")
    res = await EditFileTool().execute(
        json.dumps({"path": tmp_file, "old_string": "a = 1\nb = 2", "new_string": "a = 1\nb = 3"}), {}
    )
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\rb = 3\rc = 3\r"


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
async def test_apply_patch_keeps_line_endings(tmp_file, eol):
    _write(tmp_file, eol.join([b"a = 1", b"b = 2", b"c = 3", b""]))
    patch = f"*** Begin Patch\n*** Update File: {tmp_file}\n@@\n a = 1\n-b = 2\n+b = 3\n*** End Patch"
    res = await ApplyPatchTool().execute(patch, {})
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == eol.join([b"a = 1", b"b = 3", b"c = 3", b""])


@pytest.mark.asyncio
async def test_apply_patch_mixed_line_endings_use_majority(tmp_file):
    _write(tmp_file, b"a = 1\r\nb = 2\r\nc = 3\nd = 4\r\n")
    patch = f"*** Begin Patch\n*** Update File: {tmp_file}\n@@\n a = 1\n-b = 2\n+b = 3\n c = 3\n*** End Patch"
    res = await ApplyPatchTool().execute(patch, {})
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"a = 1\r\nb = 3\r\nc = 3\r\nd = 4\r\n"


@pytest.mark.asyncio
async def test_apply_patch_add_file_writes_lf(tmp_file):
    patch = f"*** Begin Patch\n*** Add File: {tmp_file}\n+x = 1\n+y = 2\n*** End Patch"
    res = await ApplyPatchTool().execute(patch, {})
    assert res["exit_code"] == 0, res
    assert _read(tmp_file) == b"x = 1\ny = 2\n"

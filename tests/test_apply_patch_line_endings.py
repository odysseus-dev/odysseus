"""Patch updates keep LF, CRLF and CR endings on every host (#6491)."""
import pytest

from src.agent_tools.filesystem_tools import ApplyPatchTool


@pytest.fixture
def target(tmp_path, monkeypatch):
    from src import tool_execution

    monkeypatch.setattr(tool_execution, "get_active_workspace", lambda: str(tmp_path))
    return tmp_path / "eol.txt"


async def patch(target):
    text = (f"*** Begin Patch\n*** Update File: {target}\n@@\n"
            " a = 1\n-b = 2\n+b = 3\n*** End Patch")
    return await ApplyPatchTool().execute(text, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"], ids=["lf", "crlf", "cr"])
async def test_apply_patch_keeps_line_endings(target, eol):
    target.write_bytes(eol.join([b"a = 1", b"b = 2", b"c = 3", b""]))
    result = await patch(target)
    assert result["exit_code"] == 0, result
    assert target.read_bytes() == eol.join([b"a = 1", b"b = 3", b"c = 3", b""])
    assert result["diff"]["added"] == result["diff"]["removed"] == 1


@pytest.mark.asyncio
async def test_apply_patch_mixed_line_endings_use_majority(target):
    target.write_bytes(b"a = 1\r\nb = 2\r\nc = 3\nd = 4\r\n")
    result = await patch(target)
    assert result["exit_code"] == 0, result
    assert target.read_bytes() == b"a = 1\r\nb = 3\r\nc = 3\r\nd = 4\r\n"


@pytest.mark.asyncio
async def test_apply_patch_mixed_line_endings_tie_uses_lf(target):
    target.write_bytes(b"a = 1\r\nb = 2\n")
    result = await patch(target)
    assert result["exit_code"] == 0, result
    assert target.read_bytes() == b"a = 1\nb = 3\n"


@pytest.mark.asyncio
async def test_apply_patch_add_file_writes_lf(target):
    text = f"*** Begin Patch\n*** Add File: {target}\n+x = 1\n+y = 2\n*** End Patch"
    result = await ApplyPatchTool().execute(text, {})
    assert result["exit_code"] == 0, result
    assert target.read_bytes() == b"x = 1\ny = 2\n"

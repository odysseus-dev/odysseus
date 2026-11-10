"""Exact edits must preserve bytes outside the requested replacement."""
import asyncio
import json

import pytest

from src.agent_tools.filesystem_tools import EditFileTool


def edit(path, old, new, **options):
    return asyncio.run(EditFileTool().execute(json.dumps({
        "path": str(path), "old_string": old, "new_string": new, **options}), {}))


@pytest.mark.parametrize("original", [
    b"First: alpha\r\nSecond: alpha\r\nKeep: violet-72\r\n",
    b"First: alpha\nSecond: alpha\r\nKeep: violet-72\r",
    b"First: alpha\rSecond: alpha\rKeep: violet-72",
])
def test_edit_and_undo_preserve_unrelated_line_endings(tmp_path, original):
    path = tmp_path / "edit fixture.txt"
    path.write_bytes(original)
    result = edit(path, "Second: alpha", "Second: beta")
    assert result["exit_code"] == 0
    assert path.read_bytes() == original.replace(b"Second: alpha", b"Second: beta", 1)
    assert edit(path, "Second: beta", "Second: alpha")["exit_code"] == 0
    assert path.read_bytes() == original


def test_exact_multiline_crlf_match_and_explicit_line_ending_change(tmp_path):
    path = tmp_path / 'multiline.txt'
    path.write_bytes(b'First: alpha\r\nSecond: alpha\r\nKeep: violet-72\r\n')
    assert edit(path, 'First: alpha\r\nSecond: alpha', 'First: beta\r\nSecond: beta')['exit_code'] == 0
    assert path.read_bytes() == b'First: beta\r\nSecond: beta\r\nKeep: violet-72\r\n'
    assert edit(path, '\r\n', '\n', replace_all=True)['exit_code'] == 0
    assert path.read_bytes() == b'First: beta\nSecond: beta\nKeep: violet-72\n'


def test_ambiguous_failed_edit_keeps_original_bytes(tmp_path):
    path = tmp_path / 'ambiguous.txt'
    original = b'alpha\r\nalpha\r\n'
    path.write_bytes(original)
    assert edit(path, 'alpha', 'beta')['exit_code'] == 1
    assert path.read_bytes() == original

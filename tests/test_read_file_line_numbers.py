"""read_file: line_numbers option and range-read numbering."""
import json
import os

import pytest

from src.agent_tools.filesystem_tools import ReadFileTool


@pytest.mark.asyncio
async def test_read_file_default_output_unchanged():
    p = os.path.join("/tmp", "rf_plain.txt")
    open(p, "w").write("alpha\nbeta\ngamma\n")
    res = await ReadFileTool().execute(json.dumps({"path": p}), {})
    assert res["exit_code"] == 0
    assert res["output"] == "alpha\nbeta\ngamma\n"
    os.unlink(p)


@pytest.mark.asyncio
async def test_read_file_line_numbers_full():
    p = os.path.join("/tmp", "rf_nums.txt")
    open(p, "w").write("alpha\nbeta\ngamma\n")
    res = await ReadFileTool().execute(json.dumps({"path": p, "line_numbers": True}), {})
    assert res["exit_code"] == 0
    assert res["output"] == "1\talpha\n2\tbeta\n3\tgamma\n"
    os.unlink(p)


@pytest.mark.asyncio
async def test_read_file_line_numbers_with_offset():
    # Numbering starts at the offset line, matching the real file positions.
    p = os.path.join("/tmp", "rf_nums_off.txt")
    open(p, "w").write("l1\nl2\nl3\nl4\nl5\n")
    res = await ReadFileTool().execute(
        json.dumps({"path": p, "offset": 3, "limit": 2, "line_numbers": True}), {}
    )
    assert res["exit_code"] == 0
    assert res["output"] == "3\tl3\n4\tl4\n"
    os.unlink(p)


@pytest.mark.asyncio
async def test_read_file_range_without_line_numbers():
    p = os.path.join("/tmp", "rf_range.txt")
    open(p, "w").write("l1\nl2\nl3\nl4\n")
    res = await ReadFileTool().execute(json.dumps({"path": p, "offset": 2, "limit": 2}), {})
    assert res["exit_code"] == 0
    assert res["output"] == "l2\nl3\n"
    os.unlink(p)

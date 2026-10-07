import json
from pathlib import Path

import pytest

from src.agent_tools.filesystem_tools import ApplyPatchTool
import src.agent_tools.filesystem_tools as filesystem_tools


@pytest.mark.asyncio
async def test_apply_patch_rolls_back_every_file_when_commit_fails(
    monkeypatch,
    tmp_path: Path,
):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("before first\n", encoding="utf-8")
    second.write_text("before second\n", encoding="utf-8")
    patch_text = """*** Begin Patch
*** Update File: first.txt
@@
-before first
+after first
*** Update File: second.txt
@@
-before second
+after second
*** End Patch"""

    import src.tool_execution as tool_execution

    monkeypatch.setattr(
        tool_execution,
        "_resolve_tool_path",
        lambda path: str(tmp_path / path),
    )
    original_replace = filesystem_tools.os.replace
    calls = 0

    def fail_during_second_install(source, destination):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated second-file commit failure")
        return original_replace(source, destination)

    monkeypatch.setattr(filesystem_tools.os, "replace", fail_during_second_install)

    result = await ApplyPatchTool().execute(
        json.dumps({"patch": patch_text}),
        {},
    )

    assert result["exit_code"] == 1
    assert "simulated second-file commit failure" in result["error"]
    assert first.read_text(encoding="utf-8") == "before first\n"
    assert second.read_text(encoding="utf-8") == "before second\n"

import json

import pytest

from src.agent_tools.filesystem_tools import EditFileTool, ReadFileTool, WriteFileTool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "content", "error_fragment"),
    [
        (ReadFileTool(), '{"path":"broken"', "valid JSON"),
        (ReadFileTool(), "{}", "path required"),
        (WriteFileTool(), '{"path":"broken"', "valid JSON"),
        (WriteFileTool(), '{"path":"out.txt"}', "content required"),
        (
            EditFileTool(),
            '{"path":"a.txt","old_string":"x","new_string":"y","replace_all":"false"}',
            "must be a boolean",
        ),
    ],
)
async def test_filesystem_tools_reject_invalid_structured_arguments_before_disk_access(
    monkeypatch,
    tool,
    content,
    error_fragment,
):
    import src.tool_execution as tool_execution

    resolved = []

    def unexpected_resolve(path):
        resolved.append(path)
        raise AssertionError("invalid arguments reached path resolution")

    monkeypatch.setattr(tool_execution, "_resolve_tool_path", unexpected_resolve)

    result = await tool.execute(content, {})

    assert result["exit_code"] == 1
    assert error_fragment in result["error"]
    assert resolved == []


@pytest.mark.asyncio
async def test_write_file_preserves_existing_binary_artifact(tmp_path, monkeypatch):
    import src.tool_execution as tool_execution

    target = tmp_path / "output.pdf"
    original = b"%PDF-1.7\nvalid binary payload\x00\xff"
    target.write_bytes(original)
    monkeypatch.setattr(tool_execution, "_resolve_tool_path", lambda _path: str(target))

    result = await WriteFileTool().execute(
        '{"path":"output.pdf","content":"The PDF is already complete."}', {}
    )

    assert result["exit_code"] == 1
    assert result["binary_artifact_preserved"] is True
    assert "binary artifact path" in result["error"]
    assert target.read_bytes() == original


@pytest.mark.asyncio
async def test_write_file_rejects_new_binary_artifact_path(tmp_path, monkeypatch):
    import src.tool_execution as tool_execution

    target = tmp_path / "new.pdf"
    monkeypatch.setattr(tool_execution, "_resolve_tool_path", lambda _path: str(target))

    result = await WriteFileTool().execute(
        '{"path":"new.pdf","content":"not really a PDF"}', {}
    )

    assert result["exit_code"] == 1
    assert result["binary_artifact_preserved"] is False
    assert not target.exists()


@pytest.mark.asyncio
async def test_write_file_still_rewrites_existing_text_file(tmp_path, monkeypatch):
    import src.tool_execution as tool_execution

    target = tmp_path / "notes.txt"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(tool_execution, "_resolve_tool_path", lambda _path: str(target))

    result = await WriteFileTool().execute(
        '{"path":"notes.txt","content":"new"}', {}
    )

    assert result["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_write_file_keeps_workspace_results_on_the_stable_virtual_path(tmp_path):
    from src.tool_execution import _active_workspace

    token = _active_workspace.set(str(tmp_path))
    try:
        result = await WriteFileTool().execute(
            '{"path":"/workspace/results/report.txt","content":"done"}', {}
        )
    finally:
        _active_workspace.reset(token)

    assert result["exit_code"] == 0
    assert "/workspace/results/report.txt" in result["output"]
    assert str(tmp_path) not in result["output"]
    assert (tmp_path / "results" / "report.txt").read_text() == "done"


@pytest.mark.asyncio
async def test_write_file_unwraps_markdown_fence_for_html_artifact(tmp_path, monkeypatch):
    import src.tool_execution as tool_execution

    target = tmp_path / "output.html"
    monkeypatch.setattr(tool_execution, "_resolve_tool_path", lambda _path: str(target))

    result = await WriteFileTool().execute(
        '{"path":"output.html","content":"```html\\n<div>clock</div>\\n```"}', {}
    )

    assert result["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == "<div>clock</div>"


@pytest.mark.asyncio
async def test_write_file_preserves_literal_fence_for_markdown(tmp_path, monkeypatch):
    import src.tool_execution as tool_execution

    target = tmp_path / "notes.md"
    monkeypatch.setattr(tool_execution, "_resolve_tool_path", lambda _path: str(target))
    content = "```python\\nprint('literal example')\\n```"

    result = await WriteFileTool().execute(
        json.dumps({"path": "notes.md", "content": content}), {}
    )

    assert result["exit_code"] == 0
    assert target.read_text(encoding="utf-8") == content

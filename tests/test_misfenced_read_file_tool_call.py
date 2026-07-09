import json

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_bash_fenced_read_file_function_call_runs_as_read_file():
    blocks = parse_tool_blocks('```bash\nread_file("notes/todo.md")\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "notes/todo.md"


def test_python_fenced_read_file_function_call_runs_as_read_file():
    blocks = parse_tool_blocks('```python\nread_file(path="notes/todo.md", offset=3, limit=2)\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert json.loads(blocks[0].content) == {
        "path": "notes/todo.md",
        "offset": 3,
        "limit": 2,
    }


def test_bash_fenced_read_file_command_runs_as_read_file():
    blocks = parse_tool_blocks('```bash\nread_file "notes/todo.md"\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "notes/todo.md"


def test_bash_fenced_read_file_json_command_runs_as_read_file():
    blocks = parse_tool_blocks('```bash\nread_file {"path":"notes/todo.md","offset":1,"limit":4}\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert json.loads(blocks[0].content) == {
        "path": "notes/todo.md",
        "offset": 1,
        "limit": 4,
    }


def test_multiline_bash_read_file_block_stays_bash():
    blocks = parse_tool_blocks('```bash\nread_file notes/todo.md\necho done\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert "read_file notes/todo.md" in blocks[0].content


def test_nontrivial_python_read_file_name_stays_python_code():
    blocks = parse_tool_blocks('```python\nprint(read_file("notes/todo.md"))\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"


def test_strip_tool_blocks_removes_rescued_read_file_fence():
    text = 'Opening file:\n```bash\nread_file "notes/todo.md"\n```\nDone.'

    cleaned = strip_tool_blocks(text)

    assert "```" not in cleaned
    assert "read_file" not in cleaned
    assert "Opening file:" in cleaned
    assert "Done." in cleaned


def test_same_line_read_file_path_fence_runs_as_read_file():
    blocks = parse_tool_blocks('```read_file path="README.md"\n```')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "README.md"


def test_same_line_workspace_tools_still_parse_when_native_fences_are_skipped():
    text = (
        '```read_file path="README.md"\n```\n'
        '```read_file path="package.json"```\n'
        '```ls path=".github/workflows"\n```'
    )

    blocks = parse_tool_blocks(text, skip_fenced=True)

    assert [(b.tool_type, b.content) for b in blocks] == [
        ("read_file", "README.md"),
        ("read_file", "package.json"),
        ("ls", ".github/workflows"),
    ]


def test_empty_get_workspace_fence_executes():
    blocks = parse_tool_blocks("```get_workspace\n```")

    assert len(blocks) == 1
    assert blocks[0].tool_type == "get_workspace"
    assert blocks[0].content == ""


def test_plain_readonly_workspace_tool_line_runs_as_readonly_tool():
    blocks = parse_tool_blocks('read_file path="README.md"', skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "read_file"
    assert blocks[0].content == "README.md"


def test_strip_tool_blocks_removes_same_line_readonly_workspace_fences_when_skipped():
    text = (
        'Opening files:\n'
        '```read_file path="README.md"\n```\n'
        '```ls path=".github/workflows"```\n'
        'Done.'
    )

    cleaned = strip_tool_blocks(text, skip_fenced=True)

    assert "read_file" not in cleaned
    assert "ls path" not in cleaned
    assert "```" not in cleaned
    assert "Opening files:" in cleaned
    assert "Done." in cleaned

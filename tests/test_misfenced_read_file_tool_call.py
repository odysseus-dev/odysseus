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

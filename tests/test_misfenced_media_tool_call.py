"""Recover unambiguous media calls from textual fallback fences."""

import json

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_python_fenced_inspect_media_function_call_runs_as_media_tool():
    blocks = parse_tool_blocks(
        "```python\ninspect_media('/workspace/fixtures/video.webm')\n```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "inspect_media"
    assert json.loads(blocks[0].content) == {
        "path": "/workspace/fixtures/video.webm",
    }


def test_bash_fenced_inspect_media_command_runs_as_media_tool():
    blocks = parse_tool_blocks(
        "```bash\n#!bg\ninspect_media /workspace/fixtures/video.webm\n```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "inspect_media"
    assert json.loads(blocks[0].content) == {
        "path": "/workspace/fixtures/video.webm",
    }


def test_bash_fenced_json_media_command_runs_as_media_tool():
    blocks = parse_tool_blocks(
        "```bash\n"
        'transcribe_media {"path":"/workspace/fixtures/video.mp4",'
        '"transcription_type":"video_analysis"}\n'
        "```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "transcribe_media"
    assert json.loads(blocks[0].content) == {
        "path": "/workspace/fixtures/video.mp4",
        "transcription_type": "video_analysis",
    }


def test_misfenced_json_media_command_rejects_extra_shell_text():
    blocks = parse_tool_blocks(
        '```bash\ninspect_media {"path":"/workspace/fixtures/video.mp4"} && echo nope\n```'
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"


def test_media_function_call_preserves_literal_options_and_aliases_path():
    blocks = parse_tool_blocks(
        "```python\n"
        "inspect_media(file='/workspace/fixtures/video.mp4', frames=8, start='10%')\n"
        "```"
    )

    assert len(blocks) == 1
    assert json.loads(blocks[0].content) == {
        "path": "/workspace/fixtures/video.mp4",
        "frames": 8,
        "start": "10%",
    }


def test_media_function_call_normalizes_common_frame_count_alias():
    blocks = parse_tool_blocks(
        "```bash\n"
        "inspect_media(\"/workspace/fixtures/video.mp4\", "
        "frame_count=16, exports=[{\"timestamp\": \"00:00:10\", "
        "\"output_path\": \"/workspace/frame.png\"}])\n"
        "```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "inspect_media"
    assert json.loads(blocks[0].content) == {
        "path": "/workspace/fixtures/video.mp4",
        "frames": 16,
        "exports": [{
            "timestamp": "00:00:10",
            "output_path": "/workspace/frame.png",
        }],
    }


def test_translate_media_alias_routes_to_inspect_media():
    blocks = parse_tool_blocks(
        "```python\ntranslate_media('/workspace/fixtures/video.mp4')\n```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "inspect_media"


def test_multiline_media_fence_stays_code():
    blocks = parse_tool_blocks(
        "```python\n"
        "path = '/workspace/fixtures/video.mp4'\n"
        "inspect_media(path)\n"
        "```"
    )

    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"


def test_strip_tool_blocks_removes_rescued_media_fence():
    text = (
        "Inspecting now.\n"
        "```python\ninspect_media('/workspace/fixtures/video.mp4')\n```\n"
        "Done."
    )

    cleaned = strip_tool_blocks(text)

    assert "inspect_media" not in cleaned
    assert "```" not in cleaned
    assert "Inspecting now." in cleaned
    assert "Done." in cleaned

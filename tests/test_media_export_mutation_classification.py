import json

from src.agent_loop import ToolBlock, _workspace_mutation_tool_block


def test_inspect_media_nested_exports_are_workspace_mutations():
    block = ToolBlock(
        "inspect_media",
        json.dumps({
            "path": "/workspace/fixtures/video.mp4",
            "exports": [
                {"timestamp": "00:03", "output_path": "/workspace/frame-01.png"},
                {"timestamp": "00:08", "output_path": "/workspace/frame-02.png"},
            ],
        }),
    )

    assert _workspace_mutation_tool_block(block) is True


def test_plain_inspect_media_remains_read_only():
    block = ToolBlock(
        "inspect_media",
        json.dumps({"path": "/workspace/fixtures/video.mp4", "frames": 8}),
    )

    assert _workspace_mutation_tool_block(block) is False

from src.agent_loop import _normalize_stream_document_fences
from src.tool_parsing import parse_tool_blocks


def test_truncated_update_document_fence_is_executable():
    text = "```update_documen\n# Title\n\nSwedish body\n```"

    normalized = _normalize_stream_document_fences(text, "update_document")
    blocks = parse_tool_blocks(normalized)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "update_document"
    assert "Swedish body" in blocks[0].content


def test_truncated_edit_document_fence_is_executable():
    text = (
        "```edit_documen\n"
        "<<<FIND>>>\nold\n<<<REPLACE>>>\nnew\n<<<END>>>\n"
        "```"
    )

    normalized = _normalize_stream_document_fences(text, "update_document")
    blocks = parse_tool_blocks(normalized)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "edit_document"


def test_compact_truncated_edit_document_fence_is_executable():
    text = "```edi_documen\n<<FIND>old\n<<REPLACE>new\n<<END>```\n|end|"

    normalized = _normalize_stream_document_fences(text, "update_document")
    blocks = parse_tool_blocks(normalized)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "edit_document"
    assert blocks[0].content == "<<<FIND>>>\nold\n<<<REPLACE>>>\nnew\n<<<END>>>"

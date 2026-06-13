"""Parse ```json {"name": "mcp__...", "arguments": {...}} ``` tool fences."""

from src.tool_parsing import parse_tool_blocks, _parse_json_tool_fence


def test_parse_json_tool_fence_mcp():
    text = '''Here is the call:
```json
{"name": "mcp__16ad5e29__search_arxiv", "arguments": {"search_query": "transformers", "max_results": 3}}
```'''
    block = _parse_json_tool_fence('{"name": "mcp__16ad5e29__search_arxiv", "arguments": {"search_query": "transformers", "max_results": 3}}')
    assert block is not None
    assert block.tool_type == "mcp__16ad5e29__search_arxiv"
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type.startswith("mcp__")

"""Issue #2925 — a fenced ```python/```bash block wrapping an <invoke> call that
can't be converted (e.g. a hyphenated/namespaced tool name that _XML_INVOKE_RE's
\\w+ won't match, or an unknown tool) must NOT fall through and ship the raw XML
to the code executor as if it were python/bash.
"""
from src.tool_parsing import parse_tool_blocks

def test_unconvertible_invoke_in_fence_is_not_executed_as_code():
    text = '```python\n<invoke name="foo-bar">\n<parameter name="x">1</parameter>\n</invoke>\n```'
    blocks = parse_tool_blocks(text)
    # the hyphenated name can't match _XML_INVOKE_RE, so nothing converts —
    # the raw XML must not be appended as a python/bash code block.
    assert not any(
        b.tool_type in ("python", "bash") and "<invoke" in b.content for b in blocks
    ), blocks


def test_plain_fenced_python_block_still_parses_as_code():
    # No regression: an ordinary fenced python block (no <invoke>) still works.
    blocks = parse_tool_blocks('```python\nprint("hi")\n```')
    assert any(b.tool_type == "python" and 'print("hi")' in b.content for b in blocks), blocks


def test_simple_web_search_call_inside_python_fence_runs_as_web_search():
    blocks = parse_tool_blocks('```python\nweb_search("latest Python release")\n```')
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "latest Python release"


def test_google_search_alias_inside_bash_fence_preserves_freshness_args():
    blocks = parse_tool_blocks(
        '```bash\ngoogle_search(query="Qwen latest release", freshness="week", max_pages=7)\n```'
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert '"query": "Qwen latest release"' in blocks[0].content
    assert '"freshness": "week"' in blocks[0].content
    assert '"max_pages": 7' in blocks[0].content


def test_nontrivial_python_with_web_search_name_stays_python_code():
    blocks = parse_tool_blocks('```python\nprint(web_search("latest Python release"))\n```')
    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"


def test_plain_search_function_inside_python_fence_stays_python_code():
    blocks = parse_tool_blocks('```python\nsearch("private customer name")\n```')
    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"


def test_plain_fetch_function_inside_python_fence_stays_python_code():
    blocks = parse_tool_blocks('```python\nfetch("internal-url")\n```')
    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"

"""Issue #2925 — a fenced ```python/```bash block wrapping an <invoke> call that
can't be converted (e.g. a hyphenated/namespaced tool name that _XML_INVOKE_RE's
\\w+ won't match, or an unknown tool) must NOT fall through and ship the raw XML
to the code executor as if it were python/bash.
"""
import sys
from unittest.mock import MagicMock

for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database', 'core.auth'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import src.agent_tools  # noqa: E402, F401
from src.tool_parsing import parse_tool_blocks  # noqa: E402


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

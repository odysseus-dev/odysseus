"""Regression coverage for local models emitting tool-shaped Python."""

import src.agent_tools  # noqa: F401  (initializes ToolBlock before parser import)
from src.tool_parsing import parse_tool_blocks


def test_python_fence_with_literal_known_tool_call_uses_named_tool():
    response = '''```python
create_document(
    title="Modelfile Documentation",
    language="markdown",
    content="# Odysseus"
)
```'''

    blocks = parse_tool_blocks(response)

    assert [(block.tool_type, block.content) for block in blocks] == [
        ("create_document", "Modelfile Documentation\nmarkdown\n# Odysseus")
    ]


def test_python_fence_with_dynamic_known_tool_call_remains_python():
    response = '''```python
create_document(title=title, content=build_content())
```'''

    blocks = parse_tool_blocks(response)

    assert [(block.tool_type, block.content) for block in blocks] == [
        ("python", "create_document(title=title, content=build_content())")
    ]


def test_ordinary_python_fence_remains_python():
    response = '''```python
print("hello")
```'''

    blocks = parse_tool_blocks(response)

    assert [(block.tool_type, block.content) for block in blocks] == [("python", 'print("hello")')]

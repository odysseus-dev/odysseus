"""Every FUNCTION_TOOL_SCHEMAS tool must have a ToolIndex description.

Agent mode selects tools by embedding BUILTIN_TOOL_DESCRIPTIONS and
retrieving the top-K per message. A tool that exists in tool_schemas but has
no description entry can never be retrieved, so the agent advertises the
capability (e.g. API integrations in the system prompt) while the schema is
never actually sent to the model. api_call was missing exactly this way.

Parsed with ast instead of importing, so the test does not pull in the
embedding/ChromaDB stack.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _assigned_value(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    raise AssertionError(f"{name} assignment not found")


def _schema_tool_names():
    src = open(os.path.join(ROOT, "src", "tool_schemas.py"), encoding="utf-8").read()
    value = _assigned_value(ast.parse(src), "FUNCTION_TOOL_SCHEMAS")
    return {item["function"]["name"] for item in ast.literal_eval(value)}


def _indexed_tool_names():
    src = open(os.path.join(ROOT, "src", "tool_index.py"), encoding="utf-8").read()
    value = _assigned_value(ast.parse(src), "BUILTIN_TOOL_DESCRIPTIONS")
    return {ast.literal_eval(key) for key in value.keys}


def test_every_schema_tool_has_an_index_description():
    missing = _schema_tool_names() - _indexed_tool_names()
    assert not missing, (
        "Tools defined in FUNCTION_TOOL_SCHEMAS but absent from "
        f"BUILTIN_TOOL_DESCRIPTIONS (RAG can never select them): {sorted(missing)}"
    )


def test_api_call_is_indexed_with_a_real_description():
    src = open(os.path.join(ROOT, "src", "tool_index.py"), encoding="utf-8").read()
    value = _assigned_value(ast.parse(src), "BUILTIN_TOOL_DESCRIPTIONS")
    descriptions = {
        ast.literal_eval(k): ast.literal_eval(v) for k, v in zip(value.keys, value.values)
    }
    assert "api_call" in descriptions
    assert len(descriptions["api_call"]) > 50

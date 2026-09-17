"""manage_research must be exposed to native function-calling models.

Parsed with ast (like test_tool_index_schema_parity.py) because importing
src.tool_schemas directly hits a circular import through src.agent_tools.
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


def test_manage_research_has_native_schema():
    assert "manage_research" in _schema_tool_names()


def test_research_keyword_hint_selects_manage_research():
    src = open(os.path.join(ROOT, "src", "tool_index.py"), encoding="utf-8").read()
    assert '{"trigger_research", "manage_research"}' in src

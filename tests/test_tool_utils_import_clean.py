"""Verify src.tool_utils has no project imports beyond src.constants.

If someone adds an import from src.settings, src.database, or any other
project module inside tool_utils.py, the circular import that this module
exists to break will silently return a partially-initialized module.
This test catches that statically.
"""

import ast
import pathlib


def test_tool_utils_has_no_project_imports():
    src = pathlib.Path("src/tool_utils.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                msg = f"Illegal project import in tool_utils.py: {node.module}"
                assert node.module in ("src.constants",) or not node.module.startswith(
                    "src."
                ), msg

"""Verify the fused discipline module is wired into the agent system prompt.

Mirrors the mock-import pattern in test_agent_loop.py so we can import
src.agent_loop without loading the full app stack (DB, FastAPI, etc.).
"""
import sys
from unittest.mock import MagicMock

_MOCKED_IMPORTS = [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext",
    "sqlalchemy.ext.declarative", "sqlalchemy.ext.hybrid",
    "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "src.database", "core.models", "core.database",
    "src.llm_core", "src.model_context", "src.settings",
    "src.tool_security", "src.tool_policy",
    "src.tool_utils", "src.agent_tools",
]

_INJECTED_STUBS = {}
_PREEXISTING = {m: sys.modules.get(m) for m in _MOCKED_IMPORTS}
for mod in _MOCKED_IMPORTS:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()
        _INJECTED_STUBS[mod] = sys.modules[mod]


def _drop_module_if_same(name, expected):
    if sys.modules.get(name) is expected:
        sys.modules.pop(name, None)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and getattr(parent, "__dict__", {}).get(attr) is expected:
        delattr(parent, attr)


_IMPORTED_AGENT_LOOP = None
try:
    from src.agent_loop import _assemble_prompt, _AGENT_DISCIPLINE
    _IMPORTED_AGENT_LOOP = sys.modules.get("src.agent_loop")
finally:
    # Drop src.agent_loop so later tests re-import it against the REAL
    # src.prompt_security (left un-mocked on purpose — stdlib only).
    if _IMPORTED_AGENT_LOOP is not None:
        _drop_module_if_same("src.agent_loop", _IMPORTED_AGENT_LOOP)
    for mod, stub in _INJECTED_STUBS.items():
        _drop_module_if_same(mod, stub)


def test_discipline_constant_defined():
    assert isinstance(_AGENT_DISCIPLINE, str)
    assert "Task discipline" in _AGENT_DISCIPLINE
    assert "Prompt-injection defense" in _AGENT_DISCIPLINE
    assert "Planner module" in _AGENT_DISCIPLINE
    assert "Browser agent rules" in _AGENT_DISCIPLINE
    assert "Knowledge module" in _AGENT_DISCIPLINE
    assert "Datasource priority" in _AGENT_DISCIPLINE


def test_discipline_in_full_prompt():
    out = _assemble_prompt({"bash", "edit_document"}, compact=False)
    assert _AGENT_DISCIPLINE in out
    assert "## Task discipline" in out
    assert "## Planner module" in out
    assert "## Browser agent rules" in out


def test_discipline_in_compact_prompt():
    out = _assemble_prompt({"bash"}, compact=True)
    assert _AGENT_DISCIPLINE in out
    assert "## Task discipline" in out
    assert "## Planner module" in out
    assert "## Browser agent rules" in out

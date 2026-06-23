"""Tests for context-window-aware agent prompt sizing.

Pins the _tool_limit_for_context helper introduced to reduce prompt bloat on
small local models (4k/8k/16k context windows).
"""

import sys
from unittest.mock import MagicMock

_MOCKED_IMPORTS = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'src.agent_tools',
    'core.models', 'core.database',
]
_INJECTED_IMPORT_STUBS = {}
_PREEXISTING_AGENT_LOOP = sys.modules.get("src.agent_loop")


def _drop_module_if_same(name, expected):
    if sys.modules.get(name) is expected:
        sys.modules.pop(name, None)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and getattr(parent, "__dict__", {}).get(attr) is expected:
        delattr(parent, attr)


for mod in _MOCKED_IMPORTS:
    if mod not in sys.modules:
        stub = MagicMock()
        sys.modules[mod] = stub
        _INJECTED_IMPORT_STUBS[mod] = stub

_IMPORTED_AGENT_LOOP = None
_tool_limit_for_context = None
try:
    from src.agent_loop import _tool_limit_for_context
    _IMPORTED_AGENT_LOOP = sys.modules.get("src.agent_loop")
finally:
    if _PREEXISTING_AGENT_LOOP is None and _IMPORTED_AGENT_LOOP is not None:
        _drop_module_if_same("src.agent_loop", _IMPORTED_AGENT_LOOP)
    for _mod, _stub in _INJECTED_IMPORT_STUBS.items():
        _drop_module_if_same(_mod, _stub)


def test_unknown_context_returns_full_tool_set():
    assert _tool_limit_for_context(0) == 8


def test_tiny_context_4k_returns_3_tools():
    assert _tool_limit_for_context(4096) == 3


def test_small_context_8k_returns_5_tools():
    assert _tool_limit_for_context(8192) == 5


def test_medium_context_16k_returns_6_tools():
    assert _tool_limit_for_context(16384) == 6


def test_large_context_returns_full_tool_set():
    assert _tool_limit_for_context(32768) == 8
    assert _tool_limit_for_context(128000) == 8


def test_boundary_just_inside_4k_tier():
    assert _tool_limit_for_context(1024) == 3
    assert _tool_limit_for_context(4095) == 3


def test_boundary_just_inside_8k_tier():
    assert _tool_limit_for_context(4097) == 5
    assert _tool_limit_for_context(8191) == 5


def test_boundary_just_inside_16k_tier():
    assert _tool_limit_for_context(8193) == 6
    assert _tool_limit_for_context(16383) == 6


def test_boundary_at_16k_cutoff_returns_6():
    # 16384 is the last value in the <=16384 tier
    assert _tool_limit_for_context(16384) == 6


def test_boundary_just_above_16k_returns_8():
    assert _tool_limit_for_context(16385) == 8


def test_negative_context_treated_as_unknown():
    assert _tool_limit_for_context(-1) == 8

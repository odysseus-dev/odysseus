"""Tests for personal RAG hit injection in the agent prompt (agent_loop)."""

import sys
from unittest.mock import MagicMock

_MOCKED_IMPORTS = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'src.agent_tools', 'core.models', 'core.database',
]
_INJECTED = {}
for _mod in _MOCKED_IMPORTS:
    if _mod not in sys.modules:
        _stub = MagicMock()
        sys.modules[_mod] = _stub
        _INJECTED[_mod] = _stub

try:
    from src.agent_loop import _build_personal_rag_directive, _prepend_agent_directive
finally:
    for _mod, _stub in _INJECTED.items():
        if sys.modules.get(_mod) is _stub:
            sys.modules.pop(_mod, None)


def _hit(filename, document, similarity):
    return {"document": document, "similarity": similarity, "metadata": {"filename": filename}}


def test_no_hits_returns_none():
    assert _build_personal_rag_directive([]) is None


def test_hits_below_threshold_are_dropped():
    assert _build_personal_rag_directive([_hit("a.md", "text", 0.34)]) is None


def test_qualifying_hit_is_included_with_filename():
    directive = _build_personal_rag_directive([_hit("S0004.md", "Sammelanschlussleitung ist ...", 0.8)])
    assert "[S0004.md]" in directive
    assert "Sammelanschlussleitung" in directive
    assert "nicht im Text" in directive


def test_at_most_three_hits_are_used():
    hits = [_hit(f"f{i}.md", f"doc{i}", 0.9) for i in range(5)]
    directive = _build_personal_rag_directive(hits)
    assert "[f2.md]" in directive
    assert "[f3.md]" not in directive


def test_missing_filename_falls_back_to_placeholder():
    directive = _build_personal_rag_directive([{"document": "x", "similarity": 0.5}])
    assert "[unbekannt]" in directive


def test_total_length_is_capped():
    directive = _build_personal_rag_directive([_hit("big.md", "A" * 1500, 0.9)] * 3)
    assert len(directive) < 4300  # 4000 Zeichen Auszugs-Limit + Anweisungstext


def test_directive_is_prepended_to_agent_prompt():
    messages = [{"role": "system", "content": "Basis", "_agent_injected": "prompt"}]
    _prepend_agent_directive(messages, "RAG-Hinweis")
    assert messages[0]["content"].startswith("RAG-Hinweis")
    assert messages[0]["content"].endswith("Basis")

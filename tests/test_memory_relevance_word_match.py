"""Memory relevance must classify queries by whole words, not substrings.

get_relevant_memories classified the query with `word in query_lower`. The
1-char identity keyword "i" (and "me"/"am") is a substring of almost every
English query, so nearly all queries were typed "identity" and the user's
identity memories (name, location) were force-injected regardless of
relevance, drowning out actually-relevant memories. The classifier now
matches whole query tokens. The canonical manager lives in src.memory
(services.memory.memory is a re-export shim).
"""
import pytest

from src.memory import MemoryManager


_NAME = {"text": "User's name is Sam Carter", "category": "identity", "id": "1"}


@pytest.fixture
def mgr(tmp_path):
    return MemoryManager(str(tmp_path))


def test_unrelated_fact_query_does_not_force_inject_identity(mgr):
    out = mgr.get_relevant_memories(
        "what is the capital of Brazil", [_NAME], threshold=0.05, max_items=20
    )
    assert all("Sam Carter" not in m["text"] for m in out), \
        "identity memory force-injected for an unrelated query"


def test_identity_query_still_returns_name(mgr):
    out = mgr.get_relevant_memories(
        "what is my name", [_NAME], threshold=0.05, max_items=20
    )
    assert any("Sam Carter" in m["text"] for m in out)


def test_no_memories_or_blank_query(mgr):
    assert mgr.get_relevant_memories("hello", []) == []
    assert mgr.get_relevant_memories("   ", [_NAME]) == []

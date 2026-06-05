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


def test_bare_first_person_preference_query_is_not_identity(mgr):
    # "I like jazz" has the bare pronoun "i" but is a preference query, not an
    # identity one. It must not force-inject the name memory.
    out = mgr.get_relevant_memories(
        "I like jazz", [_NAME], threshold=0.05, max_items=20
    )
    assert all("Sam Carter" not in m["text"] for m in out), \
        "bare 'i' wrongly classified the query as identity"


def test_bare_my_contact_query_is_not_identity(mgr):
    # "what is my email" should reach the contact branch, not identity.
    out = mgr.get_relevant_memories(
        "what is my email", [_NAME], threshold=0.05, max_items=20
    )
    assert all("Sam Carter" not in m["text"] for m in out), \
        "bare 'my' wrongly classified the query as identity"


def test_strong_identity_phrase_still_returns_name(mgr):
    # Explicit identity phrasing must still win.
    for q in ("who am I", "what am I called"):
        out = mgr.get_relevant_memories(q, [_NAME], threshold=0.05, max_items=20)
        assert any("Sam Carter" in m["text"] for m in out), q


def test_identity_query_does_not_force_identity_above_a_better_match(mgr):
    # query_type now boosts scoring, it no longer force-injects identity at a
    # fixed top score. On an identity query, a memory that actually contains
    # the query phrase must still rank above the name memory.
    better = {"text": "who am i in this app guide", "category": "fact", "id": "2"}
    out = mgr.get_relevant_memories("who am i", [_NAME, better], threshold=0.05, max_items=20)
    assert out and out[0]["id"] == "2", \
        "identity memory was force-injected above a more relevant match"


def test_no_memories_or_blank_query(mgr):
    assert mgr.get_relevant_memories("hello", []) == []
    assert mgr.get_relevant_memories("   ", [_NAME]) == []

"""MemoryManager.get_relevant_memories must classify the query type by whole
words, not raw substrings.

The classifier keyed off single-letter/short tokens like "i", "am", "me", "my"
using `word in query_lower`, a substring test. Because "i" is a substring of a
huge fraction of English words ("bitcoin", "price", "list", "find", ...),
almost every query was forced to query_type == "identity", which then
force-promotes every identity/name memory to score 0.9 and suppresses the
contact/preference/task boosts.

get_relevant_memories lives in the canonical src/memory.py; services.memory
re-exports it (#50). Pin the behaviour via both import paths so a future
divergent copy -- or a broken re-export -- is caught.
"""
import tempfile

import pytest

from src.memory import MemoryManager as SrcMemoryManager
from services.memory import MemoryManager as ServicesMemoryManager

_MANAGERS = [SrcMemoryManager, ServicesMemoryManager]

_NAME_MEM = {"text": "User's name is Sam Carter", "id": "name"}
_BTC_MEM = {"text": "The bitcoin price is tracked on the exchange", "id": "btc"}


def _mm(cls):
    return cls(tempfile.mkdtemp(prefix="odysseus_mem_qtype_"))


@pytest.mark.parametrize("cls", _MANAGERS)
def test_non_identity_query_does_not_force_promote_identity_memory(cls):
    # "bitcoin" and "is" merely *contain* the letter "i"; that must not be read
    # as the standalone identity keyword "i" and shove the name memory to the
    # top of an unrelated factual query.
    result = _mm(cls).get_relevant_memories(
        "what is the bitcoin price", [_NAME_MEM, _BTC_MEM]
    )
    ids = [m["id"] for m in result]
    assert ids, "expected at least one relevant memory"
    assert ids[0] == "btc"


@pytest.mark.parametrize("cls", _MANAGERS)
def test_identity_query_still_surfaces_identity_memory(cls):
    # A genuine identity query (whole word "my"/"name") must still surface it.
    result = _mm(cls).get_relevant_memories(
        "what is my name", [_NAME_MEM, _BTC_MEM]
    )
    ids = [m["id"] for m in result]
    assert "name" in ids
    assert ids[0] == "name"

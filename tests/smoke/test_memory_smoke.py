"""Memory: a stored fact is listed, found by search, and gone after delete.

Keyword mode is enough here on purpose. The memory store degrades to
keyword matching when no vector service answers, and that degraded path
is the one a clean checkout actually runs, so it is the one worth
smoking.
"""
from __future__ import annotations

MEMORY_PATH = "/api/memory"
ADD_PATH = "/api/memory/add"
SEARCH_PATH = "/api/memory/search"

# A token that cannot collide with a real memory on a scratch instance.
TOKEN = "odysseus-smoke-marker-quintile"
TEXT = f"The release smoke suite stored the token {TOKEN} as a fact."


def test_a_memory_round_trips(client):
    created = client.post(ADD_PATH, json={"text": TEXT, "category": "fact"})
    assert created.status_code == 200, created.text
    assert created.json().get("ok") is True, created.text

    listed = client.get(MEMORY_PATH)
    assert listed.status_code == 200, listed.text
    matching = [m for m in listed.json().get("memory") or [] if TOKEN in str(m.get("text"))]
    assert matching, [m.get("text") for m in listed.json().get("memory") or []]
    memory_id = matching[0]["id"]

    try:
        found = client.post(SEARCH_PATH, data={"query": TOKEN})
        assert found.status_code == 200, found.text
        hits = [m for m in found.json().get("memories") or [] if TOKEN in str(m.get("text"))]
        assert hits, found.text
    finally:
        removed = client.delete(f"{MEMORY_PATH}/{memory_id}")
        assert removed.status_code == 200, removed.text

    remaining = client.get(MEMORY_PATH).json().get("memory") or []
    assert memory_id not in [m.get("id") for m in remaining]

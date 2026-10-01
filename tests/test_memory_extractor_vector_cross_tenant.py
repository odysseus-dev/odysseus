"""Regression: auto-memory vector dedup must not drop a user's fact because it
matches ANOTHER tenant's memory.

`extract_and_store` dedups each extracted fact against the vector store first.
The vector store (`memory_vector`) is a single shared ChromaDB collection with
no owner in its metadata, so `find_similar` can return a memory_id belonging to
a different user. The old code `continue`d (skipped storing) on any vector hit
without checking ownership, so user B's freshly-extracted fact was silently
dropped when it was merely semantically similar to user A's memory. The text
dedup fallback right below is already owner-scoped; the vector path must be too.
"""
import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_extractor():
    # Load services/memory/memory_extractor.py directly by path so we don't
    # trigger services/__init__ (which imports the search stack and its heavy
    # optional deps). Model-job helpers stay lazy so this loader stays light.
    path = ROOT / "services" / "memory" / "memory_extractor.py"
    spec = importlib.util.spec_from_file_location("memory_extractor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_job_stub(mod, monkeypatch, facts_json):
    from types import SimpleNamespace

    def _submit_model_job(*a, **k):
        return SimpleNamespace(output={"text": facts_json})

    def _bounded_archetype(job_id, **overrides):
        return SimpleNamespace(id=job_id, **overrides)

    monkeypatch.setattr(mod, "_submit_model_job", _submit_model_job, raising=False)
    monkeypatch.setattr(mod, "_bounded_archetype", _bounded_archetype, raising=False)


class FakeSession:
    def __init__(self, owner):
        self.owner = owner

    def get_context_messages(self):
        return [
            {"role": "user", "content": "Tell me where I live."},
            {"role": "assistant", "content": "Noted."},
        ]


class FakeMemoryManager:
    def __init__(self, rows):
        self.rows = list(rows)
        self._n = 0

    def load_all(self):
        return list(self.rows)

    def load_all_for_update(self):
        # Mirrors the real MemoryManager: extraction is a read-modify-write and
        # goes through the strict loader (#5673). A healthy store behaves the
        # same as load_all.
        return list(self.rows)

    def load(self, owner=None):
        return [r for r in self.rows if r.get("owner") == owner]

    def find_duplicates(self, text, subset):
        t = text.strip().lower()
        return [r for r in subset if r.get("text", "").strip().lower() == t]

    def add_entry(self, text, source="auto", category="fact", owner=None):
        self._n += 1
        entry = {"id": f"new-{self._n}", "text": text, "owner": owner,
                 "source": source, "category": category}
        self.rows.append(entry)
        return entry


class FakeVector:
    """Healthy vector store whose find_similar always matches user A's memory."""
    def __init__(self, match_id):
        self.healthy = True
        self._match_id = match_id

    def find_similar(self, text, threshold=0.92):
        return self._match_id


def test_vector_match_from_other_tenant_does_not_drop_users_fact(monkeypatch):
    # User A already owns a semantically-similar memory.
    mm = FakeMemoryManager([
        {"id": "a1", "text": "I live in Lisbon", "owner": "userA"},
    ])
    # The vector store reports user B's new fact as a near-duplicate of a1.
    vec = FakeVector(match_id="a1")
    memory_extractor = _load_extractor()
    _install_job_stub(memory_extractor, monkeypatch, '["My home is in Lisbon"]')

    asyncio.run(memory_extractor.extract_and_store(
        FakeSession(owner="userB"), mm, vec,
        endpoint_url="http://x", model="m",
    ))

    b_texts = {r["text"] for r in mm.load(owner="userB")}
    assert "My home is in Lisbon" in b_texts, (
        "User B's own extracted fact was dropped because the shared vector "
        "store matched user A's memory (cross-tenant dedup)."
    )

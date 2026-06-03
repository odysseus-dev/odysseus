"""Concurrency regression tests for action_consolidate_memory.

The consolidation action runs an LLM dedup pass UNLOCKED (it must — the call is
slow), then applies the keep/drop/clean decision against the FRESH on-disk list
inside one locked ``mutate()``. The pre-fix code saved a pre-LLM snapshot
wholesale, which silently clobbered any memory written while the LLM call was in
flight. These tests assert the FIXED behavior:

  * a memory ADDED during the LLM call survives the consolidation save,
  * a memory EDITED during the LLM call is not dropped by a stale drop decision,
  * the vector index is rebuilt over the saved set (and skipped when unhealthy),
  * a no-op run raises TaskNoop.

Seams (mirrors tests/test_builtin_memory_consolidation.py): monkeypatch
``src.constants.DATA_DIR`` to a tmp dir, ``src.endpoint_resolver.resolve_endpoint``,
and ``src.llm_core.llm_call_async``.
"""
import json
import sys

import pytest


def _import_consolidate_action():
    mod = sys.modules.get("src.builtin_actions")
    if mod is not None and not hasattr(mod, "action_consolidate_memory"):
        sys.modules.pop("src.builtin_actions", None)
        if "src" in sys.modules and hasattr(sys.modules["src"], "builtin_actions"):
            delattr(sys.modules["src"], "builtin_actions")
    from src.builtin_actions import action_consolidate_memory

    return action_consolidate_memory


def _write_memories(tmp_path, memories):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "memory.json").write_text(json.dumps(memories), encoding="utf-8")
    return data_dir


def _read_memories(data_dir):
    return json.loads((data_dir / "memory.json").read_text(encoding="utf-8"))


class _FakeVector:
    """Records rebuild() calls; ``healthy`` toggles the rebuild guard."""

    def __init__(self, healthy=True):
        self.healthy = healthy
        self.rebuild_calls = []

    def rebuild(self, entries):
        # Store a shallow copy of ids so a later in-place edit can't fool the test.
        self.rebuild_calls.append([dict(e) for e in entries])


@pytest.mark.asyncio
async def test_consolidation_preserves_concurrent_add(monkeypatch, tmp_path):
    """Headline regression: a memory added DURING the LLM call must survive,
    while the duplicate the LLM dropped is still removed."""
    from src import constants
    from src import endpoint_resolver
    from src import llm_core
    from src.memory import MemoryManager

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-keep", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-dup-a", "owner": "u", "text": "User likes coffee.", "category": "preference"},
            {"id": "u-dup-b", "owner": "u", "text": "User likes coffee.", "category": "preference"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "model", {})
    )

    async def fake_llm_call_async(**kwargs):
        # Concurrent writer fires DURING the (mocked) LLM call, against the same
        # data dir, before we return the dedup decision. Synchronous + deterministic.
        def _concurrent(entries):
            entries.append(
                {"id": "concurrent-1", "owner": "u", "text": "Added mid-LLM.", "category": "fact"}
            )
            return entries, None

        MemoryManager(str(data_dir)).mutate(_concurrent)

        return json.dumps(
            {
                "keep": [
                    {"id": "u-keep", "text": "User likes tea.", "category": "preference"},
                    {"id": "u-dup-a", "text": "User likes coffee.", "category": "preference"},
                ],
                "drop": [{"id": "u-dup-b", "reason": "duplicate"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    message, ok = await action_consolidate_memory("u", memory_vector=_FakeVector(healthy=False))

    assert ok is True
    saved = {m["id"]: m for m in _read_memories(data_dir)}
    # The mid-LLM add survived the consolidation save (pre-fix: clobbered).
    assert "concurrent-1" in saved
    # The duplicate the LLM dropped is gone.
    assert "u-dup-b" not in saved
    # The kept originals remain.
    assert "u-keep" in saved
    assert "u-dup-a" in saved


@pytest.mark.asyncio
async def test_consolidation_syncs_vector_index(monkeypatch, tmp_path):
    """After a drop, the vector index is rebuilt exactly once with the saved list."""
    from src import constants
    from src import endpoint_resolver
    from src import llm_core

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-keep", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-dup-a", "owner": "u", "text": "User likes coffee.", "category": "preference"},
            {"id": "u-dup-b", "owner": "u", "text": "User likes coffee.", "category": "preference"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "model", {})
    )

    async def fake_llm_call_async(**kwargs):
        return json.dumps(
            {
                "keep": [
                    {"id": "u-keep", "text": "User likes tea.", "category": "preference"},
                    {"id": "u-dup-a", "text": "User likes coffee.", "category": "preference"},
                ],
                "drop": [{"id": "u-dup-b", "reason": "duplicate"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    fake_vec = _FakeVector(healthy=True)
    message, ok = await action_consolidate_memory("u", memory_vector=fake_vec)

    assert ok is True
    assert len(fake_vec.rebuild_calls) == 1
    rebuilt_ids = {e["id"] for e in fake_vec.rebuild_calls[0]}
    saved_ids = {m["id"] for m in _read_memories(data_dir)}
    # rebuild() ran over exactly the persisted set.
    assert rebuilt_ids == saved_ids
    assert "u-dup-b" not in rebuilt_ids


@pytest.mark.asyncio
async def test_consolidation_unhealthy_vector_skips_rebuild(monkeypatch, tmp_path):
    """An unhealthy vector store must not be rebuilt — and must not raise."""
    from src import constants
    from src import endpoint_resolver
    from src import llm_core

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-keep", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-dup-a", "owner": "u", "text": "User likes coffee.", "category": "preference"},
            {"id": "u-dup-b", "owner": "u", "text": "User likes coffee.", "category": "preference"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "model", {})
    )

    async def fake_llm_call_async(**kwargs):
        return json.dumps(
            {
                "keep": [
                    {"id": "u-keep", "text": "User likes tea.", "category": "preference"},
                    {"id": "u-dup-a", "text": "User likes coffee.", "category": "preference"},
                ],
                "drop": [{"id": "u-dup-b", "reason": "duplicate"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    fake_vec = _FakeVector(healthy=False)
    message, ok = await action_consolidate_memory("u", memory_vector=fake_vec)

    assert ok is True
    assert fake_vec.rebuild_calls == []
    # Still saved the drop.
    saved_ids = {m["id"] for m in _read_memories(data_dir)}
    assert "u-dup-b" not in saved_ids


@pytest.mark.asyncio
async def test_consolidation_no_work_raises_TaskNoop(monkeypatch, tmp_path):
    """No duplicates and no LLM endpoint -> nothing to do -> TaskNoop."""
    from src import constants
    from src import endpoint_resolver
    from src.builtin_actions import TaskNoop

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-1", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-2", "owner": "u", "text": "User lives in Berlin.", "category": "identity"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    # No endpoint -> AI tidy is skipped, falls back to exact-duplicate cleanup,
    # which finds none among these distinct memories.
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("", "", {}))

    with pytest.raises(TaskNoop):
        await action_consolidate_memory("u", memory_vector=_FakeVector(healthy=False))


@pytest.mark.asyncio
async def test_consolidation_preserves_concurrent_edit(monkeypatch, tmp_path):
    """A memory the LLM marks to DROP but that is EDITED concurrently during the
    await must be KEPT (the conflict guard: drop only when fresh text == snapshot)."""
    from src import constants
    from src import endpoint_resolver
    from src import llm_core
    from src.memory import MemoryManager

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-keep", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-drop", "owner": "u", "text": "Stale fact.", "category": "fact"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "model", {})
    )

    async def fake_llm_call_async(**kwargs):
        # Concurrent EDIT of the very entry the LLM is about to drop.
        def _edit(entries):
            for e in entries:
                if e.get("id") == "u-drop":
                    e["text"] = "Freshly edited fact."
            return entries, None

        MemoryManager(str(data_dir)).mutate(_edit)

        # LLM (seeing the pre-edit snapshot) decides to drop u-drop.
        return json.dumps(
            {
                "keep": [{"id": "u-keep", "text": "User likes tea.", "category": "preference"}],
                "drop": [{"id": "u-drop", "reason": "stale"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    message, ok = await action_consolidate_memory("u", memory_vector=_FakeVector(healthy=False))

    assert ok is True
    saved = {m["id"]: m for m in _read_memories(data_dir)}
    # The concurrently-edited entry survives despite the stale drop decision.
    assert "u-drop" in saved
    assert saved["u-drop"]["text"] == "Freshly edited fact."


@pytest.mark.asyncio
async def test_consolidation_production_lazy_vector_path_rebuilds(monkeypatch, tmp_path):
    """Production path: the scheduler dispatches the action with NO memory_vector,
    so it must lazily build MemoryVectorStore(DATA_DIR). All stores share one Chroma
    collection via get_chroma_client(), so a healthy lazily-built store rebuilds the
    saved set. The other tests inject a fake and bypass this branch — this one
    exercises the real no-injection path so the vector-sync fix can't ship as a
    silent no-op."""
    from src import constants
    from src import endpoint_resolver
    from src import llm_core
    import src.memory_vector as memory_vector_mod

    action_consolidate_memory = _import_consolidate_action()

    data_dir = _write_memories(
        tmp_path,
        [
            {"id": "u-keep", "owner": "u", "text": "User likes tea.", "category": "preference"},
            {"id": "u-dup-a", "owner": "u", "text": "User likes coffee.", "category": "preference"},
            {"id": "u-dup-b", "owner": "u", "text": "User likes coffee.", "category": "preference"},
        ],
    )
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        endpoint_resolver, "resolve_endpoint", lambda *a, **k: ("http://llm", "model", {})
    )

    async def fake_llm_call_async(**kwargs):
        return json.dumps(
            {
                "keep": [
                    {"id": "u-keep", "text": "User likes tea.", "category": "preference"},
                    {"id": "u-dup-a", "text": "User likes coffee.", "category": "preference"},
                ],
                "drop": [{"id": "u-dup-b", "reason": "duplicate"}],
            }
        )

    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    # The action does `from src.memory_vector import MemoryVectorStore` INSIDE the
    # lazy branch (resolved at call time), so patching the class on the module is
    # what the no-injection path will construct.
    built = {}
    rebuilt = []

    class _LazyFakeVector:
        def __init__(self, data_dir_arg):
            built["dir"] = data_dir_arg
            self.healthy = True

        def rebuild(self, entries):
            rebuilt.append([dict(e) for e in entries])

    monkeypatch.setattr(memory_vector_mod, "MemoryVectorStore", _LazyFakeVector)

    # No memory_vector kwarg -> exercises the production lazy-construct branch.
    message, ok = await action_consolidate_memory("u")

    assert ok is True
    assert built.get("dir") == str(data_dir)        # lazily constructed with DATA_DIR
    assert len(rebuilt) == 1                          # rebuild fired on the real path
    rebuilt_ids = {e["id"] for e in rebuilt[0]}
    saved_ids = {m["id"] for m in _read_memories(data_dir)}
    assert rebuilt_ids == saved_ids
    assert "u-dup-b" not in rebuilt_ids

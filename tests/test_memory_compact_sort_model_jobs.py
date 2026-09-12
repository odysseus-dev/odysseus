"""Memory extract, compact, and auto-sort use bounded model jobs. No llm_call*."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _fn_body(source: str, header: str, stop: str | None = None) -> str:
    body = source.split(header, 1)[1]
    if stop:
        body = body.split(stop, 1)[0]
    return body


def _assert_bounded_job(body: str) -> None:
    assert "submit_model_job" in body
    assert "llm_call_async" not in body
    assert "llm_call(" not in body
    assert "from src.llm_core import" not in body
    assert "stream_governed_agent" not in body
    assert "openhands" not in body.lower()


def test_memory_extract_and_import_are_model_jobs_not_llm_call():
    source = (_ROOT / "routes" / "memory" / "memory_routes.py").read_text(encoding="utf-8")
    extract = _fn_body(source, "async def extract_memory", "async def api_audit_memories")
    imported = _fn_body(source, "async def import_memories_from_file", "def pin_memory")
    _assert_bounded_job(extract)
    _assert_bounded_job(imported)


def test_memory_extractor_audit_and_store_are_model_jobs_not_llm_call():
    source = (_ROOT / "services" / "memory" / "memory_extractor.py").read_text(
        encoding="utf-8"
    )
    store = _fn_body(source, "async def extract_and_store", "async def audit_memories")
    audit = _fn_body(source, "async def audit_memories")
    _assert_bounded_job(store)
    _assert_bounded_job(audit)


def test_skill_extract_is_model_job_not_llm_call():
    source = (_ROOT / "services" / "memory" / "skill_extractor.py").read_text(
        encoding="utf-8"
    )
    body = _fn_body(source, "async def maybe_extract_skill")
    _assert_bounded_job(body)


def test_compact_callers_are_model_jobs_not_llm_call():
    history = (_ROOT / "routes" / "history" / "history_routes.py").read_text(
        encoding="utf-8"
    )
    session = (_ROOT / "routes" / "session_routes.py").read_text(encoding="utf-8")
    compact = (_ROOT / "src" / "context_compactor.py").read_text(encoding="utf-8")
    history_body = _fn_body(history, "async def compact_session")
    session_compact = _fn_body(
        session, "async def compact_session", "def auto_sort_sessions"
    )
    maybe = _fn_body(compact, "async def maybe_compact", "def apply_compaction_state")
    _assert_bounded_job(history_body)
    _assert_bounded_job(session_compact)
    _assert_bounded_job(maybe)


def test_auto_sort_callers_are_model_jobs_not_llm_call():
    session = (_ROOT / "routes" / "session_routes.py").read_text(encoding="utf-8")
    actions = (_ROOT / "src" / "session_actions.py").read_text(encoding="utf-8")
    route = _fn_body(session, "def auto_sort_sessions")
    run = _fn_body(actions, "async def run_auto_sort")
    _assert_bounded_job(route)
    _assert_bounded_job(run)


class _FakeSession:
    owner = "alice"
    session_id = "sess-1"

    def get_context_messages(self):
        return [
            {"role": "user", "content": "Hi, a few things about me."},
            {"role": "assistant", "content": "Noted."},
        ]


def _job_result(text: str = "", **output):
    data = dict(output)
    if text and "text" not in data:
        data["text"] = text
    return SimpleNamespace(output=data)


@pytest.mark.asyncio
async def test_extract_and_store_uses_submit_model_job(monkeypatch, tmp_path):
    from services.memory import memory_extractor as mex
    from src.memory import MemoryManager
    import src.event_bus as event_bus

    calls: list[tuple] = []

    def fake_job(archetype, payload, owner, **kwargs):
        calls.append((getattr(archetype, "id", None), payload, owner))
        return _job_result(
            '[{"text": "Alice lives in Lisbon", "category": "fact"}]'
        )

    async def boom(*args, **kwargs):
        raise AssertionError("llm_call_async fallback must not run")

    monkeypatch.setattr(mex, "_submit_model_job", fake_job, raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", boom, raising=False)
    monkeypatch.setattr(event_bus, "fire_event", lambda *a, **k: None)

    mgr = MemoryManager(str(tmp_path))
    await mex.extract_and_store(
        _FakeSession(), mgr, None, endpoint_url="http://x", model="m"
    )
    assert calls, "extract_and_store must submit_model_job"
    assert calls[0][0] == "memory-extract"
    assert calls[0][2] == "alice"
    texts = {row["text"] for row in mgr.load(owner="alice")}
    assert "Alice lives in Lisbon" in texts


@pytest.mark.asyncio
async def test_extract_and_store_skips_worker_when_too_few_messages(monkeypatch):
    from services.memory import memory_extractor as mex

    calls: list[object] = []
    monkeypatch.setattr(
        mex,
        "_submit_model_job",
        lambda *a, **k: calls.append((a, k)) or _job_result("[]"),
        raising=False,
    )

    class _Short:
        owner = "alice"

        def get_context_messages(self):
            return [{"role": "user", "content": "hi"}]

    await mex.extract_and_store(_Short(), None, None, endpoint_url="http://x", model="m")
    assert calls == []


@pytest.mark.asyncio
async def test_audit_skips_worker_when_fingerprint_unchanged(monkeypatch, tmp_path):
    from services.memory import memory_extractor as mex
    from src.memory import MemoryManager

    calls: list[object] = []
    monkeypatch.setattr(
        mex,
        "_submit_model_job",
        lambda *a, **k: calls.append((a, k)) or _job_result("[]"),
        raising=False,
    )

    mgr = MemoryManager(str(tmp_path))
    entry = mgr.add_entry("Alice likes tea", source="manual", owner="alice")
    mgr.save([entry])
    mex._save_tidy_state(mgr, "alice", mex._fingerprint_entries(mgr.load(owner="alice")))

    out = await mex.audit_memories(mgr, None, "http://x", "m", owner="alice")
    assert calls == []
    assert out.get("already_tidy") is True


@pytest.mark.asyncio
async def test_maybe_extract_skill_uses_submit_model_job(monkeypatch):
    from services.memory import skill_extractor

    calls: list[tuple] = []

    def fake_job(archetype, payload, owner, **kwargs):
        calls.append((getattr(archetype, "id", None), owner))
        return _job_result(
            '{"title": "Deploy runbook", "problem": "manual deploys", '
            '"solution": "use the script", "steps": ["build", "push", "restart"], '
            '"tags": ["deploy"], "confidence": 0.9}'
        )

    async def boom(*args, **kwargs):
        raise AssertionError("llm_call_async fallback must not run")

    monkeypatch.setattr(skill_extractor, "submit_model_job", fake_job, raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", boom, raising=False)

    class _Skills:
        def __init__(self):
            self.added = []

        def load(self, owner=None):
            return []

        def add_skill(self, **kwargs):
            self.added.append(kwargs)
            return {"id": "skill-1", **kwargs}

    class _Sess:
        def get_context_messages(self):
            return [
                {"role": "user", "content": "Walk me through deploying the service"},
                {"role": "assistant", "content": "Sure, here is the runbook"},
            ]

    skills = _Skills()
    entry = await skill_extractor.maybe_extract_skill(
        _Sess(), skills, "http://x", "m", {}, 3, 3, owner="alice"
    )
    assert calls and calls[0][0] == "skill-extract"
    assert entry is not None
    assert entry["title"] == "Deploy runbook"


@pytest.mark.asyncio
async def test_maybe_extract_skill_skips_worker_below_threshold(monkeypatch):
    from services.memory import skill_extractor

    calls: list[object] = []
    monkeypatch.setattr(
        skill_extractor,
        "submit_model_job",
        lambda *a, **k: calls.append((a, k)) or _job_result("null"),
        raising=False,
    )
    out = await skill_extractor.maybe_extract_skill(
        SimpleNamespace(get_context_messages=lambda: []),
        SimpleNamespace(),
        "http://x",
        "m",
        {},
        1,
        0,
        owner="alice",
    )
    assert out is None
    assert calls == []


@pytest.mark.asyncio
async def test_maybe_compact_skips_worker_below_threshold(monkeypatch):
    import src.context_compactor as cc

    calls: list[object] = []
    monkeypatch.setattr(cc, "get_context_length", lambda *a, **k: 10_000)
    monkeypatch.setattr(cc, "estimate_tokens", lambda msgs: 10)
    monkeypatch.setattr(
        cc,
        "submit_model_job",
        lambda *a, **k: calls.append((a, k)) or _job_result("nope"),
        raising=False,
    )
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]
    out, _ctx, compacted = await cc.maybe_compact(
        None, "http://x", "m", messages, headers={}
    )
    assert compacted is False
    assert out == messages
    assert calls == []


@pytest.mark.asyncio
async def test_maybe_compact_uses_submit_model_job_for_summary(monkeypatch):
    import src.context_compactor as cc

    calls: list[tuple] = []

    def fake_job(archetype, payload, owner, **kwargs):
        calls.append((getattr(archetype, "id", None), owner, payload))
        return _job_result("compact summary text")

    async def boom(*args, **kwargs):
        raise AssertionError("llm_call_async fallback must not run")

    monkeypatch.setattr(cc, "get_context_length", lambda *a, **k: 100)
    monkeypatch.setattr(cc, "estimate_tokens", lambda msgs: 10_000)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(cc, "_update_session_history", lambda *a, **k: None)
    monkeypatch.setattr(cc, "submit_model_job", fake_job, raising=False)
    monkeypatch.setattr(cc, "llm_call_async", boom, raising=False)

    messages = [
        {"role": "system", "content": "PRESET"},
        {"role": "user", "content": "OLDER-1"},
        {"role": "assistant", "content": "OLDER-2"},
        {"role": "user", "content": "OLDER-3"},
        {"role": "assistant", "content": "RECENT-1"},
        {"role": "user", "content": "RECENT-2"},
        {"role": "assistant", "content": "RECENT-3"},
    ]
    out, _ctx, compacted = await cc.maybe_compact(
        None, "http://x", "m", messages, headers={}, owner="alice"
    )
    assert compacted is True
    assert calls and calls[0][0] == "session-compact"
    assert calls[0][1] == "alice"
    assert any(
        m.get("role") == "system" and "compact summary text" in (m.get("content") or "")
        for m in out
    )


@pytest.mark.asyncio
async def test_maybe_compact_fails_closed_without_llm_fallback(monkeypatch):
    import src.context_compactor as cc

    def boom_job(*args, **kwargs):
        raise RuntimeError("worker down")

    async def boom_llm(*args, **kwargs):
        raise AssertionError("llm_call_async fallback must not run")

    monkeypatch.setattr(cc, "get_context_length", lambda *a, **k: 100)
    monkeypatch.setattr(cc, "estimate_tokens", lambda msgs: 10_000)
    monkeypatch.setattr(cc, "resolve_endpoint", lambda *a, **k: (None, None, None))
    monkeypatch.setattr(cc, "submit_model_job", boom_job, raising=False)
    monkeypatch.setattr(cc, "llm_call_async", boom_llm, raising=False)

    messages = [
        {"role": "system", "content": "PRESET"},
        {"role": "user", "content": "OLDER-1"},
        {"role": "assistant", "content": "OLDER-2"},
        {"role": "user", "content": "OLDER-3"},
        {"role": "assistant", "content": "RECENT-1"},
        {"role": "user", "content": "RECENT-2"},
        {"role": "assistant", "content": "RECENT-3"},
    ]
    out, _ctx, compacted = await cc.maybe_compact(
        None, "http://x", "m", messages, headers={}, owner="alice"
    )
    assert compacted is False
    assert out == messages


@pytest.mark.asyncio
async def test_run_auto_sort_skip_llm_never_submits_job(monkeypatch):
    import src.session_actions as session_actions

    calls: list[object] = []
    monkeypatch.setattr(
        session_actions,
        "submit_model_job",
        lambda *a, **k: calls.append((a, k)) or _job_result('{"folders": {}}'),
        raising=False,
    )

    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return []

        def first(self):
            return None

    class _Db:
        def query(self, *a, **k):
            return _Q()

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr("core.database.SessionLocal", lambda: _Db())
    result = await session_actions.run_auto_sort("alice", skip_llm=True)
    assert calls == []
    assert "folder sort skipped" in result.lower() or "too few" in result.lower()

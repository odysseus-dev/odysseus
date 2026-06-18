import types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from core import database as cdb
from src import agent_run_records, agent_runs
from tests.helpers.sqlite_db import make_temp_sqlite


@pytest.fixture()
def run_db(monkeypatch):
    SessionLocal, _engine, _tmp = make_temp_sqlite(cdb.Base.metadata)
    monkeypatch.setattr(agent_run_records, "SessionLocal", SessionLocal)

    db = SessionLocal()
    try:
        db.add(cdb.Session(
            id="sess-agent-run",
            name="Agent Run Test",
            endpoint_url="http://example.test/v1",
            model="selected-model",
            owner="alice",
        ))
        db.commit()
    finally:
        db.close()

    return SessionLocal


def test_begin_and_finish_persist_agent_run_lifecycle(run_db):
    run_id = agent_run_records.begin(
        "sess-agent-run",
        mode="agent",
        model="actual-model",
        requested_model="selected-model",
        workspace_path="/workspace",
        workspace_label=r"D:\Odysseus_Workspace (mounted as /workspace)",
        owner="alice",
        user_message_id="user-msg-1",
    )

    assert run_id

    db = run_db()
    try:
        run = db.query(cdb.AgentRunRecord).filter(cdb.AgentRunRecord.id == run_id).first()
        assert run is not None
        assert run.status == "running"
        assert run.mode == "agent"
        assert run.model == "actual-model"
        assert run.requested_model == "selected-model"
        assert run.workspace_path == "/workspace"
        assert run.owner == "alice"
        assert run.user_message_id == "user-msg-1"
        assert run.finished_at is None
    finally:
        db.close()

    assert agent_run_records.finish(
        run_id,
        status="done",
        event_count=7,
        partial_chars=42,
        assistant_message_id="assistant-msg-1",
        last_event_type="done",
    )

    db = run_db()
    try:
        run = db.query(cdb.AgentRunRecord).filter(cdb.AgentRunRecord.id == run_id).first()
        assert run.status == "done"
        assert run.finished_at is not None
        assert run.event_count == 7
        assert run.partial_chars == 42
        assert run.assistant_message_id == "assistant-msg-1"
        assert run.last_event_type == "done"

        latest = agent_run_records.latest_for_session("sess-agent-run")
        assert latest["id"] == run_id
        assert latest["status"] == "done"
        assert latest["event_count"] == 7
    finally:
        db.close()


def test_mark_lost_running_runs_preserves_terminal_rows(run_db):
    running_id = agent_run_records.begin("sess-agent-run", mode="chat")
    done_id = agent_run_records.begin("sess-agent-run", mode="agent")
    assert agent_run_records.finish(done_id, status="done")

    assert agent_run_records.mark_lost_running_runs() == 1

    db = run_db()
    try:
        running = db.query(cdb.AgentRunRecord).filter(cdb.AgentRunRecord.id == running_id).first()
        done = db.query(cdb.AgentRunRecord).filter(cdb.AgentRunRecord.id == done_id).first()
        assert running.status == "lost_after_restart"
        assert running.stop_reason == "server_restart"
        assert "Server restarted" in running.error
        assert running.finished_at is not None
        assert done.status == "done"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_detached_run_manager_finishes_durable_record(monkeypatch):
    calls = []

    def _finish(run_id, **kwargs):
        calls.append((run_id, kwargs))
        return True

    monkeypatch.setattr(agent_runs.agent_run_records, "finish", _finish)

    async def _stream():
        yield "data: hello\n\n"
        yield "data: [DONE]\n\n"

    session_id = "sess-agent-run-manager-record"
    agent_runs._RUNS.pop(session_id, None)
    run = agent_runs.start(session_id, _stream(), record_id="run-record-1")
    try:
        await run.task
    finally:
        agent_runs._RUNS.pop(session_id, None)

    assert run.status == "done"
    assert calls == [(
        "run-record-1",
        {
            "status": "done",
            "stop_reason": "",
            "error": "",
            "event_count": 2,
            "last_event_type": "done",
        },
    )]

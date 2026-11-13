from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.task.task_routes import setup_task_routes
from src.tool_approvals import ToolApprovalStore, tool_approval_store
from src.tool_capabilities import capabilities_for_action


class _DummyScheduler:
    pass


def _make_app(monkeypatch, current_user="alice"):
    import routes.task.task_routes as tr
    monkeypatch.setattr(tr, "get_current_user", lambda request: current_user, raising=False)
    app = FastAPI()
    app.include_router(setup_task_routes(_DummyScheduler()))
    return app


def test_list_pending_is_strictly_owner_scoped():
    store = ToolApprovalStore()
    p1 = store.create(
        owner="Alice",
        session_id="sess-1",
        origin_run_id="run-1",
        tool_name="send_email",
        content='{"to":"a@example.com"}',
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=True,
        selected_tools=("send_email",),
        continuation_query="send it",
        capabilities=capabilities_for_action("send_email", '{"to":"a@example.com"}'),
    )
    store.create(
        owner="Bob",
        session_id="sess-2",
        origin_run_id="run-2",
        tool_name="run_shell",
        content="rm -rf /tmp/x",
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=False,
        selected_tools=("run_shell",),
        continuation_query="run it",
        capabilities=capabilities_for_action("run_shell", "rm -rf /tmp/x"),
    )
    alice_items = store.list_pending(owner="alice")
    assert len(alice_items) == 1
    assert alice_items[0]["approval_id"] == p1.approval_id
    assert alice_items[0]["tool_name"] == "send_email"
    assert alice_items[0]["external_untrusted_context_seen"] is True
    assert store.list_pending(owner="charlie") == []


def test_tasks_approval_queue_routes_enforce_owner_and_single_action_consume(monkeypatch):
    pending = tool_approval_store.create(
        owner="alice",
        session_id="sess-audit-1",
        origin_run_id="run-audit-1",
        tool_name="write_file",
        content='{"path":"ledger.csv"}',
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=False,
        selected_tools=("write_file",),
        continuation_query="write ledger",
        capabilities=capabilities_for_action("write_file", '{"path":"ledger.csv"}'),
    )
    try:
        bob_client = TestClient(_make_app(monkeypatch, current_user="bob"))
        assert bob_client.get("/api/tasks/approvals/pending").json()["count"] == 0
        bob_res = bob_client.post(
            f"/api/tasks/approvals/{pending.approval_id}/resolve",
            json={"decision": "approve_task"},
        )
        assert bob_res.status_code == 404

        alice_client = TestClient(_make_app(monkeypatch, current_user="alice"))
        listed = alice_client.get("/api/tasks/approvals/pending").json()
        assert listed["count"] == 1
        assert listed["approvals"][0]["approval_id"] == pending.approval_id

        res = alice_client.post(
            f"/api/tasks/approvals/{pending.approval_id}/resolve",
            json={"decision": "approve_task"},
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert tool_approval_store.peek(pending.approval_id) is None
    finally:
        tool_approval_store.consume(
            pending.approval_id,
            decision="deny",
            owner="alice",
            session_id="sess-audit-1",
        )

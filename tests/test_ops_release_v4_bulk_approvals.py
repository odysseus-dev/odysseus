from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.task.task_routes as tr
from routes.task.task_routes import setup_task_routes
from src.tool_approvals import tool_approval_store
from src.tool_capabilities import capabilities_for_action


class _DummyScheduler:
    pass


def _build_client(monkeypatch, current_user: dict[str, str]) -> TestClient:
    monkeypatch.setattr(tr, "get_current_user", lambda request: current_user["name"], raising=False)
    app = FastAPI()
    app.include_router(setup_task_routes(_DummyScheduler()))
    return TestClient(app)


def _seed(*, owner: str, session_id: str, tool_name: str, content: str):
    return tool_approval_store.create(
        owner=owner,
        session_id=session_id,
        origin_run_id=f"run-{session_id}",
        tool_name=tool_name,
        content=content,
        workspace="",
        document_id="",
        document_version=None,
        document_digest="",
        external_untrusted_context_seen=False,
        selected_tools=(tool_name,),
        continuation_query="continue",
        capabilities=capabilities_for_action(tool_name, content),
    )


def test_bulk_approval_resolution_owner_isolation_and_history(monkeypatch):
    current_user = {"name": "alice"}
    client = _build_client(monkeypatch, current_user)
    with tool_approval_store._lock:
        tool_approval_store._pending.clear()
        tool_approval_store._decision_history.clear()

    try:
        a1 = _seed(owner="alice", session_id="sess-1a", tool_name="send_email", content='{"to":"a@example.com"}')
        a2 = _seed(owner="alice", session_id="sess-1b", tool_name="write_file", content='{"path":"notes.md"}')
        b1 = _seed(owner="bob", session_id="sess-2", tool_name="run_shell", content="rm -rf /tmp/x")

        res = client.post(
            "/api/tasks/approvals/resolve-bulk",
            json={
                "approval_ids": [a1.approval_id, a2.approval_id, b1.approval_id, "missing-id"],
                "decision": "deny",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["decision"] == "deny"
        assert body["resolved_count"] == 2
        assert {item["approval_id"] for item in body["resolved"]} == {a1.approval_id, a2.approval_id}
        assert {item["approval_id"] for item in body["failed"]} == {b1.approval_id, "missing-id"}

        assert len(tool_approval_store.list_pending(owner="bob")) == 1
        assert len(tool_approval_store.list_pending(owner="alice")) == 0

        hist = client.get("/api/tasks/approvals/history").json()
        assert hist["count"] == 2
        assert {item["approval_id"] for item in hist["decisions"]} == {a1.approval_id, a2.approval_id}

        current_user["name"] = "bob"
        bob_hist = client.get("/api/tasks/approvals/history").json()
        assert bob_hist["count"] == 0
    finally:
        with tool_approval_store._lock:
            tool_approval_store._pending.clear()
            tool_approval_store._decision_history.clear()


def test_bulk_approval_validation_guards(monkeypatch):
    client = _build_client(monkeypatch, {"name": "alice"})

    assert client.post("/api/tasks/approvals/resolve-bulk", json={"approval_ids": [], "decision": "deny"}).status_code == 400
    assert client.post("/api/tasks/approvals/resolve-bulk", json={"approval_ids": ["x"], "decision": "invalid"}).status_code == 400
    too_many = [f"id-{i}" for i in range(51)]
    assert client.post("/api/tasks/approvals/resolve-bulk", json={"approval_ids": too_many, "decision": "deny"}).status_code == 400